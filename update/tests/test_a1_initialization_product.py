"""Canonical LIF09 CLI and exact Refs; all serving/kubectl inputs EXAMPLE."""
from copy import deepcopy
from datetime import timedelta
import json,socket,subprocess
from pathlib import Path
import pytest
from typer.testing import CliRunner
from guard.adapters import vllm_initialization_sensor as sensor
from guard.adapters import vllm_initialization_release_sensor as init_release
from guard.adapters.vllm_lifecycle_profiles import public_commit
from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.collectors import lifecycle
from guard.core.judge import judge_evidence
from guard.core.run import _normalize
from guard.executors.initialization_contract import normalize_policy
from guard.executors.registry import build_executor
from guard.models import Evidence,canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.store import Store
from tests.builders import assert_unconfirmed_security_failure, baseline_pack, dummy_ref, policy,T0
from tests.test_a1_kubernetes_model_authorization_product import World as BaseWorld
from tests.test_a1_initialization_sensor import make_world,invoke,REPO
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tools.build_kubernetes_initialization_pack import derive

PRIVATE='EXAMPLE_INITIALIZATION_PRODUCT_PRIVATE'
RELEASE_IMAGE = 'docker.io/vllm/vllm-openai@sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40'


class World(BaseWorld):
    def __init__(self,*args):
        super().__init__(*args);self.native_added=False
    def add_native(self):
        if self.native_added:return
        self.native_added=True
        world=make_world(self.group.prepared['session'],'partial-failure' if self.mode=='partial-failure' else 'normal',
                         profile=self.group.prepared['profile'])
        try:invoke(world)
        except Exception:
            assert self.mode=='partial-failure'
        additions=deepcopy(world.observer.recorded)
        if self.mode=='mp-unobserved':additions=[r for r in additions if r['event']!='INITIALIZATION']
        if self.mode=='invalid-native':next(r['payload'] for r in additions if r['event']=='INITIALIZATION')['service_gate_verified']=True
        def change(rows):
            rows[-1:-1]=additions;rows[-1]['monotonic_ns']=max(rows[-1]['monotonic_ns'],additions[-1]['monotonic_ns'])
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],change)
    def runner(self,argv,**kwargs):
        if 'exec' in argv and 'input_bytes' in kwargs:
            request,=json.loads(kwargs['input_bytes'])['requests'];self.sent.append(request)
            slot=request['id'].removeprefix('lif09-');assert slot in {'before','after'}
            assert request['method']=='POST' and request['url'].endswith('/v1/completions')
            assert request['body']=={'model':'fixture-model','prompt':'hello','max_tokens':1,'temperature':0,'stream':False}
            self.add_native();body=json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE}]})
            if self.mode=='before-shape' and slot=='before':body='{}'
            if self.mode=='after-timeout' and slot=='after':raise TimeoutError(PRIVATE)
            row={'id':request['id'],'method':'POST','mode':'json','outcome':503 if self.mode=='partial-failure' else 200,
                'body':body,'body_sha256':sha256_hex(body.encode()),'body_bytes':len(body.encode()),'capture_complete':True,
                'eof':True,'utf8_valid':True,'body_truncated':False}
            if self.mode=='wrong-response-id':row['id']='other'
            pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
            if self.mode=='http-cleanup' and slot=='after':pipe.update(pipes_closed=False,read_errors=['child_reap'])
            return {'returncode':0,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        if self.mode=='dead-process' and 'exec' in argv and any('guard-lifecycle-process-context/1' in v for v in argv):raise OSError(PRIVATE)
        result=super().runner(argv,**kwargs)
        if 'get' in argv:
            value=json.loads(result['stdout_bytes'])
            if 'namespaces' in argv:
                for item in value['items']:item['kind']='Namespace'
            if 'pod' in argv:
                probe=self.target['kubernetes']['probe']['pod'] in argv
                if self.mode=='probe-not-ready' and probe or self.mode in {'partial-failure','engine-not-ready'} and not probe:
                    value['status']['conditions']=[{'type':'Ready','status':'False'}]
                    value['status']['containerStatuses'][0]['ready']=False
            result['stdout_bytes']=canonical_json(value)
        return result


def inputs(tmp_path,group,target,mode,*,profile=sensor.PROFILE):
    environment=group.env.model_copy(deep=True);environment.model.id='EXAMPLE-model';environment.model.revision='EXAMPLE-revision'
    environment.model.inventory_sha256='b'*64
    if mode=='version032':environment.engine.version='0.32.0'
    target=deepcopy(target)
    if profile == init_release.PROFILE:
        environment.engine.version = '0.30.0'
        environment.engine.image_digest = RELEASE_IMAGE
        target['kubernetes']['engine']['image'] = RELEASE_IMAGE
    group.env=environment.seal();target['environment_ref']=group.env.ref().model_dump(mode='json')
    folder=group.root/'initialization-prepared';group.prepared=lifecycle.prepare_assets(folder,REPO,profile=profile);group.manifest_path=folder/'manifest.json'
    def header(rows):
        rows[0]['payload'].update(schema=init_release.SCHEMA if profile==init_release.PROFILE else sensor.SCHEMA,
            profile=profile,commit=public_commit(profile),session=group.prepared['session'],
            settings_sha256=group.prepared['assets']['settings.json']['sha256'],sensor_sha256=group.prepared['assets']['guard_lifecycle_sensor.py']['sha256'])
        if profile==init_release.PROFILE:
            # Drop unrelated /8 request rows. The exact /26 observer fills the typed startup data.
            del rows[1:-1]
    group.raw_by_pid={pid:revised(raw,header) for pid,raw in group.raw_by_pid.items()}
    world=World(group,target,mode);receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack');now=utcnow()
    values={'init_environment_sha256':group.env.ref().sha256,'init_target_sha256':sha256_hex(canonical_json(world.target)),
        'init_prepared_sha256':sha256_hex(canonical_json(group.prepared)),'init_validity_start':(now-timedelta(minutes=1)).isoformat(),
        'init_validity_end':(now+timedelta(minutes=2)).isoformat(),'init_executor_backend':'mp','init_expected_ranks':['0','1'],
        'init_scenario_sha256':'d'*64,'init_phase':mode if mode in {'partial-failure','recovered-normal'} else 'normal',
        'init_required_stages':['worker-ready','model-load','admission'],'init_failure_rank':1,'init_failure_stage':'worker-ready',
        'init_preparation_deadline_ms':10000,'init_recovery_deadline_ms':10000}
    return world,values,pack,receipt


@pytest.mark.parametrize('mode',['normal','partial-failure','recovered-normal','dead-process','mp-unobserved','invalid-native',
    'native-one-missing','native-partial','native-census-gap','engine-not-ready','probe-not-ready','before-shape','after-timeout',
    'wrong-response-id','engine-replaced','seal-failure','http-cleanup','attachment','actual-lab-false','version032'])
def test_registered_cli_preparation_and_aggregate_claims_exact_refs(tmp_path,group,native_target,monkeypatch,mode):
    world,values,pack,_=inputs(tmp_path,group,native_target,mode)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['LIF-09']},values={'LIF-09':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\ninitialization="kubernetes"\n[adapter]\nname="vllm"\n[initialization]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(group.manifest_path))+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,code=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==code,(args,result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    assert next(i for i in plan.items if i.check_id=='LIF-09').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='http-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE'
    assert_unconfirmed_security_failure(res)
    assert res.verdicts.functional == res.verdicts.performance == "UNKNOWN"
    assert e.normal_control.succeeded is None
    flags=('init_producer_authenticated','init_all_preparation_verified','init_service_gate_verified','init_cross_phase_lineage_verified','init_scope_complete')
    if mode in {'actual-lab-false','version032'}:
        assert not world.sent and all(e.observations[k].value is None and not e.observations[k].collected for k in flags)
    else:
        assert all(e.observations[k].value is False for k in flags)
        assert len(world.sent)=={'engine-replaced':1,'wrong-response-id':1,'probe-not-ready':0}.get(mode,2)
        if mode=='wrong-response-id':
            assert e.execution.status.value==('NOT_RUN' if mode=='version032' else 'ABORTED')
            trials=e.observations['init_observation_trials'].value
            assert len(trials)==2 and all('outcome' not in row for row in trials)
            assert all('normal_shape_verified' not in row for row in trials)
    if mode in {'normal','partial-failure','recovered-normal','dead-process','engine-not-ready','after-timeout','wrong-response-id','seal-failure','http-cleanup','attachment'}:
        samples=e.observations['init_native_samples'].value;correspondence=e.observations['init_correspondence'].value
        assert samples and correspondence and all(not c['confirmed_violation'] for c in correspondence)
        if mode=='dead-process':assert all(not r['process_binding_verified'] for r in samples)
        else:assert any(c['same_process_interval_observed'] for c in correspondence)
        if mode=='partial-failure':
            assert any(c['ready_failure_propagation_observed'] for c in correspondence)
            assert all(t['engine_readiness_before']=={'pod_ready':False,'container_ready':False} for t in e.observations['init_observation_trials'].value)
    if mode in {'dead-process','mp-unobserved','invalid-native','native-partial','engine-replaced','probe-not-ready','actual-lab-false','version032'}:assert e.execution.status.value==('NOT_RUN' if mode=='version032' else 'ABORTED')
    assert bool(e.execution.cleanup_errors)==(mode=='http-cleanup')
    assert PRIVATE not in canonical_json(e.to_dict()).decode()
    for artifact in e.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/artifact.path).read_bytes()
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


@pytest.mark.parametrize(('check_id', 'mode', 'profile'), [
    ('LIF-07', 'normal', sensor.PROFILE), ('LIF-09', 'normal', init_release.PROFILE),
    ('LIF-07', 'dead-process', sensor.PROFILE), ('LIF-09', 'before-shape', sensor.PROFILE),
])
def test_canonical_initialization_evidence_preserves_bound_positive_steps(tmp_path, group, native_target, monkeypatch,
                                                                       check_id, mode, profile):
    world, values, _, _ = inputs(tmp_path, group, native_target, mode, profile=profile)
    pack = load_pack(REPO / 'update/packs/PACK-BASELINE')
    check = pack.checks[check_id]
    if check_id == 'LIF-07':
        values['required_readiness_items'] = ['required-rank', 'required-model']
    target = tmp_path / 'canonical-target.json'
    target.write_bytes(canonical_json(world.target))
    monkeypatch.setattr(http, 'capture_pipes', world.runner)
    monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes', world.runner)
    executor = build_executor('initialization', 'kubernetes', {'initialization': {
        'target': str(target), 'prepared': str(group.manifest_path)}}, engine=group.env.engine.name,
        version=group.env.engine.version)
    draft = executor.run(check, group.env, values, VllmAdapter(), tmp_path / 'evidence')
    evidence = Evidence(id='EVD-CANONICAL-INIT', data_origin=draft.data_origin, plan=dummy_ref('PLAN'),
        environment=group.env.ref(), check_id=check_id, side='single', execution={'status': draft.status},
        **_normalize(check, draft)).seal()
    observed = {key: item.value for key, item in evidence.observations.items() if item.collected}
    native = [row for row in observed['activation_steps'] if row['step'] == 'native-initialization-correspondence']
    controls = [row for row in observed['activation_steps'] if row['step'] == 'direct-initialization-normal-control']
    assert bool(native) == (mode != 'dead-process')
    assert len(controls) == (1 if mode == 'before-shape' else 2)
    assert all(row['same_process_interval_observed'] and row['required_rank_ready_return_observed'] for row in native)
    source = (init_release if profile == init_release.PROFILE else sensor).SOURCE_ADDITIONS[sensor.MP]
    assert all(row['source_sha256'] == source for row in native)
    artifact, = [item for item in draft.artifacts if item.name == 'probe_log.jsonl']
    summary = json.loads(artifact.path.read_text().splitlines()[-1])
    assert summary['summary'] is True
    for row in native:
        receipt = row['source_recording']
        assert set(receipt) == {'sha256', 'aggregate_call_and_return_seq', 'ready_call_and_return_seq'}
        capture, = [recording['capture'] for recording in summary['native']['recordings']
                    if recording['capture'] and recording['capture']['provenance']['capture_sha256'] == receipt['sha256']]
        assert capture['process_context_verified'] and capture['provenance']['owner_binding_verified']
        assert capture['summary']['recording_closed'] and capture['summary']['discarded_tail_bytes'] == 0
        records = {item['seq']: item for item in capture['records']}
        for field, call in (('aggregate', 'executor_call_sha256'), ('ready', 'ready_call_sha256')):
            call_seq, return_seq = receipt[f'{field}_call_and_return_seq']
            assert call_seq < return_seq
            assert records[call_seq]['payload']['phase'] == 'CALL'
            assert records[return_seq]['payload']['phase'] == 'RETURN'
            assert records[call_seq]['payload']['call_sha256'] == records[return_seq]['payload']['call_sha256'] == row[call]
    assert all(row['normal_shape_verified'] and not row['service_gate_verified'] for row in controls)
    assert not observed['activation_scope_complete'] and not observed['activation_violations']
    assert 'readiness_and_http_shape_are_not_independent_admission_oracle' in observed['activation_missing']
    result = judge_evidence(check, evidence, policy(pack.ref(), values={check_id: values}), utcnow())
    assert evidence.data_origin.value == 'EXAMPLE'
    assert result.verdicts.security.value == 'UNKNOWN'


def test_pack_keeps_full_startup_question_and_76_original_definition_bytes(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    assert len(pack.checks)==77 and receipt['unchanged_definition_bytes']==76 and not receipt['full_LIF09_complete']
    for name in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','legacy','method_and_evidence'):
        assert getattr(seed.checks['LIF-09'],name)==getattr(pack.checks['LIF-09'],name)
    for p in seed.path.rglob('*'):
        if p.is_file() and p.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/LIF-09.yaml'}:assert p.read_bytes()==(pack.path/p.relative_to(seed.path)).read_bytes()


@pytest.mark.parametrize('key,value',[('init_executor_backend','ray'),('init_phase','complete'),('init_required_stages',['worker-ready']),
    ('init_expected_ranks',['0','0']),('init_failure_rank',True),('init_preparation_deadline_ms',-1),('init_scenario_sha256','x'),
    ('init_validity_start','2026-10-30T01:00:00')])
def test_invalid_policy_is_rejected_without_io(tmp_path,group,native_target,key,value):
    world,values,pack,_=inputs(tmp_path,group,native_target,'normal');values[key]=value
    with pytest.raises(ValueError,match='INITIALIZATION_POLICY_INVALID'):normalize_policy(values)
    assert not world.sent
