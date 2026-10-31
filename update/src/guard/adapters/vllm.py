"""vLLM 엔진 어댑터 (SPEC §7.1). 엔진 고유 지식은 여기에만 둔다.

이번 단계(5a)에서는 실제 vLLM 컨테이너를 띄우지 않는다. HTTP·Docker 호출은 주입식이라 단위 테스트는 가짜로 한다.
"""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from guard.models import DataOrigin, Environment
from guard.models.objects import FactValue
from guard.runtime.docker import ContainerLimits, Docker, Mount

from .base import CommonOption, ContainerHandle, EngineEvent, EngineIdentity, LaunchSpec, ProbeRequest, Route

DEFAULT_MODEL = "facebook/opt-125m"
CONTAINER_PORT = 8000
MODEL_MOUNT = "/models"
CHAT_TEMPLATE_PROFILE = "guard-text-chat-v1"
CHAT_TEMPLATE_SHA256 = "e5b96cc7595d53305284a40f579747da846f01b566ad571ad2cb67526bdadc2e"
CHAT_TEMPLATE_CONTAINER_PATH = "/guard-assets/chat/guard-text-chat-v1.jinja"
CHAT_TEMPLATE_ASSET = Path(__file__).resolve().parent / "assets" / "chat" / "guard-text-chat-v1.jinja"
CHAT_REVIEWED_IMAGE = "vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b"
# Official v0.31.0 linux/amd64 manifest, distinct from its multi-platform index
# and config digest. Consumer source bytes were inspected in the OCI layers.
REQUEST_BINDING_GPU_IMAGE = "vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b"
REQUEST_BINDING_GPU_032_IMAGE = "vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9"
TYPE_TEMPLATE_PROFILE = "guard-type-boundary-v1"
TYPE_TEMPLATE_SHA256 = "38110f6e668d55f240ecfde0d38f5fecc7c11a742d76823ffd9d0918abb9fe36"
TYPE_TEMPLATE_PATH = "/guard-assets/chat/guard-type-boundary-v1.jinja"
TYPE_TEMPLATE_ASSET = CHAT_TEMPLATE_ASSET.with_name("guard-type-boundary-v1.jinja")

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

# 새 지원은 실제로 검토한 버전·전체 이미지 digest 쌍에만 한정한다.
# 기존 0.31.0 버전 전용 SAFE_PROBES는 과거 호출과 저장 증거의 계약을 보존한다.
SAFE_PROBE_REVIEWED_IMAGES = {
    "0.31.0": "vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b",
    "0.32.0": "vllm/vllm-openai-cpu@sha256:827ef38187f9b52f2bb838695ef815ec5b65f4bf3e4c061b002b2b0a19cb0701",
}
_ELASTIC_EP_ROUTER_SOURCE = (
    "vllm/entrypoints/serve/elastic_ep/api_router.py "
    "(sha256 2d6805874a868eb63747903f5e10331855b7c277d46a4cef3dca7f20a2e631db)"
)


def _reviewed_elastic_ep_probes(version: str, image: str) -> dict[tuple[str, str, str, str], ProbeRequest]:
    source = f"{image} (v{version}) {_ELASTIC_EP_ROUTER_SOURCE}"
    return {
        (version, image, "POST", "/scale_elastic_ep"): ProbeRequest(
            "POST", "/scale_elastic_ep", body={}, headers=_JSON,
            expected_handler_reached={"status": 400, "body_contains": "new_data_parallel_size is required"},
            basis="safe_probe",
            source=source + " L27-49: body {} → new_data_parallel_size None → HTTPException 400; "
                   "engine_client(L62)·client.scale_elastic_ep(L64) 미도달. "
                   "선행 Depends(validate_json_request): vllm/entrypoints/serve/utils/api_utils.py "
                   "(sha256 ffb5e08c2b6cf6d0402c6ba31d0d05e53eb7e9e8c13b29700e84a4bfada420ba) "
                   "L350-356 Content-Type만 검사. HTTPException 응답: "
                   "vllm/entrypoints/serve/exception_handling/handlers/http.py "
                   "(sha256 ae710c07f319f56e105a8d143300b0d1042ab4cdedc437da2c7dd88df21e5a18) "
                   "L16-31 상태 코드와 오류 문구 유지",
        ),
        (version, image, "POST", "/is_scaling_elastic_ep"): ProbeRequest(
            "POST", "/is_scaling_elastic_ep", body={}, headers=_JSON,
            expected_handler_reached={"status": 200, "body_contains": "is_scaling_elastic_ep"},
            basis="safe_probe",
            source=source + " L83-85: get_scaling_elastic_ep() 조회만. "
                   "vllm/entrypoints/serve/elastic_ep/middleware.py "
                   "(sha256 99ff0d6d3767acae73e5daa7d70e1be42e7e373975122e79a41fa761e27cc26c) "
                   "L13-14 전역 플래그 읽기만, L17-19 setter 미호출",
        ),
    }


PINNED_SAFE_PROBES: dict[tuple[str, str, str, str], ProbeRequest] = {
    key: probe
    for version, image in SAFE_PROBE_REVIEWED_IMAGES.items()
    for key, probe in _reviewed_elastic_ep_probes(version, image).items()
}

# REQ-09 response meanings reviewed from the two exact local CPU image sources.
# These are rejection proofs only; neither a 2xx nor a generation-shaped body
# establishes that the submitted invalid input was consumed or caused damage.
_INPUT_REJECTION_REVIEWS = {
    "0.31.0": {
        "image": SAFE_PROBE_REVIEWED_IMAGES["0.31.0"],
        "token": [("vllm/v1/engine/input_processor.py", "d79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b", "397-400,598-626")],
        "length": [("vllm/renderers/params.py", "ad38852674ebc5bbe69aa9df91602847d518382cc2ceb0a25aac1d07be66b504", "485-512,527-544"),
                   ("vllm/renderers/online_renderer.py", "1ada26191425fd9d56c3525949f999826dad9f2c82df94ad586a1cf6f67b089e", "546-631,674-750")],
        "error": ("vllm/entrypoints/serve/exception_handling/error_response.py", "354a0581fbc29e2c3acbebe5e84ff3dbb132ba0af14ceba5363b6cf2fec6d54e", "38-48,89-96"),
        "exception": ("vllm/exceptions.py", "22fe819c7ef10438f740c251d81af204871a04c3aa3eb1fd0e68604dc4c2146f", "28-58"),
    },
    "0.32.0": {
        "image": SAFE_PROBE_REVIEWED_IMAGES["0.32.0"],
        "token": [("vllm/v1/engine/input_processor.py", "97f66659814177d3f7b72b959f09201cbdd1794de21ec1542fe8970f18a1a2b6", "430-433,646-647"),
                  ("vllm/renderers/base.py", "ec2fe84538bb30ad7ed1e10833081c8c389e6a2f8fda1dcef50005b9db75a1a7", "420-455")],
        "length": [("vllm/renderers/params.py", "5113461cd308266727d9ced82900cbe65ae50245a2f3cb16224f6689574570a9", "515-540,555-572")],
        "error": ("vllm/entrypoints/serve/exception_handling/error_response.py", "f83f3571e84fed1a471d090ed5f3a64702af21a36700c1e703c84478e6286fd5", "38-48,93-100"),
        "exception": ("vllm/exceptions.py", "1c70ea415fe05a7be2c8312877894b2b30e96228461d83a2394922d06d006342", "28-58"),
    },
}
_LENGTH_ERROR_031 = re.compile(
    r"This model's maximum context length is (?P<limit>[1-9][0-9]*) tokens\. However, you requested "
    r"(?P<output>[1-9][0-9]*) output tokens and your prompt contains (?P<qualifier>at least )?"
    r"(?P<count>[1-9][0-9]*) input tokens, for a total of (?P<total_qualifier>at least )?"
    r"(?P<total>[1-9][0-9]*) tokens\. Please reduce the length of the input prompt or the "
    r"number of requested output tokens\. \(parameter=input_tokens, value=(?P<value>[1-9][0-9]*)\)"
)
_LENGTH_ERROR_032 = re.compile(
    r"This model's maximum context length is (?P<limit>[1-9][0-9]*) tokens\. However, your prompt "
    r"contains (?P<qualifier>at least )?(?P<count>[1-9][0-9]*) input tokens, which exceeds this "
    r"limit even without any output tokens\. Please reduce the length of the input prompt\. "
    r"\(parameter=input_tokens, value=(?P<value>[1-9][0-9]*)\)"
)


def _input_rejection_proof(*, engine_version, engine_image_digest, case, method, path,
                           request_body, response_status, response_body, context=None) -> dict:
    proof = {"state": "unverified", "reason": "REJECTION_CAUSE_UNVERIFIED", "sources": []}
    review = _INPUT_REJECTION_REVIEWS.get(engine_version)
    if review is None or engine_image_digest != review["image"]:
        return {**proof, "reason": "INPUT_REJECTION_IMAGE_UNREVIEWED"}
    if (method != "POST" or path != "/v1/completions" or type(request_body) is not dict
            or set(request_body) != {"model", "prompt", "max_tokens"}
            or type(request_body["model"]) is not str or not request_body["model"]
            or type(request_body["max_tokens"]) is not int or request_body["max_tokens"] != 1):
        return {**proof, "reason": "INPUT_REJECTION_REQUEST_UNREVIEWED"}
    prompt = request_body["prompt"]
    if type(prompt) is not list or not prompt or any(type(token) is not int or token < 0 for token in prompt):
        return {**proof, "reason": "INPUT_REJECTION_REQUEST_UNREVIEWED"}
    if type(response_status) is not int or response_status != 400 or type(response_body) is not dict or set(response_body) != {"error"}:
        return proof
    error = response_body["error"]
    if (type(error) is not dict or set(error) != {"message", "type", "param", "code"}
            or type(error["code"]) is not int or error["code"] != 400 or error["type"] != "BadRequestError"
            or type(error["message"]) is not str):
        return proof
    matched = False
    if case == "token_id":
        matched = (len(prompt) == 1 and error["param"] is None
                   and error["message"] == f"Token id {prompt[0]} is out of vocabulary")
    elif case == "length" and all(token == 0 for token in prompt) and error["param"] == "input_tokens":
        pattern = _LENGTH_ERROR_031 if engine_version == "0.31.0" else _LENGTH_ERROR_032
        match = pattern.fullmatch(error["message"])
        if match:
            values = match.groupdict()
            limit, count, rejected = (int(values[key]) for key in ("limit", "count", "value"))
            output = int(values["output"]) if engine_version == "0.31.0" else 0
            expected_qualifier = "at least " if count == limit - output + 1 else None
            matched = (count == rejected == len(prompt) and count > limit - output
                       and values["qualifier"] == expected_qualifier)
            if engine_version == "0.31.0":
                matched = (matched and output == request_body["max_tokens"]
                           and int(values["total"]) == count + output
                           and values["total_qualifier"] == expected_qualifier)
    if not matched:
        # In particular, max_tokens=0 can have other clamping causes. Its
        # error alone is not a verified input-length rejection.
        if (engine_version == "0.32.0" and case == "length" and all(token == 0 for token in prompt)
                and error["param"] == "max_tokens"):
            from .vllm_length_cause import ZERO_ERROR, reviewed_zero_budget
            if error["message"] == ZERO_ERROR:
                reviewed = reviewed_zero_budget(context, request_body)
                if reviewed is not None:
                    return reviewed
        return proof
    sources = [*review["token" if case == "token_id" else "length"], review["error"], review["exception"]]
    return {"state": "verified", "reason": "REVIEWED_INPUT_REJECTION",
            "sources": [{"path": path, "sha256": sha, "lines": lines} for path, sha, lines in sources]}

# 공통 옵션 → vLLM 고유 옵션
OPTION_FLAGS = {
    CommonOption.REMOTE_CODE_EXECUTION: "--trust-remote-code",
    CommonOption.API_KEY_REQUIRED: "--api-key",  # 값은 VLLM_API_KEY 환경변수로(명령 인자에 남기지 않음)
}
PROBE_PROMPT = "ping"

HttpGet = Callable[[str, float], tuple[int | str, str]]  # url, timeout → (상태코드 | 분류 문자열, 본문)

CPU_LAUNCH_KEYS = {"dtype", "max_model_len", "max_num_seqs", "max_num_batched_tokens",
                   "kv_cache_memory_bytes", "enforce_eager", "cpu_threads", "resources", "chat_template_profile", "logging_profile"}


def fixed_native_log_profile(profile: str) -> dict:
    """A fixed native logging fixture; no custom dictConfig or Python imports."""
    if type(profile) is not str or profile != "request-info-v1":
        raise ValueError("logging_profile must be request-info-v1")
    return {"profile": profile, "version": "1", "level": "INFO", "color": False,
            "max_log_len": 512, "external_request_id_trust": "untrusted_correlation",
            "logger_sha256": "4e26afd9feab3448f2af529867b00e8bed6a22690defd81a54d2df6b5afdd010",
            "formatter_sha256": "cc2153d8edfd59ed401f0c3f3ae985a7dd678799daad6829a2d33ac6ccb20ea5",
            "environment": {"VLLM_CONFIGURE_LOGGING": "1", "VLLM_LOGGING_LEVEL": "INFO",
                            "VLLM_LOGGING_COLOR": "0", "VLLM_LOGGING_PREFIX": "",
                            "VLLM_LOGGING_STREAM": "ext://sys.stdout"},
            "custom_config_allowed": False,
            "sources": [
                {"path": "vllm/logger.py", "sha256": "4e26afd9feab3448f2af529867b00e8bed6a22690defd81a54d2df6b5afdd010", "lines": "27-33,64-103"},
                {"path": "vllm/logging_utils/formatter.py", "sha256": "cc2153d8edfd59ed401f0c3f3ae985a7dd678799daad6829a2d33ac6ccb20ea5", "lines": "60-73"},
                {"path": "vllm/entrypoints/serve/utils/request_logger.py", "sha256": "e7839742189c6f336f7d9fa3355cd78ccb1b1d63a8a24361b8b13a7f7cd0e83d", "lines": "36-66"}]}


def fixed_chat_template(profile: str) -> tuple[Mount, dict]:
    """Only a shipped, byte-pinned text fixture may enable the chat endpoint.

    This formats messages for bounded API experiments. It does not turn an
    untrained base model into an instruction or system-isolation model.
    """
    if type(profile) is str and profile == TYPE_TEMPLATE_PROFILE:
        if (TYPE_TEMPLATE_ASSET.is_symlink() or not TYPE_TEMPLATE_ASSET.is_file()
                or hashlib.sha256(TYPE_TEMPLATE_ASSET.read_bytes()).hexdigest() != TYPE_TEMPLATE_SHA256):
            raise ValueError("fixed type-boundary template asset integrity differs")
        return Mount(TYPE_TEMPLATE_ASSET, TYPE_TEMPLATE_PATH, read_only=True), {
            "profile": TYPE_TEMPLATE_PROFILE, "version": "1", "sha256": TYPE_TEMPLATE_SHA256,
            "container_path": TYPE_TEMPLATE_PATH, "content_format": "string", "request_overrides": False}
    if type(profile) is not str or profile != CHAT_TEMPLATE_PROFILE:
        raise ValueError(f"chat_template_profile must be {CHAT_TEMPLATE_PROFILE}")
    if not CHAT_TEMPLATE_ASSET.is_file() or CHAT_TEMPLATE_ASSET.is_symlink():
        raise ValueError("fixed chat template asset is missing or is a symlink")
    actual = hashlib.sha256(CHAT_TEMPLATE_ASSET.read_bytes()).hexdigest()
    if actual != CHAT_TEMPLATE_SHA256:
        raise ValueError("fixed chat template asset integrity differs from the shipped profile")
    return Mount(CHAT_TEMPLATE_ASSET, CHAT_TEMPLATE_CONTAINER_PATH, read_only=True), {
        "profile": CHAT_TEMPLATE_PROFILE, "version": "1", "sha256": actual,
        "container_path": CHAT_TEMPLATE_CONTAINER_PATH, "content_format": "string",
        "request_overrides": False,
    }


@dataclass(frozen=True)
class CpuLaunchOptions:
    """A bounded CPU diagnostic profile, separate from enterprise POL values."""

    dtype: str = "float32"
    max_model_len: int = 128
    max_num_seqs: int = 1
    max_num_batched_tokens: int = 128
    kv_cache_memory_bytes: int = 67108864
    enforce_eager: bool = True
    cpu_threads: int = 2

    def __post_init__(self):
        if type(self.dtype) is not str or self.dtype not in {"float32", "float16", "bfloat16"}:
            raise ValueError("CPU dtype must be float32, float16 or bfloat16")
        for key in ("max_model_len", "max_num_seqs", "max_num_batched_tokens", "kv_cache_memory_bytes", "cpu_threads"):
            value = getattr(self, key)
            if type(value) is not int or value <= 0:
                raise ValueError(f"CPU {key} must be a positive integer")
        if type(self.enforce_eager) is not bool:
            raise ValueError("CPU enforce_eager must be a boolean")
        if self.max_num_batched_tokens < self.max_model_len:
            raise ValueError("CPU max_num_batched_tokens must cover max_model_len")

    @classmethod
    def from_options(cls, options: dict) -> "CpuLaunchOptions":
        return cls(**{key: options[key] for key in cls.__dataclass_fields__ if key in options})

    def to_dict(self) -> dict:
        return {key: getattr(self, key) for key in self.__dataclass_fields__}


def cpu_launch_limits(options: dict) -> ContainerLimits:
    values = {"cpus": 2, "memory_bytes": 3221225472, "memory_swap_bytes": 5368709120,
              "shm_bytes": 536870912, "pids_limit": 256}
    supplied = options.get("resources", {})
    if type(supplied) is not dict:
        raise ValueError("CPU resources must be a table")
    values.update(supplied)
    limits = ContainerLimits.from_mapping(values)
    if any(getattr(limits, key) is None for key in values):
        raise ValueError("bounded CPU resources cannot be null")
    cpu = CpuLaunchOptions.from_options(options)
    if cpu.cpu_threads > limits.cpus:
        raise ValueError("CPU threads must not exceed the container CPU bound")
    if cpu.kv_cache_memory_bytes >= limits.memory_bytes:
        raise ValueError("KV cache must leave memory for model weights and runtime")
    return limits


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
    limits: ContainerLimits | None = None
    entrypoint: str | None = None
    chat_template: dict | None = None
    log_profile: dict | None = None


@dataclass
class VllmAdapter:
    http_get: HttpGet | None = None
    docker: Docker | None = None
    base_url: str | None = None
    launch_spec: LaunchSpec | None = None
    name: str = field(default="vllm", init=False)
    version: str = field(default="0.4.2", init=False)

    def input_rejection_proof(self, **kwargs) -> dict:
        return _input_rejection_proof(**kwargs)

    def input_length_cause_collector(self, ctx):
        """Optional source-correlated CPU evidence; ordinary requests unchanged."""
        from .vllm_length_cause import VllmLengthCause
        return VllmLengthCause(ctx)

    # --- 기동 ------------------------------------------------------------------

    def start_command(self, spec: LaunchSpec) -> list[str]:
        revision = spec.options.get("revision")
        if not revision:
            raise ValueError("model revision must be pinned (options['revision'])")
        unknown = set(spec.options) - {"revision", "models_dir", "device", "cpu_kv_cache_gib", "launch_profile",
                                      "model_layout", *CPU_LAUNCH_KEYS, *(o.value for o in CommonOption)}
        if unknown:
            raise ValueError(f"unknown launch options: {sorted(unknown)}")
        profile = spec.options.get("launch_profile", "legacy")
        if type(profile) is not str or profile not in {"legacy", "cpu-bounded"}:
            raise ValueError("launch_profile must be legacy or cpu-bounded")
        layout = spec.options.get("model_layout", "hf-cache")
        if type(layout) is not str or layout not in {"hf-cache", "snapshot"}:
            raise ValueError("model_layout must be hf-cache or snapshot")
        if profile == "legacy" and (layout != "hf-cache" or any(key in spec.options for key in CPU_LAUNCH_KEYS)):
            raise ValueError("bounded CPU options and snapshot layout require launch_profile=cpu-bounded")
        if profile == "cpu-bounded":
            if spec.options.get("device") != "cpu":
                raise ValueError("cpu-bounded requires explicit device=cpu")
            if "cpu_kv_cache_gib" in spec.options:
                raise ValueError("cpu-bounded uses kv_cache_memory_bytes; do not also set cpu_kv_cache_gib")
            cpu = CpuLaunchOptions.from_options(spec.options)
            cpu_launch_limits(spec.options)
            argv = [MODEL_MOUNT if layout == "snapshot" else spec.model, "--revision", str(revision),
                    "--served-model-name", spec.model, "--host", "0.0.0.0", "--port", str(spec.port),
                    "--dtype", cpu.dtype, "--max-model-len", str(cpu.max_model_len),
                    "--max-num-seqs", str(cpu.max_num_seqs), "--max-num-batched-tokens", str(cpu.max_num_batched_tokens),
                    "--kv-cache-memory-bytes", str(cpu.kv_cache_memory_bytes), "--load-format", "auto"]
            if cpu.enforce_eager:
                argv.append("--enforce-eager")
            if spec.options.get(CommonOption.REMOTE_CODE_EXECUTION.value):
                argv.append(OPTION_FLAGS[CommonOption.REMOTE_CODE_EXECUTION])
            if "chat_template_profile" in spec.options:
                _, template = fixed_chat_template(spec.options["chat_template_profile"])
                argv += ["--chat-template", template["container_path"], "--chat-template-content-format", "string"]
            if "logging_profile" in spec.options:
                logging = fixed_native_log_profile(spec.options["logging_profile"])
                argv += ["--enable-log-requests", "--max-log-len", str(logging["max_log_len"])]
            return argv
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
        argv = self.start_command(spec)
        mounts = [Mount(Path(models_dir), MODEL_MOUNT, read_only=True)]
        template = None
        logging = None
        if "chat_template_profile" in spec.options:
            template_mount, template = fixed_chat_template(spec.options["chat_template_profile"])
            mounts.append(template_mount)
        if "logging_profile" in spec.options:
            logging = fixed_native_log_profile(spec.options["logging_profile"])
            env.update(logging["environment"])
        bounded = spec.options.get("launch_profile") == "cpu-bounded"
        if bounded:
            cpu = CpuLaunchOptions.from_options(spec.options)
            env.update(VLLM_CPU_OMP_THREADS_BIND="nobind", OMP_NUM_THREADS=str(cpu.cpu_threads),
                       VLLM_NO_USAGE_STATS="1", DO_NOT_TRACK="1")
            # The image itself may define the legacy GiB variable. Remove it
            # in the child before starting, otherwise it overrides byte sizing.
            argv = ["-u", "VLLM_CPU_KVCACHE_SPACE", "vllm", "serve", *argv]
        elif device == "cpu":
            # CPU 빌드: KV cache 메모리(GiB). 작은 모델·작은 VM에 맞춘 값
            gib = spec.options.get("cpu_kv_cache_gib", 1)
            if type(gib) is not int or gib <= 0:
                raise ValueError("cpu_kv_cache_gib must be a positive integer")
            env["VLLM_CPU_KVCACHE_SPACE"] = str(gib)
        return LaunchPlan(
            image=spec.image,
            argv=argv,
            env=env,
            secret_env_names=secret,
            mounts=mounts,
            gpus=device == "gpu",
            shm_size=None if bounded else "1g",
            limits=cpu_launch_limits(spec.options) if bounded else None,
            entrypoint="/usr/bin/env" if bounded else None,
            chat_template=template,
            log_profile=logging,
        )

    def request_binding_contract(self, image_digest: str, engine_version: str) -> dict | None:
        """Consumer evidence in the inspected image, not an authenticated principal."""
        if (engine_version, image_digest) not in (("0.31.0", CHAT_REVIEWED_IMAGE),
                ("0.31.0", REQUEST_BINDING_GPU_IMAGE), ("0.32.0", REQUEST_BINDING_GPU_032_IMAGE)):
            return None
        return {"profile": "request-connection-binding-v1", "engine_version": engine_version,
                "image_digest": image_digest, "id_prefix": "chatcmpl-", "body_request_id": True,
                "header_request_id": True, "header_precedence": True,
                "consumer_prompt_ids": True, "consumer_prompt_text": True,
                **({"launch_profile": "gpu-bounded-v1"} if image_digest != CHAT_REVIEWED_IMAGE else {}),
                "sources": ([
                    {"path": "vllm/entrypoints/serve/engine/serving.py", "sha256": "e46693a37e9a8b1087c8cee8a1e2cb9a2ce3d052772e3771755ef59de0885cf4", "lines": "133-143"},
                    {"path": "vllm/entrypoints/openai/chat_completion/serving.py", "sha256": "5e619c0a6866758a247c76e7b8c472f6e008ddbc720f7efe47cc6776cb986d4f", "lines": "310-405,540-573,749-791,1115-1128,1189-1205"},
                    {"path": "vllm/entrypoints/openai/chat_completion/protocol.py", "sha256": "8744d5e2004b2d376e34cf17de51c919bca6d81b0cc8392da138db1ef05a7c61", "lines": "415-432,453-461"}]
                    if image_digest == REQUEST_BINDING_GPU_032_IMAGE else [
                    {"path": "vllm/entrypoints/serve/engine/serving.py", "sha256": "e5239ca4e2119fd019ee0ffcbf6e68f642b7ce94bad1b3fabe22ccdd8cf9eb02", "lines": "119-129"},
                    {"path": "vllm/entrypoints/openai/chat_completion/serving.py", "sha256": "532dadea845c77a5a79011027b9989987c3e32467614e993f439f29c924de002", "lines": "291-317,554-565,743-760,1190-1201"},
                    {"path": "vllm/entrypoints/openai/chat_completion/protocol.py", "sha256": "4bdc71f668e4820d8ae5bae2a153fed0296b430cd6353ace8c0c27cfb982ebc1", "lines": "391-400,416-423,446-452"}])}

    def native_log_contract(self, image_digest: str, engine_version: str) -> dict | None:
        if image_digest != CHAT_REVIEWED_IMAGE or engine_version != "0.31.0":
            return None
        return fixed_native_log_profile("request-info-v1")

    def template_rejection_evidence(self, response: dict, *, kind: str,
                                    prompt_tokens: int | None = None, output_tokens: int | None = None,
                                    max_model_len: int | None = None) -> dict | None:
        """Recognize only the inspected vLLM 0.31.0 pre-generation rejections.

        HTTP 400 by itself is insufficient: auth, a bad model or an unrelated
        validation failure must never stand in for input-limit enforcement.
        The caller must independently pin/verify the image and engine version.
        """
        if (type(response) is not dict or type(response.get("outcome")) is not int
                or response["outcome"] != 400 or response.get("body_truncated")
                or type(response.get("body")) is not str):
            return None
        try:
            value = json.loads(response["body"])
        except (ValueError, TypeError):
            return None
        error = value.get("error") if type(value) is dict else None
        if (type(error) is not dict or error.get("type") != "BadRequestError"
                or type(error.get("code")) is not int or error["code"] != 400):
            return None
        if kind == "request_override":
            expected = ("Chat template is passed with request, but --trust-request-chat-template is not set. "
                        "Refused request with untrusted chat template.")
            if error.get("message") != expected or error.get("param") is not None:
                return None
            return {"engine_version": "0.31.0", "image_digest": CHAT_REVIEWED_IMAGE,
                    "phase": "pre_generation", "kind": kind,
                    "source": "vllm/renderers/online_renderer.py:636-660",
                    "source_sha256": "1ada26191425fd9d56c3525949f999826dad9f2c82df94ad586a1cf6f67b089e"}
        if kind == "input_limit":
            if (any(type(v) is not int or v <= 0 for v in (prompt_tokens, output_tokens, max_model_len))
                    or output_tokens > max_model_len or prompt_tokens <= max_model_len - output_tokens
                    or error.get("param") != "input_tokens"):
                return None
            # Tokenization may retain only max_input+1 tokens before rejecting.
            captured = min(prompt_tokens, max_model_len - output_tokens + 1)
            qualifier = "at least " if captured == max_model_len - output_tokens + 1 else ""
            expected = (f"This model's maximum context length is {max_model_len} tokens. However, you requested "
                        f"{output_tokens} output tokens and your prompt contains {qualifier}{captured} input tokens, "
                        f"for a total of {qualifier}{captured + output_tokens} tokens. Please reduce the length of the "
                        "input prompt or the number of requested output tokens."
                        f" (parameter=input_tokens, value={captured})")
            if error.get("message") != expected:
                return None
            return {"engine_version": "0.31.0", "image_digest": CHAT_REVIEWED_IMAGE,
                    "phase": "pre_generation", "kind": kind,
                    "source": "vllm/renderers/params.py:483-510",
                    "source_sha256": "ad38852674ebc5bbe69aa9df91602847d518382cc2ceb0a25aac1d07be66b504",
                    "formatter_source": "vllm/exceptions.py:49-56",
                    "formatter_source_sha256": "22fe819c7ef10438f740c251d81af204871a04c3aa3eb1fd0e68604dc4c2146f",
                    "serialization_source": "vllm/entrypoints/serve/exception_handling/error_response.py:41-44,87-95",
                    "serialization_source_sha256": "354a0581fbc29e2c3acbebe5e84ff3dbb132ba0af14ceba5363b6cf2fec6d54e",
                    "sanitizer_source": "vllm/entrypoints/serve/exception_handling/utils.py:7-15",
                    "sanitizer_source_sha256": "723214aa4c63418f1d5eb7c1ff2116eca474778576ac7198daa1f39217c17a4f",
                    "observed_input_tokens": captured, "max_model_len": max_model_len,
                    "requested_output_tokens": output_tokens}
        return None

    def supports_template_profile(self, metadata: dict) -> bool:
        """Restrict probes to the shipped text fixture, never arbitrary Jinja.

        This checks the declared profile contract only. The executor must also
        verify the actual owned container's argv, readonly mount and file bytes.
        A new supported profile requires its own version and inspected bytes.
        """
        return (type(metadata) is dict and metadata == {
            "profile": CHAT_TEMPLATE_PROFILE, "version": "1", "sha256": CHAT_TEMPLATE_SHA256,
            "container_path": CHAT_TEMPLATE_CONTAINER_PATH, "content_format": "string",
            "request_overrides": False,
        } and type(metadata.get("request_overrides")) is bool)

    def template_type_boundary_contract(self, metadata: dict, image: str, version: str) -> dict | None:
        """A separate harmless supplied-template probe; existing profiles stay fixed.

        The witness is rendering output, not a worker file-read or generated-
        output attribution claim. GPU and other images require separate review.
        """
        if (image != CHAT_REVIEWED_IMAGE or version != "0.31.0" or type(metadata) is not dict
                or type(metadata.get("request_overrides")) is not bool
                or metadata != {"profile": TYPE_TEMPLATE_PROFILE, "version": "1", "sha256": TYPE_TEMPLATE_SHA256,
                    "container_path": TYPE_TEMPLATE_PATH, "content_format": "string", "request_overrides": False}):
            return None
        return {"profile": TYPE_TEMPLATE_PROFILE, "normal_message": "guard sandbox control",
                "challenge_message": "guard sandbox boundary", "attribute": "__class__", "path": TYPE_TEMPLATE_PATH,
                "normal_rendered": "GUARD_TYPE_BOUNDARY_7f81d490c326: normal\nuser: guard sandbox control\nassistant: ",
                "reference_rendered": "GUARD_TYPE_BOUNDARY_7f81d490c326: int\n"}

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

    def probe_request(self, route: Route, model: str | None, engine_version: str | None = None,
                      engine_image_digest: str | None = None) -> ProbeRequest | None:
        safe = PINNED_SAFE_PROBES.get((engine_version, engine_image_digest, route.method, route.path))
        if safe is None:
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
