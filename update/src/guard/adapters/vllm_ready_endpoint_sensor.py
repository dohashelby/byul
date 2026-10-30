"""Explicit native22 Linux selected READY endpoint observation.

Creator, receiver and sender observations reference immutable native14/21
events. No pipe messages, factories, process launch or service policy is added.
"""
from __future__ import annotations
import contextvars, functools, hashlib, json, os, stat, sys, threading, time, types, weakref

PROFILE='vllm-native-lifecycle-metadata/22';SCHEMA='guard-native-lifecycle-events/22'
COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
EVENT_SCHEMA='guard-native-ready-endpoint/1';GAP_SCHEMA='guard-native-ready-endpoint-gap/1'
CLOSE_SCHEMA='guard-native-ready-endpoint-close/1'
GENERATION21_SHA256='5ebdbd58401bcd47ee5fd4159e82b78f13ae47773eaefd4c02ba6f2ac182a8d1'
CONTRACT_SHA256='e99df63bf7ed6c72f72421a8017dad9a87d39a6c5d4254c167ac0c64df68c745'
MAX_BYTES=2097152;MAX_ROWS=2048;MAX_CALLS=256
CODE_FILENAME='guard/adapters/vllm_ready_endpoint_sensor.py'
RUNTIME_CODE_CONTRACTS={'cpython-3.12.14': '41e3588a4580773827a05056d0136c1e8817dfd4fd33c80b63e009d2796fbfac', 'cpython-3.12.3': '21b6c5e7e4c7716aa06ae7a919b2bdc60b18d92b47104eddbf07329f2792602f'}
TRUTH=('producer_authenticated','actual_worker_verified','cross_process_lineage_verified','tensor_storage_applied_verified',
       'gpu_completion_verified','all_model_ready_verified','ready_delivery_verified','service_gate_verified','scope_complete')
GAPS=frozenset({'unsupported-platform','endpoint-delegate-unqualified','endpoint-domain-unavailable','endpoint-unavailable',
 'endpoint-kind-unqualified','creator-unqualified','reader-unbound','reader-reused','reader-ambiguous','reader-callable-changed',
 'reader-restore-unverified','other-thread','source-callable-changed','calls-bound','send-link-unavailable',
 'initialization-link-unavailable','fork-inheritance-unqualified'})
_INIT=contextvars.ContextVar('guard_ready_endpoint_init',default=None)
_WAIT=contextvars.ContextVar('guard_ready_endpoint_wait',default=None)
_SEND=contextvars.ContextVar('guard_ready_endpoint_send',default=None)
_BASE=None


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def normalize(code):return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(normalize(c) if type(c) is types.CodeType else c for c in code.co_consts))
def current(value):return type(value) is dict and value.get('pid')==os.getpid() and value.get('thread')==threading.get_ident() and not value.get('closed')


class EndpointObservation:
    def endpoint_active(self):
        return bool(self.endpoint_pid==os.getpid() and not self.endpoint_closed and not self.endpoint_failed and self.generation_active())

    def endpoint_binding(self):
        return {'session_sha256':sha(self.config['session'].encode()),'process_sha256':sha(canonical(self._writer().context)),
                'observer_runtime':self.endpoint_runtime,'observer_code_sha256':self.endpoint_code,**{k:False for k in TRUTH}}

    def endpoint_write(self,event,payload):
        if not self.endpoint_active():return None
        return super().generation_write(event,payload)

    def endpoint_gap(self,reason,point):
        if reason not in GAPS:return
        self.endpoint_write('READY_ENDPOINT_GAP',{'schema':GAP_SCHEMA,'reason':reason,'point':point,**self.endpoint_binding()})

    def endpoint_snapshot(self,reader,point):
        token,reason=self.endpoint_contract.endpoint(reader,self._writer().context,self.config['session'],self.generation_base,self.endpoint_runtime)
        if reason:self.endpoint_gap(reason,point)
        return token

    def endpoint_event(self,state,phase,*,before=None,after=None,status=None,kind=None,reference=None):
        point=state['point'];started=state['started'];reference=reference or {}
        payload={'schema':EVENT_SCHEMA,'point':point,'operation':state['operation'],'phase':phase,
            'call_sha256':state['call'],'reader_token':state.get('reader_token'),
            'parent_init_seq':state.get('parent_seq'),'parent_init_payload_sha256':state.get('parent_sha'),
            'wait_seq':state.get('wait_seq'),'wait_payload_sha256':state.get('wait_sha'),
            'generation_seq':reference.get('seq'),'generation_payload_sha256':reference.get('sha'),
            'main_call_sha256':state.get('main'),'child_pid_claim':state.get('child_pid_claim'),
            'rank':state.get('rank'),'start_method':state.get('start_method'),
            'endpoint_before_sha256':before,'endpoint_after_sha256':after,'status':status,
            'source_sha256':self.endpoint_contract.SOURCE_SHA256[self.endpoint_contract.MP],
            'source_code_sha256':state['source_code'],'delegate_code_sha256':state.get('delegate_code'),
            'operation_started_ns':started,'operation_finished_ns':time.monotonic_ns(),'exception_kind':kind,
            **self.endpoint_binding()}
        return self.endpoint_write('READY_ENDPOINT',payload)

    def endpoint_allow_call(self,point):
        bounded=False
        with self.endpoint_lock:
            if self.endpoint_calls>=MAX_CALLS:
                if not self.endpoint_bound:
                    self.endpoint_bound=True;bounded=True
                admitted=False
            else:self.endpoint_calls+=1;admitted=True
        if bounded:self.endpoint_gap('calls-bound',point)
        return admitted

    def emit(self,event,payload):
        if event!='INITIALIZATION' or not hasattr(self,'endpoint_lock') or not self.endpoint_active():
            return super().emit(event,payload)
        writer=self._writer()
        with writer.lock:
            seq=writer.seq;result=super().emit(event,payload)
            if writer.seq!=seq+1:return result
            state=_INIT.get() if payload.get('point')=='MultiprocExecutor._init_executor' else _WAIT.get() if payload.get('point')=='WorkerProc.wait_for_ready' else None
            if current(state) and payload.get('phase')=='CALL':
                state.update(seq=seq,sha=sha(canonical(payload)),call=payload['call_sha256'])
        if current(state) and payload.get('phase')=='CALL' and payload['point']=='WorkerProc.wait_for_ready':
            try:self.endpoint_select_readers(state)
            except BaseException:self.endpoint_gap('reader-unbound','WorkerProc.wait_for_ready')
        return result

    def generation_write(self,event,payload):
        seq=super().generation_write(event,payload)
        state=_SEND.get()
        if event=='WORKER_READY_SEND' and seq is not None and current(state):
            state['reference']={'seq':seq,'sha':sha(canonical(payload))}
        return seq

    def initialization_wrap(self,point,original,expected):
        previous=super().initialization_wrap(point,original,expected);observer=self
        if point not in {'MultiprocExecutor._init_executor','WorkerProc.wait_for_ready'}:return previous
        @functools.wraps(previous)
        def wrapped(*args,**kwargs):
            state={'point':point,'pid':os.getpid(),'thread':threading.get_ident(),'closed':False,'selected':[]}
            if point=='WorkerProc.wait_for_ready':state['handles']=args[0] if args else kwargs.get('unready_proc_handles')
            variable=_INIT if point=='MultiprocExecutor._init_executor' else _WAIT
            try:token=variable.set(state)
            except BaseException:return previous(*args,**kwargs)
            try:return previous(*args,**kwargs)
            finally:
                try:observer.endpoint_restore(state)
                except BaseException:pass
                state['closed']=True
                try:variable.reset(token)
                except BaseException:
                    try:observer.endpoint_gap('initialization-link-unavailable','WorkerProc.wait_for_ready')
                    except BaseException:pass
        return wrapped

    def endpoint_wrap_make(self,qualified):
        original=qualified['points']['WorkerProc.make_worker_process']['original'];observer=self
        @functools.wraps(original)
        def wrapped(*args,**kwargs):
            state=None
            try:
                init=_INIT.get();module=sys.modules.get(observer.endpoint_contract.MP)
                if observer.endpoint_active() and current(init) and init.get('seq') is not None:
                    if sys.platform!='linux':observer.endpoint_gap('unsupported-platform','WorkerProc.make_worker_process')
                    if (vars(module).get('WorkerProc') is not qualified['worker_class']
                            or vars(module).get('UnreadyWorkerProcHandle') is not qualified['handle_class']
                            or vars(module).get('get_mp_context') is not qualified['get_mp_context']
                            or vars(qualified['worker_class']).get('worker_main') is not qualified['installed_main']
                            or observer.endpoint_contract.code_digest(observer.generation_base,original.__code__,observer.endpoint_contract.MP)!=qualified['points']['WorkerProc.make_worker_process']['code_sha256']
                            or any(not observer.endpoint_contract.matches(vars(qualified['utils']).get(name),qualified['utils'],observer.endpoint_contract.POINT_CODES[observer.endpoint_runtime][name],observer.generation_base) for name in observer.endpoint_contract.HELPERS)):
                        observer.endpoint_gap('source-callable-changed','WorkerProc.make_worker_process')
                    elif observer.endpoint_allow_call('WorkerProc.make_worker_process'):
                        state={'point':'WorkerProc.make_worker_process','operation':'CREATE','call':observer.weight_nonce(),
                            'parent_seq':init['seq'],'parent_sha':init['sha'],'started':time.monotonic_ns(),
                            'source_code':qualified['points']['WorkerProc.make_worker_process']['code_sha256']}
                        observer.endpoint_event(state,'CALL')
            except BaseException:state=None
            result=None;kind=None
            try:result=original(*args,**kwargs);return result
            except BaseException as error:
                kind='exception' if isinstance(error,Exception) else 'base-exception';raise
            finally:
                if state is not None:
                    try:
                        values=observer.endpoint_contract.handle_values(result,qualified) if kind is None else None
                        if values:
                            reader=values.pop('reader');endpoint=observer.endpoint_snapshot(reader,state['point'])
                            state.update(values,reader_token=observer.weight_nonce(),endpoint=endpoint,
                                         parent_call=init['call'],pid=os.getpid(),thread=threading.get_ident())
                            with observer.endpoint_lock:
                                reused=reader in observer.endpoint_readers
                                if reused:endpoint=None
                                else:observer.endpoint_readers[reader]=dict(state)
                            if reused:observer.endpoint_gap('reader-reused',state['point'])
                            if values['start_method']=='fork':observer.endpoint_gap('fork-inheritance-unqualified',state['point'])
                            observer.endpoint_event(state,'RETURN',after=endpoint)
                        else:
                            if kind is None:observer.endpoint_gap('creator-unqualified',state['point'])
                            observer.endpoint_event(state,'RAISE' if kind else 'RETURN',kind=kind)
                    except BaseException:pass
        return wrapped

    def endpoint_select_readers(self,state):
        point='WorkerProc.wait_for_ready';handles=state.get('handles');init=_INIT.get()
        if not current(init) or init.get('seq') is None:
            self.endpoint_gap('initialization-link-unavailable',point);return
        if type(handles) is not list or not 1<=len(handles)<=64:
            self.endpoint_gap('reader-unbound',point);return
        if vars(self.endpoint_qualified['worker_class']).get('wait_for_ready') is not self.endpoint_qualified['installed_wait']:
            self.endpoint_gap('source-callable-changed',point);return
        seen=set()
        for handle in handles:
            values=self.endpoint_contract.handle_values(handle,self.endpoint_qualified)
            if not values:self.endpoint_gap('reader-unbound',point);continue
            reader=values['reader']
            if id(reader) in seen:self.endpoint_gap('reader-ambiguous',point);continue
            seen.add(id(reader))  # memory-only identity; never emitted or hashed
            bound=self.endpoint_readers.get(reader)
            if (bound is None or bound['parent_call']!=init['call'] or bound['pid']!=os.getpid()
                    or bound['thread']!=threading.get_ident() or bound['child_pid_claim']!=values['child_pid_claim']
                    or bound['rank']!=values['rank']):
                self.endpoint_gap('reader-unbound',point);continue
            original=self.endpoint_contract.delegate(reader,'recv',self.generation_base,self.endpoint_runtime)
            if original is None:
                self.endpoint_gap('reader-callable-changed',point);continue
            if self.endpoint_snapshot(reader,point)!=bound['endpoint'] or bound['endpoint'] is None:continue
            previous=vars(reader).get('recv');had='recv' in vars(reader)
            selected={'reader':reader,'had':had,'previous':previous,'wait':state,'bound':bound,'restored':False}
            wrapped=self.endpoint_recv(selected,original);selected['wrapped']=wrapped
            reader.recv=wrapped;state['selected'].append(selected)
            with self.endpoint_lock:self.endpoint_selected.append(selected)

    def endpoint_recv(self,selected,original):
        observer=self;point='WorkerProc.wait_for_ready'
        @functools.wraps(original)
        def wrapped(*args,**kwargs):
            state=None
            try:
                wait=selected['wait'];bound=selected['bound']
                if observer.endpoint_active():
                    if _WAIT.get() is not wait or not current(wait):observer.endpoint_gap('other-thread',point)
                    elif not observer.endpoint_contract.qualified_delegate(selected['reader'],original,'recv',observer.generation_base,observer.endpoint_runtime):
                        observer.endpoint_gap('reader-callable-changed',point)
                    elif observer.endpoint_allow_call(point):
                        state={**bound,'point':point,'operation':'RECV','call':observer.weight_nonce(),
                            'wait_seq':wait['seq'],'wait_sha':wait['sha'],'started':time.monotonic_ns(),
                            'source_code':observer.endpoint_qualified['points'][point]['code_sha256'],
                            'delegate_code':observer.endpoint_contract.DELEGATE_CODES[observer.endpoint_runtime]['recv']}
                        state['before']=observer.endpoint_snapshot(selected['reader'],point)
                        observer.endpoint_event(state,'CALL',before=state['before'])
            except BaseException:state=None
            value=None;kind=None
            try:value=original(*args,**kwargs);return value
            except BaseException as error:
                kind='timeout' if type(error) is TimeoutError else 'exception' if isinstance(error,Exception) else 'base-exception';raise
            finally:
                if state is not None:
                    try:
                        status=None
                        if kind is None and type(value) is dict and set(value)=={'status','handle','peer_response_handles'}:
                            status='READY' if type(value['status']) is str and value['status']=='READY' else 'other'
                        after=observer.endpoint_snapshot(selected['reader'],point)
                        observer.endpoint_event(state,'RAISE' if kind else 'RETURN',before=state['before'],after=after,status=status,kind=kind)
                    except BaseException:pass
        return wrapped

    def endpoint_restore(self,state):
        for selected in state.get('selected',[]):
            if selected['restored']:continue
            reader=selected['reader']
            try:
                if vars(reader).get('recv') is selected['wrapped']:
                    if selected['had']:reader.recv=selected['previous']
                    else:del vars(reader)['recv']
                    selected['restored']=True
                else:self.endpoint_gap('reader-restore-unverified','WorkerProc.wait_for_ready')
            finally:
                with self.endpoint_lock:
                    self.endpoint_readers.pop(reader,None)
                    if selected in self.endpoint_selected:self.endpoint_selected.remove(selected)
        state['selected'].clear()

    def generation_pipe(self,proxy,call,operation,phase,status,started,kind):
        if operation!='SEND':return super().generation_pipe(proxy,call,operation,phase,status,started,kind)
        state={'pid':os.getpid(),'thread':threading.get_ident(),'closed':False}
        token=_SEND.set(state)
        try:
            before=None
            if self.endpoint_active():
                try:before=self.endpoint_snapshot(proxy.target,'WorkerProc.worker_main')
                except BaseException:pass
            result=super().generation_pipe(proxy,call,operation,phase,status,started,kind)
            if self.endpoint_active():
                try:
                    if phase=='CALL' and self.endpoint_allow_call('WorkerProc.worker_main'):self.endpoint_sends[call]=before
                    if call not in self.endpoint_sends:return result
                    initial=self.endpoint_sends.get(call)
                    if phase!='CALL':self.endpoint_sends.pop(call,None)
                    reference=state.get('reference')
                    if reference is None:self.endpoint_gap('send-link-unavailable','WorkerProc.worker_main')
                    else:
                        data={'point':'WorkerProc.worker_main','operation':'SEND','call':call,'main':proxy.state['call'],
                            'rank':proxy.state['rank'],'started':started,
                            'source_code':self.generation_contract.POINT_CODES[self.endpoint_runtime]['WorkerProc.worker_main'],
                            'delegate_code':self.generation_contract.CONNECTION_CODES[self.endpoint_runtime]['send']}
                        self.endpoint_event(data,phase,before=initial,after=before if phase!='CALL' else None,status=status,kind=kind,reference=reference)
                except BaseException:pass
            return result
        finally:_SEND.reset(token)

    def generation_close(self):
        if hasattr(self,'endpoint_lock') and self.endpoint_pid==os.getpid() and not self.endpoint_closed:
            with self.endpoint_lock:
                self.endpoint_closed=True;selected=list(self.endpoint_selected);count=len(self.endpoint_readers)
            for entry in selected:
                try:self.endpoint_restore({'selected':[entry]})
                except BaseException:pass
            with self.endpoint_lock:self.endpoint_readers.clear();self.endpoint_sends.clear()
            if any(not entry['restored'] for entry in selected):
                super().generation_write('READY_ENDPOINT_GAP',{'schema':GAP_SCHEMA,'reason':'reader-restore-unverified',
                    'point':'WorkerProc.wait_for_ready',**self.endpoint_binding()})
            super().generation_write('READY_ENDPOINT_CLOSE',{'schema':CLOSE_SCHEMA,'cleared_readers':count,
                'selected_observer_closed':True,**self.endpoint_binding()})
        return super().generation_close()


def code_contract(base,contract):
    methods=(fn for fn in vars(EndpointObservation).values() if type(fn) is types.FunctionType)
    functions=(sha,canonical,normalize,current,configure,artifact,module,_private_base,install,*methods)
    dependencies=tuple(fn for fn in vars(contract).values() if type(fn) is types.FunctionType and fn.__module__==contract.__name__)
    return sha(''.join(base.code_digest(normalize(fn.__code__)) for fn in (*functions,*dependencies)).encode())


def configure(base,generation,contract):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if (sys.implementation.name!='cpython' or sys.flags.optimize!=0 or expected is None
            or code_contract(base,contract)!=expected or base.PROFILE!='vllm-native-lifecycle-metadata/21'
            or base.SCHEMA!='guard-native-lifecycle-events/21'):
        raise ValueError('READY_ENDPOINT_CODE_UNAVAILABLE')
    artifact(generation.__file__,GENERATION21_SHA256);artifact(contract.__file__,CONTRACT_SHA256)
    base.SOURCE_SHA={**base.SOURCE_SHA,**contract.SOURCE_SHA256};base.PROFILE,base.SCHEMA=PROFILE,SCHEMA
    base.__file__=__file__;base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES
    class EndpointObserver(EndpointObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.endpoint_pid=os.getpid();self.endpoint_lock=threading.RLock()
            self.endpoint_closed=False;self.endpoint_failed=False;self.endpoint_calls=0;self.endpoint_bound=False
            self.endpoint_readers=weakref.WeakKeyDictionary();self.endpoint_selected=[];self.endpoint_sends={}
            self.endpoint_contract=contract;self.endpoint_runtime=runtime;self.endpoint_code=expected;self.endpoint_qualified=None
        def instrument(self,source):
            if source.__name__!=contract.MP:return super().instrument(source)
            qualified=contract.qualify(source,base,self.startup_contract,runtime)
            # Qualify originals before immutable native14/21 install their wrappers.
            super().instrument(source);self.endpoint_qualified=qualified
            cls=qualified['worker_class'];previous=vars(cls)['make_worker_process']
            qualified['installed_main']=vars(cls)['worker_main'];qualified['installed_wait']=vars(cls)['wait_for_ready']
            try:setattr(cls,'make_worker_process',staticmethod(self.endpoint_wrap_make(qualified)))
            except BaseException:
                setattr(cls,'make_worker_process',previous);raise
    base.Observer=EndpointObserver;return base


def artifact(path,expected):
    selected=os.lstat(path)
    if not stat.S_ISREG(selected.st_mode):raise ValueError('READY_ENDPOINT_ASSET_UNAVAILABLE')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=131072:raise ValueError('READY_ENDPOINT_ASSET_UNAVAILABLE')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(131073)
        after=os.fstat(fd);current_path=os.lstat(path);identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns)
        if (len(raw)!=before.st_size or sha(raw)!=expected or len({identity(s) for s in (selected,before,after,current_path)})!=1
                or selected.st_ctime_ns!=current_path.st_ctime_ns or before.st_ctime_ns!=after.st_ctime_ns):raise ValueError('READY_ENDPOINT_ASSET_UNAVAILABLE')
        return raw
    finally:os.close(fd)


def module(name,filename,expected):
    path='/guard-lifecycle/'+filename;raw=artifact(path,expected);value=types.ModuleType(name);value.__file__=path
    exec(compile(raw,path,'exec',dont_inherit=True,optimize=0),vars(value));return value


def _private_base():
    generation=module('_guard_ready_generation21','guard_worker_generation.py',GENERATION21_SHA256)
    contract=module('_guard_ready_endpoint_contract','guard_ready_endpoint_contract.py',CONTRACT_SHA256)
    return configure(generation._private_base(),generation,contract)


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
