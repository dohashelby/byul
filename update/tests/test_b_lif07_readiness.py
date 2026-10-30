"""원 LIF07 전용 EXAMPLE 계약. 대상 요청·Pod 변경·GPU 실행은 0회다."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest
import yaml
from guard.collectors.lifecycle import prepare_assets
from guard.core.select import select
from guard.models import DataOrigin, Environment, Policy, new_id
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import baseline_pack, PACK_DIR
from tests.test_kubernetes_request_binding import env as example_env, target as example_target
from tools import b_lif07_readiness as tool

PRIVATE = 'EXAMPLE_PRIVATE_PROMPT_RID_TOKEN_NOT_PERSISTED'

@pytest.fixture
def case(request):
    temp = TemporaryDirectory(prefix='byul-lif07-example-'); request.addfinalizer(temp.cleanup); root = Path(temp.name).resolve()
    pack = baseline_pack(); doc = example_env().model_dump(mode='json'); doc.update(id=new_id('ENV'), content_sha256='')
    doc['runtime'].update(gpu_count=1, tp_groups=1); doc['model'].update(id='EXAMPLE/model',revision='a'*40,inventory_sha256='9'*64)
    env = Environment.model_validate(doc).seal()
    policy = Policy(id=new_id('POL'), data_origin=DataOrigin.EXAMPLE, status='DRAFT', pack=pack.ref(), scope={'include':['LIF-07']}, values={'LIF-07':{}}).seal()
    source=Store(root/'source');put_pack(source,PACK_DIR);source.put_many([env,policy]);plan=select(pack,[env],policy);source.put(plan)
    target=example_target(env);target['model_mapping']={'schema':'guard.model-request-mapping/1','environment_ref':env.ref().model_dump(mode='json'),'repository_id':env.model.id,'revision':env.model.revision,'served_model_name':'fixture-model','host_snapshot':'/EXAMPLE/model','container_mount':'/models/fixture','inventory_sha256':'9'*64};prepared=prepare_assets(root/'prepared',Path(__file__).resolve().parents[2])
    manifest={'schema':'byul-lif07-cohort/1','environment_ref':env.ref().model_dump(mode='json'),'target_sha256':tool.digest(target),
        'session':prepared['session'],'run_id':target['kubernetes']['run_id'],'owner':'EXAMPLE-owner','recovery_owner':'EXAMPLE-owner',
        'request':{'model':'fixture-model','messages':[{'role':'user','content':PRIVATE}],'max_tokens':1,'temperature':0}}
    for name,value in [('target',target),('manifest',manifest)]: (root/(name+'.json')).write_text(json.dumps(value))
    args=dict(store=source.root,plan_id=plan.id,pack=PACK_DIR,target=root/'target.json',prepared=root/'prepared/manifest.json',manifest=root/'manifest.json',output=root/'output',entered_by='EXAMPLE-operator')
    return dict(args=args,env=env,policy=policy,plan=plan,pack=pack,source=source,target=target,prepared=prepared,manifest=manifest)


def snapshot(case, phase='CALL', seq=1):
    return {'schema':'byul-v1-worker-readiness-snapshot/1','point':'Worker.load_model','phase':phase,'status':'metadata_collected','seq':seq,
        'binding':{'pid':20,'call_id_sha256':'a'*64,'session_id_sha256':tool.digest(case['prepared']['session']),'process_start_sha256':'b'*64},
        'metadata':{'owner_token':'1'*64,'thread_token':'2'*64,'rank':0,'local_rank':0,'enforce_eager':False,'configured_graph_mode':'FULL',
            'explicit_load_dummy_weights':False,'call_transition':{'paired':phase!='CALL','same_owner':True if phase!='CALL' else None,'same_thread':True if phase!='CALL' else None,
                'returned':phase=='RETURN','call_seq':1 if phase!='CALL' else None,'end_seq':seq if phase!='CALL' else None},
            'source_qualified':True,'actual_worker_observed':True,'all_model_ready_verified':True,'raw':PRIVATE},
        'native_outer':{'generation_sha256':'d'*64,'native_record_seq':1000+seq,'source_sha256':'e'*64,'code_sha256':'f'*64,'raw':PRIVATE},'gaps':[],'raw':PRIVATE}

def capture(case,rows=None):
    raw=tool._empty(case['env'],case['target'],case['prepared'],case['manifest']);raw['snapshots']=rows if rows is not None else [snapshot(case),snapshot(case,'RETURN',2)]
    raw.update(raw=PRIVATE,ready_send_verified=True,service_gate_verified=True)
    return raw

def project(case,raw):return tool.project(raw,case['env'],case['target'],case['prepared'],case['manifest'])


def test_fresh_preflight_and_closed_execute_have_no_native_heavy_import_or_io(case):
    args={k:str(v) if isinstance(v,Path) else v for k,v in case['args'].items()}
    code='''
import json,sys
from pathlib import Path
from tools import b_lif07_readiness as tool
sys.addaudithook(lambda e,a: (_ for _ in ()).throw(RuntimeError(e)) if e in ('socket.bind','socket.connect','subprocess.Popen') else None)
a=json.loads(sys.argv[1]);r=tool.run(**a)
assert not r['executed'] and r['requests']==0 and not Path(a['output']).exists()
assert 'guard.adapters.vllm_worker_readiness' not in sys.modules
assert 'torch' not in sys.modules and 'vllm' not in sys.modules
r=tool.run(**a,execute=True)
assert r['requests']==0 and not r['actual_target_executed']
assert 'guard.adapters.vllm_worker_readiness' not in sys.modules
'''
    r=subprocess.run([sys.executable,'-c',code,json.dumps(args)],capture_output=True,text=True);assert r.returncode==0,r.stderr

@pytest.mark.parametrize('key',['session','target_sha256','environment_ref','owner','recovery_owner'])
def test_manifest_mismatch_rejected_before_output(case,key):
    m=deepcopy(case['manifest']);m[key]='' if key in ('owner','recovery_owner') else PRIVATE;case['args']['manifest'].write_text(json.dumps(m))
    with pytest.raises(ValueError):tool.run(**case['args'],execute=True)
    assert not case['args']['output'].exists()

@pytest.mark.parametrize('change',[{'model':'repo-is-not-api-alias'},{'max_tokens':2},{'temperature':1},{'messages':[]}])
def test_original_harmless_canary_and_api_mapping_required(case,change):
    m=deepcopy(case['manifest']);m['request'].update(change);case['args']['manifest'].write_text(json.dumps(m))
    with pytest.raises(ValueError):tool.run(**case['args'])


def test_safe_pairs_preserve_native_and_observer_domains_without_readiness_claim(case):
    value=project(case,capture(case));assert len(value['rows'])==2 and len(value['method_pairs'])==1
    assert value['rows'][1]['native_outer']['native_record_seq']==1002 and value['rows'][1]['seq']==2
    assert value['qualified_rows']==0 and value['confirmed_violations']==[] and value['ready_send_observed'] is None and not value['order_verified']
    assert all(r['metadata']['all_model_ready_verified'] is None and not r['metadata']['source_qualified'] for r in value['rows'])
    assert PRIVATE not in json.dumps(value)

@pytest.mark.parametrize('change',[{'seq':1},{'binding':{'pid':21}},{'metadata':{'thread_token':'3'*64}},{'native_outer':{'generation_sha256':'c'*64}}])
def test_cross_order_worker_thread_generation_is_not_a_method_pair(case,change):
    rows=[snapshot(case),snapshot(case,'RETURN',2)]
    for k,v in change.items():
        if isinstance(v,dict):rows[1][k].update(v)
        else:rows[1][k]=v
    assert project(case,capture(case,rows))['method_pairs']==[]


def test_same_coordinate_conflict_preserves_both_and_marks_ambiguity(case):
    rows=[snapshot(case),snapshot(case,'RETURN',2)];other=deepcopy(rows[1]);other['metadata']['rank']=1
    value=project(case,capture(case,rows+[other]));assert len(value['rows'])==3 and value['method_pairs']==[] and 'SNAPSHOT_COORDINATE_AMBIGUOUS' in value['gaps']


def test_original_manual_policy_and_refs_survive_store_restore(case):
    before=tool._inventory(case['source'].root)
    summary=tool.run(**case['args'],execute=True,_example_capture=lambda *a:capture(case))
    out=case['args']['output'];st=Store.read_only(out/'store');restored=Store.read_only(out/'restored')
    assert tool._refs(st)==tool._refs(restored) and st.verify()==restored.verify()==[] and tool._inventory(case['source'].root)==before
    evd,=st.iter_kind('EVD');res,=st.iter_kind('RES');assert evd.data_origin.value=='DECLARED' and not evd.normal_control.attempted and all(not c.attempted for c in evd.challenges)
    assert evd.observations['declared_policy_evidence'].value=={'version':'guard-policy-evidence/1','parameter':'required_readiness_items','records':[]}
    assert res.verdicts.model_dump(mode='json')=={k:'UNKNOWN' for k in ('security','functional','performance')}
    assert 'NO_POLICY_VALUE' in [x.value for x in case['plan'].item('LIF-07').gaps]
    assert summary['capture_origin']=='EXAMPLE' and not summary['actual_target_executed']
    doc=yaml.safe_load((out/'manual-evidence.yaml').read_text());entry,=doc['checks'];check=case['pack'].checks['LIF-07']
    assert entry['question']==check.question and [x['item'] for x in entry['observation_notes']]==check.required_observations_prose
    assert [x['step'] for x in entry['challenges']]==check.challenge_steps
    assert PRIVATE not in json.dumps(evd.model_dump(mode='json')) and PRIVATE not in (out/'worker-readiness.json').read_text()


def test_attachment_failure_keeps_all_safe_facts_in_notes_and_return(case,monkeypatch):
    original=tool._write
    def fail(path,value):
        if path.name=='worker-readiness.json':raise OSError(PRIVATE)
        return original(path,value)
    monkeypatch.setattr(tool,'_write',fail)
    summary=tool.run(**case['args'],execute=True,_example_capture=lambda *a:capture(case))
    assert len(summary['safe_facts']['rows'])==2 and summary['safe_facts']['method_pairs']
    entry,=yaml.safe_load((case['args']['output']/'manual-evidence.yaml').read_text())['checks']
    assert 'LIF07_SAFE_FACTS=' in entry['observation_notes'][0]['answer'] and entry['attachments']==[]
    assert PRIVATE not in json.dumps(summary)


def test_store_failure_exposes_safe_facts_and_explicit_failed_recovery(case,monkeypatch):
    monkeypatch.setattr(tool,'import_evidence',lambda *a,**k:(_ for _ in ()).throw(OSError(PRIVATE)))
    with pytest.raises(tool.EvidencePreservationError) as error:tool.run(**case['args'],execute=True,_example_capture=lambda *a:capture(case))
    assert error.value.safe_facts['method_pairs'] and not error.value.recovery['success'] and not error.value.recovery['capture_rerun_required']
    assert PRIVATE not in json.dumps(error.value.safe_facts) and PRIVATE not in json.dumps(error.value.recovery)


def test_actual_producer_default_and_unsupported_records_reach_consumer(case):
    from guard.adapters.vllm_worker_readiness import WorkerReadinessObserver
    observer=WorkerReadinessObserver()
    default=observer.snapshot('Worker.load_model',(),{},phase='CALL')
    unsupported=observer.snapshot('EXAMPLE.unsupported',(),{},phase='CALL')
    raw=capture(case,[default,unsupported]);value=project(case,raw)
    assert len(value['rows'])==2 and all(r['status']=='unavailable' for r in value['rows']) and value['method_pairs']==[]
    assert value['rows'][1]['point']=='unsupported' and value['rows'][1]['gaps']
    raw['observer_close']=observer.close()
    summary=tool.run(**case['args'],execute=True,_example_capture=lambda *a:raw)
    assert summary['capture_origin']=='EXAMPLE' and summary['verdicts']=={k:'UNKNOWN' for k in ('security','functional','performance')}
    saved=json.loads((case['args']['output']/'worker-readiness.json').read_text())
    assert len(saved['rows'])==2 and saved['observer_close']['complete'] is True

@pytest.mark.parametrize('field',['session_id_sha256','process_start_sha256','pid'])
def test_missing_startup_scope_does_not_form_method_pair(case,field):
    rows=[snapshot(case),snapshot(case,'RETURN',2)]
    for r in rows:r['binding'].pop(field,None)
    assert project(case,capture(case,rows))['method_pairs']==[]


def test_actual_startup_pair_has_no_request_ids_and_stays_unqualified(case):
    import os
    from types import SimpleNamespace
    from guard.adapters.vllm_worker_readiness import WorkerReadinessObserver
    class Worker:pass
    owner=Worker();owner.rank=0;owner.local_rank=0
    owner.model_config=SimpleNamespace(enforce_eager=False)
    owner.vllm_config=SimpleNamespace(compilation_config=SimpleNamespace(cudagraph_mode='FULL'))
    binding={'pid':os.getpid(),'session_id_sha256':tool.digest(case['prepared']['session']),'process_start_sha256':'b'*64}
    observer=WorkerReadinessObserver()
    rows=[observer.snapshot('Worker.load_model',(owner,),{'load_dummy_weights':False},phase=phase,binding=binding) for phase in ('CALL','RETURN')]
    raw=capture(case,rows);raw['observer_close']=observer.close();value=project(case,raw)
    assert len(value['method_pairs'])==1 and value['method_pairs'][0]['generation_verified'] is False
    assert value['qualified_rows']==0 and value['ready_send_observed'] is None and value['expected_ranks'] is None and value['rank_coverage_verified'] is None
    assert all(not any(k in r['binding'] for k in ('call_id_sha256','asgi_request_id_sha256','native_request_id_sha256')) for r in value['rows'])

@pytest.mark.parametrize('changed',['manifest','prepared_asset','store'])
def test_capture_mutation_is_detected_from_pre_capture_baseline_with_safe_facts(case,changed):
    def backend(*args):
        value=capture(case)
        if changed=='manifest':case['args']['manifest'].write_text('{}')
        elif changed=='prepared_asset':(case['args']['prepared'].parent/'settings.json').write_text('{}')
        else:
            extra=case['env'].model_copy(update={'id':new_id('ENV'),'content_sha256':''}).seal();case['source'].put(extra)
        return value
    with pytest.raises(tool.EvidencePreservationError) as error:tool.run(**case['args'],execute=True,_example_capture=backend)
    assert len(error.value.safe_facts['rows'])==2 and error.value.safe_facts['method_pairs']
    assert 'ORIGINAL_INPUT_CHANGED' in error.value.recovery['issues'] and not error.value.recovery['success']
    assert not case['args']['output'].exists() and PRIVATE not in json.dumps(error.value.safe_facts)

@pytest.mark.parametrize('kind',['array','duplicate','nonfinite','directory','symlink','oversize','bare_snapshot','binding'])
def test_import_capture_rejects_unbound_or_unsafe_files_before_store_clone(case,kind):
    import os
    p=case['args']['output'].parent/'input.json';value=capture(case)
    if kind=='array':p.write_text('[]')
    elif kind=='duplicate':p.write_text('{"schema":1,"schema":2}')
    elif kind=='nonfinite':p.write_text('{"a":NaN}')
    elif kind=='directory':p.mkdir()
    elif kind=='symlink':p.symlink_to(case['args']['manifest'])
    elif kind=='oversize':p.write_bytes(b' '*2097153)
    elif kind=='bare_snapshot':p.write_text(json.dumps(snapshot(case)))
    elif kind=='binding':value['target_sha256']='0'*64;p.write_text(json.dumps(value))
    with pytest.raises(tool.EvidencePreservationError) as error:tool.run(**case['args'],import_capture=p)
    assert not case['args']['output'].exists() and not error.value.recovery['success']
    assert not error.value.safe_facts['rows']


def test_import_cli_actual_producer_records_roundtrip_without_live_execution(case):
    import os
    from types import SimpleNamespace
    from guard.adapters.vllm_worker_readiness import WorkerReadinessObserver
    class Worker:pass
    owner=Worker();owner.rank=0;owner.local_rank=0;owner.model_config=SimpleNamespace(enforce_eager=False)
    owner.vllm_config=SimpleNamespace(compilation_config=SimpleNamespace(cudagraph_mode='FULL'))
    binding={'pid':os.getpid(),'session_id_sha256':tool.digest(case['prepared']['session']),'process_start_sha256':'b'*64}
    observer=WorkerReadinessObserver();rows=[observer.snapshot('Worker.load_model',(),{},phase='CALL')]
    rows.extend(observer.snapshot('Worker.load_model',(owner,),{'load_dummy_weights':False},phase=phase,binding=binding) for phase in ('CALL','RETURN'))
    raw=capture(case,rows);raw['observer_close']=observer.close();p=case['args']['output'].parent/'input.json';p.write_text(json.dumps(raw))
    before=tool._inventory(case['source'].root);input_before=p.read_bytes()
    argv=[sys.executable,'-m','tools.b_lif07_readiness']
    for key,value in case['args'].items():argv.extend(['--'+('plan' if key=='plan_id' else key.replace('_','-')),str(value)])
    argv.extend(['--import-capture',str(p)])
    r=subprocess.run(argv,capture_output=True,text=True);assert r.returncode==0,r.stderr
    summary=json.loads(r.stdout);assert summary['imported_from_file'] and summary['input_mode']=='imported_observation_file' and not summary['actual_target_executed'] and summary['requests']==0
    out=case['args']['output'];saved=json.loads((out/'worker-readiness.json').read_text());assert len(saved['rows'])==3 and len(saved['method_pairs'])==1
    assert saved['qualified_rows']==0 and saved['ready_send_observed'] is None and saved['data_origin']=='EXAMPLE'
    assert not saved['source_authentication_verified'] and PRIVATE not in json.dumps(saved)
    st=Store.read_only(out/'store');restored=Store.read_only(out/'restored');assert tool._refs(st)==tool._refs(restored) and st.verify()==restored.verify()==[]
    assert tool._inventory(case['source'].root)==before and p.read_bytes()==input_before
    evd,=st.iter_kind('EVD');res,=st.iter_kind('RES');assert evd.data_origin.value=='DECLARED' and len(evd.artifacts)==1
    assert res.verdicts.model_dump(mode='json')=={k:'UNKNOWN' for k in ('security','functional','performance')}


def test_import_and_live_modes_are_explicitly_separate(case):
    with pytest.raises(ValueError,match='MODES_EXCLUSIVE'):tool.run(**case['args'],execute=True,import_capture='unused.json')
    assert not case['args']['output'].exists()


def test_import_file_mutated_during_projection_keeps_safe_facts(case,monkeypatch):
    p=case['args']['output'].parent/'input.json';p.write_text(json.dumps(capture(case)))
    original=tool.project
    def changing(raw,*args):
        value=original(raw,*args)
        if raw.get('snapshots'):p.write_text('{}')
        return value
    monkeypatch.setattr(tool,'project',changing)
    with pytest.raises(tool.EvidencePreservationError) as error:tool.run(**case['args'],import_capture=p)
    assert len(error.value.safe_facts['rows'])==2 and 'ORIGINAL_INPUT_CHANGED' in error.value.recovery['issues']
    assert not case['args']['output'].exists() and PRIVATE not in json.dumps(error.value.safe_facts)


def test_native_shared_observation_gap_is_preserved(case):
    from guard.adapters import vllm_worker_readiness as native
    row=snapshot(case);gap='OBSERVATION_GAP_PENDING_INVALIDATED';assert gap in native.GAPS
    row['gaps']=[gap,PRIVATE];value=project(case,capture(case,[row]))
    assert value['rows'][0]['gaps']==[gap] and PRIVATE not in json.dumps(value)
