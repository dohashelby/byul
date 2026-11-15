# 기준 V1 초기화 원관측 후보 (2026-11-15)

M-A6의 LIF-07·09 후속이다. 제품 실행기·준비 profile·생산자 등록을 열지
않는다. `/14`·`/15` 및 기존 Worker callback의 bytes, 판정·정책·팩은 보존한다.
로컬 Python·pytest는 실행하지 않았다. 아래 회귀는 PR Linux CI에서 확인한다.

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

직접 candidate API는 import hook이나 journal을 소유하지 않는다. 아래 `/31`
composition이 기존 lifecycle 설치·Writer·capture 경로에 이를 연결한다. 부모
wrapper를 fork로 물려받아 부모 context로 자식 관측을 기록하지 않는다.

## 회귀와 남은 연결

`test_ma6_baseline_startup.py`는 정확 fixture 전체 source를 컴파일하고
그 원 code object로 함수와 정적 descriptor를 구성한다. native import·모듈
본문·annotation은 실행하지 않는다. source 자격 검사는 함수의 전체 code가
정확 source와 같은지 다시 확인한다. 고정 실제 constructor와 load_model 본문에 inert I/O 경계를
붙여 READY가 먼저 전송되고 load 실패가 constructor 예외로 전파되는지
검사한다. 기본·미지원 원본 callback record를 실제 consumer에 넣고,
호출 쌍·scope/source 변조 거부·불투명 handle 비접근·원 예외 보존·sink
실패·wrapper 복원을 검사한다. 이 테스트는 GPU·엔진 실측이 아니다.

독립적으로 구현한 원관측 후보와 제품 경로의 활성화는 구분한다. 남은 것은
실제 worker/부모 세대의 독립 인과 결합, parent `_init_executor`
집계와의 독립 인과 결합, model/KV/graph 전체 준비, 실패 자극·수정 기동
lineage, 외부 신규 요청 접수 oracle이다. 이 연결과 실제 이미지/ABI 확인
전에는 initialization executor의 기준 V1 gap을 그대로 둔다.

## 기존 lifecycle 경로 연결 `/31`

`vllm-native-lifecycle-metadata/31`은 `/27`의 정확 snapshot 행 8개에 startup
두 모듈을 더한 **별도 composition**이다. `/27` template·render 의미와 기존
`/14`·`/15`·`/19` 등 원본 bytes는 보존한다. 기능 추가 없이 profile만 복제한
것이 아니라 새 `BASELINE_STARTUP` 사건과 엄격한 parser를 가진다.

- 준비는 기존 `lifecycle.prepare_assets`·`load_prepared`를 사용한다. 원본
  helper 6개와 candidate를 각각 고정 SHA로 묶고, 다시 해시한 변조 manifest도
  source와의 대조에서 거부한다. 실행 전에는 디렉터리·파일을 nofollow로
  열고 path/fd의 dev/inode/mode/uid/size/mtime/ctime 안정성과 SHA를 확인한다.
- 새 extension은 `/27` template 뒤에만 붙인다. private dependency의 from
  import를 명시된 객체로 연결하며 source bytes·global importer·sys.modules를
  바꾸지 않는다. base의 검증된 CPython 3.12.3·3.12.8 계약을 재사용하고
  callback·helper는 정확 source를 현재 interpreter로 직접 컴파일해 비교한다.
  원본 센서의 configure/install 또는 runtime 표를 우회해 실행하지 않는다.
- 기존 `CheckedLoader`가 exact native module을 적재한 뒤
  `Observer.instrument`에서 candidate를 붙인다. native 함수/메시지/출력
  동작을 바꾸지 않는다. 같은 기존 Writer가 bounded START/MODULE/event/END를
  기록하고 seal·프로세스 종료·상한 처리를 소유한다.
- binding은 Writer가 실제 읽은 전체 process context의 canonical SHA와
  session SHA다. fork 뒤에는 기존 Writer의 새 process context를 얻고 원본
  callback 상태와 상속된 parent call을 버린다. fresh spawn은 기존
  sitecustomize bootstrap으로 동일한 자산을 새 interpreter에서 설치한다.
- capture parser는 `/31`에서만 새 사건을 받는다. source의 verified MODULE
  선행, 정확 source SHA·runtime, header의 process/session binding, envelope
  clock을 대조한다. callback 기본·미지원 record의 null/gap 의미는 유지한다.
  capture의 기존 `summary.baseline_startup`에 구성요소·constructor 원결과를
  남긴다. 준비/실패/수락 판정 또는 등록된 공통 생산자 record로 바꾸지 않는다.

회귀는 실제 candidate→Writer→parse_recording→collector summary로 load와 child
constructor의 원 예외를 전달한다. 자산 bytes/재해시/심볼릭 링크·source MODULE·
process hash·runtime 변조 거부, fork binding 갱신·상속 parent 제거도 포함한다.
실제 원격 worker 운반·두 이미지 설치 bytes·GPU 실측은 PR CI의 대역 검증과
구분하며, 이 연결만으로 initialization executor gate를 열지 않는다.
