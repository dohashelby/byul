"""Closed Linux selected READY endpoint source and delegate qualification.

Endpoint tokens identify a held FIFO within an observation domain. They are
not code approval values, producer authentication, or service readiness.
"""
from __future__ import annotations
import hashlib, json, multiprocessing, os, stat, sys, types
from multiprocessing import connection

MP='vllm.v1.executor.multiproc_executor'
UTILS='vllm.utils.system_utils'
SOURCE_SHA256={MP:'11a1554380d1efa516bb481416c798798d5839f38ac3b0f379ef4b8b547f4d1b',
              UTILS:'9be361854412a4d58cc9eef6b999346c606ab11d388e2644fc47fb454546255d'}
FILENAMES={MP:'vllm/v1/executor/multiproc_executor.py',UTILS:'vllm/utils/system_utils.py'}
POINTS=frozenset({'WorkerProc.make_worker_process','WorkerProc.wait_for_ready'})
HELPERS=frozenset({'get_mp_context','_maybe_force_spawn','_sync_visible_devices_env_vars'})
POINT_CODES={'cpython-3.12.15': {'WorkerProc.make_worker_process': 'eafb7cc6376d3133f40442c88e33a3635721faadcb26288242ed09b10fcc2d52', 'WorkerProc.wait_for_ready': '85c8b8669f098b02301381941a4410144986548971b02723b8a61432cb98a71b', '_sync_visible_devices_env_vars': 'ffc3160fe9118c663ea58fbf8745353015a6e641c9cfad3331d584dbe516a0af', '_maybe_force_spawn': 'dc67408cae5aeb03c1dca2859c42392628a2950c289c875df9c4d54394703cad', 'get_mp_context': 'f598800a048da7a932cdd4a9e2120baf606715f911e4e2200ceb7162c1d9800d'}, 'cpython-3.12.14': {'WorkerProc.make_worker_process': 'eafb7cc6376d3133f40442c88e33a3635721faadcb26288242ed09b10fcc2d52', 'WorkerProc.wait_for_ready': '85c8b8669f098b02301381941a4410144986548971b02723b8a61432cb98a71b', '_sync_visible_devices_env_vars': 'ffc3160fe9118c663ea58fbf8745353015a6e641c9cfad3331d584dbe516a0af', '_maybe_force_spawn': 'dc67408cae5aeb03c1dca2859c42392628a2950c289c875df9c4d54394703cad', 'get_mp_context': 'f598800a048da7a932cdd4a9e2120baf606715f911e4e2200ceb7162c1d9800d'}, 'cpython-3.12.3': {'WorkerProc.make_worker_process': 'b741548366de0102a1f1b72a01159e62498a70f43ef6556f4f3d6df31199aacf', 'WorkerProc.wait_for_ready': '11753530c68a05bcd40714788c1c9e98e3e730a2fdc2bd830a82d03c800c0dc5', '_sync_visible_devices_env_vars': 'ffc3160fe9118c663ea58fbf8745353015a6e641c9cfad3331d584dbe516a0af', '_maybe_force_spawn': 'dc67408cae5aeb03c1dca2859c42392628a2950c289c875df9c4d54394703cad', 'get_mp_context': 'f598800a048da7a932cdd4a9e2120baf606715f911e4e2200ceb7162c1d9800d'}}
DELEGATE_CODES={'cpython-3.12.15': {'recv': 'c7056f4fa2028627d19c7bdb0f4ee9ea3e92af30c53c4828cad74bf9e42dae9f', 'fileno': '78e0e53c0ced00a1ef839a1e7668398ce8787aeeddac7ad66a88e5bc6a4ae8f3'}, 'cpython-3.12.14': {'recv': 'c7056f4fa2028627d19c7bdb0f4ee9ea3e92af30c53c4828cad74bf9e42dae9f', 'fileno': '78e0e53c0ced00a1ef839a1e7668398ce8787aeeddac7ad66a88e5bc6a4ae8f3'}, 'cpython-3.12.3': {'recv': 'c7056f4fa2028627d19c7bdb0f4ee9ea3e92af30c53c4828cad74bf9e42dae9f', 'fileno': '78e0e53c0ced00a1ef839a1e7668398ce8787aeeddac7ad66a88e5bc6a4ae8f3'}}
CONNECTION_FILENAME='python3.12/multiprocessing/connection.py'
DOMAIN_KEYS=('boot_sha256','pid_namespace','time_namespace','time_offsets_sha256')


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def normalize(code,filename):
    return code.replace(co_filename=filename,co_consts=tuple(normalize(v,filename) if type(v) is types.CodeType else v for v in code.co_consts))
def code_digest(base,code,name):return base.code_digest(normalize(code,FILENAMES[name]))


def codes(module,base,prior):
    name=vars(module).get('__name__')
    if name not in SOURCE_SHA256:raise ValueError('READY_ENDPOINT_MODULE_UNAVAILABLE')
    raw=prior.artifact(vars(module).get('__file__'),SOURCE_SHA256[name]);result={}
    def visit(code):
        result[code.co_qualname]=code_digest(base,code,name)
        for child in code.co_consts:
            if type(child) is types.CodeType:visit(child)
    visit(compile(raw,module.__file__,'exec',dont_inherit=True,optimize=0));return result


def matches(value,module,expected,base):
    return bool(type(value) is types.FunctionType and value.__globals__ is vars(module)
                and expected is not None and code_digest(base,value.__code__,module.__name__)==expected)


def qualify(module,base,prior,runtime):
    if type(module) is not types.ModuleType or module.__name__!=MP:raise ValueError('READY_ENDPOINT_MODULE_UNAVAILABLE')
    expected=POINT_CODES.get(runtime)
    if type(expected) is not dict:raise ValueError('READY_ENDPOINT_RUNTIME_UNAVAILABLE')
    compiled=codes(module,base,prior);worker=vars(module).get('WorkerProc');handle=vars(module).get('UnreadyWorkerProcHandle')
    if (type(worker) is not type or worker.__module__!=MP or type(handle) is not type
            or handle.__module__!=MP or handle.__bases__!=(object,)
            or type(vars(handle).get('__annotations__')) is not dict
            or set(vars(handle)['__annotations__'])!={'proc','rank','ready_pipe','death_writer'}
            or handle.__getattribute__ is not object.__getattribute__ or handle.__setattr__ is not object.__setattr__
            or '__getattr__' in vars(handle) or type(vars(handle).get('__dict__')) is not types.GetSetDescriptorType
            or vars(module).get('Connection') is not connection.Connection):raise ValueError('READY_ENDPOINT_CLASS_UNAVAILABLE')
    selected={}
    for point in POINTS:
        descriptor=vars(worker).get(point.split('.')[1])
        if type(descriptor) is not staticmethod:raise ValueError('READY_ENDPOINT_DESCRIPTOR_UNAVAILABLE')
        original=descriptor.__func__
        if (compiled.get(point)!=expected.get(point) or not matches(original,module,expected.get(point),base)
                or original.__qualname__!=point or original.__defaults__!=((None,) if point=='WorkerProc.make_worker_process' else None)
                or original.__kwdefaults__ is not None):raise ValueError('READY_ENDPOINT_BODY_UNAVAILABLE')
        selected[point]={'original':original,'code_sha256':expected[point]}
    utilities=sys.modules.get(UTILS)
    if type(utilities) is not types.ModuleType:raise ValueError('READY_ENDPOINT_CONTEXT_SOURCE_UNAVAILABLE')
    utility_codes=codes(utilities,base,prior)
    for name in HELPERS:
        if utility_codes.get(name)!=expected.get(name) or not matches(vars(utilities).get(name),utilities,expected.get(name),base):
            raise ValueError('READY_ENDPOINT_CONTEXT_SOURCE_UNAVAILABLE')
    if vars(module).get('get_mp_context') is not vars(utilities).get('get_mp_context'):
        raise ValueError('READY_ENDPOINT_CONTEXT_ALIAS_UNAVAILABLE')
    return {'points':selected,'worker_class':worker,'handle_class':handle,
            'get_mp_context':vars(utilities)['get_mp_context'],'utils':utilities}


def delegate(value,name,base,runtime):
    # A Windows PipeConnection fileno is an OS HANDLE, not a POSIX fd.
    if sys.platform!='linux' or type(value) is not connection.Connection:return None
    expected=DELEGATE_CODES.get(runtime,{}).get(name)
    if expected is None:return None
    method=object.__getattribute__(value,name)
    return method if qualified_delegate(value,method,name,base,runtime) else None


def qualified_delegate(value,method,name,base,runtime):
    expected=DELEGATE_CODES.get(runtime,{}).get(name)
    if sys.platform!='linux' or type(value) is not connection.Connection or expected is None:return False
    if (type(method) is not types.MethodType or method.__self__ is not value
            or method.__func__.__globals__ is not vars(connection)
            or method.__func__.__qualname__!='_ConnectionBase.'+name
            or base.code_digest(normalize(method.__func__.__code__,CONNECTION_FILENAME))!=expected):return False
    return True


def endpoint(value,context,session,base,runtime):
    if sys.platform!='linux':return None,'unsupported-platform'
    method=delegate(value,'fileno',base,runtime)
    if method is None:return None,'endpoint-delegate-unqualified'
    if (type(context) is not dict or any(context.get(key) is None for key in DOMAIN_KEYS)
            or type(context.get('pid_namespace')) is not int or type(context.get('time_namespace')) is not int):
        return None,'endpoint-domain-unavailable'
    try:info=os.fstat(method())
    except (OSError,ValueError):return None,'endpoint-unavailable'
    if not stat.S_ISFIFO(info.st_mode):return None,'endpoint-kind-unqualified'
    # Raw fd, kernel device/inode and memory addresses never leave this function.
    value={'schema':'guard-ready-endpoint-domain/1','session_sha256':sha(session.encode()),
           **{key:context[key] for key in DOMAIN_KEYS},'device':info.st_dev,'inode':info.st_ino,'kind':'fifo'}
    return sha(canonical(value)),None


def handle_values(value,qualified):
    if type(value) is not qualified['handle_class']:return None
    data=object.__getattribute__(value,'__dict__')
    if type(data) is not dict or set(data)!={'proc','rank','ready_pipe','death_writer'}:return None
    rank=data['rank'];proc=data['proc'];reader=data['ready_pipe']
    if type(rank) is not int or not 0<=rank<=63 or type(reader) is not connection.Connection:return None
    classes=(multiprocessing.context.SpawnProcess,multiprocessing.context.ForkProcess) if sys.platform=='linux' else ()
    if type(proc) not in classes:return None
    child_pid=proc.pid
    if type(child_pid) is not int or not 0<child_pid<2**63:return None
    return {'reader':reader,'rank':rank,'child_pid_claim':child_pid,
            'start_method':'spawn' if type(proc) is multiprocessing.context.SpawnProcess else 'fork'}
