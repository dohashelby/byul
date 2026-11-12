# BYUL UPDATE 작업지시서 3차 — vLLM MVP(1차 완성)

- 작성: 2026-11-12. 개정: 2026-11-12(MVP 판본을 데이터 표로 분리, SUP-08은 A·나머지는 B로 분담), 2026-11-12(CPU 이미지 제외, 69개 두 판본 실제 비교)
- 기준: main `f1f3f301`(PR #240·#246·#248 병합 뒤)
- 대체: [작업지시서 2차](WORK_ORDER_VLLM_1.md)의 남은 항목을 이 문서로 옮긴다.
- 근거: `../CLAUDE.md`·`../AGENTS.md`, `../DECISIONS.md` 2026-11-12, [11-09 결정](dev-1/user-decisions-20261109.md), [11-08 결정](dev-1/user-decisions-20261108.md), [현황표](CHECKLIST_VALIDATION_STATUS.md)

## 0. MVP 정의

**MVP는 기업이 vLLM을 새 판본으로 올릴 때마다 guard 하나로 같은 업데이트 보안 검증을 끝까지 받을 수 있는 상태다.** 제품 목표는 특정 판본에 묶이지 않는다. MVP lab은 이 목표를 아래 한 쌍의 판본으로 확인한다.

### 0.1 MVP 판본 표 (데이터)

판본은 이 표에만 적는다. 이 문서의 나머지는 "기준 판본"·"후보 판본"으로 쓴다.

| 역할 | 판본 | GPU 이미지 | 근거 |
|---|---|---|---|
| 기준 판본 | vLLM 0.7.0 | `docker.io/vllm/vllm-openai@sha256:a43963ed149a7b8b6c8c9dd028d4ab2be9fe804761d41b11cc07043a1edb61a8` | #246 PoC에서 고정 |
| 후보 판본 | vLLM 0.30.0 | `docker.io/vllm/vllm-openai@sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40` | #246 PoC에서 고정 |

- 사용자 결정(2026-11-12)으로 11-09 결정의 판본 쌍(실측 판본·비교 판본)을 이 표로 바꿨다. 이전 판본의 고정값·지원표 행·실측 기록은 지우지 않고 데이터와 이력으로 둔다.
- 기준 → 후보는 정식 릴리스 변경이다. U1 규칙([UPDATE_SELECTION](UPDATE_SELECTION.md))에 따라 69개 전체를 시험하고 소스 diff를 보고한다.
- 다음 판본 쌍으로 바꿀 때는 이 표와 고정값·지원표 행만 바꾼다. 코드를 바꿔야 한다면 앵커링 결함으로 보고 M-A5에서 고친다.

### 0.2 완료 기준

| # | 완료 기준 | 확인 방법 |
|---|---|---|
| 1 | update 범위 69개를 기준·후보 두 판본의 GPU lab에서 제품 경로(`guard update`: select → run → judge → report)로 실행한다. 새 정본 팩 PACK-BASELINE@34를 쓴다. | 점검·쪽마다 EVD·RES와 비교 RPT의 정확 Ref, `docs/evidence/` 공개 요약, 현황표 |
| 2 | 1차 제외 10개(REQ-02, OBS-01·06·11, PLT-07·08·10·11·12, OBS-09 기업 수집기 범위)는 정본 팩에서 실행하고 UNKNOWN과 원인을 남긴다. | RES의 UNKNOWN 사유 |
| 3 | 대상 비적용 2개(REQ-04·06)는 정본 선정에서 비적용 근거를 남긴다. | 선정 기록 |
| 4 | 비교 RPT에서 SUP-08이 `REGRESSED`로 나온다. 기준 판본은 비승인 코드 판본 선택을 무시하고 후보 판본은 따르는 실제 차이다(#246 PoC). 일부러 넣는 회귀 사례가 필요 없다. | 비교 RPT, SUP-08 RES 두 쪽 |
| 5 | **판본 추가는 데이터만으로 된다.** 두 판본 지원을 고정값·지원표 행·provider 데이터 추가로만 하고, `executors/`·`runtime/`·`collectors/`·`core/`·`models/`·`judges/`에 엔진 이름·판본 문자열 비교가 남지 않는다. | M-A5의 검사 시험, 지원표 diff |
| 6 | 정책 확정자(사용자)와 팩 검토자를 거쳐 `official=true` RPT를 만든다. | 공식 RPT Ref |

MVP에서 허용하는 것과 허용하지 않는 것:
- **목표:** 69개 모두 두 판본에서 실제로 관측해 비교한다(2026-11-12 사용자 결정). 기준 판본에서 고정값이 맞지 않는 관측은 공백으로 두기 전에 M-A6·M-B4의 분류(a~d)로 판본별 대상표나 adapter 관측기를 만든다.
- **허용:** 관측할 수 없는 분기, 사용자가 승인한 "너무 오래 걸리거나 어려운" 항목의 공백(결정 10), 기업 입력이 없는 분기, 1차 제외 범위의 UNKNOWN(모두 원인 기록 필수). 승인 전의 `ENGINE_VERSION_UNSUPPORTED` 공백은 완료로 세지 않는다. 한쪽만 공백이면 비교 결과의 `sides.<side>.gaps`에 둔다. 실제 보안 FAIL도 그대로 둔다. 기준은 "69개 모두 PASS"가 아니라 "69개 모두 제품 경로로 정직한 결과"다.
- **불허:** CPU·대역 결과를 GPU 실측으로 올리기, CPU용 vLLM 이미지(`vllm/vllm-openai-cpu`, `cpu-bounded`)로 실증하기, 실행하지 않은 점검을 결과로 세기, 판정 기준을 결과에 맞춰 고치기, 다른 판본의 결과를 옮겨 쓰기, 고정값이 맞지 않는 판본을 받으려고 고정값 검사를 넓히거나 끄기.

### 0.3 알고 시작할 위험

- 기준 판본은 후보 판본보다 훨씬 오래된 판본이다. 기본 엔진 구조가 다를 수 있어(기준 판본 시기의 기본 엔진은 V0), 지금의 엔진 내부 관측기 고정값(V1 경로 기준)이 기준 판본에서 많이 맞지 않을 수 있다. 맞지 않으면 감싸지 않고 진단(gap)을 남기므로, 해당 점검은 기준 쪽 UNKNOWN 또는 비교 `NOT_COMPARABLE`이 된다.
- 그래서 **GPU를 쓰기 전에 판본 적용표(M-A6)로 점검별 예상 결과(실행 가능 / 대상표·관측기 추가로 실행 가능 / 승인 요청할 공백)를 먼저 낸다.** 예상 공백 수가 사용자 기대와 크게 다르면 J02 비용 승인 전에 보고한다.
- 기준 판본에 없던 엔드포인트·옵션을 쓰는 점검은 기준 쪽에서 실행되지 않을 수 있다. 같은 이유로 UNKNOWN과 원인을 남긴다.

MVP 밖(후속): 이전 판본 쌍의 정본 실측·비교, SGLang 생산자·실측, TensorRT-LLM, 11-08 결정 6-2~6-4 기업 입력, 2차 지시서 A08·A09·A10, `kubernetes_http.py`의 SUP-08 부분 분리.

## 1. 현재 상태 (main `f1f3f301`)

| 담당 | 현황표 상태 | 점검 수 |
|---|---|---:|
| A | lab 완료 | 8 |
| A | 기업 입력 대기 | 12 |
| A | GPU 확인만(TP2) | 3 |
| A | 측정 남음 | 1 |
| A | 대상 비적용 | 2 |
| B | CPU 개발 남음 | 30 (SUP-08 포함) |
| B | lab 완료 | 6 |
| B | 측정 남음 | 7 |

- 현황표의 "CPU 개발 남음"은 GPU 없이 하는 실행기·판정 개발이 덜 끝났다는 뜻이다. CPU용 vLLM 이미지 실증을 뜻하지 않으며(2026-11-12 결정으로 하지 않음), 해당 점검의 실측은 모두 GPU 세션에서 한다.
- 현황표의 담당은 1단계 분담(실행기 파일 소유)으로 계산된다. **MVP 동안 SUP-08은 A가 맡는다**(2절). 그래서 MVP 기준 담당 수는 A 27개, B 42개다.
- 현행 팩(@33) GPU 실행 기록은 77개 중 MEASURED 11·EXAMPLE 14·DECLARED 1·중단 1, 미측정 50이다. 이 기록은 모두 MVP 판본 표가 아닌 판본의 결과이고, @34가 77개 정의를 모두 바꾼다(지금 77개 모두 `impact: null`). **MVP 실측은 @34와 MVP 판본 표로 새로 한다.**
- 판본 고정값은 지금 대부분 이전 판본 한 판본에만 있다(`adapters/` 17개 파일, 지원표 `vllm-native-source/1`의 27개 기능). 기준·후보 판본에는 #246의 SUP-08 코드 선택 행(`vllm-input-validation-http/1`, `native_input`)만 있다.
- `runtime/`에 판본 문자열 비교 3곳이 남았다(M-A5).
- 2차 지시서 이후 main에 들어온 A 작업은 #246(SUP-08 PoC)뿐이다.

## 2. 분담: A는 SUP-08, B는 SUP-08을 피한다

**A는 SUP-08 PoC를 MVP 제품 경로까지 끌고 간다(M-A8). B는 SUP-08을 맡지 않고, SUP-08 전용 파일을 고치지 않는다.** A의 나머지 작업(M-A1~A7)은 B 작업의 선행 조건이라 A가 계속 맡되, A 인력이 SUP-08에 묶여 늦어지면 결정 8로 담당을 다시 정한다.

### 2.1 A 전용 파일 (B는 고치지 않는다)

| 파일 | 내용 |
|---|---|
| `packs/PACK-BASELINE/checks/SUP-08.yaml`과 @34의 SUP-08 정의 | 코드 선택 정의(revision 5) |
| `tools/build_code_selection_pack.py`, `tools/prepare_sup08_phi3v_fixture.py` | 파생 팩·무해 fixture 준비 |
| `docs/gpu-a/policy-code-selection-draft.yaml`, `docs/gpu-a/RESULTS-20261111-SUP08.md`, `docs/evidence/sup08-code-selection-20261111.json` | SUP-08 정책 초안·결과 |
| `tests/test_code_selection_observation.py`, `tests/test_sup08_phi3v_fixture.py` | SUP-08 시험 |
| `docs/phase1/gpu.yaml`의 SUP-08 항목 | MVP 동안 A가 고친다(DECISIONS 2026-11-12). 같은 파일의 다른 항목은 B가 고친다 |

### 2.2 함께 쓰는 파일 (부분 소유)

| 파일 | A 부분 (SUP-08) | B 부분 |
|---|---|---|
| `src/guard/executors/request_field_probe.py` | `CODE_SELECTION_PROFILE` 경로: `code_module_settings`·`challenge_revision`·`control_body`·`nested_fields`·`classify_modules`와 코드 선택 관측 항목 | `REUSED_TOKEN_PROFILE` 경로(REQ-08), 공통 `send` |
| `src/guard/executors/kubernetes_input_validation.py` | 코드 선택 설정 키(`code_module_*`·`code_marker_root`·`unapproved_revision`·`control_image`)와 `_contract`의 코드 선택 분기 | REQ-08·09 분기, `runner_capture`, `request_input_checks` 확인 |
| `src/guard/runtime/kubernetes_http.py` | `engine_code_modules`와 모듈 목록 스크립트 | 그 밖의 수집 경로 |
| `src/guard/adapters/assets/engine_profiles.json` | `vllm-input-validation-http/1` 행 | 다른 판본 행·기능 |
| `tools/gpu_a_run_inputs.py`, `tools/gpu_a_aws_lab.py` | A 실행 입력 하네스 | B는 자기 도구(`tools/gpu_b_*`, `tools/b_*`)를 쓴다 |

함께 쓰는 파일 규칙:
- 상대 부분의 함수·설정 키·profile 상수·관측 항목 이름은 바꾸지 않는다. 바꿔야 하면 PR 설명에 적고 상대에게 먼저 알린다.
- 같은 파일을 두 PR이 고치면 먼저 병합된 쪽 위로 나중 PR을 맞추고, 나중에 병합하는 쪽이 `tools/verify_req10_consumption.py --write`로 소스 목록을 다시 만든다(#240·#246 충돌과 같은 방식).
- 두 판본 실행 세션(J01·J02)의 입력은 A·B가 각자 만들고 M-A7에서 하나의 세션 계획으로 합친다.

## 3. 단계와 순서

| 단계 | 내용 | 시작 조건 |
|---|---|---|
| 0. GPU 없이 (지금) | A: M-A1~A9, B: M-B1~B6 | 바로 |
| 1. TP2 세션 J01 | REQ-14·16·21, LIF-08·11, SUP-06을 두 판본에서 실행, 생산자 확인·등록 | M-A2·A3·A4·A5 병합, 해당 관측기의 두 판본 고정값, AWS 할당량(결정 6) |
| 2. 두 판본 정본 세션 J02 | J01 외 63개를 `guard update`로 기준·후보 순차 실행 | M-A4·A5·A6·A7·A8·A9, M-B2~B6 입력, 결정 10 승인, 비용 승인(결정 1) |
| 3. 공식 재판정 J03 | `official=true` RPT | J01·J02, 정책 CONFIRMED, 팩 검토 |

- **가장 긴 경로는 M-A4(@34) → J02 → J03이다.** M-A6(판본 적용표·고정값)이 J02 범위를 정하므로 M-A4와 나란히 가장 먼저 시작한다.
- `guard update` 한 번이 기준·후보를 순차로 실행하므로 2차 지시서의 "정본 실측"과 "두 판본 비교" 세션을 J02 하나로 합쳤다. 같은 이미지·모델·기동 설정의 점검은 상태 독립성을 확인한 뒤 한 번의 기동으로 묶는다.

## 4. A 작업 (개발 담당)

### M-A8. SUP-08 코드 선택 MVP (A의 주 작업)
- #246 PoC(동적 모듈 목록 + import 표식, `approved_code_revisions`, 중첩 필드)를 @34 SUP-08 정의로 올린다(결정 7, M-A4와 같은 PR 또는 바로 뒤).
- 두 판본에서 PoC와 같은 모델 snapshot·무해한 승인/비승인 processor fixture로 정본 실행 입력을 만든다. PoC 결과(DRAFT/EXAMPLE, `official=false`)를 정본 결과로 옮기지 않는다.
- 완료 기준 4(`REGRESSED`)의 근거를 남긴다: 기준 쪽 `ignored`, 후보 쪽 `consumed`, 비교 조건 차이 없음.
- `kubernetes_http.py`의 모듈 목록 스크립트가 쓰는 Transformers 캐시 경로 규칙은 라이브러리 고유 지식이다. 두 판본에서 다르면 adapter 데이터로 옮긴다.
- 완료: 직접 시험, J02 입력 사전 점검 통과, PR 병합.

### M-A1. 규칙·문서 정리 (작게, 먼저)
- 공통 입력 "`/2`" 내용을 `/4`로 옮긴다(11-09 결정, `gpu-common-inputs-196.md`, `CLAUDE.md`·`AGENTS.md` 2절). 내용은 그대로, 번호만.
- 2차 지시서는 요점을 PROGRESS에 남기고 지운다(7.6절).
- 결정 5(검토 기록 규칙)를 6절 소스 고정 목록 규칙에 반영한다.
- 완료: 문서 PR 병합.

### M-A2. 공통 입력 계약 `/4` 구현
- 반복 행 요약(첫·끝 원 행, 상한 4,096), V2 sampler `logit_bias` 비교, KV 엔진 고유 단위+단위 필드, `limit_enforced` 모든 한도 집행+방식 필드, 생산자 신원에 공통 감싸기 기반 파일 포함.
- 생산자 변환(`collectors/gpu_input_producers.py` 등)과 B 소비자 형식을 함께 맞춘다(M-B1).
- 완료: `/1`·`/4` 직접 시험, B 소비자가 두 판본을 모두 읽음, PR 병합. 등록은 J01.

### M-A3. rank-fault 생산자 (LIF-08)
- 계약 종류와 판정 쪽(`executors/rank_failure_contract.py`)은 있고 엔진 안 관측기·변환이 없다. 사건: `fault_applied`·`fault_released`·`detected`·`client_delivered`·`recovered`.
- 완료: CPU 직접 시험, PR 병합. GPU 확인·등록은 J01.

### M-A4. 새 정본 팩 PACK-BASELINE@34 (최우선)
- 77개 `impact`를 11-08 초안(HIGH 33·MEDIUM 38·LOW 6)대로 넣는다.
- SUP-11 `-OO` 지원 범위 밖 표기(판정 규칙은 그대로).
- 확정 정책 값 5개(REQ-03·13·17, OBS-05, LIF-13, 11-08 결정 6-1)를 정본 정책 파일(`verification.values`)에 넣는다.
- SUP-08 정의는 M-A8과 맞춘다.
- REQ-12의 사건·점검 선언·투영 함수를 B에 반환한다(#240 인계의 A 반환 요청).
- 과거 팩·결과는 소급해서 바꾸지 않는다.
- 완료: 팩 검토(결정 5), 현황표 재생성, PR 병합.

### M-A5. 판본 문자열 비교 제거와 검사 시험 (MVP 필수)
- 남은 비교를 지원표 조회로 바꾼다. 이대로면 MVP 두 판본에서 해당 수집 경로가 열리지 않는다.
  - `src/guard/runtime/kubernetes_http.py`의 `ROUTE_AUTHENTICATION_PROFILE` 분기(엔진 이름 비교)
  - `src/guard/runtime/kubernetes_startup_binding.py`(판본 비교)
  - `src/guard/runtime/kubernetes_worker_readiness.py`(판본 비교)
- 세 파일의 bytes는 수집기 신원과 `runtime/reviewed_http_contract.py` 고정값에 쓰인다. 고정값·소스 목록·검토 기록을 같은 PR에서 갱신한다(결정 5).
- 완료 기준 5의 검사 시험을 넣는다: `tests/test_engine_neutral_core.py`의 범위(core·models·store)를 `executors/`·`runtime/`·`collectors/`·`judges/`의 판본 문자열 비교까지 넓힌다(주석·문서 문자열 제외).
- 완료: 기존 판본 동작 동일, 관련 B 시험 통과, PR 병합.

### M-A6. 판본 적용표와 두 판본 고정값 (J02 범위를 정함)
- **선행 확인 (가장 먼저, 보고한다):**
  - 두 판본 이미지 안 엔진 Python 판본. 관측기는 엔진 Python 3.12만 감싼다(예: `adapters/vllm_cache_callback.py`의 `sys.version_info[:2] != (3, 12)` 거부). 3.12가 아니면 결정 11로 올리고, 그 전에는 그 판본 관측기 작업을 시작하지 않는다.
  - 판본마다 기본 엔진(V0·V1), 기동 CLI 옵션, 점검이 쓰는 기능(구조화 출력, tool calling, 멀티모달, prefix cache, 동적 import 등)의 유무 표.
  - 정본 시험 모델(REQ-16 Qwen2-VL-2B-Instruct, SUP-08 PoC snapshot, 기본 모델)이 두 판본에서 같은 revision으로 뜨는지. 안 뜨면 교체안을 낸다(결정 12).
- **판본 고정값 갱신 도구:** 두 판본의 태그 소스와 실제 이미지 설치 파일에서 감싼 함수 코드를 함수 단위로 비교한다. 같으면 고정값 이월을 제안하고, 다르면 검토 목록을 낸다. 고정값은 실제 이미지 bytes와 대조하기 전까지 후보다.
- **판본 적용표:** 관측기·공통 입력 생산자·수집 경로마다 판본별로 다음 중 하나로 분류하고, 점검별 예상 결과로 묶는다(0.3절).
  - (a) 대상 함수가 같음 → 고정값만 갱신
  - (b) 대상 함수가 옮겨지거나 이름만 바뀜 → 판본별 대상표(adapter 데이터)를 더함
  - (c) 구조가 달라 관측 의미를 새로 설계해야 함 → 판본 profile로 고르는 adapter 관측기·생산자를 설계
  - (d) (c) 중 너무 오래 걸리거나 어려운 것 → 원인·영향 점검을 적어 UNKNOWN 승인 요청(결정 10)
- 한 판본에 기능 자체가 없는 점검은 비적용/UNKNOWN 판단이 판정 의미 변경이라 점검별 승인을 받는다(결정 9). 승인 전 기본은 UNKNOWN이다.
- A 담당 점검이 쓰는 관측기의 (a)~(c)를 구현하고 두 판본 고정값 행과 `engine_profiles.json` 행을 더한다. B 담당 관측기는 M-B4가 한다. 판본마다 관측 코드를 복사하지 않고, 같은 관측 코드에 판본별 대상·고정값 데이터를 넘기는 구조를 먼저 쓴다. 구조가 달라 공유할 수 없을 때만 판본 전용 adapter 관측기를 두고 판본 profile로 고른다(완료 기준 5).
- 판본별 기동 설정(CLI 옵션·기본 엔진 선택)은 provider와 판본 profile에 둔다.
- `adapters/vllm.py`의 판본별 표(HTTP 경로표·길이 오류 형식 등)에 두 판본 행을 더한다. 판본 문자열로 분기하는 코드는 판본 행 조회로 바꾼다.
- `adapters/assets/vllm_change_rules.yaml`로 기준 → 후보 소스 diff 보고를 확인한다. 두 판본 사이에 옮겨진 경로를 반영해 바뀐 영역 표시가 맞게 한다.
- 완료: 선행 확인 보고, 도구 시험, 판본 적용표(분류 a~d) 보고와 결정 9·10 승인, (a)~(c) 직접 시험, 두 판본 지원 profile 조회 성공, PR 병합. (c)가 많으면 M-A6이 가장 긴 경로가 된다. 적용표 보고 뒤 담당·순서를 다시 잡는다(결정 8).

### M-A7. A 담당 점검 실행 입력과 J01·J02 세션 계획
- 대상 A 27개: lab 완료 8개, 확정 값 5개, 1차 제외 8개 UNKNOWN 기록, TP2 3개(J01), 비적용 2개 근거 기록, SUP-08(M-A8).
- 실행 입력(target·prepared·정책 파일)을 `tools/gpu_a_run_inputs.py`·`tools/gpu_a_aws_lab.py`·`docs/dev-1/tp2-run-plan.md`로 두 판본에 대해 만든다. 판본·이미지는 0.1절 표에서 읽는다.
- **세션 계획:** A·B 입력을 합쳐 J01·J02의 묶음·순서·예상 시간·예상 비용을 표로 만든다. 판본 적용표(M-A6)의 예상 공백을 함께 적는다. 결정 1·6의 근거다.
- 완료: 입력 사전 점검 통과, 세션 계획 보고.

### M-A9. CPU 이미지 경로 정리 (2026-11-12 결정)
- CPU용 vLLM 이미지 대상 코드를 제거하거나 GPU 조건으로 바꾼다. 대상:
  - `cpu-bounded` 기동과 기동 생성 확인: `adapters/vllm.py`, `adapters/engine_provider.py`, `runtime/generation_readiness.py`
  - REQ-09 CPU 길이 원인 증거(선택 증거): `adapters/vllm_length_cause.py`, `adapters/vllm.py`의 `input_length_cause_collector`. 빼도 REQ-09 기본 경로는 그대로다. REQ-09는 B 점검이라 B에 알린다(2.2절 규칙).
  - 두 이미지 비교 수락 조건: `update_validation.py`·`update_acceptance.py`의 `cpu-bounded`·`gpu_count == 0`·CPU 일치 조건 → GPU 조건(결정 13). `guard update`가 J02에서 두 GPU 이미지를 받으려면 필요하다.
- 파일 이름만 보고 지우지 않는다. 시험·도구마다 제거 / GPU 전환 / 유지로 나눈 분류표를 먼저 낸다. 유지 대상: vLLM 소스 분석 실행기(`*_cpu.py` 중 소스만 읽는 것, 2차 A09 이동 대상), 모의 엔진·고정 자료 시험.
- 과거 CPU 이미지 결과(2026-10-24 두 이미지 CPU 비교 등)는 기록으로 남기고 소급해서 지우지 않는다.
- 완료: 분류표 보고, 기존 GPU 경로 동작 동일, 관련 직접 시험 통과, PR 병합. **J02 전에 필수다.**

## 5. B 작업 (GPU 실증 담당, SUP-08 제외)

### M-B1. 공통 입력 판정 연결 마감
- 대상: LIF-03·05·06·07·09·10·11·12·14, REQ-08·09·10·12, OBS-02·03, PLT-01·02·04, SUP-03·06.
- #240이 기존 공통 입력의 소비 경로와 REQ-08 native 거부 소비를 연결했다. 남은 것은 `/4` 소비(M-A2), REQ-12 점검 선언 반영(M-A4)이다.
- `/1`~`/4`를 모두 읽고, 등록되지 않은 생산자 기록으로 PASS를 만들지 않는다.
- REQ-08·09는 SUP-08과 파일을 함께 쓴다. 2.2절 규칙을 따른다.
- 완료: 직접 시험, PR 병합.

### M-B2. CPU 개발 남은 29개 마감과 두 판본 실측 입력
- 묶음별로 두 판본 실측 입력을 준비하고 입력 사전 점검을 통과시킨다.
  - 공급·기동: SUP-07·09·10·11·12·15, LIF-01, 측정 남음 SUP-05·PLT-03·PLT-05·LIF-02
  - 입력·샘플링·템플릿: SUP-03, REQ-09·10, 측정 남음 REQ-07·SUP-13 (SUP-08은 A)
  - 수명 주기: LIF-03·04·05·06·07·09·10·12·14·16
  - 플랫폼: PLT-02·06, REQ-05
  - 관측: OBS-02·03, REQ-12
  - lab 완료 6개(SUP-04, REQ-08, PLT-01·04·08·09): @34에서 두 판본 정본 실행 입력
- CPU 개발 마감은 "GPU에서 제품 경로로 실행되어 EVD를 남기는 실행 경로"까지다. 판본 적용표에서 공백인 쪽은 `ENGINE_VERSION_UNSUPPORTED`·UNKNOWN과 원인을 남긴다.
- `docs/phase1/gpu.yaml`을 실제 상태로 갱신한다(SUP-08 항목 제외). CPU 결과만으로 DONE을 올리지 않는다.
- 완료: 묶음별 입력 사전 점검 통과.

### M-B3. TP2 분기 입력 (LIF-08·11, SUP-06)
- #240의 TP2 입력 계획을 두 판본 기준으로 고친다. 정상·장애·복구 대조와 시간표를 J01 전에 확정한다.
- 필요한 A 반환: LIF-08 peer/rank 제어, LIF-11 실패 group 회수·복구, SUP-06 rank별 required/assigned/loaded 지원 범위와 원자료.

### M-B4. B 담당 관측기의 두 판본 고정값
- M-A6의 갱신 도구와 판본 적용표로 B 담당 점검이 쓰는 관측기(`adapters/vllm_*.py`, `adapters/vllm_runner_capture.py` 등)의 두 판본 고정값 행을 더한다.
- M-A6과 같은 분류(a~d)를 B 담당 관측기·생산자에 적용하고 (a)~(c)를 구현한다. 함수 코드가 다르면 관측 의미를 다시 검토한다. 다른 판본을 받으려고 고정값 검사를 넓히지 않는다. (d)는 원인·영향 점검을 적어 결정 10으로 승인 요청한다.
- TP2 분기(M-B3)의 rank·worker 관측도 두 판본 구조를 각각 확인해 분류한다.
- 완료: 분류 보고, 판본별 고정 자료(`tests/fixtures/vllm-<판본>/`)로 직접 시험, PR 병합.

### M-B5. SUP-16 실제 수집 완성
- #240 인계 기준: GB12 회수 자료는 SHA·CRC 확인까지다. 정상·부분 파일·복구의 실제 수집과 제품 판정이 남았다. journal `BOUND_REACHED`, 부분 파일 후보 없음, install·첫 emit·Writer 원인 미확정 상태를 해소한다.
- 원 R8 전체 FAIL과 읽기 수락 UNKNOWN은 유지한다. 회수 성공을 PASS로 세지 않는다.
- 완료: CPU 준비 → J02에서 두 판본 실제 수집.

### M-B6. 기업 입력이 필요한 B 점검
- PLT-08(1차 제외): 정본 팩 실행, UNKNOWN+원인.
- PLT-03·05, SUP-04, REQ-05·07의 기업 입력 분기: EXAMPLE 정책으로 실행하고 미입력 분기는 UNKNOWN으로 남긴다.

### M-B7. 비교 결과 원인 기록 (J02 뒤)
- B 담당 점검의 비교 결과(`NO_CHANGE`·`REGRESSED`·`NOT_COMPARABLE` 등)와 원인을 기록한다. 판본 적용표의 예상과 다른 결과는 따로 적는다.

## 6. 합동 세션

### J01. TP2 GPU 세션
- g6.12xlarge(L4 4장 중 2장). REQ-14·16·21, LIF-08·11, SUP-06을 기준·후보 두 판본에서 순차 실행한다(REQ-16 모델 Qwen2-VL-2B-Instruct는 EC2 안에서 내려받기).
- `/4` 생산자 8종·rank-fault·failed-group-resources·ipc-ownership·worker-shard-load의 GPU 확인과 등록(판본별).
- 11-08 승인 상한($20, EBS 별도)은 한 판본 기준이었다. 두 판본에 맞는 상한을 세션 계획과 함께 다시 승인받는다(결정 6).
- 마감: 증거 반출 SHA 대조, 인스턴스 유형 복구, stopped 확인. 상한에 닿으면 그 자리에서 멈추고 정리한다.

### J02. 두 판본 정본 세션 (단일 L4)
- J01 외 63개를 PACK-BASELINE@34로 `guard update` 실행(기준 → 후보 순차). 계획은 M-A7 합본을 따른다.
- SUP-08은 A 입력(M-A8)으로 같은 세션에서 실행한다.
- 점검·쪽마다 EVD·RES와 비교 RPT의 정확 Ref를 `docs/evidence/` 공개 요약과 현황표에 연결한다.

### J03. 공식 재판정
- 정책 확정자(사용자)가 정책을 CONFIRMED로 확정하고 팩 검토를 마친 뒤, J01·J02 결과로 `official=true` RPT를 만든다.
- MVP 완료 보고: 공개 실측 요약, 현황표, 비교 RPT, 공식 RPT의 정확 Ref, 판본 적용표 대비 실제 공백.
- 외부 의존: RPT 화면 변환(verification-result)은 platform 담당(#226)이다. MVP 시연 전에 공식 RPT 형식을 넘긴다.

## 7. 사용자 결정이 필요한 것

| # | 결정 | 필요한 때 | 권장 |
|---|---|---|---|
| 1 | J02 비용 상한 | J02 전 | M-A7 세션 계획과 판본 적용표 보고 뒤 승인 |
| 2 | MVP 판본 표의 이미지 digest 확정 | M-A6 전 | #246에서 고정한 두 digest를 그대로 쓴다 |
| 3 | 완료 기준 4를 SUP-08 실제 회귀로 채우는 것 | M-A8 | 그대로 승인(일부러 넣는 회귀 사례는 만들지 않음) |
| 4 | 공통 입력 번호 `/4` | M-A1 | `/2`·`/3`은 이미 사용 중이라 `/4`로 확정 |
| 5 | 소스 검토 기록·팩 검토 담당 | M-A1, 모든 제품 소스 PR | 11-12 결정은 #240·#246만 B 검토를 면제했다. MVP까지 계속 면제할지, B 또는 대리 검토자를 둘지 정한다. 면제하면 `reviewed_by`에 면제를 밝히는 방식을 규칙으로 적는다 |
| 6 | TP2 할당량(G and VT vCPU 8→48)과 두 판본 J01 상한 | J01 전 | 승인 상태 확인. 늦어지면 J02를 먼저 하고 TP2 6개는 J01 뒤 합친다 |
| 7 | SUP-08 정본 정의 | M-A4·M-A8 | #246의 코드 선택 정의(revision 5)를 @34 정본에 넣는다 |
| 8 | A의 M-A1~A7 담당 | 단계 0 중 | A가 SUP-08(M-A8)에 묶여 M-A4·A5·A6이 늦어지면, M-A5·M-A6을 B 또는 다른 담당에 넘길지 정한다. 이 셋이 J02의 선행 조건이다 |
| 9 | 한 판본에 기능이 없을 때 판정 (비적용 / UNKNOWN) | M-A6·M-B4 적용표 보고 뒤 | 판정 의미 변경이라 점검별 승인. 승인 전 기본은 UNKNOWN (DECISIONS G3) |
| 10 | UNKNOWN으로 남길 어려운 항목 목록 | M-A6·M-B4 적용표 보고 뒤, J02 전 | 분류 (d) 항목을 원인·영향 점검과 함께 보고받아 항목별 승인. PASS·NOT_APPLICABLE로 바꾸지 않는다 |
| 11 | 엔진 이미지 Python이 3.12가 아닐 때 | M-A6 선행 확인 뒤(해당 시) | 관측기 공통 기반의 Python 판본 조건을 넓힐지, 그 판본 관측을 UNKNOWN으로 둘지 (DECISIONS G2) |
| 12 | 시험 모델 교체 | M-A6 선행 확인 뒤(해당 시) | 두 판본에서 모두 뜨는 같은 revision 모델로 바꾸는 안 |
| 13 | 두 이미지 비교의 GPU 수락 조건 | M-A9 | 양쪽 GPU 모델·드라이버·GPU 수·TP 구성이 같아야 비교. 기존 CPU 조건과 같은 원칙 (DECISIONS G1) |

## 8. 점검별 배정표 (69개)

"MVP 담당"은 이 문서의 분담이다(SUP-08만 현황표 담당과 다르다). 현재 상태는 main `f1f3f301`의 현황표 기준이다.

| 점검 | MVP 담당 | 현재 상태 | MVP까지 할 일 | 세션 |
|---|---|---|---|---|
| SUP-03 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| SUP-04 | B | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| SUP-05 | B | 측정 남음 | 두 판본 실측 입력 확정(M-B2) | J02 |
| SUP-06 | B | CPU 개발 남음 | M-B1 판정 연결 + M-B3 TP2 입력, TP2 두 판본 실행 | J01 |
| SUP-07 | B | CPU 개발 남음 | CPU 개발 마감 + 두 판본 실측 입력(M-B2) | J02 |
| SUP-08 | A | CPU 개발 남음 | M-A8: 코드 선택 PoC를 @34 정의로 정본화, 두 판본 실행에서 `REGRESSED` 확인 | J02 |
| SUP-09 | B | CPU 개발 남음 | CPU 개발 마감 + 두 판본 실측 입력(M-B2) | J02 |
| SUP-10 | B | CPU 개발 남음 | CPU 개발 마감 + 두 판본 실측 입력(M-B2) | J02 |
| SUP-11 | B | CPU 개발 남음 | CPU 개발 마감 + 두 판본 실측 입력(M-B2) | J02 |
| SUP-12 | B | CPU 개발 남음 | CPU 개발 마감 + 두 판본 실측 입력(M-B2) | J02 |
| SUP-13 | B | 측정 남음 | 두 판본 실측 입력 확정(M-B2) | J02 |
| SUP-15 | B | CPU 개발 남음 | CPU 개발 마감 + 두 판본 실측 입력(M-B2) | J02 |
| SUP-16 | B | 측정 남음 | M-B5: 정상·부분 파일·복구 실제 수집 경로 완성, 두 판본 실행 | J02 |
| REQ-01 | A | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| REQ-02 | A | 기업 입력 대기 | 두 판본에서 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| REQ-03 | A | 기업 입력 대기 | 확정 정책 값(11-08 6-1)으로 두 판본 실행 | J02 |
| REQ-04 | A | 대상 비적용 | 정본 선정에서 비적용 근거 기록 | - |
| REQ-05 | B | CPU 개발 남음 | CPU 개발 마감 + 두 판본 실측 입력(M-B2) | J02 |
| REQ-06 | A | 대상 비적용 | 정본 선정에서 비적용 근거 기록 | - |
| REQ-07 | B | 측정 남음 | 두 판본 실측 입력 확정(M-B2) | J02 |
| REQ-08 | B | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| REQ-09 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| REQ-10 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| REQ-11 | A | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| REQ-12 | B | CPU 개발 남음 | M-A4 점검 선언·투영 함수 반환 → M-B1 판정 연결 | J02 |
| REQ-13 | A | 기업 입력 대기 | 확정 정책 값(11-08 6-1)으로 두 판본 실행 | J02 |
| REQ-14 | A | GPU 확인만 | TP2 두 판본 실행 | J01 |
| REQ-15 | A | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| REQ-16 | A | GPU 확인만 | TP2 두 판본 실행 | J01 |
| REQ-17 | A | 기업 입력 대기 | 확정 정책 값(11-08 6-1)으로 두 판본 실행 | J02 |
| REQ-18 | A | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| REQ-19 | A | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| REQ-20 | A | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| REQ-21 | A | GPU 확인만 | TP2 두 판본 실행 | J01 |
| PLT-01 | B | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| PLT-02 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| PLT-03 | B | 측정 남음 | 두 판본 실측 입력 확정(M-B2) | J02 |
| PLT-04 | B | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| PLT-05 | B | 측정 남음 | 두 판본 실측 입력 확정(M-B2) | J02 |
| PLT-06 | B | CPU 개발 남음 | CPU 개발 마감 + 두 판본 실측 입력(M-B2) | J02 |
| PLT-07 | A | 기업 입력 대기 | 두 판본에서 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| PLT-08 | B | lab 완료 | 두 판본에서 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| PLT-09 | B | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| PLT-10 | A | 기업 입력 대기 | 두 판본에서 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| PLT-11 | A | 기업 입력 대기 | 두 판본에서 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| PLT-12 | A | 기업 입력 대기 | 두 판본에서 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| LIF-01 | B | CPU 개발 남음 | CPU 개발 마감 + 두 판본 실측 입력(M-B2) | J02 |
| LIF-02 | B | 측정 남음 | 두 판본 실측 입력 확정(M-B2) | J02 |
| LIF-03 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| LIF-04 | B | CPU 개발 남음 | CPU 개발 마감 + 두 판본 실측 입력(M-B2) | J02 |
| LIF-05 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| LIF-06 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| LIF-07 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| LIF-08 | B | CPU 개발 남음 | M-A3 rank-fault 생산자 + M-B1 판정 연결 + TP2 두 판본 실행 | J01 |
| LIF-09 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| LIF-10 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| LIF-11 | B | CPU 개발 남음 | M-B1 판정 연결 + M-B3 TP2 입력, TP2 두 판본 실행 | J01 |
| LIF-12 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| LIF-13 | A | 기업 입력 대기 | 확정 정책 값(11-08 6-1)으로 두 판본 실행 | J02 |
| LIF-14 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| LIF-16 | B | CPU 개발 남음 | CPU 개발 마감 + 두 판본 실측 입력(M-B2) | J02 |
| OBS-01 | A | 기업 입력 대기 | 두 판본에서 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| OBS-02 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| OBS-03 | B | CPU 개발 남음 | M-B1 판정 연결 + 두 판본 실측 입력(M-B2) | J02 |
| OBS-04 | A | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| OBS-05 | A | 측정 남음 | 확정 정책 값(11-08 6-1)으로 두 판본 실행 | J02 |
| OBS-06 | A | 기업 입력 대기 | 두 판본에서 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| OBS-09 | A | lab 완료 | @34에서 두 판본 정본 실행 | J02 |
| OBS-11 | A | 기업 입력 대기 | 두 판본에서 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |

## 9. 공통 규칙 (요약)

- 실제 엔진 실증은 GPU 이미지로만 한다. CPU용 vLLM 이미지 실증을 하지 않고, GPU 없는 개발·CI는 모의 엔진과 고정 자료로 한다(2026-11-12).

- 엔진·판본에 묶이지 않는다. 새 코드에 엔진 이름·판본 문자열 비교를 쓰지 않고, 판본 지원은 `engine_profiles.json`과 provider로 정한다. 이 문서도 판본은 0.1절 표에만 적는다.
- 제품 소스를 바꾸면 `uv run python tools/verify_req10_consumption.py --write`로 소스 목록을 다시 만들고 검토 기록을 넣는다(결정 5). 나중에 병합하는 쪽이 다시 만든다.
- 전체 기본 시험은 PR의 Linux CI(CPython 3.12.3)로 확인한다. 로컬에서는 바꾼 부분의 직접 시험과 `tools.checklist_status --check`만 한다.
- 현황표 DONE은 lab 단계 완료이고 보안 PASS·기업 승인이 아니다. CPU·대역 결과를 GPU 실측으로 올리지 않는다. 한 판본의 결과를 다른 판본으로 옮기지 않는다.
- GPU는 준비된 시험 묶음과 비용 승인 범위에서만 쓰고, 세션마다 정리와 stopped 확인을 기록한다.
- update 작업에서 `../runtime/`은 고치지 않는다.
