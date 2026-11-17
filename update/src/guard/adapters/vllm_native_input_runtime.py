"""Private passive source installation and closed snapshots; no engine launch."""
from hashlib import sha256
import hmac
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading

from . import vllm_rank_ipc_runtime as shared
from . import vllm_native_input_observer as sensor
from . import vllm_native_input_profiles as profiles

ROOT=Path('/guard-native-input')
MODULES={**shared.MODULES,'vllm_rank_ipc_runtime':shared,
         'vllm_native_input_observer':sensor,'vllm_native_input_profiles':profiles}
ASSETS=frozenset({*shared.ASSETS,'guard/adapters/vllm_native_input_runtime.py',
    'guard/adapters/assets/vllm_native_input_profiles.json',
    'guard/adapters/vllm_native_input_observer.py','guard/adapters/vllm_native_input_profiles.py'})
_INSTALLED=None


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


def load_settings(path, *, root=ROOT):
    before=os.stat(path,follow_symlinks=False)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        opened=os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode)or opened.st_uid!=os.geteuid()
                or stat.S_IMODE(opened.st_mode)not in{0o400,0o600}or not 0<opened.st_size<=32768):
            raise ValueError('NATIVE_INPUT_SETTINGS_UNAVAILABLE')
        raw=os.read(fd,32769);after=os.fstat(fd);current=os.stat(path,follow_symlinks=False)
        if len(raw)!=opened.st_size or len({shared.sources._identity(s)for s in(before,opened,after,current)})!=1:
            raise ValueError('NATIVE_INPUT_SETTINGS_CHANGED')
    finally:os.close(fd)
    cfg=json.loads(raw,object_pairs_hook=shared._pairs)
    if (type(cfg)is not dict or set(cfg)!={'schema','session','image','key','output_dir','assets'}
            or cfg['schema']!='guard-native-input-settings/1'
            or type(cfg['session'])is not str or not re.fullmatch('[0-9a-f]{32}',cfg['session'])
            or type(cfg['key'])is not str or not re.fullmatch('[0-9a-f]{64}',cfg['key'])
            or cfg['output_dir']!=str(root/'data'/cfg['session'])
            or type(cfg['assets'])is not dict or set(cfg['assets'])!=ASSETS):
        raise ValueError('NATIVE_INPUT_SETTINGS_INVALID')
    return cfg,sha256(raw).hexdigest()


def freeze(cfg):
    for name,pin in cfg['assets'].items():shared._asset(ROOT/name,pin)
    modules={**MODULES,'vllm_native_input_runtime':sys.modules[__name__]}
    for name,module in modules.items():
        selected={'sha','code_digest','_context','_open_directory'}if name=='vllm_lifecycle_sensor'else None
        shared._freeze_module(module,cfg['assets']['guard/adapters/'+name+'.py'],selected)
    if (sensor.ipc is not shared.hooks or sensor.source is not shared.sources
            or shared.sources.code_digest is not shared.base.code_digest
            or shared.sources.relative_code is not shared.relative.relative_code
            or shared.hooks.base is not shared.base or shared.hooks.sources is not shared.sources):
        raise ValueError('NATIVE_INPUT_OBSERVER_ALIAS_CHANGED')


class SnapshotWriter:
    """Publish a closed file by a nonoverwriting link; the backing link stays."""
    def __init__(self,cfg,digest, *, validate=None):
        self.cfg,self.digest,self.validate=cfg,digest,validate
        self.key=bytes.fromhex(cfg['key'])
        self.pid,self.serial,self.lock=os.getpid(),0,threading.RLock()

    def __call__(self,payload):
        if self.pid!=os.getpid():self.pid,self.serial,self.lock=os.getpid(),0,threading.RLock()
        with self.lock:
            if self.validate is not None:self.validate()
            if self.serial>=4096:raise ValueError('NATIVE_INPUT_SNAPSHOT_BOUND')
            context=shared.base._context();self.serial+=1
            value={'schema':'guard-native-input-snapshot-candidate/1','session':self.cfg['session'],
                'image':self.cfg['image'],'settings_sha256':self.digest,'observer_assets':self.cfg['assets'],
                'context':context,'ordinal':self.serial,'payload':payload}
            raw=canonical({'snapshot':value,'hmac_sha256':hmac.new(self.key,canonical(value),sha256).hexdigest()})+b'\n'
            if len(raw)>131072:raise ValueError('NATIVE_INPUT_SNAPSHOT_BOUND')
            directory=shared.base._open_directory(self.cfg['output_dir']);fd=None;owned=False;published=False
            backing='.sealed-native-input-'+os.urandom(16).hex()+'.bin'
            try:
                status=os.fstat(directory)
                if status.st_uid!=os.geteuid()or stat.S_IMODE(status.st_mode)!=0o700:
                    raise ValueError('NATIVE_INPUT_SINK_UNAVAILABLE')
                fd=os.open(backing,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory);owned=True
                with os.fdopen(fd,'wb',closefd=False)as stream:stream.write(raw);stream.flush()
                os.fsync(fd);os.close(fd);fd=None
                name='pid-{}-{}-{}.json'.format(context['pid'],context['start_ticks'],self.serial)
                os.link(backing,name,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False);published=True
            finally:
                if fd is not None:os.close(fd)
                try:
                    if owned and not published:os.unlink(backing,dir_fd=directory)
                finally:os.close(directory)


class SourceLoader(shared.IPCSourceLoader):
    def exec_module(self,module):
        self.delegate.exec_module(module)
        try:self.observer.attach(module)
        except Exception:
            try:self.writer({'schema':'guard-native-input-hook-gap/1','diagnostic':'NATIVE_INPUT_SOURCE_UNAVAILABLE',
                'source_authenticated':False,'producer_registered':False,'scope_complete':False})
            except Exception:pass


class SourceFinder:
    def __init__(self,observer,writer):self.observer,self.writer,self.local=observer,writer,threading.local()

    def find_spec(self,fullname,path=None,target=None):
        if fullname not in self.observer.profile['sources']or target is not None or getattr(self.local,'busy',False):return None
        self.local.busy=True
        try:spec=importlib.util.find_spec(fullname)
        finally:self.local.busy=False
        if spec is not None and type(spec.loader)is importlib.machinery.SourceFileLoader:
            spec.loader=SourceLoader(spec.loader,self.observer,self.writer)
        return spec


def install():
    global _INSTALLED
    if _INSTALLED is not None:return True
    cfg,digest=load_settings(ROOT/'settings.json');freeze(cfg)
    profile=profiles.select(cfg['image'])
    if any(name in sys.modules for name in profile['sources']):raise ValueError('NATIVE_INPUT_SOURCE_ALREADY_LOADED')
    writer=SnapshotWriter(cfg,digest,validate=lambda:freeze(cfg))
    observer=sensor.NativeInputObserver(profile,bytes.fromhex(cfg['key']),writer)
    finder=SourceFinder(observer,writer);sys.meta_path.insert(0,finder);_INSTALLED=finder
    return True
