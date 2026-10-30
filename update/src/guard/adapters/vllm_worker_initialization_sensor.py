"""Explicit native20 composition of immutable Worker19 and MP14 observers.

The two process domains remain separate; this module does not spawn workers,
inject faults, infer READY, or qualify a cross-process startup lineage.
"""
from __future__ import annotations
import hashlib, os, stat, sys, types

PROFILE = 'vllm-native-lifecycle-metadata/20'
SCHEMA = 'guard-native-lifecycle-events/20'
COMMIT = 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
STARTUP19_SHA256 = '96544214cb02a84517b6329149ef0897150b98cfbedee60c8baa2189a07f94a1'
INITIALIZATION14_SHA256 = 'f0c392ac9024fb3bb7a1cbdd65bb8c3f4ab23bcfa61065af06d4f91542d09ce5'
MAX_BYTES = 2*1024**2
MAX_ROWS = 2048
CODE_FILENAME = 'guard/adapters/vllm_worker_initialization_sensor.py'
RUNTIME_CODE_CONTRACTS = {'cpython-3.12.14':'408b2a00bf2cc124e612ed865b1de8ca84889cd9c4e1e8194b5a9d8683312ea1','cpython-3.12.3':'575420ab0646cec6d3cef30960771e43c4230ca910fcad55508557283cdf68b1'}
_BASE = None


def artifact(path, expected):
    selected = os.lstat(path)
    if not stat.S_ISREG(selected.st_mode):
        raise ValueError('WORKER_INITIALIZATION_ASSET_UNAVAILABLE')
    fd = os.open(path, os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 131072:
            raise ValueError('WORKER_INITIALIZATION_ASSET_UNAVAILABLE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(131073)
        after, current = os.fstat(fd), os.lstat(path)
        identity = lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns)
        if (len(raw) != before.st_size or hashlib.sha256(raw).hexdigest() != expected
                or len({identity(s) for s in (selected,before,after,current)}) != 1
                or selected.st_ctime_ns != current.st_ctime_ns or before.st_ctime_ns != after.st_ctime_ns):
            raise ValueError('WORKER_INITIALIZATION_ASSET_UNAVAILABLE')
        return raw
    finally:
        os.close(fd)


def relative_code(code):
    return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(relative_code(v) if type(v) is types.CodeType else v for v in code.co_consts))


def code_contract(base):
    functions = (artifact, relative_code, configure, module, _private_base, install)
    return hashlib.sha256(''.join(base.code_digest(relative_code(f.__code__)) for f in functions).encode()).hexdigest()


def configure(base, initialization, startup):
    runtime = 'cpython-'+'.'.join(map(str,sys.version_info[:3]))
    expected = RUNTIME_CODE_CONTRACTS.get(runtime)
    if (sys.implementation.name != 'cpython' or sys.flags.optimize != 0 or expected is None
            or code_contract(base) != expected
            or (runtime,os.name) not in {('cpython-3.12.14','nt'),('cpython-3.12.3','posix')}
            or base.PROFILE != 'vllm-native-lifecycle-metadata/19' or base.SCHEMA != 'guard-native-lifecycle-events/19'):
        raise ValueError('WORKER_INITIALIZATION_CODE_UNAVAILABLE')
    artifact(startup.__file__, STARTUP19_SHA256)
    artifact(initialization.__file__, INITIALIZATION14_SHA256)
    initialization.configure(base)
    base.PROFILE, base.SCHEMA = PROFILE, SCHEMA
    base.__file__ = __file__
    base.MAX_ROWS, base.MAX_BYTES = MAX_ROWS, MAX_BYTES
    return base


def module(name, filename, expected):
    path = '/guard-lifecycle/'+filename
    raw = artifact(path, expected)
    result = types.ModuleType(name)
    result.__file__ = path
    exec(compile(raw,path,'exec',dont_inherit=True,optimize=0),vars(result))
    return result


def _private_base():
    startup = module('_guard_joint_startup19','guard_worker_startup.py',STARTUP19_SHA256)
    initialization = module('_guard_joint_initialization14','guard_initialization.py',INITIALIZATION14_SHA256)
    base = startup._private_base()
    return configure(base, initialization, startup)


def install(path=None):
    global _BASE
    if _BASE is not None:
        return False
    try:
        base = _private_base()
        if not base.install(path):
            return False
        _BASE = base
        return True
    except Exception:
        return False


class Middleware:
    def __init__(self,app):
        self.app = app
    async def __call__(self,scope,receive,send):
        if _BASE is None:
            return await self.app(scope,receive,send)
        return await _BASE.Middleware(self.app)(scope,receive,send)
