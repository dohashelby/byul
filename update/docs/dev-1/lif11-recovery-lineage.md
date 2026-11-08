# LIF-11 복구 계보 공통 입력 (개발 담당, B 의존 반환 7, 2026-11-08)

B 요청(`gpu-b/consolidation-20261106/DEPENDENCY_RETURNS.md` 7번): `resource_recovery_contract.project`가 항상 남기는 네 사유
(독립 실패 trigger·조건부 기한, 선정 FD 부재≠전역 반환, 전체 소유자·참조·반환 시각, 다음 정상 기동 계보·정상 추론)를 채울 실제 입력.
선정 FD 부재나 컨테이너 종료를 전역 GPU/IPC 반환으로 바꾸지 않을 것.

## 반환물

| 항목 | 내용 |
|---|---|
| 계약 | 기존 `failed-group-resources`에 행 `failure_trigger`·`normal_inference`를 더했다(기존 세 행·완료 조건 그대로). 조건부 기한은 `failure_trigger.at_ms`에서 잰다 |
| 관측기 | `collectors/group_resource_observer.py` `OBSERVER` — 엔진 컨테이너 안 읽기 전용. 세대(PID 1 시작 ticks)·수집 시각·프로세스(PID·ticks·역할)·unix socket inode·`/dev/shm` 항목(dev, ino, 크기, 이름 SHA)과 프로세스별 fd·매핑 참조·GPU 장치 fd |
| 생산자 | `collectors/group_resource_producer.py` — `guard-engine-failed-group-resources` 판본 1. 실패 전·후 관측 + trigger + 다음 세대 정상 추론 → 기록 |
| 전송 | `KubernetesHTTP.engine_group_resources()` — request-binding·engine-callback 전송. 재기동 뒤에는 바인딩을 새 컨테이너로 옮긴 다음 부른다 |
| 요약 | `group_resource_producer.lineage(record)` — trigger 시각, 종류별 반환 수·반환 상한 시각, 종류별 잔존 수, 다음 세대, 다음 정상 추론 시각 |
| 등록 | **미등록**. TP2 GPU 세션에서 실제 실패·재기동으로 확인한 뒤 등록한다 |

## 정의

- 자원 신원은 세대를 넘어 같은 자원이면 같은 값: pid=(PID, 시작 ticks), ipc_socket=커널 inode, shm=(dev, ino, 이름 SHA), device=(PID, ticks, 경로 SHA).
- 소유 그룹 = (Pod UID, container ID, 재시작 횟수, PID 1 시작 ticks). 다음 세대 id = `c<container 12자>-r<재시작>-p<ticks>`.
- `released`: 실패 전에 있던 자원이 다음 관측에 없음. 시각은 그 관측 시각(상한)이며 정확한 반환 시각이 아니다.
- 실패 전·후 모두 있는 자원은 계약의 위반 후보 `resource_remaining_after_failure`. 기한 판정은 소비자가 정책으로 한다.
- `/dev/shm`이 Pod 볼륨이면 컨테이너 재시작 뒤에도 남으므로, 실패 뒤 반환되지 않은 공유 메모리가 다음 세대에서 보인다.

## 남기는 공백(진단 코드)

`GPU_MEMORY_RELEASE_UNOBSERVED`(항상: 컨테이너 안에서 NVML host PID를 이 그룹과 잇지 못함), `FAILURE_TRIGGER_UNOBSERVED`·`_UNACKNOWLEDGED`·`_TARGET_NOT_IN_GROUP`,
`NEXT_GENERATION_NOT_STARTED`, `NEXT_NORMAL_INFERENCE_UNVERIFIED`, `NEXT_API_SERVER_UNOBSERVED`, `SHM_WITHOUT_PROCESS_REFERENCE`,
`PROCESS_IDENTITY_CHANGED`·`GROUP_RESOURCE_BINDING_CHANGED`(source_ended=false). 등록 뒤에도 GPU 메모리 공백 때문에 complete가 되지 않는다.

## 확인

- k3d 실제 vLLM 0.31.0 CPU 엔진(arm64, EXAMPLE) 실패 전 관측: 4프로세스 안정, `/dev/shm` 3항목, EngineCore 2·Worker 3 공유 메모리 참조. `tests/fixtures/group_resources_k3d_20261108.json`.
- 실패·재기동 뒤 관측은 그 출력을 바탕으로 만든 **합성** 다음 세대다(실제 실패 실측 아님).
- 시험 `tests/test_group_resources.py` 16개: trigger→다음 정상 추론 계보, 프로세스·socket 반환과 남은 공유 메모리 잔존 후보, 등록 뒤에도 GPU 반환 공백,
  trigger·정상 추론·세대 누락 코드, 미확인·남의 trigger 거부, 같은 세대 잔존, 참조 없는 공유 메모리·불안정 프로세스, 형식 오류, 전송 프로필 제한.

## B 연결 방법(제안)

LIF-11 실행기가 (1) 실패 전 `engine_group_resources()`, (2) lab 실패 동작의 시각·대상(PID·ticks)·ack, (3) 재기동 바인딩 이동 뒤
`engine_group_resources()`, (4) 정상 추론 시각을 모아 `record()`에 넣는다. `project`의 기존 선정 FD·절대 기한 독립 FAIL은 그대로 두고,
이 기록의 `lineage()`를 네 미확인 사유를 줄이는 별도 관측으로 쓴다. 실패를 일으키는 lab 동작과 B 소비자 수정은 B·TP2 세션에서 한다.
