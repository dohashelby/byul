"""Provider references stay separate from real use, values and whole coverage."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import pytest
from guard.adapters import credential_provider_inventory as inv
from guard.executors import credential_provider_inventory as consume
from guard.executors.kubernetes_credential_exposure import KubernetesCredentialExposureExecutor
from guard.models import canonical_json,sha256_hex
from tests.test_lifecycle_capture import external  # noqa: F401
from tests.test_worker_security import assets,sample

PRIVATE='EXAMPLE_SECRET_VALUE_MUST_NEVER_BE_READ'


def inputs():
    binding={'pod_uid':'EXAMPLE-uid','pod':'EXAMPLE-pod','namespace':'EXAMPLE-ns','container':'engine'}
    pod={'kind':'Pod','metadata':{'uid':binding['pod_uid'],'name':binding['pod'],'namespace':binding['namespace'],
        'annotations':{'sensitive':PRIVATE}},'spec':{'containers':[{'name':'engine',
            'env':[{'name':'DIRECT','value':PRIVATE},{'name':'REF','valueFrom':{'secretKeyRef':{'name':'example-secret','key':'api-key'}}}],
            'envFrom':[{'prefix':'APP_','configMapRef':{'name':'example-config'}}],
            'volumeMounts':[{'name':'projected','mountPath':'/var/run/identity','readOnly':True},
                {'name':'csi','mountPath':'/var/run/external'}]}],
        'volumes':[{'name':'projected','projected':{'sources':[{'serviceAccountToken':{'audience':PRIVATE,'path':'token'}},
            {'secret':{'name':'example-secret','items':[{'key':PRIVATE,'path':'key'}]}}]}},
            {'name':'csi','csi':{'driver':'example.test','volumeAttributes':{'secret':PRIVATE}}}]}}
    return pod,binding


def test_closed_projection_keeps_reference_kinds_without_values_or_contents():
    pod,binding=inputs();value=inv.project(pod,binding);text=json.dumps(value)
    assert value['declarations_complete']and PRIVATE not in text
    assert 'example-secret'not in text and 'api-key'not in text
    assert [r['source']for r in value['environment']]==['literal-declaration','secretKeyRef']
    assert value['mounted_sources'][0]['sources'][0]=={'kind':'serviceAccountToken'}
    assert all(value[k]is False for k in('provider_resolution_verified','credential_use_verified','provider_authority_verified','whole_inheritance_verified'))


def test_literal_value_is_not_even_inspected_or_hashed():
    pod,binding=inputs()
    class PrivateValue:
        def __str__(self):raise AssertionError('literal inspected')
        def __repr__(self):raise AssertionError('literal inspected')
    pod['spec']['containers'][0]['env'][0]['value']=PrivateValue()
    assert inv.project(pod,binding)['environment'][0]=={'name':'DIRECT','source':'literal-declaration'}


@pytest.mark.parametrize('damage',['uid','namespace','container','duplicate-container','deleted','oversized','duplicate-volume'])
def test_unbound_or_ambiguous_api_data_is_rejected(damage):
    pod,binding=inputs()
    if damage in{'uid','namespace'}:pod['metadata'][damage]='other'
    elif damage=='container':binding['container']='other'
    elif damage=='duplicate-container':pod['spec']['containers']*=2
    elif damage=='deleted':pod['metadata']['deletionTimestamp']='EXAMPLE'
    elif damage=='oversized':pod['spec']['containers'][0]['env']=[{}]*1025
    else:pod['spec']['volumes']*=2
    with pytest.raises(ValueError,match='DECLARATIONS_UNVERIFIED'):inv.project(pod,binding)


def test_partial_later_declaration_keeps_earlier_name_without_absence_claim():
    pod,binding=inputs();pod['spec']['containers'][0]['env'].append({'name':'DIRECT','value':PRIVATE})
    value=inv.project(pod,binding)
    assert value['environment'][0]['name']=='DIRECT'and not value['declarations_complete']
    assert value['diagnostic_codes']==['PROVIDER_ENV_DECLARATION_UNVERIFIED']


def test_name_presence_is_not_value_equivalence_origin_or_provider_use():
    pod,binding=inputs();value=inv.associate(inv.project(pod,binding),[
        {'rank':1,'environment_names':['DIRECT','OTHER']},{'rank':0,'environment_names':['REF']}],roster_complete=False)
    assert [r['rank']for r in value['workers']]==[0,1]
    assert value['workers'][0]['declared_names_observed']==['REF']
    assert value['workers'][1]['declared_names_not_observed']==['REF']
    assert all(value[k]is False for k in('selected_tp_roster_verified','absence_proves_provider_nonuse',
        'value_equivalence_verified','parent_inheritance_verified','provider_use_verified'))


@pytest.mark.parametrize('damage',['duplicate-rank','bad-name','bool-rank','oversized'])
def test_malformed_worker_associations_do_not_establish_a_roster(damage):
    pod,binding=inputs();rows=[{'rank':0,'environment_names':['DIRECT']}]
    if damage=='duplicate-rank':rows*=2
    elif damage=='bad-name':rows[0]['environment_names']=['NAME='+PRIVATE]
    elif damage=='bool-rank':rows[0]['rank']=True
    else:rows[0]['environment_names']=['NAME']*1025
    with pytest.raises(ValueError):inv.associate(inv.project(pod,binding),rows,roster_complete=True)


@pytest.mark.parametrize('damage',[None,'changed','late-failure','unregistered','wrong-target','wrong-pid','partial-roster'])
def test_worker_link_requires_exact_existing_registered_source_and_current_process(external,damage):
    prepared,cfg=assets(external);record=sample(prepared,cfg);target={'schema':'EXAMPLE'}
    pod,binding=inputs();pod['spec']['containers'][0]['env'].append({'name':'EXAMPLE_MARKER_NAME','value':PRIVATE})
    before={'available':True,'declarations':inv.project(pod,binding),'diagnostic_codes':[]};after=deepcopy(before)
    worker={'available':True,'qualified_registered':True,'target_sha256':sha256_hex(canonical_json(target)),
        'prepared_sha256':sha256_hex(canonical_json(prepared)),'records':[record]}
    summary={'worker_security_probe':worker,'worker_security_scope':{'selected_tp_worker_roster_verified':True},
        'process_observation':{'binding_verified_before_after':True,'packet':{
            'capture_subject':{'pid_namespace_inode':12},'processes':[{'pid':42,'start_ticks':99,'uid_effective':1000,
                'cap_eff':'0','mount_namespace_inode':13,'pid_identity_stable':True}]}}}
    if damage=='changed':after['declarations']['environment'].pop()
    elif damage=='late-failure':after={'available':False,'declarations':None,'diagnostic_codes':['EXAMPLE_LATE_READ_FAILURE']}
    elif damage=='unregistered':worker['qualified_registered']=False
    elif damage=='wrong-target':worker['target_sha256']='0'*64
    elif damage=='wrong-pid':summary['process_observation']['packet']['processes'][0]['start_ticks']+=1
    elif damage=='partial-roster':summary['worker_security_scope']['selected_tp_worker_roster_verified']=False
    ctx=NS(_worker_security_prepared=external/'worker/manifest.json',target=target)
    value=consume.finish(ctx,before,after,summary)
    assert not value['gpu_qualified']and not value['qualified_registered']and not value['provider_scope_verified']
    link=value['worker_associations']
    if damage in{'unregistered','wrong-target'}:assert link is None
    elif damage=='wrong-pid':assert link['workers']==[]and not link['selected_tp_roster_verified']
    else:
        assert link['workers'][0]['declared_names_observed']==['EXAMPLE_MARKER_NAME']
        assert link['selected_tp_roster_verified']is (damage is None)
    assert PRIVATE not in json.dumps(value)


@pytest.mark.parametrize('bad',[None,'true',1,{},[]])
def test_opt_in_requires_a_boolean_before_any_transport_or_process_read(bad):
    with pytest.raises(ValueError,match='CREDENTIAL_PROVIDER_CONFIGURATION_INVALID'):
        KubernetesCredentialExposureExecutor({'provider_inventory':bad})


def test_provider_inventory_crosses_existing_evd_judge_report_and_export(tmp_path,monkeypatch):
    from typer.testing import CliRunner
    from guard.cli import app
    from guard.runtime import kubernetes_http
    from guard.store import Store
    from tests.builders import baseline_pack,policy,T0
    from tests.test_kubernetes_process_consumers import ProcessWorld,CREDENTIAL_VALUES
    from tests.test_model_preparation_integration import full_inputs
    env,target=full_inputs();target['lab']=False;world=ProcessWorld(target)
    monkeypatch.setattr(kubernetes_http,'capture_pipes',world.runner)
    pack=baseline_pack();pol=policy(pack.ref(),scope={'include':['PLT-04']},values={'PLT-04':CREDENTIAL_VALUES})
    for name,obj in [('environment',env.to_dict()),('policy',pol.to_dict()),('target',target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(obj))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\ncredential_exposure="kubernetes"\n[adapter]\nname="vllm"\n[credential_exposure]\nprovider_inventory=true\ntarget='+json.dumps(str(tmp_path/'target.json'))+'\n')
    runner=CliRunner();common=['--config',str(cfg),'--store',str(tmp_path/'store')]
    def cli(args):
        result=runner.invoke(app,list(map(str,args)))
        if result.exit_code!=0:pytest.fail(result.output,pytrace=False)
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    cli(['run','--plan',plan.id,'--pack',pack.path,*common]);cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common])
    cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert res.verdicts.security.value=='UNKNOWN'and evd.data_origin.value=='EXAMPLE'
    artifact,=[a for a in evd.artifacts if a.name=='probe_log.jsonl']
    body=(tmp_path/'store'/artifact.path).read_text();summary=json.loads(body.splitlines()[-1])
    value=summary['credential_provider_inventory']
    assert value['before']['available']and value['declarations_stable']
    assert value['before']['declarations']['environment'][0]['name']=='TOKEN'
    assert not value['provider_scope_verified']and world.process_reads==1
    from tests.test_kubernetes_process_consumers import PRIVATE as PROCESS_PRIVATE
    assert PROCESS_PRIVATE not in body
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip'])
    cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored')
    for kind in('ENV','POL','PLAN','EVD','RES','RPT'):
        for obj in store.iter_kind(kind):assert restored.resolve(obj.ref()).to_dict()==obj.to_dict()
