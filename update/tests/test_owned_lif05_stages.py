"""Exercise the coordinator's boundary and the unchanged product judge."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from guard.collectors.file import load_environment
from guard.collectors.lifecycle import load_prepared
from guard.configuration import for_side
from guard.core.execution_recipe import bind_recipe, execution_lock
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan_locked
from guard.core.select import select
from guard.executors import lifecycle_transition as native
from guard.executors.lif05_stages import SETTING, StagedLif05, combine_dispatch
from guard.models import DataOrigin, ExecStatus, PolicyStatus, canonical_json, sha256_hex, utcnow
from guard.store import Store
from guard.store.packs import put_pack
from tests import test_redeployment_preflight as fixture
from tests.test_lifecycle_capture import external


ATTEMPT = 'ATTEMPT-STAGED-LIF05-REGRESSION'
PRIVATE_DIGEST = sha256_hex(b'absolutely no raw request correlation export')
ORIGIN = datetime(2026, 11, 28, 0, 0, 0, tzinfo=timezone.utc)


def at(offset):
    return (ORIGIN + timedelta(seconds=offset)).isoformat()


def ref(obj):
    return obj.ref().model_dump(mode='json')


def load_generation(row):
    env = load_environment(Path(row['environment']))
    target = json.loads(Path(row['target']).read_bytes())
    return native.Generation(row['id'], env, target, native.generation_native_target(target, env, require_lab=False),
                             load_prepared(Path(row['prepared'])))


def owner(generation):
    kube = generation.target['kubernetes']
    engine = kube['engine']
    env = generation.env
    return dict(context=kube['context'], cluster_uid='fixture-cluster-uid', namespace=engine['namespace'],
        namespace_uid=engine['namespace_uid'], deployment='serving-engine', deployment_uid='fixture-deployment',
        replicaset='serving-engine-one', replicaset_uid='fixture-replicaset', template_sha256='e'*64,
        launch_sha256='9'*64, node=engine['node'], image=engine['image'], image_id=engine['image_id'],
        version=env.engine.version, model_inventory_sha256=env.model.inventory_sha256,
        model_sha256=sha256_hex(canonical_json(env.model.model_dump(mode='json'))), probe_pod_uid=kube['probe']['pod_uid'])


def start(pack, policy, side, old):
    return StagedLif05(ATTEMPT, side, ref(policy), ref(pack), '7'*64, old, owner(old), at(0))


def retirement(stage):
    old = stage.scope()['old']
    return {'scope_sha256':stage.scope_sha256, 'pod_uid':old['pod_uid'], 'container_id':old['container_id'],
        'namespace_uid':old['owner']['namespace_uid'], 'deployment_uid':old['owner']['deployment_uid'],
        'old_pod_absent':True, 'replicas_zero':True, 'gpu_idle':True, 'old_container_task_absent':True,
        'stop_requested_at':at(2), 'absent_at':at(3), 'gpu_observed_at':at(4)}


def finish(stage, old, new, *, traced=False):
    stage.retire(retirement(stage))
    new.target['kubernetes']['engine']['started_at'] = at(5)
    new.native_target['kubernetes']['engine']['started_at'] = at(5)
    stage.bind_replacement(new, owner(new), at(5))
    trials = [{'case_id':case, 'generation':kind, 'request_id_sha256':PRIVATE_DIGEST if case == 'before' else None,
               'attempted':True, 'normal_shape_verified':case in {'before','after','recovered'},
               'transport_dispatched':case != 'during', 'dispatch_evidence':[],
               'transport':{'requests':[{'id':'before' if case == 'before' else 'after',
                   'binding_verified_before_after':case in {'before','after','recovered'}}]},
               'started_at':at(start), 'finished_at':at(end)}
              for case, kind,start,end in [('before','old',1,1.1),('inflight','old',1.5,2.5),('during','old',4.1,4.2),
                                         ('after','new',7,7.1),('recovered','new',8,9)]]
    trials[2]['transport_attempt'] = {'target_pod_uid':old.target['kubernetes']['engine']['pod_uid'],
                                    'probe_pod_uid':old.target['kubernetes']['probe']['pod_uid']}
    samples = [{'phase':phase, 'generation':g.key, 'native':None,
                'sample_started_at':at(start), 'sample_finished_at':at(end)}
               for phase,g,start,end in [('before',old,1.2,1.3),('recovered',new,9.1,9.2)]]
    request_trace = []
    for gen in (old,new):
        owned = owner(gen)
        cases = ('before','inflight') if gen.key == 'old' else ('after','recovered')
        request_trace.append({'scope': {
            'generation':gen.key, 'attempt_id':ATTEMPT, 'session':gen.prepared['session'],
            **{key:gen.target['kubernetes']['engine'][key] for key in ('pod_uid','container_id')},
            **{key:owned[key] for key in ('image','image_id','namespace_uid','probe_pod_uid')}},
            'source_verified':True, 'cases':[
                {'case_id':case, 'transport_dispatched':True,
                 'request_observer_invoked':traced and case == 'before', 'assignment_bound':False}
                for case in cases]})
    stage.seal(phases={'before':at(1),'during':at(2),'after':at(7),'recovered':at(9)},
               trials=trials,samples=samples,diagnostics=['TRANSITION_DURING_CONTROL_NOT_DISPATCHED'],
               cleanup=[],stream={'http_status':200, 'normal_completion_verified':False},
               request_trace=request_trace,
               replacement_scale={'replacement_requested_at':at(4.6), 'acknowledged_at':at(4.9)})


@pytest.fixture
def staged(external, monkeypatch):
    create_environment = fixture.environment

    def environment(**kwargs):
        kwargs['origin'] = DataOrigin.DECLARED
        kwargs['model']['revision'] = 'same-owned-model-revision'
        return create_environment(**kwargs)

    monkeypatch.setattr(fixture,'environment',environment)
    pack, envs, policy, config = fixture._configuration(external, 'LIF-05', mvp_inputs=True, lab=False)
    policy = policy.model_copy(update={'status': PolicyStatus.CONFIRMED, 'data_origin': DataOrigin.DECLARED,
                                      'confirmed_by': 'fixture only', 'confirmed_at': ORIGIN}).seal()
    states = {}
    for side in ('baseline','candidate'):
        settings = config['sides'][side]['check_config']['LIF-05']
        old, new = map(load_generation, settings['generations'])
        # Use the pinned old relationship and a fresh simulated container.
        # This fixture's timestamps are a CPU-only control.
        new.target['kubernetes']['engine']['started_at'] = at(5)
        new.native_target['kubernetes']['engine']['started_at'] = at(5)
        path = Path(settings['generations'][1]['target'])
        path.write_bytes(canonical_json(new.target))
        native_target = native.generation_native_target(new.target, new.env, require_lab=False)
        from guard.collectors.lifecycle import prepare_assets
        from guard.collectors.model_read_scope import build_scope
        scope = build_scope(new.env, native_target, path.parent/'reference.json', '/models', nonsecret_model_weights=True)
        scope_file = path.parent/'new-scope.json'
        scope_file.write_bytes(canonical_json(scope))
        assets = path.parent/'fresh-assets'
        prepare_assets(assets, fixture.REPOSITORY, profile=native.generation_sensor.PROFILE, model_read_scope=str(scope_file))
        settings['generations'][1]['prepared'] = str(assets/'manifest.json')
        states[side] = (old, load_generation(settings['generations'][1]))
    return pack, envs, policy, config, states


@pytest.mark.parametrize('change', ['early','gpu','retirement_cid','same_pod','same_cid','template','replicaset','cluster'])
def test_rejects_unqualified_replacement(staged, change):
    pack, _, policy, _, states = staged
    old, new = states['baseline']
    stage = start(pack, policy, 'baseline', old)
    if change == 'early':
        with pytest.raises(ValueError, match='REPLACEMENT_OUT_OF_ORDER'):
            stage.bind_replacement(new, owner(new), at(5))
        return
    observed = retirement(stage)
    if change in ('gpu','retirement_cid'):
        observed['gpu_idle' if change == 'gpu' else 'container_id'] = False
        with pytest.raises(ValueError, match='RETIREMENT_UNVERIFIED'):
            stage.retire(observed)
        return
    stage.retire(observed)
    later_owner = owner(new)
    if change in ('same_pod','same_cid'):
        field = 'pod_uid' if change == 'same_pod' else 'container_id'
        new.target['kubernetes']['engine'][field] = old.target['kubernetes']['engine'][field]
    else:
        field = {'template':'template_sha256','replicaset':'replicaset_uid','cluster':'cluster_uid'}[change]
        later_owner[field] = 'b'*64
    with pytest.raises(ValueError, match='REPLACEMENT_IDENTITY_MISMATCH'):
        stage.bind_replacement(new, later_owner, at(5))


@pytest.mark.parametrize('change', [None,'observed_no_assignment','missing_coordinator','other_attempt',
                                    'other_side','changed_digest','during_new'])
def test_live_handoff_uses_native_evidence_pipeline(staged,tmp_path,monkeypatch,change):
    pack, envs, policy, config, states = staged
    stages = {}
    for side,(old,new) in states.items():
        stage = start(pack,policy,'candidate' if change == 'other_side' else side,old)
        finish(stage,old,new,traced=change == 'observed_no_assignment')
        stages[side] = stage
        settings = config['sides'][side]['check_config']['LIF-05']
        settings[native.GENERATION_MANIFEST_PIN] = sha256_hex(canonical_json(native.inputs_manifest(settings)))
        settings[SETTING] = 'd'*64 if change == 'changed_digest' else stage.digest()
        config['sides'][side]['run_binding'] = {'LIF-05': stage.binding()}
    if change == 'during_new':
        config['sides']['baseline']['run_binding']['LIF-05']['phase_generation']['during'] = 'new'
    target = 'baseline' if change and change != 'observed_no_assignment' else None
    store = Store(tmp_path/'store')
    put_pack(store, pack.path)
    store.put_many([*envs,policy])
    plan = bind_recipe(select(pack,envs,policy,mode='update'),config,envs)
    store.put(plan)
    attempt = 'ATTEMPT-OTHER' if change == 'other_attempt' else ATTEMPT
    cfg = RunConfig(requested=config,raw=config,executors=config['executors'],attempt_id=attempt,
        side_configs={side:RunConfig(executors=config['executors'],raw=for_side(config,side),adapter='vllm',
            lif05_stage=None if change == 'missing_coordinator' else stage) for side,stage in stages.items()})
    monkeypatch.setattr(native.KubernetesHTTP,'__call__',fixture._forbidden)
    monkeypatch.setattr(native.LifecycleTransitionExecutor,'_capture',fixture._forbidden)
    from guard.models import Side
    with execution_lock(store.root,plan.id):
        evidence = run_plan_locked(store,pack,plan,cfg,only_side=Side(target) if target else None)
    if change == 'during_new':
        one, = evidence
        assert one.execution.status == ExecStatus.ABORTED
        assert one.execution.abort_reason == 'EXECUTOR_FAILURE'
        stage = stages['baseline']
        generations = dict(zip(('old','new'), states['baseline']))
        schedule = {key:native.stamp(value) for key,value in stage.public_receipt()['phases'].items()}
        phases = stage.binding()['phase_generation']
        with pytest.raises(ValueError,match='LIF05_STAGE_PHASE_PLAN_MISMATCH'):
            stage.consume(stage.digest(), generations, schedule, {**phases,'during':'new'})
        capture, _ = stage.consume(stage.digest(), generations, schedule, phases)
        assert next(row for row in capture['trials'] if row['case_id'] == 'during')['generation'] == 'old'
        return
    if target:
        one, = evidence
        assert one.execution.status == ExecStatus.NOT_RUN
        assert one.execution.abort_reason in ('LIF05_STAGE_COORDINATOR_REQUIRED','LIF05_STAGE_PLAN_SCOPE_MISMATCH')
        return
    assert len(evidence) == 2
    assert all(e.execution.status == ExecStatus.ABORTED and e.execution.started_at is not None for e in evidence)
    for item in evidence:
        assert item.execution.attempt_id == ATTEMPT and item.data_origin == DataOrigin.DECLARED
        assert PRIVATE_DIGEST not in json.dumps(item.model_dump(mode='json'))
        assert item.observations['activation_scope_complete'].value is False
        assert item.observations['activation_steps'].value[0]['owned_sequential_generation_receipt']
        assert [step.attempted for step in item.challenges] == [True, True, change == 'observed_no_assignment']
        assert item.challenges[2].observations['bound_assignment_cases'] == []
    moment = utcnow()
    results = judge_plan(store,pack,plan,moment,attempt_id=ATTEMPT,evidence=evidence)
    report = build_report(store,pack,plan,moment,attempt_id=ATTEMPT,evidence=evidence,judged=results)
    assert len(results) == 2 and report.hash_ok()
    with execution_lock(store.root,plan.id), pytest.raises(ValueError, match='ATTEMPT_ALREADY_SEALED'):
        run_plan_locked(store,pack,plan,cfg)
    assert all(store.resolve(e.ref()).ref() == e.ref() for e in evidence)


def test_staged_projection_does_not_link_an_unattempted_or_other_generation_request(monkeypatch):
    def index(_recordings):
        return {'domains':{'one':{'assignments':[
            {'asgi_request_sha256': PRIVATE_DIGEST, 'internal_sha256':'a'*64},
            {'asgi_request_sha256': None, 'internal_sha256':'b'*64},
        ],'model_observations':[]}},'stored_record_index_complete':True}

    monkeypatch.setattr(native, 'request_index', index)
    samples = [{'phase':'after','generation':'new',
                'native':{'session':'a'*32,'recordings':[],'recorded_files_covered':True}}]
    trials = [{'case_id':'before','generation':'old','request_id_sha256':PRIVATE_DIGEST,'attempted':True,
               'transport_dispatched':True,'stock_generation_attribution_verified':True},
              {'case_id':'during','generation':'old','request_id_sha256':None,'attempted':False}]
    assert native.project(samples, trials, strict_generation=True)['request_generation_bindings'] == []
    trials[0]['generation'] = 'new'
    bindings = native.project(samples, trials, strict_generation=True)['request_generation_bindings']
    assert [row['request_id_sha256'] for row in bindings] == [PRIVATE_DIGEST]


@pytest.mark.parametrize('kind', ['preflight_not_found','reply_before_owner_loss','send_before_owner_loss',
                                  'exec_without_evidence','bound_reply'])
def test_stock_transport_dispatch_receipt_and_staged_consumer(staged, monkeypatch, kind):
    monkeypatch.syspath_prepend(Path(__file__).resolve().parents[2]/'.github/scripts/gpu63')
    from owned_lif05_staged_probe import trial

    _, _, _, _, states = staged
    old, new = states['baseline']
    engine, probe = (old.target['kubernetes'][role] for role in ('engine','probe'))
    by_pod = {binding['pod']:binding for binding in (engine,probe)}
    pod_calls = []
    execs = []
    token = 'an-owned-control'
    case = 'during' if kind == 'preflight_not_found' else 'before'
    label = 'after' if case == 'during' else 'before'
    model = old.target['model_mapping'].get('served_model_name',old.target['model_mapping'].get('api_model'))
    body = json.dumps({'choices':[{'message':{'content':'private fixture response'}}], 'model':model})

    def capture(argv, **options):
        args = argv[3:]
        ok = dict(returncode=0,capture={'complete':True},stderr_bytes=b'')
        if args[:2] == ['get','namespaces']:
            return {**ok,'stdout_bytes':canonical_json({'kind':'List','items':[
                {'kind':'Namespace','metadata':{'name':binding['namespace'],'uid':binding['namespace_uid']}}
                for binding in (engine,probe)]})}
        if args[1:2] == ['exec']:
            assert args[:5] == [f"--namespace={probe['namespace']}",'exec','-i',probe['pod'],
                                f"--container={probe['container']}"]
            execs.append(args[:5])
            request, = json.loads(options['input_bytes'])['requests']
            assert request['id'] == label and request['headers'] == {'X-Request-Id':token}
            if kind == 'exec_without_evidence':
                return {**ok,'returncode':1,'stdout_bytes':b''}
            row = {'id':label,'method':'POST','mode':'json','dispatch_ms':1,'request_sent_ms':2,
                   'timing_basis':'batch_monotonic_client_connection_only'}
            if kind == 'send_before_owner_loss':
                row['outcome'] = 'timeout'
            else:
                row.update(outcome=200,headers_ms=3,capture_complete=True,eof=True,utf8_valid=True,
                           body_truncated=False,body=body,body_bytes=len(body.encode()),
                           body_sha256=sha256_hex(body.encode()))
            return {**ok,'stdout_bytes':canonical_json([row])}
        binding = by_pod[args[3]]
        assert args == [f"--namespace={binding['namespace']}",'get','pod',binding['pod'],'-o','json']
        assert 'input_bytes' not in options
        pod_calls.append(binding['pod'])
        if kind == 'preflight_not_found':
            return {**ok,'returncode':1,'stdout_bytes':b'',
                    'stderr_bytes':f"Error from server (NotFound): pods \"{binding['pod']}\" not found\n".encode()}
        owner_uid = binding['pod_uid']
        if execs and kind.endswith('owner_loss'):
            owner_uid = new.target['kubernetes']['engine']['pod_uid']
        pod = {'kind':'Pod','metadata':{'name':binding['pod'],'namespace':binding['namespace'],
            'uid':owner_uid,'labels':{'guard.run':old.target['kubernetes']['run_id']}},
            'spec':{'nodeName':binding['node'],'containers':[{'name':binding['container'],'image':binding['image']}]},
            'status':{'phase':'Running','podIP':binding['pod_ip'],'conditions':[{'type':'Ready','status':'True'}],
                'containerStatuses':[{'name':binding['container'],'imageID':binding['image_id'],
                    'containerID':binding['container_id'],'restartCount':binding['restart_count'],'ready':True,
                    'state':{'running':{'startedAt':binding['started_at']}}}]}}
        return {**ok,'stdout_bytes':canonical_json(pod)}

    result = trial(old,case,token,runner=capture)
    observed = result['transport_attempt']
    consumed = combine_dispatch(result)
    assert observed['target_pod_uid'] == engine['pod_uid'] != new.target['kubernetes']['engine']['pod_uid']
    assert observed['probe_exec_invocations'] == len(execs) == (0 if kind == 'preflight_not_found' else 1)
    assert observed['zero_dispatch_verified'] is (kind == 'preflight_not_found')
    dispatch = {'preflight_not_found':False,'exec_without_evidence':None}.get(kind,True)
    assert consumed['transport_dispatched'] is dispatch
    assert consumed['stock_generation_attribution_verified'] is (kind == 'bound_reply')
    assert consumed['normal_shape_verified'] is (kind == 'bound_reply')
    assert (result.get('outcome') == 200) is (kind == 'bound_reply')
    if kind == 'preflight_not_found':
        assert pod_calls == [engine['pod']] and observed['selected_engine_lookups'][0]['state'] == 'selected_pod_not_found'
    if kind.endswith('owner_loss'):
        assert pod_calls == [engine['pod'],probe['pod'],engine['pod']]
        client = observed['client_probe']['observation']
        assert client['request_sent_ms'] == 2
        assert client['http_status'] == (200 if kind == 'reply_before_owner_loss' else None)
    assert 'private fixture response' not in json.dumps(consumed) and token not in json.dumps(consumed)

    monkeypatch.setattr(native,'request_index',lambda _: {'domains':{'scope':{'assignments':[
        {'asgi_request_sha256':result['request_id_sha256'],'internal_sha256':'a'*64}],
        'model_observations':[]}},'stored_record_index_complete':True})
    sample = {'phase':case,'generation':'old','native':{'session':old.prepared['session'],'recordings':[],
                                                      'recorded_files_covered':True}}
    projected = native.project([sample],[consumed],strict_generation=True)['request_generation_bindings']
    assert len(projected) == (1 if kind == 'bound_reply' else 0)


def test_staged_chat_controls_use_strict_selection_and_keep_stream_path(monkeypatch):
    chat, stream = 'c'*64, 'd'*64
    chat_inner, stream_inner = 'e'*64, 'f'*64
    qualified = {'session_owner_match':True, 'listed_file_identity_match':True, 'capture':{
        'process_context_verified':True, 'provenance':{'owner_binding_verified':True,
            'capture_sha256':'7'*64,'file_identity':{}},
        'records':[{'payload':{'context':{'pid':9,'start_ticks':80}}},
            {'payload':{'source_sha256':'a'*64}}, {'payload':{'source_sha256':'a'*64}}]}}
    samples = [{'phase':'before','generation':'old',
                'native':{'session':'a'*32,'recordings':[qualified],'recorded_files_covered':True}}]
    trials = [{'case_id':case,'generation':'old','request_id_sha256':digest,'attempted':True,
               'transport_dispatched':True,'stock_generation_attribution_verified':True}
              for case,digest in [('before',chat),('inflight',stream)]]
    monkeypatch.setattr(native,'request_index',lambda _: {
        'domains':{'scope':{'assignments':[{'internal_sha256':chat_inner,'event':[0,1]},
            {'internal_sha256':stream_inner,'asgi_request_sha256':stream,'event':[0,1]}],'model_observations':[]}},
        'stored_record_index_complete':True})
    metadata = {'asgi_request_sha256':chat,'selected_request_id_sha256':chat_inner,'request_id_observed':True,
                'entry_path_sha256':sha256_hex(b'/v1/chat/completions'),'entry_method_sha256':sha256_hex(b'POST')}
    row = {'point':'InputProcessor.assign_request_id','phase':'RETURN','metadata':metadata,'event':[0,2]}
    monkeypatch.setattr(native,'selection_index',lambda _: {
        'stored_index_complete':True,'events':[row],'invocations':{'one':metadata}})
    measure = lambda: native.project(samples,trials,strict_generation=True,chat_cases=native.PHASES)
    links = {entry['request_id_sha256']:entry for entry in measure()['request_generation_bindings']}
    assert links[chat]['selection_source_receipt']['event'] == [0,2]
    assert links[chat]['source_receipt']['event'] == [0,1]
    assert links[stream]['internal_request_sha256'] == stream_inner
    row['event'] = [1,2]
    assert {entry['request_id_sha256'] for entry in measure()['request_generation_bindings']} == {stream}


def test_native_reader_counts_files_acks_and_prefix_attribution_separately():
    def recording(*, checkpoint, attributed):
        return {'listed_file_identity_match':True,'session_owner_match':True,
            'capture':{'checkpoint_prefix':{'sequence':1} if checkpoint else None,
                'records':[{'event':'CHECKPOINT' if checkpoint else 'START','payload':{'private':'none for export'}}],
                'provenance':{'owner_binding_verified':True}, 'process_context_verified':attributed,
                'diagnostic_codes':[] if attributed else ['LIFECYCLE_CHECKPOINT_STALE_OR_UNAVAILABLE',
                                                         'LIFECYCLE_PROCESS_BINDING_UNVERIFIED']}}

    inventory = {'metadata':{'files':[{'pid':1951}, {'pid':1952}, {'pid':1953}]}}
    packet = {'before':inventory,'after':inventory,'live_census_stable':True,'capture_mode':'live_checkpoint',
              'recordings':[recording(checkpoint=False,attributed=False), recording(checkpoint=True,attributed=False),
                            recording(checkpoint=True,attributed=True)]}
    counts = native._reader_coverage(packet)
    assert (counts['before_files_listed'],counts['after_files_listed'],counts['recordings_parsed']) == (3,3,3)
    assert (counts['live_checkpoint_ack_rows_observed'],counts['checkpoint_prefixes_observed'],
            counts['checkpoint_prefixes_attributed']) == (2,2,1)
    assert counts['recording_diagnostic_counts'] == {'LIFECYCLE_CHECKPOINT_STALE_OR_UNAVAILABLE':2,
                                                  'LIFECYCLE_PROCESS_BINDING_UNVERIFIED':2}
    assert 'pid' not in json.dumps(counts) and 'none for export' not in json.dumps(counts)
    packet.update(before=None,after=None,recordings=[{'capture':None,
        'diagnostic_codes':['LIFECYCLE_PROCESS_RECORDING_UNAVAILABLE']}])
    counts = native._reader_coverage(packet)
    assert counts['before_files_listed'] is None and counts['live_checkpoint_ack_rows_observed'] is None
    assert counts['recordings_parsed'] == 0 and counts['checkpoint_prefixes_attributed'] == 0
