"""Owned .30 worker access probes after an exact Worker.load_model return.

Exported standalone. No engine import, verdict, secret read, signal or cleanup
outside this observer's exclusively created sentinel and recording files.
"""
from __future__ import annotations
from abc import ABCMeta
import errno
import functools
import hashlib
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import types

PROFILE='vllm-owned-worker-security/1'
SCHEMA='guard-owned-worker-security/1'
MODULE='vllm.v1.worker.gpu_worker'
SOURCE_SHA256='6994436e4547c0996ab555b3e814e8ccea26b650dfc1b378b97d640745309347'
SETTINGS_SHA256=None
ROOT='/guard-worker-security'
SENTINELS=frozenset({'/guard-security-denied','/guard-security-allowed'})
PERMISSION_PATHS=frozenset({'/','/models','/guard-assets','/guard-cache','/tmp','/dev/shm',
    '/guard-worker-security','/guard-worker-security/data','/guard-security-denied',
    '/guard-security-allowed','/guard-security-candidate'})
_FINDER=None


def sha(raw):return hashlib.sha256(raw).hexdigest()


def source_bytes(path,limit=262144):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=limit:raise ValueError('WORKER_SECURITY_SOURCE_UNAVAILABLE')
        raw=os.read(fd,limit+1);after=os.fstat(fd);current=os.stat(path,follow_symlinks=False)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if len(raw)!=before.st_size or len({identity(s) for s in (before,after,current)})!=1:raise ValueError('WORKER_SECURITY_SOURCE_CHANGED')
        return raw
    finally:os.close(fd)


def paths(value):
    if (type(value) is not list or len(value)>16 or len(set(map(str,value)))!=len(value)
            or any(type(p) is not str or not 1<len(p)<=256 or not p.startswith('/')
                or any(c in p for c in ('\0','\n','\r')) or any(s in ('','..','.') for s in p.split('/')[1:]) for p in value)):
        raise ValueError('WORKER_SECURITY_PATHS_INVALID')
    return list(value)


def settings(value):
    if (type(value) is not dict or set(value)!={'schema','session','expected_ranks','credential_paths','sentinel_paths','permission_paths'}
            or value['schema']!='guard-worker-security-settings/1'
            or type(value['session']) is not str or re.fullmatch('[0-9a-f]{32}',value['session']) is None
            or type(value['expected_ranks']) is not list or not 1<=len(value['expected_ranks'])<=4
            or value['expected_ranks']!=list(range(len(value['expected_ranks'])))
            or any(type(r) is not int for r in value['expected_ranks'])
            or not set(paths(value['sentinel_paths']))<=SENTINELS
            or type(value['permission_paths']) is not list
            or any(type(p) is not str for p in value['permission_paths'])
            or not set(value['permission_paths'])<=PERMISSION_PATHS
            or len(set(value['permission_paths']))!=len(value['permission_paths'])):raise ValueError('WORKER_SECURITY_SETTINGS_INVALID')
    paths(value['credential_paths'])
    return value


def read_settings():
    raw=source_bytes(ROOT+'/settings.json',16384)
    if sha(raw)!=SETTINGS_SHA256:raise ValueError('WORKER_SECURITY_SETTINGS_CHANGED')
    return settings(json.loads(raw))


def context():
    pid=os.getpid()
    raw=Path('/proc/self/stat').read_bytes()
    ticks=int(raw[raw.rfind(b')')+2:].split()[19])
    status={k:v.split() for line in Path('/proc/self/status').read_text().splitlines()
        if ':' in line for k,v in [line.split(':',1)] if k in {'Uid','Gid','CapEff'}}
    with open('/proc/self/mountinfo','rb') as stream:mountinfo=stream.read(2*1024**2+1)
    if len(mountinfo)>2*1024**2:raise ValueError('WORKER_SECURITY_MOUNTS_BOUND')
    return {'pid':pid,'start_ticks':ticks,'uid_effective':int(status['Uid'][1]),
        'gid_effective':int(status['Gid'][1]),'cap_eff':status['CapEff'][0].lower(),
        'pid_namespace_inode':os.stat('/proc/self/ns/pid').st_ino,
        'mount_namespace_inode':os.stat('/proc/self/ns/mnt').st_ino,
        'time_namespace_inode':os.stat('/proc/self/ns/time').st_ino,
        'time_offsets_sha256':sha(Path('/proc/self/timens_offsets').read_bytes()),
        'mountinfo_sha256':sha(mountinfo),
        'boot_sha256':sha(Path('/proc/sys/kernel/random/boot_id').read_bytes())}


def _open_candidate_nofollow(path):
    """Test this worker's read-open permission; never read credential bytes."""
    current=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        parts=path.split('/')[1:]
        for part in parts[:-1]:
            following=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=current)
            os.close(current);current=following
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=current)
        try:
            value=os.fstat(fd)
            return 'candidate_readable' if stat.S_ISREG(value.st_mode) else 'unknown'
        finally:os.close(fd)
    except OSError as error:
        return ('denied' if error.errno in {errno.EACCES,errno.EPERM} else
                'absent' if error.errno==errno.ENOENT else 'unknown')
    finally:os.close(current)


def open_candidate(path):
    # This one standard Linux alias is resolved by metadata, not by following
    # arbitrary caller-supplied links. Its identity/target must remain stable.
    if path.startswith('/var/run/'):
        try:
            before=os.lstat('/var/run')
            if stat.S_ISLNK(before.st_mode):
                target=os.readlink('/var/run')
                if target not in {'/run','../run'}:return 'unknown'
                result=_open_candidate_nofollow('/run/'+path[len('/var/run/'):])
                after=os.lstat('/var/run')
                identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
                return result if identity(before)==identity(after) and os.readlink('/var/run')==target else 'unknown'
        except FileNotFoundError:return 'absent'
        except OSError:return 'unknown'
    return _open_candidate_nofollow(path)


def sentinel(path,session):
    """Only an exclusive new owned test file can be removed by this probe."""
    directory=None;created=False
    name='.guard-worker-'+session+'-'+str(os.getpid())
    try:
        directory=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        created=True
        try:os.write(fd,b'guard-owned-worker-sentinel\n')
        finally:os.close(fd)
        os.unlink(name,dir_fd=directory);created=False
        return 'succeeded'
    except OSError as error:
        if created:return 'error'
        return ('denied' if error.errno in {errno.EACCES,errno.EPERM,errno.EROFS} else
                'absent' if error.errno==errno.ENOENT else 'error')
    finally:
        if directory is not None:os.close(directory)


def mount_rows():
    with open('/proc/self/mountinfo','rb') as stream:raw=stream.read(2*1024**2+1)
    if len(raw)>2*1024**2:raise ValueError('WORKER_SECURITY_MOUNTS_BOUND')
    result=[]
    for line in raw.decode('utf-8').splitlines():
        fields=line.split();separator=fields.index('-')
        if len(fields)<10 or separator<6:raise ValueError('WORKER_SECURITY_MOUNTS_INVALID')
        path=fields[4]
        for before,after in ((r'\040',' '),(r'\011','\t'),(r'\012','\n'),(r'\134','\\')):path=path.replace(before,after)
        if not path.startswith('/') or '\n' in path or len(path)>512:raise ValueError('WORKER_SECURITY_MOUNTS_INVALID')
        flags=set(fields[5].split(','))|set(fields[separator+3].split(','))
        result.append({'path':path,'read_only':'ro' in flags,'noexec':'noexec' in flags,
                       'filesystem':fields[separator+1]})
    if not 1<=len(result)<=256:raise ValueError('WORKER_SECURITY_MOUNTS_BOUND')
    return sorted(result,key=lambda r:r['path'])


def device_fds():
    names=os.listdir('/proc/self/fd')
    if len(names)>512:raise ValueError('WORKER_SECURITY_FD_BOUND')
    paths=set()
    for name in names:
        if not name.isdecimal():continue
        path='/proc/self/fd/'+name
        try:
            destination=os.readlink(path);metadata=os.stat(path)
            if stat.S_ISCHR(metadata.st_mode) and re.fullmatch('/dev/(nvidia[0-9]+|nvidiactl|nvidia-uvm(-tools)?|nvidia-caps/nvidia-cap[0-9]+|dri/renderD[0-9]+)',destination):
                paths.add(destination)
        except FileNotFoundError:pass
    return sorted(paths)


def code_digest(code):
    def item(value):
        if type(value) is types.CodeType:
            return [value.co_name,value.co_qualname,value.co_argcount,value.co_posonlyargcount,
                value.co_kwonlyargcount,value.co_flags,value.co_stacksize,value.co_code.hex(),
                value.co_linetable.hex(),value.co_exceptiontable.hex(),value.co_names,value.co_varnames,
                value.co_freevars,value.co_cellvars,[item(v) for v in value.co_consts]]
        if type(value) is bytes:return ['bytes',value.hex()]
        if type(value) in (tuple,frozenset):
            children=[item(v) for v in value]
            return ['frozenset',sorted(children,key=lambda v:json.dumps(v,sort_keys=True))] if type(value) is frozenset else ['tuple',children]
        if value is None or type(value) in (str,int,bool):return value
        if type(value) is float:return ['float',value.hex()]
        if value is Ellipsis:return ['ellipsis']
        raise ValueError('WORKER_SECURITY_CODE_CONSTANT_UNAVAILABLE')
    return sha(json.dumps(item(code),sort_keys=True,separators=(',',':')).encode())


def selected_code(root):
    if root.co_qualname=='Worker.load_model':return root
    for value in root.co_consts:
        if type(value) is types.CodeType:
            found=selected_code(value)
            if found is not None:return found
    return None


def write_record(value,cfg):
    directory=ROOT+'/data/'+cfg['session']
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        owner=os.fstat(fd)
        if owner.st_uid!=os.geteuid() or stat.S_IMODE(owner.st_mode)!=0o700:raise ValueError('WORKER_SECURITY_SINK_UNVERIFIED')
        raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        if len(raw)>65536:raise ValueError('WORKER_SECURITY_RECORD_BOUND')
        file=os.open('rank-'+str(value['rank'])+'.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            with os.fdopen(file,'wb',closefd=False) as stream:stream.write(raw)
        finally:os.close(file)
    finally:os.close(fd)


def invalidate(worker,cfg):
    rank=object.__getattribute__(worker,'__dict__').get('rank')
    if type(rank) is not int or rank not in cfg['expected_ranks']:return
    directory=os.open(ROOT+'/data/'+cfg['session'],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        try:fd=os.open('rank-'+str(rank)+'.invalid',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        except FileExistsError:return
        try:os.write(fd,b'load-model-called-again\n')
        finally:os.close(fd)
    finally:os.close(directory)


def probe(worker,owner,cfg,body_digest):
    if type(worker) is not owner:raise ValueError('WORKER_SECURITY_OWNER_CHANGED')
    attrs=object.__getattribute__(worker,'__dict__');rank=attrs.get('rank')
    parallel=object.__getattribute__(attrs['parallel_config'],'__dict__')
    if (type(rank) is not int or rank not in cfg['expected_ranks']
            or any(type(parallel.get(k)) is not int for k in ('tensor_parallel_size','pipeline_parallel_size','data_parallel_size'))
            or parallel.get('tensor_parallel_size')!=len(cfg['expected_ranks'])
            or parallel.get('pipeline_parallel_size')!=1 or parallel.get('data_parallel_size')!=1):
        raise ValueError('WORKER_SECURITY_GROUP_UNSUPPORTED')
    began=time.monotonic_ns();before=context()
    names=sorted(os.environ)
    if len(names)>1024 or any(re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}',name) is None for name in names):
        raise ValueError('WORKER_SECURITY_ENVIRONMENT_UNAVAILABLE')
    candidates={p:open_candidate(p) for p in cfg['credential_paths']}
    sentinels={p:sentinel(p,cfg['session']) for p in cfg['sentinel_paths']}
    write_results={p:sentinel(p,cfg['session']) for p in cfg['permission_paths']}
    observed_mounts=mount_rows();devices=device_fds();after=context()
    if before!=after:raise ValueError('WORKER_SECURITY_GENERATION_CHANGED')
    value={'schema':SCHEMA,'profile':PROFILE,'session':cfg['session'],'rank':rank,
        'source_module':MODULE,'source_sha256':SOURCE_SHA256,'body_code_sha256':body_digest,
        'sensor_sha256':sha(source_bytes(__file__)),'settings_sha256':SETTINGS_SHA256,
        'observer_runtime':'cpython-'+'.'.join(map(str,sys.version_info[:3])),
        'context':before,'started_ns':began,'finished_ns':time.monotonic_ns(),
        'environment_names':names,'credential_results':candidates,'sentinel_results':sentinels,'write_results':write_results,
        'mounts':observed_mounts,'device_fds':devices,'source_verified':True,'producer_authenticated':False,
        'scope':'selected-owned-tp-worker-after-load-model'}
    write_record(value,cfg)


def instrument(module):
    if vars(module).get('__name__')!=MODULE:raise ValueError('WORKER_SECURITY_MODULE_UNAVAILABLE')
    owner=vars(module).get('Worker');fn=vars(owner).get('load_model') if type(owner) in (type,ABCMeta) else None
    if (type(fn) is not types.FunctionType or fn.__globals__ is not vars(module) or fn.__closure__ is not None
            or fn.__defaults__ is not None or fn.__kwdefaults__!={'load_dummy_weights':False}):
        raise ValueError('WORKER_SECURITY_CALLABLE_UNAVAILABLE')
    path=vars(module).get('__file__');raw=source_bytes(path)
    if sha(raw)!=SOURCE_SHA256:raise ValueError('WORKER_SECURITY_ENGINE_SOURCE_UNAVAILABLE')
    expected=selected_code(compile(raw,path,'exec',dont_inherit=True,optimize=0))
    digest=code_digest(fn.__code__)
    if expected is None or digest!=code_digest(expected):raise ValueError('WORKER_SECURITY_ENGINE_CODE_UNAVAILABLE')
    cfg=read_settings()
    seen=set()
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        key=os.getpid()
        if key in seen:
            try:invalidate(args[0],cfg)
            except Exception:pass
        result=fn(*args,**kwargs)
        # Dummy-weight or failed loads never stand in for a loaded worker.
        if not kwargs.get('load_dummy_weights',False):
            seen.add(key)
            try:probe(args[0],owner,cfg,digest)
            except Exception:pass
        return result
    owner.load_model=wrapped


class Loader(importlib.abc.Loader):
    def __init__(self,delegate):self.delegate=delegate
    def create_module(self,spec):return self.delegate.create_module(spec) if hasattr(self.delegate,'create_module') else None
    def exec_module(self,module):
        self.delegate.exec_module(module)
        try:instrument(module)
        except Exception:pass


class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname!=MODULE:return None
        spec=importlib.machinery.PathFinder.find_spec(fullname,path,target)
        if spec is not None and spec.loader is not None:spec.loader=Loader(spec.loader)
        return spec


def install():
    global _FINDER
    if (sys.implementation.name!='cpython' or sys.version_info[:3] not in {(3,12,3),(3,12,8)}
            or sys.flags.optimize!=0 or sys.platform!='linux' or os.uname().machine!='x86_64'
            or _FINDER is not None or MODULE in sys.modules):return False
    read_settings();_FINDER=Finder();sys.meta_path.insert(0,_FINDER);return True


def render_source(settings_sha256):
    if type(settings_sha256) is not str or re.fullmatch('[0-9a-f]{64}',settings_sha256) is None:
        raise ValueError('WORKER_SECURITY_SETTINGS_SHA_REQUIRED')
    return Path(__file__).read_bytes()+('\nSETTINGS_SHA256='+repr(settings_sha256)+'\n').encode()
