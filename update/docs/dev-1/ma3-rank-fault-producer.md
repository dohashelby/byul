# M-A3 rank-fault 후보 생산자

## IPC 소속 근거의 후속 구성요소 (2026-11-14)

기존 원 기록 변환과 별도로 읽기 전용 후보 두 개를 추가했다. `vllm_rank_ipc_identity`는 정확한 표준 라이브러리 Connection/SharedMemory 형식의 FD를 잠깐 복제해 전후 fstat을 대조하고, 실제 커널 객체/boot/IPC namespace를 세션 HMAC으로만 기록한다. 원 FD·payload·공유 메모리 이름·property·fileno를 읽거나 바꾸지 않는다. 실제 자식 프로세스의 동일 pipe/공유 메모리 대조와 payload 보존, 다른 IPC·키·FD 교체/폐쇄·속성 실행 거부를 시험했다.

`vllm_rank_ipc_sources`는 고정 이미지 행과 정확 파일 bytes/SHA·원 함수 본문·정의 namespace를 대조한다. 함수 실행·관측기 설치는 하지 않는다. 새 adapter 데이터의 기준 쪽은 rank0 driver/executor가 같은 프로세스인 task/result queue 구조, 후보 쪽은 executor/worker의 broadcast/response 공유 메모리 구조로 나눴다. 실제 설치4파일·함수7개를 대상으로 하며 기존 native24 고정값/관문을 넓히지 않는다.

두 구성요소의18개 직접 시험이 CPython3.12.3과 승인된 기준 이미지 Python 바이너리3.12.8에서 각각 통과했다. 이는 CPU 구성요소 시험이다. 반환된 source_authenticated/engine_group_verified/observer_installed/producer_registered는 false이며 공통 입력·생산자 지원표에 등록하지 않았다. 기준 관측기15개 전체의 패치 판본 지원을 추가하거나 M-B4를 재개하지 않았다.

남은 연결: 정확 source hook의 원 호출 경계→부모/worker의 물리 PID/시작세대→task/broadcast와 개별 response IPC의 닫힌 원 기록→같은 요청의 실행/오류 전달·기한→실제 신호 해제·새 소유자 복구다. 같은 요청 ID·같은 IPC 지문 하나를 전체 그룹 인증으로 바꾸지 않는다. 기존 cross_process_group_verified/scope_complete=false와 독립 FAIL은 보존한다.

기존 엔진 안 native24 fault·rank 관측을 재사용하고 `adapters/vllm_rank_fault_input.py`와 `collectors/rank_fault_producer.py`가 공통 입력으로 변환한다. 관측기 복사·새 주입·기존 소스 고정값 완화는 없다. 과거 닫힌 입력은 `/1` 재생을 유지하고, 새 명시적 입력은 구현된 `/4` 봉투를 사용한다. 기존 #263은 단일 rank 후보 draft로 그대로 두며 후속은 새 main 브랜치에서 개발한다.

| 사건 | 필요한 원 근거 |
|---|---|
| fault_applied | 기존 엄격 투영이 검증한 source/물리 주입·정확 요청·nonce·세션·대상·프로세스·clock. ARM/READY 파일만으로 만들지 않는다 |
| fault_released | 같은 worker의 같은 호출에서 나온 정확 RELEASE와 action receipt 지문. EXPIRED·파일 삭제·성공 요청으로 대신하지 않는다 |
| detected | 기존 기한 투영이 선택한 탐지 원 참조. 상위 통지만 있으면 탐지로 올리지 않는다 |
| client_delivered | 동일한 영향 요청의 실제 오류 응답 수신 또는 원 투영이 확인한 늦은 수신. 정상 HTTP 성공을 장애 전달로 바꾸지 않는다 |
| recovered | RELEASE 뒤 별도 요청의 source ID 배정·worker RPC 정상 반환·단일 rank executor·성공 future·ASGI 정상 종료·클라이언트200/완료·동일 clock이 결합된 경우만 만든다 |

공통 binding은 장애 대상 worker다. 탐지/전달 관측자의 PID를 worker PID라고 쓰지 않는다. 투영의 `observer_context`와 `source_ref`는 실제 관측 프로세스/원 행을 따로 보존한다. 공통 `at_ms`는 해당 사실을 확인한 관측 시각이며 원 주입 구간이나 정책 기한을 다시 계산하는 기준이 아니다. 원 `native_validation`의 시간 구간·위반·공백·참조를 함께 보존하며 새 보안 판정은 만들지 않는다.

## 생성

`uv run python -m tools.rank_fault_inputs --input <원-묶음.json> --output <새-입력.json>`

원 묶음은 packet·client·injection_receipt·source_scope·deadlines·pod·environment_ref·target_sha256·prepared_sha256·session·recovery_client를 가진 닫힌 JSON이다. 예상 ENV/target/prepared와 source_scope는 수집자가 별도로 제공해야 한다. 도구는 최대8MiB·동일 파일 신원·중복 키와 비정상 JSON·심볼릭 링크를 검사하고 기존 출력을 덮어쓰지 않는다. 생성물은 DECLARED이며 엔진을 실행하지 않는다. 원 파일의 bytes SHA를 남기며 기존 실측을 새 실측으로 표시하지 않는다.

## 한계와 후속

- `guard-engine-callback-vllm-rank-fault/1`은 GPU 미확인 후보이며 등록표에 추가하지 않는다. 모의 등록은 EXAMPLE 단위 시험 안에서만 한다.
- 기존 엄격 투영의 검증 범위는 단일 rank·같은 소유 범위다. 새 경로는 TP2 정상 관측·물리 해제·새 세대 정상 요청을 각각 확인하지만 기존 TP1 자격을 TP2 수락으로 넓히지 않는다. 기준 V0 및 두 이미지의 설치 bytes/GPU 확인·J01 수락 자격은 없다.
- death는 SIGCONT로 해제하지 않는다. delay는 아래의 별도 물리 영수증이 있어야 해제 사건을 만들고, 새 세대도 독립적인 예상값과 원 기록이 있어야 복구 관측 사건을 만든다. GPU 메모리 반환·전체 장애 전파 수락은 별도로 미확인이다.
- source packet 자체가 검증되지 않으면 ack를 만들지 않는다. 원 행이 없으면 가상 PID/binding을 넣지 않고 계약이 거부한다. 일부 사건만 확인되면 나머지는 누락 gap으로 남긴다.
- 정상 worker 관측은 전체 GPU 자원 회수·기업 복구 정책 준수·생산자 인증이 아니다. 기존 LIF-08 판정 규칙과 제품 실행기는 변경하지 않았다.

## 2026-11-14 CPU 후속 입력

새 입력은 기존 필드에 `schema: guard-rank-fault-source-input/2`와 `closure_evidence`를 더한다. 과거 `recovery_client`는 null이어야 한다. 새 봉투의 공통 기록은 `/4`이며 생산자 지문에는 새 검증 모듈과 `/4` 변환 bytes를 포함한다. 기존 GPU 등록표는 변경하지 않는다.

`closure_evidence`는 `guard-rank-fault-closure-evidence/1`로 다음 필드만 받는다.

| 필드 | 검증하는 근거 |
|---|---|
| normal_packet·normal_client | ARM 전 종료한 별도 정상 요청. 정확 소스·닫힌 파일·소유자·같은 clock, ID 배정, 서로 다른 worker rank0/1, world_size2/output0 executor, 같은 프로세스/객체의 성공 future, ASGI 및 HTTP200/완료. 영향 요청의 ARM 전 worker 호출과 정상 요청의 프로세스도 일치해야 한다 |
| release_receipt | `guard-rank-fault-resume-receipt/1`. 별도 scope/주입 묶음 bytes의 정규 JSON SHA, READY 지문, 정확 PID/start ticks/namespace/clock 문맥, SIGCONT(18), 정지 T→R/S/D/I 상태. 상태 읽기→신호→상태 읽기의 시작/끝 여섯 시각과 원 RELEASE를 순서대로 대조한다 |
| recovery_packet·recovery_client | 해제 뒤의 새 정상 요청. 앞선 정상·장애 요청과 다른 HMAC, TP2의 두 worker·executor·future·ASGI·200/완료를 모두 같은 소스/clock/소유자에 결합한다 |
| expected_recovery | 별도로 제공한 rank0/1의 전체 예상 context 목록, executor_context·executor_sha256. 두 worker와 executor 모두 이전 PID/start ticks/namespace 세대와 달라야 하고 executor 객체 지문도 달라야 한다. 같은 Pod/container 안의 새 프로세스 세대만 다룬다 |

신호 영수증 필드는 `schema`, `scope_sha256`, `injection_sha256`, `ready_receipt_sha256`, `context`, `signal`, `before_state`, `after_state`, `before_started_ns`, `before_finished_ns`, `signal_started_ns`, `signal_finished_ns`, `after_started_ns`, `after_finished_ns`다. 파일 삭제·EXPIRED·다른 PID 재사용·같은 세대의 성공 응답·HTTP 성공만으로 대체하지 않는다. 수집자가 독립적으로 제공한 예상값과 정규 해시의 정확한 대조이며 생산자 인증은 아니다.

새 `source_closure`의 세 사실(`tp2_normal_observed`, `physical_release_verified`, `new_generation_recovery_observed`)은 따로 반환한다. 부분 실패는 앞서 확인된 사실을 지우지 않는다. 정상 관측의 두 rank가 인과적으로 같은 executor 그룹에 속한다는 별도 근거는 아직 없어 `cross_process_group_verified=False`, `scope_complete=False`를 유지한다. 기존 TP1 투영이 TP2에서 거부하는 탐지·상위 전달·기한을 새 경로로 확정하지 않고 원 공백과 확인된 독립 위반을 보존한다. 생성 파일은 DECLARED이며 EXAMPLE 시험을 MEASURED로 올리지 않는다.

CPU 시험은 고정 자료의 잘못된 소스·rank·world/output·그룹·clock·세대·정지 상태·시각·영수증 재사용을 거부한다. 실제 OS 신호 시험은 시험이 만든 Python 자식 하나만 SIGSTOP/SIGCONT하고 finally에서 회수한다. 엔진·GPU·Kubernetes 실증 결과가 아니다. 도구는 신호를 보내지 않고 기존 8MiB 입력 한도와 새 출력만 쓰는 조건을 유지한다.

이 작업의 완료 범위는 미등록 후보 변환·입력 도구와 세 사실의 CPU 검증이다. TP2 인과적 그룹/장애 전파·death의 새 소유자 복구·실제 이미지 자격·GPU 재확인/등록은 남아 있으며 M-A3 전체와 J01 준비 완료로 세지 않는다.


## 실제 이미지 대조 후속

[설치 bytes 대조와 남은 자격](ma3-image-qualification-20261114.md)을 확인했다. 후보13개 원본/53함수는 기존 후보와 같지만 fault sensor의 MP 원본은 두 이미지 모두 기존 고정값과 다르다. 표 키 존재·파일 일치만으로 runtime/GPU 자격을 만들지 않으며 기존 고정값과 scope_complete=false를 보존한다.
