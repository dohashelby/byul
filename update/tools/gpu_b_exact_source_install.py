"""Materialize exact full source in a new owned root; offline standard uv only."""
import argparse,hashlib,json,os,shutil,signal,stat,subprocess,sys,time
from pathlib import Path

def metadata(p):
    s=p.lstat()
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def guarded(path,directory=False,owner=1000):
    p=Path(path).absolute()
    for n in (p,*p.parents):
        s=n.lstat()
        assert not stat.S_ISLNK(s.st_mode),'SOURCE_PATH_SYMLINK'
        assert not (s.st_mode&0o022),'SOURCE_PATH_GROUP_OTHER_WRITABLE'
    s=p.lstat()
    assert s.st_uid==owner and (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode))
    if not directory:assert s.st_nlink==1
    return p

def pinned(path,expected):
    p=guarded(path);before=metadata(p)
    with p.open('rb') as f:raw=f.read()
    assert metadata(p)==before and hashlib.sha256(raw).hexdigest()==expected
    return raw

def cache_git_admission(original):
    """Reuse source-owner admission; cache metadata is not executable source.

    The approved source_owner_verify._git_context permits a non-symlink
    gitfile as well as a directory, owned by the repository owner. Keep the
    strict no022 guard for the repository/ancestors and all NEW execution data.
    """
    original=guarded(original,True)
    assert original.resolve(strict=True)==original,'SOURCE_CACHE_ROOT_ALIAS'
    entry=original/'.git';before=metadata(entry);mode=before[2]
    print(json.dumps({'schema':'byul-retained-cache-entry-observation/1','path':str(entry),
        'mode':oct(stat.S_IMODE(mode)),'uid':before[3],'gid':before[4],'dev':before[0],'inode':before[1],
        'nlink':before[5],'bytes':before[6],'mtime_ns':before[7],'ctime_ns':before[8],
        'regular':stat.S_ISREG(mode),'directory':stat.S_ISDIR(mode),'symlink':stat.S_ISLNK(mode),
        'observation_is_not_past_failure_reconstruction':True}),file=sys.stderr,flush=True)
    assert not stat.S_ISLNK(mode),'SOURCE_CACHE_GIT_SYMLINK'
    assert before[3]==original.stat().st_uid==os.geteuid(),'SOURCE_CACHE_GIT_OWNER'
    kind='directory' if stat.S_ISDIR(mode) else 'gitfile'
    assert stat.S_ISDIR(mode) or stat.S_ISREG(mode),'SOURCE_CACHE_GIT_KIND'
    captured={str(original):metadata(original),str(entry):before};files={}
    def small_file(path):
        path=Path(path).absolute()
        for part in (path,*path.parents):assert not part.is_symlink(),'SOURCE_CACHE_ADMIN_SYMLINK'
        prior=metadata(path)
        assert stat.S_ISREG(prior[2]) and prior[3]==os.geteuid() and prior[5]==1,'SOURCE_CACHE_ADMIN_REGULAR_OWNER'
        assert 0<prior[6]<=4096,'SOURCE_CACHE_ADMIN_BYTE_BOUND'
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            assert tuple(getattr(os.fstat(stream.fileno()),'st_'+n) for n in ('dev','ino','mode','uid','gid','nlink','size','mtime_ns','ctime_ns'))==prior,'SOURCE_CACHE_ADMIN_FD'
            raw=stream.read(4097)
            after=tuple(getattr(os.fstat(stream.fileno()),'st_'+n) for n in ('dev','ino','mode','uid','gid','nlink','size','mtime_ns','ctime_ns'))
        assert metadata(path)==after==prior and len(raw)==prior[6],'SOURCE_CACHE_ADMIN_CHANGED'
        captured[str(path)]=prior;files[str(path)]=hashlib.sha256(raw).hexdigest()
        return raw
    if kind=='gitfile':
        raw=small_file(entry)
        assert raw.startswith(b'gitdir: ') and b'\x00' not in raw,'SOURCE_CACHE_GITFILE_FORMAT'
        text=raw[8:].decode('utf-8').rstrip('\r\n')
        assert text and '\n' not in text and '\r' not in text,'SOURCE_CACHE_GITFILE_FORMAT'
        gitdir=Path(os.path.abspath(entry.parent/text))
    else:gitdir=entry
    for part in (gitdir,*gitdir.parents):assert not part.is_symlink(),'SOURCE_CACHE_ADMIN_SYMLINK'
    current=metadata(gitdir)
    assert stat.S_ISDIR(current[2]) and current[3]==os.geteuid(),'SOURCE_CACHE_GITDIR_OWNER'
    captured[str(gitdir)]=current
    common=gitdir
    if (gitdir/'commondir').exists():
        text=small_file(gitdir/'commondir').decode('utf-8').rstrip('\r\n')
        assert text and '\n' not in text and '\r' not in text and '\x00' not in text,'SOURCE_CACHE_COMMONDIR_FORMAT'
        common=Path(os.path.abspath(gitdir/text))
        for part in (common,*common.parents):assert not part.is_symlink(),'SOURCE_CACHE_ADMIN_SYMLINK'
        current=metadata(common)
        assert stat.S_ISDIR(current[2]) and current[3]==os.geteuid(),'SOURCE_CACHE_COMMONDIR_OWNER'
        captured[str(common)]=current
    result={'schema':'byul-owned-retained-git-cache-admission/1','at_monotonic':time.monotonic(),
        'repository':str(original),'entry_kind':kind,'gitdir':str(gitdir),'common_directory':str(common),
        'metadata':{p:{'dev':v[0],'inode':v[1],'mode':oct(stat.S_IMODE(v[2])),'uid':v[3],'gid':v[4],
            'nlink':v[5],'bytes':v[6],'mtime_ns':v[7],'ctime_ns':v[8]} for p,v in captured.items()},
        'admin_file_sha256':files,'observation_is_not_past_failure_reconstruction':True}
    print(json.dumps(result,sort_keys=True),file=sys.stderr,flush=True)
    return result,captured,files

def cache_unchanged(captured,files):
    assert all(metadata(Path(p))==value for p,value in captured.items()),'SOURCE_CACHE_METADATA_CHANGED'
    for name,digest in files.items():
        path=Path(name);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:raw=stream.read(4097)
        assert hashlib.sha256(raw).hexdigest()==digest and metadata(path)==captured[name],'SOURCE_CACHE_ADMIN_CHANGED'

def independent_copy(repo):
    gitdir=guarded(repo/'.git',True)
    guarded(gitdir/'objects',True)
    assert not (gitdir/'commondir').exists(),'SOURCE_NEW_COPY_COMMONDIR_DEPENDENCY'
    assert not (gitdir/'objects/info/alternates').exists(),'SOURCE_NEW_COPY_ALTERNATE_DEPENDENCY'
    return {'new_git_directory':str(gitdir),'no_old_common_directory':True,'no_alternate_object_dependency':True}

def group_active(pgid):

    for entry in Path('/proc').iterdir():

        if not entry.name.isdigit():

            continue

        try:

            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()

            if int(fields[2]) == pgid and fields[0] != 'Z':

                return True

        except (FileNotFoundError, ProcessLookupError):

            continue

    return False

def wait_child(proc, timeout):

    result = {'timed_out': False, 'child_reaped': False, 'group_terminal': False,

        'cleanup_errors': []}

    error = None

    try:

        result['returncode'] = proc.wait(timeout=timeout)

        result['child_reaped'] = True

    except BaseException as caught:

        error = caught

        result['timed_out'] = isinstance(caught, subprocess.TimeoutExpired)

        result['wait_error'] = type(caught).__name__ + ': ' + str(caught)

    # Kill any remaining guard descendants even when the direct CLI already exited.

    try:

        if group_active(proc.pid):

            os.killpg(proc.pid, signal.SIGKILL)

    except ProcessLookupError:

        pass

    except OSError as caught:

        result['cleanup_errors'].append(type(caught).__name__ + ': ' + str(caught))

    if not result['child_reaped']:

        try:

            result['returncode'] = proc.wait(timeout=30)

            result['child_reaped'] = True

        except BaseException as caught:

            result['cleanup_errors'].append(type(caught).__name__ + ': ' + str(caught))

    until = time.monotonic() + 30

    while group_active(proc.pid) and time.monotonic() < until:

        time.sleep(.05)

    result['group_terminal'] = not group_active(proc.pid)

    if error is not None and not result['timed_out']:

        result['interrupted'] = True

    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--config-sha256',required=True)
    a=p.parse_args();assert os.getuid()==os.geteuid()==1000
    config=json.loads(pinned(a.config,a.config_sha256))
    pins=json.loads(pinned(config['pins_path'],config['pins_sha256']))
    original=guarded(config['retained_repository'],True)
    cache_admission,cache_metadata,cache_files=cache_git_admission(original)
    oldroot=metadata(original);oldgit=metadata(original/'.git')
    destination=Path(config['new_root']).absolute();guarded(destination.parent,True)
    assert not destination.exists() and not destination.is_symlink()
    os.umask(0o077);destination.mkdir(mode=0o700)
    logs=destination/'install-logs';logs.mkdir(mode=0o700)
    environment=dict(os.environ)
    environment.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null',GIT_TERMINAL_PROMPT='0',
                       UV_OFFLINE='1',UV_PYTHON_DOWNLOADS='never',UV_PYTHON='/usr/bin/python3.12',PYTHONDONTWRITEBYTECODE='1')
    records=[]
    def call(label,argv,cwd=None,timeout=120):
        with (logs/(label+'.stdout')).open('xb') as out,(logs/(label+'.stderr')).open('xb') as err:
            child=subprocess.Popen(argv,cwd=cwd,env=environment,stdout=out,stderr=err,start_new_session=True)
            terminal=wait_child(child,timeout)
        raw=(logs/(label+'.stdout')).read_bytes();error=(logs/(label+'.stderr')).read_bytes()
        records.append({'label':label,'argv':argv,'terminal':terminal,'stdout_sha256':hashlib.sha256(raw).hexdigest(),'stderr_sha256':hashlib.sha256(error).hexdigest()})
        assert terminal['child_reaped'] and terminal['group_terminal'] and not terminal['cleanup_errors']
        assert not terminal['timed_out'] and terminal['returncode']==0,'SOURCE_INSTALL_SUBPROCESS_FAILED'
        return raw
    git='/usr/bin/git'
    def git_args(repo,*args):return [git,'-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-C',str(repo),*args]
    assert call('retained-head',git_args(original,'rev-parse','HEAD')).decode().strip()==pins['prerequisite_commit']
    assert call('retained-tree',git_args(original,'rev-parse','HEAD^{tree}')).decode().strip()==pins['prerequisite_tree']
    bundle=guarded(config['bundle_path']);bundle_before=metadata(bundle)
    assert bundle.stat().st_size==pins['bundle']['bytes']
    pinned(bundle,pins['bundle']['sha256'])
    repo=destination/'repo'
    call('local-clone',[git,'-c','core.hooksPath=/dev/null','clone','--local','--no-hardlinks','--no-checkout','--dissociate',str(original),str(repo)])
    copy_identity=independent_copy(repo)
    call('bundle-prerequisites',git_args(repo,'bundle','verify',str(bundle)))
    call('offline-fetch',git_args(repo,'fetch','--no-tags',str(bundle),'HEAD'))
    call('exact-checkout',git_args(repo,'checkout','--detach',pins['final_commit']))
    assert call('final-head',git_args(repo,'rev-parse','HEAD')).decode().strip()==pins['final_commit']
    assert call('final-tree',git_args(repo,'rev-parse','HEAD^{tree}')).decode().strip()==pins['final_tree']
    for relative,row in pins['files'].items():
        assert not Path(relative).is_absolute() and '..' not in Path(relative).parts
        target=guarded(repo/relative)
        assert target.stat().st_size==row['bytes'] and hashlib.sha256(target.read_bytes()).hexdigest()==row['sha256']
    call('clean-tracked-tree',git_args(repo,'diff','--exit-code','HEAD','--'))
    assert metadata(original)==oldroot and metadata(original/'.git')==oldgit and metadata(bundle)==bundle_before
    cache_unchanged(cache_metadata,cache_files)
    uv=Path(shutil.which('uv') or '').absolute()
    assert uv.is_file(),'EXISTING_UV_NOT_FOUND_NO_DOWNLOAD'
    uv_actual=uv.resolve(strict=True);uv_meta=metadata(uv_actual)
    assert uv_actual.stat().st_uid in (0,1000) and not uv_actual.stat().st_mode&0o022
    assert not (repo/'update/.venv').exists()
    call('new-offline-venv',[str(uv),'sync','--offline','--frozen'],cwd=repo/'update',timeout=600)
    python=repo/'update/.venv/bin/python'
    isolated="import guard,json,pathlib,sys;root=pathlib.Path(sys.argv[1]);origin=pathlib.Path(guard.__file__).resolve();assert origin.is_relative_to(root/'update/src/guard');assert pathlib.Path(sys.prefix).resolve()==root/'update/.venv';print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'guard_origin':str(origin)}))"
    origin=json.loads(call('isolated-origin',[str(python),'-I','-B','-c',isolated,str(repo)]))
    assert metadata(uv_actual)==uv_meta and metadata(original)==oldroot and metadata(original/'.git')==oldgit
    cache_unchanged(cache_metadata,cache_files)
    receipt={'schema':'byul-new-full-source-standard-offline-runtime/1','source_commit':pins['final_commit'],'tree':pins['final_tree'],
      'verified_full_tree_files':len(pins['files']),'repository':str(repo),'isolated_origin':origin,
      'retained_source_metadata_unchanged':True,'new_runtime_only':True,'network_downloads':False,'GPU_or_model_execution':False,
      'cache_admission':cache_admission,'cache_metadata_and_gitfile_bytes_unchanged':True,'copy_identity':copy_identity,'records':records}
    with (destination/'source-install-receipt.json').open('x',encoding='utf-8') as f:json.dump(receipt,f,indent=2);f.write('\n')
    print(json.dumps({k:v for k,v in receipt.items() if k!='records'}),flush=True)

if __name__=='__main__':main()
