"""Pull one operation at a time. No remote command strings and no secret output."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler

from .. import bundle_tools
from ..enforcement.intent import load_intent, TETRAGON_RULE_MODES
from ..enforcement.tetragon import comparable_spec
from ..mvp.policy_bundle import verify_policy_bundle
from ..mvp.tetragon_state import read_tetragon_state
from .safety import evaluate, is_mode_switch_target, recent, requires_restart_confirmation

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
REQUIRED_ROLES = {"gateway_policy", "policy_approvals", "target", "enforcement_intent"}


class Rejected(ValueError):
    def __init__(self, code, observed=None, reason_code=None):
        super().__init__(code)
        self.observed = observed
        self.reason_code = reason_code


class RestoreReported(RuntimeError):
    """Privileged helper reports restoration; local Gateway must still verify it."""


def reject(code, observed=None, *, reason_code=None):
    raise Rejected(code, observed, reason_code)


def now():
    return datetime.now(timezone.utc).isoformat()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PlatformClient:
    def __init__(self, url: str, token_file: str, ca_file: str | None = None):
        parsed = urlparse(url)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})):
            raise ValueError("platform URL requires HTTPS (HTTP only for loopback)")
        path = Path(token_file)
        info = path.stat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid not in {0, os.getuid()}:
            raise ValueError("agent token file requires private permissions and trusted ownership")
        self._token = path.read_text().strip()
        if not self._token or "\n" in self._token or "\r" in self._token:
            raise ValueError("invalid agent token file")
        self.base = url.rstrip("/") + "/api/runtime/operations"
        # cafile replaces the default trust store; never disable verification.
        if ca_file is not None and not ca_file:
            raise ValueError("CA file must not be empty")
        context = ssl.create_default_context(cafile=ca_file)
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=context))

    def request(self, path="", body=None, *, binary=False):
        req = Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                      headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"})
        with self.opener.open(req, timeout=30) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            reject("VALIDATION_FAILED")
        return raw if binary else json.loads(raw)


@dataclass(frozen=True)
class Config:
    server: str
    bundle_root: str
    context: str
    namespace: str
    status_url: str | None = None
    kubeconfig: str | None = None
    token_env_file: str | None = None
    state_file: str | None = None
    review_file: str | None = None
    history_file: str | None = None
    tools_dir: str = str(Path(__file__).resolve().parents[3] / "tools")
    monitor_seconds: int = 86400
    execute: bool = False
    sudo_bundle_apply: bool = False
    lab_self_approval: bool = False


@contextmanager
def locked(path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class Agent:
    def __init__(self, client, config: Config):
        self.client, self.config = client, config
        self.monitor_seconds = config.monitor_seconds
        self.target_ref = None
        if not ID.fullmatch(config.server) or config.monitor_seconds < 0:
            raise ValueError("invalid agent configuration")
        if config.status_url is not None:
            parsed = urlparse(config.status_url)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError("Gateway status URL must be local")

    def tool(self, name):
        path = Path(self.config.tools_dir) / (name + ".py")
        # Tools are installed/trusted local code; never supplied in an operation.
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, self.config.tools_dir)
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        return module

    def run(self, command):
        if command and command[0] == "kubectl" and self.config.kubeconfig:
            command = [command[0], "--kubeconfig", self.config.kubeconfig, *command[1:]]
        return subprocess.run(command, capture_output=True, text=True, timeout=70, check=False)

    def current(self):
        cfg = self.config
        root = Path(cfg.bundle_root)
        current = root / "current"
        try:
            if not current.is_symlink() or not ID.fullmatch(os.readlink(current)):
                reject("VALIDATION_FAILED", reason_code="CURRENT_LINK_INVALID")
            destination = root / os.readlink(current)
            if destination.is_symlink() or not destination.is_dir():
                reject("VALIDATION_FAILED", reason_code="CURRENT_LINK_INVALID")
        except OSError:
            reject("VALIDATION_FAILED", reason_code="CURRENT_LINK_INVALID")
        bundle = self.verified_bundle(destination)
        self.check_target(bundle)
        paths = bundle.role_paths()
        try:
            intent = load_intent(paths["enforcement_intent"])
        except (OSError, ValueError):
            reject("VALIDATION_FAILED", reason_code="BUNDLE_INVALID")
        token = bundle_tools._token(cfg.token_env_file)
        try:
            state = bundle_tools._gateway_status(cfg.context, cfg.namespace, cfg.kubeconfig, 8080, token,
                                                 status_url=cfg.status_url)
        except bundle_tools.StatusUnavailable:
            reject("VALIDATION_FAILED", reason_code="STATUS_UNREACHABLE")
        policy = state.get("policy")
        active_bundle = state.get("policy_bundle")
        if (not isinstance(policy, dict) or not isinstance(active_bundle, dict)
                or not isinstance(policy.get("approval"), dict)):
            reject("VALIDATION_FAILED", reason_code="STATUS_MISMATCH")
        if (active_bundle.get("bundle_id") != bundle.bundle_id or active_bundle.get("sha256") != bundle.sha256
                or policy["approval"].get("state") != "verified" or policy.get("gateway_active") is not True):
            reject("STATE_CHANGED", reason_code="STATUS_MISMATCH")
        self.target_ref = bundle.target_ref
        return {"bundle_id": bundle.bundle_id, "approval": "verified", "policy_id": None, "mode": None, "spec_sha256": None}, intent

    def verified_bundle(self, directory):
        try:
            path = Path(directory)
            if path.is_symlink() or not path.is_dir():
                reject("VALIDATION_FAILED", reason_code="BUNDLE_INVALID")
            bundle = verify_policy_bundle(path)
            if bundle.bundle_id != path.name:
                reject("VALIDATION_FAILED", reason_code="BUNDLE_INVALID")
        except Rejected:
            raise
        except (OSError, ValueError):
            reject("VALIDATION_FAILED", reason_code="BUNDLE_INVALID")
        if REQUIRED_ROLES - bundle.role_paths().keys():
            reject("VALIDATION_FAILED", reason_code="BUNDLE_ROLES_MISSING")
        return bundle

    def check_target(self, bundle):
        if REQUIRED_ROLES - bundle.role_paths().keys():
            reject("VALIDATION_FAILED", reason_code="BUNDLE_ROLES_MISSING")
        try:
            target = json.loads(Path(bundle.role_paths()["target"]).read_text())
            kube = target.get("kubernetes")
            if not isinstance(kube, dict):
                reject("VALIDATION_FAILED", reason_code="BUNDLE_INVALID")
        except (OSError, ValueError):
            reject("VALIDATION_FAILED", reason_code="BUNDLE_INVALID")
        if (kube.get("context") != self.config.context or kube.get("namespace") != self.config.namespace
                or (self.target_ref is not None and bundle.target_ref != self.target_ref)):
            reject("TARGET_MISMATCH")

    def snapshot(self):
        if not self.config.state_file:
            return {}
        path = Path(self.config.state_file)
        try:
            info = path.stat()
            if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_uid not in {0, os.getuid()}:
                return {}
        except OSError:
            return {}
        return read_tetragon_state(path, context=self.config.context)

    def review(self):
        try:
            if not self.config.review_file:
                return {}
            path = Path(self.config.review_file)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_uid not in {0, os.getuid()} or info.st_size > 2 * 1024 * 1024):
                    return {}
                raw = source.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    return {}
            value = json.loads(raw)
            value["file_sha256"] = hashlib.sha256(raw).hexdigest()
            return value
        except (OSError, ValueError, TypeError):
            return {}

    def readiness(self, current, intent):
        snapshot = self.snapshot()
        rows = []
        review = self.review()
        for name in sorted({p["name"] for p in snapshot.get("policies", []) if p.get("namespace") == self.config.namespace}):
            policies = [p for p in snapshot.get("policies", [])
                        if p.get("name") == name and p.get("namespace") == self.config.namespace]
            if not all(is_mode_switch_target(p) for p in policies):
                continue
            ready = evaluate(snapshot, name, intent, review, self.monitor_seconds)
            if ready["expected"] and all(ready["expected"].values()):
                if len({p.get("node") for p in snapshot.get("policies", []) if p.get("name") == name}) != 1:
                    ready["checks"]["policy"] = False
                ready["expected"] = {**current, **ready["expected"]}
                rows.append(ready)
        return rows

    def mode_state(self, name, current):
        tool = self.tool("tetragon_mode")
        policy = tool._read_policy(self.run, self.config.context, "kube-system", name, self.config.namespace)
        if not policy:
            reject("STATE_CHANGED")
        result = self.run(["kubectl", "--context", self.config.context, "-n", self.config.namespace,
                           "get", "tracingpolicynamespaced", name, "-o", "json"])
        if result.returncode:
            reject("STATE_CHANGED")
        spec = comparable_spec(json.loads(result.stdout)["spec"])
        spec_hash = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {**current, "policy_id": policy["id"], "mode": policy["mode"], "spec_sha256": spec_hash}

    def live_safety(self, name):
        snapshot = self.snapshot()
        # The existing set-mode helper executes on one Tetragon daemon. Until
        # per-node set-mode exists, require exactly one node and one ready pod.
        result = self.run(["kubectl", "--context", self.config.context, "-n", "kube-system",
                           "get", "pods", "-l", "app.kubernetes.io/name=tetragon", "-o", "json"])
        pods = json.loads(result.stdout).get("items", []) if not result.returncode else []
        if len(pods) != 1:
            reject("SAFETY_REJECTED")
        pod = pods[0]
        node = pod.get("spec", {}).get("nodeName")
        if not any(s.get("name") == "tetragon" and s.get("ready") is True for s in pod.get("status", {}).get("containerStatuses", [])):
            reject("SAFETY_REJECTED")
        def read(*args):
            value = self.run(["kubectl", "--context", self.config.context, "-n", "kube-system", "exec",
                              pod["metadata"]["name"], "-c", "tetragon", "--", *args])
            return value.stdout if value.returncode == 0 else None
        kernel = (read("uname", "-r") or "").strip()
        classifier = self.tool("tetragon_capability")
        cap = classifier.classify_capabilities(kernel=kernel,
              config=read("cat", f"/boot/config-{kernel}") or read("zcat", "/proc/config.gz"),
              lsm=read("cat", "/sys/kernel/security/lsm"),
              injection_list=read("cat", "/sys/kernel/debug/error_injection/list"), probe=read("tetra", "probe"))
        cap["node"] = node
        snapshot["capabilities"] = [cap]
        if any(p.get("node") != node for p in snapshot.get("policies", []) if p.get("name") == name):
            reject("SAFETY_REJECTED")
        return snapshot

    def validate(self, op, before, intent):
        if op.get("server") != self.config.server:
            reject("TARGET_MISMATCH")
        kind, payload = op.get("kind"), op.get("payload", {})
        if kind not in {"bundle.apply", "bundle.switch", "tetragon.mode", "enforcement.apply"}:
            reject("VALIDATION_FAILED")
        if op.get("state") != "claimed" or not recent(op.get("created_at"), 86400):
            reject("VALIDATION_FAILED")
        try:
            lease_until = datetime.fromisoformat(op["lease_until"])
            expired = lease_until <= datetime.now(timezone.utc)
        except (KeyError, ValueError, TypeError):
            reject("VALIDATION_FAILED")
        if expired:
            reject("LEASE_EXPIRED")
        needs = kind in {"bundle.apply", "enforcement.apply"} or (kind == "tetragon.mode" and payload.get("mode") == "enforce")
        if op.get("approval_required") is not needs:
            reject("APPROVAL_REQUIRED")
        if needs:
            approvals = op.get("approvals", [])
            approved = any((a.get("role") == "approver" and a.get("token_id") != op.get("requested_by"))
                           or (kind != "enforcement.apply" and self.config.lab_self_approval and op.get("lab_self_approval") is True
                               and a.get("self_approval") is True and a.get("token_id") == op.get("requested_by")
                               and a.get("role") in {"runtime_operator", "approver"}) for a in approvals)
            if not approved:
                reject("APPROVAL_REQUIRED")
        if op.get("requested_role") != "runtime_operator":
            reject("APPROVAL_REQUIRED")
        if kind == "enforcement.apply":
            if (payload.get("components") not in [["tetragon"], ["tetragon", "cilium"]]
                    or type(payload.get("cilium_apply", False)) is not bool
                    or (payload.get("cilium_apply") and "cilium" not in payload["components"])):
                reject("VALIDATION_FAILED")
            bundle = self.verified_bundle(Path(self.config.bundle_root) / before["bundle_id"])
            self.check_target(bundle)
            if payload.get("bundle_id") != before["bundle_id"] or payload.get("bundle_sha256") != bundle.sha256:
                reject("STATE_CHANGED")
        if kind == "tetragon.mode":
            name, mode = payload.get("policy_name"), payload.get("mode")
            if not isinstance(name, str) or not ID.fullmatch(name) or mode not in {"monitor", "enforce"}:
                reject("VALIDATION_FAILED")
            # Safety-direction monitor remains available for a known allowed rule.
            rule = name.removeprefix("runtime-").upper()
            if rule not in TETRAGON_RULE_MODES or mode not in intent.allowed_modes(rule) or name != "runtime-" + rule.lower():
                reject("SAFETY_REJECTED")
            before = self.mode_state(name, before)
            expected = {k: v for k, v in op.get("expected", {}).items() if v is not None}
            if not expected or any(before.get(k) != v for k, v in expected.items()):
                reject("STATE_CHANGED", before)
            if mode == "enforce":
                ready = evaluate(self.live_safety(name), name, intent, self.review(), self.monitor_seconds)
                if (not all(ready["checks"].values()) or ready["expected"] != {k: before[k] for k in ("policy_id", "mode", "spec_sha256")}
                        or (requires_restart_confirmation(ready) and payload.get("confirmation") != "ENGINE_RESTART")):
                    reject("SAFETY_REJECTED")
        expected = {k: v for k, v in op.get("expected", {}).items() if v is not None}
        if not expected or any(before.get(k) != v for k, v in expected.items()):
            reject("STATE_CHANGED", before)
        return before

    def unpack(self, raw, payload, directory):
        if hashlib.sha256(raw).hexdigest() != payload.get("archive_sha256"):
            reject("HASH_MISMATCH")
        bundle_id = payload.get("bundle_id", "")
        if not ID.fullmatch(bundle_id):
            reject("VALIDATION_FAILED")
        destination = Path(directory) / bundle_id
        destination.mkdir()
        allowed = {"bundle.json", "runtime-policy.json", "policy-approvals.json", "intent.json", "target.json"}
        seen = set()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            for item in archive.getmembers():
                name = Path(item.name).name
                if (not item.isfile() or item.name != f"{bundle_id}/{name}" or name not in allowed
                        or name in seen or item.size > 2 * 1024 * 1024):
                    reject("VALIDATION_FAILED")
                seen.add(name)
                (destination / name).write_bytes(archive.extractfile(item).read())
        if seen != allowed:
            reject("VALIDATION_FAILED")
        bundle = self.verified_bundle(destination)
        if bundle.sha256 != payload.get("bundle_sha256") or bundle.bundle_id != bundle_id:
            reject("HASH_MISMATCH")
        self.check_target(bundle)
        return destination

    def execute_bundle(self, source, existing):
        cfg = self.config
        if cfg.sudo_bundle_apply:
            command = ["sudo", "-n", sys.executable, "-B", "-m", "serving_runtime", "bundle", "apply",
                       "--bundle-root", cfg.bundle_root, "--context", cfg.context, "--namespace", cfg.namespace,
                       "--status-url", cfg.status_url or "http://127.0.0.1:8080"]
            command += ["--existing", existing] if existing else ["--bundle-dir", str(source)]
            if cfg.kubeconfig:
                command += ["--kubeconfig", cfg.kubeconfig]
            if cfg.token_env_file:
                command += ["--token-env-file", cfg.token_env_file]
            command += ["--operation-result"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=540, check=False)
            # The privileged CLI exposes only typed machine status to this caller.
            try:
                outcome = json.loads(result.stdout)
            except (ValueError, TypeError):
                reject("STATUS_UNKNOWN")
            if result.returncode:
                if outcome == {"state": "rolled_back", "code": "ROLLED_BACK"}:
                    raise RestoreReported()
                reject("STATUS_UNKNOWN")
            if outcome != {"state": "succeeded", "code": "OK"}:
                reject("STATUS_UNKNOWN")
            return
        bundle_tools.apply_bundle(str(source) if source else None, cfg.bundle_root, cfg.context, cfg.namespace,
                                  cfg.status_url or "http://127.0.0.1:8080", cfg.token_env_file, kubeconfig=cfg.kubeconfig, existing=existing)

    def sync_mode(self, name):
        """Read every scheduled daemon; partial or mixed observations fail closed."""
        result = self.run(["kubectl", "--context", self.config.context, "-n", "kube-system",
                           "get", "daemonset", "tetragon", "-o", "json"])
        if result.returncode:
            return None
        status = json.loads(result.stdout).get("status", {})
        desired = status.get("desiredNumberScheduled", 0)
        pods = self.run(["kubectl", "--context", self.config.context, "-n", "kube-system", "get", "pods",
                         "-l", "app.kubernetes.io/name=tetragon", "-o", "json"])
        if pods.returncode:
            return None
        items = json.loads(pods.stdout).get("items", [])
        if not desired or len(items) != desired or status.get("numberReady") != desired:
            return None
        nodes, modes = set(), set()
        tool = self.tool("tetragon_mode")
        for pod in items:
            node = pod.get("spec", {}).get("nodeName")
            if (not node or node in nodes or pod.get("metadata", {}).get("deletionTimestamp")
                    or not any(c.get("name") == "tetragon" and c.get("ready") is True
                               for c in pod.get("status", {}).get("containerStatuses", []))):
                return None
            nodes.add(node)
            result = self.run(["kubectl", "--context", self.config.context, "-n", "kube-system", "exec",
                               pod["metadata"]["name"], "-c", "tetragon", "--", "tetra", "tracingpolicy", "list"])
            row = tool.parse_policy_row(result.stdout, name, self.config.namespace) if not result.returncode else None
            if not row or row.get("state") != "enabled":
                return None
            modes.add(row.get("mode"))
        return next(iter(modes)) if len(modes) == 1 else None

    def sync_gateway(self, inference=False):
        self.current()
        if not inference:
            return
        # One bounded inference request; credentials and response text never escape.
        from contextlib import nullcontext
        cfg = self.config
        bundle = self.verified_bundle(Path(cfg.bundle_root) / os.readlink(Path(cfg.bundle_root) / "current"))
        policy = json.loads(Path(bundle.role_paths()["gateway_policy"]).read_text())
        models = policy.get("allowed_models", [])
        if not models:
            reject("VALIDATION_FAILED")
        token = None
        if cfg.token_env_file:
            values = [line.partition("=")[2].strip().strip("\"'")
                      for line in Path(cfg.token_env_file).read_text().splitlines()
                      if line.startswith("RUNTIME_INFERENCE_TOKEN=")]
            if len(values) != 1 or not values[0]:
                reject("VALIDATION_FAILED")
            token = values[0]
        connection = (nullcontext(cfg.status_url) if cfg.status_url else
                      bundle_tools._port_forward(cfg.context, cfg.namespace, cfg.kubeconfig, 8080))
        with connection as url:
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            request = Request(url + "/v1/completions", headers=headers, data=json.dumps({
                "model": models[0], "prompt": "ping", "max_tokens": 1, "stream": False}).encode())
            with build_opener(NoRedirect()).open(request, timeout=15) as response:
                body = response.read(1024 * 1024 + 1)
            value = json.loads(body)
            if len(body) > 1024 * 1024 or not isinstance(value.get("choices"), list) or not value["choices"]:
                reject("VALIDATION_FAILED")

    def process_sync(self, op):
        from ..enforcement.sync import SyncFailure, sync
        report = {"lease_id": op["lease_id"], "started_at": now(), "before": None, "after": None,
                  "state": "failed", "code": "VALIDATION_FAILED", "steps": [], "evidence_sha256": [],
                  "dry_run": not self.config.execute}
        try:
            before, intent = self.current()
            report["before"] = self.validate(op, before, intent)
            payload = op["payload"]
            running = False
            def guard():
                nonlocal running
                if not running:
                    self.client.request(f"/{op['id']}/result", {**report, "state": "running", "code": "OK"})
                    running = True
                try:
                    current, current_intent = self.current()
                    self.validate(op, current, current_intent)
                except Rejected as error:
                    raise SyncFailure(str(error)) from None
            def verify(inference):
                self.sync_gateway(inference)
                observed, observed_intent = self.current()
                bundle = self.verified_bundle(Path(self.config.bundle_root) / observed["bundle_id"])
                if observed["bundle_id"] != payload["bundle_id"] or bundle.sha256 != payload["bundle_sha256"]:
                    raise SyncFailure("STATE_CHANGED")
            result = sync(intent, context=self.config.context, namespace=self.config.namespace, run=self.run,
                          read_mode=self.sync_mode, verify_gateway=verify,
                          components=payload["components"], cilium_apply=payload.get("cilium_apply", False),
                          execute=self.config.execute, before_apply=guard)
            report.update(result)
            report["evidence_sha256"] = [payload["bundle_sha256"]]
            try:
                report["after"], _ = self.current()
            except Exception:
                if report["state"] in {"succeeded", "rolled_back"}:
                    report.update(state="unknown", code="STATUS_UNKNOWN")
        except Rejected as error:
            report["code"] = str(error)
            if error.reason_code:
                report["reason_code"] = error.reason_code
        except Exception:
            pass
        if not report["steps"]:
            report["steps"] = [{"stage": "validation", "state": "failed"}] + [
                {"stage": stage, "state": "skipped"} for stage in ("render", "dry-run", "apply", "verify")]
        report["finished_at"] = now()
        self.client.request(f"/{op['id']}/result", report)
        return report

    def process(self, op):
        if op.get("kind") == "enforcement.apply":
            return self.process_sync(op)
        started = now()
        report = {"lease_id": op["lease_id"], "state": "failed", "code": "VALIDATION_FAILED", "started_at": started,
                  "dry_run": not self.config.execute, "before": None, "after": None, "steps": [], "evidence_sha256": []}
        executing = False
        try:
            before, intent = self.current()
            report["before"] = before
            payload = op["payload"]
            with tempfile.TemporaryDirectory(prefix="runtime-agent-") as temporary:
                source, existing = None, None
                if op["kind"] == "bundle.apply":
                    raw = self.client.request(f"/{op['id']}/bundle", binary=True)
                    source = self.unpack(raw, payload, temporary)
                    report["evidence_sha256"].extend([payload["archive_sha256"], payload["bundle_sha256"]])
                elif op["kind"] == "bundle.switch":
                    existing = payload.get("bundle_id", "")
                    if not ID.fullmatch(existing):
                        reject("VALIDATION_FAILED")
                    bundle = self.verified_bundle(Path(self.config.bundle_root) / existing)
                    self.check_target(bundle)
                    report["evidence_sha256"].append(bundle.sha256)
                # Download/verification may take time: reload current immediately before validation.
                before, intent = self.current()
                before = self.validate(op, before, intent)
                if op["kind"] in {"bundle.apply", "bundle.switch"}:
                    bundle_tools.apply_bundle(str(source) if source else None, self.config.bundle_root,
                        self.config.context, self.config.namespace, self.config.status_url or "http://127.0.0.1:8080", self.config.token_env_file,
                        kubeconfig=self.config.kubeconfig, existing=existing, dry_run=True)
                report["before"] = before
                report["steps"].append({"stage": "validation", "state": "passed"})
                if op["kind"] == "tetragon.mode" and payload["mode"] == "enforce":
                    report["evidence_sha256"].extend(evaluate(self.snapshot(), payload["policy_name"], intent, self.review(), self.monitor_seconds)["evidence_sha256"])
                if not self.config.execute:
                    report.update(state="checked", code="DRY_RUN", after=before)
                else:
                    self.client.request(f"/{op['id']}/result", {**report, "state": "running", "code": "OK"})
                    # Renew neither lease nor request. A delayed/ambiguous operation is never rerun.
                    before2, intent2 = self.current()
                    self.validate(op, before2, intent2)
                    executing = True
                    if op["kind"] == "tetragon.mode":
                        result = self.tool("tetragon_mode").switch_mode(self.config.context, self.config.namespace,
                                  payload["policy_name"], payload["mode"], run=self.run,
                                  history_path=self.config.history_file)
                        report["steps"].append({"stage": "set-mode", "state": "passed" if result["ok"] else "failed"})
                        if result["ok"]:
                            report.update(state="succeeded", code="OK")
                        elif result.get("rollback_ok"):
                            report.update(state="rolled_back", code="ROLLED_BACK")
                            report["steps"].append({"stage": "rollback", "state": "passed"})
                        else:
                            report.update(state="unknown", code="STATUS_UNKNOWN")
                        report["after"] = self.mode_state(payload["policy_name"], before)
                    else:
                        self.execute_bundle(source, existing)
                        report["after"], _ = self.current()
                        report.update(state="succeeded", code="OK")
                        report["steps"].extend({"stage": stage, "state": "passed"} for stage in ("link", "restart", "status"))
        except Rejected as error:
            if error.observed is not None:
                report["before"] = error.observed
            report.update(state="unknown" if executing else "failed", code="STATUS_UNKNOWN" if executing else str(error))
            if error.reason_code:
                report["reason_code"] = error.reason_code
        except bundle_tools.StatusUnavailable:
            report.update(state="unknown", code="STATUS_UNKNOWN")
            report["reason_code"] = "STATUS_UNREACHABLE"
        except RuntimeError as error:
            # apply_bundle explicitly distinguishes rollback and unverified restoration.
            restored = isinstance(error, RestoreReported) or "이전 링크 복원 및 재시작 완료" in str(error)
            if restored:
                try:
                    after, _ = self.current()
                    restored = after["bundle_id"] == report["before"]["bundle_id"]
                    report["after"] = after
                except Exception:
                    restored = False
            report.update(state="rolled_back" if restored else "unknown", code="ROLLED_BACK" if restored else "STATUS_UNKNOWN")
            report["steps"].append({"stage": "rollback", "state": "passed" if restored else "unknown"})
        except Exception:
            report.update(state="unknown" if executing else "failed", code="STATUS_UNKNOWN" if executing else "VALIDATION_FAILED")
        if not any(s["stage"] == "validation" for s in report["steps"]):
            report["steps"].append({"stage": "validation", "state": "failed"})
        stages = ("set-mode",) if op.get("kind") == "tetragon.mode" else ("link", "restart", "status")
        for stage in stages:
            if not any(s["stage"] == stage for s in report["steps"]):
                report["steps"].append({"stage": stage, "state": "unknown" if executing else "skipped"})
        report["finished_at"] = now()
        # No subprocess stdout, exception text, addresses, kubeconfig, tokens or names.
        self.client.request(f"/{op['id']}/result", report)
        return report

    def available_bundles(self):
        """Advertise verified same-target IDs and hashes only; never local paths."""
        bundles = []
        try:
            paths = sorted(Path(self.config.bundle_root).iterdir())
        except OSError:
            return bundles
        for path in paths:
            if not ID.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
                continue
            try:
                bundle = self.verified_bundle(path)
                self.check_target(bundle)
                bundles.append({"bundle_id": bundle.bundle_id, "sha256": bundle.sha256})
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if len(bundles) >= 256:
                break
        return bundles

    def once(self):
        listing = self.client.request("?" + urlencode({"server": self.config.server}))
        self.monitor_seconds = max(self.config.monitor_seconds, listing.get("monitor_seconds", 86400))
        current, policies = None, []
        try:
            current, intent = self.current()
            policies = self.readiness(current, intent)
        except Exception:
            pass
        self.client.request("/agent-state", {"server": self.config.server, "current": current, "policies": policies,
                                             "bundles": self.available_bundles() if current else []})
        for candidate in listing["items"]:
            if candidate["state"] == "approved":
                op = self.client.request(f"/{candidate['id']}/claim", {})
                return self.process(op)
        return None


def add_arguments(parser):
    for flag in ("platform-url", "agent-token-file", "server", "bundle-root", "context", "namespace", "lock-file"):
        parser.add_argument("--" + flag, required=True)
    for flag in ("kubeconfig", "token-env-file", "state-file", "review-file", "history-file", "ca-file"):
        parser.add_argument("--" + flag)
    parser.add_argument("--tools-dir", default=Config.tools_dir)
    parser.add_argument("--status-url", default=Config.status_url,
                        help="로컬 Gateway 직접 조회(명시할 때만); 기본은 deployment 새 port-forward")
    parser.add_argument("--monitor-seconds", type=int, default=86400)
    parser.add_argument("--interval", type=float, default=10)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="기본값: 실행 없이 검증 결과 보고")
    group.add_argument("--execute", action="store_true", help="검증 후 실제 서버 변경")
    parser.add_argument("--sudo-bundle-apply", action="store_true")
    parser.add_argument("--lab-self-approval", action="store_true")
    parser.add_argument("--once", action="store_true")


def main(args):
    config = Config(**{k: getattr(args, k) for k in Config.__dataclass_fields__})
    try:
        client = PlatformClient(args.platform_url, args.agent_token_file, args.ca_file)
        agent = Agent(client, config)
        with locked(args.lock_file):
            while True:
                try:
                    result = agent.once()
                    if result:
                        print(json.dumps({k: result[k] for k in ("state", "code", "dry_run")}))
                except Exception:
                    print("agent: polling/report unavailable; no automatic replay", file=sys.stderr)
                    if args.once:
                        return 1
                if args.once:
                    return 0
                time.sleep(max(1, args.interval))
    except (OSError, ValueError):
        print("agent: configuration or lock unavailable", file=sys.stderr)
        return 1
