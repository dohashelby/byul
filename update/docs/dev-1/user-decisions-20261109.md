# 사용자 결정 (2026-11-09) — SGLang 확장 준비와 vLLM 완성

목표: **SGLang 확장은 구조 준비까지만 하고 vLLM을 빨리 완성한다.** 이 문서는 그 목표에 필요한 결정을 기록한다. 한 줄 요약은 `DECISIONS.md`에 있다. 반영 상태는 각 항목 끝에 적는다.

## 1. 작업 순서와 병합

| # | 결정 | 반영 상태 |
|---|---|---|
| 1-1 | A 작업 트리(`Buyl/byul-dev1-k8s`, 기준 `8402ff89`)의 미커밋 설계 보수는 **A/B 작업 분배 전에 먼저 정리해 main에 넣는다.** 들어 있는 것: 엔진 provider(`adapters/engine_provider.py`), 판본 profile 목록(`adapters/engine_profiles.py`, `adapters/assets/engine_profiles.json`), 생산자 자격 receipt(`collectors/producer_qualifications.json`·`producer_approvals.py`·`producer_release.py`), 실행 recipe(`core/execution_recipe.py`) 등 R1~R9 경계. | 반영: 브랜치 `claude/a-design-repairs`(PR #239 위). 원본은 A 작업 트리 그대로 보존 |
| 1-2 | 병합 순서는 **PR dohashelby/byul#239 → A 설계 보수**다. #239에는 아래 1-3 계약 형식을 반영해 먼저 병합한다. A 작업은 최신 main으로 옮기면서 충돌을 푼다. | #239 반영, A는 `claude/a-design-repairs`로 정리(PR #239 병합 뒤 병합) |
| 1-3 | 계약 `environment.target.engine.runtime_kind`는 정해진 목록이 아니라 **소문자 driver 식별자 형식**(`^[a-z][a-z0-9_.-]{0,63}$`)만 검사한다. 엔진 이름(`target.engine.name`)과 `framework-input` 엔진 칸도 같은 형식이다. | 반영(`contracts/schemas/*`, `contracts/README.md`) |
| 1-4 | 환경 변환은 **A 방식**으로 한다. `tools/import_byul_inputs.py`의 `convert_documents(..., version=)`에서 `guard-byul-input-import/1`은 과거 결과 재생, `/2`가 새 기본이다. `/2`는 아래 값을 guard 객체에 직접 넣는다. 값이 없으면 직렬화에서 빠져 기존 객체 해시는 그대로다. 이때 #239의 `*_RETAINED_IN_SOURCE_ONLY` 제한 코드는 `/2`에서 쓰지 않는다(`/1` 재생에서만 남는다). | 반영(`claude/a-design-repairs`): `EngineInfo.frontend`, `RuntimeInfo.tp_size`·`pp_size`·`dp_size` 추가 |
|  | - `EngineInfo`: `runtime_kind`(A), `frontend`(#239 계약 필드, 이번 결정으로 추가) |  |
|  | - `RuntimeInfo`: `tp_size`·`pp_size`·`dp_size`(#239 계약 필드, 이번 결정으로 추가) |  |
|  | - `ModelInfo`: `tokenizer_revision`·`components`(A) |  |
|  | - `Policy`: `valid_from`·`valid_until`·`source_document_sha256`(A) |  |
| 1-5 | A 미커밋 작업 안의 **runtime 코드 변경**(`runtime/src/serving_runtime/mvp/backend_drivers.py` 새 파일, `cli.py`·`mvp/app.py`·`mvp/backend.py`·`mvp/config.py`·`mvp/engine_client.py`·`mvp/target.py`, `runtime/tests/test_backend_driver_extension.py`, `runtime/project-context/STATUS.md`)과 **platform 시험·CI 변경**(`platform/api/tests/test_update_integration_contracts.py`, `.github/workflows/update.yml`)은 **update PR에서 빼서** 각 담당 검토용으로 따로 둔다. update 작업은 runtime을 고치지 않는다. | 반영: update PR에서 뺐고 브랜치 `claude/a-runtime-platform-proposal`에 따로 둔다 |

## 2. 공통 입력 계약 `guard-gpu-common-input/2`

2026-11-08에 승인한 두 가지(반복 행 요약, V2 logit bias 비교)에 아래 세 가지를 더해 **한 번의 판본 변경으로** 구현한다. 생산자 소스가 바뀌면 등록(`SUPPORTED_PRODUCERS`)이 풀리므로, TP2 GPU 세션에서 한 번에 다시 확인·등록한다.

| # | 결정 | 계약 영향 |
|---|---|---|
| 2-1 | `request-attempt`의 KV 값은 **엔진 고유 단위 + 단위 필드**로 적는다. 값은 엔진이 실제로 쓰는 단위 그대로 두고, 단위 이름과 (알면) 단위당 token 수를 함께 적는다. 엔진 간 비교는 소비자가 단위로 환산한다. | `kv_blocks`에 단위 필드 추가(예: `kv_unit`: `block`·`token_slot`, `kv_unit_tokens`: 정수 또는 null). 이름은 구현 때 정하고 계약 문서에 적는다 |
| 2-2 | `limit_enforced`는 **할당 거부만이 아니라 모든 한도 집행**을 뜻한다. 방식은 필드로 남긴다(예: `alloc_refused`·`preempted`·`rejected`). vLLM KV 할당 거부와 SGLang retraction이 모두 이 사건을 내고, retraction은 `retry`도 함께 낸다. | `limit_enforced` 행에 집행 방식 필드 추가 |
| 2-3 | **생산자 신원(소스 SHA)에 공통 감싸기 기반 파일을 포함한다.** 예: `adapters/vllm_cache_callback.py`처럼 여러 관측기가 함께 쓰는 감싸기·자격 확인 파일. 기반이 바뀌면 생산자는 자동으로 미지원이 된다. | 등록된 vLLM 생산자 8종의 SHA가 바뀐다. 2-1·2-2와 함께 재등록 |
| (11-08) | 반복 `stage`·`gpu_fence`·`kv_owned`·`consumed`·`worker_state` 행 요약: 같은 요청·attempt·단계의 연속 행만 묶고, 값이 바뀔 때마다 끊고, 첫·끝 행은 원 행으로 남긴다. 상한 4,096은 그대로다. | 요약 행 형식 |
| (11-08) | V2 sampler의 `logit_bias` 개수가 null이 아니면 API 값과 비교한다(다르면 `value_changed_before_consumption` 후보). null이면 지금처럼 gap. | 비교 규칙 |

세부 형식은 [공통 입력 계약](gpu-common-inputs-196.md) "/2 확정 사항" 절에 둔다.

## 3. vLLM 완성 기준

| # | 결정 |
|---|---|
| 3-1 | **vLLM 0.31.0에서 69개 점검을 GPU lab 제품 경로(EVD→RES→RPT)로 실측하고, 0.32.0 고정값을 만들어 두 판본 비교(`guard update`)까지 한다.** 비교에서는 일부러 넣은 회귀 사례 하나로 `REGRESSED` 탐지를 확인한다. |
| 3-2 | 1차 범위에서 뺀 점검(REQ-02, OBS-01·06·11, PLT-07·10·11·12, OBS-09 기업 수집기 범위, PLT-08 TLS 구성)은 **실행 경로를 준비하고 UNKNOWN으로 남긴 상태를 완성으로 본다.** 입력이 오면 그때 실측한다. UNKNOWN을 PASS·NOT_APPLICABLE로 바꾸지 않는다. |
| 3-3 | 점검별 영향도(`impact`)는 **2026-11-08 초안(HIGH 33·MEDIUM 38·LOW 6)대로 확정**한다. 새 팩 판본으로 반영하고, 그 뒤 공식(official) 재판정을 한다. 과거 팩·결과는 소급 변경하지 않는다. 표: [11-08 결정 5절](user-decisions-20261108.md). |

## 4. SGLang

| # | 결정 |
|---|---|
| 4-1 | 정식/패치 판별은 **SGLang도 vLLM과 같은 규칙**이다. 버전의 셋째 자리만 오르면 패치 릴리스로 보고 소스 diff로 골라 시험한다(`docs/UPDATE_SELECTION.md` §1). |
| 4-2 | SGLang v0.5.22 request-attempt 생산자 후보(`/Users/doha/Desktop/byul-sglang`, 브랜치 `claude/sglang-request-attempt`, 미커밋)는 **보류**한다. SGLang 실제 확장을 시작할 때 A 설계 보수의 provider 구조와 계약 `/2`에 맞춰 옮긴다. 후보 설계·결과는 그 작업 트리의 `update/docs/SGLANG_REQUEST_ATTEMPT_PRODUCER.md`에 있다. |
| 4-3 | 지금 SGLang에 대해 하는 일은 **구조 준비까지**다. 엔진 provider·판본 profile·계약 의미(2절)·엔진 식별 계약(1-3)·작업 규칙(`CLAUDE.md`·`AGENTS.md` 1절)까지만 하고, SGLang 생산자·설치기·수집 도구·lab·GPU 실측·등록은 하지 않는다. |
