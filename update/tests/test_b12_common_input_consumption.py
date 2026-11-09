"""Committed A producer -> existing B consumers. CPU contracts/EXAMPLE I/O only."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.collectors import gpu_common_inputs as contract, gpu_input_producers as producers
from guard.collectors import ipc_ownership_producer as ipc, group_resource_producer as resources
from guard.collectors import process_device_producer as device
from guard.core.run import RunConfig, _normalize, run_plan
from guard.core.select import select
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.executors import registry, lab_activation
from guard.executors.base import EvidenceDraft
from guard.executors.lifecycle_common_input_consumption import consume_record, attach_inputs, merge_activation_inputs
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, load_pack, utcnow
from guard.store import Store
from guard.store.packs import put_pack, get_pack
from guard.store.bundle import export_bundle, restore_bundle
from tests.builders import environment, policy
from tests.test_gpu_input_producers import POD, R1, lifecycle, row, trow, sched, hrow, prow
from tests.test_ipc_ownership import observed
from tests.test_group_resources import before, restarted, trigger_for
from tests.test_process_device_provider import node, OPENED, CANDIDATES

ROOT = Path(__file__).resolve().parents[1]


def make_record(kind, scope, pod=POD):
    """Execute the real committed A transform; do not patch producer registration."""
    if kind == 'request-attempt':
        return producers.request_attempt_record([{'pid': 74, 'start_ticks': 1000, 'events': lifecycle(preempt=True)}], pod=pod, **scope)[0]
    if kind == 'generation-transition':
        events = [sched(1_000_000, R1), trow(2_000_000, 'EngineCore.collective_rpc', 'CALL', rpc_transition=True,
                    in_flight_request_sha256=[R1]), trow(3_000_000, 'EngineCore.collective_rpc', 'RETURN', rpc_transition=True),
                  sched(4_000_000, R1)]
        return producers.generation_transition_record([{'pid': 74, 'start_ticks': 1000, 'events': events}], pod=pod, **scope)[0]
    if kind == 'health-worker-causal':
        processes = [{'pid': 90, 'start_ticks': 5, 'events': [hrow(1_000_000, 'CALL', engine_dead=False, output_handler_running=True)]},
                     {'pid': 74, 'start_ticks': 1000, 'events': lifecycle()}]
        return producers.health_worker_record(processes, pod=pod, **scope)[0]
    if kind == 'profiler-control':
        processes = [{'pid': 1, 'start_ticks': 1, 'events': [prow(1_000_000, 'AsyncLLM.start_profile', 'CALL', is_start=True)]},
                     {'pid': 74, 'start_ticks': 2, 'events': [prow(2_000_000, 'EngineCore.profile', 'RETURN', is_start=True)]},
                     {'pid': 111, 'start_ticks': 3, 'events': [prow(3_000_000, 'Worker.profile', 'RETURN', is_start=True, rank=0),
                       prow(4_000_000, 'TorchProfilerWrapper._stop', 'RETURN', is_start=False,
                            output_files=[{'name_sha256': 'f'*64, 'bytes': 10}])]}]
        return producers.profiler_control_record(processes, pod=pod, **scope)[0]
    if kind == 'process-device-provider':
        return device.process_device_record(node(), OPENED, CANDIDATES, pod=pod, container='engine', **scope)[0]
    if kind == 'ipc-ownership':
        return ipc.record(observed(), pod=pod, **scope)[0]
    if kind == 'failed-group-resources':
        first = before()
        return resources.record(first, restarted(), pod_before=pod, pod_after={**pod, 'container_id': 'containerd://'+'c'*64,
            'restart_count': pod['restart_count']+1}, trigger=trigger_for(first),
            normal_after={'at_ns': first['realtime_ns']+40*10**9, 'ok': True}, **scope)[0]
    raise AssertionError(kind)


def inputs(folder, kind='request-attempt', env=None, target=None, prepared=None, pod=POD):
    folder.mkdir(parents=True, exist_ok=True)
    env = env or environment(engine={'name': 'vllm', 'version': '0.31.0'})
    target = target or {'lab': True, 'owned': 'EXAMPLE'}
    prepared = prepared or {'session': 'd'*32, 'profile': 'EXAMPLE-CPU-CONTRACT'}
    scope = {'environment_ref': env.ref().model_dump(mode='json'), 'target_sha256': sha256_hex(canonical_json(target)),
             'prepared_sha256': sha256_hex(canonical_json(prepared)), 'session': prepared['session']}
    record = make_record(kind, scope, pod)
    (folder/'prepared.json').write_bytes(canonical_json(prepared))
    (folder/'record.json').write_bytes(canonical_json(record))
    descriptor = {'prepared': str(folder/'prepared.json'), 'session': scope['session'], 'records': {kind: str(folder/'record.json')}}
    return env, target, prepared, record, scope, descriptor


PAIRS = [(check, kind) for kind in ('request-attempt', 'generation-transition', 'health-worker-causal',
    'profiler-control', 'process-device-provider', 'ipc-ownership', 'failed-group-resources') for check in contract.KINDS[kind]['checks']]


@pytest.mark.parametrize('check_id,kind', PAIRS)
def test_real_producer_to_domain_consumer_keeps_support_candidates_and_limits(tmp_path, check_id, kind):
    env, target, prepared, record, scope, _ = inputs(tmp_path, kind)
    result = consume_record(check_id, record, owner=POD, **scope)
    producer = record['producer']
    supported = contract.SUPPORTED_PRODUCERS.get((producer['name'], producer['version'])) == producer['source_sha256']
    assert result['validation']['supported'] is supported
    assert not result['producer_authenticated'] and not result['whole_question_complete']
    assert result['gaps'] and result['facts']['event_counts']
    if kind in {'ipc-ownership', 'failed-group-resources'}:
        assert not supported and 'GPU_INPUT_PRODUCER_UNSUPPORTED' in result['gaps']
    if kind == 'generation-transition':
        assert result['violation_candidates'] and not result['facts']['actual_weights_cache_consumption_verified']
    if kind == 'health-worker-causal':
        assert result['facts']['shared_health_worker_causal_keys'] == 0
    if kind == 'ipc-ownership':
        assert result['facts']['connections']['engine_core->worker'] == 2
        assert result['facts']['unresolved_peers'] == 0
    if kind == 'failed-group-resources':
        assert result['facts']['released_upper_bound_ms'] is not None
        assert not result['facts']['gpu_memory_release_observed'] and result['violation_candidates']


@pytest.mark.parametrize('key,value', [('environment_ref', {'kind': 'ENV', 'id': 'ENV-OTHER', 'revision': 1, 'sha256': 'a'*64}),
    ('target_sha256', 'a'*64), ('prepared_sha256', 'b'*64), ('session', 'e'*32)])
def test_external_expected_scope_not_record_self_binding(tmp_path, key, value):
    *_, record, scope, descriptor = inputs(tmp_path)
    with pytest.raises(ValueError):
        consume_record('LIF-10', record, owner=POD, **{**scope, key: value})


def draft(origin=DataOrigin.EXAMPLE):
    return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=origin, normal_control={'attempted': True, 'succeeded': True},
        observations={'activation_steps': {'value': [{'step': 'original'}], 'collected': True},
            'activation_violations': {'value': [{'code': 'ORIGINAL_INDEPENDENT_FAIL'}], 'collected': True},
            'activation_missing': {'value': ['ORIGINAL_GAP'], 'collected': True}})


@pytest.mark.parametrize('mode', ['normal', 'source', 'missing', 'replaced', 'session', 'prepared', 'artifact', 'malformed'])
def test_normalize_retains_original_fail_and_new_gap_even_after_followup_error(tmp_path, mode):
    env, target, prepared, record, scope, descriptor = inputs(tmp_path/'input')
    if mode == 'source':
        record['producer']['source_sha256'] = '0'*64
        Path(descriptor['records']['request-attempt']).write_bytes(canonical_json(record))
    if mode == 'missing':
        Path(descriptor['records']['request-attempt']).unlink()
    if mode == 'replaced':
        record['bindings'][0]['restart_count'] += 1
        Path(descriptor['records']['request-attempt']).write_bytes(canonical_json(record))
    if mode == 'session':descriptor['session'] = 'e'*32
    if mode == 'prepared':prepared = {**prepared, 'profile': 'OTHER'}
    if mode == 'malformed':Path(descriptor['records']['request-attempt']).write_text('[]')
    work = tmp_path/'out'; work.mkdir()
    if mode == 'artifact':(work/'b-common-inputs.json').write_bytes(b'preserve')
    check = load_pack(ROOT/'packs/PACK-BASELINE').checks['LIF-10']
    value = attach_inputs(draft(), check, env, target, descriptor, work, prepared=prepared, owner=POD)
    normalized = _normalize(check, value)['observations']
    assert normalized['activation_violations']['value'] == [{'code': 'ORIGINAL_INDEPENDENT_FAIL'}]
    assert normalized['activation_steps']['value'][-1]['schema'] == 'guard-b-lifecycle-common-inputs/1'
    assert normalized['activation_scope_complete']['value'] is False
    assert 'ORIGINAL_GAP' in normalized['activation_missing']['value']
    assert value.status == ExecStatus.ABORTED and value.data_origin == DataOrigin.EXAMPLE
    if mode == 'source':assert 'GPU_INPUT_PRODUCER_UNSUPPORTED' in normalized['activation_missing']['value']
    if mode == 'artifact':
        assert (work/'b-common-inputs.json').read_bytes() == b'preserve'
        assert 'B_COMMON_INPUT_ARTIFACT_UNAVAILABLE' in value.cleanup_errors


@pytest.mark.parametrize('origin', ['EXAMPLE', 'DECLARED', 'MEASURED'])
def test_file_input_cannot_upgrade_to_measured(tmp_path, origin):
    env, target, prepared, _, _, descriptor = inputs(tmp_path/'input', env=environment(origin=DataOrigin.MEASURED))
    descriptor['data_origin'] = origin
    check = load_pack(ROOT/'packs/PACK-BASELINE').checks['LIF-10']
    value = attach_inputs(draft(DataOrigin.MEASURED), check, env, target, descriptor, tmp_path/'out', prepared=prepared, owner=POD)
    assert value.data_origin == (DataOrigin.DECLARED if origin == 'DECLARED' else DataOrigin.EXAMPLE)
    if origin == 'MEASURED':assert not value.observations['activation_steps'].value[-1]['records']


def test_req12_not_declared_and_existing_closed_dispatch_preserved(tmp_path):
    _, _, _, record, scope, descriptor = inputs(tmp_path)
    with pytest.raises(ValueError, match='B_COMMON_INPUT_CHECK_UNDECLARED:REQ-12'):
        consume_record('REQ-12', record, owner=POD, **scope)
    from guard.executors.kubernetes_request_attempts import KubernetesRequestAttemptsExecutor, PROFILE
    executor = registry.build_executor('request_termination', 'kubernetes', {'request_termination': {'common_inputs': descriptor}})
    child = executor._consumer(SimpleNamespace(id='REQ-12', collection_profile=PROFILE))
    assert isinstance(child, KubernetesRequestAttemptsExecutor) and child.b_common_inputs == descriptor
    with pytest.raises(ValueError, match='TERMINATION_PROFILE_UNSUPPORTED'):
        executor._consumer(SimpleNamespace(id='REQ-12', collection_profile='wrong'))


@pytest.mark.parametrize('name,impl', [('stream_buffer','kubernetes'), ('request_termination','kubernetes'), ('initialization','kubernetes'),
    ('observation_state','kubernetes'), ('profiler_observation','kubernetes'), ('lifecycle_transition','kubernetes'),
    ('container_config','kubernetes'), ('credential_exposure','kubernetes'), ('unix_ipc','kubernetes'),
    ('resource_recovery','kubernetes'), ('log_analysis','real')])
def test_existing_factories_preserve_optional_input_and_native_cfg(tmp_path, name, impl):
    *_, descriptor = inputs(tmp_path)
    executor = registry.build_executor(name, impl, {name: {'common_inputs': descriptor}})
    assert executor.name == name and executor.b_common_inputs == descriptor and 'common_inputs' not in executor.cfg


@pytest.mark.parametrize('primary_artifact_error', [False, True, 'mkdir'])
def test_activation_preserves_both_independent_fails_and_example_ceiling(tmp_path, monkeypatch, primary_artifact_error):
    env, target, prepared, _, _, descriptor = inputs(tmp_path/'input')
    check = load_pack(ROOT/'packs/PACK-BASELINE').checks['LIF-10']
    passive = attach_inputs(draft(), check, env, target, descriptor, tmp_path/'passive', prepared=prepared, owner=POD)
    active = {'activation_steps': [{'step':'active'}], 'activation_violations':[{'code':'ACTIVE_INDEPENDENT_FAIL'}],
              'activation_missing':[], 'activation_scope_complete':True}
    lab = SimpleNamespace(initialization_case=None, fault_evidence=[], normal=[True], cleanup_errors=[], diagnostic_write_errors=[], write_diagnostics=lambda _: [])
    monkeypatch.setattr(lab_activation, 'run_scenario', lambda *a, **kw: (deepcopy(active), lab))
    if primary_artifact_error is True:
        original = Path.write_bytes
        def write(path, data):
            if path.name == 'lab-activation.json':raise OSError('EXAMPLE-primary-artifact-error')
            return original(path,data)
        monkeypatch.setattr(Path,'write_bytes',write)
    elif primary_artifact_error == 'mkdir':
        original = Path.mkdir
        def mkdir(path,*a,**kw):
            if path == tmp_path/'active':raise OSError('EXAMPLE-primary-directory-error')
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'mkdir',mkdir)
    child = SimpleNamespace(run=lambda *a, **kw: passive)
    executor = lab_activation.ActivatedExecutor(child, {}, runner=None)
    result = executor.run(check, env, {}, None, tmp_path/'active')
    assert result.data_origin == DataOrigin.EXAMPLE
    assert [v['code'] for v in result.observations['activation_violations'].value] == ['ACTIVE_INDEPENDENT_FAIL','ORIGINAL_INDEPENDENT_FAIL']
    assert not result.observations['activation_scope_complete'].value
    assert result.observations['activation_steps'].value[-1]['records']
    assert merge_activation_inputs(active, None) is active
    if primary_artifact_error:
        assert result.status == ExecStatus.ABORTED
        assert result.abort_reason == 'LAB_ACTIVATION_COMMON_INPUT_ARTIFACT_UNAVAILABLE'
        assert result.artifacts == passive.artifacts


@pytest.mark.parametrize('primary_stage', ['write','mkdir'])
def test_without_common_inputs_primary_artifact_error_keeps_original_exception_boundary(tmp_path, monkeypatch, primary_stage):
    active={'activation_steps':[], 'activation_violations':[{'code':'ACTIVE_INDEPENDENT_FAIL'}],
            'activation_missing':[], 'activation_scope_complete':False}
    monkeypatch.setattr(lab_activation,'run_scenario',lambda *a,**kw:(active,None))
    original=Path.write_bytes
    def write(path,data):
        if path.name=='lab-activation.json':raise OSError('EXAMPLE-primary-artifact-error')
        return original(path,data)
    monkeypatch.setattr(Path,'write_bytes',write)
    if primary_stage == 'mkdir':
        original_mkdir=Path.mkdir
        def mkdir(path,*a,**kw):
            if path == tmp_path:raise OSError('EXAMPLE-primary-artifact-error')
            return original_mkdir(path,*a,**kw)
        monkeypatch.setattr(Path,'mkdir',mkdir)
    check=load_pack(ROOT/'packs/PACK-BASELINE').checks['LIF-10']
    with pytest.raises(OSError, match='EXAMPLE-primary-artifact-error'):
        lab_activation.ActivatedExecutor(SimpleNamespace(run=lambda *a:draft()),{}).run(check,None,{},None,tmp_path)


def pipeline(folder, pack, env, check_id, values, cfg, *, activation=None):
    pol = policy(pack.ref(), scope={'include':[check_id]}, values={check_id:values})
    store = Store(folder/'store'); put_pack(store, pack.path); store.put_many([env,pol])
    plan = select(pack,[env],pol); store.put(plan)
    check = pack.checks[check_id]
    assert next(item for item in plan.items if item.check_id == check_id).selected
    evd, = run_plan(store,pack,plan,RunConfig(executors={check.executor:'kubernetes'},adapter='vllm',
        raw={check.executor:cfg, **({'lab_activation':activation} if activation is not None else {})}))
    now = utcnow(); result, = judge_plan(store,pack,plan,as_of=now)
    report = build_report(store,pack,plan,as_of=now); store.put(report)
    export_bundle(store.root,folder/'bundle.zip'); restore_bundle(folder/'bundle.zip',folder/'restored')
    restored = Store(folder/'restored')
    replay, = judge_plan(restored,get_pack(restored,plan.inputs.pack),restored.resolve(plan.ref()),as_of=now)
    assert replay.ref() == result.ref() and restored.verify() == []
    assert next(restored.iter_kind('RPT')).ref() == report.ref()
    assert evd.data_origin == DataOrigin.EXAMPLE
    (folder/'exact-refs.json').write_bytes(canonical_json({'PLAN':plan.ref().model_dump(mode='json'),
        'EVD':evd.ref().model_dump(mode='json'),'RES':result.ref().model_dump(mode='json'),'RPT':report.ref().model_dump(mode='json')}))
    return store, evd, result


@pytest.mark.parametrize('mode', ['normal','violation','cleanup'])
def test_plt02_actual_producer_summary_to_original_factory_judge_report_and_restored_pack(tmp_path, monkeypatch, mode):
    from tests.test_a1_kubernetes_ipc_product import inputs as ipc_inputs
    from tests.test_kubernetes_unix_ipc_consumer import VALUES
    from guard.runtime import kubernetes_http as http
    env, target, world, pack, cfg = ipc_inputs(tmp_path, mode)
    Path(cfg['target']).write_bytes(canonical_json(target))
    _, _, _, _, _, descriptor = inputs(tmp_path/'common','ipc-ownership',env,target,pod=target['kubernetes']['engine'])
    cfg['common_inputs'] = descriptor
    monkeypatch.setattr(http,'capture_pipes',world.runner)
    store, evd, result = pipeline(tmp_path/'product',pack,env,'PLT-02',deepcopy(VALUES),cfg)
    artifact = next(a for a in evd.artifacts if a.name=='b-common-inputs.json')
    facts = json.loads((store.root/artifact.path).read_bytes())
    assert facts['records'][0]['facts']['connections']['engine_core->worker'] == 2
    assert 'GPU_INPUT_PRODUCER_UNSUPPORTED' in facts['records'][0]['gaps']
    assert evd.observations['ipc_scope_complete'].value is False
    assert result.verdicts.security.value == ('UNKNOWN' if mode=='normal' else 'FAIL')
    if mode=='cleanup':assert evd.execution.cleanup_errors


# Existing native fixtures exercise the real fixed methods with EXAMPLE OS I/O.
from tests.test_kubernetes_lifecycle import group, target as native_target  # noqa: E402,F401
from tests.test_lifecycle_capture import case, external, revised  # noqa: E402,F401
from tests.test_lifecycle_sensor import writer_factory  # noqa: E402,F401


def test_lif10_actual_producer_to_current_pack_normalized_evidence_and_rejudge(tmp_path, group, native_target, monkeypatch):
    from tests.test_a1_kubernetes_request_termination_product import inputs as native_inputs
    from guard.runtime import kubernetes_http as http
    world, values, _, _ = native_inputs(tmp_path,group,native_target,'normal')
    target = tmp_path/'target.json'; target.write_bytes(canonical_json(world.target))
    *_, descriptor = inputs(tmp_path/'common',env=group.env,target=world.target,prepared=group.prepared,pod=world.target['kubernetes']['engine'])
    monkeypatch.setattr(http,'capture_pipes',world.runner)
    monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    _, evd, result = pipeline(tmp_path/'product',load_pack(ROOT/'packs/PACK-BASELINE'),group.env,'LIF-10',values,
        {'target':str(target),'prepared':str(group.manifest_path),'common_inputs':descriptor})
    report = evd.observations['activation_steps'].value[-1]
    assert report['records'][0]['kind']=='request-attempt' and report['records'][0]['validation']['supported']
    assert report['native_observations']['termination_attempts']['collected']
    assert result.verdicts.security.value=='UNKNOWN' and not evd.observations['activation_scope_complete'].value


@pytest.mark.parametrize('primary_stage', ['write','mkdir'])
def test_primary_artifact_error_keeps_original_judge_fail_remaining_artifacts_report_and_restored_refs(tmp_path, group, native_target, monkeypatch, primary_stage):
    from tests.test_a1_kubernetes_request_termination_product import inputs as native_inputs
    from guard.runtime import kubernetes_http as http
    world, values, _, _ = native_inputs(tmp_path,group,native_target,'normal')
    target=tmp_path/'target.json';target.write_bytes(canonical_json(world.target))
    *_, descriptor=inputs(tmp_path/'common',env=group.env,target=world.target,prepared=group.prepared,
                            pod=world.target['kubernetes']['engine'])
    monkeypatch.setattr(http,'capture_pipes',world.runner)
    monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    active={'activation_steps':[{'step':'EXAMPLE-active'}], 'activation_violations':[{'code':'EXAMPLE_CONFIRMED_ACTIVE_FAIL'}],
            'activation_missing':[], 'activation_scope_complete':False}
    lab=SimpleNamespace(initialization_case=None, fault_evidence=[], normal=[True], cleanup_errors=[], diagnostic_write_errors=[], write_diagnostics=lambda _:[])
    monkeypatch.setattr(lab_activation,'run_scenario',lambda *a,**kw:(deepcopy(active),lab))
    original=Path.write_bytes
    def write(path,data):
        if path.name=='lab-activation.json':raise OSError('EXAMPLE-primary-artifact-error')
        return original(path,data)
    if primary_stage == 'write':
        monkeypatch.setattr(Path,'write_bytes',write)
    else:
        original_mkdir=Path.mkdir
        def mkdir(path,*a,**kw):
            # Let the passive child create its own work/artifacts first. Fail
            # the primary collector's subsequent preparation of that parent.
            if path.name=='single' and path.parent.name=='LIF-10' and path.is_dir():
                raise OSError('EXAMPLE-primary-directory-error')
            return original_mkdir(path,*a,**kw)
        monkeypatch.setattr(Path,'mkdir',mkdir)
    store, evd, result=pipeline(tmp_path/'product',load_pack(ROOT/'packs/PACK-BASELINE'),group.env,'LIF-10',values,
        {'target':str(target),'prepared':str(group.manifest_path),'common_inputs':descriptor},activation={'namespace':'EXAMPLE'})
    assert evd.execution.status == ExecStatus.ABORTED
    assert result.verdicts.security.value == 'FAIL'
    assert evd.observations['activation_violations'].value == [{'code':'EXAMPLE_CONFIRMED_ACTIVE_FAIL'}]
    names={a.name for a in evd.artifacts}
    assert {'b-common-inputs.json','probe_log.jsonl'} <= names and 'lab-activation.json' not in names
    assert 'LAB_ACTIVATION_COMMON_INPUT_ARTIFACT_UNAVAILABLE' in evd.execution.cleanup_errors


@pytest.mark.parametrize('mode', ['released-descriptors','retained','retained-http-failure'])
def test_lif11_lineage_keeps_original_selected_fd_deadline_fail_and_axis(tmp_path, group, native_target, monkeypatch, mode):
    from tests.test_a1_resource_recovery_product import inputs as native_inputs
    from guard.runtime import kubernetes_http as http
    world, selected, values, pack = native_inputs(tmp_path,group,native_target,mode,'runtime-failure')
    target = tmp_path/'target.json'; target.write_bytes(canonical_json(world.target))
    selection = tmp_path/'selection.json'; selection.write_bytes(canonical_json(selected))
    *_, descriptor = inputs(tmp_path/'common','failed-group-resources',group.env,world.target,group.prepared,
                            world.target['kubernetes']['engine'])
    monkeypatch.setattr(http,'capture_pipes',world.runner)
    monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    store, evd, result = pipeline(tmp_path/'product',pack,group.env,'LIF-11',values,
        {'target':str(target),'prepared':str(group.manifest_path),'selection':str(selection),'common_inputs':descriptor})
    assert result.verdicts.functional.value == ('UNKNOWN' if mode=='released-descriptors' else 'FAIL')
    assert result.verdicts.security.value == 'UNKNOWN'
    assert evd.observations['recovery_absolute_deadline_violated'].value is (mode!='released-descriptors')
    assert not evd.observations['recovery_global_resources_released'].value
    artifact = next(a for a in evd.artifacts if a.name=='b-common-inputs.json')
    report = json.loads((store.root/artifact.path).read_bytes())
    assert not report['records'][0]['facts']['gpu_memory_release_observed']
    assert report['records'][0]['facts']['next_generation']
    assert 'GPU_INPUT_PRODUCER_UNSUPPORTED' in report['records'][0]['gaps']


def test_current_plt02_profile_mismatch_is_not_hidden_by_optional_input(tmp_path, monkeypatch):
    from tests.test_a1_kubernetes_ipc_product import inputs as ipc_inputs
    from tests.test_kubernetes_unix_ipc_consumer import VALUES
    from guard.runtime import kubernetes_http as http
    env, target, world, _, cfg = ipc_inputs(tmp_path,'normal')
    Path(cfg['target']).write_bytes(canonical_json(target))
    *_, descriptor = inputs(tmp_path/'common','ipc-ownership',env,target,pod=target['kubernetes']['engine'])
    cfg['common_inputs'] = descriptor
    def forbidden(*a,**kw):raise AssertionError('profile mismatch must refuse before OS I/O')
    monkeypatch.setattr(http,'capture_pipes',forbidden)
    _, evd, result = pipeline(tmp_path/'product',load_pack(ROOT/'packs/PACK-BASELINE'),env,'PLT-02',deepcopy(VALUES),cfg)
    assert evd.execution.status == ExecStatus.ABORTED and evd.execution.abort_reason == 'EXECUTOR_FAILURE'
    assert not evd.artifacts and result.verdicts.security.value=='UNKNOWN'


def test_profiler_original_dispatch_consumes_own_session_record(tmp_path, external, monkeypatch):
    from tests.test_kubernetes_profiler_control import inputs as native_inputs
    from guard.executors import kubernetes_profiler_control as control
    from guard.executors import kubernetes_profiler_observation as observation
    env, world, cfg, values = native_inputs(tmp_path, external, monkeypatch)
    prepared = json.loads(Path(cfg['prepared']).read_bytes())
    *_, descriptor = inputs(tmp_path/'common','profiler-control',env,world.target,prepared,
                            world.target['kubernetes']['engine'])
    cfg['common_inputs'] = descriptor
    class Reader:
        def __init__(self,*a,**kw):self.binding={'engine':world.target['kubernetes']['engine']}
        def snapshot(self):return {}
        def seal_session(self):return {'sealed':True}
        def report(self):return {}
    monkeypatch.setattr(control,'KubernetesLifecycleReader',Reader)
    monkeypatch.setattr(control.KubernetesProfilerControlExecutor,'_capture',lambda *a:world.packet())
    check=load_pack(ROOT/'packs/PACK-BASELINE').checks['LIF-14']
    executor=observation.KubernetesProfilerObservationExecutor(cfg,runner=world.runner)
    value=executor.run(check,env,values,None,tmp_path/'work')
    report=value.observations['activation_steps'].value[-1]
    assert report['records'][0]['kind']=='profiler-control'
    assert not report['records'][0]['facts']['cross_process_causality_verified']
    assert report['native_observations']['profiler_control_trials']['collected']
    assert not value.observations['activation_scope_complete'].value


@pytest.mark.parametrize('selected_key', ['old','missing'])
def test_transition_requires_explicit_selected_generation_not_time_proximity(tmp_path, monkeypatch, selected_key):
    from tests.test_lifecycle_transition import harness, policy as transition_policy
    from guard.executors import lifecycle_transition as transition
    executor, env, _ = harness(monkeypatch)
    generations, _ = transition.load_generations({}, env, {})
    for generation in generations.values():generation.native_target={'kubernetes':{'engine':POD}}
    selected=generations['old']
    *_, descriptor=inputs(tmp_path/'common','generation-transition',env,selected.target,selected.prepared)
    descriptor['generation']=selected_key;executor.b_common_inputs=descriptor
    check=load_pack(ROOT/'packs/PACK-BASELINE').checks['LIF-05']
    value=executor.run(check,env,transition_policy(),None,tmp_path/'work')
    report=value.observations['activation_steps'].value[-1]
    assert bool(report['records']) is (selected_key=='old')
    assert not value.observations['activation_scope_complete'].value


def test_activation_common_input_preserves_initialization_and_fault_artifacts(tmp_path, monkeypatch):
    env, target, prepared, _, _, descriptor = inputs(tmp_path/'input')
    check = load_pack(ROOT/'packs/PACK-BASELINE').checks['LIF-10']
    passive = attach_inputs(draft(), check, env, target, descriptor, tmp_path/'passive', prepared=prepared, owner=POD)
    active = {'activation_steps': [], 'activation_violations': [{'code': 'ACTIVE_INDEPENDENT_FAIL'}],
              'activation_missing': [], 'activation_scope_complete': True}
    primary = {'schema': 'EXAMPLE-initialization-primary'}
    fault = {'schema': 'EXAMPLE-fault-receipt'}
    lab = SimpleNamespace(normal=[True], cleanup_errors=[], diagnostic_write_errors=[],
        initialization_case=primary, fault_evidence=[{'name': 'fault-receipt.json', 'document': fault}],
        write_diagnostics=lambda _: [])
    monkeypatch.setattr(lab_activation, 'run_scenario', lambda *a, **kw: (deepcopy(active), lab))
    result = lab_activation.ActivatedExecutor(SimpleNamespace(run=lambda *a, **kw: passive), {}).run(
        check, env, {}, None, tmp_path/'active')
    artifacts = {item.name: item.path for item in result.artifacts}
    assert artifacts['initialization-primary-case.json'].read_bytes() == canonical_json(primary)
    assert artifacts['fault-receipt.json'].read_bytes() == canonical_json(fault)
    assert [v['code'] for v in result.observations['activation_violations'].value] == [
        'ACTIVE_INDEPENDENT_FAIL', 'ORIGINAL_INDEPENDENT_FAIL']
    assert result.data_origin == DataOrigin.EXAMPLE
    assert not result.observations['activation_scope_complete'].value


@pytest.mark.parametrize('failed_name,gap', [
    ('initialization-primary-case.json', 'LAB_ACTIVATION_INITIALIZATION_ARTIFACT_UNAVAILABLE'),
    ('fault-receipt.json', 'LAB_ACTIVATION_FAULT_ARTIFACT_UNAVAILABLE')])
@pytest.mark.parametrize('common_input', [False, True])
def test_activation_supplemental_artifact_failure_preserves_independent_fails(tmp_path, monkeypatch, failed_name, gap, common_input):
    env, target, prepared, _, _, descriptor = inputs(tmp_path/'input')
    check = load_pack(ROOT/'packs/PACK-BASELINE').checks['LIF-10']
    passive = draft()
    if common_input:
        passive = attach_inputs(passive, check, env, target, descriptor, tmp_path/'passive', prepared=prepared, owner=POD)
    active = {'activation_steps': [], 'activation_violations': [{'code': 'ACTIVE_INDEPENDENT_FAIL'}],
              'activation_missing': [], 'activation_scope_complete': True}
    lab = SimpleNamespace(normal=[True], cleanup_errors=[], diagnostic_write_errors=[],
        initialization_case={'schema': 'EXAMPLE-initialization'},
        fault_evidence=[{'name': 'fault-receipt.json', 'document': {'schema': 'EXAMPLE-fault'}}],
        write_diagnostics=lambda _: [])
    monkeypatch.setattr(lab_activation, 'run_scenario', lambda *a, **kw: (deepcopy(active), lab))
    original = Path.write_bytes
    def write(path, data):
        if path.name == failed_name:
            raise OSError('EXAMPLE-private-error-must-not-enter-evidence')
        return original(path, data)
    monkeypatch.setattr(Path, 'write_bytes', write)
    result = lab_activation.ActivatedExecutor(SimpleNamespace(run=lambda *a, **kw: passive), {}).run(
        check, env, {}, None, tmp_path/'active')
    assert result.status == ExecStatus.ABORTED
    assert result.abort_reason == gap and gap in result.cleanup_errors
    assert not result.observations['activation_scope_complete'].value
    assert gap in result.observations['activation_missing'].value
    codes = [v['code'] for v in result.observations['activation_violations'].value]
    assert 'ACTIVE_INDEPENDENT_FAIL' in codes
    if common_input:
        assert 'ORIGINAL_INDEPENDENT_FAIL' in codes
        assert result.data_origin != DataOrigin.MEASURED
    names = {item.name for item in result.artifacts}
    assert failed_name not in names
    assert 'lab-activation.json' in names
    primary_artifact = next(item for item in result.artifacts if item.name == 'lab-activation.json')
    assert gap in json.loads(primary_artifact.path.read_bytes())['activation_missing']
    assert ({'initialization-primary-case.json', 'fault-receipt.json'} - {failed_name}) <= names
    assert all(item in result.artifacts for item in passive.artifacts)
    assert 'EXAMPLE-private-error' not in repr(result)
