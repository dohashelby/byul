"""REQ-08·09·SUP-08 Kubernetes 전송의 닫힌 요청 계약. EXAMPLE target만 쓰고 kubectl은 부르지 않는다."""
import json

import pytest

from guard.collectors.file import load_environment
from guard.executors.kubernetes_input_validation import KubernetesInputValidationExecutor, _WithBody
from guard.executors.stream_probe import _prepare
from guard.executors.registry import EXECUTORS
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE, KubernetesBindingError, KubernetesHTTP
from tests.builders import baseline_pack
from tests.test_gpu_a_run_inputs import UPDATE, build
from tools import gpu_a_run_inputs as inputs

TOKENS = {'model': 'test-model', 'prompt': [0], 'max_tokens': 1}
CHAT = {'model': 'test-model', 'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 1, 'temperature': 0}


def _no_kubectl(*args, **kwargs):
    raise AssertionError('no kubectl in contract tests')


def transport(tmp_path):
    build(tmp_path, 'v2-default')
    target = json.loads((tmp_path / 'target.json').read_text())
    return KubernetesHTTP(target, load_environment(tmp_path / 'env.json'), duration=5, runner=_no_kubectl,
        profile=INPUT_VALIDATION_PROFILE)


def request(body, rid='func|POST_/v1/completions', **extra):
    return {'id': rid, 'method': 'POST', 'url': 'http://10.244.0.11:8000/v1/completions', 'headers': {}, 'body': body, **extra}


def test_token_prompt_contract_accepts_only_bounded_token_arrays(tmp_path):
    t = transport(tmp_path)
    with pytest.raises(KubernetesBindingError):
        t._input_validation_request(request(TOKENS))  # 계약 선언 전에는 아무 요청도 받지 않는다
    t.declare_input_contract({'path': '/v1/completions', 'control': TOKENS, 'prompt_tokens': {'max_length': 128, 'max_value': 151936},
        'fields': {}})
    for prompt in ([0], [151936], [0] * 128):
        t._input_validation_request(request({**TOKENS, 'prompt': prompt}))
    for body in ({**TOKENS, 'prompt': [0] * 129}, {**TOKENS, 'prompt': [151937]}, {**TOKENS, 'prompt': [-1]},
            {**TOKENS, 'prompt': 'text'}, {**TOKENS, 'max_tokens': 2}, {**TOKENS, 'echo': True}, {'model': 'test-model', 'prompt': [0]},
            {**TOKENS, 'model': 'other'}):
        with pytest.raises(KubernetesBindingError):
            t._input_validation_request(request(body))
    for bad in (request(TOKENS, rid='sampling-x'), request(TOKENS, rid='func|POST_/v1/chat/completions'),
            request(TOKENS, rid='func|POST /v1/completions'), request(TOKENS, headers={'Authorization': 'Bearer x'}),
            request(TOKENS, method='GET'), request(TOKENS, mode='sse')):
        with pytest.raises(KubernetesBindingError):
            t._input_validation_request(bad)
    with pytest.raises(KubernetesBindingError):
        t.declare_input_contract({'path': '/v1/completions', 'control': TOKENS, 'prompt_tokens': None, 'fields': {}})


def test_field_contract_accepts_only_the_declared_fixture(tmp_path):
    t = transport(tmp_path)
    t.declare_input_contract({'path': '/v1/chat/completions', 'control': CHAT, 'prompt_tokens': None,
        'fields': {'code_revision': 'byul-unapproved-revision'}})
    chat = 'func|POST_/v1/chat/completions'
    t._input_validation_request(request(CHAT, rid=chat))
    t._input_validation_request(request({**CHAT, 'code_revision': 'byul-unapproved-revision'}, rid=chat))
    for body in ({**CHAT, 'code_revision': 'main'}, {**CHAT, 'trust_remote_code': True},
            {**CHAT, 'messages': [{'role': 'user', 'content': 'other'}]}):
        with pytest.raises(KubernetesBindingError):
            t._input_validation_request(request(body, rid=chat))


@pytest.mark.parametrize('contract', [
    {'path': '/v1/embeddings', 'control': TOKENS, 'prompt_tokens': None, 'fields': {}},
    {'path': '/v1/completions', 'control': {**TOKENS, 'model': 'other'}, 'prompt_tokens': None, 'fields': {}},
    {'path': '/v1/completions', 'control': TOKENS, 'prompt_tokens': None, 'fields': {'max_tokens': 2}},
    {'path': '/v1/completions', 'control': TOKENS, 'prompt_tokens': None, 'fields': {'a.b': 1}},
    {'path': '/v1/completions', 'control': CHAT, 'prompt_tokens': {'max_length': 2, 'max_value': 2}, 'fields': {}},
    {'path': '/v1/completions', 'control': TOKENS, 'prompt_tokens': {'max_length': 0, 'max_value': 2}, 'fields': {}}])
def test_contract_declaration_is_closed(tmp_path, contract):
    with pytest.raises(KubernetesBindingError):
        transport(tmp_path).declare_input_contract(contract)


def test_executor_is_registered_and_rejects_unknown_settings(tmp_path):
    assert 'input_validation:kubernetes' in EXECUTORS
    build(tmp_path, 'v2-default')
    check = baseline_pack().checks['REQ-09']
    executor = KubernetesInputValidationExecutor({'target': str(tmp_path / 'target.json'), 'vocab_size': 151936,
        'vocab_source': 'EXAMPLE', 'shell': 'x'}, runner=_no_kubectl)
    values = {'max_input_tokens': 127, 'rejection_codes': ['400'], 'allowed_endpoints': ['POST /v1/completions'],
        'allowed_input_formats': ['token_ids']}
    with pytest.raises(ValueError, match='KUBERNETES_INPUT_CONFIGURATION_INVALID'):
        executor.run(check, load_environment(tmp_path / 'env.json'), values, object(), tmp_path / 'work')


@pytest.mark.parametrize('path,body', [('/v1/completions', TOKENS), ('/v1/chat/completions', CHAT)])
def test_executor_ids_cross_both_request_contracts_and_return_unchanged(tmp_path, path, body):
    t = transport(tmp_path)
    t.declare_input_contract({'path': path, 'control': body, 'prompt_tokens': None, 'fields': {}})
    original = request(body, rid='func|POST ' + path)

    def fixed_probe(network, requests, timeout):
        assert network == 'engine'
        wire, = requests
        t._input_validation_request(wire)
        source = {key: value for key, value in wire.items() if key != 'want_body'}
        prepared, = _prepare([source], timeout)
        assert wire['want_body'] is True
        return [{'id': prepared['id'], 'outcome': 200, 'body': '{}'}]

    rows = _WithBody(fixed_probe)('engine', [original], 5)
    assert rows == [{'id': original['id'], 'outcome': 200, 'body': '{}'}]
    assert original == request(body, rid='func|POST ' + path)


@pytest.mark.parametrize('check_id', ['REQ-08', 'REQ-09', 'SUP-08'])
def test_run_inputs_write_kubernetes_input_configs(tmp_path, check_id):
    build(tmp_path, 'v2-default')
    inputs.main(['http-check', '--check', check_id, '--env', str(tmp_path / 'env.json'), '--target', str(tmp_path / 'target.json'),
        '--policy-draft', str(UPDATE / 'docs/gpu-a/policy-a-draft.yaml'), '--out', str(tmp_path / check_id)])
    config = (tmp_path / check_id / 'guard.toml').read_text()
    assert "input_validation = 'kubernetes'" in config and 'vocab_size = 151936' in config
    assert ('reused_token_ids' in config) is (check_id == 'REQ-08')
