# 서빙엔진 보안 검증 솔루션 — 설계 초안 (SPEC v0.1)

> 2026-10-26 BYUL 이관: GUA `8e23bd2`의 U1 제품 연결·팩23·Python 3.12를 선택 이관했다. 아래의 2026-10-25 설계/과거 팩 실측 문구는 당시 기록이다. 현행 코드 검증은 PROGRESS, 77개 분기 현황은 docs/CHECKLIST_VALIDATION_STATUS.md, 외부 경계는 ../contracts/README.md를 따른다. RPT JSON 원본·플랫폼 표시를 유지하며 자체 HTML 보고서는 복구하지 않는다. 이번 실제 엔진 실증은 사용자 요청으로 보류한다.


> 2026-10-25 U1 결정(제품 연결·오프라인 검증 및 분류 한계는 설계 §11 참조): 엔진 정식 릴리스는 적용 점검 전체를 기준·후보로 시험하고 소스 diff는 보고(바뀐 영역·도달 확인·체크리스트 공백)에만 쓴다. 엔진 패치 릴리스(0.xx.1 …)는 항상 시험 20개와 소스 diff에 걸리는 점검만 기준·후보로 시험한다. 엔진 이미지가 같은 모델·환경·정책 변경은 증분 감사로 걸리는 점검만 시험한다. 선정되지 않은 점검은 이전 PASS를 이월한다. INV-05를 개정하고 INV-19를 추가했다. 맨 끝 "U1" 절과 [UPDATE_SELECTION.md](docs/UPDATE_SELECTION.md)를 따른다.

> 2026-10-24 판정 의미 보완 후보: PR #29는 main 641f12f에 병합됐다. 현행 PACK22/DRAFT·REQ-09 정의6·PLT-04 정의3·정책22점검50경로·자동연결11이다. 사용자 결정으로 HTTP 성공을 위험한 소비와 구분하고, 명시 마운트 금지와 자격 후보 접근 관측을 분리한다. 어댑터0.4.2·input_validation0.5.0·credential_exposure0.4.0·declared0.14.0이며 이번 테스트·실증 결과는 아래 이전 팩21의 기록과 별도로 갱신한다. 원본 7객체·불변식·다른75개 정의·저장 팩의 과거 판정은 유지한다.

> 2026-10-24 PR #29 통합 당시 기록: 당시 브랜치는 PR #24·#25·#28을 합치고 전체 회귀·설치본·새 복원 재판정을 통과한 [v1 기술 완료 후보](docs/UPDATE_VALIDATION.md#v1-통합-후보-검증)였으며 main 반영·공식 출시 승인은 별도였다. PACK-BASELINE 21/DRAFT·77개 점검 정의·정책 22점검/49경로·자동 연결 11개·adapter 0.4.1·declared 0.13.0을 사용했다. PR #28에서 두 실제 vLLM CPU 이미지(API 0.31.0/0.32.0)의 기존 5개 점검은 양쪽 보안·기능 PASS였고 수락·반출/복원·저장 팩 재판정이 일치했다. [실측 결과](docs/UPDATE_VALIDATION.md#실측-결과--c-후속-2026-10-24)는 해당 실행 코드의 기록이며 통합판의 새 실증으로 간주하지 않는다. 전부 NO_CHANGE라 실제 변화 탐지는 미증명이고 성능 UNKNOWN·DRAFT·official=false를 유지한다. EVD의 MEASURED 출처와 REQ-01/REQ-03 Result의 DECLARED 검증 수준은 다르며, REQ-01 일부 경로는 HTTP 대신 TCP 차단 근거로 검증했다. 기업 승인이나 전체 handler 실행을 뜻하지 않는다.

> 2026-10-24 앞선 정책·로그 증거 보강 당시 기록: REQ-15 M14의 제한 수동 계약, OBS03/09 수집 경계·저장 로그 재검증, 설치 wheel CI·준비 guidance를 [구현/검증 기록](docs/POLICY_RELIABILITY_BATCH.md)에 정리했다. 객체 7종·원본/Ref·판정/비교 불변식을 유지하며 실제 캐시 자동 실행기를 포함하지 않았다. 해당 단계는 실제 엔진/두 이미지 실행을 하지 않았다.

> 2026-10-24 앞선 검증 도구 구현 당시 기록: 기존 다섯 점검의 두 이미지 CPU 비교를 prepare/update·원본 증거 수락·반출/복원·저장 팩 재판정으로 연결했다. 당시 객체/판정/비교 계약과 팩20을 유지했고 update의 선택적 `--as-of`로 예외 만료 판단 시각을 고정했다. 당시 실제 두 이미지 시험은 미수행이었다. 이후 결과와 [당시 기록](docs/UPDATE_VALIDATION.md#이번-검증-결과)을 구분한다.

> 2026-10-23 현행 보강: REQ-11 revision 3은 독립 생성 출력 귀속 오라클 부재로 보안 PASS를 보류하고 입력 projection 하위 검증을 분리한다. managed update side별 sentinel과 읽기 전용 점검 지원 안내를 연결한다. [계약·검증·다음 실증 조건](docs/AUTOMATION_HARDENING.md)을 따른다. 새 정책/실행기·실제 엔진 기동·객체7종 변경은 없다. 과거 1~3차 실측은 해당 당시 팩의 기록이다.

> 작성 2026-10-19 · 상태: **초안**. 팀 확인 전. 구현·실측 완료를 뜻하지 않는다.
>
> **근거:** `serving-security-framework`(점검 77행·시나리오 83개·환경/결과 템플릿)를 "무엇을 점검하나"의 정본으로, `BoBProject/개발계획`(A1 불변식·A3 파이프라인·A17 콘텐츠 팩/확장 지점)을 "어떻게 만드나"의 구조 기준으로 삼았다. BoBProject의 승인 서명·감사 outbox·객체 20여 종은 의도적으로 뺐다(§9).
>
> **이 문서의 독자:** 코드를 작성하는 AI 에이전트와 팀원. AI에게는 이 문서 전체를 컨텍스트로 주고, 원본 문서는 "필요할 때 찾아보는 참고"로만 연결한다.

---

## 0. 한 줄 정의와 범위

**환경 정보와 기업 정책을 받아, 서빙엔진에 적용되는 보안 점검을 고르고, 시험을 실행해 증거를 모으고, 규칙 기반으로 판정해 보고서를 낸다.** 엔진 업데이트 시에는 두 버전에서 같은 점검을 돌려 비교한다.

| 포함 (v1) | 제외 (v1) |
| --- | --- |
| vLLM 어댑터 1개 (인터페이스는 엔진 중립) | SGLang·TensorRT-LLM 어댑터 구현 |
| CLI + JSON 보고서 (화면은 byul platform, 2026-10-25부터) | 자체 웹 UI·HTML 보고서, 역할별 로그인 |
| 로컬 Docker 단일 노드 실행 | K8s·다중 노드·다중 테넌트 |
| 우선 점검 5~10행 자동화 (§8) | 77행 전부 자동화 |
| 승인 기록(누가·언제) | 전자서명·registry·철회 절차 |
| 엔진 정식 릴리스는 전체 시험, 패치 릴리스·모델·환경·정책 변경은 골라서 시험 (U1) | LLM 판단 보조 |

**두 가지 실행 모드**

| 모드 | 입력 | 질문 |
| --- | --- | --- |
| `audit` | 환경 1개 | 지금 이 구성이 정책을 충족하는가? |
| `update` | 환경 2개(기준/후보) + 엔진 소스 diff + 이전 결과(패치 릴리스) | 버전을 올리면 충족 여부가 달라지는가? 정식 릴리스는 적용 점검 전체, 패치 릴리스는 필요한 점검만 시험한다(U1) |

---

## 1. 파이프라인

```
 [CheckDef 팩] ─┐
 [Environment] ─┼─► select ─► Plan ─► run ─► Evidence ─► judge ─► Result ─► report ─► Report
 [Policy]      ─┤                      ▲                    ▲
 [Change]*     ─┘                      │                    │
                              (실행기·센서 플러그인)   (판정 플러그인, 순수 함수)
 * update 모드: 엔진 소스 diff(정식은 보고용, 패치는 선정용). audit 증분 감사: 모델 manifest diff(선정용). 둘 다 선택(U1)
```

변경 유형별 시험 범위(엔진 정식 릴리스 전체 / 패치 릴리스·모델·환경·정책 변경은 골라서)와 입력 유지·이월은 맨 끝 "U1" 절과 [UPDATE_SELECTION.md](docs/UPDATE_SELECTION.md)를 따른다.

| 단계 | 하는 일 | 하지 않는 일 |
| --- | --- | --- |
| `select` | 환경·정책·변경으로 점검별 적용 여부를 계산하고 시험을 고른다 | 시험 실행 |
| `run` | 엔진을 띄우고, 정상 대조와 챌린지를 실행하고, 관측·증거 파일을 모은다 | **판정하지 않는다** |
| `judge` | 증거를 검증한 뒤 판정 플러그인으로 Result를 만든다 | 엔진 접속, 새 요청 |
| `report` | Result를 영역·보호 목적으로 집계하고, update 모드면 버전을 비교한다 | 새 판정 |

`run`과 `judge`의 분리가 핵심이다. 저장된 Evidence만으로 언제든 다시 `judge`할 수 있어야 한다(재판정).

---

## 2. 공통 규칙

### 2.1 ID

| 대상 | 형식 | 예 |
| --- | --- | --- |
| 점검 행 (CheckDef) | `<영역>-<2자리>` | `REQ-01` |
| 객체 | `<종류>-<ULID>` | `ENV-01JA...` |
| 팩 | `PACK-<이름>` | `PACK-BASELINE` |
| 시나리오·Q | 프레임워크 원래 ID 유지 | `SC-01-01`, `Q-C01` |

**영역 코드 5종** (프레임워크 `checklist.json`의 5개 draft 배열과 1:1, 행 수는 현재 기준):

| 코드 | 영역 | 행 수 | 원본 키 |
| --- | --- | --- | --- |
| `SUP` | 모델·코드·실행 공급물 | 16 | `supply_assessment_draft` |
| `REQ` | 추론 요청·자료·공유 상태 | 21 | `request_state_assessment_draft` |
| `PLT` | 실행 기반·자격·통신 | 12 | `platform_assessment_draft` |
| `LIF` | 배포·변경 상태와 실행 자원 | 16 | `lifecycle_assessment_draft` |
| `OBS` | 관측·진단·감사 자료 | 12 | `observability_assessment_draft` |

> **결정 필요:** 프레임워크 77행에는 지금 고유 ID가 없다(논리 검토 L1). 위 코드를 붙이는 변환 스크립트를 첫 작업으로 만든다. **한 번 붙인 ID는 문구가 바뀌어도 바꾸지 않는다.** 행을 없애면 `retired: true`로 남긴다.

### 2.2 모든 객체의 공통 필드

```yaml
kind: ENV | POL | CHG | PLAN | EVD | RES | RPT
id: string            # §2.1
revision: int         # 1부터. 같은 id의 새 내용은 revision+1, 이전 revision은 수정 금지
created_at: RFC3339   # UTC
data_origin: MEASURED | DECLARED | EXAMPLE
content_sha256: hex   # §2.3
refs: [Ref]           # 이 객체가 근거로 쓴 다른 객체
```

```yaml
Ref: { kind: str, id: str, revision: int, sha256: hex }   # 항상 정확한 revision까지 고정
```

- `MEASURED`: 실제 환경에서 수집하거나 실행해 얻은 값
- `DECLARED`: 사람이 입력한 값(설정 파일 신고 등). 실제 효과를 관측한 것은 아님
- `EXAMPLE`: 합성·데모 자료. **공식 판정 근거가 될 수 없다**

### 2.3 해시

`content_sha256` = `content_sha256` 필드를 뺀 객체를 **정렬된 키·UTF-8·공백 없는 JSON**으로 직렬화한 bytes의 SHA-256. 증거 파일은 파일 bytes 자체의 SHA-256.

### 2.4 공통 enum

```yaml
Applicability: APPLICABLE | NOT_APPLICABLE | UNDETERMINED | CONFLICT
Verdict:       PASS | FAIL | UNKNOWN | NOT_APPLICABLE      # 문서상 충족/미충족/미확인/해당 없음
ExecStatus:    PENDING | RUNNING | COMPLETED | ABORTED | NOT_RUN
Comparison:    NO_CHANGE | REGRESSED | FIXED | STILL_FAILING | UNKNOWN | NOT_COMPARABLE
Gap:           NO_EXECUTOR | NO_POLICY_VALUE | NO_FIXTURE | NO_ENV_FACT | ADAPTER_UNSUPPORTED
```

한국어 표시는 보고서 렌더링에서만 바꾼다. 데이터에는 영문 enum만 쓴다.

---

## 3. 카탈로그 입력: CheckDef (객체 7종에 포함하지 않음)

CheckDef는 실행 중 생기는 객체가 아니라 **팩에 담긴 정의**다. 프레임워크 `checklist.json`의 77행을 변환해 만든다.

```yaml
# packs/PACK-BASELINE/checks/REQ-01.yaml
id: REQ-01
revision: 1
area: REQ
purpose: "요청·대상 접근 권한"             # ← parent_purpose
question: "Gateway를 우회한 직통 요청으로 비허용 작업을 수행할 수 없는가?"   # ← question
risk_summary: "..."                       # ← risk_summary
review_order: reference_baseline_first    # ← review_order (심각도 아님, 준비 순서)
impact: null                              # 결정 필요: HIGH|MEDIUM|LOW (논리 검토 L4)
applies_when:                             # ← applies_when (지금은 산문 → 구조화 필요)
  all: [{ fact: gateway, eq: true }]
  prose: "사용자→Gateway 경유와 엔진 직통 제한"   # 원문 보존
policy_params:                            # ← policy_to_decide / enterprise_decisions
  - { key: allowed_callers, type: "list[str]", required: true }
normal_control: "허용 키로 같은 route 호출 시 추론 성공"         # ← normal_control
challenge_steps: ["...", "..."]           # ← challenge_steps
required_observations:                    # ← required_observations (구조화, 경로별)
  - { key: routes_registered, type: "list[str]" }        # 엔진 /openapi.json(+Gateway 설정의 알려진 경로), 정보성 경로 제외
  - { key: routes_tested, type: "list[str]" }
  - { key: direct_outcomes, type: "dict[str, int|outcome]" }   # 무키·오키 대표값. outcome = refused|timeout|unreachable|dns_error|error
  - { key: gateway_outcomes, type: "dict[str, int]" }          # 허용 키로 Gateway 경유
  - { key: trusted_origin_reachable, type: bool }              # 같은 시각 신뢰 출발점 → 엔진 도달 (양성 대조)
  - { key: functional_probes_planned, type: "list[str]" }      # functional_probes의 "METHOD path"
  - { key: functional_probe_results, type: "dict[str, pass|fail|no_response]" }
satisfied_if: "..."                       # ← satisfied_if (원문)
fail_criteria: "..."                      # ← fail_criteria
unknown_criteria: "..."                   # ← unknown_criteria
remediation: "..."                        # ← remediation
executor: null                            # 실행기 플러그인 이름. null이면 자동 실행 불가 → Gap NO_EXECUTOR
judge: { plugin: rule_table, params: {...} }  # null이면 수동 판정만 (REQ-01 판정표는 §7.2)
functional_probes:                        # 기능 축 표본(허용 키, Gateway 경유). 경로를 코드에 두지 않는다
  - { method: POST, path: /v1/completions, body: { model: "{model}", prompt: ping, max_tokens: 1 }, expect: [200] }
functional_scope: { probed_obs: functional_probe_results, of_obs: routes_registered }   # 보고서 범위 표시
legacy: { q_ids: [Q-C01], scenario_ids: [SC-01-01] }   # 근거 링크. 판정 단위 아님
```

**변환 시 유의**
- `applies_when`, `required_observations`는 원문이 산문이다. 자동화할 행만 구조화하고, 나머지는 `prose`만 두고 `executor: null`로 둔다.
- `satisfied_if`가 얇다(중앙값 41자, 논리 검토 §3). 자동화하는 행은 판정 규칙을 `judge.params`에 체크 가능한 조건으로 다시 쓴다(§6 INV-11).
- `legacy.q_ids`·`scenario_ids`는 추적용이다. **평가·집계 단위는 CheckDef ID 하나다**(논리 검토 L2).

**팩 매니페스트** (`packs/<id>/pack.yaml`)

```yaml
pack_id: PACK-BASELINE
revision: 1
engines: [vllm]
checks: [SUP-01, ..., OBS-12]
files_sha256: [{ path: checks/REQ-01.yaml, sha256: ... }]   # pack.yaml 제외 전 파일, 경로순
review: { status: DRAFT | REVIEWED, reviewer: str|null, reviewed_at: RFC3339|null }
```

`REVIEWED`가 아닌 팩으로 만든 Result는 `data_origin`과 관계없이 보고서에 "검토 전 팩"으로 표시한다.

---

## 4. 객체 스키마 7종

Pydantic v2 모델로 구현한다. 아래는 공통 필드(§2.2)를 생략한 고유 필드다.

### 4.1 ENV — Environment (환경 프로필)

프레임워크 `environment_template.json`을 그대로 확장한다.

```yaml
kind: ENV
engine: { name: vllm, version: "0.31.0", source_commit: hex|null, image_digest: "sha256:..."|null }
adapter: { name: vllm, version: "0.1.0" }          # 사용한 엔진 어댑터 (§7.1)
runtime: { nodes: int, tp_groups: int, gpu_count: int|null, gpu_model: str|null, driver: str|null }
model: { id: str|null, revision: str|null, inventory_sha256: hex|null,   # 비교용(INV-09): 마운트한 snapshot 전체의 정렬 목록 해시
         files_read_by_engine: { file_count: int, inventory_sha256: hex }|null }   # 참고용: 엔진이 읽은 파일만. 판정·비교에 쓰지 않음
facts:                                             # 적용 판단에 쓰는 사실. 키는 등록된 것만
  gateway:                       { value: true, origin: DECLARED, source: "env.yaml#gateway" }
  prefix_cache:                  { value: true, origin: MEASURED, source: "..." }
  shared_or_remote_weights:      { value: false, origin: DECLARED, source: "...", absence_evidence: "EVD-...#..." }
  dynamic_import:                { value: null }
  # ... 프레임워크 템플릿의 키 전체
```

- `facts`의 각 값은 `value`·`origin`·`source`를 반드시 가진다. 값을 모르면 `value: null`.
- 기능을 `false`로 신고하려면 `absence_evidence`(비사용·비노출 근거)가 있어야 한다. 없으면 적용 판단에서 `null`로 취급한다(프레임워크 selector 규칙, INV-06).
- 출처끼리 값이 충돌하면 임의로 고르지 않고 `value: null, conflict: [..]`로 둔다.

### 4.2 POL — Policy (기업 정책 값)

```yaml
kind: POL
status: DRAFT | CONFIRMED
pack: Ref                                          # 어떤 팩 기준의 정책인가
values:                                            # CheckDef.policy_params 채우기
  REQ-01: { allowed_callers: ["gateway-sa"] }
scope: { include: [REQ-*, SUP-01], exclude: [] }   # 기업이 고른 점검 범위
exceptions:
  - check_id: OBS-04
    reason: str
    approved_by: str
    approved_at: RFC3339
    expires_at: RFC3339
confirmed_by: str|null
confirmed_at: RFC3339|null
```

- v1 승인은 **"누가·언제" 기록만** 남긴다. 서명은 하지 않는다(§9).
- 예외는 판정을 바꾸지 않는다. 보고서에 "예외 승인됨"을 따로 표시할 뿐이다(INV-08).

### 4.3 CHG — Change (버전 변경 사실, update 모드 선택)

```yaml
kind: CHG
baseline:  { version: str, source_commit: hex, image_digest: str|null }
candidate: { version: str, source_commit: hex, image_digest: str|null }
extractor: { name: git_diff_ast | advisory | manual, version: str }
facts:
  - id: str
    category: API_ROUTE | CLI_OPTION | ENV_VAR | DEFAULT_VALUE | DEPENDENCY | ADVISORY | AUTH | OTHER
    target: str                    # 예: "/v1/completions", "--trust-remote-code"
    before: any|null
    after: any|null
    certainty: CONFIRMED | PARTIAL | UNKNOWN
    evidence: [{ path: str, line: int|null, sha256: hex }]
    related_checks: [CheckId]      # 팩의 분석 규칙이 매핑. 비어 있으면 미분류
reviewed_by: str|null              # 사람 검토 전이면 certainty는 최대 PARTIAL
```

- 엔진 소스 diff는 정식 릴리스에서 **보고 근거**(바뀐 영역 표시·도달 확인·체크리스트 공백)이고 패치 릴리스에서 **선정 근거**다. 모델 manifest diff는 증분 감사의 **선정 근거**다. 어느 쪽도 판정 근거가 아니다(INV-05 개정, INV-19, U1).
- U1에서 `category`는 엔진·모델 변경 종류 집합으로 확장하고 `unmapped`(규칙 미분류) 선택 필드를 더한다. `extractor.name`에 `git_diff`·`model_manifest`를 더한다. 세부는 [UPDATE_SELECTION.md](docs/UPDATE_SELECTION.md) §3·§4·§7.

### 4.4 PLAN — Plan (적용 판단 + 시험 선정)

```yaml
kind: PLAN
mode: audit | update
inputs: { pack: Ref, environments: [Ref], policy: Ref, change: Ref|null }
items:
  - check_id: REQ-01
    applicability: APPLICABLE
    conditions:                        # 판단 근거를 조건별로 보존
      - { fact: gateway, expected: true, actual: true, origin: DECLARED, result: TRUE }
    reason: str
    triggered_by_change: [CHG.fact.id] # update 모드에서 이 점검을 끌어올린 변경
    selected: true
    gaps: []                           # 선정됐지만 자동 실행이 막힌 이유 (Gap enum)
    priority: reference_baseline_first
inventory_complete:                    # 보호 목적별 점검 목록 완결 여부 (INV-13)
  "요청·대상 접근 권한": true
```

**적용 판단 규칙** (BoBProject `planner/impact.py` 규칙을 계승)

1. 조건 중 근거 있는 `FALSE`가 하나라도 있으면 → `NOT_APPLICABLE` (다른 미확인 조건은 `conditions`에 그대로 남긴다)
2. 아니고, 모든 필수 조건이 근거 있는 `TRUE`면 → `APPLICABLE`
3. 아니고, 같은 사실에 값이 상충하면 → `CONFLICT`
4. 그 밖(근거 없음·null) → `UNDETERMINED`
5. 빈 조건 목록은 "항상 참"이 아니다. CheckDef에 `applies_when: { always: true }`를 명시한 경우만 무조건 `APPLICABLE`

**선정 규칙**
- `APPLICABLE` → 선정
- `UNDETERMINED`·`CONFLICT` → **선정**하고, 보고서에 "적용 미정 상태로 시험함"을 표시한다. 빼지 않는다
- `NOT_APPLICABLE` → 미선정. 사유를 남긴다
- 정책 `scope.exclude`로 뺀 점검 → 미선정. 사유 `POLICY_SCOPE`
- 선정됐지만 `executor`가 없으면 `gaps: [NO_EXECUTOR]` → 수동 시험 대상으로 보고서에 나열
- update 모드: 정식 릴리스는 위 규칙 그대로 적용 점검 전체다. 패치 릴리스에 `--change`·`--previous-plan`을 주면 항상 시험 20개·diff 변경 종류·입력 변경에 걸린 점검만 선정하고 나머지는 이월(`CARRIED_OVER`)한다. audit 모드에 `--previous-plan`을 주면 증분 감사다: 엔진 이미지가 같을 때 모델·입력 변경에 걸린 점검만 선정하고 나머지는 이월한다(U1)

### 4.5 EVD — Evidence (실행 기록 + 관측 + 증거 파일)

`run` 단계의 유일한 산출물이다. **판정 필드가 없다.**

```yaml
kind: EVD
plan: Ref
check_id: REQ-01
side: baseline | candidate | single           # audit 모드는 single
environment: Ref
execution:
  status: ExecStatus
  executor: { name: str, version: str, config_sha256: hex }
  adapter:  { name: str, version: str }
  started_at: RFC3339|null
  finished_at: RFC3339|null
  abort_reason: str|null
  cleanup_errors: [str]                        # 실행 후 정리 실패. 비어 있으면 직렬화 생략(과거 해시 유지)
normal_control: { attempted: bool, succeeded: bool|null, detail: str }
challenges:
  - step: str                                  # CheckDef.challenge_steps 중 하나
    attempted: bool                            # 실제로 요청·시도를 보냈는가
    observations: { direct_route_status: 401 }
observations:                                  # CheckDef.required_observations 키만
  routes_registered:  { value: ["POST /v1/completions", "POST /invocations"], collected: true }   # 키는 "METHOD path"
  routes_tested:      { value: ["POST /v1/completions", "POST /invocations"], collected: true }
  direct_outcomes:    { value: { "POST /v1/completions": "timeout", "POST /invocations": "timeout" }, collected: true }
  gateway_outcomes:   { value: { "POST /v1/completions": 200, "POST /invocations": 200 }, collected: true }
  trusted_origin_reachable: { value: true, collected: true }
  functional_probes_planned: { value: ["POST /v1/completions"], collected: true }
  functional_probe_results:  { value: { "POST /v1/completions": "pass" }, collected: true }
artifacts:
  - { name: request_log.jsonl, path: "artifacts/<sha>.jsonl", sha256: hex, bytes: int,
      collected_at: RFC3339, collector: str }
comparability:                                 # update 모드 비교 조건 (INV-09)
  model_inventory_sha256: hex|null
  fixture_sha256: hex|null
  config_sha256: hex|null
  gpu: str|null
  driver: str|null
  cpu: { architecture: str, model: str, system: str, kernel: str, threads: int } | null  # 명시적 CPU 비교만, 없으면 직렬화 생략
```

- 수집 실패는 `collected: false`로 남긴다. 값을 비우거나 추정하지 않는다.
- 직통 결과 값은 HTTP 상태코드(int) 또는 `refused`(연결 거부) · `timeout` · `unreachable`(ENETUNREACH/EHOSTUNREACH) · `dns_error` · `error`. 응답 미수신 종류를 서로 구별한다.
- **직통 결과의 근거(basis)**: 실행기는 (출발점·대상, 키) 조합마다 먼저 TCP 연결만 시도한다(바이트 미전송). 연결 자체가 `timeout`·`refused`·`unreachable`이면 HTTP를 보내지 않고 그 조합의 **모든 등록 경로(상태 변경 경로 포함)** 결과를 그 값으로 기록하고 증거 파일에 `basis: tcp_connect (inferred)`로 표시한다. 연결되면 비상태변경 경로에만 HTTP를 보내고(`basis: http`) 상태 변경 경로는 보내지 않는다(미시험). `routes_tested`는 모든 (대상, 비허용 키) 조합의 직통 결과가 있는 경로다.
- 판정에 쓰지 않는 보조 관측(예: REQ-01 `direct_allowed_outcomes`)은 증거 파일에 남긴다.
- 증거 파일은 `artifacts/<sha256>` 이름으로 저장한다(내용 주소). 같은 파일은 한 번만 저장된다.

**수동 증거(사람이 입력한 시험 결과, `guard evidence template`·`guard evidence import`)**
- **사람의 판정은 신고된 관측이다.** 도구의 판정이 아니며, 도구의 판정기가 PASS 관문을 적용해 RES를 만든다. 신고 내용은 관측으로 들어간다: `declared_verdicts`(축별 PASS|FAIL|UNKNOWN), `declared_basis`(근거 설명), `declared_observation_notes`(CheckDef 필수 관측 문장별 설명), `declared_limitations`. 구조화 필수 관측(`required_observations`)이 있으면 값도 함께 넣는다.
- `data_origin`은 항상 `DECLARED`(MEASURED 불가, 모델이 거부 — INV-02). `execution.status: COMPLETED`, `execution.executor.name: manual-entry`, `execution.operator: {by, at}`(입력자·시각 — 인증되지 않은 자기 신고), 첨부 파일은 `artifacts`(내용 주소).
- 신고된 PASS는 첨부 증거가 1개 이상이고 필수 관측 문장마다 설명이 있어야 하며, 코어 PASS 관문(정상 대조 성공·모든 챌린지 시도·구조화 필수 관측 수집)도 통과해야 한다. 아니면 UNKNOWN. 신고된 FAIL은 유지(INV-04).
- 선정되지 않은 점검은 거부(INV-10). 자동 실행기가 있는 점검은 `override_reason`이 있을 때만 받고 `execution.override_reason`에 남긴다. 양식 검증은 전부 먼저, 하나라도 틀리면 아무것도 쓰지 않는다.
- **증거 대체 규칙:** 같은 PLAN·점검·side의 EVD는 하나의 id에 revision으로 쌓이고, **판정에는 최신 revision을 쓴다**(이전 revision은 남는다, INV-16). 수동 증거는 새 revision으로 들어간다. `guard run`은 최신 revision이 사람이 입력한 증거면 덮지 않는다(`--replace-manual`일 때만 대체). 최신 증거가 출처가 다른(자동↔수동) 이전 COMPLETED revision을 대체했으면 보고서 행에 `replaced`("대체됨", 이전 revision·사유)를 표시한다. 말 없이 덮어쓰지 않는다.
- **M4 수동 양식 v2:** 새 생성 양식은 `guard-manual-evidence/2`이고 PLAN의 POL/팩 전체 Ref를 추가 대조한다. `/1` 입력도 읽지만 신규 `policy_evidence`를 `/1`에 숨겨 전달하지 않는다. manual-entry·declared는 0.3.0이며 과거 객체의 버전/해시는 바꾸지 않는다.
- SUP-14.required_checks / LIF-07.required_readiness_items가 required list[str]로 선언된 정의에만 항목·case·첨부 연결을 적용한다. `declared_policy_evidence`는 기존 EVD.observations 안의 예약 수동 관측이며 계약 버전 `guard-policy-evidence/1`·parameter·정규화 records를 보존한다. 기존 객체/DB 종류는 늘리지 않는다. 정확한 항목 ID·trial·대상 범위와 현재 entry의 첨부 sha256·locator·근거를 연결하며, 원본 파일 내용을 자동 해석하거나 자기 신고를 인증하지 않는다.
- 정책 키/빈·모호한 집합/관측 부족은 새 PASS를 UNKNOWN으로 보류한다. UNKNOWN을 PASS로 올리지 않고 신고 FAIL·독립적으로 확인된 위반은 유지한다. SUP-14는 원검사 결과와 승인/배포 전이를 구별하며 security만 검증한다. LIF-07은 접수 시점의 준비 상태와 순서 근거·공통 ready trial/정상 추론을 대조해 security/functional을 구분한다. 두 계약은 performance를 검증하지 않는다. 기존 CPU/타 점검의 계약은 그대로다.
- 부분·미확인 관측은 저장 가능하나 Ref/side/미등록 항목/중복/타입 오류는 배치 전체를 거부한다. 정책 선언이 있는 대상의 과거 `/1` 증거는 새 판정에서 해당 PASS가 UNKNOWN일 수 있으며 새 RES revision/이유로 남긴다. 선언 없는 과거 팩·비대상 점검에는 요구를 소급하지 않는다. 항목별 이유는 선택된 RES의 `POLICY_ITEMS:` 이유만 기존 Report.limitations로 전달한다. 상세 입력 계약과 미결 기업 결정은 [정책 문서](docs/POLICY_TEMPLATE.md)를 따른다.

### 4.6 RES — Result (점검 판정)

```yaml
kind: RES
evidence: Ref
check_id: REQ-01
side: baseline | candidate | single
judge: { plugin: str, version: str, params_sha256: hex }
verdicts:                                      # 세 축은 따로 판정 (프레임워크 05 규칙)
  security:    Verdict
  functional:  Verdict
  performance: Verdict
reasons: [str]                                 # 판정 규칙의 어떤 조건이 맞았는가
missing_observations: [str]                    # UNKNOWN의 원인
exception: { check_id: str, expires_at: RFC3339 } | null   # 정책 예외 표시만. 판정에 영향 없음
verification_level: MEASURED | DECLARED | EXAMPLE | null   # 근거 중 가장 약한 것. 하나라도 null이면 null
```

### 4.7 RPT — Report (집계·비교·보고)

```yaml
kind: RPT
mode: audit | update
inputs_manifest: [Ref]                         # 이 보고서가 쓴 모든 객체 (해시 포함)
pack_review_status: DRAFT | REVIEWED
by_check:
  - check_id: REQ-01
    applicability: APPLICABLE
    selected: true
    baseline:  { result: Ref|null, security: Verdict }
    candidate: { result: Ref|null, security: Verdict }
    comparison: Comparison                     # update 모드만
    functional_comparison: Comparison          # update 모드, 값이 있을 때만
    comparison_reasons: [str]                  # 불일치/미확인 필드, 값이 있을 때만
    gaps: [Gap]
    evidence_source: automated | manual | null             # 판정에 쓰인 최신 증거의 출처(실행 완료 시)
    replaced: { source, revision, executor, reason } | null # 출처가 다른 이전 증거를 대체함("대체됨")
    functional_scope: { probed: int, of: int|null, label: "기능 확인 범위: N / 등록 경로 M (표본)" }   # CheckDef.functional_scope가 있을 때만
by_purpose:  { "요청·대상 접근 권한": { security: Verdict, inventory_complete: bool } }
by_area:     { REQ: { security: Verdict } }
not_selected: [{ check_id: str, reason: str }]
manual_required: [CheckId]                     # 선정됐지만 자동 실행 불가
limitations: [str]                             # 미검증 범위, EXAMPLE 사용, 검토 전 팩 등
approval: { approved_by: str, approved_at: RFC3339, decision: ACCEPT | REJECT, note: str } | null
evidence_counts: { <영역>: { manual: int, automated: int, not_run: int } }   # 보고서 상단 "수동 입력 N건 / 자동 측정 M건"
```

- JSON이 원본이다. 화면은 byul platform이 이 JSON만 읽어 보여주고, JSON에 없는 내용을 넣지 않는다(2026-10-25 HTML 보고서 `report/html.py` 삭제, 같은 내용은 platform 검증 결과 화면).
- `approval`은 보고서를 받아들인 기록이다. 자동 권고가 아니다(INV-07).

**버전 비교 규칙** (`security` 축 기준, 다른 축도 같은 표)

| 기준 \ 후보 | PASS | FAIL | UNKNOWN |
| --- | --- | --- | --- |
| **PASS** | NO_CHANGE | REGRESSED | UNKNOWN |
| **FAIL** | FIXED | STILL_FAILING | UNKNOWN |
| **UNKNOWN** | UNKNOWN | UNKNOWN | UNKNOWN |

비교 조건(INV-09)이 맞지 않으면 표와 관계없이 `NOT_COMPARABLE`.

**집계 규칙** (점검 → 보호 목적 → 영역)

1. 적용 점검 중 `FAIL`이 하나라도 있으면 → `FAIL`
2. 아니고 `UNKNOWN`이 있거나 `inventory_complete: false`면 → `UNKNOWN`
3. 아니고 `PASS`가 하나 이상이면 → `PASS`
4. 전부 `NOT_APPLICABLE`이면 → `NOT_APPLICABLE`

미충족은 보고서에서 `impact`(정해지면) → `review_order` 순으로 정렬한다. 집계 규칙 자체는 바꾸지 않는다.

---

## 5. 객체 관계

```
CheckDef(팩) ──┐
ENV ───────────┼──► PLAN ──► EVD (점검×side마다 1개) ──► RES (EVD마다 1개) ──► RPT
POL ───────────┤                                                              ▲
CHG* ──────────┘                                                              │
                                                               (RES 전부 + PLAN)
```

- 모든 화살표는 `refs`의 정확한 `Ref`(id+revision+sha256)로 표현한다. "최신 revision"으로 바꿔 읽지 않는다.
- 재판정: 같은 EVD로 새 RES를 만든다(판정 플러그인 버전이 바뀐 경우 등). 기존 RES는 지우지 않는다.

---

## 6. 불변식 (위반은 버그)

각 항목은 **테스트 1개 이상**으로 고정한다. 출처: A = BoBProject A1 §5, F = 프레임워크, N = 이 SPEC에서 새로 정함.

| ID | 불변식 | 출처 |
| --- | --- | --- |
| **INV-01** | `UNKNOWN`을 `PASS`로 올리지 않는다. 확인 부족과 위반 확인을 구별한다. | A1-1 |
| **INV-02** | `EXAMPLE` 자료, 기대값, 실행하지 않은 시험을 `MEASURED`로 표시하지 않는다. | A1-2 |
| **INV-03** | 시도가 없으면 차단 성공이 아니다. `challenges[].attempted: false`인데 "거부됨"으로 `PASS`를 낼 수 없다. 응답 미수신만으로 `PASS`가 되지 않는다. (주: `refused`·`timeout`·`unreachable`은 **같은 시각 신뢰 출발점에서 대상에 도달함(양성 대조)이 관측된 경우에만** 차단 근거가 된다. `dns_error`·`error`는 차단 근거가 아니다. TCP 연결 실패에서 경로별 결과를 추론한 경우(`basis: tcp_connect`)도 연결 시도는 실제로 했으므로 "시도함"이며, 같은 양성 대조 조건을 따른다. 연결이 된 뒤의 응답 없음은 `error`다.) | A1-3,4 |
| **INV-04** | 위반 증거가 없는 것과 준수 증거가 있는 것은 다르다. 센서 장애·수집 실패는 `UNKNOWN`이다. 이미 확인된 `FAIL`은 다른 누락 때문에 지우지 않는다. | A1-10 |
| **INV-05** | (2026-10-25 U1 개정) 엔진 정식 릴리스(major·minor 변경, 롤백, rc·dev, 버전 확인 불가 포함)는 변경 분석으로 시험을 줄이지 않는다(적용 점검 전체). 시험을 줄이는 것은 엔진 패치 릴리스와 증분 감사에서, 변경 트리거(패치의 항상 시험 20개·diff 변경 종류, 모델 변경 종류, 입력 변경)가 없고 이월 조건(INV-19)을 모두 만족하는 점검의 이월로만 가능하다. 이전 결과가 없거나, 이미지와 diff 버전이 맞지 않거나, 미분류 엔진 파일·연결되지 않은 환경 사실·규칙에 없는 모델 파일 변경이 있으면 해당 범위를 넓게 고른다. (개정 전: 기본 점검은 변경 분석이 "영향 없음"이어도 빼지 않는다) | A(planner) + U1 사용자 결정 |
| **INV-06** | 환경 사실을 `false`로 쓰려면 비사용·비노출 근거가 필요하다. 근거 없는 `false`는 `null`로 취급한다. 빈 조건 목록은 "항상 참"이 아니다. | F(selector) |
| **INV-07** | 자동 판정·권고는 승인이 아니다. 보고서의 `approval`은 사람만 쓴다. 코드가 `approval`을 채우지 않는다. | A1-7 |
| **INV-08** | 정책 예외는 판정을 바꾸지 않는다. 예외가 승인된 `FAIL`도 `FAIL`이다. 만료·미승인 예외는 표시하지 않고 사유를 남긴다. | A1-5,11 |
| **INV-09** | 버전 비교의 단위는 엔진 이미지다. 모델 inventory·시험 자료·의미 있는 실행 설정이 같고 확인돼야 한다. GPU는 GPU·driver, 명시적 CPU(gpu_count=0)는 architecture/model/system/kernel/threads가 같아야 한다. CPU 프로파일 없이 GPU·driver가 null인 경우는 여전히 비교 불가다. CPU는 보안·기능만 비교하고 성능은 UNKNOWN이다(2026-10-19 사용자 결정). | A1-9 + CPU 결정 |
| **INV-10** | 선정되지 않은 점검은 EVD·RES를 만들지 않는다. 선정됐지만 실행하지 못한 점검은 EVD(`status: NOT_RUN` 또는 `ABORTED`)와 RES(`UNKNOWN`)를 만든다. 둘을 같은 상태로 합치지 않는다. | A1-12 |
| **INV-11** | `PASS`는 다음이 **모두** 관측될 때만 낸다: ① 정상 대조 성공 ② 모든 챌린지가 시도되고 기대대로 거부·무효 ③ `required_observations` 전부 `collected: true`. 하나라도 빠지면 `UNKNOWN`. 과차단(정상 대조 실패)은 `PASS`가 아니다. | F(05, L3) |
| **INV-12** | 여러 근거를 집계한 `verification_level`·`data_origin`은 필수 근거 중 하나라도 null이면 null이다. null을 빼고 나머지로 대표값을 정하지 않는다. | A1-14 |
| **INV-13** | 보호 목적의 `inventory_complete`는 **그 목적에 속한 모든 CheckDef가 PLAN에서 적용 여부를 판단받았고(UNDETERMINED 제외), 팩이 REVIEWED일 때만** `true`다. `false`면 집계는 `PASS`가 될 수 없다. | N(L3 해소) |
| **INV-14** | `run`은 판정하지 않고 `judge`는 엔진에 접속하지 않는다. 판정 플러그인은 순수 함수다(같은 입력 → 같은 출력, 네트워크·파일 쓰기·시계 접근 금지). | A3, A17 X4 |
| **INV-15** | 플러그인(실행기·센서·어댑터) 실패는 근거 미확보로 전파한다. 예외를 삼켜 `PASS`·`NOT_APPLICABLE`로 바꾸지 않는다. | A17 §3 |
| **INV-16** | 객체는 append-only다. 같은 `id`의 기존 revision을 수정·삭제하지 않는다. 모든 참조는 정확한 revision과 해시로 고정한다. | A1 §2 |
| **INV-17** | `DRAFT` 정책으로 만든 보고서는 "공식 아님"으로 표시한다. 검토 전 팩도 마찬가지다. | A1-6 |
| **INV-18** | 보안·기능·성능 판정은 따로 기록한다. 한 축의 결과로 다른 축을 채우지 않는다. | F(05) |
| **INV-19** | 이월은 새 판정이 아니다. 판정기 버전이 바뀌면 **이월 자격 사전 확인 단계**에서 기존 EVD를 현재 판정기로 재판정해 새 RES를 append-only로 저장하고 원본 EVD·RES는 보존한다. **이월 단계 자체**에서는 EVD·RES를 추가 생성하지 않는다. `carried_over.result`는 사전 재판정했으면 그 새 RES의 정확한 Ref, 아니면 기존 유효 RES의 정확한 Ref이며, `carried_over.evidence`는 그 RES가 판정한 EVD의 정확한 Ref다. 보고서는 이월을 이번 실행 결과와 구분하고 원래 실행 시각과 재판정 시각을 분리하며 재판정을 새 실측으로 표시하지 않는다. 엔진 정식 릴리스는 이월하지 않는다. 다음 중 하나라도 해당하면 이월할 수 없다: 이전 보안 판정이 PASS가 아님(판정기 버전이 바뀌었으면 사전 재판정한 RES 기준) / 이전 시험의 엔진 이미지가 이번 이미지(증분 감사)·이번 기준 이미지(패치 릴리스)와 다름 / 같은 점검 ID의 CheckDef revision 또는 정의 파일 해시가 다름 / 실행기 이름·버전·설정 해시 또는 어댑터 이름·버전이 다름 / 고정 시험 자료 해시가 다르거나 시험 자료를 실행 전에 확정할 수 없음 / 실행 설정 해시가 이번 변경 유형으로 판별되지 않은 이유로 다름 / 이번 변경 트리거에 걸림 / 이전 증거가 `EXAMPLE` / 패치 릴리스에서 소스–이미지 관계가 불일치 또는 미확인. | N(U1) + F(evidence_reuse_rule) |

---

## 7. 플러그인 인터페이스

모든 플러그인은 `name`과 `version`을 가지며, 그 값을 산출물에 남긴다. 구현체 선택은 `guard.toml`로 한다.

### 7.1 엔진 어댑터 (X8 — v1에 포함, 구현은 vLLM 1개)

엔진마다 다른 부분을 이 한 곳에 모은다. 코어 코드에 `if engine == "vllm"`이 나오면 버그다.

```python
class EngineAdapter(Protocol):
    name: str
    version: str
    def start_command(self, spec: LaunchSpec) -> list[str]: ...          # 모델·포트·옵션 → 실행 인자
    def public_routes(self, env: Environment) -> list[Route]: ...        # 노출되는 HTTP 경로
    def routes_from_openapi(self, doc: dict) -> list[Route]: ...         # public_routes의 파싱 부분 (5a 추가)
    def probe_request(self, route: Route, model: str|None) -> ProbeRequest|None: ...  # 무해한 시험 요청, None=보내지 않음 (5a 추가)
    def detect_version(self, handle: ContainerHandle) -> EngineIdentity: ...  # 실제로 뜬 엔진·버전
    def read_option(self, name: CommonOption, env: Environment) -> FactValue: ...  # 공통 옵션 질문
    def parse_log(self, line: str) -> EngineEvent | None: ...            # 로그 → 공통 이벤트
    def ready(self, handle: ContainerHandle) -> bool: ...                # 기동 완료 확인
```

`CommonOption`은 엔진 중립 이름(예: `REMOTE_CODE_EXECUTION`, `API_KEY_REQUIRED`)이고, 어댑터가 엔진 고유 옵션(`--trust-remote-code` 등)으로 번역한다.

### 7.2 나머지

```python
class EnvCollector(Protocol):      # X2: 환경 사실 수집
    def collect(self, target: TargetSpec) -> dict[str, FactValue]: ...

class ChangeExtractor(Protocol):   # X3: 변경 사실 추출 (update 모드, U1). 구현은 adapters/에만
    def extract(self, repo: Path, base: str, cand: str) -> list[ChangeFact]: ...
    # repo는 엔진 소스 저장소의 로컬 clone. clone/fetch는 호출 전에 끝내고 base·cand는 커밋 SHA로 고정한다

class Executor(Protocol):          # 점검 실행기. CheckDef.executor로 지정
    def run(self, check: CheckDef, env: Environment, policy_values: dict,
            adapter: EngineAdapter, workdir: Path) -> EvidenceDraft: ...

class Judge(Protocol):             # X4: 순수 함수 (INV-14)
    def judge(self, check: CheckDef, evidence: Evidence, params: dict,
              policy_values: dict) -> JudgeOutput: ...
```

**판정기 선택**: EVD의 `execution.executor.default_judge`가 있으면 그 판정기, 없으면 CheckDef.judge. `default_judge`는 실행기 레지스트리의 메타데이터("이 실행기의 증거를 판정하는 기본 판정기")에서 증거를 만들 때 기록한다. 코어는 실행기 이름을 비교하지 않는다. 지금 메타데이터: `manual-entry` → `declared`.

**`declared` 판정기**: 수동 증거의 `declared_verdicts`를 RES로 옮긴다(순수 함수). 신고된 PASS는 첨부 0개이거나 필수 관측 문장 설명이 비면 UNKNOWN. PASS 관문은 코어가 따로 적용한다.

**`rule_table` 판정기**: 관측 키·연산자·기대값의 표로 판정한다. 자동화하는 대부분의 행은 코드 없이 이 표로 판정하는 것을 목표로 한다.

연산자: 스칼라 `eq ne in not_in lt le gt ge`, 모음 `nonempty`, `all_in`·`any_in`(리스트 원소 또는 dict 값), `any_between`·`all_between`(숫자 원소, `value: [lo, hi]`), `superset_of`(`value_obs`로 다른 관측과 비교, dict는 키 집합). 규칙에 `when: {obs, op, value}`를 달면 전제가 성립할 때만 그 규칙을 요구한다. `value_obs`·`when`이 가리키는 관측도 수집돼야 한다.

```yaml
# REQ-01
judge:
  plugin: rule_table
  params:
    pass_when_all:
      - { obs: routes_registered, op: nonempty }
      - { obs: routes_tested, op: superset_of, value_obs: routes_registered }   # 등록 경로 전부 시험
      - { obs: direct_outcomes, op: superset_of, value_obs: routes_registered } # 경로마다 직통 결과
      - { obs: direct_outcomes, op: all_in, value: [401, 403, refused, timeout, unreachable] }
      - { obs: trusted_origin_reachable, op: eq, value: true,                   # 무응답을 근거로 쓰면 양성 대조 필수
          when: { obs: direct_outcomes, op: any_in, value: [refused, timeout, unreachable] } }
    fail_when_any:
      - { obs: direct_outcomes, op: any_between, value: [200, 299] }           # 비허용 호출자의 직통 요청이 처리됨
    axes:
      functional:     # CheckDef.functional_probes 목록의 모든 항목이 기대대로 통과. 목록이 늘어도 이 표는 그대로
        pass_when_all:
          - { obs: functional_probe_results, op: nonempty }
          - { obs: functional_probe_results, op: superset_of, value_obs: functional_probes_planned }
          - { obs: functional_probe_results, op: all_in, value: [pass] }
        fail_when_any:
          - { obs: functional_probe_results, op: any_in, value: [fail] }   # 기대 코드가 아닌 HTTP 응답
    # 정상 대조(EVD.normal_control)도 같은 표본: 전부 pass일 때만 succeeded (INV-11 PASS 관문)
    # 어느 쪽도 아니면 UNKNOWN (INV-11): 일부 경로만 시험, 양성 대조 실패, 직통 404·5xx·dns_error·error, 관측 누락
```

---

## 8. 첫 번째 수직 슬라이스

**목표:** 점검 1행을 처음부터 끝까지 실제로 통과시킨다. 그다음에 행을 늘린다.

**후보:** `REQ-01` Gateway 우회 직통 요청 (`checklist.json` 요청 영역 1번 행, `Q-C01` / `SC-01-01`, `review_order: reference_baseline_first`). 프레임워크 사용성 검토가 end-to-end 예시로 권한 항목이고, 로컬 Docker로 재현할 수 있다.

| 순서 | 만들 것 | 완료 기준 |
| --- | --- | --- |
| 1 | `tools/import_checklist.py`: `checklist.json` 77행 → `packs/PACK-BASELINE/checks/*.yaml` (ID 부여) | 77개 파일 생성, 재실행해도 ID 불변 |
| 2 | `models/`: 객체 7종 + CheckDef Pydantic 모델, 해시 함수 | 예시 YAML 왕복 직렬화, 해시 고정 테스트 |
| 3 | `store/`: SQLite append-only + 증거 파일 내용 주소 저장 | INV-16 테스트 |
| 4 | `guard select`: ENV+POL+팩 → PLAN | 적용 판단 규칙 5개, INV-05·06·13 테스트 |
| 5 | `adapters/vllm.py` + `executors/direct_route.py` | 로컬 vLLM(작은 모델)을 띄워 EVD 생성 |
| 6 | `judges/rule_table.py` | INV-01·03·04·11·14·15 테스트 |
| 7 | `guard report`: RES → RPT(JSON) (화면은 byul platform) | 집계·비교 규칙 표 전체 테스트 |
| 8 | `guard update`: 두 이미지로 같은 점검 실행 → 비교 | INV-09 테스트, NOT_COMPARABLE 케이스 |
| 9 | U1: 입력 유지·`guard diff`(정식 보고·도달 확인, 패치 선정)·`guard model-diff`·패치 릴리스와 증분 감사 선정·이월 | INV-05 개정·INV-19 테스트, v0.31.0→v0.32.0 diff 재현, 과거 패치 5개 선정 재현, 모델·환경 변경 사례 ([UPDATE_SELECTION.md](docs/UPDATE_SELECTION.md)) |

그다음 확장 순서: `review_order: reference_baseline_first`이면서 로컬에서 재현 가능한 행부터 5~10개.

---

## 9. 일부러 만들지 않는 것 (BoBProject 대비)

| BoBProject에 있던 것 | v1 처리 | 이유 |
| --- | --- | --- |
| Ed25519 서명 승인·registry·철회 | 승인자·시각 기록만 | 구현량 대비 초기 가치가 낮음 |
| 감사 outbox·2단계 확정·CAS 경합 처리 | 단일 SQLite 트랜잭션 + append-only | 성능 문제의 근원, 단일 사용자 CLI에는 불필요 |
| 읽을 때마다 해시·감사 전체 재검증 | 쓸 때 검증 + `guard verify` 명령으로 일괄 검증 | 이력에 비례해 느려지는 구조를 피함 |
| 객체 20여 종(RUN/OBS/EVD 분리, CMP/REC 분리, RTP/RTE/ENS 등) | 7종 | 필요해지면 그때 분리 |
| 웹 UI·역할 4종·토큰/세션 | CLI만 | 파이프라인이 먼저 |
| 게이트웨이 구현·런타임 통제 | 없음 | 점검 대상이지 우리가 만들 것이 아님 |
| LLM 판단 보조(A23) | 없음 | 선택 기능, 나중에 |
| 54단계 프롬프트 플랜·D번호 결정 체계 | 이 SPEC + `DECISIONS.md` 한 장 | 개발 관리 비용 축소 |

---

## 10. 저장소 구조와 CLI

현행 기술 기준은 Python 3.12(`>=3.12,<3.13`)와 `uv`다. 개발·guard 제품·CI·gua의 시험 보조 Python을 이 범위로 통일한다. 대상 vLLM 이미지 내부 환경은 독립이며 기존 이미지 계약과 과거 3.11 실행 증거·팩·Ref·해시는 보존한다. 별도 3.11용 소스나 제품은 유지하지 않는다.

```
guard/
├─ SPEC.md                 # 이 문서
├─ DECISIONS.md            # 결정 기록 (날짜·결정·이유, 한 줄씩)
├─ pyproject.toml          # Python 3.12 (>=3.12,<3.13), pydantic, typer, pyyaml, pytest (httpx는 미사용이라 2026-10-19 제거)
├─ src/guard/
│  ├─ models/              # §4 객체 7종 + CheckDef + enum
│  ├─ core/                # select, run, judge, report, compare, aggregate
│  ├─ store/               # SQLite + artifacts/
│  ├─ adapters/            # vllm.py (EngineAdapter)
│  ├─ executors/           # 점검 실행기
│  ├─ judges/              # rule_table.py + 개별 판정기
│  ├─ collectors/          # 환경 수집기 (file.py 먼저)
│  ├─ application.py       # 제품 실행 합성 지점. side별 엔진 생명주기와 코어 파이프라인 연결
│  ├─ runtime/             # Docker·sandbox. adapters/·executors/·application만 사용 (CLI·코어는 직접 제어하지 않음)
│  └─ cli.py
├─ packs/PACK-BASELINE/    # §3
├─ tools/import_checklist.py
├─ lab/                    # 로컬 전용 컨테이너 실험실 (stub 엔진·nginx Gateway, 증거는 EXAMPLE)
└─ tests/
   ├─ invariants/          # INV-01 ~ INV-18 각각
   └─ ...
```

```bash
guard select  --pack packs/PACK-BASELINE --env env.yaml --policy policy.yaml   # → PLAN
guard run     --plan PLAN-...                                                  # → EVD들
guard run     --plan PLAN-... --replace-manual                                 # 수동 증거를 명시적으로 새 실행으로 대체
guard judge   --plan PLAN-...                                                  # → RES들 (재실행 = 재판정)
guard report  --plan PLAN-... --json out.json                                  # → RPT (화면: byul platform)
guard update  --pack ... --baseline env-a.yaml --candidate env-b.yaml --policy ...  # 위 전체를 두 버전으로
guard verify                                                                   # 저장소 해시 일괄 검증
guard prepare-update --pack ... --baseline ... --candidate ... --policy ... --config ... # 읽기 전용 준비 검사
guard evidence export --store ... --output evidence.zip                        # 검증한 DB 사본·artifacts·팩 반출
guard evidence restore --bundle evidence.zip --destination new-store            # 새 경로에 무결성 검증 후 복원
guard evidence template --plan PLAN-... --out manual.yaml                       # 수동 입력 양식
guard evidence import manual.yaml                                              # 검증한 DECLARED 객체 배치 입력
# U1 (구현 명령·검증 범위는 UPDATE_SELECTION.md §11)
guard env update    --base ENV-... --patch patch.yaml                          # 변경분만 입력 → 같은 id 새 revision
guard policy update --base POL-... --patch patch.yaml
guard diff --from v0.31.0 --to v0.32.0                                         # 엔진 소스 diff → CHG (정식: 보고, 패치: 선정)
guard update ... --change CHG-... --previous-plan PLAN-...                      # 엔진 패치 릴리스: 선정 점검만 기준·후보 시험 + 이월
guard model-diff --from <snapshot|manifest> --to <snapshot|manifest>           # 모델 manifest diff → CHG (선정용)
guard select --env ... --policy ... --previous-plan PLAN-... [--change CHG-...] # 증분 감사
```

---

## 11. AI 코딩 에이전트에게 주는 작업 규칙

1. **이 SPEC이 정본이다.** 원본 프레임워크·BoBProject 문서와 충돌하면 이 SPEC을 따르고, 충돌 사실을 `DECISIONS.md`에 적는다.
2. **§8의 개발 순서를 따른다.** 사용자 지시에 따라 합의한 구현 묶음을 마친 뒤 테스트하며, 묶음 안의 각 단계마다 테스트를 실행하지 않는다.
3. **사용자 지시(2026-10-19): 구현 묶음을 마친 뒤 테스트 → 실패 수정 → 필요한 재검증 순으로 진행한다.** 불변식마다 `tests/invariants/test_inv_XX.py` 회귀를 유지한다. 구현이 불변식을 어기면 구현을 고치고 불변식을 완화하지 않는다.
4. **§9 목록에 있는 것을 만들지 않는다.** 필요해 보이면 멈추고 사람에게 묻는다.
5. **실제 결과를 꾸며 넣지 않는다.** 실행하지 않은 시험의 EVD를 만들거나 예시 값을 `MEASURED`로 표시하지 않는다(INV-02).
6. **코어에 엔진 이름을 쓰지 않는다.** 엔진별 코드는 `adapters/`에만 둔다.
7. **사용자 지시(2026-10-24): 60초를 넘겨도 중단하지 않으며 시간 초과 실패 gate를 추가하지 않는다.** 전체 suite의 pytest 시간과 부모 프로세스 벽시계를 각각 기록한다. 중복 준비 비용은 줄이되 기존 사례·assert·기대값·불변식을 완화하거나 새 skip/제외로 통과시키지 않는다. 실제 엔진이 필요한 테스트를 `@pytest.mark.engine`으로 분리하는 기존 기본 제외 조건은 유지한다.

---

## 12. 남은 결정 (팀 확인 필요)

| # | 항목 | 제안 |
| --- | --- | --- |
| D1 | 점검 행 ID 체계 | §2.1 영역코드-순번 |
| D2 | CheckDef에 `impact`(상/중/하) 추가 여부 | 추가. 보고서 정렬에만 사용 |
| D3 | 수치 미정 40행의 기준값 도출 방법 | 기준선 측정 후 한도 규칙. 05 문서에 방법 추가 |
| D4 | `UNDETERMINED` 점검을 선정할지 | 선정 (§4.4). 빼면 미검증 범위가 숨겨짐 |
| D5 | 첫 슬라이스 점검 | `REQ-01` Gateway 우회 직통 (§8) |
| D6 | 버전 비교에서 기능·성능 축도 같은 표를 쓸지 | 같은 표. 성능은 수치가 정해진 뒤 |
| D7 | 두 번째 엔진 어댑터 시점 | 우선 점검 10행이 vLLM에서 돈 뒤 |

## M5 수동 정책 문맥·충돌 판정 보완

- declared 0.4.0은 LIF-07 준비 전 수락의 확인된 보안 FAIL을 추론 결과 충돌로 지우지 않는다. 동일 사건의 수락/차단 모순은 위반 확정을 보류한다. 다른 유효 순서 확인 방법은 보안 위반의 반증이 아니지만 충돌 사건의 PASS는 계속 보류한다. 순서 미확인·첨부 미연결은 위반을 확정하지 않는다. 서로 다른 필수 준비 항목의 상태 차이 자체는 모순이 아니다. 원본·신고 FAIL·UNKNOWN 비승격·기능 정상 묶음·성능 미검증 계약을 유지한다.
- SUP-01/SUP-02에 선언된 required str approval_record_system은 승인 도구·기록 체계의 정책상 식별 문맥이다. 키 누락은 NO_POLICY_VALUE, null/타입 오류는 기존 select 오류다. 대상 선언이 있고 문자열이 공백이면 declared PASS를 UNKNOWN으로 보류한다. 정상 값은 권한/진위 증명이 아니며 기존 PASS 관문을 우회하지 않는다. 두 점검 값은 독립이며 자동 복사하지 않는다.
- 신규 공백 전제는 해당 선언이 없는 과거 팩에는 소급하지 않는다. /1 읽기, /2 생성·manual-entry 0.3.0·policy-evidence/1을 유지하고 scalar를 항목별 증거에 넣지 않는다. Report.limitations는 기존 POLICY_ITEMS: 외에 POLICY_CONTEXT: 이유도 전달한다. EVD/기존 RES는 보존한다.
- PACK-BASELINE revision 12/DRAFT, SUP-01/SUP-02 revision 2. 정책 선언은 9점검/15경로, 자동 실행/판정 연결은 5개다. OBS-11은 미등록·미구현이며 [후속 계획](docs/POLICY_M5_PLAN.md)을 먼저 합의한다.

## M7 OBS-11 수동 검증 계약

- 타입 지원에 dict[str,int]를 추가한다. 문자열 키·정수 값의 얕은 검사이며 bool은 int가 아니다. 기존4타입·누락 키 NO_POLICY_VALUE·null/범위 밖 타입 검사는 유지한다. OBS 전용 의미 검증은 순수 helper에 둔다.
- OBS-11은 required_records와 사건별 collection_deadline_ms/max_clock_uncertainty_ms/detection_deadline_ms/alert_deadline_ms를 required로 선언한다. 수집C는 발생t0부터, 식별D·경보A는 수집 기한t0+C부터 각각의 예산이다. 실제 값/승인을 만들지 않는다.
- 새 수동 양식/3·manual-entry0.4.0·declared0.5.0이다. /1 읽기는 기존 계약, /2는 정확 POL/팩 Ref 및 policy_evidence 검증 유지, /3은 추가 audit_evidence를 지원한다. /1·/2의 audit 숨김과 /1의 policy_evidence 숨김은 거부한다. 기존 guard-policy-evidence/1은 보존한다.
- OBS 전용 guard-audit-evidence/1은 기존 EVD.observations.declared_audit_evidence에 저장한다. normal/collection_fault/clock_uncertain별 프로파일 적용·실제 시도·독립 발생·시계/조회 창·경보/종속/복구 근거를 연결한다. 현재 첨부만 참조하고 배치 사전 검증·추가 전용 저장·의미 있는 config hash를 유지한다.
- 시간은 명시 timezone·최대 밀리초 정밀도로만 지원한다. 현재 시각을 보충하지 않으며 오차 구간 상한≤예산은 기한 내, 하한>예산은 기한 후, 경계 겹침/허용 오차 초과는 미확인이다. 부재 조회는 시작·종료·완전성·독립 범위와 별도 경보 수신점 근거를 요구한다.
- 최초 프로파일은 필수 누락 모두에 경보가 필요한 경우다. 적용/독립 근거를 자동 인증하지 않는다. 확정 누락 자체는 관측 미충족이며 OBS11 FAIL은 이를 정상/종속PASS 처리한 직접 근거 또는 필요한 경보의 확정 부재에 한정한다. 근거 부족/모순은 필요한 전제별 UNKNOWN, 독립 확인FAIL·신고FAIL·UNKNOWN 비승격·INV11을 유지한다.
- 신규 상한은security만이다. 기능/성능 신고PASS는UNKNOWN, 신고FAIL은 유지하며 기존CPU 등의 상위 제약은 그대로다. clock_uncertain은 근거로 확인된 경계 겹침을 대상이 UNKNOWN 처리했는지의 범위만 평가한다. 단순 시각 누락은 이 case의 성공이 아니다. 실제 다른RES를 수정/조회/집계 제외하지 않는다.
- REPORT는 AUDIT_COLLECTION: 이유를 기존 limitations에 전달한다. 해시/항목/case·미확인/한계만 표시하고 원본 locator/basis·경로를 노출하지 않는다. DECLARED·미인증 자기 신고·DRAFT/official 계약을 유지한다. 정책 선언 없는 과거 정의에 신규 요구를 소급하지 않는다.
- PACK-BASELINE13/DRAFT·OBS11 정의2·10점검20경로·자동 연결5. 자세한 필드/제약은 docs/POLICY_M7.md다. 실제 엔진/collector·두 이미지·운영 체계 실증은 별도다.

## M8 usage·replay 수동 정책 문맥

- OBS-04/05의 usage_allowed, OBS-07/08의 replay_allowed를 required bool로 선언한다. POL은 해당 점검 범위의 허용 의사이며 실제 활성·비활성·도달·권한의 ENV/EVD를 대신하지 않는다. 새 ENV 키·실행기·전역 정책 상속은 없다.
- OBS04는 false, OBS05/07/08은 true가 기존 수동 검토의 문맥이다. 다른 bool 또는 미확인 값이면 각 축의 신고 PASS만 UNKNOWN으로 보류한다. 활성이어도 기존 증거/INV11 관문이 필요하다. 신고 FAIL·기존 독립 확인FAIL·UNKNOWN 비승격을 보존한다. 플래그만으로 새 FAIL·NOT_APPLICABLE·자동 제외를 만들지 않는다.
- select의 기존 bool 타입/누락 검사를 재사용한다. false는 값이고 null/0/1/문자열은 변환하지 않는다. 이 전제는 네 대상의 정확한 required bool 선언에만 적용하며 과거/optional/다른 타입/비대상 정의에는 소급하지 않는다.
- declared0.6.0, manual-entry0.4.0·수동/3·기존 증거 계약과 객체7종·코어·저장소는 유지한다. 기존 POLICY_CONTEXT와 팩 한계 표시로 실제 값·분기·자동 검증/공유 정책 일관성 한계를 보고한다. 기업값·승인·실제 usage/replay 검증을 만들지 않는다.
- PACK-BASELINE14/DRAFT·네 정의2·14점검24경로·a21/b6/c161/d115, 77ID/303결정595연결·자동 연결5다. [M8 계약](docs/POLICY_M8.md)의 분기 표·구형 증거·후속 계획을 따른다.

## M10 REQ-09 입력 범위 계약

- REQ-09의 required `allowed_endpoints`·`allowed_input_formats`는 list[str]다. 정상 engine POST 표본과 정확히 같은 endpoint 하나·`token_ids` 하나만 지원한다. 기업 전체 허용 목록을 줄이거나 기본값을 채우지 않는다. REQ-07/08/10·복수 관계표·재사용 ID/renderer는 후속이다.
- 정책2개·관측2개의 정확한 선언 묶음에 적용한다. 모든 새 이름이 없는 과거 정의는 기존 계약, 일부/잘못된 선언은 PASS 보류다. 순수 input_policy가 실행 전·rule_table·declared의 의미를 공유한다. 의미 오류는 target/readiness/Docker/probe 전 ABORTED, 누락은 기존 NOT_RUN, 잘못된 타입은 선정 오류다.
- 실제 제출에서 input_endpoints_tested·input_formats_tested를 수집하고 기존 정상3·경계2 요청을 보존한다. 두 관측의 전체 정책 일치·지원 프로파일을 보안/기능 PASS 전제로 사용한다. 오프라인 재판정에도 PASS 상한을 적용하고 기존 FAIL/UNKNOWN은 보존한다. 성능 규칙은 추가하지 않는다.
- /3 generic 수동 관측과 --include-automated·override_reason을 사용한다. 새 계약 REQ-09에 한해 두 관측 값/collected를 config hash에 반영한다. 과거 정의/다른점검 입력과 기존 EVD/RES는 보존하며 /1·/2 읽기도 유지한다.
- input_validation0.3.0·rule_table0.5.0·declared0.7.0·manual-entry0.5.0. core/report 한 파일에서 rule_table의 POLICY_CONTEXT 전달만 보강한다. official·축·출처·비교 규칙은 유지한다.
- 팩15/DRAFT·REQ09정의5·14점검26경로·a23/b4/c161/d115·자동연결5다. 실제 parser 소비·전체 원문 요구·기업 승인·실제 엔진은 미검증이다. [M10 구현/검증](docs/POLICY_M10.md)을 따른다.

## M11 REQ-09 수동 PASS 일관성

- 사용자 실행 지시로 M10 신규 계약 REQ-09의 보안/기능에 고정 CheckDef rule_table의 PASS 상한을 추가한다. 수동 신고 PASS와 판정표 PASS가 일치해야 기존 PASS 후보를 유지한다. 판정표 FAIL은 상충 UNKNOWN, UNKNOWN/규칙부재/미지원/평가오류는 근거부족 UNKNOWN이다. 수동 FAIL·UNKNOWN은 그대로 보존한다.
- 판정 코어를 재호출하지 않고 순수 판정표를 직접 조합한다. default_judge=declared의 재귀를 피하고 의존 예외로 신고 FAIL을 지우지 않는다. 원문 관측/예외를 노출하지 않는 POLICY_CONTEXT 사유를 기존 JSON 보고서에 전달한다.
- declared0.8.0이다. 수동 성능·명시 CPU·INV11·첨부/답변/정상/챌린지·M10 범위·과거 정의/부분선언·다른점검을 보존한다. 실행기/코어/모델/Store/양식/Ref/side/config hash 입력은 변경하지 않는다. M10 상충 증거는 그대로 두고 새 RES에서 PASS→UNKNOWN을 기록하며 동일 as_of는 멱등이다.
- 팩15/REQ09정의5·77점검·14정책점검26경로·a23/b4/c161/d115·자동연결5 유지. 캐시 설계와 M12 프롬프트는 [M11 문서](docs/POLICY_M11.md)의 미구현 제안이며 현재 제품 계약이 아니다.

## M12 REQ-16 필수 캐시 문맥 수동 계약

사용자 실행 지시로 M11 §4의 제한 계약을 채택한다. required list[str] `values.REQ-16.required_cache_contexts`와 dict 관측 `declared_cache_context_evidence`를 등록한다. 비어 있지 않은 고유 model_generation/adapter_content/media_identity 부분집합만 지원하며 전체 기업 목록을 줄이거나 자동 정규화하지 않는다.

`guard-cache-context-evidence/1` / `same_authenticated_group_private`는 수동 /3 generic 값이다. 필수 문맥별 same_context/changed_context/post_control·동일 trial/target/workload·고유 event·전후 쌍·첨부 hash/locator/basis를 순수 검증한다. 사건 순서·신원·실제 소비 객체·hit 귀속·첨부 진위는 사람이 신고하며 자동 인증하지 않는다. 구조·전제 누락/상충은 PASS를 보류하며 미완결 generic 값의 전역 import 계약은 바꾸지 않는다.

declared0.9.0의 REQ16 상한은 신고 PASS만 제한한다. 보안은 문맥·객체·key·cache effect 계약, 기능은 그 계약과 모든 행 추론 성공을 요구한다. 추론 실패만으로 별도 신고된 격리 사실을 자동 부정하지 않는다. old_context_reused는 보안/기능 PASS를 UNKNOWN으로 보류하고 새 자동 FAIL은 만들지 않는다. 신고 FAIL/UNKNOWN을 보존한다. 새 계약 성능 PASS는 UNKNOWN이며 명시 CPU의 기존 성능 UNKNOWN 규칙이 우선한다. INV11·첨부·답변·기존 REQ09/SUP/LIF/OBS를 유지한다.

모든 새 표식이 없는 과거 정의는 legacy, 부분/잘못된 선언은 보류다. collector/양식·core/models/store/adapter/HTTP/실행기·config hash 입력은 변경하지 않는다. 관측 변경은 EVD content hash, 판정 버전 변경은 새 RES에 남기며 원본을 바꾸지 않는다. REQ16정의2·팩16/DRAFT·77점검·15정책점검27경로·a24/b3/c161/d115·303결정595연결·자동실행5다. PUBLIC-PREFIX와 REQ15/17·실제 캐시 자동 실행은 미구현이다. 상세 계약/명령/검증은 [M12](docs/POLICY_M12.md).

## M13 REQ-16 허용 enum과 미확인 상태

사용자 지시로 POL-M13-UNKNOWN-AXES를 채택한다. guard-cache-context-evidence/1의 unknown은 enum에 선언된 경우 유효한 관측 상태이며 형태 오류와 구분한다. 빈/공백·잘못된 타입은 RECORD_INCOMPLETE, 미지원 문자열은 ENUM_INVALID다. 식별자·집단·신원/상세/첨부 근거의 unknown 표식은 계속 근거 부족이다.

보안 구조·격리 관측·첨부·기존 INV11 관문이 완결된 신고 PASS에서 inference의 failed/not_attempted/unknown만으로 독립된 보안 근거를 지우지 않는다. 기능은 모든 행 succeeded를 요구한다. 격리 관련 object_change/other_conditions/key_relation/cache_effect의 unknown은 보안/기능 PASS를 보류한다. raw FAIL/UNKNOWN·성능 UNKNOWN·CPU·legacy/부분선언·REQ09/SUP/LIF/OBS는 보존한다. 과거 RES UNKNOWN과 EVD 원본 신고 UNKNOWN을 구분하고 동일 증거의 새 판정 버전으로만 변화시킨다.

declared0.9.1과 기존 캐시 helper만 변경한다. 수동 구조/1·양식/3·/1/2읽기·기존 config hash·팩16/DRAFT/REQ16정의2·77점검·15정책점검27경로·303결정595연결·a24/b3/c161/d115·자동실행5는 그대로다. REQ15 신규 계약과 M14는 [M13](docs/POLICY_M13.md)의 미구현 제안이다.

## 자동화 1차 — Windows CPU와 실제 관측 확대

2026-10-23 사용자 승인 범위는 기존 5개 점검 보강과 REQ-10/REQ-13/OBS-03 자동화다. `guard prepare-validation`/`guard validate`의 단일 managed 실행 경로와 명시적 CPU 자원 프로파일을 추가한다. 기술 설정과 POL을 분리하고, 원본 77개 ID·7객체·보존 증거·기존 판정 동작을 유지한다. 구현·계약·미검증 범위와 실행 후 결과는 [자동화 1차](docs/AUTOMATION_PHASE1.md)를 따른다. 이 범위 승인은 §0의 5~10개 우선 자동화 범위 안이며 향후 A/B/C 확대는 실제 관측과 인프라 조건별로 진행한다.

## U1 변경 유형별 시험 선정 (2026-10-24·25 사용자 결정, 구현·검증 범위는 설계 §11)

- 정본 설계는 [UPDATE_SELECTION.md](docs/UPDATE_SELECTION.md), 구현 지시는 [UPDATE_SELECTION_PROMPT.md](UPDATE_SELECTION_PROMPT.md), 결정은 `DECISIONS.md` U1 절이다.
- **엔진 정식 릴리스**(major·minor가 바뀜, 롤백·rc·버전 확인 불가 포함): 적용 점검 전체를 기준·후보 두 이미지로 시험·비교한다. 줄이지 않고 이월하지 않는다. vLLM 소스 diff는 보고서의 바뀐 영역 표시, 도달 확인(이 배포가 쓰는 진입점은 미도달 0이 목표), 체크리스트 공백 후보에만 쓴다.
- **엔진 패치 릴리스**(major·minor 같고 패치 자리만 올라감, 예: 0.32.0 → 0.32.1): 항상 시험 20개(경로 규칙으로 추적할 수 없는 흩어진 속성), 소스 diff의 변경 종류가 가리키는 점검, 입력 변경에 걸리는 점검을 기준·후보로 시험한다. 미분류 엔진 파일이 있으면 변경 종류 표의 모든 점검을 고른다. 나머지는 이전 PASS를 이월한다.
- **모델 변경**(엔진 이미지 같음): 모델 manifest diff의 변경 종류(가중치·추가 코드·template·tokenizer·아키텍처)와 모든 모델 변경 공통 점검을 시험한다. 규칙에 없는 모델 파일 변경은 모든 모델 변경 종류를 적용한다.
- **환경·정책 변경**(엔진 이미지·모델 같음): 바뀐 정책 값의 점검, 새로 범위에 들어온 점검, 바뀐 ENV 사실을 참조하는 점검을 시험한다. 어느 점검과도 연결되지 않은 사실이 바뀌면 적용 점검 전체를 시험한다.
- 패치 릴리스와 증분 감사에서 선정되지 않은 점검은 이전 보안 PASS를 이월할 수 있다(INV-19). 증분 감사는 새 구성 한 쪽만 실행하며 버전 비교(Comparison)를 내지 않는다(INV-09).
- CHG의 근거 파일(변경 목록·원본 해시·적용 규칙 해시·미분류 목록)은 내용 주소 artifact로 저장하고 `guard verify`·반출·복원이 누락·변조를 검사한다. 분석 시각·개인 경로·임의 ID는 시험 조건 해시에 넣지 않는다. 사람이 쓴 CHG와 자동 추출 CHG는 같은 형식·검증 함수를 쓴다(2026-10-25 팀원 개선안 반영).
- 엔진 경로 규칙·추출기는 `adapters/`, 모델 파일 규칙은 `collectors/`, 변경 종류 → 점검 표·패치 항상 시험 목록·모델 변경 종류 → 점검·`fact_triggers`는 팩(`change_selection.yaml`)에 둔다.
- CheckDef에 프레임워크 `framework_id`(CHK-…)·`mechanism_ids`를 더하되 내부 ID(`REQ-01` 등)는 바꾸지 않는다. 체크리스트 원본은 mg-princess/serving-security-framework `729216b1`(77행·순서·subtest_ids 동일, 질문 문구 개정) 기준으로 갱신한다.
- 기존 audit(이전 PLAN 없이), 기존 `guard update`(이전 PLAN 없이)의 시험 범위·`guard.update_validation`, 저장 증거 재판정은 그대로 둔다.
