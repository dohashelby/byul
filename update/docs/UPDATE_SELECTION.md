# 변경 유형별 시험 선정 (U1) — 설계

> 작성 2026-10-24, 개정 2026-10-25 · 상태: **설계 확정, 구현 전**. 사용자와 대화로 합의한 내용이다. 구현·실측 완료를 뜻하지 않는다.
> 구현 지시는 [UPDATE_SELECTION_PROMPT.md](../UPDATE_SELECTION_PROMPT.md), 결정 기록은 `DECISIONS.md`의 "U1" 절이다.
> 2026-10-25 개정: 엔진 **정식** 릴리스는 줄이지 않고 전체 시험한다. 엔진 **패치** 릴리스(0.xx.1, 0.xx.2 …)는 소스 diff로 필요한 점검만 골라 시험한다. 엔진은 그대로이고 모델·환경·정책이 바뀔 때도 골라서 시험한다. 2026-10-24 판의 "모든 엔진 릴리스에 diff 선정"은 폐기했다(§9).

## 1. 변경 유형과 시험 범위

| 변경 유형 | 판별 | 시험 범위 | 실행 |
| --- | --- | --- | --- |
| **엔진 정식 릴리스** | 엔진 이미지가 바뀌고, 버전의 major·minor가 바뀜(예: 0.31.x → 0.32.0) | 적용되는 점검 **전체**. 줄이지 않고 이월하지 않는다 | 기존 `guard update`: 기준·후보 두 이미지 실행·비교 |
| **엔진 패치 릴리스** | 엔진 이미지가 바뀌고, major·minor는 같고 패치 자리만 올라감(예: 0.32.0 → 0.32.1, 0.32.1 → 0.32.3, 0.10.1 → 0.10.1.1) | 항상 시험 20개 + 소스 diff의 변경 종류에 걸리는 점검 + 입력 변경에 걸리는 점검. 나머지는 이월(§3.4) | 선정 점검만 기준·후보 두 이미지 실행·비교 |
| **모델 변경** | 엔진 이미지는 같고 모델(id·revision·snapshot inventory)이 바뀜 | 모델 변경 종류에 걸리는 점검 + 입력 변경에 걸리는 점검. 나머지는 이월 | 증분 감사: 새 구성 한 쪽 실행 |
| **환경·정책 변경** | 엔진 이미지·모델은 같고 ENV 사실·POL 값이 바뀜 | 바뀐 사실·값에 걸리는 점검. 나머지는 이월 | 증분 감사: 새 구성 한 쪽 실행 |

- 여러 유형이 겹치면 넓은 쪽을 따른다. 정식 릴리스면 모델·환경이 함께 바뀌어도 전체 시험이다. 패치 릴리스·모델 변경·환경 변경이 함께 있으면 각 선정의 합집합이다.
- 패치로 보지 않는 것: 버전이 내려감(롤백), rc·dev·커밋 단위 이미지, 버전 문자열을 확인할 수 없음, 같은 버전의 다른 digest. 모두 정식 릴리스처럼 전체 시험한다.
- 적용 판단(SPEC §4.4 규칙 1~5)은 모든 유형에서 먼저 적용한다. 근거 있는 `NOT_APPLICABLE`은 선정하지 않는다.
- 이전 결과(`--previous-plan`)가 없으면 이월할 근거가 없으므로 적용 점검 전체를 시험한다(첫 실행).

## 2. 프레임워크 근거

새 `checklist.json`(mg-princess/serving-security-framework `0ec5fb5fa5633b0817468b27684690a66fc7b4f5`)의 `framework_model`이 같은 흐름을 요구한다.

- `lifecycle`의 `LC-CHANGE`는 "엔진·모델·설정·의존성·배포 방식이나 정책을 변경할 때" 변경 영향을 검토해 대상과 분기를 고르라고 한다. `LC-INTAKE`는 필요한 시험을 격리 환경에서 하라고 한다.
- `lifecycle_policy.selection_rule`은 각 시점의 점검을 "검토 후보"로 두고 선정·비적용·미정 이유를 기록하라고 한다. `evidence_reuse_rule`은 증거 재사용 시 대상·구성·정책·시험 절차 판본의 유효성 확인과 원 실행 ID·출처·시각·한계·재사용 이유 기록을 요구한다.
- `engine_application.model_engine_distinction`은 "모델 변경은 실제 모델·파일·tokenizer·template·한도·코드/의존성 영향을 재평가"하라고 한다.
- 프레임워크에는 선정기 구현이 없다(`assessment_registry.selector_support`). 아래 규칙은 이 도구의 선정 규칙이다.

## 3. 엔진 릴리스: 소스 diff

정식 릴리스의 선정은 기존 update 모드와 같다(적용 점검 전체, 기준·후보 비교, INV-09). 패치 릴리스는 §3.4로 고른다. 두 경우 모두 **vLLM 소스 diff**(`guard diff`)를 붙인다. 정식 릴리스에서 diff는 아래 세 가지에만 쓰고, 패치 릴리스에서는 선정에도 쓴다.

1. **바뀐 영역 표시**: 보고서에 변경 종류별 파일 수와 관련 점검을 보인다(예: "TEMPLATE 38개 파일 → REQ-07·SUP-13"). 변경 종류 → 점검 표는 §3.2.
2. **도달 확인**: 바뀐 진입점을 실험이 실제로 시험했는지 대조한다(§3.3).
3. **체크리스트 공백 보고**: 어떤 점검에도 연결되지 않는 변경 영역을 "체크리스트 공백 후보"로 보인다(1차 실험에서 tool·reasoning 출력 파서 105개 파일).

### 3.1 소스 diff 추출 (`guard diff`)

엔진 전용 코드는 `src/guard/adapters/`에만 둔다(코어에 `vllm` 금지).

1. **소스 확보**: 설정의 저장소 URL(기본 `https://github.com/vllm-project/vllm.git`)을 캐시 디렉토리(기본 `.guard/cache/engine-src/vllm`, `guard.toml`로 변경)에 clone·fetch한다. 태그를 커밋 SHA로 고정해 CHG에 기록한다. 1차 실험에서는 `git clone --filter=blob:none`(11초)로 diff를 내고, 소스 파일이 필요할 때 `git clone --depth 1 --branch <tag>`(9초)를 썼다. blobless clone에서 `checkout`으로 파일을 펼치면 파일마다 따로 받아 매우 느리다(10분에 2,641개 중 1,037개). git은 시스템 `git`을 subprocess로 호출한다. 엔진 중립 git 래퍼는 `runtime/git.py`, 호출은 어댑터에서만.
2. **변경 목록**: `git diff --no-ext-diff --no-textconv --name-status <from>..<to>`. 이름 변경은 이전·이후 경로를 모두 분류한다. git은 항상 인자 배열로 호출하고 셸 문자열·외부 diff 도구·textconv를 쓰지 않는다. 파일 해시는 `git cat-file blob <commit>:<path>`의 원본 바이트로 계산한다(줄바꿈 변환 전). 분석 대상 소스는 텍스트·AST로만 읽고 import하거나 실행하지 않는다.
3. **경로 규칙**: `src/guard/adapters/assets/vllm_change_rules.yaml`이 경로 접두어 → 변경 종류를 정한다. 제외 규칙(`tests/`, `docs/`, `.buildkite/`, `benchmarks/`, `examples/`, `.github/`, `tools/`, `vllm/benchmarks/`, `rust/src/bench/`, 경로 안의 `/tests/`·`/benches/`·`/examples/`, `*.md` 등)도 둔다. 한 파일이 여러 규칙에 맞으면 종류를 모두 붙인다. 첫 내용은 1차 실험 규칙(`§8`)을 출발점으로 하되 실제 저장소 구조로 다시 확인한다.
4. **미분류**: 어떤 규칙·제외에도 맞지 않는 파일은 `category: OTHER`, `certainty: UNKNOWN`, `unmapped: true` 사실로 남기고, 보고서에 목록과 "규칙표에 추가할 후보"로 표시한다.
5. **이미지 대응 확인**: 기준·후보 ENV의 엔진 버전·`source_commit`이 diff의 태그·커밋과 맞는지 확인한다. 결과는 세 상태로 남긴다: **불일치**, **관계 미확인**, **관계 확인**(근거와 범위 보존). **불일치와 관계 미확인은 같은 처리를 한다**: 패치 릴리스에서는 그 diff를 시험 축소·이월 근거로 쓰지 않고 전체 시험한다. 정식 릴리스에서는 diff 보고를 붙이되 "소스–이미지 관계 미확인/불일치"를 한계로 적고 변경 검증 완료를 주장하지 않는다. 이미지 digest와 버전 문자열만으로 모든 소스가 같다고 추정하지 않는다. 가능하면 이미지 안의 관련 소스 파일 해시를 지정 커밋의 파일과 대조한다. 커밋을 확인할 수 없으면 "버전 문자열만 일치"를 한계로 적는다. 맞지 않으면 diff를 쓰지 않고 불일치를 한계로 적는다. 정식 릴리스는 원래 전체 시험이므로 범위가 바뀌지 않고, 패치 릴리스는 전체 시험으로 되돌린다(관계 미확인도 같다).
6. **근거 고정**: 변경 목록, 경로별 원본 SHA-256, 적용한 규칙 파일 해시, 미분류 목록을 하나의 근거 파일로 저장소의 내용 주소 artifact에 넣고 CHG가 참조한다. 현재 저장소 검증(`store/sqlite.py`)은 EVD의 `artifacts`만 확인하고 `ChangeFact.evidence`의 경로·해시는 확인하지 않으므로, CHG 근거 artifact도 `guard verify`·반출·복원의 누락·변조 검사 대상에 넣는다(§7).
7. **비교 조건과 분리**: 분석 시각, 캐시 디렉토리 같은 개인 경로, 임의 ID는 시험 조건 해시(`fixture_sha256`·`config_sha256`)에 넣지 않는다. CHG 참조는 PLAN 입력으로만 남긴다.
8. **import 전파는 하지 않는다**: 1차 실험에서 바뀐 모듈 1,027개를 import하는 모듈이 1,945개(전체 2,641개)였다. `vllm/logger.py`는 888개, `vllm/envs.py`는 298개 모듈이 import한다. 전파하면 패치도 거의 전체가 된다. 간접 영향은 §3.4의 항상 시험 20개로 보완한다.

CHG는 사람 검토 전이면 기존 규칙대로 `certainty` 최대 `PARTIAL`이다. diff 결과는 보고·선정 근거이며 판정 근거가 아니다.

### 3.2 엔진 변경 종류 → 관련 점검 (정식: 표시 / 패치: 선정)

변경 종류는 엔진 중립 이름이다. 표는 팩의 `change_selection.yaml`에 둔다.

| 변경 종류 | 관련 점검 |
| --- | --- |
| `API_ROUTE` | REQ-01, REQ-03, REQ-06 |
| `AUTH` | REQ-01, REQ-02, REQ-03, REQ-04, REQ-06, REQ-15 |
| `STREAMING` | LIF-04 |
| `INPUT_PROCESSING` | REQ-08, REQ-09, SUP-12 |
| `TEMPLATE` | REQ-07, SUP-13 |
| `SAMPLING` | REQ-10 |
| `MODEL_SELECTION` | REQ-05, LIF-06 |
| `CACHE` | REQ-15, REQ-16, REQ-17 |
| `RUNNER_KERNEL` | REQ-18, REQ-19, REQ-20, REQ-21 |
| `DISTRIBUTED_IPC` | REQ-14, PLT-02, PLT-04, PLT-06, PLT-08, LIF-13, SUP-12 |
| `MODEL_LOADING` | SUP-03, SUP-06, SUP-12, LIF-01, LIF-02 |
| `REMOTE_CODE` | SUP-08, SUP-09 |
| `PLUGIN` | SUP-10, SUP-11 |
| `COMPILE_CACHE` | SUP-07, SUP-15 |
| `SCHEDULER` | LIF-03 |
| `STARTUP_HEALTH` | LIF-07, LIF-09 |
| `FAILURE` | LIF-08, LIF-10, LIF-11, LIF-12 |
| `PROFILER` | LIF-14 |
| `USAGE_STATS` | OBS-04, OBS-05 |
| `REPLAY` | OBS-07, OBS-08 |
| `OBSERVABILITY` | OBS-02, OBS-03, OBS-09 |
| `OUTPUT` | REQ-11, REQ-12 |
| `ERROR` | REQ-13 |
| `OUTPUT_PARSING` | (없음 — 체크리스트 공백 후보) |
| `DEPENDENCY`, `IMAGE` | SUP-04 |
| `CLI_OPTION`, `ENV_VAR`, `GENERIC` | (영역 표시만) |

엔진 코드 영역과 무관한 인프라·절차 점검(승인·자격 철회·기록 저장소 등 22개)은 이 표에 없다. 정식 릴리스는 전체 시험이므로 표에 없어도 범위가 줄지 않는다. 패치 릴리스에서는 이 점검들이 diff로 선정되지 않으며, 입력 변경(§5)에 걸리지 않으면 이월 조건(§6)으로 이월된다. 엔진 코드 변경이 이들의 보호 속성을 바꾸지 않는다는 판단에 근거한다.

1차 실험에서 드러난 연결(미디어 디코딩·역직렬화 → SUP-12)을 반영했다. 정식 릴리스에서는 표시용이라 표가 틀려도 시험 범위가 줄지 않는다. 패치 릴리스에서는 이 표가 선정을 정하므로, 표에서 빠진 연결은 그 점검의 이월로 이어진다(§10).

### 3.3 도달 확인

목표: **이 배포에서 실제로 쓰는 진입점은 미도달 0**. 쓰지 않는 진입점은 비사용 근거(ENV 사실·`absence_evidence`)로 `NOT_APPLICABLE` 처리한다.

1. **실험 전(정적)**: diff에서 진입점 변경을 뽑는다. HTTP 라우트 외에 실행기가 다루지 않는 진입점(예: `rust/src/server/` Rust 프런트엔드, `vllm/entrypoints/grpc_server.py`)의 변경을 표시하고, ENV가 그 진입점을 쓴다고 하면 "원리상 미도달 변경"으로 실험 전에 알린다.
2. **실험 후(동적, 진입점 수준)**: 바뀐 진입점마다 다섯 단계를 따로 기록한다.
   1. 관련 점검이 선정됐는가
   2. 그 진입점이 점검 범위(`route_scope`·어댑터 분류)에 들어갔는가. 어댑터는 모르는 경로를 추론 경로로 분류하므로, 새 관리 경로가 REQ-03 범위에서 빠질 수 있다
   3. 필요한 역할·요청·안전 프로브가 있는가
   4. 실제로 시도했는가
   5. 필요한 관측을 수집했는가

   `routes_tested`에 이름이 있다는 것만으로는 부족하다. TCP 연결 차단(`basis: tcp_connect`), HTTP 응답, handler 도달은 서로 다른 확인 범위이므로, 그 변경에서 확인하려는 보안 속성에 맞는 근거인지 함께 적는다. 실행기가 다루는 진입점 목록과 ENV의 사용 진입점도 대조한다.
3. **처리**: 미도달 변경은 보고서에 목록으로 남기고 수동 시험 대상에 넣는다. 판정을 UNKNOWN으로 낮추는 것은 판정 규칙 변경이라 열린 질문 U1-Q2로 두고, 확정 전에는 보고만 한다.
4. 코드 수준 확인(엔진을 coverage 측정 모드로 실행)은 이번 범위가 아니다. 새 의존성·이미지 변경이 필요하고 Python 코드만 측정된다.

정적 분석 결과는 "도달 가능(추정)"이며 "도달함"의 근거가 아니다(INV-02·INV-11). 도달 확인은 실험 기록으로만 한다.

### 3.4 패치 릴리스 선정

1. **항상 시험(20개)**: 경로 규칙으로 추적할 수 없는 속성(엔진 코드 전반에 흩어진 속성)과 업데이트가 반드시 바꾸는 속성이다. 2026-10-24 사용자 결정("규칙 기반 분석이 안 되는 항목은 기본 포함")을 따른다.

   | 점검 | 이유 |
   | --- | --- |
   | SUP-04 | 이미지·패키지가 반드시 바뀐다 |
   | SUP-11 | 모든 실행 모드의 동적 import 제한 |
   | SUP-16 | 중단·부분 생성물 처리(취득·컴파일 전반) |
   | REQ-02, REQ-04 | 인증 경로·handler 권한 일치, 신원 사칭(middleware·router·전달 지점이 여러 곳) |
   | REQ-11, REQ-12, REQ-13 | 결과 귀속, 취소 뒤 늦은 결과, 오류 정보(engine core·출력·오류 처리 전반) |
   | PLT-07 | 외부 송신 목적지(송신 코드가 여러 곳) |
   | LIF-08, LIF-09, LIF-10, LIF-11, LIF-12 | 실패 감지·부분 실패·취소 정리·실패 자원 회수·재시도 잔존 |
   | OBS-02, OBS-03, OBS-09 | 실행 상태 기록, 수집 정보, 로그 위조 |
   | LIF-03, REQ-09, REQ-10 | 요청 자원 고갈, 입력 오류로 인한 엔진 중단. 모델 구현 파일 안에서도 생긴다(1차 실험의 GHSA-8397·GHSA-gq5q·GHSA-frvg) |

2. **diff 선정**: §3.1의 변경 종류가 §3.2 표에서 가리키는 점검.
3. **미분류**: 미분류 파일이 하나라도 있으면 §3.2 표의 모든 점검(56개, 항상 시험과 합치면 58개)을 선정하고 보고서에 미분류 목록과 "규칙표에 추가할 후보"를 표시한다.
4. **입력 변경**: §5와 같다.
5. **이월**: 위에 걸리지 않은 점검은 §6 조건으로 이월한다. 패치 릴리스에서는 이전 시험의 엔진 이미지가 **이번 기준(baseline) 이미지와 같아야** 한다(검증된 그 버전에서 출발하는 패치).
6. 이전 결과(`--previous-plan`)가 없거나, 소스–이미지 관계가 **불일치 또는 미확인**이거나, 패치 판별이 안 되면 전체 시험이다.
7. 선정된 점검은 기준·후보 두 이미지로 실행·비교한다(INV-09).

과거 패치 5개에 적용하면 25~38/77이 선정된다(§8).

## 4. 모델 변경: 골라서 시험

### 4.1 판별과 변경 종류

이전 PLAN의 ENV와 이번 ENV의 `engine.image_digest`가 같고 `model`(id·revision·`inventory_sha256`)이 다르면 모델 변경이다. 파일 단위 변경 종류를 알기 위해 모델 snapshot의 파일 목록(경로 → sha256)을 **모델 manifest**로 만들어 두 버전을 비교한다(`guard model-diff`, 결과는 CHG, `extractor.name: model_manifest`). 모델 파일 형식 규칙은 엔진 중립이므로 `collectors/`(예: `collectors/model_manifest.py`와 규칙 파일)에 둔다. 기존 `tools/model_inventory.py`의 snapshot 목록 계산을 재사용한다.

| 모델 변경 종류 | 판별(예) | 점검 |
| --- | --- | --- |
| `MODEL_WEIGHTS` | `*.safetensors`, `*.bin`, `*.pt`, `*.gguf`, `*.index.json` | SUP-03, SUP-05, SUP-06, SUP-16, LIF-01, LIF-02 |
| `MODEL_CODE` | snapshot 안 `*.py`, `config.json`·processor 설정의 `auto_map` 추가·변경 | SUP-08, SUP-09, SUP-12 |
| `MODEL_TEMPLATE` | `chat_template.jinja`·`chat_template.json`, `tokenizer_config.json`의 `chat_template` | SUP-13, REQ-07 |
| `MODEL_TOKENIZER` | `tokenizer.json`, `tokenizer.model`, `vocab.*`, `merges.txt`, `special_tokens_map.json`, `added_tokens.json`, `generation_config.json`, `config.json`의 `vocab_size`·`max_position_embeddings` | REQ-08, REQ-09, REQ-10 |
| `MODEL_ARCH` | `config.json`의 `architectures`·`model_type` 변경, 또는 모델 id 변경 | REQ-09, REQ-10, LIF-03, REQ-18, REQ-19, REQ-20, REQ-21 |
| (모든 모델 변경 공통) | — | REQ-05, REQ-16, SUP-15, LIF-05, LIF-06 |

- `MODEL_ARCH`는 그 모델이 처음 쓰게 되는 엔진 코드 경로를 시험하기 위한 것이다. 1차 실험에서 놓친 공지 중 Phi-4 오디오 OOM(GHSA-8397)·DeepSeek-OCR 엔진 중단(GHSA-gq5q)이 특정 모델에서만 실행되는 엔진 코드였다.
- 모델 id가 바뀌면(다른 모델로 교체) 모든 모델 변경 종류를 적용한다.
- 규칙에 없는 파일이 바뀌면 모든 모델 변경 종류를 적용한다(모르는 변경은 넓게).
- GPU 점검(REQ-18~21)은 적용 판단에서 GPU 비사용 근거가 있으면 `NOT_APPLICABLE`이다.
- LoRA·adapter 변경은 이번 범위가 아니다(U1-Q4).

### 4.2 입력 변경도 함께 본다

모델 변경 때도 §5의 입력 변경 선정을 같이 적용하고 합집합을 시험한다.

## 5. 환경·정책 변경: 골라서 시험

이전 PLAN의 ENV·POL과 이번 ENV·POL을 비교한다(입력 diff). 엔진 이미지와 모델은 같아야 한다.

| 바뀐 것 | 선정 |
| --- | --- |
| POL `values.<check_id>` | 그 점검 |
| POL `scope` | 새로 포함된 점검 |
| POL `exceptions` | 다시 시험하지 않는다(예외는 판정을 바꾸지 않음, INV-08). 보고서 표시만 갱신 |
| POL `status`(DRAFT→CONFIRMED 등) | 다시 시험하지 않는다. 보고서 공식 여부만 바뀐다 |
| ENV 사실 | `applies_when`이 그 사실을 참조하는 점검 + `change_selection.yaml`의 `fact_triggers`가 그 사실을 가리키는 점검 |
| 어느 점검과도 연결되지 않은 ENV 사실 | 적용 점검 전체(모르는 변경은 넓게, U1-Q3) |
| ENV `runtime`(노드·TP·GPU·driver) | `fact_triggers`의 `runtime.*` 항목 |

`fact_triggers`(ENV 사실 키 → 점검)의 첫 내용은 체크리스트의 구조화 필드(`applicability_criteria`, `test_branch_conditions`, `assessment_registry.environment_axes_document`)에서 만든다. 원문에서 연결을 끌어낼 수 없는 사실은 넣지 않고 "연결 없음"으로 남긴다(그 사실이 바뀌면 전체 시험).

## 6. 이월 (패치 릴리스와 증분 감사)

엔진 정식 릴리스는 이월하지 않는다. 패치 릴리스(§3.4)와 증분 감사(모델·환경·정책 변경)에서 선정되지 않은 점검만 이전 결과를 이월한다. 이월은 새 판정이 아니다(INV-19). **이월 단계 자체**에서는 EVD·RES를 추가 생성하지 않고, 아래 사전 확인을 거친 유효 RES를 정확한 Ref로 참조한다(INV-10 유지).

이월 조건(전부 만족):

- 이전 PLAN에 같은 점검의 RES가 있고 이월 자격 판단에 쓰는 유효 RES의 **보안 판정이 PASS**다(판정기 버전이 바뀌면 아래 사전 재판정한 RES 기준). FAIL·UNKNOWN은 다시 시험한다.
- 그 RES가 판정한 EVD의 엔진 이미지 digest가 **이번 ENV의 digest와 같다**(증분 감사). 패치 릴리스는 **이번 기준(baseline) ENV의 digest와 같다**.
- **같은 점검 정의**: 이전 PLAN 항목의 `check_revision`과 이번 팩 안 **같은 점검 ID**의 CheckDef revision이 같고, 이전 PLAN이 참조한 팩 보존본의 `checks/<ID>.yaml` 해시와 이번 팩 매니페스트(`files_sha256`)의 같은 파일 해시가 같다. 팩 전체 revision(예: 22)은 비교 대상이 아니다.
- **같은 시험 절차**: 그 EVD의 `execution.executor`(이름·버전·`config_sha256`)와 `execution.adapter`(이름·버전)가 이번 실행에 쓸 값과 같다.
- **같은 시험 자료**: 실행기가 고정 시험 자료(예: `comparison_routes`)를 쓰면 그 해시가 이전 EVD의 `comparability.fixture_sha256`과 같다. 시험 자료를 실행 중에 대상에서 만들어 실행 전에 같은 값을 확정할 수 없는 점검(예: 등록 경로를 실행 중에 수집하는 REQ-01·REQ-03)은 이월하지 않고 선정한다.
- **같은 실행 설정**: 이전 EVD의 `comparability.config_sha256`과 이번 설정이 같다. 다르면, 그 차이가 이번 변경 유형(모델 변경·입력 변경)으로 판별돼 트리거 계산에 들어간 경우에만 허용하고, 판별되지 않은 차이가 하나라도 있으면 이월하지 않는다.
- 이 점검이 이번 변경(패치의 항상 시험·diff 변경 종류, 모델 변경 종류, 입력 변경)에 걸리지 않았다.
- 이전 EVD의 `data_origin`이 `EXAMPLE`이 아니다.

**판정기 버전만 바뀐 경우 — 이월 자격 사전 확인**: 엔진 기동 없이 저장된 EVD를 현재 판정기로 재판정하고 새 RES를 append-only로 저장한다([SPEC §5 객체 관계](../SPEC.md#5-객체-관계)). 원본 EVD·RES와 정확한 Ref는 보존한다. 이 사전 재판정은 이월 단계와 구분하며, 재판정한 RES를 기준으로 위 조건을 본다. 재판정 결과가 보안 PASS가 아니면 이월하지 않고 선정한다. CheckDef의 판정 규칙(`judge.params`)이 바뀐 경우는 점검 정의가 바뀐 것이므로 같은 점검 정의 조건에서 걸린다.

PLAN 항목에 `carried_over: { result: Ref, evidence: Ref, image_digest, judged_at, reason }`를 남긴다. `result`는 사전 재판정했으면 새 재판정 RES의 정확한 Ref, 아니면 기존 유효 RES의 정확한 Ref다. `evidence`는 그 RES가 판정한 EVD의 정확한 Ref다. `judged_at`은 참조한 RES의 판정 시각이며, 원래 EVD의 실행 시각과 재판정 시각을 구분해 표시하고 재판정을 새 실측으로 표현하지 않는다. 보고서는 이월 항목을 별도 표로 보이고 상단에 "이번 실행 N건 / 이월 M건"을 표시한다. 이월 PASS는 집계에 포함하되 `carried` 표시를 단다(U1-Q5).

증분 감사는 새 구성 한 쪽만 실행한다. INV-09상 모델·설정이 다른 두 구성은 버전 비교 대상이 아니므로 비교(Comparison)를 내지 않고, 보고서에 같은 점검의 이전 결과를 참고로 나란히 보인다.

## 7. 객체·모델 변경 요약

| 대상 | 변경 |
| --- | --- |
| CheckDef | `framework_id`(예: `CHK-REQ-001`), `mechanism_ids`(예: `[mechanism-01]`) 추가. 내부 ID(`REQ-01`) 유지 |
| 팩 | `change_selection.yaml`(엔진 변경 종류 → 점검 표, 패치 항상 시험 20개, 모델 변경 종류 → 점검, `fact_triggers`) 추가. 매니페스트 해시에 포함 → 팩 revision 증가 |
| CHG | `category`를 엔진·모델 변경 종류 집합으로 확장(기존 값 유지). `unmapped` 선택 필드 추가. `extractor.name`에 `git_diff`·`model_manifest` 추가. 근거 파일을 내용 주소 artifact로 연결하는 선택 필드(예: `artifacts`)를 더하고 저장소 검증·반출·복원이 이를 검사한다. 기존 형식의 과거 CHG는 그대로 읽되 소급해 검증 완료로 올리지 않는다 |
| PLAN | `inputs.previous_plan: Ref|null`, `change_type: ENGINE_RELEASE|ENGINE_PATCH|MODEL|INPUT|INITIAL|null`. 항목에 `triggers: [{type, refs}]`, `carried_over` 추가 |
| enum | `NotSelectedReason.CARRIED_OVER` 추가. `Mode`·`Comparison`은 그대로(증분 감사는 audit 모드) |
| RPT | 엔진 릴리스(정식·패치): 바뀐 영역·미분류 파일·체크리스트 공백 후보·미도달 변경. 패치 릴리스·증분 감사: 이월 표·건수·트리거별 선정 사유 |

새 필드는 기본값이 있는 선택 필드로 추가해 과거 객체 해시가 바뀌지 않게 한다.

CLI(제안):

```bash
guard env update    --base ENV-... --patch patch.yaml          # 변경분만 입력 → 같은 id 새 revision
guard policy update --base POL-... --patch patch.yaml
guard diff --from v0.31.0 --to v0.32.0                         # 엔진 소스 diff → CHG (정식: 보고, 패치: 선정)
guard model-diff --from <snapshot|manifest> --to <snapshot|manifest>   # 모델 manifest diff → CHG
guard update ... --change CHG-...                              # 엔진 정식 릴리스: 전체 시험 + diff 보고
guard update ... --change CHG-... --previous-plan PLAN-...     # 엔진 패치 릴리스: 선정 점검만 기준·후보 시험 + 이월
guard select --env ... --policy ... --previous-plan PLAN-... [--change CHG-...]   # 증분 감사
```

## 8. 1차 검증 실험 (2026-10-25, Claude, scratchpad)

v0.31.0(`db9527a4`, 체크리스트 `implementation_reference`와 같은 커밋) → v0.32.0(`5df63a72`) 실제 vLLM 저장소로 했다. 엔진은 띄우지 않았다. 경로 규칙은 폴더 단위 1차안이며 공지 수정 파일을 겨냥한 규칙은 넣지 않았다. CLI 옵션·환경 변수 단위 비교와 의존성 패키지 표는 하지 않았다.

- 840개 커밋, 변경 경로 2,801개(이름 변경 양쪽 포함). 무시 1,146, 분류 1,645, **미분류 10**.
  - 미분류: `CMakeLists.txt`, `mkdocs.yaml`, `rust/src/llm/src/{lib,output,request_metrics}.rs`, `vllm/snapshot/{manifest,runtime}.py`, `vllm/config/engram.py`, `vllm/engine/protocol.py`, `vllm/v1/engine/__init__.py`.
- 그때 규칙(폐기된 A/B/C 안)으로 선정하면 56~60/77이었다. 정식 릴리스는 거의 모든 영역을 건드려 줄이는 효과가 작다. 이 결과가 엔진 릴리스 전체 시험 결정의 근거 중 하나다.
- 패치 릴리스는 작다: v0.25.0→v0.25.1 커밋 2·파일 4, v0.27.0→v0.27.1 커밋 4·파일 4, v0.22.0→v0.22.1 커밋 8·파일 21, v0.20.1→v0.20.2 커밋 6·파일 13, v0.19.0→v0.19.1 커밋 19·파일 82. 정식 v0.30.0→v0.31.0은 커밋 728·파일 3,146.
- **설계 보완·회귀 사례(독립 평가 아님)**: v0.32.0에서 고친 보안 공지 9개(2026-10-20 공개)의 수정 PR 파일이 맞는 점검의 변경 종류로 분류되는지 대조했다. 5건 적중, 4건 미연결. 미연결 원인: 모델 구현 파일 안의 자원 고갈·입력 오류(GHSA-8397 Phi-4 오디오 OOM, GHSA-gq5q DeepSeek-OCR, GHSA-frvg P2P ZMQ 고갈의 LIF-03 부분), 미디어 디코딩·역직렬화가 SUP-12에 연결되지 않음(GHSA-h3rc, GHSA-6757). 앞의 둘은 §4.1 `MODEL_ARCH`에, 뒤의 둘은 §3.2 표에 반영했다.
- 체크리스트 공백 후보: tool·reasoning 출력 파서(`vllm/tool_parsers/`, `vllm/reasoning/`, `vllm/parser/`, `rust/src/parser/`) 105개 파일이 어떤 점검에도 연결되지 않았다.
- 과거 패치 릴리스에 §3.4 규칙(항상 20 + diff 선정)을 적용한 결과:

  | 패치 | 변경 경로 | 미분류 | 선정 | diff로 추가된 점검 |
  | --- | ---: | ---: | ---: | --- |
  | v0.25.0 → v0.25.1 | 4 | 0 | 25/77 | REQ-08, SUP-07, SUP-10, SUP-12, SUP-15 |
  | v0.27.0 → v0.27.1 | 4 | 0 | 29/77 | LIF-01·02, REQ-18~21, SUP-03·06·12 |
  | v0.20.1 → v0.20.2 | 13 | 0 | 32/77 | 위 + REQ-15~17 |
  | v0.22.0 → v0.22.1 | 21 | 0 | 32/77 | LIF-01·02, REQ-01·03·06, REQ-18~21, SUP-03·06·12 |
  | v0.19.0 → v0.19.1 | 82 | 1(`CMakeLists.txt`) | 38/77 (미분류 규칙 적용 시 58/77) | 라우트·cache·컴파일·모델·kernel 영역 |

  `CMakeLists.txt` 같은 빌드 파일은 규칙에 `IMAGE`로 넣어야 한다. 모델 구현 파일(`vllm/model_executor/models/`) 하나만 바뀌어도 `MODEL_LOADING`·`RUNNER_KERNEL`로 9개가 붙는다.
- 실험 스크립트(재현용, 저장소 밖): `classify.py`, `imports.py`, `patch_select.py`, 결과 `classified.json`·`adv_files.tsv`.

구현 후 새 세션은 같은 범위로 `guard diff`를 실행해 위 숫자(미분류·종류별 파일 수·공백 후보)가 재현되는지, 다르면 이유를 보고한다.

### 8.1 평가 방법 (팀원 개선안 반영)

규칙의 정확도는 실제 건수로 본다.

- 필수 연결 누락: 정답 사례에서 반드시 골라야 할 점검을 놓친 수
- 근거 없는 추가 선정: 정답 사례와 무관한데 고른 점검 수
- 미분류 누락: 규칙이 분류하지 못했는데 미분류로도 표시하지 않은 파일 수
- 미지원 확정: 지원하지 않는 구문·경로를 확정 사실로 기록한 수
- 같은 입력의 결과 차이: 같은 저장소·태그로 두 번 실행했을 때 의미가 다른 결과 수

정답 사례는 소스 전후 바이트, 사람이 확인한 변경 사실, 필수·선택 관련 점검, 구체 시험 대상, 미확인 경계, 판단 이유를 남긴다. 규칙을 만들 때 쓴 사례와 평가에 쓰는 사례를 나눈다. §8의 보안 공지 9건은 `MODEL_ARCH`·SUP-12 연결·항상 시험 20개를 보강하는 데 이미 썼으므로 **설계 보완·회귀 사례**다(구현 후 같은 결과가 나오는지 확인하는 용도). **독립 평가 사례**는 구현 후 규칙 파일 해시를 고정한 뒤 처음 적용하는 릴리스(v0.32.0 이후 정식·패치)의 보안 공지·보안 수정으로 둔다. 그 결과를 보고 규칙을 고치면 그 사례도 회귀 사례로 옮긴다. 이 사례 집합만으로 전체 보안 영향의 완전성을 주장하지 않는다.

### 8.2 구현 순서 (한 묶음 안에서)

사용자 결정대로 한 묶음으로 구현하고 마지막에 테스트하되, 어느 단계에서 잘못된 판단이 생겼는지 가릴 수 있게 다음 순서로 만든다.

1. **CHG 제품 연결**: 사람이 쓴 CHG를 `guard prepare-update`·`guard update`·`guard select`·`python -m guard.update_validation`에 넣고, 근거 artifact를 저장·검증·반출·복원한다.
2. **자동 추출**: `guard diff`·`guard model-diff`가 1과 같은 CHG 형식을 만든다.
3. **선정과 이월**: 패치 릴리스·증분 감사 선정.
4. **대상별 도달 확인**: §3.3의 다섯 단계.

1과 2를 분리하면 분석 오류와 연결 오류를 따로 볼 수 있다. 4까지 연결되기 전에는 "변경을 검증했다"고 표시하지 않는다.

### 8.3 후속 확장 (이번 범위 아님)

- **라우트·인증 AST 정밀화**: 경로 규칙 위에, 명시적 endpoint 등록(문자열 경로·메서드)과 endpoint에 직접 붙은 인증 dependency·decorator의 전후 구조 비교를 더한다. `API_ROUTE`·`AUTH` 종류의 정확도를 높이는 층이며, 동적 경로·공통 함수·임의 역할 조건은 미지원으로 남긴다. "인증"이라는 이름만으로 보안 기능으로 확정하지 않는다.
- **간접 영향**: 명시적 함수 호출·middleware → 설정 정의와 사용처 → 입력 검증과 실제 소비 경로 → 오류·재시도·취소 경로 → 프로세스 간 전달 순서. 기준·후보 양쪽 관계를 비교한다(후보만 보면 제거된 연결을 놓친다). 1차 실험에서 공통 모듈 때문에 import 전파가 전체로 번졌으므로, 관계 종류를 제한한 추적이어야 한다.
- **시험·점검 후보 분류**: 매핑 실패 → 매핑 보강 / 기존 요구사항인데 시험 부족 → 실행기·관측 후보 / 정책 미정 → 기업 정책 결정 / 기존 요구사항으로 설명 안 됨 → 신규 점검 후보. 후보 작성까지만 자동화하고 실행·판정은 검증된 계약을 따른다.

## 9. 폐기한 안 (2026-10-24 판)

모든 엔진 릴리스에 소스 diff로 점검을 A(항상 17)·B(diff 42)·C(입력 18)로 나눠 B·C를 이월하는 안이었다. 1차 실험에서 정식 릴리스가 B 42개 중 38개를 건드려 줄이는 효과가 작았고, 사용자가 정식 릴리스는 전체 시험하기로 결정해 폐기했다. 같은 생각은 패치 릴리스(§3.4)에만 남았다: A는 1차 실험 결과(LIF-03·REQ-09·REQ-10)를 더해 "항상 시험 20개"가 됐고, B·C 구분 대신 §3.2 표와 §5 입력 변경으로 고른다.

## 10. 한계 (보고서에 명시)

- 패치 릴리스와 증분 감사의 이월은 "관련 변경이 없다"는 판별을 믿는 것이다. 경로 규칙·§3.2 표·모델 manifest 규칙·`fact_triggers`가 놓친 변경은 이월된 점검에서 드러나지 않는다. 패치 릴리스는 항상 시험 20개로 흩어진 속성을 보완한다.
- 패치 릴리스는 import 전파를 하지 않으므로, 바뀐 파일을 쓰는 다른 영역의 점검은 항상 시험 20개에 들지 않으면 이월될 수 있다.
- 같은 모델 파일 안의 의미 변화(예: template 문자열 일부 변경)는 파일 단위로만 잡는다.
- 엔진 릴리스의 도달 확인은 진입점 수준이다. 바뀐 내부 코드가 실험 중 실행됐는지는 확인하지 않는다.
- 안전 규칙상 보내지 않는 상태 변경 요청, 장애를 일으켜야 실행되는 실패 경로, 실험 환경에 없는 하드웨어 경로는 도달할 수 없다.

### 2026-10-26 참조 기준 동기화
원문·고정 ID 대응·위협 연결 메타데이터의 동기화는 완료했다. 변경 영향에 따른 시험 선정과 재사용 기능은 이 문서의 후속 구현 범위다. 기존 실행용 목적 분류는 유지하고 최신 원문 목적은 `framework_purpose`에 보존한다. 자세한 범위는 [동기화 기록](FRAMEWORK_SYNC_20261026.md)을 따른다.
