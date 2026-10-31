"""Explicit composed Worker startup boundaries over immutable native18.

The original decorated method is called once. B callback and mapping outputs
remain separate facts, never worker assignment, approval, GPU or READY proof.
"""
from __future__ import annotations
import contextvars,functools,hashlib,json,os,stat,sys,threading,time,types

PROFILE='vllm-native-lifecycle-metadata/19'
SCHEMA='guard-native-lifecycle-events/19'
EVENT_SCHEMA='guard-native-worker-startup/1'
LINK_SCHEMA='guard-native-worker-startup-link/1'
CLOSE_SCHEMA='guard-native-worker-startup-close/1'
LIMIT_SCHEMA='guard-native-worker-startup-limit/1'
COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
BASE8_SHA256='2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd'
ITERATION16_SHA256='6048767399a893605c693c49f89a3f97360ec081b59169b7ac29fb08ac9e7648'
SCOPE17_SHA256='e32c0a53e810735f9eaca1a648397af49ca259d264020fd0f99193c401b2697d'
MAPPING18_SHA256='dd9e75f2752e042b43527ddd9cf13cc06a3c82fd4c5ba73cfe7114df7dcafaf1'
CONTRACT_SHA256='6788df2f4c6f04f46e681eb5eab48631e2ba037754d26166bb02341e4b592950'
CALLBACK_SHA256='a583d32f31d74def618256557ee9e7aee801d5fe1ff55e27b6221d7f2420a965'
SUPPORT_SHA256='659d615f133cdc96ef54163678753a60e6b8ee51aa0e78e0560e0d308f7f50da'
MAX_ROWS=2048;MAX_BYTES=2*1024**2;MAX_CALLS=256
CODE_FILENAME='guard/adapters/vllm_worker_startup_sensor.py'
RUNTIME_CODE_CONTRACTS={'cpython-3.12.15': 'e572bde10b082c4d117f12857045224766996d76c589f3925501dbfc971dc9b4', 'cpython-3.12.14': 'e572bde10b082c4d117f12857045224766996d76c589f3925501dbfc971dc9b4', 'cpython-3.12.3': '1201dd1198e60884e32ac494380f5693f83a15666d7ddd8653870aacc8ad067b'}
TRUTH=('actual_worker_observed','producer_authenticated','approved_model_file_verified','tensor_storage_applied_verified','gpu_completion_verified','all_model_ready_verified','ready_send_verified','service_gate_verified','scope_complete')
_ACTIVE=contextvars.ContextVar('guard_worker_startup_call',default=None)
_BASE=None


def sha(raw):return hashlib.sha256(raw).hexdigest()


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def normalized_code(code):
    return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(normalized_code(x) if type(x) is types.CodeType else x for x in code.co_consts))


class StartupObservation:
    def startup_binding(self,call):
        context=self._writer().context
        return {'pid':os.getpid(),'session_id_sha256':sha(self.config['session'].encode()),
            'process_start_sha256':sha(canonical(context)),'call_id_sha256':call}

    def startup_snapshot(self,point,args,kwargs,phase,binding):
        verified=False
        try:
            if self.startup_contract.callback_code_contract(self.startup_callback_module,self.startup_support_module,self.startup_base)!=self.startup_callback_codes:raise ValueError
            verified=True
            return self.startup_contract.closed_callback(self.startup_callback.snapshot(point,args,kwargs,phase=phase,binding=binding),self.startup_callback_module),None,verified
        except BaseException:return None,'CALLBACK_UNAVAILABLE',verified

    def startup_emit(self,state,phase,args,kwargs,started,exception=None):
        try:
            snapshot,error,callback_verified=self.startup_snapshot(state['point'],args,kwargs,phase,state['binding'])
            self.emit('WORKER_STARTUP',{'schema':EVENT_SCHEMA,'point':state['point'],'phase':phase,
                'source_module':self.startup_contract.WORKER,'source_files':dict(self.startup_contract.SOURCE_SHA256),
                'source_verified':all(self.modules.get(name,{}).get('source_sha256')==digest for name,digest in self.startup_contract.SOURCE_SHA256.items()),
                'body_code_sha256':state['qualified']['body_code_sha256'],'entry_code_sha256':state['qualified']['entry_code_sha256'],
                'callback_source_sha256':CALLBACK_SHA256,'callback_helper_source_sha256':SUPPORT_SHA256,
                'callback_code_sha256':self.startup_callback_codes[self.startup_contract.CALLBACK_MODULE],
                'callback_helper_code_sha256':self.startup_callback_codes[self.startup_contract.SUPPORT_MODULE],
                'callback_code_verified':callback_verified,
                'entry_kind':state['qualified']['entry_kind'],'inner_body_entry_observed':False,
                'observer_runtime':self.startup_runtime,'observer_code_sha256':self.startup_code,
                'call_sha256':state['call'],'parent_call_sha256':state['parent'],'call_started_ns':state['started'],
                'operation_started_ns':started,'operation_finished_ns':time.monotonic_ns(),
                'callback':snapshot,'callback_error':error,'exception_kind':exception,**{key:False for key in TRUTH}})
        except BaseException:self.disabled_pid=os.getpid()

    def startup_wrap(self,point,qualified):
        original=qualified['original'];observer=self
        @functools.wraps(original)
        def wrapped(*args,**kwargs):
            if observer.startup_pid!=os.getpid() or not observer.active():return original(*args,**kwargs)
            with observer.startup_lock:
                admitted=observer.startup_calls<MAX_CALLS
                if admitted:observer.startup_calls+=1
                report=not admitted and not observer.startup_bound_reported
                if report:observer.startup_bound_reported=True
            if not admitted:
                if report:
                    observer.emit('WORKER_STARTUP_LIMIT',{'schema':LIMIT_SCHEMA,'session_sha256':sha(observer.config['session'].encode()),
                        'observer_runtime':observer.startup_runtime,'observer_code_sha256':observer.startup_code,
                        'bound':'calls','limit':MAX_CALLS,**{key:False for key in TRUTH}})
                return original(*args,**kwargs)
            try:
                call=observer.weight_nonce();parent=_ACTIVE.get()
                parent=parent['call'] if type(parent) is dict and parent.get('pid')==os.getpid() and not parent.get('closed') else None
                started=time.monotonic_ns();state={'point':point,'qualified':qualified,'call':call,'parent':parent,'started':started,
                    'binding':observer.startup_binding(call),'pid':os.getpid(),'closed':False}
            except BaseException:return original(*args,**kwargs)
            if not observer.active():
                try:observer.startup_close_callback()
                except BaseException:pass
                return original(*args,**kwargs)
            observer.startup_emit(state,'CALL',args,kwargs,started)
            token=_ACTIVE.set(state);kind=None
            try:return original(*args,**kwargs)
            except BaseException as error:
                kind='exception' if isinstance(error,Exception) else 'base-exception';raise
            finally:
                observer.startup_emit(state,'RAISE' if kind else 'RETURN',args,kwargs,time.monotonic_ns(),exception=kind)
                state['closed']=True;_ACTIVE.reset(token)
        return wrapped

    def startup_close_callback(self):
        if self.startup_pid!=os.getpid() or self.startup_callback_closed:return
        if not self.startup_close_lock.acquire(blocking=False):return
        try:
            if self.startup_callback_closed:return
            self.startup_callback_closed=True
            try:
                if self.startup_contract.callback_code_contract(self.startup_callback_module,self.startup_support_module,self.startup_base)!=self.startup_callback_codes:raise ValueError
                result=self.startup_callback.close()
                if (type(result) is not dict or set(result)!={'complete','pending_calls','collection_gap','gaps'}
                        or type(result['complete']) is not bool or type(result['collection_gap']) is not bool
                        or result['pending_calls'] is not None and (type(result['pending_calls']) is not int or not 0<=result['pending_calls']<=64)
                        or type(result['gaps']) is not list or any(type(x) is not str or x not in self.startup_callback_module.GAPS for x in result['gaps'])):raise ValueError
            except BaseException:result={'complete':False,'pending_calls':None,'collection_gap':True,'gaps':['CALLBACK_CLOSE_UNAVAILABLE']}
            self.emit('WORKER_STARTUP_CLOSE',{'schema':CLOSE_SCHEMA,'session_sha256':sha(self.config['session'].encode()),
                'observer_runtime':self.startup_runtime,'observer_code_sha256':self.startup_code,'result':result,**{key:False for key in TRUTH}})
        finally:self.startup_close_lock.release()

    def emit(self,event,payload):
        # Link only an exact root factory record from this startup call context.
        active=_ACTIVE.get()
        linked=(event=='WEIGHT_ITERATION' and type(payload) is dict and payload.get('point')=='AutoWeightsLoader._load_module'
            and payload.get('phase')=='FACTORY' and type(active) is dict and active.get('pid')==os.getpid() and not active.get('closed'))
        if not linked:return super().emit(event,payload)
        try:
            writer=self._writer()
            with writer.lock:
                seq=writer.seq;super().emit(event,payload)
                if writer.seq!=seq+1:return
                super().emit('WORKER_STARTUP_LINK',{'schema':LINK_SCHEMA,'worker_call_sha256':active['call'],
                    'weight_event_seq':seq,'weight_payload_sha256':sha(canonical(payload)),
                    'weight_call_sha256':payload['call_sha256'],'model_object_sha256':payload['model_object_sha256'],
                    'observer_runtime':self.startup_runtime,'observer_code_sha256':self.startup_code,**{key:False for key in TRUTH}})
        except BaseException:self.disabled_pid=os.getpid()

    def _writer(self):
        writer=super()._writer()
        if self.startup_pid!=os.getpid():return writer
        with writer.lock:
            if writer is not self.startup_writer:
                self.startup_writer=writer
                original=writer.close;observer=self
                def close(reason):
                    try:observer.startup_close_callback()
                    except BaseException:pass
                    return original(reason)
                writer.close=close
        return writer


def code_contract(base,contract):
    functions=(sha,canonical,normalized_code,StartupObservation.startup_binding,StartupObservation.startup_snapshot,
        StartupObservation.startup_emit,StartupObservation.startup_wrap,StartupObservation.startup_close_callback,
        StartupObservation.emit,StartupObservation._writer,configure,_private_base,install)
    dependencies=tuple(vars(contract)[name] for name in ('sha','relative_code','method_digest','artifact','compiled_codes','function_matches','qualify','load_callback','fingerprint','closed_callback','callback_code_contract'))
    return sha(''.join(base.code_digest(normalized_code(fn.__code__)) for fn in (*functions,*dependencies)).encode())


def configure(base,iteration,scope,mapping,contract,callback,support,*,scope_path='/guard-lifecycle/model-read-scope.json'):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if ((sys.implementation.name!='cpython' or sys.flags.optimize!=0 or expected is None or code_contract(base,contract)!=expected
            or (runtime,os.name) not in {('cpython-3.12.15','posix'),('cpython-3.12.14','nt'),('cpython-3.12.3','posix')}) or (runtime == 'cpython-3.12.15' and (sys.platform != 'linux' or os.name != 'posix' or os.uname().machine != 'x86_64' or sys.flags.optimize != 0))):raise ValueError('WORKER_STARTUP_OBSERVER_CODE_UNAVAILABLE')
    for module,digest in ((mapping,MAPPING18_SHA256),(contract,CONTRACT_SHA256),(callback,CALLBACK_SHA256),(support,SUPPORT_SHA256)):
        contract.artifact(module.__file__,digest)
    mapping.configure(base,iteration,scope,scope_path=scope_path)
    callback_codes=contract.callback_code_contract(callback,support,base)
    base.PROFILE,base.SCHEMA=PROFILE,SCHEMA;base.__file__=__file__;base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES
    if any(name in base.SOURCE_SHA and base.SOURCE_SHA[name]!=digest for name,digest in contract.SOURCE_SHA256.items()):
        raise ValueError('WORKER_STARTUP_SOURCE_CONFLICT')
    base.SOURCE_SHA={**base.SOURCE_SHA,**contract.SOURCE_SHA256}
    class StartupObserver(StartupObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.startup_pid=os.getpid();self.startup_runtime=runtime;self.startup_code=expected
            self.startup_contract=contract;self.startup_callback_module=callback;self.startup_callback=callback.WorkerReadinessObserver()
            self.startup_base=base;self.startup_support_module=support;self.startup_callback_codes=dict(callback_codes)
            self.startup_calls=0;self.startup_lock=threading.Lock();self.startup_close_lock=threading.Lock()
            self.startup_bound_reported=False
            self.startup_callback_closed=False;self.startup_writer=None
        def instrument(self,module):
            if module.__name__!=contract.WORKER:return super().instrument(module)
            qualified=contract.qualify(module,base);cls=vars(module)['Worker'];changes=[]
            for point,value in qualified.items():changes.append((point.split('.')[1],value['original'],self.startup_wrap(point,value)))
            super().instrument(module);applied=[]
            try:
                for name,original,wrapped in changes:setattr(cls,name,wrapped);applied.append((name,original))
            except BaseException:
                for name,original in reversed(applied):
                    try:setattr(cls,name,original)
                    except BaseException:pass
                raise
    base.Observer=StartupObserver;return base


def _private_base():
    def module(name,filename,digest):
        path='/guard-lifecycle/'+filename
        # Bootstrap contract is checked independently before it can read assets.
        selected=os.lstat(path)
        if not stat.S_ISREG(selected.st_mode):raise ValueError
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        try:
            before=os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=131072:raise ValueError
            raw=os.read(fd,131073);after=os.fstat(fd);current=os.lstat(path)
            common=lambda value:(value.st_dev,value.st_ino,value.st_mode,value.st_size,value.st_mtime_ns)
            if (sha(raw)!=digest or len(raw)!=before.st_size
                    or len({common(v) for v in (selected,before,after,current)})!=1
                    or selected.st_ctime_ns!=current.st_ctime_ns or before.st_ctime_ns!=after.st_ctime_ns
                    or not stat.S_ISREG(current.st_mode)):raise ValueError
        finally:os.close(fd)
        result=types.ModuleType(name);result.__file__=path
        exec(compile(raw,path,'exec',dont_inherit=True,optimize=0),vars(result));return result
    contract=module('_guard_startup_contract','guard_worker_startup_contract.py',CONTRACT_SHA256)
    base=module('_guard_startup_base','guard_lifecycle_base.py',BASE8_SHA256)
    iteration=module('_guard_startup_iteration','guard_weight_iteration.py',ITERATION16_SHA256)
    scope=module('_guard_startup_scope','guard_file_read_scope.py',SCOPE17_SHA256)
    mapping=module('_guard_startup_mapping','guard_safe_open_mapping.py',MAPPING18_SHA256)
    callback,support=contract.load_callback('/guard-lifecycle/guard_worker_readiness_callback.py','/guard-lifecycle/guard_worker_readiness_support.py')
    return configure(base,iteration,scope,mapping,contract,callback,support)


def install(path=None):
    global _BASE
    if _BASE is not None:return False
    try:
        base=_private_base()
        if not base.install(path):return False
        _BASE=base;return True
    except Exception:return False


class Middleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if _BASE is None:return await self.app(scope,receive,send)
        return await _BASE.Middleware(self.app)(scope,receive,send)

