# M-A3 rank-fault 후보 생산자

## 현재 V1 RPC 원 함수 대조 (2026-11-15 로컬 후속)

기존 이미지별 IPC/제어 profile은 과거 기준 V0·후보 V1의 자격 범위로 보존한다. `vllm_rank_v1_source_reviews.json`은 현재 두 V1의 설치 소스3파일·원 함수12개의 별도 검토 표다. `qualify_current_v1`은 고정 이미지·정확 파일 bytes/크기·현재 함수 code/globals·원문에서 다시 컴파일한 본문을 대조한다. 기존 exact 대조 구현을 재사용하며 hook을 설치하거나 장애 제어/수락·공통 입력/생산자 등록을 열지 않는다.

| 기준 V1 대상 | 실제 의미와 연결할 때의 제한 |
|---|---|
| `Executor.execute_model` → `MultiprocExecutor.collective_rpc` | parent는 모든 rank의 response를 순서대로 받은 뒤 목록을 반환하고 상위 execute_model이 rank0 결과를 고른다. 후보의 unique reply rank/future/non_block 계약으로 바꾸지 않음 |
| `WorkerProc.worker_busy_loop` | 세 항목 `(method,args,kwargs)`를 dequeue한 뒤 직접 worker 함수를 호출한다. 별도 `_execute_worker_rpc` 프레임은 없다. 원 Exception은 failure response에 넣고 루프를 계속하므로 worker 메서드의 예외와 parent 탐지/클라이언트 오류를 구분해야 함 |
| `WorkerProc.__init__`·`wait_for_startup`·`worker_main` | READY와 response handle을 먼저 전송한 뒤 `init_device`·`load_model`을 실행한다. wait_for_startup의 성공만으로 모델 로드/worker 실행 준비 완료를 만들지 않음. main의 queue 준비 완료와 실제 정상 요청을 추가로 연결해야 함 |
| `MultiprocExecutor._init_executor`·`shutdown` | 시작 시 worker 멤버·queue 준비를 대조한다. shutdown의 실제 조건문/회수 경로는 원문 그대로 검토하며 호출/반환만으로 물리 자원 회수·새 세대 복구를 주장하지 않음 |

후보는 실제 `collective_rpc`·`worker_busy_loop`·`_execute_worker_rpc`의 별도 원 본문을 고정한다. 같은 이름이 있어도 기준 행과 관측 의미를 이월하지 않는다. 두 CPython 패치 판본의 표는 **오프라인 소스/본문 대조**용이다. 반환의 `review_scope=offline_source_body_only`, `image_runtime_verified=false`를 유지하며 실제 이미지 런타임·ASGI/worker 결합·프로세스/IPC 소속·전체 TP2 인과/기한·실제 해제/새 세대 복구·별도 communication-stall은 아직 미완료다. 시험 namespace는 원 code와 EXAMPLE 협력 객체만 쓰며 엔진/모델/GPU는 기동하지 않는다. M-B4는 보관한다.

직접159개(pytest7.41초·부모7.590초)와 승인 이미지 CPython3.12.8 직접69개(0.40/0.522초)가 통과했다. 현황·경로16개·소스561파일 검토 관문은 통과했으며 새 PR CI와 실제 엔진/GPU는 미실행이다.

## 최신 main 통합과 PR 작성자의 다음 작업 (2026-11-15)

#276은 #275·#277이 병합된 main `77bc05a3`에 맞춰 통합했다. ARM/2 사전 결합과 미등록 공통 부분 입력의 생성/검증에 이어 `rank_failure:common-input`의 LIF-08 파일 소비·양쪽 제품 비교·정확 EVD→RES→RPT 왕복까지 구현했다. 파일 소비는 엔진 실행이 아니며 부분 입력의 ABORTED·UNKNOWN과 기존 독립 FAIL을 보존한다. #278에는 실제 파일 조립 도구가 별도로 있다. 전체 TP2 수락·새 V1 GPU 자격·등록 완료를 뜻하지 않으며, 원 준비 보고서와 여섯 ARM/1 GPU 사례는 당시 source에 귀속한 채 보존한다.

`bb14f094`의 PR 전체 시험은 candidate/death 입력 생성 중 CONTROL_READY 원 snapshot이 없는 사례1건으로 실패했다. READY 파일을 먼저 저장한 source가 snapshot을 닫기 전에 actor의 SIGKILL을 받을 수 있는 순서 경쟁이다. actor는 이제 같은 CALL·상위 프레임·대상 세대·nonce·ARM/READY 해시·정확 source의 닫힌 CONTROL_READY snapshot을 확인해야 동작한다. 기존 ARM 기한 안에서만 기다리며 누락/중복/범위 불일치는 장애 적용 없이 거부한다. 판정·물리 신호/해제 증명·등록 조건은 유지한다.

작성자가 이어갈 순서는 다음과 같다.

1. **제품 연결의 최신 CI 확인:** 구현된 LIF-08 소비와 #278 조립 도구를 중복 구현하지 않는다. READY 경쟁 수정 후 확정 head의 Linux 전체 시험·기존 실제 소유 자식 신호 시험과 제품 왕복을 확인한다. 파일 검증의 통과를 실제 엔진 자격으로 올리지 않는다.
2. **기준 V1의 RPC 관측 자격:** main은 기준·후보 모두 V1 선택이다. 기존 `v0-task-queue` 행과 기준 V0 GPU 기록을 초기 V1의 자격으로 넘기지 않는다. 기준 V1의 정확 RPC/worker 경계·요청 연결·소스 bytes/함수 본문·Python ABI를 대조하고 필요한 adapter 데이터를 검토한다. 후보의 자격도 해당 정확 소스에 한정한다.
3. **남은 사건의 원 근거 연결:** TP2 전체 인과 그룹, 탐지·같은 영향 요청의 상위 전달·정책 기한, 같은 소유 범위의 해제/복구 근거를 연결한다. 현재 `fault_applied`/일부 `fault_released`만으로 `detected`/`client_delivered`/`recovered`나 complete를 채우지 않는다. 별도 communication-stall 분기와 death의 복구 의미도 각각 검증한다.
4. **새 입력과 J01 검증:** 병합 후 확정 source의 새 prepared/독립 세션과 현재 V1 ENV·target을 만들고 ARM 전에 제품 입력을 결합한다. 승인된 실제 GPU 범위에서 기동 전 자격·정상 요청을 확인한 뒤 새 ARM/2 원 기록을 수집해 공통 입력과 제품 결과를 대조한다. 정확 이미지/판본/producer source의 검증을 마친 뒤에만 등록을 검토한다. 과거 ARM/1을 새 제품 입력으로 사후 귀속하지 않는다.

이번 통합은 새 GPU/클라우드 실행이나 M-B4 재개를 수행하지 않는다. main 통합 시 SDK14개 자산은 통합 전 #276과 동일했지만, 후속 READY 경쟁 수정으로 actor bytes가 바뀌었다. 과거 준비물/원자료는 보존하고 실제 실행용 준비물은 최종 병합 소스로 다시 만들어야 한다. 새 소스 고정 목록은 기존 준비물의 실행 자격을 갱신하지 않는다.

## 두 이미지의 실제 IPC 기동 관측 (2026-11-14)

후속 PR #271의7e49f608/source7c225d4e 준비 자산을 동일 승인 GPU 노드에 설치했다. 두 판본 모두 TP2 모델 로드·GPU2개·정상 HTTP200을 확인했다. 기준/후보 각3개의 닫힌 snapshot을 회수했고 정확 준비 자산·ABI/설치 파일/원 함수 본문·HMAC 대조에서 공백0개였다. [원자료 요약](ma3-image-qualification-20261114.json)의 후속_GPU_IPC_관측에28,565 bytes ZIP과10개 파일의 크기/SHA를 기록했다. 비밀 settings/키는 공개 요약에 넣지 않았다.

기준 쪽은 동일 부모의 executor/worker wrapper와 실제 rank1 프로세스의 PID·시작세대가 일치하고, 서로 다른 task/result pipe의 양끝 커널 HMAC 지문이 모두 일치했다. 부모가 기록한 wrapper 객체 지문도 executor 멤버와 같았다. driver RPC rank0를 GPU TP rank 증명으로 바꾸지 않았다. 후보 쪽은 부모가 기록한 rank0/1 프로세스 세대가 각 worker 자신의 문맥과 일치하며 broadcast 지문은 세 프로세스에서 같고, 개별 response 지문은 부모-해당 worker끼리 일치하며 두 rank 사이에는 다르다.

이는 초기 IPC 소속 메타데이터의 실제 대조다. 활성 요청/RPC·nonce와 주입·탐지·상위 전달·기한·해제·새 세대 복구를 연결한 전체 수락은 아니다. 기존 source_authenticated/engine_group_verified/producer_registered=false, native24 고정값과 TP1 거부 조건을 유지하며 장애 주입은0회다. 정상 요청의 응답을 장애 전달/복구 사건으로 변환하지 않는다. 현재 main의 기존 fault 소스 불일치가 해소됐다고 주장하지 않는다.

기준 모델 로드 중 두 프로세스가 실제 디스크 읽기 대기 상태였다. 기존 가중치를 순차 읽으며 두 SHA를 재확인한 뒤 기동이 완료됐고, 새 다운로드·모델/판정 설정 변경은 없었다. 지연과 정상 HTTP를 성능 판정으로 쓰지 않는다. 다음 구현은 정확 RPC/요청 관측 경계와 IPC/프로세스 세대를 결합한 제어, 물리 신호 해제 영수증 및 새 세대 복구 연결이다. 준비된 경로만 다시 GPU에서 확인한다.

## IPC 소속 근거의 후속 구성요소 (2026-11-14)

기존 원 기록 변환과 별도로 읽기 전용 후보 두 개를 추가했다. `vllm_rank_ipc_identity`는 정확한 표준 라이브러리 Connection/SharedMemory 형식의 FD를 잠깐 복제해 전후 fstat을 대조하고, 실제 커널 객체/boot/IPC namespace를 세션 HMAC으로만 기록한다. 원 FD·payload·공유 메모리 이름·property·fileno를 읽거나 바꾸지 않는다. 실제 자식 프로세스의 동일 pipe/공유 메모리 대조와 payload 보존, 다른 IPC·키·FD 교체/폐쇄·속성 실행 거부를 시험했다.

`vllm_rank_ipc_sources`는 고정 이미지 행과 정확 파일 bytes/SHA·원 함수 본문·정의 namespace를 대조한다. 함수 실행·관측기 설치는 하지 않는다. 새 adapter 데이터의 기준 쪽은 rank0 driver/executor가 같은 프로세스인 task/result queue 구조, 후보 쪽은 executor/worker의 broadcast/response 공유 메모리 구조로 나눴다. 실제 설치4파일·함수7개를 대상으로 하며 기존 native24 고정값/관문을 넓히지 않는다.

### 읽기 전용 hook과 준비 자산

`vllm_rank_ipc_hooks`는 같은 구현에 adapter 데이터의 경계/안전한 필드 경로를 넣는다. 일반 인스턴스 dict만 읽고 property·사용자 getter·stale dict 값을 actual 속성으로 쓰지 않는다. 부모의 각 worker 객체와 실제 PID/시작 세대를 따로 기록하며 RPC rank와 GPU TP rank를 같은 것으로 가정하지 않는다. 기준 worker 프로세스가 부모 executor 모듈을 import하지 않은 경우에는 자기 호출에 필요한 정확 source만 요구하고, 부모 쪽 원 기록을 별도로 결합해야 한다.

`tools.rank_ipc_candidates prepare --image <고정 이미지> --output <새 디렉터리>`는 현재 검토 소스 관문을 확인한 뒤 준비한다. settings의 키는 비밀 파일에만 있으며 반환 manifest에는 해시만 있다. `/guard-rank-ipc`의 읽기 전용 코드/설정과 별도 private data 디렉터리로 배치한다. 준비 경로를 원 import 탐색에서 제거하고 기존 sitecustomize를 먼저 실행한다. helper는 _byul_rank_ipc 전용 namespace로 로드해 원 guard 패키지와 finder/loader 선택을 보존한다. 설치 source/ABI가 맞지 않으면 원 엔진을 바꾸지 않고 관측 공백으로 둔다.

`tools.rank_ipc_candidates verify --prepared <로컬 원 준비물> --capture <반출 snapshot 폴더>`는 파일/설정·개별 snapshot HMAC·정확 source/본문과 snapshot 안의 context·세션 일치를 대조한다. 결과는 후보 원자료의 무결성 대조이며 producer 인증·그룹 전체 수락·LIF-08 PASS가 아니다. 파일/키/본문을 바꾼 원자료는 거부한다. 실제 엔진 확인 전까지 설치/그룹/등록 완료를 주장하지 않는다.

추가48개 시험은 CPython3.12.3/3.12.8에서 각각 통과했다. 직접 영향/경로305개·현황/544파일 관문이 통과했으며, 기존 native24 고정값·생산자 지원표와 패치 판본별15개 관측기 지원은 그대로다.

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


## 활성 요청 연결의 ID 읽기 구성요소 (구현 중)

`vllm_rank_request_identity`는 정확 source hook이 고른 클래스/필드의 요청 ID만 세션 HMAC으로 바꾸는 미등록 후보다. 기준의 구조체 목록과 후보의 스케줄러 양수 작업 dict, external/internal ID 쌍을 구분한다. 일반 dict가 없는 구조체는 정확 클래스 namespace와 표준 member descriptor 소유자를 확인하며 property·사용자 getter/descriptor를 실행하지 않는다. ID 중복/상한·잘못된 형식·변경 중인 필드·클래스 교체를 거부하고 원 ID/본문/토큰을 반환하지 않는다.

직접17개가 CPython3.12.3/3.12.8에서 각각 통과했다. 승인된 기존 이미지 레이어에서 추가 다운로드 없이 msgspec C 모듈844,672 bytes를 제한 추출해, 각 이미지 Python/해당 구조체 구현에서도 같은 표준 슬롯 읽기와 요청 ID 변경 구분을 확인했다. 프로젝트·venv 의존성은 바꾸지 않았고 엔진/CPU 이미지 실증은 아니다. 이 구성요소만으로 활성 RPC·nonce·실제 주입·해제·새 세대 복구를 증명하지 않으며 rpc_active_verified/source_authenticated/producer_registered=false다. 아직 실제 source hook/제어에 연결하지 않았다.


## 정확 RPC 프레임·worker·요청의 CPU 연결 후보

`vllm_rank_rpc_hooks`를 비밀 준비 자산/독립 namespace의 설치 경로에 연결했다. 기준은 실제 driver 실행 안의 worker 호출, 후보는 실제 worker RPC 안의 wrapper 호출을 adapter 데이터로 고른다. 같은 요청 HMAC·같은 프로세스 문맥·실제 상위 원 함수의 code/globals/self 프레임과 worker 객체 경로가 모두 맞아야 rpc_parent_frame_verified/rpc_parent_worker_verified가 true다. ContextVar 토큰·같은 요청 ID·시각만으로 대체하지 않는다. 해당 true는 원 프레임/worker 연결의 한 사실이며 전체 그룹/생산자 인증은 아니다. API 반환 ID와 후보 external/internal 재배정도 원 ID 없이 HMAC으로만 기록한다.

원 함수의 인자/반환/예외를 보존하고 관측 sink의 SystemExit/KeyboardInterrupt도 원 호출을 바꾸지 않도록 처리한다. 정상/예외 호출 앞뒤에 같은 call 지문을 남기며 source·본문·설치 hook이 바뀌면 연결을 만들지 않는다. 기존 IPC scope는 그대로 두고 RPC scope를 명시적으로 선택한다. 실제 반출한 이전10파일 준비물은 정확 소스 SHA/자산 전체 해시 행으로만 재검증하며 현재 준비물의 자산 누락과 바꿔치기는 거부한다.

추가39개(요청 ID17·RPC hook15·독립 설치/변조 거부7)가 두 Python ABI에서 각각 통과했고 직접 영향/경로103개가 통과했다. 설치 소스6/8파일·함수4/5개는 각 ABI의 컴파일/소비 대조이며 엔진을 import/실행한 GPU 자격이 아니다. 과거 실제 GPU 원 기록6개도 현재 도구로 재검증했다. 기존 native24 고정값·등록표·TP1 거부 조건을 보존한다. 아직 이 RPC 후보를 실제 엔진에서 관측하지 않았고 nonce 제어·주입·해제·새 세대 복구는 미연결이다. source_authenticated/engine_group_verified/producer_registered=false이며 실제 장애0회다.


## 일회용 nonce 제어와 pidfd actor의 CPU 검증

source control은 private ARM의 HMAC·세션/설정/자산·정확 대상 세대/원 CALL/API ID 근거·rank0/TP2를 대조하고 일회용 claim 뒤 READY를 만든다. 후보 내부 ID는 실제 재배정 쌍으로 연결한다. READY/RELEASE는 barrier 사건이고 신호 적용/해제와 다르며 만료를 RELEASE로 쓰지 않는다. 의도적인 경계 예외는 CONTROL_RAISE와 original_invoked=false로 구분해 원 함수가 실행됐다고 기록하지 않는다.

Pod 안 actor는 정상 요청과 실제 초기 IPC/객체 연결을 먼저 확인하고 pidfd_open 전후 같은 프로세스 문맥을 대조한다. 실제 SIGSTOP/SIGCONT·SIGKILL의 전후 상태/시각을 별도 영수증에 남기며 death에는 SIGCONT를 보내지 않는다. delay는 finally 정리 외에도 별도 fork lease가 actor 사망/만료 때 같은 pidfd만 재개한다. 새 자원/이미지/모델을 만들지 않고 실제 대상 Pod UID/모델 영수증을 실행별 scope로 받는다. 전체 수락/등록은 여전히 false다.

직접 영향/경로130개, 새66개의 기준 이미지 Python ABI 대조가 통과했다. 시험의 HTTP는 대역이며 원 RPC/barrier·시험이 만든 Python 자식의 실제 신호/해제·actor의 finally 없는 사망/lease 만료 정리는 실제 OS에서 검증했다. GPU 실행·실제 엔진 장애로 올리지 않는다. Source548파일/목록 자체SHA70f2607a의 정확 검토 행을 추가했다. 다음 GPU에서 먼저 정상 source/ID/IPC 자격을 대조하고 자격 실패 시 주입하지 않는다. 새 세대 복구와 원 판정/상위 오류 전파 전체 수락은 별도다.


## 실제 GPU RPC 사례의 부분 대조 (2026-11-14)

소스 `789817e3`·목록 자체 SHA `30f4ea1d`의 별도 비밀 준비물을 실제 기준 TP2 GPU에서 실행했다. 지연은 원 RPC/요청 연결, 같은 pidfd의 STOP/CONT 물리 영수증, 해당 요청 HTTP200과 새 Pod 정상 HTTP200을 확인했다. 예외는 원 RPC 안 CONTROL_RAISE와 HTTP500, 종료는 pidfd SIGKILL/EXITED와 HTTP500을 확인했고 두 경우 모두 새 Pod 정상 HTTP200을 확인했다. 원 snapshot/HMAC/정확 자산 대조의 gap은 세 사례 모두 0이다. 정상 응답을 오류 전달로 바꾸지 않고, 전체 그룹·상위 전파 기한·기존 TP1 수락·등록은 미확인으로 유지한다.

후보 지연 v2는 물리 STOP/CONT·HTTP200·새 Pod 정상 HTTP200이 기록됐으나 추가 RPC 호출에서 fault16개·복구8개 gap이 있어 전체 원자료 대조를 통과하지 않았다. 원본을 보존하며 gap을 제거하거나 성공으로 바꾸지 않는다. 후속 구현은 gap을 유지하면서 허용된 고정 진단 코드/실패 단계만 더한다. 사용자 예외의 원문·본문·토큰은 기록하지 않는다. 새 확정 소스의 읽기 전용 요청으로 원인을 확인하기 전에는 후보 추가 장애를 실행하지 않는다.

기준 원본 ZIP: 지연100,012 bytes/SHA `d80fe50621457a673d0c876d37faada6d033f65c3a5242c4e75f8fd82aef15f1`, 예외91,148 bytes/SHA `427b52fdc58152a3c69cea73c63cbdc0661e1e29502e29afe983f6847505e3e1`, 종료83,584 bytes/SHA `53273a063bf2825e5d38c5cf08af1ca8dd6c9bc69466c051be9b54882ba483be`. 실제 원본/검토 자료는 작업 폴더에 보존한다. 제품 EVD/RES 생성이나 M-A3 전체 완료·생산자 인증을 주장하지 않는다.


## 두 판본의 RPC 구성요소 실제 사례 대조 완료 (2026-11-14)

[원 ZIP·소스 귀속·분기별 대조 기록](ma3-rpc-gpu-observations-20261114.json)의 여섯 사례에서 329개 snapshot의 HMAC·정확 자산·설치 소스/함수·상위 원 프레임·요청 연결과 별도 제어 영수증을 대조했다. 기준은 소스789817e3/목록30f4ea1d, 후보는 소스5a39cb4e/목록564e73b1에 귀속한다. 후보의 빈 일정은 요청/장애 자격이 없는 별도 IDLE 기록이며 기존 진단 실행을 소급 성공으로 바꾸지 않는다.

| 쪽 | 지연 | 예외 | 종료 | 각 사례의 새 Pod |
|---|---|---|---|---|
| 기준 | STOP/CONT·HTTP200 | 원 RPC 경계 예외·HTTP500 | pidfd SIGKILL/EXITED·HTTP500 | 정상 요청200·새 Pod/세대 |
| 후보 | STOP/CONT·HTTP200 | 원 RPC 경계 예외·HTTP500 | pidfd SIGKILL/EXITED·HTTP500 | 정상 요청200·새 Pod/세대 |

여섯 사례의 원자료 대조 gap은 0이다. 전체 TP2 그룹 수락·탐지/상위 전달 기한·공통 판정 입력 연결·생산자 등록·M-A3 전체 완료는 남아 있으며 scope_complete/source_authenticated/engine_group_verified/producer_registered=false다. 같은 PID namespace inode 숫자가 재사용된 경우도 있으므로 Pod UID·PID/start ticks·전체 namespace/boot 문맥을 함께 대조한다.

## 제품 입력 사전 결합 후속 (2026-11-15)

`tools/rank_rpc_product_binding.py`는 현재 정확 검토 소스의 준비 자산과 sealed ENV·제품 target을 대조해 private `guard-rank-rpc-product-binding/1`을 새 디렉터리에 만든다. ENV의 정확 Ref·target 정규 JSON SHA·prepared manifest bytes SHA·세션·Pod UID·정규 container ID·restart count를 settings의 세션 키 HMAC에 묶는다. ENV는 MVP 표의 해당 이미지/엔진/판본, TP2/PP1/DP1·GPU 두 개 이상이어야 하며 target의 ENV Ref·이미지·실제 범위 필드가 일치해야 한다. 과거 SDK bytes·잘못된 hash·symlink·기존 출력 덮어쓰기는 거부한다. 비밀 키를 반환하거나 출력하지 않는다.

actor의 `--for-common-input`은 정상 calibration 전에 별도로 공급한 `control-product-binding.json`을 요구한다. 기동 시 외부에서 제공하는 `GUARD_POD_UID`, 정규 `GUARD_CONTAINER_ID`, 십진 `GUARD_RESTART_COUNT`와 정확 대조하고 ARM/2·lab target/2 안에 같은 결합을 넣는다. source control은 원 CALL을 멈추기 전에 독립적으로 같은 서명 파일과 기동 범위를 재대조한다. 이는 외부 예상값의 결합이며 현재 Kubernetes 소유권·생산자 인증의 증명이 아니다. 실제 기동 전 현재 Pod 조회/자격 관문은 여전히 필요하다. 기존 ARM/1 lab 재생·nonce/원 프레임/요청/시계·물리 신호/lease 관문은 유지한다.

후속 변환기의 선행 조건인 `require_case_binding()`은 ARM/2·target/2·외부 예상 결합·target 정규 해시를 요구한다. 이 함수만으로 HMAC/원 snapshot/물리 영수증/판정 자격이 검증된 것은 아니다. 기존 여섯 GPU 사례는 ARM/1이므로 제품 ENV/target을 사후 붙이지 않고 `RANK_RPC_PRODUCT_BINDING_REQUIRED`로 거부한다. 여섯 실제 기록과 기존 부분 대조는 원 소스에 그대로 귀속한다.

이번 준비 도구는 네트워크·기동·장애 주입·공통 입력 생성·생산자 등록을 하지 않는다. 성공 시에도 live_target_verified/input_preflight_passed/engine_executed/scope_complete/producer_registered는 false다. CPU 시험은 EXAMPLE 입력과 시험 소유 자식만 사용한다. 새 SDK의 실제 GPU 실행·제품 rank-fault 사건 변환·TP2 인과 그룹·탐지/상위 전달 기한·새 소유자 복구 수락·등록은 미완료다. M-B4는 보관하고 기존 native24 TP1/같은 소유자 복구 고정값·판정 기준·등록표를 넓히지 않는다.

새 미결합 준비물6개와 기존 원자료 재검증은 [현재 준비 보고서](ma3-product-binding-preparation-20261115.json)에 구분했다. 직접151개·기준 이미지 Python ABI95개가 통과했고 기존6사례/329개 원 기록의 gap0과 제품 사후 결합 거부를 확인했다. 새 소스의 실제 엔진 실행은 없다.

## RPC 공통 부분 입력 후속 (2026-11-15)

`vllm_rank_ipc_capture`로 기존 도구의 원 snapshot 검증3함수를 AST 동일하게 옮기고 도구의 import API를 유지했다. 제품 adapter/collector가 개발 도구에 의존하지 않게 했다. 엔진에 넣는 SDK14파일·기존 source 고정값·등록표는 이 이동으로 바뀌지 않는다.

`vllm_rank_rpc_input`과 미등록 `rank_rpc_fault_producer`는 외부 예상 ENV/target/prepared/session/Pod 범위, ARM/2 사전 결합, 모든 선택 제어 HMAC·claim·READY, 정확 원 CALL/상위 프레임·worker·요청 HMAC, 별도 물리 영수증·단조 시계를 대조한다. 전체 근거가 맞는 장애 적용만 `fault_applied`로, 지연의 실제 STOP/CONT와 같은 원 CALL의 SOURCE RELEASE가 모두 있는 경우만 `fault_released`로 변환한다. 기존 metadata/정책의 delay/death/exception 이름을 그대로 보존하며 별도 communication-stall 분기를 대신하지 않는다. 공통 `/1`·`/4`와 기존 수락 기준을 유지한다.

HTTP200/500은 클라이언트 구성요소 관측으로만 기록한다. 현재 원 관측으로 탐지/상위 전달/복구의 전체 인과 경로가 증명되지 않아 detected/client_delivered/recovered를 만들지 않는다. 다른 원 기록의 gap과 SOURCE RELEASE 부재는 확인된 적용 사실과 함께 공백으로 보존한다. 죽은 프로세스의 SIGCONT/해제는 허용하지 않는다. 모든 source_ended/scope_complete/인증/그룹/등록은 false다. 같은 신원을 시험 중 임시 등록해도 누락된 사건·불완전 근거 때문에 complete가 될 수 없다. 실제 등록표에는 추가하지 않는다.

`tools/rank_rpc_fault_inputs.py`는 현재 소스의 sealed ENV·target과 새 준비물을 먼저 검증한 뒤 새 디렉터리에 `common-input.json`·변환 요약을 만든다. 기본은 `/4`이며 예시 ENV는 EXAMPLE로 남긴다. 변환 자체를 새 엔진 실행/실환경 사전 점검/제품 EVD·RES·RPT 생성으로 세지 않는다. 기존 ARM/1 GPU 여섯 사례에는 실제 새 제품 입력을 사후 붙이지 않는다. 현재 실환경 수집·전체 TP2 인과 그룹/기한·같은 소유자 복구 수락·새 소스 GPU 대조·등록은 계속 미완료다.

최종 host 변환은 직접187개·기준 이미지 Python ABI131개·현황/경로/소스552파일 관문을 통과했다. [후속 미결합 준비물](ma3-rpc-common-preparation-20261115.json)은 엔진 SDK14 bytes가 앞선 후보와 같으며 새 host 변환 신원과 미완료를 구분한다. 실제 ARM2 원 기록의 제품 부분 입력·전체 M-A3 완료는 아직 없다.

## 제품 파일 소비 경로 후속 (2026-11-15)

기존 rank_failure 실행기 구현에 `common-input` 파일 소비 경로를 연결한다. 설정의 `input_mode="common-records"`는 명시적으로 파일만 소비하며 lab_activation/record_attestation과 섞지 않는다. 기존 Kubernetes native 경로/소스 자격·현재 팩·판정 규칙은 그대로다. 실행 결합은 이 경로에서 선언 JSON 준비물의 정규 해시를 계산하고 기존 ENV/target/유효 기간/정책 검사를 유지한다.

기존 공통 소비 기반에 LIF-08/rank-fault를 연결해 정확 공통 기록과 소스 지문·공백·위반 후보를 EVD artifact/activation_steps에 보존한다. 현재 owner·준비물 runtime·원 관측시각·모든 rank 정상/상위 전달은 미확인으로 남기며 `activation_scope_complete=false`다. 파일 소비 자체는 실제 엔진 요청이 아니고 출처는 EXAMPLE 또는 최대 DECLARED다. 새 기록의 위반 후보를 확인된 보안 FAIL로 바꾸지 않으며 기존 독립 FAIL은 보존한다. 이 경로의 EVD→RES→RPT는 UNKNOWN이며 실제 GPU 실행/수락/등록을 대신하지 않는다.


제품 파일 소비 경로와 새 main의 점검별 설정 통합 후 직접200개가 통과했다(pytest41.56초·벽시계41.876초). 팩 필수 관측만 EVD에 남기는 기존 규칙을 유지하며, 출처 표시는 `activation_steps`와 원 기록 artifact에 남는다. 새 전체 CI는 미실행이다. [현재 후속 준비물6개](ma3-rpc-product-preparation-20261115.json)는 새 소스 목록에 결합했지만 미실행/미결합이다.

#277의 결정14 변경을 반영해 이후 선택 엔진은 두 쪽 V1이다. 과거 기준 V0 GPU 결과와 예외는 이력이며 새 V1 자격으로 이월하지 않는다. SDK bytes가 앞선 후보와 같다는 사실은 선택 모드의 함수/정확 핀·GPU 자격을 증명하지 않는다. 현재 owner/엔진 모드·새 ARM2 실제 기록·TP2 정상/탐지/상위 전달/복구 수락·등록은 남아 있다. M-B4는 보관한다.
