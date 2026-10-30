"""Opt-in ordinary safe_open boundaries and minimized same-process mapping hints.

No model contents, tensor values, raw maps/addresses or extra model reads are
collected. Mapping/file/function return is never worker, approval or tensor proof.
"""
from __future__ import annotations
import hashlib,inspect,json,os,re,stat,sys,time,types

PROFILE='vllm-native-lifecycle-metadata/18'
SCHEMA='guard-native-lifecycle-events/18'
EVENT_SCHEMA='guard-native-safe-open-mapping/1'
COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
BASE8_SHA256='2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd'
ITERATION16_SHA256='e7494edaa0a512201652228acc73ac13621cb40d938e6f23b6d4eaba73d05bc6'
SCOPE17_SHA256='7729661d6611e40f527760b3029822bb9cb690f8a48931aa9e9c9fb00cab08e5'
WEIGHTS='vllm.model_executor.model_loader.weight_utils'
POINT='weight_utils.safetensors_weights_iterator'
PHASES=frozenset({'OPEN_RETURN','OPEN_RAISE','ENTER_RETURN','ENTER_RAISE','TENSOR_RETURN','TENSOR_RAISE','EXIT_RETURN','EXIT_RAISE','LIBRARY_UNAVAILABLE'})
LIBRARY_FILES={'__init__.py':'92c988289b8c225a92f25e487ad5d7a1eb7e326eec0fedfc6b75e6f04d85e32f',
 'torch.py':'f3f476d1f8c04fe65fa3797426556a0b7afa43f8c4db9db6b799c7cf84748f3d',
 '_safetensors_rust.abi3.so':'e6f17a9e9846bc2bc4ad94cc5431746b59785de3d2681ef2666e8890ae192dfb'}
MAX_ROWS=2048;MAX_BYTES=2*1024**2;MAX_MAP_BYTES=2*1024**2;MAX_MAP_ROWS=16384;MAX_MATCHES=32;MAX_REGIONS=256;MAX_CONTEXTS=256
CODE_FILENAME='guard/adapters/vllm_safe_open_mapping_sensor.py'
RUNTIME_CODE_CONTRACTS={'cpython-3.12.14':'10f2126f05c92bc289b80fc3159e7a1a958de56404a140031fd9e7b34604ca05',
 'cpython-3.12.3':'0cb277ef789ccb6431408d06223472c9a7846342955496840ef606c2cd63eb7c'}
TRUTH=('worker_assignment_verified','approved_model_file_verified','producer_authenticated','tensor_storage_applied_verified',
 'model_bytes_read_verified','native_loaded_memory_verified','mapping_release_verified','scope_complete')
_BASE=None


def sha(raw):return hashlib.sha256(raw).hexdigest()


def fingerprint(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def artifact(path,expected,limit=4*1024**2):
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=limit:raise ValueError
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(limit+1)
        after=os.fstat(fd);identity=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
        if len(raw)!=before.st_size or identity(before)!=identity(after) or sha(raw)!=expected:raise ValueError
        return raw,identity(after)
    finally:os.close(fd)


def library_identity(original,cache):
    """Fixed on-disk package and already-loaded aliases; no native import/exec."""
    try:
        if os.name!='posix' or not sys.platform.startswith('linux'):return False
        package=sys.modules.get('safetensors');native=sys.modules.get('safetensors._safetensors_rust');torch=sys.modules.get('safetensors.torch')
        if any(type(v) is not types.ModuleType for v in (package,native,torch)):return False
        if vars(package).get('safe_open') is not original or vars(native).get('safe_open') is not original or type(original) is not type:return False
        descriptors=tuple(vars(original).get(name) for name in ('__enter__','get_tensor','__exit__'))
        if any(type(value) is not types.MethodDescriptorType or value.__objclass__ is not original for value in descriptors):return False
        paths=[vars(package).get('__file__'),vars(torch).get('__file__'),vars(native).get('__file__')]
        if any(type(v) is not str for v in paths):return False
        root=os.path.dirname(paths[0]);expected=[os.path.join(root,n) for n in LIBRARY_FILES]
        if paths!=expected:return False
        identities=[]
        for path in paths:
            value=os.lstat(path)
            if not stat.S_ISREG(value.st_mode):return False
            identities.append((value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns))
        key=(package,native,torch,original,descriptors,tuple(identities))
        if cache.get('key')==key:return True
        cache.clear()
        for path,(name,digest),identity in zip(paths,LIBRARY_FILES.items(),identities):
            _,observed=artifact(path,digest)
            if observed!=identity:return False
        cache['key']=key
        return True
    except (OSError,ValueError,TypeError):return False


def selected_identity(path):
    try:
        value=os.lstat(path)
        if not stat.S_ISREG(value.st_mode):return None
        return (value.st_dev,value.st_ino)
    except OSError:return None


def mapping_snapshot(observer,identity):
    result={'status':'unavailable','complete':False,'non_atomic':True,'matches':[]}
    if observer.mapping_pid!=os.getpid() or identity is None or os.name!='posix' or not sys.platform.startswith('linux'):return result
    try:
        with open('/proc/self/maps','rb') as stream:raw=stream.read(MAX_MAP_BYTES+1)
        bounded=len(raw)>MAX_MAP_BYTES;raw=raw[:MAX_MAP_BYTES];lines=raw.splitlines()
        if not raw.endswith(b'\n') and lines:lines.pop();bounded=True
        if len(lines)>MAX_MAP_ROWS:lines=lines[:MAX_MAP_ROWS];bounded=True
        for line in lines:
            fields=line.split(None,5)
            if len(fields)<5:raise ValueError
            begin,end=map(lambda n:int(n,16),fields[0].split(b'-'));major,minor=map(lambda n:int(n,16),fields[3].split(b':'))
            device=os.makedev(major,minor);inode=int(fields[4]);offset=int(fields[2],16)
            if (device,inode)!=identity:continue
            permissions=fields[1].decode('ascii')
            if not re.fullmatch('[r-][w-][x-][ps]',permissions) or not 0<=begin<end<2**64 or not 0<=offset<2**63:raise ValueError
            if len(result['matches'])>=MAX_MATCHES:bounded=True;continue
            coordinate=(begin,end,device,inode,offset)
            with observer.weight_lock:
                token=observer.mapping_regions.get(coordinate)
                if token is None:
                    if len(observer.mapping_regions)>=MAX_REGIONS:bounded=True;continue
                    token=observer.weight_nonce();observer.mapping_regions[coordinate]=token
            result['matches'].append({'region_sha256':token,'file_offset':offset,'mapped_bytes':end-begin,
                'permissions':permissions,'deleted':len(fields)==6 and fields[5].endswith(b' (deleted)'),
                'device_sha256':sha(str(device).encode()),'inode_sha256':sha(str(inode).encode())})
        result.update(status='bounded' if bounded else 'observed',complete=not bounded)
    except (OSError,ValueError,UnicodeError,OverflowError):result.update(status='bounded' if result['matches'] else 'unavailable',complete=False)
    return result


def safe_snapshot(observer,identity):
    try:return mapping_snapshot(observer,identity)
    except BaseException:return {'status':'unavailable','complete':False,'non_atomic':True,'matches':[]}


class MappingObservation:
    def mapping_emit(self,context,phase,started,before,after,*,exception=None):
        try:
            if self.mapping_pid!=os.getpid():return
            step=self.mapping_iteration._STEP.get();linked=bool(type(step) is dict and step.get('pid')==os.getpid()
                and step.get('point')==POINT and step.get('call')==context['call'] and not step.get('closed'))
            self.emit('SAFE_OPEN_MAPPING',{'schema':EVENT_SCHEMA,'point':POINT,'phase':phase,'source_module':WEIGHTS,
                'source_sha256':self.mapping_iteration.SOURCE_ADDITIONS[WEIGHTS],'source_method_code_sha256':context['method_code'],
                'source_verified':self.modules.get(WEIGHTS,{}).get('source_sha256')==self.mapping_iteration.SOURCE_ADDITIONS[WEIGHTS],
                'observer_runtime':self.mapping_runtime,'observer_code_sha256':self.mapping_code,'scope_sha256':self.mapping_scope_sha,
                'context_sha256':context['nonce'],'call_sha256':context['call'],'root_load_sha256':context['root'],
                'model_object_sha256':context['model'],'loader_call_sha256':context['loader_call'],'loader_model_sha256':context['loader_model'],
                'operation_index':step['operations'] if linked else None,'iterator_step_link_observed':linked,
                'normalized_path_sha256':context['path_sha'],'library_files_verified_at_open':context['library'],
                'native_binary_file_sha256':LIBRARY_FILES['_safetensors_rust.abi3.so'] if context['library'] else None,
                'operation_started_ns':started,'operation_finished_ns':time.monotonic_ns(),'before':before,'after':after,
                'path_identity_unchanged_observed':context['identity'] is not None and selected_identity(context['path'])==context['identity'],
                'exception_kind':exception,'tensor_return_observed':phase=='TENSOR_RETURN',**{key:False for key in TRUTH}})
        except BaseException:self.disabled_pid=os.getpid()

    def mapping_open(self,code,module,original,*args,**kwargs):
        frame=sys._getframe(2);step=self.mapping_iteration._STEP.get();context=None;strategy=frame.f_locals.get('safetensors_load_strategy')
        try:
            if (self.active() and self.mapping_pid==os.getpid() and self.mapping_contexts<MAX_CONTEXTS
                    and frame.f_code is code and frame.f_globals is vars(module)
                    and type(step) is dict and step.get('pid')==os.getpid() and step.get('point')==POINT and not step.get('closed')
                    and (strategy is None or type(strategy) is str and strategy in ('prefetch','lazy'))
                    and len(args)==1 and type(args[0]) is str and kwargs=={'framework':'pt'}
                    and args[0]==frame.f_locals.get('st_file')):
                path=os.path.normpath(os.path.abspath(args[0]));key=sha(path.encode())
                if any(row['normalized_path_sha256']==key for row in self.mapping_scope['files']):
                    root=step['root']
                    with self.weight_lock:
                        if self.mapping_contexts>=MAX_CONTEXTS:raise ValueError('SAFE_OPEN_MAPPING_CONTEXT_BOUND')
                        self.mapping_contexts+=1
                    context={'nonce':self.weight_nonce(),'path':path,'path_sha':key,'identity':selected_identity(path),'code':code,'module':module,
                        'call':step['call'],'method_code':step['method_code'],'root':root['call'] if root else None,
                        'model':root['model'] if root else None,'loader_call':root['loader_call'] if root else step['loader_call'],
                        'loader_model':root['loader_model'] if root else step['loader_model'],
                        'library':library_identity(original,self.mapping_library_cache)}
        except BaseException:context=None
        if context is None:return original(*args,**kwargs)
        started=time.monotonic_ns()
        if not context['library']:
            self.mapping_emit(context,'LIBRARY_UNAVAILABLE',started,None,None)
            return original(*args,**kwargs)
        before=safe_snapshot(self,context['identity'])
        try:value=original(*args,**kwargs)
        except BaseException as error:
            self.mapping_emit(context,'OPEN_RAISE',started,before,safe_snapshot(self,context['identity']),exception='exception' if isinstance(error,Exception) else 'base-exception');raise
        self.mapping_emit(context,'OPEN_RETURN',started,before,safe_snapshot(self,context['identity']))
        if type(value) is not original:return value
        context['descriptors']=tuple(vars(original).get(name) for name in ('__enter__','get_tensor','__exit__'))
        return MappingContext(self,context,value)


class MappingContext:
    def __init__(self,observer,context,value):self.observer,self.context,self.value=observer,context,value
    def __getattr__(self,name):return getattr(self.value,name)
    def invoke(self,phase,method,*args,**kwargs):
        observer=self.observer;context=self.context;frame=sys._getframe(2)
        if (observer.mapping_pid!=os.getpid() or frame.f_code is not context['code'] or frame.f_globals is not vars(context['module'])
                or tuple(vars(type(self.value)).get(name) for name in ('__enter__','get_tensor','__exit__'))!=context['descriptors']):return method(*args,**kwargs)
        started=time.monotonic_ns();before=safe_snapshot(observer,context['identity']);exception=None
        try:return method(*args,**kwargs)
        except BaseException as error:exception='exception' if isinstance(error,Exception) else 'base-exception';raise
        finally:observer.mapping_emit(context,phase+('_RAISE' if exception else '_RETURN'),started,before,safe_snapshot(observer,context['identity']),exception=exception)
    def __enter__(self):
        entered=self.invoke('ENTER',self.value.__enter__)
        return self if entered is self.value else entered
    def get_tensor(self,*args,**kwargs):return self.invoke('TENSOR',self.value.get_tensor,*args,**kwargs)
    def __exit__(self,*args):return self.invoke('EXIT',self.value.__exit__,*args)


def normalized_code(code):
    return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(normalized_code(v) if isinstance(v,types.CodeType) else v for v in code.co_consts))


def code_contract(base):
    functions=(sha,fingerprint,artifact,library_identity,selected_identity,mapping_snapshot,safe_snapshot,MappingObservation.mapping_emit,
        MappingObservation.mapping_open,MappingContext.__init__,MappingContext.__getattr__,MappingContext.invoke,
        MappingContext.__enter__,MappingContext.get_tensor,MappingContext.__exit__,normalized_code,configure)
    return sha(''.join(base.code_digest(normalized_code(fn.__code__)) for fn in functions).encode())


def configure(base,iteration,scope,*,scope_path='/guard-lifecycle/model-read-scope.json'):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if (sys.implementation.name!='cpython' or sys.flags.optimize!=0 or expected is None or code_contract(base)!=expected
            or (runtime,os.name) not in {('cpython-3.12.14','nt'),('cpython-3.12.3','posix')}):raise ValueError('SAFE_OPEN_MAPPING_OBSERVER_CODE_UNAVAILABLE')
    iteration.configure(base);base.PROFILE,base.SCHEMA=PROFILE,SCHEMA;base.__file__=__file__;base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES
    class MappingObserver(MappingObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);raw=scope.dependency(scope_path,None)
            self.mapping_scope=scope.validate_scope(json.loads(raw),session=self.config['session']);self.mapping_scope_sha=sha(raw)
            self.mapping_iteration=iteration;self.mapping_runtime=runtime;self.mapping_code=expected;self.mapping_pid=os.getpid()
            self.mapping_contexts=0;self.mapping_regions={};self.mapping_library_cache={}
        def instrument(self,module):
            if module.__name__!=WEIGHTS:return super().instrument(module)
            original=vars(module).get('safetensors_weights_iterator');original_open=vars(module).get('safe_open')
            if not inspect.isfunction(original) or original_open is None:raise ValueError('SAFE_OPEN_MAPPING_SOURCE_UNAVAILABLE')
            code=original.__code__;super().instrument(module);observer=self
            def observed_safe_open(*args,**kwargs):return observer.mapping_open(code,module,original_open,*args,**kwargs)
            module.safe_open=observed_safe_open
    base.Observer=MappingObserver;return base


def _private_base():
    modules=[]
    for name,file,digest in (('_guard_mapping_base','guard_lifecycle_base.py',BASE8_SHA256),('_guard_mapping_iteration','guard_weight_iteration.py',ITERATION16_SHA256),('_guard_mapping_scope','guard_file_read_scope.py',SCOPE17_SHA256)):
        path='/guard-lifecycle/'+file;raw,_=artifact(path,digest,262144);module=types.ModuleType(name);module.__file__=path
        exec(compile(raw,path,'exec',dont_inherit=True,optimize=0),module.__dict__);modules.append(module)
    return configure(*modules)


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
