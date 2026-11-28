"""Collect call boundaries from exact stock startup code without wrapping it."""

import hashlib
import json
import os
from pathlib import Path, PosixPath
import re
import stat
import sys
import threading
import time
from types import CodeType, FunctionType, ModuleType


TOOL = 3
MAX_ROWS = 360
SITE_PROFILE = Path("/cache/home/.local/lib/python3.12/site-packages/byul_owned_trace_profile.json")
WRITER = Path("/cache/byul-boot-trace")
ENV_MARKERS = ("VLLM_PLUGINS", "VLLM_LORA_RESOLVER_CACHE_DIR", "VLLM_LORA_RESOLVER_HF_REPO_LIST")
REGISTRY_LABELS = ("Filesystem Resolver", "Hf Hub Resolver")
MODE_FLAGS = ("optimize", "isolated", "ignore_environment", "no_site", "no_user_site", "safe_path")
AOT_OBJECT = re.compile(r"/cache/vllm/torch_compile_cache/torch_aot_compile/[0-9a-f]{64}/rank_0_0/model")
ARTIFACT_STATS = {"device":"st_dev","inode":"st_ino","size":"st_size","uid":"st_uid",
                  "gid":"st_gid","mode":"st_mode","mtime_ns":"st_mtime_ns","ctime_ns":"st_ctime_ns"}
MODEL_MODULE = "vllm.model_executor.models.qwen2_vl"
MODEL_CLASS = "Qwen2VLForConditionalGeneration"
MODEL_ROLES = ("model-loader-return", "model-registry-return")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def code_objects(code):
    yield code
    for item in code.co_consts:
        if type(item) is CodeType:
            yield from code_objects(item)


def time_namespace():
    try:
        return Path("/proc/self/ns/time").stat().st_ino
    except FileNotFoundError:
        return None


def startup_actor(argv):
    script = next((arg for arg in argv[1:] if not arg.startswith("-")), None)
    if script in {"/usr/local/bin/vllm", "/usr/bin/vllm", "/opt/venv/bin/vllm"}:
        return "stock-vllm-cli"
    # CPython multiprocessing spawns stock EngineCore in a fresh interpreter.
    if ("-c" in argv and len(argv) > argv.index("-c") + 1
            and re.fullmatch(r"from multiprocessing\.spawn import spawn_main; spawn_main\([\w=, ]+\)",
                             argv[argv.index("-c") + 1])):
        return "stock-multiprocessing-spawn"
    return None


def compile_profile(profile):
    code = {}
    for path, row in profile["sources"].items():
        filename = Path(path)
        if filename.resolve() != filename:
            raise ValueError("STARTUP_TRACE_SOURCE_PATH_UNVERIFIED")
        with filename.open("rb") as stream:
            raw = stream.read(512 * 1024 + 1)
        if not 0 < len(raw) <= 512 * 1024 or digest(raw) != row["sha256"]:
            raise ValueError("STARTUP_TRACE_SOURCE_PIN_MISMATCH")
        source_code = compile(raw, path, "exec", dont_inherit=True, optimize=sys.flags.optimize)
        declarations = {item.co_qualname: item for item in code_objects(source_code)}
        for qualname, spec in row["qualnames"].items():
            code[path, qualname] = (declarations[qualname], spec, row["sha256"])
    return code


def registry_fields(receiver):
    cls = type(receiver)
    if type(cls) is not type or cls.__name__ != "_LoRAResolverRegistry" or cls.__module__ != "vllm.lora.resolver":
        return None
    values = object.__getattribute__(receiver,"__dict__").get("resolvers")
    return values if type(values) is dict else None


def registry_event(frame):
    local = frame.f_locals
    name = local.get("resolver_name")
    values = registry_fields(local.get("self"))
    if type(name) is not str or name not in REGISTRY_LABELS or values is None:
        return None
    return {"approved_registry_label":name,"entry_present":name in values,"registry_count":len(values),
            "selected_instance_is_call_argument":name in values and values[name] is local.get("resolver")}


def registry_after_loader():
    module = sys.modules.get("vllm.lora.resolver")
    if module is None:
        return None
    values = registry_fields(vars(module).get("LoRAResolverRegistry"))
    if values is None:
        return None
    return {"registry_count":len(values),"known_labels_present":[label for label in REGISTRY_LABELS if label in values]}


def observed_model_class(cls, origin):
    if not isinstance(cls, type) or type.__getattribute__(cls, "__module__") != MODEL_MODULE:
        return None
    module = sys.modules.get(MODEL_MODULE)
    if type(module) is not ModuleType:
        return None
    namespace = vars(module)
    constructor = type.__getattribute__(cls, "__dict__").get("__init__")
    filename = origin["file"]
    spec = namespace.get("__spec__")
    if (namespace.get(MODEL_CLASS) is not cls or type(constructor) is not FunctionType
            or type.__getattribute__(cls, "__qualname__") != MODEL_CLASS
            or namespace.get("__file__") != filename or getattr(spec, "origin", None) != filename
            or constructor.__globals__ is not namespace or constructor.__code__.co_filename != filename):
        return None
    try:
        path = Path(filename)
        if path.resolve() != path:
            return None
        with path.open("rb") as stream:
            raw = stream.read(512 * 1024 + 1)
        if not 0 < len(raw) <= 512 * 1024 or digest(raw) != origin["sha256"]:
            return None
        compiled = compile(raw, filename, "exec", dont_inherit=True, optimize=sys.flags.optimize)
        if not any(code.co_qualname == f"{MODEL_CLASS}.__init__" and code == constructor.__code__
                   for code in code_objects(compiled)):
            return None
    except (OSError, RuntimeError, SyntaxError, ValueError):
        return None
    return {"module": MODEL_MODULE, "qualname": MODEL_CLASS, "source_file": filename,
            "source_sha256": origin["sha256"]}


def loaded_module_origin(frame, source_sha):
    namespace = frame.f_globals
    name = namespace.get("__name__")
    if type(name) is not str:
        return None
    module = sys.modules.get(name)
    if type(module) is not ModuleType or vars(module) is not namespace:
        return None
    if type(namespace.get("__file__")) is not str or namespace["__file__"] != frame.f_code.co_filename:
        return None
    return {"module_name_sha256": digest(encode(name)), "file_sha256": source_sha}


def model_return(role, frame, value, origin):
    config = frame.f_locals.get("model_config")
    cls = type(config)
    if cls.__module__ != "vllm.config.model" or cls.__qualname__ != "ModelConfig":
        return None
    try:
        model = vars(config)
    except TypeError:
        return None
    if model.get("model") != origin["model"] or model.get("trust_remote_code") is not False:
        return None
    if role == "model-registry-return":
        if type(value) is not tuple or len(value) != 2 or value[1] != MODEL_CLASS:
            return None
        cls = value[0]
    else:
        cls = type(value)
    observed = observed_model_class(cls, origin)
    if observed is None:
        return None
    return {**observed, "model": origin["model"], "trust_remote_code": False}


def candidate_artifact(frame, point):
    if point["role"] == "aot-reader":
        path = frame.f_locals.get("aot_compilation_path")
    elif point["role"] == "aot-writer":
        receiver = frame.f_locals.get("self")
        try:
            path = object.__getattribute__(receiver,"__dict__").get("_aot_compilation_path")
        except AttributeError:
            return None
    elif point["role"] == "config-parser":
        value = frame.f_locals.get(point["path_parameter"])
        path = str(value) if type(value) in {str, PosixPath} else None
        return path if path == point["approved_path"] else None
    else:
        return None
    return path if type(path) is str and AOT_OBJECT.fullmatch(path) else None


def aot_stat(path):
    try:
        metadata = os.stat(path,follow_symlinks=False)
    except FileNotFoundError:
        return {"state":"absent"}
    except OSError:
        return {"state":"unavailable"}
    if not stat.S_ISREG(metadata.st_mode):
        return {"state":"unavailable"}
    return {"state":"file",**{key:getattr(metadata,name) for key,name in ARTIFACT_STATS.items()}}


class StartupTrace:
    def __init__(self, profile, fd, actor):
        self.fd = fd
        self.session = profile["session"]
        self.code = compile_profile(profile)
        self.groups = set(profile.get("groups", ()))
        self.features = profile.get("feature_settings", {})
        self.lock = threading.Lock()
        self.seq = 0
        self.active = {}
        self.errors = set()
        self.armed_code = set()
        self.monitor = sys.monitoring
        self.report("interpreter", actor=actor, sources={
            name: item["sha256"] for name, item in profile["sources"].items()},
            entry_points_file_sha256=profile.get("entry_points_file_sha256"),
            trial_approval_sha256=profile.get("trial_approval_sha256"),
            python_flags={name: getattr(sys.flags, name) for name in MODE_FLAGS},
            settings={key: "unset" if key not in os.environ else "empty" if not os.environ[key] else "set"
                      for key in ENV_MARKERS}, feature_settings=self.features,
            start_ticks=int(Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19]),
            pid_namespace=Path("/proc/self/ns/pid").stat().st_ino,
            mount_namespace=Path("/proc/self/ns/mnt").stat().st_ino,
            time_namespace=time_namespace(),
            boot_sha256=digest(Path("/proc/sys/kernel/random/boot_id").read_bytes().strip()))

    def report(self, kind, **values):
        with self.lock:
            if self.seq >= MAX_ROWS:
                return None
            row = {"session": self.session, "kind": kind, "seq": self.seq, "pid": os.getpid(),
                   "receipt_utc_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(), **values}
            payload = encode(row) + b"\n"
            if len(payload) > 4096 or os.write(self.fd, payload) != len(payload):
                raise ValueError("STARTUP_TRACE_WRITE_UNAVAILABLE")
            self.seq += 1
            return row["seq"]

    def arm(self):
        self.monitor.use_tool_id(TOOL, "byul-owned-startup")
        ev = self.monitor.events
        self.monitor.register_callback(TOOL, ev.PY_START, self.call)
        self.monitor.register_callback(TOOL, ev.PY_RETURN, self.returned)
        self.monitor.register_callback(TOOL, ev.PY_UNWIND, self.unwind)
        self.monitor.set_events(TOOL, ev.PY_START | ev.PY_UNWIND)

    def release(self):
        self.monitor.set_events(TOOL, self.monitor.events.NO_EVENTS)
        for ev in self.armed_code:
            self.monitor.set_local_events(TOOL, ev, self.monitor.events.NO_EVENTS)
        self.monitor.free_tool_id(TOOL)

    def mismatch(self, code):
        key = (code.co_filename, code.co_qualname)
        if key not in self.errors:
            self.errors.add(key)
            self.report("diagnostic", reason="STARTUP_TRACE_CODE_OR_FRAME_UNVERIFIED",
                        class_name=code.co_qualname, expected_source_sha256=self.code[key][2])

    def call(self, code, _offset):
        entry = self.code.get((code.co_filename, code.co_qualname))
        if entry is None:
            return self.monitor.DISABLE
        expected, point, source_sha = entry
        frame = sys._getframe(1)
        if frame.f_code is not code or expected != code:
            self.mismatch(code)
            return self.monitor.DISABLE
        role = point["role"]
        group = point.get("group")
        if role == "group-loader":
            group = frame.f_locals.get("group")
            if type(group) is not str or group not in self.groups:
                return None
        if role in {"hf-config","engine-config"}:
            model = frame.f_locals.get("model")
            if (type(model) not in {str,PosixPath} or str(model) != "/models"
                    or frame.f_locals.get("trust_remote_code") is not False):
                return None
        artifact = candidate_artifact(frame, point)
        if role in {"aot-reader","aot-writer","config-parser"} and artifact is None:
            return None
        parent = None
        outer = frame.f_back
        for _ in range(12):
            if outer is None:
                break
            if id(outer) in self.active:
                parent = self.active[id(outer)][0]
                break
            outer = outer.f_back
        call = self.report("enter", role=role, point=code.co_qualname, group=group,
                           source_sha256=source_sha, parent_call=parent,
                           loaded_module=loaded_module_origin(frame, source_sha),
                           entry_fingerprint=point.get("entry_fingerprint"),
                           registry_at_enter=registry_event(frame) if role == "registry-write" else None,
                           artifact_path_sha256=digest(artifact.encode()) if artifact else None,
                           artifact_at_entry=aot_stat(artifact) if artifact else None)
        if call is not None:
            self.active[id(frame)] = (call, point, group, artifact)
            self.armed_code.add(code)
            self.monitor.set_local_events(TOOL, code, self.monitor.events.PY_RETURN)
        return None

    def _finish(self, frame, code, outcome, value):
        item = self.active.pop(id(frame), None)
        if item is None or frame.f_code is not code:
            return
        call, point, group, artifact = item
        summary = None
        if outcome == "return":
            summary = {"return_kind": "none" if value is None else "object"}
            if type(value) is list:
                summary = {"return_kind": "list", "count": len(value)}
            elif type(value) is dict:
                summary = {"return_kind": "dict", "count": len(value)}
            if point["role"] == "config-parser":
                summary["approved_model_type_parsed"] = (
                    type(value) is dict and value.get("model_type") == "qwen2_vl")
            elif point["role"] == "hf-config":
                summary["approved_config_pair_returned"] = (
                    type(value) is tuple and len(value)==2 and type(value[0]) is dict
                    and value[0].get("model_type")=="qwen2_vl" and type(value[1]).__name__=="Qwen2VLConfig")
            elif point["role"] == "engine-config":
                summary["approved_config_instance_returned"] = type(value).__name__=="Qwen2VLConfig"
            if point["role"] == "registry-write":
                summary["registry_at_return"] = registry_event(frame)
            elif point["role"] == "general-loader":
                summary["registry_at_loader_return"] = registry_after_loader()
            elif point["role"] in MODEL_ROLES:
                summary["model_class_origin"] = model_return(point["role"], frame, value, point["model_origin"])
            if artifact:
                summary["same_artifact_path"] = candidate_artifact(frame,point) == artifact
                summary["artifact_at_return"] = aot_stat(artifact)
            if point["role"] in {"group-loader", "endpoint-loader"}:
                candidates = frame.f_locals.get(point.get("discovered_variable", "discovered_plugins"))
                if isinstance(candidates, tuple):
                    summary["discovered_count"] = tuple.__len__(candidates)
                if type(value) is dict:
                    matches = []
                    for name, callback in value.items():
                        if type(callback) is FunctionType:
                            f = callback.__code__
                            declared = self.code.get((f.co_filename, f.co_qualname))
                            if declared and declared[0] == f and declared[1].get("external_name") == name:
                                matches.append(declared[1]["entry_fingerprint"])
                    summary["selected_entry_fingerprints"] = sorted(matches)
        self.report(outcome, call=call, role=point["role"], group=group, result=summary)

    def returned(self, code, _offset, value):
        self._finish(sys._getframe(1), code, "return", value)

    def unwind(self, code, _offset, _exception):
        if (code.co_filename, code.co_qualname) in self.code:
            self._finish(sys._getframe(1), code, "unwind", None)


def bootstrap():
    actor = startup_actor(sys.orig_argv)
    if actor is None:
        return
    fd = None
    try:
        profile = json.loads(SITE_PROFILE.read_bytes())
        subdirectory = profile.get("journal_subdirectory")
        if subdirectory and not re.fullmatch(r"[A-Z0-9-]{12,80}", subdirectory):
            raise ValueError("STARTUP_TRACE_JOURNAL_UNBOUND")
        destination = WRITER / subdirectory if subdirectory else WRITER
        name = destination / f"{os.getpid()}.jsonl"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        trace = StartupTrace(profile, fd, actor)
        trace.arm()
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        if fd is not None:
            os.write(fd, b'{"kind":"diagnostic","reason":"STARTUP_TRACE_BOOTSTRAP_UNAVAILABLE"}\n')
            os.close(fd)
