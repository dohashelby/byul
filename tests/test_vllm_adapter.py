"""vLLM 어댑터: 가짜 HTTP·Docker로 검증 (실제 vLLM 기동 없음)."""

import json
from pathlib import Path

import pytest

from guard.adapters.base import CommonOption, ContainerHandle, EngineAdapter, LaunchSpec, Route
from guard.adapters.vllm import DEFAULT_MODEL, VllmAdapter
from guard.runtime.docker import CmdResult, Docker
from tests.builders import environment

OPENAPI = {"paths": {
    "/health": {"get": {}}, "/version": {"get": {}}, "/metrics": {"get": {}}, "/openapi.json": {"get": {}},
    "/v1/models": {"get": {}}, "/v1/completions": {"post": {}}, "/v1/chat/completions": {"post": {}},
    "/invocations": {"post": {}}, "/tokenize": {"post": {}}, "/sleep": {"post": {}}, "/is_sleeping": {"get": {}},
    "/reset_prefix_cache": {"post": {}}, "/load": {"get": {}}, "/kv_event_sources": {"get": {}},
    "/v1/responses/{response_id}": {"get": {}}, "/v1/responses/{response_id}/cancel": {"post": {}},
}}


def spec(**opts):
    return LaunchSpec(model=DEFAULT_MODEL, port=8000, image="vllm/vllm-openai@sha256:abc",
                      options={"revision": "a" * 40, "models_dir": Path("/srv/models"), **opts})


def test_is_engine_adapter():
    assert isinstance(VllmAdapter(), EngineAdapter)


def test_start_command_pins_model_revision():
    argv = VllmAdapter().start_command(spec())
    assert argv[:4] == ["--model", "facebook/opt-125m", "--revision", "a" * 40]
    assert "--trust-remote-code" not in argv
    assert "--trust-remote-code" in VllmAdapter().start_command(spec(REMOTE_CODE_EXECUTION=True))
    with pytest.raises(ValueError):
        VllmAdapter().start_command(LaunchSpec(model=DEFAULT_MODEL, port=8000, options={}))
    with pytest.raises(ValueError):
        VllmAdapter().start_command(spec(bogus=1))


def test_launch_plan_offline_readonly_and_key_not_in_argv():
    plan = VllmAdapter().launch_plan(spec(API_KEY_REQUIRED=True))
    assert plan.env["HF_HUB_OFFLINE"] == "1" and plan.env["TRANSFORMERS_OFFLINE"] == "1"
    assert plan.mounts[0].read_only and plan.mounts[0].target == "/models"
    assert plan.secret_env_names == ["VLLM_API_KEY"]
    assert "--api-key" not in plan.argv  # 키 값은 환경변수로만
    with pytest.raises(ValueError):
        VllmAdapter().launch_plan(LaunchSpec(model=DEFAULT_MODEL, port=8000, options={"revision": "x"}))


def test_launch_plan_device():
    gpu = VllmAdapter().launch_plan(spec())
    assert gpu.gpus and "VLLM_CPU_KVCACHE_SPACE" not in gpu.env
    cpu = VllmAdapter().launch_plan(spec(device="cpu", cpu_kv_cache_gib=2))
    assert not cpu.gpus and cpu.env["VLLM_CPU_KVCACHE_SPACE"] == "2"
    with pytest.raises(ValueError):
        VllmAdapter().launch_plan(spec(device="tpu"))


def test_routes_from_openapi_classification():
    routes = {(r.method, r.path): r for r in VllmAdapter().routes_from_openapi(OPENAPI)}
    assert routes[("GET", "/health")].kind == "info"
    assert routes[("GET", "/metrics")].kind == "info"
    assert routes[("POST", "/v1/completions")].kind == "inference"
    assert routes[("POST", "/sleep")].kind == "admin" and routes[("POST", "/sleep")].mutating
    assert routes[("GET", "/is_sleeping")].kind == "admin" and not routes[("GET", "/is_sleeping")].mutating
    # 5b 실측 후 승인된 분류
    assert routes[("GET", "/load")].kind == "info"
    assert routes[("GET", "/kv_event_sources")].kind == "info"
    assert routes[("POST", "/v1/responses/{response_id}/cancel")].mutating
    assert not routes[("GET", "/v1/responses/{response_id}")].mutating


def test_public_routes_reads_openapi():
    calls = []

    def get(url, timeout):
        calls.append(url)
        return 200, json.dumps(OPENAPI)

    routes = VllmAdapter(http_get=get, base_url="http://e:8000").public_routes(environment())
    assert calls == ["http://e:8000/openapi.json"] and len(routes) == 16
    with pytest.raises(RuntimeError):
        VllmAdapter(http_get=lambda u, t: (500, ""), base_url="http://e").public_routes(environment())


def test_probe_requests_are_harmless():
    a = VllmAdapter()
    r = a.probe_request(Route("POST", "/v1/completions"), None)
    assert r.body == {"model": DEFAULT_MODEL, "prompt": "ping", "max_tokens": 1}
    assert a.probe_request(Route("POST", "/v1/chat/completions"), "m").body["max_tokens"] == 1
    assert a.probe_request(Route("GET", "/v1/models"), None).body is None
    assert a.probe_request(Route("POST", "/sleep", kind="admin", mutating=True), None) is None


def test_path_variables_replaced_with_nonexistent_fixed_id():
    from guard.adapters.vllm import PATH_PARAM_PLACEHOLDER

    r = VllmAdapter().probe_request(Route("GET", "/v1/responses/{response_id}"), None)
    assert r.path == f"/v1/responses/{PATH_PARAM_PLACEHOLDER}" and "{" not in r.path
    assert VllmAdapter().probe_request(Route("POST", "/v1/responses/{response_id}/cancel", kind="admin", mutating=True), None) is None
    assert VllmAdapter().probe_request(Route("POST", "/a/{x}/b/{y}"), None).path == f"/a/{PATH_PARAM_PLACEHOLDER}/b/{PATH_PARAM_PLACEHOLDER}"


def test_detect_version_and_ready():
    def get(url, timeout):
        if url.endswith("/version"):
            return 200, '{"version": "0.31.0"}'
        if url.endswith("/health"):
            return 200, ""
        return 404, ""

    responses = {"inspect": "vllm/vllm-openai:v0.31.0\n", "image": '["vllm/vllm-openai@sha256:abc"]'}

    def runner(argv, *, input, timeout, env_extra):
        return CmdResult(0, responses["inspect" if argv[1] == "inspect" else "image"], "")

    a = VllmAdapter(http_get=get, docker=Docker("t1abc", runner=runner))
    ident = a.detect_version(ContainerHandle(id="c1", base_url="http://e:8000"))
    assert (ident.name, ident.version, ident.image_digest) == ("vllm", "0.31.0", "vllm/vllm-openai@sha256:abc")
    assert a.ready(ContainerHandle(id="c1", base_url="http://e:8000"))

    def boom(url, timeout):
        raise ConnectionRefusedError

    assert not VllmAdapter(http_get=boom).ready(ContainerHandle(id="c1", base_url="http://e"))


def test_read_option():
    env = environment()
    assert VllmAdapter().read_option(CommonOption.REMOTE_CODE_EXECUTION, env).value is None  # 모름
    fv = VllmAdapter(launch_spec=spec(REMOTE_CODE_EXECUTION=False)).read_option(CommonOption.REMOTE_CODE_EXECUTION, env)
    assert fv.value is False and fv.origin == "DECLARED" and "--trust-remote-code" in fv.source


@pytest.mark.parametrize(
    "line, event",
    [
        ('INFO:     172.17.0.1:5555 - "POST /v1/completions HTTP/1.1" 200 OK', ("request", {"method": "POST", "path": "/v1/completions", "status": 200})),
        ("INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)", ("server_started", {"url": "http://0.0.0.0:8000"})),
        ("INFO 10-19 12:00:00 api_server.py:1 vLLM API server version 0.31.0", ("version", {"version": "0.31.0"})),
        ("ERROR 10-19 engine died", ("error", {"message": "ERROR 10-19 engine died"})),
        ("DEBUG nothing", None),
    ],
)
def test_parse_log(line, event):
    ev = VllmAdapter().parse_log(line)
    assert (ev.type, ev.attrs) == event if event else ev is None
