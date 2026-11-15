# 기준 V1 초기화 원관측 후보 (2026-11-15)

M-A6의 LIF-07·09 후속이다. 제품 실행기·준비 profile·생산자 등록을 열지
않는다. `/14`·`/15` 및 기존 Worker callback의 bytes, 판정·정책·팩은 보존한다.
로컬 Python·pytest는 실행하지 않았다. 아래 회귀는 PR Linux CI 대기다.

## 실제 소스와 의미

읽기 전용 기존 clone의 HEAD는
`5204ff5c3feeb96e8a6eea65dfcb78395f90d4d8`이고 Git 상태는 깨끗했다.
기존 Apache-2.0 fixture에 정확 bytes를 추가했다. 두 파일은 태그 소스
후보이며 설치 이미지 bytes 또는 GPU 관측으로 승격하지 않는다.

| 파일 | 크기 | SHA-256 |
|---|---:|---|
| `vllm/v1/executor/multiproc_executor.py` | 14086 | `5db418332360f43e2a9718f62d8ec5d856d554b8d0d27d14f053fb3392d33239` |
| `vllm/v1/worker/gpu_worker.py` | 11991 | `7bc08a4e47bac5e1d68e68f540f014e3f9244c0f8d773f0dd7f5b959da5afb7a` |

- `wait_for_startup(proc, ready_path)`은 정적 함수다. READY 문자열과 IPC
  handle을 받아 handle 자체를 반환한다. rank·pid가 있는 WorkerProcHandle
  목록을 반환하는 `wait_for_ready`와 대응시키지 않는다. handle의 내용을
  관측하거나 임의로 역직렬화하지 않는다.
- `WorkerProc.__init__`은 READY와 응답 MQ handle을 **먼저 보내고**
  `self.worker.init_device()`·`load_model()`을 호출한다. 따라서 transport
  READY 반환은 모델 준비 완료가 아니다.
- `_init_executor`는 worker 생성 뒤 broadcast·response MQ의
  `wait_until_ready()`를 기다린다. 자식 준비 완료와 상위 집계의 전체 인과는
  소켓 핸드셰이크 반환만으로 증명할 수 없다.
- baseline Worker의 `init_device`·`load_model`·`compile_or_warm_up_model`은
  직접 함수다. 원본 callback이 이해하는 이 세 지점만 재사용한다.
  `initialize_cache(KVCacheConfig)`를 기존
  `initialize_from_config(...)`로 이름만 바꾸지 않는다. dummy 인자 부재와
  graph 설정 차이는 원본 callback의 gap으로 보존한다.

## 구현 경계

`vllm_baseline_startup.StartupCandidate.install()`은 이미 적재된 명시적 module의
정확 파일·원 함수 code·descriptor를 확인한 뒤 직접 감싼다. 기존 `_bytes`의
file identity/ctime 검사, `_codes`·`_function`, `code_digest`,
`WorkerReadinessObserver`와 `closed_callback`을 재사용한다. 읽은 source를
허용된 실행 interpreter로 직접 컴파일해 현재 원 함수와 비교하며 계산하지
않은 bytecode 지문을 표에 넣지 않는다. CPython 3.12.3·3.12.8 POSIX만 받는다.
원본 센서의 ABI gate를 넓히지 않는다.

같은 프로세스·세션·생명주기 binding에서 CALL/RETURN/RAISE와 lexical parent
call을 기록한다. collector는 constructor 구간 안의 동일 rank Worker body
반환 또는 예외를 대응시킨다. 이는 LIF-09의 구성요소 원결과와 **자식
constructor 결과**에 대한 실질적인 부분 관측이다. init/load 실패가 child
constructor 예외로 전파되는 사실과 실패를 삼킨 constructor 반환 후보를
구분한다. 상위 프로세스 전체 성공 또는 보안 FAIL로 바꾸지 않는다.

hook은 원 함수를 한 번 호출하고 반환 객체·원 예외 객체를 보존한다.
관측 sink·snapshot 실패로 엔진 결과를 대체하지 않는다. 종료는 자신이
설치한 wrapper만 복원하며 다른 변경이 있으면 복원 미확인을 표시한다.
owner·함수 강한 참조는 후보 설치 수명 동안만 보관하고 close에서 해제한다.
모델 본문·handle 내용·exception 메시지를 기록하지 않는다.

이 후보는 import hook·spawn/fork 설치·Kubernetes 운반을 제공하지 않는다.
생산자가 프로세스별 실제 context와 bounded journal을 제공해야 한다.
부모 wrapper를 fork로 물려받아 자식 관측인 것처럼 쓰지 않는다.

## 회귀와 남은 연결

`test_ma6_baseline_startup.py`는 정확 fixture 함수 AST를 native import 없이
컴파일한다. source 자격 검사는 함수의 전체 code가 정확 source와 같은지
다시 확인한다. 고정 실제 constructor와 load_model 본문에 inert I/O 경계를
붙여 READY가 먼저 전송되고 load 실패가 constructor 예외로 전파되는지
검사한다. 기본·미지원 원본 callback record를 실제 consumer에 넣고,
호출 쌍·scope/source 변조 거부·불투명 handle 비접근·원 예외 보존·sink
실패·wrapper 복원을 검사한다. 이 테스트는 GPU·엔진 실측이 아니다.

독립적으로 구현한 원관측 후보와 제품 경로의 활성화는 구분한다. 남은 것은
실제 worker 프로세스의 owner/generation journal 운반, parent `_init_executor`
집계와의 독립 인과 결합, model/KV/graph 전체 준비, 실패 자극·수정 기동
lineage, 외부 신규 요청 접수 oracle이다. 이 연결과 실제 이미지/ABI 확인
전에는 initialization executor의 기준 V1 gap을 그대로 둔다.
