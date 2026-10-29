"""Explicit Online native controller /9; standalone owned serving assets.

The shipped /8 base and B callback bytes are private, checked dependencies.
No engine import, hook installation or source approval happens on import.
"""
from __future__ import annotations

import contextvars
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import sys
import time
import types

PROFILE = "vllm-native-lifecycle-metadata/9"
SCHEMA = "guard-native-lifecycle-events/9"
COMMIT = "db9527a46873454610df6dbedf79a36d6bf1a7f6"
MAX_ROWS = 2048
MAX_BYTES = 2 * 1024**2
CALLBACK_MODULE = "guard.adapters.vllm_online_input"
CODE_CONTRACT = "python312-source-relative-code/1"
SOURCE_ADDITIONS = {
    "vllm.renderers.online_renderer": "1ada26191425fd9d56c3525949f999826dad9f2c82df94ad586a1cf6f67b089e",
    "vllm.entrypoints.generate.base.protocol": "67ede411b35a8344ddb87bfaaf1a1abea8acd39a4d23655e34032015505167c0",
}
POINT_ADDITIONS = {
    "vllm.renderers.online_renderer": {"OnlineRenderer": ("render_chat", "preprocess_chat", "validate_chat_template")},
    "vllm.v1.engine.input_processor": {"InputProcessor": ("process_inputs", "_validate_model_input", "_validate_prompt_len")},
}
FUNCTION_ADDITIONS = {
    "vllm.renderers.online_renderer": ("_reused_prompt_token_ids",),
    "vllm.entrypoints.generate.base.protocol": ("validate_request_mm_kwargs",),
}
# REVIEWED_PINS
BASE_SHA256 = '2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd'
CALLBACK_SHA256 = "bd15958029e91ba189c2fe588009a97ddd4d3d339e32824e5e5016a52fa736b1"
CALLBACK_CODE_SHA256 = '1728a373625ec44d943c9762ea6b31c85571876897a0ed81a1d321e4e5eee2a3'
CALLBACK_FUNCTION_SHA256 = '70369194951aaf040b401ecb9c5e9d5492285da1c98802860fd09b347b604cf2'
MODULE_CONTRACTS = {'vllm.entrypoints.generate.base.protocol': {'code_sha256': '4925c4d0e258e9aad091f229d6747c429c3307337ab894437479b707277211b4',
                                             'filename': 'vllm/entrypoints/generate/base/protocol.py',
                                             'source_sha256': '67ede411b35a8344ddb87bfaaf1a1abea8acd39a4d23655e34032015505167c0'},
 'vllm.renderers.online_renderer': {'code_sha256': '561c1cfbf72b5ecc908310596018c3a99254f44156410a81520a893710aca4b4',
                                    'filename': 'vllm/renderers/online_renderer.py',
                                    'source_sha256': '1ada26191425fd9d56c3525949f999826dad9f2c82df94ad586a1cf6f67b089e'},
 'vllm.v1.engine.input_processor': {'code_sha256': '82734a678f09390c1cf59c4899fcf567c254557508cfdfd0971c5101e40c0f2a',
                                    'filename': 'vllm/v1/engine/input_processor.py',
                                    'source_sha256': 'd79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b'}}
POINT_CONTRACTS = {'InputProcessor._validate_model_input': {'async': False,
                                          'filename': 'vllm/v1/engine/input_processor.py',
                                          'function_code_sha256': '9e741ab702847ae7123ebbe6224083c3feb3152aa122adb4e692d5f27edb4f0f',
                                          'module': 'vllm.v1.engine.input_processor'},
 'InputProcessor._validate_prompt_len': {'async': False,
                                         'filename': 'vllm/v1/engine/input_processor.py',
                                         'function_code_sha256': '9b5e805454a645106f9ecef9c3a2d6ff5205158f28668c1d676e2a70ca57faae',
                                         'module': 'vllm.v1.engine.input_processor'},
 'InputProcessor.process_inputs': {'async': False,
                                   'filename': 'vllm/v1/engine/input_processor.py',
                                   'function_code_sha256': '8cc4c2a41215abc29a20967c52e17e362a3f6abf63f618ecad9d1f4534d32ae0',
                                   'module': 'vllm.v1.engine.input_processor'},
 'OnlineRenderer.preprocess_chat': {'async': True,
                                    'filename': 'vllm/renderers/online_renderer.py',
                                    'function_code_sha256': 'c39dd01ebda9161eb33195c08c44a987c42f5c09af68dcde1c49a850d8e178d1',
                                    'module': 'vllm.renderers.online_renderer'},
 'OnlineRenderer.render_chat': {'async': True,
                                'filename': 'vllm/renderers/online_renderer.py',
                                'function_code_sha256': '12781547cd1bca158c29ea2e7e86eec8510b4569d5f3cf8b0ea7f454c5d5a562',
                                'module': 'vllm.renderers.online_renderer'},
 'OnlineRenderer.validate_chat_template': {'async': False,
                                           'filename': 'vllm/renderers/online_renderer.py',
                                           'function_code_sha256': 'c797417738f55004bd75a709c3e60f22584b64ea77f91323e509d5f2fc25782c',
                                           'module': 'vllm.renderers.online_renderer'},
 'online_renderer._reused_prompt_token_ids': {'async': False,
                                              'filename': 'vllm/renderers/online_renderer.py',
                                              'function_code_sha256': '14f7fbe2beb7e1227751a2eccc7cc424b3464a46124f91f552ab3f5991ec0830',
                                              'module': 'vllm.renderers.online_renderer'},
 'protocol.validate_request_mm_kwargs': {'async': False,
                                         'filename': 'vllm/entrypoints/generate/base/protocol.py',
                                         'function_code_sha256': '2df55628e9262c2c8c87bdbf904c02a58eedfe5f41118c378129da6e93b0de44',
                                         'module': 'vllm.entrypoints.generate.base.protocol'}}

_NATIVE = contextvars.ContextVar("guard_online_native_call", default=None)
_BASE = None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def relative_code(code, filename):
    """Separate /1 algorithm. Existing loaded code_sha256 keeps its filename."""
    return code.replace(co_filename=filename, co_consts=tuple(
        relative_code(value, filename) if type(value) is types.CodeType else value for value in code.co_consts))


def points(base):
    result = {module: dict(classes) for module, classes in base.POINTS.items()}
    for module, classes in POINT_ADDITIONS.items():
        result.setdefault(module, {})
        for name, methods in classes.items():
            result[module][name] = (*result[module].get(name, ()), *methods)
    return result


def functions(base):
    return {**base.FUNCTION_POINTS, **FUNCTION_ADDITIONS}


def _asset(name, expected):
    # Fixed public source assets only. No arbitrary path/config/engine import.
    directory = os.open("/guard-lifecycle", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 262144:
            raise ValueError("ONLINE_ASSET_UNAVAILABLE")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(262145)
        fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        after = os.fstat(fd)
        if (any(getattr(before, key) != getattr(after, key) for key in fields)
                or len(raw) != before.st_size or hashlib.sha256(raw).hexdigest() != expected):
            raise ValueError("ONLINE_ASSET_UNAVAILABLE")
        return raw
    finally:
        os.close(fd)


def configure(base, callback):
    """Configure a privately loaded /8 module; never mutate the installed /8."""
    if (sys.version_info[:2] != (3, 12) or sys.flags.optimize != 0
            or callback.__dict__.get("SCHEMA") != "byul-online-input-snapshot/1"
            or set(callback.POINTS) != set(POINT_CONTRACTS)
            or not inspect.isfunction(callback.snapshot_online_input)
            or base.code_digest(relative_code(callback.snapshot_online_input.__code__, "guard/adapters/vllm_online_input.py")) != CALLBACK_FUNCTION_SHA256):
        raise ValueError("ONLINE_CALLBACK_CODE_UNAVAILABLE")
    combined_points, combined_functions = points(base), functions(base)
    base.PROFILE, base.SCHEMA = PROFILE, SCHEMA
    base.SOURCE_SHA = {**base.SOURCE_SHA, **SOURCE_ADDITIONS}
    base.POINTS, base.FUNCTION_POINTS = combined_points, combined_functions
    base.MAX_ROWS, base.MAX_BYTES = MAX_ROWS, MAX_BYTES
    base.__file__ = __file__  # START identifies this explicit controller asset.
    callback_info = {"source_sha256": CALLBACK_SHA256, "code_sha256": CALLBACK_CODE_SHA256}
    snapshot_callback = callback.snapshot_online_input

    class OnlineObserver(base.Observer):
        def __init__(self, config, config_sha):
            super().__init__(config, config_sha)
            self.modules[CALLBACK_MODULE] = dict(callback_info)

        def _writer(self):
            first = self.writer is None and self.pid == os.getpid()
            writer = super()._writer()
            if first:
                writer.write("MODULE", {"module": CALLBACK_MODULE, "verified": True, **callback_info})
            return writer

        def online_context(self):
            value = self.route_context()
            if (value is None or value.get("path") != base.id_sha("/v1/chat/completions")
                    or value.get("method") != base.id_sha("POST")):
                return None
            return value

        def wrap(self, module_name, point, original):
            if point not in POINT_CONTRACTS:
                return super().wrap(module_name, point, original)
            contract = POINT_CONTRACTS[point]
            if (module_name != contract["module"] or not inspect.isfunction(original)
                    or inspect.iscoroutinefunction(original) != contract["async"]
                    or base.code_digest(relative_code(original.__code__, contract["filename"])) != contract["function_code_sha256"]):
                raise ValueError("ONLINE_POINT_CODE_UNAVAILABLE")
            observer = self

            def emit(frame, args, kwargs, phase, result=None, exception=None):
                started = time.monotonic_ns()
                try:
                    snapshot = snapshot_callback(point, args, kwargs, phase=phase, result=result,
                        exception=exception, binding=frame["binding"])
                    observer.emit("NATIVE", {"point": point, "phase": phase,
                        "source_sha256": base.SOURCE_SHA[module_name], "requests": [],
                        "metadata": {"online_input": snapshot}, "selection_observed": False,
                        "observation_started_ns": started, "observation_finished_ns": time.monotonic_ns()})
                except BaseException:
                    # Observation never replaces a serving return, cancellation or exception.
                    pass

            def begin(args, kwargs):
                context = observer.online_context()
                if not observer.active() or context is None:
                    return None
                owner = observer._writer().context
                parent = _NATIVE.get()
                native = (parent.get("native") if type(parent) is dict and parent.get("pid") == os.getpid()
                    and parent.get("closed") is False and parent.get("invocation") == context["invocation"] else None)
                if point == "InputProcessor.process_inputs":
                    native = base.id_sha(kwargs.get("request_id") if "request_id" in kwargs else args[1] if len(args) > 1 else None)
                binding = {"asgi_request_id_sha256": context["request"],
                    "call_id_sha256": observer.route_nonce("online-call"),
                    "session_id_sha256": digest(observer.config["session"]),
                    "process_start_sha256": digest(owner["start_ticks"]), "pid": owner["pid"]}
                if native is not None:
                    binding["native_request_id_sha256"] = native
                frame = {"binding": binding, "pid": os.getpid(), "invocation": context["invocation"],
                    "native": native, "closed": False}
                token = _NATIVE.set(frame)
                emit(frame, args, kwargs, "CALL")
                return frame, token

            def finish(state, args, kwargs, result, exception):
                if state is None:
                    return
                frame, token = state
                try:
                    # A retained task/context cannot acquire a later ASGI invocation.
                    context = observer.online_context()
                    if context is not None and context["invocation"] == frame["invocation"]:
                        emit(frame, args, kwargs, "RETURN" if exception is None else "RAISE", result, exception)
                finally:
                    frame["closed"] = True
                    _NATIVE.reset(token)

            def safe_begin(args, kwargs):
                try:return begin(args, kwargs)
                except BaseException:return None

            if inspect.iscoroutinefunction(original):
                @functools.wraps(original)
                async def asynchronous(*args, **kwargs):
                    state, result, exception = safe_begin(args, kwargs), None, None
                    try:
                        result = await original(*args, **kwargs)
                        return result
                    except BaseException as exc:
                        exception = exc
                        raise
                    finally:
                        try:finish(state, args, kwargs, result, exception)
                        except BaseException:pass
                return asynchronous

            @functools.wraps(original)
            def synchronous(*args, **kwargs):
                state, result, exception = safe_begin(args, kwargs), None, None
                try:
                    result = original(*args, **kwargs)
                    return result
                except BaseException as exc:
                    exception = exc
                    raise
                finally:
                    try:finish(state, args, kwargs, result, exception)
                    except BaseException:pass
            return synchronous

    class OnlineLoader(base.CheckedLoader):
        def exec_module(self, module):
            if self.name not in MODULE_CONTRACTS:
                return super().exec_module(module)
            contract = MODULE_CONTRACTS[self.name]
            try:
                filename = self.original.get_filename(self.name)
                raw = self.original.get_data(filename)
                if hashlib.sha256(raw).hexdigest() != contract["source_sha256"]:
                    raise ValueError()
                actual = self.original.get_code(self.name)
                expected = compile(raw, filename, "exec", dont_inherit=True, optimize=0)
                if (base.code_digest(actual) != base.code_digest(expected)
                        or base.code_digest(relative_code(actual, contract["filename"])) != contract["code_sha256"]):
                    raise ValueError()
            except Exception:
                self.observer.emit("MODULE", {"module": self.name, "verified": False})
                return self.original.exec_module(module)
            exec(actual, module.__dict__)  # Preserve the original module's exception.
            try:self.observer.instrument(module)
            except Exception:self.observer.emit("MODULE", {"module": self.name, "verified": False})
            else:
                info = {"source_sha256": contract["source_sha256"], "code_sha256": base.code_digest(actual),
                    "online_code_sha256": contract["code_sha256"]}
                self.observer.modules[self.name] = info
                self.observer.emit("MODULE", {"module": self.name, "verified": True, **info})

    class OnlineMiddleware(base.Middleware):
        async def _observed(self, observer, scope, receive, send):
            if scope.get("path") != "/v1/chat/completions" or scope.get("method") != "POST":
                return await super()._observed(observer, scope, receive, send)
            context = observer.online_context()
            if context is None:
                return await self.app(scope, receive, send)
            def emit(phase, more_body=None):
                observer.emit("ASGI", {"request_sha256": context["request"], "phase": phase, "more_body": more_body})
            async def received():
                message = await receive()
                if type(message) is dict and message.get("type") == "http.disconnect":emit("DISCONNECT_RECEIVED")
                return message
            async def sent(message):
                value = await send(message)
                if type(message) is dict and message.get("type") == "http.response.body":
                    more = message.get("more_body", False)
                    emit("BODY_SEND_RETURN", more if type(more) is bool else None)
                return value
            returned = False
            try:
                result = await self.app(scope, received, sent);returned = True;return result
            finally:emit("APP_RETURN" if returned else "APP_RAISE")

    base.Observer, base.CheckedLoader, base.Middleware = OnlineObserver, OnlineLoader, OnlineMiddleware
    return base


def _private_base():
    raw = _asset("guard_lifecycle_base.py", BASE_SHA256)
    base = types.ModuleType("_guard_online_private_base")
    base.__file__ = "/guard-lifecycle/guard_lifecycle_base.py"
    exec(compile(raw, base.__file__, "exec", dont_inherit=True, optimize=0), base.__dict__)
    return base


def install(path=None):
    global _BASE
    if _BASE is not None or sys.version_info[:2] != (3, 12) or sys.flags.optimize != 0:
        return False
    try:
        base = _private_base()
        raw = _asset("guard_online_input_callback.py", CALLBACK_SHA256)
        code = compile(raw, "guard/adapters/vllm_online_input.py", "exec", dont_inherit=True, optimize=0)
        if base.code_digest(code) != CALLBACK_CODE_SHA256:
            return False
        callback = types.ModuleType("_guard_online_private_callback")
        exec(code, callback.__dict__)
        configure(base, callback)
        if not base.install(path):return False
        _BASE = base
        return True
    except Exception:
        return False


class Middleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if _BASE is None:return await self.app(scope, receive, send)
        return await _BASE.Middleware(self.app)(scope, receive, send)


def seal(path="/guard-lifecycle/settings.json"):
    # Separate marker helper: no hook/callback installation or engine imports.
    return (_BASE if _BASE is not None else _private_base()).seal(path)


if __name__ == "__main__":
    if sys.argv[1:] != ["--seal"]:
        raise SystemExit("NATIVE_SENSOR_SEAL_ARGUMENTS_REQUIRED")
    try:seal()
    except Exception:
        raise SystemExit("NATIVE_SENSOR_SEAL_UNAVAILABLE") from None
