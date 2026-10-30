"""Closed Worker body/decorator provenance and byte-preserved B callback loading.

Only Python source/callable identity is qualified. No backend, GPU, worker role,
model approval or actual body entry is inferred from a decorator boundary.
"""
from __future__ import annotations
import builtins,hashlib,os,stat,sys,types

WORKER='vllm.v1.worker.gpu_worker'
TRACING='vllm.tracing'
OTEL='vllm.tracing.otel'
UTILS='vllm.tracing.utils'
SOURCE_SHA256={WORKER:'43bd01925cd84f7d8a1d4a2ec30e289014a485fe4492a5343cbc893e8ed2f518',
 TRACING:'6bbc91c37e28346e61359ddb53a5169bcadd8dee149aa93ebf08267778388386',
 OTEL:'481f62779faa29b87a5b7a1f003a90b94eec81d84fbe6fd8e4e2e843297149bf',
 UTILS:'889bce0fdf8e6a13380b32a58ce133401d80623f463b88c37b54a1e504d12fc4'}
SPAN_NAMES={'Worker.init_device':'Init device','Worker.load_model':None,
 'Worker.initialize_from_config':'Allocate KV cache','Worker.compile_or_warm_up_model':'Warmup (GPU)'}
CALLBACK_SHA256='a583d32f31d74def618256557ee9e7aee801d5fe1ff55e27b6221d7f2420a965'
SUPPORT_SHA256='659d615f133cdc96ef54163678753a60e6b8ee51aa0e78e0560e0d308f7f50da'
BACKEND_NAMES=('is_otel_available','init_otel_tracer','init_otel_worker_tracer','instrument_otel','manual_instrument_otel')
SOURCE_FILENAMES={WORKER:'vllm/v1/worker/gpu_worker.py',TRACING:'vllm/tracing/__init__.py',OTEL:'vllm/tracing/otel.py',UTILS:'vllm/tracing/utils.py'}


def sha(raw):return hashlib.sha256(raw).hexdigest()


def relative_code(code,module_name):
    return code.replace(co_filename=SOURCE_FILENAMES[module_name],co_consts=tuple(relative_code(value,module_name) if type(value) is types.CodeType else value for value in code.co_consts))


def method_digest(base,code,module_name):return base.code_digest(relative_code(code,module_name))


def artifact(path,expected,limit=131072):
    """Keep path and descriptor ctime comparisons within their own OS basis."""
    selected=os.lstat(path)
    if not stat.S_ISREG(selected.st_mode):raise ValueError('WORKER_STARTUP_SOURCE_UNAVAILABLE')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=limit:raise ValueError('WORKER_STARTUP_SOURCE_UNAVAILABLE')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(limit+1)
        after=os.fstat(fd);current=os.lstat(path)
        common=lambda value:(value.st_dev,value.st_ino,value.st_mode,value.st_size,value.st_mtime_ns)
        if (len({common(value) for value in (selected,before,after,current)})!=1
                or selected.st_ctime_ns!=current.st_ctime_ns or before.st_ctime_ns!=after.st_ctime_ns
                or not stat.S_ISREG(current.st_mode) or len(raw)!=before.st_size or sha(raw)!=expected):
            raise ValueError('WORKER_STARTUP_SOURCE_UNAVAILABLE')
        return raw
    finally:os.close(fd)


def compiled_codes(module,base):
    name=vars(module).get('__name__');path=vars(module).get('__file__')
    if name not in SOURCE_SHA256 or type(path) is not str:raise ValueError('WORKER_STARTUP_MODULE_UNAVAILABLE')
    raw=artifact(path,SOURCE_SHA256[name]);result={}
    def visit(code):
        result[code.co_qualname]=method_digest(base,code,name)
        for value in code.co_consts:
            if type(value) is types.CodeType:visit(value)
    visit(compile(raw,path,'exec',dont_inherit=True,optimize=0))
    return result


def function_matches(value,module,expected,base):
    return bool(type(value) is types.FunctionType and value.__globals__ is vars(module)
        and expected is not None and method_digest(base,value.__code__,module.__name__)==expected)


def qualify(worker_module,base,*,modules=None):
    """Return the unchanged callable, never its unwrapped replacement."""
    if type(worker_module) is not types.ModuleType or worker_module.__name__!=WORKER:
        raise ValueError('WORKER_STARTUP_MODULE_UNAVAILABLE')
    loaded=sys.modules if modules is None else modules
    selected={name:worker_module if name==WORKER else loaded.get(name) for name in SOURCE_SHA256}
    if any(type(module) is not types.ModuleType for module in selected.values()):
        raise ValueError('WORKER_STARTUP_DECORATOR_UNAVAILABLE')
    expected={name:compiled_codes(module,base) for name,module in selected.items()}
    tracing,otel=selected[TRACING],selected[OTEL]
    if not function_matches(vars(tracing).get('instrument'),tracing,expected[TRACING].get('instrument'),base):
        raise ValueError('WORKER_STARTUP_DECORATOR_UNAVAILABLE')
    if vars(worker_module).get('instrument') is not vars(tracing).get('instrument'):
        raise ValueError('WORKER_STARTUP_DECORATOR_UNAVAILABLE')
    backends=vars(tracing).get('_REGISTERED_TRACING_BACKENDS')
    if type(backends) is not dict or set(backends)!={'otel'} or type(backends['otel']) is not tuple or len(backends['otel'])!=5:
        raise ValueError('WORKER_STARTUP_DECORATOR_UNAVAILABLE')
    for original,name in zip(backends['otel'],BACKEND_NAMES):
        if (original is not vars(otel).get(name) or vars(tracing).get(name) is not original
                or not function_matches(original,otel,expected[OTEL].get(name),base)):
            raise ValueError('WORKER_STARTUP_DECORATOR_UNAVAILABLE')
    availability=vars(otel).get('_IS_OTEL_AVAILABLE')
    if type(availability) is not bool:raise ValueError('WORKER_STARTUP_DECORATOR_UNAVAILABLE')
    cls=vars(worker_module).get('Worker')
    if type(cls) is not type or cls.__module__!=WORKER:
        raise ValueError('WORKER_STARTUP_CLASS_UNAVAILABLE')
    result={}
    for point,span in SPAN_NAMES.items():
        name=point.split('.')[1];original=vars(cls).get(name);body=original;kind='direct-body'
        if span is not None and availability:
            if not function_matches(original,otel,expected[OTEL].get('instrument_otel.<locals>.sync_wrapper'),base):
                raise ValueError('WORKER_STARTUP_DECORATOR_CODE_UNAVAILABLE')
            cells=dict(zip(original.__code__.co_freevars,original.__closure__ or ()))
            values={key:cell.cell_contents for key,cell in cells.items()}
            body=values.get('func')
            if (vars(original).get('__wrapped__') is not body or values.get('final_span_name')!=span
                    or values.get('module_name')!=WORKER or values.get('record_exception') is not True
                    or type(values.get('code_attrs')) is not dict):
                raise ValueError('WORKER_STARTUP_DECORATOR_BINDING_UNAVAILABLE')
            kind='otel-sync-wrapper'
        if not function_matches(body,worker_module,expected[WORKER].get(point),base):
            raise ValueError('WORKER_STARTUP_BODY_CODE_UNAVAILABLE')
        if body.__qualname__!=point or body.__name__!=name:
            raise ValueError('WORKER_STARTUP_BODY_CODE_UNAVAILABLE')
        result[point]={'original':original,'body_code_sha256':expected[WORKER][point],
            'entry_code_sha256':method_digest(base,original.__code__,OTEL if kind=='otel-sync-wrapper' else WORKER),'entry_kind':kind,
            'source_files':dict(SOURCE_SHA256),'inner_body_entry_observed':False}
    if vars(otel).get('_IS_OTEL_AVAILABLE') is not availability:
        raise ValueError('WORKER_STARTUP_DECORATOR_CHANGED')
    return result


def load_callback(callback_path,support_path):
    """Use exact original B bytes and an explicit private dependency edge.

    The one original from-import resolves to its byte-preserved private B helper.
    No guard namespace or serving import hook is installed or replaced.
    """
    support_raw=artifact(support_path,SUPPORT_SHA256)
    callback_raw=artifact(callback_path,CALLBACK_SHA256)
    support=types.ModuleType('_guard_worker_readiness_support');support.__file__=support_path
    exec(compile(support_raw,support_path,'exec',dont_inherit=True,optimize=0),vars(support))
    callback=types.ModuleType('_guard_worker_readiness_callback');callback.__file__=callback_path
    original_import=builtins.__import__
    def dependency_import(name,globals=None,locals=None,fromlist=(),level=0):
        if name=='guard.adapters.vllm_slot_mask':
            if level!=0 or tuple(fromlist)!=('BINDING_HASHES','_binding','_get'):
                raise ImportError('WORKER_STARTUP_CALLBACK_DEPENDENCY_UNAVAILABLE')
            return support
        return original_import(name,globals,locals,fromlist,level)
    callback.__dict__['__builtins__']={**vars(builtins),'__import__':dependency_import}
    exec(compile(callback_raw,callback_path,'exec',dont_inherit=True,optimize=0),vars(callback))
    return callback,support

SNAPSHOT_FIELDS=frozenset({'schema','point','phase','status','seq','binding','metadata','gaps'})
BINDING_FIELDS=frozenset({'pid','asgi_request_id_sha256','native_request_id_sha256','call_id_sha256','session_id_sha256','process_start_sha256'})
METADATA_FIELDS=frozenset({'owner_token','thread_token','rank','local_rank','enforce_eager','configured_graph_mode','explicit_load_dummy_weights','source_qualified','actual_worker_observed','gpu_completion_verified','all_model_ready_verified','ready_send_verified','service_gate_verified','call_transition'})
TRANSITION_FIELDS=frozenset({'paired','call_seq','end_seq','same_owner','same_thread','returned'})


def fingerprint(value):
    return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def closed_callback(value,callback):
    """Validate the known scalar B output without changing its qualification."""
    try:
        if type(value) is not dict or set(value)!=SNAPSHOT_FIELDS or value['schema']!=callback.SCHEMA:raise ValueError
        if value['point'] not in callback.POINTS|{'unsupported'} or value['phase'] not in callback.PHASES|{'unsupported'} or value['status'] not in callback.STATUSES:raise ValueError
        seq=value['seq']
        if not (seq is None and value['status']=='unavailable' or type(seq) is int and 0<seq<2**63):raise ValueError
        binding=value['binding'];data=value['metadata'];gaps=value['gaps']
        if type(binding) is not dict or not set(binding)<=BINDING_FIELDS:raise ValueError
        for key,item in binding.items():
            if key=='pid':
                if type(item) is not int or not 0<item<2**63:raise ValueError
            elif not fingerprint(item):raise ValueError
        if type(gaps) is not list or len(gaps)>len(callback.GAPS) or len(set(gaps))!=len(gaps) or any(type(g) is not str or g not in callback.GAPS for g in gaps):raise ValueError
        if type(data) is not dict:raise ValueError
        if value['status']=='unavailable':
            if data:raise ValueError
        else:
            if set(data)!=METADATA_FIELDS:raise ValueError
            if any(data[k] is not None and not fingerprint(data[k]) for k in ('owner_token','thread_token')):raise ValueError
            if any(data[k] is not None and (type(data[k]) is not int or not 0<=data[k]<2**63) for k in ('rank','local_rank')):raise ValueError
            if any(data[k] is not None and type(data[k]) is not bool for k in ('enforce_eager','explicit_load_dummy_weights')):raise ValueError
            if data['configured_graph_mode'] is not None and data['configured_graph_mode'] not in callback.GRAPH_MODES:raise ValueError
            if data['source_qualified'] is not False or data['actual_worker_observed'] is not False:raise ValueError
            if any(data[k] is not None for k in ('gpu_completion_verified','all_model_ready_verified','ready_send_verified','service_gate_verified')):raise ValueError
            transition=data['call_transition']
            if type(transition) is not dict or set(transition)!=TRANSITION_FIELDS or type(transition['paired']) is not bool:raise ValueError
            if any(transition[k] is not None and type(transition[k]) is not bool for k in ('same_owner','same_thread','returned')):raise ValueError
            if any(transition[k] is not None and (type(transition[k]) is not int or not 0<transition[k]<2**63) for k in ('call_seq','end_seq')):raise ValueError
            if transition['paired'] and (value['phase']=='CALL' or transition['same_owner'] is not True or transition['same_thread'] is not True
                    or transition['returned']!=(value['phase']=='RETURN') or transition['call_seq'] is None
                    or transition['end_seq']!=seq or not transition['call_seq']<transition['end_seq']):raise ValueError
        import json
        return json.loads(json.dumps(value,allow_nan=False))
    except (KeyError,ValueError,TypeError,OverflowError):
        raise ValueError('WORKER_STARTUP_CALLBACK_INVALID') from None


CALLBACK_MODULE='guard.adapters.vllm_worker_readiness'
SUPPORT_MODULE='guard.adapters.vllm_slot_mask'
SOURCE_FILENAMES.update({CALLBACK_MODULE:'guard/adapters/vllm_worker_readiness.py',SUPPORT_MODULE:'guard/adapters/vllm_slot_mask.py'})


def callback_code_contract(callback,support,base):
    """Compare actual callback/helper functions with the fixed original bytes."""
    expected={}
    for module,name,digest in ((callback,CALLBACK_MODULE,CALLBACK_SHA256),(support,SUPPORT_MODULE,SUPPORT_SHA256)):
        raw=artifact(module.__file__,digest)
        def visit(code):
            if name==CALLBACK_MODULE and ('.' not in code.co_qualname or code.co_qualname.startswith('WorkerReadinessObserver.') and code.co_qualname.count('.')==1):
                if code.co_name not in ('<module>','WorkerReadinessObserver'):expected[(name,code.co_qualname)]=method_digest(base,code,name)
            if name==SUPPORT_MODULE and code.co_qualname in ('_binding','_get','_sha'):
                expected[(name,code.co_qualname)]=method_digest(base,code,name)
            for value in code.co_consts:
                if type(value) is types.CodeType:visit(value)
        visit(compile(raw,SOURCE_FILENAMES[name],'exec',dont_inherit=True,optimize=0))
    cls=vars(callback).get('WorkerReadinessObserver')
    if type(cls) is not type:raise ValueError('WORKER_STARTUP_CALLBACK_CODE_UNAVAILABLE')
    for (name,point),digest in expected.items():
        module=callback if name==CALLBACK_MODULE else support
        value=vars(cls).get(point.split('.')[1]) if point.startswith('WorkerReadinessObserver.') else vars(module).get(point)
        if type(value) is staticmethod:value=value.__func__
        if (type(value) is not types.FunctionType or value.__globals__ is not vars(module)
                or method_digest(base,value.__code__,name)!=digest):
            raise ValueError('WORKER_STARTUP_CALLBACK_CODE_UNAVAILABLE')
    if (vars(callback).get('_binding') is not vars(support).get('_binding')
            or vars(callback).get('_get') is not vars(support).get('_get')
            or vars(callback).get('BINDING_HASHES') is not vars(support).get('BINDING_HASHES')):
        raise ValueError('WORKER_STARTUP_CALLBACK_BINDING_UNAVAILABLE')
    return {name:sha(''.join(point+':'+digest for (owner,point),digest in sorted(expected.items()) if owner==name).encode()) for name in (CALLBACK_MODULE,SUPPORT_MODULE)}



