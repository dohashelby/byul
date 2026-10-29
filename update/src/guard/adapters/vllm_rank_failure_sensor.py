"""Draft opt-in selected mp failure facts over immutable native8.

No fault injection, callback replacement, polling, RPC edits or worker cleanup.
Four fault activations and upper receipt remain independently unverified.
"""
from __future__ import annotations
import contextvars,functools,hashlib,inspect,os,stat,sys,threading,time,types,weakref
from concurrent.futures import Future

PROFILE='vllm-native-lifecycle-metadata/13'
SCHEMA='guard-native-lifecycle-events/13'
EVENT_SCHEMA='guard-native-rank-failure/1'
COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
BASE8_SHA256='2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd'
MP='vllm.v1.executor.multiproc_executor'
CORE='vllm.v1.engine.core'
SOURCE_ADDITIONS={MP:'11a1554380d1efa516bb481416c798798d5839f38ac3b0f379ef4b8b547f4d1b'}
SOURCES={**SOURCE_ADDITIONS,CORE:'f116962c35d9d312b20b0b76c34a8cdaa6d3f1d31f2036db4e123957d2d1555b'}
TARGETS={MP:{'MultiprocExecutor':('execute_model','collective_rpc','shutdown'),
 'FutureWrapper':('__init__','result','_wait_for_response'),
 'WorkerProc':('_execute_worker_rpc','handle_output','enqueue_output')},
 CORE:{'EngineCoreProc':('_handle_client_request','_send_engine_dead')}}
POINTS=frozenset(cls+'.'+method for classes in TARGETS.values() for cls,methods in classes.items() for method in methods)
MAX_OBJECTS=256
MAX_ROWS=2048
MAX_BYTES=2*1024**2
CODE_FILENAME='guard/adapters/vllm_rank_failure_sensor.py'
# Own completed source must be reviewed offline before any installation/freeze.
RUNTIME_CODE_CONTRACTS={'cpython-3.12.14':'31c2a7bf9d0b72322e731905a7d373d595957c638245ecef2b16dd70cc109ad3','cpython-3.12.3':'76c5d6932a22838fbcd8c49a39f98e6170b1b7591b2f2b11d860f996874de972'}
_BASE=None
_CALL=contextvars.ContextVar('guard_rank_failure_call',default=None)


def attrs(value):
    try:data=object.__getattribute__(value,'__dict__')
    except (AttributeError,TypeError):data={}
    return data if type(data) is dict else {}


def uint(value):return value if type(value) is int and 0<=value<2**63 else None


def sha(raw):return hashlib.sha256(raw).hexdigest()


def native_ids(scheduler):
    values=attrs(scheduler).get('num_scheduled_tokens')
    if type(values) is not dict or len(values)>8:return ()
    if any(type(k) is not str or not 0<len(k)<=512 for k in values):return ()
    return tuple(sha(k.encode()) for k in values)


def exact_mp(value):
    return type(value).__module__==MP and type(value).__name__=='MultiprocExecutor'


def checked_codes(module,base):
    path=vars(module).get('__file__');path_before=os.stat(path,follow_symlinks=False)
    if not stat.S_ISREG(path_before.st_mode):raise ValueError('RANK_SOURCE_UNAVAILABLE')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=131072:raise ValueError('RANK_SOURCE_UNAVAILABLE')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(131073)
        after=os.fstat(fd);path_after=os.stat(path,follow_symlinks=False)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns)+(() if os.name=='nt' else (s.st_ctime_ns,))
        if (len(raw)!=before.st_size or len({identity(s) for s in (path_before,before,after,path_after)})!=1
                or before.st_ctime_ns!=after.st_ctime_ns or path_before.st_ctime_ns!=path_after.st_ctime_ns
                or sha(raw)!=SOURCES[module.__name__]):raise ValueError('RANK_SOURCE_UNAVAILABLE')
    finally:os.close(fd)
    root=compile(raw,path,'exec',dont_inherit=True,optimize=0);result={}
    def visit(code):
        result[code.co_qualname]=base.code_digest(code)
        for value in code.co_consts:
            if isinstance(value,types.CodeType):visit(value)
    visit(root);return result


class RankObservation:
    def rank_reset(self):
        if self.rank_pid!=os.getpid():
            self.rank_pid=os.getpid();self.rank_lock=threading.RLock();self.rank_serial=0
            self.rank_objects=weakref.WeakKeyDictionary();self.rank_futures=weakref.WeakKeyDictionary()

    def rank_entry(self,owner,*,create=True):
        self.rank_reset()
        if owner is None:return None
        try:
            with self.rank_lock:
                entry=self.rank_objects.get(owner)
                if entry is None and create:
                    if len(self.rank_objects)>=MAX_OBJECTS:return None
                    self.rank_serial+=1;context=self._writer().context
                    identity=(self.config['session'],context['pid'],context['start_ticks'],context['boot_sha256'],context['pid_namespace'],self.rank_serial)
                    entry={'owner':sha(':'.join(map(str,identity)).encode()),'calls':0}
                    self.rank_objects[owner]=entry
                return entry
        except (TypeError,RuntimeError):return None

    def rank_context(self,module,point,original,args,kwargs,caller):
        owner=args[0] if args else None;data=attrs(owner);parent=_CALL.get();parent=parent if type(parent) is dict else None
        entry=None;group=None;requests=();method=None;role=None;reply_rank=None;non_block=None;marker=False
        monitor={'verified':False,'rank':None,'pid':None,'exit_abs':None,'exit_sign':None};worker_output_is_exception=None
        if point.startswith('MultiprocExecutor.'):
            if not exact_mp(owner):return None
            entry=self.rank_entry(owner,create=point!='MultiprocExecutor.shutdown')
            if entry is None:return None
            group=entry['owner'];role='executor'
            if point=='MultiprocExecutor.execute_model':
                scheduler=args[1] if len(args)>1 else kwargs.get('scheduler_output')
                requests=native_ids(scheduler);method='execute_model';reply_rank=uint(data.get('output_rank'))
                non_block=args[2] if len(args)>2 else kwargs.get('non_block',False)
            elif point=='MultiprocExecutor.collective_rpc':
                method=args[1] if len(args)>1 else kwargs.get('method')
                if type(method) is not str or method!='execute_model':return None
                rpc_args=args[3] if len(args)>3 else kwargs.get('args',())
                requests=native_ids(rpc_args[0]) if type(rpc_args) is tuple and rpc_args else ()
                reply_rank=uint(args[6] if len(args)>6 else kwargs.get('unique_reply_rank'))
                non_block=args[5] if len(args)>5 else kwargs.get('non_block',False)
            else:
                expected=self.rank_codes.get(module,{}).get('MultiprocExecutor.start_worker_monitor.<locals>.monitor_workers')
                if (caller is not None and expected is not None and self.rank_base.code_digest(caller.f_code)==expected
                        and caller.f_locals.get('_self') is owner and data.get('is_failed') is True):
                    monitor['verified']=True;proc=caller.f_locals.get('proc');workers=data.get('workers')
                    if type(workers) is list and len(workers)<=64:
                        found=[h for h in workers if attrs(h).get('proc') is proc]
                        if len(found)==1:
                            monitor['rank']=uint(attrs(found[0]).get('rank'));popen=attrs(proc).get('_popen');p=attrs(popen)
                            monitor['pid']=uint(p.get('pid'));exit_code=p.get('returncode')
                            if type(exit_code) is int and -255<=exit_code<=255:
                                monitor['exit_abs']=abs(exit_code);monitor['exit_sign']='negative' if exit_code<0 else 'positive' if exit_code>0 else 'zero'
        elif point.startswith('FutureWrapper.'):
            role='future'
            if point=='FutureWrapper.__init__':
                if not parent or parent['point']!='MultiprocExecutor.collective_rpc':return None
                binding={k:parent[k] for k in ('group','requests','method','reply_rank','non_block')}
                entry=self.rank_entry(owner)
                if entry is None:return None
                with self.rank_lock:self.rank_futures[owner]=binding
            else:
                with self.rank_lock:binding=self.rank_futures.get(owner)
                if binding is None:return None
                entry=self.rank_entry(owner,create=False)
                if entry is None:return None
            group,requests,method,reply_rank,non_block=(binding[k] for k in ('group','requests','method','reply_rank','non_block'))
        elif point.startswith('WorkerProc.'):
            role='worker';entry=self.rank_entry(owner)
            if entry is None:return None
            if point=='WorkerProc._execute_worker_rpc':
                rpc=args[1] if len(args)>1 else kwargs.get('rpc_request')
                if type(rpc) is not tuple or len(rpc)!=4 or type(rpc[0]) is not str or rpc[0]!='execute_model':return None
                method=rpc[0];rpc_args=rpc[1];reply_rank=uint(rpc[3])
                requests=native_ids(rpc_args[0]) if type(rpc_args) is tuple and rpc_args else ()
            else:
                if not parent or parent['owner']!=entry['owner'] or parent['method']!='execute_model':return None
                requests=parent['requests'];method=parent['method'];reply_rank=parent['reply_rank']
                if len(args)>1 or 'output' in kwargs:
                    worker_output_is_exception=isinstance(args[1] if len(args)>1 else kwargs['output'],Exception)
        else:
            executor=data.get('model_executor')
            if not exact_mp(executor):return None
            role='core';entry=self.rank_entry(owner);executor_entry=self.rank_entry(executor)
            if entry is None or executor_entry is None:return None
            group=executor_entry['owner']
            if point=='EngineCoreProc._handle_client_request':
                actual=args[1] if len(args)>1 else kwargs.get('request_type')
                enum=original.__globals__.get('EngineCoreRequestType')
                expected=vars(enum).get('EXECUTOR_FAILED') if isinstance(enum,type) else None
                if expected is None or actual is not expected:return None
                marker=True
        with self.rank_lock:entry['calls']+=1;serial=entry['calls']
        parent_call=parent['call'] if parent and (parent['group']==group and group is not None or parent['owner']==entry['owner']) else None
        return {'owner':entry['owner'],'group':group,'call':sha((entry['owner']+':'+str(serial)).encode()),
            'parent':parent_call,'point':point,'role':role,'requests':requests,'method':method,'reply_rank':reply_rank,
            'non_block':non_block if type(non_block) is bool else None,'marker':marker,'monitor':monitor,
            'worker_output_is_exception':worker_output_is_exception}

    def rank_snapshot(self,module,binding,phase,args,result,error_kind,started,finished):
        owner=args[0] if args else None;data=attrs(owner);source=self.modules.get(module,{})
        state=data.get('_state');state=state if type(state) is str and state in {'PENDING','RUNNING','FINISHED','CANCELLED','CANCELLED_AND_NOTIFIED'} else None
        monitor=binding['monitor']
        return {'schema':EVENT_SCHEMA,'point':binding['point'],'phase':phase,'source_module':module,'source_sha256':SOURCES[module],
            'source_verified':source.get('source_sha256')==SOURCES[module],'observer_runtime':self.rank_runtime,'observer_code_sha256':self.rank_code,
            'owner_sha256':binding['owner'],'executor_sha256':binding['group'],'call_sha256':binding['call'],'parent_call_sha256':binding['parent'],
            'role':binding['role'],'native_request_sha256':list(binding['requests']),'rpc_method':binding['method'],
            'rank':uint(data.get('rank')) if binding['role']=='worker' else None,'world_size':uint(data.get('world_size')) if binding['role']=='executor' else None,
            'configured_output_rank':uint(data.get('output_rank')) if binding['role']=='executor' else None,
            'selected_reply_rank':binding['reply_rank'],'non_block':binding['non_block'],
            'executor_is_failed':data.get('is_failed') if binding['role']=='executor' and type(data.get('is_failed')) is bool else None,
            'worker_output_is_exception':binding['worker_output_is_exception'],
            'future_state':state if binding['role']=='future' else None,
            'future_has_exception':data.get('_exception') is not None if binding['role']=='future' and '_exception' in data else None,
            'returned_kind':('none' if result is None else 'future' if isinstance(result,Future) else 'value') if phase=='RETURN' else None,
            'exception_kind':error_kind if phase=='RAISE' else None,'executor_failed_marker_observed':binding['marker'],
            'monitor_caller_verified':monitor['verified'],'monitor_dead_rank':monitor['rank'],'monitor_dead_pid':monitor['pid'],
            'monitor_exit_code_abs':monitor['exit_abs'],'monitor_exit_code_sign':monitor['exit_sign'],
            'operation_started_ns':started,'operation_finished_ns':finished,'fault_activation_verified':False,
            'cross_process_group_verified':False,'upper_client_receipt_verified':False}

    def rank_emit(self,*args):
        try:self.emit('RANK_FAILURE',self.rank_snapshot(*args))
        except BaseException:self.disabled_pid=os.getpid()

    def rank_wrap(self,module,point,original,expected):
        observer=self
        @functools.wraps(original)
        def wrapped(*args,**kwargs):
            binding=None
            try:
                if observer.active():
                    if observer.rank_base.code_digest(original.__code__)!=expected:
                        observer.modules.pop(module,None);observer.emit('MODULE',{'module':module,'verified':False})
                    else:binding=observer.rank_context(module,point,original,args,kwargs,sys._getframe(1))
            except BaseException:observer.disabled_pid=os.getpid()
            if binding is None:return original(*args,**kwargs)
            observer.rank_emit(module,binding,'CALL',args,None,None,None,None)
            try:token=_CALL.set(binding)
            except BaseException:
                observer.disabled_pid=os.getpid();return original(*args,**kwargs)
            result=None;error_kind=None;returned=False;started=time.monotonic_ns()
            try:
                result=original(*args,**kwargs);returned=True;return result
            except BaseException as error:
                error_kind='timeout' if type(error) is TimeoutError else 'exception' if isinstance(error,Exception) else 'base-exception'
                raise
            finally:
                finished=time.monotonic_ns();observer.rank_emit(module,binding,'RETURN' if returned else 'RAISE',args,result,error_kind,started,finished)
                try:_CALL.reset(token)
                except BaseException:observer.disabled_pid=os.getpid()
        return wrapped


def relative_code(code):
    return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(relative_code(v) if isinstance(v,types.CodeType) else v for v in code.co_consts))


def code_contract(base):
    functions=(attrs,uint,sha,native_ids,exact_mp,checked_codes,RankObservation.rank_reset,RankObservation.rank_entry,
        RankObservation.rank_context,RankObservation.rank_snapshot,RankObservation.rank_emit,RankObservation.rank_wrap)
    return sha(''.join(base.code_digest(relative_code(fn.__code__)) for fn in functions).encode())


def configure(base):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if sys.implementation.name!='cpython' or sys.flags.optimize!=0 or expected is None or code_contract(base)!=expected:
        raise ValueError('RANK_FAILURE_OBSERVER_CODE_UNAVAILABLE')
    base.PROFILE,base.SCHEMA=PROFILE,SCHEMA;base.__file__=__file__;base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES
    base.SOURCE_SHA={**base.SOURCE_SHA,**SOURCE_ADDITIONS}
    class RankObserver(RankObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.rank_base=base;self.rank_runtime=runtime;self.rank_code=expected
            self.rank_pid=os.getpid();self.rank_lock=threading.RLock();self.rank_serial=0
            self.rank_objects=weakref.WeakKeyDictionary();self.rank_futures=weakref.WeakKeyDictionary();self.rank_codes={}
        def instrument(self,module):
            name=module.__name__
            if name not in TARGETS:return super().instrument(module)
            codes=checked_codes(module,base);changes=[]
            for cls_name,methods in TARGETS[name].items():
                cls=vars(module).get(cls_name)
                if not isinstance(cls,type):raise ValueError('RANK_FAILURE_CLASS_UNAVAILABLE')
                for method in methods:
                    original=vars(cls).get(method);point=cls_name+'.'+method
                    if not inspect.isfunction(original) or base.code_digest(original.__code__)!=codes.get(point):raise ValueError('RANK_FAILURE_TARGET_CODE_UNAVAILABLE')
                    changes.append((cls,method,self.rank_wrap(name,point,original,codes[point])))
            super().instrument(module);self.rank_codes[name]=codes
            for cls,method,wrapped in changes:setattr(cls,method,wrapped)
    base.Observer=RankObserver;return base


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
    base=types.ModuleType('_guard_rank_failure_base');base.__file__='/guard-lifecycle/guard_lifecycle_base.py'
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


def seal(path='/guard-lifecycle/settings.json'):
    return (_BASE if _BASE is not None else _private_base()).seal(path)


if __name__=='__main__':
    if sys.argv[1:]!=['--seal']:raise SystemExit('RANK_FAILURE_SEAL_ARGUMENTS_REQUIRED')
    try:seal()
    except Exception:raise SystemExit('RANK_FAILURE_SEAL_UNAVAILABLE') from None
