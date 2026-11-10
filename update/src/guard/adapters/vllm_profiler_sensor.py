"""Opt-in profiler adapter facts over immutable native8; no profiler control.

VLLM wrapper flags and adapter returns never certify Torch/backend collection,
trace callbacks, output transfer, cross-process RPC or the next normal start.
"""
from __future__ import annotations
import contextvars,functools,gzip,hashlib,inspect,json,os,re,stat,sys,threading,time,types,weakref

PROFILE='vllm-native-lifecycle-metadata/15'
SCHEMA='guard-native-lifecycle-events/15'
EVENT_SCHEMA='guard-native-profiler/2'
LEGACY_EVENT_SCHEMA='guard-native-profiler/1'
COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
BASE8_SHA256='e537d1d1b19813f171aa30b9344c1dbb49139eed3dea60bfa6dc71744d0d4f39'
WRAPPER='vllm.profiler.wrapper'
WORKER='vllm.v1.worker.gpu_worker'
ROUTER='vllm.entrypoints.serve.profile.api_router'
SOURCE_ADDITIONS={WRAPPER:'ba71c0accf29b2a987f7afc4dbf5cbe95cb16b72f7a5b67bb95c4b2e7f255fe8',
 WORKER:'43bd01925cd84f7d8a1d4a2ec30e289014a485fe4492a5343cbc893e8ed2f518',
 ROUTER:'b943c3a2201134e2d0b38bfa86d00e8b5685f5be6a54f6c7178dee784c1b78e5'}
TARGETS={WRAPPER:{'WorkerProfiler':('start','stop','_call_start','_call_stop','step','shutdown'),
 'TorchProfilerWrapper':('_start','_stop','_profiler_step')},WORKER:{'Worker':('profile',)}}
API_POINTS={'ProfilerAPI.start_profile':'start_profile','ProfilerAPI.stop_profile':'stop_profile'}
POINT_SOURCES={**{name+'.'+method:module for module,classes in TARGETS.items() for name,methods in classes.items() for method in methods},
 **{point:ROUTER for point in API_POINTS}}
POINTS=frozenset(POINT_SOURCES)
MAX_ROWS=2048
MAX_BYTES=2*1024**2
CODE_FILENAME='guard/adapters/vllm_profiler_sensor.py'
LEGACY_RUNTIME_CODE_CONTRACTS={'cpython-3.12.15': '40ce580a64fec147cc1fa5ecc33d8eafae24d61a0753d3b06aa7d61d58701628', 'cpython-3.12.14':'40ce580a64fec147cc1fa5ecc33d8eafae24d61a0753d3b06aa7d61d58701628','cpython-3.12.3':'423b896038c2ab86cf4b718d4be0ff71c6f22ce7102e7d58c97ccb68589308de'}
RUNTIME_CODE_CONTRACTS={'cpython-3.12.3':'6c47eb56e6ff474eda9c5b40231559fef440fd7934e8f8d0d1568d4d49272f59',
 'cpython-3.12.14':'f21bb6cf60090a6f75110deb50162c42735b9a94dcdd0304873bce5d13022485','cpython-3.12.15':'f21bb6cf60090a6f75110deb50162c42735b9a94dcdd0304873bce5d13022485'}
_BASE=None
_CALL=contextvars.ContextVar('guard_profiler_call',default=None)


def attrs(value):
    try:value=object.__getattribute__(value,'__dict__')
    except (AttributeError,TypeError):value={}
    return value if type(value) is dict else {}


def uint(value):return value if type(value) is int and 0<=value<2**63 else None
def flag(value):return value if type(value) is bool else None
def sha(value):return hashlib.sha256(value).hexdigest()


def file_identity(info):
    return [info.st_dev,info.st_ino,info.st_uid,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns]


def trace_inventory(owner,session):
    """Bounded, stable, local output only. Never return trace text or filenames."""
    result={'status':'unavailable','directory_identity':None,'collision_sha256':None,'files':[]}
    expected='/guard-profiler-traces/'+session
    path=attrs(attrs(owner).get('profiler_config')).get('torch_profiler_dir')
    if path!=expected:return result
    root='/guard-profiler-traces'
    try:
        rootfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            parent=os.fstat(rootfd)
            if parent.st_uid!=os.geteuid() or parent.st_mode&0o022:return result
            entry=os.stat(session,dir_fd=rootfd,follow_symlinks=False)
            if entry.st_uid!=os.geteuid():return result
            if stat.S_ISREG(entry.st_mode):
                fd=os.open(session,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=rootfd)
                try:
                    info=os.fstat(fd)
                    if info.st_nlink!=1 or info.st_size>128:return result
                    raw=os.read(fd,129)
                    if file_identity(info)!=file_identity(os.fstat(fd)) or file_identity(info)!=file_identity(os.stat(session,dir_fd=rootfd,follow_symlinks=False)):return result
                    marker=('guard-owned-profiler-stop-fault/1:'+session+'\n').encode()
                    if raw==marker:result.update(status='owned-collision',collision_sha256=sha(raw))
                    return result
                finally:os.close(fd)
            folder=os.open(session,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rootfd)
            try:
                before=os.fstat(folder)
                if before.st_mode&0o022 or file_identity(before)!=file_identity(entry):return result
                names=os.listdir(folder)
                if len(names)>16:return result
                files=[];left=8*1024**2
                for name in sorted(names):
                    kind='trace' if name.endswith(('.pt.trace.json','.pt.trace.json.gz')) else 'table' if re.fullmatch(r'profiler_out_[0-9]+\.txt',name) else None
                    if kind is None:return result
                    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=folder)
                    try:
                        info=os.fstat(fd)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid() or not 0<info.st_size<=left:return result
                        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(left+1)
                        if len(raw)!=info.st_size or file_identity(info)!=file_identity(os.fstat(fd)) or file_identity(info)!=file_identity(os.stat(name,dir_fd=folder,follow_symlinks=False)):return result
                        events=None
                        if kind=='trace':
                            if name.endswith('.gz'):
                                import io
                                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:decoded=stream.read(16*1024**2+1)
                            else:decoded=raw
                            if len(decoded)>16*1024**2:return result
                            value=json.loads(decoded)
                            if type(value) is not dict or type(value.get('traceEvents')) is not list or not value['traceEvents']:return result
                            events=len(value['traceEvents'])
                        files.append({'name_sha256':sha(name.encode()),'kind':kind,'sha256':sha(raw),'bytes':len(raw),'identity':file_identity(info),'trace_events':events})
                        left-=len(raw)
                    finally:os.close(fd)
                if file_identity(before)!=file_identity(os.fstat(folder)) or file_identity(before)!=file_identity(os.stat(session,dir_fd=rootfd,follow_symlinks=False)):return result
                result.update(status='directory',directory_identity=file_identity(before)[:4],files=files)
                return result
            finally:os.close(folder)
        finally:os.close(rootfd)
    except Exception:return result


def checked_codes(module,base):
    name=module.__name__;path=vars(module).get('__file__');first=os.stat(path,follow_symlinks=False)
    if not stat.S_ISREG(first.st_mode):raise ValueError('PROFILER_SOURCE_UNAVAILABLE')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=131072:raise ValueError
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(131073)
        after=os.fstat(fd);last=os.stat(path,follow_symlinks=False)
        identity=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_size,v.st_mtime_ns)+(() if os.name=='nt' else (v.st_ctime_ns,))
        if (len(raw)!=before.st_size or len({identity(v) for v in (first,before,after,last)})!=1
            or first.st_ctime_ns!=last.st_ctime_ns or before.st_ctime_ns!=after.st_ctime_ns or sha(raw)!=SOURCE_ADDITIONS[name]):raise ValueError('PROFILER_SOURCE_UNAVAILABLE')
    finally:os.close(fd)
    result={}
    def visit(code):
        result[code.co_qualname]=base.code_digest(code)
        for value in code.co_consts:
            if isinstance(value,types.CodeType):visit(value)
    visit(compile(raw,path,'exec',dont_inherit=True,optimize=0));return result


def route_targets(module,point,original):
    router=vars(module).get('router');routes=attrs(router).get('routes');path='/start_profile' if point.endswith('start_profile') else '/stop_profile'
    if type(routes) is not list or len(routes)>16:raise ValueError('PROFILER_REGISTERED_ROUTE_UNAVAILABLE')
    matches=[]
    for route in routes:
        data=attrs(route)
        if data.get('path')!=path:continue
        dependant=data.get('dependant');fields=attrs(dependant)
        if data.get('endpoint') is not original or fields.get('call') is not original or data.get('methods')!={'POST'}:raise ValueError('PROFILER_REGISTERED_ROUTE_UNAVAILABLE')
        matches.extend([(route,'endpoint',original),(dependant,'call',original)])
    if len(matches)!=2:raise ValueError('PROFILER_REGISTERED_ROUTE_UNAVAILABLE')
    return matches


class ProfilerObservation:
    def profiler_backend(self,owner):
        result={'value':None,'collected':False,'backend_sha256':None,'thread_id':None}
        if type(owner).__module__!=WRAPPER or type(owner).__name__!='TorchProfilerWrapper':return result
        torch=sys.modules.get('torch');data=attrs(owner);backend=data.get('profiler')
        try:
            # The independent probe is Torch's C builtin, never a wrapper flag,
            # Python replacement, EXAMPLE backend or HTTP response.
            probe=torch.autograd._profiler_enabled
            if (not inspect.isbuiltin(probe) or probe is not torch._C._autograd._profiler_enabled
                    or probe.__module__!='torch._C._autograd'):return result
            value=probe()
            if type(value) is not bool:return result
            result.update(value=value,collected=True,thread_id=threading.get_native_id())
            if type(backend) is torch.profiler.profile:result['backend_sha256']=self.profiler_nonce(backend)
        except Exception:pass
        return result

    def profiler_nonce(self,owner=None):
        if self.profiler_pid!=os.getpid():
            self.profiler_pid=os.getpid();self.profiler_lock=threading.RLock();self.profiler_serial=0;self.profiler_objects=weakref.WeakKeyDictionary();self.profiler_generations={}
        with self.profiler_lock:
            if owner is not None:
                existing=self.profiler_objects.get(owner)
                if existing is not None:return existing
                if len(self.profiler_objects)>=256:return None
            self.profiler_serial+=1;context=self._writer().context
            result=sha(':'.join(map(str,(self.config['session'],context['pid'],context['start_ticks'],context['boot_sha256'],context['pid_namespace'],self.profiler_serial))).encode())
            if owner is not None:self.profiler_objects[owner]=result
            return result

    def profiler_state(self,owner):
        data=attrs(owner);kind=None;wrapper=None;rank=None;backend=None
        if owner is not None and type(owner).__module__==WORKER and type(owner).__name__=='Worker':
            kind='worker';wrapper=data.get('profiler');rank=uint(data.get('rank'))
            declared=attrs(data.get('profiler_config')).get('profiler');backend=declared if type(declared) is str and declared in {'torch','cuda','proton'} else 'other' if declared is not None else None
        elif owner is not None and type(owner).__module__==WRAPPER and type(owner).__name__=='TorchProfilerWrapper':kind='torch-wrapper';wrapper=owner;backend='torch'
        selected=wrapper is not None and type(wrapper).__module__==WRAPPER and type(wrapper).__name__=='TorchProfilerWrapper'
        fields=attrs(wrapper) if selected else {};local_rank=uint(fields.get('local_rank'))
        return {'owner_kind':kind,'wrapper_sha256':self.profiler_nonce(wrapper) if selected else None,'backend_kind':backend,
            'rank':rank if rank is not None and rank<64 else None,'local_rank':local_rank if local_rank is not None and local_rank<64 else None,
            'active':flag(fields.get('_active')),'running':flag(fields.get('_running')),
            'active_iteration_count':uint(fields.get('_active_iteration_count')),'profiling_for_iters':uint(fields.get('_profiling_for_iters'))}

    def profiler_begin(self,point,original,expected,args,kwargs,registered):
        if not self.active():return None
        module=POINT_SOURCES[point]
        if self.modules.get(module,{}).get('source_sha256')!=SOURCE_ADDITIONS[module]:return None
        if self.profiler_base.code_digest(original.__code__)!=expected:
            self.modules.pop(module,None);self.emit('MODULE',{'module':module,'verified':False});return None
        owner=args[0] if point not in API_POINTS and args else None;state=self.profiler_state(owner)
        if point not in API_POINTS and state['owner_kind'] is None:return None
        parent=_CALL.get();parent=parent if type(parent) is dict and parent.get('pid')==os.getpid() and parent.get('closed') is False else None
        context=self.route_context() or {};is_start=None
        if point=='Worker.profile':is_start=flag(args[1] if len(args)>1 else kwargs.get('is_start',True))
        backend=self.profiler_backend(owner) if point in {'TorchProfilerWrapper._start','TorchProfilerWrapper._stop'} else None
        return {'point':point,'call_sha256':self.profiler_nonce(),'parent_call_sha256':parent['call_sha256'] if parent else None,
            'owner_sha256':self.profiler_nonce(owner) if owner is not None else None,'owner':owner,'state_before':state,
            'asgi_request_sha256':context.get('request'),'asgi_invocation_sha256':context.get('invocation'),
            'registered_handler_observed':registered,'is_start':is_start,'pid':os.getpid(),'closed':False,
            'backend_before':backend,'trace_before':trace_inventory(owner,self.config['session']) if backend is not None else None}

    def profiler_emit(self,binding,phase,result,kind,started,finished):
        try:
            point=binding['point'];module=POINT_SOURCES[point];status=attrs(result).get('status_code') if phase=='RETURN' and point in API_POINTS else None
            before=binding['backend_before'];after=self.profiler_backend(binding['owner']) if phase!='CALL' and before is not None else None
            generation=None;qualified=False
            if before is not None:
                key=(binding['state_before']['wrapper_sha256'],before['thread_id'])
                with self.profiler_lock:
                    if point=='TorchProfilerWrapper._start' and phase=='RETURN' and before['collected'] and before['value'] is False and after['collected'] and after['value'] is True and after['backend_sha256'] is not None and before['thread_id']==after['thread_id']:
                        self.profiler_generations[key]={'generation':self.profiler_nonce(),'backend':after['backend_sha256'],'thread':after['thread_id']}
                    lease=self.profiler_generations.get(key)
                    if lease is not None and ((point.endswith('._start') and phase=='RETURN' and after['backend_sha256']==lease['backend']) or (point.endswith('._stop') and before['collected'] and before['value'] is True and before['backend_sha256']==lease['backend'])):
                        generation=lease['generation']
                        qualified=bool(after is not None and after['collected'] and after['thread_id']==lease['thread'] and after['backend_sha256']==lease['backend'])
            self.emit('PROFILER',{'schema':EVENT_SCHEMA,'point':point,'phase':phase,'source_module':module,'source_sha256':SOURCE_ADDITIONS[module],
                'source_verified':self.modules.get(module,{}).get('source_sha256')==SOURCE_ADDITIONS[module],
                'observer_runtime':self.profiler_runtime,'observer_code_sha256':self.profiler_code,
                **{key:binding[key] for key in ('call_sha256','parent_call_sha256','owner_sha256','asgi_request_sha256','asgi_invocation_sha256','registered_handler_observed','is_start','state_before')},
                'state_after':self.profiler_state(binding['owner']) if phase!='CALL' else None,
                'api_status_code':status if type(status) is int and 100<=status<=599 else None,'exception_kind':kind,
                'operation_started_ns':started,'operation_finished_ns':finished,
                'backend_collection_stopped_verified':False,'trace_output_completed_verified':False,'cross_process_rpc_verified':False,'producer_authenticated':False,
                'backend_before':before,'backend_after':after,'backend_generation_sha256':generation,'backend_probe_qualified':qualified,
                'trace_before':binding['trace_before'],'trace_after':trace_inventory(binding['owner'],self.config['session']) if phase!='CALL' and before is not None else None})
        except BaseException:self.disabled_pid=os.getpid()

    def profiler_wrap(self,point,original,expected,registered=False):
        observer=self
        def begin(args,kwargs):
            try:binding=observer.profiler_begin(point,original,expected,args,kwargs,registered)
            except BaseException:observer.disabled_pid=os.getpid();return None,None
            if binding is None:return None,None
            observer.profiler_emit(binding,'CALL',None,None,None,None)
            try:return binding,_CALL.set(binding)
            except BaseException:observer.disabled_pid=os.getpid();return None,None
        def finish(binding,token,result,kind,returned,started):
            observer.profiler_emit(binding,'RETURN' if returned else 'RAISE',result,kind,started,time.monotonic_ns());binding['closed']=True
            try:_CALL.reset(token)
            except BaseException:observer.disabled_pid=os.getpid()
        if inspect.iscoroutinefunction(original):
            @functools.wraps(original)
            async def wrapped(*args,**kwargs):
                binding,token=begin(args,kwargs)
                if binding is None:return await original(*args,**kwargs)
                result=None;kind=None;returned=False;started=time.monotonic_ns()
                try:result=await original(*args,**kwargs);returned=True;return result
                except BaseException as error:
                    kind='exception' if isinstance(error,Exception) else 'base-exception';raise
                finally:finish(binding,token,result,kind,returned,started)
        else:
            @functools.wraps(original)
            def wrapped(*args,**kwargs):
                binding,token=begin(args,kwargs)
                if binding is None:return original(*args,**kwargs)
                result=None;kind=None;returned=False;started=time.monotonic_ns()
                try:result=original(*args,**kwargs);returned=True;return result
                except BaseException as error:
                    kind='exception' if isinstance(error,Exception) else 'base-exception';raise
                finally:finish(binding,token,result,kind,returned,started)
        return wrapped


def relative_code(code):
    return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(relative_code(v) if isinstance(v,types.CodeType) else v for v in code.co_consts))


def code_contract(base):
    functions=(attrs,uint,flag,sha,file_identity,trace_inventory,checked_codes,route_targets,ProfilerObservation.profiler_backend,ProfilerObservation.profiler_nonce,ProfilerObservation.profiler_state,
        ProfilerObservation.profiler_begin,ProfilerObservation.profiler_emit,ProfilerObservation.profiler_wrap,configure)
    return sha(''.join(base.code_digest(relative_code(fn.__code__)) for fn in functions).encode())


def configure(base):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if (sys.implementation.name!='cpython' or sys.flags.optimize!=0 or expected is None or code_contract(base)!=expected) or (runtime == 'cpython-3.12.15' and (sys.platform != 'linux' or os.name != 'posix' or os.uname().machine != 'x86_64' or sys.flags.optimize != 0)):raise ValueError('PROFILER_OBSERVER_CODE_UNAVAILABLE')
    base.PROFILE,base.SCHEMA=PROFILE,SCHEMA;base.__file__=__file__;base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES
    if any(key in base.SOURCE_SHA and base.SOURCE_SHA[key]!=value for key,value in SOURCE_ADDITIONS.items()):raise ValueError('PROFILER_SOURCE_CONFLICT')
    base.SOURCE_SHA={**base.SOURCE_SHA,**SOURCE_ADDITIONS}
    class ProfilerObserver(ProfilerObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.profiler_base=base;self.profiler_runtime=runtime;self.profiler_code=expected
            self.profiler_pid=os.getpid();self.profiler_lock=threading.RLock();self.profiler_serial=0;self.profiler_objects=weakref.WeakKeyDictionary();self.profiler_generations={}
        def instrument(self,module):
            name=module.__name__
            if name not in SOURCE_ADDITIONS:return super().instrument(module)
            codes=checked_codes(module,base);changes=[]
            if name==ROUTER:
                for point,method in API_POINTS.items():
                    original=vars(module).get(method)
                    if not inspect.isfunction(original) or not inspect.iscoroutinefunction(original) or base.code_digest(original.__code__)!=codes.get(method):raise ValueError('PROFILER_HANDLER_CODE_UNAVAILABLE')
                    routes=route_targets(module,point,original);wrapped=self.profiler_wrap(point,original,codes[method],registered=True)
                    changes.append((module,method,original,wrapped));changes.extend((obj,key,old,wrapped) for obj,key,old in routes)
            else:
                for cls_name,methods in TARGETS[name].items():
                    cls=vars(module).get(cls_name)
                    if not isinstance(cls,type):raise ValueError('PROFILER_CLASS_UNAVAILABLE')
                    for method in methods:
                        original=vars(cls).get(method);point=cls_name+'.'+method
                        if not inspect.isfunction(original) or base.code_digest(original.__code__)!=codes.get(point):raise ValueError('PROFILER_POINT_CODE_UNAVAILABLE')
                        changes.append((cls,method,original,self.profiler_wrap(point,original,codes[point])))
            super().instrument(module);applied=[]
            try:
                for obj,key,old,new in changes:setattr(obj,key,new);applied.append((obj,key,old))
            except BaseException:
                for obj,key,old in reversed(applied):
                    try:setattr(obj,key,old)
                    except BaseException:pass
                self.modules.pop(name,None);self.emit('MODULE',{'module':name,'verified':False});raise
    base.Observer=ProfilerObserver;return base


def _private_base():
    path='/guard-lifecycle/guard_lifecycle_base.py';fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=262144:raise ValueError
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(262145)
        after=os.fstat(fd)
        if len(raw)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or sha(raw)!=BASE8_SHA256:raise ValueError
    finally:os.close(fd)
    base=types.ModuleType('_guard_profiler_base');base.__file__='/guard-lifecycle/guard_lifecycle_base.py'
    exec(compile(raw,base.__file__,'exec',dont_inherit=True,optimize=0),base.__dict__);return configure(base)


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
