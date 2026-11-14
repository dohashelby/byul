# 실행 결합값을 정책에서 분리 — 설계안 (2026-11-14, 미승인)

상태: **승인 대기**. 이 문서는 설계안과 영향 목록이다. 팩 판본 상향(@35)과 정책 키 제거는 판정 계약 변경이라 `DECISIONS.md` 열린 질문 RB-Q1~Q5의 사용자 결정 전에는 구현하지 않는다.

## 1. 문제

PACK-BASELINE@34의 69개 점검 중 44개가 실행마다 바뀌는 기술 값(ENV 해시, target·prepared·manifest 해시, 유효 기간)을 `policy_params`(required)로 요구한다. 1단계 대상 키만 228개다. 실행기는 이 값을 `env.ref().sha256`, 실행기 설정 파일(target·prepared·manifest)의 정규 JSON 해시, 관측 시각과 대조한다.

그래서 기업은 엔진 판본을 올릴 때마다 Pod를 다시 띄우고 정책 문서에 새 해시를 다시 넣어야 한다. 지금은 개발 도구(`tools/gpu_a_run_inputs.py`, `tools/mvp_a_input_materialize.py`, `tools/mvp_sup08_inputs.py` 등)로만 반자동 결합한다. 웹 입력(`platform/` → `tools/import_byul_inputs.py`)은 이 값을 만들 수 없다.

이 값들은 기업의 결정이 아니다. ENV 해시는 PLAN이 이미 정확한 Ref로 고정한다(INV-16). target·prepared 해시는 같은 실행의 설정 파일에서 계산된다. 정책에 넣는 것은 같은 값을 사람이 한 번 더 옮겨 적는 일이다.

## 2. 키 분류 (PACK-BASELINE@34)

| 분류 | 정의 | 키(접두어는 점검별) | 점검 | 이번 제안 |
|---|---|---|---|---|
| **A 기업 결정값** | 허용 기준·한도·마감·허용 목록·모드 | `*_budget_*`, `*_max_*`, `*_deadline_*`, `*_allowed_*`, `approved_*`, `*_mode`, `*_required_*`, `selected_model_grants`, `stored_retention` 등 | 전 점검 | 유지 |
| **A′ 기업 정책 문서 참조** | 기업이 승인한 정책 문서·소유자의 해시·참조 | `forwarded_identity_policy_sha256`, `stored_identity_policy_sha256`, `retry_policy_sha256`, `copy_policy_sha256`, `observation_event_policy_sha256`, `cache_identity_policy_ref`, `profiler_control_owner_ref` | REQ-04·06·15, LIF-12·14, PLT-12, OBS-01 | 유지 |
| **B1 실행 결합값 (guard가 직접 계산)** | 같은 실행의 ENV·실행기 설정 파일·실행 창에서 계산되는 값 | `*_environment_sha256`, `*_target_sha256`, `*_prepared_sha256`, `sensor_manifest_sha256`, `*_validity_start`·`*_validity_end` (REQ-02의 `route_native_*`, OBS-02의 `identity_*`·`profiler_*` 포함) | LIF-01·02·03·04·07·08·09·10·11·12·13·14, OBS-01·02·06, PLT-03·05·07·08·10·11·12, REQ-02·04·05·06·12·14~21, SUP-05·06·07·09·10·11·12·15·16 (44개, 키 228개) | **1단계: 정책에서 빼고 실행 결합으로 옮김** |
| **B2 실행 준비물 결합값** | 실행 전에 준비된 파일·세션의 해시·식별자. guard가 계산할 수 있지만 파일 안에 기업 결정이 섞였는지 점검별 확인이 필요 | `*_session`, `record_inventory_session`, `collection_session`, `identity_record_session`; `init_scenario_sha256`, `recovery_selection_sha256`, `recovery_scenario_sha256`, `retry_scope_sha256`, `copy_inventory_sha256`, `record_subject_plan_sha256`, `route_file_sha256`, `transition_inputs_sha256`; LIF-05·06·16 `transition_*_at`·`transition_*_generation`; PLT-07 `subject_container_id`·`subject_uid`·`subject_net_namespace_inode`·`forbidden_receiver_*`; PLT-08 `api_container_id`·`api_network_id`·`api_endpoint_id`·`api_ip`·`api_port` | LIF-05·06·07·09·11·12·16, OBS-01·02·06·11, PLT-07·08·12, REQ-02·04·06 | 2단계(점검별 확인 뒤 별도 승인) |
| **C 생산자·수집기 소스 신원** | 기록을 만드는 코드의 SHA. 판본별로 고정이고 실행마다 바뀌지 않음 | `*_record_source_sha256`, `observation_expected_source_sha256`, `record_inventory_source_sha256`, `forwarded_gateway_source_sha256`·`forwarded_engine_source_sha256`, `stored_*_source_sha256` | LIF-01·02·12, OBS-01·02·06, PLT-03·05·12, REQ-04·06, SUP-05~16 | 후속: adapter·생산자 등록표 데이터로 이동 제안(별도) |
| **D 환경 시험 신원** | 환경에 준비된 시험용 주체 | `allowed_principal_sha256`·`denied_principal_sha256`, `observation_allowed_actor_sha256`·`observation_denied_actor_sha256` | REQ-05, OBS-01 | 후속: ENV 쪽 이동 검토(별도) |
| **E 외부 사건 신고값** | 외부 발급자가 낸 사건 | PLT-10 `revocation_issuer_id`·`event_id`·`event_at`·`deny_after`, PLT-11 `session_issuer_id`·`event_id`·`event_at`·`open_before`·`deny_after` | PLT-10·11 | 유지(발급자 기록 수집은 후속) |

B1만 1단계로 묶은 이유: 키 이름과 계산식이 실행기 전체에서 같고(`env.ref().sha256`, 설정 파일 정규 JSON 해시, 시각 범위), 판정기·core·보고서가 이 키를 읽지 않는다(확인: `judges/`·`core/`·`report/`에 해당 키 참조 없음). B2는 파일 내용(예: `copy_inventory`, `retry_scope`)에 기업 결정이 섞였을 수 있어 점검별 확인이 먼저다.

## 3. 설계: `guard-run-binding/1`

선례 `runtime/sensor_binding.py`의 `guard-sensor-run-binding/2`(센서 4개 키, `migrate_pack`)를 일반화한다.

### 3.1 객체

```yaml
schema: guard-run-binding/1
check_id: REQ-14
side: baseline | candidate | single
attempt_id: <run attempt id>
environment_sha256: <env.ref().sha256>          # PLAN이 고정한 ENV와 같아야 함
targets:   {<역할>: <sha256>}                    # 예: normal, challenge, native — 실행기 설정의 target 파일 정규 JSON 해시
prepared:  {<역할>: <sha256>}                    # 같은 방식
manifest_sha256: <sha256>|null                  # engine_sensor만
validity: {start: RFC3339, end: RFC3339, basis: run_window | run_window∩policy}
issued_at: RFC3339
```

역할 이름은 실행기가 선언한다(`binding_roles()`), 예: supply_boot은 `normal`·`challenge`, REQ-02는 `route`·`route_native`, OBS-02는 `identity`·`profiler`. 지금의 접두어별 키와 1:1로 대응한다.

### 3.2 누가 계산하나 — `run` 단계

- `select`는 바꾸지 않는다. 1단계 키가 팩에서 빠지므로 `NO_POLICY_VALUE` gap이 생기지 않는다(선례 시험 `test_new_binding_pack_selection_has_no_policy_gap_for_technical_hashes`와 같음).
- `guard run`/`guard update`의 합성 지점(`application.py`)이 쪽(side)·점검마다 실행 직전에 결합을 만든다. 입력은 그 실행의 ENV 객체와 실행기 설정에 적힌 target·prepared·manifest 파일이다. 시각은 실행 시작 시각이다.
- 유효 기간: `start = issued_at`, `end = issued_at + 실행기 최대 실행 시간`(지금 실행기별 상한, 예: engine_sensor 300/900초). POL에 `valid_from`·`valid_until`이 있으면 그 범위와 교집합을 쓴다(`basis: run_window∩policy`). 교집합이 비면 실행하지 않고 `NOT_RUN`(사유 `RUN_BINDING_OUTSIDE_POLICY_VALIDITY`).
- 계산 실패(파일 없음·JSON 아님·해시 불가)는 `NOT_RUN`(사유 `RUN_BINDING_UNAVAILABLE`)이고 RES는 UNKNOWN이다(INV-10·15).

### 3.3 어디에 저장하나 — EVD

- 결합 문서를 증거 파일 `run-binding.json`(내용 주소)으로 저장한다.
- `Execution`에 선택 필드 `run_binding: {schema, sha256}`를 더한다. 비어 있으면 직렬화에서 뺀다(기존 EVD 해시 유지, `operator`·`attempt_id`와 같은 방식).
- **`executor.config_sha256`에는 넣지 않는다.** 지금 센서 /2는 `draft.config['run_binding']`에 넣어 설정 해시가 실행마다 바뀌고, 그러면 INV-19 이월 조건(실행기 설정 해시 같음)을 늘 어긴다. 새 설계는 결합을 설정에서 분리해 이 문제도 없앤다.
- PLAN은 바꾸지 않는다. PLAN은 이미 ENV·POL·팩을 정확한 Ref로 고정한다.

### 3.4 실행기는 어떻게 대조하나

- 실행기는 정책 값 대신 `ctx.run_binding`을 받는다. 대조식은 지금과 같다: `binding.environment_sha256 == env.ref().sha256`, `binding.targets[r] == sha256(canonical_json(target_r))` 등. 차이는 기대값의 출처뿐이다.
- 대조는 실행기가 파일을 **실제로 읽은 시점**에 다시 한다. 결합 계산 뒤 파일이 바뀌면(TOCTOU) 지금과 같은 `*_BINDING_MISMATCH`로 ABORTED·UNKNOWN이다.
- 관측 시각 창 검사(`policy_window_verified`, `start <= 관측 <= end`)는 `binding.validity`로 한다. 창 밖 관측은 지금과 같이 미검증으로 남는다.
- `expected_target_sha256`(운영자가 특정 target을 고정하는 실행기 인자)은 유지한다. 기업이 특정 대상을 미리 고정하고 싶으면 이것을 쓴다.

### 3.5 재판정·재생

- 판정기(judge)는 결합 키를 읽지 않는다(확인함). 재판정은 EVD만 읽으므로 바뀌지 않는다(INV-14).
- 재생 검증(오프라인)은 `run-binding.json`을 읽어 `environment_sha256 == EVD.environment.sha256`, `check_id`·`side`·`attempt_id` 일치, `sha256(run-binding.json) == execution.run_binding.sha256`을 확인한다. 불일치는 그 EVD를 재생 불가로 표시한다(판정을 PASS로 올리지 않음).

### 3.6 판정 의미가 유지되는가

| 경우 | 지금(@34) | 바꾼 뒤(@35) |
|---|---|---|
| 정책 키 없음 | select gap `NO_POLICY_VALUE` → NOT_RUN → UNKNOWN | 해당 없음(키가 없음). 결합 계산 실패 시 NOT_RUN → UNKNOWN |
| ENV·target·prepared 불일치 | 실행기 `*_BINDING_MISMATCH` → ABORTED → UNKNOWN | 같음(실행 중 파일 교체 시) |
| 관측이 유효 기간 밖 | 창 미검증 → UNKNOWN 쪽 | 같음(창 = 실행 창 ∩ POL 유효 기간) |
| 정상 결합 | 실행·판정 | 같음 |

잃는 것과 대체: "기업이 정책 문서로 이 해시를 승인했다"는 기록이 사라진다. 대신 ENV(기업이 입력·승인하는 환경 객체)가 PLAN에 Ref로 고정되고, 결합은 그 ENV와 같은 실행의 파일에서 계산된다. 기업 승인 범위는 POL의 `valid_from`·`valid_until`이 맡는다. 불변식 INV-01·04·10·11·14·15·16은 바뀌지 않는다. INV-19는 오히려 지켜지기 쉬워진다(3.3).

## 4. 팩·정책 호환

- **새 팩 판본 @35**: `migrate_pack`을 일반화해 B1 키를 `policy_params`에서 빼고, 해당 점검의 `revision`을 올리고, `collection_profile`에 접미어 `-run-binding-v1`을 붙인다. 판정 규칙(`judge`)·`required_observations`는 바꾸지 않는다. 팩은 DRAFT로 새로 만들고 @34는 그대로 둔다(append-only).
- **실행기는 두 팩을 모두 받는다**: 접미어 없는 프로필(@34)은 지금처럼 정책 값을 대조한다. 접미어 있는 프로필(@35)은 정책에 B1 키가 있으면 `RUN_BINDING_BELONGS_TO_EXECUTION`으로 거부한다(섞어 쓰지 않음, 센서 /2와 같음).
- **기존 정책 문서**: @34 정책은 @34 팩과 그대로 재생된다. @35로 옮길 때 도구(`tools/migrate_policy_run_binding.py`, 신규)가 B1 키만 빼고 나머지 값과 문서 해시 출처를 새 POL revision으로 만든다. 원 POL은 남는다.
- **과거 결과**: @34로 만든 EVD·RES·RPT는 그대로 재생·재판정된다. @34 결과를 @35 결과로 옮기지 않는다. 이월은 INV-19대로 CheckDef revision이 다르면 불가다 — @35 첫 실행은 전체 시험이다.
- **센서 /2와의 관계**: `guard-sensor-run-binding/2`는 운영자가 결합 문서를 손으로 넘기는 방식이다. `/1` 일반 결합이 들어오면 센서 /2 접미어 팩은 과거 재생용으로만 남긴다(새로 만들지 않음).
- **웹 입력**: `import_byul_inputs.py`는 B1 키를 만들지 않으므로 바꿀 것이 없다. 플랫폼 정책 양식에서 B1 칸이 사라진다(platform 쪽 안내 필요).
- **개발 도구**: `gpu_a_run_inputs.py`, `mvp_a_input_materialize.py`, `b03_input_materialize.py`, `b_lif07_readiness.py`, `b12_tp2_verify.py`, `dev1_k3d_cpu_lab.py`, `engine_callback_cpu_smoke.py`는 @34용으로 유지하고 @35 경로에서는 결합 계산을 쓰지 않는다.

## 5. 1단계 구현 범위 (승인 시)

| 영역 | 변경 |
|---|---|
| `runtime/run_binding.py` (신규) | 결합 계산·검증·`migrate_pack` 일반화. `sensor_binding.py`는 재생용 유지 |
| `models/objects.py` | `Execution.run_binding` 선택 필드(빈 값 직렬화 생략) |
| `core/run.py` | 결합을 실행기에 넘기고 EVD에 기록, config 해시에서 제외 |
| `application.py` | 쪽·점검별 결합 계산 |
| `executors/` 34개, `runtime/` 3개 | 기대값 출처를 `policy_values` → `ctx.run_binding`으로(대조식 그대로). 목록은 `grep -lE "_environment_sha256\|_validity_start" src/guard/executors src/guard/runtime` |
| `packs/PACK-BASELINE` @35 | 44개 점검 revision+1, B1 키 228개 제거, 프로필 접미어 |
| `tools/migrate_policy_run_binding.py` (신규) | 정책 새 revision 생성 |
| 시험 | 결합 계산·TOCTOU 불일치·창 밖 관측·@34 재생·@35 혼용 거부·config 해시 불변, 실행기별 직접 시험 |
| 소스 목록 | `tools/verify_req10_consumption.py --write`, `tools/reviewed_manifest_receipts.json` 행(결정 5) |

`core/`·`models/` 변경과 팩 판본 상향이 포함되므로 CLAUDE.md 9절에 따라 승인 뒤에만 한다.
