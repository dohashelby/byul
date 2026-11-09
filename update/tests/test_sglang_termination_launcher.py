"""Pinned source fragments + real FastAPI ASGI, CPU only; no SGLang engine."""
from __future__ import annotations

import ast
import dataclasses
import logging
import io
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
import zipfile

import pytest
from fastapi import FastAPI, Request
from starlette.responses import Response

from guard.adapters import sglang_termination_launcher as launcher
from guard.adapters import sglang_request_termination_sensor as sensor
from tests.test_sglang_termination_sensor import observed, source_function

FIXTURE=Path(__file__).parent/'fixtures/sglang0522-launch-source.zip'


def raw(name):
    with zipfile.ZipFile(FIXTURE) as archive:return archive.read(name)


def source_app():
    tree=ast.parse(raw('http_server.py'))
    statement=next(node for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='app' for t in node.targets))
    namespace={'FastAPI':FastAPI,'lifespan':None,'get_bool_env_var':lambda name:False,'fastapi_telemetry_disabled_kwargs':lambda:{}}
    fragment=ast.Module(body=[statement],type_ignores=[]);ast.fix_missing_locations(fragment)
    exec(compile(fragment,'http_server.py','exec'),namespace)
    return namespace['app']


@pytest.fixture
def linked(tmp_path,monkeypatch,observed):
    files={name:tmp_path/name for name in ('http_server.py','launch_server.py','server_args.py','engine.py')}
    for name,path in files.items():path.write_bytes(raw(name))
    http=types.ModuleType('sglang.srt.entrypoints.http_server');http.__file__=str(files['http_server.py']);http.app=source_app()
    arguments=types.ModuleType('sglang.srt.server_args');arguments.__file__=str(files['server_args.py'])
    arguments.record_fields=dataclasses.fields
    arguments.ServerArgs=dataclasses.make_dataclass('ServerArgs',[(name,object,None)for name in ('api_key','admin_api_key','watermark_key','watermark_key_b','watermark_config','model_path')])
    original_find=launcher.importlib.util.find_spec;original_import=launcher.importlib.import_module;original_version=launcher.importlib.metadata.version
    monkeypatch.setattr(launcher.importlib.util,'find_spec',lambda name:types.SimpleNamespace(origin=str(files['server_args.py' if name.endswith('server_args') else 'http_server.py' if name.endswith('http_server') else 'engine.py' if name.endswith('engine') else 'launch_server.py'])) if name in launcher.SOURCE_SHA256 else original_find(name))
    monkeypatch.setattr(launcher.importlib,'import_module',lambda name:arguments if name=='sglang.srt.server_args' else http if name=='sglang.srt.entrypoints.http_server' else original_import(name))
    monkeypatch.setattr(launcher.importlib.metadata,'version',lambda name:'0.5.22' if name=='sglang' else original_version(name))
    monkeypatch.setattr(launcher,'SENSOR_PATH',Path(sensor.__file__))
    monkeypatch.setitem(sys.modules,'guard_lifecycle_sensor',sensor)
    return http,files,observed


def test_fixed_official_source_files_and_default_http_dispatch():
    assert hashlib.sha256(raw('engine.py')).hexdigest()==launcher.SOURCE_SHA256['sglang.srt.entrypoints.engine']
    assert hashlib.sha256(raw('server_args.py')).hexdigest()==launcher.SOURCE_SHA256['sglang.srt.server_args']
    assert hashlib.sha256(raw('launch_server.py')).hexdigest()==launcher.SOURCE_SHA256['sglang.launch_server']
    assert hashlib.sha256(raw('http_server.py')).hexdigest()==launcher.SOURCE_SHA256['sglang.srt.entrypoints.http_server']
    tree=ast.parse(raw('launch_server.py'))
    dispatch=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='run_server')
    imports=[node for node in ast.walk(dispatch) if isinstance(node,ast.ImportFrom)]
    assert any(node.module=='sglang.srt.entrypoints.http_server' and any(n.name=='launch_server' for n in node.names) for node in imports)
    # These exact two entry files offer no generic vLLM-style middleware CLI.
    assert b'--middleware' not in raw('launch_server.py') and b'--middleware' not in raw('http_server.py')


def test_same_bootstrap_module_adds_middleware_once_to_original_app(linked):
    http,_files,_events=linked;original=http.app
    assert launcher.prepare() is original and launcher.prepare() is original
    assert len([entry for entry in original.user_middleware if entry.cls is sensor.Middleware])==1
    assert sys.modules['guard_lifecycle_sensor'] is sensor


def test_source_or_bootstrap_missing_stops_before_entrypoint(linked,monkeypatch):
    http,files,_events=linked
    files['http_server.py'].write_bytes(raw('http_server.py')+b'\n')
    with pytest.raises(RuntimeError,match='SOURCE_MISMATCH'):launcher.prepare()
    files['http_server.py'].write_bytes(raw('http_server.py'))
    monkeypatch.delitem(sys.modules,'guard_lifecycle_sensor')
    with pytest.raises(RuntimeError,match='BOOTSTRAP_SENSOR_REQUIRED'):launcher.prepare()
    assert not http.app.user_middleware


def test_wrong_version_or_started_app_is_not_patched(linked,monkeypatch):
    http,_files,_events=linked
    monkeypatch.setattr(launcher.importlib.metadata,'version',lambda name:'0.5.23')
    with pytest.raises(RuntimeError,match='VERSION_MISMATCH'):launcher.prepare()
    monkeypatch.setattr(launcher.importlib.metadata,'version',lambda name:'0.5.22')
    http.app.middleware_stack=object()
    with pytest.raises(RuntimeError,match='ALREADY_STARTED'):launcher.prepare()


def test_runpy_entrypoint_receives_identical_args_and_same_app(linked,monkeypatch):
    http,_files,_events=linked;original=sys.argv;seen=[]
    def run(name,**kwargs):
        seen.append((name,kwargs,list(sys.argv),http.app))
        raise SystemExit(7)
    monkeypatch.setattr(launcher.runpy,'run_module',run)
    with pytest.raises(SystemExit) as error:launcher.main(['--model-path','/fixed/model','--disable-overlap-schedule','--api-key','synthetic-memory-value'])
    assert error.value.code==7 and sys.argv is original
    assert seen[0][0]=='sglang.launch_server' and seen[0][1]=={'run_name':'__main__','alter_sys':True}
    assert seen[0][2]==['sglang.launch_server','--model-path','/fixed/model','--disable-overlap-schedule','--api-key','synthetic-memory-value']
    assert seen[0][3] is http.app


def test_real_asgi_disconnect_binds_real_pinned_init_state_fragment(linked):
    http,_files,(observer,events)=linked
    class Stats:
        def __init__(self,**kwargs):pass
        def set_created_time(self,value):pass
    native=source_function('managers/tokenizer_manager.py','TokenizerManager','_init_req_state',
        {'asyncio':asyncio,'APIServerReqTimeStats':Stats,'ReqState':lambda *args:types.SimpleNamespace(obj=args[3])})
    wrapped=observer.wrap('sglang.srt.managers.tokenizer_manager','TokenizerManager._init_req_state',native)
    manager=types.SimpleNamespace(enable_trace=False,disaggregation_mode=None,rid_to_state={})
    obj=types.SimpleNamespace(rid='synthetic-native-rid',received_time=1,is_single=True,bootstrap_room=None,text='SYNTHETIC_PRIVATE_PROMPT')
    @http.app.post('/v1/chat/completions')
    async def route(request:Request):
        await request.body();wrapped(manager,obj)
        assert (await request.receive())['type']=='http.disconnect'
        return Response(b'normal-response')
    app=launcher.prepare();received=iter([{'type':'http.request','body':b'{}','more_body':False},{'type':'http.disconnect'}]);sent=[]
    async def receive():return next(received)
    async def send(value):sent.append(value)
    asyncio.run(app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST','scheme':'http',
        'path':'/v1/chat/completions','raw_path':b'/v1/chat/completions','query_string':b'',
        'headers':[(b'x-request-id',b'synthetic-server-id')],'server':('127.0.0.1',8000),'client':('127.0.0.1',1234)},receive,send))
    native_row=next(value for event,value in events if event=='SG_NATIVE' and value['phase']=='RETURN')
    disconnected=next(value for event,value in events if event=='SG_ASGI' and value['phase']=='DISCONNECT_RECEIVED')
    assert native_row['asgi_request_sha256']==disconnected['request_sha256']==hashlib.sha256(b'synthetic-server-id').hexdigest()
    assert native_row['asgi_invocation_sha256']==disconnected['invocation_sha256']
    assert native_row['requests'][0]['request_sha256']==sensor.hash_id('synthetic-native-rid')
    assert native_row['requests'][0]['state_present'] is True
    assert manager.rid_to_state and sent[-1]['body']==b'normal-response'
    assert sensor._REQUEST.get() is None and 'SYNTHETIC_PRIVATE_PROMPT' not in json.dumps(events)


def test_actual_vendor_repr_leaks_before_guard_and_keeps_auth_values_after(linked):
    module=launcher.importlib.import_module('sglang.srt.server_args')
    tree=ast.parse(raw('server_args.py'))
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef)and node.name=='ServerArgs')
    method=next(node for node in cls.body if isinstance(node,ast.FunctionDef)and node.name=='__repr__')
    ns={'record_fields':dataclasses.fields};exec(compile(ast.Module(body=[method],type_ignores=[]),'actual_server_args.py','exec'),ns)
    module.ServerArgs.__repr__=ns['__repr__']
    args=module.ServerArgs(api_key=['SYNTHETIC_API_A','SYNTHETIC_API_B'],admin_api_key='SYNTHETIC_ADMIN',watermark_key='SYNTHETIC_WATERMARK',model_path='/approved/model')
    assert 'SYNTHETIC_ADMIN' in repr(args)
    launcher.protect_server_args(module)
    output=io.StringIO();handler=logging.StreamHandler(output);logger=logging.Logger('startup-test');logger.addHandler(handler)
    logger.info('startup %s',args);logger.error('ordinary startup failure %s',args)
    value=output.getvalue()
    assert '/approved/model' in value and 'ordinary startup failure' in value
    assert not any(secret in value for secret in ['SYNTHETIC_API_A','SYNTHETIC_API_B','SYNTHETIC_ADMIN','SYNTHETIC_WATERMARK'])
    assert args.admin_api_key=='SYNTHETIC_ADMIN' and args.api_key==['SYNTHETIC_API_A','SYNTHETIC_API_B']


def test_missing_private_fields_fail_closed_and_repr_error_does_not_echo_secret():
    module=types.SimpleNamespace(ServerArgs=dataclasses.make_dataclass('Bad',[('model_path',str)]),record_fields=dataclasses.fields)
    with pytest.raises(RuntimeError,match='PRIVATE_SERVER_ARGS_UNAVAILABLE'):launcher.protect_server_args(module)
    class Broken:
        api_key='SYNTHETIC_API';admin_api_key='SYNTHETIC_ADMIN'
        @property
        def model_path(self):raise ValueError('SYNTHETIC_PRIVATE_PROMPT token_ids=[12345]')
    module=types.SimpleNamespace(ServerArgs=Broken,record_fields=lambda cls:[types.SimpleNamespace(name=n)for n in ('api_key','admin_api_key','model_path')])
    launcher.protect_server_args(module)
    assert repr(Broken())=='ServerArgs(<configuration unavailable>)'


@pytest.mark.parametrize('changed_source',['none','vendor','launcher','engine'])
def test_fresh_spawn_interpreters_install_guard_before_vendor_repr(tmp_path,monkeypatch,changed_source):
    import subprocess
    from guard.collectors.lifecycle import prepare_assets
    assets=tmp_path/'assets'
    manifest=prepare_assets(assets,Path(__file__).parents[2],profile='sglang-native-lifecycle-metadata/1')
    package=tmp_path/'package';(package/'sglang/srt').mkdir(parents=True)
    (package/'sglang/__init__.py').write_text('')
    (package/'sglang/srt/__init__.py').write_text('')
    actual_tree=ast.parse(raw('server_args.py'))
    actual_cls=next(n for n in actual_tree.body if isinstance(n,ast.ClassDef)and n.name=='ServerArgs')
    actual_repr=next(n for n in actual_cls.body if isinstance(n,ast.FunctionDef)and n.name=='__repr__')
    # Exact vendor renderer with a bounded standard-library record fixture.
    fake='from dataclasses import make_dataclass,fields as record_fields\nServerArgs=make_dataclass("ServerArgs",[(n,object,None)for n in ("api_key","admin_api_key","watermark_key","watermark_key_b","watermark_config","model_path")])\n'+ast.unparse(actual_repr)+'\nServerArgs.__repr__=__repr__\n'
    source=package/'sglang/srt/server_args.py';source.write_text(fake)
    (package/'sglang/srt/entrypoints').mkdir()
    (package/'sglang/srt/entrypoints/__init__.py').write_text('')
    helper=next(n for n in ast.parse(raw('engine.py')).body if isinstance(n,ast.FunctionDef)and n.name=='_server_args_for_logging')
    fake_engine='from __future__ import annotations\nfrom sglang.srt.server_args import ServerArgs\nfrom typing import Dict,Any\nredact_watermark_secrets=lambda value:dict(value)\n'+ast.unparse(helper)+'\n'
    engine_source=package/'sglang/srt/entrypoints/engine.py';engine_source.write_text(fake_engine)
    deployed=(assets/'guard_sglang_launcher.py').read_text().replace(launcher.SOURCE_SHA256['sglang.srt.server_args'],hashlib.sha256(fake.encode()).hexdigest())
    deployed=deployed.replace(launcher.SOURCE_SHA256['sglang.srt.entrypoints.engine'],hashlib.sha256(fake_engine.encode()).hexdigest())
    old_launcher=hashlib.sha256((assets/'guard_sglang_launcher.py').read_bytes()).hexdigest()
    (assets/'guard_sglang_launcher.py').write_text(deployed)
    boot=(assets/'sitecustomize.py').read_text().replace('/guard-lifecycle/',str(assets)+'/').replace(old_launcher,hashlib.sha256(deployed.encode()).hexdigest())
    (assets/'sitecustomize.py').write_text(boot)
    if changed_source=='vendor':source.write_text(fake+'\n# changed after qualification\n')
    if changed_source=='engine':engine_source.write_text(fake_engine+'\n# changed helper source\n')
    if changed_source=='launcher':(assets/'guard_sglang_launcher.py').write_text(deployed+'\n# changed guard\n')
    script=tmp_path/'spawn.py';script.write_text('''import multiprocessing,logging
from sglang.srt.server_args import ServerArgs
from sglang.srt.entrypoints.engine import _server_args_for_logging
ServerArgs.resolved_dict=lambda self:vars(self).copy()

def worker():
    args=ServerArgs(api_key=['SYNTHETIC_API'],admin_api_key='SYNTHETIC_ADMIN',model_path='/approved/model')
    assert args.admin_api_key=='SYNTHETIC_ADMIN' and args.api_key==['SYNTHETIC_API']
    logging.basicConfig(level=logging.INFO,force=True)
    logging.error('ordinary failure %s',args)
    logging.info('server_args=%s',_server_args_for_logging(args))
    assert args.admin_api_key=='SYNTHETIC_ADMIN'
    print(repr(args))

if __name__=='__main__':
    worker()
    child=multiprocessing.get_context('spawn').Process(target=worker);child.start();child.join(10)
    assert child.exitcode==0
''')
    result=subprocess.run([sys.executable,'-B',str(script)],env={**__import__('os').environ,'PYTHONPATH':str(assets)+__import__('os').pathsep+str(package),'PYTHONPYCACHEPREFIX':str(tmp_path/'pycache')},cwd=tmp_path,capture_output=True,text=True,timeout=15)
    output=result.stdout+result.stderr
    assert 'SYNTHETIC_API' not in output and 'SYNTHETIC_ADMIN' not in output
    if changed_source in {'vendor','engine'}:
        assert result.returncode!=0 and 'SOURCE_MISMATCH' in output
    elif changed_source=='launcher':
        assert result.returncode==78 and not output
    else:
        assert result.returncode==0,output
        assert output.count('ordinary failure')==2 and output.count('/approved/model')==6


def test_actual_vendor_logging_dict_is_sanitized_without_mutating_auth():
    helper=next(n for n in ast.parse(raw('engine.py')).body if isinstance(n,ast.FunctionDef)and n.name=='_server_args_for_logging')
    ns={'ServerArgs':object,'Dict':dict,'Any':object,'redact_watermark_secrets':lambda value:dict(value)}
    exec(compile(ast.Module(body=[helper],type_ignores=[]),'actual_engine.py','exec'),ns)
    module=types.ModuleType('fixed_engine');module._server_args_for_logging=ns['_server_args_for_logging']
    values={'api_key':['SYNTHETIC_API'],'admin_api_key':'SYNTHETIC_ADMIN','model_path':'/approved/model','port':8000}
    args=types.SimpleNamespace(resolved_dict=lambda:values)
    assert 'SYNTHETIC_ADMIN' in str(module._server_args_for_logging(args))
    launcher.protect_logging_args(module)
    safe=module._server_args_for_logging(args)
    assert safe=={'model_path':'/approved/model','port':8000}
    assert values['admin_api_key']=='SYNTHETIC_ADMIN'and values['api_key']==['SYNTHETIC_API']
    launcher.protect_logging_args(module)
    assert module._server_args_for_logging(args)==safe
