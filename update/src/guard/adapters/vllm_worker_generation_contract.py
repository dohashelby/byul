"""Closed source/callable and selected stdlib pipe qualification for native21."""
from __future__ import annotations
import hashlib,multiprocessing.connection as connection,os,sys,types

MP='vllm.v1.executor.multiproc_executor'
WRAPPER='vllm.v1.worker.worker_base'
WORKER='vllm.v1.worker.gpu_worker'
TRACING='vllm.tracing';OTEL='vllm.tracing.otel';UTILS='vllm.tracing.utils'
SOURCE_SHA256={MP:'11a1554380d1efa516bb481416c798798d5839f38ac3b0f379ef4b8b547f4d1b',WRAPPER:'41cdc6fb72d5cb2352093de7fdf35bf829fbb9e097dc6113d4597cd9a88a46aa',TRACING:'6bbc91c37e28346e61359ddb53a5169bcadd8dee149aa93ebf08267778388386',OTEL:'481f62779faa29b87a5b7a1f003a90b94eec81d84fbe6fd8e4e2e843297149bf',UTILS:'889bce0fdf8e6a13380b32a58ce133401d80623f463b88c37b54a1e504d12fc4'}
FILENAMES={MP:'vllm/v1/executor/multiproc_executor.py',WRAPPER:'vllm/v1/worker/worker_base.py',OTEL:'vllm/tracing/otel.py'}
TARGETS={MP:{'WorkerProc.__init__':'Worker init','WorkerProc.worker_main':None},WRAPPER:{'WorkerWrapperBase.init_worker':'Worker init'}}
POINTS=frozenset(point for values in TARGETS.values() for point in values)
POINT_CODES={'cpython-3.12.15': {'WorkerProc.__init__': '85d69cb553f0ea00d18781af6708382cde396970101d9ebb6091caf29ed2c85a', 'WorkerProc.worker_main': '3282f22dbc3394868387698eff54f4dd061a0d2608fed692ac681da686c19dcf', 'WorkerWrapperBase.init_worker': '7d3d03d9f4ba28746b14b0b75c9007188dfdd4bfe6758f498f8c8f9a78d486f0', 'otel-sync-wrapper': '9fde31857299fbabbc2c5c322bc72ee76f47155aca75762602f4de1bd00d8132'}, 'cpython-3.12.14': {'WorkerProc.__init__': '85d69cb553f0ea00d18781af6708382cde396970101d9ebb6091caf29ed2c85a', 'WorkerProc.worker_main': '3282f22dbc3394868387698eff54f4dd061a0d2608fed692ac681da686c19dcf', 'WorkerWrapperBase.init_worker': '7d3d03d9f4ba28746b14b0b75c9007188dfdd4bfe6758f498f8c8f9a78d486f0', 'otel-sync-wrapper': '9fde31857299fbabbc2c5c322bc72ee76f47155aca75762602f4de1bd00d8132'}, 'cpython-3.12.3': {'WorkerProc.__init__': '99db52f2b8bb4a5b9d47b991a71a6ca927b63a57c4f498746421848097ccdf5c', 'WorkerProc.worker_main': 'e7e187a9523a379e72f1a750c83b4cb02270265c6841a6b37c0f0dac049c2066', 'WorkerWrapperBase.init_worker': '872409aed43854de1e0e7543c0c523c65c5f9b5b069ed3478f0409869ea4cbad', 'otel-sync-wrapper': '64a5e83f01a56099c7fd66af1865cdc9ccbde9203196e801df1c18dcfa51bdca'}}
CONNECTION_CODES={'cpython-3.12.15': {'send': 'a87ab0da6a765a85d67f3cf9e147de3fa574417a14661e068a38c58ad2c8f572', 'close': '0aac1243a67b0533f1c2c0832aa9a45f06eafd9edfff56b2b1fb4df1f6d4646b'}, 'cpython-3.12.14': {'send': 'a87ab0da6a765a85d67f3cf9e147de3fa574417a14661e068a38c58ad2c8f572', 'close': '0aac1243a67b0533f1c2c0832aa9a45f06eafd9edfff56b2b1fb4df1f6d4646b'}, 'cpython-3.12.3': {'send': 'a87ab0da6a765a85d67f3cf9e147de3fa574417a14661e068a38c58ad2c8f572', 'close': '0aac1243a67b0533f1c2c0832aa9a45f06eafd9edfff56b2b1fb4df1f6d4646b'}}
CONNECTION_FILENAME='python3.12/multiprocessing/connection.py'


def sha(raw):return hashlib.sha256(raw).hexdigest()


def relative_code(code,filename):
    return code.replace(co_filename=filename,co_consts=tuple(relative_code(v,filename) if type(v) is types.CodeType else v for v in code.co_consts))


def method_digest(base,code,name):return base.code_digest(relative_code(code,FILENAMES[name]))


def compiled_codes(module,base,prior):
    name=vars(module).get('__name__')
    if name not in {MP,WRAPPER}:raise ValueError('WORKER_GENERATION_MODULE_UNAVAILABLE')
    raw=prior.artifact(vars(module).get('__file__'),SOURCE_SHA256[name]);result={}
    def visit(code):
        result[code.co_qualname]=method_digest(base,code,name)
        for value in code.co_consts:
            if type(value) is types.CodeType:visit(value)
    visit(compile(raw,module.__file__,'exec',dont_inherit=True,optimize=0));return result


def matches(value,module,expected,base):
    return bool(type(value) is types.FunctionType and value.__globals__ is vars(module)
        and expected is not None and method_digest(base,value.__code__,module.__name__)==expected)


def qualify(module,base,prior,*,loaded=None):
    name=vars(module).get('__name__')
    if type(module) is not types.ModuleType or name not in TARGETS:raise ValueError('WORKER_GENERATION_MODULE_UNAVAILABLE')
    loaded=sys.modules if loaded is None else loaded
    selected={key:loaded.get(key) for key in (TRACING,OTEL,UTILS)}
    if any(type(v) is not types.ModuleType for v in selected.values()):raise ValueError('WORKER_GENERATION_TRACING_UNAVAILABLE')
    checked={key:prior.compiled_codes(value,base) for key,value in selected.items()}
    tracing,otel=selected[TRACING],selected[OTEL]
    instrument=vars(tracing).get('instrument')
    if (not prior.function_matches(instrument,tracing,checked[TRACING].get('instrument'),base)
            or vars(module).get('instrument') is not instrument):raise ValueError('WORKER_GENERATION_TRACING_UNAVAILABLE')
    backends=vars(tracing).get('_REGISTERED_TRACING_BACKENDS')
    if type(backends) is not dict or set(backends)!={'otel'} or type(backends['otel']) is not tuple or len(backends['otel'])!=5:
        raise ValueError('WORKER_GENERATION_TRACING_UNAVAILABLE')
    for original,key in zip(backends['otel'],prior.BACKEND_NAMES):
        if (original is not vars(otel).get(key) or vars(tracing).get(key) is not original
                or not prior.function_matches(original,otel,checked[OTEL].get(key),base)):
            raise ValueError('WORKER_GENERATION_TRACING_UNAVAILABLE')
    available=vars(otel).get('_IS_OTEL_AVAILABLE')
    if type(available) is not bool:raise ValueError('WORKER_GENERATION_TRACING_UNAVAILABLE')
    codes=compiled_codes(module,base,prior);result={}
    for point,span in TARGETS[name].items():
        cls_name,method=point.split('.');cls=vars(module).get(cls_name)
        if type(cls) is not type or cls.__module__!=name:raise ValueError('WORKER_GENERATION_CLASS_UNAVAILABLE')
        descriptor=vars(cls).get(method)
        if point=='WorkerProc.worker_main':
            if type(descriptor) is not staticmethod:raise ValueError('WORKER_GENERATION_DESCRIPTOR_UNAVAILABLE')
            original=descriptor.__func__
        else:original=descriptor
        body=original;kind='direct-body'
        if span is not None and available:
            if not prior.function_matches(original,otel,checked[OTEL].get('instrument_otel.<locals>.sync_wrapper'),base):
                raise ValueError('WORKER_GENERATION_DECORATOR_UNAVAILABLE')
            cells=dict(zip(original.__code__.co_freevars,original.__closure__ or ()))
            values={key:cell.cell_contents for key,cell in cells.items()};body=values.get('func')
            if (vars(original).get('__wrapped__') is not body or values.get('final_span_name')!=span
                    or values.get('module_name')!=name or values.get('record_exception') is not True
                    or type(values.get('code_attrs')) is not dict):raise ValueError('WORKER_GENERATION_DECORATOR_UNAVAILABLE')
            kind='otel-sync-wrapper'
        if not matches(body,module,codes.get(point),base) or body.__qualname__!=point or body.__name__!=method:
            raise ValueError('WORKER_GENERATION_BODY_UNAVAILABLE')
        entry=prior.method_digest(base,original.__code__,OTEL) if kind=='otel-sync-wrapper' else method_digest(base,original.__code__,name)
        result[point]={'original':original,'source_module':name,'source_sha256':SOURCE_SHA256[name],
            'body_code_sha256':codes[point],'entry_code_sha256':entry,'entry_kind':kind}
    if vars(otel).get('_IS_OTEL_AVAILABLE') is not available:raise ValueError('WORKER_GENERATION_TRACING_CHANGED')
    return result


def pipe_methods(value,base,runtime):
    allowed=(connection.Connection,)+(tuple([connection.PipeConnection]) if os.name=='nt' and hasattr(connection,'PipeConnection') else ())
    if type(value) not in allowed:return None
    expected=CONNECTION_CODES.get(runtime)
    if type(expected) is not dict:return None
    methods={}
    for name in ('send','close'):
        method=object.__getattribute__(value,name)
        if (type(method) is not types.MethodType or method.__self__ is not value
                or method.__func__.__globals__ is not vars(connection)
                or method.__func__.__qualname__!='_ConnectionBase.'+name
                or base.code_digest(relative_code(method.__func__.__code__,CONNECTION_FILENAME))!=expected[name]):return None
        methods[name]=method
    return methods


def exact_worker(value,loaded):
    gpu=loaded.get(WORKER);wrapper=loaded.get(WRAPPER)
    if type(gpu) is not types.ModuleType or type(wrapper) is not types.ModuleType:return False
    cls=vars(gpu).get('Worker');parent=vars(wrapper).get('WorkerBase')
    return bool(type(cls) is type and type(value) is cls and cls.__module__==WORKER
        and type(parent) is type and cls.__bases__==(parent,)
        and cls.__hash__ is object.__hash__ and cls.__eq__ is object.__eq__)
