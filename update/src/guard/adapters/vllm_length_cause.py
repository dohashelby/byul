"""Opt-in, source-correlated CPU completion length rejection evidence.

The bounded Python reader is a separate process, not API-server memory
inspection. Identity, immutable code, launch, model and paired controls must
agree; an error string alone never establishes the cause.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from guard.runtime.docker import Docker
from .base import LaunchSpec, ProbeRequest

# 검토한 이미지·모델·소스 값은 아래 JSON 데이터로 분리했다.
# 현재는 지정한 프로파일 파일 하나만 고정 로드한다.
# 다중 프로파일 선택과 새 pinned 이미지 연결은 후속 구현이다.
_PROFILE_PATH = Path(__file__).resolve().parent / "reviewed_profiles" / "vllm-cpu-length-cause-1.json"
_REVIEWED = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
PROFILE = _REVIEWED["profile"]
ENGINE_VERSION = _REVIEWED["engine_version"]
IMAGE = _REVIEWED["image"]
MODEL = _REVIEWED["model"]
MODEL_REVISION = _REVIEWED["model_revision"]
MODEL_INVENTORY = _REVIEWED["model_inventory_sha256"]
HARDENED_TMPFS = dict(_REVIEWED["hardened_tmpfs"])
ZERO_ERROR = _REVIEWED["zero_error"]
SOURCE_ROOT = _REVIEWED["source_root"]
NATIVE_PRELOAD = _REVIEWED["native_preload"]
NATIVE_PRELOAD_SHA256 = _REVIEWED["native_preload_sha256"]
NATIVE_LIBRARIES = dict(_REVIEWED["native_libraries"])
SOURCE_REVIEWS = {name: (item["sha256"], item["lines"]) for name, item in _REVIEWED["source_reviews"].items()}
MAX_BYTES = 16384
TIMEOUT = 30

# No model/file contents, raw environment, host paths or import diagnostics are
# emitted. Importing the existing platform module creates no inference engine.
RUNTIME_READER = r'''
import contextlib, hashlib, importlib, inspect, json, os, pathlib, sys
p=json.loads(sys.argv[1]); root=pathlib.Path(p["root"])
def digest(path):
    with open(path,"rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()
def env(raw):
    return dict(x.split("=",1) for x in raw.split("\0") if "=" in x)
api_env=env(pathlib.Path("/proc/1/environ").read_bytes().decode("utf-8","strict"))
related=("VLLM_TARGET_DEVICE","VLLM_PLUGINS","PYTHONPATH","PYTHONHOME",
 *sorted(k for k in set(api_env)|set(os.environ) if k.startswith("LD_")))
same_env=all(api_env.get(k)==os.environ.get(k) for k in related)
clean_env=all(not api_env.get(k) for k in ("PYTHONPATH","PYTHONHOME"))
clean_loader=all(not value for values in (api_env,os.environ) for key,value in values.items()
                 if key.startswith("LD_") and key!="LD_PRELOAD")
isolated_python=bool(sys.flags.isolated and sys.flags.ignore_environment and sys.flags.no_user_site)
preload=api_env.get("LD_PRELOAD","")
native={"profile":"reviewed-cpu-native-libraries/1",
 "preload_sha256":hashlib.sha256(preload.encode()).hexdigest(),"library_sha256":{}}
if preload==p["native_preload"]:
    native["library_sha256"]={name:digest(name) for name in p["native_libraries"]}
native_matches=(preload==p["native_preload"] and native["library_sha256"]==p["native_libraries"])
cmd=[x.decode("utf-8","strict") for x in pathlib.Path("/proc/1/cmdline").read_bytes().split(b"\0") if x]
tail=p["tail"]; same_cmd=len(cmd)>=len(tail)+1 and cmd[-len(tail):]==tail and any(x.rsplit("/",1)[-1]=="vllm" for x in cmd[:-len(tail)])
same_exe=os.readlink("/proc/1/exe")==os.readlink("/proc/self/exe")
cpu=pathlib.Path("/proc/cpuinfo").read_text()
intel="GenuineIntel" in cpu and "AuthenticAMD" not in cpu
hashes={name:digest(root/name) for name in p["sources"]}
platform_class=None; method_relative=None; caps=None; module_root=None
if native_matches and clean_env and clean_loader and same_env and isolated_python:
    with open(os.devnull,"w") as sink, contextlib.redirect_stdout(sink),contextlib.redirect_stderr(sink):
        platforms=importlib.import_module("vllm.platforms")
        platform=platforms.current_platform
        method=platform.get_max_output_tokens
        method_path=pathlib.Path(inspect.getsourcefile(method)).resolve()
        method_relative=str(method_path.relative_to(root)).replace("\\","/")
        caps=[method(1),method(p["limit"])]
        module_root=pathlib.Path(inspect.getfile(platforms)).resolve().parent.parent
        platform_class=type(platform).__module__+"."+type(platform).__name__
value={"schema":"guard-vllm-length-runtime/1","separate_process":True,
 "same_relevant_environment":same_env,"no_python_path_injection":clean_env,
 "no_unreviewed_loader_environment":clean_loader,"isolated_python":isolated_python,
 "native_libraries":native,
 "same_api_command":same_cmd,"same_interpreter":same_exe,"intel_cpu":intel,
 "module_root_matches":module_root==root,"source_sha256":hashes,
 "platform_class":platform_class,
 "cap_method_source":method_relative,"cap_values":caps,"sys_maxsize":sys.maxsize}
print(json.dumps(value,separators=(",",":"),sort_keys=True))
'''


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _unique(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = child
    return value


def _json_response(response):
    raw = response.get("body")
    if (type(response.get("outcome")) is not int or response["outcome"] != 200 or not isinstance(raw, str)
            or len(raw.encode()) >= MAX_BYTES or response.get("body_bytes", len(raw.encode())) != len(raw.encode())
            or response.get("body_truncated") or response.get("truncated")):
        raise ValueError("incomplete response")
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(value) is not dict:
        raise ValueError("invalid response")
    return value, hashlib.sha256(raw.encode()).hexdigest()


def runtime_valid(value, limit):
    """Validate observed data, not a caller-supplied 'verified' flag."""
    return (type(value) is dict and value.get("schema") == "guard-vllm-length-runtime/1"
        and value.get("separate_process") is True
        and all(value.get(key) is True for key in ("same_relevant_environment", "no_python_path_injection",
            "no_unreviewed_loader_environment", "isolated_python", "same_api_command", "same_interpreter",
            "intel_cpu", "module_root_matches"))
        and value.get("native_libraries") == {"profile": "reviewed-cpu-native-libraries/1",
            "preload_sha256": NATIVE_PRELOAD_SHA256, "library_sha256": NATIVE_LIBRARIES}
        and value.get("source_sha256") == {name: item[0] for name, item in SOURCE_REVIEWS.items()}
        and value.get("platform_class") == "vllm.platforms.cpu.CpuPlatform"
        and value.get("cap_method_source") == "platforms/interface.py"
        and type(value.get("sys_maxsize")) is int and value["sys_maxsize"] == 2**63 - 1
        and value.get("cap_values") == [value["sys_maxsize"], value["sys_maxsize"]]
        and type(limit) is int and limit > 1)


def context_valid(context, request_body):
    if type(context) is not dict or context.get("profile") != PROFILE:
        return False
    before, after = context.get("before"), context.get("after")
    if type(before) is not dict or before != after or before.get("verified") is not True:
        return False
    limit = before.get("max_model_len")
    if (type(limit) is not int or limit != 128 or limit != len(request_body["prompt"])
            or before.get("model") != request_body["model"]
            or request_body["model"] != MODEL or before.get("image_digest") != IMAGE
            or context.get("model_inventory_sha256") != MODEL_INVENTORY
            or re.fullmatch(r"[a-f0-9]{64}", str(before.get("container_id"))) is None
            or re.fullmatch(r"sha256:[a-f0-9]{64}", str(before.get("image_id"))) is None
            or any(re.fullmatch(r"[a-f0-9]{64}", str(before.get(key))) is None
                   for key in ("command_sha256", "mounts_sha256"))
            or type(before.get("owner")) is not str or not before["owner"]
            or type(before.get("started_at")) is not str or not before["started_at"]
            or before.get("trusted_network") != f"guard-{before['owner']}-engine"
            or re.fullmatch(r"[a-f0-9]{64}", str(before.get("trusted_network_id"))) is None
            or before.get("engine_url") != f"http://{before.get('trusted_ip')}:8000"):
        return False
    for phase in ("before", "after"):
        models = context.get("models", {}).get(phase, {})
        if (models.get("model") != request_body["model"] or models.get("max_model_len") != limit
                or not re.fullmatch(r"[a-f0-9]{64}", str(models.get("response_sha256", "")))
                or not runtime_valid(context.get("runtime", {}).get(phase), limit)):
            return False
    controls = context.get("controls", {})
    return (set(controls) == {"before", "after_token_id", "after_length"}
        and all(type(value) is dict and value == {"model": request_body["model"], "prompt_tokens": 1,
                         "completion_tokens": 1, "total_tokens": 2, "valid_generation": True}
                and value["valid_generation"] is True
                and all(type(value[key]) is int for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
                for value in controls.values()))


class VllmLengthCause:
    """Only owned immutable CPU-bounded targets are eligible for this proof."""

    def __init__(self, ctx, docker=None):
        self.ctx = ctx
        self.docker = docker or Docker("lc" + secrets.token_hex(4))
        self.data = {"profile": PROFILE, "state": "unverified", "reason": "LENGTH_CAUSE_NOT_COLLECTED",
                     "limitations": ["Platform import is a separate process, not API process memory inspection.",
                                     "No input-consumption, damage or performance sensor is provided."]}

    def _expected(self):
        from .vllm import CPU_LAUNCH_KEYS
        ctx = self.ctx
        launch = ctx.target.get("comparison_config", {}).get("launch", {})
        if (ctx.env.engine.version != ENGINE_VERSION or ctx.env.engine.image_digest != IMAGE
                or ctx.env.model.id != MODEL or ctx.env.model.revision != MODEL_REVISION
                or ctx.env.model.inventory_sha256 != MODEL_INVENTORY or launch.get("max_model_len") != 128
                or launch.get("profile") != "cpu-bounded-v1" or launch.get("model_layout") != "snapshot"
                or launch.get("gpu") is not False or launch.get("model") != ctx.env.model.id
                or launch.get("revision") != ctx.env.model.revision
                or "chat_template_profile" in launch or "logging_profile" in launch):
            raise ValueError("unreviewed launch")
        options = {key: launch[key] for key in CPU_LAUNCH_KEYS if key in launch}
        options.update(revision=ctx.env.model.revision, models_dir="/not-observed", device="cpu",
                       launch_profile="cpu-bounded", model_layout="snapshot")
        plan = ctx.adapter.launch_plan(LaunchSpec(model=ctx.env.model.id, port=8000, options=options, image=IMAGE))
        if launch.get("argv") != plan.argv or launch.get("entrypoint") != plan.entrypoint:
            raise ValueError("launch command mismatch")
        return plan, launch["max_model_len"]

    def _snapshot(self):
        ctx = self.ctx
        plan, limit = self._expected()
        run_id = ctx.target.get("run_id")
        container = ctx.target.get("engine", {}).get("container")
        if (type(run_id) is not str or re.fullmatch(r"[a-z0-9][a-z0-9-]{3,39}", run_id) is None
                or container != f"guard-{run_id}-engine"):
            raise ValueError("unowned target")
        raw = self.docker.inspect_json(container, timeout=5)
        config, state, host = raw.get("Config", {}), raw.get("State", {}), raw.get("HostConfig", {})
        mounts = raw.get("Mounts")
        if (config.get("Labels", {}).get("guard.run") != run_id or state.get("Running") is not True
                or config.get("Image") != IMAGE or config.get("Cmd") != plan.argv
                or config.get("Entrypoint") != [plan.entrypoint] or host.get("ReadonlyRootfs") is not True
                or type(mounts) is not list or not mounts or not re.fullmatch(r"[a-f0-9]{64}", str(raw.get("Id")))
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(raw.get("Image")))
                or not isinstance(state.get("StartedAt"), str) or not state["StartedAt"]):
            raise ValueError("source identity unavailable")
        environment = config.get("Env")
        if type(environment) is not list or any(type(item) is not str or "=" not in item for item in environment):
            raise ValueError("environment unavailable")
        env_pairs = [item.split("=", 1) for item in environment]
        if len({pair[0] for pair in env_pairs}) != len(env_pairs):
            raise ValueError("ambiguous environment")
        env_values = dict(env_pairs)
        if (env_values.get("LD_PRELOAD") != NATIVE_PRELOAD
                or any(env_values.get(key) for key in ("PYTHONPATH", "PYTHONHOME"))
                or any(value for key, value in env_values.items() if key.startswith("LD_") and key != "LD_PRELOAD")):
            raise ValueError("unreviewed native environment")
        if ctx.trusted_net != f"guard-{run_id}-engine":
            raise ValueError("unowned trusted network")
        network = raw.get("NetworkSettings", {}).get("Networks", {}).get(ctx.trusted_net, {})
        address = str(ipaddress.IPv4Address(network.get("IPAddress")))
        parsed_url = urlsplit(ctx.engine_url)
        if (parsed_url.scheme != "http" or parsed_url.netloc != f"{address}:8000" or parsed_url.path
                or parsed_url.query or parsed_url.fragment or not ipaddress.IPv4Address(address).is_private
                or re.fullmatch(r"[a-f0-9]{64}", str(network.get("NetworkID"))) is None):
            raise ValueError("HTTP target does not match owned engine")
        model_mounts = [mount for mount in mounts if mount.get("Destination") == "/models"]
        if (len(model_mounts) != 1 or model_mounts[0].get("RW") is not False
                or model_mounts[0].get("Type") != "bind" or not model_mounts[0].get("Source")
                or host.get("Tmpfs") != HARDENED_TMPFS
                or any(mount.get("Destination") != "/models" and not (
                    mount.get("Type") == "tmpfs" and mount.get("Destination") in HARDENED_TMPFS
                    and mount.get("RW") is True) for mount in mounts)):
            raise ValueError("unreviewed mounts")
        self.container, self.limit = container, limit
        # Only digests/allowlisted fields leave this method. Env and host paths
        # are intentionally neither saved nor included in exception text.
        return {"verified": True, "container_id": raw["Id"], "image_id": raw["Image"],
                "image_digest": IMAGE, "started_at": state["StartedAt"], "owner": run_id,
                "trusted_network": ctx.trusted_net, "trusted_network_id": network["NetworkID"],
                "trusted_ip": address, "engine_url": ctx.engine_url,
                "command_sha256": _hash(config["Cmd"]), "mounts_sha256": _hash([mounts, host["Tmpfs"]]),
                "model": ctx.env.model.id, "max_model_len": limit}

    def _runtime(self):
        payload = {"root": SOURCE_ROOT, "sources": list(SOURCE_REVIEWS), "limit": self.limit,
                   "native_preload": NATIVE_PRELOAD, "native_libraries": NATIVE_LIBRARIES,
                   "tail": self.ctx.target["comparison_config"]["launch"]["argv"][3:]}
        result = self.docker.exec_bounded(self.container, ["python3", "-I", "-c", RUNTIME_READER,
            json.dumps(payload, separators=(",", ":"))], timeout=TIMEOUT, max_bytes=MAX_BYTES)
        capture = result.capture or {}
        if (result.returncode != 0 or capture.get("bounded_at_source") is not True
                or capture.get("complete") is not True or capture.get("truncated") or capture.get("timed_out")
                or len(result.stdout.encode()) >= MAX_BYTES):
            self.data.setdefault("runtime_diagnostics", []).append({"code": "RUNTIME_CAPTURE_INCOMPLETE",
                "returncode_zero": result.returncode == 0, "bounded_at_source": capture.get("bounded_at_source") is True,
                "complete": capture.get("complete") is True, "timed_out": capture.get("timed_out") is True,
                "truncated": capture.get("truncated") is True})
            raise ValueError("runtime collection incomplete")
        value = json.loads(result.stdout, object_pairs_hook=_unique)
        if not runtime_valid(value, self.limit):
            self.data.setdefault("runtime_diagnostics", []).append({"code": "RUNTIME_PROFILE_MISMATCH",
                **{key: value.get(key) is True for key in ("same_relevant_environment", "no_python_path_injection",
                    "no_unreviewed_loader_environment", "isolated_python", "same_api_command", "same_interpreter",
                    "intel_cpu", "module_root_matches")},
                "source_hashes_match": value.get("source_sha256") == {name: item[0] for name, item in SOURCE_REVIEWS.items()},
                "native_libraries_match": value.get("native_libraries") == {"profile": "reviewed-cpu-native-libraries/1",
                    "preload_sha256": NATIVE_PRELOAD_SHA256, "library_sha256": NATIVE_LIBRARIES},
                "cpu_platform_matches": value.get("platform_class") == "vllm.platforms.cpu.CpuPlatform"})
            raise ValueError("runtime unreviewed")
        return value

    def _models(self, phase):
        ctx = self.ctx
        request = ctx.request("length-cause-models-" + phase, ctx.engine_url,
                              ProbeRequest("GET", "/v1/models"), ctx.keys.get("allowed"), want_body=True)
        result = ctx.cprobe(ctx.trusted_net, [request], min(ctx.timeout, 10))
        if type(result) is not list or len(result) != 1 or type(result[0]) is not dict:
            raise ValueError("model response unavailable")
        value, digest = _json_response(result[0])
        data = value.get("data")
        if (value.get("object") != "list" or type(data) is not list or len(data) != 1
                or type(data[0]) is not dict or data[0].get("id") != ctx.env.model.id
                or type(data[0].get("max_model_len")) is not int or data[0]["max_model_len"] != self.limit):
            raise ValueError("model limit mismatch")
        return {"model": data[0]["id"], "max_model_len": self.limit, "response_sha256": digest}

    def start(self):
        try:
            self.data["before"] = self._snapshot()
            self.data["reader_sha256"] = hashlib.sha256(RUNTIME_READER.encode()).hexdigest()
            self.data["runtime"] = {"before": self._runtime()}
            self.data["models"] = {"before": self._models("before")}
            self.data["reason"] = "LENGTH_CAUSE_AWAITING_PAIRED_CONTROLS"
        except Exception:
            self.data["reason"] = "LENGTH_CAUSE_PRECONDITIONS_UNVERIFIED"

    def finish(self, samples):
        try:
            if self.data["reason"] != "LENGTH_CAUSE_AWAITING_PAIRED_CONTROLS":
                return self.data
            self.data["models"]["after"] = self._models("after")
            self.data["runtime"]["after"] = self._runtime()
            self.data["after"] = self._snapshot()
            self.data["model_inventory_sha256"] = self.ctx.env.model.inventory_sha256
            controls = {}
            for sample in samples:
                if sample["case"] not in {"before", "after_token_id", "after_length"}:
                    continue
                value, _ = _json_response(sample["response"])
                usage = value.get("usage", {})
                choices = value.get("choices")
                valid_generation = (type(choices) is list and bool(choices)
                    and all(type(choice) is dict and type(choice.get("text")) is str
                            and choice.get("finish_reason") in {"stop", "length"} for choice in choices)
                    and sample.get("request", {}).get("body") == {"model": MODEL, "prompt": [0], "max_tokens": 1})
                controls[sample["case"]] = {"model": value.get("model"),
                    "valid_generation": valid_generation,
                    **{key: usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}}
            self.data["controls"] = controls
            length_samples = [sample for sample in samples if sample["case"] == "length"]
            if len(length_samples) != 1 or not context_valid(self.data, length_samples[0]["request"]["body"]):
                raise ValueError("cause observations incomplete")
            self.data.update(state="verified", reason="REVIEWED_CPU_CONTEXT_EXHAUSTION")
        except Exception:
            self.data.update(state="unverified", reason="LENGTH_CAUSE_OBSERVATIONS_UNVERIFIED")
        return self.data


def reviewed_zero_budget(context, request_body):
    """The caller already verified the exact pinned image/request/error shape."""
    if not context_valid(context, request_body):
        return None
    return {"state": "verified", "reason": "REVIEWED_CPU_CONTEXT_EXHAUSTION",
            "sources": [{"path": "vllm/" + name, "sha256": sha, "lines": lines}
                        for name, (sha, lines) in SOURCE_REVIEWS.items()]}
