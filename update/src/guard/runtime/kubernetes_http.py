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

from guard.models import canonical_json, sha256_hex
from .bounded import capture_pipes


PROFILE = "kubernetes-owned-sampling-http-v1"
PROBE_SOURCE_SHA256 = "ffa7d7f44236818ae0842344f88f2ec01c2f75ed5b4a0a6032cd6fb38a36587d"
from .kubernetes_binding import (NAME as _NAME, UID as _UID, PIN as _PIN, FIELDS as _FIELDS,
                                 validate_container_binding, verify_owned_pod)


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
            validate_container_binding(k[role])
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
        # This profile admits only its pinned HTTP Pod endpoint below. Preserve
        # its published source/fixture bytes when the general probe evolves.
        probe_source = (Path(__file__).parent / "assets/http_probe_v1.py.txt").read_text(encoding="utf-8")
        if sha256_hex(probe_source.encode("utf-8")) != PROBE_SOURCE_SHA256:
            raise KubernetesBindingError()
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
            try:
                verify_owned_pod(pod, ns, expected, self.binding["run_id"])
            except ValueError:
                raise KubernetesBindingError() from None

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
