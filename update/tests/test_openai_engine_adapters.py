"""SGLang·TensorRT-LLM 어댑터 경로 분류와 안전 프로브. 실제 엔진 없음."""
import json

import pytest

from guard.adapters.base import CommonOption, ContainerHandle, LaunchSpec, Route
from guard.adapters.openai_engine import SGLangAdapter, TensorRTAdapter
from guard.executors import registry

OPENAPI = {'paths': {'/health': {'get': {}}, '/metrics': {'get': {}}, '/openapi.json': {'get': {}},
    '/v1/chat/completions': {'post': {}}, '/flush_cache': {'post': {}}, '/get_model_info': {'get': {}}, '/custom': {'post': {}}}}


@pytest.mark.parametrize('name,kind', [('sglang', SGLangAdapter), ('tensorrt-llm', TensorRTAdapter)])
def test_registered_by_engine_name(name, kind):
    assert registry.ADAPTERS[name] is kind and kind().name == name


def test_sglang_mutating_route_gets_no_probe():
    routes = {r.path: r for r in SGLangAdapter().routes_from_openapi(OPENAPI)}
    assert routes['/flush_cache'].mutating and routes['/flush_cache'].kind == 'admin'
    assert routes['/get_model_info'].kind == 'admin' and not routes['/get_model_info'].mutating
    assert routes['/health'].kind == 'liveness' and routes['/metrics'].kind == 'diagnostic'
    assert SGLangAdapter().probe_request(routes['/flush_cache'], 'm') is None


def test_unknown_post_shape_is_not_sent():
    assert TensorRTAdapter().probe_request(Route('POST', '/custom'), 'm') is None


def test_chat_probe_is_one_token():
    probe = TensorRTAdapter().probe_request(Route('POST', '/v1/chat/completions', inference=True), 'm')
    assert probe.body['max_tokens'] == 1 and probe.body['model'] == 'm'


def test_path_parameter_uses_a_nonexistent_id():
    assert SGLangAdapter().probe_request(Route('GET', '/v1/models/{model_id}'), 'm').path == '/v1/models/guard-probe-nonexistent-id'
    assert SGLangAdapter().probe_request(Route('GET', '/v1/items/{id}'), 'm') is None


def test_start_commands_bind_the_requested_port():
    spec = LaunchSpec(model='m', port=9000)
    assert '9000' in SGLangAdapter().start_command(spec) and TensorRTAdapter().start_command(spec)[:2] == ['trtllm-serve', 'm']


def test_version_and_ready_use_http():
    calls = []
    def http_get(url, timeout):
        calls.append(url)
        return 200, json.dumps({'version': '0.5'})
    adapter = SGLangAdapter(http_get=http_get)
    assert adapter.detect_version(ContainerHandle('c', 'http://x')).version == '0.5' and adapter.ready(ContainerHandle('c', 'http://x'))
    assert calls[0].endswith('/get_server_info')


def test_missing_launch_option_stays_unknown():
    assert TensorRTAdapter().read_option(list(CommonOption)[0], None).value is None


def test_log_lines_parse_to_events():
    adapter = SGLangAdapter()
    assert adapter.parse_log('"POST /v1/completions HTTP/1.1" 200').attrs['status'] == 200
    assert adapter.parse_log('INFO quiet') is None
