"""Opt-in standalone native lifecycle metadata producer; no verdict or cleanup.

Exported unchanged for a dedicated owned engine. Fixed source/code gates run
before hooks are installed. Failure never repairs native state or hides native
exceptions. Files are bounded observations, not authenticated producer records.
"""
from __future__ import annotations

import atexit
import contextvars
from collections import deque
from itertools import islice
import functools
import hashlib
import importlib.abc
import importlib.machinery
import inspect
import json
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import sys
import threading
import time
import types
import weakref

PROFILE = "vllm-native-lifecycle-metadata/4"
COMMIT = "db9527a46873454610df6dbedf79a36d6bf1a7f6"
SCHEMA = "guard-native-lifecycle-events/4"
CONFIG_SCHEMA = "guard-native-lifecycle-settings/1"
CONFIG_ENV = "GUARD_LIFECYCLE_CONFIG"
CONFIG_PATH = "/guard-lifecycle/settings.json"
MAX_ROWS = 512
MAX_BYTES = 262144
MAX_IDS = 8
MAX_MODEL_OBJECTS = 64
SOURCE_SHA = {
 "vllm.model_executor.model_loader.weight_utils":"7d2573931b9684147b17018c1f6de318eea1c105556ffea03154201a72418afd",
 "vllm.model_executor.model_loader.base_loader":"5291c330c4ed5634a9ea1d535364c26162b60251aa2af03fd409b88c34d836e2",
 "vllm.model_executor.model_loader.default_loader":"d17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9",
 "vllm.v1.engine.input_processor":"d79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b",
 "vllm.v1.engine.output_processor":"c72d0149e18d6da71d53e23b6550d09310af6917523fef6e282b0907d36b8d54",
 "vllm.v1.engine.async_llm":"ff933ed78aa3195782cf1c6d18c53897adfa5bf920421bd2bcc330f2adc84d1e",
 "vllm.v1.engine.core":"f116962c35d9d312b20b0b76c34a8cdaa6d3f1d31f2036db4e123957d2d1555b",
 "vllm.v1.engine.core_client":"24eecc78e8fc4849d4eac948c7586ee349ebfd503d07e80f05ec6c859da06fdd",
 "vllm.v1.core.sched.scheduler":"f820bee4f42b878b262a30f3728a97d7413477b2a277748e20f2528ac2ec2f2a",
 "vllm.v1.core.sched.output":"972e0bc8e7af1f4262ac73f660f6b8f798ba780f86c031a9c8a7f10cbfaad3a8",
 "vllm.v1.core.kv_cache_manager":"bd9bf46a2101f9368d4941867415a239d18d95e0b956f82f885ce25e10e69f3b",
 "vllm.v1.core.single_type_kv_cache_manager":"3a3cb20bd6aa1e0ad46471fc9e5e01ee5797ab4bb759e00ef5fb309d2830db13",
 "vllm.v1.core.kv_cache_coordinator":"6a1e00227b981a7bb6108a42db0e446c716f789274f40c63eb9a6ce4d699ddcb",
 "vllm.v1.core.block_pool":"05c73052934a9d4664ccd5df562ce71011278d9a511932da102ef2efff00e934",
 "vllm.v1.worker.gpu_model_runner":"bb903dffcb5a6e5b8a477daf0854b3812e135349d61fc20c6133f91cd3c9752a",
 "vllm.v1.worker.gpu_input_batch":"e7e57887b396014a84d64cab26bf678f982844fea15361b09d95f36bd78bc58d",
 "vllm.v1.worker.cpu_model_runner":"69cd626ddbc3b1693e026fda99018201d8c9f4dd559540970e8ff265b7a97093",
 "vllm.v1.request":"dd7c30bf8b0894dc123a739c299c0133566f8cc075b081e45905f1c29506e69d",
 "vllm.entrypoints.serve.utils.api_utils":"ffb5e08c2b6cf6d0402c6ba31d0d05e53eb7e9e8c13b29700e84a4bfada420ba",
 "vllm.entrypoints.openai.completion.serving":"aac889978efb034b95ab81ec59695d83ba7c879ab48cd5be2448c980f109a3bd",
}
POINTS = {
 "vllm.model_executor.model_loader.base_loader":{"BaseModelLoader":("load_model",)},
 "vllm.model_executor.model_loader.default_loader":{"DefaultModelLoader":("load_weights","_prepare_weights","_get_weights_iterator")},
 "vllm.v1.worker.cpu_model_runner":{"CPUModelRunner":("load_model","get_model")},
 "vllm.v1.engine.input_processor":{"InputProcessor":("assign_request_id",)},
 "vllm.v1.engine.output_processor":{"OutputProcessor":("add_request","abort_requests","_finish_request")},
 "vllm.v1.engine.async_llm":{"AsyncLLM":("abort",)},
 "vllm.v1.engine.core":{"EngineCore":("abort_requests",)},
 "vllm.v1.core.sched.scheduler":{"Scheduler":("finish_requests","_free_request_blocks","_drain_deferred_frees")},
 "vllm.v1.core.kv_cache_manager":{"KVCacheManager":("free","pop_blocks_for_free")},
 "vllm.v1.core.single_type_kv_cache_manager":{"SingleTypeKVCacheManager":("free","pop_blocks_for_free")},
 "vllm.v1.core.block_pool":{"BlockPool":("free_blocks",)},
 "vllm.v1.worker.gpu_model_runner":{"GPUModelRunner":("_update_states","execute_model","sample_tokens","load_model","get_model","_model_forward"),
   "AsyncGPUModelRunnerOutput":("get_output",),"AsyncGPUPoolingModelRunnerOutput":("get_output",)},
 "vllm.v1.worker.gpu_input_batch":{"InputBatch":("remove_request",)},
}
_CHAIN = contextvars.ContextVar("guard_native_lifecycle_chain", default=())
_ASGI_REQUEST = contextvars.ContextVar("guard_native_lifecycle_asgi_request", default=None)
FUNCTION_POINTS = {"vllm.model_executor.model_loader.weight_utils": (
    "safetensors_weights_iterator","pt_weights_iterator","multi_thread_pt_weights_iterator",
    "multi_thread_safetensors_weights_iterator","fastsafetensors_weights_iterator",
    "instanttensor_weights_iterator","np_cache_weights_iterator")}
WEIGHT_POINTS = {"weight_utils."+name for names in FUNCTION_POINTS.values() for name in names} | {
    "DefaultModelLoader._prepare_weights","DefaultModelLoader._get_weights_iterator"}
_OBSERVER = None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def code_digest(code):
    # Stable across compile()/pyc deserialization: marshal reference flags are
    # implementation details and may differ for equal code object graphs.
    def item(value):
        if isinstance(value, types.CodeType):
            return {"kind":"code", "name":value.co_name,"qualname":value.co_qualname,
                "filename":value.co_filename,"firstlineno":value.co_firstlineno,
                "args":[value.co_argcount,value.co_posonlyargcount,value.co_kwonlyargcount],
                "flags":value.co_flags,"stack":value.co_stacksize,"locals":value.co_nlocals,
                "bytecode":value.co_code.hex(),"lines":value.co_linetable.hex(),
                "exceptions":value.co_exceptiontable.hex(),"names":value.co_names,
                "vars":value.co_varnames,"free":value.co_freevars,"cells":value.co_cellvars,
                "constants":[item(v) for v in value.co_consts]}
        if value is Ellipsis:
            return {"kind":"ellipsis"}
        if type(value) is bytes:
            return {"kind":"bytes","value":value.hex()}
        if type(value) is float:
            return {"kind":"float","value":value.hex()}
        if type(value) is complex:
            return {"kind":"complex","real":value.real.hex(),"imag":value.imag.hex()}
        if type(value) in (tuple,frozenset):
            children=[item(v) for v in value]
            if type(value) is frozenset:
                children.sort(key=lambda v:json.dumps(v,sort_keys=True))
            return {"kind":"tuple" if type(value) is tuple else "frozenset","value":children}
        if value is None or type(value) in (str,int,bool):
            return value
        raise ValueError("NATIVE_SENSOR_CODE_CONSTANT_UNSUPPORTED")
    return sha(json.dumps(item(code),sort_keys=True,separators=(",",":"),ensure_ascii=True).encode())


def id_sha(value):
    if type(value) is not str or not 0 < len(value) <= 512:
        return None
    return sha(value.encode("utf-8", "surrogatepass"))


class Fields:
    __slots__ = ("value",)
    def __init__(self, value):
        self.value = value

    def get(self, name, default=None):
        try:
            data = object.__getattribute__(self.value, "__dict__")
        except (AttributeError, TypeError):
            data = {}
        if type(data) is dict and name in data:
            return data[name]
        descriptor = inspect.getattr_static(type(self.value), name, None)
        if type(descriptor) is types.MemberDescriptorType:
            return descriptor.__get__(self.value, type(self.value))
        return default


def attrs(value):
    # Supports native msgspec/dataclass slots without invoking arbitrary properties.
    return Fields(value)


def _open_directory(path):
    parts = PurePosixPath(path).parts
    if not parts or parts[0] != "/" or any(p in {".", ".."} for p in parts):
        raise ValueError("NATIVE_SENSOR_PATH_INVALID")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def ids(value):
    # Never consume an input Iterator/generator merely to observe it.
    if type(value) is str:
        return (value,) if id_sha(value) else ()
    if type(value) is dict:
        value = value.keys()
    if type(value) in (list, tuple, set, frozenset) or type(value) is type({}.keys()):
        if len(value) <= MAX_IDS and all(id_sha(v) for v in value):
            return tuple(value)
    return ()


def _int(value):
    return int(value) if isinstance(value, int) and type(value) is not bool and 0 <= value < 2**63 else None


def _request_ids(value):
    if type(value) in (list, tuple) and len(value) <= MAX_IDS:
        return tuple(r for v in value if id_sha(r := attrs(v).get("request_id")))
    return ()


def _mapping(value, key):
    mapping = attrs(value).get(key)
    return mapping if type(mapping) is dict else None


def _present(value, key, rid):
    mapping = _mapping(value, key)
    return rid in mapping if mapping is not None else None


def _context():
    if sys.platform != "linux" or sys.version_info[:2] != (3, 12):
        raise ValueError("NATIVE_SENSOR_RUNTIME_UNSUPPORTED")
    raw = Path("/proc/self/stat").read_text()
    fields = raw[raw.rfind(")") + 2:].split()
    return {"pid": os.getpid(), "start_ticks": int(fields[19]), "uid": os.geteuid(),
        "boot_sha256": sha(Path("/proc/sys/kernel/random/boot_id").read_bytes()),
        "pid_namespace": os.stat("/proc/self/ns/pid").st_ino,
        "time_namespace": os.stat("/proc/self/ns/time").st_ino,
        "time_offsets_sha256": sha(Path("/proc/self/timens_offsets").read_bytes())}


def load_settings(path):
    raw = Path(path).read_bytes()
    if len(raw) > 4096:
        raise ValueError("NATIVE_SENSOR_SETTINGS_BOUND")
    def pairs(items):
        value = {}
        for key, child in items:
            if key in value:
                raise ValueError("NATIVE_SENSOR_SETTINGS_DUPLICATE")
            value[key] = child
        return value
    config = json.loads(raw, object_pairs_hook=pairs)
    if (type(config) is not dict or set(config) != {"schema", "session", "output_dir", "control_path"}
            or config["schema"] != CONFIG_SCHEMA or type(config["session"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", config["session"]) is None
            or config["output_dir"] != "/guard-lifecycle/data/" + config["session"]
            or config["control_path"] != config["output_dir"] + "/SEALED"):
        raise ValueError("NATIVE_SENSOR_SETTINGS_INVALID")
    return config, sha(raw)


class Writer:
    def __init__(self, config, config_sha, context):
        self.config, self.context = config, context
        self.lock, self.seq, self.bytes, self.closed = threading.RLock(), 0, 0, False
        self.previous = "0"*64
        self.fd = None
        # Directory must already be a private owned mount; do not create a path.
        directory = _open_directory(config["output_dir"])
        try:
            stat = os.fstat(directory)
            if stat.st_uid != context["uid"] or stat.st_mode & 0o077:
                raise ValueError("NATIVE_SENSOR_SINK_UNVERIFIED")
            name = f"pid-{context['pid']}-{context['start_ticks']}.jsonl"
            self.fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        finally:
            os.close(directory)
        self.write("START", {"schema": SCHEMA, "profile": PROFILE, "commit": COMMIT, "session": config["session"],
            "settings_sha256": config_sha, "sensor_sha256": sha(Path(__file__).read_bytes()),
            "context": context, "producer_authenticated": False})
        try:
            thread = threading.Thread(target=self._watch, name="guard-lifecycle-seal", daemon=True)
            thread.start()
        except Exception:
            self.close("CONTROL_UNAVAILABLE")

    def _watch(self):
        try:
            while not self.closed:
                if Path(self.config["control_path"]).exists():
                    self.close("SEALED")
                    return
                threading.Event().wait(0.1)
        except Exception:
            try:
                self.close("CONTROL_UNAVAILABLE")
            except Exception:
                pass

    def _append(self, event, payload):
        value = {"seq": self.seq, "event": event, "monotonic_ns": time.monotonic_ns(),
                 "previous_sha256": self.previous, "payload": payload}
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        if len(raw) > 16384 or self.bytes + len(raw) > MAX_BYTES:
            raise ValueError("NATIVE_SENSOR_RECORD_BOUND")
        if os.write(self.fd, raw) != len(raw):
            raise OSError("NATIVE_SENSOR_PARTIAL_WRITE")
        self.previous = sha(raw)
        self.seq += 1
        self.bytes += len(raw)

    def write(self, event, payload):
        with self.lock:
            if self.closed:
                return
            try:
                if self.seq >= MAX_ROWS-1 or self.bytes > MAX_BYTES-16384:
                    self.close("BOUND_REACHED")
                else:
                    self._append(event, payload)
            except Exception:
                # A missing END/partial line is rejected downstream; never repair.
                self.closed = True
                if self.fd is not None:
                    os.close(self.fd)
                    self.fd = None

    def close(self, reason):
        with self.lock:
            if self.closed:
                return
            try:
                self._append("END", {"reason": reason})
            finally:
                self.closed = True
                if self.fd is not None:
                    os.close(self.fd)
                    self.fd = None


class Observer:
    def __init__(self, config, config_sha):
        self.config, self.config_sha = config, config_sha
        self.writer = None
        self.pid = os.getpid()
        self.modules = {}
        self.pools = {}
        self.pool_serial = 0
        self.disabled_pid = None
        self.empty_points = set()
        self.model_objects = {}
        self.model_serial = 0
        self.model_lock = threading.RLock()
        self.weight_serial = 0

    def active(self):
        return self.disabled_pid != os.getpid() and not (self.pid == os.getpid()
            and self.writer is not None and self.writer.closed)

    def wanted(self, point, snapshot):
        if point in WEIGHT_POINTS:
            return True
        if point in {"BaseModelLoader.load_model","DefaultModelLoader.load_weights",
                     "CPUModelRunner.load_model","GPUModelRunner.load_model"}:
            return True
        if point == "GPUModelRunner._model_forward" and not snapshot["requests"]:
            return False  # Warmup/model profiling is not a request association.
        if snapshot["requests"] or snapshot["metadata"].get("pool",{}).get("blocks"):
            return True
        if point.endswith("._drain_deferred_frees") and any(p["blocks"] for p in self.pools.values()):
            return True
        if point not in self.empty_points:
            self.empty_points.add(point)
            return True
        return False

    def _writer(self):
        forked = self.pid != os.getpid()
        if forked:
            # Fork inherits code hooks, not a recording identity or open sink.
            if self.writer is not None and self.writer.fd is not None:
                os.close(self.writer.fd)
            self.writer, self.pools, self.pool_serial = None, {}, 0
            self.model_objects, self.model_serial, self.model_lock = {}, 0, threading.RLock()
            self.weight_serial = 0
            self.disabled_pid, self.empty_points = None, set()
            self.pid = os.getpid()
        if self.writer is None:
            self.writer = Writer(self.config, self.config_sha, _context())
            if forked:
                for module, info in self.modules.items():
                    self.writer.write("MODULE", {"module":module,"verified":True,"inherited":True,**info})
        return self.writer

    def emit(self, event, payload):
        try:
            self._writer().write(event, payload)
        except Exception:
            self.disabled_pid = os.getpid()  # Missing records never prove no native activity.

    def close(self):
        try:
            if self.writer is not None and self.pid == os.getpid():
                self.writer.close("PROCESS_EXIT")
        except Exception:
            pass

    def pool(self, pool):
        key = id(pool)
        previous = self.pools.get(key)
        if previous is not None and previous["weak"]() is pool:
            return previous
        if len(self.pools) >= MAX_IDS:
            return None
        self.pool_serial += 1
        context = self._writer().context
        domain = f"{self.config['session']}:{context['pid']}:{context['start_ticks']}:{self.pool_serial}".encode()
        entry = {"weak": weakref.ref(pool, lambda _: self.pools.pop(key, None)),
                 "pool_sha256": sha(domain), "blocks": {}}
        self.pools[key] = entry
        return entry

    def blocks(self, owner, request_ids):
        # Numeric copies and weak pool references only; never retain a block/tensor.
        data = attrs(owner)
        manager = data.get("kv_cache_manager", owner)
        managers = attrs(attrs(manager).get("coordinator")).get("single_type_managers")
        if type(attrs(manager).get("req_to_blocks")) is dict:
            managers = (manager,)
        if type(managers) not in (list, tuple) or len(managers) > MAX_IDS:
            return {"complete": False, "groups": []}
        rows, complete = [], True
        for child in managers:
            values = attrs(child)
            pool = values.get("block_pool")
            entry = self.pool(pool)
            mapping = values.get("req_to_blocks")
            if entry is None or type(mapping) is not dict:
                complete = False
                continue
            for rid in request_ids:
                blocks = mapping.get(rid, ())
                if type(blocks) not in (list, tuple) or len(blocks) > MAX_IDS:
                    complete = False
                    continue
                block_rows = []
                for block in blocks:
                    index = _int(attrs(block).get("block_id"))
                    if index is None or len(entry["blocks"]) >= MAX_IDS and index not in entry["blocks"]:
                        complete = False
                        continue
                    owners = entry["blocks"].setdefault(index, set())
                    if len(owners) >= MAX_IDS and id_sha(rid) not in owners:
                        complete = False
                        continue
                    owners.add(id_sha(rid))
                    block_rows.append(sha(f"{entry['pool_sha256']}:{index}".encode()))
                rows.append({"request_sha256": id_sha(rid), "pool_sha256": entry["pool_sha256"],
                             "block_sha256": block_rows})
        return {"complete": complete, "groups": rows}

    def pool_snapshot(self, pool):
        entry = self.pool(pool)
        blocks = attrs(pool).get("blocks")
        if entry is None or type(blocks) is not list:
            return {"complete": False, "blocks": []}
        rows = []
        for index, requests in entry["blocks"].items():
            if index >= len(blocks):
                return {"complete": False, "blocks": rows}
            rows.append({"block_sha256": sha(f"{entry['pool_sha256']}:{index}".encode()),
                "observed_request_sha256": sorted(requests), "ref_count": _int(attrs(blocks[index]).get("ref_cnt")),
                "is_null": attrs(blocks[index]).get("is_null") if type(attrs(blocks[index]).get("is_null")) is bool else None})
        return {"complete": True, "pool_sha256": entry["pool_sha256"], "blocks": rows}

    def snapshot(self, point, args, kwargs, result=None, returned=False):
        if point in WEIGHT_POINTS:
            return {"requests":[],"metadata":{"weight_selection":self.weight_metadata(point,args,kwargs,result,returned)},
                "selection_observed":False}
        owner = args[0] if args else None
        static = point.endswith(".assign_request_id")
        value = (args[0] if args else kwargs.get("request")) if static else (
            args[1] if len(args) > 1 else next((kwargs[k] for k in
                ("request", "request_id", "request_ids", "req_id", "scheduler_output", "req_state") if k in kwargs), None))
        raw_ids = ids(value) or ids(attrs(value).get("request_id"))
        metadata = {}
        if static or point.endswith(".add_request") or point.endswith("._finish_request"):
            raw_ids = ids(attrs(value).get("request_id"))
            metadata = {"external_request_sha256": id_sha(attrs(value).get("external_req_id"))}
        # Only the synchronous ID assignment observes this context. Long-lived
        # output-handler tasks may inherit a request context and serve other IDs.
        if static:
            context = _ASGI_REQUEST.get()
            if context is not None and context[0] == os.getpid() and context[1] is not None:
                metadata["asgi_request_sha256"] = context[1]
        if returned and point.endswith(".abort_requests"):
            raw_ids = ids(result)
        if returned and point.endswith(".finish_requests"):
            raw_ids = _request_ids(result)
        data = attrs(owner)
        if point.endswith("._update_states") or point.endswith(".execute_model"):
            output = attrs(value)
            selected = ids(output.get("num_scheduled_tokens"))
            finished = ids(output.get("finished_req_ids"))
            raw_ids = tuple(dict.fromkeys((*selected, *finished)))[:MAX_IDS]
            metadata = {"selected_scheduled_sha256": [id_sha(r) for r in selected],
                "selected_finished_sha256": [id_sha(r) for r in finished],
                "selection_complete": (type(output.get("num_scheduled_tokens")) is dict
                    and len(output.get("num_scheduled_tokens")) <= MAX_IDS
                    and type(output.get("finished_req_ids")) in (set, frozenset)
                    and len(output.get("finished_req_ids")) <= MAX_IDS
                    and len(set(selected) | set(finished)) <= MAX_IDS),
                "scheduler_sequence": _int(output.get("sched_step_seq"))}
        if point.endswith(".get_output"):
            raw_ids = ids(attrs(result if returned else data.get("model_runner_output")).get("req_ids"))
        if point.endswith(".sample_tokens"):
            raw_ids = ids(attrs(result).get("req_ids")) if returned else ids(attrs(data.get("input_batch")).get("req_id_to_index"))
        if not raw_ids and point.endswith(".finish_requests") and value is None and not returned:
            raw_ids = ids(data.get("requests"))
        rows = []
        batch = data.get("input_batch", owner)
        for rid in raw_ids:
            request = (data.get("requests") or {}).get(rid) if type(data.get("requests")) is dict else None
            row = {"request_sha256": id_sha(rid),
                   "request_present": _present(owner, "requests", rid),
                   "output_state_present": _present(owner, "request_states", rid),
                   "batch_row_present": _present(batch, "req_id_to_index", rid),
                   "status_code": _int(attrs(request).get("status"))}
            rows.append(row)
        if "Scheduler." in point or "KVCacheManager." in point:
            metadata["ownership"] = self.blocks(owner, raw_ids)
        if "Scheduler." in point:
            metadata.update(scheduled_sequence=_int(data.get("sched_step_seq")),
                processed_sequence=_int(data.get("processed_step_seq")))
            pending = data.get("deferred_frees")
            if type(pending) is deque:
                pending = list(islice(pending,MAX_IDS+1))
                metadata["deferred_fences"] = [_int(row[0]) for row in pending[:MAX_IDS]]
                metadata["deferred_complete"] = len(pending) <= MAX_IDS
        if "BlockPool." in point:
            metadata["pool"] = self.pool_snapshot(owner)
        model_points = {"BaseModelLoader.load_model","DefaultModelLoader.load_weights",
            "CPUModelRunner.load_model","GPUModelRunner.load_model","CPUModelRunner.get_model",
            "GPUModelRunner.get_model","GPUModelRunner._model_forward"}
        if point in model_points:
            metadata["model_selection"] = self.model_metadata(point,args,kwargs,result,returned)
            raw_ids = ()
            if point == "GPUModelRunner._model_forward":
                frame = next((f for f in reversed(_CHAIN.get()) if f["point"] == "GPUModelRunner.execute_model"), None)
                if frame is not None:
                    # These are the selected scheduler IDs observed by the caller,
                    # not a tensor/row ownership or successful generation proof.
                    rows = [{"request_sha256":v,"request_present":None,"output_state_present":None,
                        "batch_row_present":None,"status_code":None} for v in frame["request_sha256"]]
                else:
                    rows = []
            else:
                rows = []
        return {"requests": rows, "metadata": metadata, "selection_observed": bool(rows) if point in model_points else bool(raw_ids)}

    def model_reference(self, value):
        if value is None:
            return None
        context = self._writer().context
        with self.model_lock:
            key = id(value)
            entry = self.model_objects.get(key)
            if entry is not None and entry[0]() is value:
                return entry[1]
            for stale in [k for k,v in self.model_objects.items() if v[0]() is None]:
                del self.model_objects[stale]
            if len(self.model_objects) >= MAX_MODEL_OBJECTS:
                return None
            try:
                reference = weakref.ref(value)
            except TypeError:
                return None
            self.model_serial += 1
            raw = json.dumps([self.config["session"],context,self.model_serial],sort_keys=True,separators=(",", ":")).encode()
            digest = sha(raw)
            self.model_objects[key] = (reference,digest)
            return digest

    def model_metadata(self, point, args, kwargs, result, returned):
        owner = args[0] if args else None
        data = attrs(owner)
        config = data.get("model_config")
        argument = None
        if point == "BaseModelLoader.load_model":
            config = args[2] if len(args)>2 else kwargs.get("model_config")
        if point == "DefaultModelLoader.load_weights":
            argument = args[1] if len(args)>1 else kwargs.get("model")
            config = args[2] if len(args)>2 else kwargs.get("model_config")
        returned_model = result if returned and point in {
            "BaseModelLoader.load_model","CPUModelRunner.get_model","GPUModelRunner.get_model"} else None
        sentinel = object()
        def present(name):
            value = data.get(name,sentinel)
            return None if value is sentinel else value is not None
        dummy = None
        if point in {"CPUModelRunner.load_model","GPUModelRunner.load_model"}:
            value = args[1] if len(args)>1 else kwargs.get("load_dummy_weights",False)
            dummy = value if type(value) is bool else None
        return {"owner_model_sha256":self.model_reference(data.get("model")),
            "argument_model_sha256":self.model_reference(argument),
            "returned_model_sha256":self.model_reference(returned_model),
            "configured_model_sha256":id_sha(attrs(config).get("model")),
            "configured_revision_sha256":id_sha(attrs(config).get("revision")),
            "load_format_sha256":id_sha(attrs(data.get("load_config")).get("load_format")),
            "load_dummy_weights":dummy,"lora_config_present":present("lora_config"),
            "speculative_config_present":present("speculative_config")}

    def weight_frame(self, snapshot):
        context = self._writer().context
        with self.model_lock:
            self.weight_serial += 1
            digest = sha(json.dumps(["weight-call",self.config["session"],context,self.weight_serial],
                sort_keys=True,separators=(",", ":")).encode())
        return {"weight_call_sha256":digest,"weight_model_sha256":snapshot["metadata"].get(
            "model_selection",{}).get("argument_model_sha256"),"_weight_pid":os.getpid()}

    def weight_metadata(self, point, args, kwargs, result, returned):
        parent = next((f for f in reversed(_CHAIN.get()) if f["point"] == "DefaultModelLoader.load_weights"
            and f.get("_weight_pid") == os.getpid()), {})
        cwd = os.getcwd()
        def path_sha(value):
            if type(value) is not str or not 0<len(value)<=4096 or "\x00" in value:
                return None
            if not value.startswith("/"):
                if type(cwd) is not str or not cwd.startswith("/"):return None
                value=posixpath.join(cwd,value)
            return sha(posixpath.normpath(value).encode())
        files=None;folder=None;use_safe=None;index=None;source=None;revision=None
        if point=="DefaultModelLoader._prepare_weights":
            source=args[1] if len(args)>1 else kwargs.get("model_name_or_path")
            revision=args[3] if len(args)>3 else kwargs.get("revision")
            if returned and type(result) is tuple and len(result)==4:
                folder,files,use_safe,index=result
        elif point=="DefaultModelLoader._get_weights_iterator":
            value=args[1] if len(args)>1 else kwargs.get("source")
            source,revision=attrs(value).get("model_or_path"),attrs(value).get("revision")
        else:
            offset=3 if point=="weight_utils.np_cache_weights_iterator" else 0
            files=args[offset] if len(args)>offset else kwargs.get("hf_weights_files")
        count=len(files) if type(files) is list else None
        hashes=[path_sha(value) for value in files[:64]] if type(files) is list else []
        return {"weight_call_sha256":parent.get("weight_call_sha256"),
            "weight_model_sha256":parent.get("weight_model_sha256"),"cwd_sha256":id_sha(cwd),
            "source_model_sha256":id_sha(source),"source_revision_sha256":id_sha(revision),
            "normalized_path_sha256":[v for v in hashes if v is not None],"path_count":count,
            "paths_complete":count is not None and count<=64 and all(v is not None for v in hashes)
                and cwd==os.getcwd(),
            "prepared_folder_sha256":path_sha(folder),"prepared_use_safetensors":use_safe if type(use_safe) is bool else None,
            "prepared_index_name_sha256":id_sha(index),
            "factory_returned_generator":type(result) is types.GeneratorType if returned and point!="DefaultModelLoader._prepare_weights" else None}

    @staticmethod
    def chain_rows():
        return [{k:v for k,v in row.items() if k in {"point","request_sha256"} or
            k in {"weight_call_sha256","weight_model_sha256"} and row.get("_weight_pid")==os.getpid()}
            for row in _CHAIN.get()]

    def wrap(self, module_name, point, original):
        observer = self
        def sample(args, kwargs, result=None, returned=False):
            started = time.monotonic_ns()
            try:
                value = observer.snapshot(point, args, kwargs, result, returned)
            except Exception:
                value = {"requests": [], "metadata": {}, "selection_observed": False, "diagnostic": "SAMPLE_UNAVAILABLE"}
            value["observation_started_ns"] = started
            value["observation_finished_ns"] = time.monotonic_ns()
            return value
        def begin(args, kwargs):
            before = sample(args, kwargs)
            chain = _CHAIN.get()
            payload = {"point": point, "phase": "CALL", "source_sha256": SOURCE_SHA[module_name], **before}
            wanted = observer.wanted(point,before)
            if wanted:
                observer.emit("NATIVE", payload)
            frame={"point":point,"request_sha256":[r["request_sha256"] for r in before["requests"]]}
            if point=="DefaultModelLoader.load_weights":
                try:frame.update(observer.weight_frame(before))
                except Exception:pass  # Missing association never changes native work.
            return _CHAIN.set((*chain[-3:],frame)), wanted
        def finish(args, kwargs, result, returned, token, started):
            try:
                after = sample(args,kwargs,result,returned)
                if started or observer.wanted(point,after):
                    observer.emit("NATIVE", {"point": point, "phase": "RETURN" if returned else "RAISE",
                        "source_sha256": SOURCE_SHA[module_name], **after,
                        "call_chain": observer.chain_rows()})
            finally:
                _CHAIN.reset(token)
        if inspect.iscoroutinefunction(original):
            @functools.wraps(original)
            async def asynchronous(*args, **kwargs):
                if not observer.active():
                    return await original(*args,**kwargs)
                (token, started), result, returned = begin(args, kwargs), None, False
                try:
                    result = await original(*args, **kwargs)
                    returned = True
                    return result
                finally:
                    finish(args, kwargs, result, returned, token, started)
            return asynchronous
        @functools.wraps(original)
        def synchronous(*args, **kwargs):
            if not observer.active():
                return original(*args,**kwargs)
            (token, started), result, returned = begin(args, kwargs), None, False
            try:
                result = original(*args, **kwargs)
                returned = True
                return result
            finally:
                finish(args, kwargs, result, returned, token, started)
        return synchronous

    def instrument(self, module):
        module_name = module.__name__
        targets = []
        for class_name, methods in POINTS.get(module_name, {}).items():
            cls = vars(module).get(class_name)
            if not isinstance(cls, type):
                raise ValueError("NATIVE_SENSOR_CLASS_UNAVAILABLE")
            for name in methods:
                descriptor = vars(cls).get(name)
                static = isinstance(descriptor, staticmethod)
                original = descriptor.__func__ if static else descriptor
                if not inspect.isfunction(original) or inspect.isasyncgenfunction(original):
                    raise ValueError("NATIVE_SENSOR_METHOD_UNAVAILABLE")
                wrapped = self.wrap(module_name, class_name + "." + name, original)
                targets.append((cls, name, staticmethod(wrapped) if static else wrapped))
        for name in FUNCTION_POINTS.get(module_name, ()):
            original=vars(module).get(name)
            if not inspect.isfunction(original) or inspect.isasyncgenfunction(original):
                raise ValueError("NATIVE_SENSOR_FUNCTION_UNAVAILABLE")
            targets.append((module,name,self.wrap(module_name,module_name.rsplit(".",1)[-1]+"."+name,original)))
        # Do not leave a half-installed module when a required method is absent.
        for cls, name, wrapped in targets:
            setattr(cls, name, wrapped)


class CheckedLoader:
    def __init__(self, name, original, observer):
        self.name, self.original, self.observer = name, original, observer

    def __getattr__(self, name):
        return getattr(self.original, name)

    def create_module(self, spec):
        return self.original.create_module(spec)

    def exec_module(self, module):
        try:
            filename = self.original.get_filename(self.name)
            raw = self.original.get_data(filename)
            if sha(raw) != SOURCE_SHA[self.name]:
                raise ValueError("NATIVE_SENSOR_SOURCE_MISMATCH")
            expected = compile(raw, filename, "exec", dont_inherit=True, optimize=sys.flags.optimize)
            actual = self.original.get_code(self.name)
            if code_digest(actual) != code_digest(expected):
                raise ValueError("NATIVE_SENSOR_CODE_MISMATCH")
        except Exception:
            self.observer.emit("MODULE", {"module": self.name, "verified": False})
            return self.original.exec_module(module)
        # Execute the very code object checked above, preserving normal exceptions.
        exec(actual, module.__dict__)
        try:
            self.observer.instrument(module)
        except Exception:
            self.observer.emit("MODULE", {"module": self.name, "verified": False})
        else:
            info = {"source_sha256": SOURCE_SHA[self.name], "code_sha256": code_digest(actual)}
            self.observer.modules[self.name] = info
            self.observer.emit("MODULE", {"module": self.name, "verified": True, **info})


class Finder(importlib.abc.MetaPathFinder):
    def __init__(self, observer):
        self.observer = observer

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in SOURCE_SHA or not self.observer.active():
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None and type(spec.loader) is importlib.machinery.SourceFileLoader:
            spec.loader = CheckedLoader(fullname, spec.loader, self.observer)
        return spec


class Middleware:
    """ASGI callback observations only: no extra receives, sends or cleanup."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        observer = _OBSERVER
        if observer is None or type(scope) is not dict or scope.get("type") != "http" or scope.get("path") != "/v1/completions":
            return await self.app(scope, receive, send)
        header = None
        headers = scope.get("headers", ())
        if type(headers) in (list, tuple):
            header = next((pair[1] for pair in headers if type(pair) in (list, tuple) and len(pair) == 2
                and type(pair[0]) is bytes and pair[0].lower() == b"x-request-id"), None)
        request_sha = sha(header) if type(header) is bytes and 0 < len(header) <= 512 else None
        context_token = _ASGI_REQUEST.set((os.getpid(), request_sha))
        def emit(phase, more_body=None):
            observer.emit("ASGI", {"request_sha256": request_sha, "phase": phase, "more_body": more_body})
        async def received():
            message = await receive()
            if type(message) is dict and message.get("type") == "http.disconnect":
                emit("DISCONNECT_RECEIVED")
            return message
        async def sent(message):
            value = await send(message)
            if type(message) is dict and message.get("type") == "http.response.body":
                more_body = message.get("more_body", False)
                emit("BODY_SEND_RETURN", more_body if type(more_body) is bool else None)
            return value
        returned = False
        try:
            result = await self.app(scope, received, sent)
            returned = True
            return result
        finally:
            try:
                emit("APP_RETURN" if returned else "APP_RAISE")
            finally:
                _ASGI_REQUEST.reset(context_token)


def install(path=None):
    global _OBSERVER
    if _OBSERVER is not None:
        return False
    path = path or os.environ.get(CONFIG_ENV)
    if path is None:
        return False
    try:
        if path != CONFIG_PATH or any(name in sys.modules for name in SOURCE_SHA):
            return False
        _context()
        config, config_sha = load_settings(path)
        if Path(config["control_path"]).exists():
            return False
        observer = Observer(config, config_sha)
    except Exception:
        return False
    _OBSERVER = observer
    sys.meta_path.insert(0, Finder(observer))
    atexit.register(observer.close)
    return True


def seal(path=CONFIG_PATH):
    config, _ = load_settings(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    directory = _open_directory(config["output_dir"])
    try:
        stat = os.fstat(directory)
        if stat.st_uid != os.geteuid() or stat.st_mode & 0o077:
            raise ValueError("NATIVE_SENSOR_SINK_UNVERIFIED")
        descriptor = os.open("SEALED",flags,0o600,dir_fd=directory)
        os.close(descriptor)
    finally:
        os.close(directory)


if __name__ == "__main__":
    if sys.argv[1:] != ["--seal"]:
        raise SystemExit("NATIVE_SENSOR_SEAL_ARGUMENTS_REQUIRED")
    try:
        seal()
    except Exception:
        raise SystemExit("NATIVE_SENSOR_SEAL_UNAVAILABLE") from None
