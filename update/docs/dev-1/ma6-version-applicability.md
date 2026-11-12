# M-A6 판본 적용표 — 1단계 선행 확인

- 작성: 2026-11-12. 범위: [작업지시서 3차](../WORK_ORDER_VLLM_MVP.md) M-A6 "선행 확인".
- 판본 번호와 이미지 digest는 작업지시서 0.1절 표에만 있다. 이 문서는 "기준 판본"·"후보 판본"·"이전 판본"(지금 고정값이 있는 판본, `engine_profiles.json`의 `vllm-native-source/1` 행)으로 쓴다.
- 근거 종류를 구분한다. **[레지스트리]** = Docker Hub 레지스트리의 매니페스트·이미지 config(레이어는 받지 않음), **[소스]** = vLLM 저장소 태그 소스(얕은 clone), **[실측]** = 이미 저장된 GPU 실측 기록. 이미지 레이어·설치 파일 bytes는 받지 않았다. 그래서 아래의 소스 근거는 모두 실제 이미지 설치 파일과 대조하기 전까지 후보다.

## 1. 확인한 원본

| 쪽 | 매니페스트 | 이미지 config | 태그 소스 커밋 |
|---|---|---|---|
| 기준 | 0.1절 digest와 받은 매니페스트 bytes의 SHA-256 일치 [레지스트리] | `sha256:644d31f210b8e4d005fcbc6bc3ad13221993c0cd5ec627e9503d74dd53509b52` (12,308 bytes), 생성 2025-01-27, amd64 | `5204ff5c3feeb96e8a6eea65dfcb78395f90d4d8` |
| 후보 | 0.1절 digest와 받은 매니페스트 bytes의 SHA-256 일치 [레지스트리] | `sha256:d076d77d8dade699041dbcb6bb989a058e51b532e6367f13f80b5a4e63ee5c53` (42,367 bytes), 생성 2026-09-21, amd64 | `ced6857afa0ea7b2e3f0846a62e1394e90f15607` |

- 두 매니페스트는 단일 플랫폼 manifest(v2)이며 index가 아니다.
- 후보 이미지 config는 `VLLM_BUILD_COMMIT`·`org.opencontainers.image.revision` = `ced6857a…`로 태그 소스 커밋과 같다 [레지스트리].
- 기준 이미지 config에는 빌드 커밋 표시가 없다. 기준 이미지가 태그 커밋 `5204ff5c…`로 빌드됐다는 것은 아직 추정이다. 실제 이미지의 `vllm/` 설치 파일 bytes를 태그 소스와 대조해야 확정된다(2단계 도구의 이미지 모드).

## 2. 엔진 Python 판본 — 두 판본 모두 3.12

| 쪽 | 근거 | Python |
|---|---|---|
| 기준 | 이미지 config history의 빌드 인자 `PYTHON_VERSION=3.12`(`vllm-base` 단계의 `apt-get install python3.12`, `update-alternatives python3 → python3.12`) [레지스트리]. 태그 소스 `Dockerfile` `ARG PYTHON_VERSION=3.12`, 설치 경로 `/usr/local/lib/python3.12/dist-packages` [소스] | 3.12 |
| 후보 | 이미지 config history의 빌드 인자 `PYTHON_VERSION=3.12` [레지스트리]. 태그 소스 `docker/Dockerfile` `ARG PYTHON_VERSION=3.12` [소스] | 3.12 |

- 관측기 공통 기반의 `sys.version_info[:2] != (3, 12)` 조건에 두 판본 모두 들어온다. **결정 11은 필요 없다.**
- 패치 판본(3.12.x)은 레지스트리 메타데이터에 없다. 관측기 조건은 minor까지만 보므로 이 단계의 판단에 영향이 없다. 바이트코드 code 객체 지문은 minor 판본에 묶이므로, 2단계 고정값 계산도 3.12로 한다.

## 3. 기본 엔진과 실행 구조

| 항목 | 기준 판본 | 후보 판본 | 근거 |
|---|---|---|---|
| 기본 엔진 | **V0** (`VLLM_USE_V1` 기본 `"0"`). V1은 `VLLM_USE_V1=1`로 켜는 초기 구현이 같이 들어 있다 | **V1만** (V0 경로 `vllm/worker`·`vllm/core`·`vllm/executor`·`vllm/engine/multiprocessing` 없음) | [소스] `vllm/envs.py`, 디렉터리 목록 |
| V1 구성 | `vllm/v1/core/scheduler.py`(단일 파일), `v1/worker/gpu_model_runner.py`, `v1/structured_output` 없음 | `vllm/v1/core/sched/`, `v1/worker/gpu/`(새 model runner)와 `v1/worker/gpu_model_runner.py`, `v1/structured_output`, `v1/fault_tolerance` | [소스] |
| 이미지 기동 명령 | `ENTRYPOINT python3 -m vllm.entrypoints.openai.api_server`, `--model` 인자 | `ENTRYPOINT vllm serve`, 모델은 위치 인자(`--model`도 받음), nonroot wrapper·UID 2000 사용자 있음 | [레지스트리] config, [실측] `tools/gpu_a_aws_lab.py`의 SUP-08 PoC 기동 분기 |
| prefix cache 기본 | **꺼짐** (V0에서 `enable_prefix_caching=None → bool(VLLM_USE_V1)`) | **켜짐** (`CacheConfig.enable_prefix_caching = True`) | [소스] `engine/arg_utils.py`, `config/cache.py` |
| 프런트엔드 | V0 `MQLLMEngine` 다중 프로세스(`--disable-frontend-multiprocessing`로 끔) | V1 `AsyncLLM` + `EngineCore` 프로세스 | [소스] |

**영향:** 현재 엔진 내부 관측기는 거의 모두 V1 모듈(`vllm.v1.*`)이나 후보 판본에서 새로 생긴 경로(`vllm.entrypoints.serve.*`, `vllm.renderers.*`, `vllm.v1.worker.gpu.*`)를 감싼다. 기준 판본 기본 기동(V0)에서는 이 모듈들이 import되지 않거나 없다. 기준 판본은 대부분 (c) 이상 분류가 될 가능성이 크다. 분류는 2단계 도구로 함수 단위로 낸다.

기준 판본을 `VLLM_USE_V1=1`로 띄우면 V1 관측기 일부를 공유할 수 있으나, 기업이 쓰는 기본 구성이 아니게 되고 그 시기 V1의 기능 공백(구조화 출력 없음 등)이 생긴다. 기본 구성을 바꾸는 것은 시험 조건 변경이라 이 문서는 제안하지 않는다(필요하면 2단계 보고에서 따로 묻는다).

## 4. 기동 CLI 옵션 (guard가 쓰는 것)

`vllm/engine/arg_utils.py`와 프런트엔드 인자 파일(기준 `entrypoints/openai/cli_args.py`, 후보 `entrypoints/launchers/cli_args.py`)의 문자열 옵션과 dataclass 필드에서 뽑았다 [소스]. 기준 126개, 후보 245개.

| 옵션 | 기준 | 후보 | guard 사용처 |
|---|---|---|---|
| `--model`/위치 인자, `--revision`, `--served-model-name`, `--host`, `--port` | 있음 | 있음 | 기본 기동 |
| `--max-model-len`, `--max-num-seqs`, `--gpu-memory-utilization`, `--enforce-eager`, `--seed`, `--tensor-parallel-size`, `--load-format`, `--tokenizer-revision` | 있음 | 있음 | 기동 profile, TP2 |
| `--trust-remote-code`, `--code-revision` | 있음 | 있음 | SUP-08 [실측: 두 판본 PoC 기동] |
| `--enable-prefix-caching` | 있음(기본 꺼짐) | 있음(기본 켜짐) | REQ-16·17 계열 |
| `--chat-template`, `--chat-template-content-format` | 있음 | 있음 | SUP-03 고정 템플릿 |
| `--enable-auto-tool-choice`, `--tool-call-parser` | 있음 | 있음 | REQ-10 |
| `--api-key`, `--allowed-origins`, `--root-path`, `--middleware` | 있음 | 있음 | 인증·경로 점검 |
| `--enable-log-requests` | **없음**(기본 요청 로그 켜짐, `--disable-log-requests`로 끔) | 있음(기본 꺼짐) | `adapters/vllm.py` 로그 profile — 판본별 기동 인자 데이터 필요 |
| `--max-log-len` | 있음 | 있음 | 로그 profile |
| `--profiler-config` | **없음**(`VLLM_TORCH_PROFILER_DIR` 환경 변수로 profiler 경로 열림) | 있음 | 프로파일러 점검 — 판본별 기동 데이터 필요 |
| `--kv-cache-memory-bytes` | 없음 | 있음 | CPU 기동 전용(M-A9 대상, MVP 미사용) |
| `--structured-outputs-config` | 없음(`--guided-decoding-backend`) | 있음 | 구조화 출력 백엔드 |
| `--limit-mm-per-prompt`, `--mm-processor-kwargs`, `--allowed-local-media-path` | 있음 | 있음 | 멀티모달 |
| `--enable-sleep-mode` | 있음(서버 경로 없음, 아래 5절) | 있음 | — |
| `--enable-log-outputs`, `--enable-server-load-tracking` | 없음 | 있음 | — |

## 5. 점검이 쓰는 기능 유무

| 기능 | 기준 판본 | 후보 판본 | 근거 | 관련 점검(예) |
|---|---|---|---|---|
| OpenAI chat/completions, `/v1/models`, `/health`, `/ping`, `/version`, `/tokenize`, `/metrics` | 있음 | 있음 | [소스] 경로 정의 | 다수 |
| `/ready`, `/readyz` | **없음** | 있음 | [소스] | LIF 준비 상태(`vllm_ready_endpoint_*`) |
| `/v1/responses` 저장 응답 | **없음** | 있음 | [소스] | 저장 응답(`vllm_stored_responses`) |
| `/sleep`·`/wake_up`·`/pause`·`/resume`·`/update_weights` 계열 | **없음** | 있음 | [소스] | 수명 주기·가중치 갱신 계열 |
| `/start_profile`·`/stop_profile` | 있음(`VLLM_TORCH_PROFILER_DIR` 설정 시만) | 있음 | [소스] | 프로파일러 점검 |
| `/reset_prefix_cache` | 있음 | 있음 | [소스] | |
| 구조화 출력 요청 필드 | `guided_json`·`guided_regex`·`response_format` (V0 guided decoding) | `structured_outputs`·`response_format` (`guided_*` 제거) | [소스] protocol | REQ-10(`executors/structured_tools.py`는 `structured_outputs`를 보낸다 → 기준 판본에서는 무시되는 추가 필드) |
| `tool_choice` | `none`·`auto`·named (**`required` 없음**) | `none`·`auto`·`required`·named | [소스] protocol | REQ-10 `required` 사례 |
| 추가 요청 필드 처리 | `extra="allow"`, 경고 로그 후 무시 | 같음 | [소스] | SUP-08 `mm_processor_kwargs` 무시(기준 PoC `ignored`) |
| `cache_salt`, `bad_words`, `return_token_ids`, `prompt_embeds`, `vllm_xargs` | **없음** | 있음 | [소스] protocol | 캐시 격리·입력 검증 계열 |
| `logit_bias`, `allowed_token_ids`, `prompt_logprobs`, `priority`, `truncate_prompt_tokens`, `chat_template_kwargs` | 있음 | 있음 | [소스] | 샘플링·입력 점검 |
| 멀티모달(Qwen2-VL, Phi-3-vision) | 있음 | 있음 | [소스] registry | REQ-16, SUP-08 |
| prefix cache | 있음(기본 꺼짐) | 있음(기본 켜짐) | [소스] | REQ-16·17 |
| 동적 import(`trust_remote_code`, `code_revision`) | 있음 | 있음 | [소스], [실측] SUP-08 PoC | SUP-08 |
| tool parser·reasoning parser | 있음(`vllm/entrypoints/openai/tool_parsers`) | 있음(`vllm/tool_parsers`, `vllm/reasoning`) | [소스] 디렉터리 | REQ-10 |
| fault tolerance 경로 | 없음 | 있음(`v1/fault_tolerance`, `/fault_tolerance/status`) | [소스] | LIF-08·11 |

"없음"은 태그 소스에 그 경로·필드·옵션이 없다는 뜻이다. 그 점검을 비적용으로 볼지 UNKNOWN으로 둘지는 판정 의미 변경이라 결정 9로 점검별로 받는다(승인 전 기본 UNKNOWN). 점검별 묶음은 2단계 적용표에서 낸다.

## 6. 정본 시험 모델

| 모델 | 구조 | 기준 판본 registry | 후보 판본 registry | 두 판본 같은 revision으로 기동 |
|---|---|---|---|---|
| SUP-08 PoC snapshot (Phi-3-vision 파생, inventory `c2dd6747…`) | `Phi3VForCausalLM` | 있음 | 있음 | **실측으로 확인됨**: 같은 snapshot·inventory로 두 판본 모두 기동하고 정상 요청 전후 200([SUP-08 결과](../gpu-a/RESULTS-20261111-SUP08.md)) |
| REQ-16 Qwen2-VL-2B-Instruct | `Qwen2VLForConditionalGeneration` | 있음 | 있음 | 소스 근거로 가능, **revision 미고정·미실측**. 기준 판본 요구 Transformers `>= 4.45.2`(이 구조 지원 시작 판본), 후보 판본 `>= 5.10.4`. 이미지 안 실제 Transformers 판본은 레이어를 받아야 안다 |
| 기본 GPU 모델 Qwen2.5-0.5B-Instruct `7ae55760…` ([환경](../gpu-a/ENVIRONMENT.md)) | `Qwen2ForCausalLM` | 있음 | 있음 | 소스 근거로 가능, 두 판본 미실측 |
| 기본 소형 모델 facebook/opt-125m `27dcfa74…` | `OPTForCausalLM` | 있음 | 있음 | 소스 근거로 가능, 두 판본 GPU 미실측. chat template이 없어 SUP-03 고정 템플릿이 필요 |

- 지금 근거로는 **결정 12(모델 교체)가 필요한 모델이 없다.** 다만 Qwen2-VL-2B-Instruct는 revision을 아직 고정하지 않았다. J01 전에 revision을 고정하고, 기준 판본 이미지의 Transformers로 그 revision의 processor·config가 읽히는지 첫 기동에서 확인해야 한다. 실패하면 그때 결정 12로 올린다.

## 7. 2단계로 넘기는 것

1. 기준 판본은 V0 기본이라, V1 모듈을 감싸는 관측기는 대부분 기준 쪽에서 대상 모듈이 import되지 않는다. 함수 단위 비교 도구로 관측기·생산자·수집 경로를 (a)~(d)로 나눈다.
2. 판본별 기동 데이터: 이미지 기동 명령(`api_server --model` / `vllm serve`), 로그 옵션(`--enable-log-requests` 유무), profiler(`--profiler-config` / 환경 변수), prefix cache 기본값 차이를 provider·판본 profile 데이터로 둔다.
3. HTTP 경로표·요청 필드표의 판본 행(`/ready`·`/v1/responses` 없음, `structured_outputs`↔`guided_json`, `tool_choice=required` 없음).
4. 기준 이미지의 빌드 커밋은 아직 추정이다. 고정값을 확정하려면 두 이미지의 설치 파일 bytes 대조가 필요하다(이미지 레이어 다운로드, 사용자 승인 필요).

## 재현

```bash
git clone --depth 1 --branch <0.1절 기준 태그> https://github.com/vllm-project/vllm.git
git clone --depth 1 --branch <0.1절 후보 태그> https://github.com/vllm-project/vllm.git
```

레지스트리 확인은 익명 pull 토큰으로 `GET /v2/vllm/vllm-openai/manifests/sha256:<digest>`와 그 config blob만 받았다(레이어 없음).
