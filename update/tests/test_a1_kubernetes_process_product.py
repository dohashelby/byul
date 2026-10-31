"""Actual registered classes and CLI/Ref path; no replacement registry factory."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.executors import registry
from guard.executors.kubernetes_container_config import KubernetesContainerConfigExecutor
from guard.executors.kubernetes_credential_exposure import KubernetesCredentialExposureExecutor
from guard.models import canonical_json, sha256_hex
from guard.runtime import kubernetes_http
from guard.store import Store
from tests.builders import baseline_pack, policy, T0
from tests.test_kubernetes_process_consumers import (ProcessWorld, PERMISSION_VALUES, CREDENTIAL_VALUES, PRIVATE)
from tests.test_model_preparation_integration import full_inputs


@pytest.mark.parametrize('check_id,kind,normal_mode,attachment_failure',[
    ('PLT-01','normal',None,False),('PLT-01','root',None,False),
    ('PLT-01','capability',None,False),('PLT-01','root','denied',False),
    ('PLT-01','root','partial',False),('PLT-01','root','replacement',False),
    ('PLT-01','root','cleanup',False),('PLT-01','root',None,True),
    ('PLT-04','normal',None,False),('PLT-04','normal','wrong-model',False),
    ('PLT-04','normal','partial',False),('PLT-04','normal',None,True)])
def test_registered_process_cli_preserves_origin_facts_failures_and_exact_refs(
        tmp_path,monkeypatch,check_id,kind,normal_mode,attachment_failure):
    env,target=full_inputs();target['lab']=False
    assert env.data_origin.value=='EXAMPLE'
    world=ProcessWorld(target,kind=kind,normal_mode=normal_mode)
    # Exercise the default-runner slot, so injected=False cannot be the reason
    # EXAMPLE survives. The public producer must preserve the ENV's origin.
    monkeypatch.setattr(kubernetes_http,'capture_pipes',world.runner)
    section='container_config' if check_id=='PLT-01' else 'credential_exposure'
    factory=registry.resolve_executor(section,'kubernetes');assert factory is not None
    raw={section:{'target':str(tmp_path/'target.json')},'_runtime_environ':{},
         '_kubernetes_target_sha256':sha256_hex(canonical_json(target))}
    executor=factory(raw)
    assert isinstance(executor,KubernetesContainerConfigExecutor)
    if check_id=='PLT-04':assert isinstance(executor,KubernetesCredentialExposureExecutor)
    assert executor._expected_target_sha256==raw['_kubernetes_target_sha256']
    values=(PERMISSION_VALUES if check_id=='PLT-01' else
            {**CREDENTIAL_VALUES,'credential_candidate_paths':['/EXAMPLE-credentials/key']})
    pack=baseline_pack();pol=policy(pack.ref(),scope={'include':[check_id]},values={check_id:deepcopy(values)})
    for name,obj in [('environment',env.to_dict()),('policy',pol.to_dict()),('target',target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(obj))
    cfg=tmp_path/'guard.toml'
    cfg.write_text('[executors]\n'+section+'="kubernetes"\n[adapter]\nname="vllm"\n['+section+']\ntarget='+
        json.dumps(str(tmp_path/'target.json'))+'\n',encoding='utf-8')
    def forbidden(*args,**kwargs):raise AssertionError('only the injected kubectl boundary may run')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if attachment_failure:
        original=Path.open
        def open_file(path,*args,**kwargs):
            if path.name=='probe_log.jsonl' and args and args[0]=='w':raise OSError(PRIVATE)
            return original(path,*args,**kwargs)
        monkeypatch.setattr(Path,'open',open_file)
    runner=CliRunner();common=['--config',str(cfg),'--store',str(tmp_path/'store')]
    def cli(args,expected=0):
        value=runner.invoke(app,list(map(str,args)));assert value.exit_code==expected,(args,value.output)
        assert PRIVATE not in value.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if normal_mode=='cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common])
    cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common])
    cli(['verify',*common]);store=Store.read_only(tmp_path/'store')
    evd,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert world.process_reads==len(world.packets)==1
    assert registry.resolve_executor(section,'kubernetes') is factory
    assert evd.data_origin.value=='EXAMPLE'
    assert evd.execution.status.value==('ABORTED' if attachment_failure else 'COMPLETED')
    assert bool(evd.execution.cleanup_errors) is (normal_mode=='cleanup')
    assert evd.normal_control.attempted and evd.normal_control.succeeded is (normal_mode is None)
    assert res.verdicts.security.value==('FAIL' if check_id=='PLT-01' and kind!='normal' else 'UNKNOWN')
    assert res.verdicts.functional.value==('FAIL' if normal_mode=='denied' else 'UNKNOWN')
    assert res.verdicts.performance.value=='UNKNOWN'
    assert evd.observations['process_scan_complete'].value is False
    if check_id=='PLT-01':
        assert evd.observations['permission_scan_complete'].value is False
        assert evd.observations['root_uid_present'].collected is (kind=='root')
    else:
        assert not evd.observations['worker_env_names'].collected
        assert evd.observations['credential_candidate_results'].value=={'/EXAMPLE-credentials/key':'unknown'}
    assert PRIVATE not in canonical_json(evd.to_dict()).decode()
    if not attachment_failure:
        artifact,=[a for a in evd.artifacts if a.name=='probe_log.jsonl']
        text=(tmp_path/'store'/artifact.path).read_text(encoding='utf-8');assert PRIVATE not in text
        summary=json.loads(text.splitlines()[-1])
        assert summary['process_observation']['data_origin']=='EXAMPLE'
        assert summary['worker_subject_verified'] is False
        assert summary['process_observation']['packet']['capture_subject']['uid_effective']==0
        assert all(row['pid']!=9999 for row in summary['process_observation']['packet']['processes'])
    objects=[x for k in ('ENV','POL','PLAN','EVD','RES','RPT') for x in store.iter_kind(k)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip'])
    cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored')
    assert all(restored.resolve(x.ref()).to_dict()==x.to_dict() for x in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg])
    assert [x.ref() for x in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


@pytest.mark.parametrize('damage',['check','old-definition','missing-policy','wrong-type','target-hash'])
def test_common_admission_rejects_before_remote_process_or_http(damage,tmp_path,monkeypatch):
    env,target=full_inputs();world=ProcessWorld(target)
    monkeypatch.setattr(kubernetes_http,'capture_pipes',world.runner)
    path=tmp_path/'target.json';path.write_bytes(canonical_json(target))
    check=baseline_pack().checks['PLT-01'];values=deepcopy(PERMISSION_VALUES)
    kwargs={}
    if damage=='check':check=baseline_pack().checks['PLT-02']
    elif damage=='old-definition':check=check.model_copy(update={'required_observations':[]})
    elif damage=='missing-policy':values.pop('require_non_root')
    elif damage=='wrong-type':values['allowed_devices']=PRIVATE
    else:kwargs['expected_target_sha256']='0'*64
    executor=KubernetesContainerConfigExecutor({'target':str(path)},**kwargs)
    with pytest.raises(ValueError) as caught:executor.run(check,env,values,VllmAdapter(),tmp_path/'work')
    assert PRIVATE not in str(caught.value) and not world.calls and not world.process_reads


def test_sup04_default_runner_example_origin_and_original_api_only_branch(tmp_path,monkeypatch):
    from tests.test_a1_kubernetes_image_dispatch import World
    env,target=full_inputs();target['lab']=False;world=World(target)
    monkeypatch.setattr(kubernetes_http,'capture_pipes',world.runner)
    path=tmp_path/'target.json';path.write_bytes(canonical_json(target))
    draft=KubernetesContainerConfigExecutor({'target':str(path)}).run(baseline_pack().checks['SUP-04'],
        env,{'approved_image_digests':[env.engine.image_digest]},VllmAdapter(),tmp_path/'work')
    assert draft.data_origin.value=='EXAMPLE' and not draft.normal_control.attempted
    summary=json.loads(draft.artifacts[0].path.read_text(encoding='utf-8'))
    assert summary['dispatch']['scope']=='api-metadata-only'
    assert all('exec' not in argv for argv in world.calls)
