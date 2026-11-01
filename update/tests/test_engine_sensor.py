"""Explicit EXAMPLE dispatch contracts; no native deployment or GPU measured."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.executors import registry, engine_sensor as sensor
from guard.models import DataOrigin, Environment
from tests.builders import baseline_pack, evidence
from tests.test_req18_slot_integration import cpu_record, mismatch, PRIVATE


def check(check_id='REQ-18', *, cpu=True):
    from guard.models.checkdef import ObsSpec
    value = baseline_pack().checks[check_id].model_copy(deep=True)
    value.executor = 'engine_sensor'
    value.collection_profile = sensor.CPU_PROFILE if cpu else sensor.PROFILES[check_id]
    value.required_observations = [ObsSpec(key=key, type=kind) for key, kind in (
        ('engine_sensor_violation', 'bool'), ('engine_sensor_full_scope', 'bool'),
        ('engine_sensor_gpu_execution', 'bool'), ('engine_sensor_rows', 'list'))]
    return value


def env():
    from tools import b_req18_slot
    return Environment(id='ENV-ENGINE-SENSOR-CPU-EXAMPLE', data_origin=DataOrigin.EXAMPLE,
        engine={'name': 'vllm', 'version': 'source-' + b_req18_slot.SOURCE_COMMIT}).seal()


def run(tmp_path, monkeypatch, raw, *, output_error=False):
    from guard.runtime import bounded
    calls = []
    def pipes(argv, **kw):
        calls.append((argv, kw))
        return {'returncode': 0, 'stdout_bytes': (sensor.CPU_PREFIX + json.dumps(raw)).encode(),
                'stderr_bytes': PRIVATE.encode(), 'capture': {'complete': True, 'bounded_at_source': True,
                    'truncated': False, 'timed_out': False, 'pipes_closed': True, 'read_errors': []}}
    monkeypatch.setattr(bounded, 'capture_pipes', pipes)
    if output_error:
        original = Path.open
        def fail(path, *args, **kwargs):
            if path.name == 'engine-sensor.json':
                raise OSError(PRIVATE)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', fail)
    factory = registry.resolve_executor('engine_sensor', 'source-cpu')
    instance = factory({'engine_sensor': {'source_root': str(tmp_path/'source'),
        'owned_parent': str(tmp_path/'owned'), 'component_root': str(tmp_path/'frozen')}})
    draft = instance.run(check(), env(), {}, None, tmp_path/'artifacts')
    return draft, calls


@pytest.mark.parametrize('check_id', list(sensor.PROFILES))
def test_no_unreviewed_native_capability_is_counted_or_wrapped_as_unknown(check_id):
    instance = registry.resolve_executor('engine_sensor', 'kubernetes')({})
    state = instance.support(check_id, sensor.PROFILES[check_id])
    assert not state['implemented'] and state['reason'] and state['scope'] == 'native'
    with pytest.raises(sensor.EngineSensorNotImplemented, match=state['reason']):
        instance.validate_policy(check(check_id, cpu=False), {})
    assert sensor.REQUIRED_BRANCHES[check_id]


def test_real_and_kubernetes_are_not_substituted_and_fake_is_not_a_support_count():
    assert registry.resolve_executor('engine_sensor', 'real') is None
    assert registry.resolve_executor('engine_sensor', 'kubernetes') is not None
    assert registry.resolve_executor('engine_sensor', 'source-cpu') is not None
    instance = registry.resolve_executor('engine_sensor', 'source-cpu')({})
    assert instance.support('REQ-18', sensor.CPU_PROFILE)['implemented']
    assert not instance.support('REQ-19', sensor.CPU_PROFILE)['implemented']
    assert not instance.support('REQ-18', sensor.PROFILES['REQ-18'])['implemented']


def test_source_cpu_calls_frozen_component_instead_of_weakening_old_snapshot_gate(tmp_path, monkeypatch):
    draft, calls = run(tmp_path, monkeypatch, cpu_record())
    assert len(calls) == 1 and calls[0][0][1:4] == ['-I', '-B', '-c']
    assert 'slot.verify_components(root)' in calls[0][0][4]
    assert draft.status.value == 'COMPLETED'
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert draft.config['scope'] == 'source_component' and draft.config['installed_target'] is None
    assert draft.observations['engine_sensor_violation'].value is False
    assert draft.observations['engine_sensor_full_scope'].value is False
    assert draft.observations['engine_sensor_gpu_execution'].value is False
    assert PRIVATE not in draft.artifacts[0].path.read_text()
    assert PRIVATE not in json.dumps(draft.model_dump(mode='json'))


@pytest.mark.parametrize('late', ['none', 'case_missing', 'source_postcheck', 'component_postcheck', 'artifact'])
def test_confirmed_source_invariant_failure_survives_independent_late_gaps(tmp_path, monkeypatch, late):
    raw = mismatch(cpu_record())
    if late == 'case_missing':
        raw['results'].pop(); raw['capture_status'] = 'incomplete'
    elif late == 'source_postcheck':
        raw['source_validation']['post']['status'] = 'unavailable'
    elif late == 'component_postcheck':
        raw['engine_sensor_component_postcheck'] = 'unavailable'; raw['capture_status'] = 'incomplete'
    draft, _ = run(tmp_path, monkeypatch, raw, output_error=late == 'artifact')
    assert draft.observations['engine_sensor_violation'].collected
    assert draft.observations['engine_sensor_violation'].value is True
    if late != 'none':
        assert draft.status.value == 'ABORTED'
    from guard.judges.rule_table import RuleTableJudge
    evd = evidence(status=draft.status, check_id='REQ-18', observations={
        key: value.model_dump() for key, value in draft.observations.items()})
    result = RuleTableJudge().judge(check(), evd, {'fail_when_any': [
        {'obs': 'engine_sensor_violation', 'op': 'eq', 'value': True}]}, {})
    assert result.verdicts['security'].value == 'FAIL'


@pytest.mark.parametrize('missing', ['sampler', 'same_logits', 'source', 'consumption'])
def test_missing_consumption_or_source_never_becomes_a_confirmed_violation(tmp_path, monkeypatch, missing):
    raw = mismatch(cpu_record())
    row = raw['results'][1]
    if missing == 'sampler': row['sampler_returned'] = False
    elif missing == 'same_logits': row['logits_sha256'] = 'f'*64
    elif missing == 'source': row['source_validation']['post']['status'] = 'mismatch'
    else: row['metadata_argument_matches_refresh'] = False
    draft, _ = run(tmp_path, monkeypatch, raw)
    assert draft.observations['engine_sensor_violation'].value is not True


def test_deployed_environment_cannot_consume_cpu_component_evidence(tmp_path, monkeypatch):
    from tests.test_kubernetes_request_binding import env as deployed
    instance = registry.resolve_executor('engine_sensor', 'source-cpu')({'engine_sensor': {
        'source_root': str(tmp_path), 'owned_parent': str(tmp_path/'owned'), 'component_root': str(tmp_path)}})
    with pytest.raises(ValueError, match='REQUIRES_SEPARATE_ENV'):
        instance.run(check(), deployed(), {}, None, tmp_path)


def test_existing_producer_default_error_record_is_not_fabricated_false(tmp_path, monkeypatch):
    from tools import b_req18_slot_cpu as producer
    from guard.executors import structured_tools_cpu
    monkeypatch.setattr(producer, 'verify_component', lambda: None)
    monkeypatch.setattr(structured_tools_cpu, '_verify_source', lambda *a, **kw: None)
    monkeypatch.setattr(structured_tools_cpu, '_validate_target031_dependencies', lambda: None)
    monkeypatch.setattr(producer, '_post', lambda *a: {'status': 'unavailable'})
    def unavailable(*args):
        raise RuntimeError(PRIVATE)
    monkeypatch.setattr(producer, '_run_actual', unavailable)
    raw = producer.capture_slot_cpu(tmp_path/'source', tmp_path/'capture', source_profile='target031')
    assert raw['results'] == [] and raw['capture_status'] == 'incomplete'
    observations, result = sensor.slot_observations(raw)
    assert observations['engine_sensor_violation'] == {'value': None, 'collected': False}
    assert result is None
    assert PRIVATE not in json.dumps(raw)


def test_profile_and_policy_cannot_redirect_source_cpu_to_a_different_check():
    instance = sensor.EngineSensorExecutor({}, implementation='source-cpu')
    with pytest.raises(sensor.EngineSensorNotImplemented):
        instance.validate_policy(check('REQ-19'), {})
    with pytest.raises(ValueError, match='POLICY_UNSUPPORTED'):
        instance.validate_policy(check(), {'sensor_target_sha256': 'a'*64})


def test_registered_factory_selection_evidence_result_report_restore_and_exact_rejudgment(tmp_path, monkeypatch):
    """The only injected boundary is an explicitly EXAMPLE child byte stream."""
    import hashlib
    import yaml
    from guard.core.select import select
    from guard.core.run import RunConfig, run_plan
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.models import Policy, load_pack, utcnow
    from guard.store import Store
    from guard.store.packs import put_pack, get_pack
    from guard.store.bundle import export_bundle, restore_bundle
    from guard.runtime import bounded
    unit = check()
    from guard.models.checkdef import JudgeSpec
    # Keep original question/criteria; this private source-only fixture is not PACK31.
    unit.judge = JudgeSpec(plugin='rule_table', params={'fail_when_any': [
        {'obs': 'engine_sensor_violation', 'op': 'eq', 'value': True}]})
    body = yaml.safe_dump(unit.model_dump(mode='json'), allow_unicode=True).encode('utf-8')
    pack_dir = tmp_path/'contract-pack'
    (pack_dir/'checks').mkdir(parents=True)
    (pack_dir/'checks/REQ-18.yaml').write_bytes(body)
    (pack_dir/'pack.yaml').write_text(yaml.safe_dump({'pack_id': 'PACK-OWNER2-SOURCE-CONTRACT', 'revision': 1,
        'engines': ['vllm'], 'checks': ['REQ-18'], 'review': {'status': 'DRAFT'},
        'files_sha256': [{'path': 'checks/REQ-18.yaml', 'sha256': hashlib.sha256(body).hexdigest()}]}), encoding='utf-8')
    pack = load_pack(pack_dir)
    environment = env()
    policy = Policy(id='POL-OWNER2-EXAMPLE', data_origin=DataOrigin.EXAMPLE, status='DRAFT',
        pack=pack.ref(), scope={'include': ['REQ-18']}).seal()
    store = Store(tmp_path/'store')
    put_pack(store, pack_dir)
    store.put_many([environment, policy])
    plan = select(pack, [environment], policy)
    store.put(plan)
    raw = mismatch(cpu_record())
    def response(*args, **kwargs):
        return {'returncode': 0, 'stdout_bytes': (sensor.CPU_PREFIX+json.dumps(raw)).encode(),
                'stderr_bytes': b'', 'capture': {'complete': True, 'bounded_at_source': True,
                    'truncated': False, 'timed_out': False, 'pipes_closed': True, 'read_errors': []}}
    monkeypatch.setattr(bounded, 'capture_pipes', response)
    draft, = run_plan(store, pack, plan, RunConfig(executors={'engine_sensor': 'source-cpu'}, raw={
        'engine_sensor': {'component_root': str(tmp_path/'frozen'), 'source_root': str(tmp_path/'source'),
                          'owned_parent': str(tmp_path/'owned')}}))
    assert draft.execution.executor.name == 'engine_sensor'
    assert draft.observations['engine_sensor_violation'].value is True
    now = utcnow()
    result, = judge_plan(store, pack, plan, as_of=now)
    report = build_report(store, pack, plan, as_of=now)
    store.put(report)
    assert result.verdicts.security.value == 'FAIL'
    export_bundle(store.root, tmp_path/'contract.zip')
    restore_bundle(tmp_path/'contract.zip', tmp_path/'restored')
    restored = Store(tmp_path/'restored')
    replay, = judge_plan(restored, get_pack(restored, plan.inputs.pack), restored.resolve(plan.ref()), as_of=now)
    assert replay.ref() == result.ref() and restored.verify() == []
    assert list(restored.iter_kind('RPT'))[0].ref() == report.ref()
