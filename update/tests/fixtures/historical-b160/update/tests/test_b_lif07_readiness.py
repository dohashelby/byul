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
    callback_path=Path(__file__).resolve().parents[1]/'src/guard/adapters/vllm_worker_readiness.py'
    code='''
import json,sys,threading
from pathlib import Path
expected=Path(sys.argv[2]).resolve()
methods={"WorkerReadinessObserver."+name for name in ("__init__","snapshot","close")}
counts={name:0 for name in methods};phases={};forbidden=[]
phase="import"
def audit(event,args):
    if event in ("socket.bind","socket.connect","subprocess.Popen"):
        forbidden.append(event)
        raise RuntimeError(event)
def profile(frame,event,arg):
    if event=="call" and frame.f_code.co_qualname in methods and Path(frame.f_code.co_filename).resolve()==expected:
        counts[frame.f_code.co_qualname]+=1
sys.addaudithook(audit)
sys.setprofile(profile);threading.setprofile(profile)
def measured(name):
    phases[name]={"observer_calls":dict(counts),"callback_metadata_loaded":"guard.adapters.vllm_worker_readiness" in sys.modules}
    assert all(value==0 for value in counts.values()),(name,counts)
    assert not forbidden
    assert "torch" not in sys.modules and "vllm" not in sys.modules
    assert "tools.b_lif07_native_capture" not in sys.modules
from tools import b_lif07_readiness as tool
measured("import")
a=json.loads(sys.argv[1])
assert not Path(a["output"]).exists()
phase="preflight";r=tool.run(**a)
assert not r["executed"] and r["requests"]==0 and not Path(a["output"]).exists()
measured("preflight")
phase="closed_execute";r=tool.run(**a,execute=True)
assert r["requests"]==0 and not r["actual_target_executed"]
assert r["executed"] and Path(a["output"]).is_dir()
measured("closed_execute")
phase="positive_control"
from guard.adapters.vllm_worker_readiness import WorkerReadinessObserver
for name in ("__init__","snapshot","close"):
    original=getattr(WorkerReadinessObserver,name).__code__
    assert Path(original.co_filename).resolve()==expected and original.co_qualname in methods
observer=WorkerReadinessObserver()
unsupported=observer.snapshot("EXAMPLE.unsupported",(),{},phase="CALL")
closed=observer.close()
assert unsupported["status"]=="unavailable" and closed["complete"]
assert counts=={name:1 for name in methods},counts
assert not forbidden and "torch" not in sys.modules and "vllm" not in sys.modules
assert "tools.b_lif07_native_capture" not in sys.modules
sys.setprofile(None);threading.setprofile(None)
print(json.dumps({"phases":phases,"positive_control":counts,"forbidden_io_events":forbidden,"callback_source":str(expected)}))
'''
    r=subprocess.run([sys.executable,'-c',code,json.dumps(args),str(callback_path)],capture_output=True,text=True);assert r.returncode==0,r.stderr
    measured=json.loads(r.stdout)
    assert set(measured['phases'])=={'import','preflight','closed_execute'}
    assert all(all(value==0 for value in row['observer_calls'].values()) for row in measured['phases'].values())
    assert measured['positive_control']=={'WorkerReadinessObserver.'+name:1 for name in ('__init__','snapshot','close')}
    assert measured['forbidden_io_events']==[]
    print(r.stdout,end='')

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
    def changing(raw,*args, native_observation=False):
        value=original(raw,*args, native_observation=native_observation)
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


def test_regular_reader_keeps_stable_independent_path_and_fd_ctime_domains(tmp_path, monkeypatch):
    from types import SimpleNamespace
    path = tmp_path / 'capture.json'; content = b'{"EXAMPLE":true}'
    path.write_bytes(content)
    original = tool.os.fstat
    def fd_stat(fd):
        value = original(fd)
        return SimpleNamespace(**{k: getattr(value, k) for k in
            ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_mode')}, st_ctime_ns=value.st_ctime_ns + 1000000)
    monkeypatch.setattr(tool.os, 'fstat', fd_stat)
    assert tool._regular_bytes(path) == content


@pytest.mark.parametrize('domain', ['fd', 'path'])
def test_regular_reader_rejects_ctime_change_within_each_stat_domain(tmp_path, monkeypatch, domain):
    from types import SimpleNamespace
    path = tmp_path / 'capture.json'; path.write_bytes(b'{"EXAMPLE":true}')
    original_fd, original_path = tool.os.fstat, Path.lstat
    count = [0]
    def changed(value, delta):
        return SimpleNamespace(**{k: getattr(value, k) for k in
            ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_mode')}, st_ctime_ns=value.st_ctime_ns + delta)
    def fd_stat(fd):
        count[0] += 1
        value = original_fd(fd)
        return changed(value, count[0]) if domain == 'fd' else value
    def path_stat(selected, *args, **kwargs):
        value = original_path(selected, *args, **kwargs)
        return changed(value, 1) if domain == 'path' and selected == path and count[0] >= 2 else value
    monkeypatch.setattr(tool.os, 'fstat', fd_stat)
    monkeypatch.setattr(Path, 'lstat', path_stat)
    with pytest.raises(ValueError, match='^LIF07_INPUT_CHANGED_DURING_READ$'):
        tool._regular_bytes(path)


def test_regular_reader_rejects_actual_path_replacement_after_fd_read(tmp_path, monkeypatch):
    import os
    path = tmp_path / 'capture.json'; path.write_bytes(b'{"EXAMPLE":true}')
    replacement = tmp_path / 'replacement.json'; replacement.write_bytes(path.read_bytes())
    original = tool.os.fstat; count = [0]
    def fd_stat(fd):
        value = original(fd); count[0] += 1
        if count[0] == 2: os.replace(replacement, path)
        return value
    monkeypatch.setattr(tool.os, 'fstat', fd_stat)
    with pytest.raises(ValueError, match='^LIF07_INPUT_CHANGED_DURING_READ$'):
        tool._regular_bytes(path)


@pytest.mark.parametrize('other', ['execute', 'import', 'example'])
def test_native_collection_mode_is_exclusive_before_helper_or_output(case, monkeypatch, other):
    calls = []
    monkeypatch.setattr(tool, '_collect_native', lambda *a: calls.append(a))
    options = {'execute': True} if other == 'execute' else {'import_capture': 'unused.json'} if other == 'import' else {'_example_capture': lambda *a: capture(case)}
    with pytest.raises(ValueError, match='MODES_EXCLUSIVE'):
        tool.run(**case['args'], collect_native=True, **options)
    assert calls == [] and not case['args']['output'].exists()


def native_observation(case, rows=None):
    """정제 codec의 EXAMPLE 입력. 실제 native packet은 별도 실 helper 연결 시험에서 만든다."""
    from guard.adapters import vllm_worker_generation_metadata as meta
    raw = capture(case, rows)
    raw['native21'] = meta.project(None, {'profile': tool.NATIVE_OBSERVATION_PROFILE, 'session': case['prepared']['session']})
    raw['native_collection'] = {'initial_capture_attempted': True, 'final_capture_attempted': True,
        'process_recordings': [], 'live_census_stable': False, 'recorded_files_covered': False,
        'reader_collection_gap': True, 'parser_diagnostic_codes': [], 'snapshot_omitted_count': 0}
    raw['cleanup'].update(pre_snapshot_attempted=True, pre_snapshot_verified=True,
        post_snapshot_attempted=True, post_snapshot_verified=False, api_epoch_same=None,
        selected_session_seal_attempted=True, selected_session_sealed=False,
        final_capture_attempted=True, final_capture_complete=False)
    return raw


def test_native21_outer_source_facts_and_bootstrap_send_stay_separate_from_readiness(case):
    from tools import b_lif07_native_capture as helper
    raw = native_observation(case)
    for row in raw['snapshots']:
        row['stage'] = 'initial'
        row['native_outer'].update(recording=0, source_verified=True, callback_code_verified=True,
            source_contract_verified=True, process_binding_verified=True, observer_runtime='cpython-3.12.3',
            entry_kind='direct-body', raw=PRIVATE)
    raw['native21']['ready_sends'] = [{'stage': 'initial', 'recording': 0, 'operation': 'SEND', 'status': 'READY',
        'terminal_phase': 'RETURN', 'paired': True, 'source_main_entry_observed': True,
        'original_send_return_observed': True, 'actual_worker_verified': True, 'service_gate_verified': True, 'raw': PRIVATE}]
    raw['native21']['missing'].extend(['worker_main_terminal_not_observed', PRIVATE])
    raw['native_collection']['parser_diagnostic_codes'] = [next(iter(helper.PARSER_DIAGNOSTIC_CODES)), PRIVATE]
    value = tool.project(raw, case['env'], case['target'], case['prepared'], case['manifest'], native_observation=True)
    assert value['rows'][0]['native_outer']['source_verified'] is True
    assert value['rows'][0]['metadata']['source_qualified'] is False
    assert value['native21']['ready_sends'][0]['original_send_return_observed'] is True
    assert value['native21']['ready_sends'][0]['actual_worker_verified'] is False
    assert value['native21']['ready_sends'][0]['service_gate_verified'] is False
    assert 'worker_main_terminal_not_observed' in value['native21']['missing']
    assert value['native21']['additional_missing_count'] == 1
    assert value['native_collection']['additional_parser_diagnostic_count'] == 1
    assert value['ready_send_observed'] is value['post_ready_canary'] is value['rank_coverage_verified'] is None
    assert value['target_io_attempted'] is True and value['target_http_requests'] == value['controlled_delay_requests'] == 0
    assert PRIVATE not in json.dumps(value)


def test_imported_native21_extra_box_does_not_gain_live_source_facts(case):
    raw = native_observation(case)
    for row in raw['snapshots']:
        row['native_outer'].update(source_verified=True, callback_code_verified=True, source_contract_verified=True)
    value = project(case, raw)
    assert value['native21'] is value['native_collection'] is None
    assert value['target_io_attempted'] is False
    assert all(not row['native_outer']['source_verified'] and not row['native_outer']['source_contract_verified'] for row in value['rows'])
    assert value['qualified_rows'] == 0 and value['ready_send_observed'] is None


@pytest.mark.parametrize('different', ['stage', 'recording', 'process_domain'])
def test_native_method_pairs_never_join_other_capture_or_recording_domains(case, different):
    rows = [snapshot(case), snapshot(case, 'RETURN', 2)]
    for row in rows:
        row['stage'] = 'initial'; row['native_outer']['recording'] = 0
        row['native_outer']['process_domain'] = {'pid': 20, 'start_ticks': 99, 'boot_sha256': '1' * 64}
    if different == 'stage': rows[1]['stage'] = 'final'
    elif different == 'recording': rows[1]['native_outer']['recording'] = 1
    else: rows[1]['native_outer']['process_domain']['start_ticks'] = 100
    assert project(case, capture(case, rows))['method_pairs'] == []


def test_repeated_journal_prefix_is_preserved_per_stage_and_late_gap_keeps_initial_pair(case):
    rows = [snapshot(case), snapshot(case, 'RETURN', 2)]
    for row in rows: row['stage'] = 'initial'; row['native_outer']['recording'] = 0
    repeated = deepcopy(rows)
    for row in repeated: row['stage'] = 'final'
    complete = project(case, capture(case, rows + repeated))
    assert len(complete['rows']) == 4 and len(complete['method_pairs']) == 2
    assert {row['stage'] for row in complete['method_pairs']} == {'initial', 'final'}
    incomplete = native_observation(case, rows)
    incomplete['cleanup'].update(selected_session_sealed=False, final_capture_complete=False)
    value = tool.project(incomplete, case['env'], case['target'], case['prepared'], case['manifest'], native_observation=True)
    assert len(value['method_pairs']) == 1 and value['method_pairs'][0]['stage'] == 'initial'
    assert not value['cleanup']['selected_session_sealed'] and not value['cleanup']['final_capture_complete']
    assert value['expected_ranks'] is value['rank_coverage_verified'] is None


def test_native_projection_across_recordings_keeps_more_than_one_journal_rows(case):
    raw = native_observation(case, [])
    raw['native21']['generation_events'] = [{'stage': 'initial', 'recording': recording, 'event_seq': seq,
        'point': 'WorkerProc.worker_main', 'phase': 'CALL'} for recording in (0, 1) for seq in range(1025)]
    value = tool.project(raw, case['env'], case['target'], case['prepared'], case['manifest'], native_observation=True)
    assert len(value['native21']['generation_events']) == 2050
    assert all(row['actual_worker_verified'] is False for row in value['native21']['generation_events'])
    assert value['full_scope_coverage'] is False


@pytest.mark.parametrize('failure', ['artifact', 'store'])
def test_native_storage_failure_preserves_observation_and_independent_seal_cleanup(case, monkeypatch, failure):
    raw = native_observation(case, [])
    calls = []
    def collect(*args):
        calls.append(True); return raw
    monkeypatch.setattr(tool, '_collect_native', collect)
    if failure == 'artifact':
        original = tool._write
        def write(path, value):
            if path.name == 'worker-readiness.json': raise OSError(PRIVATE)
            return original(path, value)
        monkeypatch.setattr(tool, '_write', write)
        summary = tool.run(**case['args'], collect_native=True)
        facts = summary['safe_facts']
        entry, = yaml.safe_load((case['args']['output'] / 'manual-evidence.yaml').read_text())['checks']
        assert 'LIF07_SAFE_FACTS=' in entry['observation_notes'][0]['answer'] and entry['attachments'] == []
    else:
        monkeypatch.setattr(tool, 'import_evidence', lambda *a, **k: (_ for _ in ()).throw(OSError(PRIVATE)))
        with pytest.raises(tool.EvidencePreservationError) as error: tool.run(**case['args'], collect_native=True)
        assert not error.value.recovery['capture_rerun_required']
        facts = error.value.safe_facts
    assert calls == [True] and facts['native21']['missing']
    assert facts['cleanup']['selected_session_seal_attempted'] is True
    assert facts['cleanup']['selected_session_sealed'] is False
    assert facts['cleanup']['gpu_model_recovery_verified'] is None and PRIVATE not in json.dumps(facts)


def test_native_collection_input_mutation_keeps_safe_facts_and_pre_backend_baseline(case, monkeypatch):
    raw = native_observation(case, [])
    calls = []
    def collect(*args):
        calls.append(True); case['args']['manifest'].write_text('{}'); return raw
    monkeypatch.setattr(tool, '_collect_native', collect)
    with pytest.raises(tool.EvidencePreservationError) as error: tool.run(**case['args'], collect_native=True)
    assert calls == [True] and error.value.safe_facts['native21']['missing']
    assert 'ORIGINAL_INPUT_CHANGED' in error.value.recovery['issues']
    assert not case['args']['output'].exists()


def test_actual_native_helper_default_public_entrypoint_store_restore_preserved(request, monkeypatch):
    """실 Bhelper·실 A default packet/project를 연결한다. Reader 전송만 EXAMPLE이며 실제 대상 0회다."""
    import contextlib
    import hashlib
    import io
    import os
    import shutil
    from tools import b_lif07_native_capture as helper
    from tests.test_b_lif07_native_capture import make_inputs, UnavailableReader
    temp = TemporaryDirectory(prefix='byul-lif07-native-consumer-contract-')
    request.addfinalizer(temp.cleanup)
    root = Path(temp.name).resolve()
    env, target, prepared, manifest = make_inputs(root)
    manifest['request']['messages'][0]['content'] = 'hello'
    pack = baseline_pack()
    policy = Policy(id=new_id('POL'), data_origin=DataOrigin.EXAMPLE, status='DRAFT', pack=pack.ref(),
        scope={'include': ['LIF-07']}, values={'LIF-07': {}}).seal()
    source = Store(root / 'source'); put_pack(source, PACK_DIR); source.put_many([env, policy])
    plan = select(pack, [env], policy); source.put(plan)
    for name, value in (('target', target), ('manifest', manifest)):
        (root / (name + '.json')).write_text(json.dumps(value))
    args = dict(store=source.root, plan_id=plan.id, pack=PACK_DIR, target=root / 'target.json',
        prepared=root / 'prepared/manifest.json', manifest=root / 'manifest.json', output=root / 'output', entered_by='EXAMPLE-native-contract')
    before = tool._inventory(source.root)
    original = helper.collect_startup
    helper_calls = []
    def example_transport(*a, **kwargs):
        helper_calls.append(True)
        return original(*a, **kwargs, _reader_factory=UnavailableReader)
    monkeypatch.setattr(helper, 'collect_startup', example_transport)
    argv = []
    for key, value in args.items(): argv.extend(['--' + ('plan' if key == 'plan_id' else key.replace('_', '-')), str(value)])
    argv.append('--collect-native')
    stdout = io.StringIO(); stderr = io.StringIO()
    requested_evidence = os.environ.get('BYUL_LIF07_NATIVE_CONTRACT_EVIDENCE')
    destination = Path(requested_evidence) if requested_evidence else None
    if destination is not None:
        assert not destination.exists()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr): tool.main(argv)
        summary = json.loads(stdout.getvalue())
        out = args['output']; saved = json.loads((out / 'worker-readiness.json').read_text())
        st, restored = Store.read_only(out / 'store'), Store.read_only(out / 'restored')
        assert helper_calls == [True] and summary['input_mode'] == 'native_startup_observation'
        assert summary['requests'] == 0 and summary['actual_target_executed'] is False
        assert summary['target_io_attempted'] is True and summary['target_io_origin'] == 'EXAMPLE'
        assert summary['selected_session_seal_attempted'] and summary['selected_session_sealed']
        assert saved['native21']['missing'] and saved['native_collection']['parser_diagnostic_codes']
        assert len(saved['native_collection']['capture_states']) == 2
        assert saved['ready_send_observed'] is saved['rank_coverage_verified'] is None
        assert saved['qualified_rows'] == 0 and saved['source_authentication_verified'] is False
        assert tool._refs(st) == tool._refs(restored) and st.verify() == restored.verify() == []
        evd, = st.iter_kind('EVD'); res, = st.iter_kind('RES')
        assert evd.data_origin.value == 'DECLARED' and not evd.normal_control.attempted
        assert res.verdicts.model_dump(mode='json') == {k: 'UNKNOWN' for k in ('security', 'functional', 'performance')}
        assert tool._inventory(source.root) == before and PRIVATE not in json.dumps(saved)
    finally:
        if destination is not None:
            destination.mkdir(parents=True)
            shutil.copytree(root, destination / 'case')
            (destination / 'stdout.log').write_text(stdout.getvalue())
            (destination / 'stderr.log').write_text(stderr.getvalue())
            (destination / 'invocation.json').write_text(json.dumps({'entrypoint': 'tools.b_lif07_readiness.main(argv)',
                'argv': argv, 'pytest_process_argv': sys.argv, 'helper_calls': len(helper_calls),
                'helper_source_sha256': hashlib.sha256(Path(helper.__file__).read_bytes()).hexdigest(),
                'EXAMPLE_reader': 'UnavailableReader; original public capture_session and generation_metadata.project used',
                'actual_target_worker_GPU': 0, 'source_store_inventory': before}, ensure_ascii=False, indent=2))


@pytest.mark.parametrize('error', [None, 'CALLBACK_UNAVAILABLE', PRIVATE, False])
def test_actual_A_callback_error_enum_none_and_string_survive_without_private_or_bool_coercion(case, error):
    from guard.adapters import vllm_worker_startup_metadata as meta
    from tests.test_b_lif07_native_capture import unavailable_callback_packet
    payload = unavailable_callback_packet(case['prepared'])['recordings'][0]['capture']['records'][1]['payload']
    valid = error is None or error == 'CALLBACK_UNAVAILABLE'
    payload['callback_error'] = error
    if error is not None: payload.update(callback=None, callback_code_verified=False)
    if valid: assert meta.validate_sample(payload) == payload
    else:
        with pytest.raises(ValueError, match='^WORKER_STARTUP_EVENT_INVALID$'): meta.validate_sample(payload)
    raw = native_observation(case)
    raw['native21']['events'] = [payload]
    for row in raw['snapshots']: row['native_outer']['callback_error'] = error
    value = tool.project(raw, case['env'], case['target'], case['prepared'], case['manifest'], native_observation=True)
    saved = value['native21']['events'][0]
    assert saved['callback_error'] == (error if valid else None)
    assert saved['callback_error_collected'] is valid
    for row in value['rows']:
        assert row['native_outer']['callback_error'] == (error if valid else None)
        assert row['native_outer']['callback_error_collected'] is valid
        assert row['metadata']['source_qualified'] is False
    assert PRIVATE not in json.dumps(value)


def test_actual_A_callback_close_unavailable_gap_survives_native_close_projection(case):
    from guard.adapters import vllm_worker_startup_metadata as meta, vllm_worker_startup_sensor as sensor
    runtime = 'cpython-3.12.3'
    close = {'schema': sensor.CLOSE_SCHEMA, 'session_sha256': tool.digest(case['prepared']['session']),
        'observer_runtime': runtime, 'observer_code_sha256': sensor.RUNTIME_CODE_CONTRACTS[runtime],
        'result': {'complete': False, 'pending_calls': None, 'collection_gap': True, 'gaps': ['CALLBACK_CLOSE_UNAVAILABLE']},
        **{k: False for k in sensor.TRUTH}}
    assert meta.validate_close(close) == close
    raw = native_observation(case, [])
    raw['observer_closes'] = [{'stage': 'final', 'recording': 0, 'pid': 20, 'start_ticks': 99,
        'event_seq': 12, 'result': {**close['result'], 'gaps': ['CALLBACK_CLOSE_UNAVAILABLE', PRIVATE]}}]
    raw['observer_close'] = close['result']
    value = tool.project(raw, case['env'], case['target'], case['prepared'], case['manifest'], native_observation=True)
    assert value['observer_closes'][0]['result']['gaps'] == ['CALLBACK_CLOSE_UNAVAILABLE']
    assert value['observer_close']['gaps'] == ['CALLBACK_CLOSE_UNAVAILABLE']
    assert value['observer_closes'][0]['result']['complete'] is False and PRIVATE not in json.dumps(value)


def test_fixed_helper_close_qualification_and_nested_diagnostics_are_preserved(case):
    from tools import b_lif07_native_capture as helper
    codes = {'LIFECYCLE_IMAGE_RECHECK_UNAVAILABLE', 'LIFECYCLE_RECHECK_UNAVAILABLE',
        'LIFECYCLE_IMAGE_BINDING_UNVERIFIED', 'LIFECYCLE_PROCESS_BINDING_UNVERIFIED', 'LIFECYCLE_INITIAL_OWNER_RECHECK_UNAVAILABLE'}
    assert codes <= helper.PARSER_DIAGNOSTIC_CODES
    raw = native_observation(case, [])
    raw['cleanup']['worker_observer_closed'] = False
    raw['native_collection'].update(parser_diagnostic_codes=[*sorted(codes), PRIVATE],
        capture_states=[{'stage': 'initial', 'capture_succeeded': True, 'live_census_stable': False, 'recorded_files_covered': False},
            {'stage': 'final', 'capture_succeeded': False, 'live_census_stable': None, 'recorded_files_covered': None}])
    value = tool.project(raw, case['env'], case['target'], case['prepared'], case['manifest'], native_observation=True)
    assert value['cleanup']['worker_observer_closed'] is False
    assert value['native_collection']['parser_diagnostic_codes'] == sorted(codes)
    assert value['native_collection']['additional_parser_diagnostic_count'] == 1
    assert value['native_collection']['capture_states'] == raw['native_collection']['capture_states']
    assert value['native21']['producer_authenticated'] is False and PRIVATE not in json.dumps(value)


def test_native_manual_text_separates_bootstrap_SEND_from_upper_READY_and_service(case, monkeypatch):
    raw = native_observation(case, [])
    raw['native21']['ready_sends'] = [{'stage': 'initial', 'operation': 'SEND', 'status': 'READY', 'paired': True,
        'terminal_phase': 'RETURN', 'source_main_entry_observed': True, 'original_send_return_observed': True}]
    monkeypatch.setattr(tool, '_collect_native', lambda *a: raw)
    summary = tool.run(**case['args'], collect_native=True)
    doc = yaml.safe_load((case['args']['output'] / 'manual-evidence.yaml').read_text()); entry, = doc['checks']
    assert '선정 bootstrap SEND' in entry['basis'] and '상위 READY' in entry['basis']
    assert '선정 bootstrap SEND' in entry['observation_notes'][0]['answer']
    assert '실제 READY-send/지연/접수·준비후canary 미관측' not in entry['basis']
    assert '실제 READY/지연/접수/복구 receipt 미수집' not in entry['observation_notes'][0]['answer']
    saved = json.loads((case['args']['output'] / 'worker-readiness.json').read_text())
    assert saved['native21']['ready_sends'][0]['original_send_return_observed'] is True
    assert entry['normal_control']['result'] == 'not_attempted' and all(not row['attempted'] for row in entry['challenges'])
    assert summary['verdicts'] == {k: 'UNKNOWN' for k in ('security', 'functional', 'performance')}


@pytest.mark.parametrize('claimed', [
    '062af9d1e0bc76a9c6f180af40e2b9fe52ffeb89',
    '9d8987a5a306ffce28fcb735e012c400589303f2',
    PRIVATE, None, [],
])
def test_native_reviewed_common_provenance_preserves_exact_known_set_without_import_truth(case, monkeypatch, claimed):
    """소비기 단독 EXAMPLE codec: helper의 확정 known set만 주입하며 실제 source 검증은 수행하지 않는다."""
    from tools import b_lif07_native_capture as helper
    approved = frozenset({'062af9d1e0bc76a9c6f180af40e2b9fe52ffeb89',
        '9d8987a5a306ffce28fcb735e012c400589303f2'})
    monkeypatch.setattr(helper, 'APPROVED_REVIEWED_COMMON_COMMITS', approved, raising=False)
    raw = native_observation(case)
    raw['source_validation'] = {'reviewed_common_commit': claimed,
        'approved_native_source_commit': helper.APPROVED_SOURCE_COMMIT,
        'runtime_hash_approval': True, 'producer_authenticated': True}
    for row in raw['snapshots']:
        row['stage'] = 'initial'
        row['native_outer'].update(recording=0, source_verified=True,
            process_domain={'pid': 20, 'start_ticks': 99, 'boot_sha256': '1' * 64})
    native = tool.project(raw, case['env'], case['target'], case['prepared'], case['manifest'], native_observation=True)
    imported = project(case, raw)
    expected = claimed if type(claimed) is str and claimed in approved else None
    assert native['native_source_validation'] == {'reviewed_common_commit': expected,
        'approved_native_source_commit': helper.APPROVED_SOURCE_COMMIT,
        'runtime_hash_approval': False, 'producer_authenticated': False}
    assert imported['native_source_validation'] is None
    for value in (native, imported):
        assert value['qualified_rows'] == 0 and value['ready_send_observed'] is None
        assert all(row['metadata']['source_qualified'] is False for row in value['rows'])
        assert [(row['stage'], row['seq'], row['native_outer']['recording'], row['native_outer']['process_domain'])
            for row in value['rows']] == [('initial', seq, 0, {'pid': 20, 'start_ticks': 99, 'boot_sha256': '1' * 64,
                'time_offsets_sha256': None, 'pid_namespace': None, 'time_namespace': None}) for seq in (1, 2)]
        assert PRIVATE not in json.dumps(value)
    assert all(row['native_outer']['source_verified'] is False for row in imported['rows'])
    assert native['data_origin'] == imported['data_origin'] == 'EXAMPLE'
