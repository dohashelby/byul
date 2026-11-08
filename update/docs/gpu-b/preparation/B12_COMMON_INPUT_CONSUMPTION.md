# B1·B2 공통 입력의 기존 소비자 연결

기준 `37c284d91286dbcf2483f8fa654d2e50f32f9d38`, 전용 브랜치 `codex/b12-lifecycle-20261108`.
등록·관측·인증·질문 전체 완료를 구분한다. 이 변경은 GPU 실행 패킷이나 신규 실행기가 아니다.
GPU/AWS·모델 실행·producer 등록·core/models/judges/registry·기본 팩·정본 manifest 변경은 없다.

## 입력과 경로

기존 실행기의 설정에 선택적인 `common_inputs`를 준다. 기존 설정과 native 준비·자극·수집·정리 절차는 유지한다.
기록을 주지 않으면 기존 경로를 그대로 사용한다. 예시 값은 실제 입력으로 사용하지 않는다.

```toml
[request_termination.common_inputs]
prepared = "/operator/fixed/prepared.json"
session = "실제 준비 세션의 32자리 소문자 hex"
data_origin = "EXAMPLE"
[request_termination.common_inputs.records]
request-attempt = "/operator/fixed/request-attempt.json"
```

`data_origin`은 기본 EXAMPLE이며 명시적인 DECLARED만 추가 허용한다. 파일로 받은 입력을 MEASURED로 선언할 수 없다.
원 native draft·ENV보다 강한 출처로 올리지 않으며, 이 합성 기록의 출처 제한을 과거 별도 EVD Ref에 소급하지 않는다.
원 record schema에 출처 필드를 새로 만들지 않는다.

ENV Ref와 실제 실행 target의 canonical SHA, 선언한 prepared의 canonical SHA, 명시한 세션을 A 검증기의 기대값으로 준다.
record 자신의 필드를 기대값으로 사용하지 않는다. native prepared가 있으면 bytes의 canonical 일치와 session 일치도 확인한다.
현재 engine의 PodUID/containerID/restartCount를 binding과 대조한다. worker PID/start/rank 독립 인증·완전 roster는 확인하지 않았다고 남긴다.
파일은 symlink·종류·8MiB 상한·읽기 전후 신원/크기/mtime·strict JSON을 검사한다.
생산자 지원 여부는 원 SUPPORTED_PRODUCERS를 그대로 쓰며, 등록 SHA를 출처 인증으로 간주하지 않는다.

전환 실행기는 `common_inputs.generation`으로 기존 준비 목록의 한 세대를 명시해야 한다. 가까운 시각이나 두 정적 target을 결합하지 않는다.
OBS-02 복합 프로파일은 `common_inputs.identity`/`common_inputs.profiler` 아래에 각각 위 형식을 둔다.
각 child의 기존 target·prepared·세션을 따로 대조한다. 같은 record를 양쪽 경계의 증거로 인증하지 않는다.

| 입력 | 기존 소비자 | 보존하는 사실과 남은 범위 |
|---|---|---|
| request-attempt | stream_buffer LIF-03, request_termination LIF-10·12 | 원 events/attempt·후보·gap. 전체 budget/종료/자동 retry와 최종 출력 결합 아님 |
| generation-transition | lifecycle_transition LIF-05·06, initialization LIF-07·09 | 세대 label/혼합 후보. 실제 weight/cache 소비·부분 로딩 효과 아님 |
| health-worker-causal | observation_state OBS-02 | 실제 공유 causal key 수. 현재 서로 다른 process의 독립 causal 행을 인증된 인과로 만들지 않음 |
| profiler-control | profiler_observation LIF-14, log_analysis OBS-03 | API/worker/backend/output 행. RPC·backend trace 인과와 profiler 전체 수명 보증 아님 |
| process-device-provider | container_config PLT-01, credential_exposure PLT-04 | namespace/host PID 쌍·device/provider metadata. 완전 worker roster·기업 자격 정책 보증 아님 |
| ipc-ownership | unix_ipc PLT-02 | A summarize의 역할/listener/방향/미해결 peer. actor 허용 UID·메시지 소비 판정 대체 안 함 |
| failed-group-resources | resource_recovery LIF-11 | A lineage의 trigger/released 상계/잔존/next generation/normal. 전역 GPU memory 반환·회수 정책 보증 아님 |

## 정규화와 원 판정의 경계

core의 `_normalize`는 CheckDef.required_observations에 없는 키를 제거한다.
LIF/OBS-02의 공통 입력 보고는 기존 `activation_steps`의 `committed-A-common-inputs` 하위에 둔다.
기존 `activation_missing`·`activation_violations`를 보존하고 `activation_scope_complete=false`로 둔다.
원 native 관측은 보고의 `native_observations`에도 보존한다. 원 후보를 새 확정 위반으로 승격하지 않는다.

PLT-01/04는 기존 process_scan_complete, PLT-02는 ipc_scope_complete, OBS-03은 log_coverage_complete/gaps를 보수적으로 유지한다.
원 record·검증 결과·도메인 사실·native 관측은 `b-common-inputs.json` artifact로 저장한다.
이름 있는 새 관측 키가 현재 팩에 선언되지 않은 부분은 판정 연결 완료로 세지 않는다.
기존 lab_activation의 좁은 병합은 원 passive/active 독립 activation 위반을 둘 다 보존하며 새 입력으로 출처·완전성을 올리지 않는다.
후속 JSON/파일/artifact 오류도 이미 수집한 독립 위반을 지우지 않는다.

## 공통 담당 반환 사항

1. REQ-12: A request-attempt /1 checks에는 LIF-03·10·12만 있다. 새 입력은 `B_COMMON_INPUT_CHECK_UNDECLARED:REQ-12`로 거부한다.
   기존 REQ-12 native consumer/닫힌 profile 분기는 유지한다. 지원 ID·직접 관측·판정 계약의 별도 확정이 필요하다.
2. PLT-02: 현재 기본 PACK은 `unix-ipc-access-v1`, 기존 등록 Kubernetes consumer는 `kubernetes-unix-ipc-access-v1`만 허용한다.
   기본 경로 거부를 보존한다. 기존 `tools.build_kubernetes_unix_ipc_pack`의 DRAFT 파생팩에서 실제 factory→EVD→원 judge→RES→RPT→복원·재판정을 검증한다.
   기본팩 연결을 닫으려면 담당자가 profile/정책/필수 관측/판정의 실제 diff를 검토해야 한다. consumer 허용 목록을 몰래 늘리지 않는다.
3. LIF-11: 기본 PACK은 activation 4키만 요구한다. 기존 native `recovery_absolute_deadline_violated`는 파생팩에서 기능 FAIL이다.
   이를 activation_violations에 옮겨 보안 FAIL로 재분류하지 않는다. 원 native 값과 원 기능 FAIL은 기존 파생팩·artifact에 보존한다.
   기본팩에서 이 직접 관측을 판정하려면 담당자의 관측·축 매핑이 필요하다. 전역 반환 미확인은 그대로다.
4. IPC/group 생산자는 기준선에서 미등록이다. 원 `GPU_INPUT_PRODUCER_UNSUPPORTED`와 group의 GPU memory 미관측을 남긴다.
   B 시험은 실제 생산 함수를 호출하지만 등록표를 monkeypatch하거나 자동 승인하지 않는다.
5. PLT/OBS의 새 named 사실을 기본팩의 판정 입력으로 쓰려면 담당자의 CheckDef required_observations/정책/원 judge 연결이 필요하다.
   현재 artifact 보존을 그 연결이나 전체 질문 완료로 세지 않는다.

## CPU 검증과 고정 인계

`tests/test_b12_common_input_consumption.py`는 실제 A transform이 만든 기록을 소비한다. 입력 callback 행과 OS I/O는 명시적 CPU EXAMPLE fixture다.
IPC의 before 관측은 기존 k3d CPU fixture, group의 after 세대는 기존 합성 fixture다. 이번에 엔진·클러스터·모델을 실행하지 않는다.
factory 지원 목록 변경 없이 정상/위반 후보/미등록·누락/ENV·target·prepared·session 불일치/교체/후속 오류를 검증한다.
현재 팩 LIF-10, 기존 PLT-02/LIF-11 DRAFT 파생팩은 canonical select→run→EVD→원 judge→RES→RPT→별도 DB 복원→저장팩 재판정까지 연결한다.
기본 PLT-02의 profile 거부도 별도 계약으로 남긴다. 원 assert·판정·기한을 약화하지 않는다.

최초 `update/out/b12-consumption-v1/pytest.log`의 42통과/4실패는 보존한다.
list JSON의 `.get` 오류는 전용 소비자에서 형식을 확인하도록 수정했다.
나머지 3개는 기본 PLT-02 profile 불일치를 숨기지 않고 위 경계대로 시험을 분리했다.
최종 소스 SHA·실행 argv·pytest 시간·부모 wall time·exact Ref는 같은 worktree `out/` 및 `update/out/b12-*`의 고정 인계로 참조한다.
같은 버전의 유효 검증을 반복하지 않는다. GPU·실제 생산자 인증·정책 승인·전체 질문 완료를 주장하지 않는다.
