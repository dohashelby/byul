"""Opt-in ordinary weight iterator steps over immutable native8.

Yields from the reviewed parameter generator occur after its selected callback
returned. Neither yields nor exhaustion certify file reads, tensor storage,
backend completion, worker assignment or an approval authority.
"""
from __future__ import annotations
import contextvars,functools,hashlib,inspect,os,stat,sys,threading,time,types

PROFILE='vllm-native-lifecycle-metadata/16'
SCHEMA='guard-native-lifecycle-events/16'
EVENT_SCHEMA='guard-native-weight-iteration/1'
COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
BASE8_SHA256='2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd'
DEFAULT='vllm.model_executor.model_loader.default_loader'
WEIGHTS='vllm.model_executor.model_loader.weight_utils'
MODELS='vllm.model_executor.models.utils'
SOURCE_ADDITIONS={DEFAULT:'d17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9',
 WEIGHTS:'7d2573931b9684147b17018c1f6de318eea1c105556ffea03154201a72418afd',
 MODELS:'7171b6347b3cbab213df299fa317094ac6c9b2b397daf6a2ec93ff3a152b0e75'}
TARGETS={DEFAULT:{'DefaultModelLoader':('get_all_weights',)},MODELS:{'AutoWeightsLoader':('_load_module','_load_param')}}
FUNCTIONS={WEIGHTS:('pt_weights_iterator','safetensors_weights_iterator')}
POINT_SOURCES={**{cls+'.'+method:module for module,classes in TARGETS.items() for cls,methods in classes.items() for method in methods},
 **{'weight_utils.'+name:module for module,names in FUNCTIONS.items() for name in names}}
POINTS=frozenset(POINT_SOURCES)
PHASES=frozenset({'FACTORY','STEP_BEGIN','STEP_YIELD','STEP_STOP','STEP_RAISE','STEP_CLOSED'})
MAX_ROWS=2048
MAX_BYTES=2*1024**2
CODE_FILENAME='guard/adapters/vllm_weight_iteration_sensor.py'
# Filled from the reviewed source compiler before this profile can configure.
RUNTIME_CODE_CONTRACTS={'cpython-3.12.14': 'b30ec8ba3447ba218cb37838a55af0d28893a5e4518e7421de119e22ff7426f9', 'cpython-3.12.3': 'e93012cf2ef2f74defbb675b7aa2df03c37c243b84b09b42588371b926419626'}
RUNTIME_POINT_CODES={'cpython-3.12.14': {'DefaultModelLoader.get_all_weights': '1407f8e95362651176f9fa28d60e9f46bc2366ed573a0d38ca7068b2645bacfc', 'weight_utils.pt_weights_iterator': '47a02531b7b37048864cc6d2453192c0bb7ffc5f0ea3a851c400119c79643f59', 'weight_utils.safetensors_weights_iterator': '2c608d4c28a06d0b992f8d57628532f113e82e865d8ca832222f4725d66644b7', 'AutoWeightsLoader._load_module': 'c4c8cda0f8359316cdd694bef53823f7e230f3446fa8fc7d7f1984c22c37a8f5', 'AutoWeightsLoader._load_param': '63b6cc08908e62b5e5dd15d771fde47810312fc6d11c641628561dd51d42a701'}, 'cpython-3.12.3': {'DefaultModelLoader.get_all_weights': 'a831c35f92523604619a266d0234df82a1c3e14e06df51ab71367d136b1f33f4', 'weight_utils.safetensors_weights_iterator': '604f0b08bf48e4eda5d2951377d1cf41e23d35d0fcce8d6bfcd362f7d8e9623a', 'weight_utils.pt_weights_iterator': 'f9e0959ffa3e06faa269ee478583081f4dfc5e13ab09e719b6f6ec2162ad0056', 'AutoWeightsLoader._load_module': '3fc795e4b444af201b74fe8671f98f9f39fd63a040337ec8ffc3c1a8d966a276', 'AutoWeightsLoader._load_param': 'e08968c2706a1c06d7f61f000710909805a86d3befeb354d1ec0d7804e30c934'}}
_STEP=contextvars.ContextVar('guard_weight_iteration_step',default=None)
_BASE=None


def sha(raw):return hashlib.sha256(raw).hexdigest()


def normalized_code(code,filename):
    return code.replace(co_filename=filename,co_consts=tuple(normalized_code(v,filename) if isinstance(v,types.CodeType) else v for v in code.co_consts))


def method_code(base,code,module):return base.code_digest(normalized_code(code,module.replace('.','/')+'.py'))


def checked_codes(module,base):
    name=module.__name__;path=vars(module).get('__file__');first=os.stat(path,follow_symlinks=False)
    if not stat.S_ISREG(first.st_mode):raise ValueError('WEIGHT_ITERATION_SOURCE_UNAVAILABLE')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=131072:raise ValueError
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(131073)
        after=os.fstat(fd);last=os.stat(path,follow_symlinks=False)
        identity=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_size,v.st_mtime_ns)+(() if os.name=='nt' else (v.st_ctime_ns,))
        if (len(raw)!=before.st_size or len({identity(v) for v in (first,before,after,last)})!=1
                or first.st_ctime_ns!=last.st_ctime_ns or before.st_ctime_ns!=after.st_ctime_ns
                or sha(raw)!=SOURCE_ADDITIONS[name]):raise ValueError('WEIGHT_ITERATION_SOURCE_UNAVAILABLE')
    finally:os.close(fd)
    result={}
    def visit(code):
        result[code.co_qualname]=method_code(base,code,name)
        for value in code.co_consts:
            if isinstance(value,types.CodeType):visit(value)
    visit(compile(raw,path,'exec',dont_inherit=True,optimize=0));return result


class IterationObservation:
    def weight_nonce(self):
        context=self._writer().context
        if self.weight_pid!=os.getpid():
            self.weight_pid=os.getpid();self.weight_serial=0;self.weight_lock=threading.RLock()
        with self.weight_lock:
            self.weight_serial+=1
            return sha((':'.join(map(str,('weight-iteration',self.config['session'],context['pid'],context['start_ticks'],
                context['boot_sha256'],context['pid_namespace'],self.weight_serial)))).encode())

    def weight_begin(self,point,original,expected,args,kwargs,iterator):
        if not self.active():return None
        module=POINT_SOURCES[point]
        if (self.modules.get(module,{}).get('source_sha256')!=SOURCE_ADDITIONS[module]
                or method_code(self.weight_base,original.__code__,module)!=expected
                or type(iterator) is not types.GeneratorType
                or method_code(self.weight_base,iterator.gi_code,module)!=expected):
            self.modules.pop(module,None);self.emit('MODULE',{'module':module,'verified':False});return None
        parent=_STEP.get();parent=parent if type(parent) is dict and parent.get('pid')==os.getpid() and not parent.get('closed') else None
        root=parent.get('root') if parent else None;hint=None;parameter=None
        if point=='AutoWeightsLoader._load_module':
            model=args[2] if len(args)>2 else kwargs.get('module');hint=self.model_reference(model)
        elif point=='DefaultModelLoader.get_all_weights':
            model=args[2] if len(args)>2 else kwargs.get('model');hint=self.model_reference(model)
        elif point=='AutoWeightsLoader._load_param':
            param=args[2] if len(args)>2 else kwargs.get('param');parameter=self.model_reference(param)
        call=self.weight_nonce()
        loader=next((frame for frame in reversed(self.weight_base._CHAIN.get()) if frame.get('point')=='DefaultModelLoader.load_weights' and frame.get('_weight_pid')==os.getpid()),{})
        loader_call=loader.get('weight_call_sha256');loader_model=loader.get('weight_model_sha256')
        if point=='AutoWeightsLoader._load_module' and root is None:
            root={'call':call,'model':hint,'pid':os.getpid(),'closed':False,'loader_call':loader_call,'loader_model':loader_model}
        return {'point':point,'call':call,'parent':parent['call'] if parent else None,'root':root,'hint':hint,'parameter':parameter,
            'loader_call':loader_call,'loader_model':loader_model,
            'pid':os.getpid(),'closed':False,'operations':0,'yields':0,'method_code':expected,'iterator_code':iterator.gi_code}

    def weight_emit(self,binding,phase,action=None,started=None,finished=None,item=None,exception=None,closed=False):
        try:
            if binding['pid']!=os.getpid():return
            root=binding['root'];name=None;tensor=None
            if phase=='STEP_YIELD':
                value=item[0] if type(item) is tuple and len(item)==2 else item
                if type(value) is str and 0<len(value)<=4096:name=sha(value.encode())
                if type(item) is tuple and len(item)==2:tensor=self.model_reference(item[1])
            module=POINT_SOURCES[binding['point']]
            self.emit('WEIGHT_ITERATION',{'schema':EVENT_SCHEMA,'point':binding['point'],'phase':phase,'source_module':module,
                'source_sha256':SOURCE_ADDITIONS[module],'source_method_code_sha256':binding['method_code'],
                'source_verified':self.modules.get(module,{}).get('source_sha256')==SOURCE_ADDITIONS[module],
                'observer_runtime':self.weight_runtime,'observer_code_sha256':self.weight_code,'call_sha256':binding['call'],
                'parent_call_sha256':binding['parent'],'root_load_sha256':root['call'] if root else None,
                'model_object_sha256':root['model'] if root else None,'factory_model_hint_sha256':binding['hint'],
                'loader_call_sha256':root['loader_call'] if root else binding['loader_call'],
                'loader_model_sha256':root['loader_model'] if root else binding['loader_model'],
                'parameter_object_sha256':binding['parameter'],'operation_index':binding['operations'],'yield_index':binding['yields'],
                'action':action,'operation_started_ns':started,'operation_finished_ns':finished,'yielded_name_sha256':name,
                'yielded_tensor_object_sha256':tensor,'exception_kind':exception,'iterator_closed_observed':closed,
                'root_load_active_observed':bool(root and root['pid']==os.getpid() and not root['closed']),
                'parameter_callback_returned_observed':bool(phase=='STEP_YIELD' and binding['point']=='AutoWeightsLoader._load_param' and name),
                'worker_assignment_verified':False,'file_read_verified':False,'tensor_storage_applied_verified':False,
                'callback_implementation_verified':False,'producer_authenticated':False})
        except BaseException:self.disabled_pid=os.getpid()

    def weight_wrap(self,point,original,expected,inner):
        observer=self
        @functools.wraps(original)
        def wrapped(*args,**kwargs):
            iterator=inner(*args,**kwargs)
            try:binding=observer.weight_begin(point,original,expected,args,kwargs,iterator)
            except BaseException:observer.disabled_pid=os.getpid();binding=None
            if binding is None:return iterator
            observer.weight_emit(binding,'FACTORY')
            proxy=IterationProxy(observer,binding,iterator)
            def relay():return (yield from proxy)
            return relay()
        return wrapped


class IterationProxy:
    def __init__(self,observer,binding,iterator):self.observer,self.binding,self.iterator=observer,binding,iterator
    def __iter__(self):return self
    def __next__(self):return self.operate('NEXT',next,self.iterator)
    def send(self,value):return self.operate('SEND',self.iterator.send,value)
    def throw(self,*args):return self.operate('THROW',self.iterator.throw,*args)
    def close(self):return self.operate('CLOSE',self.iterator.close)

    def operate(self,action,function,*args):
        observer,binding=self.observer,self.binding;token=None;observing=False;started=None
        try:
            observing=observer.active() and binding['pid']==os.getpid() and self.iterator.gi_code is binding['iterator_code']
            if observing:
                parent=_STEP.get()
                if binding['root'] is None and type(parent) is dict and parent.get('pid')==os.getpid() and not parent.get('closed'):
                    root=parent.get('root')
                    if root and not root['closed'] and (binding['hint'] is None or binding['hint']==root['model']):
                        binding['root']=root;binding['parent']=parent['call']
                binding['operations']+=1;started=time.monotonic_ns()
                observer.weight_emit(binding,'STEP_BEGIN',action,started)
                token=_STEP.set(binding)
        except BaseException:observer.disabled_pid=os.getpid();observing=False
        phase='STEP_CLOSED' if action=='CLOSE' else 'STEP_YIELD';item=None;kind=None
        try:
            item=function(*args)
            if action!='CLOSE':binding['yields']+=1
            return item
        except StopIteration:phase='STEP_STOP';raise
        except BaseException as error:
            phase='STEP_RAISE';kind='exception' if isinstance(error,Exception) else 'base-exception';raise
        finally:
            if token is not None:
                try:_STEP.reset(token)
                except BaseException:observer.disabled_pid=os.getpid()
            if observing:
                try:
                    closed=self.iterator.gi_frame is None
                    observer.weight_emit(binding,phase,action,started,time.monotonic_ns(),item,kind,closed)
                    if closed:
                        binding['closed']=True
                        if binding['root'] and binding['root']['call']==binding['call']:binding['root']['closed']=True
                except BaseException:observer.disabled_pid=os.getpid()


def code_contract(base):
    functions=(sha,normalized_code,method_code,checked_codes,IterationObservation.weight_nonce,IterationObservation.weight_begin,
        IterationObservation.weight_emit,IterationObservation.weight_wrap,IterationProxy.__init__,IterationProxy.__iter__,IterationProxy.__next__,
        IterationProxy.send,IterationProxy.throw,IterationProxy.close,IterationProxy.operate,configure)
    return sha(''.join(base.code_digest(normalized_code(fn.__code__,CODE_FILENAME)) for fn in functions).encode())


def configure(base):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if (sys.implementation.name!='cpython' or sys.flags.optimize!=0 or expected is None or code_contract(base)!=expected
            or set(RUNTIME_POINT_CODES.get(runtime,{}))!=POINTS):raise ValueError('WEIGHT_ITERATION_OBSERVER_CODE_UNAVAILABLE')
    base.PROFILE,base.SCHEMA=PROFILE,SCHEMA;base.__file__=__file__;base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES
    if any(key in base.SOURCE_SHA and base.SOURCE_SHA[key]!=value for key,value in SOURCE_ADDITIONS.items()):raise ValueError('WEIGHT_ITERATION_SOURCE_CONFLICT')
    base.SOURCE_SHA={**base.SOURCE_SHA,**SOURCE_ADDITIONS}
    class WeightObserver(IterationObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.weight_base=base;self.weight_runtime=runtime;self.weight_code=expected
            self.weight_pid=os.getpid();self.weight_serial=0;self.weight_lock=threading.RLock()
        def instrument(self,module):
            name=module.__name__
            if name not in SOURCE_ADDITIONS:return super().instrument(module)
            codes=checked_codes(module,base);targets=[]
            for cls_name,methods in TARGETS.get(name,{}).items():
                cls=vars(module).get(cls_name)
                if not isinstance(cls,type):raise ValueError('WEIGHT_ITERATION_CLASS_UNAVAILABLE')
                targets.extend((cls,method,cls_name+'.'+method) for method in methods)
            targets.extend((module,method,'weight_utils.'+method) for method in FUNCTIONS.get(name,()))
            originals=[]
            for obj,key,point in targets:
                original=vars(obj).get(key);qualified=point.removeprefix('weight_utils.')
                expected_method=RUNTIME_POINT_CODES[runtime][point]
                if (not inspect.isfunction(original) or not inspect.isgeneratorfunction(original)
                        or method_code(base,original.__code__,name)!=codes.get(qualified)
                        or codes.get(qualified)!=expected_method):raise ValueError('WEIGHT_ITERATION_METHOD_CODE_UNAVAILABLE')
                originals.append((obj,key,point,original,expected_method))
            super().instrument(module)
            changes=[]
            for obj,key,point,original,expected_method in originals:
                inner=vars(obj).get(key)
                if inner is not original and getattr(inner,'__wrapped__',None) is not original:raise ValueError('WEIGHT_ITERATION_BASE_WRAPPER_UNAVAILABLE')
                changes.append((obj,key,self.weight_wrap(point,original,expected_method,inner)))
            for obj,key,wrapped in changes:setattr(obj,key,wrapped)
    base.Observer=WeightObserver
    return base


def _private_base():
    path='/guard-lifecycle/guard_lifecycle_base.py';fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=262144:raise ValueError
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(262145)
        after=os.fstat(fd)
        if len(raw)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or sha(raw)!=BASE8_SHA256:raise ValueError
    finally:os.close(fd)
    base=types.ModuleType('_guard_weight_iteration_base');base.__file__='/guard-lifecycle/guard_lifecycle_base.py'
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
