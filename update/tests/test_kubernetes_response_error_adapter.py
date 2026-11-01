"""EXAMPLE adapter extraction contracts; no Kubernetes or source CPU execution."""
import ast
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from guard.adapters import kubernetes_response_error as adapter
from guard.models import DataOrigin
from tests.builders import environment
from tests.test_response_kubernetes_capture import ExampleTransport, FIELDS
from tools import b2_response_kubernetes_capture as original_capture
from tools import b2_response_kubernetes as original_projection

PRIVATE = 'PRIVATE_FIELD'


@pytest.fixture
def inputs():
    env = environment(engine={'name': 'vllm', 'version': '0.31.0'}, model={'id': 'repository/model', 'revision': 'r1'})
    return env, {'environment_ref': env.ref().model_dump(mode='json')}


def capture(inputs, *, variant=None, fields=FIELDS):
    env, base = inputs
    target = {**base, 'example_variant': variant}
    instances = []
    def factory(*args, **kwargs):
        transport = ExampleTransport(*args, **kwargs); instances.append(transport); return transport
    record = adapter.capture_validation_error(env, target, _transport_factory=factory,
        allowed_error_fields=fields, binary='/EXAMPLE/kubectl')
    return record, instances[0], target


def test_original_capture_and_projection_algorithms_are_preserved():
    # Installed paths and approved A source pin change; dispatch/projection does not.
    for name in ('_digest', '_sha', '_normal', '_report', '_bound_response', '_capture_body', '_normal_response', 'capture_validation_error'):
        assert ast.dump(ast.parse(inspect.getsource(getattr(adapter, name)))) == ast.dump(ast.parse(inspect.getsource(getattr(original_capture, name))))
    for name in ('_control', '_time'):
        assert inspect.getsource(getattr(adapter, name)) == inspect.getsource(getattr(original_projection, name))
    expected = inspect.getsource(original_projection._project).replace('def _project(', 'def project_error_capture(').replace("'profile': PROFILE", "'profile': PROJECTION_PROFILE")
    assert ast.dump(ast.parse(expected)) == ast.dump(ast.parse(inspect.getsource(adapter.project_error_capture)))


def test_installed_helper_paths_match_all_four_approved_bytes_and_function_origins():
    adapter.verify_product_helpers()
    assert adapter.HELPER_PINS == original_capture.HELPER_PINS
    assert adapter.A_HTTP_SOURCE_SHA256 == '3da64c61ccceda9899e57d022b3ff7afaee3a85906d118a9309f368dd8f38566'


def test_current_old_a_source_is_closed_before_transport(inputs, monkeypatch):
    env, target = inputs
    env = env.model_copy(update={'data_origin': DataOrigin.DECLARED}).seal()
    target = {'environment_ref': env.ref().model_dump(mode='json')}
    calls = []
    monkeypatch.setattr(adapter.kubernetes_http, 'KubernetesHTTP', lambda *a, **kw: calls.append(kw))
    result = adapter.capture_validation_error(env, target, allowed_error_fields=FIELDS)
    assert calls == [] and result['attempted_requests'] == 0 and result['error_observation'] is None
    assert 'A_CAPABILITY_UNAVAILABLE' in result['gaps']


def test_extraction_uses_exact_three_public_cases_and_original_safe_projection(inputs):
    raw, transport, target = capture(inputs)
    env, _ = inputs
    projected = adapter.project_error_capture(raw, env, target, 'api-alias', FIELDS)
    assert projected == original_projection._project(raw, env, target, 'api-alias', FIELDS)
    assert raw['profile'] == adapter.CAPTURE_PROFILE and raw['schema'] == adapter.CAPTURE_SCHEMA
    assert projected['profile'] == adapter.PROJECTION_PROFILE
    assert [request['id'] for request in transport.calls] == list(adapter.CASE_IDS.values())
    assert all(request['body']['model'] == 'api-alias' for request in transport.calls)
    assert projected['error_observation']['violations'] == []
    assert raw['capture_status'] == 'completed' and raw['gaps'] == ['POLICY_UNSET']
    assert projected['native_handler_consumption_verified'] is False and projected['source_routing_verified'] is False
    assert projected['model_inference_verified'] is False
    assert not any(value in json.dumps(projected) for value in ('api-alias', 'hello', transport.calls[1]['body']['max_tokens']))


@pytest.mark.parametrize('variant', ['extra', 'after_error', 'status500', 'partial'])
def test_confirmed_field_violation_survives_late_or_incomplete_subset_capture(inputs, variant):
    raw, _, target = capture(inputs, variant=variant)
    projected = adapter.project_error_capture(raw, inputs[0], target, 'api-alias', FIELDS)
    expected = original_projection._project(raw, inputs[0], target, 'api-alias', FIELDS)
    assert projected == expected
    if variant == 'partial':
        # The existing helper preserves fields from a complete retained JSON
        # document while the transport capture itself remains incomplete.
        assert projected['error_observation']['violations'] == []
        assert projected['error_observation']['field_path_sha256']
    else:
        assert projected['error_observation']['violations'] == ['NONPERMITTED_FIELD_SHA256:' + hashlib.sha256(PRIVATE.encode()).hexdigest()]
    if variant in {'after_error', 'status500', 'partial'}: assert raw['capture_status'] == 'incomplete'
    saved = json.dumps(projected)
    assert PRIVATE not in saved and 'PRIVATE_RESPONSE' not in saved and 'PRIVATE_TRANSPORT_EXCEPTION' not in saved


def test_explicit_empty_field_policy_has_its_own_hash_and_confirms_all_observed_fields(inputs):
    raw, _, target = capture(inputs, fields=[])
    projected = adapter.project_error_capture(raw, inputs[0], target, 'api-alias', [])
    assert len(projected['error_observation']['violations']) == len(FIELDS)
    assert raw['allowed_error_fields_sha256'] == adapter._digest([])
    assert projected == original_projection._project(raw, inputs[0], target, 'api-alias', [])


def test_missing_field_policy_is_not_replaced_by_empty_policy(inputs):
    raw, _, target = capture(inputs, fields=None)
    assert raw['allowed_error_fields_sha256'] is None
    assert 'FIELD_POLICY_UNSET' in raw['error_observation']['coverage_gaps']
    assert adapter.project_error_capture(raw, inputs[0], target, 'api-alias', []) is None
    assert adapter.project_error_capture(raw, inputs[0], target, 'api-alias', None) == original_projection._project(raw, inputs[0], target, 'api-alias', None)


def test_projection_rejects_different_policy_values_even_with_the_same_environment(inputs):
    raw, _, target = capture(inputs, variant='extra')
    assert adapter.project_error_capture(raw, inputs[0], target, 'api-alias', FIELDS + [PRIVATE]) is None
    wrong = deepcopy(raw); wrong['error_observation']['violations'] = []
    assert adapter.project_error_capture(wrong, inputs[0], target, 'api-alias', FIELDS)['error_observation'] is None


@pytest.mark.parametrize('variant', ['status200', 'empty_fields', 'bad_binding', 'wrong_id'])
def test_unconfirmed_primary_response_remains_uncollected(inputs, variant):
    raw, _, target = capture(inputs, variant=variant)
    projected = adapter.project_error_capture(raw, inputs[0], target, 'api-alias', FIELDS)
    assert projected['error_observation'] is None


def test_example_transport_injection_cannot_enter_declared_environment(inputs):
    env, _ = inputs
    env = env.model_copy(update={'data_origin': DataOrigin.DECLARED}).seal()
    with pytest.raises(ValueError, match='EXAMPLE_TRANSPORT_REQUIRES_EXAMPLE_ENV'):
        adapter.capture_validation_error(env, {'environment_ref': env.ref().model_dump(mode='json')}, _transport_factory=ExampleTransport)


def test_helper_foreign_function_origin_is_rejected(inputs, monkeypatch):
    monkeypatch.setattr(adapter, 'observe_error_response', lambda *a, **kw: None)
    with pytest.raises(ValueError, match='HELPER_IMPORT_MISMATCH'): adapter.verify_product_helpers()


def test_example_installed_layout_capability_checks_positive_bytes_and_origin(tmp_path, monkeypatch):
    # Synthetic EXAMPLE approval validates the installed-path gate itself.
    # The actual fixed A108 bytes are consumed in root's separate A worktree.
    root = tmp_path / 'installed/guard'
    (root / 'adapters').mkdir(parents=True); (root / 'runtime').mkdir()
    file = root / 'runtime/kubernetes_http.py'
    source = "VALIDATION_ERROR_PROFILE='kubernetes-owned-validation-error-http-v1'\nclass KubernetesHTTP:\n def __init__(self):pass\n def __call__(self):pass\n def report(self):pass\n"
    file.write_text(source)
    module = ModuleType('EXAMPLE-approved-public-a'); module.__file__ = str(file)
    exec(compile(source, str(file), 'exec'), module.__dict__)
    monkeypatch.setattr(adapter, '__file__', str(root / 'adapters/kubernetes_response_error.py'))
    monkeypatch.setattr(adapter, 'kubernetes_http', module)
    monkeypatch.setattr(adapter, 'A_HTTP_SOURCE_SHA256', hashlib.sha256(source.encode()).hexdigest())
    adapter.verify_a_capability()
    file.write_text(source + '# unapproved bytes\n')
    with pytest.raises(ValueError, match='A_CAPABILITY_UNAVAILABLE'): adapter.verify_a_capability()


def test_late_helper_postcheck_failure_keeps_previously_verified_field_facts(inputs, monkeypatch):
    original = adapter.verify_product_helpers
    checks = []
    def verify():
        checks.append(True)
        if len(checks) == 8:
            raise OSError('PRIVATE_POST_ERROR')
        return original()
    monkeypatch.setattr(adapter, 'verify_product_helpers', verify)
    raw, _, target = capture(inputs, variant='post_source_error')
    projected = adapter.project_error_capture(raw, inputs[0], target, 'api-alias', FIELDS)
    assert raw['capture_status'] == 'incomplete' and 'A_SOURCE_POSTCHECK_UNAVAILABLE' in raw['gaps']
    assert projected['error_observation']['violations'] == ['NONPERMITTED_FIELD_SHA256:' + hashlib.sha256(PRIVATE.encode()).hexdigest()]
    assert 'PRIVATE_POST_ERROR' not in json.dumps(projected)
