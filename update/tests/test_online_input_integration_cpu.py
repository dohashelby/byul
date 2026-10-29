"""EXAMPLE online CPU evidence contracts; live collectors run after joint freeze."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from guard.executors import online_input_cpu as product, registry
from guard.models import DataOrigin, Environment, ExecStatus
from tools import b1_input_integration_cpu as pipeline

CANARY = 'ONLINE_PRIVATE_EXCEPTION_DO_NOT_SAVE'
PRODUCER_HASH = 'a' * 64
COLLECTION_ID = 'online-cpu-' + 'b' * 32


def admission(meta, ids, *, matches_reuse=False):
    meta.update(admission_completed=True, prompt_count=len(ids), admitted_token_ids_canonical_sha256=product.digest(ids),
        processor_input_count=len(ids), processor_input_ids_sha256=product.digest(ids), processor_input_matches_reuse=matches_reuse)


def example_record(profile='reference5d'):
    now = datetime.now(timezone.utc)
    validation = {'pre': 'verified', 'post': {'status': 'verified'}, 'boundary': 'source_fixture_component'}
    record = {'source_profile': profile, 'source_commit': pipeline.SOURCE_PROFILES[profile],
        'collection_id': COLLECTION_ID, 'own_producer_sha256': PRODUCER_HASH, 'installed_target': None,
        'capture_started_at': now.isoformat(), 'capture_finished_at': (now + timedelta(milliseconds=1)).isoformat(),
        'capture_status': 'completed', 'source_validation': validation, 'auxiliary_write': {'status': 'completed'},
        'config_file_bytes_sha256': {'config.json': 'c' * 64, 'tokenizer.json': 'd' * 64},
        'fixture_config': {'origin': 'DECLARED', 'vocab_size': 32, 'max_model_len': 16, 'hidden_size': 4},
        'source_features': {'validate_token_ids': {'present': profile == 'reference5d'}},
        'results': [], 'exception': CANARY}
    for case in product.fixtures():
        fields = case['fields']
        meta = {'request_parsed': True, 'trust_request_chat_template': case['trust_template'],
            'trust_request_mm_kwargs': case['trust_mm'], 'effective_max_model_len': 16,
            'effective_vocab_size': 32, 'effective_max_token_id': 31,
            'parsed_fields': {key: {'present': True, 'fixture_matches': True, 'canonical_sha256': product.digest(value)}
                              for key, value in fields.items()}, 'private': CANARY}
        events = ['online_render_chat', 'template_gate', 'mm_kwargs_gate', 'online_preprocess_chat', 'reuse_input_gate']
        outcome = case['expected'] or 'accepted'
        if profile == 'target031' and case['id'] in pipeline._TARGET_EXCLUDED:
            outcome = 'rejected' if case['id'] == 'reuse-echo-ignores-ids' else 'accepted'
        if outcome == 'accepted':
            ids = [5, 3, 4]
            if case['id'] in ('reuse-valid', 'reuse-bool'):
                ids = fields['kv_transfer_params']['prompt_token_ids']
            elif case['id'] in ('template-trusted',):
                ids = [3, 4]
            elif case['id'] == 'kwargs-trusted-boundary':
                ids = [3] * 15
            admission(meta, ids, matches_reuse=case['id'] in ('reuse-valid', 'reuse-bool'))
            events += ['process_inputs', 'validate_prompt_len', 'validate_model_input']
        else:
            meta.update(rejection_basis='native_typed_error_response', native_error_code=400)
            if profile == 'target031' and case['id'] in ('reuse-negative', 'reuse-oov', 'reuse-long'):
                ids = fields['kv_transfer_params']['prompt_token_ids']
                meta.update(processor_input_matches_reuse=True, processor_input_count=len(ids), processor_input_ids_sha256=product.digest(ids))
                events += ['process_inputs', 'validate_prompt_len', 'validate_model_input']
        after_meta = {'request_parsed': True, 'effective_max_model_len': 16, 'effective_vocab_size': 32,
            'effective_max_token_id': 31, 'parsed_fields': {}}
        admission(after_meta, [5, 3, 4])
        excluded = profile == 'target031' and case['id'] in pipeline._TARGET_EXCLUDED
        record['results'].append({**product.describe(case), 'outcome': outcome, 'events': events,
            'safe_metadata': meta, 'source_profile': profile, 'source_commit': record['source_commit'],
            'collection_id': COLLECTION_ID, 'target_started': True, 'source_validation': deepcopy(validation),
            'source_scope_eligibility': {'eligible': not excluded,
                'reason': 'source_expectation_unavailable' if excluded else 'source_contract_observed'},
            'after': {'outcome': 'accepted', 'events': ['online_render_chat', 'process_inputs'], 'safe_metadata': after_meta}})
    return record


def row(raw, key):
    return next(value for value in raw['results'] if value['case_id'] == key)


def bad_admission(raw, key, *, trace=True):
    target = row(raw, key)
    target['outcome'] = 'accepted'
    if key.startswith('reuse-'):
        ids = pipeline._CASES[key]['fields']['kv_transfer_params']['prompt_token_ids']
    elif key in ('template-untrusted', 'template-nested-untrusted'):
        ids = [3, 4]
    elif key == 'kwargs-trusted-overbound':
        ids = [3] * 16
    else:
        ids = [5, 3, 4]
    admission(target['safe_metadata'], ids, matches_reuse=key.startswith('reuse-'))
    if not trace:
        target['events'] = []
    return target


def env(profile='reference5d', **changes):
    values = {'id': 'ENV-EXAMPLE-ONLINE', 'created_at': datetime.now(timezone.utc), 'data_origin': DataOrigin.EXAMPLE,
              'engine': {'name': 'vllm', 'version': 'reference-' + pipeline.SOURCE_PROFILES[profile]}}
    values.update(changes)
    return Environment(**values).seal()


@pytest.fixture
def private(tmp_path):
    environment = env()
    plan_ref = environment.ref().model_copy(update={'kind': 'PLAN', 'id': 'PLAN-EXAMPLE-ONLINE'})
    pack = pipeline.proposal_pack(tmp_path / 'pack', 'reference5d')
    def build(raw, profile='reference5d'):
        bound_env = environment if profile == 'reference5d' else env(profile)
        calls = []
        def capture(*args, **kwargs):
            calls.append(kwargs)
            return deepcopy(raw)
        shared = pipeline._SharedCapture(bound_env, plan_ref, tmp_path / 'source', profile,
            capture_fn=capture, producer_sha256=PRODUCER_HASH, component_check=lambda: None)
        return shared, bound_env, calls
    return tmp_path, environment, plan_ref, pack, build


def private_drafts(private, raw, profile='reference5d'):
    root, _, plan_ref, pack, build = private
    shared, environment, calls = build(raw, profile)
    drafts = {cid: shared.draft(pack.checks[cid], environment, {}, root / cid,
        plan_ref=plan_ref, source_profile=profile) for cid in pipeline.CHECKS}
    return shared, calls, drafts


def test_reference_fixtures_and_projection_remain_unchanged(private):
    raw = example_record()
    bad_admission(raw, 'reuse-empty')
    bad_admission(raw, 'reuse-bool')
    expected = product.project(raw['results'])
    shared, calls, drafts = private_drafts(private, raw)
    assert len(calls) == 1 and len(drafts) == 3
    for cid, draft in drafts.items():
        assert draft.observations['online_input_confirmed_violations'].value == expected[cid]['online_input_confirmed_violations']['value']
        assert draft.data_origin == DataOrigin.EXAMPLE
    assert shared.record['results'][1]['expected'] == 'rejected'


@pytest.mark.parametrize('case', sorted(pipeline._TARGET_EXCLUDED))
def test_target_unpromised_cases_keep_rows_and_unknown_scope_even_with_admission(private, case):
    raw = example_record('target031')
    bad_admission(raw, case)
    row(raw, case)['source_scope_eligibility'] = {'eligible': True, 'reason': 'source_contract_observed'}
    shared, _, drafts = private_drafts(private, raw, 'target031')
    assert drafts['REQ-08'].observations['online_input_confirmed_violations'].value == []
    saved = next(value for value in shared.record['results'] if value['case_id'] == case)
    assert saved['fixture_canonical_sha256'] == product.describe(pipeline._CASES[case])['fixture_canonical_sha256']
    assert saved['expected'] == pipeline._CASES[case]['expected']
    assert saved['producer_scope_eligibility'] == {'eligible': False, 'reason': 'source_expectation_unavailable'}
    assert saved['safe_metadata']['admission_completed'] is True


@pytest.mark.parametrize('case', ['reuse-negative', 'reuse-oov', 'reuse-long', 'kwargs-trusted-overbound',
                                  'template-untrusted', 'template-nested-untrusted'])
def test_strong_actual_bad_admission_survives_missing_specific_validator_trace(private, case):
    raw = example_record('target031')
    bad_admission(raw, case, trace=False)
    raw['capture_status'] = 'incomplete'
    shared, _, drafts = private_drafts(private, raw, 'target031')
    cid = pipeline._CASES[case]['check_id']
    assert shared.record['source_scope_qualification'][case]['eligible'] is True
    assert drafts[cid].observations['online_input_confirmed_violations'].value == [case]
    assert drafts[cid].status == ExecStatus.ABORTED


@pytest.mark.parametrize('fault', ['ignored_field', 'empty_trace_no_consumption', 'parsed_hash', 'inactive_config', 'mm_text_only'])
def test_unsupported_or_unconsumed_acceptance_is_not_source_filter_fail(private, fault):
    raw = example_record('target031')
    key = 'code-revision-default-deny' if fault == 'mm_text_only' else 'reuse-negative'
    value = bad_admission(raw, key, trace=False)
    if fault == 'ignored_field':
        admission(value['safe_metadata'], [5, 3, 4], matches_reuse=False)
    elif fault == 'empty_trace_no_consumption':
        value['safe_metadata'].pop('admission_completed')
    elif fault == 'parsed_hash':
        value['safe_metadata']['parsed_fields']['kv_transfer_params']['canonical_sha256'] = '0' * 64
    elif fault == 'inactive_config':
        value['safe_metadata']['trust_request_mm_kwargs'] = True
    shared, _, drafts = private_drafts(private, raw, 'target031')
    assert shared.record['source_scope_qualification'][key]['eligible'] is False
    assert drafts[pipeline._CASES[key]['check_id']].observations['online_input_confirmed_violations'].value == []


def test_status_400_and_empty_trace_alone_do_not_prove_consumption(private):
    raw = example_record('target031')
    value = row(raw, 'template-untrusted')
    value['events'] = []
    shared, _, _ = private_drafts(private, raw, 'target031')
    assert shared.record['source_scope_qualification']['template-untrusted']['eligible'] is False
    assert value['safe_metadata']['native_error_code'] == 400


@pytest.mark.parametrize('fault', ['child_mismatch', 'wrong_profile', 'wrong_collection', 'not_started', 'fixture_hash'])
def test_untrusted_events_are_retained_without_source_fail(private, fault):
    raw = example_record('target031')
    value = bad_admission(raw, 'reuse-negative')
    if fault == 'child_mismatch':
        value['source_validation']['post']['status'] = 'mismatch'
    elif fault == 'wrong_profile':
        value['source_profile'] = 'reference5d'
    elif fault == 'wrong_collection':
        value['collection_id'] = 'online-cpu-' + 'e' * 32
    elif fault == 'fixture_hash':
        value['fixture_canonical_sha256'] = '0' * 64
    else:
        value['target_started'] = False
    shared, _, drafts = private_drafts(private, raw, 'target031')
    assert drafts['REQ-08'].observations['online_input_confirmed_violations'].value == []
    saved = next(value for value in shared.record['results'] if value['case_id'] == 'reuse-negative')
    assert saved['producer_scope_eligibility']['eligible'] is False
    assert saved['safe_metadata']['admission_completed'] is True


@pytest.mark.parametrize('fault', ['after_failure', 'auxiliary', 'post_unavailable', 'later_case'])
def test_known_violations_survive_independent_collection_failures(private, fault):
    raw = example_record('target031')
    for case in ('reuse-negative', 'template-untrusted', 'code-revision-default-deny'):
        bad_admission(raw, case)
    raw['capture_status'] = 'incomplete'
    if fault == 'after_failure':
        row(raw, 'reuse-negative')['after'] = {'outcome': 'execution_error', 'exception': CANARY}
    elif fault == 'auxiliary':
        raw['auxiliary_write'] = {'status': 'failed', 'error_type': 'OSError', 'exception': CANARY, 'path': CANARY}
    elif fault == 'post_unavailable':
        row(raw, 'reuse-negative')['source_validation']['post'] = {'status': 'unavailable', 'error_type': 'OSError'}
    else:
        raw['results'][-1] = {**product.describe(product.fixtures()[-1]), 'outcome': 'execution_error',
            'target_started': False, 'preparation_status': 'failed', 'exception': CANARY}
    _, calls, drafts = private_drafts(private, raw, 'target031')
    assert len(calls) == 1
    for cid, draft in drafts.items():
        assert draft.status == ExecStatus.ABORTED
        assert draft.observations['online_input_confirmed_violations'].value
        assert CANARY not in draft.artifacts[0].path.read_text()


def test_each_check_owns_artifact_and_shares_actual_window(private, monkeypatch):
    raw = example_record()
    original = pipeline._write_artifact
    def write(path, record):
        if 'req-08' in path.name:
            raise OSError(CANARY)
        return original(path, record)
    monkeypatch.setattr(pipeline, '_write_artifact', write)
    _, calls, drafts = private_drafts(private, raw)
    assert len(calls) == 1 and drafts['REQ-08'].status == ExecStatus.ABORTED
    for cid in ('REQ-07', 'SUP-08'):
        assert drafts[cid].status == ExecStatus.COMPLETED and len(drafts[cid].artifacts) == 1
    assert len({draft.started_at for draft in drafts.values()}) == len({draft.finished_at for draft in drafts.values()}) == 1
    assert drafts['REQ-07'].started_at == datetime.fromisoformat(raw['capture_started_at'])
    assert drafts['REQ-07'].finished_at == datetime.fromisoformat(raw['capture_finished_at'])


@pytest.mark.parametrize('fault', ['plan', 'revision', 'origin', 'profile', 'engine', 'hash'])
def test_shared_capture_cannot_cross_boundaries(private, fault):
    root, environment, plan, pack, build = private
    shared, _, calls = build(example_record())
    shared.draft(pack.checks['REQ-07'], environment, {}, root / 'first', plan_ref=plan, source_profile='reference5d')
    candidate, candidate_plan, profile = environment, plan, 'reference5d'
    if fault == 'plan': candidate_plan = plan.model_copy(update={'id': 'PLAN-OTHER'})
    elif fault == 'revision': candidate = env(revision=environment.revision + 1)
    elif fault == 'origin': candidate = env(data_origin=DataOrigin.DECLARED)
    elif fault == 'profile': profile = 'target031'
    elif fault == 'engine': candidate = env(engine={'name': 'other', 'version': environment.engine.version})
    else: candidate = environment.model_copy(update={'content_sha256': '0' * 64})
    with pytest.raises(ValueError, match='ONLINE_INPUT_CPU_SHARED_ENVIRONMENT_OR_PLAN_MISMATCH'):
        shared.draft(pack.checks['REQ-08'], candidate, {}, root / 'second', plan_ref=candidate_plan, source_profile=profile)
    assert len(calls) == 1


@pytest.mark.parametrize('scenario', ['normal', 'violations', 'uncollected', 'artifact_failure'])
def test_example_core_refs_registry_privacy_and_single_capture(tmp_path, monkeypatch, scenario):
    raw = example_record('target031')
    if scenario in ('violations', 'artifact_failure'):
        for key in ('reuse-negative', 'template-untrusted', 'code-revision-default-deny'):
            bad_admission(raw, key)
    calls = []
    def capture(*args, **kwargs):
        calls.append(kwargs)
        if scenario == 'uncollected': raise RuntimeError(CANARY)
        return deepcopy(raw)
    monkeypatch.setattr(pipeline, 'verify_runtime_components', lambda repo: None)
    monkeypatch.setattr(pipeline, 'COLLECTOR_SHA256', PRODUCER_HASH)
    monkeypatch.setattr(pipeline.collector, 'capture_online', capture, raising=False)
    if scenario == 'artifact_failure':
        monkeypatch.setattr(pipeline, '_write_artifact', lambda *a: (_ for _ in ()).throw(OSError(CANARY)))
    before = dict(registry.EXECUTORS)
    output = tmp_path / 'EXAMPLE-output'
    summary = pipeline.run_pipeline(tmp_path / 'source', output, source_profile='target031', origin=DataOrigin.EXAMPLE)
    assert calls == [{'source_profile': 'target031'}] and summary['capture_invocations'] == 1
    assert len(summary['evidence']) == len(summary['results']) == 3 and registry.EXECUTORS == before
    assert summary['installed_target'] is None and summary['runner_consumption_verified'] is False
    assert summary['exact_ref_rejudgment_unchanged'] and summary['store_verify_errors'] == []
    for cid in pipeline.CHECKS:
        assert summary['checks'][cid]['data_origin'] == 'EXAMPLE'
        assert summary['checks'][cid]['verdicts']['security'] == ('FAIL' if scenario in ('violations', 'artifact_failure') else 'UNKNOWN')
        assert summary['checks'][cid]['verdicts']['functional'] == 'UNKNOWN'
    report = json.loads((output / 'RPT.json').read_text())
    assert report['official'] is False and all(ref in report['refs'] for ref in summary['evidence'] + summary['results'])
    for path in output.rglob('*'):
        if path.is_file(): assert CANARY not in path.read_text(errors='replace')


def _function(path, name):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'def {name}(*args): return None\n')
    namespace = {}
    exec(compile(path.read_text(), str(path), 'exec'), namespace)
    return namespace[name]


@pytest.fixture
def gate(tmp_path, monkeypatch):
    gate_path, col_path = tmp_path / 'update/tools/lif14_profiler_cpu.py', tmp_path / 'update/tools/b1_input_cpu.py'
    fn, capture = _function(gate_path, 'verify_component'), _function(col_path, 'capture_online')
    monkeypatch.setattr(pipeline, 'component_gate', SimpleNamespace(__file__=str(gate_path), verify_component=fn,
        PRODUCT_COMPONENT_COMMIT=pipeline.COMPONENT_COMMIT))
    monkeypatch.setattr(pipeline, 'collector', SimpleNamespace(__file__=str(col_path), capture_online=capture))
    monkeypatch.setattr(pipeline, 'GATE_SHA256', hashlib.sha256(gate_path.read_bytes()).hexdigest())
    monkeypatch.setattr(pipeline, 'COLLECTOR_SHA256', hashlib.sha256(col_path.read_bytes()).hexdigest())
    return tmp_path, gate_path, col_path


def test_runtime_gate_reuses_fixed_component(gate):
    pipeline.verify_runtime_components(gate[0])


@pytest.mark.parametrize('fault', ['gate', 'collector', 'foreign_function', 'unconfigured'])
def test_runtime_gate_rejects_unapproved_tools(gate, monkeypatch, fault):
    root, gate_path, col_path = gate
    if fault == 'gate': gate_path.write_text('changed\n')
    elif fault == 'collector': col_path.write_text('changed\n')
    elif fault == 'unconfigured': monkeypatch.setattr(pipeline, 'COLLECTOR_SHA256', None)
    else: monkeypatch.setattr(pipeline.collector, 'capture_online', _function(root / 'foreign.py', 'capture_online'))
    with pytest.raises(ValueError, match='ONLINE_INPUT_CPU_'): pipeline.verify_runtime_components(root)


@pytest.mark.parametrize('flag', ['-O', '-OO'])
def test_optimized_parent_rejects_before_source(tmp_path, flag):
    import os
    script = Path(pipeline.__file__).resolve()
    output = tmp_path / 'must-not-exist'
    process = subprocess.run([sys.executable, flag, str(script), '--source-root', str(tmp_path / 'missing'),
        '--output-root', str(output)], capture_output=True, text=True,
        env={**os.environ, 'PYTHONPATH': str(script.parents[1]) + ':' + str(script.parents[1] / 'src')})
    assert process.returncode != 0 and 'ONLINE_INPUT_CPU_REQUIRES_NORMAL_PYTHON312' in process.stderr
    assert not output.exists()
