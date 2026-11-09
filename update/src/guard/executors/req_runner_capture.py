"""B native 요청을 변경하지 않고 응답 ID→기존 callback HMAC을 연결한다.

선정 0.31 단일 prompt/n=1/LAB 환경만 지원한다. A 전송·생산자·schema·판정은 바꾸지 않는다.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path, PurePosixPath
import re
import threading
import tempfile
from urllib.parse import urlsplit

from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.adapters.vllm import REQUEST_BINDING_GPU_IMAGE
from guard.collectors.engine_callbacks import load_prepared, load_secret
from guard.collectors import gpu_input_producers as producers
from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP, ENGINE_CALLBACK_PROFILE
from .base import DraftArtifact

PREPARER_SHA = "0175f8e85d801edae0ccbb9f46a31f334aa53878eac73356174b30ae2d0b206b"
SOURCE_COMMIT = "db9527a46873454610df6dbedf79a36d6bf1a7f6"
SOURCES = {
    "entrypoints/serve/engine/serving.py": "e5239ca4e2119fd019ee0ffcbf6e68f642b7ce94bad1b3fabe22ccdd8cf9eb02",
    "entrypoints/openai/completion/serving.py": "aac889978efb034b95ab81ec59695d83ba7c879ab48cd5be2448c980f109a3bd",
    "v1/engine/input_processor.py": "d79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b",
    "v1/engine/async_llm.py": "ff933ed78aa3195782cf1c6d18c53897adfa5bf920421bd2bcc330f2adc84d1e",
    "entrypoints/openai/completion/protocol.py": "4616cd6887ef0a23fd8a77468e82f2668e3d0b53075112f2d97f3de960c22c81",
    "entrypoints/openai/chat_completion/serving.py": "532dadea845c77a5a79011027b9989987c3e32467614e993f439f29c924de002",
    "entrypoints/openai/chat_completion/protocol.py": "4bdc71f668e4820d8ae5bae2a153fed0296b430cd6353ace8c0c27cfb982ebc1",
}
_RESPONSE_ID = re.compile(r"(?:chatcmpl|cmpl)-[0-9a-f]{32}")


def _preparer():
    # 선택 모드는 고정 full source 배치를 요구한다. CWD/PYTHONPATH에서 동명 도구를 탐색하지 않는다.
    path = Path(__file__).resolve().parents[3] / "tools/b_runner_inputs.py"
    if not path.is_file() or sha256_hex(path.read_bytes()) != PREPARER_SHA:
        raise ValueError("B_REQ_FIXED_SOURCE_BUNDLE_REQUIRED")
    spec = importlib.util.spec_from_file_location("guard._fixed_b_runner_inputs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _label(check_id, value):
    if check_id == "REQ-08":
        return {"before": "before", "challenge-0": "reused_input", "after-0": "after-0"}.get(value)
    if check_id == "REQ-09":
        return value if value in {"before", "token_id", "length", "after_token_id", "after_length"} else None
    if value == "sampling-before":
        return "before"
    match = re.fullmatch(r"sampling-(temperature|top_p)-(boundary|challenge|concurrent|after)", value)
    return (match[1] if match[2] == "challenge" else match[1] + ":" + match[2]) if match else None


def _bounded(request, check_id):
    body = request.get("body")
    url = urlsplit(request.get("url", ""))
    expected_path = "/v1/chat/completions" if check_id == "REQ-08" else "/v1/completions"
    if (request.get("method") != "POST" or url.path != expected_path or url.query or url.fragment
            or request.get("headers", {}) != {} or type(body) is not dict
            or body.get("n", 1) != 1 or type(body.get("n", 1)) is not int
            or body.get("stream", False) is not False or body.get("use_beam_search", False) is not False
            or type(body.get("max_tokens")) is not int or not 1 <= body["max_tokens"] <= 8
            or "request_id" in body):
        raise ValueError("B_REQ_NATIVE_REQUEST_OUTSIDE_SELECTED_SCOPE")
    if check_id == "REQ-08":
        messages = body.get("messages")
        if (type(messages) is not list or len(messages) != 1 or type(messages[0]) is not dict
                or set(messages[0]) != {"role", "content"} or messages[0]["role"] != "user"
                or type(messages[0]["content"]) is not str or not 0 < len(messages[0]["content"].encode("utf-8")) <= 512):
            raise ValueError("B_REQ_SINGLE_SMALL_PROMPT_REQUIRED")
        return
    prompt = body.get("prompt")
    if not (type(prompt) is str and 0 < len(prompt.encode("utf-8")) <= 512 or type(prompt) is list
            and 0 < len(prompt) <= 64 and all(type(token) is int and token >= 0 for token in prompt)):
        raise ValueError("B_REQ_SINGLE_SMALL_PROMPT_REQUIRED")


class Capture:
    def __init__(self, ctx, cfg):
        self.ctx, self.cfg = ctx, cfg
        self.lock = threading.Lock()
        self.calls = []
        self.tool = _preparer()
        spec = cfg["runner_capture"]
        if (type(spec) is not dict or set(spec) != {"environment", "prepared_dir", "prepared_sha256", "vllm_source_root"}
                or "runner_input" in cfg or ctx.check.id not in {"REQ-08", "REQ-09", "REQ-10"} or ctx.target.get("lab") is not True
                or ctx.env.engine.version != "0.31.0" or ctx.env.engine.image_digest != REQUEST_BINDING_GPU_IMAGE):
            raise ValueError("B_REQ_CAPTURE_SCOPE_INVALID")
        if ctx.check.id == "REQ-10" and set(ctx.policy_values["allowed_sampling_options"]) - {"temperature", "top_p"}:
            raise ValueError("B_REQ_V1_SELECTED_OPTIONS_REQUIRED")
        if ctx.check.id == "REQ-09" and ctx.policy_values["max_input_tokens"] >= 64:
            raise ValueError("B_REQ_SMALL_LENGTH_BOUND_REQUIRED")
        self.required_cases = (["before", "reused_input", "after-0"] if ctx.check.id == "REQ-08" else
            ["before", "token_id", "length", "after_token_id", "after_length"] if ctx.check.id == "REQ-09" else
            ["before", *(case for option in ctx.policy_values["allowed_sampling_options"]
                         for case in (option, option + ":boundary", option + ":concurrent", option + ":after"))])
        self.environment_raw, self.environment_pin = self.tool._read_pin(spec["environment"], base=Path.cwd(), limit=2*1024**2)
        env_value = strict_json(self.environment_raw, limit=2*1024**2)
        if canonical_json(env_value) != canonical_json(ctx.env.to_dict()):
            raise ValueError("B_REQ_ENVIRONMENT_BINDING_MISMATCH")
        self.target_path = Path(cfg["target"]).absolute()
        self.target_raw = self.target_path.read_bytes()
        if strict_json(self.target_raw) != ctx.target:
            raise ValueError("B_REQ_NATIVE_TARGET_CHANGED")
        self.prepared_dir = Path(spec["prepared_dir"]).absolute()
        self.prepared = load_prepared(self.prepared_dir)
        if (sha256_hex(canonical_json(self.prepared)) != spec["prepared_sha256"]
                or self.prepared.get("runner_consumption") is not True or self.prepared.get("lab_kernel_checks") is not False):
            raise ValueError("B_REQ_PREPARED_BINDING_MISMATCH")
        secret = load_secret(self.prepared_dir)
        if len(secret) != 32:
            raise ValueError("B_REQ_PREPARED_KEY_INVALID")
        # 공식 fingerprint만 사용한다. 호스트에서 Linux 엔진 센서의 수집 context를 초기화하지 않는다.
        self.fingerprinter = object.__new__(sensor.ConsumptionObserver)
        self.fingerprinter.secret = secret
        root = spec["vllm_source_root"]
        if type(root) is not str or not root.startswith("/") or str(PurePosixPath(root)) != root or ".." in PurePosixPath(root).parts:
            raise ValueError("B_REQ_SOURCE_ROOT_REQUIRED")
        self.roots = [root, sensor.ROOT]
        self.expected_files = {(0, name): digest for name, digest in SOURCES.items()}
        self.expected_files.update({(1, name): digest for name, digest in self.prepared["assets"].items()})
        native = getattr(ctx.cprobe, "transport", ctx.cprobe)
        if not isinstance(native, KubernetesHTTP):
            raise ValueError("B_REQ_OWNED_NATIVE_TRANSPORT_REQUIRED")
        remaining = native.deadline - native.clock()
        if remaining <= 0:
            raise ValueError("B_REQ_ORIGINAL_DEADLINE_REACHED")
        self.transport = KubernetesHTTP(ctx.target, ctx.env, duration=remaining, binary=native.binary,
            runner=native.runner if native.injected else None, clock=native.clock, profile=ENGINE_CALLBACK_PROFILE)
        self.transport.deadline = min(self.transport.deadline, native.deadline)
        self.before = self._snapshot()
        self.before_events = self.transport.engine_attempt_events(self.prepared["session"])
        if self.before_events["truncated"]:
            raise ValueError("B_REQ_CALLBACK_BASELINE_TRUNCATED")

    def _snapshot(self):
        pod = self.transport.engine_pod_document()
        name = self.transport.binding["engine"]["container"]
        container, = [row for row in pod["spec"]["containers"] if row["name"] == name]
        state, = [row for row in pod["status"]["containerStatuses"] if row["name"] == name]
        env = {}
        for row in container.get("env", []):
            if row.get("name") in {"VLLM_DISABLE_REQUEST_ID_RANDOMIZATION", "VLLM_USE_V2_MODEL_RUNNER", "PYTHONPATH"}:
                if row["name"] in env or "valueFrom" in row:
                    raise ValueError("B_REQ_LITERAL_RUNTIME_ENV_REQUIRED")
                env[row["name"]] = row.get("value", "")
        if (env.get("VLLM_DISABLE_REQUEST_ID_RANDOMIZATION") != "1" or env.get("VLLM_USE_V2_MODEL_RUNNER") != "0"
                or sensor.ROOT not in env.get("PYTHONPATH", "").split(":")
                or container.get("image") != REQUEST_BINDING_GPU_IMAGE):
            raise ValueError("B_REQ_VERIFIED_LAB_V1_ENV_REQUIRED")
        mounts = [row for row in container.get("volumeMounts", [])
                  if sensor.ROOT == row.get("mountPath") or sensor.ROOT.startswith(str(row.get("mountPath", "!")) + "/")]
        if not mounts or any(row.get("readOnly") is not True or row.get("subPathExpr") for row in mounts):
            raise ValueError("B_REQ_READONLY_CALLBACK_MOUNT_REQUIRED")
        files = [{"root": key[0], "relative": key[1]} for key in self.expected_files]
        hashes = self.transport.engine_supply_hashes(self.roots, files)
        observed = {(row["root"], row["relative"]): row for row in hashes}
        if (len(observed) != len(files) or set(observed) != set(self.expected_files)
                or any(observed[key].get("state") != "hashed" or observed[key].get("sha256") != digest
                       for key, digest in self.expected_files.items())):
            raise ValueError("B_REQ_RUNTIME_SOURCE_OR_ASSET_MISMATCH")
        return {"pod_uid": pod["metadata"]["uid"], "container_id": state["containerID"],
            "restart_count": state["restartCount"], "started_at": state["state"]["running"]["startedAt"],
            "image": container["image"], "env": env, "mounts": mounts,
            "security_context": {key: container.get("securityContext", {}).get(key) for key in ("runAsUser", "runAsGroup", "readOnlyRootFilesystem")},
            "source_hashes": [{key: row.get(key) for key in ("root", "relative", "state", "sha256", "size")} for row in hashes]}

    def call(self, label, request, dispatch):
        if request.get("method") == "GET":
            return dispatch()
        case = _label(self.ctx.check.id, label)
        _bounded(request, self.ctx.check.id)
        if case is None:
            raise ValueError("B_REQ_NATIVE_CASE_UNSUPPORTED")
        body = canonical_json(request["body"])
        with self.lock:
            if len(self.calls) >= 16 or any(row["case"] == case for row in self.calls):
                raise ValueError("B_REQ_NATIVE_CASE_REPEATED")
            row = {"case": case, "dispatch_index": len(self.calls), "method": request["method"],
                   "path": urlsplit(request["url"]).path, "request_fixture_sha256": sha256_hex(body),
                   "outcome": None, "response_id_sha256": None, "request_sha256": None, "diagnostic": None}
            self.calls.append(row)
        try:
            result = dispatch()  # 원 요청 객체·헤더·본문·전송 ID를 그대로 정확히 한 번 호출한다.
        except Exception:
            row["diagnostic"] = "B_REQ_NATIVE_DISPATCH_FAILED"
            raise
        response = result[0] if type(result) is list and len(result) == 1 else result
        if type(response) is not dict:
            row["diagnostic"] = "B_REQ_NATIVE_RESPONSE_UNAVAILABLE"
            return result
        outcome = response.get("outcome")
        row["outcome"] = outcome if type(outcome) is int else "probe_error"
        try:
            raw = response.get("body", "").encode("utf-8")
            value = strict_json(raw, limit=2*1024**2)
        except (ValueError, AttributeError, UnicodeError):
            value = None
        rid = value.get("id") if type(value) is dict else None
        prefix = "chatcmpl-" if self.ctx.check.id == "REQ-08" else "cmpl-"
        if type(rid) is str and rid.startswith(prefix) and _RESPONSE_ID.fullmatch(rid) and not response.get("body_truncated"):
            row["response_id_sha256"] = sha256_hex(rid.encode())
            row["request_sha256"] = self.fingerprinter.fingerprint(rid if self.ctx.check.id == "REQ-08" else rid + "-0")
        else:
            row["diagnostic"] = "B_REQ_HTTP_REJECTION_WITHOUT_ID" if type(outcome) is int and 400 <= outcome < 500 else "B_REQ_RESPONSE_ID_UNAVAILABLE"
        return result

    def finish(self):
        value = self.transport.engine_attempt_events(self.prepared["session"])
        # 반환된 parsed envelope를 후반 환경 검사/투영보다 먼저 보존한다. 원 pipe bytes라는 주장은 하지 않는다.
        output = self.ctx.workdir / "runner-capture"
        output.mkdir(parents=True, exist_ok=False)
        self.callback_cleanup_errors = []
        fd, temporary = tempfile.mkstemp(prefix="callback-received-", suffix=".tmp", dir=output)
        temporary = Path(temporary)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        self.callback_payload_path = temporary
        # link는 기존 목적지를 덮어쓰지 않고 완성된 파일만 원자적으로 공개한다.
        destination = output / "callback-received.json"
        os.link(temporary, destination)
        self.callback_payload_path = destination
        try:
            os.unlink(temporary)
        except OSError as error:
            self.callback_cleanup_errors.append({"type": type(error).__name__, "code": "B_REQ_CALLBACK_STAGING_CLEANUP_UNAVAILABLE"})
        after = self._snapshot()
        self.after = after
        if after != self.before:
            raise ValueError("B_REQ_RUNTIME_GENERATION_OR_SOURCE_CHANGED")
        old = {(p["pid"], p["start_ticks"], sha256_hex(canonical_json(row)))
               for p in self.before_events["processes"] for row in p["events"]}
        processes = [{**p, "events": [row for row in p["events"]
                      if (p["pid"], p["start_ticks"], sha256_hex(canonical_json(row))) not in old]} for p in value["processes"]]
        scope = {"environment_ref": self.ctx.env.ref().model_dump(mode="json"),
            "target_sha256": sha256_hex(canonical_json(self.ctx.target)),
            "prepared_sha256": sha256_hex(canonical_json(self.prepared)), "session": self.prepared["session"]}
        record, validation = producers.runner_consumption_record(processes, pod=self.transport.binding["engine"],
            truncated=value["truncated"], **scope)
        counts = {}
        for row in self.calls:
            key = row["response_id_sha256"]
            if key is not None:
                counts[key] = counts.get(key, 0) + 1
        requests, proof = {}, []
        for row in self.calls:
            selected = [event for event in record["events"] if event["request_sha256"] == row["request_sha256"]]
            api = [event for event in selected if event["event"] in {"api_validated", "api_rejected"}]
            unique = row["response_id_sha256"] is not None and counts[row["response_id_sha256"]] == 1
            matched = unique and len(api) == 1
            if matched:
                requests[row["case"]] = row["request_sha256"]
            proof.append({**row, "same_native_request_api_join": matched,
                          "event_sequences": [event["seq"] for event in selected],
                          "duplicate_response_id": row["response_id_sha256"] is not None and not unique})
        raw_files = {"environment.json": self.environment_raw, "target.json": self.target_raw,
            "prepared.json": canonical_json(self.prepared), "requests.json": canonical_json(requests),
            "wrapper.json": canonical_json({"schema": self.tool.WRAPPER_SCHEMA, "session": self.prepared["session"],
                "truncated": value["truncated"], "records": {"runner-consumption": {"record": record, "validation": validation}}}),
            "callback-delta.json": canonical_json({"processes": processes, "truncated": value["truncated"]})}
        sources = {}
        for name, raw in raw_files.items():
            with (output / name).open("xb") as stream:
                stream.write(raw)
            if name != "callback-delta.json":
                sources[name.removesuffix(".json")] = {"path": str(output / name), "file_sha256": sha256_hex(raw)}
        manifest = {"schema": self.tool.INPUT_SCHEMA, "check_id": self.ctx.check.id, "session": self.prepared["session"],
            "required_cases": self.required_cases, "sources": sources, "data_origin": "EXAMPLE" if self.transport.injected or self.ctx.env.data_origin.value == "EXAMPLE" else "DECLARED"}
        manifest_path = output / "inputs.json"
        manifest_path.write_bytes(canonical_json(manifest))
        result = self.tool.prepare(manifest_path, sha256_hex(manifest_path.read_bytes()), output / "fixed")
        summary = {"schema": "guard-b-native-req-runner-link/1", "check_id": self.ctx.check.id,
            "source_commit": SOURCE_COMMIT, "before": self.before, "after": after, "cases": proof,
            "request_id_join_complete": {row["case"] for row in proof} == set(self.required_cases) and all(row["same_native_request_api_join"] for row in proof),
            "native_request_mapping_verified": False,
            "consumer_projection": result["consumer_projection"], "runtime_link_complete": False, "gpu_ready": False,
            "capture_origin": "EXAMPLE" if self.transport.injected or self.ctx.env.data_origin.value == "EXAMPLE" else "LIVE_COMPONENT",
            "producer_authenticated": False, "data_origin": manifest["data_origin"],
            "limitations": ["Same response ID/API fingerprint is correlation, not producer authentication or token identity.",
                "HTTP rejection without response ID remains unmapped; no suffix search, event relabeling or declared case swap.",
                "Existing external-file consumer keeps EXAMPLE/DECLARED; actual full policy/RPT status is separate.",
                "Local/Pod source hashes and declared mount/security metadata do not prove kernel/tensor or all FD/ownership scope."]}
        self.summary = summary
        summary["native_request_mapping_verified"] = summary["request_id_join_complete"] and summary["capture_origin"] == "LIVE_COMPONENT"
        summary["runtime_link_complete"] = summary["native_request_mapping_verified"] and result["consumer_projection"]["complete"]
        return result["runner_input"], summary


def begin(ctx, cfg):
    if "runner_capture" in cfg:
        ctx._req_runner_capture = Capture(ctx, cfg)


def case_call(ctx, label, request, dispatch):
    capture = getattr(ctx, "_req_runner_capture", None)
    return capture.call(label, request, dispatch) if capture else dispatch()


def finish(ctx, draft, cfg):
    capture = getattr(ctx, "_req_runner_capture", None)
    if capture is None:
        return cfg
    try:
        runner_input, summary = capture.finish()
    except Exception as error:
        runner_input = {}
        text = str(error)
        code = text if re.fullmatch(r"(?:B_REQ_|B_RUNNER_|B_COMMON_|GPU_INPUT_|ENGINE_CALLBACK_)[A-Z0-9_:]*", text) else "B_REQ_CAPTURE_INCOMPLETE"
        summary = {"schema": "guard-b-native-req-runner-link/1", "check_id": ctx.check.id,
            "error": {"type": type(error).__name__, "code": code},
            "before": capture.before, "after": getattr(capture, "after", None),
            "cases": capture.calls, "native_request_mapping_verified": False,
            "runtime_link_complete": False, "gpu_ready": False}
        draft.cleanup_errors.append("B_REQ_CAPTURE_INCOMPLETE")
    received = getattr(capture, "callback_payload_path", None)
    cleanup = getattr(capture, "callback_cleanup_errors", [])
    draft.cleanup_errors.extend(row["code"] for row in cleanup)
    summary["callback_payload"] = {"preserved": bool(received and received.is_file()),
        "authenticated": False, "encoding": "canonical JSON of parsed engine_attempt_events result",
        "published_atomically": bool(received and received.name == "callback-received.json"), "cleanup_errors": cleanup}
    if received and received.is_file():
        draft.artifacts.append(DraftArtifact(name="req_runner_callback_received.json", path=received, collector="b-native-req-runner-link/1"))
    for relative, name in (("wrapper.json", "req_runner_wrapper.json"), ("callback-delta.json", "req_runner_callback_delta.json"),
                           ("requests.json", "req_runner_requests.json"), ("fixed/receipt.json", "req_runner_prepare_receipt.json")):
        captured_path = ctx.workdir / "runner-capture" / relative
        if captured_path.is_file():
            draft.artifacts.append(DraftArtifact(name=name, path=captured_path, collector="b-native-req-runner-link/1"))
    path = ctx.workdir / "req_runner_capture.json"
    try:
        with path.open("xb") as stream:
            stream.write(canonical_json(summary))
        draft.artifacts.append(DraftArtifact(name=path.name, path=path, collector="b-native-req-runner-link/1"))
    except OSError:
        runner_input = {}
        draft.cleanup_errors.append("B_REQ_CAPTURE_ARTIFACT_UNAVAILABLE")
    return {**cfg, "runner_input": runner_input}
