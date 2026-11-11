"""S2-only normal native17 sealing/capture via existing bounded reader.

No engine creation/restart, product verdict, paired policy or AWS management.
"""
from pathlib import Path
import argparse,hashlib,json,sys,traceback,os,stat,threading,time
def verify_source(repo,commit,manifest):
    helper=Path(__file__).with_name('source_owner_verify.py');raw=helper.read_bytes()
    if hashlib.sha256(raw).hexdigest()!='fb12dbe8d82d6bc027b613a477d6e1a432fa4693c46c6f1380cb38cecdf79fc9':raise ValueError('SUP16_SOURCE_OWNER_HELPER_CHANGED')
    namespace={'__name__':'_owned_sup16_source_verify','__file__':str(helper)}
    exec(compile(raw,str(helper),'exec',dont_inherit=True),namespace)
    return namespace['verify_source_owned'](repo,commit,manifest)

class FirstClientFailure:
    """One owner-private diagnostic; never replaces a producer result or error."""
    def __init__(self, output):
        self.output=Path(output)
        self._lock=threading.Lock()
        self._attempted=False
        self._summary={'attempted':False,'saved':False}

    def summary(self):
        with self._lock:
            return dict(self._summary)

    def save_result(self, argv, result):
        try:
            pipe=result.get('capture',{})
            failed=(result.get('returncode')!=0 or pipe.get('complete') is not True
                or pipe.get('bounded_at_source') is not True or pipe.get('timed_out') is not False
                or pipe.get('truncated') is not False or pipe.get('pipes_closed') is False
                or bool(pipe.get('read_errors')))
            if failed:self._record(argv,result=result)
        except BaseException:
            # Diagnostic classification must not change the result either.
            pass

    def save_exception(self, argv, error):
        result=getattr(error,'result',None);layer='root-client-result'
        if type(result) is not dict:
            result=getattr(error,'transport',None);layer='root-bootstrap-transport'
        if type(result) is not dict:result=None;layer='exception-without-capture'
        self._record(argv,error=error,result=result,result_layer=layer)

    def _record(self, argv, *, result=None, error=None, result_layer="root-client-result"):
        with self._lock:
            if self._attempted:return
            self._attempted=True
            self._summary={'attempted':True,'saved':False}
        parent_fd=directory_fd=None
        try:
            if os.getuid()!=1000 or os.geteuid()!=1000:
                raise PermissionError('OWNER1000_DIAGNOSTIC_REQUIRED')
            parent_fd=os.open(self.output,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            parent=os.fstat(parent_fd)
            if parent.st_uid!=1000 or stat.S_IMODE(parent.st_mode)&0o022:
                raise PermissionError('PRIVATE_DIAGNOSTIC_PARENT_REQUIRED')
            os.mkdir('first-client-failure',0o700,dir_fd=parent_fd)
            directory_fd=os.open('first-client-failure',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent_fd)
            directory=os.fstat(directory_fd)
            if directory.st_uid!=1000 or stat.S_IMODE(directory.st_mode)!=0o700:
                raise PermissionError('PRIVATE_DIAGNOSTIC_DIRECTORY_REQUIRED')
            def write(name, raw):
                fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=directory_fd)
                try:
                    before=os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_uid!=1000 or before.st_nlink!=1 or stat.S_IMODE(before.st_mode)!=0o600:
                        raise PermissionError('OWNER1000_REGULAR600_REQUIRED')
                    view=memoryview(raw)
                    while view:
                        count=os.write(fd,view)
                        if not count:raise OSError('DIAGNOSTIC_WRITE_INCOMPLETE')
                        view=view[count:]
                    after=os.fstat(fd)
                    named=os.stat(name,dir_fd=directory_fd,follow_symlinks=False)
                    key=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_gid,s.st_mode,s.st_nlink,s.st_size)
                    if key(before)[:-1]!=key(after)[:-1] or key(after)!=key(named) or after.st_size!=len(raw):
                        raise OSError('DIAGNOSTIC_IDENTITY_CHANGED')
                    return {'path':str(self.output/'first-client-failure'/name),'bytes':len(raw),
                        'sha256':hashlib.sha256(raw).hexdigest(),'dev':after.st_dev,'inode':after.st_ino,
                        'uid':after.st_uid,'gid':after.st_gid,'mode':oct(stat.S_IMODE(after.st_mode)),'nlink':after.st_nlink}
                finally:os.close(fd)
            argv_raw=json.dumps(argv,ensure_ascii=False,separators=(',',':')).encode()
            value={'schema':'byul-first-client-failure/1','argv_sha256':hashlib.sha256(argv_raw).hexdigest(),
                'argv_bytes':len(argv_raw),'argc':len(argv),'argv_body_persisted':False,'argv_scope':'caller-requested-reader-argv',
                'result_layer':result_layer,
                'scope':'first runner-observed producer failure only; not consumer JSON/identity failure'}
            stderr_ref=None
            if error is not None:
                frames=[];tb=error.__traceback__
                while tb is not None and len(frames)<16:
                    frames.append({'file':tb.tb_frame.f_code.co_filename,'function':tb.tb_frame.f_code.co_name,'line':tb.tb_lineno})
                    tb=tb.tb_next
                value['exception']={'type':type(error).__module__+'.'+type(error).__qualname__,
                    'errno':getattr(error,'errno',None),'frames':frames,'frames_truncated':tb is not None,
                    'message_or_source_body_persisted':False,'original_object_reraised_unchanged':True}
            if result is not None:
                stdout=result.get('stdout_bytes');stderr=result.get('stderr_bytes')
                value.update(returncode=result.get('returncode'),capture=result.get('capture'),
                    root_supervision=result.get('root_supervision'),
                    stdout={'bytes':len(stdout),'sha256':hashlib.sha256(stdout).hexdigest(),'body_persisted':False}
                        if type(stdout) is bytes else {'type':type(stdout).__name__,'body_persisted':False})
                if type(stderr) is bytes:
                    retained=stderr[:4096]
                    stderr_ref=write('stderr.raw',retained)
                    value['stderr']={'original_bytes':len(stderr),'original_sha256':hashlib.sha256(stderr).hexdigest(),
                        'retained':stderr_ref,'retained_truncated':len(stderr)>4096}
                else:value['stderr']={'type':type(stderr).__name__}
            raw=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False).encode()
            if len(raw)>16384:raise ValueError('DIAGNOSTIC_METADATA_BOUND')
            metadata=write('first-failure.json',raw)
            with self._lock:self._summary={'attempted':True,'saved':True,'metadata':metadata,'stderr':stderr_ref}
        except BaseException as caught:
            with self._lock:self._summary={'attempted':True,'saved':False,
                'write_error_type':type(caught).__module__+'.'+type(caught).__qualname__,
                'write_error_errno':getattr(caught,'errno',None)}
        finally:
            for fd in (directory_fd,parent_fd):
                if fd is not None:
                    try:os.close(fd)
                    except BaseException as caught:
                        with self._lock:self._summary={**self._summary,
                            'fd_close_error_type':type(caught).__module__+'.'+type(caught).__qualname__,
                            'fd_close_error_errno':getattr(caught,'errno',None)}

COMPONENT_SHA='e359cf357627e3f5c26e1300e489132be045b6ecffd0fa708162bc1d3604581b'
BOUNDED_SHA='25c81666a17e13651c0b5b3bd67c9f310c6feee8311fcac408404d895dc67290'
ORIGINAL_PROGRAM_SHA={
 'file':'ce5bf977d9b6170085e23fcfbd34f439f0f14260bfacf52062a17b1f301522a2',
 'context':'0e713bed5c8990e5625df6ce8a82c4074f8753d322d1010a7068663c007245ca',
 'inventory':'4f19d9c427d4a51e165d6870a8b80248da507b12bbf73931a8b3f7b9e878f671',
 'seal':'0567222e3675f272909412f44bd08a77efcf85c0c7a93a0dd9f1a6b09b3d9368'}

def _fd_identity(s):
    return (s.st_dev,s.st_ino,s.st_uid,s.st_gid,s.st_mode,s.st_nlink,
        s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def _directory_fd(path, *, private):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise ValueError('ABSOLUTE_SOURCE_DIRECTORY_REQUIRED')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        info=os.fstat(fd)
        if info.st_uid!=1000 or (stat.S_IMODE(info.st_mode)!=0o700 if private else bool(stat.S_IMODE(info.st_mode)&0o022)):
            raise PermissionError('OWNED_SOURCE_DIRECTORY_REQUIRED')
        return fd
    except BaseException:
        os.close(fd);raise

def _owned_source(path, pin, size, *, private):
    path=Path(path);directory=_directory_fd(path.parent,private=private)
    try:
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        try:
            before=os.fstat(fd);mode=stat.S_IMODE(before.st_mode)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid!=1000 or before.st_nlink!=1
                    or before.st_size!=size or (mode!=0o600 if private else bool(mode&0o022))):
                raise PermissionError('OWNED_REGULAR_SOURCE_REQUIRED')
            raw=os.read(fd,size+1);after=os.fstat(fd)
            named=os.stat(path.name,dir_fd=directory,follow_symlinks=False)
            if (_fd_identity(before)!=_fd_identity(after) or _fd_identity(after)!=_fd_identity(named)
                    or len(raw)!=size or hashlib.sha256(raw).hexdigest()!=pin):
                raise ValueError('OWNED_SOURCE_IDENTITY_OR_BYTES_CHANGED')
            return raw
        finally:os.close(fd)
    finally:os.close(directory)

def _private_runtime(output, bounded_raw):
    parent=_directory_fd(output,private=True)
    directory=None
    try:
        os.mkdir('root-client-private',0o700,dir_fd=parent)
        directory=os.open('root-client-private',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        info=os.fstat(directory)
        if info.st_uid!=1000 or stat.S_IMODE(info.st_mode)!=0o700:raise PermissionError('PRIVATE_RUNTIME_REQUIRED')
        fd=os.open('bounded.py',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=directory)
        try:
            before=os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid!=1000 or before.st_nlink!=1
                    or stat.S_IMODE(before.st_mode)!=0o600):raise PermissionError('PRIVATE_BOUNDED_SOURCE_REQUIRED')
            view=memoryview(bounded_raw)
            while view:
                n=os.write(fd,view)
                if not n:raise OSError('PRIVATE_BOUNDED_COPY_INCOMPLETE')
                view=view[n:]
            after=os.fstat(fd);named=os.stat('bounded.py',dir_fd=directory,follow_symlinks=False)
            if (_fd_identity(before)[:6]!=_fd_identity(after)[:6]
                    or _fd_identity(after)!=_fd_identity(named) or after.st_size!=len(bounded_raw)):
                raise OSError('PRIVATE_BOUNDED_IDENTITY_CHANGED')
        finally:os.close(fd)
        os.mkdir('wire',0o700,dir_fd=directory)
        path=Path(output)/'root-client-private'
        # Factory reopens and verifies these exact bytes and modes independently.
        return path/'bounded.py',path/'wire'
    finally:
        if directory is not None:os.close(directory)
        os.close(parent)

def selected_client_private_root(value, *, assets, protected):
    # A capture output is not the trust root for private client state.
    value=Path(value);assets=Path(assets).absolute()
    names={'client-private-'+phase for phase in ('normal-before','challenge-partial','normal-recovered')}
    if (not value.is_absolute() or '..' in value.parts or value.parent!=assets or value.name not in names):
        raise ValueError('SUP16_SELECTED_CLIENT_PRIVATE_ROOT_REQUIRED')
    for item in protected:
        item=Path(item).absolute()
        if value.is_relative_to(item) or item.is_relative_to(value):
            raise ValueError('SUP16_CLIENT_PRIVATE_OUTPUT_SEPARATION_REQUIRED')
    return value

class RootComponentBridge:
    """Callable at original reader construction; binds its actual values once."""
    def __init__(self,binary,kubeconfig,*,existing_root_client,source_root,client_private_root,failure_preserver):
        self.binary=binary;self.kubeconfig=kubeconfig;self.existing_root_client=existing_root_client
        self.source_root=Path(source_root);self.client_private_root=Path(client_private_root);self.failure_preserver=failure_preserver
        self._runner=None;self._binding_attempted=False

    def bind_reader(self,reader):
        if self._binding_attempted:raise ValueError('ROOT_COMPONENT_ALREADY_BINDING_ATTEMPTED')
        self._binding_attempted=True
        if os.getuid()!=1000 or os.geteuid()!=1000:raise PermissionError('UID1000_CONSUMER_REQUIRED')
        if reader.runner is not self or reader.injected is not True:raise ValueError('ORIGINAL_INJECTED_READER_REQUIRED')
        if type(reader.wrapped) is not dict or set(reader.wrapped)!=set(ORIGINAL_PROGRAM_SHA):
            raise ValueError('ORIGINAL_FOUR_PROGRAMS_REQUIRED')
        programs={key:hashlib.sha256(value.encode()).hexdigest() for key,value in reader.wrapped.items()}
        if programs!=ORIGINAL_PROGRAM_SHA:raise ValueError('ORIGINAL_FOUR_PROGRAM_BYTES_CHANGED')
        engine=reader.binding['engine']
        binding={'context':reader.binding['context'],**{key:engine[key] for key in ('namespace','pod','container')},
            'program_sha256':[programs[key] for key in ORIGINAL_PROGRAM_SHA]}
        component=Path(__file__).with_name('root_client_component.py')
        raw=_owned_source(component,COMPONENT_SHA,28637,private=True)
        bounded_raw=_owned_source(self.source_root/'update/src/guard/runtime/bounded.py',BOUNDED_SHA,6134,private=False)
        bounded,wire=_private_runtime(self.client_private_root,bounded_raw)
        import types
        module=types.ModuleType('_owned_root_client_component')
        module.__file__=str(component)
        exec(compile(raw,str(component),'exec',dont_inherit=True),module.__dict__)
        self._runner=module.selected_kubeconfig_runner(self.binary,self.kubeconfig,
            existing_root_client=self.existing_root_client,component_path=component,
            component_sha256=COMPONENT_SHA,bounded_path=bounded,output_dir=wire,binding=binding)

    def __call__(self,argv,**kwargs):
        try:
            if self._runner is None:raise ValueError('ROOT_COMPONENT_NOT_BOUND')
            result=self._runner(argv,**kwargs)
        except BaseException as error:
            try:self.failure_preserver.save_exception(argv,error)
            except BaseException:pass
            raise
        try:self.failure_preserver.save_result(argv,result)
        except BaseException:pass
        return result


def observe_pre_seal_inventory(reader, target, prepared, producer):
    """Preserve one existing producer return before seal; no verdict or timer."""
    observation = {'stage': 'before_seal', 'producer_returned': False,
                   'started_monotonic_ns': time.monotonic_ns()}
    try:
        engine = reader.binding['engine']
        observation['producer_result'] = producer(reader, target,
            container=engine['container'], image=engine['image'],
            session=prepared['session'], owner_identity=reader.owner_identity)
        observation['producer_returned'] = True
    except Exception as error:
        observation['error'] = {'type': type(error).__name__, 'message': str(error),
                                'errno': getattr(error, 'errno', None)}
    observation['finished_monotonic_ns'] = time.monotonic_ns()
    return observation


def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ['source-root','environment','target','prepared','output','client-private-root']:p.add_argument('--'+n,type=Path,required=True)
 for n in ['source-commit','manifest-sha256','tool-sha256']:p.add_argument('--'+n,required=True)
 p.add_argument('--kubeconfig',type=Path,required=True)
 p.add_argument('--use-existing-root-kubectl',action='store_true',required=True)
 p.add_argument('--kubectl-binary',default='kubectl');p.add_argument('--execute',action='store_true')
 a=p.parse_args();sys.path[:0]=[str(a.source_root/'update/src'),str(a.source_root/'update')]
 verify_source(a.source_root,a.source_commit,a.manifest_sha256)
 from guard.collectors.kubernetes_lifecycle import load_inputs
 from guard.collectors.model_read_scope import validate_binding
 from guard.collectors.native_model_file_reads import analyze
 from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
 from guard.executors.lifecycle_session import capture_session,inventory
 from guard.models import canonical_json
 from guard.update_validation import reserve_output
 if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=a.tool_sha256:raise ValueError('SUP16_NORMAL_CAPTURE_SOURCE_CHANGED')
 env,target,prepared=load_inputs(a.environment,a.target,a.prepared);validate_binding(prepared,env,target)
 if (env.engine.name,env.engine.version,env.engine.image_digest)!=('vllm','0.31.0',
  'vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b'):
  raise ValueError('SUP16_NORMAL_CAPTURE_EXISTING_GPU_SUPPORT_MISMATCH')
 if prepared['profile']!='vllm-native-lifecycle-metadata/17' or target.get('lab') is not True:
  raise ValueError('SUP16_NORMAL_CAPTURE_NATIVE17_LAB_REQUIRED')
 if not a.execute:
  print(json.dumps({'validated_inputs':True,'capture_executed':False,'engine_or_AWS_started':False}));return
 private_root=selected_client_private_root(a.client_private_root,assets=Path(__file__).parent,
  protected=[a.source_root,a.environment,a.target,a.prepared,a.output])
 out=reserve_output(a.output,protected=[a.source_root,a.environment,a.target,a.prepared,private_root])
 report={'capture_executed':False,'source':a.source_commit,'errors':[],'security_verdict_generated':False,
  'scope':'normal live native diagnostic; paired SUP16 product execution not performed'}
 reader=None
 first_failure=FirstClientFailure(private_root)
 try:
  # One reader keeps one original120s budget across seal, capture and report.
  bridge=RootComponentBridge(a.kubectl_binary,a.kubeconfig,existing_root_client=a.use_existing_root_kubectl,
   source_root=a.source_root,client_private_root=private_root,failure_preserver=first_failure)
  reader=KubernetesLifecycleReader(target,env,prepared,binary=a.kubectl_binary,duration=120,runner=bridge)
  bridge.bind_reader(reader)
  reader.snapshot()
  report['before_seal']=observe_pre_seal_inventory(reader,target,prepared,inventory)
  report['seal']=reader.seal_session()
  engine=reader.binding['engine']
  packet=capture_session(reader,target,container=engine['container'],image=engine['image'],prepared=prepared,
   owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
  (out/'capture.json').write_bytes(canonical_json(packet))
  report['capture_executed']=True
  analysis=analyze(packet,prepared)
  (out/'native-analysis.json').write_bytes(canonical_json(analysis))
  report['model_root_read_candidates']=len(analysis['candidates']);report['missing']=analysis['missing']
 except BaseException as exc:
  report['errors'].append({'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()})
  traceback.print_exc()
 finally:
  if reader is not None:
   try:report['transport']=reader.report()
   except BaseException as exc:report['errors'].append({'phase':'report','type':type(exc).__name__,'message':str(exc)})
  report['first_client_failure']=first_failure.summary()
  (out/'capture-receipt.json').write_bytes(canonical_json(report))
 print(json.dumps(report,sort_keys=True))
 if report['errors']:raise SystemExit(1)
if __name__=='__main__':main()
