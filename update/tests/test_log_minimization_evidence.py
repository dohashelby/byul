"""원 OBS-03 manual 제품 연결의 EXAMPLE 회귀. 실제 Kubernetes/API 실행 0회."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest
import yaml
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors import log_minimization_evidence as bridge
from guard.core.select import select
from guard.models import DataOrigin
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import baseline_pack, policy, PACK_DIR
from tests.test_kubernetes_request_binding import env as example_env, target as example_target

PRIVATE = 'OBS03_EXAMPLE_PRIVATE_LOG_CANARY_RID_CREDENTIAL_DO_NOT_PERSIST'


@pytest.fixture
def case(tmp_path, monkeypatch):
    pack, env = baseline_pack(), example_env()
    values = {'allowed_log_fields': ['timestamp', 'level', 'message', 'request_id'],
        'forbidden_log_fields': ['request.prompt'],
        'forbidden_marker_contexts': ['caller_input', 'private_system', 'error_input'],
        'required_sources': ['EXAMPLE-pod-stdout', 'EXAMPLE-collector-copy', 'EXAMPLE-backup-copy'],
        'required_branches': ['normal', 'error', 'exception_dump']}
    pol = policy(pack.ref(), values={'OBS-03': values}, scope={'include': ['OBS-03'], 'exclude': []})
    source = Store(tmp_path / 'source')
    source.put_many([env, pol]); put_pack(source, PACK_DIR)
    plan = select(pack, [env], pol); source.put(plan)
    target = example_target(env)
    target_path = tmp_path / 'target.json'; target_path.write_text(json.dumps(target))
    capture = {'schema': 'guard-log-minimization-capture/1', 'profile': bridge.PROFILE,
        'environment_ref': env.ref().model_dump(mode='json'), 'target_sha256': bridge._digest(target),
        'policy_ref': pol.ref().model_dump(mode='json'), 'policy_values_sha256': bridge._digest(values),
        'source_id': 'EXAMPLE-pod-stdout', 'source_kind': 'kubernetes_pod_logs',
        'collection_id': 'EXAMPLE-obs03-1', 'started_at': '2026-10-30T03:00:00+00:00',
        'finished_at': '2026-10-30T03:00:01+00:00', 'data_origin': 'EXAMPLE',
        'request_summaries': [{'case_id': key, 'attempted': True, 'binding_verified': True,
                               'outcome': 400 if key == 'primary' else 200} for key in ('before', 'primary', 'after')],
        'normal_control': {'attempted': True, 'succeeded': True}, 'normal_control_scope': 'tokenize_api_shape_only',
        'model_inference_verified': False, 'coverage_complete': False,
        'analysis_binding_verified': True, 'source_generation_verified': True,
        'field_violation_count': 1, 'forbidden_error_input_count': 0, 'violation_codes': ['FIELD_POLICY_VIOLATION'],
        'marker_contexts_seen': [], 'branches_tested': ['normal', 'error'], 'exception_dump_verified': None,
        'source_reads': [{'attempted': True, 'binding_verified': True, 'complete': True,
                          'returncode': 0, 'byte_count': 99, 'event_count': 2, 'trial_linked_event_count': 2}],
        'cleanup': {'complete': True, 'collection_gap': True}, 'gaps': ['OBS03_OTHER_COPIES_UNCOLLECTED']}
    calls = []
    def example_capture(environment, target_value, policy_values, **kwargs):
        assert environment.data_origin == DataOrigin.EXAMPLE
        assert policy_values == values and kwargs['expected_policy_ref'] == pol.ref()
        calls.append((environment.ref(), kwargs))
        return deepcopy(capture)
    monkeypatch.setattr(bridge, '_capture', example_capture)
    args = dict(store=source.root, plan_id=plan.id, pack=PACK_DIR, target=target_path,
                source_id='EXAMPLE-pod-stdout', output=tmp_path / 'output', entered_by='EXAMPLE-operator')
    return dict(pack=pack, env=env, pol=pol, plan=plan, values=values, target=target,
                source=source, capture=capture, calls=calls, args=args)


def latest_evidence(case):
    return next(Store.read_only(case['args']['output'] / 'store').iter_kind('EVD'))


def test_preflight_does_not_import_collector_or_make_output(case):
    result = bridge.run(**case['args'])
    assert result['preflight'] and result['collector_calls'] == 0 and not case['calls']
    assert result['pack'] == bridge.PACK_REF and not case['args']['output'].exists()
    assert 'guard.collectors.kubernetes_log_minimization' not in sys.modules
    assert 'torch' not in sys.modules and 'vllm' not in sys.modules


def test_original_manual_definition_and_scoped_declared_fail_roundtrip(case):
    before = bridge._inventory(case['source'].root)
    result = bridge.run(**case['args'], execute=True)
    assert result['security'] == 'FAIL' and result['exact_refs_preserved'] and len(case['calls']) == 1
    assert bridge._inventory(case['source'].root) == before
    assert result['policy'] == case['pol'].ref().model_dump(mode='json')
    evidence = latest_evidence(case)
    assert evidence.data_origin == DataOrigin.DECLARED and evidence.plan == case['plan'].ref()
    assert evidence.environment == case['env'].ref() and evidence.execution.override_reason == bridge.OVERRIDE
    assert not evidence.normal_control.attempted and evidence.normal_control.succeeded is None
    assert all(not row.attempted for row in evidence.challenges)
    assert evidence.observations['declared_verdicts'].value == {'security': 'FAIL', 'functional': 'UNKNOWN', 'performance': 'UNKNOWN'}
    for key in ('log_fields_seen', 'functional_probes_planned', 'functional_probe_results'):
        assert evidence.observations[key].value is None and not evidence.observations[key].collected
    assert evidence.observations['log_coverage_complete'].value is False
    assert evidence.observations['log_source_ids_tested'].value == ['EXAMPLE-pod-stdout']
    assert evidence.observations['log_required_branches_tested'].value == ['error', 'normal']
    doc = yaml.safe_load((case['args']['output'] / 'manual-evidence.yaml').read_text())
    (entry,) = doc['checks']; original = case['pack'].checks['OBS-03']
    assert entry['question'] == original.question and original.revision == 5
    assert [item['step'] for item in entry['challenges']] == original.challenge_steps
    assert [note['item'] for note in entry['observation_notes']] == original.required_observations_prose
    assert 'EXAMPLE' in entry['observation_notes'][0]['answer']
    for name in ('store', 'restored'):
        checked = CliRunner().invoke(app, ['verify', '--store', str(case['args']['output'] / name)])
        assert checked.exit_code == 0, checked.output


@pytest.mark.parametrize('kind', ['field', 'error_input'])
def test_only_actual_qualified_predicate_becomes_fail(case, kind):
    if kind == 'error_input':
        case['capture'].update(field_violation_count=0, forbidden_error_input_count=1,
            violation_codes=['FORBIDDEN_ERROR_INPUT'], marker_contexts_seen=['error_input'])
    assert bridge.run(**case['args'], execute=True)['security'] == 'FAIL'


@pytest.mark.parametrize('missing', ['analysis', 'generation', 'linked_event', 'count', 'code', 'profile_unavailable', 'untransmitted_context'])
def test_incomplete_or_unqualified_predicate_remains_unknown(case, missing):
    if missing == 'analysis': case['capture']['analysis_binding_verified'] = False
    elif missing == 'generation': case['capture']['source_generation_verified'] = False
    elif missing == 'linked_event': case['capture']['source_reads'][0]['trial_linked_event_count'] = 0
    elif missing == 'count': case['capture']['field_violation_count'] = 0
    elif missing == 'code': case['capture']['violation_codes'] = []
    elif missing == 'untransmitted_context':
        case['capture'].update(field_violation_count=0, forbidden_error_input_count=0,
            violation_codes=['FORBIDDEN_CALLER_INPUT', 'FORBIDDEN_PRIVATE_SYSTEM'],
            marker_contexts_seen=['caller_input', 'private_system'])
    else:
        case['capture'].update(analysis_binding_verified=False, source_generation_verified=False,
            field_violation_count=0, violation_codes=[], source_reads=[],
            gaps=['OBS03_A_PROFILE_UNAVAILABLE'], normal_control={'attempted': False, 'succeeded': None})
        for row in case['capture']['request_summaries']: row.update(attempted=False, binding_verified=False, outcome=None)
    result = bridge.run(**case['args'], execute=True)
    assert result['security'] == 'UNKNOWN'
    assert 'PASS' not in latest_evidence(case).observations['declared_verdicts'].value.values()


@pytest.mark.parametrize('late', ['after', 'cleanup', 'artifact', 'form', 'export'])
def test_independent_fail_survives_late_failure(case, monkeypatch, late):
    if late == 'after':
        case['capture']['request_summaries'][-1].update(binding_verified=False, outcome=None)
        case['capture']['normal_control']['succeeded'] = None
        case['capture']['gaps'].append('OBS03_AFTER_UNAVAILABLE')
    elif late == 'cleanup':
        case['capture']['cleanup'] = {'complete': False, 'collection_gap': True}
        case['capture']['gaps'].append('OBS03_CLEANUP_UNAVAILABLE')
    elif late == 'artifact':
        original = bridge._write
        def write(path, value):
            if path.name == 'log-minimization.json': raise OSError(PRIVATE)
            return original(path, value)
        monkeypatch.setattr(bridge, '_write', write)
    elif late == 'form':
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path.name == 'manual-evidence.yaml': raise OSError(PRIVATE)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', open_file)
    else:
        original = bridge.export_bundle
        def export(source, output):
            if output.name == 'evidence.zip': raise OSError(PRIVATE)
            return original(source, output)
        monkeypatch.setattr(bridge, 'export_bundle', export)
    if late == 'export':
        with pytest.raises(OSError): bridge.run(**case['args'], execute=True)
    else: assert bridge.run(**case['args'], execute=True)['security'] == 'FAIL'
    assert latest_evidence(case).observations['declared_verdicts'].value['security'] == 'FAIL'
    assert next(Store.read_only(case['args']['output'] / 'store').iter_kind('RES')).verdicts.security.value == 'FAIL'


def test_raw_fields_hashes_credentials_and_rids_never_persist(case):
    case['capture'].update(raw=PRIVATE, stdout=PRIVATE, whoami=PRIVATE, request_id=PRIVATE,
        log_fields_seen=[PRIVATE], arbitrary_field_name=PRIVATE, content_sha256='f' * 64,
        projection={'field': PRIVATE}, response=PRIVATE, credential=PRIVATE)
    case['capture']['source_reads'][0].update(stdout=PRIVATE, raw_retained_sha256='e' * 64, field_hash='d' * 64)
    case['capture']['request_summaries'][1].update(request_id=PRIVATE, request_body=PRIVATE)
    case['capture']['gaps'].append(PRIVATE)
    bridge.run(**case['args'], execute=True)
    artifact = (case['args']['output'] / 'log-minimization.json').read_text()
    assert PRIVATE not in artifact
    assert all(key not in artifact for key in ('content_sha256', 'raw_retained_sha256', 'field_hash', 'projection', 'log_fields_seen'))
    for path in (case['args']['output'] / 'store/artifacts').iterdir(): assert PRIVATE.encode() not in path.read_bytes()


@pytest.mark.parametrize('bad', ['policy_hash', 'policy_ref', 'target_hash', 'source_id', 'origin'])
def test_collector_binding_mismatch_never_creates_evidence(case, bad):
    key = {'policy_hash': 'policy_values_sha256', 'policy_ref': 'policy_ref', 'target_hash': 'target_sha256',
           'source_id': 'source_id', 'origin': 'data_origin'}[bad]
    case['capture'][key] = 'DECLARED' if bad == 'origin' else PRIVATE
    with pytest.raises(ValueError): bridge.run(**case['args'], execute=True)
    assert not list(Store.read_only(case['args']['output'] / 'store').iter_kind('EVD'))


@pytest.mark.parametrize('key', ['forbidden_log_fields', 'forbidden_marker_contexts'])
def test_original_empty_forbidden_list_is_not_filled_or_rejected(case, key):
    pol = case['pol'].model_copy(update={'revision': 2,
        'values': {'OBS-03': {**case['values'], key: []}}}).seal()
    case['source'].put(pol)
    plan = select(case['pack'], [case['env']], pol); case['source'].put(plan)
    args = {**case['args'], 'plan_id': plan.id}
    result = bridge.run(**args)
    assert result['policy_values_sha256'] == bridge._digest(pol.values['OBS-03']) and not case['calls']
    assert pol.values['OBS-03'][key] == []


@pytest.mark.parametrize('bad', ['missing_policy', 'wrong_source', 'target_ref', 'output'])
def test_invalid_input_closes_before_collection(case, bad):
    if bad == 'missing_policy':
        pol = case['pol'].model_copy(update={'revision': 2, 'values': {}}).seal(); case['source'].put(pol)
        plan = select(case['pack'], [case['env']], pol); case['source'].put(plan); case['args']['plan_id'] = plan.id
    elif bad == 'wrong_source': case['args']['source_id'] = 'EXAMPLE-not-in-policy'
    elif bad == 'target_ref':
        value = deepcopy(case['target']); value['environment_ref']['sha256'] = '0' * 64
        case['args']['target'].write_text(json.dumps(value))
    else: case['args']['output'].mkdir()
    with pytest.raises(ValueError): bridge.run(**case['args'], execute=True)
    assert not case['calls']


def test_unverified_dump_never_becomes_verified_branch(case):
    case['capture']['branches_tested'].append('exception_dump')
    bridge.run(**case['args'], execute=True)
    evidence = latest_evidence(case)
    assert 'exception_dump' not in evidence.observations['log_required_branches_tested'].value
    assert evidence.observations['log_fields_seen'].value is None


def test_existing_output_and_original_refs_are_preserved(case):
    bridge.run(**case['args'], execute=True)
    first = latest_evidence(case)
    source = case['args']['output'] / 'store'; before = bridge._inventory(source)
    second_args = {**case['args'], 'store': source, 'output': case['args']['output'].parent / 'second'}
    bridge.run(**second_args, execute=True)
    restored = Store.read_only(second_args['output'] / 'store')
    assert restored.resolve(first.ref()).ref() == first.ref()
    assert restored.latest(first.id).revision == first.revision + 1 and bridge._inventory(source) == before
    with pytest.raises(ValueError): bridge.run(**second_args, execute=True)
