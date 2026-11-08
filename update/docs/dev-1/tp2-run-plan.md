# TP2 실측 계획 (REQ-14·REQ-21, 개발 담당, 2026-11-06)

## 구성

- 사용자 결정(2026-11-06): 2장 구성은 개발 담당이 정한다. 서울에 L4 2장 유형이 없어 **g6.12xlarge**(L4 4장, vCPU 48, 메모리 192 GiB, 콘솔 표시 $5.65823/h, 2a·2c·2d)에서 2장만 쓴다.
- 지금까지와 같은 L4·드라이버 580.178.04·vLLM 0.31.0 이미지(`a4a4c043…`)·모델(opt-125m)이라 단일 L4 결과와 비교할 수 있다.
- 기존 인스턴스 `i-04f510eaa76c2bc89`(2c)를 stopped 상태에서 유형만 바꿔 시작한다. 같은 EBS의 kubeadm 클러스터·이미지·모델을 그대로 쓰고, 끝나면 g6.2xlarge로 되돌린다.
- 할당량: Running On-Demand G and VT instances(L-DB2E81BA) vCPU 8 → 48 요청(2026-11-06 15:50경, 승인 대기). 승인 전에는 시작할 수 없다.

## 순서

1. 할당량 승인 확인. 비용 상한은 사용자 승인(2026-11-08): **A+B 한 세션 EC2 $20**(EBS 별도). A만이면 1.5~2시간·약 $8.5~$11. 범위·조건은 `user-decisions-20261108.md` 1·2절
   (REQ-16 Qwen2-VL-2B-Instruct를 EC2 안에서 내려받아 같은 세션에서 실행, B 분기 입력이 세션 전에 없으면 A 범위만).
2. 콘솔: 인스턴스 유형 변경(g6.2xlarge → g6.12xlarge) → 시작 → SSH 원본 확인.
3. 서버: 커밋 소스 묶음 배치(SHA 대조), 하네스 `harness-tp2.tgz` 배치, `uv sync --frozen`.
4. `BYUL_RUN_DATE=<날짜> python a-tp2.py facts`: GPU 수·`nvidia-smi topo -m`·P2P 읽기·노드 allocatable을 기록한다. device plugin이 4장을 보고하는지 확인한다.
5. `a-tp2.py req14`: TP2 엔진(콜백 센서, `--tensor-parallel-size 2`, GPU 2·shm 4Gi·메모리 24Gi) → `req14-normal` 4사례.
6. `a-tp2.py req21`: 같은 설정 + `restartPolicy: Always` → before → `engine_restart` → after. 실행 상한 900초(엔진 재기동 대기 300초 포함).
7. 증거 반출(SHA 대조) → 소유 namespace 삭제·GPU 0MiB → EC2 중지 → 유형을 g6.2xlarge로 되돌림 → stopped·비용 장부.

## 미리 알려 둘 위험

- REQ-21은 vLLM custom all-reduce 통신기를 관측한다. 이 통신기는 GPU 사이 P2P가 있어야 켜진다. L4는 PCIe 연결이라 P2P가 없으면 통신기가 꺼지고 `tensor_parallel_communicator_inactive`로 UNKNOWN이 된다. 4단계의 P2P 기록으로 원인을 남기며 판정을 바꾸지 않는다.
- REQ-14의 TP 중간 전송 계보는 rank별 실행·출력 rank 대조까지이며, 가중치 실제 소비는 미관측으로 남는다(기존 메모).

## 준비 점검 (CPU)

- 하네스: `byul-dev1-evidence/tp2-prep/harness/` (11-06 하네스 + `a-tp2.py`, 날짜는 실행 때 환경변수).
- 11-05 GPU env·target으로 REQ-14(`req14-normal`)·REQ-21(재기동 사례·lab 선언) 입력 생성과 `guard select` 통과(팩33).
- REQ-21도 LIF-13처럼 실행 안에서 엔진을 띄우므로 실행기·입력 도구의 900초 상한에 넣었다(`LONG_LAB_CHECKS`). 전송의 900초는 소유 lab 콜백 대상에서만 받는다.

<!-- B4-S2-TP2-INPUTS-BEGIN -->
## B4 — B 분기 입력과 A6 인계 계획 (S2, 2026-11-08)

이 절은 사용자 첨부 `BYUL A·B 작업지시서 (1).html`의 A6·B4·J1을 위한 **준비 전용 계획**이다. 위 A 원문은 그대로 보존했다. 코드 대조 기준은 `37c284d91286dbcf2483f8fa654d2e50f32f9d38`이며, CPU fixture·정적 계약·기존 TP1 기록을 새 TP2 실측으로 세지 않는다. 현재 인스턴스 상태·할당량 승인·가격을 조회한 결과가 아니고, 기동 명령이나 실행 승인을 생성하지 않는다. 이 절 작성 중 GPU·SSM·모델 실행·다운로드는 하지 않는다.

### B4 준비 명령과 반환물

루트 담당의 준비 도구 인터페이스는 저장소 루트 기준 다음과 같다. `--inputs`는 생략할 수 있으며, 없는 런타임 입력은 `null`과 담당자가 있는 미확정 항목으로 반환한다. **실행 옵션은 없고 `ready_for_execution=False`, `execution_authorized=False`**다. 도구 자체를 만들었다는 사실과 실제 ENV/POL·생산자·하네스 결합 완료를 구분한다.

```text
python update/tools/b12_tp2_inputs.py --output <새-준비-디렉터리>
python update/tools/b12_tp2_inputs.py --output <다른-새-준비-디렉터리> --inputs <고정-입력.json>
```

입력 schema는 `guard-b-tp2-inputs/1`, 출력 schema는 `guard-b-tp2-prepare/1`이다. 최상위는 `schema/checks`만, checks는 세 ID 중 선언한 항목만 받는다. 각 항목은 `runtime_refs:{ENV,target,prepared,selection,scenario}`, `policy_values:{기존 CheckDef key: 값}`, `controls:{normal,failure,recovery}`, `planned_upper_seconds`로 구성한다. pointer는 `null` 또는 `{path,file_sha256}`이며 여기 SHA는 **파일 bytes SHA**다. ENV/PLAN 등의 객체 Ref hash와 혼동하지 않는다. 도구는 포인터 파일을 열거나 실제 환경 결합을 검증하지 않는다. 아래는 값이 없는 정상적인 부분 입력이며 실제 준비 완료 예시가 아니다.

```json
{"schema":"guard-b-tp2-inputs/1","checks":{"LIF-08":{"runtime_refs":{"ENV":null,"target":null,"prepared":null,"selection":null,"scenario":null},"policy_values":{},"controls":{"normal":null,"failure":null,"recovery":null},"planned_upper_seconds":null}}}
```

반환 파일은 `LIF-08-plan.json`, `LIF-11-plan.json`, `SUP-06-plan.json`, `inputs-template.json`, `declared-inputs.json`, `manifest.json`이다. ID별 파일에는 기존 executor/collection_profile·policy_types, `scenario_order`, `argv_templates`, 미지원 producer와 `unresolved[{check_id,field,reason,owner}]`가 담긴다. manifest의 `source_refs`, `artifacts`, `time_plan`은 선언된 입력과 소스 추적용이고 `joint_budget_fit_verified=False`다. 미입력 `null`은 실제 파일/Ref나 정책 승인으로 대체하지 않는다. S2는 이 문서만 작성하며 생성기·등록부·팩·A 하네스를 수정하지 않는다.

루트 고정본은 커밋 `b928b760`의 `update/tools/b12_tp2_inputs.py`, 파일 SHA `494274f0b1cbee6efa553ebf62aa598fd5020b6f6530baeff0a2c509bc006632`다. 루트가 반환한 직접 영향 검증은 `tests/test_b12_tp2_inputs.py` SHA `bcc0d34392d49227b37d535de2e6b0f934ebe4ec31df5e046399542791f96506`, **13 passed/6.15초**이며 S2는 같은 시험을 반복하지 않는다. 실제 준비 CLI의 출력은 통합 worktree의 `out/team-handoff/b12-tp2-prepare-final/`, 그 `manifest.json` SHA는 `47e83067a972a195109163033d7da6e9082ffbe22076734a0f9661ddd7c7bb8f`다. 준비 CLI 완료는 외부/GPU 실행이나 런타임 포인터 검증 완료가 아니다.

생성기가 돌려주는 명령은 **자리표시자가 남은 계획 argv**다. 예를 들어 LIF-08 반환 형식은 아래와 같고 LIF-11·SUP-06의 변수 접두사는 각각 `LIF_11`, `SUP_06`이다. 준비 도구 실행 위치는 저장소 루트지만, 반환 argv의 `packs/PACK-BASELINE`은 `update/` 기준 상대 경로다. 이 준비 작업에서는 실행하지 않으며, 스크립트 이름에 존재하지 않는 `a-tp2.py b4` 같은 하위 명령도 만들지 않는다.

```text
guard select --pack packs/PACK-BASELINE --env ${LIF_08_ENV_FILE} --policy ${LIF_08_POLICY_FILE} --config ${LIF_08_CONFIG_FILE} --store ${STORE}
guard run --plan ${LIF_08_PLAN_ID} --config ${LIF_08_CONFIG_FILE} --store ${STORE}
guard judge --plan ${LIF_08_PLAN_ID} --as-of ${AS_OF} --config ${LIF_08_CONFIG_FILE} --store ${STORE}
guard report --plan ${LIF_08_PLAN_ID} --json ${LIF_08_REPORT_FILE} --config ${LIF_08_CONFIG_FILE} --store ${STORE}
```

`select`도 Store에 팩·ENV·POL·PLAN을 쓴다. 따라서 명령 문자열 생성과 실제 select/run/judge 실행을 구분한다. `checks`의 세 ID는 준비 반환 범위이며 `select --checks` 같은 실행 제한 옵션이 아니다. 실제 하네스에서는 기존 팩·POL/설정의 선정 절차로 해당 점검만 선택됐는지 PLAN을 먼저 확인한다. 최종 템플릿의 네 명령은 모두 같은 `--config`와 `--store`를 명시한다. 준비 도구는 EVD/RES/RPT를 만들지 않는다. 기존 `tools/prepare_checklist_cases.py --pack … --output … --checks LIF-08 LIF-11 SUP-06`은 EXAMPLE 사례 계획 도구이며 실제 runtime 입력 생성·생산자 지원·TP2 실행의 대체물이 아니다. 기존 `build_kubernetes_rank_failure_pack.py`와 `build_kubernetes_resource_recovery_pack.py`는 정확한 원 PACK31 Ref를 요구하는 DRAFT 파생 도구다. 이를 새 PACK-BASELINE에 임의 적용하거나 팩을 재판정하지 않는다.

### 세 분기의 공통 고정 입력

| 입력 | A6에 넘길 값과 책임 | 현재 준비의 한계 |
|---|---|---|
| 코드·팩·하네스 | 루트의 통합 커밋/파일 SHA, 실제 선택 PACK Ref, A 담당의 `a-tp2.py`·`harness-tp2.tgz` 파일 SHA와 정확 argv | 이 기준 checkout에는 A 문서의 외부 `byul-dev1-evidence/tp2-prep/harness/` 실행 파일이 포함되지 않는다. 하네스 바이트·추가 B 호출 위치의 실제 적용은 A6 반환물이다 |
| ENV·모델 | 실제 ENV Ref, vLLM 버전/image digest, driver·GPU UUID·TP=2/mp·필수 rank `["0","1"]`·출력 rank, model id/revision·inventory SHA·API 별칭 | rank 집합과 출력 rank는 관측·선정값을 고정한다. 정상 HTTP 200만으로 두 rank의 계산·통신·가중치 소비를 입증하지 않는다 |
| target·prepared | branch/role별 target canonical SHA·prepared Ref/SHA·session·modelroot·native/callback source SHA, namespace/Pod UID/container ID/restart/PID/start ticks/rank | normal·fault·recovery와 재기동 후 새 세대는 별도로 결합한다. 근접 시각·같은 모델·이전 세션 prepared를 현재 결합으로 복사하지 않는다 |
| POL·시계 | 체크별 정확 policy key와 값, timezone 있는 유효창, 감지/상위 통지·자원 회수 기한의 정책 출처, 발생·감지·접수·복구 원시 시각 | 가정한 실행 시간과 정책 기한은 다르다. 실행 중 관측값으로 정책/허용 기한을 자동 생성하거나 연장하지 않는다 |
| 생산자 record | `guard-gpu-common-input/1`, kind, producer name/version/source SHA, 실제 ENV/target/prepared/session, 전체 binding·events·completeness | schema 정합·등록 SHA는 출처 인증이 아니다. 미지원/누락/gap을 남기며 외부 EXAMPLE record를 결합해 전체 EVD/RES를 MEASURED로 올리지 않는다 |
| 실험·복원 대상 | A6의 소유 실험 namespace/worker·안전한 장애 방법·원본 불변 모델과 별도 challenge 사본·복구 manifest·종료 책임 | 공유 워커·기존 raw·원 모델·POL·공통 센서 변경은 범위 밖이다. 모델 변경 사본도 정확한 source/정리 계획 없이는 준비 완료가 아니다 |

참조: [사용자 결정](user-decisions-20261108.md), [공통 입력 계약](../../src/guard/collectors/gpu_common_inputs.py), [공통 생산자](../../src/guard/collectors/gpu_input_producers.py). 이미 검증한 CPU/CI/wheel/archive를 이 계획 때문에 반복하지 않는다. 새 소비자 연결 검증은 해당 담당자가 반환하는 정확한 통합 커밋과 직접 영향 검증으로 별도 연결한다.

### LIF-08 — rank 실패 감지·상위 통지

| 항목 | A6에 전달할 실행 입력과 한계 |
|---|---|
| 기존 실행기/API | `KubernetesRankFailureExecutor` (`rank_failure`), collection profile `kubernetes-mp-rank-failure-observation-v1`. `cfg={target,prepared,kubectl_binary?,max_duration_seconds?}`. native `vllm-native-lifecycle-metadata/13` / `guard-native-lifecycle-events/13`, 개별 event `guard-native-rank-failure/1` |
| 필수 POL | `rank_environment_sha256`, `rank_target_sha256`, `rank_prepared_sha256`, `rank_validity_start`, `rank_validity_end`, `rank_executor_backend`, `rank_expected_ranks`, `rank_output_rank`; 각 `exception/death/delay/communication_stall`의 `rank_<kind>_detection_ms`, `rank_<kind>_notification_ms`. backend는 `mp`, aware 유효창은 600초 이하. 4종×감지·통지 8개 수치는 담당 정책 입력이며 임의 기본값을 만들지 않는다 |
| normal | 같은 선정 mp·두 rank에서 정상 계산/통신·출력 rank·상위 client 응답의 정확 요청/attempt/세대 결합을 얻는다. 기존 passive HTTP slots는 `normal-before → observe-exception → observe-death → observe-delay → observe-communication-stall → normal-after`; `observe-*` 이름은 장애 발생 증거가 아니다 |
| fault | 네 종류의 실제 발생 대상 rank/PID/start·원시 시각·수락 ack·감지·상위 전달·영향 요청 결과를 각각 기록한다. 기존 선택적 `lab_activation._rank_faults`와 passive native13 경로는 별개다. 기존 lab 제어에는 death/delay·예외 동작이 있으나 통신 정체는 실제 주입 미관측이고 예외 protocol의 rank/ack 공백이 있다. 지연과 통신 정체를 같은 timeout으로 대신하지 않는다 |
| recovery | 각 fault 해제·실제 종료/회수·선정 새 worker 세대·정상 요청을 개별 연결한다. 복원 실패 시 뒤 fault를 진행하지 않는 기존 계약을 유지한다. 다음 실험의 정상 응답을 앞 fault의 복구로 합치지 않는다 |
| 공통 record/source | kind `rank-fault`: `fault_applied/fault_released/detected/client_delivered/recovered`, `at_ms/ack/fault_kind/request_sha256`. 현재 종류 선언은 있지만 기준 소스에 해당 record 생산자 factory·지원 identity/source SHA는 확인되지 않았다. **A6/A가 실제 생산자 구현·소스 pin·발생 시계·상위 접수 경로를 반환해야 한다**. native13 센서나 공통 종류 선언만으로 대체하지 않는다 |
| 측정/미측정 | 기존 passive 경로는 실제 source·PID/start·메서드 call/return/raise·부모 구간·출력 rank 후보를 관측한다. `fault_control_implemented=False`, `rank_producer_authenticated=False`, `rank_scope_complete=False`이며 네 장애·모든 rank 정상 완료·client 실제 수신을 현재 구현만으로 측정 완료라 하지 않는다 |
| A6 삽입점 | `facts`·engine callback/native 자산 고정 뒤 첫 B fault 전 normal 원기록을 보존한다. 정확히 배정된 fault마다 위 발생→감지→상위 통지→해제→복구를 수집한 다음 그 session record와 passive packet을 B 소비자에 넘긴다. 생산자/실제 제어 입력이 없으면 이 분기 미실행 사유를 반환하며 다른 정상 A 점검을 이 분기의 완료로 세지 않는다 |

근거: [실행기](../../src/guard/executors/kubernetes_rank_failure.py), [POL 계약](../../src/guard/executors/rank_failure_contract.py), [고정 HTTP slots](../../src/guard/runtime/kubernetes_rank_failure.py), [native13 projection](../../src/guard/adapters/vllm_rank_failure_metadata.py), [기존 선택 lab 제어](../../src/guard/executors/lab_activation.py). 정책 유효창과 각 fault 감지/전달 예산을 동시에 지킨다. 4종 전체에 필요한 시간이 창을 넘으면 별도의 고정 사례/세션 입력이 필요하며 활성 기한을 늘리지 않는다.

### LIF-11 — 실패 그룹 회수·후속 정상 기동

| 항목 | A6에 전달할 실행 입력과 한계 |
|---|---|
| 기존 실행기/API | `KubernetesResourceRecoveryExecutor` (`resource_recovery`), profile `kubernetes-selected-resource-recovery-v1`; `cfg={target,prepared,selection,kubectl_binary?,max_duration_seconds?}`. `runtime-failure`는 native13, `partial-initialization`은 native14 prepared. 두 분기를 독립 사례로 남긴다 |
| 필수 POL | `recovery_environment_sha256`, `recovery_target_sha256`, `recovery_prepared_sha256`, `recovery_selection_sha256`, `recovery_scenario_sha256`, `recovery_validity_start`, `recovery_validity_end`, `recovery_branch`, `recovery_executor_backend`, `recovery_expected_ranks`, `recovery_phase`, `recovery_deadline_basis`, `recovery_required_absent_after`, `recovery_max_clock_skew_ms`, `recovery_required_resource_kinds`. branch는 `runtime-failure/partial-initialization`, phase는 `before-failure/after-cleanup/restarted-normal`, deadline basis는 `absolute-unconditional/after-verified-failure`. 유효창 600초 이하·절대 기한은 그 창 안·선정 소유 rank와 선언 rank 일치 |
| normal/fault | 실패 전 정상 요청과 실제 그룹 inventory를 먼저 보존한다. 부분 초기화 실패는 Ready worker 종료와 별개다. `KubernetesHTTP.engine_group_resources()`의 before 원본, 선정 대상 PID/start·실패 동작의 시각·ack(`failure_trigger`), 실패 뒤 census를 연결한다. 기존 passive resource 실행기는 자체 failure/restart 제어를 하지 않는다 |
| recovery | 새 container에 binding을 옮긴 뒤 after inventory와 다음 세대 정상 추론 원시 시각/요청을 수집한다. `group_resource_producer.record()` → `lineage()`를 기존 선정 자원 project와 별도 근거로 소비한다. 소유 그룹은 Pod UID/container ID/restart/PID1 start ticks이며, 새 세대와 원자원 신원을 혼동하지 않는다 |
| 공통 record/source | kind `failed-group-resources`, 생산자 `guard-engine-failed-group-resources`, version `1`. `group_resource_producer.producer()`의 SHA는 `group_resource_observer.py`와 생산자 파일의 **basename + NUL + 원 bytes + NUL**을 순서대로 연결한 SHA다. 현재 미등록이므로 지원·complete를 true로 만들지 않는다 |
| 측정/미측정 | 원 process/FD·socket inode·shm dev/ino·참조·다음 세대·추론 시각을 연결한다. `released.at_ms`는 사라짐을 확인한 **반환 상한 시각**, 정확 반환 시각이 아니다. `GPU_MEMORY_RELEASE_UNOBSERVED`는 별도 남는다. FD 부재/컨테이너 종료를 전역 GPU·IPC 반환이나 다음 정상 기동 완료로 대체하지 않는다 |
| 독립 FAIL 보존 | `absolute-unconditional`의 전후 동일 소유/시계·선정 자원과 기한 뒤 잔존으로 성립한 기존 독립 FAIL을 후속 lineage/gap이나 반출·정리 오류가 지우지 않는다. 조건부 기한은 확인된 `failure_trigger.at_ms`와 POL을 결합해야 하며 시나리오 이름으로 발생 시각을 만들지 않는다 |
| A6 삽입점 | A의 실패 그룹 자원 생산자 확인 단계 바로 전 before inventory → 실패 동작 원receipt → 새 binding/after inventory → 다음 정상 요청 → `record/lineage` → B 소비자 순서다. before만 실제인 CPU fixture와 합성 after fixture는 TP2 실측 근거가 아니다. runtime/partial 두 분기의 전체 자원·후속 기동 결과를 각각 반환한다 |

근거: [실행기](../../src/guard/executors/kubernetes_resource_recovery.py), [선정 자원/POL](../../src/guard/executors/resource_recovery_contract.py), [공통 반환 설명](lif11-recovery-lineage.md), [그룹 생산자](../../src/guard/collectors/group_resource_producer.py). 소비자 연결의 최신 소스는 S1 통합 결과를 별도 pin한다. 이 기준의 `recovery_global_resources_released`, `recovery_next_normal_launch_verified`, `recovery_scope_complete` false를 문서 작성만으로 해소하지 않는다.

### SUP-06 — worker/rank별 필수 shard와 준비 상태

| 항목 | A6에 전달할 실행 입력과 한계 |
|---|---|
| 기존 실행기/API | `KubernetesSupplyBootExecutor` (`supply_boot`), profile `supply-boot-worker-shard-v1`. `cfg.normal={target,prepared}`, `cfg.challenge={target,prepared}`, 선택 `kubectl_binary/max_duration_seconds`. normal/challenge는 별도 session이며 수집기는 엔진 생성·변조·재시작을 하지 않는다 |
| 필수 POL | `supply_environment_sha256`, `supply_normal_target_sha256`, `supply_challenge_target_sha256`, `supply_normal_prepared_sha256`, `supply_challenge_prepared_sha256`, `supply_record_source_sha256`, `supply_validity_start`, `supply_validity_end`. 유효창은 3600초 이하. 이 record source pin과 공통 worker-shard 생산자 SHA는 다른 필드/계약이므로 혼합하지 않는다 |
| 준비 자산 | Python API `guard.collectors.engine_callbacks.prepare_assets(output, worker_shards=True)`로 고정한 관측 자산이 필요하다. 기준 `prepare-engine-callbacks` CLI에는 `--worker-shards` 옵션이 노출되지 않았으므로 그런 CLI를 적거나 실행됐다고 가정하지 않는다. A6가 실제 API 호출 위치·자산 manifest·설치 binding을 반환한다. native 공급 파일 읽기 계약은 별도 유지한다 |
| normal | 고정 모델의 필수 shard inventory/index와 두 rank별 required/assigned/load_completed/rank_ready 후보를 대조한다. index가 없을 때 `REQUIRED_FROM_ASSIGNED` 진단을 남기며, rank 미확인 시 값 0을 정상 rank 증거로 쓰지 않는다 |
| fault/challenge | 원 모델을 보존한 별도 고정 사본에서 필수 shard 하나가 누락된 사례를 준비하고, 해당 rank의 `load_failed`·READY 미수락을 관측한다. 정상의 단일 파일/다중 shard 구성과 실패 사례의 inventory/source를 구분한다. 수작업 `rank_ready` 이벤트나 가짜 worker로 실패 대조를 채우지 않는다 |
| recovery | challenge 자산·소유 Pod를 정리한 뒤 원 고정 모델과 새 세대/세션으로 정상 적재·요청을 확인하는 별도 반환물이다. 기존 공급 boot 수집기의 두 역할만으로 복구 소비까지 확인했다고 하지 않는다 |
| 공통 record/source | kind `worker-shard-load`, 생산자 `guard-engine-callback-worker-shard-load`, version `1`. `worker_shard_producer.producer()`는 `vllm_worker_shard_callback.py`와 생산자 파일의 basename+NUL+bytes+NUL SHA다. record의 `shard_rows()`가 반환하는 부가 loaded/failed 사실을 보존하되, 기존 boot packet의 정확 `{rank,required_missing,ready_accepted}` 행 계약에 맞춰 B 소비자가 투영한다. 공통 record는 미등록 상태이며 이번 계획이 등록을 수행하지 않는다 |
| 측정/미측정 | `_prepare_weights`/`track_weights_loading`/`WorkerProc.monitor_death_pipe`의 콜백과 PID/start/rank binding을 기록한다. READY 후보·함수 완료는 실제 GPU 메모리 내용 적재·모든 rank 정상 추론·공급 파일 전체 소비·교체 내용 동일성·복구 소비의 증명이 아니다. 기존 rank1 CPU EXAMPLE 3사례는 재실행하지 않고 한계로 재사용한다 |
| A6 삽입점 | 엔진 시작 전 callback 자산을 고정 → normal/challenge 각각 기동·원기록 수집 → `worker_shard_producer.record()`/`shard_rows()` → S5의 고정 B 소비자 → 기존 판정 → 정리/별도 recovery 순서. A의 생산자 실제 TP2 확인·등록과 B의 소비자 판정을 다른 단계로 기록한다 |

근거: [수집기](../../src/guard/executors/kubernetes_supply_boot.py), [공급 boot 계약](../../src/guard/executors/supply_boot_contract.py), [공통 반환 설명](sup06-worker-shard.md), [worker shard 생산자](../../src/guard/collectors/worker_shard_producer.py). 실제 TP2 두 rank·누락 실패·복원 동작과 B 소비자 연결의 고정 source가 없으면 normal/fault/recovery 전체 준비 완료로 표시하지 않는다.

### 시간·비용 — 관측, 계획 가정, 계약 상한 구분

현재 새 TP2 소요 시간 관측은 **없다**. 위 A 원문의 $5.65823/h와 A만 1.5~2시간은 과거 문서/계획 값이다. 가격·할당량 최신 관측으로 바꾸지 않는다. 아래는 같은 계획 단가를 쓴 단순 EC2 시간 계산이며 EBS·기타 비용은 포함하지 않는다.

| 순서 | 한 세션 계획 배정(가정) | 결과/초과 처리 |
|---|---:|---|
| A6 공통 기동·facts·TP2 자산/입력 확인 | 15분 | 코드·이미지·모델·하네스 불일치면 실행 결과를 만들지 않는다 |
| A 기존·추가 점검 및 REQ-16 EC2 내 준비 | 120분 | A 원문 2시간을 계획값으로만 사용. Qwen2-VL-2B-Instruct의 최초 다운로드·변환/기동 추가 시간은 미측정이며 이 배정에 들어간다고 보장하지 않는다. revision·파일 SHA를 EC2에서 고정하고 로컬에서 받지 않는다 |
| B LIF-08 | 15분 | 정상·4종 fault·각 복구를 위한 슬롯 가정. 미지원 fault를 다른 사례로 대신하지 않음 |
| B LIF-11 | 10분 | runtime/partial 두 분기와 다음 정상 기동을 위한 슬롯 가정; 각 정책 창/정리 상한을 늘리지 않음 |
| B SUP-06 | 15분 | 두 rank normal·고정 누락 challenge·별도 recovery 반환의 슬롯 가정; 모델 준비시간 보장 아님 |
| 반출·SHA·소유 정리·정지·중지 후 유형 복원·장부 | 20분 | 시험 시간을 줄여 이 정리 여유를 확보. 실행 중 유형 복원하지 않음 |
| 추가 여유 | 10분 | 원 phase/backstop/timer를 reset하거나 늘리는 권한이 아님 |
| 합계 | **205분(3시간 25분)** | 계획 EC2 **$19.3322858333**, $20 대비 **$0.6677141667** 여유 |

계획 단가만으로 $20 전부를 실행 시간으로 환산하면 약 **12,724.827375초(212.0804563분)**이다. 이를 시험 상한으로 사용하면 정리 여유가 사라지므로 위 20분 정리 슬롯은 따로 남긴다. 실제 A6의 단가·새 총비용·현재 원명령/소유권·기한·할당량은 운영 담당자가 실행 전에 따로 확인할 항목이며, 이 문서는 확인 결과가 아니다. A 단계/모델 준비가 늘면 B 슬롯을 자동 연장하지 않고 미실행 분기와 사유를 남긴다. B 입력이 세션 전에 없으면 사용자 지시대로 A 범위만 진행한다. $20 안에 정리까지 들어가지 않으면 사용자에게 초과 필요분을 구분해 알리는 기존 지시를 유지한다.

기존 코드 상한도 비용표와 별개다. LIF-08·LIF-11 정책 창은 각각 600초 이하, SUP-06 정책 창은 3600초 이하이고 세 실행기의 기본 `max_duration_seconds`는 120초다. 전체 슬롯은 여러 준비/사례를 위한 가정이지 위 기본 수집 기한이나 fault별 정책 예산을 늘리는 설정이 아니다. A 원문 REQ-21의 900초(재기동 300초 포함), 기존 실행의 보호/정리 기한을 이 표로 바꾸지 않는다. 실제 하네스 phase·전체 보호창·각 정책 창과 맞지 않는 분기는 설정을 임의 변경하지 않고 정확한 미충족 입력으로 반환한다.

### 원결과 반출·정리 및 A6 최종 반환 계약

각 branch/case/role/session별로 실제 argv·시작/종료 시각·exit/status·PLAN/EVD/RES/RPT Ref(생성됐을 때만)·ENV/POL/target/prepared/source SHA·원생산자 record와 validation/gap·센서 원파일을 묶는다. 첫 실제 FAIL·timeout·정리 오류·UNKNOWN 사유와 미실행 사유는 따로 보존한다. 미등록/EXAMPLE/단일 rank/부분 native·API200을 TP2 제품 PASS나 전체 실측 완료로 올리지 않는다.

반출 대상은 정확한 절대 경로·파일명·bytes·SHA를 생성 receipt와 묶고 실제 선택한 archive 식별자를 확인한다. 예전 archive 이름/최근 목록 순서로 선택하지 않는다. 전송 종료와 local exactbytes/SHA를 확인한 뒤 CRC/의미 검증은 새 결과에 필요한 범위만 수행한다. 불확실 API 수락·활성 actor/SSM이 있으면 해당 원request/RID/CID·receipt부터 확인하며 동일 start/collect/stop을 중복하지 않는다.

정리 순서는 **원결과 반출 → 정확 소유 namespace UID/RV·worker·프로세스·GPU 점유 정리 확인 → 정상 stop → 실제 stopped 확인 → stopped 상태에서 g6.2xlarge 유형 복원 → 비용 장부**다. 앞 A 원문의 한 줄 순서를 구체화한 계획이며 이번에 수행한 동작이 아니다. 보존 EBS/raw 삭제, Force stop, 보호 timer reset, 공유 환경 변경은 계획에 넣지 않는다. 정확 종료 전이 시각을 관측하지 못하면 구간 관측과 미확정을 그대로 남기고 저장 비용을 별도 기록한다.

| 남은 반환물 | 담당 | 지금 자동 충족으로 보지 않는 이유 |
|---|---|---|
| 준비 생성기 고정 source/선언 입력·명령·미확정 목록 | 루트 고정 반환 완료 | 위 source/manifest SHA로 연결됨. 실제 runtime binding/실행 권한은 여전히 false |
| A6 실제 하네스 bytes/argv·B 삽입부·TP2 ENV/POL/target/prepared·안전 제어·복원 입력 | A 담당/A6, 운영 담당 | 문서의 경로나 시간 가정으로 실제 환경값을 만들 수 없음 |
| rank-fault 생산자 구현·name/version/source SHA·네 fault의 발생/감지/상위 통지 clock/ack | A 담당/A6 | 기준에는 종류 선언과 passive native13만 있어 공통 record 생산자를 확인하지 못함 |
| LIF-11 B 소비자 연결, before/trigger/after/next inference 통합 source | S1/루트 | 원 선정 FD 독립 FAIL과 신규 lineage를 분리한 고정 변경·직접 검증이 필요 |
| SUP-06 B 소비자 연결·정확 3키 projection·source/origin/gap 보존 | S5/루트 | 미등록 생산자·READY 후보·기존 native 소비와의 구분이 필요 |
| 실제 생산자 TP2 검증/지원 등록, 분기별 실측 결과 | A 담당/실행 담당 | GPU 기동 자체가 생산자 구현·지원 등록·질문 전체 확인을 대신하지 않음 |

이 절의 완료 범위는 **B4 입력·시간·반환 계획 작성**이며 `execution_ready=False`, `GPU_execution_performed=False`다. 43개 전체 완료, 과거 25/18 분류 변경, 새 제품 판정은 선언하지 않는다.
<!-- B4-S2-TP2-INPUTS-END -->
