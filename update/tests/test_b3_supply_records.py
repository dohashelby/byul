"""Retained real CPU records; no compiler/model/native worker is started here."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from datetime import timedelta

import pytest

from guard.collectors.file import load_environment
from guard.executors import registry, supply_boot_contract as contract
from guard.models import canonical_json, load_pack, utcnow

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=Path(__file__).parent/'fixtures/b3_supply_retained_cpu_records.json'


def retained(check_id,tmp_path):
    data=json.loads(FIXTURE.read_bytes())['cases'][check_id]
    raw=data['environment_utf8'].encode()
    assert hashlib.sha256(raw).hexdigest()==data['environment_sha256']
    env_path=tmp_path/'environment.json';env_path.write_bytes(raw)
    env=load_environment(env_path)
    parts={}
    for role,item in data['roles'].items():
        raw=item['record_utf8'].encode()
        assert hashlib.sha256(raw).hexdigest()==item['record_sha256']
        binding=item['binding']
        assert binding['environment_ref']==env.ref().model_dump(mode='json')
        packet=contract.validate_record(json.loads(raw),check_id=check_id,role=role,
            environment_ref=binding['environment_ref'],target_sha256=binding['target_sha256'],source_sha256=binding['source_sha256'])
        parts[role]={'packet':packet,'native':None,'record_bytes':raw,'evaluated':contract.evaluate_record(check_id,packet,{}),
            'row':{'role':role,'collected':True,'sealed':False,'record_sha256':item['record_sha256'],
                'owner_binding_verified':False,'native_files_covered':False,'diagnostic_codes':['CPU_REPLAY_NO_CURRENT_OWNER']}}
    return data,env,parts


@pytest.mark.parametrize('check_id',['SUP-15','SUP-16'])
@pytest.mark.parametrize('stage',['mkdir','write'])
def test_draft_artifact_error_keeps_actual_cpu_record_details(tmp_path,monkeypatch,check_id,stage):
    data,env,parts=retained(check_id,tmp_path)
    out=tmp_path/'evidence'
    if stage=='mkdir':
        original=Path.mkdir
        def mkdir(path,*a,**kw):
            if path==out:raise OSError('EXAMPLE-B3-PRIMARY-DIRECTORY')
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'mkdir',mkdir)
    else:
        original=Path.open
        def write(path,*a,**kw):
            if path.name=='supply_boot_summary.json' and a and a[0] in ('x','w'):
                raise OSError('EXAMPLE-B3-SUMMARY-WRITE')
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',write)
    factory=registry.build_executor('supply_boot','kubernetes',{'supply_boot':{}})
    check=load_pack(ROOT/'packs/PACK-BASELINE').checks[check_id]
    value=factory._draft(check,env,{},out,utcnow(),parts)
    assert value.status.value=='ABORTED' and value.data_origin.value=='EXAMPLE'
    sessions=value.observations['supply_boot_sessions'].value
    assert [r['reported_boot_record'] for r in sessions]==[parts[r]['packet'] for r in contract.ROLES]
    assert not value.observations['supply_boot_complete'].value
    assert value.cleanup_errors


@pytest.mark.parametrize('field',['relevant_change','reused','compatibility_verified'])
def test_sup15_rejects_non_boolean_input_without_reinterpreting_it(tmp_path,field):
    data,env,parts=retained('SUP-15',tmp_path)
    packet=deepcopy(parts['challenge']['packet']);packet['rows'][0][field]='false'
    binding=data['roles']['challenge']['binding']
    with pytest.raises(ValueError,match='SUPPLY_BOOT_RECORD_INVALID'):
        contract.validate_record(packet,check_id='SUP-15',role='challenge',environment_ref=binding['environment_ref'],
            target_sha256=binding['target_sha256'],source_sha256=binding['source_sha256'])


@pytest.mark.parametrize('check_id',['SUP-15','SUP-16'])
def test_original_record_bytes_and_reported_failure_recovery_are_preserved(tmp_path,check_id):
    data,env,parts=retained(check_id,tmp_path)
    factory=registry.build_executor('supply_boot','kubernetes',{'supply_boot':{}})
    check=load_pack(ROOT/'packs/PACK-BASELINE').checks[check_id]
    value=factory._draft(check,env,{},tmp_path/'evidence',utcnow(),parts)
    artifacts={a.name:a for a in value.artifacts}
    for role in contract.ROLES:
        raw=artifacts['supply_boot_'+role+'_record.json'].path.read_bytes()
        assert hashlib.sha256(raw).hexdigest()==data['roles'][role]['record_sha256']
    sessions=value.observations['supply_boot_sessions'].value
    assert [r['reported_boot_record'] for r in sessions]==[parts[r]['packet'] for r in contract.ROLES]
    assert all(r['reported_record_is_native_worker_verification'] is False for r in sessions)
    if check_id=='SUP-16':
        rows=sessions[1]['reported_boot_record']['rows']
        assert [(r['error_type'],r['reader_returned_any_tensor'],r['yield_count']) for r in rows]==[
            ('SafetensorError',False,0),(None,True,196)]
        assert 'normal:native_consumption_unavailable' in value.observations['supply_boot_missing'].value
    normalized=__import__('guard.core.run',fromlist=['_normalize'])._normalize(check,value)
    assert normalized['observations']['supply_boot_sessions']['value']==sessions
    assert not value.observations['supply_boot_complete'].value


@pytest.mark.parametrize('check_id',['SUP-15','SUP-16'])
@pytest.mark.parametrize('mode',['record','missing-normal','summary-error','record-collision','cleanup'])
def test_cpu_record_replay_factory_contract_refs_and_saved_pack(tmp_path,monkeypatch,check_id,mode):
    """Role I/O replay only, not Kubernetes target admission or a new model run."""
    from guard.core.select import select
    from guard.core.run import RunConfig,run_plan
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.executors.kubernetes_supply_boot import KubernetesSupplyBootExecutor
    from guard.models import Policy,new_id
    from guard.store import Store
    from guard.store.packs import put_pack,get_pack
    from guard.store.bundle import export_bundle,restore_bundle
    data,env,parts=retained(check_id,tmp_path)
    calls=[]
    if mode=='missing-normal':
        parts['normal']={'packet':None,'native':None,'evaluated':None,'row':{
            **parts['normal']['row'],'collected':False,'record_sha256':None,
            'diagnostic_codes':['CPU_REPLAY_MISSING_ROLE_INPUT']}}
    if mode=='cleanup':parts['challenge']['cleanup']='SUPPLY_BOOT_PARENT_CLEANUP_UNVERIFIED'
    # The real registered factory/run and original projection/judge execute.
    # Replayed records have no current Pod/worker owner and are never admitted
    # as a supported standalone->Kubernetes target conversion.
    def replay(self,check,environment,values,role):
        calls.append(role)
        assert environment.ref()==env.ref()
        return deepcopy(parts[role])
    monkeypatch.setattr(KubernetesSupplyBootExecutor,'_collect_role',replay)
    if mode=='summary-error':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='supply_boot_summary.json' and a and a[0]=='x':raise OSError('EXAMPLE-B3-summary')
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pack=load_pack(ROOT/'packs/PACK-BASELINE');now=utcnow()
    values={'supply_environment_sha256':env.ref().sha256,'supply_record_source_sha256':data['roles']['normal']['binding']['source_sha256'],
        'supply_validity_start':(now-timedelta(seconds=30)).isoformat(),'supply_validity_end':(now+timedelta(seconds=90)).isoformat()}
    for role in contract.ROLES:
        values['supply_'+role+'_target_sha256']=data['roles'][role]['binding']['target_sha256']
        values['supply_'+role+'_prepared_sha256']=hashlib.sha256(canonical_json(data['roles'][role]['prepared'])).hexdigest()
    pol=Policy(id=new_id('POL'),data_origin='EXAMPLE',status='DRAFT',pack=pack.ref(),
        scope={'include':[check_id]},values={check_id:values}).seal()
    store=Store(tmp_path/'store');put_pack(store,pack.path);store.put_many([env,pol])
    plan=select(pack,[env],pol);store.put(plan)
    if mode=='record-collision':
        path=store.root/'work'/plan.id/check_id/'single'/'supply_boot_normal_record.json'
        path.parent.mkdir(parents=True);path.write_bytes(b'ORIGINAL-PRESERVED')
    evd,=run_plan(store,pack,plan,RunConfig(executors={'supply_boot':'kubernetes'},adapter='vllm',
        raw={'supply_boot':{'normal':{},'challenge':{}}}))
    asof=utcnow();res,=judge_plan(store,pack,plan,as_of=asof)
    rpt=build_report(store,pack,plan,as_of=asof);store.put(rpt)
    export_bundle(store.root,tmp_path/'bundle.zip');restore_bundle(tmp_path/'bundle.zip',tmp_path/'restored')
    restored=Store(tmp_path/'restored')
    again,=judge_plan(restored,get_pack(restored,plan.inputs.pack),restored.resolve(plan.ref()),as_of=asof)
    assert again.ref()==res.ref() and restored.verify()==[]
    assert calls==['normal','challenge']
    assert evd.execution.executor.name=='supply_boot' and evd.data_origin.value=='EXAMPLE'
    assert res.verdicts.security.value=='UNKNOWN' and rpt.official is False
    assert not evd.observations['supply_boot_complete'].value
    challenge=evd.observations['supply_boot_sessions'].value[1]['reported_boot_record']
    assert challenge==parts['challenge']['packet']
    assert not evd.observations['supply_boot_record_authenticated'].value
    if mode in {'summary-error','record-collision','cleanup'}:assert evd.execution.cleanup_errors
    if mode=='record-collision':assert path.read_bytes()==b'ORIGINAL-PRESERVED'
    refs={k:v.ref().model_dump(mode='json') for k,v in [('PLAN',plan),('EVD',evd),('RES',res),('RPT',rpt)]}
    (tmp_path/'cpu-contract-refs.json').write_bytes(canonical_json({'refs':refs,'check_id':check_id,'role_io':'retained-record-replay',
        'actual_Kubernetes_collection':False,'new_model_or_worker':False,'original_environment_ref':env.ref().model_dump(mode='json')}))
