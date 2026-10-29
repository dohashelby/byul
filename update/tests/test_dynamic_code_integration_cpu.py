"""EXAMPLE contracts for one live-capture boundary shared by SUP-09 and SUP-11."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from guard.executors import dynamic_code_cpu as product, registry
from guard.models import DataOrigin, Environment, ExecStatus
from tools import b1_dynamic_code_integration_cpu as pipeline

CANARY = 'DYNAMIC_PRIVATE_EXCEPTION_DO_NOT_SAVE'
PRODUCER_HASH = 'a' * 64
COLLECTION_ID = 'dynamic-cpu-' + 'b' * 32


def example_record(profile='reference5d'):
    start = datetime.now(timezone.utc)
    raw = {'source_profile': profile, 'source_commit': pipeline.SOURCE_PROFILES[profile],
        'own_producer_sha256': PRODUCER_HASH, 'capture_status': 'completed', 'installed_target': None,
        'collection_id': COLLECTION_ID, 'capture_started_at': start.isoformat(),
        'capture_finished_at': (start + timedelta(milliseconds=1)).isoformat(),
        'source_validation': {'pre': 'verified', 'post': {'status': 'verified'}, 'boundary': 'source_fixture_component'},
        'auxiliary_write': {'status': 'completed'},
        'fixture_file_bytes_sha256': {name: 'c' * 64 for name in pipeline._FIXTURE_FILES},
        'observations': {'raw_unused': CANARY}, 'exception': CANARY, 'rows': []}
    for case in product.fixtures():
        raw['rows'].append({**case, 'case_identity': product.identity(case),
            'outcome': 'accepted' if case['trust'] else 'rejected_at_trust_gate',
            'events': [{'phase': 'import'}, {'phase': 'constructor'}] if case['trust'] else [],
            'event_collection_status': 'completed', 'selected_fixture_source_matches': case['trust'],
            'before': {'outcome': 'accepted', 'raw': CANARY}, 'after': {'outcome': 'accepted'},
            'source_profile': profile, 'source_commit': raw['source_commit'], 'collection_id': raw['collection_id'],
            'source_validation': deepcopy(raw['source_validation']), 'target_started': True,
            'python_optimize': pipeline._MODES[case['mode']], 'exception': CANARY,
            'source_bytes_sha256': {CANARY: 'd' * 64}})
    return raw


def row(raw, case='autoconfig-False-O'):
    return next(item for item in raw['rows'] if item['case_id'] == case)


def add_violation(raw, case='autoconfig-False-O'):
    row(raw, case).update(outcome='accepted', events=[{'phase': 'import'}, {'phase': 'constructor'}])
    return raw


def environment(profile='reference5d', **changes):
    values = {'id': 'ENV-EXAMPLE-DYNAMIC-CPU', 'created_at': datetime.now(timezone.utc),
        'data_origin': DataOrigin.EXAMPLE, 'engine': {'name': 'vllm', 'version': 'reference-' + pipeline.SOURCE_PROFILES[profile]}}
    values.update(changes)
    return Environment(**values).seal()


@pytest.fixture
def private(tmp_path):
    profile = 'reference5d'
    env = environment(profile)
    pack = pipeline.proposal_pack(tmp_path / 'pack', profile)
    plan_ref = env.ref().model_copy(update={'kind': 'PLAN', 'id': 'PLAN-EXAMPLE-DYNAMIC'})
    def build(raw, *, capture_error=False):
        calls = []
        def capture(source_root, workdir, *, source_profile):
            calls.append((source_root, workdir, source_profile))
            if capture_error:
                raise RuntimeError(CANARY)
            return deepcopy(raw)
        shared = pipeline._SharedCapture(env, plan_ref, tmp_path / 'source', profile,
            capture_fn=capture, producer_sha256=PRODUCER_HASH, component_check=lambda: None)
        return shared, calls
    return tmp_path, env, plan_ref, pack, build


def drafts(private, raw, monkeypatch=None, capture_error=False):
    root, env, plan_ref, pack, build = private
    shared, calls = build(raw, capture_error=capture_error)
    values = [shared.draft(pack.checks[cid], env, {}, root / cid, plan_ref=plan_ref, source_profile='reference5d')
              for cid in pipeline.CHECKS]
    return shared, calls, values


def test_two_drafts_share_one_capture_id_and_actual_window(private):
    raw = example_record()
    shared, calls, values = drafts(private, raw)
    assert len(calls) == 1 and shared.collection_id == raw['collection_id']
    for draft in values:
        assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
        assert draft.started_at == datetime.fromisoformat(raw['capture_started_at'])
        assert draft.finished_at == datetime.fromisoformat(raw['capture_finished_at'])
        assert draft.config['collection_id'] == raw['collection_id']
        assert draft.config['installed_target'] is None and draft.config['runner_hook_verified'] is False
        assert len(draft.artifacts) == 1 and CANARY not in draft.artifacts[0].path.read_text()
    assert values[0].artifacts[0].path != values[1].artifacts[0].path


def test_confirmed_mode_violation_survives_baseline_control_and_followup_failure(private):
    raw = add_violation(example_record())
    row(raw, 'autoconfig-False-normal')['before'] = {'outcome': 'execution_error', 'error': CANARY}
    row(raw)['after'] = {'outcome': 'execution_error'}
    raw['capture_status'] = 'incomplete'
    shared, calls, values = drafts(private, raw)
    assert len(calls) == 1 and shared.baseline['autoconfig'] is True
    for draft, key in zip(values, ('dynamic_code_confirmed_violations', 'dynamic_mode_confirmed_violations')):
        assert draft.status == ExecStatus.ABORTED and draft.normal_control.succeeded is False
        assert draft.observations[key].value == ['autoconfig-False-O']


@pytest.mark.parametrize('fault', ['mismatch', 'missing', 'pre_unavailable', 'wrong_mode', 'wrong_collection', 'wrong_profile'])
def test_unqualified_optimized_events_do_not_become_source_specific_fail(private, fault):
    raw = add_violation(example_record())
    bad = row(raw)
    if fault == 'mismatch':
        bad['source_validation']['post']['status'] = 'mismatch'
    elif fault == 'missing':
        bad.pop('source_validation')
    elif fault == 'pre_unavailable':
        bad['source_validation']['pre'] = 'unavailable'
    elif fault == 'wrong_mode':
        bad['python_optimize'] = 2
    elif fault == 'wrong_collection':
        bad['collection_id'] = 'dynamic-cpu-' + 'e' * 32
    else:
        bad['source_profile'] = 'target031'
    raw['capture_status'] = 'incomplete'
    _, _, values = drafts(private, raw)
    assert values[0].observations['dynamic_code_confirmed_violations'].value == []
    assert values[1].observations['dynamic_mode_confirmed_violations'].value == []
    saved = json.loads(values[0].artifacts[0].path.read_text())['rows']
    assert next(item for item in saved if item['case_id'] == bad['case_id'])['events'] == bad['events']


def test_unqualified_normal_baseline_is_unknown_for_sup11_but_preserves_sup09(private):
    raw = add_violation(example_record())
    row(raw, 'autoconfig-False-normal')['source_validation']['post']['status'] = 'mismatch'
    raw['capture_status'] = 'incomplete'
    shared, _, values = drafts(private, raw)
    assert shared.baseline['autoconfig'] is False
    assert values[0].observations['dynamic_code_confirmed_violations'].value == ['autoconfig-False-O']
    assert values[1].observations['dynamic_mode_confirmed_violations'].value == []


@pytest.mark.parametrize('fault', ['child_unavailable', 'parent_unavailable', 'auxiliary_write', 'other_child_mismatch'])
def test_qualified_independent_violations_survive_other_failures(private, fault):
    raw = add_violation(example_record())
    raw['capture_status'] = 'incomplete'
    if fault == 'child_unavailable':
        row(raw)['source_validation']['post'] = {'status': 'unavailable', 'error_type': 'OSError'}
    elif fault == 'parent_unavailable':
        raw['source_validation']['post'] = {'status': 'unavailable', 'error_type': 'OSError'}
    elif fault == 'auxiliary_write':
        raw['auxiliary_write'] = {'status': 'failed', 'error_type': 'PermissionError', 'path': CANARY, 'exception': CANARY}
    else:
        row(raw, 'vllm-config-True-OO')['source_validation']['post']['status'] = 'mismatch'
    _, calls, values = drafts(private, raw)
    assert len(calls) == 1
    for draft, key in zip(values, ('dynamic_code_confirmed_violations', 'dynamic_mode_confirmed_violations')):
        assert draft.status == ExecStatus.ABORTED
        assert draft.observations[key].value == ['autoconfig-False-O']
        assert CANARY not in draft.artifacts[0].path.read_text()


def test_each_check_artifact_failure_does_not_clear_shared_facts(private, monkeypatch):
    raw = add_violation(example_record())
    monkeypatch.setattr(pipeline, '_write_artifact', lambda *a: (_ for _ in ()).throw(OSError(CANARY)))
    _, calls, values = drafts(private, raw)
    assert len(calls) == 1
    for draft in values:
        assert draft.status == ExecStatus.ABORTED and draft.abort_reason == 'ARTIFACT_CAPTURE_INCOMPLETE'
        assert not draft.artifacts and CANARY not in draft.model_dump_json()
    assert values[0].observations['dynamic_code_confirmed_violations'].value == ['autoconfig-False-O']
    assert values[1].observations['dynamic_mode_confirmed_violations'].value == ['autoconfig-False-O']


@pytest.mark.parametrize('changed', ['new_plan', 'new_generation', 'new_origin', 'wrong_engine', 'wrong_profile', 'invalid_hash'])
def test_shared_capture_cannot_cross_environment_plan_or_source_boundaries(private, changed):
    root, env, plan_ref, pack, build = private
    shared, calls = build(example_record())
    shared.draft(pack.checks['SUP-09'], env, {}, root / 'first', plan_ref=plan_ref, source_profile='reference5d')
    candidate, plan, profile = env, plan_ref, 'reference5d'
    if changed == 'new_plan':
        plan = plan_ref.model_copy(update={'id': 'PLAN-OTHER'})
    elif changed == 'new_generation':
        candidate = environment(revision=env.revision + 1)
    elif changed == 'new_origin':
        candidate = environment(data_origin=DataOrigin.DECLARED)
    elif changed == 'wrong_engine':
        candidate = environment(engine={'name': 'other', 'version': env.engine.version})
    elif changed == 'wrong_profile':
        profile = 'target031'
    else:
        candidate = env.model_copy(update={'content_sha256': '0' * 64})
    with pytest.raises(ValueError, match='DYNAMIC_CPU_SHARED_ENVIRONMENT_OR_PLAN_MISMATCH'):
        shared.draft(pack.checks['SUP-11'], candidate, {}, root / 'second', plan_ref=plan, source_profile=profile)
    assert len(calls) == 1


@pytest.mark.parametrize('scenario,security,execution', [
    ('normal', ('UNKNOWN', 'UNKNOWN'), 'COMPLETED'), ('violation', ('FAIL', 'FAIL'), 'COMPLETED'),
    ('sup09_only', ('FAIL', 'UNKNOWN'), 'COMPLETED'), ('missing', ('UNKNOWN', 'UNKNOWN'), 'ABORTED'),
    ('unsupported', ('UNKNOWN', 'UNKNOWN'), 'ABORTED'), ('auxiliary', ('FAIL', 'FAIL'), 'ABORTED'),
    ('attachment', ('FAIL', 'FAIL'), 'ABORTED'),
])
def test_example_pipeline_core_consumes_once_and_preserves_refs_privacy_and_rules(tmp_path, monkeypatch, scenario, security, execution):
    raw = example_record('target031')
    if scenario in ('violation', 'auxiliary', 'attachment'):
        add_violation(raw)
    if scenario == 'sup09_only':
        add_violation(raw, 'autoconfig-False-normal')
    if scenario == 'auxiliary':
        raw.update(capture_status='incomplete', auxiliary_write={'status': 'failed', 'error_type': 'OSError', 'exception': CANARY})
    if scenario == 'unsupported':
        raw['capture_status'] = 'incomplete'
        for value in raw['rows']:
            value.update(target_started=False, outcome='unsupported')
    calls = []
    def capture(*args, **kwargs):
        calls.append(kwargs)
        if scenario == 'missing':
            raise RuntimeError(CANARY)
        return deepcopy(raw)
    monkeypatch.setattr(pipeline, 'verify_runtime_components', lambda repo: None)
    monkeypatch.setattr(pipeline, 'COLLECTOR_SHA256', PRODUCER_HASH)
    monkeypatch.setattr(pipeline.collector, 'capture', capture, raising=False)
    if scenario == 'attachment':
        monkeypatch.setattr(pipeline, '_write_artifact', lambda *a: (_ for _ in ()).throw(OSError(CANARY)))
    original_registry = dict(registry.EXECUTORS)
    output = tmp_path / 'EXAMPLE-pipeline'
    summary = pipeline.run_pipeline(tmp_path / 'source', output, source_profile='target031', origin=DataOrigin.EXAMPLE)
    assert calls == [{'source_profile': 'target031'}] and summary['capture_invocations'] == 1
    assert registry.EXECUTORS == original_registry
    assert len(summary['evidence']) == len(summary['results']) == 2
    assert summary['exact_ref_rejudgment_unchanged'] and summary['store_verify_errors'] == []
    assert summary['installed_target'] is None and summary['runner_hook_verified'] is False
    for cid, expected in zip(pipeline.CHECKS, security):
        result = summary['checks'][cid]
        assert result['data_origin'] == 'EXAMPLE' and result['execution_status'] == execution
        assert result['verdicts']['security'] == expected and result['verdicts']['functional'] == 'UNKNOWN'
    if scenario not in ('missing',):
        evidences = [json.loads((output / ('EVD-' + cid + '.json')).read_text()) for cid in pipeline.CHECKS]
        assert evidences[0]['execution']['started_at'] == evidences[1]['execution']['started_at']
        assert evidences[0]['execution']['finished_at'] == evidences[1]['execution']['finished_at']
        assert datetime.fromisoformat(evidences[0]['execution']['started_at'].replace('Z', '+00:00')) == datetime.fromisoformat(raw['capture_started_at'])
        assert datetime.fromisoformat(evidences[0]['execution']['finished_at'].replace('Z', '+00:00')) == datetime.fromisoformat(raw['capture_finished_at'])
    report = json.loads((output / 'RPT.json').read_text())
    assert report['official'] is False
    assert all(reference in report['refs'] for reference in summary['evidence'] + summary['results'])
    for path in output.rglob('*'):
        if path.is_file():
            assert CANARY not in path.read_text(errors='replace')


def test_new_pipeline_has_a_new_in_memory_collection(tmp_path, monkeypatch):
    calls = []
    def capture(*args, **kwargs):
        raw = example_record()
        cid = 'dynamic-cpu-' + uuid.uuid4().hex
        raw['collection_id'] = cid
        for value in raw['rows']:
            value['collection_id'] = cid
        calls.append(cid)
        return raw
    monkeypatch.setattr(pipeline, 'verify_runtime_components', lambda repo: None)
    monkeypatch.setattr(pipeline, 'COLLECTOR_SHA256', PRODUCER_HASH)
    monkeypatch.setattr(pipeline.collector, 'capture', capture, raising=False)
    summaries = [pipeline.run_pipeline(tmp_path / 'source', tmp_path / str(index), origin=DataOrigin.EXAMPLE) for index in range(2)]
    assert len(calls) == 2 and calls[0] != calls[1]
    assert [summary['collection_id'] for summary in summaries] == calls


def _function(path, name):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'def {name}(*args): return None\n')
    namespace = {}
    exec(compile(path.read_text(), str(path), 'exec'), namespace)
    return namespace[name]


@pytest.fixture
def gate(tmp_path, monkeypatch):
    gate_path = tmp_path / 'update/tools/lif14_profiler_cpu.py'
    col_path = tmp_path / 'update/tools/b1_dynamic_code_cpu.py'
    check, capture = _function(gate_path, 'verify_component'), _function(col_path, 'capture')
    monkeypatch.setattr(pipeline, 'component_gate', SimpleNamespace(__file__=str(gate_path), verify_component=check,
        PRODUCT_COMPONENT_COMMIT=pipeline.COMPONENT_COMMIT))
    monkeypatch.setattr(pipeline, 'collector', SimpleNamespace(__file__=str(col_path), capture=capture))
    monkeypatch.setattr(pipeline, 'GATE_SHA256', hashlib.sha256(gate_path.read_bytes()).hexdigest())
    monkeypatch.setattr(pipeline, 'COLLECTOR_SHA256', hashlib.sha256(col_path.read_bytes()).hexdigest())
    return tmp_path, gate_path, col_path


def test_runtime_gate_accepts_only_fixed_own_tools(gate):
    pipeline.verify_runtime_components(gate[0])


@pytest.mark.parametrize('fault', ['gate_bytes', 'collector_bytes', 'foreign_gate_function', 'foreign_collector_function', 'unconfigured'])
def test_runtime_gate_rejects_foreign_or_unapproved_helpers(gate, monkeypatch, fault):
    root, gate_path, col_path = gate
    if fault == 'gate_bytes':
        gate_path.write_text('changed\n')
    elif fault == 'collector_bytes':
        col_path.write_text('changed\n')
    elif fault == 'unconfigured':
        monkeypatch.setattr(pipeline, 'COLLECTOR_SHA256', None)
    else:
        foreign = _function(root / 'foreign.py', 'foreign')
        monkeypatch.setattr(pipeline.component_gate if fault == 'foreign_gate_function' else pipeline.collector,
            'verify_component' if fault == 'foreign_gate_function' else 'capture', foreign)
    with pytest.raises(ValueError, match='DYNAMIC_CPU_'):
        pipeline.verify_runtime_components(root)


@pytest.mark.parametrize('flag', ['-O', '-OO'])
def test_optimized_parent_rejects_before_missing_source(tmp_path, flag):
    script = Path(pipeline.__file__).resolve()
    import os
    env = {**os.environ, 'PYTHONPATH': str(script.parents[1]) + ':' + str(script.parents[1] / 'src')}
    output = tmp_path / 'must-not-exist'
    result = subprocess.run([sys.executable, flag, str(script), '--source-root', str(tmp_path / 'missing'),
        '--output-root', str(output)], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and 'DYNAMIC_CPU_REQUIRES_NORMAL_PYTHON312' in result.stderr
    assert not output.exists()



def test_component_helper_scope_and_unsupported_policy_are_preserved(private):
    assert pipeline.component_gate.HELPER_SHA256 == '858666f5595fa6328a2975ef5a91eb9db19ad0712d11be08f64d8296cadaf6bf'
    assert pipeline.component_gate.PRODUCT_COMPONENT_COMMIT == pipeline.COMPONENT_COMMIT
    assert pipeline.component_gate.PINNED_PATHS == ('update/src/guard', 'update/packs', 'update/uv.lock',
        'update/pyproject.toml', 'update/reference/framework')
    root, env, plan_ref, pack, build = private
    shared, calls = build(example_record())
    with pytest.raises(ValueError, match='DYNAMIC_CPU_CHECK_OR_POLICY_UNSUPPORTED'):
        shared.draft(pack.checks['SUP-09'], env, {'unapproved_limit': 1}, root / 'work',
            plan_ref=plan_ref, source_profile='reference5d')
    assert not calls



@pytest.mark.parametrize('fault', ['profile', 'source_commit', 'producer_hash', 'precheck', 'collection_id'])
def test_unattributed_record_is_cached_as_uncollected_for_both_checks(private, fault):
    raw = add_violation(example_record())
    if fault == 'profile':
        raw['source_profile'] = 'target031'
    elif fault == 'source_commit':
        raw['source_commit'] = pipeline.SOURCE_PROFILES['target031']
    elif fault == 'producer_hash':
        raw['own_producer_sha256'] = '0' * 64
    elif fault == 'precheck':
        raw['source_validation']['pre'] = 'unavailable'
    else:
        raw['collection_id'] = CANARY
    shared, calls, values = drafts(private, raw)
    assert len(calls) == 1 and shared.collection_id is None
    for draft in values:
        assert draft.status == ExecStatus.ABORTED and not draft.observations and not draft.artifacts
        assert CANARY not in draft.model_dump_json()
