"""OBS02 profiler canonical product; native/probe and backend are EXAMPLE."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import asyncio,json,shutil,socket,subprocess
import pytest
from typer.testing import CliRunner
from guard.adapters import vllm_profiler_sensor as sensor
from guard.cli import app
from guard.collectors.lifecycle import prepare_assets,load_prepared
from guard.executors.kubernetes_profiler_observation import normalize_policy
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_a1_observation_state_product import World as StateWorld
from tests.test_a1_profiler_sensor import make_world,invoke,PRIVATE
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tools.build_kubernetes_profiler_state_pack import derive

REPO=Path(__file__).resolve().parents[2]


def insert_profiler(raw,events,mode):
    def add(rows):
        values=deepcopy(events);offset=rows[-1]['monotonic_ns']+10000-values[0]['monotonic_ns']
        for value in values:
            value['monotonic_ns']+=offset;payload=value['payload']
            if value['event']=='PROFILER':
                for key in ('operation_started_ns','operation_finished_ns'):
                    if payload[key] is not None:payload[key]+=offset
                if mode=='unsafe-profiler-record':payload['unexpected']=PRIVATE
            rows.insert(-1,value)
        rows[-1]['monotonic_ns']=max(rows[-1]['monotonic_ns'],values[-1]['monotonic_ns']+1)
    return revised(raw,add)


def inputs(tmp_path,group,target,patch,mode):
    if mode!='profile8':
        destination=group.root/'profiler-assets';prepare_assets(destination,REPO,profile=sensor.PROFILE)
        group.prepared=load_prepared(destination/'manifest.json');group.manifest_path=destination/'manifest.json'
        def header(rows):
            rows[0]['payload'].update(profile=sensor.PROFILE,schema=sensor.SCHEMA,session=group.prepared['session'],
                sensor_sha256=group.prepared['assets']['guard_lifecycle_sensor.py']['sha256'],settings_sha256=group.prepared['assets']['settings.json']['sha256'])
        for pid in group.raw_by_pid:group.raw_by_pid[pid]=revised(group.raw_by_pid[pid],header)
    if mode not in {'profile8','missing-profiler'}:
        selected=mode if mode in {'backend-stop-failure','post-stop-output-failure','start-failure'} else 'backend-stop-failure' if mode in {'attachment','after-timeout','seal-failure','http-cleanup'} else 'normal'
        producer=make_world(session=group.prepared['session'],mode=selected,patch=patch);loop=asyncio.new_event_loop()
        try:invoke(producer,loop)
        finally:loop.close()
        group.raw_by_pid[43]=insert_profiler(group.raw_by_pid[43],producer.observer.recorded,mode)
    env=group.env.model_copy(deep=True);env.model.id='EXAMPLE-model';env.model.revision='EXAMPLE-revision'
    if mode=='version032':env.engine.version='0.32.0'
    group.env=env.seal();target=deepcopy(target);target['environment_ref']=group.env.ref().model_dump(mode='json')
    world=StateWorld(group,target,mode);derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack');now=utcnow()
    values={'observation_state_environment_sha256':group.env.ref().sha256,'observation_state_target_sha256':sha256_hex(canonical_json(world.target)),
        'observation_state_validity_start':(now-timedelta(seconds=60)).isoformat(),'observation_state_validity_end':(now+timedelta(seconds=60)).isoformat(),
        'observation_state_required_components':['http_listener'],'profiler_prepared_sha256':'0'*64 if mode=='prepared-mismatch' else sha256_hex(canonical_json(group.prepared)),
        'profiler_backend_kind':'torch','profiler_required_ranks':['1' if mode=='wrong-rank' else '0']}
    shutil.copytree(group.manifest_path.parent,tmp_path/'prepared')
    return world,values,pack


@pytest.mark.parametrize('mode',['normal','backend-stop-failure','post-stop-output-failure','start-failure','native-one-missing','native-partial','native-census-gap',
    'after-timeout','attachment','seal-failure','engine-replaced','wrong-response-id','missing-profiler','wrong-rank','profile8','version032',
    'actual-lab-false','prepared-mismatch','http-cleanup','unsafe-profiler-record'])
def test_registered_cli_profiler_facts_and_source_failures_exact_refs(tmp_path,group,native_target,writer_factory,monkeypatch,mode):
    world,values,pack=inputs(tmp_path,group,native_target,monkeypatch,mode)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*args,**kw):
            if path.name=='probe_log.jsonl' and args and args[0]=='w':raise OSError(PRIVATE)
            return original(path,*args,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['OBS-02']},values={'OBS-02':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nprofiler_observation="kubernetes"\n[adapter]\nname="vllm"\n[profiler_observation]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(tmp_path/'prepared/manifest.json'))+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,expected=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==expected,(args,result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    assert next(item for item in plan.items if item.check_id=='OBS-02').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='http-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE' and all(getattr(res.verdicts,key).value=='UNKNOWN' for key in ('security','functional','performance'))
    assert e.normal_control.succeeded is None
    preflight=mode in {'profile8','version032','actual-lab-false','prepared-mismatch'}
    if preflight:assert not world.sent and e.observations['profiler_native_samples'].value is None
    else:
        assert len(world.sent)=={'wrong-response-id':3,'after-timeout':4,'engine-replaced':1}.get(mode,5)
        if mode in {'after-timeout','engine-replaced'}:assert all(row['id']!='obs02-health-after' for row in world.sent)
        assert all(e.observations[key].value is False for key in ('profiler_backend_collection_stopped_verified','profiler_trace_output_completed_verified',
            'profiler_cross_process_rpc_verified','profiler_scope_complete','state_semantics_verified','state_scope_complete'))
    if mode in {'normal','backend-stop-failure','post-stop-output-failure','start-failure','attachment','after-timeout','seal-failure','http-cleanup'}:
        assert e.observations['profiler_native_samples'].value
        candidates=e.observations['profiler_conflict_candidates'].value
        conflict=mode in {'backend-stop-failure','post-stop-output-failure','attachment','after-timeout','seal-failure','http-cleanup'}
        assert any(c['flag_adapter_conflict_observed'] for c in candidates)==conflict
        assert all(not c['actual_backend_stop_failed_verified'] and not c['actual_collection_continued_verified'] and not c['confirmed_full_state_violation'] for c in candidates)
    if mode in {'normal','backend-stop-failure','post-stop-output-failure','start-failure'}:assert e.execution.status.value=='COMPLETED'
    if mode in {'native-one-missing','native-partial','native-census-gap','after-timeout','attachment','seal-failure','engine-replaced','wrong-response-id',
        'missing-profiler','wrong-rank','profile8','version032','actual-lab-false','prepared-mismatch','http-cleanup','unsafe-profiler-record'}:assert e.execution.status.value=='ABORTED'
    assert bool(e.execution.cleanup_errors)==(mode=='http-cleanup')
    assert PRIVATE not in canonical_json(e.to_dict()).decode()
    for artifact in e.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/artifact.path).read_bytes()
    objects=[obj for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for obj in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(obj.ref()).to_dict()==obj.to_dict() for obj in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [obj.ref() for obj in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_pack_retains_original_three_branches_and_other76_bytes(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    assert len(pack.checks)==77 and receipt['unchanged_definition_bytes']==76 and not receipt['full_OBS02_complete']
    for name in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','legacy','method_and_evidence'):
        assert getattr(seed.checks['OBS-02'],name)==getattr(pack.checks['OBS-02'],name)
    for path in seed.path.rglob('*'):
        if path.is_file() and path.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/OBS-02.yaml'}:assert path.read_bytes()==(pack.path/path.relative_to(seed.path)).read_bytes()


@pytest.mark.parametrize('key,value',[('profiler_prepared_sha256','unreviewed'),('profiler_backend_kind','infer-from-HTTP200'),
    ('profiler_required_ranks',[]),('profiler_required_ranks',['0','0']),('profiler_required_ranks',['64']),('profiler_required_ranks',['00'])])
def test_policy_requires_fixed_prepared_backend_and_rank_scope(tmp_path,group,native_target,writer_factory,monkeypatch,key,value):
    _,values,_=inputs(tmp_path,group,native_target,monkeypatch,'normal');values[key]=value
    with pytest.raises(ValueError,match='PROFILER_OBSERVATION_POLICY_INVALID'):normalize_policy(values)
