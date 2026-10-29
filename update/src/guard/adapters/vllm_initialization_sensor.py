"""Opt-in mp startup facts over immutable native8; never starts/stops workers."""
from __future__ import annotations
import contextvars,functools,hashlib,inspect,os,stat,sys,threading,time,types,weakref

PROFILE='vllm-native-lifecycle-metadata/14'
SCHEMA='guard-native-lifecycle-events/14'
EVENT_SCHEMA='guard-native-initialization/1'
COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
BASE8_SHA256='2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd'
MP='vllm.v1.executor.multiproc_executor'
SOURCE_ADDITIONS={MP:'11a1554380d1efa516bb481416c798798d5839f38ac3b0f379ef4b8b547f4d1b'}
TARGETS={'MultiprocExecutor':('_init_executor',),'WorkerProc':('wait_for_ready','wait_for_response_handle_ready')}
STATIC_POINTS=frozenset({'WorkerProc.wait_for_ready','WorkerProc.wait_for_response_handle_ready'})
POINTS=frozenset(c+'.'+m for c,methods in TARGETS.items() for m in methods)
MAX_ROWS=2048
MAX_BYTES=2*1024**2
CODE_FILENAME='guard/adapters/vllm_initialization_sensor.py'
RUNTIME_CODE_CONTRACTS={'cpython-3.12.14':'986d4162ad54ff844f8ba71daf144548cbd94ed30b658c57518ca8fccf4e57da','cpython-3.12.3':'24cddf109ec01d401855a8b6076e753829d94b3869652388d05c85f11c0808d4'}
_BASE=None
_CALL=contextvars.ContextVar('guard_initialization_call',default=None)


def attrs(value):
    try:data=object.__getattribute__(value,'__dict__')
    except (AttributeError,TypeError):data={}
    return data if type(data) is dict else {}


def uint(value):return value if type(value) is int and 0<=value<2**63 else None


def sha(raw):return hashlib.sha256(raw).hexdigest()


def handles(value):
    if type(value) is not list or not 1<=len(value)<=64:return None
    result=[]
    for handle in value:
        data=attrs(handle);rank=uint(data.get('rank'));popen=attrs(attrs(data.get('proc')).get('_popen'))
        if rank is None or rank>63:return None
        result.append({'rank':rank,'pid':uint(popen.get('pid'))})
    if len({r['rank'] for r in result})!=len(result):return None
    return result


def checked_codes(module,base):
    path=vars(module).get('__file__');before_path=os.stat(path,follow_symlinks=False)
    if not stat.S_ISREG(before_path.st_mode):raise ValueError('INITIALIZATION_SOURCE_UNAVAILABLE')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=131072:raise ValueError
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(131073)
        after=os.fstat(fd);after_path=os.stat(path,follow_symlinks=False)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns)+(() if os.name=='nt' else (s.st_ctime_ns,))
        if (len(raw)!=before.st_size or len({identity(s) for s in (before_path,before,after,after_path)})!=1
                or before.st_ctime_ns!=after.st_ctime_ns or before_path.st_ctime_ns!=after_path.st_ctime_ns
                or sha(raw)!=SOURCE_ADDITIONS[MP]):raise ValueError('INITIALIZATION_SOURCE_UNAVAILABLE')
    finally:os.close(fd)
    result={}
    def visit(code):
        result[code.co_qualname]=base.code_digest(code)
        for value in code.co_consts:
            if isinstance(value,types.CodeType):visit(value)
    visit(compile(raw,path,'exec',dont_inherit=True,optimize=0));return result


class InitializationObservation:
    def initialization_binding(self,point,args,kwargs):
        if self.initialization_pid!=os.getpid():
            self.initialization_pid=os.getpid();self.initialization_lock=threading.RLock()
            self.initialization_serial=0;self.initialization_objects=weakref.WeakKeyDictionary()
        parent=_CALL.get();parent=parent if type(parent) is dict else None
        owner=args[0] if point=='MultiprocExecutor._init_executor' and args else None
        if owner is not None and (type(owner).__module__!=MP or type(owner).__name__!='MultiprocExecutor'):return None
        with self.initialization_lock:
            self.initialization_serial+=1;context=self._writer().context
            call=sha(':'.join(map(str,(self.config['session'],context['pid'],context['start_ticks'],context['boot_sha256'],context['pid_namespace'],self.initialization_serial))).encode())
            if owner is not None:
                group=self.initialization_objects.get(owner)
                if group is None:
                    if len(self.initialization_objects)>=256:return None
                    group=call;self.initialization_objects[owner]=group
            else:group=parent['executor_sha256'] if parent else None
        expected=None;response=None
        if point=='WorkerProc.wait_for_ready':expected=handles(args[0] if args else kwargs.get('unready_proc_handles'))
        elif point=='WorkerProc.wait_for_response_handle_ready':
            value=args[0] if args else kwargs.get('handles');response=(value.get('status') if type(value) is dict else None)
            response='READY' if type(response) is str and response=='READY' else 'other' if response is not None else None
            handle=args[1] if len(args)>1 else kwargs.get('proc_handle');expected=handles([handle])
        return {'call_sha256':call,'executor_sha256':group,'parent_call_sha256':parent['call_sha256'] if parent else None,
            'point':point,'expected_handles':expected,'response_status':response}

    def initialization_emit(self,binding,phase,args,result,kind,started,finished):
        try:
            point=binding['point'];owner=args[0] if args and point=='MultiprocExecutor._init_executor' else None
            returned=handles(result) if point=='WorkerProc.wait_for_ready' and phase=='RETURN' else handles([result]) if point=='WorkerProc.wait_for_response_handle_ready' and phase=='RETURN' else None
            workers=handles(attrs(owner).get('workers')) if owner is not None and phase!='CALL' else None
            self.emit('INITIALIZATION',{'schema':EVENT_SCHEMA,**binding,'phase':phase,'source_module':MP,'source_sha256':SOURCE_ADDITIONS[MP],
                'source_verified':self.modules.get(MP,{}).get('source_sha256')==SOURCE_ADDITIONS[MP],
                'observer_runtime':self.initialization_runtime,'observer_code_sha256':self.initialization_code,
                'returned_handles':returned,'executor_workers':workers,'world_size':uint(attrs(owner).get('world_size')),
                'exception_kind':kind,'operation_started_ns':started,'operation_finished_ns':finished,
                'failure_activation_verified':False,'cross_process_group_verified':False,'service_gate_verified':False})
        except BaseException:self.disabled_pid=os.getpid()

    def initialization_wrap(self,point,original,expected):
        observer=self
        @functools.wraps(original)
        def wrapped(*args,**kwargs):
            binding=None
            try:
                if observer.active():
                    if observer.initialization_base.code_digest(original.__code__)!=expected:
                        observer.modules.pop(MP,None);observer.emit('MODULE',{'module':MP,'verified':False})
                    else:binding=observer.initialization_binding(point,args,kwargs)
            except BaseException:observer.disabled_pid=os.getpid()
            if binding is None:return original(*args,**kwargs)
            observer.initialization_emit(binding,'CALL',args,None,None,None,None)
            try:token=_CALL.set(binding)
            except BaseException:
                observer.disabled_pid=os.getpid();return original(*args,**kwargs)
            result=None;kind=None;returned=False;started=time.monotonic_ns()
            try:
                result=original(*args,**kwargs);returned=True;return result
            except BaseException as error:
                kind='timeout' if type(error) is TimeoutError else 'exception' if isinstance(error,Exception) else 'base-exception'
                raise
            finally:
                observer.initialization_emit(binding,'RETURN' if returned else 'RAISE',args,result,kind,started,time.monotonic_ns())
                try:_CALL.reset(token)
                except BaseException:observer.disabled_pid=os.getpid()
        return wrapped


def relative_code(code):
    return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(relative_code(v) if isinstance(v,types.CodeType) else v for v in code.co_consts))


def code_contract(base):
    functions=(attrs,uint,sha,handles,checked_codes,InitializationObservation.initialization_binding,
        InitializationObservation.initialization_emit,InitializationObservation.initialization_wrap)
    return sha(''.join(base.code_digest(relative_code(fn.__code__)) for fn in functions).encode())


def configure(base):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if sys.implementation.name!='cpython' or sys.flags.optimize!=0 or expected is None or code_contract(base)!=expected:
        raise ValueError('INITIALIZATION_OBSERVER_CODE_UNAVAILABLE')
    base.PROFILE,base.SCHEMA=PROFILE,SCHEMA;base.__file__=__file__;base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES
    base.SOURCE_SHA={**base.SOURCE_SHA,**SOURCE_ADDITIONS}
    class InitializationObserver(InitializationObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.initialization_base=base;self.initialization_runtime=runtime;self.initialization_code=expected
            self.initialization_pid=os.getpid();self.initialization_lock=threading.RLock();self.initialization_serial=0
            self.initialization_objects=weakref.WeakKeyDictionary()
        def instrument(self,module):
            if module.__name__!=MP:return super().instrument(module)
            codes=checked_codes(module,base);changes=[]
            for cls_name,methods in TARGETS.items():
                cls=vars(module).get(cls_name)
                if not isinstance(cls,type):raise ValueError('INITIALIZATION_CLASS_UNAVAILABLE')
                for method in methods:
                    descriptor=vars(cls).get(method);point=cls_name+'.'+method
                    if point in STATIC_POINTS:
                        if type(descriptor) is not staticmethod:raise ValueError('INITIALIZATION_DESCRIPTOR_UNAVAILABLE')
                        original=descriptor.__func__
                    else:original=descriptor
                    if not inspect.isfunction(original) or base.code_digest(original.__code__)!=codes.get(point):raise ValueError('INITIALIZATION_TARGET_CODE_UNAVAILABLE')
                    wrapped=self.initialization_wrap(point,original,codes[point])
                    changes.append((cls,method,staticmethod(wrapped) if point in STATIC_POINTS else wrapped))
            super().instrument(module)
            for cls,method,wrapped in changes:setattr(cls,method,wrapped)
    base.Observer=InitializationObserver;return base


def _private_base():
    directory=os.open('/guard-lifecycle',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:fd=os.open('guard_lifecycle_base.py',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
    finally:os.close(directory)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=262144:raise ValueError
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(262145)
        after=os.fstat(fd)
        if len(raw)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or sha(raw)!=BASE8_SHA256:raise ValueError
    finally:os.close(fd)
    base=types.ModuleType('_guard_initialization_base');base.__file__='/guard-lifecycle/guard_lifecycle_base.py'
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
