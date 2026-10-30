"""Opt-in binary eager-read observations over byte-preserved native8/native16.

Only a reviewed safetensors generator frame's module-local open is wrapped.
The original read result reaches the decoder unchanged. No helper model read,
strategy switch, C safe_open observation or authority assertion is performed.
"""
from __future__ import annotations
import builtins,hashlib,inspect,io,json,os,re,stat,sys,time,types

PROFILE='vllm-native-lifecycle-metadata/17'
SCHEMA='guard-native-lifecycle-events/17'
EVENT_SCHEMA='guard-native-file-read/1'
SCOPE_SCHEMA='guard-native-model-read-scope/1'
COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
BASE8_SHA256='2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd'
ITERATION16_SHA256='e7494edaa0a512201652228acc73ac13621cb40d938e6f23b6d4eaba73d05bc6'
WEIGHTS='vllm.model_executor.model_loader.weight_utils'
SOURCE_ADDITIONS={WEIGHTS:'7d2573931b9684147b17018c1f6de318eea1c105556ffea03154201a72418afd'}
POINT='weight_utils.safetensors_weights_iterator'
PHASES=frozenset({'OPEN_RETURN','OPEN_RAISE','READ_RETURN','READ_RAISE','CLOSE_RETURN','CLOSE_RAISE'})
MAX_ROWS=2048
MAX_BYTES=2*1024**2
MAX_SCOPE_BYTES=262144
MAX_HASH_BYTES=2**31
CODE_FILENAME='guard/adapters/vllm_file_read_sensor.py'
SCOPE_PATH='/guard-lifecycle/model-read-scope.json'
RUNTIME_CODE_CONTRACTS={'cpython-3.12.14': '52ca1a3bc23cc2a6670b6e8859ad5b5162f9091dce90b2506fe03216c8d134f1', 'cpython-3.12.3': '136e4530b1afe4932f79a59cefd9e4d3c0148e6b9f0aefc2b86b7d8389c89464'}
_BASE=None


def sha(raw):return hashlib.sha256(raw).hexdigest()


def fingerprint(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def validate_scope(value,*,session):
    fields={'schema','session','input_scope_sha256','environment_ref','target_sha256','model_sha256','revision_sha256',
        'inventory_sha256','manifest_bytes_sha256','data_origin','nonsecret_model_weights','hash_model_bytes','max_hash_bytes','files'}
    try:
        if type(value) is not dict or set(value)!=fields or value['schema']!=SCOPE_SCHEMA or value['session']!=session:raise ValueError
        if session is not None and (type(session) is not str or re.fullmatch('[0-9a-f]{32}',session) is None):raise ValueError
        if session is None and value['input_scope_sha256'] is not None or session is not None and not fingerprint(value['input_scope_sha256']):raise ValueError
        env=value['environment_ref']
        if (type(env) is not dict or set(env)!={'kind','id','revision','sha256'} or env['kind']!='ENV'
                or type(env['id']) is not str or re.fullmatch('[A-Za-z0-9_.:-]{1,160}',env['id']) is None
                or type(env['revision']) is not int or env['revision']<1 or not fingerprint(env['sha256'])):raise ValueError
        if any(not fingerprint(value[k]) for k in ('target_sha256','model_sha256','revision_sha256','inventory_sha256','manifest_bytes_sha256')):raise ValueError
        if (value['data_origin'] not in {'EXAMPLE','DECLARED'} or value['nonsecret_model_weights'] is not True
                or type(value['hash_model_bytes']) is not bool or type(value['max_hash_bytes']) is not int
                or not 0<value['max_hash_bytes']<=MAX_HASH_BYTES or type(value['files']) is not list or not 0<len(value['files'])<=256):raise ValueError
        seen=set()
        for row in value['files']:
            if (type(row) is not dict or set(row)!={'normalized_path_sha256','reference_sha256','bytes'}
                    or not fingerprint(row['normalized_path_sha256']) or not fingerprint(row['reference_sha256'])
                    or row['normalized_path_sha256'] in seen or type(row['bytes']) is not int or not 0<=row['bytes']<2**63):raise ValueError
            seen.add(row['normalized_path_sha256'])
    except (ValueError,KeyError,TypeError):raise ValueError('MODEL_READ_SCOPE_INVALID') from None
    return value


def dependency(path,expected):
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=MAX_SCOPE_BYTES:raise ValueError('FILE_READ_DEPENDENCY_UNAVAILABLE')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(MAX_SCOPE_BYTES+1)
        after=os.fstat(fd)
        identity=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
        if len(raw)!=before.st_size or identity(before)!=identity(after) or expected is not None and sha(raw)!=expected:raise ValueError('FILE_READ_DEPENDENCY_UNAVAILABLE')
        return raw
    finally:os.close(fd)


def fd_snapshot(stream):
    try:
        value=os.fstat(stream.fileno());offset=stream.tell()
        result={'device_sha256':sha(str(value.st_dev).encode()),'inode_sha256':sha(str(value.st_ino).encode()),'mode':value.st_mode,'uid':getattr(value,'st_uid',0),
            'bytes':value.st_size,'mtime_ns':value.st_mtime_ns,'ctime_ns':value.st_ctime_ns,'offset':offset,
            'regular':stat.S_ISREG(value.st_mode)}
        return result if all(0<=v<2**63 for v in result.values() if type(v) is int) else None
    except BaseException:return None


def path_snapshot(path):
    try:
        value=os.stat(path,follow_symlinks=False)
        result={'device_sha256':sha(str(value.st_dev).encode()),'inode_sha256':sha(str(value.st_ino).encode()),'mode':value.st_mode,'uid':getattr(value,'st_uid',0),
            'bytes':value.st_size,'mtime_ns':value.st_mtime_ns,'ctime_ns':value.st_ctime_ns,'regular':stat.S_ISREG(value.st_mode)}
        return result if all(0<=v<2**63 for v in result.values() if type(v) is int) else None
    except BaseException:return None


class FileObservation:
    def read_emit(self,binding,handle,phase,started,finished,before,after,path_before,path_after,*,returned=None,exception=None,closed=False):
        try:
            if binding['pid']!=os.getpid():return
            root=binding['root'];digest=None;status='disabled'
            if phase=='READ_RETURN' and self.read_scope['hash_model_bytes']:
                status='unavailable'
                if type(returned) is bytes:
                    status='too-large' if len(returned)>self.read_scope['max_hash_bytes'] else 'complete'
                    if status=='complete':digest=sha(returned)
            self.emit('FILE_READ',{'schema':EVENT_SCHEMA,'point':POINT,'phase':phase,'source_module':WEIGHTS,
                'source_sha256':SOURCE_ADDITIONS[WEIGHTS],'source_method_code_sha256':binding['method_code'],
                'source_verified':self.modules.get(WEIGHTS,{}).get('source_sha256')==SOURCE_ADDITIONS[WEIGHTS],
                'observer_runtime':self.read_runtime,'observer_code_sha256':self.read_code,'scope_sha256':self.read_scope_sha,
                'handle_sha256':handle['nonce'],'call_sha256':binding['call'],'root_load_sha256':root['call'] if root else None,
                'model_object_sha256':root['model'] if root else None,'loader_call_sha256':root['loader_call'] if root else binding['loader_call'],
                'loader_model_sha256':root['loader_model'] if root else binding['loader_model'],
                'operation_index':binding['operations'],'normalized_path_sha256':handle['path_sha256'],'cwd_sha256':handle['cwd_sha256'],
                'operation_started_ns':started,'operation_finished_ns':finished,'fd_before':before,'fd_after':after,
                'path_before':path_before,'path_after':path_after,'returned_bytes':len(returned) if type(returned) is bytes else None,
                'returned_bytes_sha256':digest,'hash_status':status,'exception_kind':exception,'fd_closed_observed':closed,
                'source_fd_read_return_observed':phase=='READ_RETURN' and type(returned) is bytes,
                'worker_assignment_verified':False,'approved_model_file_verified':False,'producer_authenticated':False,
                'tensor_storage_applied_verified':False,'decoder_completion_verified':False,'nofollow_components_verified':False})
        except BaseException:self.disabled_pid=os.getpid()

    def read_open(self,original_code,module,original,*args,**kwargs):
        frame=sys._getframe(2);binding=self.read_iteration._STEP.get();handle=None
        try:
            if (self.active() and frame.f_code is original_code and frame.f_globals is vars(module)
                    and type(binding) is dict and binding.get('pid')==os.getpid() and not binding.get('closed')
                    and binding.get('point')==POINT and frame.f_locals.get('safetensors_load_strategy')=='eager'
                    and len(args)==2 and type(args[0]) is str and args[1]=='rb' and not kwargs
                    and type(frame.f_locals.get('st_file')) is str and args[0]==frame.f_locals['st_file']):
                cwd=os.getcwd();path=os.path.normpath(os.path.join(cwd,args[0]));key=sha(path.encode())
                if any(row['normalized_path_sha256']==key for row in self.read_scope['files']):
                    handle={'nonce':self.weight_nonce(),'path':path,'path_sha256':key,'cwd_sha256':sha(cwd.encode())}
        except BaseException:handle=None
        started=time.monotonic_ns() if handle else None
        path_before=path_snapshot(handle['path']) if handle else None
        try:stream=original(*args,**kwargs)
        except BaseException as error:
            if handle:
                self.read_emit(binding,handle,'OPEN_RAISE',started,time.monotonic_ns(),None,None,
                    path_before,path_snapshot(handle['path']),exception='exception' if isinstance(error,Exception) else 'base-exception')
            raise
        if handle is None or type(stream) is not io.BufferedReader:return stream
        # The observed open follows the serving implementation's semantics.
        # lstat/fstat agreement never asserts all-component nofollow or trust.
        snap=fd_snapshot(stream);path=path_snapshot(handle['path'])
        self.read_emit(binding,handle,'OPEN_RETURN',started,time.monotonic_ns(),snap,snap,path_before,path)
        return ReadProxy(self,binding,handle,stream)


class ReadProxy:
    def __init__(self,observer,binding,handle,stream):self.observer,self.binding,self.handle,self.stream=observer,binding,handle,stream
    def __enter__(self):
        self.stream.__enter__();return self
    def read(self,*args,**kwargs):
        observer,binding,handle,stream=self.observer,self.binding,self.handle,self.stream
        # The fixed source calls read() once with no arguments. Unexpected calls
        # pass through, leaving a missing read rather than broadening the scope.
        if args or kwargs or binding['pid']!=os.getpid():return stream.read(*args,**kwargs)
        before=fd_snapshot(stream);path_before=path_snapshot(handle['path']);started=time.monotonic_ns();returned=None;exception=None
        try:
            returned=stream.read();return returned
        except BaseException as error:
            exception='exception' if isinstance(error,Exception) else 'base-exception';raise
        finally:
            observer.read_emit(binding,handle,'READ_RAISE' if exception else 'READ_RETURN',started,time.monotonic_ns(),before,
                fd_snapshot(stream),path_before,path_snapshot(handle['path']),returned=returned,exception=exception)
    def __exit__(self,*args):
        observer,binding,handle,stream=self.observer,self.binding,self.handle,self.stream
        before=fd_snapshot(stream);path_before=path_snapshot(handle['path']);started=time.monotonic_ns();exception=None
        try:return stream.__exit__(*args)
        except BaseException as error:
            exception='exception' if isinstance(error,Exception) else 'base-exception';raise
        finally:
            observer.read_emit(binding,handle,'CLOSE_RAISE' if exception else 'CLOSE_RETURN',started,time.monotonic_ns(),before,
                fd_snapshot(stream),path_before,path_snapshot(handle['path']),exception=exception,closed=stream.closed is True)


def normalized_code(code):
    return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(normalized_code(v) if isinstance(v,types.CodeType) else v for v in code.co_consts))


def code_contract(base):
    functions=(sha,fingerprint,validate_scope,dependency,fd_snapshot,path_snapshot,FileObservation.read_emit,FileObservation.read_open,
        ReadProxy.__init__,ReadProxy.__enter__,ReadProxy.read,ReadProxy.__exit__,normalized_code,configure)
    return sha(''.join(base.code_digest(normalized_code(fn.__code__)) for fn in functions).encode())


def configure(base,iteration,*,scope_path=SCOPE_PATH):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if (sys.implementation.name!='cpython' or sys.flags.optimize!=0 or expected is None or code_contract(base)!=expected):raise ValueError('FILE_READ_OBSERVER_CODE_UNAVAILABLE')
    iteration.configure(base);base.PROFILE,base.SCHEMA=PROFILE,SCHEMA;base.__file__=__file__;base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES
    class FileObserver(FileObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);raw=dependency(scope_path,None)
            self.read_scope=validate_scope(json.loads(raw),session=self.config['session']);self.read_scope_sha=sha(raw)
            self.read_iteration=iteration;self.read_runtime=runtime;self.read_code=expected
        def instrument(self,module):
            if module.__name__!=WEIGHTS:return super().instrument(module)
            original=vars(module).get('safetensors_weights_iterator');original_open=vars(module).get('open',builtins.open)
            if not inspect.isfunction(original) or original_open is not builtins.open:raise ValueError('FILE_READ_OPEN_UNAVAILABLE')
            original_code=original.__code__
            super().instrument(module)
            # The wrapper body must directly call read_open, so its caller frame
            # is inspected explicitly there. No global builtins mutation.
            observer=self
            def observed_open(*args,**kwargs):
                return observer.read_open(original_code,module,original_open,*args,**kwargs)
            module.open=observed_open
    base.Observer=FileObserver
    return base


def _private_base():
    base=types.ModuleType('_guard_file_read_base');base.__file__='/guard-lifecycle/guard_lifecycle_base.py'
    raw=dependency(base.__file__,BASE8_SHA256);exec(compile(raw,base.__file__,'exec',dont_inherit=True,optimize=0),base.__dict__)
    iteration=types.ModuleType('_guard_file_read_iteration');iteration.__file__='/guard-lifecycle/guard_weight_iteration.py'
    raw=dependency(iteration.__file__,ITERATION16_SHA256);exec(compile(raw,iteration.__file__,'exec',dont_inherit=True,optimize=0),iteration.__dict__)
    return configure(base,iteration)


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
