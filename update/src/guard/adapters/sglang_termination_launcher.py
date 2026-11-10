"""Opt-in launcher for the fixed SGLang 0.5.22 single-tokenizer HTTP app.

The existing sitecustomize bootstrap must have installed guard_lifecycle_sensor.
Keep its module/context identity; never import a second copy of the sensor.
Invocation explicitly selects this wrapper instead of changing SGLang globally.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import runpy
import stat
import sys

VERSION = '0.5.22'
SENSOR_PATH = Path('/guard-lifecycle/guard_lifecycle_sensor.py')
SENSOR_SHA256 = 'b65eaaba32c80f4c778a41d8f86602361eab8d48be2e1b96520c9a2865e57f27'
SOURCE_SHA256 = {
    'sglang.srt.entrypoints.engine':'e0c9beca051d080ef712fa54e914e337f97d9d04a36b3ae2e2551b8ca7a1520f',
    'sglang.srt.server_args':'d1b9ffc79edcbc857efd02a47d47eed9b414f8ac44bc4e3214d78a7922e90e03',
    'sglang.launch_server':'374976475a62ee53a3f32454bf17071eb9b18bab7ed293390e2c765e3669a7a2',
    'sglang.srt.entrypoints.http_server':'a89d20bb2fa8a86c511ab4d9d054f74d707423ab220f733ff25e62fcc0e49979',
}


def verify_file(path, expected):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=1024*1024:
            raise RuntimeError('SGLANG_LAUNCH_SOURCE_UNAVAILABLE')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(1024*1024+1)
        after=os.fstat(fd);current=os.lstat(path)
        identity=lambda value:(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns,value.st_mode)
        if (len(raw)!=before.st_size or identity(before)!=identity(after) or identity(after)!=identity(current) or
                hashlib.sha256(raw).hexdigest()!=expected):
            raise RuntimeError('SGLANG_LAUNCH_SOURCE_MISMATCH')
    finally:os.close(fd)


def protect_server_args(module):
    """Extend the vendor's existing secret-field omission before startup logs."""
    cls=getattr(module,'ServerArgs',None)
    fields=getattr(module,'record_fields',None)
    try:
        names=tuple(field.name for field in fields(cls))
        hidden={'api_key','admin_api_key','watermark_key','watermark_key_b','watermark_config'}
        if (not isinstance(cls,type) or not {'api_key','admin_api_key'}<=set(names)
                or len(names)!=len(set(names)) or any(type(name)is not str for name in names)):
            raise ValueError
        visible=tuple(name for name in names if name not in hidden)
    except Exception:
        raise RuntimeError('SGLANG_PRIVATE_SERVER_ARGS_UNAVAILABLE') from None
    def private_repr(self):
        try:
            return 'ServerArgs('+', '.join(f'{name}={getattr(self,name)!r}' for name in visible)+')'
        except Exception:
            # Logging must not fall back to formatting an unsafe object/error.
            return 'ServerArgs(<configuration unavailable>)'
    cls.__repr__=private_repr


def protect_logging_args(module):
    """Sanitize only the pinned vendor's logging view; auth values stay intact."""
    original=vars(module).get('_server_args_for_logging')
    if not callable(original):
        raise RuntimeError('SGLANG_PRIVATE_LOG_ARGS_UNAVAILABLE')
    if getattr(original,'_guard_private_logging_args',False):
        return
    def private_logging_args(server_args):
        try:
            value=original(server_args)
            if type(value)is not dict:
                raise ValueError
            hidden={'api_key','admin_api_key','watermark_key','watermark_key_b','watermark_config'}
            return {key:item for key,item in value.items()if key not in hidden}
        except Exception:
            raise RuntimeError('SGLANG_PRIVATE_LOG_ARGS_UNAVAILABLE')from None
    private_logging_args._guard_private_logging_args=True
    module._server_args_for_logging=private_logging_args


class _PrivateArgsLoader:
    def __init__(self, loader, path, name):
        self.loader,self.path,self.name=loader,path,name
    def create_module(self, spec):
        return self.loader.create_module(spec)
    def exec_module(self, module):
        verify_file(self.path,SOURCE_SHA256[self.name])
        self.loader.exec_module(module)
        (protect_server_args if self.name=='sglang.srt.server_args'else protect_logging_args)(module)


class _PrivateArgsFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in {'sglang.srt.server_args','sglang.srt.entrypoints.engine'}:return None
        spec=importlib.machinery.PathFinder.find_spec(fullname,path,target)
        if spec is None or not isinstance(spec.origin,str) or spec.loader is None:
            raise RuntimeError('SGLANG_PRIVATE_SERVER_ARGS_UNAVAILABLE')
        spec.loader=_PrivateArgsLoader(spec.loader,Path(spec.origin),fullname)
        return spec


def install_private_args():
    """Each fresh Python interpreter installs this lazy, source-pinned guard."""
    for name,protect in (('sglang.srt.server_args',protect_server_args),('sglang.srt.entrypoints.engine',protect_logging_args)):
        current=sys.modules.get(name)
        if current is not None:
            verify_file(current.__file__,SOURCE_SHA256[name])
            protect(current)
    if not any(isinstance(finder,_PrivateArgsFinder)for finder in sys.meta_path):
        sys.meta_path.insert(0,_PrivateArgsFinder())


def prepare():
    if importlib.metadata.version('sglang')!=VERSION:
        raise RuntimeError('SGLANG_LAUNCH_VERSION_MISMATCH')
    install_private_args()
    sensor=sys.modules.get('guard_lifecycle_sensor')
    if (sensor is None or Path(getattr(sensor,'__file__',''))!=SENSOR_PATH or
            getattr(sensor,'PROFILE',None)!='sglang-native-lifecycle-metadata/1' or
            getattr(sensor,'SCHEMA',None)!='guard-sglang-native-lifecycle-events/1' or
            getattr(getattr(sensor,'_BASE',None),'_OBSERVER',None) is None):
        raise RuntimeError('SGLANG_BOOTSTRAP_SENSOR_REQUIRED')
    verify_file(SENSOR_PATH,SENSOR_SHA256)
    origins={}
    for name,expected in SOURCE_SHA256.items():
        spec=importlib.util.find_spec(name)
        if spec is None or not isinstance(spec.origin,str):
            raise RuntimeError('SGLANG_LAUNCH_SOURCE_UNAVAILABLE')
        origins[name]=Path(spec.origin)
        verify_file(origins[name],expected)
    arguments=importlib.import_module('sglang.srt.server_args')
    if Path(arguments.__file__)!=origins['sglang.srt.server_args']:
        raise RuntimeError('SGLANG_ARGUMENT_IMPORT_MISMATCH')
    verify_file(arguments.__file__,SOURCE_SHA256['sglang.srt.server_args'])
    protect_server_args(arguments)
    http=importlib.import_module('sglang.srt.entrypoints.http_server')
    if Path(http.__file__)!=origins['sglang.srt.entrypoints.http_server']:
        raise RuntimeError('SGLANG_HTTP_IMPORT_MISMATCH')
    verify_file(http.__file__,SOURCE_SHA256['sglang.srt.entrypoints.http_server'])
    app=http.app
    if getattr(app,'middleware_stack',None) is not None:
        raise RuntimeError('SGLANG_HTTP_APP_ALREADY_STARTED')
    installed=[entry for entry in app.user_middleware if entry.cls is sensor.Middleware]
    if not installed:app.add_middleware(sensor.Middleware)
    elif len(installed)!=1:raise RuntimeError('SGLANG_HTTP_MIDDLEWARE_DUPLICATE')
    return app


def main(argv=None):
    # Preserve every selected SGLang option. Root's fixed profile uses default
    # HTTP/one tokenizer; alternate transports/workers require their own entry.
    args=list(sys.argv[1:] if argv is None else argv)
    prepare()
    original=sys.argv
    try:
        sys.argv=['sglang.launch_server',*args]
        return runpy.run_module('sglang.launch_server',run_name='__main__',alter_sys=True)
    finally:sys.argv=original


if __name__=='__main__':main()
