"""Versioned file-backed pack and real registry/CLI, with only kubectl injected."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.executors import registry
from guard.executors.kubernetes_api_transport import KubernetesAPITransportExecutor
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.store import Store
from guard.runtime import kubernetes_api_hop as hop,kubernetes_http
from tests.builders import baseline_pack,policy,T0
from tests.test_a1_kubernetes_api_hop import setup
from tests.test_kubernetes_sampling import PRIVATE
from tools.build_kubernetes_api_pack import derive,SEED_REF,PACK_ID


def inputs(tmp_path):
    receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    world,_,values=setup();world.target['lab']=False
    values['api_target_sha256']=sha256_hex(canonical_json(world.target))
    from tests.test_kubernetes_request_binding import env
    return world,env(),values,pack,receipt


@pytest.mark.parametrize('mode',['forbidden','allowed','denied','partial','engine-replaced',
    'probe-restart','late-window','attachment','cleanup'])
def test_registered_api_hop_cli_exact_refs_and_source_ceiling(tmp_path,monkeypatch,mode):
    world,environment,values,pack,receipt=inputs(tmp_path)
    values['plaintext_forbidden']=mode!='allowed'
    if mode in {'denied','partial'}:
        def damage(rows):
            if mode=='denied':rows[0]['outcome']=403
            else:rows[0]['capture_complete']=False
        world.damage=damage
    if mode in {'engine-replaced','probe-restart'}:
        def mutate(which,value,after):
            if not after:return
            if mode=='engine-replaced' and which=='engine':value['metadata']['uid']='replaced'
            elif mode=='probe-restart' and which=='probe':value['status']['containerStatuses'][0]['restartCount']+=1
        world.mutate=mutate
    if mode=='late-window':
        first=utcnow();times=iter([first,first+timedelta(minutes=2)])
        monkeypatch.setattr(hop,'utcnow',lambda:next(times))
    def boundary(argv,**kwargs):
        value=world.runner(argv,**kwargs)
        if mode=='cleanup' and 'exec' in argv:value['capture'].update(pipes_closed=False,read_errors=['child_reap'])
        return value
    monkeypatch.setattr(kubernetes_http,'capture_pipes',boundary)
    def forbidden(*args,**kwargs):raise AssertionError('Only the injected kubectl boundary may run')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*args,**kwargs):
            if path.name=='probe_log.jsonl' and args and args[0]=='w':raise OSError(PRIVATE)
            return original(path,*args,**kwargs)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['PLT-08']},values={'PLT-08':values})
    for name,value in [('environment',environment.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\napi_transport="kubernetes"\n[adapter]\nname="vllm"\n[api_transport]\ntarget='+json.dumps(str(tmp_path/'target.json'))+'\n',encoding='utf-8')
    factory=registry.resolve_executor('api_transport','kubernetes');assert factory is not None
    implementation=factory({'api_transport':{'target':str(tmp_path/'target.json')},'_runtime_environ':{},'_kubernetes_target_sha256':values['api_target_sha256']})
    assert isinstance(implementation,KubernetesAPITransportExecutor) and implementation._expected_target_sha256==values['api_target_sha256']
    assert implementation.preview_reuse_inputs(None,None,None,None) is None
    runner=CliRunner();common=['--store',str(tmp_path/'store'),'--config',str(cfg)]
    def cli(args,expected=0):
        value=runner.invoke(app,list(map(str,args)));assert value.exit_code==expected,(args,value.output)
        assert PRIVATE not in value.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN');item=next(x for x in plan.items if x.check_id=='PLT-08');assert item.selected and not item.gaps
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert len(world.execs)==1 and registry.resolve_executor('api_transport','kubernetes') is factory
    assert e.check_id=='PLT-08' and e.data_origin.value=='EXAMPLE'
    assert e.execution.status.value==('ABORTED' if mode=='attachment' else 'COMPLETED')
    assert bool(e.execution.cleanup_errors) is (mode=='cleanup')
    normal=mode in {'forbidden','allowed','attachment','cleanup'}
    assert e.normal_control.attempted and e.normal_control.succeeded is normal
    assert res.verdicts.security.value==('FAIL' if mode in {'forbidden','attachment','cleanup'} else 'UNKNOWN')
    assert res.verdicts.functional.value==res.verdicts.performance.value=='UNKNOWN'
    trial,=e.observations['api_transport_trials'].value
    assert e.observations['api_transport_scope_complete'].value is False
    assert not trial['internal_IPC_TP_scope_complete'] and not trial['deployed_caller_path_verified']
    if mode in {'engine-replaced','probe-restart'}:
        assert not trial['binding_verified_before_after'] and trial['request'] is trial['subjects'] is None
    else:
        assert trial['request']=={'logical_id':'func|POST /v1/completions','wire_id':'functional-control','method':'POST','route':'/v1/completions','identity_basis':'fixed-source-correlated-attempt'}
        assert trial['subjects']['source']==world.target['kubernetes']['probe']
        assert trial['subjects']['destination']==world.target['kubernetes']['engine']
        assert not trial['subjects']['worker_subject_verified'] and not trial['subjects']['deployed_caller_verified']
        assert trial['http_status_observed'] is True
    if mode=='late-window':assert trial['initial_response']['outcome']==200 and not trial['available']
    assert PRIVATE not in canonical_json(e.to_dict()).decode()
    for a in e.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/a.path).read_bytes()
    objects=[x for k in ('ENV','POL','PLAN','EVD','RES','RPT') for x in store.iter_kind(k)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(x.ref()).to_dict()==x.to_dict() for x in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_derived_pack_keeps_all_questions_criteria_and_76_definition_bytes(tmp_path):
    seed=baseline_pack();before={p.relative_to(seed.path).as_posix():p.read_bytes() for p in seed.path.rglob('*') if p.is_file()}
    receipt=derive(seed.path,tmp_path/'first');other=derive(seed.path,tmp_path/'second')
    assert receipt==other and receipt['seed_ref']==SEED_REF and receipt['pack_ref']['id']==PACK_ID
    generated=load_pack(tmp_path/'first');assert len(generated.checks)==77 and not generated.reviewed
    assert generated.checks['PLT-08'].revision==6 and generated.checks['PLT-08'].collection_profile==hop.PROFILE
    assert {p.key:p.type for p in generated.checks['PLT-08'].policy_params if p.required}==hop.POLICY_TYPES
    for name,raw in before.items():
        assert (seed.path/name).read_bytes()==raw
        if name not in {'pack.yaml','checks/PLT-08.yaml'}:assert (tmp_path/'first'/name).read_bytes()==raw
    a=seed.checks['PLT-08'].model_dump();b=generated.checks['PLT-08'].model_dump()
    assert {k for k in a if a[k]!=b[k]}=={'revision','policy_params','collection_profile','limitations'}
    assert seed.ref().model_dump(mode='json')==SEED_REF and not receipt['policy_values_created']
    with pytest.raises(ValueError,match='NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED'):derive(seed.path,tmp_path/'first')
    with pytest.raises(ValueError,match='NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED'):derive(seed.path,seed.path/'new')


def test_resealed_unreviewed_seed_is_not_accepted(tmp_path):
    import shutil,yaml
    seed=baseline_pack();copy=tmp_path/'bad';shutil.copytree(seed.path,copy)
    path=copy/'pack.yaml';data=yaml.safe_load(path.read_text(encoding='utf-8'));data['revision']+=1;path.write_text(yaml.safe_dump(data,allow_unicode=True),encoding='utf-8')
    with pytest.raises(ValueError,match='REVIEWED_PACK31_REQUIRED'):derive(copy,tmp_path/'output')
    assert not (tmp_path/'output').exists()


@pytest.mark.parametrize('damage',['missing','wrong-type','old-docker-fields','wrong-pack'])
def test_formal_selection_retains_missing_policy_gate_before_io(tmp_path,monkeypatch,damage):
    from guard.core.select import select
    from guard.models import Gap
    world,environment,values,pack,_=inputs(tmp_path)
    if damage=='missing':values.pop('plaintext_forbidden')
    elif damage=='wrong-type':values['api_port']=True
    elif damage=='old-docker-fields':
        for name in ['api_environment_sha256','api_target_sha256','api_binding_sha256']:values.pop(name)
        values.update(api_container_id='a'*64,api_network_id='b'*64,api_endpoint_id='c'*64)
    pol=policy(baseline_pack().ref() if damage=='wrong-pack' else pack.ref(),scope={'include':['PLT-08']},values={'PLT-08':values})
    if damage in {'wrong-type','wrong-pack'}:
        with pytest.raises(ValueError):select(pack,[environment],pol)
    else:
        plan=select(pack,[environment],pol);item=next(x for x in plan.items if x.check_id=='PLT-08');assert item.selected and Gap.NO_POLICY_VALUE in item.gaps
    assert not world.calls and not world.execs
