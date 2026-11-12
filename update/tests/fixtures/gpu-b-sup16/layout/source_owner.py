"""SUP16-only source verification with two read-only Git calls as source owner.

No safe.directory/config/permissions change and no root fallback. Manifest,
verify_req10.verify, pack, complete path and import checks remain unchanged.
"""
from pathlib import Path
import hashlib,json,os,stat,subprocess,sys

def _git_context(repo):
    repo=Path(repo).absolute()
    if repo.is_symlink() or repo.resolve(strict=True)!=repo:
        raise ValueError('SUP16_SOURCE_ROOT_SYMLINK_OR_ALIAS')
    git_entry=repo/'.git'
    if git_entry.is_symlink():raise ValueError('SUP16_SOURCE_GIT_ENTRY_SYMLINK')
    before=[p.lstat() for p in (repo,git_entry)]
    if not stat.S_ISDIR(before[0].st_mode):raise ValueError('SUP16_SOURCE_DIRECTORY_REQUIRED')
    prefix=[]
    context={'repository':str(repo),'actor_prefix':prefix,'process_euid':None,
             'repository_uid':before[0].st_uid,'git_entry_uid':before[1].st_uid}
    if os.name=='posix':
        uid=os.geteuid();context['process_euid']=uid
        if before[1].st_uid!=before[0].st_uid:raise ValueError('SUP16_SOURCE_GIT_OWNER_MISMATCH')
        if uid!=before[0].st_uid:
            import pwd
            if uid!=0 or before[0].st_uid!=1000:
                raise ValueError('SUP16_SOURCE_GIT_ACTOR_UNSUPPORTED')
            account=pwd.getpwnam('ubuntu')
            if account.pw_uid!=1000 or pwd.getpwuid(1000).pw_name!='ubuntu':
                raise ValueError('SUP16_SOURCE_GIT_ACTOR_UNSUPPORTED')
            prefix=['sudo','-n','-u','ubuntu']
        context['actor_prefix']=prefix
    print('SUP16_SOURCE_GIT_CONTEXT '+json.dumps(context,sort_keys=True),file=sys.stderr,flush=True)
    return repo,prefix,before,context

def _git(repo,prefix,args):
    argv=[*prefix,'git','-C',str(repo),*args]
    value=subprocess.run(argv,capture_output=True)
    print('SUP16_SOURCE_GIT_RESULT '+json.dumps({'argv':argv,'exit':value.returncode,
        'stdout_sha256':hashlib.sha256(value.stdout).hexdigest(),
        'stderr_sha256':hashlib.sha256(value.stderr).hexdigest()},sort_keys=True),file=sys.stderr,flush=True)
    # Preserve original raw text in stderr; callers keep stdout for JSON.
    if value.stdout:print(value.stdout.decode(errors='replace'),file=sys.stderr,end='',flush=True)
    if value.stderr:print(value.stderr.decode(errors='replace'),file=sys.stderr,end='',flush=True)
    if value.returncode:raise subprocess.CalledProcessError(value.returncode,argv,output=value.stdout,stderr=value.stderr)
    return value.stdout

def verify_source_owned(repo,commit,manifest):
    repo,prefix,before,context=_git_context(repo)
    # Same two original Git commands; only the verified owner prefix differs.
    if _git(repo,prefix,['rev-parse','HEAD']).decode().strip()!=commit:
        raise ValueError('SUP04_FIXED_SOURCE_CHANGED')
    _git(repo,prefix,['diff','--exit-code','HEAD','--','update/src','update/release',
        'update/tools/verify_req10_consumption.py','update/tests/builders.py',
        'update/tools/gpu_b_sup04_realmodel.py'])
    after=[p.lstat() for p in (repo,repo/'.git')]
    identity=lambda st:(st.st_dev,st.st_ino,st.st_uid)
    if [identity(s) for s in before]!=[identity(s) for s in after]:
        raise ValueError('SUP16_SOURCE_GIT_IDENTITY_CHANGED')
    if hashlib.sha256((repo/'update/release/reviewed-source.json').read_bytes()).hexdigest()!=manifest:
        raise ValueError('SUP04_FIXED_MANIFEST_CHANGED')
    sys.path[:0]=[str(repo/'update/src'),str(repo/'update')]
    from tools.verify_req10_consumption import verify
    return verify(repo)
