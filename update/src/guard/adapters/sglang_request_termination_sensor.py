"""Opt-in SGLang 0.5.22 request lifecycle observer.

Uses the immutable native8 sink, source/code loader, and process/clock identity.
Only hashes and bounded scalar state are recorded. Native exceptions, tensors,
queues, and cache contents are left untouched. GPU fences are observer-owned;
they never synchronize the device or assert a security verdict.
"""
from __future__ import annotations

import contextvars
import functools
import hashlib
import inspect
import os
import stat
import sys
import threading
import time
import types
from pathlib import Path

PROFILE = "sglang-native-lifecycle-metadata/1"
SCHEMA = "guard-sglang-native-lifecycle-events/1"
# The legacy wire key 'commit' names this immutable OCI source, not a Git SHA.
COMMIT = "5fc09edb6a0bb1d5b6992b543723c66d08f86d7a507e951e87e0d05db4ac766e"
SOURCE_REFERENCE_KIND = "oci-manifest-sha256"
BASE8_SHA256 = "e537d1d1b19813f171aa30b9344c1dbb49139eed3dea60bfa6dc71744d0d4f39"
MAX_ROWS, MAX_BYTES, MAX_IDS, MAX_QUEUE = 4096, 4 * 1024**2, 32, 512
MAX_GPU_EVENTS, GPU_WAIT_NS = 64, 5_000_000_000
SOURCE_SHA = {
    "sglang.srt.managers.io_struct": "3d01e239ffb7918d7ab6959544007d8ec785932f548430b8dac8243a0afbbec0",
    "sglang.srt.runtime_context": "1b4e9cc6d9afa941d7d4619dab1f434a5b83072fe60f165008e68c3740e54aab",
    "sglang.srt.managers.scheduler": "ab23556739fc4a1a089da5d33f8cb925c14b7c68ae564294b5ed13d96b427c36",
    "sglang.srt.managers.tokenizer_manager": "c4300627da6161c7ec52998a4bf7ae13d19acf7bf3d075ed9de991097a73f355",
    "sglang.srt.managers.schedule_batch": "c382bf0066d70e19b4b9e38c92ff0029d39996238f8f0685f4c1dadbfa4e8a96",
    "sglang.srt.mem_cache.common": "e8d3ff0a8a0357e04e048bd18bd56f109ac59d6e44b205d366d9c617b8effc7d",
    "sglang.srt.mem_cache.memory_pool": "8251fab74646427510d9dc2a358668816c26c35d928c6817bf31fd6d7c8ed92c",
    "sglang.srt.managers.scheduler_components.batch_result_processor": "bf45be863809db610712b68679a69ba9f9c011e63753f4f2244e3ca824ab1432",
}
POINTS = {
    "sglang.srt.managers.tokenizer_manager": {"TokenizerManager": (
        "_init_req_state", "abort_request", "_handle_batch_output", "_release_req_states_on_failure")},
    "sglang.srt.managers.scheduler": {"Scheduler": ("abort_request", "run_batch", "process_batch_result", "ingest_requests", "on_idle")},
    "sglang.srt.managers.schedule_batch": {"ScheduleBatch": ("filter_batch",)},
}
FUNCTION_POINTS = {"sglang.srt.mem_cache.common": ("release_kv_cache",)}
_REQUEST = contextvars.ContextVar("guard_sglang_request", default=None)
_BASE = None


def artifact(path, expected):
    """Read one regular immutable asset snapshot, rejecting symlink/replacement."""
    before = os.lstat(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or not 0 < opened.st_size <= 262144:
            raise ValueError("SGLANG_BASE_ASSET_UNAVAILABLE")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(262145)
        after, current = os.fstat(fd), os.lstat(path)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (not stat.S_ISREG(before.st_mode) or len(raw) != opened.st_size
                or len({identity(s) for s in (before, opened, after, current)}) != 1
                or hashlib.sha256(raw).hexdigest() != expected):
            raise ValueError("SGLANG_BASE_ASSET_UNAVAILABLE")
        return raw
    finally:
        os.close(fd)


def fields(value):
    try:
        result = object.__getattribute__(value, "__dict__")
    except (AttributeError, TypeError):
        return {}
    return result if type(result) is dict else {}


def request_id(value):
    return value if type(value) is str and 0 < len(value) <= 512 else None


def request_ids(value):
    if request_id(value):
        return (value,)
    if type(value) in (list, tuple) and len(value) <= MAX_IDS:
        return tuple(dict.fromkeys(v for v in value if request_id(v)))
    return ()


def hash_id(value):
    return hashlib.sha256(value.encode("utf-8", "strict")).hexdigest()


def bounded_rows(value):
    if type(value) is list and len(value) <= MAX_QUEUE:
        return value, True
    return (), False


def identity_fields(value, modules):
    """Read only pinned IPC identity slots; never invoke arbitrary getters."""
    data = fields(value)
    if data:
        return data
    cls = type(value)
    module = "sglang.srt.managers.io_struct"
    if (cls.__module__ != module or cls.__name__ not in {
            "AbortReq", "BatchStrOutput", "BatchTokenIDOutput", "BatchEmbeddingOutput"}
            or module not in modules or sys.modules.get(module) is None
            or vars(sys.modules[module]).get(cls.__name__) is not cls):
        return {}
    result = {}
    for name in ("rid", "rids"):
        for parent in cls.__mro__:
            descriptor = vars(parent).get(name)
            if descriptor is None:
                continue
            if type(descriptor) is types.MemberDescriptorType:
                try:
                    result[name] = descriptor.__get__(value, cls)
                except (AttributeError, TypeError):
                    pass
            break
    return result


def selected_ids(point, args, kwargs, modules=None):
    if point == "common.release_kv_cache":
        return request_ids(fields(args[0] if args else None).get("rid"))
    value = args[1] if len(args) > 1 else None
    if point == "TokenizerManager.abort_request":
        return request_ids(value if value is not None else kwargs.get("rid"))
    data = identity_fields(value, modules or {})
    if point in {"TokenizerManager._init_req_state", "Scheduler.abort_request"}:
        return request_ids(data.get("rid"))
    if point == "TokenizerManager._handle_batch_output":
        return request_ids(data.get("rids"))
    if point == "TokenizerManager._release_req_states_on_failure":
        mapping = value if type(value) is dict else None
        return request_ids(list(mapping) if mapping is not None and len(mapping) <= MAX_IDS else None)
    batch = args[0] if point == "ScheduleBatch.filter_batch" and args else value
    rows, complete = bounded_rows(fields(batch).get("reqs"))
    if not complete or len(rows) > MAX_IDS:
        return ()
    return request_ids([fields(req).get("rid") for req in rows])


def scheduler_presence(owner, rid):
    """Default text single scheduler only; missing queues remain unverified."""
    data = fields(owner)
    if not {"waiting_queue", "running_batch", "last_batch", "cur_batch_for_debug",
            "chunked_req", "enable_continuous_input_polling"} <= set(data):
        return None
    if data["enable_continuous_input_polling"] is not False:
        return None
    deferred, deferred_valid = bounded_rows(data.get("_deferred_input_requests"))
    grammar, grammar_valid = bounded_rows(fields(data.get("grammar_manager")).get("grammar_queue"))
    if not deferred_valid or not grammar_valid:
        return None
    if deferred or grammar:
        # These queues are outside the basic-text profile; never infer absence
        # from the ordinary waiting/running batches while they contain work.
        return True if any(fields(req).get("rid") == rid for req in (*deferred, *grammar)) else None
    waiting, complete = bounded_rows(data.get("waiting_queue"))
    found = any(fields(req).get("rid") == rid for req in waiting)
    for name in ("running_batch", "cur_batch_for_debug", "last_batch"):
        batch = data.get(name)
        if batch is None:
            continue
        rows, valid = bounded_rows(fields(batch).get("reqs"))
        complete = complete and valid
        found = found or any(fields(req).get("rid") == rid for req in rows)
    chunked = data.get("chunked_req")
    found = found or (chunked is not None and fields(chunked).get("rid") == rid)
    return found if complete else None


def qualified(owner, modules):
    """Read pinned runtime configuration without executing native getters."""
    try:
        module = sys.modules.get("sglang.srt.runtime_context")
        if module is None or "sglang.srt.runtime_context" not in modules:
            return False
        parallel = vars(module).get("_PARALLEL")
        config = object.__getattribute__(parallel, "_config")
        overrides = object.__getattribute__(parallel, "_overrides")
        stamp = object.__getattribute__(parallel, "_stamp")
        if type(overrides) is not dict or type(stamp) is not dict:
            return False
        leaves = fields(config).get("_fields")
        if type(leaves) is not dict:
            return False
        widths = [overrides.get(k, stamp.get(k, leaves.get(k))) for k in ("tp_size", "pp_size", "dp_size")]
        context = vars(module).get("_CONTEXT")
        bags = object.__getattribute__(context, "_config_bags")
        if type(bags) is not dict:
            return False
        spec = fields(bags.get("spec"))
        data = fields(owner)
        mode = data.get("disaggregation_mode")
        mode = mode if type(mode) is str else fields(mode).get("_value_")
        return (all(type(v) is int and v == 1 for v in widths)
                and "speculative_algorithm" in spec and spec["speculative_algorithm"] is None
                and data.get("enable_overlap") is False and data.get("enable_hisparse") is False
                and "dllm_config" in data and data["dllm_config"] is None
                and data.get("enable_continuous_input_polling") is False and mode == "null"
                and data.get("_deferred_input_requests") == []
                and fields(data.get("grammar_manager")).get("grammar_queue") == []
                and data.get("device") in ("cuda", "cuda:0"))
    except Exception:
        return False


def kv_fields(value, modules):
    """Read the pinned ReqKvInfo scalar slots without evaluating properties."""
    names=("req_pool_idx", "kv_allocated_len", "mamba_pool_idx")
    data=fields(value)
    if all(name in data for name in names):
        return data
    cls=type(value)
    module="sglang.srt.managers.schedule_batch"
    if (cls.__module__!=module or cls.__name__!="ReqKvInfo" or module not in modules
            or sys.modules.get(module)is None or vars(sys.modules[module]).get('ReqKvInfo')is not cls):
        return {}
    result={}
    for name in names:
        descriptor=vars(cls).get(name)
        if type(descriptor)is not types.MemberDescriptorType:
            return {}
        try:
            result[name]=descriptor.__get__(value,cls)
        except (AttributeError,TypeError):
            return {}
    return result


def snapshot(point, args, selected, modules=None):
    owner = args[0] if args else None
    result = []
    for rid in selected:
        row = {"request_sha256": hash_id(rid), "state_present": None,
               "kv_registered": None, "kv_allocated": None, "mamba_present": None}
        if point.startswith("TokenizerManager."):
            mapping = fields(owner).get("rid_to_state")
            row["state_present"] = rid in mapping if type(mapping) is dict else None
        elif point.startswith("Scheduler."):
            row["state_present"] = scheduler_presence(owner, rid)
        elif point == "ScheduleBatch.filter_batch":
            rows, complete = bounded_rows(fields(owner).get("reqs"))
            row["state_present"] = any(fields(req).get("rid") == rid for req in rows) if complete else None
        elif point == "common.release_kv_cache":
            kv = kv_fields(fields(owner).get("kv"), modules or {})
            if kv:
                row["kv_registered"] = kv.get("req_pool_idx") is not None
                allocated = kv.get("kv_allocated_len")
                row["kv_allocated"] = allocated if type(allocated) is int and 0 <= allocated < 2**63 else None
                row["mamba_present"] = kv.get("mamba_pool_idx") is not None
        result.append(row)
    return result


class CheckpointWriterMixin:
    """A healthy writer's observed prefix; request completion is adjudicated separately.

    The marker is part of the ordinary hash chain. It asserts no fsync durability,
    request result, absence of future work, or observer coverage of other processes.
    """
    def __init__(self, *args, **kwargs):
        self._rolling = os.environ.get("BYUL_NATIVE_ROLLING") == "1"
        self._segment_generation = 0
        self._segment_files = []
        self._segment_header = None
        self._segment_modules = {}
        super().__init__(*args, **kwargs)

    def _ensure_segment_capacity(self):
        if self._rolling and not self.closed:
            rows = getattr(self, "_rolling_max_rows", MAX_ROWS)
            size = getattr(self, "_rolling_max_bytes", MAX_BYTES)
            if self.seq >= rows - 4 or self.bytes > size - 32768:
                try:
                    self._rotate_segment()
                except Exception:
                    self.closed = True
                    if self.fd is not None:
                        os.close(self.fd)
                        self.fd = None
        return not self.closed and self.fd is not None

    def write(self, event, payload):
        with self.lock:
            if event != "END" and not self._ensure_segment_capacity():
                return
            if event == "START" and self._segment_header is None:
                self._segment_header = dict(payload)
            if event == "MODULE":
                module = payload.get("module")
                if isinstance(module, str) and (module in self._segment_modules or len(self._segment_modules) < 128):
                    self._segment_modules[module] = dict(payload)
                elif self._rolling:
                    self.close("BOUND_REACHED")
                    return
            return super().write(event, payload)

    def _rotate_segment(self):
        # Native tracking/fences remain in their Observer. Only the bounded
        # output shard changes; original bytes are never rewritten or repaired.
        directory = self._checkpoint_directory(self.config["output_dir"])
        try:
            info = os.fstat(directory)
            if info.st_uid != self.context["uid"] or info.st_mode & 0o077:
                raise ValueError("NATIVE_SEGMENT_DIRECTORY_UNAVAILABLE")
            name = f"pid-{self.context['pid']}-{self.context['start_ticks']}"
            current = name + ".jsonl"
            segment = name + f"-segment-{self._segment_generation}.jsonl"
            try:
                os.stat(segment, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("NATIVE_SEGMENT_ALREADY_EXISTS")
            self._append("END", {"reason": "SEGMENT_ROLLOVER"})
            os.close(self.fd)
            self.fd = None
            descriptor = os.open(current, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                opened = os.fstat(descriptor)
                if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != self.context["uid"]
                        or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_size != self.bytes):
                    raise ValueError("NATIVE_SEGMENT_FILE_UNAVAILABLE")
                with os.fdopen(descriptor, "rb", closefd=False) as source:
                    raw = source.read(getattr(self, "_rolling_max_bytes", MAX_BYTES) + 1)
                if len(raw) != self.bytes:
                    raise ValueError("NATIVE_SEGMENT_FILE_BOUND")
            finally:
                os.close(descriptor)
            # link is atomic no-replace: a raced archive destination is never
            # overwritten. Verify the linked inode before removing its alias.
            os.link(current, segment, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            linked = os.stat(segment, dir_fd=directory, follow_symlinks=False)
            current_info = os.stat(current, dir_fd=directory, follow_symlinks=False)
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
            if identity(linked) != identity(opened) or identity(current_info) != identity(opened):
                raise ValueError("NATIVE_SEGMENT_FILE_CHANGED")
            os.unlink(current, dir_fd=directory)
            self._segment_files.append(segment)
            previous_generation = self._segment_generation
            self._segment_generation += 1
            self.fd = os.open(current, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            self.seq, self.bytes, self.previous = 0, 0, "0" * 64
            self._append("START", self._segment_header)
            self._append("SEGMENT", {"schema": "guard-native-segment/1", "session": self.config["session"],
                "segment": self._segment_generation, "previous_segment": previous_generation,
                "previous_sha256": hashlib.sha256(raw).hexdigest(), "previous_bytes": len(raw)})
            for payload in self._segment_modules.values():
                self._append("MODULE", payload)
            if self.seq >= getattr(self, "_rolling_max_rows", MAX_ROWS) - 4 or self.bytes > getattr(self, "_rolling_max_bytes", MAX_BYTES) - 32768:
                self.close("BOUND_REACHED")
                return
            while len(self._segment_files) > 2:
                old = self._segment_files[0]
                archived = os.stat(old, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(archived.st_mode) or archived.st_uid != self.context["uid"] or stat.S_IMODE(archived.st_mode) != 0o600:
                    raise ValueError("NATIVE_SEGMENT_RETENTION_UNAVAILABLE")
                os.unlink(old, dir_fd=directory)
                self._segment_files.pop(0)
        finally:
            os.close(directory)

    def checkpoint(self):
        with self.lock:
            if not self._ensure_segment_capacity():
                return False
            sequence = self.seq
            self.write("CHECKPOINT", {
                "schema": "guard-native-checkpoint-ack/1",
                "session": self.config["session"],
                "prefix_seq": sequence,
                "prefix_sha256": self.previous,
            })
            # Writer.write closes silently on a bound or failed append. Such a
            # writer cannot acknowledge a new cutoff, even if it wrote an END.
            return not self.closed and self.fd is not None and self.seq == sequence + 1

    def _watch(self):
        next_checkpoint = time.monotonic() + 1
        try:
            while not self.closed:
                if Path(self.config["control_path"]).exists():
                    self.close("SEALED")
                    return
                if time.monotonic() >= next_checkpoint:
                    if not self.checkpoint():
                        return
                    next_checkpoint = time.monotonic() + 1
                threading.Event().wait(0.1)
        except Exception:
            try:
                self.close("CONTROL_UNAVAILABLE")
            except Exception:
                pass


def configure(base):
    if base.PROFILE != "vllm-native-lifecycle-metadata/8" or base.SCHEMA != "guard-native-lifecycle-events/8":
        raise ValueError("SGLANG_BASE_PROFILE_MISMATCH")
    artifact(base.__file__, BASE8_SHA256)
    base.PROFILE, base.SCHEMA, base.COMMIT = PROFILE, SCHEMA, COMMIT
    base.SOURCE_SHA, base.POINTS, base.FUNCTION_POINTS = dict(SOURCE_SHA), POINTS, FUNCTION_POINTS
    base.HANDLER_POINTS = {}
    base.MAX_ROWS, base.MAX_BYTES = MAX_ROWS, MAX_BYTES
    base.__file__ = __file__
    class Writer(CheckpointWriterMixin, base.Writer):
        _checkpoint_directory = staticmethod(base._open_directory)
    base.Writer = Writer
    parent = base.Observer

    class Observer(parent):
        def __init__(self, config, config_sha):
            super().__init__(config, config_sha)
            self.operation = 0
            self.operation_lock = threading.Lock()
            self.sg_pid = os.getpid()
            self.gpu_lock = threading.Lock()
            self.gpu_pending = []
            self.gpu_active = self.gpu_reserved = 0
            self.gpu_thread = None
            self.cancelled = {}
            self.idle_states = {}

        def _ensure_process(self):
            if self.sg_pid != os.getpid():
                self.sg_pid = os.getpid()
                self.operation = 0
                self.operation_lock = threading.Lock()
                self.gpu_lock = threading.Lock()
                self.gpu_pending, self.gpu_thread = [], None
                self.gpu_active = self.gpu_reserved = 0
                self.cancelled, self.idle_states = {}, {}

        def observe(self, module, point, phase, args, selected, operation):
            try:
                self._ensure_process()
                context = _REQUEST.get()
                invocation = context if context and context[0] == os.getpid() else None
                payload = {"point": point, "phase": phase, "operation": operation,
                    "source_module": module, "source_verified": module in self.modules,
                    "requests": snapshot(point, args, selected, self.modules),
                    "asgi_request_sha256": invocation[1] if invocation else None,
                    "asgi_invocation_sha256": invocation[2] if invocation else None,
                    "qualified_single_scheduler": qualified(args[0], self.modules) if args and point.startswith("Scheduler.") else False}
                if point in {"Scheduler.ingest_requests", "Scheduler.on_idle"}:
                    key = (point, phase)
                    state = tuple((r["request_sha256"], r["state_present"]) for r in payload["requests"])
                    if self.idle_states.get(key) == state:
                        return
                    self.idle_states[key] = state
                if selected:
                    self.emit("SG_NATIVE", payload)
            except Exception:
                self.emit("SG_GAP", {"code": "STATE_OBSERVATION_UNAVAILABLE"})

        def fences(self, owner, selected, operation):
            """Record only event handles/scalars, never retain native batches."""
            if not selected:
                return
            reserved = 0
            pending = []
            event = None
            failed = False
            try:
                data = fields(owner)
                device = data.get("device_module")
                if not isinstance(device, types.ModuleType) or device.__name__ != "torch.cuda":
                    raise ValueError("CUDA_MODULE_UNAVAILABLE")
                with self.gpu_lock:
                    if len(self.gpu_pending) + self.gpu_active + self.gpu_reserved + 4 > MAX_GPU_EVENTS:
                        raise ValueError("CUDA_EVENT_BOUND")
                    self.gpu_reserved += 4
                    reserved = 4
                for name in ("forward_stream", "copy_stream", "schedule_stream", "return_stream"):
                    stream = device.current_stream() if name == "return_stream" else data.get(name)
                    if stream is None:
                        raise ValueError("CUDA_STREAM_UNAVAILABLE")
                    event = device.Event(enable_timing=False)
                    event.record(stream=stream)
                    pending.append((event, name, tuple(hash_id(rid) for rid in selected), operation,
                                    time.monotonic_ns(), qualified(owner, self.modules)))
                with self.gpu_lock:
                    self.gpu_reserved -= reserved
                    reserved = 0
                    self.gpu_pending.extend(pending)
                    pending.clear()
                    event = None
                    if self.gpu_thread is None:
                        self.gpu_thread = threading.Thread(target=self.poll_fences, daemon=True,
                                                          name="guard-sglang-fences")
                        try:
                            self.gpu_thread.start()
                        except Exception:
                            self.gpu_thread = None
                            self.gpu_pending.clear()
                            raise
            except Exception:
                # Keep the reservation until the failed Event.record traceback
                # and every partially constructed handle have been released.
                pending.clear()
                event = None
                failed = True
            if reserved:
                with self.gpu_lock:
                    self.gpu_reserved -= reserved
            if failed:
                self.emit("SG_GAP", {"code": "GPU_FENCE_UNAVAILABLE", "operation": operation})

        def poll_fences(self):
            while self.sg_pid == os.getpid():
                with self.gpu_lock:
                    if not self.gpu_pending:
                        self.gpu_thread = None
                        return
                    values = self.gpu_pending
                    self.gpu_pending = []
                    count = len(values)
                    self.gpu_active += count
                remaining = []
                for event, stream, ids, operation, began, mode_qualified in values:
                    try:
                        done = event.query() is True
                        expired = time.monotonic_ns() - began > GPU_WAIT_NS
                    except Exception:
                        done, expired = False, True
                    if not done and not expired:
                        remaining.append((event, stream, ids, operation, began, mode_qualified))
                        continue
                    self.emit("SG_GPU_FENCE", {"operation": operation, "stream": stream,
                        "requests": list(ids), "completed": done, "timed_out": not done,
                        "qualified_single_scheduler": mode_qualified})
                values.clear()
                event = None
                with self.gpu_lock:
                    self.gpu_active -= count
                    self.gpu_pending.extend(remaining)
                threading.Event().wait(0.005)

        def wrap(self, module, point, original):
            def entered(args, kwargs):
                self._ensure_process()
                with self.operation_lock:
                    self.operation += 1
                    operation = self.operation
                selected = selected_ids(point, args, kwargs, self.modules)
                if point == "Scheduler.abort_request":
                    if len(self.cancelled) + len(set(selected) - self.cancelled.keys()) > MAX_IDS:
                        self.emit("SG_GAP", {"code": "REQUEST_TRACKING_BOUND"})
                    else:
                        self.cancelled.update((rid, time.monotonic_ns()) for rid in selected)
                if point in {"Scheduler.ingest_requests", "Scheduler.on_idle"}:
                    now = time.monotonic_ns()
                    self.cancelled = {rid: t for rid, t in self.cancelled.items() if now - t < 30_000_000_000}
                    selected = tuple(self.cancelled)
                self.observe(module, point, "CALL", args, selected, operation)
                return selected, operation

            def finished(args, selected, operation, phase):
                try:
                    self.observe(module, point, phase, args, selected, operation)
                    if point == "Scheduler.process_batch_result" and phase == "RETURN":
                        self.fences(args[0], selected, operation)
                except Exception:
                    self.emit("SG_GAP", {"code": "STATE_OBSERVATION_UNAVAILABLE"})

            def safely_enter(args, kwargs):
                try:
                    return entered(args, kwargs)
                except Exception:
                    self.emit("SG_GAP", {"code": "CALL_OBSERVATION_UNAVAILABLE"})
                    return (), None

            if inspect.iscoroutinefunction(original):
                @functools.wraps(original)
                async def coroutine(*args, **kwargs):
                    selected, operation = safely_enter(args, kwargs)
                    try:
                        result = await original(*args, **kwargs)
                    except BaseException:
                        finished(args, selected, operation, "RAISE")
                        raise
                    finished(args, selected, operation, "RETURN")
                    return result
                return coroutine

            @functools.wraps(original)
            def synchronous(*args, **kwargs):
                selected, operation = safely_enter(args, kwargs)
                try:
                    result = original(*args, **kwargs)
                except BaseException:
                    finished(args, selected, operation, "RAISE")
                    raise
                finished(args, selected, operation, "RETURN")
                return result
            return synchronous

    base.Observer = Observer
    return base


def _private_base():
    path = "/guard-lifecycle/guard_lifecycle_base.py"
    raw = artifact(path, BASE8_SHA256)
    base = types.ModuleType("_guard_sglang_base8")
    base.__file__ = path
    exec(compile(raw, path, "exec", dont_inherit=True, optimize=0), vars(base))
    return configure(base)


def install(path=None):
    global _BASE
    if _BASE is not None:
        return False
    try:
        base = _private_base()
        if not base.install(path, open_sink=True):
            return False
        _BASE = base
        return True
    except Exception:
        return False


class Middleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        observer = _BASE._OBSERVER if _BASE is not None else None
        if (observer is None or type(scope) is not dict or scope.get("type") != "http"
                or scope.get("method") != "POST" or scope.get("path") not in
                {"/v1/completions", "/v1/chat/completions"}):
            token = _REQUEST.set(None)
            try:
                return await self.app(scope, receive, send)
            finally:
                _REQUEST.reset(token)
        header = None
        headers = scope.get("headers")
        if type(headers) in (list, tuple) and len(headers) <= 128:
            found = [v for item in headers if type(item) in (list, tuple) and len(item) == 2
                     and type(item[0]) is bytes and item[0].lower() == b"x-request-id"
                     for v in (item[1],) if type(v) is bytes and 0 < len(v) <= 512]
            if len(found) == 1:
                header = found[0]
        context = (os.getpid(), hashlib.sha256(header).hexdigest(),
                   hashlib.sha256(os.urandom(32)).hexdigest()) if header else None
        token = _REQUEST.set(context)
        def emit(phase):
            observer.emit("SG_ASGI", {"phase": phase,
                "request_sha256": context[1] if context else None,
                "invocation_sha256": context[2] if context else None})
        async def received():
            value = await receive()
            if type(value) is dict and value.get("type") == "http.disconnect":
                emit("DISCONNECT_RECEIVED")
            return value
        returned = False
        try:
            result = await self.app(scope, received, send)
            returned = True
            return result
        finally:
            try:
                emit("APP_RETURN" if returned else "APP_RAISE")
            finally:
                _REQUEST.reset(token)
