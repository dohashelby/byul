"""Typed selected resource census and actual owned Linux CPU resources only."""
from copy import deepcopy
from datetime import timedelta
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
import pytest
from guard.adapters import resource_recovery_probe as probe
from guard.executors.resource_recovery_contract import project
from guard.models import utcnow
from guard.runtime.kubernetes_resource_recovery import source,PROBE_SOURCE_SHA256

PRIVATE='EXAMPLE_RECOVERY_PRIVATE'


def selection():
    context={'pid':42,'start_ticks':123,'uid':1000,'boot_sha256':'a'*64,'pid_namespace':11,'ipc_namespace':12,
        'mount_namespace':13,'time_namespace':14,'time_offsets_sha256':'b'*64}
    return {'schema':probe.SELECTION_SCHEMA,'owners':[{'id':'1'*64,'rank':0,'context':context,'require_absent':False},
        {'id':'2'*64,'rank':1,'context':{**context,'pid':43},'require_absent':False}],
        'resources':[{'id':'3'*64,'owner_id':'1'*64,'fd':7,'kind':'unix-socket','device':1,'inode':200,'require_absent':True},
        {'id':'4'*64,'owner_id':'1'*64,'fd':8,'kind':'posix-shm-fd','device':2,'inode':300,'require_absent':True}]}


def packet(selected):
    now=time.time_ns();mono=time.monotonic_ns()
    return {'schema':probe.SCHEMA,'selection_sha256':probe.sha(probe.canonical(selected)),
        'observer':{**selected['owners'][0]['context'],'pid':99},'started_wall_ns':now,'finished_wall_ns':now,
        'started_monotonic_ns':mono,'finished_monotonic_ns':mono,
        'owners':[{'id':o['id'],'rank':o['rank'],'status':'match','context':deepcopy(o['context'])} for o in selected['owners']],
        'resources':[{'id':r['id'],'owner_id':r['owner_id'],'status':'match'} for r in selected['resources']],
        'global_resource_release_verified':False,'gpu_release_verified':False,'producer_authenticated':False,
        'processes_signalled':False,'resource_contents_read':False}


def values(selected):
    now=utcnow();return {'recovery_environment_sha256':'a'*64,'recovery_target_sha256':'b'*64,'recovery_prepared_sha256':'c'*64,
        'recovery_selection_sha256':probe.sha(probe.canonical(selected)),'recovery_scenario_sha256':'d'*64,
        'recovery_validity_start':(now-timedelta(seconds=120)).isoformat(),'recovery_validity_end':(now+timedelta(seconds=120)).isoformat(),
        'recovery_branch':'runtime-failure','recovery_executor_backend':'mp','recovery_expected_ranks':['0','1'],'recovery_phase':'after-cleanup',
        'recovery_deadline_basis':'absolute-unconditional','recovery_required_absent_after':(now-timedelta(seconds=30)).isoformat(),
        'recovery_max_clock_skew_ms':100,'recovery_required_resource_kinds':['process','unix-socket','posix-shm-fd','gpu-memory','queue','shared-reference']}


def snapshot(value,bound=True,clock=True):
    now=utcnow().isoformat();return {'collected':True,'packet':value,'owner_binding_verified':bound,'clock_window_verified':clock,
        'started_at':now,'finished_at':now}


@pytest.mark.parametrize('condition',['retained','conditional','before-deadline','absent','fd-reused','pid-reused','unbound','clock-missing'])
def test_absolute_independent_retention_and_unverified_conditional_deadline(condition):
    selected=selection();pol=values(selected);before=packet(selected);after=packet(selected)
    if condition=='conditional':pol['recovery_deadline_basis']='after-verified-failure'
    if condition=='before-deadline':pol['recovery_required_absent_after']=(utcnow()+timedelta(seconds=30)).isoformat()
    if condition in {'absent','fd-reused'}:
        for row in after['resources']:row['status']='absent' if condition=='absent' else 'replaced'
    if condition=='pid-reused':
        after['owners'][0]['status']='replaced';after['owners'][0]['context']['start_ticks']+=1
        for row in after['resources']:row['status']='owner-unavailable'
    result=project([snapshot(before),snapshot(after,condition!='unbound',condition!='clock-missing')],selected,pol)
    assert result['absolute_deadline_violated']==(condition=='retained')
    assert result['selected_absence_observed']==(condition=='absent')
    assert all(result[k] is False for k in ('global_resources_released','next_normal_launch_verified','scope_complete'))
    assert result['missing'] and PRIVATE not in json.dumps(result)


@pytest.mark.parametrize('mutation',['duplicate-owner','duplicate-fd','bool-pid','extra-path','mixed-namespace','wrong-snapshot-id','bool-rank','overclaimed-release'])
def test_closed_selection_and_packet_reject_invalid_or_overclaimed_input(mutation):
    selected=selection();value=packet(selected)
    if mutation=='duplicate-owner':selected['owners'].append(deepcopy(selected['owners'][0]))
    if mutation=='duplicate-fd':selected['resources'][1]['fd']=7
    if mutation=='bool-pid':selected['owners'][0]['context']['pid']=True
    if mutation=='extra-path':selected['resources'][0]['path']=PRIVATE
    if mutation=='mixed-namespace':selected['owners'][1]['context']['ipc_namespace']+=1
    if mutation=='wrong-snapshot-id':value['selection_sha256']='f'*64
    if mutation=='bool-rank':value['owners'][0]['rank']=False
    if mutation=='overclaimed-release':value['gpu_release_verified']=True
    with pytest.raises(ValueError):probe.validate_selection(selected);probe.validate_result(value,selected)


def test_reviewed_helper_source_is_literal_and_source_change_is_rejected(tmp_path,monkeypatch):
    assert hashlib.sha256(source().encode()).hexdigest()==PROBE_SOURCE_SHA256
    changed=tmp_path/'probe.py';changed.write_text('unreviewed '+PRIVATE)
    monkeypatch.setattr(probe,'__file__',str(changed))
    with pytest.raises(ValueError,match='RESOURCE_PROBE_SOURCE_UNREVIEWED'):source()


HARNESS=r'''
import hashlib,json,os,runpy,socket,subprocess,sys,tempfile
p=runpy.run_path(sys.argv[1]);owned=None;sock=None;shm=None;alias=None;child=None
try:
 with tempfile.TemporaryDirectory(prefix='byul-a1-owned-recovery-') as directory:
  sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);sock.bind(directory+'/owned.sock')
  shm=tempfile.TemporaryFile(dir='/dev/shm',prefix='byul-a1-owned-')
  context=p['context'](os.getpid());owner={'id':'1'*64,'rank':0,'context':context,'require_absent':False}
  rows=[]
  for fd,kind,identity in ((sock.fileno(),'unix-socket','2'*64),(shm.fileno(),'posix-shm-fd','3'*64)):
   info=os.fstat(fd);rows.append({'id':identity,'owner_id':owner['id'],'fd':fd,'kind':kind,'device':info.st_dev,'inode':info.st_ino,'require_absent':True})
  selection={'schema':p['SELECTION_SCHEMA'],'owners':[owner],'resources':rows}
  before=p['observe'](selection);p['validate_result'](before,selection)
  assert all(r['status']=='match' for r in before['resources'])
  alias=os.dup(sock.fileno());sock.close();sock=None;shm.close();shm=None
  after=p['observe'](selection);p['validate_result'](after,selection)
  assert all(r['status']=='absent' for r in after['resources'])
  assert os.fstat(alias).st_ino==rows[0]['inode']
  assert not after['global_resource_release_verified'] and not after['gpu_release_verified']
  child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  child_context=p['context'](child.pid)
  child_selection={'schema':p['SELECTION_SCHEMA'],'owners':[{'id':'4'*64,'rank':0,'context':child_context,'require_absent':True}],'resources':[]}
  assert p['observe'](child_selection)['owners'][0]['status']=='match'
  child.terminate();child.wait();child=None
  assert p['observe'](child_selection)['owners'][0]['status']=='absent'
  print(json.dumps({'success':True,'owned_socket_and_shm_observed':True,'selected_fd_absent_alias_resource_still_live':True,'owned_child_exit_observed':True,'serving_TP_GPU':False}))
finally:
 if child is not None:child.terminate();child.wait()
 if alias is not None:os.close(alias)
 if sock is not None:sock.close()
 if shm is not None:shm.close()
'''


def test_actual_owned_linux_socket_shm_alias_and_child_are_not_global_release():
    path=Path(probe.__file__).resolve()
    if sys.platform=='linux':command=[sys.executable,'-c',HARNESS,str(path)]
    else:
        assert path.drive.upper()=='C:'
        mapped='/mnt/c/'+path.as_posix()[3:]
        command=['wsl.exe','-d','Ubuntu','-u','youngjo','--','/home/youngjo/.cache/byul-a1-01a185f9-linux/venv/bin/python','-c',HARNESS,mapped]
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert result.returncode==0,(result.stdout.decode(errors='replace'),result.stderr.decode(errors='replace'))
    receipt=json.loads(result.stdout);assert receipt['success'] and receipt['selected_fd_absent_alias_resource_still_live'] and receipt['owned_child_exit_observed']
    assert receipt['serving_TP_GPU'] is False
