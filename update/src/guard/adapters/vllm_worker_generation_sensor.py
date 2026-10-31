"""Native21 local object generation and selected original READY pipe facts.

Original Worker19/MP14/native20 bytes and call behavior are retained. Local
object and send-return observations do not authenticate a producer or READY.
"""
from __future__ import annotations
import contextvars,functools,hashlib,json,os,stat,sys,threading,time,types,weakref

PROFILE='vllm-native-lifecycle-metadata/21';SCHEMA='guard-native-lifecycle-events/21'
COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
EVENT_SCHEMA='guard-native-worker-generation/1';LINK_SCHEMA='guard-native-worker-generation-link/1'
PIPE_SCHEMA='guard-native-worker-ready-send/1';GAP_SCHEMA='guard-native-worker-generation-gap/1'
CLOSE_SCHEMA='guard-native-worker-generation-close/1'
JOINT20_SHA256='b7447db52f40b61b20361703ce786d591ada767602e6ec5ea38404e90e559cf1'
CONTRACT_SHA256='f0c37215577292ce7b89b535cf147553ac22f5ae6ef6a08708cf055f4d4eb27a'
MAX_BYTES=2097152;MAX_ROWS=2048;MAX_CALLS=256
CODE_FILENAME='guard/adapters/vllm_worker_generation_sensor.py'
RUNTIME_CODE_CONTRACTS={'cpython-3.12.15': '0763e2269203f831cb3b382f04687271155d8b60f6f824fbb2ce415c2718dbdf', 'cpython-3.12.14': '0763e2269203f831cb3b382f04687271155d8b60f6f824fbb2ce415c2718dbdf', 'cpython-3.12.3': '578be44bc128f8304768d9e5536b6ce6d12fa6a7a1bf936fa6df1d019209ddc6'}
TRUTH=('producer_authenticated','actual_worker_verified','cross_process_lineage_verified','tensor_storage_applied_verified','gpu_completion_verified','all_model_ready_verified','ready_delivery_verified','service_gate_verified','scope_complete')
GAPS=frozenset({'calls-bound','callable-changed','selected-pipe-unavailable','pipe-code-unavailable','other-thread','worker-object-unbound','worker-object-unqualified','worker-object-bound','constructor-worker-ambiguous','wrapper-outside-constructor'})
_CONTEXT=contextvars.ContextVar('guard_worker_generation_call',default=None)
_WORKER=contextvars.ContextVar('guard_worker_generation_worker',default=None)
_BASE=None


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def attrs(value):
    try:data=object.__getattribute__(value,'__dict__')
    except (AttributeError,TypeError):data={}
    return data if type(data) is dict else {}
def rank(value):return value if type(value) is int and 0<=value<=63 else None
def normalized_code(code):return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(normalized_code(v) if type(v) is types.CodeType else v for v in code.co_consts))


class SelectedPipe:
    __slots__=('observer','target','state','token','__weakref__')
    def __init__(self,observer,target,state):
        self.observer=observer;self.target=target;self.state=state;self.token=observer.weight_nonce()
    def forward(self,operation,args):
        observer=self.observer;name='send' if operation=='SEND' else 'close'
        original=getattr(self.target,name);qualified=False;status=None;call=None
        try:
            active=observer.generation_active() and _CONTEXT.get() is self.state
            methods=observer.generation_contract.pipe_methods(self.target,observer.generation_base,observer.generation_runtime) if active else None
            qualified=bool(methods and type(original) is types.MethodType and methods[name].__func__ is original.__func__)
            if qualified:call=observer.weight_nonce()
            if active and not qualified:observer.generation_gap('pipe-code-unavailable','WorkerProc.worker_main')
            if operation=='SEND' and args and type(args[0]) is dict and set(args[0])=={'status','handle','peer_response_handles'}:
                value=args[0]['status'];status='READY' if type(value) is str and value=='READY' else 'other'
        except BaseException:qualified=False
        started=time.monotonic_ns()
        if qualified:
            try:observer.generation_pipe(self,call,operation,'CALL',status,started,None)
            except BaseException:pass
        kind=None
        try:return original(*args)
        except BaseException as error:
            kind='exception' if isinstance(error,Exception) else 'base-exception';raise
        finally:
            if qualified:
                try:observer.generation_pipe(self,call,operation,'RAISE' if kind else 'RETURN',status,started,kind)
                except BaseException:pass
    def send(self,value):return self.forward('SEND',(value,))
    def close(self):return self.forward('CLOSE',())


class GenerationObservation:
    def generation_active(self):
        return bool(self.generation_pid==os.getpid() and not self.generation_closed and not self.generation_failed and self.active())

    def generation_binding(self):
        context=self._writer().context
        return {'session_sha256':sha(self.config['session'].encode()),'process_sha256':sha(canonical(context))}

    def generation_write(self,event,payload):
        try:
            writer=self._writer()
            with writer.lock:
                seq=writer.seq;super().emit(event,payload)
                return seq if writer.seq==seq+1 else None
        except BaseException:
            self.generation_failed=True;return None

    def generation_gap(self,reason,point):
        if reason not in GAPS or not self.generation_active():return
        self.generation_write('WORKER_GENERATION_GAP',{'schema':GAP_SCHEMA,'reason':reason,'point':point,
            **self.generation_binding(),'observer_runtime':self.generation_runtime,'observer_code_sha256':self.generation_code,**{k:False for k in TRUTH}})

    def generation_emit(self,state,phase,started,kind=None):
        if not self.generation_active():return None
        contract=self.generation_contract;value=state['qualified']
        payload={'schema':EVENT_SCHEMA,'point':state['point'],'phase':phase,'call_sha256':state['call'],
            'parent_call_sha256':state['parent'],'main_call_sha256':state['main'],'constructor_call_sha256':state['constructor'],
            'owner_token':state['owner'],'worker_token':state.get('worker'),'rank':state['rank'],'local_rank':state['local_rank'],
            'source_module':value['source_module'],'source_sha256':value['source_sha256'],
            'body_code_sha256':value['body_code_sha256'],'entry_code_sha256':value['entry_code_sha256'],'entry_kind':value['entry_kind'],
            'source_verified':all(self.modules.get(n,{}).get('source_sha256')==d for n,d in contract.SOURCE_SHA256.items()),
            'observer_runtime':self.generation_runtime,'observer_code_sha256':self.generation_code,**self.generation_binding(),
            'operation_started_ns':started,'operation_finished_ns':time.monotonic_ns(),'exception_kind':kind,**{k:False for k in TRUTH}}
        return self.generation_write('WORKER_GENERATION',payload)

    def generation_bind_worker(self,args,state,parent):
        if not parent or parent['point']!='WorkerProc.__init__':
            self.generation_gap('wrapper-outside-constructor',state['point']);return None
        owner=args[0] if args else None;module=sys.modules.get(self.generation_contract.WRAPPER)
        if (type(module) is not types.ModuleType or type(owner) is not self.generation_source_classes.get('WorkerWrapperBase')
                or vars(module).get('WorkerWrapperBase') is not type(owner)):
            self.generation_gap('worker-object-unqualified',state['point']);return None
        worker=attrs(owner).get('worker')
        if type(worker) is not self.generation_worker_class or not self.generation_contract.exact_worker(worker,sys.modules):
            self.generation_gap('worker-object-unqualified',state['point']);return None
        with self.generation_lock:
            if self.generation_closed or len(self.generation_workers)>=MAX_CALLS:
                self.generation_gap('worker-object-bound',state['point']);return None
            token=self.weight_nonce();state['worker']=token;parent['worker']=token;parent['workers'].append(token)
            if len(parent['workers'])!=1:self.generation_gap('constructor-worker-ambiguous',state['point'])
            binding={'constructor_call_sha256':parent['call'],'constructor_event_seq':parent.get('event_seq'),
                'wrapper_call_sha256':state['call'],'wrapper_event_seq':None,'worker_token':token,
                'owner_token':parent['owner'],'main_call_sha256':parent['main'],'rank':parent['rank'],'local_rank':parent['local_rank'],
                'valid':True,'pid':os.getpid(),'thread':threading.get_ident()}
            self.generation_workers[worker]=binding;return binding

    def generation_prepare(self,point,qualified,args,kwargs):
        if not self.generation_active():return None
        contract=self.generation_contract;original=qualified['original']
        if point=='WorkerProc.__init__':
            source=sys.modules.get(contract.MP)
            if (type(source) is not types.ModuleType or not args or type(args[0]) is not self.generation_source_classes.get('WorkerProc')
                    or vars(source).get('WorkerProc') is not type(args[0])
                    or vars(source).get('WorkerWrapperBase') is not self.generation_source_classes.get('WorkerWrapperBase')):
                self.generation_gap('worker-object-unqualified',point);return None
        actual=self.startup_contract.method_digest(self.generation_base,original.__code__,contract.OTEL) if qualified['entry_kind']=='otel-sync-wrapper' else contract.method_digest(self.generation_base,original.__code__,qualified['source_module'])
        if actual!=qualified['entry_code_sha256']:
            self.generation_gap('callable-changed',point);return None
        with self.generation_lock:
            admitted=self.generation_calls<MAX_CALLS
            if admitted:self.generation_calls+=1
            bounded=not admitted and not self.generation_bound
            if bounded:self.generation_bound=True
        if not admitted:
            if bounded:self.generation_gap('calls-bound',point)
            return None
        parent=_CONTEXT.get();parent=parent if type(parent) is dict and parent.get('pid')==os.getpid() and not parent.get('closed') else None
        call=self.weight_nonce();is_constructor=point=='WorkerProc.__init__'
        state={'point':point,'call':call,'qualified':qualified,'parent':parent['call'] if parent else None,
            'main':call if point=='WorkerProc.worker_main' else parent['main'] if parent else None,
            'constructor':call if is_constructor else parent['constructor'] if parent else None,
            'owner':self.weight_nonce() if is_constructor else parent['owner'] if parent else None,
            'rank':rank(args[3] if is_constructor and len(args)>3 else kwargs.get('rank')) if is_constructor or point=='WorkerProc.worker_main' else parent['rank'] if parent else None,
            'local_rank':rank(args[2] if is_constructor and len(args)>2 else kwargs.get('local_rank')) if is_constructor or point=='WorkerProc.worker_main' else parent['local_rank'] if parent else None,
            'worker':None,'workers':[],'pid':os.getpid(),'closed':False}
        selected=kwargs
        if point=='WorkerProc.worker_main':
            pipe=kwargs.get('ready_pipe')
            if contract.pipe_methods(pipe,self.generation_base,self.generation_runtime):
                selected=dict(kwargs);selected['ready_pipe']=SelectedPipe(self,pipe,state)
            else:self.generation_gap('selected-pipe-unavailable',point)
        started=time.monotonic_ns();state['event_seq']=self.generation_emit(state,'CALL',started)
        return state,selected,started,_CONTEXT.set(state),parent

    def generation_wrap(self,point,qualified):
        observer=self;original=qualified['original']
        @functools.wraps(original)
        def wrapped(*args,**kwargs):
            try:prepared=observer.generation_prepare(point,qualified,args,kwargs)
            except BaseException:prepared=None
            if prepared is None:return original(*args,**kwargs)
            state,selected,started,token,parent=prepared;kind=None;binding=None
            try:
                result=original(*args,**selected)
                if point=='WorkerWrapperBase.init_worker':
                    try:binding=observer.generation_bind_worker(args,state,parent)
                    except BaseException:observer.generation_failed=True
                return result
            except BaseException as error:
                kind='exception' if isinstance(error,Exception) else 'base-exception';raise
            finally:
                try:
                    end=observer.generation_emit(state,'RAISE' if kind else 'RETURN',started,kind)
                    if binding is not None:binding['wrapper_event_seq']=end
                    if point=='WorkerProc.__init__' and kind:
                        with observer.generation_lock:
                            for value in observer.generation_workers.values():
                                if value['constructor_call_sha256']==state['call']:value['valid']=False
                    if point=='WorkerProc.worker_main':
                        with observer.generation_lock:
                            for value in observer.generation_workers.values():
                                if value['main_call_sha256']==state['call']:value['valid']=False
                except BaseException:observer.generation_failed=True
                finally:
                    state['closed']=True
                    try:_CONTEXT.reset(token)
                    except BaseException:pass
        return wrapped

    def startup_wrap(self,point,qualified):
        original=super().startup_wrap(point,qualified);observer=self
        @functools.wraps(original)
        def wrapped(*args,**kwargs):
            binding=None;owner=args[0] if args else None;token=None
            try:
                if observer.generation_active() and type(owner) is observer.generation_worker_class and observer.generation_contract.exact_worker(owner,sys.modules):
                    with observer.generation_lock:binding=observer.generation_workers.get(owner)
                    if binding is not None and (not binding['valid'] or binding['pid']!=os.getpid() or binding['thread']!=threading.get_ident()):binding=None
                token=_WORKER.set(binding)
            except BaseException:pass
            try:return original(*args,**kwargs)
            finally:
                if token is not None:
                    try:_WORKER.reset(token)
                    except BaseException:pass
        return wrapped

    def emit(self,event,payload):
        if event!='WORKER_STARTUP' or not self.generation_active():return super().emit(event,payload)
        binding=_WORKER.get();writer=self._writer()
        with writer.lock:
            seq=writer.seq;super().emit(event,payload)
            if writer.seq!=seq+1:return
            if type(binding) is not dict or binding['wrapper_event_seq'] is None:
                self.generation_gap('worker-object-unbound',payload['point']);return
            data={k:v for k,v in binding.items() if k not in {'pid','valid','thread'}}
            self.generation_write('WORKER_GENERATION_LINK',{'schema':LINK_SCHEMA,**data,'worker_event_seq':seq,
                'worker_payload_sha256':sha(canonical(payload)),'worker_call_sha256':payload['call_sha256'],'phase':payload['phase'],
                **self.generation_binding(),'observer_runtime':self.generation_runtime,'observer_code_sha256':self.generation_code,**{k:False for k in TRUTH}})

    def generation_pipe(self,proxy,call,operation,phase,status,started,kind):
        if not self.generation_active():return
        state=proxy.state
        self.generation_write('WORKER_READY_SEND',{'schema':PIPE_SCHEMA,'main_call_sha256':state['call'],'pipe_token':proxy.token,'pipe_call_sha256':call,
            'operation':operation,'phase':phase,'status':status,'delegate_code_sha256':self.generation_contract.CONNECTION_CODES[self.generation_runtime]['send' if operation=='SEND' else 'close'],
            'delegate_code_verified':True,**self.generation_binding(),'observer_runtime':self.generation_runtime,'observer_code_sha256':self.generation_code,
            'operation_started_ns':started,'operation_finished_ns':time.monotonic_ns(),'exception_kind':kind,**{k:False for k in TRUTH}})

    def generation_close(self):
        with self.generation_lock:
            if self.generation_closed or self.generation_pid!=os.getpid():return
            count=len(self.generation_workers);self.generation_closed=True;self.generation_workers.clear()
            self.generation_write('WORKER_GENERATION_CLOSE',{'schema':CLOSE_SCHEMA,'cleared_local_worker_bindings':count,
                'local_observer_closed':True,**self.generation_binding(),'observer_runtime':self.generation_runtime,
                'observer_code_sha256':self.generation_code,**{k:False for k in TRUTH}})

    def _writer(self):
        writer=super()._writer()
        if not hasattr(self,'generation_lock') or self.generation_pid!=os.getpid():return writer
        with writer.lock:
            if writer is not self.generation_writer:
                self.generation_writer=writer;original=writer.close;observer=self
                def close(reason):
                    try:observer.generation_close()
                    except BaseException:pass
                    return original(reason)
                writer.close=close
        return writer


def code_contract(base,contract):
    functions=(sha,canonical,attrs,rank,normalized_code,GenerationObservation.generation_active,GenerationObservation.generation_binding,
        GenerationObservation.generation_write,GenerationObservation.generation_gap,GenerationObservation.generation_emit,
        GenerationObservation.generation_bind_worker,GenerationObservation.generation_prepare,GenerationObservation.generation_wrap,GenerationObservation.startup_wrap,
        GenerationObservation.emit,GenerationObservation.generation_pipe,GenerationObservation.generation_close,GenerationObservation._writer,
        SelectedPipe.__init__,SelectedPipe.forward,SelectedPipe.send,SelectedPipe.close,configure,artifact,module,_private_base,install)
    dependencies=tuple(vars(contract)[name] for name in ('sha','relative_code','method_digest','compiled_codes','matches','qualify','pipe_methods','exact_worker'))
    return sha(''.join(base.code_digest(normalized_code(f.__code__)) for f in (*functions,*dependencies)).encode())


def configure(base,joint,contract):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if ((sys.implementation.name!='cpython' or sys.flags.optimize!=0 or expected is None or code_contract(base,contract)!=expected
            or (runtime,os.name) not in {('cpython-3.12.15','posix'),('cpython-3.12.14','nt'),('cpython-3.12.3','posix')}
            or base.PROFILE!='vllm-native-lifecycle-metadata/20' or base.SCHEMA!='guard-native-lifecycle-events/20') or (runtime == 'cpython-3.12.15' and (sys.platform != 'linux' or os.name != 'posix' or os.uname().machine != 'x86_64' or sys.flags.optimize != 0))):
        raise ValueError('WORKER_GENERATION_CODE_UNAVAILABLE')
    artifact(joint.__file__,JOINT20_SHA256);artifact(contract.__file__,CONTRACT_SHA256)
    if any(n in base.SOURCE_SHA and base.SOURCE_SHA[n]!=d for n,d in contract.SOURCE_SHA256.items()):raise ValueError('WORKER_GENERATION_SOURCE_CONFLICT')
    base.SOURCE_SHA={**base.SOURCE_SHA,**contract.SOURCE_SHA256};base.PROFILE,base.SCHEMA=PROFILE,SCHEMA
    base.__file__=__file__;base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES
    class GenerationObserver(GenerationObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.generation_pid=os.getpid();self.generation_lock=threading.RLock()
            self.generation_closed=False;self.generation_failed=False;self.generation_writer=None;self.generation_calls=0;self.generation_bound=False
            self.generation_workers=weakref.WeakKeyDictionary();self.generation_contract=contract;self.generation_base=base
            self.generation_source_classes={};self.generation_worker_class=None
            self.generation_runtime=runtime;self.generation_code=expected
        def instrument(self,source):
            if source.__name__ not in contract.TARGETS:
                result=super().instrument(source)
                if source.__name__==contract.WORKER:self.generation_worker_class=vars(source).get('Worker')
                return result
            qualified=contract.qualify(source,base,self.startup_contract);changes=[]
            for point,info in qualified.items():
                cls_name,name=point.split('.');changes.append((vars(source)[cls_name],name,vars(vars(source)[cls_name])[name],self.generation_wrap(point,info)))
            super().instrument(source);applied=[]
            try:
                for cls,name,original,wrapped in changes:
                    setattr(cls,name,staticmethod(wrapped) if name=='worker_main' else wrapped);applied.append((cls,name,original))
                for cls,name,original,wrapped in changes:self.generation_source_classes[cls.__name__]=cls
            except BaseException:
                for cls,name,original in reversed(applied):setattr(cls,name,original)
                raise
    base.Observer=GenerationObserver;return base


def artifact(path,expected):
    chosen=os.lstat(path)
    if not stat.S_ISREG(chosen.st_mode):raise ValueError('WORKER_GENERATION_ASSET_UNAVAILABLE')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=131072:raise ValueError('WORKER_GENERATION_ASSET_UNAVAILABLE')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(131073)
        after,current=os.fstat(fd),os.lstat(path);identity=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_size,v.st_mtime_ns)
        if (len({identity(v) for v in (chosen,before,after,current)})!=1 or chosen.st_ctime_ns!=current.st_ctime_ns
                or before.st_ctime_ns!=after.st_ctime_ns or len(raw)!=before.st_size or sha(raw)!=expected):raise ValueError('WORKER_GENERATION_ASSET_UNAVAILABLE')
        return raw
    finally:os.close(fd)


def module(name,filename,expected):
    path='/guard-lifecycle/'+filename;raw=artifact(path,expected);value=types.ModuleType(name);value.__file__=path
    exec(compile(raw,path,'exec',dont_inherit=True,optimize=0),vars(value));return value


def _private_base():
    joint=module('_guard_generation_joint20','guard_worker_initialization.py',JOINT20_SHA256)
    contract=module('_guard_generation_contract','guard_worker_generation_contract.py',CONTRACT_SHA256)
    return configure(joint._private_base(),joint,contract)


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
