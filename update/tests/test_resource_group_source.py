"""Standalone source and owned-container binding tests, without Kubernetes/GPU."""
from copy import deepcopy
import hashlib
import subprocess
import sys
import time

import pytest

from guard.adapters import resource_group_probe as group
from guard.models import canonical_json
from guard.runtime import resource_group_source as source
from guard.runtime.kubernetes_resource_recovery import KubernetesResourceRecoveryReader
from tests.test_resource_group_probe import owned_child


def test_exact_package_free_program_refuses_duplicate_input_without_scanning():
    program=source.source()
    assert 'from guard.adapters'not in program and 'sys.modules'not in program
    compile(program,'reviewed-test-source','exec')
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',program],input=b'{"schema":1,"schema":2}',capture_output=True)
    assert result.returncode==2 and result.stdout==b'{"diagnostic":"RESOURCE_GROUP_CENSUS_UNAVAILABLE"}'
    assert not result.stderr


@pytest.mark.parametrize('module',['group','selected'])
def test_either_changed_helper_is_rejected_before_container_exec(module,tmp_path,monkeypatch):
    changed=tmp_path/'changed.py';changed.write_text('unreviewed source')
    monkeypatch.setattr(getattr(source,module),'__file__',str(changed))
    with pytest.raises(ValueError,match='RESOURCE_GROUP_SOURCE_UNREVIEWED'):source.source()


def reader(scope,packet,mode):
    value=KubernetesResourceRecoveryReader.__new__(KubernetesResourceRecoveryReader)
    value.binary='kubectl';value.deadline=time.monotonic()+30;value.gap=False;value._parent_cleanup_failures=0;value.group_censuses=[]
    value.binding={'context':'owned-lab','engine':{'namespace':'owned-ns','pod':'owned-pod','container':'engine'}}
    owners=[]
    def owner(timeout):
        assert 0<timeout<=10
        owners.append(1)
        return ('owned-pod-uid','engine-container-id',2 if mode=='owner-changed'and len(owners)>1 else 1)
    value._owner=owner
    sent=[]
    def run(argv,**kwargs):
        sent.append(argv)
        assert '--namespace=owned-ns'in argv and '--container=engine'in argv and '-i'in argv
        command=argv[argv.index('--')+1:]
        assert command[:5]==['python3','-I','-S','-B','-c']and float(command[-1])<=5
        assert kwargs['input_bytes']==canonical_json(scope)and kwargs['max_stdout_bytes']==group.MAX_OUTPUT
        assert kwargs['env_extra']=={}and kwargs['timeout']<=5
        if mode=='exec-error':raise OSError('PRIVATE_EXCEPTION')
        body=deepcopy(packet)
        if mode=='clock-skew':body['started_wall_ns']+=10**15;body['finished_wall_ns']+=10**15
        if mode=='invalid':body['gpu_release_verified']=True
        pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False,'pipes_closed':True,'read_errors':[]}
        if mode=='pipe-cleanup':pipe.update(pipes_closed=False,read_errors=['child_reap'])
        if mode=='truncated':pipe['truncated']=True
        return {'returncode':0,'stdout_bytes':canonical_json(body),'capture':pipe}
    value.runner=run
    return value,sent


@pytest.mark.parametrize('mode',['normal','owner-changed','exec-error','clock-skew','invalid','pipe-cleanup','truncated'])
def test_census_requires_exact_owner_clock_closed_packet_and_parent_cleanup(owned_child,mode):
    child,scope=owned_child;packet=group.observe(scope,process_ids=[child.pid]);value,sent=reader(scope,packet,mode)
    row=value.group_resource_census(scope,max_clock_skew_ms=1000)
    assert len(sent)==1 and value.group_censuses==[row]
    assert row['probe_source_sha256']==source.GROUP_SHA256
    assert row['program_sha256']==hashlib.sha256(source.source().encode()).hexdigest()
    assert row['collected']==(mode not in {'exec-error','invalid','truncated'})
    assert row['owner_binding_verified']==(mode not in {'owner-changed','exec-error','invalid','truncated','pipe-cleanup'})
    assert row['clock_window_verified']==(mode not in {'exec-error','invalid','truncated','clock-skew'})
    assert value.gap==(mode!='normal')
    assert b'PRIVATE_EXCEPTION'not in canonical_json(row)
    if row['collected']:assert row['packet']['whole_group_recovery_verified']is False
