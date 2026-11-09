"""Opt-in native24 source hooks, composing reviewed native23 and rank13.

Intentional LAB exceptions occur only inside the original worker RPC try.
Observer failures call the unchanged native operation and leave incomplete
records. Owned control files select work; the final packet validator qualifies
its physical process, source, request, clock and generation separately.
"""
from __future__ import annotations
import contextvars,functools,hashlib,inspect,json,os,stat,sys,threading,time,types,weakref

PROFILE='vllm-native-lifecycle-metadata/24';SCHEMA='guard-native-lifecycle-events/24'
EVENT_SCHEMA='guard-native-fault-control-events/1'
COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
BASE8_SHA256='e537d1d1b19813f171aa30b9344c1dbb49139eed3dea60bfa6dc71744d0d4f39'
ASSETS={'guard_termination.py':'1cc33dbfb078b3489f8b8ee7dfa9b251dfa851252b9a2daee4f1ec0cac862d00',
 'guard_rank_failure.py':'133c317a59ba1d1a1a4bdbcc6710823b7e7b0382e6e39a2a580d346881fb04e0',
 'guard_fault_control.py':'900fdecf8c32ab5b008d23bd187326a55b35023443d7d53625e64230166afbe9',
 'guard_fault_runtime.py':'8c561a71e9c5acffa39351cf97bdd724aac3cbbff00b23288c42a796378a84a8'}
MP='vllm.v1.executor.multiproc_executor';CORE='vllm.v1.engine.core'
INPUT='vllm.v1.engine.input_processor';RUNNER='vllm.v1.worker.gpu.model_runner'
SOURCE_ADDITIONS={MP:'11a1554380d1efa516bb481416c798798d5839f38ac3b0f379ef4b8b547f4d1b'}
MAX_ROWS=2048;MAX_BYTES=2*1024**2;MAX_OUTPUTS=8
CODE_FILENAME='guard/adapters/vllm_fault_sensor.py'
RUNTIME_CODE_CONTRACTS={'cpython-3.12.3':'5c1001658e8ee74b6453a2c60b1ea48f27e09e3b2b0db2a2867ae10544657a4a',**{key:'c43083a30bdf0a51f83bdae74133d044f63c0ef79c10287bb3c4960700624d6a'for key in ('cpython-3.12.13','cpython-3.12.15','cpython-3.12.16')}}
RANK_CODE_CONTRACTS={'cpython-3.12.3':'76c5d6932a22838fbcd8c49a39f98e6170b1b7591b2f2b11d860f996874de972','cpython-3.12.13':'31c2a7bf9d0b72322e731905a7d373d595957c638245ecef2b16dd70cc109ad3','cpython-3.12.15':'31c2a7bf9d0b72322e731905a7d373d595957c638245ecef2b16dd70cc109ad3','cpython-3.12.16':'31c2a7bf9d0b72322e731905a7d373d595957c638245ecef2b16dd70cc109ad3'}
_BASE=None
_CURRENT=contextvars.ContextVar('byul-native24-rpc',default=None)


def sha(raw):return hashlib.sha256(raw).hexdigest()
def attrs(value):
    try:result=object.__getattribute__(value,'__dict__')
    except (TypeError,AttributeError):result={}
    return result if type(result)is dict else{}
def normalized(code):return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(normalized(v)if type(v)is types.CodeType else v for v in code.co_consts))


def artifact(path,expected=None):
    before=os.stat(path,follow_symlinks=False);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        start=os.fstat(fd);raw=os.read(fd,262145);end=os.fstat(fd);after=os.stat(path,follow_symlinks=False)
        identity=lambda v:(v.st_dev,v.st_ino,v.st_uid,v.st_mode,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
        if(not stat.S_ISREG(start.st_mode)or not 0<len(raw)<=262144 or len(raw)!=start.st_size
            or len({identity(v)for v in (before,start,end,after)})!=1 or expected is not None and sha(raw)!=expected
            or expected is None and(start.st_uid!=os.geteuid()or stat.S_IMODE(start.st_mode)not in {0o400,0o600})):raise ValueError('FAULT_SENSOR_ASSET_UNAVAILABLE')
        return raw
    finally:os.close(fd)


def module(name,filename):
    path='/guard-lifecycle/'+filename;raw=artifact(path,ASSETS[filename]);value=types.ModuleType(name);value.__file__=path
    exec(compile(raw,path,'exec',dont_inherit=True,optimize=0),vars(value));return value


def source_settings(control,session):
    path='/guard-lifecycle/fault-control-settings.json';raw=artifact(path)
    value=control.strict_json(raw)
    if(type(value)is not dict or set(value)!={'schema','directory','session','target_sha256'}
        or value['schema']!='byul-native-fault-settings/1'or value['session']!=session
        or value['directory']!='/guard-lifecycle/fault-control'or not control.fingerprint(value['target_sha256'])):raise ValueError('FAULT_SENSOR_SETTINGS_INVALID')
    root=control._directory(value['directory']);os.close(root);return value


class FaultObservation:
    def fault_reset(self):
        if self.fault_pid!=os.getpid():
            self.fault_pid=os.getpid();self.fault_serial=0;self.fault_outputs=weakref.WeakKeyDictionary();self.fault_runtime=None
    def fault_context(self):
        context=self._writer().context
        return {k:context[k]for k in ('pid','start_ticks','pid_namespace')}|{'clock_context':{k:context[k]for k in ('boot_sha256','time_namespace','time_offsets_sha256')}}
    def fault_call(self):
        self.fault_reset();self.fault_serial+=1
        return sha(json.dumps([self.config['session'],self.fault_context(),self.fault_serial],sort_keys=True).encode())
    def fault_controls(self):
        self.fault_reset()
        if self.fault_runtime is None:
            cfg=source_settings(self.fault_control,self.config['session'])
            self.fault_runtime=self.fault_mechanics.SourceControl(self.fault_control,directory=cfg['directory'],session=cfg['session'],
                target_sha256=cfg['target_sha256'],emit=self.fault_emit,context=self.fault_context,
                observer_runtime=self.fault_abi,observer_code_sha256=self.fault_code)
        return self.fault_runtime
    def fault_emit(self,phase,receipt,receipt_sha):
        context=_CURRENT.get();context=context if type(context)is dict else{}
        point='InputProcessor.assign_request_id'if phase=='ASSIGNED'else receipt.get('point')
        source=INPUT if phase=='ASSIGNED'else MP
        payload={'schema':EVENT_SCHEMA,'phase':phase,'point':point,'source_module':source,
            'source_sha256':self.fault_base.SOURCE_SHA.get(source),'body_code_sha256':self.fault_body.get((source,point)),
            'observer_runtime':self.fault_abi,'observer_code_sha256':self.fault_code,
            'nonce':receipt['nonce'],'fault_kind':receipt['fault_kind'],'rank':receipt['rank'],
            'external_request_sha256':receipt['external_request_sha256'],'native_request_sha256':receipt['native_request_sha256'],
            'session':receipt['session'],'target_sha256':receipt['target_sha256'],
            'asgi_invocation_sha256':receipt.get('asgi_invocation_sha256',context.get('invocation')),
            'call_sha256':receipt.get('call_sha256',context.get('call')),
            'ready_receipt_sha256':receipt_sha if phase=='READY'else context.get('ready_sha'),
            'action_receipt_sha256':receipt_sha if phase=='ACTIVATE'else None,
            'operation_started_ns':receipt.get('operation_started_ns',context.get('started')),
            'observation_ns':time.monotonic_ns(),'barrier_deadline_ns':receipt.get('barrier_deadline_ns'),
            'clock_context':self.fault_context()['clock_context'],'rpc_active':context.get('rpc_active')is True,
            'runner_bound':context.get('runner_bound')is True,'async_output_bound':context.get('async_output_bound')is True,'worker_loop_observed':context.get('worker_loop_observed')is True}
        self.emit('FAULT_CONTROL',payload)
        if phase=='READY'and context:
            context['ready']=receipt;context['ready_sha']=receipt_sha
    def fault_ids(self,scheduler):
        selected=attrs(scheduler).get('num_scheduled_tokens')
        if(type(selected)is not dict or not 0<len(selected)<=8 or any(type(k)is not str or not 0<len(k)<=512 or type(v)is not int or v<=0 for k,v in selected.items())):return ()
        return tuple(sha(k.encode())for k in selected)
    def fault_rpc(self,owner,args,kwargs):
        if type(owner).__module__!=MP or type(owner).__name__!='WorkerProc'or type(attrs(owner).get('rank'))is not int or attrs(owner).get('rank')!=0:return None
        rpc=args[1]if len(args)>1 else kwargs.get('rpc_request')
        if type(rpc)is not tuple or len(rpc)!=4 or rpc[0]!='execute_model'or type(rpc[1])is not tuple or len(rpc[1])!=1 or type(rpc[2])is not dict or rpc[2]or not(rpc[3]is None or type(rpc[3])is int and rpc[3]==0):return None
        selected=self.fault_ids(rpc[1][0]);wrapper=attrs(owner).get('worker');worker=attrs(wrapper).get('worker');runner=attrs(worker).get('model_runner')
        if(type(wrapper).__module__!='vllm.v1.worker.worker_base'or type(wrapper).__name__!='WorkerWrapperBase'
            or type(worker).__module__!='vllm.v1.worker.gpu_worker'or type(worker).__name__!='Worker'
            or type(runner).__module__!=RUNNER or type(runner).__name__!='GPUModelRunner'):return None
        parallel=attrs(attrs(runner).get('vllm_config')).get('parallel_config');parallel=attrs(parallel)
        if not selected or not all(type(parallel.get(k))is int and parallel[k]==1 for k in ('tensor_parallel_size','pipeline_parallel_size','data_parallel_size')):return None
        return {'requests':selected,'runner':weakref.ref(runner),'worker':weakref.ref(owner),'call':self.fault_call(),
            'started':time.monotonic_ns(),'rpc_active':True,'runner_bound':False,'async_output_bound':False,'invocation':None}
    def fault_live_worker_loop(self,worker):
        expected=self.rank_codes.get(MP,{}).get('WorkerProc.worker_busy_loop')
        if expected is None:return False
        frames=sys._current_frames()
        try:
            if len(frames)>64:return False
            for thread,frame in frames.items():
                if thread==threading.get_ident():continue
                for depth in range(16):
                    if frame is None:break
                    if(self.fault_base.code_digest(frame.f_code)==expected and frame.f_locals.get('self')is worker):return True
                    frame=frame.f_back
            return False
        finally:
            frame=None;frames.clear()
    def fault_wrap(self,module_name,point,original):
        observer=self
        @functools.wraps(original)
        def wrapped(*args,**kwargs):
            context=None;token=None;intention=None
            try:
                if(observer.active()and observer.fault_base.code_digest(inspect.unwrap(original).__code__)==observer.fault_body[(module_name,point)]):
                    if point=='WorkerProc._execute_worker_rpc':
                        context=observer.fault_rpc(args[0]if args else None,args,kwargs)
                        if context is not None:token=_CURRENT.set(context)
                    elif point=='GPUModelRunner.execute_model':
                        context=_CURRENT.get()
                        if(type(context)is dict and context['rpc_active']and context['runner']()is(args[0]if args else None)
                            and observer.fault_ids(args[1]if len(args)>1 else kwargs.get('scheduler_output'))==context['requests']):
                            context['runner_bound']=True;runtime=observer.fault_controls()
                            selected=runtime.select(context['requests'],0,'WorkerProc._execute_worker_rpc')
                            intention=runtime.hold(selected,point='WorkerProc._execute_worker_rpc',call_sha256=context['call'],operation_started_ns=context['started'])
                    elif point=='WorkerProc.handle_output':
                        context=_CURRENT.get();output=args[1]if len(args)>1 else kwargs.get('output')
                        if(type(context)is dict and context['worker']()is(args[0]if args else None)
                            and context['runner_bound']and len(observer.fault_outputs)<MAX_OUTPUTS):
                            observer.fault_outputs[output]=context
                            if type(output)is observer.fault_mechanics.NativeLabFault and context.get('ready')is not None:
                                observer.fault_emit('EXCEPTION_CAUGHT',context['ready'],None)
                    elif point=='WorkerProc.enqueue_output':
                        output=args[1]if len(args)>1 else kwargs.get('output');context=observer.fault_outputs.get(output)
                        if type(context)is dict and context['worker']()is(args[0]if args else None):
                            # An asynchronous response thread is necessary for a
                            # bounded IPC-only stall with the worker loop alive.
                            caller=sys._getframe(1);expected=observer.rank_codes.get(MP,{}).get('WorkerProc.async_output_busy_loop')
                            if(attrs(args[0]).get('use_async_scheduling')is True and expected is not None
                                and observer.fault_base.code_digest(caller.f_code)==expected
                                and caller.f_locals.get('self')is args[0]and caller.f_locals.get('output')is output
                                and observer.fault_live_worker_loop(args[0])):
                                context=dict(context,rpc_active=False,async_output_bound=True,worker_loop_observed=True);token=_CURRENT.set(context)
                                runtime=observer.fault_controls();selected=runtime.select(context['requests'],0,'WorkerProc.enqueue_output')
                                runtime.hold(selected,point='WorkerProc.enqueue_output',call_sha256=context['call'],operation_started_ns=context['started'])
            except BaseException:
                # A malformed/expired control or observer failure never becomes
                # a native exception or replaces the operation's return value.
                intention=None
            try:
                if(intention is not None and intention['activated']and intention['kind']=='exception'
                    and time.monotonic_ns()<context['ready']['barrier_deadline_ns']):
                    try:observer.fault_emit('EXCEPTION_INTENT',context['ready'],None)
                    except BaseException:pass
                    if time.monotonic_ns()<context['ready']['barrier_deadline_ns']:
                        raise observer.fault_mechanics.NativeLabFault('BYUL_LAB_WORKER_RPC_EXCEPTION')
                result=original(*args,**kwargs)
                if point=='InputProcessor.assign_request_id':
                    try:
                        value=args[0]if args else kwargs.get('request');route=observer.route_context();asgi=observer.fault_base._ASGI_REQUEST.get()
                        if(type(asgi)is tuple and len(asgi)==2 and asgi[0]==os.getpid()and type(route)is dict
                            and route.get('request')==asgi[1]and type(attrs(value).get('request_id'))is str):
                            observer.fault_controls().assign(asgi[1],sha(attrs(value)['request_id'].encode()),route['invocation'])
                    except BaseException:pass
                return result
            finally:
                if point=='WorkerProc._execute_worker_rpc'and context is not None:context['rpc_active']=False
                if point=='WorkerProc.enqueue_output':
                    try:observer.fault_outputs.pop(args[1]if len(args)>1 else kwargs.get('output'),None)
                    except BaseException:pass
                if token is not None:
                    try:_CURRENT.reset(token)
                    except BaseException:pass
        return wrapped


def configure(base,termination,rank,control,mechanics):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if(sys.implementation.name!='cpython'or sys.flags.optimize!=0 or expected is None
        or code_contract(base)!=expected or base.PROFILE!=termination.PROFILE):raise ValueError('FAULT_OBSERVER_CODE_UNAVAILABLE')
    for value,filename in ((termination,'guard_termination.py'),(rank,'guard_rank_failure.py'),
        (control,'guard_fault_control.py'),(mechanics,'guard_fault_runtime.py')):artifact(value.__file__,ASSETS[filename])
    base.PROFILE,base.SCHEMA=PROFILE,SCHEMA;base.__file__=__file__;base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES
    base.SOURCE_SHA={**base.SOURCE_SHA,**SOURCE_ADDITIONS}
    rank_contract=rank.code_contract(base)
    if rank_contract!=RANK_CODE_CONTRACTS.get(runtime):raise ValueError('FAULT_RANK_OBSERVER_CODE_UNAVAILABLE')
    class FaultObserver(FaultObservation,rank.RankObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.fault_base=base;self.fault_control=control;self.fault_mechanics=mechanics
            self.fault_abi=runtime;self.fault_code=expected;self.fault_pid=os.getpid();self.fault_serial=0
            self.fault_outputs=weakref.WeakKeyDictionary();self.fault_runtime=None;self.fault_body={}
            self.rank_base=base;self.rank_runtime=runtime;self.rank_code=rank_contract;self.rank_pid=os.getpid()
            self.rank_lock=threading.RLock();self.rank_serial=0;self.rank_objects=weakref.WeakKeyDictionary()
            self.rank_futures=weakref.WeakKeyDictionary();self.rank_codes={}
        def instrument(self,module):
            name=module.__name__;rank_changes=[]
            if name in rank.TARGETS:
                codes=rank.checked_codes(module,base)
                for cls,methods in rank.TARGETS[name].items():
                    owner=vars(module).get(cls)
                    if not isinstance(owner,type):raise ValueError('FAULT_RANK_CLASS_UNAVAILABLE')
                    for method in methods:
                        original=vars(owner).get(method);point=cls+'.'+method
                        if not inspect.isfunction(original)or base.code_digest(original.__code__)!=codes.get(point):raise ValueError('FAULT_RANK_BODY_UNAVAILABLE')
                        rank_changes.append((owner,method,self.rank_wrap(name,point,original,codes[point])))
                        self.fault_body[(name,point)]=codes[point]
                self.rank_codes[name]=codes
            # The original native23/base hook installation checks the source
            # and original method bodies before any new source wrapper is set.
            super().instrument(module)
            for owner,method,wrapped in rank_changes:setattr(owner,method,wrapped)
            points={INPUT:{'InputProcessor':('assign_request_id',)},RUNNER:{'GPUModelRunner':('execute_model',)},
                MP:{'WorkerProc':('_execute_worker_rpc','handle_output','enqueue_output')}}
            if name not in points:return
            source=termination.checked_codes(module,base)if name in termination.SOURCE_ADDITIONS else rank.checked_codes(module,base)if name==MP else None
            if source is None:
                # InputProcessor is already a checked immutable native8 module.
                path=vars(module).get('__file__');raw=artifact(path,base.SOURCE_SHA[name]);source={}
                def visit(code):
                    source[code.co_qualname]=base.code_digest(code)
                    for child in code.co_consts:
                        if type(child)is types.CodeType:visit(child)
                visit(compile(raw,path,'exec',dont_inherit=True,optimize=0))
            for cls,methods in points[name].items():
                owner=vars(module).get(cls)
                if not isinstance(owner,type):raise ValueError('FAULT_CLASS_UNAVAILABLE')
                for method in methods:
                    selected=vars(owner).get(method);static=isinstance(selected,staticmethod)
                    original=selected.__func__ if static else selected;body=inspect.unwrap(original);point=cls+'.'+method
                    if(type(body)is not types.FunctionType or body.__globals__ is not vars(module)
                        or base.code_digest(body.__code__)!=source.get(point)):raise ValueError('FAULT_SOURCE_BODY_UNAVAILABLE')
                    self.fault_body[(name,point)]=source[point];wrapped=self.fault_wrap(name,point,original)
                    setattr(owner,method,staticmethod(wrapped)if static else wrapped)
    base.Observer=FaultObserver;return base


def code_contract(base):
    functions=(sha,attrs,normalized,artifact,module,source_settings,configure,
        *[v for k,v in vars(FaultObservation).items()if type(v)is types.FunctionType])
    return sha(''.join(base.code_digest(normalized(fn.__code__))for fn in functions).encode())


def _private_base():
    termination=module('_guard_fault_termination','guard_termination.py')
    rank=module('_guard_fault_rank','guard_rank_failure.py');control=module('_guard_fault_control','guard_fault_control.py')
    mechanics=module('_guard_fault_runtime','guard_fault_runtime.py')
    return configure(termination._private_base(),termination,rank,control,mechanics)


def install(path=None):
    global _BASE
    if _BASE is not None:return False
    try:
        base=_private_base()
        if not base.install(path,open_sink=True):return False
        _BASE=base;return True
    except BaseException:return False


class Middleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        return await _BASE.Middleware(self.app)(scope,receive,send)if _BASE is not None else await self.app(scope,receive,send)


def seal(path='/guard-lifecycle/settings.json'):
    return (_BASE if _BASE is not None else _private_base()).seal(path)
