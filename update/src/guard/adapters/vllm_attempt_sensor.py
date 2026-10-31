"""Explicit ASGI invocation observations over the immutable native /8 base.

Explicit /10 admission binds its parser and reviewed source/code contracts.
No engine imports or installation occur on module import.
"""
from __future__ import annotations
import hashlib
import os
import stat
import sys
import types

PROFILE="vllm-native-lifecycle-metadata/10"
SCHEMA="guard-native-lifecycle-events/10"
COMMIT="db9527a46873454610df6dbedf79a36d6bf1a7f6"
BASE_SHA256="2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd"
MAX_ROWS=2048
MAX_BYTES=2*1024**2
BODY_HASH_LIMIT=65536
CODE_FILENAME="guard/adapters/vllm_attempt_sensor.py"
# Offline source/code review fills these literals before profile admission.
# A target's observed code hash is never an approval value.
RUNTIME_CODE_CONTRACTS={'cpython-3.12.15': 'b6ba96a8055d77fd6e848e7aaebe63770568c038a0252a6f3e3b2d367ea0f20f', 'cpython-3.12.14':'b6ba96a8055d77fd6e848e7aaebe63770568c038a0252a6f3e3b2d367ea0f20f','cpython-3.12.3':'63377a277dbaf2452c6f3fa14ef93c4816c5528c021d40694cb5d36453040b56'}
_BASE=None


def _dependency():
    directory=os.open('/guard-lifecycle',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:fd=os.open('guard_lifecycle_base.py',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
    finally:os.close(directory)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=262144:raise ValueError('ATTEMPT_BASE_UNAVAILABLE')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(262145)
        after=os.fstat(fd);fields=('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns')
        if (any(getattr(before,k)!=getattr(after,k) for k in fields) or len(raw)!=before.st_size
                or hashlib.sha256(raw).hexdigest()!=BASE_SHA256):raise ValueError('ATTEMPT_BASE_UNAVAILABLE')
        return raw
    finally:os.close(fd)


def relative_code(code):
    return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(relative_code(v) if isinstance(v,types.CodeType) else v for v in code.co_consts))


def configure(base):
    """Configure only a private exact-byte /8 module; installed /8 and /9 stay unchanged."""
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]))
    expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if (sys.implementation.name!='cpython' or sys.flags.optimize!=0 or expected is None) or (runtime == 'cpython-3.12.15' and (sys.platform != 'linux' or os.name != 'posix' or os.uname().machine != 'x86_64' or sys.flags.optimize != 0)):raise ValueError('ATTEMPT_RUNTIME_UNAVAILABLE')
    base.PROFILE,base.SCHEMA=PROFILE,SCHEMA
    base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES
    base.__file__=__file__

    class AttemptMiddleware(base.Middleware):
        async def _observed(self,observer,scope,receive,send):
            try:context=observer.selection_context()
            except BaseException:context=None
            if (type(context) is not dict or scope.get('path')!='/v1/completions' or scope.get('method')!='POST'):
                return await super()._observed(observer,scope,receive,send)
            invocation={k:context.get(v) for k,v in (
                ('asgi_request_sha256','request'),('asgi_invocation_sha256','invocation'),('entry_path_sha256','path'),
                ('entry_root_path_sha256','root'),('entry_method_sha256','method'))}
            try:code_sha=base.code_digest(relative_code(type(self)._observed.__code__))
            except BaseException:return await super()._observed(observer,scope,receive,send)
            def emit(phase,message=None):
                try:
                    body=message.get('body') if type(message) is dict else None
                    size=len(body) if type(body) is bytes else None
                    hashed=size is not None and size<=BODY_HASH_LIMIT
                    more=message.get('more_body',False) if type(message) is dict and message.get('type')=='http.response.body' else None
                    observer.emit('ATTEMPT',{**invocation,'phase':phase,'context_live':context.get('pid')==os.getpid() and context.get('closed') is False,
                        'body_bytes':size,'body_sha256':hashlib.sha256(body).hexdigest() if hashed else None,'body_hash_complete':hashed,
                        'more_body':more if type(more) is bool else None,'observer_code_sha256':code_sha,'observer_runtime':runtime})
                except BaseException:pass  # Observation must never change serving returns or exceptions.
            async def received():
                message=await receive()
                if type(message) is dict and message.get('type')=='http.disconnect':emit('DISCONNECT_RECEIVED')
                return message
            async def sent(message):
                try:result=await send(message)
                except BaseException:
                    if type(message) is dict and message.get('type')=='http.response.body':emit('BODY_SEND_RAISE',message)
                    raise
                if type(message) is dict and message.get('type')=='http.response.body':emit('BODY_SEND_RETURN',message)
                return result
            emit('APP_ENTER')
            try:result=await super()._observed(observer,scope,received,sent)
            except BaseException:
                emit('APP_RAISE');raise
            emit('APP_RETURN')
            return result

    actual=base.code_digest(relative_code(AttemptMiddleware._observed.__code__))
    if actual!=expected:raise ValueError('ATTEMPT_CODE_UNAVAILABLE')
    base.Middleware=AttemptMiddleware
    return base


def _private_base():
    raw=_dependency();base=types.ModuleType('_guard_attempt_private_base')
    base.__file__='/guard-lifecycle/guard_lifecycle_base.py'
    exec(compile(raw,base.__file__,'exec',dont_inherit=True,optimize=0),base.__dict__)
    return base


def install(path=None):
    global _BASE
    if _BASE is not None:return False
    try:
        base=configure(_private_base())
        if not base.install(path):return False
        _BASE=base;return True
    except Exception:return False


class Middleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if _BASE is None:return await self.app(scope,receive,send)
        return await _BASE.Middleware(self.app)(scope,receive,send)


def seal(path='/guard-lifecycle/settings.json'):
    return (_BASE if _BASE is not None else _private_base()).seal(path)


if __name__=='__main__':
    if sys.argv[1:]!=['--seal']:raise SystemExit('NATIVE_SENSOR_SEAL_ARGUMENTS_REQUIRED')
    try:seal()
    except Exception:raise SystemExit('NATIVE_SENSOR_SEAL_UNAVAILABLE') from None
