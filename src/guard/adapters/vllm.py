"""vLLM 엔진 어댑터 (SPEC §7.1). 엔진 고유 지식은 여기에만 둔다.

이번 단계(5a)에서는 실제 vLLM 컨테이너를 띄우지 않는다. HTTP·Docker 호출은 주입식이라 단위 테스트는 가짜로 한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from guard.models import DataOrigin, Environment
from guard.models.objects import FactValue
from guard.runtime.docker import Docker, Mount

from .base import CommonOption, ContainerHandle, EngineEvent, EngineIdentity, LaunchSpec, ProbeRequest, Route

DEFAULT_MODEL = "facebook/opt-125m"
CONTAINER_PORT = 8000
MODEL_MOUNT = "/models"

# 접근 권한 점검에서 제외하는 정보성 경로 (DECISIONS 5a)
# 경로 종류. 점검은 CheckDef.route_scope.include_kinds로 대상 종류를 고른다.
LIVENESS_ROUTES = frozenset({"/health", "/ping", "/version"})
DOCS_ROUTES = frozenset({"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"})
DIAGNOSTIC_ROUTES = frozenset({"/metrics", "/load", "/kv_event_sources"})  # /load·/kv_event_sources: 5b 실측 후 승인
INFO_ROUTES = LIVENESS_ROUTES | DOCS_ROUTES | DIAGNOSTIC_ROUTES  # REQ-01이 제외하던 정보성 경로 전체
# 경로 변수({response_id} 등)를 채우는 존재하지 않는 고정 ID
PATH_PARAM_PLACEHOLDER = "guard-probe-nonexistent-id"
_PATH_PARAM = re.compile(r"\{[^/{}]+\}")
# 엔진 상태를 바꾸는 관리 경로: 직통 요청을 기본으로 보내지 않는다
MUTATING_ADMIN_ROUTES = frozenset({
    "/sleep", "/wake_up", "/reset_prefix_cache", "/reset_mm_cache", "/load_lora_adapter", "/unload_lora_adapter",
    "/collective_rpc", "/scale_elastic_ep", "/start_profile", "/stop_profile", "/pause", "/resume",
    "/update_weights", "/init_weight_transfer",
})
# 상태를 바꾸는 사용자(추론 API) 경로: 종류는 inference, 상태 변경 표시는 유지(보내지 않음)
MUTATING_USER_ROUTES = frozenset({
    "/v1/responses/{response_id}/cancel",  # openai/responses/api_router.py — 사용자 응답 객체 취소 (2026-10-19 사람 결정)
    "/v1/responses",  # 서버 설정(VLLM_ENABLE_RESPONSES_API_STORE)에 따라 저장·background 작업 생성 — ROUTE_REVIEWS 참고
})

# 상태 변경 여부를 대상 이미지 소스로 확인한 기록: (엔진 버전, 메서드, 경로) → 판단·근거. 분류 자체는 위 집합이 정한다.
ROUTE_REVIEWS: dict[tuple[str, str, str], dict] = {
    ("0.31.0", "POST", "/v1/chat/completions/batch"): {
        "mutating": False,
        "source": "vllm/vllm-openai-cpu@sha256:495c84fe…(v0.31.0) openai/chat_completion/api_router.py "
                  "(sha256 77f858fe0079…) L84-108; chat_completion/batch_serving.py (sha256 0daa49681783…) L114-160",
        "note": "대화 N개를 한 요청 안에서 동기 생성 후 반환. batch_serving.py 전체에 store·save·persist·write·queue·"
                "background 경로 없음 → 상태 변경 없음(추론과 같음)",
    },
    ("0.31.0", "POST", "/v1/responses"): {
        "mutating": True,
        "source": "openai/responses/protocol.py (sha256 5fa24e326726…) L210 store 기본 True, L167 background; "
                  "responses/serving.py (sha256 c70158037… ) L155-193 메모리 저장소·background_tasks, L337-344 "
                  "VLLM_ENABLE_RESPONSES_API_STORE 꺼짐이면 store 무시, L513-528 저장",
        "note": "대상 서버 환경변수에 따라 응답·메시지·이벤트를 메모리에 계속 저장하고 background 작업을 만든다. "
                "실행기는 서버 설정을 알 수 없으므로 상태 변경으로 표시(보내지 않음). 안전 프로브 없음",
    },
}
READONLY_ADMIN_ROUTES = frozenset({"/is_sleeping", "/server_info", "/is_scaling_elastic_ep", "/get_world_size"})

# 검증된 안전 프로브: (엔진 버전, 메서드, 경로) → 요청. 인가 없이 핸들러에 닿으면 expected_handler_reached 응답이 나오고
# 엔진 상태는 바뀌지 않음을 대상 이미지의 소스로 확인한 것만 둔다(DECISIONS 2026-10-19 REQ-03).
_JSON = {"Content-Type": "application/json"}
SAFE_PROBES: dict[tuple[str, str, str], ProbeRequest] = {
    ("0.31.0", "POST", "/scale_elastic_ep"): ProbeRequest(
        "POST", "/scale_elastic_ep", body={}, headers=_JSON,
        expected_handler_reached={"status": 400, "body_contains": "new_data_parallel_size is required"},
        basis="safe_probe",
        source="vllm/vllm-openai-cpu@sha256:495c84fe…(v0.31.0) vllm/entrypoints/serve/elastic_ep/api_router.py "
               "(sha256 2d6805874a86…) L37-49: body {} → new_data_parallel_size None → HTTPException 400, "
               "client.scale_elastic_ep(L64) 미도달; 선행 Depends(validate_json_request)는 Content-Type만 검사",
    ),
    ("0.31.0", "POST", "/is_scaling_elastic_ep"): ProbeRequest(
        "POST", "/is_scaling_elastic_ep", body={}, headers=_JSON,
        expected_handler_reached={"status": 200, "body_contains": "is_scaling_elastic_ep"},
        basis="safe_probe",
        source="같은 파일 L83-85: 전역 플래그 읽기만(get_scaling_elastic_ep), 상태 변경 없음",
    ),
}

# 공통 옵션 → vLLM 고유 옵션
OPTION_FLAGS = {
    CommonOption.REMOTE_CODE_EXECUTION: "--trust-remote-code",
    CommonOption.API_KEY_REQUIRED: "--api-key",  # 값은 VLLM_API_KEY 환경변수로(명령 인자에 남기지 않음)
}
PROBE_PROMPT = "ping"

HttpGet = Callable[[str, float], tuple[int | str, str]]  # url, timeout → (상태코드 | 분류 문자열, 본문)


@dataclass(frozen=True)
class LaunchPlan:
    image: str
    argv: list[str]
    env: dict[str, str]
    secret_env_names: list[str]
    mounts: list[Mount]
    container_port: int = CONTAINER_PORT
    gpus: bool = False  # True면 docker --gpus all
    shm_size: str | None = None


@dataclass
class VllmAdapter:
    http_get: HttpGet | None = None
    docker: Docker | None = None
    base_url: str | None = None
    launch_spec: LaunchSpec | None = None
    name: str = field(default="vllm", init=False)
    version: str = field(default="0.1.0", init=False)

    # --- 기동 ------------------------------------------------------------------

    def start_command(self, spec: LaunchSpec) -> list[str]:
        revision = spec.options.get("revision")
        if not revision:
            raise ValueError("model revision must be pinned (options['revision'])")
        unknown = set(spec.options) - {"revision", "models_dir", "device", "cpu_kv_cache_gib", *(o.value for o in CommonOption)}
        if unknown:
            raise ValueError(f"unknown launch options: {sorted(unknown)}")
        argv = [
            "--model", spec.model, "--revision", str(revision), "--served-model-name", spec.model,
            "--host", "0.0.0.0", "--port", str(spec.port),
        ]
        if spec.options.get(CommonOption.REMOTE_CODE_EXECUTION.value):
            argv.append(OPTION_FLAGS[CommonOption.REMOTE_CODE_EXECUTION])
        return argv

    def launch_plan(self, spec: LaunchSpec) -> LaunchPlan:
        """오프라인·읽기 전용 모델 마운트. 이미지는 digest로 고정해 받는다."""
        if not spec.image:
            raise ValueError("image (pinned by digest) is required")
        models_dir = spec.options.get("models_dir")
        if not models_dir:
            raise ValueError("options['models_dir'] (pre-downloaded HF cache) is required for offline start")
        device = spec.options.get("device", "gpu")
        if device not in ("gpu", "cpu"):
            raise ValueError(f"device must be gpu or cpu, got {device!r}")
        secret = ["VLLM_API_KEY"] if spec.options.get(CommonOption.API_KEY_REQUIRED.value) else []
        env = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HOME": MODEL_MOUNT}
        if device == "cpu":
            # CPU 빌드: KV cache 메모리(GiB). 작은 모델·작은 VM에 맞춘 값
            env["VLLM_CPU_KVCACHE_SPACE"] = str(spec.options.get("cpu_kv_cache_gib", 1))
        return LaunchPlan(
            image=spec.image,
            argv=self.start_command(spec),
            env=env,
            secret_env_names=secret,
            mounts=[Mount(Path(models_dir), MODEL_MOUNT, read_only=True)],
            gpus=device == "gpu",
            shm_size="1g",
        )

    # --- 경로 ------------------------------------------------------------------

    def routes_from_openapi(self, doc: dict) -> list[Route]:
        routes = []
        for path, methods in sorted((doc.get("paths") or {}).items()):
            for method in sorted(m.upper() for m in (methods or {}) if m.lower() in {"get", "post", "put", "delete", "patch"}):
                if path in LIVENESS_ROUTES:
                    kind = "liveness"
                elif path in DOCS_ROUTES:
                    kind = "docs"
                elif path in DIAGNOSTIC_ROUTES:
                    kind = "diagnostic"
                elif path in MUTATING_ADMIN_ROUTES or path in READONLY_ADMIN_ROUTES:
                    kind = "admin"
                else:
                    kind = "inference"
                routes.append(Route(method=method, path=path, inference=kind == "inference", kind=kind,
                                    mutating=path in MUTATING_ADMIN_ROUTES or path in MUTATING_USER_ROUTES))
        return routes

    def public_routes(self, env: Environment) -> list[Route]:
        if not (self.http_get and self.base_url):
            raise RuntimeError("public_routes needs http_get and base_url")
        status, body = self.http_get(f"{self.base_url}/openapi.json", 5.0)
        if status != 200:
            raise RuntimeError(f"/openapi.json returned {status}")
        return self.routes_from_openapi(json.loads(body))

    def probe_request(self, route: Route, model: str | None, engine_version: str | None = None) -> ProbeRequest | None:
        safe = SAFE_PROBES.get((engine_version, route.method, route.path))
        if safe is not None:
            return safe
        if route.mutating:
            return None  # 검증된 안전 프로브가 없는 상태 변경 경로는 보내지 않는다
        pr = self._probe_request(route, model or DEFAULT_MODEL)
        # 경로 변수는 존재하지 않는 고정 ID로 채운다(실제 객체에 닿지 않음)
        return ProbeRequest(pr.method, _PATH_PARAM.sub(PATH_PARAM_PLACEHOLDER, pr.path), pr.body)

    def _probe_request(self, route: Route, model: str) -> ProbeRequest:
        if route.method == "GET":
            return ProbeRequest("GET", route.path)
        if route.path in ("/v1/completions", "/invocations"):
            return ProbeRequest("POST", route.path, {"model": model, "prompt": PROBE_PROMPT, "max_tokens": 1})
        if route.path == "/v1/chat/completions":
            return ProbeRequest("POST", route.path,
                                {"model": model, "messages": [{"role": "user", "content": PROBE_PROMPT}], "max_tokens": 1})
        if route.path in ("/tokenize", "/v1/tokenize"):
            return ProbeRequest("POST", route.path, {"model": model, "prompt": PROBE_PROMPT})
        if route.path in ("/detokenize", "/v1/detokenize"):
            return ProbeRequest("POST", route.path, {"model": model, "tokens": [1]})
        if route.path in ("/v1/embeddings", "/pooling", "/classify"):
            return ProbeRequest("POST", route.path, {"model": model, "input": PROBE_PROMPT})
        # 모양을 모르는 POST: 빈 본문(검증 오류 4xx가 나오면 판정은 UNKNOWN)
        return ProbeRequest(route.method, route.path, {})

    # --- 식별·옵션·로그·준비 ------------------------------------------------------

    def detect_version(self, handle: ContainerHandle) -> EngineIdentity:
        version = None
        if self.http_get:
            status, body = self.http_get(f"{handle.base_url}/version", 5.0)
            if status == 200:
                version = json.loads(body).get("version")
        digest = None
        if self.docker:
            digest = self.docker.image_digest(self.docker.container_image(handle.id, timeout=30), timeout=30)
        return EngineIdentity(name="vllm", version=version, image_digest=digest)

    def read_option(self, name: CommonOption, env: Environment) -> FactValue:
        """기동 명세에서 공통 옵션을 읽는다. 명세가 없으면 모른다(null). 실제 효과의 관측이 아니므로 DECLARED."""
        if self.launch_spec is None or name.value not in self.launch_spec.options:
            return FactValue(value=None)
        return FactValue(value=bool(self.launch_spec.options[name.value]), origin=DataOrigin.DECLARED,
                         source=f"launch_spec:{OPTION_FLAGS[name]}")

    _UVICORN = re.compile(r"Uvicorn running on (\S+)")
    _VERSION = re.compile(r"vLLM API server version (\S+)")
    _ACCESS = re.compile(r'"(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD) (\S+) HTTP/[\d.]+" (\d{3})')
    _ERROR = re.compile(r"\bERROR\b")

    def parse_log(self, line: str) -> EngineEvent | None:
        if m := self._ACCESS.search(line):
            return EngineEvent("request", {"method": m.group(1), "path": m.group(2), "status": int(m.group(3))})
        if m := self._UVICORN.search(line):
            return EngineEvent("server_started", {"url": m.group(1)})
        if m := self._VERSION.search(line):
            return EngineEvent("version", {"version": m.group(1)})
        if self._ERROR.search(line):
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
