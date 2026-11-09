"""OpenAI 호환 서빙엔진 어댑터. 외부 HTTP 점검 범위만 지원한다.

엔진 내부 센서·Kubernetes 수집기는 vLLM 고정 소스 전용이다. 이 어댑터의 경로 표는
공개 문서 기준이며 실제 엔진 실측 전이다. 모르는 POST 경로는 보내지 않는다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import re

from guard.models import DataOrigin, Environment
from guard.models.objects import FactValue

from .base import CommonOption, ContainerHandle, EngineEvent, EngineIdentity, LaunchSpec, ProbeRequest, Route

HttpGet = Callable[[str, float], tuple[int | str, str]]
PROBE_PROMPT = "ping"
LIVENESS_ROUTES = frozenset({"/health", "/health_generate", "/ping"})
DOCS_ROUTES = frozenset({"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"})
DIAGNOSTIC_ROUTES = frozenset({"/metrics"})
_ACCESS = re.compile(r'"(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD) (\S+) HTTP/[\d.]+" (\d{3})')
_UVICORN = re.compile(r"Uvicorn running on (\S+)")
_ERROR = re.compile(r"\bERROR\b")
_PATH_PARAM = re.compile(r"\{[^/{}]+\}")


@dataclass
class OpenAIEngineAdapter:
    http_get: HttpGet | None = None
    base_url: str | None = None
    launch_spec: LaunchSpec | None = None
    name: str = field(default="", init=False)
    version: str = field(default="0.2.0", init=False)
    admin_mutating = frozenset()
    admin_readonly = frozenset()
    version_path = "/version"
    version_key = "version"

    def start_command(self, spec: LaunchSpec) -> list[str]:
        raise NotImplementedError

    def routes_from_openapi(self, doc: dict) -> list[Route]:
        routes = []
        for path, methods in sorted((doc.get("paths") or {}).items()):
            for method in sorted(m.upper() for m in (methods or {}) if m.lower() in {"get", "post", "put", "delete", "patch"}):
                kind = ("liveness" if path in LIVENESS_ROUTES else "docs" if path in DOCS_ROUTES
                    else "diagnostic" if path in DIAGNOSTIC_ROUTES
                    else "admin" if path in self.admin_mutating | self.admin_readonly else "inference")
                routes.append(Route(method=method, path=path, inference=kind == "inference", kind=kind,
                                    mutating=path in self.admin_mutating))
        return routes

    def public_routes(self, env: Environment) -> list[Route]:
        if not (self.http_get and self.base_url):
            raise RuntimeError("public_routes needs http_get and base_url")
        status, body = self.http_get(f"{self.base_url}/openapi.json", 5.0)
        if status != 200:
            raise RuntimeError(f"/openapi.json returned {status}")
        return self.routes_from_openapi(json.loads(body))

    def probe_request(self, route: Route, model: str | None, engine_version: str | None = None,
                      engine_image_digest: str | None = None) -> ProbeRequest | None:
        if route.mutating:
            return None
        path = _PATH_PARAM.sub("guard-probe-nonexistent-id", route.path)
        if route.method == "GET":
            known = LIVENESS_ROUTES | DOCS_ROUTES | DIAGNOSTIC_ROUTES | {'/v1/models', '/v1/models/{model}', '/get_model_info', '/get_server_info', '/get_load'}
            if _PATH_PARAM.sub('{id}', route.path) in {_PATH_PARAM.sub('{id}', p) for p in known}:
                return ProbeRequest("GET", path)
            return None  # Discovery does not authorize probing an unknown action.
        if route.method != 'POST':
            return None
        if route.path == "/v1/completions":
            return ProbeRequest("POST", path, {"model": model, "prompt": PROBE_PROMPT, "max_tokens": 1})
        if route.path == "/v1/chat/completions":
            return ProbeRequest("POST", path, {"model": model, "max_tokens": 1,
                "messages": [{"role": "user", "content": PROBE_PROMPT}]})
        return None

    def input_rejection_proof(self, **kwargs) -> dict:
        return {"verified": False, "reason": "ENGINE_INPUT_REJECTION_NOT_REVIEWED"}

    def detect_version(self, handle: ContainerHandle) -> EngineIdentity:
        version = None
        if self.http_get:
            status, body = self.http_get(f"{handle.base_url}{self.version_path}", 5.0)
            if status == 200:
                version = json.loads(body).get(self.version_key)
        return EngineIdentity(name=self.name, version=version)

    def read_option(self, name: CommonOption, env: Environment) -> FactValue:
        if self.launch_spec is None or name.value not in self.launch_spec.options:
            return FactValue(value=None)
        return FactValue(value=bool(self.launch_spec.options[name.value]), origin=DataOrigin.DECLARED,
                         source=f"launch_spec:{name.value}")

    def parse_log(self, line: str) -> EngineEvent | None:
        if m := _ACCESS.search(line):
            return EngineEvent("request", {"method": m.group(1), "path": m.group(2), "status": int(m.group(3))})
        if m := _UVICORN.search(line):
            return EngineEvent("server_started", {"url": m.group(1)})
        if _ERROR.search(line):
            return EngineEvent("error", {"message": line.strip()})
        return None

    def ready(self, handle: ContainerHandle) -> bool:
        if not self.http_get:
            return False
        try:
            status, _ = self.http_get(f"{handle.base_url}/health", 2.0)
        except Exception:  # noqa: BLE001 - 준비 안 됨
            return False
        return status == 200


@dataclass
class SGLangAdapter(OpenAIEngineAdapter):
    name: str = field(default="sglang", init=False)
    admin_mutating = frozenset({"/flush_cache", "/start_profile", "/stop_profile", "/start_expert_distribution_record",
        "/stop_expert_distribution_record", "/dump_expert_distribution_record", "/update_weights_from_disk",
        "/update_weights_from_tensor", "/update_weights_from_distributed", "/init_weights_update_group",
        "/release_memory_occupation", "/resume_memory_occupation", "/set_internal_state", "/abort_request",
        "/configure_logging", "/slow_down", "/load_lora_adapter", "/unload_lora_adapter"})
    admin_readonly = frozenset({"/get_model_info", "/get_server_info", "/get_weights_by_name", "/get_load"})
    version_path = "/get_server_info"

    def start_command(self, spec: LaunchSpec) -> list[str]:
        return ["python3", "-m", "sglang.launch_server", "--model-path", spec.model,
            "--host", "0.0.0.0", "--port", str(spec.port)]


@dataclass
class TensorRTAdapter(OpenAIEngineAdapter):
    name: str = field(default="tensorrt-llm", init=False)
    admin_readonly = frozenset({"/kv_cache_events", "/perf_metrics"})

    def start_command(self, spec: LaunchSpec) -> list[str]:
        return ["trtllm-serve", spec.model, "--host", "0.0.0.0", "--port", str(spec.port)]
