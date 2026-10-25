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
| `environment` | platform → update, runtime | 기업 서빙 환경 | guard ENV 고유 필드 + runtime `runtime-target/1.0` + intent의 배치 정보 |
| `policy` | platform → update, runtime | 기업 정책 한 판본 | `verification` = guard POL, `runtime.gateway` = `runtime-mvp/1.1·1.2`, `runtime.enforcement` = `runtime-enforcement-intent/1.x` 원문 |
| `policy-bundle` | platform → runtime | 승인된 정책을 runtime 내부 형식 파일로 묶은 것 | §5.4 `approved-policy.json` |
| `run-context` | update → runtime | 격리 시험 실행 문맥 | §5.4 `run-context.json` |
| `verification-result` | update → platform | 화면용 검증 결과 | guard RPT의 투영 |
| `runtime-event` | runtime → platform | 사건 한 줄(JSONL) | `runtime-mvp-record/1.0`, `runtime-observation/1.0` 등을 `native`에 감쌈 |
| `runtime-status` | runtime → platform | 상태 스냅샷 | `/api/status` |
| `revalidation-request` | runtime → platform → update | 재검증 요청 | `runtime-revalidation-request/1.0` |
| `evidence-index` | update, runtime → platform | 증거 파일 목록 | §5.4 `evidence/index.json` |

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

Hubble `DROPPED`는 출발·목적지·정책 귀속과 수신 대조 전에는 거부 확인이 아니다(runtime 문서 §7).

### runtime `data_origin` → 등급

| runtime 값 | 등급 |
| --- | --- |
| `real`, `external_backend`, `kubernetes_api`, `local_file` | `MEASURED` |
| `mock`, `synthetic`, `actual_test`(모의 엔진) | `EXAMPLE` |
| 사람이 입력한 정책·예외·승인 | `DECLARED` |

모의 엔진에서 실제로 돌린 시험(`actual_test`)도 실제 엔진 결과가 아니므로 `EXAMPLE`로 낮춘다.

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
5. **verification-result 생산 위치:** update가 직접 내보낼지, platform이 RPT를 읽어 변환할지.
6. **번들 전달 경로:** 같은 호스트의 관리 디렉터리(§5.4)로 시작한다. 원격 배포 채널은 미정.
