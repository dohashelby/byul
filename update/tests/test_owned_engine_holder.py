"""Only a test-owned stub child is launched; no serving-engine import or GPU."""
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from tools import owned_engine_holder as holder

REPO=Path(__file__).resolve().parents[1]


def binding(tmp_path):
    root=tmp_path/'data';directory=root/('a'*32);directory.mkdir(parents=True,mode=0o700)
    path=directory/'engine-launch.json'
    value={'pod_uid':'EXAMPLE-pod','container_id':'b'*64,'restart_count':0,'argv':['vllm','serve','EXAMPLE-model']}
    path.write_bytes(holder.canonical(value));path.chmod(0o600)
    return root,path,value


@pytest.mark.parametrize('damage',['pod','container','restart','boolean','shell','nul','extra','symlink','mode','duplicate'])
def test_invalid_launch_binding_is_rejected_before_process_creation(tmp_path,damage):
    root,path,value=binding(tmp_path)
    if damage=='pod':value['pod_uid']='other-pod'
    elif damage=='container':value['container_id']='tag-only'
    elif damage=='restart':value['restart_count']=-1
    elif damage=='boolean':value['restart_count']=True
    elif damage=='shell':value['argv']=['sh','-c','EXAMPLE']
    elif damage=='nul':value['argv'].append('bad\0arg')
    elif damage=='extra':value['resource_recovery_verified']=True
    path.write_bytes(holder.canonical(value))
    if damage=='symlink':
        original=path.with_name('original.json');path.rename(original);path.symlink_to(original)
    elif damage=='mode':path.chmod(0o644)
    elif damage=='duplicate':path.write_bytes(path.read_bytes()[:-1]+b',"restart_count":0}')
    with pytest.raises((OSError,ValueError)):holder.read_launch(path,'EXAMPLE-pod',root=root)


def test_holder_survives_engine_exit_and_keeps_recovery_flags_false(tmp_path):
    root,path,_=binding(tmp_path)
    binary=tmp_path/'bin';binary.mkdir()
    stub=binary/'vllm';stub.write_text('#!'+sys.executable+'\nimport sys\nsys.exit(7)\n');stub.chmod(0o700)
    program='''import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tools.owned_engine_holder import run
raise SystemExit(run(Path(sys.argv[2]),root=Path(sys.argv[3]),max_seconds=10,binding_wait_seconds=2))
'''
    environment={**os.environ,'GUARD_POD_UID':'EXAMPLE-pod','PATH':str(binary)+os.pathsep+os.environ.get('PATH','')}
    process=subprocess.Popen([sys.executable,'-I','-S','-c',program,str(REPO),str(path),str(root)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment)
    try:
        exit_file=path.parent/'holder-engine-exit.json';deadline=time.monotonic()+5
        while not exit_file.exists()and time.monotonic()<deadline:
            assert process.poll()is None
            time.sleep(.01)
        assert exit_file.exists()
        record=json.loads(exit_file.read_bytes())
        assert record['returncode']==7 and record['holder_pid']==process.pid
        assert type(record['engine_start_ticks'])is int and record['engine_start_ticks']>0
        assert process.poll()is None
        for key in('engine_restarted','worker_signals_sent','resources_removed','resource_recovery_verified','scope_complete'):
            assert record[key]is False
        process.send_signal(signal.SIGTERM);stdout,stderr=process.communicate(timeout=5)
        assert process.returncode==0,stderr
        stopped=json.loads((path.parent/'holder-stopped.json').read_bytes())
        assert stopped['reason']=='operator_signal'and stopped['engine_exit_observed']is True
        assert not stdout and not stderr
    finally:
        if process.poll()is None:process.kill();process.wait()


def test_invalid_lifetime_never_launches_child(tmp_path,monkeypatch):
    root,path,_=binding(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('invalid holder limits must not launch a child')
    monkeypatch.setattr(holder.subprocess,'Popen',forbidden)
    for bound in(0,7201,True):
        with pytest.raises(ValueError,match='LIFETIME_INVALID'):holder.run(path,root=root,max_seconds=bound)
