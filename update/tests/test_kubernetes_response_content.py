"""REQ-13 Kubernetes 전송: 원 응답 내용 실행기가 만든 요청을 실제 전송 검증기에 넣고 판정까지 잇는다.

kubectl은 부르지 않는다. 엔진 응답은 대역이며 전송 검증기·사례·관측·판정은 원 코드다.
"""
import json

import pytest

from guard.collectors.file import load_environment
from guard.executors import kubernetes_response_content as module
from guard.executors.registry import EXECUTORS
from guard.runtime.kubernetes_http import RESPONSE_CONTENT_PROFILE, KubernetesBindingError, KubernetesHTTP
from tests.builders import baseline_pack
from tests.test_gpu_a_run_inputs import build

VALUES = {"allowed_response_fields": ["id", "model", "choices[].text", "choices[].message.role", "choices[].message.content",
          "choices[].finish_reason", "usage.prompt_tokens", "usage.completion_tokens", "usage.total_tokens"],
          "allowed_error_fields": ["error.message"], "forbidden_return_options": ["return_prompt_text", "return_token_ids"],
          "required_targets": ["engine"]}


def _no_kubectl(*args, **kwargs):
    raise AssertionError('no kubectl in contract tests')


class Engine:
    """엔진 응답 대역. leak이면 금지 옵션 필드를 돌려준다."""
    def __init__(self, transport, *, leak=False):
        self.transport = transport; self.leak = leak; self.seen = []

    def __call__(self, network, requests, timeout):
        assert network == 'kubernetes-probe'
        request, = requests
        self.transport._response_content_request(request)  # 원 실행기가 만든 그대로 검증한다
        self.seen.append(request['id'])
        body = request['body']; chat = 'messages' in body
        choice = {'message': {'role': 'assistant', 'content': 'ok'}} if chat else {'text': 'ok'}
        choice['finish_reason'] = 'length'
        value = {'id': 'x', 'model': self.transport.api_model, 'choices': [choice],
                 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
        status = 200
        if isinstance(body['max_tokens'], str):
            status, value = 400, {'error': {'message': 'invalid max_tokens'}}
        elif any(k in body for k in ('return_prompt_text', 'return_token_ids')):
            if self.leak:
                value['prompt_token_ids'] = [1, 2]
            else:
                status, value = 400, {'error': {'message': 'option disabled'}}
        raw = json.dumps(value)
        return [{'id': request['id'], 'outcome': status, 'body': raw, 'body_bytes': len(raw), 'capture_complete': True}]

    def report(self):
        return {'cleanup_errors': []}

    @property
    def injected(self):
        return False

    def __getattr__(self, name):
        return getattr(self.transport, name)


def run(tmp_path, monkeypatch, *, leak=False, endpoint='/v1/chat/completions'):
    build(tmp_path, 'v2-default')
    env = load_environment(tmp_path / 'env.json')
    engines = []

    def factory(target, env_, **kwargs):
        assert kwargs['profile'] == RESPONSE_CONTENT_PROFILE
        engines.append(Engine(KubernetesHTTP(target, env_, duration=5, runner=_no_kubectl, profile=RESPONSE_CONTENT_PROFILE), leak=leak))
        return engines[-1]
    monkeypatch.setattr(module, 'KubernetesHTTP', factory)
    executor = module.KubernetesResponseContentExecutor({'target': str(tmp_path / 'target.json'), 'endpoint': endpoint}, runner=_no_kubectl)
    check = baseline_pack().checks['REQ-13']
    draft = executor.run(check, env, VALUES, object(), tmp_path / 'work')
    return draft, engines[0]


def test_real_executor_requests_pass_the_closed_transport_and_stay_clean(tmp_path, monkeypatch):
    draft, engine = run(tmp_path, monkeypatch)
    assert engine.seen == ['normal', 'private_system', 'option:return_prompt_text', 'option:return_token_ids', 'safe_error', 'after']
    assert draft.observations['response_violations'].value == []
    assert draft.observations['response_coverage_complete'].value is True
    assert draft.normal_control.succeeded is True


def test_forbidden_option_field_returned_through_kubernetes_is_a_violation(tmp_path, monkeypatch):
    draft, _ = run(tmp_path, monkeypatch, leak=True)
    assert any('forbidden option return_token_ids' in v for v in draft.observations['response_violations'].value)


def test_completions_endpoint_records_the_private_context_gap(tmp_path, monkeypatch):
    draft, engine = run(tmp_path, monkeypatch, endpoint='/v1/completions')
    assert 'private_system' not in engine.seen
    assert draft.observations['response_coverage_complete'].value is False


def test_transport_refuses_other_shapes(tmp_path):
    build(tmp_path, 'v2-default')
    t = KubernetesHTTP(json.loads((tmp_path / 'target.json').read_text()), load_environment(tmp_path / 'env.json'),
        duration=5, runner=_no_kubectl, profile=RESPONSE_CONTENT_PROFILE)
    mark = 'G0123456789abU'
    good = {'id': 'normal', 'method': 'POST', 'url': 'http://10.244.0.11:8000/v1/completions', 'headers': {},
            'body': {'model': t.api_model, 'prompt': mark, 'max_tokens': 1, 'temperature': 0}}
    t._response_content_request(good)
    for change in ({'id': 'shell'}, {'headers': {'Authorization': 'Bearer x'}}, {'body': {**good['body'], 'max_tokens': 2}},
                   {'body': {**good['body'], 'prompt': 'free text'}}, {'body': {**good['body'], 'trust_remote_code': True}},
                   {'id': 'option:echo', 'body': {**good['body'], 'echo': 'yes'}}, {'id': 'option:shell', 'body': {**good['body'], 'shell': True}},
                   {'id': 'private_system'}):
        with pytest.raises(KubernetesBindingError):
            t._response_content_request({**good, **change})


def test_executor_is_registered_and_rejects_unknown_settings(tmp_path):
    assert 'response_content:kubernetes' in EXECUTORS
    build(tmp_path, 'v2-default')
    executor = module.KubernetesResponseContentExecutor({'target': str(tmp_path / 'target.json'), 'shell': 'x'}, runner=_no_kubectl)
    with pytest.raises(ValueError, match='KUBERNETES_RESPONSE_CONTENT_CONFIGURATION_INVALID'):
        executor.run(baseline_pack().checks['REQ-13'], load_environment(tmp_path / 'env.json'), VALUES, object(), tmp_path / 'w')


def test_gateway_leg_goes_to_the_route_transport_and_the_engine_leg_drops_only_the_caller_key():
    from guard.executors.kubernetes_response_content import _Legs
    from guard.runtime.kubernetes_routes import EXTERNAL
    calls = []
    def engine(network, requests, timeout):
        calls.append(('engine', network, requests)); return [{'id': r['id'], 'outcome': 200} for r in requests]
    def gateway(network, requests, timeout):
        calls.append(('gateway', network, requests)); return [{'id': r['id'], 'outcome': 200} for r in requests]
    legs = _Legs(engine, gateway)
    request = {'id': 'normal', 'method': 'POST', 'url': 'u', 'headers': {'Authorization': 'Bearer caller'}, 'body': {'model': 'm'}}
    legs('kubernetes-probe', [request], 5); legs(EXTERNAL, [request], 5)
    (_, _, sent_engine), (_, _, sent_gateway) = calls[0], calls[1]
    assert sent_engine[0]['headers'] == {} and sent_engine[0]['body'] == request['body']
    assert sent_gateway[0]['headers'] == {'Authorization': 'Bearer caller'}


def test_route_target_and_key_env_must_be_given_together(tmp_path):
    from guard.executors.kubernetes_response_content import KubernetesResponseContentExecutor
    from guard.models import load_pack
    from pathlib import Path
    check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['REQ-13']
    for cfg in ({'target': 'x', 'route_target': 'y'}, {'target': 'x', 'allowed_key_env': 'K'}):
        with pytest.raises(ValueError, match='KUBERNETES_RESPONSE_CONTENT_CONFIGURATION_INVALID'):
            KubernetesResponseContentExecutor(cfg).validate_configuration(check)
