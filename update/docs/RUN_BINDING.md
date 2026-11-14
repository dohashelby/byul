# 실행 결합값을 정책에서 분리 (RB, 2026-11-14 승인·구현)

상태: **B1 구현됨**(사용자 승인 2026-11-14, `DECISIONS.md` RB). **B2는 승인 요청**(7절, RB2). PACK-BASELINE@35부터 아래 B1 키는 정책 값이 아니고 `guard run`이 계산한다. 구현은 `src/guard/executors/run_binding.py`, 시험은 `tests/test_run_binding.py`. 설계안과 달라진 점은 6절에 모았다.

## 1. 문제

PACK-BASELINE@34의 69개 점검 중 44개가 실행마다 바뀌는 기술 값(ENV 해시, target·prepared·manifest 해시, 유효 기간)을 `policy_params`(required)로 요구한다. 1단계 대상 키만 228개다. 실행기는 이 값을 `env.ref().sha256`, 실행기 설정 파일(target·prepared·manifest)의 정규 JSON 해시, 관측 시각과 대조한다.

그래서 기업은 엔진 판본을 올릴 때마다 Pod를 다시 띄우고 정책 문서에 새 해시를 다시 넣어야 한다. 지금은 개발 도구(`tools/gpu_a_run_inputs.py`, `tools/mvp_a_input_materialize.py`, `tools/mvp_sup08_inputs.py` 등)로만 반자동 결합한다. 웹 입력(`platform/` → `tools/import_byul_inputs.py`)은 이 값을 만들 수 없다.

이 값들은 기업의 결정이 아니다. ENV 해시는 PLAN이 이미 정확한 Ref로 고정한다(INV-16). target·prepared 해시는 같은 실행의 설정 파일에서 계산된다. 정책에 넣는 것은 같은 값을 사람이 한 번 더 옮겨 적는 일이다.

## 2. 키 분류 (PACK-BASELINE@34)

| 분류 | 정의 | 키(접두어는 점검별) | 점검 | 이번 제안 |
|---|---|---|---|---|
| **A 기업 결정값** | 허용 기준·한도·마감·허용 목록·모드 | `*_budget_*`, `*_max_*`, `*_deadline_*`, `*_allowed_*`, `approved_*`, `*_mode`, `*_required_*`, `selected_model_grants`, `stored_retention` 등 | 전 점검 | 유지 |
| **A′ 기업 정책 문서 참조** | 기업이 승인한 정책 문서·소유자의 해시·참조 | `forwarded_identity_policy_sha256`, `stored_identity_policy_sha256`, `retry_policy_sha256`, `copy_policy_sha256`, `observation_event_policy_sha256`, `cache_identity_policy_ref`, `profiler_control_owner_ref` | REQ-04·06·15, LIF-12·14, PLT-12, OBS-01 | 유지 |
| **B1 실행 결합값 (guard가 직접 계산)** | 같은 실행의 ENV·실행기 설정 파일·실행 창에서 계산되는 값 | `*_environment_sha256`, `*_target_sha256`, `*_prepared_sha256`, `sensor_manifest_sha256`, `*_validity_start`·`*_validity_end` (REQ-02의 `route_native_*`, OBS-02의 `identity_*`·`profiler_*` 포함). 단 PLT-10·11·LIF-11의 유효 기간은 정책에 남김(6절) | LIF-01·02·03·04·07·08·09·10·11·12·13·14, OBS-01·02·06, PLT-03·05·07·08·10·11·12, REQ-02·04·05·06·12·14~21, SUP-05·06·07·09·10·11·12·15·16 (44개, 키 222개) | **구현: 정책에서 빼고 실행 결합으로 옮김** |
| **B2 실행 준비물 결합값** | 실행 전에 준비된 파일·세션의 해시·식별자. guard가 계산할 수 있지만 파일 안에 기업 결정이 섞였는지 점검별 확인이 필요 | `*_session`, `record_inventory_session`, `collection_session`, `identity_record_session`; `init_scenario_sha256`, `recovery_selection_sha256`, `recovery_scenario_sha256`, `retry_scope_sha256`, `copy_inventory_sha256`, `record_subject_plan_sha256`, `route_file_sha256`, `transition_inputs_sha256`; LIF-05·06·16 `transition_*_at`·`transition_*_generation`; PLT-07 `subject_container_id`·`subject_uid`·`subject_net_namespace_inode`·`forbidden_receiver_*`; PLT-08 `api_container_id`·`api_network_id`·`api_endpoint_id`·`api_ip`·`api_port` | LIF-05·06·07·09·11·12·16, OBS-01·02·06·11, PLT-07·08·12, REQ-02·04·06 | 2단계 승인 요청(7절, RB2-Q1~Q7) |
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

## 6. 구현 결과와 설계안과 달라진 점 (2026-11-14)

- **팩 선택 규칙**: `collection_profile` 접미어를 붙이지 않았다. 점검이 @34의 수집 프로필을 쓰고 정의에 결합 키가 없으면(@35) 결합을 쓰고, 정의에 키가 있으면(@34) 예전처럼 정책 값을 쓴다. 다른 프로필(소스 CPU, 센서 /2 접미어 팩)은 영향이 없다(`run_binding.keys_for`). 프로필 문자열을 바꾸면 실행기 37개 파일의 지원 판별을 모두 고쳐야 해서, 실행기 코드는 바꾸지 않는 쪽을 택했다. 실행기는 같은 키 이름으로 값을 받고 대조식도 그대로다.
- **유효 기간을 정책에 남긴 점검**: PLT-10·PLT-11·LIF-11. 이 점검의 정책은 발급자 사건 시각·마감·재접속 시각처럼 유효 창 안에 있어야 하는 시각을 함께 선언한다. 창을 guard가 정하면 그 시각들과 맞출 수 없다. ENV·target·prepared 해시는 이 점검들도 옮겼다. 그래서 이동한 키는 228개가 아니라 222개다.
- **유효 창 길이**: 실행기 정책 계약이 받는 최대치로 정했다. supply_boot 3600초, 나머지 600초다. POL `valid_from`·`valid_until`이 있으면 교집합을 쓰고, 비면 `NOT_RUN`(`RUN_BINDING_OUTSIDE_POLICY_VALIDITY`)이다. 창은 점검마다 실행 직전에 새로 연다.
- **저장**: 별도 증거 파일 없이 결합 문서 전체를 `Execution.run_binding`에 넣었다. EVD 해시에 봉인되고, 비어 있으면 직렬화에서 빠진다. 재생 대조는 `run_binding.verify(document, evidence)`로 한다.
- **설정 해시**: core는 결합을 `executor.config_sha256`에 넣지 않는다. 다만 engine_sensor처럼 실행기가 원래 정책 값을 자기 `draft.config`에 적는 경우는 @34와 똑같이 그 값이 들어간다. 그래서 "INV-19 이월이 쉬워진다"는 설계안의 주장은 철회한다. 이월 조건은 @34와 같다.
- **파일 해시 계산**: 실행기가 쓰는 로더를 그대로 쓴다. 엄격 JSON, `collectors.lifecycle.load_prepared`, `collectors.engine_callbacks.load_prepared` 중 하나로 읽은 뒤 정규 JSON SHA-256을 낸다. 키별 파일 위치는 `_FILE_OVERRIDES`·`_CHECK_FILE_OVERRIDES`에 둔다(supply 역할별, REQ-02 native, OBS-02 identity/profiler). 실제 파일로 끝까지 실행한 시험이 있다. engine_sensor(REQ-14·16·17·18), LIF-10·REQ-12 공통 입력 소비, PLT-10·11·12, OBS-01, REQ-04·05·06 제품 왕복이 이에 해당한다.
- **사전 점검**: `prepare`/준비 확인의 `validate_policy`도 같은 결합을 계산해 대조한다(`application.py`).
- **팩 생성**: 팩은 `tools/import_checklist.py`의 산출물이라 `tools/overrides/*.yaml`에서 키를 빼고, 변환기가 커밋된 @35와 바이트 단위로 같음을 확인했다(`test_committed_pack_matches_importer`). 검토 표기는 @34와 같은 방식이다(B 검토 면제, 사용자 결정 5).
- **정책 이전**: `tools/migrate_policy_run_binding.py`. 결합 키만 빼고 팩 참조를 옮긴다. 문서가 바뀌므로 DRAFT로 두고 확인은 사람이 다시 한다. 봉인된 POL 객체는 고치지 않는다.
- **저장소 입력 갱신**: `examples/policy.yaml`과 `docs/gpu-a/policy-a-draft.yaml`을 @35로 옮겼다(초안의 `sensor_*` 자리표시 삭제). `docs/dev-1/ma7-session-input-plan.json`의 팩 참조와 정책 요구 키도 옮겼다. `tools/gpu_a_run_inputs.py`는 정책에 결합 키를 쓰지 않는다. `tools/b12_tp2_inputs.py`의 로컬 선언 파일은 같은 해시를 사전 대조하므로 결합 키 칸을 유지한다(POL 아님). `tools/mvp_sup08_inputs.py`의 팩 판본 관문은 35로 올렸다(SUP-08 정의 rev 5는 그대로).
- **과거 결과**: @34로 만든 PLAN·EVD·RES는 저장소의 팩 보존본으로 그대로 재판정된다. 상태표에서는 팩·점검 판본과 정책 키 열만 바뀌었고, 과거 GPU 결과의 현재 정의 판정은 바뀌지 않았다.

## 7. B2 실행 준비물 결합값 — 점검별 확인과 승인안 (2026-11-14, 미승인)

상태: **설계·승인 요청**(`DECISIONS.md` 열린 질문 RB2-Q1~Q7). 구현·팩 판본 상향은 하지 않았다. 아래 확인은 PACK-BASELINE@35 정의와 실행기 소스를 읽어 한 것이다.

### 7.1 대상과 분류 기준

@35에 남은 B2 키는 17개 점검 57개다. 2절 표의 키에 계약 상수 2개(`subject_scope`, `api_hop_scope`)를 더했다.

| 분류 | 뜻 | 정책에서 빼는 방법 |
|---|---|---|
| **G guard 계산 가능** | 같은 실행의 설정 파일이나 실행기 계약에서 정해진다. 기업 결정이 없다 | 1단계와 같은 결합 계산. 실행기 설정에 파일·값 칸이 없으면 칸을 더한다 |
| **E 기업 결정 포함(분리 필요)** | 해시 대상 파일 안에 허용 범위·한도·조치·승인 주체 같은 기업 결정이 섞여 있다. 지금은 정책의 해시가 그 결정을 기업이 승인했다는 유일한 고정점이다 | 파일을 기업 결정(정책 키나 A′ 정책 문서 참조)과 실행 부분(결합)으로 나눈 뒤에만 옮긴다 |
| **P 실행 준비·실행 중에 생기는 값** | 생산자 설치, 엔진 기동, 운영 절차가 정한다. 기업 결정은 아니지만 guard가 혼자 계산할 수 없다 | 실행기 설정(실행 준비물)으로 옮기고 결합이 그 값을 읽는다. 실행 중 관측한 값을 그대로 기대값으로 쓰면 대조가 자기 일치가 되므로 쓰지 않는다 |

### 7.2 키별 확인

| 점검 | 키 | 실행기 대조(확인한 코드) | 분류 | 판단 근거 |
|---|---|---|---|---|
| LIF-05·06·16 | `transition_inputs_sha256` | `lifecycle_transition.load_generations`가 설정 `generations` 행마다 `{id, environment_ref, target_sha256, prepared_sha256}` 목록을 만들고 그 정규 JSON 해시를 대조 | **G** | 같은 설정 파일들에서 계산된다. 목록 안에 기업 결정이 없다 |
| LIF-05·06·16 | `transition_{before,during,after,recovered}_generation` (12) | 단계별 세대 id가 설정 `generations`의 id에 있어야 하고, `before` 세대 ENV가 실행 ENV와 같아야 함 | **G**(설정 이동) | 시험 구성(어느 세대를 어느 단계에 쓰나)이다. 기업 허용 기준이 아니다 |
| LIF-05·06·16 | `transition_{before,during,after,recovered}_at` (12) | 네 표본 시각이 오름차순이고 전체 240초 이내. 실행기는 이 절대 시각에 표본을 뜬다 | **P** | 운영자가 재배포·전환 동작에 맞춰 고르는 실행 일정이다. guard가 혼자 정할 수 없다 |
| LIF-07·09 | `init_scenario_sha256` | 형식(64 hex)만 검사하고 관측 `declared_scenario_sha256`에 그대로 복사한다. 실행기가 읽는 파일이 없다. 판정기는 이 값을 읽지 않는다 | **G**(설정 파일 추가 시) | 개발 도구의 `runtime_refs.scenario`(시나리오 순서·범위 한계, `tools/b12_tp2_inputs.py`) 해시다. 기업 결정이 없다 |
| LIF-11 | `recovery_scenario_sha256` | 위와 같음 | **G**(설정 파일 추가 시) | 위와 같음 |
| LIF-11 | `recovery_selection_sha256` | 설정 `selection`의 정규 JSON 해시 대조. 내용은 소유 프로세스(pid·rank)와 자원(fd·device·inode·kind)이고 행마다 `require_absent`가 있다 | **E** | pid·fd·inode는 엔진 기동 뒤 생기는 실행 값이다. 반면 `require_absent`는 "복구 뒤 반드시 없어야 할 자원"이라는 판정 기준이고, `resource_recovery_contract`가 이 값으로 위반 확정 여부를 정한다. 정책의 `recovery_required_resource_kinds`·`recovery_expected_ranks`는 종류·rank만 묶고 행별 `require_absent`는 묶지 않는다 |
| LIF-12 | `retry_scope_sha256` | 설정 `retry_scope` 파일 **bytes** SHA-256 대조(정규 JSON 아님). 파일 안 `policy_sha256`은 정책 `retry_policy_sha256`(A′)과 대조 | **E** | `limits`(`max_attempts`·`max_overlap_ms`·`max_remaining_ms`·`max_return_ms`·`max_cost`)는 기업 한도다. `subjects`(pid·시작 tick·actor)는 실행 값이다. 파일 안 `policy_sha256`은 참조일 뿐이라 한도 내용을 고정하지 않는다 |
| PLT-12 | `copy_inventory_sha256` | 설정 `copy_inventory` 파일 **bytes** SHA-256 대조. 파일 안 `policy_sha256`은 정책 `copy_policy_sha256`과 대조 | **E** | 복사본마다 `required_action`(revoke/restrict)·`deadline_at`·`retention_exception`이 있다. 모두 기업 조치 결정이다 |
| OBS-06 | `record_subject_plan_sha256` | 설정 `subjects`의 정규 JSON 해시 대조. 실행기는 `expected_policy_ref=subjects["policy_ref"]`로 **계획 파일이 스스로 낸 정책 참조**를 쓴다 | **E** | `authorized_reader`·`unauthorized_reader` 지정(누가 기록을 읽어도 되나)과 `policy_ref`는 기업 접근 정책이다. 지금은 정책의 이 해시만이 그 지정을 고정한다. 그대로 결합으로 옮기면 정책 참조가 자기 선언이 된다 |
| LIF-12 | `retry_session` | Pod 안 `/guard-observation/data/<session>/required-events.json`을 읽고, 기록의 `session`이 같아야 함 | **P** | 아래 7.3 |
| OBS-01 | `observation_session` | Gateway 기록 읽기 경로·기록의 `session` 대조 | **P** | 7.3 |
| OBS-02 | `identity_record_session` | `/guard-observation/data/<session>/required-events.json` | **P** | 7.3 |
| OBS-06 | `record_inventory_session` | `/guard-record-access/data/<session>/copy-inventory.json` | **P** | 7.3 |
| OBS-11 | `collection_session` | `/guard-collection/data/<session>/collection-status.json`, 기록 `session` 대조 | **P** | 7.3 |
| PLT-12 | `copy_record_session` | `/guard-observation/data/<session>/required-events.json` | **P** | 7.3 |
| REQ-02 | `route_session` | `/guard-route/data/<session>/route-observations.json` | **P** | 7.3 |
| REQ-04 | `forwarded_session` | `/guard-identity/data/<session>/forwarded-identity.json` | **P** | 7.3 |
| REQ-06 | `stored_session` | 저장 응답 scope(`session`·`origins`·`operations`·`roles`)의 한 칸 | **P** | 7.3. `stored_origins`·`stored_operations`는 A로 남는다 |
| REQ-02 | `route_file_sha256` | 실행 중 Pod에서 읽은 route 기록 파일의 **bytes** 해시(`provenance.capture_sha256`)가 이 값과 같아야 함 | **P** | 실행 중 생산자가 쓰는 파일이라 실행 전에 알 수 없다. guard가 읽은 값으로 채우면 대조가 자기 일치가 된다 |
| PLT-07 | `subject_container_id`·`subject_uid`·`subject_net_namespace_inode`·`forbidden_receiver_container_id`·`forbidden_receiver_ip`·`forbidden_receiver_port` | 실행 중 Docker로 확인한 송신 컨테이너·uid·net namespace와 guard 소유 수신기 주소가 이 값과 같아야 함 | **P** | 실행 신원이다. 금지 대상의 기업 결정은 `forbidden_protocol`·`forbidden_operation`(A)이 맡고, 수신기는 guard가 띄우는 대역이다 |
| PLT-08 | `api_container_id`·`api_network_id`·`api_endpoint_id`·`api_ip`·`api_port` | 실행 중 찾은 소유 API 컨테이너의 네트워크 결합이 이 값과 같아야 함 | **P** | 실행 신원이다. 기업 결정은 `plaintext_forbidden`(A) |
| PLT-07 | `subject_scope` | 값이 `all_processes_in_container_uid_netns` 하나만 허용 | **G** | 실행기 계약 상수 |
| PLT-08 | `api_hop_scope` | 값이 `guard_probe_to_owned_direct_api` 하나만 허용 | **G** | 실행기 계약 상수 |

합계: G 20개, E 4개, P 33개(세션 9, 표본 시각 12, route 파일 1, PLT-07·08 실행 신원 11).

### 7.3 세션 값은 prepared 매니페스트에서 읽을 수 있나

- lifecycle 준비물(`guard-lifecycle-assets/1`, `collectors.lifecycle.load_prepared`)에는 `session`이 있다. 하지만 B2의 세션 9개는 **이 세션이 아니다.** 모두 기록 생산자(Gateway·필수 사건·식별 전달·route·수집 상태·기록 접근 inventory)의 기록 폴더(`/guard-observation`·`/guard-identity`·`/guard-route`·`/guard-collection`·`/guard-record-access`) 열쇠다. lifecycle 세션은 `/guard-lifecycle/data/<session>`을 연다.
- 해당 실행기 9개 중 prepared를 받는 것은 OBS-02(`identity_prepared`, lifecycle)와 LIF-12(선택 `prepared`)뿐이고, 그 prepared의 세션과 기록 세션이 같아야 한다는 규칙은 코드에 없다. 같다고 가정하고 읽으면 다른 생산자의 폴더를 여는 의미 변경이다.
- 기록 세션은 지금 생산자를 설치할 때 운영자가 정한다(개발 도구 `tools/dev1_k3d_cpu_lab.py --session`). 기록 생산자용 준비물 매니페스트는 아직 없다.
- 세션은 HMAC 키가 아니고 평문 식별자다(기록 파일에도 평문으로 있다). 결합 문서에 값으로 남겨도 지금 POL에 있는 것과 노출 범위가 같다.
- **안**: 실행기 설정에 `record_session`(문자열, `[0-9a-f]{32}`)을 더하고 결합이 그 값을 읽어 같은 정책 키 이름으로 넘긴다. 실행기는 기록 안 `session`과 계속 대조하므로 다른 세션의 기록은 지금처럼 불일치→UNKNOWN이다. 기록 생산자 준비물 매니페스트를 만들면 거기서 읽는 것으로 바꿀 수 있다(후속).

### 7.4 1단계 구현을 넓히는 설계

원칙은 1단계와 같다. 실행기 대조식·판정 규칙·필수 관측은 바꾸지 않고, 기대값의 출처만 정책에서 결합으로 옮긴다.

1. **단계별 키 판별 (`keys_for`)**: 지금 `keys_for`는 정의가 결합 키 중 하나라도 선언하면 전체를 정책 값으로 본다. B2 키를 같은 점검의 `KEYS_BY_CHECK`에 그냥 더하면, B2 키를 아직 선언하는 @35 정의에서 B1까지 정책 값으로 되돌아가 @35 재생이 깨진다. 그래서 `KEYS_BY_STAGE = {1: ..., 2: ...}`로 나누고, 정의가 **선언하지 않은** 단계의 키만 결합으로 계산한다. 한 단계 안에서 일부만 선언한 정의는 지금처럼 섞어 쓰지 않고 거부한다(`RUN_BINDING_BELONGS_TO_EXECUTION`).
2. **값 출처 표 (`_VALUE_SOURCES`)**: 지금은 키 접미어로 env·창·파일을 가른다. B2는 출처가 여러 가지라 키별 표로 바꾼다.
   - `json`·`lifecycle`·`callback`: 1단계와 같은 정규 JSON 해시
   - `bytes`: 파일 bytes SHA-256. `retry_scope_sha256`·`copy_inventory_sha256`은 실행기가 bytes 해시를 대조하므로 정규 JSON 해시를 쓰면 늘 불일치가 된다(E 분리 뒤 옮길 때 필요)
   - `config`: 실행기 설정의 문자열·정수 값(세션, 단계별 세대, PLT-07·08 결합 파일의 칸). 형식은 실행기 정책 계약의 형식 검사를 그대로 쓴다
   - `transition_manifest`: `lifecycle_transition`의 세대 목록 생성 부분을 순수 함수로 떼어 실행기와 결합이 같이 쓴다. 코드를 복사하지 않는다
   - `constant`: 실행기 계약 상수(`subject_scope`, `api_hop_scope`)
   - `offset_time`: `transition_*_at = issued_at + 설정 sample_offsets_ms[단계]`. 실행기의 기존 검사(오름차순·240초 이내)를 그대로 거친다
3. **실행기 설정 칸 추가**(판정 무관, 설정 허용 키 집합만 넓힘): `record_session`(세션 9개 실행기), `scenario`(LIF-07·09·11), `phase_generation`·`sample_offsets_ms`(LIF-05·06·16), `egress_binding`(PLT-07), `api_binding`(PLT-08). 새 칸은 @36 정의에서만 받는다. @35 정의에는 새 칸을 주면 거부해 두 출처가 섞이지 않게 한다.
4. **결합 문서**: B1 문서는 `files`(해시)만 있다. B2는 해시가 아닌 값(세션·세대·시각)이 있어 `values` 칸이 필요하다. B2 키가 있는 문서만 `guard-run-binding/2`로 쓰고, `verify`는 /1·/2를 모두 받는다. B1만 쓰는 점검의 문서는 /1 그대로라 기존 EVD 해시가 바뀌지 않는다.
5. **창**: B2는 유효 창 계산을 바꾸지 않는다. LIF-05·06·16은 창 키가 없고, 표본 시각만 실행 시작 기준으로 계산한다.
6. **PLT-07·08**: 실행 신원을 실행 전에 선언하는 결합 파일(`egress_binding`·`api_binding`, 엄격 JSON)을 설정에 두고, 결합이 그 파일 해시와 칸 값을 낸다. 실행기는 지금처럼 실행 중 Docker로 찾은 값과 대조한다. 실행 중 찾은 값을 기대값으로 쓰지 않으므로 대조가 자기 일치가 되지 않는다.

### 7.5 판정 의미 영향

| 경우 | 지금(@35) | 바꾼 뒤(@36 안) |
|---|---|---|
| 세션 값 없음 | select gap `NO_POLICY_VALUE` → NOT_RUN → UNKNOWN | 설정 `record_session` 없음 → 결합 실패 `RUN_BINDING_UNAVAILABLE` → NOT_RUN → UNKNOWN |
| 다른 실행의 기록 세션 | 기록 `session` 불일치 → 실행기 실패 → UNKNOWN | 같음 |
| 세대·준비물 교체 | `TRANSITION_CONFIGURATION_INVALID` | 같음(읽은 시점 재대조) |
| 표본 시각 순서·240초 위반 | 정책 검사 실패 → NOT_RUN | 설정 offset 검사 실패 → NOT_RUN(사유 `RUN_BINDING_UNAVAILABLE`) |
| PLT-07·08 실행 신원 불일치 | `EGRESS_POLICY_BINDING_UNVERIFIED` 등 → UNKNOWN | 같음(기대값이 결합 파일에서 옴) |
| E 키 | 정책 해시 대조 | **이번 안에서는 유지**(7.6) |

- 판정기·core·보고서·models는 B2 키를 읽지 않는다(`grep`으로 `judges/`·`core/`·`report/`·`models/`·`store/` 확인). 재판정은 EVD만 읽으므로 바뀌지 않는다(INV-14).
- INV-01·04·10·11·15·16은 바뀌지 않는다. INV-19 이월은 CheckDef revision이 바뀌므로 @36 첫 실행은 전체 시험이다(1단계와 같다).
- 잃는 것: "기업이 정책 문서로 이 세션·세대·시각·실행 신원을 승인했다"는 기록. 이 값들은 기업 결정이 아니므로 ENV·target과 같은 이유(3.6)로 대체된다. E 키는 이 대체가 성립하지 않아 옮기지 않는다.
- `route_file_sha256`만은 다르다. 결합으로 채울 수 있는 실행 전 값이 없다. 옮기려면 "선언한 파일 = 읽은 파일" 대조를 빼고 읽은 파일 해시를 기록만 하게 된다. 수락 기준이 줄어드는 변경이라 별도 질문(RB2-Q5)으로 둔다.

### 7.6 E 키 분리안 (옮기기 전 선행)

| 키 | 기업 결정 부분 → | 실행 부분 → |
|---|---|---|
| `retry_scope_sha256` (LIF-12) | `limits` → 정책 키(`retry_max_attempts` 등)나 A′ `retry_policy_sha256`가 가리키는 문서 안 | `subjects`·`run_id`·생산자·이미지·모델 결합 → 실행기 설정 파일(결합, bytes 해시) |
| `copy_inventory_sha256` (PLT-12) | 복사본별 `required_action`·`deadline_at`·`retention_exception` → 정책(또는 A′ 문서) | 복사본 위치·수신자·세대 목록 → 설정 파일(결합) |
| `record_subject_plan_sha256` (OBS-06) | `policy_ref` → 새 A′ 키(예: `record_access_policy_ref`). 실행기는 계획 파일 대신 이 정책 값으로 대조 | 시험 주체 `username`·`groups` → D(환경 시험 신원)와 함께 검토 |
| `recovery_selection_sha256` (LIF-11) | 행별 `require_absent` → 정책 규칙(예: `recovery_require_absent_kinds`)으로 바꾸고 실행기가 행 값과 대조 | pid·rank·fd·device·inode → 설정 파일(결합) |

각 분리는 실행기 입력 형식과 정책 키를 바꾸므로 점검별 승인이 따로 필요하다. 이번 안은 E 키를 정책에 남긴다.

### 7.7 팩·정책 호환안

- **새 팩 @36**: `tools/overrides`에서 승인된 B2 키만 빼고 해당 점검 revision을 올린다. 판정 규칙·필수 관측은 그대로다. @35는 보존한다(append-only). 1단계와 같이 수집 프로필 문자열은 바꾸지 않고 정의의 키 선언으로 판별한다(7.4-1).
- **정책 이전**: `tools/migrate_policy_run_binding.py`를 단계 인자(`--stage 2`)로 넓힌다. 키를 빼면서 **빠진 값을 실행기 설정 후보로 출력**한다(세션·세대·시각 offset·PLT-07·08 결합 파일). 이 출력은 운영자가 확인할 DRAFT다. 원 POL은 남는다.
- **과거 결과**: @34·@35 PLAN·EVD·RES는 보존본으로 그대로 재판정된다. @35 결과를 @36으로 옮기지 않는다.
- **개발 도구**: `tools/dev1_k3d_cpu_lab.py`, `tools/b12_tp2_inputs.py`·`b12_tp2_verify.py`(`runtime_refs.scenario`·`selection`), `gpu18_prototype/` 사례가 B2 값을 정책에 쓴다. @36 경로에서는 같은 값을 실행기 설정으로 쓴다. @35용 출력은 유지한다.
- **웹 입력**: `import_byul_inputs.py`와 platform 정책 양식에는 B2 키가 없다(`platform/`·`contracts/` grep에서 B2 키 이름은 나오지 않음. `observation_session`은 runtime 계약의 `observation_session_id` 일부다). 바꿀 것이 없다.

### 7.8 구현 범위 (승인 시)

| 영역 | 변경 |
|---|---|
| `executors/run_binding.py` | 단계별 키 표, `_VALUE_SOURCES`, `bytes`·`config`·`transition_manifest`·`constant`·`offset_time` 출처, 문서 /2와 `verify` 두 판본 수용 |
| `executors/lifecycle_transition.py` | 세대 목록 생성을 순수 함수로 분리(대조식 그대로), 설정 `phase_generation`·`sample_offsets_ms` 수용 |
| 세션 실행기 9개, LIF-07·09·11, PLT-07·08 실행기 | 설정 허용 키에 새 칸 추가(@36 정의일 때만). 대조식·판정 무변경 |
| `packs/PACK-BASELINE` @36, `tools/overrides` | 승인된 키 제거, revision+1 |
| `tools/migrate_policy_run_binding.py` | 단계 2와 설정 후보 출력 |
| 시험 | 단계별 판별(@35 재생에서 B1 유지·B2 정책 값), 세션 불일치·세대 교체·offset 위반·bytes 해시·문서 /1·/2 재생, 실행기별 직접 시험 |
| 소스 목록 | `tools/verify_req10_consumption.py --write`, 검토 행(결정 5) |
