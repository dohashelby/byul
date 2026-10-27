# byul contracts 0.1 (DRAFT)

update(격리 검증, 원래 serving-guard), runtime(탐지·차단, 원래 serving-runtime), platform(통합 화면)이 **밖으로 주고받는 데이터**의 형식이다. 각 프로젝트의 내부 구조는 바꾸지 않는다. 경계에서만 이 형식으로 변환한다.

- 상태: **DRAFT**. 세 담당자가 같이 확인하기 전까지 0.x로 둔다. runtime 계획서의 D-03(검증 도구 연동)이 이 문서로 닫힌다.
- 근거: serving-guard `SPEC.md` §2·§4, serving-runtime `docs/내부_기록형식.md`와 계획서 v1.4 §5.4(파일 교환 계약). 새 개념은 만들지 않았고 두 저장소에 이미 있는 것을 맞췄다.
- 확인: `uv run --no-project --with jsonschema python contracts/tools/check_contracts.py`

## 흐름

```
                 environment ─┐
platform(입력) ──             ├─► update  : ENV·POL로 변환 → 격리 시험 → verification-result ──► platform
                 policy ──────┤
                              └─► policy-bundle ─► runtime : 정책 적용 → runtime-event / runtime-status ──► platform
                                                     ▲   │
update ── run-context (격리 시험에서 runtime 통제를 켤 때) ┘   └─ revalidation-request ─► platform ─(사람)─► update 증분 감사
양쪽 ── evidence-index (증거 파일 목록) ──► platform
```

## 파일

| 스키마 | 방향 | 무엇 | 기존 형식과의 관계 |
| --- | --- | --- | --- |
| `common` | - | ID, 참조, enum | guard SPEC §2.1~2.4 |
| `environment` | platform → update, runtime | 기업 서빙 환경 | guard ENV 고유 필드 + runtime `runtime-target/1.0` + intent의 배치 정보. 선택 `framework_profile`(아래 `framework-input`) |
| `policy` | platform → update, runtime | 기업 정책 한 판본 | `verification` = guard POL, `runtime.gateway` = `runtime-mvp/1.1·1.2`, `runtime.enforcement` = `runtime-enforcement-intent/1.x` 원문. 선택 `framework_values`(아래 `framework-input`) |
| `policy-bundle` | platform → runtime | 승인된 정책을 runtime 내부 형식 파일로 묶은 것 | §5.4 `approved-policy.json` |
| `run-context` | update → runtime | 격리 시험 실행 문맥 | §5.4 `run-context.json` |
| `verification-result` | update → platform | 검증 결과·보고서 화면 자료 (0.2) | guard RPT의 투영. 0.2부터 삭제된 update HTML 보고서가 보여주던 RPT 필드를 모두 담는다 |
| `runtime-event` | runtime → platform | 사건 한 줄(JSONL) | `runtime-mvp-record/1.0`, `runtime-observation/1.0` 등을 `native`에 감쌈 |
| `runtime-status` | runtime → platform | 상태 스냅샷 (0.2) | `/api/status`·`/policy`·`/collectors`·`/target`·`/baseline`·`/summary`·`/revalidation`·`/observations`의 합성 |
| `runtime-lab-report` | runtime → platform | kind lab 시험 결과 (0.1, 신규) | `/api/lab-report` |
| `runtime-request-detail` | runtime → platform | 요청 상세 (0.1, 신규) | `/api/requests/{id}` |
| `revalidation-request` | runtime → platform → update | 재검증 요청 | `runtime-revalidation-request/1.0` |
| `evidence-index` | update, runtime → platform | 증거 파일 목록 | §5.4 `evidence/index.json` |
| `framework-input` | platform → update | environment·policy 선택 필드의 정의 묶음(독립 문서 아님): 환경 역할·확인 상태·12개 환경 조건·구조화된 비사용 근거, 36개 보호 요구별 승인 기준(유지·변경·확인 요청, 수치의 단위·범위·상태)·승인 조건 | framework `02`·`03`·Q 보조기 claim. 조건·요구 안의 키와 선택지는 `platform/web/src/lib/frameworkCatalog.ts`가 정한다. 현재 update·runtime은 읽지 않고 원본에 보존한다 |

`examples/`에 스키마마다 예시가 있다. 전부 `EXAMPLE` 자료다.

## 원칙

1. **모르면 null 또는 UNKNOWN.** 0, 빈 문자열, 추정값으로 채우지 않는다.
2. **자료 등급은 세 가지다.** `MEASURED`, `DECLARED`, `EXAMPLE`. 생산자의 원래 출처 값은 `origin_detail`에 남긴다.
3. **탐지는 차단이 아니다.** `detected`(탐지), `enforce_attempted`(집행 시도), `denied`(거부 확인)를 섞지 않는다. Hubble은 관측원이라 `enforcer`가 될 수 없다(스키마가 막는다).
4. **원본을 잃지 않는다.** runtime 사건은 내부 기록을 `native.record`에 그대로 둔다. 검증 결과는 `report_ref`로 RPT 원본을 가리킨다.
5. **비밀과 본문은 넣지 않는다.** 프롬프트·응답·토큰·Secret 내용은 어느 계약에도 없다.
6. **승인은 기록일 뿐이다.** `approval`은 누가·언제의 자기 신고이고 서명이 아니다.
7. **데이터에는 영문 enum만 쓴다.** 한국어 표시는 화면에서만 바꾼다.

## 연결 키

| 키 | 만드는 곳 | 쓰는 곳 |
| --- | --- | --- |
| `target_ref` | platform(environment) | policy, bundle, runtime의 `target_id`, 사건 |
| `policy_ref` = `policy_id` + `policy_version` + `sha256` | platform(policy) | bundle, run-context, 사건, 상태, 재검증 |
| `environment_ref` = id + revision + sha256 | platform(environment) | policy, run-context |
| `run_id` = guard PLAN ID | update | run-context, runtime 사건의 `scope.run_id` |
| `check_id` (`REQ-01` 등) | update 팩 | policy.verification, run-context, verification-result, 사건의 `related_checks` |
| `request_id` (32자 hex) | runtime Gateway | 사건 `event_id`(`req:`), 증거 |
| `observation_session_id` | runtime Gateway | 사건, 상태 |

해시는 serving-guard SPEC §2.3과 같다: 정렬된 키, UTF-8, 공백 없는 JSON의 SHA-256. 파일은 bytes 그대로의 SHA-256이다.

### policy_ref 해시가 두 가지인 점

- `policy.json`(byul 정책 문서)의 정규 해시는 bundle·run-context의 `policy_ref.sha256`이다.
- runtime이 실제로 읽는 `runtime-policy.json` 파일 bytes의 해시는 runtime 사건·상태의 `policy_ref.sha256`이다(runtime이 이미 그렇게 기록한다).
- 둘은 bundle의 `files[].sha256`으로 이어진다. 화면은 bundle을 거쳐 "이 사건은 어느 byul 정책 판본에서 나왔나"를 찾는다.

## 변환 규칙

### runtime Gateway 판정 → `outcome`

| `decision.result` | 조건 | `outcome` | `enforcer` |
| --- | --- | --- | --- |
| `ALLOWED` | | `allowed` | `gateway` |
| `DENIED` | `delivery.attempted: false` | `denied` | `gateway` |
| `DENIED` | 전달을 시도함 | `error` | `gateway` |
| `INVALID` | | `invalid` | `gateway` |
| `ENGINE_REJECTED` | | `engine_rejected` | `gateway` |
| `ERROR` | | `error` | `gateway` |
| (final 기록 없음) | started만 있음 | `unknown` | `gateway` |

요청 하나에 사건 하나를 낸다. final 기록이 있으면 final을 `native`에 넣고, started만 있으면 started 기록으로 `unknown` 사건을 낸다.

### runtime 관측 → `outcome`

| 원본 | 조건 | `outcome` | `enforcer` |
| --- | --- | --- | --- |
| Hubble flow | 모든 verdict(`DROPPED` 포함) | `observed` | `null` |
| Tetragon exec | `exec_verdict`가 `allowed_in_phase`·`exception` | `observed` | `tetragon` |
| Tetragon exec | `outside_allowlist`·`suspect_binary`·`exec_from_cache`, `action_mode: monitor` | `detected` | `tetragon` |
| Tetragon exec·file | `action_mode: enforce` | `enforce_attempted` | `tetragon` |
| Tetragon exec | `phase_unknown`·`phase_signal_unverified` | `unknown` | `tetragon` |
| Tetragon file_access | `action_mode: monitor`, `rule_id`: `TG-ASSET-01`·`TG-ASSET-02`·`TG-SECRET-01` | `detected` | `tetragon` |
| Tetragon file_access | `action_mode: monitor`, `rule_id: TG-CACHE-01`, `cache_generation: current` | `observed` | `tetragon` |
| Tetragon file_access | `action_mode: monitor`, `rule_id: TG-CACHE-01`, `cache_generation`: `other`·`outside` | `detected` | `tetragon` |
| Tetragon file_access | `action_mode: monitor`, `rule_id: TG-CACHE-01`, `cache_generation` 없음 | `unknown` | `tetragon` |
| Tetragon file_access | 그 밖의 알 수 없는 action 또는 rule | `unknown` | `tetragon` |

Hubble `DROPPED`는 출발·목적지·정책 귀속과 수신 대조 전에는 거부 확인이 아니다(runtime 문서 §7).
비고: runtime 결정(2026-10-26), 근거: `runtime/docs/contracts_대조_2026-10-26.md`.

### runtime `data_origin` → 등급

| runtime 값 | 등급 |
| --- | --- |
| `real`, `external_backend`, `kubernetes_api`, `local_file` | `MEASURED` |
| `declared`(manifest 선언 필드) | `DECLARED` |
| `mock`, `synthetic`, `actual_test`(모의 엔진) | `EXAMPLE` |
| `fake_backend_test` | `EXAMPLE` |
| 사람이 입력한 정책·예외·승인 | `DECLARED` |

모의 엔진에서 실제로 돌린 시험(`actual_test`)도 실제 엔진 결과가 아니므로 `EXAMPLE`로 낮춘다.
비고: runtime 결정(2026-10-26), 근거: `runtime/docs/contracts_대조_2026-10-26.md`.

## 이번 초안에서 보수적으로 정한 것

- `policy`는 update와 runtime 정책을 한 문서에 담는다. 그래야 시험과 차단이 같은 판본으로 이어진다.
- runtime 내부 형식(`runtime-mvp/1.1`, intent 1.1, exec 예외)은 바꾸지 않고, platform이 변환해 bundle에 넣는다.
- `runtime.enforcement.intent`와 `exec_exceptions`의 내부 검증은 runtime에 맡긴다. 여기서는 원문 객체로만 받는다.
- `revalidation-request.evaluation_refs`는 runtime이 모르면 빈 배열이다. 시험 선정은 update가 한다.
- 사건의 `related_checks`는 매핑 규칙이 정해질 때까지 빈 배열이다.
- 재검증은 자동으로 시험을 돌리지 않는다. 사람이 platform에서 보고 시작한다.

## 열린 질문

1. **정책 승인 주체:** 현재 `approval`은 자기 신고다. 플랫폼 로그인과 역할(D-03/D-08)이 정해지면 `approved_by`를 인증된 주체로 바꿀지.
2. **사건 → 점검 매핑:** Tetragon 규칙 `SP3-EXEC`를 어느 `check_id`와 잇는지. 매핑표를 누가 관리할지.
3. **운영 사건 보존 기간과 저장소:** 사건 규모에 따라 Postgres로 충분한지, OpenSearch가 필요한지.
4. **환경 `facts`와 runtime 관측의 충돌:** runtime이 관측한 값(예: prefix cache 사용)이 platform 신고와 다를 때 environment를 어떻게 갱신할지. 지금은 runtime이 environment를 고치지 않는다.
5. **verification-result 생산 위치:** 지금은 platform이 RPT를 읽어 변환한다(`platform/api/.../sources/update.py`). update가 직접 내보낼지는 미정.
6. **번들 전달 경로:** 같은 호스트의 관리 디렉터리(§5.4)로 시작한다. LAB 수신 방식은 runtime 결정 기록(`runtime/docs/결정기록_정책묶음_수신.md`) 참조. 원격 전달은 미정.
7. **보고서 표시 규칙의 소유:** 대표 상태·종합 판정·비공식 사유의 표시 규칙은 update HTML 보고서에서 platform으로 옮겨 왔다. 이 값들을 update가 RPT에 직접 넣을지 정해야 한다.

## 변경 이력

### runtime 결정(2026-10-26) · Hubble 수집 손실 상태

`runtime-status` 0.2의 판본과 DRAFT 상태를 유지하면서 observation feed detail에 nullable `lost_events_total`, `lost_events_by_source`, `last_lost_at`, `loss_detected`를 추가하고 collector 상태에 `partial`을 추가했다. 해당 없거나 Hubble 유효 줄을 아직 처리하지 않은 경우는 `null`이며, `lost_events_total: 0`은 처리한 줄에서 loss 레코드를 보지 못했다는 뜻이지 무손실 확인이 아니다. lost-events 레코드는 `runtime-event`로 변환하지 않는다.

### runtime 결정(2026-10-26) · 미측정 개수 `null` 보존

`runtime-status` 0.2의 판본과 DRAFT 상태는 유지한다. runtime이 자산 root를 순회하지 못했을 때 platform으로 변환되는 `asset_detail.extra_files`·`extra_symlinks`·`walk_errors`, 정책이 없을 때의 `policy.validate_count`에만 `null`을 허용했다. 나머지 개수 필드는 기존 정수 제약을 유지한다.

### 2026-10-25 · `verification-result` 0.2 (`byul-verification-result/0.2`)

update의 HTML 보고서를 없애고 화면을 platform으로 옮기면서, 그 보고서가 보여주던 RPT 필드를 계약에 넣었다. 다른 계약은 그대로 0.1이다.

- 추가(최상위): `official`, `policy_status`, `inputs_manifest`(팩 포함, `$defs/manifest_ref`), `summary.by_purpose`, `manual_required`, `not_selected`
- 추가(점검 행): `area`, `purpose`, `question`, `review_order`, `impact`, `tested_while_undetermined`, `functional_comparison`, `comparison_reasons`, `exception`(상태·사유·만료), `replaced`, `functional_scope`
- 바꿈: `summary.by_area`·`by_purpose` 항목은 `security`와 `inventory_complete`만(`$defs/axis_summary`). 점검 측 `functional`·`performance`는 RPT에 없으면 `null`(0.1은 `UNKNOWN`으로 채웠다 — 원칙 1 위반을 바로잡음)
- 없앰: 점검 행 `exception_approved`(→ `exception.status`)
- 쓰는 곳: platform만(update·runtime은 이 문서를 읽거나 쓰지 않는다). 예시 `examples/verification-result.json`은 `update/out/report.json`에서 다시 만들었고(점검은 SUP-01만 남김) `tools/check_contracts.py` 통과.

### 2026-10-25 · runtime 화면 계약 (`runtime-status`·`runtime-event`·`revalidation-request` 0.2, 신규 `runtime-lab-report`·`runtime-request-detail` 0.1)

runtime 자체 대시보드(삭제)가 보여주던 항목을 platform이 계약 형식으로만 받도록 넣었다. runtime 조회 API 응답에서 옮긴 값이며 비밀값·본문은 없다.

- `runtime-status` 0.2: 실행 환경, 대상 등록·보류·인스턴스, 정책 프로필·로딩 검증·승인·유효성 항목·이력, 엔진 연결, 동시성·연결 수, 인증 상태, 수집원 상세(실시간 수집기·보존 정리·지표·자산 무결성·Cilium), 기록 저장, 재검증·관측 집계, 구성·대상 비교 표
- `runtime-event` 0.2: `observation`(Hubble 출발·도착·판정, Tetragon 실행 파일·경로·귀속·규칙·action·단계·exec 판정)
- `revalidation-request` 0.2: `opened_at`, `current_evidence_present`
- 신규 `runtime-lab-report` 0.1, `runtime-request-detail` 0.1
- 쓰는 곳: platform만. 예시는 모두 EXAMPLE이며 `tools/check_contracts.py` 통과

