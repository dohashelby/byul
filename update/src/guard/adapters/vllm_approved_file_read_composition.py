"""Observe an approved local eager read while its selected loader frames are live."""

import builtins
import contextvars
import functools
import hashlib
import inspect
import json
import os
import stat
import sys
import types


PROFILES = {"0.30.0": "vllm-native-lifecycle-metadata/35", "0.31.0": "vllm-native-lifecycle-metadata/36"}
RUNTIME = "cpython-3.12.3"
EVENT = "APPROVED_FILE_READ"
EVENT_SCHEMA = "guard-native-approved-file-read/1"
LOADER = "vllm.model_executor.model_loader.default_loader"
WEIGHTS = "vllm.model_executor.model_loader.weight_utils"
LOAD_POINT = "DefaultModelLoader.load_weights"
ITERATOR_POINT = "DefaultModelLoader.get_all_weights"
EAGER_POINT = "safetensors_weights_iterator"
PRIMITIVE_FILE = "guard_file_read_scope.py"
PRIMITIVE_SHA256 = "5542067206f0d92a68575f6fd9cfbaf1c85fa97c35a707f50d9cb1ee260a0b64"
PRIMITIVE_CODE_SHA256 = "b2c28706d6c5ca0a8dc3dac0f0e46514c1235b53e20f6e62b4de1c61783e0b56"
ASSET_ROOT = "/guard-lifecycle"
INSTALL_ROOT = "/usr/local/lib/python3.12/dist-packages"
MODEL_ROOT = "/models"
_ACTIVE_LOADER = contextvars.ContextVar("guard_approved_file_loader", default=None)


def _approved_file_bytes(path, expected, size=None):
    directory, name = os.path.split(path)
    flags = os.O_RDONLY | os.O_NOFOLLOW
    parent = os.open(directory, flags | os.O_DIRECTORY)
    try:
        fd = os.open(name, flags | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 262144:
                raise ValueError("APPROVED_FILE_READ_SOURCE_UNAVAILABLE")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(262145)
            after = os.fstat(fd)
            current = os.stat(name, follow_symlinks=False, dir_fd=parent)
            fields = ("st_dev", "st_ino", "st_size", "st_mode", "st_mtime_ns", "st_ctime_ns")
            if (any(getattr(before, key) != getattr(after, key) or getattr(before, key) != getattr(current, key)
                    for key in fields) or len(raw) != before.st_size or size not in (None, len(raw))
                    or hashlib.sha256(raw).hexdigest() != expected):
                raise ValueError("APPROVED_FILE_READ_SOURCE_UNAVAILABLE")
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _relative_method(code, filename):
    return code.replace(co_filename=filename, co_consts=tuple(
        _relative_method(value, filename) if type(value) is types.CodeType else value
        for value in code.co_consts))


def _reviewed_methods(base, module, functions, source, install_root):
    relative = module.__name__.replace(".", "/") + ".py"
    path = os.path.join(install_root, relative)
    if vars(module).get("__file__") != path:
        raise ValueError("APPROVED_FILE_READ_INSTALLED_PATH_UNAVAILABLE")
    raw = _approved_file_bytes(path, source["sha256"], source["bytes"])
    compiled = compile(raw, path, "exec", dont_inherit=True, optimize=0)

    def reviewed_points(code):
        if code.co_qualname in functions:
            yield code.co_qualname, code
        for value in code.co_consts:
            if type(value) is types.CodeType:
                yield from reviewed_points(value)

    expected = dict(reviewed_points(compiled))
    result = {}
    for point, function in functions.items():
        actual = inspect.unwrap(function)
        if (not inspect.isfunction(actual) or actual.__globals__ is not vars(module)
                or point not in expected or base.code_digest(actual.__code__) != base.code_digest(expected[point])):
            raise ValueError("APPROVED_FILE_READ_METHOD_CODE_UNAVAILABLE")
        result[point] = (actual.__code__, base.code_digest(_relative_method(actual.__code__, relative)))
    return result


def configure_approved_file_reads(base, spec, *, asset_root=ASSET_ROOT, install_root=INSTALL_ROOT, model_root=MODEL_ROOT):
    runtime = sys.implementation.name + "-" + ".".join(map(str, sys.version_info[:3]))
    if (runtime != RUNTIME or sys.platform != "linux" or os.uname().machine != "x86_64" or sys.flags.optimize != 0
            or os.environ.get("VLLM_USE_V2_MODEL_RUNNER") != "0"
            or spec["profile"] not in PROFILES.values() or set(spec["sources"]) != {LOADER, WEIGHTS}):
        raise ValueError("APPROVED_FILE_READ_RUNTIME_UNAVAILABLE")
    primitive_path = os.path.join(asset_root, PRIMITIVE_FILE)
    primitive = types.ModuleType("_guard_approved_file_read_primitives")
    primitive.__file__ = primitive_path
    raw = _approved_file_bytes(primitive_path, PRIMITIVE_SHA256)
    exec(compile(raw, primitive_path, "exec", dont_inherit=True, optimize=0), primitive.__dict__)
    if primitive.code_contract(base) != PRIMITIVE_CODE_SHA256:
        raise ValueError("APPROVED_FILE_READ_PRIMITIVE_CODE_UNAVAILABLE")
    primitive.SOURCE_ADDITIONS = {WEIGHTS: spec["sources"][WEIGHTS]["sha256"]}
    step = contextvars.ContextVar("guard_approved_file_read_step", default=None)
    original_observer = base.Observer
    base.MAX_ROWS, base.MAX_BYTES = primitive.MAX_ROWS, primitive.MAX_BYTES

    class ApprovedFileObserver(primitive.FileObservation, original_observer):
        def __init__(self, config, config_sha):
            super().__init__(config, config_sha)
            self.read_scope_sha = spec["model_read_scope_sha256"]
            raw = _approved_file_bytes(os.path.join(asset_root, "model-read-scope.json"), self.read_scope_sha)
            self.read_scope = primitive.validate_scope(json.loads(raw), session=config["session"])
            if not self.read_scope["hash_model_bytes"]:
                raise ValueError("APPROVED_FILE_READ_HASH_REQUIRED")
            self.read_runtime = runtime
            self.read_code = PRIMITIVE_CODE_SHA256
            self.read_iteration = types.SimpleNamespace(_STEP=step)
            self.loader_points = {}

        def weight_nonce(self):
            return self.route_nonce("approved-file-read-handle")

        def emit(self, event, payload):
            if event == "FILE_READ":
                payload["schema"] = EVENT_SCHEMA
                payload["loader_iterator_observation_sha256"] = payload.pop("root_load_sha256")
                payload["loader_source_sha256"] = spec["sources"][LOADER]["sha256"]
                payload["loader_method_code_sha256"] = self.loader_points[LOAD_POINT][1]
                payload["loader_iterator_code_sha256"] = self.loader_points[ITERATOR_POINT][1]
                event = EVENT
            return super().emit(event, payload)

        def _loader_anchor(self, frame, point_code, module):
            active = _ACTIVE_LOADER.get()
            loader_source = self.modules.get(LOADER, {})
            weights_source = self.modules.get(WEIGHTS, {})
            if (active is None or active["pid"] != os.getpid() or not self.active()
                    or loader_source.get("source_sha256") != spec["sources"][LOADER]["sha256"]
                    or weights_source.get("source_sha256") != spec["sources"][WEIGHTS]["sha256"]
                    or frame.f_code is not point_code or frame.f_globals is not vars(module)):
                return None
            iterator = None
            for _ in range(32):
                frame = frame.f_back
                if frame is None:
                    return None
                if frame.f_globals is not active["globals"]:
                    continue
                if frame.f_code is self.loader_points[ITERATOR_POINT][0] and iterator is None:
                    iterator = frame
                if frame.f_code is self.loader_points[LOAD_POINT][0]:
                    break
            else:
                return None
            for selected in (iterator, frame):
                if selected is None or any(selected.f_locals.get(key) is not active[key]
                                           for key in ("self", "model", "model_config")):
                    return None
            root = {"call": self.route_nonce("approved-file-iterator-observation"),
                    "model": active["model_ref"], "loader_call": active["call"], "loader_model": active["model_ref"]}
            return {"pid": active["pid"], "point": primitive.POINT, "root": root, "closed": False,
                    "call": self.route_nonce("approved-file-eager-observation"), "operations": 1}

        def instrument(self, module):
            if module.__name__ == LOADER:
                cls = vars(module).get("DefaultModelLoader")
                if not isinstance(cls, type):
                    raise ValueError("APPROVED_FILE_READ_LOADER_UNAVAILABLE")
                original = vars(cls).get("load_weights")
                self.loader_points = _reviewed_methods(base, module,
                    {LOAD_POINT: original, ITERATOR_POINT: vars(cls).get("get_all_weights")},
                    spec["sources"][LOADER], install_root)

                @functools.wraps(original)
                def load_weights(*args, **kwargs):
                    active = None
                    try:
                        owner, model, config = (args[index] if len(args) > index else kwargs.get(key)
                                                for index, key in enumerate(("self", "model", "model_config")))
                        if self.active() and base.attrs(config).get("model") == model_root and type(owner) is cls:
                            model_ref = self.model_reference(model)
                            if model_ref:
                                active = {"pid": os.getpid(), "self": owner, "model": model, "model_config": config,
                                          "globals": vars(module), "model_ref": model_ref,
                                          "call": self.route_nonce("approved-file-loader-call")}
                    except Exception:
                        pass
                    token = _ACTIVE_LOADER.set(active)
                    try:
                        return original(*args, **kwargs)
                    finally:
                        _ACTIVE_LOADER.reset(token)

                cls.load_weights = load_weights
            elif module.__name__ == WEIGHTS:
                namespace = vars(module)
                original_open = namespace.get("open", builtins.open)
                if original_open is not builtins.open:
                    raise ValueError("APPROVED_FILE_READ_OPEN_UNAVAILABLE")
                ((point_code, digest),) = _reviewed_methods(base, module,
                    {EAGER_POINT: namespace.get(EAGER_POINT)}, spec["sources"][WEIGHTS], install_root).values()

                def observed_open(*args, **kwargs):
                    if (not args or type(args[0]) is not str or not args[0].startswith(model_root + "/")
                            or os.path.normpath(args[0]) != args[0]):
                        return original_open(*args, **kwargs)
                    binding = None
                    try:
                        binding = self._loader_anchor(sys._getframe(1), point_code, module)
                    except Exception:
                        pass
                    if binding is None:
                        return original_open(*args, **kwargs)
                    binding["method_code"] = digest
                    token = step.set(binding)
                    try:
                        return self.read_open(point_code, module, original_open, *args, **kwargs)
                    finally:
                        step.reset(token)

                namespace["open"] = observed_open
            else:
                super().instrument(module)

    base.Observer = ApprovedFileObserver
    return base


if "_configure" in globals():
    _approved_file_read_previous = _configure

    def _configure(base):
        return configure_approved_file_reads(_approved_file_read_previous(base), SPEC)
