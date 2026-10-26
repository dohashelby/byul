"""One owned Pod-to-Pod HTTP path. No discovery, workload mutation or lifecycle.

kubectl reads exact namespaces/Pods and execs the existing stdlib HTTP probe in
an explicitly selected Python container. Raw API objects, bodies, stderr and
credentials are transient; only the allowlisted binding is available as evidence.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import ipaddress
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit

from guard.executors import http_probe
from guard.models import canonical_json, sha256_hex
from .bounded import capture_pipes


PROFILE = "kubernetes-owned-sampling-http-v1"
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
_UID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_PIN = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}\Z")
_FIELDS = ("namespace", "namespace_uid", "pod", "pod_uid", "container", "container_id",
           "image", "image_id", "restart_count", "node", "pod_ip", "started_at")


class KubernetesBindingError(RuntimeError):
    def __init__(self):
        super().__init__("KUBERNETES_HTTP_BINDING_UNAVAILABLE")


def _validate(target, env):
    """Explicit immutable identities; reject before kubectl or HTTP dispatch."""
    try:
        k = target["kubernetes"]
        if (type(k) is not dict or set(k) != {"context", "run_id", "engine", "probe"}
                or type(k["context"]) is not str or not re.fullmatch(r"[A-Za-z0-9._:@/-]{1,200}", k["context"])
                or type(k["run_id"]) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", k["run_id"])
                or type(target.get("lab")) is not bool or target.get("topology") != "kubernetes-owned-direct"):
            raise ValueError
        for role in ("engine", "probe"):
            item = k[role]
            if type(item) is not dict or set(item) != set(_FIELDS):
                raise ValueError
            for field in ("namespace", "pod", "container", "node"):
                if type(item[field]) is not str or not _NAME.fullmatch(item[field]):
                    raise ValueError
            for field in ("namespace_uid", "pod_uid"):
                if type(item[field]) is not str or not _UID.fullmatch(item[field]):
                    raise ValueError
            if (not _PIN.fullmatch(item["image"])
                    or not re.fullmatch(r"containerd://[0-9a-f]{64}", item["container_id"])
                    or type(item["image_id"]) is not str or len(item["image_id"]) > 300
                    or not re.fullmatch(r"(?:sha256:[0-9a-f]{64}|(?:docker-pullable://)?[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64})", item["image_id"])
                    or type(item["restart_count"]) is not int or item["restart_count"] < 0
                    or datetime.fromisoformat(item["started_at"].replace("Z", "+00:00")).tzinfo is None
                    or ipaddress.ip_address(item["pod_ip"]).version != 4):
                raise ValueError
        endpoint = urlsplit(target["engine"]["trusted_url"])
        if (endpoint.scheme != "http" or endpoint.hostname != k["engine"]["pod_ip"]
                or endpoint.username is not None or endpoint.password is not None
                or endpoint.path or endpoint.query or endpoint.fragment or not endpoint.port
                or target["engine"]["image_digest"] != env.engine.image_digest
                or k["engine"]["image"] != env.engine.image_digest
                or k["engine"]["pod_uid"] == k["probe"]["pod_uid"]
                or k["engine"]["container_id"] == k["probe"]["container_id"]
                or k["engine"]["pod_ip"] == k["probe"]["pod_ip"]
                or target["origins"]["trusted"]["network"] != "kubernetes-probe"):
            raise ValueError
        return deepcopy(k), endpoint
    except (KeyError, TypeError, ValueError, AttributeError):
        raise KubernetesBindingError() from None


class KubernetesHTTP:
    def __init__(self, target, env, *, duration, binary="kubectl", runner=None):
        self.binding, self.endpoint = _validate(target, env)
        if (type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 300
                or type(binary) is not str or not binary.strip()):
            raise KubernetesBindingError()
        self.deadline = time.monotonic() + duration
        self.binary = binary
        self.runner = runner or capture_pipes
        self.injected = runner is not None
        probe_source = Path(http_probe.__file__).read_text(encoding="utf-8")
        # The parent owns kubectl, not the remote exec process. A daemon timer
        # bounds this probe itself, including stdin and a slowly dripping body.
        # It terminates only this Python process, never a Pod or another PID.
        self.source = ("import math,os,sys,threading\n"
                       "limit=float(sys.argv[1])\n"
                       "if not math.isfinite(limit) or not 0 < limit <= 30: sys.exit(124)\n"
                       "watchdog=threading.Timer(limit,lambda:os._exit(124))\n"
                       "watchdog.daemon=True\nwatchdog.start()\n"
                       + "exec(compile(" + repr(probe_source) + ",'guard-http-probe','exec'),{'__name__':'__main__'})\n")
        self.source_sha256 = sha256_hex(self.source.encode("utf-8"))
        self.rows = []
        self.failed = False
        self.lock = threading.Lock()

    def _call(self, args, *, payload=None, cap=262144):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise KubernetesBindingError()
        argv = [self.binary, f"--context={self.binding['context']}", f"--request-timeout={remaining:.3f}s", *args]
        try:
            result = self.runner(argv, timeout=remaining, env_extra={}, max_stdout_bytes=cap,
                                 max_stderr_bytes=4096, **({"input_bytes": payload} if payload is not None else {}))
            if (result["returncode"] != 0 or result["capture"]["complete"] is not True
                    or self.deadline - time.monotonic() <= 0):
                raise KubernetesBindingError()
            return json.loads(result["stdout_bytes"])
        except Exception:
            raise KubernetesBindingError() from None

    def _verify(self):
        # Namespace UIDs bind a named context to the selected cluster generation.
        namespaces = sorted({self.binding[role]["namespace"] for role in ("engine", "probe")})
        result = self._call(["get", "namespaces", *namespaces, "-o", "json"])
        items = result["items"] if result.get("kind") == "List" else [result]
        actual = {x["metadata"]["name"]: x["metadata"] for x in items}
        if len(actual) != len(items) or set(actual) != set(namespaces):
            raise KubernetesBindingError()
        for role in ("engine", "probe"):
            expected = self.binding[role]
            ns = actual[expected["namespace"]]
            if ns.get("uid") != expected["namespace_uid"] or ns.get("deletionTimestamp"):
                raise KubernetesBindingError()
            pod = self._call([f"--namespace={expected['namespace']}", "get", "pod", expected["pod"], "-o", "json"])
            meta, spec, status = pod["metadata"], pod["spec"], pod["status"]
            containers = [x for x in spec["containers"] if x["name"] == expected["container"]]
            states = [x for x in status["containerStatuses"] if x["name"] == expected["container"]]
            if len(containers) != 1 or len(states) != 1:
                raise KubernetesBindingError()
            container, state = containers[0], states[0]
            if (meta.get("name") != expected["pod"] or meta.get("namespace") != expected["namespace"]
                    or meta.get("uid") != expected["pod_uid"] or meta.get("deletionTimestamp")
                    or meta.get("labels", {}).get("guard.run") != self.binding["run_id"]
                    or spec.get("nodeName") != expected["node"]
                    or any(spec.get(x, False) is not False for x in ("hostNetwork", "hostPID", "hostIPC"))
                    or container.get("image") != expected["image"]
                    or status.get("phase") != "Running" or status.get("podIP") != expected["pod_ip"]
                    or not any(x.get("type") == "Ready" and x.get("status") == "True" for x in status.get("conditions", []))
                    or state.get("ready") is not True or state.get("containerID") != expected["container_id"]
                    or state.get("imageID") != expected["image_id"]
                    or type(state.get("restartCount")) is not int or state["restartCount"] != expected["restart_count"]
                    or state.get("state", {}).get("running", {}).get("startedAt") != expected["started_at"]):
                raise KubernetesBindingError()

    def __call__(self, network, requests, timeout):
        # No shared serialization lock: REQ-10's two invocations stay concurrent.
        with self.lock:
            if self.failed:
                raise KubernetesBindingError()
        rid = None
        try:
            if (network != "kubernetes-probe" or type(requests) is not list or len(requests) != 1
                    or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30):
                raise KubernetesBindingError()
            request = requests[0]
            rid = request["id"]
            url = urlsplit(request["url"])
            if (type(rid) is not str or not re.fullmatch(r"sampling-[a-z_:-]{1,80}", rid)
                    or url.scheme != self.endpoint.scheme or url.netloc != self.endpoint.netloc
                    or url.query or url.fragment
                    or (request["method"], url.path) not in {("GET", "/health"), ("POST", "/v1/completions")}):
                raise KubernetesBindingError()
            self._verify()
            payload = json.dumps({"requests": requests, "timeout": min(timeout, self.deadline - time.monotonic())},
                                 ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(payload) > 65536:
                raise KubernetesBindingError()
            probe = self.binding["probe"]
            source_budget = min(timeout, self.deadline - time.monotonic())
            if source_budget <= 0:
                raise KubernetesBindingError()
            raw = self._call([f"--namespace={probe['namespace']}", "exec", "-i", probe["pod"],
                              f"--container={probe['container']}", "--", "python3", "-c", self.source, str(source_budget)],
                             payload=payload, cap=2 * 1024**2)
            self._verify()
            if type(raw) is not list or len(raw) != 1 or type(raw[0]) is not dict or raw[0].get("id") != rid:
                raise KubernetesBindingError()
            value = raw[0]
            outcome = value.get("outcome")
            if not (type(outcome) is int and 100 <= outcome <= 599):
                outcome = "probe_unavailable"
            # Body exists only for the existing structural validator. Ctx.record
            # cannot persist it, and preview/detail/headers/URL never cross here.
            safe = {"id": rid, "method": request["method"], "outcome": outcome,
                    "body": value.get("body") if type(value.get("body")) is str else None}
            for name in ("body_bytes", "elapsed_ms"):
                if type(value.get(name)) is int and value[name] >= 0:
                    safe[name] = value[name]
            with self.lock:
                self.rows.append({"id": rid, "binding_verified_before_after": True, "outcome": outcome})
            return [safe]
        except Exception:
            with self.lock:
                self.failed = True
                self.rows.append({"id": rid if type(rid) is str and re.fullmatch(r"sampling-[a-z_:-]{1,80}", rid) else None,
                                  "binding_verified_before_after": False, "diagnostic": "KUBERNETES_HTTP_BINDING_UNAVAILABLE"})
            raise KubernetesBindingError() from None

    def report(self):
        with self.lock:
            return {"profile": PROFILE, "binding": deepcopy(self.binding),
                    "binding_sha256": sha256_hex(canonical_json(self.binding)),
                    "probe_source_sha256": self.source_sha256,
                    "requests": sorted(deepcopy(self.rows), key=lambda row: row["id"] or ""),
                    "collection_gap": self.failed,
                    "limitations": ["Explicit operator selection and guard.run label are not an approval signature.",
                                    "Pod spec digest and exact runtime imageID are bound; manifest-index ancestry is not verified.",
                                    "No GPU execution, runner consumption, resource/log isolation or deployed caller path proof."]}
