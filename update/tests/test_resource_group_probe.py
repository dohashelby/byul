"""Real kernel metadata of owned Python children; no engine or GPU execution."""
from copy import deepcopy
from hashlib import sha256
import json
import os
import subprocess
import sys

import pytest

from guard.adapters import resource_group_probe as group
from guard.adapters import resource_recovery_probe as selected


@pytest.fixture
def owned_child():
    code='''
import os,socket,sys
from multiprocessing.shared_memory import SharedMemory
left,right=socket.socketpair()
memory=SharedMemory(create=True,size=4096)
print(os.getpid(),flush=True)
try:sys.stdin.buffer.read(1)
finally:
    left.close();right.close();memory.close();memory.unlink()
'''
    child=subprocess.Popen([sys.executable,'-I','-S','-B','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        assert int(child.stdout.readline())==child.pid
        scope={'schema':group.SELECTION_SCHEMA,'owners':[{'id':sha256(b'owned-child-only').hexdigest(),'context':selected.context(child.pid)}]}
        yield child,scope
    finally:
        if child.poll()is None:
            child.stdin.write(b'x');child.stdin.flush()
        _,error=child.communicate()
        assert child.returncode==0,error


def test_owned_child_socket_and_mapped_shm_are_kernel_metadata_only(owned_child):
    child,scope=owned_child
    packet=group.observe(scope,process_ids=[child.pid])
    assert packet['owners']==[{'id':scope['owners'][0]['id'],'status':'match'}]
    row,=packet['processes'];assert row['stable']and row['generation_stable']and row['parent_pid']==os.getpid()
    assert len([r for r in packet['references']if r['kind']=='socket'])==2
    assert any(r['kind']=='posix-shm'and r['fd']is None for r in packet['references'])
    assert any(r['kind']=='posix-shm'and type(r['fd'])is int for r in packet['references'])
    raw=group.canonical(packet)
    assert b'/dev/shm/'not in raw and b'psm_'not in raw and b'contents'not in raw.replace(b'resource_contents_read',b'')
    assert packet['namespace_scan_complete']is False and packet['explicit_pid_subset']is True
    assert all(packet[k]is False for k in ('whole_group_recovery_verified','gpu_release_verified','producer_authenticated','processes_signalled','resource_contents_read'))


def test_observer_fd_directory_stays_open_during_own_scan():
    scope={'schema':group.SELECTION_SCHEMA,'owners':[{'id':sha256(b'own-test-process').hexdigest(),'context':selected.context(os.getpid())}]}
    packet=group.observe(scope,process_ids=[os.getpid()])
    row,=packet['processes']
    assert row['stable']and row['generation_stable']and not packet['diagnostic_codes']


def test_remaining_references_survive_partial_scope_and_child_exit_cannot_prove_global_release(owned_child):
    child,scope=owned_child
    before=group.observe(scope,process_ids=[child.pid]);after=group.observe(scope,process_ids=[child.pid])
    result=group.compare(before,after,scope)
    assert result['selected_kernel_objects']>=3 and result['remaining_kernel_objects']
    assert result['selected_kernel_reference_absence_observed']is False
    child.stdin.write(b'x');child.stdin.flush();child.wait()
    ended=group.observe(scope,process_ids=[child.pid]);result=group.compare(before,ended,scope)
    assert ended['owners'][0]['status']=='unavailable'and not result['remaining_kernel_objects']
    assert result['selected_kernel_reference_absence_observed']is False and result['whole_group_recovery_verified']is False


@pytest.mark.parametrize('damage',[
    lambda p:p.update(namespace_scan_complete=True),
    lambda p:p.update(explicit_pid_subset=False,namespace_scan_complete=True),
    lambda p:p.update(gpu_release_verified=True),
    lambda p:p.update(whole_group_recovery_verified=True),
    lambda p:p.update(producer_authenticated=True),
    lambda p:p.update(resource_contents_read=True),
    lambda p:p.update(processes_signalled=True),
    lambda p:p['references'][0].update(inode=True),
    lambda p:p['references'][0].update(start_ticks=p['references'][0]['start_ticks']+1),
    lambda p:p['references'][0].update(raw_path='/dev/shm/private'),
    lambda p:p['processes'][0].update(pid=True),
    lambda p:p['owners'][0].update(status='absent'),
    lambda p:p.update(selection_sha256='0'*64),
    lambda p:p['observer'].update(ipc_namespace=p['observer']['ipc_namespace']+1),
    lambda p:p.update(named_shm=[{'device':1,'inode':1}]),
])
def test_packet_cannot_promote_partial_collection_or_change_binding(owned_child,damage):
    child,scope=owned_child;packet=group.observe(scope,process_ids=[child.pid]);damage(packet)
    with pytest.raises(ValueError,match='RESOURCE_GROUP_CENSUS_INVALID'):group.validate_result(packet,scope)


@pytest.mark.parametrize('change',[
    lambda s:s['owners'][0].update(id='bad'),
    lambda s:s['owners'].append(deepcopy(s['owners'][0])),
    lambda s:s['owners'][0]['context'].update(pid=True),
    lambda s:s.update(unreviewed_scope=True),
])
def test_selection_is_closed_and_generation_bound(owned_child,change):
    _,scope=owned_child;scope=deepcopy(scope);change(scope)
    with pytest.raises(ValueError,match='RESOURCE_GROUP_SELECTION_INVALID'):group.selection(scope)


@pytest.mark.parametrize('ids',[[],[True],[0],[-1],[1,1],'all'])
def test_invalid_pid_subset_never_scans(owned_child,ids):
    _,scope=owned_child
    with pytest.raises(ValueError,match='RESOURCE_GROUP_PID_SUBSET_INVALID'):group.observe(scope,process_ids=ids)


def test_reverse_or_overlapping_censuses_cannot_prove_recovery(owned_child):
    child,scope=owned_child;first=group.observe(scope,process_ids=[child.pid]);last=group.observe(scope,process_ids=[child.pid])
    with pytest.raises(ValueError,match='RESOURCE_GROUP_CENSUS_SCOPE_MISMATCH'):group.compare(last,first,scope)
    with pytest.raises(ValueError,match='RESOURCE_GROUP_CENSUS_SCOPE_MISMATCH'):group.compare(first,first,scope)


def test_positive_references_are_preserved_if_later_maps_read_fails(owned_child,monkeypatch):
    child,scope=owned_child;read=selected.read_small
    def interrupted(path,limit):
        if path=='/proc/'+str(child.pid)+'/maps':raise OSError('private diagnostic must not be exported')
        return read(path,limit)
    monkeypatch.setattr(selected,'read_small',interrupted)
    packet=group.observe(scope,process_ids=[child.pid]);row,=packet['processes']
    assert row['generation_stable']and not row['stable']and packet['references']
    assert packet['diagnostic_codes']==['RESOURCE_GROUP_PROCESS_UNAVAILABLE_OR_CHANGED']
    assert b'private diagnostic'not in group.canonical(packet)
