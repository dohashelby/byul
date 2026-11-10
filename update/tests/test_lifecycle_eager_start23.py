"""Eager START is opt-in for23/24/SG; installation failure cleans owned state."""
import json,sys
from types import SimpleNamespace
import pytest
from guard.adapters import vllm_lifecycle_sensor as base
from guard.adapters import vllm_termination_sensor as term,vllm_fault_sensor as fault,sglang_request_termination_sensor as sglang
from tests.test_lifecycle_sensor import CONTEXT,FakeOS,SilentThread

@pytest.fixture
def installation(monkeypatch):
    fake=FakeOS();monkeypatch.setattr(base,'os',fake)
    monkeypatch.setattr(base,'_context',lambda:dict(CONTEXT));monkeypatch.setattr(base,'_open_directory',lambda _:10)
    monkeypatch.setattr(base.threading,'Thread',SilentThread)
    monkeypatch.setattr(base,'_OBSERVER',None);monkeypatch.setattr(base,'SOURCE_SHA',{})
    monkeypatch.setattr(sys,'meta_path',list(sys.meta_path))
    monkeypatch.setattr(base.atexit,'register',lambda _:None)
    config={'session':'a'*32,'output_dir':'/guard-lifecycle/data/'+'a'*32,'control_path':'/guard-lifecycle/data/'+'a'*32+'/SEALED'}
    monkeypatch.setattr(base,'load_settings',lambda _:(config,'b'*64))
    return fake

@pytest.mark.parametrize('eager',[False,True])
def test_install_default_lazy_and_eager_start_have_same_owned_writer_contract(installation,eager):
    fake=installation
    assert base.install(base.CONFIG_PATH,open_sink=eager)
    observer=base._OBSERVER
    try:
        assert (observer.writer is not None)is eager
        rows=[json.loads(line)for line in bytes(fake.data).splitlines()]
        assert [r['event']for r in rows]==(['START']if eager else[])
        if eager:assert rows[0]['payload']['context']==CONTEXT and fake.name=='pid-42-99.jsonl'
    finally:observer.close()

@pytest.mark.parametrize('failure',['write','private-directory'])
def test_eager_start_failure_never_publishes_finder_or_observer_and_closes_owned_fd(installation,failure):
    fake=installation;before=list(sys.meta_path)
    if failure=='write':fake.failure=True
    else:fake.fstat=lambda _:SimpleNamespace(st_uid=0,st_mode=0o40700)
    assert base.install(base.CONFIG_PATH,open_sink=True)is False
    assert base._OBSERVER is None and sys.meta_path==before
    assert 10 in fake.closed
    if failure=='write':assert 11 in fake.closed

@pytest.mark.parametrize('sensor',[term,fault,sglang])
def test_selected_termination_sensors_request_the_opt_in_start_sink(monkeypatch,sensor):
    calls=[]
    def install(path,**kwargs):calls.append((path,kwargs));return True
    private=SimpleNamespace(install=install)
    monkeypatch.setattr(sensor,'_BASE',None);monkeypatch.setattr(sensor,'_private_base',lambda:private)
    assert sensor.install('/selected/settings.json')is True
    assert calls==[('/selected/settings.json',{'open_sink':True})]
    assert sensor._BASE is private



def test_sglang_import_only_python_has_own_start_and_end_without_loading_engine(monkeypatch):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('_sg_eager_base',Path(base.__file__))
    private=importlib.util.module_from_spec(spec);spec.loader.exec_module(private)
    sglang.configure(private);fake=FakeOS()
    monkeypatch.setattr(private,'os',fake)
    monkeypatch.setattr(private,'_context',lambda:dict(CONTEXT))
    monkeypatch.setattr(private,'_open_directory',lambda _:10)
    monkeypatch.setattr(private.threading,'Thread',SilentThread)
    monkeypatch.setattr(private.atexit,'register',lambda _:None)
    monkeypatch.setattr(sys,'meta_path',list(sys.meta_path))
    cfg={'session':'a'*32,'output_dir':'/guard-lifecycle/data/'+'a'*32,'control_path':'/guard-lifecycle/data/'+'a'*32+'/SEALED'}
    monkeypatch.setattr(private,'load_settings',lambda _:(cfg,'b'*64))
    monkeypatch.setattr(sglang,'_BASE',None);monkeypatch.setattr(sglang,'_private_base',lambda:private)
    before={n for n in sys.modules if n.startswith('sglang')}
    assert sglang.install(private.CONFIG_PATH) is True
    assert {n for n in sys.modules if n.startswith('sglang')}==before
    rows=[json.loads(line)for line in bytes(fake.data).splitlines()]
    assert [r['event']for r in rows]==['START']
    assert rows[0]['payload']['profile']==sglang.PROFILE
    assert rows[0]['payload']['context']==CONTEXT
    private._OBSERVER.close()
    rows=[json.loads(line)for line in bytes(fake.data).splitlines()]
    assert rows[-1]['event']=='END'
