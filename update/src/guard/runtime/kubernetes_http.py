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

from guard.models import canonical_json, sha256_hex, utcnow
from .bounded import capture_pipes, strict_json


PROFILE = "kubernetes-owned-sampling-http-v1"
REQUEST_BINDING_PROFILE = "kubernetes-owned-request-binding-http-v1"
VALIDATION_ERROR_PROFILE = "kubernetes-owned-validation-error-http-v1"
MODEL_AUTHORIZATION_PROFILE = "kubernetes-owned-model-authorization-http-v1"
ONLINE_INPUT_PROFILE = "kubernetes-owned-online-input-http-v1"
_BINDING_PROFILES = frozenset({REQUEST_BINDING_PROFILE, VALIDATION_ERROR_PROFILE, MODEL_AUTHORIZATION_PROFILE, ONLINE_INPUT_PROFILE})
PROBE_SOURCE_SHA256 = "ffa7d7f44236818ae0842344f88f2ec01c2f75ed5b4a0a6032cd6fb38a36587d"
from .kubernetes_binding import (NAME as _NAME, UID as _UID, PIN as _PIN, FIELDS as _FIELDS,
                                 validate_container_binding, verify_owned_pod, project_owned_snapshot)


BINDING_PROBE_SHA256 = "6ffd129cd855170a1a2fefb496efef4abfcfd7928d2dc6795c22344bd152a765"
TEMPLATE_READER_SHA256 = "badf4bc4b01fc5bacbba103a606cac994e0921be28ef054116bd34dfd4b1c74b"


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
    def __init__(self, target, env, *, duration, binary="kubectl", runner=None, profile=PROFILE, clock=None):
        if profile not in {PROFILE, *_BINDING_PROFILES}:
            raise KubernetesBindingError()
        self.profile = profile
        if profile in {VALIDATION_ERROR_PROFILE, MODEL_AUTHORIZATION_PROFILE, ONLINE_INPUT_PROFILE} and (env.engine.name != "vllm" or env.engine.version != "0.31.0"):
            raise KubernetesBindingError()
        if profile in _BINDING_PROFILES:
            try:
                mapping=target["model_mapping"]
                if type(mapping) is dict and mapping.get("schema")=="guard.model-request-mapping/1":
                    from guard.collectors.model_mapping import validate_model_mapping
                    validate_model_mapping(env,mapping)
                    self.api_model=mapping["served_model_name"]
                else:
                    if (type(mapping) is not dict or set(mapping)!={"repository_id","revision","api_model"}
                            or mapping["repository_id"]!=env.model.id or mapping["revision"]!=env.model.revision
                            or type(mapping["api_model"]) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}",mapping["api_model"])):
                        raise ValueError
                    self.api_model=mapping["api_model"]
                if (target.get("schema")!="guard-kubernetes-request-binding-target/1" or not env.hash_ok()
                        or target.get("environment_ref")!=env.ref().model_dump(mode="json")):
                    raise ValueError
                self.model_mapping=deepcopy(mapping)
            except (KeyError,TypeError,ValueError,AttributeError):
                raise KubernetesBindingError() from None
        self.binding, self.endpoint = _validate(target, env)
        self.target_sha256=sha256_hex(canonical_json(target))
        self.environment_ref=(deepcopy(target["environment_ref"]) if profile in _BINDING_PROFILES else None)
        if (type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 300
                or type(binary) is not str or not binary.strip()):
            raise KubernetesBindingError()
        self.clock = clock or time.monotonic
        self.deadline = self.clock() + duration
        self.binary = binary
        self.runner = runner or capture_pipes
        self.injected = runner is not None
        self.process_observation_origin = ('EXAMPLE' if self.injected or target['lab']
            or env.data_origin.value == 'EXAMPLE' else 'MEASURED')
        if profile == ONLINE_INPUT_PROFILE and self.process_observation_origin != 'EXAMPLE':
            self.process_observation_origin = env.data_origin.value
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
        if profile in _BINDING_PROFILES:
            probe_source=(Path(__file__).parents[1]/"executors/stream_probe.py").read_text(encoding="utf-8")
            if sha256_hex(probe_source.encode())!=BINDING_PROBE_SHA256:
                raise KubernetesBindingError()
            self.source=("import math,os,sys,threading\nlimit=float(sys.argv[1])\n"
                "if not math.isfinite(limit) or not 0 < limit <= 120: sys.exit(124)\n"
                "watchdog=threading.Timer(limit,lambda:os._exit(124))\nwatchdog.daemon=True\nwatchdog.start()\n"
                + "exec(compile("+repr(probe_source)+",'guard-stream-probe','exec'),{'__name__':'__main__'})\n")
            self.source_sha256=sha256_hex(self.source.encode())
        self.model_snapshots = []  # Transient allowlisted pair for the existing A-2 consumer.
        self.snapshot_reads = []
        self.rows = []
        self.failed = False
        self.cleanup_errors = []
        self.lock = threading.Lock()
        self._validation_error_token = None  # transient; never part of report/evidence
        self._validation_error_cases = set()
        self._model_authorization_token = None
        self._model_authorization_cases = set()
        self._model_normal_credential = None
        self._online_dispatch = threading.Lock()
        self._online_token = None
        self._online_cases = set()
        self._online_pending = None

    def _call(self, args, *, payload=None, cap=262144, budget=None, allow_empty=False):
        remaining = self.deadline - self.clock()
        if budget is not None:remaining=min(remaining,budget)
        if remaining <= 0:
            raise KubernetesBindingError()
        argv = [self.binary, f"--context={self.binding['context']}", f"--request-timeout={remaining:.3f}s", *args]
        try:
            result = self.runner(argv, timeout=remaining, env_extra={}, max_stdout_bytes=cap,
                                 max_stderr_bytes=4096, **({"input_bytes": payload} if payload is not None else {}))
            if self.profile in _BINDING_PROFILES and (result.get("capture",{}).get("pipes_closed") is False
                    or "child_reap" in result.get("capture",{}).get("read_errors",[])):
                with self.lock:
                    if "KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED" not in self.cleanup_errors:
                        self.cleanup_errors.append("KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED")
            if (result["returncode"] != 0 or result["capture"]["complete"] is not True
                    or self.deadline - self.clock() <= 0):
                raise KubernetesBindingError()
            if allow_empty and result["stdout_bytes"]==b"":return None
            return strict_json(result["stdout_bytes"],limit=cap) if self.profile in _BINDING_PROFILES else json.loads(result["stdout_bytes"])
        except Exception:
            raise KubernetesBindingError() from None

    def _verify(self, *, require_ready=True, capture_snapshot=False):
        # Namespace UIDs bind a named context to the selected cluster generation.
        namespaces = sorted({self.binding[role]["namespace"] for role in ("engine", "probe")})
        result = self._call(["get", "namespaces", *namespaces, "-o", "json"])
        items = result["items"] if result.get("kind") == "List" else [result]
        mapped=self.profile in _BINDING_PROFILES and self.model_mapping.get("schema")=="guard.model-request-mapping/1"
        snapshot_required=mapped or capture_snapshot
        if snapshot_required and any(x.get("kind")!="Namespace" for x in items):raise KubernetesBindingError()
        actual = {x["metadata"]["name"]: x["metadata"] for x in items}
        if len(actual) != len(items) or set(actual) != set(namespaces):
            raise KubernetesBindingError()
        pods={}
        for role in ("engine", "probe"):
            expected = self.binding[role]
            ns = actual[expected["namespace"]]
            if ns.get("uid") != expected["namespace_uid"] or ns.get("deletionTimestamp"):
                raise KubernetesBindingError()
            pod = self._call([f"--namespace={expected['namespace']}", "get", "pod", expected["pod"], "-o", "json"])
            try:
                if snapshot_required and pod.get("kind")!="Pod":raise ValueError
                verify_owned_pod(pod, ns, expected, self.binding["run_id"],require_ready=require_ready)
                pods[role]=pod
            except ValueError:
                raise KubernetesBindingError() from None

        if snapshot_required:
            snapshot=project_owned_snapshot(pods["engine"],
                next(x for x in items if x["metadata"]["name"]==self.binding["engine"]["namespace"]),
                self.binding["engine"],self.binding["run_id"],require_ready=require_ready)
            self.model_snapshots=([self.model_snapshots[0],snapshot] if self.model_snapshots else [snapshot])
        return pods

    def snapshot(self):
        """Existing bound API reads only; no HTTP/exec or node image relation."""
        row={"profile":"kubernetes-owned-pod-api-read-v1", "started_at":utcnow().isoformat(),
             "target_sha256":self.target_sha256,
             "source_file_sha256":None,
             "collected":False,"diagnostic_codes":[]}
        try:
            row["source_file_sha256"]=sha256_hex(Path(__file__).read_bytes())
            with self.lock:
                if self.failed:raise KubernetesBindingError()
            self._verify(capture_snapshot=True)
            value=deepcopy(self.model_snapshots[-1])
            row.update(collected=True,snapshot_sha256=sha256_hex(canonical_json(value)))
            return value
        except Exception:
            with self.lock:self.failed=True
            row["diagnostic_codes"]=["KUBERNETES_HTTP_BINDING_UNAVAILABLE"]
            raise KubernetesBindingError() from None
        finally:
            row["finished_at"]=utcnow().isoformat()
            with self.lock:self.snapshot_reads.append(row)

    def __call__(self, network, requests, timeout):
        if self.profile == ONLINE_INPUT_PROFILE:
            # Only this profile is serial; legacy sampling/REQ10 concurrency stays unchanged.
            with self._online_dispatch:
                return self._binding_requests(network, requests, timeout)
        if self.profile in _BINDING_PROFILES:
            return self._binding_requests(network,requests,timeout)
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
            payload = json.dumps({"requests": requests, "timeout": min(timeout, self.deadline - self.clock())},
                                 ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(payload) > 65536:
                raise KubernetesBindingError()
            probe = self.binding["probe"]
            source_budget = min(timeout, self.deadline - self.clock())
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

    def _binding_requests(self, network, requests, timeout):
        ids=[]
        label=re.compile(r"(?:tokenize-[AB]|before|after|(?:json|sse)-(?:generated|distinct_body|collision_body|collision_header)-[AB])\Z")
        try:
            with self.lock:
                if self.failed:raise KubernetesBindingError()
            if (network!="kubernetes-probe" or type(requests) is not list or not 1<=len(requests)<=2
                    or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=120):
                raise KubernetesBindingError()
            prepared=[]
            for request in requests:
                if self.profile == VALIDATION_ERROR_PROFILE:
                    if len(requests) != 1:
                        raise KubernetesBindingError()
                    self._validation_error_request(request)
                elif self.profile == MODEL_AUTHORIZATION_PROFILE:
                    if len(requests) != 1:
                        raise KubernetesBindingError()
                    self._model_authorization_request(request)
                elif self.profile == ONLINE_INPUT_PROFILE:
                    if len(requests) != 1:
                        raise KubernetesBindingError()
                    self._online_input_request(request, timeout)
                elif type(request) is not dict or type(request.get("id")) is not str or not label.fullmatch(request["id"]):
                    raise KubernetesBindingError()
                ids.append(request["id"])
                value=deepcopy(request)
                if value.pop("want_body",True) is not True:raise KubernetesBindingError()
                url=urlsplit(value.get("url",""));mode=value.get("mode","json")
                if (set(value)-{"id","mode","method","url","headers","body","read_delay_ms"}
                        or value.get("method")!="POST" or mode not in {"json","sse"}
                        or url.scheme!=self.endpoint.scheme or url.netloc!=self.endpoint.netloc or url.query or url.fragment
                        or url.path not in {"/tokenize","/v1/chat/completions"}
                        or url.path=="/tokenize" and mode!="json"
                        or type(value.get("body")) is not dict or value["body"].get("model")!=self.api_model):
                    raise KubernetesBindingError()
                value.setdefault("mode","json");prepared.append(value)
            if len(set(ids))!=len(ids):raise KubernetesBindingError()
            self._verify()
            remaining=min(float(timeout),self.deadline-self.clock())
            if remaining<=0:raise KubernetesBindingError()
            payload=json.dumps({"requests":prepared,"timeout":remaining},ensure_ascii=False,allow_nan=False).encode()
            if len(payload)>128*1024:raise KubernetesBindingError()
            probe=self.binding["probe"]
            raw=self._call([f"--namespace={probe['namespace']}","exec","-i",probe["pod"],
                f"--container={probe['container']}","--","python3","-c",self.source,str(remaining)],payload=payload,cap=2*1024**2,budget=remaining)
            self._verify()
            if (type(raw) is not list or len(raw)!=len(ids) or any(type(row) is not dict for row in raw)
                    or len({row.get("id") for row in raw})!=len(ids) or {row.get("id") for row in raw}!=set(ids)):
                raise KubernetesBindingError()
            keys={"id","mode","method","outcome","body","body_bytes","body_sha256","body_truncated",
                  "utf8_valid","capture_complete","eof","terminal_seen","events","content_type","connection_id",
                  "dispatch_ms","request_sent_ms","headers_ms","end_ms","elapsed_ms","timing_basis"}
            by_id={row["id"]:row for row in raw};results=[]
            for request in prepared:
                row=by_id[request["id"]];outcome=row.get("outcome")
                if not (type(outcome) is int and 100<=outcome<=599 or type(outcome) is str and outcome in
                        {"refused","timeout","unreachable","dns_error","error","probe_error"}):
                    raise KubernetesBindingError()
                if row.get("body") is not None and (type(row["body"]) is not str or len(row["body"].encode("utf-8"))>128*1024):
                    raise KubernetesBindingError()
                results.append({key:row[key] for key in keys if key in row})
            with self.lock:
                self.rows.extend({"id":row["id"],"binding_verified_before_after":True,"outcome":row["outcome"],
                    "capture_complete":row.get("capture_complete") is True} for row in results)
            return results
        except Exception:
            with self.lock:
                self.failed=True
                self.rows.extend({"id":rid,"binding_verified_before_after":False,"diagnostic":"KUBERNETES_HTTP_BINDING_UNAVAILABLE"} for rid in ids or [None])
            raise KubernetesBindingError() from None


    def _validation_error_request(self, request):
        """Closed REQ13 wire contract; no arbitrary prompt/error body/header."""
        try:
            if (type(request) is not dict or set(request) - {"id", "mode", "method", "url", "headers", "body", "want_body"}
                    or request.get("mode", "json") != "json" or request.get("method") != "POST"
                    or request.get("want_body", True) is not True):
                raise ValueError
            slot = {"req13-error-before": "before", "req13-error-primary": "primary",
                    "req13-error-after": "after"}[request["id"]]
            headers = request["headers"]
            if type(headers) is not dict or set(headers) != {"X-Request-Id"} or type(headers["X-Request-Id"]) is not str:
                raise ValueError
            match = re.fullmatch(r"byul-req13-error-([0-9a-f]{32})-" + slot, headers["X-Request-Id"])
            if match is None:
                raise ValueError
            token = match[1]
            body = ({"model": self.api_model, "messages": [{"role": "user", "content": "hello"}],
                     "max_tokens": "b2error-" + token[:12], "stream": False} if slot == "primary" else
                    {"model": self.api_model, "prompt": "hello", "add_special_tokens": False, "return_token_strs": False})
            path = "/v1/chat/completions" if slot == "primary" else "/tokenize"
            url = urlsplit(request.get("url", ""))
            if (type(request.get("body")) is not dict or canonical_json(request["body"]) != canonical_json(body)
                    or url.path != path):
                raise ValueError
            with self.lock:
                if (slot in self._validation_error_cases or "after" in self._validation_error_cases
                        or slot != "before" and "before" not in self._validation_error_cases
                        or self._validation_error_token is not None and token != self._validation_error_token):
                    raise ValueError
                self._validation_error_token = token
                self._validation_error_cases.add(slot)
        except (KeyError, TypeError, ValueError):
            raise KubernetesBindingError() from None


    def _model_authorization_request(self, request):
        """One owned credential matrix against the exact selected base-model alias."""
        try:
            if (type(request) is not dict or set(request) - {"id", "mode", "method", "url", "headers", "body", "want_body"}
                    or request.get("mode", "json") != "json" or request.get("method") != "POST"
                    or request.get("want_body", True) is not True):
                raise ValueError
            slot = {"req05-model-before": "before", "req05-model-challenge": "challenge",
                    "req05-model-after": "after"}[request["id"]]
            headers = request["headers"]
            if (type(headers) is not dict or set(headers) != {"X-Request-Id", "Authorization"}
                    or type(headers["X-Request-Id"]) is not str or type(headers["Authorization"]) is not str
                    or len(headers["Authorization"]) > 4103
                    or re.fullmatch(r"Bearer [A-Za-z0-9._~+/-]{1,4096}={0,2}", headers["Authorization"]) is None):
                raise ValueError
            match = re.fullmatch(r"byul-req05-model-([0-9a-f]{32})-" + slot, headers["X-Request-Id"])
            body = {"model": self.api_model, "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 1, "stream": False}
            if (match is None or type(request.get("body")) is not dict
                    or canonical_json(request["body"]) != canonical_json(body)
                    or urlsplit(request.get("url", "")).path != "/v1/chat/completions"):
                raise ValueError
            with self.lock:
                if (slot in self._model_authorization_cases or "after" in self._model_authorization_cases
                        or slot != "before" and "before" not in self._model_authorization_cases
                        or self._model_authorization_token is not None and match[1] != self._model_authorization_token
                        or slot == "after" and headers["Authorization"] != self._model_normal_credential
                        or slot == "challenge" and headers["Authorization"] == self._model_normal_credential):
                    raise ValueError
                self._model_authorization_token = match[1]
                self._model_authorization_cases.add(slot)
                if slot == "before":
                    self._model_normal_credential = headers["Authorization"]  # transient, never report/config
        except (KeyError, TypeError, ValueError):
            raise KubernetesBindingError() from None


    def _online_input_request(self, request, timeout):
        """Closed text/JSON Online envelope; native admission remains a separate fact."""
        try:
            if (type(request) is not dict or set(request) - {"id", "mode", "method", "url", "headers", "body", "want_body", "read_delay_ms"}
                    or request.get("method") != "POST" or request.get("mode", "json") != "json"
                    or request.get("want_body", True) is not True
                    or type(request.get("read_delay_ms", 0)) is not int or request.get("read_delay_ms", 0) != 0
                    or timeout > 30):
                raise ValueError
            label = request.get("id")
            if type(label) is not str:
                raise ValueError
            match = re.fullmatch(r"online-([a-z][a-z0-9-]{0,59})-(primary|after)", label)
            if match is None:
                raise ValueError
            case, phase = match.groups()
            headers = request.get("headers")
            if type(headers) is not dict or set(headers) != {"X-Request-Id"} or type(headers["X-Request-Id"]) is not str:
                raise ValueError
            rid = re.fullmatch(r"byul-online-([0-9a-f]{32})-" + re.escape(case) + "-" + phase, headers["X-Request-Id"])
            body = request.get("body")
            normal = {"model", "messages", "max_tokens", "stream"}
            extra = {"kv_transfer_params", "echo", "chat_template", "chat_template_kwargs",
                     "mm_processor_kwargs", "media_io_kwargs"}
            if (rid is None or urlsplit(request.get("url", "")).path != "/v1/chat/completions"
                    or type(body) is not dict or set(body) - (normal | (extra if phase == "primary" else set()))
                    or body.get("model") != self.api_model or type(body.get("max_tokens")) is not int
                    or body["max_tokens"] != 1 or body.get("stream", False) is not False
                    or type(body.get("messages")) is not list or not 1 <= len(body["messages"]) <= 16):
                raise ValueError
            uri = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|data:|www\.)")
            for message in body["messages"]:
                if (type(message) is not dict or set(message) != {"role", "content"}
                        or message["role"] not in {"system", "user", "assistant"}
                        or type(message["content"]) is not str or len(message["content"]) > 4096
                        or uri.search(message["content"])):
                    raise ValueError
            for key in ("chat_template_kwargs", "mm_processor_kwargs", "media_io_kwargs"):
                if key in body and body[key] is not None and type(body[key]) is not dict:
                    raise ValueError
            if "chat_template" in body and body["chat_template"] is not None and type(body["chat_template"]) is not str:
                raise ValueError
            if "echo" in body and body["echo"] is not None and type(body["echo"]) is not bool:
                raise ValueError
            if "kv_transfer_params" in body and body["kv_transfer_params"] is not None:
                transfer = body["kv_transfer_params"]
                if type(transfer) is not dict or set(transfer) - {"prompt_token_ids"}:
                    raise ValueError
                if "prompt_token_ids" in transfer:
                    ids = transfer["prompt_token_ids"]
                    if (type(ids) is not list or len(ids) > 4096
                            or any(type(value) is not int or not -(2**63) <= value < 2**63 for value in ids)):
                        raise ValueError
            # All nested fields are bounded JSON data, never Python objects,
            # iterators, media/remote addresses, arbitrary request parameters.
            nodes = 0
            def bounded(value, depth=0, allow_uri=False):
                nonlocal nodes
                nodes += 1
                if depth > 12 or nodes > 4096:
                    raise ValueError
                if value is None or type(value) is bool:
                    return
                if type(value) is int:
                    if value.bit_length() > 256:
                        raise ValueError
                    return
                if type(value) is float:
                    if not math.isfinite(value):
                        raise ValueError
                    return
                if type(value) is str:
                    if len(value) > 4096 or not allow_uri and uri.search(value):
                        raise ValueError
                    return
                if type(value) is list:
                    for item in value:
                        bounded(item, depth + 1)
                    return
                if type(value) is dict:
                    for key, item in value.items():
                        if type(key) is not str or not 1 <= len(key) <= 128:
                            raise ValueError
                        bounded(item, depth + 1, allow_uri=depth == 0 and key == "model")
                    return
                raise ValueError
            bounded(body)
            wire_body = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(wire_body) > 16384:
                raise ValueError
            with self.lock:
                if (self._online_token is not None and rid[1] != self._online_token
                        or phase == "primary" and (self._online_pending is not None
                            or case in self._online_cases or len(self._online_cases) >= 16)
                        or phase == "after" and self._online_pending != case):
                    raise ValueError
                self._online_token = rid[1]
                if phase == "primary":
                    self._online_cases.add(case)
                    self._online_pending = case
                else:
                    self._online_pending = None
        except (KeyError, TypeError, ValueError, OverflowError):
            raise KubernetesBindingError() from None

    def template_snapshot(self, identity, argv, command, reader):
        """Fixed bounded reader in the exact owned engine generation; no raw spec retained."""
        try:
            if (self.profile!=REQUEST_BINDING_PROFILE or sha256_hex(reader.encode())!=TEMPLATE_READER_SHA256
                    or identity.get("container_path")!="/guard-assets/chat/guard-text-chat-v1.jinja"
                    or type(command) is not list or not command or any(type(v) is not str or not v or len(v)>256 for v in command)):
                raise KubernetesBindingError()
            pods=self._verify();engine=self.binding["engine"]
            container=next(row for row in pods["engine"]["spec"]["containers"] if row["name"]==engine["container"])
            if (container.get("command")!=command or container.get("args")!=argv or not any(
                    row.get("mountPath")==identity["container_path"] and row.get("readOnly") is True
                    for row in container.get("volumeMounts",[]))):
                raise KubernetesBindingError()
            value=self._call([f"--namespace={engine['namespace']}","exec",engine["pod"],
                f"--container={engine['container']}","--","python3","-c",reader,identity["container_path"]],cap=1024,budget=5)
            self._verify()
            if (type(value) is not dict or set(value)!={"sha256","bytes"} or type(value["sha256"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}",value["sha256"]) or type(value["bytes"]) is not int or not 0<value["bytes"]<=8192):
                raise KubernetesBindingError()
            return {"container_id":engine["container_id"],"started_at":engine["started_at"],"image":engine["image"],
                "selected_path":identity["container_path"],"readonly":True,**value,"namespace_uid":engine["namespace_uid"],
                "pod_uid":engine["pod_uid"],"image_id":engine["image_id"],"restart_count":engine["restart_count"]}
        except Exception:
            with self.lock:self.failed=True
            return None

    def process_snapshot(self, candidate_paths=None):
        """Fixed process metadata read for existing permission/credential consumers."""
        from .kubernetes_process import capture
        return capture(self,candidate_paths)

    def normal_requests(self, network, requests, timeout):
        """Existing PLT01/04 completion control, with an explicit wire ID map."""
        from .kubernetes_functional import capture
        return capture(self,network,requests,timeout)

    def api_hop_snapshot(self, policy_values, timeout):
        """One explicit policy-bound HTTP hop for the existing PLT08 consumer."""
        from .kubernetes_api_hop import capture
        return capture(self,policy_values,timeout)

    def report(self):
        with self.lock:
            return {"profile": self.profile, "binding": deepcopy(self.binding),
                    "binding_sha256": sha256_hex(canonical_json(self.binding)),
                    "probe_source_sha256": self.source_sha256,
                    "requests": sorted(deepcopy(self.rows), key=lambda row: row["id"] or ""),
                    "collection_gap": self.failed or (self.profile in _BINDING_PROFILES and any(row.get("capture_complete") is not True for row in self.rows))
                        or (self.profile == ONLINE_INPUT_PROFILE and self._online_pending is not None),
                    **({"snapshot_reads":deepcopy(self.snapshot_reads)} if self.snapshot_reads else {}),
                    **({"cleanup_errors":list(self.cleanup_errors)} if self.profile in _BINDING_PROFILES else {}),
                    **({"input_contract_version":1,"data_origin":self.process_observation_origin,
                        "native_consumption_verified":False} if self.profile==ONLINE_INPUT_PROFILE else {}),
                    "limitations": ["Explicit operator selection and guard.run label are not an approval signature.",
                                    "Pod spec digest and exact runtime imageID are bound; manifest-index ancestry is not verified.",
                                    "No GPU execution, runner consumption, resource/log isolation or deployed caller path proof."]}
