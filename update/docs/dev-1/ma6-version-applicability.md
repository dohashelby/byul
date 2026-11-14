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

- 관측기 공통 기반의 `sys.version_info[:2] != (3, 12)` 조건에 두 판본 모두 들어온다. **minor 판본 기준으로는 결정 11이 필요 없다.**
- **패치 판본(3.12.x)은 아직 모른다.** 레지스트리 메타데이터에 없고, Python을 설치한 레이어 안에만 있다(기준 레이어 7번 427.6 MB, 후보 레이어 7번 233.4 MB. 이미지 전체는 기준 5.56 GB, 후보 8.73 GB).
- 패치 판본이 중요한 관측기가 있다. 엔진 안 sensor 15개(`vllm_attempt_sensor`·`vllm_fault_sensor`·`vllm_file_read_sensor`·`vllm_initialization_sensor`·`vllm_profiler_sensor`·`vllm_ready_endpoint_sensor`·`vllm_rank_failure_sensor`·`vllm_request_output_sensor`·`vllm_safe_open_mapping_sensor`·`vllm_stream_buffer_sensor`·`vllm_termination_sensor`·`vllm_weight_iteration_sensor`·`vllm_worker_startup_sensor`·`vllm_worker_initialization_sensor`·`vllm_worker_generation_sensor`)는 `RUNTIME_CODE_CONTRACTS`에서 CPython **패치 판본별** 자기 코드 지문을 찾는다(지금 `cpython-3.12.3`·`.13`·`.14`·`.15`·`.16`). 이미지의 패치 판본이 이 목록에 없으면 그 sensor는 기동 시 거부(`*_RUNTIME_UNAVAILABLE`)한다.
- 이것은 엔진 판본이 아니라 guard 관측기 코드 쪽 데이터다. 그 패치 판본 CPython으로 관측기 코드 지문을 계산해 행을 더하면 된다(분류 (a)와 같은 데이터 추가). 패치 판본을 알려면 위 Python 레이어를 받아야 한다(사용자 승인 필요, 2단계 보고에서 묻는다).
- 바이트코드 code 객체 지문은 minor 판본(3.12)에 묶이므로, 2단계 vLLM 대상 함수 비교도 3.12로 한다.

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
| REQ-16 Qwen2-VL-2B-Instruct | `Qwen2VLForConditionalGeneration` | 있음 | 있음 | revision895c3a49로 고정하고 실제 두 이미지 TP2 모델 로드·정상 HTTP200을 확인했다. 기준 V0의 멀티모달 prefix cache는 실제 내부 비활성: 결정34로 해당 기준 캐시 분기만 원인 있는 UNKNOWN 예외. [기동 원자료](ma7-gpu-preflight-20261114.json) |
| 기본 GPU 모델 Qwen2.5-0.5B-Instruct `7ae55760…` ([환경](../gpu-a/ENVIRONMENT.md)) | `Qwen2ForCausalLM` | 있음 | 있음 | 소스 근거로 가능, 두 판본 미실측 |
| 기본 소형 모델 facebook/opt-125m `27dcfa74…` | `OPTForCausalLM` | 있음 | 있음 | 소스 근거로 가능, 두 판본 GPU 미실측. chat template이 없어 SUP-03 고정 템플릿이 필요 |

- 지정 모델은 revision을 고정해 두 이미지에서 실제 로드·정상 HTTP200을 확인했으며 결정12의 모델 교체는 하지 않았다. 기준 V0의 멀티모달 prefix cache는 기동 옵션을 명시해도 내부에서 꺼지므로 결정34의 해당 기준 분기 UNKNOWN 예외를 적용한다. 모델 로드 성공을 캐시 검증 성공으로 바꾸지 않는다.

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

---

# M-A6 2단계 — 판본 고정값 비교 도구와 판본 적용표

## 8. 비교 도구 `tools/engine_pin_compare.py`

- `inventory`: `guard.adapters.vllm_*` 관측기 모듈을 import해 module-level 고정값 표를 모은다. 엔진 모듈별 고정 SHA-256, 대상 함수(`Class.method`·함수 이름), 그 값을 적은 관측기 표를 낸다. 지금 68개 엔진 모듈, 142개 대상 함수를 찾는다. 엔진 밖 라이브러리 고정값(`uvicorn.protocols.http.*`, `transformers.configuration_utils`·`tokenization_utils_base`, safetensors 파일)은 `other_pins`로 따로 낸다.
- `compare --reference <현재 고정값 판본 소스> --side base=<기준> --side candidate=<후보> [--diff-dir]`: 대상 함수마다 code 객체 지문(관측기와 같은 `base.code_digest(relative_code(...))`)과 줄 위치를 뺀 지문, docstring을 뺀 AST, 인자 목록을 비교한다. 결과 상태:

| 상태 | 뜻 | 제안 분류 |
|---|---|---|
| `FILE_IDENTICAL` | 파일 SHA-256까지 같음 | (a) |
| `CODE_IDENTICAL` | 함수 지문까지 같음(파일은 다름) | (a) |
| `CODE_EQUAL_LINES_SHIFTED` | 줄 위치만 다름(지문은 새로 계산) | (a) |
| `DOCSTRING_ONLY` | docstring만 다름(지문은 새로 계산) | (a) |
| `CHANGED` | 실행 코드가 다름. `signature_equal`로 인자·sync/async 유지 여부 표시 | 검토 |
| `MOVED_EQUAL` / `MOVED_CHANGED` | 같은 qualname이 다른 모듈에 있음(같음/다름) | (b) / 검토 |
| `ABSENT` / `MODULE_ABSENT` | 함수 / 모듈이 없음 | (c) 이상 |

- 도구는 (d)를 정하지 않는다. (d)는 아래 10절에서 항목별 근거로 제안한다.
- `--side name=ROOT:image`로 이미지에서 꺼낸 설치 파일 트리를 주면 고정값이 `image_pin`이 된다. 태그 소스는 `candidate_pin`이다. 이번 결과는 모두 `candidate_pin`이다.
- 직접 시험: `tests/test_engine_pin_compare.py` 11개(합성 소스 트리, 실제 관측기 표 inventory).
- 이번 실행: reference = 이전 판본 소스(`vllm-0.31.0-src`, 고정 SHA 대조로 확인), base = 기준 태그 `5204ff5c…`, candidate = 후보 태그 `ced6857a…`, CPython 3.12.13. 결과 전체는 [ma6-pin-compare.json](ma6-pin-compare.json)이다.

| 대상 함수 142개 | FILE/CODE 같음 | 줄 이동 | docstring만 | 바뀜(인자 같음/다름) | 옮겨짐(같음/다름) | 함수 없음 | 모듈 없음 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 후보 판본 | 11 | 68 | 19 | 36 (30/6) | 0/0 | 1 | 0 |
| 기준 판본 | 0 | 1 | 1 | 30 (10/20) | 2/11 | 32 | 58 |

엔진 모듈 68개 중 기준 판본에 없는 모듈은 46개, 후보 판본에 없는 모듈은 0개다. 단, 기준 판본에 "있는" V1 모듈도 기본 기동(V0)에서는 import되지 않는다(3절).

도구의 한계:
- 관측기 고정값이 module-level 표가 아닌 형태(예: `vllm_stored_responses`의 짧은 키, `vllm_worker_generation_contract`의 중첩 표, `MODULE_CONTRACTS`)는 파일 단위만 잡히거나 빠진다. 해당 관측기는 11절 표에서 따로 적는다.
- `RUNTIME_CODE_CONTRACTS`(관측기 자기 코드의 CPython 패치 판본별 지문)와 엔진 밖 라이브러리 고정값은 태그 소스로 비교할 수 없다. 이미지 bytes가 필요하다(12절).

## 9. 관측 계열별 판본 적용표

분류는 쪽(기준/후보)마다 따로 적는다. 후보 판본은 이전 판본과 한 단계 차이라 거의 (a)다. 기준 판본은 V0 기본이라 엔진 내부 관측이 대부분 (c) 이상이다.

| # | 관측 계열(관측기·수집 경로) | 주 점검 | 후보 판본 | 기준 판본 | 근거 |
|---|---|---|---|---|---|
| 1 | HTTP·Kubernetes 외부 관측(경로표 `adapters/vllm.py` SAFE_PROBES, `kubernetes_*` 수집기, 프로세스 역할 이름) | REQ-01·02·03·13, PLT-01·04·07·08·09, SUP-04, OBS-04·05·06·09·11, OBS-01, PLT-10·11·12 | (b) 판본 행 추가 | (b) 판본 행 추가 | 엔진 내부 고정값이 없다. `SAFE_PROBES`·`SAFE_PROBE_REVIEWED_IMAGES`·길이 오류 형식(`_LENGTH_ERROR_031/032`)·`kubernetes_response_error.SUPPORTED_ENGINE_VERSION`·`engine_provider` 검토 이미지가 판본 문자열 키라 두 판본 행이 필요하다. 기준 판본은 `/ready`·`/v1/responses`·`/sleep` 계열이 없고(5절) 프런트엔드가 V0 `MQLLMEngine` 다중 프로세스라 역할 이름표가 다르다 |
| 2 | 기동 데이터(provider·판본 profile) | 전체 | (b) | (b) | 이미지 명령(`vllm serve` / `api_server --model`), 로그 옵션, profiler 옵션, prefix cache 기본값(4절) |
| 3 | 요청 입구 관측 `vllm_lifecycle_sensor`·`vllm_lifecycle_profiles` (serving·auth middleware·models router·input_processor·output_processor·async_llm) | PLT-10·11(1차 제외), REQ-05, OBS-02, LIF-05·06 계열 기반 | (a) 대부분 + 검토 8(인자 같음) | 모듈 17/26 없음. models 경로 `MOVED_CHANGED`(→`entrypoints/openai/api_server`·`serving_models`), 인증은 `build_app` 안 지역 함수 `authentication` → (c)/(d) | 기반 sensor라 다른 sensor 15개가 이 위에 선다 |
| 4 | 요청 시도·캐시 조회 `vllm_attempt_callback`·`vllm_cache_callback`·`vllm_scoped_consumption_callback` | REQ-15·16·17, LIF-03·12 | (a) (cache는 docstring만) + attempt 검토 5 | V1 scheduler·block_pool 없음(V0는 `core/scheduler.py`·`core/block/prefix_caching_block.py`, 객체 모델이 다름) → (c)/(d) | 기준 판본은 prefix cache 기본 꺼짐 |
| 5 | 실행 연결·소비 `vllm_execution_link_callback`·`vllm_consumption_callback`·`vllm_consumption_v2_callback`·`vllm_engine_callback_sensor`(slot) | REQ-08·09·10·12, LIF-05·06, REQ-18 | (a) + 검토(input_processor 3, gpu_model_runner 3, V2 runner 4) | V1 input_processor·structured_output·V2 runner 없음, V0 `worker/model_runner.py`로 구조가 다름 → (c)/(d) | |
| 6 | TP 통신 버퍼 `vllm_comm_buffer_callback`·`vllm_distributed_callback` | REQ-14·21 | (a) + 검토 2(`CustomAllreduce.__init__` 인자 다름, `close`) | `custom_all_reduce` 모듈 있음, 대상 4개 중 바뀜 3·docstring만 1 → (b)/(c) 후보. distributed는 V1 `gpu_model_runner` 대상이라 (c) | 같은 기능이 두 판본에 있는 몇 안 되는 엔진 내부 대상 |
| 7 | CUDA Graph·커널 경계 `vllm_graph_replay_callback`·`vllm_kernel_bounds_callback`·`vllm_graph_buffers`·`vllm_slot_mask` | REQ-18·19·20 | (a) + 검토 1 | V2 runner(`v1/worker/gpu/*`) 없음. V0는 `CUDAGraphRunner`(`worker/model_runner.py`) → (c)/(d) | |
| 8 | 가중치 공급·적재 `vllm_weight_iteration_sensor`·`vllm_file_read_sensor`·`vllm_safe_open_mapping_sensor`·`vllm_worker_startup_*`·`vllm_worker_shard_callback`·`vllm_supply_read_callback` | SUP-05·06·07·15·16, LIF-01·02, PLT-03·05 | (a) + 검토(`_get_weights_iterator` 인자 다름 등 3) | `weight_utils` 있음(대상 7 중 바뀜 3·없음 4), loader는 `model_loader/loader.py`로 `MOVED_CHANGED` → (b)+(c) | safetensors 라이브러리 파일 고정값은 이미지 필요 |
| 9 | 공유 가중치 수명 `vllm_weights_lifetime_callback` | LIF-13 | (a) + 검토 3 | `weight_cache` 모듈 없음 → 기능 부재(결정 9) | |
| 10 | 동적 import·플러그인(`transformers` 고정값, `vllm.plugins`) | SUP-09·10·11·12 | 라이브러리 판본 확인 필요 | 같음. 기준 Transformers 계열이 다르다(4.x / 5.x) | `other_pins`의 `transformers.*`는 이미지 bytes로만 대조 가능 |
| 11 | 템플릿·보조 공급 `vllm_auxiliary_read`·`vllm_auxiliary_link_callback`·`vllm_template_boundary` | SUP-03·13, REQ-07 | (a) | `entrypoints/chat_utils` 있음. 대상 표가 다른 형태라 도구는 파일 단위만 비교 → 수동 검토 | |
| 12 | 온라인 입력 `vllm_online_sensor` (renderers·input_processor·`validate_request_mm_kwargs`) | REQ-08·09, SUP-08 내부 원인 | 대상 1개 **없음**(`validate_request_mm_kwargs`) + 검토 4 | 3개 모듈 모두 없음 → (c)/(d) | 없는 함수는 이전 판본이 SUP-08 요청을 import 전에 거부한 검증 지점이다. 후보 판본에 없는 것이 실측 회귀(`consumed`)와 맞는다 |
| 13 | 수명 전환 `vllm_transition_callback`·`vllm_transition_dispatch_callback`·`vllm_ready_endpoint_*`·`vllm_worker_generation_*` | LIF-05·06·07·16, OBS-02 | (a) + 검토 2 | `EngineCore` sleep/wake/pause 등 9개 없음, `/ready` 없음 → 기능 부재(결정 9) 또는 (c) | |
| 14 | 초기화·rank 장애 `vllm_initialization_sensor`·`vllm_rank_failure_sensor`·`vllm_fault_*`·`vllm_worker_initialization_sensor` | LIF-07·08·09·11 | (a) | V1 `multiproc_executor` 대상 18개 중 8개 없음, V0 기본은 `executor/mp_distributed_executor.py` → (c)/(d) | 후보는 `v1/fault_tolerance`도 있음 |
| 15 | 요청 종료 `vllm_termination_sensor`·`vllm_request_output_sensor` | REQ-12, LIF-10·12, OBS-02 | (a) + 검토 5(`AsyncOutput.__init__` 인자 다름) | 모듈 8/10 없음 → (c)/(d) | |
| 16 | 프로파일러 `vllm_profiler_sensor`·`vllm_profiler_callback` | LIF-14 | (a) + 검토 3 | `profiler/wrapper` 없음. V0 profiler는 `worker/worker.py`의 `VLLM_TORCH_PROFILER_DIR` 경로 → (c) | 기동 옵션도 다르다(4절) |
| 17 | 저장 응답 `vllm_stored_responses` | REQ-06(대상 비적용) | 파일 3개 수동 확인 필요 | `/v1/responses` 없음 → 기능 부재 | 정본 선정에서 대상 비적용 근거만 남기는 점검 |
| 18 | 실행기 소스 분석(`*_cpu.py`, `executors/engine_sensor.py` source slot) | REQ-10·SUP-13 등 | 소스 commit 행 추가 (b) | 같음, 분석 대상 모듈이 없으면 (c) | M-A9 대상이 아니다(CLAUDE.md 5절) |
| 19 | 스트림 버퍼 `vllm_stream_buffer_sensor`(uvicorn 고정값) | LIF-03·04 | 이미지 필요 | 이미지 필요 | uvicorn 판본이 이미지마다 다르다. 태그 소스로 비교할 수 없음 |

공통 입력 생산자(`SUPPORTED_PRODUCERS` 8종: request-attempt, generation-transition, health-worker, profiler-control, runner-consumption(/v2), supply-reads, node-process-device-provider)의 생산자 SHA는 guard 쪽 파일 bytes라 판본과 무관하다. 다만 생산자가 감싸는 엔진 관측기가 위 4·5·8·13·16행이라, 그 행의 분류를 따른다. 등록은 그 판본의 GPU 확인 뒤(J01·J02)다.

## 10. 점검별 예상 결과 (69개)

"외부" = 엔진 내부 고정값 없이 HTTP·Kubernetes로 관측한다. 분류는 [후보 / 기준] 순서.

| 묶음 | 점검 | 후보 | 기준 | 예상 J02 결과 |
|---|---|---|---|---|
| A. 외부 관측, 판본 행만 추가 | REQ-01·03·13, PLT-01·04·08·09, SUP-04, OBS-04·05·09 | (b) | (b) | 두 판본 실행 가능 |
| B. 1차 제외(UNKNOWN 완성) | REQ-02, OBS-01·06·11, PLT-07·10·11·12, (PLT-08 TLS 구성 분기) | (b) | (b) | 두 판본 정본 실행 → UNKNOWN+원인. PLT-10·11의 엔진 내부 근거는 기준 쪽 (d) 후보 |
| C. 대상 비적용 | REQ-04·06 | - | - | 선정 기록. REQ-06은 기준 판본에 `/v1/responses` 자체가 없다는 근거를 더할 수 있다 |
| D. SUP-08 | SUP-08 | 실행됨(PoC) | 실행됨(PoC) | `REGRESSED` (A, M-A8) |
| E. 엔진 내부, 후보는 (a), 기준은 같은 기능 있음 | REQ-21(통신 버퍼), REQ-05(모델 목록·선택 경로: 기준은 `entrypoints/openai/api_server`·`serving_models`로 `MOVED_CHANGED`, (b) 대상표 후보), SUP-05·06·15·16·LIF-01·02·PLT-03·05의 가중치 적재 부분, SUP-03·13·REQ-07 템플릿 | (a)+검토 | (b)/(c) | 후보 실행 가능. 기준은 대상표·관측기 추가로 실행 가능 후보 |
| F. 엔진 내부, 기준은 V0 구조라 새 설계 | REQ-14·15·16·17·18·19·20, REQ-08·09·10·11·12, LIF-03·04·07·08·09·10·11·12·14, OBS-02·03, PLT-02·06, SUP-07·09·10·11·12 | (a)+검토 | (c) 또는 (d) | 후보 실행 가능. 기준은 10절 결정 전까지 `ENGINE_VERSION_UNSUPPORTED` → 비교 `NOT_COMPARABLE` |
| G. 기준 판본에 기능 자체가 없음 | LIF-13(공유 가중치 캐시), LIF-05·06·16의 실행 중 sleep/wake/pause·가중치 갱신 분기, LIF-07의 `/ready` 분기 | (a)+검토 | 기능 부재 | 결정 9 |

- 위 묶음은 관측 계열(9절)에서 점검으로 옮긴 예상이다. 한 점검이 여러 계열을 쓰면 가장 어려운 계열을 따랐다. 점검별 실행기 입력을 만들 때(M-A7·M-B2) 다시 확인한다.
- 후보 판본 "검토" 36개 함수(인자 다름 6개 포함)는 함수 단위 diff(`--diff-dir`)를 관측 의미와 대조해야 (a)로 확정된다. 3단계 구현 때 관측기별로 한다.

## 11. 결정 9 — 한 판본에 기능이 없을 때 (점검별 승인 요청)

2026-11-13 사용자에게 항목별로 검토받아 아래 여섯 제안을 각각 승인받았다. 데이터·계획 반영과 실제 소비 연결·GPU 검증을 구분한다.

| 점검 | 기준 판본에 없는 것 (근거) | 제안 |
|---|---|---|
| LIF-13 | `model_executor/model_loader/weight_cache/*` 모듈 없음(공유 가중치 데몬·IPC loader) [소스] | 기준 쪽 UNKNOWN(원인: 기능 부재). 비적용으로 바꾸려면 "그 판본은 공유 가중치를 제공하지 않는다"를 선정 근거로 남겨야 해 사용자 판단 |
| LIF-05·06·16 (실행 중 전환 분기) | 서버 `/sleep`·`/wake_up`·`/pause`·`/resume`·`/update_weights` 경로 없음, `EngineCore.sleep/wake_up/pause_scheduler` 없음 [소스] | 재배포(Pod 교체) 분기는 외부 관측으로 두 판본 실행. 실행 중 전환 분기만 기준 쪽 UNKNOWN |
| LIF-07 (`/ready` 분기) | `/ready`·`/readyz` 없음 [소스] | 기준 쪽은 `/health`만으로 준비 상태를 판단할 근거가 없으므로 그 분기 UNKNOWN |
| REQ-06 | `/v1/responses` 없음 [소스] | 이미 대상 비적용. 근거 문장만 더함 |
| REQ-10 (`tool_choice=required`, `structured_outputs` 필드) | `tool_choice`에 `required` 없음, 구조화 출력 필드가 `guided_json` [소스] | `structured_outputs`→`guided_json` 대응은 (b) 요청 필드표로 실행. `required` 사례만 기준 쪽 UNKNOWN |
| REQ-16·17 (prefix cache) | 기능은 있으나 기본 꺼짐 [소스] | 결정 9가 아니라 기동 조건 문제다. 두 판본 모두 `--enable-prefix-caching`을 명시하는 안(비교 조건 동일)을 제안 |

## 12. 결정 10 — UNKNOWN으로 남길 어려운 항목 (항목별 승인 요청)

**결정 결과 (2026-11-12 사용자):**
- **D4·D5 승인** — 기준 판본 쪽 UNKNOWN으로 남긴다. 원인과 영향 점검(D4: PLT-10·11, D5: LIF-14)을 RES·문서에 남기고 PASS·NOT_APPLICABLE로 바꾸지 않는다.
- **D1·D2·D3 미승인** — UNKNOWN으로 남기지 않고 **(c) 후속, 미구현**으로 둔다. 나중에 V0 관측기로 만들 대상이고 지금 구현 순서에서는 뒤로 미룬다. 미구현 동안 해당 점검(D1: REQ-12·15·16·17, LIF-03·10·12 / D2: REQ-18·19·20, REQ-08·09·10의 소비 부분 / D3: LIF-07·08·09·11, PLT-02·06의 엔진 내부 근거)의 기준 쪽은 `ENGINE_VERSION_UNSUPPORTED`이며 **MVP 완료로 세지 않는다**.
- 결정 9, 기준 판본 V0 유지, 이미지 레이어 다운로드는 아직 결정 대기다. 다운로드 승인 전 고정값은 모두 후보(`candidate_pin`)다.

아래는 승인 요청 당시의 표다.

숫자 기준을 만들지 않고, 항목마다 원인과 영향 점검을 적는다. 모두 **기준 판본 쪽만**이다. 후보 판본 쪽은 (a)라 (d) 제안이 없다.

| # | 항목 | 원인 | 영향 점검 | (c)로 하려면 필요한 것 |
|---|---|---|---|---|
| D1 | V0 scheduler·block manager 기반 요청 시도·캐시 조회·종료 관측 | V0 `core/scheduler.py`·`core/block_manager.py`·`prefix_caching_block.py`는 V1과 객체 모델이 다르다(SequenceGroup 단위, block allocator). 지금 계약 사건(시도·적중·해제)의 의미를 V0 객체로 다시 정의해야 한다 | REQ-12·15·16·17, LIF-03·10·12 | V0 관측기 신규 설계, V0 모의 엔진 시험 자료, GPU 확인 |
| D2 | V0 model runner·CUDA Graph·slot·커널 경계 관측 | V0 `worker/model_runner.py`(`CUDAGraphRunner`)는 V2 runner(`v1/worker/gpu/*`)와 버퍼·slot 구조가 다르다 | REQ-18·19·20, REQ-08·09·10의 소비 부분 | 같은 의미의 버퍼·slot 식별 재설계 |
| D3 | V0 다중 프로세스 프런트엔드·executor 기반 초기화·rank 장애·자원 회수 | V0 `MQLLMEngine`(ZMQ) + `executor/mp_distributed_executor.py`는 V1 `MultiprocExecutor`·`EngineCoreProc`와 프로세스 구성·장애 전달 경로가 다르다 | LIF-07·08·09·11, PLT-02·06(엔진 내부 근거) | 프로세스 역할·rank 의미 재확인(CLAUDE.md 2절), TP2 GPU |
| D4 | 인증 middleware 내부 관측 | 기준 판본 인증은 `build_app` 안 지역 함수(`@app.middleware("http") authentication`)라 module 속성으로 감쌀 대상이 없다 | PLT-10·11(1차 제외라 원래 UNKNOWN) | 감싸기 방식 자체를 바꿔야 함 |
| D5 | V0 profiler 관측 | profiler가 Worker 메서드와 환경 변수로만 열리고 `profiler/wrapper` 대상이 없다 | LIF-14 | V0 worker profiler 대상표 + 기동 데이터. 비교적 작을 수 있어 (c) 후보로도 둔다 |

(c)로 진행을 제안하는 기준 쪽 항목: 9절 1·2행(판본 데이터), 6행 `custom_all_reduce`(REQ-21), 8행 가중치 적재(`weight_utils`·`loader.py`), 11행 템플릿, 16행 profiler(D5를 승인하지 않으면). 이 항목들은 같은 기능이 두 판본에 있어 대상표·고정값 데이터로 다루거나 관측기를 작게 더하는 범위다.

**별도로 물을 것 — 기준 판본 엔진 선택.** 기준 판본을 기본(V0)으로 띄우면 D1~D3가 남는다. `VLLM_USE_V1=1`로 띄우면 V1 관측기 일부를 대상표로 공유할 여지가 있다. 하지만 기업 기본 구성이 아니고, 그 시기 V1 초기 구현의 기능 공백이 시험 조건 차이로 들어간다. 이 문서는 기본(V0) 유지를 권한다. 기준을 바꾸려면 결정이 필요하다.

## 13. 이미지 bytes 대조 (승인 요청)

태그 소스 고정값을 확정하고 아래를 알려면 두 이미지의 일부 레이어가 필요하다. GPU·클라우드 비용은 없고 로컬 다운로드만 한다.

| 필요한 것 | 레이어 | 크기 |
|---|---|---|
| CPython 패치 판본(`RUNTIME_CODE_CONTRACTS` 행) | 기준 7번, 후보 7번(Python 설치) | 427.6 MB + 233.4 MB |
| `vllm/` 설치 파일 bytes(고정값 `image_pin`, 기준 빌드 커밋 확인), uvicorn·transformers·safetensors 판본 | vLLM wheel·의존성 설치 레이어 | 레이어별 수백 MB~수 GB(이미지 전체 기준 5.56 GB, 후보 8.73 GB) |

승인을 받으면 레이어를 파일 단위로 풀지 않고, 필요한 경로만 꺼내 `engine_pin_compare compare --side base=<꺼낸 site-packages>:image`로 다시 비교한다.

## 14. 3단계 구현 현황 (결정 10 뒤, 승인 범위만)

**구조 — 판본별 데이터를 넘긴다(코드 복사 없음).** 공통 감싸기 기반 `adapters/vllm_cache_callback.py`의 `_codes`는 관측기 표의 모듈 고정값으로 SHA-256 문자열 하나 또는 **검토 고정값 tuple**(판본별 정확 bytes 행)을 받는다.
- 파일 bytes가 그중 하나와 정확히 같을 때만 그 파일의 함수 지문으로 대상 함수를 감싼다. 다른 판본 파일의 함수 코드를 섞으면 `CACHE_CALLBACK_ENTRY_UNSUPPORTED`로 거부한다.
- 증거 행의 `source_file_sha256`에는 실제로 맞은 행의 고정값을 적는다. 표 형식이 틀리면(빈 tuple, 중복, SHA 아님) 거부한다.
- 고정값 검사를 넓히지 않았다. 판본 문자열 비교는 없다. 등록 생산자 신원(`SUPPORTED_PRODUCERS`)에는 이 파일이 들어가지 않아 등록 상태는 바뀌지 않는다.

| 관측기 | 점검 | 후보 판본 | 기준 판본 | 상태 |
|---|---|---|---|---|
| `vllm_comm_buffer_callback` | REQ-21 | 행 추가 `a048c132…` (candidate). 바뀐 것은 MNNVL multimem 경로 삭제뿐이고 관측 속성은 같음 | 행 추가 `f3e7b346…` (candidate). 생성자 인자 `max_size`만, MNNVL 없음. `_ptr`·`buffer_ptrs`·`disabled`·`rank`·`world_size`·해제 순서는 같음 | 구현·직접 시험. GPU 확인·이미지 bytes 대조 전 |

- `PIN_ROWS`의 `candidate`는 태그 소스 bytes라는 뜻이다. 이미지 설치 파일과 대조하면(13절) `reviewed`로 올린다.
- 기준 판본 쪽은 엔진 안 설치 경로(sitecustomize → `vllm_engine_callback_sensor.install`)가 V0 기본 기동에서 이 모듈을 실제로 감싸는지 GPU에서 확인해야 한다. 설치 경로의 다른 V1 전제는 확인하지 않았다.

**알려진 영향 — 소스 검토 관문.** 제품 소스를 바꿔 `release/reviewed-source.json`을 다시 만들었다. 그 결과 `verify_req10_consumption`이 `SOURCE_REVIEW_RECEIPT_REQUIRED`를 낸다. 이 관문을 쓰는 LIF-07 수집 시험(`test_b_lif07_readiness::test_actual_native_helper_default_public_entrypoint_store_restore_preserved`)도 실패한다. 당시에는 결정 5(검토 기록 규칙)가 미결이라 기록을 만들지 않았다. 이후 사용자 결정 5로 면제가 확정돼 새 SHA마다 검토 기록을 넣는다.

**#255 시점의 잔여 이력 (현행 상태는 15·16절):**
- A 담당 엔진 내부 관측기의 후보 판본 (a) 행: REQ-14 `vllm_distributed_callback`, REQ-15~17 `vllm_cache_callback`·`vllm_scoped_consumption_callback`, REQ-18~20 slot·graph·kernel, LIF-13 `vllm_weights_lifetime_callback`. 이 관측기들은 `base.SOURCE_SHA` 등 공유 표를 써서, 같은 tuple 행 구조를 공유 표에 들이는 작업이 먼저다.
- 판본 문자열 키 표(`adapters/vllm.py` SAFE_PROBES·길이 오류 형식, `kubernetes_response_error`, `engine_provider`, `vllm_runner_capture`)를 판본 행 조회로 바꾸는 일. `adapters/vllm.py`는 M-A9와 같은 파일이라 이 세션에서 고치지 않았다.
- 기준 판본 쪽 D1~D3은 "(c) 후속, 미구현"이다.

## 15. 단독 개발 후속 — 후보 행·함수 의미 대조 (2026-11-13)

- 관측기 후보 파일 13개(763,545 bytes)를 태그 원본 SHA와 대조해 fixture와 adapter 데이터에 추가했다. 캐시·분산·scoped consumption·slot·kernel·graph·공유 가중치 계열이다. 이미지 bytes 대조 전이므로 모두 candidate_pin이며 등록 생산자 목록은 바꾸지 않는다.
- V2 sampler의 상태 속성·sampling_params 인자 위치와 공유 가중치 조회의 반환 tuple 위치를 adapter 데이터로 처리한다. 공통 감싸기·최종 소스 확인은 여러 후보 중 실제로 일치한 SHA를 사용한다. 알 수 없는 bytes는 거부한다.
- 함수 36개의 대조는 [기계 판독 검토 기록](ma6-candidate-function-review.json)에 남겼다. scheduler 대기열·전환·출력·profiler·공급 반환의 차이는 M-B4 후속이며 같은 관측 의미로 무조건 이월하지 않는다.
- 직접 시험: 관측기·고정값·준비 자산·scoped 소비 등 175개 통과. 추가로 생산자 신원·정확 크기·후보 자격 89개 통과. 새 데이터 의존성이 바뀐 생산자는 과거 GPU 등록을 상속하지 않는지 확인했다. 양성 소비 시험은 모의 등록을 명시하고 기존 assert를 유지했다.
- 남은 M-A6: 두 판본 HTTP 경로·길이 오류·기동 provider 데이터, 변경 경로표 대조. 기준 쪽 D1~D3 및 실제 이미지·GPU 확인은 원래 후속 범위다. 이 절은 M-A6 전체 완료 기록이 아니다.

## 16. 승인된 Python 레이어 확인 (2026-11-13)

결정 15의 부분 승인 범위 안에서 Python 설치 레이어 두 개만 받았다. 매니페스트 원 bytes와 레이어 SHA·크기를 각각 확인했고, tar 전체를 풀지 않고 patchlevel.h와 dpkg status를 읽었다. [원 메타데이터 요약](ma6-python-layer-check.json).

| 쪽 | Python 설치 레이어의 CPython | 확인 근거 |
|---|---|---|
| 기준 | 3.12.8 | patchlevel.h, python3.12·python3.12-dev 패키지 3.12.8-1+jammy1 |
| 후보 | 3.12.3 | patchlevel.h, python3.12·python3.12-dev·python3.12-minimal 패키지 3.12.3-1ubuntu0.17 |

- 문서 13절의 "7번"은 0부터 센 인덱스다(매니페스트상 여덟 번째 레이어). 압축 bytes 합계는 661,014,960이다. 다른 이미지 레이어는 받지 않았다.
- 설치 레이어의 Python 판본을 확인한 것이며 최종 엔진 프로세스의 interpreter 경로·패치 판본 확인은 실행하지 않았다. 이후 레이어의 덮어쓰기 가능성을 실제 파일 대조나 기동 확인 없이 제거하지 않는다.
- 기준 쪽 관측기 15종에 CPython 3.12.8의 자기 코드 지문 행이 있는지는 M-B4에서 확인·추가해야 한다. 기존 패치 판본의 지문을 그대로 승인하거나 자격 검사를 넓히지 않는다.
- 결정 14는 V0 유지로 확정했고 결정 9의 6행은 항목별 검토 대기다. vLLM·의존성 설치 레이어는 아직 미승인이므로 태그 소스 핀은 후보를 유지한다.

## 17. 기동·HTTP 데이터와 이동 경로 검토

provider의 기동 profile에 두 판본의 명령·모델 인자·기본 엔진·로그/profiler 데이터를 넣었다. HTTP 데이터는 오류 envelope·길이 형식·정확 이미지 쌍과 안전 프로브 후보를 담는다. 기존 판본 비교 분기는 형식 행 조회로 바꿨다. 후보 데이터는 이미지 bytes 검토 전 안전 프로브 실행이나 거부 원인 증명에 사용하지 않으며, 해당 원인은 미검증으로 돌려준다. 후보 관측 profile은 정확 소스·함수·CPython·준비 자산 검사를 거치고 새 생산자의 GPU 등록을 상속하지 않는다. 기준 V0 내부 관측 공백은 여전히 남는다.

공식 두 commit의 전체 Git tree(blob) 비교는 변경 경로 8,002개다. 검토한 정확 경로 17개만 변경 영역 표에 더했다. 제외는 늘리지 않았고 미분류 332개 중 315개를 그대로 남긴다. 관련 후보 경로는 책임 비교이며 git rename이나 동일 관측 의미의 증명이 아니다. 상세 SHA·근거·미분류 목록은 `ma6-change-path-review.json`에 있다. 정식 릴리스 전체 점검 계약은 유지한다.

M-A6 이틀 범위의 후보 행·36개 함수 검토·기동/HTTP 데이터·경로표는 PR 검증 대상으로 준비했다. 이미지 설치 파일 대조·기준 CPython 패치 자격·D1~D3 V0 관측 설계·실제 GPU 등록·M-B4와 결정 9는 별도 미완료다. 이를 전체 M-A6 또는 MVP 완료로 세지 않는다.

## 18. 결정 9 항목별 승인 반영

사용자가 9-1~9-6을 각각 승인했다. 11절 표의 UNKNOWN 분기 범위를 유지하고 REQ-06은 기존 비적용 근거만 추가한다. 기준의 guided_json 필드 대응과 기능 부재 분기는 adapter 데이터에 기록했으며 M-B2·M-B4에서 실제 소비를 연결한다. REQ-16·17의 양쪽 prefix caching 명시는 판본 profile 데이터와 렌더 도구의 `--check-id`로 적용한다. 다른 점검 기동의 기본값을 바꾸지 않는다.


## 실제 기동 후속: 기준 V0 멀티모달 prefix cache 공백 (2026-11-14)

앞의 prefix cache 기능표는 일반 기능의 유무와 기본값이다. 지정 REQ-16 멀티모달 모델을 실제 두 고정 이미지에서 기동한 결과, 기준 V0는 --enable-prefix-caching을 받더라도 내부에서 껐다. 정확 설치 engine/arg_utils.py1049~1054행 조건과 실제 로그의 enable_prefix_caching=False를 [GPU 사전 점검](ma7-gpu-preflight-20261114.json)에 남겼다. 모델 로드와 정상 HTTP200은 두 쪽 모두 확인했지만, 그 사실은 이미지 블록/문맥 캐시 격리의 측정력이 아니다. 결정9-6의 양쪽 옵션 명시는 유지하고 기능 부재를 PASS·비적용으로 바꾸지 않는다. 사용자 결정34로 기준 V0 유지와 해당 기준 캐시 분기의 원인 있는 UNKNOWN 예외를 승인받았다. 실제 결과 객체를 생성하거나 나머지 M-A6/7 전체 완료로 세지 않는다. 다른 텍스트 모델/점검의 cache 기능 부재로 일반화하지 않는다.
