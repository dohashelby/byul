"""EXAMPLE error-only producer/core contracts; no Kubernetes or engine launch."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import types

import pytest

from guard.executors import registry
from guard.executors.response_error_contract import observe_error_response
from guard.models import DataOrigin, Policy
from guard.store import Store
from tests.test_kubernetes_request_binding import env, target
from tools import b2_response_kubernetes as producer

PRIVATE = 'PRIVATE_FIELD_OR_ERROR_DO_NOT_PERSIST'
FIELDS = ['error.message', 'error.type', 'error.param', 'error.code']


@pytest.fixture
def inputs(tmp_path):
    environment = env()
    destination = target(environment)
    pack = producer.proposal_pack(tmp_path / 'pack')
    policy = Policy(id='POL-REQ13-K8S-EXAMPLE', created_at=datetime.now(timezone.utc),
        data_origin=DataOrigin.EXAMPLE, status='DRAFT', pack=pack.ref(), scope={'include': ['REQ-13']},
        values={'REQ-13': {'allowed_response_fields': ['id'], 'allowed_error_fields': FIELDS,
                          'forbidden_return_options': [], 'required_targets': ['engine']}}).seal()
    return environment, policy, pack, destination


def error_record(inputs, *, violation=False, after=True, cleanup=True, status=400):
    environment, _, _, destination = inputs
    value = {'error': {'message': 'invalid request ' + PRIVATE, 'type': 'BadRequestError', 'param': None, 'code': status}}
    if violation:
        value['error'][PRIVATE] = PRIVATE
    body = json.dumps(value)
    observed = observe_error_response({'outcome': status, 'body': body, 'body_bytes': len(body.encode())},
        marks={'error_input': PRIVATE}, allowed_fields=inputs[1].values.get('REQ-13', {}).get('allowed_error_fields'), forbidden_contexts=None)
    now = datetime.now(timezone.utc).isoformat()
    control = {'attempted': True, 'collected': True, 'succeeded': True, 'http_status': 200,
        'token_count': 3, 'max_model_len': 100, 'tokens_sha256': 'a' * 64,
        'response_sha256': 'b' * 64, 'request_id_sha256': 'c' * 64,
        'binding_verified': True, 'capture_complete': True, 'private': PRIVATE}
    return {'schema': producer.CAPTURE_SCHEMA, 'profile': producer.CAPTURE_PROFILE, 'source_profile': producer.SOURCE_PROFILE,
        'collection_id': 'req13-error-k8s-' + 'd' * 32, 'data_origin': 'EXAMPLE',
        'environment_ref': environment.ref().model_dump(mode='json'), 'target_sha256': producer._digest(destination),
        'api_model_sha256': hashlib.sha256(b'fixture-model').hexdigest(),
        'allowed_error_fields_sha256': producer._digest(inputs[1].values.get('REQ-13', {}).get('allowed_error_fields')),
        'capture_started_at': now, 'capture_finished_at': now, 'attempted_requests': 3,
        'request_summaries': [{'case_id': cid, 'method': 'POST', 'path': path, 'request_id_sha256': str(index) * 64,
            'body_sha256': 'f' * 64, 'attempted': True, 'binding_verified': True, 'started_at': now, 'finished_at': now,
            'raw_response_bytes_sha256': '0' * 64} for index, (cid, path) in enumerate(producer._REQUEST_PATHS.items(), 1)],
        'capture_status': 'completed' if after and cleanup else 'incomplete',
        'normal_controls': {'before': control, 'after': {**control, 'succeeded': after}},
        'error_observation': {**observed, 'private': PRIVATE},
        'gaps': [] if after else ['AFTER_CONTROL_FAILED', PRIVATE],
        'cleanup': {'collected': True, 'complete': cleanup, 'collection_gap': not cleanup, 'private': PRIVATE,
                    'error_sha256': [] if cleanup else ['e' * 64]},
        'private': PRIVATE}


def run(tmp_path, inputs, record=None, *, fail=False):
    calls = []
    def capture(environment, destination, **kwargs):
        calls.append((environment.ref(), deepcopy(destination), deepcopy(kwargs)))
        if fail:
            raise OSError(PRIVATE)
        return deepcopy(record)
    summary = producer.run_pipeline(*inputs, tmp_path / 'run', execute=True, _example_capture=capture)
    store = Store(tmp_path / 'run/store')
    evidence, = store.iter_kind('EVD')
    result, = store.iter_kind('RES')
    return summary, calls, store, evidence, result


def test_allowed_error_remains_partial_unknown_and_exact_ref_restore(tmp_path, inputs):
    previous = dict(registry.EXECUTORS)
    summary, calls, store, evidence, result = run(tmp_path, inputs, error_record(inputs))
    assert len(calls) == 1 and calls[0][2]['allowed_error_fields'] == FIELDS
    assert calls[0][2]['forbidden_contexts'] is None and calls[0][2]['binary'] == 'kubectl'
    assert summary['collector_status'] == 'completed' and summary['execution_status'] == 'ABORTED'
    assert result.verdicts.security.value == 'UNKNOWN' and result.verdicts.functional.value == 'UNKNOWN'
    assert evidence.data_origin == DataOrigin.EXAMPLE and evidence.normal_control.succeeded
    assert evidence.observations['response_coverage_complete'].value is False
    for key in ('error_fields_seen', 'response_fields_seen', 'response_options_returned',
                'response_options_tested', 'private_markers_returned', 'functional_probe_results'):
        assert evidence.observations[key].value is None and not evidence.observations[key].collected
    assert evidence.observations['response_violations'].value == []
    assert summary['exact_ref_rejudgment_unchanged'] and summary['restore_verified']
    assert store.verify() == [] and registry.EXECUTORS == previous


@pytest.mark.parametrize('after,cleanup,status', [(True, True, 400), (False, True, 400),
    (True, False, 400), (False, False, 500)])
def test_actual_policy_field_fail_survives_later_gaps(tmp_path, inputs, after, cleanup, status):
    record = error_record(inputs, violation=True, after=after, cleanup=cleanup, status=status)
    summary, calls, _, evidence, result = run(tmp_path, inputs, record)
    assert len(calls) == 1 and result.verdicts.security.value == 'FAIL'
    assert summary['restore_verified'] and evidence.execution.status.value == 'ABORTED'
    violations = evidence.observations['response_violations'].value
    assert violations == ['NONPERMITTED_FIELD_SHA256:' + hashlib.sha256(('error.' + PRIVATE).encode()).hexdigest()]
    assert evidence.normal_control.succeeded is after
    if not cleanup:
        assert evidence.execution.cleanup_errors == ['REQ13_OWNED_PROBE_CLEANUP_UNVERIFIED']


def test_raw_keys_marks_and_exception_text_never_persist(tmp_path, inputs):
    summary, _, store, evidence, _ = run(tmp_path, inputs, error_record(inputs, violation=True))
    for artifact in evidence.artifacts:
        saved = (store.root / artifact.path).read_text()
        assert PRIVATE not in saved and 'BadRequestError' not in saved
        assert 'field_path_sha256' in saved and 'NONPERMITTED_FIELD_SHA256:' in saved
    assert PRIVATE not in json.dumps(summary)


def test_artifact_failure_preserves_independent_fail(tmp_path, inputs, monkeypatch):
    original = producer._write
    def write(path, value):
        if Path(path).name == 'req13-error.json':
            raise OSError(PRIVATE)
        original(path, value)
    monkeypatch.setattr(producer, '_write', write)
    _, _, _, evidence, result = run(tmp_path, inputs, error_record(inputs, violation=True))
    assert not evidence.artifacts and result.verdicts.security.value == 'FAIL'


def test_unset_policy_preserves_not_run_selection_and_does_not_capture(tmp_path, inputs):
    args = list(inputs)
    raw = deepcopy(args[1].values)
    raw['REQ-13'].pop('allowed_error_fields')
    args[1] = args[1].model_copy(update={'values': raw}).seal()
    summary, calls, _, evidence, result = run(tmp_path, args, error_record(args))
    assert not calls and summary['capture_calls'] == 0
    assert evidence.execution.status.value == 'NOT_RUN' and result.verdicts.security.value == 'UNKNOWN'
    assert not evidence.artifacts


def test_explicit_none_retains_policy_type_error_before_request_or_output(tmp_path, inputs):
    args = list(inputs)
    raw = deepcopy(args[1].values)
    raw['REQ-13']['allowed_error_fields'] = None
    args[1] = args[1].model_copy(update={'values': raw}).seal()
    with pytest.raises(ValueError):
        producer.run_pipeline(*args, tmp_path / 'bad', execute=True)
    assert not (tmp_path / 'bad').exists()


def test_empty_allowlist_is_not_defaulted_and_deny_all_is_projected(tmp_path, inputs):
    args = list(inputs)
    raw = deepcopy(args[1].values)
    raw['REQ-13']['allowed_error_fields'] = []
    args[1] = args[1].model_copy(update={'values': raw}).seal()
    record = error_record(args)
    record['error_observation']['violations'] = ['NONPERMITTED_FIELD_SHA256:' + h
        for h in record['error_observation']['field_path_sha256']]
    _, calls, _, evidence, result = run(tmp_path, args, record)
    assert calls[0][2]['allowed_error_fields'] == []
    assert len(evidence.observations['response_violations'].value) == 4
    assert result.verdicts.security.value == 'FAIL'


@pytest.mark.parametrize('change', ['environment', 'target', 'alias', 'origin', 'time', 'classification', 'policy'])
def test_wrong_binding_or_policy_projection_is_not_promoted(tmp_path, inputs, change):
    record = error_record(inputs, violation=True)
    if change == 'environment': record['environment_ref']['sha256'] = '0' * 64
    elif change == 'target': record['target_sha256'] = '0' * 64
    elif change == 'alias': record['api_model_sha256'] = '0' * 64
    elif change == 'origin': record['data_origin'] = 'MEASURED'
    elif change == 'time': record['capture_finished_at'] = 'not-a-time'
    elif change == 'policy': record['allowed_error_fields_sha256'] = '0' * 64
    else: record['error_observation']['violations'] = []
    _, _, _, evidence, result = run(tmp_path, inputs, record)
    assert result.verdicts.security.value == 'UNKNOWN'
    assert not evidence.observations['response_violations'].collected


@pytest.mark.parametrize('status', [200, 301, 'transport_error'])
def test_non_error_response_never_uses_error_field_policy(tmp_path, inputs, status):
    record = error_record(inputs, violation=True)
    record['error_observation']['outcome'] = status
    _, _, _, evidence, result = run(tmp_path, inputs, record)
    assert result.verdicts.security.value == 'UNKNOWN'
    assert evidence.observations['response_violations'].value is None


def test_capture_exception_is_fixed_diagnostic_and_unknown(tmp_path, inputs):
    summary, _, _, evidence, result = run(tmp_path, inputs, fail=True)
    assert summary['diagnostics'] == ['REQ13_CAPTURE_OR_COMPONENT_UNAVAILABLE']
    assert not evidence.artifacts and result.verdicts.security.value == 'UNKNOWN'
    assert PRIVATE not in json.dumps(summary)


def test_declared_environment_cannot_use_example_capture(tmp_path, inputs):
    args = list(inputs)
    args[0] = args[0].model_copy(update={'data_origin': DataOrigin.DECLARED}).seal()
    args[3] = target(args[0])
    with pytest.raises(ValueError, match='INJECTION_REQUIRES_EXAMPLE'):
        producer.run_pipeline(*args, tmp_path / 'bad', execute=True, _example_capture=lambda *a, **k: {})


def test_draft_preserves_original_policy_required_observations_and_judge_rules(inputs):
    check = inputs[2].checks['REQ-13']
    original = producer.CheckDef.model_validate(producer.yaml.safe_load((Path(producer.__file__).resolve().parents[1]
        / 'reference/pack-seeds/PACK-BASELINE-31/checks/REQ-13.yaml').read_text()))
    assert check.policy_params == original.policy_params
    assert check.required_observations[:-1] == original.required_observations
    assert check.judge.params['pass_when_all'] == original.judge.params['pass_when_all']
    assert check.judge.params['fail_when_any'][:-1] == original.judge.params['fail_when_any']
    assert check.judge.params['axes'] == original.judge.params['axes']


def test_import_gate_checks_bytes_and_function_origin_without_git_history(tmp_path):
    source = tmp_path / 'owned.py'
    source.write_text('def capture():\n    return None\n')
    module = types.ModuleType('EXAMPLE-owned')
    module.__file__ = str(source)
    exec(compile(source.read_text(), str(source), 'exec'), module.__dict__)
    pin = hashlib.sha256(source.read_bytes()).hexdigest()
    producer._verify_import(module, module.capture, source, pin)
    source.write_text('def capture():\n    return 1\n')
    with pytest.raises(ValueError): producer._verify_import(module, module.capture, source, pin)
    foreign = tmp_path / 'foreign.py'
    foreign.write_text('def capture():\n    return None\n')
    namespace = {}
    exec(compile(foreign.read_text(), str(foreign), 'exec'), namespace)
    with pytest.raises(ValueError):
        producer._verify_import(module, namespace['capture'], source, hashlib.sha256(source.read_bytes()).hexdigest())


def test_public_entry_pin_unavailable_never_dispatches(tmp_path, inputs, monkeypatch):
    monkeypatch.setattr(producer, 'verify_runtime_components', lambda repo: (_ for _ in ()).throw(ValueError(PRIVATE)))
    summary = producer.run_pipeline(*inputs, tmp_path / 'closed', execute=True)
    assert summary['capture_calls'] == 0 and summary['security'] == 'UNKNOWN' and summary['restore_verified']


def test_later_component_postcheck_failure_does_not_erase_primary_policy_fail(tmp_path, inputs, monkeypatch):
    calls = []
    def verify(repo):
        calls.append('gate')
        if len(calls) > 1:
            raise OSError(PRIVATE)
        return lambda *a, **k: error_record(inputs, violation=True)
    monkeypatch.setattr(producer, 'verify_runtime_components', verify)
    summary = producer.run_pipeline(*inputs, tmp_path / 'post', execute=True)
    assert calls == ['gate', 'gate'] and summary['security'] == 'FAIL'
    assert summary['diagnostics'] == ['REQ13_COMPONENT_POSTCHECK_UNAVAILABLE']


def test_zero_requests_cannot_promote_even_forged_nonempty_error_observation(tmp_path, inputs):
    record = error_record(inputs, violation=True)
    record.update(attempted_requests=0, capture_status='incomplete', gaps=['A_CAPABILITY_UNAVAILABLE'])
    for frame in record['request_summaries']:
        frame.update(attempted=False, binding_verified=False, started_at=None, finished_at=None, raw_response_bytes_sha256=None)
    _, _, _, evidence, result = run(tmp_path, inputs, record)
    assert evidence.observations['response_violations'].value is None
    assert not evidence.observations['response_violations'].collected and result.verdicts.security.value == 'UNKNOWN'


def test_same_response_under_explicitly_different_policy_is_classified_by_that_policy(tmp_path, inputs):
    args = list(inputs)
    values = deepcopy(args[1].values)
    values['REQ-13']['allowed_error_fields'] = [*FIELDS, 'error.' + PRIVATE]
    args[1] = args[1].model_copy(update={'values': values}).seal()
    record = error_record(args, violation=True)
    _, calls, _, evidence, result = run(tmp_path, args, record)
    assert calls[0][2]['allowed_error_fields'] == values['REQ-13']['allowed_error_fields']
    assert evidence.observations['response_violations'].value == []
    assert result.verdicts.security.value == 'UNKNOWN'


def test_gateway_only_policy_does_not_send_engine_request(tmp_path, inputs):
    args = list(inputs)
    values = deepcopy(args[1].values)
    values['REQ-13']['required_targets'] = ['gateway']
    args[1] = args[1].model_copy(update={'values': values}).seal()
    summary, calls, _, _, result = run(tmp_path, args, error_record(args))
    assert not calls and summary['diagnostics'] == ['REQ13_ENGINE_NOT_SELECTED']
    assert result.verdicts.security.value == 'UNKNOWN'


def test_unsupported_capability_keeps_zero_request_metadata_without_api_identity_claim(tmp_path, inputs):
    record = error_record(inputs)
    record.update(api_model_sha256=None, attempted_requests=0, request_summaries=[],
        error_observation=None, capture_status='incomplete', gaps=['A_CAPABILITY_UNAVAILABLE'])
    record['normal_controls'] = {key: {'attempted': False, 'collected': False, 'succeeded': None}
                                 for key in ('before', 'after')}
    summary, _, store, evidence, result = run(tmp_path, inputs, record)
    assert summary['collector_status'] == 'incomplete'
    assert summary['diagnostics'] == [] and result.verdicts.security.value == 'UNKNOWN'
    assert not evidence.observations['response_violations'].collected
    artifact = json.loads((store.root / evidence.artifacts[0].path).read_text())
    assert artifact['attempted_requests'] == 0 and artifact['api_model_sha256'] is None
    assert artifact['gaps'] == ['A_CAPABILITY_UNAVAILABLE']


def test_unsupported_engine_version_cannot_promote_error_facts(tmp_path, inputs):
    args = list(inputs)
    engine = args[0].engine.model_copy(update={'version': '0.32.0'})
    args[0] = args[0].model_copy(update={'engine': engine}).seal()
    args[3] = target(args[0])
    _, _, _, evidence, result = run(tmp_path, args, error_record(args, violation=True))
    assert evidence.observations['response_violations'].value is None
    assert result.verdicts.security.value == 'UNKNOWN'
