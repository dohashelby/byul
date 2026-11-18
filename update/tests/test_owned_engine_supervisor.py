"""Actual owned Python children, never an engine image or GPU qualification."""
from copy import deepcopy
import hashlib
import hmac
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from tools import owned_engine_supervisor as harness


def config():
    return {'schema':harness.SCHEMA,'session':'a'*32,'key':'b'*64,
            'module':'EXAMPLE_owned_child','arguments':[], 'maximum_seconds':30,'maximum_generations':3}


@pytest.fixture
def child_config(tmp_path, monkeypatch):
    module=tmp_path/'EXAMPLE_owned_child.py'
    module.write_text('import time\nwhile True: time.sleep(1)\n')
    monkeypatch.setenv('PYTHONPATH',str(tmp_path))
    return config()


def signed(supervisor, action='status', nonce='c'*32):
    value={'session':supervisor.config['session'],'action':action,'nonce':nonce,'issued_ns':supervisor.clock()}
    return {'request':value,'hmac_sha256':hmac.new(bytes.fromhex(supervisor.config['key']),
        b'guard-owned-engine-supervisor-request/1\0'+harness.canonical(value),hashlib.sha256).hexdigest()}


@pytest.mark.parametrize('damage',['schema','extra','shell','module','arguments','newline','seconds-bool','too-long','generations','key'])
def test_settings_refuse_unbounded_or_nonmodule_launches(damage):
    value=config()
    if damage=='schema':value['schema']='EXAMPLE-other'
    elif damage=='extra':value['environment']={'SECRET':'EXAMPLE-private'}
    elif damage=='shell':value['module']='sh -c'
    elif damage=='module':value['module']='module/../other'
    elif damage=='arguments':value['arguments']=[{}]
    elif damage=='newline':value['arguments']=['a\nb']
    elif damage=='seconds-bool':value['maximum_seconds']=True
    elif damage=='too-long':value['maximum_seconds']=2701
    elif damage=='generations':value['maximum_generations']=9
    else:value['key']='EXAMPLE'
    with pytest.raises(ValueError,match='SETTINGS_INVALID'):harness.Supervisor(value)


@pytest.mark.parametrize('damage',['hmac','nonce','session','action','future','old','extra','bool-clock'])
def test_requests_cannot_signal_selected_pids_or_replay_other_sessions(damage):
    supervisor=harness.Supervisor(config(),clock=lambda:10**10)
    envelope=signed(supervisor);request=envelope['request']
    if damage=='hmac':envelope['hmac_sha256']='0'*64
    elif damage=='nonce':request['nonce']='bad'
    elif damage=='session':request['session']='d'*32
    elif damage=='action':request['action']='kill-pid'
    elif damage=='future':request['issued_ns']+=1
    elif damage=='old':request['issued_ns']-=5*10**9+1
    elif damage=='extra':request['pid']=os.getpid()
    else:request['issued_ns']=True
    with pytest.raises(ValueError):supervisor.handle(envelope)
    assert not supervisor.generations


def test_authenticated_status_has_no_key_arguments_or_recovery_claim():
    supervisor=harness.Supervisor(config())
    envelope=signed(supervisor)
    result=supervisor.handle(envelope)
    assert result['fault_causality_verified']is result['whole_resources_released']is result['new_normal_inference_verified']is False
    assert supervisor.config['key']not in json.dumps(result)and supervisor.config['module']not in json.dumps(result)
    with pytest.raises(ValueError):supervisor.handle(envelope)


def test_private_settings_refuse_links_permissions_and_duplicate_keys(tmp_path):
    path=tmp_path/'settings.json';path.write_bytes(harness.canonical(config()));path.chmod(0o600)
    assert harness.private_file(path)==config()
    path.chmod(0o644)
    with pytest.raises(ValueError):harness.private_file(path)
    path.chmod(0o600);link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(ValueError):harness.private_file(link)
    path.write_text('{"key":1,"key":2}')
    with pytest.raises(ValueError):harness.private_file(path)


def test_child_identity_shutdown_and_fresh_generation_use_only_owned_pidfds(child_config):
    supervisor=harness.Supervisor(child_config)
    unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    try:
        first=supervisor.launch();generation=supervisor.generations[0]
        assert first['generations'][0]['identity']==harness.process(generation.child.pid)
        with pytest.raises(ValueError,match='PREVIOUS_CHILD_RUNNING'):supervisor.launch()
        supervisor.handle(signed(supervisor,'shutdown-child'))
        assert generation.child.wait(timeout=3)==-signal.SIGTERM
        assert unrelated.poll()is None
        second=supervisor.launch()
        assert len(second['generations'])==2 and second['generations'][0]['shutdown_requested']
        assert second['generations'][1]['identity']['pid']!=generation.identity['pid']
        assert not second['whole_resources_released']and not second['new_normal_inference_verified']
    finally:
        supervisor.close()
        unrelated.terminate();unrelated.wait(timeout=3)


def test_remaining_group_or_incomplete_inventory_refuses_restart(child_config,monkeypatch):
    supervisor=harness.Supervisor(child_config)
    try:
        supervisor.launch();supervisor.shutdown_child();supervisor.generations[0].child.wait(timeout=3)
        monkeypatch.setattr(harness,'group_members',lambda group:[{'pid':123,'EXAMPLE':'remaining reference'}])
        with pytest.raises(ValueError,match='PREVIOUS_GROUP_REMAINS'):supervisor.launch()
        def unavailable(group):raise ValueError('EXAMPLE-partial inventory')
        monkeypatch.setattr(harness,'group_members',unavailable)
        with pytest.raises(ValueError,match='EXAMPLE-partial inventory'):supervisor.launch()
        assert len(supervisor.generations)==1
    finally:supervisor.close()


def test_expired_lease_refuses_new_launch_but_status_and_shutdown_remain(child_config):
    now=[0];supervisor=harness.Supervisor(child_config,clock=lambda:now[0])
    try:
        supervisor.launch();now[0]=supervisor.deadline_ns
        with pytest.raises(ValueError,match='LEASE_EXPIRED'):supervisor.launch()
        assert supervisor.handle(signed(supervisor))['generations'][0]['returncode']is None
        supervisor.handle(signed(supervisor,'shutdown-child',nonce='d'*32))
    finally:supervisor.close()


@pytest.mark.parametrize('long_path', [False, True])
def test_owner_socket_roundtrip_returns_authenticated_current_status_and_cleans_child(child_config,tmp_path,long_path):
    path=tmp_path/'settings.json';path.write_bytes(harness.canonical(child_config));path.chmod(0o600)
    parent=tmp_path
    if long_path:
        parent=tmp_path/('long-parent-'+'a'*100)/('nested-'+'b'*100)
        parent.mkdir(parents=True)
    directory=parent/'new-supervisor'
    command=[sys.executable,str(Path(harness.__file__).resolve()),'--config',str(path),'--new-directory',str(directory)]
    server=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+5
        while not(directory/'control.sock').exists()and server.poll()is None and time.monotonic()<deadline:time.sleep(.01)
        result=harness.client(path,directory,'status')
        child_pid=result['generations'][0]['identity']['pid']
        assert result['supervisor']['pid']==server.pid and harness.process(child_pid)['parent_pid']==server.pid
        assert not result['fault_causality_verified']and not result['new_normal_inference_verified']
        harness.client(path,directory,'shutdown-child')
        server.terminate();assert server.wait(timeout=5)==0
        assert not(directory/'control.sock').exists()
        assert not Path('/proc',str(child_pid)).exists()
    finally:
        if server.poll()is None:server.terminate();server.wait(timeout=5)
        server.stderr.close()


def test_control_directory_pins_inode_and_refuses_public_or_linked_directories(tmp_path):
    directory=tmp_path/'private';directory.mkdir(mode=0o700)
    with harness.control_directory(directory)as path:
        held=path.parent.stat()
        assert (held.st_dev,held.st_ino)==(directory.stat().st_dev,directory.stat().st_ino)
        directory.rename(tmp_path/'moved')
        directory.mkdir(mode=0o700)
        assert path.parent.stat().st_ino==held.st_ino!=directory.stat().st_ino
        assert len(os.fsencode(path))<108
    directory.chmod(0o755)
    with pytest.raises(ValueError,match='OWNER_UNVERIFIED'):
        with harness.control_directory(directory):pass
    link=tmp_path/'linked';link.symlink_to(tmp_path/'moved',target_is_directory=True)
    with pytest.raises(OSError):
        with harness.control_directory(link):pass


def test_standalone_adoption_reaps_only_exited_owned_descendants_and_restores_flag(tmp_path):
    """Actual Linux subreaper in a disposable process, never the pytest parent."""
    (tmp_path/'EXAMPLE_owned_orphans.py').write_text('''import os,sys,time
from pathlib import Path
if os.fork()==0:
    deadline=time.monotonic()+5
    while not Path(sys.argv[1]).exists()and time.monotonic()<deadline:time.sleep(.01)
    os._exit(0)
time.sleep(.1)
os._exit(0)
''')
    program='''import ctypes,hashlib,json,os,subprocess,sys,time
from pathlib import Path
from tools import owned_engine_supervisor as h
def adoption():
    value=ctypes.c_int();library=ctypes.CDLL(None)
    assert library.prctl(37,ctypes.byref(value),0,0,0)==0
    return value.value
before=adoption();release=Path(sys.argv[1])
cfg={'schema':h.SCHEMA,'session':'a'*32,'key':'b'*64,'module':'EXAMPLE_owned_orphans',
     'arguments':[str(release)],'maximum_seconds':30,'maximum_generations':3}
supervisor=h.Supervisor(cfg)
unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],start_new_session=True)
try:
    with h.child_adoption():
        assert adoption()==1
        supervisor.launch();first=supervisor.generations[0]
        assert first.child.wait(timeout=3)==0
        supervisor.reap_exited_descendants()
        assert not supervisor.reaped and unrelated.poll()is None
        try:supervisor.launch()
        except ValueError as error:assert str(error)=='SUPERVISOR_PREVIOUS_GROUP_REMAINS'
        else:raise AssertionError('live adopted child must block restart')
        release.touch()
        deadline=time.monotonic()+5
        while len(supervisor.reaped)<1 and time.monotonic()<deadline:
            supervisor.reap_exited_descendants();time.sleep(.01)
        assert len(supervisor.reaped)==1
        row=supervisor.reaped[0]
        assert row['generation']==0 and row['group']==row['session']==first.identity['pid']
        assert row['pid']!=first.identity['pid']and not Path('/proc',str(row['pid'])).exists()
        supervisor.launch();second=supervisor.generations[1]
        assert second.child.wait(timeout=3)==0
        deadline=time.monotonic()+5
        while len(supervisor.reaped)<2 and time.monotonic()<deadline:
            supervisor.reap_exited_descendants();time.sleep(.01)
        assert len(supervisor.reaped)==2 and unrelated.poll()is None
        status=supervisor.status()
        assert status['reaped_descendant_count']==2
        assert status['reaped_descendants_sha256']==hashlib.sha256(h.canonical(status['reaped_descendants'])).hexdigest()
        assert not status['whole_resources_released']and not status['new_normal_inference_verified']
    assert adoption()==before
    print(json.dumps({'adoption_restored':True,'owned_exited_descendants_reaped':2,'unrelated_child_alive':unrelated.poll()is None}))
finally:
    supervisor.close()
    if unrelated.poll()is None:unrelated.terminate()
    unrelated.wait(timeout=3)
'''
    environment={**os.environ,'PYTHONPATH':os.pathsep.join((str(tmp_path),str(Path(harness.__file__).resolve().parents[1])))}
    result=subprocess.run([sys.executable,'-c',program,str(tmp_path/'release')],env=environment,
                          cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'adoption_restored':True,'owned_exited_descendants_reaped':2,'unrelated_child_alive':True}
