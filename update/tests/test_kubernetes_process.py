"""Same common API/exec boundary; no new fake product or worker inference."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors.kubernetes_process import collect, load_inputs
from guard.executors.container_config import parse_proc_status
from guard.models import canonical_json, DataOrigin
from guard.runtime import kubernetes_http as kube
from guard.runtime import kubernetes_process as process
from tests.test_model_preparation_integration import full_inputs, FullWorld

PRIVATE='EXAMPLE_PRIVATE_PROCESS_VALUE_263b'
T='2026-10-29T09:00:00+00:00'


def packet(paths=()):
    row={'pid':1,'start_ticks':200,'name':'EngineCore','uids':[2000]*4,'uid_effective':2000,
         'cap_eff':'0000000000000000','mount_namespace_inode':111,
         'environment_names':['PATH','TOKEN_NAME'],'environment_names_complete':True,
         'pid_identity_stable':True,'started_at':T,'finished_at':T,'diagnostic_codes':[]}
    paths_=[{'path':p,'state':'metadata','metadata':{'device':1,'inode':2,'mode':0o100600,
        'uid':0,'gid':0,'size':17,'mtime_ns':1,'ctime_ns':1},'metadata_identity_stable':True,
        'path_chain_sha256':'a'*64,'access_tested':False,'started_at':T,'finished_at':T,
        'diagnostic_codes':[]} for p in paths]
    return {'schema':process.SCHEMA,'started_at':T,'finished_at':T,
        'capture_subject':{'pid':99,'uid_effective':10001,'pid_namespace_inode':101,
                           'mount_namespace_inode':102,'identity_stable':True},
        'capture_process_excluded':True,'processes':[row],'pid_count_before':1,'pid_count_after':1,
        'pid_inventory_stable':True,'process_metadata_complete':True,'candidate_metadata':paths_,
        'worker_subject_verified':False,'worker_inventory_complete':False,'diagnostic_codes':[]}


class World(FullWorld):
    def __init__(self,target,mode=None):
        super().__init__(target);self.mode=mode;self.process_calls=0
    def runner(self,argv,**kwargs):
        if 'exec' in argv:
            assert '-i' in argv and '--container=engine' in argv
            assert self.target['kubernetes']['engine']['pod'] in argv
            assert self.target['kubernetes']['probe']['pod'] not in argv
            assert argv[-2]==process.source() and PRIVATE not in ' '.join(argv)
            spec=json.loads(kwargs['input_bytes']);assert set(spec)=={'candidate_paths'}
            value=packet(spec['candidate_paths']);self.process_calls+=1;self.after_exec=True
            if self.mode=='environment-gap':
                row=value['processes'][0];row.update(environment_names_complete=False,
                    diagnostic_codes=['PROCESS_ENVIRONMENT_NAMES_UNAVAILABLE'])
                value['process_metadata_complete']=False
            elif self.mode=='pid-changed':
                value['processes'][0].update(pid_identity_stable=False,diagnostic_codes=['PROCESS_IDENTITY_CHANGED'])
                value.update(pid_inventory_stable=False,process_metadata_complete=False)
            elif self.mode=='forged-worker':value['worker_subject_verified']=True
            elif self.mode=='private-extra':value['argv']=PRIVATE
            elif self.mode=='value-in-name':value['processes'][0]['environment_names']=['TOKEN='+PRIVATE]
            result={'returncode':0,'stdout_bytes':canonical_json(value),'stderr_bytes':PRIVATE.encode(),'capture':{'complete':True}}
            if self.mode=='cleanup':result['capture'].update(complete=False,pipes_closed=False,read_errors=['child_reap'])
            return result
        result=super().runner(argv,**kwargs);value=json.loads(result['stdout_bytes'])
        if value.get('kind')=='Pod' and value['metadata']['name']==self.target['kubernetes']['engine']['pod']:
            if self.mode=='shared-pid':value['spec']['shareProcessNamespace']=True
            elif self.after_exec and self.mode=='mount-change':value['spec']['containers'][0]['volumeMounts'][0]['readOnly']=False
        result['stdout_bytes']=canonical_json(value);return result


def inputs(tmp_path,mode=None):
    env,target=full_inputs();world=World(target,mode)
    return env,target,world


def test_bound_packet_keeps_capture_uid_distinct_and_never_authenticates_workers(tmp_path):
    env,target,world=inputs(tmp_path)
    result=collect(env,target,runner=world.runner,candidate_paths=['/run/secrets/EXAMPLE'])
    assert result['available'] and result['binding_verified_before_after']
    assert result['binding_before']==result['binding_after']==target['kubernetes']['engine']
    assert result['data_origin']=='EXAMPLE' and result['program_sha256']==process.SOURCE_SHA256
    assert result['packet']['capture_subject']['uid_effective']==10001
    row=result['packet']['processes'][0];assert row['uid_effective']==2000
    # Existing A-2 status parsing consumes the actual four UID fields; no fake
    # Docker inspect dictionary or fabricated worker subject is introduced.
    text='PID 1\nName: '+row['name']+'\nUid: '+' '.join(map(str,row['uids']))+'\nCapEff: '+row['cap_eff']+'\n'
    parsed,=parse_proc_status(text);assert parsed['uid_effective']==2000 and parsed['caps']==[]
    assert not result['worker_subject_verified'] and not result['worker_inventory_complete']
    assert result['packet']['candidate_metadata'][0]['access_tested'] is False
    assert result['candidate_paths_requested'] and result['mount_declarations_stable']
    assert PRIVATE not in json.dumps(result) and 'EXAMPLE_PRIVATE_HOST_SNAPSHOT' not in json.dumps(result)
    assert world.process_calls==1 and all('create' not in a and 'patch' not in a and 'delete' not in a for a in world.calls)


@pytest.mark.parametrize('mode',['environment-gap','pid-changed'])
def test_partial_process_metadata_retains_earlier_uid_and_caps(tmp_path,mode):
    env,t,world=inputs(tmp_path,mode);r=collect(env,t,runner=world.runner)
    assert r['available'] and r['binding_verified_before_after']
    row=r['packet']['processes'][0];assert row['uid_effective']==2000 and row['cap_eff']=='0000000000000000'
    assert not r['packet']['process_metadata_complete']
    assert r['packet']['pid_inventory_stable'] is (mode=='environment-gap')
    assert not r['worker_subject_verified']


@pytest.mark.parametrize('role',['engine','probe','namespaces'])
@pytest.mark.parametrize('field',['uid','restart'])
def test_later_owner_failure_retains_unbound_packet_without_claiming_generation(tmp_path,role,field):
    env,t,world=inputs(tmp_path)
    def mutate(which,value,after):
        if which!=role or not after:return
        if role=='namespaces':value['items'][0]['metadata']['uid']='replacement'
        elif field=='uid':value['metadata']['uid']='replacement'
        else:value['status']['containerStatuses'][0]['restartCount']=1
    world.mutate=mutate;r=collect(env,t,runner=world.runner)
    assert not r['available'] and not r['binding_verified_before_after']
    assert r['binding_before']==t['kubernetes']['engine'] and r['binding_after'] is None
    assert r['packet']['processes'][0]['uid_effective']==2000
    assert not r['worker_subject_verified'] and not r['worker_inventory_complete']
    assert r['diagnostic_codes']==['KUBERNETES_PROCESS_CAPTURE_UNAVAILABLE']


@pytest.mark.parametrize('mode',['forged-worker','private-extra','value-in-name','cleanup','shared-pid'])
def test_invalid_or_incomplete_capture_has_only_fixed_diagnostics(tmp_path,mode):
    env,t,world=inputs(tmp_path,mode);r=collect(env,t,runner=world.runner)
    assert not r['available'] and r['packet'] is None and PRIVATE not in json.dumps(r)
    assert not r['worker_subject_verified']
    if mode=='shared-pid':assert world.process_calls==0
    if mode=='cleanup':assert r['cleanup_errors']==['KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED']


def test_changed_mount_declaration_is_separate_from_stable_pid_observations(tmp_path):
    env,t,world=inputs(tmp_path,'mount-change');r=collect(env,t,runner=world.runner)
    assert r['available'] and r['binding_verified_before_after'] and not r['mount_declarations_stable']
    assert r['packet']['processes'][0]['uid_effective']==2000
    assert r['diagnostic_codes']==['PROCESS_MOUNT_DECLARATIONS_CHANGED']


@pytest.mark.parametrize('damage',['worker','extra','uid-bool','uid-mismatch','duplicate','self-pid','env-value','complete-gap','time','candidate-access'])
def test_packet_decoder_rejects_unsupported_claims_and_type_confusion(damage):
    p=packet(['/run/secrets/EXAMPLE']);row=p['processes'][0]
    if damage=='worker':p['worker_inventory_complete']=True
    elif damage=='extra':row['environ']=PRIVATE
    elif damage=='uid-bool':row['uid_effective']=True
    elif damage=='uid-mismatch':row['uid_effective']=0
    elif damage=='duplicate':p['processes'].append(deepcopy(row))
    elif damage=='self-pid':row['pid']=p['capture_subject']['pid']
    elif damage=='env-value':row['environment_names']=['TOKEN='+PRIVATE]
    elif damage=='complete-gap':p['pid_inventory_stable']=False
    elif damage=='time':row['finished_at']='2026-10-29T08:59:59'
    else:p['candidate_metadata'][0]['access_tested']=True
    with pytest.raises(ValueError,match='KUBERNETES_PROCESS_PACKET_INVALID'):
        process.decode(p,['/run/secrets/EXAMPLE'])


def test_injected_actual_declaration_is_example_and_none_differs_from_explicit_empty(tmp_path):
    env,t,world=inputs(tmp_path);env=env.model_copy(update={'data_origin':DataOrigin.MEASURED}).seal()
    t.update(lab=False,environment_ref=env.ref().model_dump(mode='json'))
    t['model_mapping']['environment_ref']=env.ref().model_dump(mode='json')
    first=collect(env,t,runner=world.runner);second=collect(env,t,runner=world.runner,candidate_paths=[])
    assert first['data_origin']==second['data_origin']=='EXAMPLE'
    assert first['candidate_paths_requested'] is False and second['candidate_paths_requested'] is True


@pytest.mark.parametrize('paths_value',[['relative'],['/a/../b'],['/a','/a'],['/a\x7f'],[None],['/x']*33])
def test_invalid_paths_are_rejected_before_api(tmp_path,paths_value):
    env,t,_=inputs(tmp_path)
    def forbidden(*a,**k):pytest.fail('invalid paths must not read API or execute')
    with pytest.raises(ValueError):collect(env,t,runner=forbidden,candidate_paths=paths_value)


def test_changed_fixed_program_is_rejected_before_api(tmp_path,monkeypatch):
    env,t,_=inputs(tmp_path)
    monkeypatch.setattr(process,'SOURCE_SHA256','0'*64)
    def forbidden(*a,**k):pytest.fail('unreviewed source must not execute')
    with pytest.raises(ValueError,match='KUBERNETES_PROCESS_SOURCE_UNAVAILABLE'):
        collect(env,t,runner=forbidden)


@pytest.mark.parametrize('mode',[None,'environment-gap','cleanup','private-extra'])
def test_same_cli_capture_and_exclusive_artifact_without_new_judge(tmp_path,monkeypatch,mode):
    env,t,world=inputs(tmp_path,mode)
    for name,value in [('environment',env.to_dict()),('target',t)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    monkeypatch.setattr(kube,'capture_pipes',world.runner)
    def forbidden(*a,**k):raise AssertionError('no process/socket outside injected common boundary')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    output=tmp_path/'snapshot.json';args=['capture-kubernetes-process-snapshot','--env',str(tmp_path/'environment.json'),
        '--target',str(tmp_path/'target.json'),'--output',str(output)]
    runner=CliRunner();preflight=runner.invoke(app,args)
    assert preflight.exit_code==0 and not output.exists() and not world.calls
    actual=runner.invoke(app,[*args,'--execute']);assert actual.exit_code==(1 if mode in {'cleanup','private-extra'} else 0),(actual.output,repr(actual.exception))
    record=json.loads(output.read_text());assert record['security']=='UNKNOWN' and record['data_origin']=='EXAMPLE'
    assert not record['worker_subject_verified'] and PRIVATE not in output.read_text()
    count=world.process_calls;unchanged=output.read_bytes();again=runner.invoke(app,[*args,'--execute'])
    assert again.exit_code==2 and output.read_bytes()==unchanged and world.process_calls==count


def test_cli_artifact_failure_preserves_only_validated_capture(tmp_path,monkeypatch):
    env,t,world=inputs(tmp_path)
    for name,value in [('environment',env.to_dict()),('target',t)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    output=tmp_path/'snapshot.json';original=Path.open
    def fail(self,*args,**kwargs):
        if self==output and args and args[0]=='x':raise OSError(PRIVATE)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'open',fail);monkeypatch.setattr(kube,'capture_pipes',world.runner)
    r=CliRunner().invoke(app,['capture-kubernetes-process-snapshot','--env',str(tmp_path/'environment.json'),
        '--target',str(tmp_path/'target.json'),'--output',str(output),'--execute'])
    assert r.exit_code==1 and not output.exists() and PRIVATE not in r.output
    body=json.loads(r.output);assert body['artifact_saved'] is False and body['capture']['packet']['processes'][0]['uid_effective']==2000
