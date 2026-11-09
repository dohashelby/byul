# 2차 설계 감사 — 보수 후의 실제 연결과 반복 실행

사용자의 기업용 목표는 엔진이 바뀔 때마다 영향평가→시험선정→격리실험→보고를 반복하는 것이다. 기능을 추가하거나 제품 코드를 수정하지 않고 현재 A worktree의 보수 코드까지 다시 감사했다. 앞선 D01~D12 보수에는 미완료 연결과 새 결함이 남아 있다. 남은 일을 GPU 실증만으로 요약할 수 없다.

대상은 `/Users/doha/Desktop/Buyl/byul-dev1-k8s`, HEAD `8402ff8947e78c85e75d76c62b0bd91d88cc0512`와 기존 미커밋 변경이다. `src/guard` Python 364파일의 AST 비교식·import 목록을 만들고 실행/비교/이월/기업 입력 경계를 추적했다. 아래 재현은 격리된 Python 프로세스, 명시적 EXAMPLE/DECLARED fixture, 소유 임시 파일, 메모리 patch를 사용했다. 실제 엔진·GPU·네트워크 실행이나 운영 대상 공격 결과가 아니다. 각 파일의 모든 함수를 전수 의미 검증했다는 뜻도 아니다.

P1은 이 제품 목표로 지원 범위를 넓히기 전에 수정할 항목이다. 현재 배포에서 취약점이 악용됐다는 등급이 아니다. 새 scoped 생산자와 receipt가 미등록이라는 경계는 그대로 유지된다. 예시 receipt를 넣은 재현을 실제 qualification으로 세지 않는다.

## 결과

| ID | 우선 | 실제 문제 | 확인 방식 |
|---|---|---|---|
| F01 | P1 | 새 생산자 등록값과 receipt가 생산자가 해시하는 자기 파일 안에 있어, 등록 작업 자체가 승인 대상 신원을 바꿈 | 등록 literal의 메모리상 bytes 치환 |
| F02 | P1 | native 센서는 비교용 필드를 전혀 채우지 않아, 같은 capture끼리도 NOT_COMPARABLE | 완료된 native EXAMPLE 경로 실행 |
| F03 | P1 | 한 worker의 rank가 None→0이면 writer/validator의 binding 키가 충돌해 전체 기록 거부 | 원 closed-call fixture의 rank 전환 |
| F04 | P1 | worker 집단을 flat TP rank 집합으로만 셈. TP 크기도 실제 기동 관측 대신 입력 목록 길이에서 만듦 | 4 worker→2 worker 모두 예시 complete=True |
| F05 | P1 | journal 상태가 오래된 순간에는 실제 drop이 있어도 capture가 gap 없이 종료됨 | 실제 로컬 queue·파일의 동기화 재현 |
| F06 | P1 | profile catalog를 통하지 않는 현재 등록 실행기에 vLLM/0.31 gate가 남음 | registry→실행기→runtime 추적, 메모리 프로파일 추가 재현 |
| F07 | P1 | 업데이트 이월 자격에서 baseline 설정만 검사. candidate 설정 변경을 검사하지 않음 | 기존 DECLARED 이월 fixture·preview spy |
| F08 | P1 | BYUL 환경 계약의 runtime_kind는 guard 모델에서 거부되고, 계약의 digest 형식은 managed launch에 부족함 | 공용 스키마/변환 코드 + 실제 모델·provider 거부 |
| F09 | P1 | 공통 모델 매핑이 HF org/name·40자리 Git revision을 강제함 | opaque artifact 신원의 sealed ENV 재현 |
| F10 | P2 | 실제 HTTP 실행과 reuse preview가 같은 조건을 다르게 해시함 | 실제 EXAMPLE collector 출력과 preview 대조 |
| F11 | P2 | 새 page 수집도 매번 처음부터 읽어 이전 4096행이 새 시험을 밀어냄 | 4100행의 실제 로컬 파일 수집 |
| F12 | P2 | 새 pack migration을 두 번 적용하면 지원되지 않는 profile 이름을 만듦 | 원 팩33→34→35 실제 생성·지원 조회 |
| F13 | P2 | 기업 정책 유효 기간은 import에서만 검사하고, 실행이 받는 POL에는 표현하지 않음 | 공용 계약→importer→POL→실행 경계 추적 |
| F14 | P1 | 소유 lab 허용과 EXAMPLE 자료 출처가 같은 bool에 묶임 | 실제 transport 생성 로직과 lab 필수 실행 분기 확인 |

## F01 — 새 등록을 완료할 수 없는 자기 참조

[scoped_consumption.py:23](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/scoped_consumption.py:23)의 `QUALIFICATIONS`/`SUPPORTED_PRODUCERS`는 같은 파일에 있다. [producer():31](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/scoped_consumption.py:31)는 그 파일 전체를 신원 해시에 포함한다.

현재 source SHA를 계산한 뒤 그 SHA를 승인 dictionary literal에 넣는 bytes를 메모리에서 만들었다. 승인을 추가한 source의 신원은 즉시 다른 SHA가 됐다. 승인한 값과 현재 값이 맞지 않는다. receipt에 producer SHA를 넣어 같은 파일을 고쳐도 같은 문제가 생긴다. 등록을 승인하는 별도 외부 loader도 현재 없다.

기존 시험은 dictionary/tuple을 메모리에서 patch하므로 파일 bytes가 변하지 않아 이 결함을 보지 못한다. GPU 측정 전에 독립 receipt artifact와 producer 실행 bytes의 경계를 먼저 고쳐야 한다. 기대 해시를 실행 중 관측값으로 자동 승인해서 해결하면 안 된다.

## F02 — native 증거가 버전 비교에 들어갈 수 없음

[engine_sensor._callback:515](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/engine_sensor.py:515)는 `EvidenceDraft`를 만들면서 `comparability`를 제공하지 않는다. [기본값](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/base.py:30)은 모든 비교 필드가 null이다.

실제 native executor 코드에 EXAMPLE transport를 연결했다. model inventory·GPU·driver를 ENV에 명시한 경우도 결과는 `COMPLETED`, 독립 위반 관측=True, 비교 필드 5개=null이다. 그 capture를 자기 자신과 비교해도 [compare:23](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/core/compare.py:23)에 따라 `NOT_COMPARABLE`이다. 현재 상태에서는 native 수집 성공을 버전별 회귀/개선 보고로 연결할 수 없다.

단순히 draft.config 전체를 비교 해시로 복사해도 안 된다. [config:523](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/engine_sensor.py:523)에 target/prepared/manifest SHA가 있어 두 실행의 출처 신원이 다르면 해시도 달라진다. 실제 trial 의미·모델·실행 조건과 실행 binding을 분리한 비교 계약이 native 경로에도 필요하다.

## F03 — rank 불확실성이 전체 증거를 없앰

writer의 [binding 키:209](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/scoped_consumption.py:209)는 `(role, rank, ticks)`다. validator의 [중복 키:72](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/scoped_consumption.py:72)는 rank를 제외한다.

같은 PID/시작 ticks에서 runner 호출의 rank=None, 다음 sampler 호출의 rank=0인 EXAMPLE closed-call을 넣었다. writer가 binding 두 개를 만들고 자기 validator가 `GPU_INPUT_BINDING_INVALID`로 전체를 거부했다. 정상 행·앞선 독립 위반 후보도 반환되지 않는다. GPU 수집 도구에는 기록 대신 contract_error만 남는다.

프로세스 세대 신원과 rank 관측 상태를 별도로 관리해야 한다. rank를 임의로 0으로 바꾸지 않고 불확실성을 gap으로 남기면서 원 행·독립 후보를 보존해야 한다.

## F04 — 여전히 하나의 TP 집단을 가정함

[coverage:136](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/scoped_consumption.py:136)는 관측 rank의 set만 비교한다. wire binding/receipt에는 TP group·replica·PP stage 등의 집단 신원이 없다. [수집 도구:122](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/tools/gpu_a_producers_collect.py:122)는 `tp_size`를 실제 실행 관측 대신 runner_scope 배열 길이로 만든다.

예시 receipt 아래에 rank 0/1을 가진 worker 집단 두 개, 총4 worker를 넣었다. 한 집단을 통째로 없애 2 worker만 남겨도 같은 receipt로 complete=True였다. 이는 실제 PP/DP 실행 재현은 아니며, 현재 표현이 같은 번호의 집단을 구분하지 못한다는 계약 반례다. production receipt는 여전히 비어 있다.

기대 scope를 실제 launch/bootstrap/world-size 및 actor group 신원에 결합해야 한다. TP-only로 지원할 경우에도 다른 topology를 명시적으로 거부할 입력이 필요하다. 향후 topology를 추가할 때 기존 TP receipt를 재사용하지 않아야 한다.

## F05 — 논리적 trial 종료와 파일 prefix 종료가 다름

[JournalSink.__call__:140](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/adapters/observer_runtime.py:140)은 queue overflow를 메모리에만 올리고 즉시 상태 파일에 게시하지 않는다. 상태 게시도 같은 writer thread가 수행한다. [reader:52](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/runtime/assets/callback_page.py.txt:52)는 게시된 counters만 본다.

로컬 writer가 첫 행을 쓴 뒤 상태를 갱신하기 직전에 잠시 멈추도록 했다. queue를 채워 실제 drop=1을 만든 다음 수집했다. 파일의 상태는 여전히 accepted=written=dropped=0이었다. 수집 결과는 `truncated=False`, gaps=[]였다. 시험 종료 후 해당 thread는 해제·종료했다.

현재 source_ended는 logical capture 종료/flush ACK를 증명하지 않는다. trial epoch·각 producer의 수신/기록 watermark·유실 상태를 묶는 종료/배리어 계약이 필요하다. 살아 있는 엔진에서 atexit gap flush를 기다리는 방식도 그 계약을 대신할 수 없다.

## F06 — 0.31 앵커가 현재 실행 경로에 남음

AST inventory에서 adapters 밖 literal 버전 비교는 25파일/27개였다. 모두 결함이라는 숫자는 아니다. 하지만 [registry:78](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/registry.py:78)의 `initialization:kubernetes`는 현재도 [kubernetes_initialization:16](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/runtime/kubernetes_initialization.py:16)의 `engine=vllm && version=0.31.0` gate를 호출한다. 철회·초기화·복구·worker readiness 등에도 같은 지식이 있다. 이들은 과거 증거 reader만이 아니라 등록된 신규 실행 경로다.

catalog에 EXAMPLE 0.32 항목을 메모리로 추가하고 base 생성 경계만 spy로 교체했다. catalog는 0.32에 available=True를 반환했지만 startup wrapper는 base에 도달하기 전에 거부했다. source qualification을 수행한 시험이 아니라, 공통 버전 gate와 catalog의 분리 상태를 확인한 시험이다.

새 버전을 무조건 허용하라는 뜻이 아니다. immutable 기존 implementation/receipt를 보존하면서, 새 실행의 feature→profile→implementation 선택을 실제 factory까지 연결해야 한다. 지금은 일부 경계만 이동해 다른 엔진/버전을 추가할 때 공통 파일 수정이 계속 필요하다.

## F07 — 이월은 baseline 설정에만 맞춰 검증됨

[_effective_config:128](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/core/change_selection.py:128)는 update에서 baseline 설정만 돌려준다. [build_context:317](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/core/change_selection.py:317)도 baseline ENV로 preview를 만든다. candidate 설정 차이는 input_diff의 별도 선정 trigger에도 없다.

기존 DECLARED 이월 계약 fixture에 candidate의 의미 있는 옵션만 바꿨다. candidate를 검사했다면 다른 config 해시를 내는 preview를 사용했지만 실제 선정은 old baseline 옵션만 한 번 조회하고 이전 PASS를 이월했다. 실제 운영 결과가 잘못 이월됐다는 측정은 아니며, 해당 검사에 적합한 preview가 존재할 때의 선정 로직 반례다.

이월 범위를 어느 side에 적용하는지 명시하고 candidate의 실제 절차/fixture/정책/환경 변경까지 대조해야 한다. preview가 항상 None인 현재 경로에서는 이 결함이 불필요한 재시험에 가려질 수 있다.

## F08 — 공용 환경 입력을 그대로 실행할 수 없는 경계

두 가지가 확인됐다.

1. [공용 계약:30](/Users/doha/Desktop/Buyl/byul-dev1-k8s/contracts/schemas/environment.schema.json:30)은 `engine.runtime_kind`를 허용하고 runtime bundle exporter는 그 값이 필요하다. [importer:158](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/tools/import_byul_inputs.py:158)은 engine dict를 그대로 [EngineInfo](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/models/objects.py:70)에 넣는다. 이 모델은 runtime_kind를 받지 않아, 같은 신원에 이 합법 필드만 넣어도 extra_forbidden이다. 실제 importer에서는 GUARD_INPUT_MODEL_INVALID 경계다.
2. [공용 계약:32](/Users/doha/Desktop/Buyl/byul-dev1-k8s/contracts/schemas/environment.schema.json:32)의 image_digest는 `sha256:...` 형식이다. [managed provider:98](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/adapters/engine_provider.py:98)는 ENV 자체에 `repository@sha256:...`를 요구한다. cfg에 repository reference를 줘도 ENV 검사에서 먼저 거부한다.

공용 원본을 사람이 편집해 우회하면 원 Ref가 바뀐다. 원 계약 신원·실제 launch locator·runtime용 필드를 구분해 번역/결합하고 정확한 원본 Ref를 보존해야 한다. 외부 target을 별도로 만드는 우회는 있지만 기업 입력→managed 격리실험의 기본 경계가 연결된 상태는 아니다.

## F09 — HF 가정이 공통 모델 별칭 경계에 남음

[model_mapping:24](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/model_mapping.py:24)는 repository_id에 정확히 `org/name` 모양을 요구하고 revision은40자리 hex로 제한한다.

유효하게 seal된 ENV에 `registry.internal/acme/engines/customer`, `build-2026.11.08`, inventory SHA를 넣고 API 별칭을 매핑하면 MODEL_MAPPING_INVALID다. 물리적 engine/모델 실행 시험은 아니다. 매핑을 생략하는 경로가 있지만, 별칭이 필요한 비-HF artifact에 공통 매핑을 사용할 수 없다.

HF source revision, OCI artifact digest, 사내 model build, compiled engine artifact를 opaque identity와 artifact-kind별 검증으로 구분해야 한다. HF 검증을 지우는 대신 해당 검증을 HF provider에 남겨야 한다.

## F10 — 실행/이월 projection이 서로 다름

실제 HTTP [config:403](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/http_common.py:403)는 `http_probe_profile=http-probe/2`를 넣는다. [reuse preview:55](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/reuse.py:55)는 기본값을 넣지 않는다.

같은 fixed-route EXAMPLE collector를 완료한 뒤 같은 입력으로 preview했다. 두 config의 차이는 settings의 http_probe_profile 한 개였고 해시가 달랐다. 원 EVD가 갖춰져 있어도 기본 설정 그대로는 이월 자격을 잃는다. 실행/preview가 별도 함수에서 default를 조립하는 구조를 합쳐야 한다.

최초 재현 스크립트는 fixed routes를 정렬하지 않아 실행이 ABORTED됐다. 이를 제품 결함으로 세지 않았다. 정렬한 후 COMPLETED를 확인하고 field별 대조를 보존했다.

## F11 — page를 도입해도 실행 간 cursor는 유지되지 않음

[iter_callback_pages:136](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/runtime/engine_http.py:136)는 수집 호출마다 snapshot/cursor를 None으로 시작한다. [assemble:6](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/runtime/callback_pages.py:6)은 전체 session의 앞4096행만 projection한다. trial barrier/window의 필터링은 그 뒤다.

4100행을 쓴 실제 로컬 파일에서 끝의 새4행은 projection에 들어오지 않았다. 원 page는 보존되고 truncated=True이므로 정상 PASS로 잘못 승격한 반례는 아니다. 하지만 같은 prepared session으로 반복 수집하면 새 시험이 과거 로그에 밀려 계속 incomplete가 된다.

수집 page 안의 cursor와 시험 간 continuation/epoch를 구분해야 한다. 해당 trial의 원 범위를 먼저 고정하고 그 범위에 projection budget을 적용해야 한다. cap을 올리는 것으로 해결하지 않는다.

## F12 — migration이 이전 v1 팩 한 번만 가정함

[sensor_binding:39](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/runtime/sensor_binding.py:39)는 기존 profile에 suffix를 무조건 붙인다. 원 팩33→34는 지원되지만34→35는 `...-run-binding-v2-run-binding-v2`를 만들고 executor가 미지원으로 거부했다.

재실행/기존 이행 여부를 검사하는 versioned migration이 필요하다. 이미 v2인 팩은 안전하게 no-op 처리하거나 명시적인 unsupported-source-version으로 거부해야 한다. 지원할 수 없는 팩을 성공적으로 생성해서 반환하면 안 된다.

## F13 — 기업 정책의 수명이 import 시점에 고정됨

[importer:141](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/tools/import_byul_inputs.py:141)는 valid_from/valid_until을 검사한다. [생성되는 POL:163](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/tools/import_byul_inputs.py:163)와 [Policy 모델](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/models/objects.py:167)에는 그 기간이 없다. importer도 POLICY_VALIDITY_MUST_BE_RECHECKED_AT_EXECUTION 한계를 명시한다.

현재 실행은 provenance sidecar를 필수 입력으로 재검증하지 않는다. 유효할 때 import한 POL을 기간이 지난 뒤 다시 넘기는 것을 core가 기간 정보로 거부할 수 없다. 이는 source 확인 결과이며 실제 기업의 만료 정책으로 엔진을 실행한 결과는 아니다.

반복 업데이트용 실행 계약에 원 정책의 유효 시점/폐기/현재성 확인을 포함해야 한다. 과거 RES를 재판정하는 as-of와 신규 실행의 현재 유효성 검사를 구분해야 한다.

## F14 — 격리된 실제 실험과 합성 자료를 구분할 수 없음

[engine_http:73](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/runtime/engine_http.py:73)는 runner 주입이나 ENV의 EXAMPLE 여부와 함께 target.lab만으로도 process_observation_origin을 EXAMPLE로 만든다. REQ-19/20 및 일부 상태 변경 실험은 engine_sensor에서 lab=True를 필수로 한다.

DECLARED ENV, runner 주입 없음, lab=True로 실제 transport 생성 코드를 통과시키면 source 등급은 EXAMPLE다. constructor만 확인했으며 엔진/I/O는 실행하지 않았다. 향후 source·GPU 관측이 검증돼도 이 조건 자체는 남는다.

lab 소유권/위험한 challenge 허용, isolated deployment 여부, synthetic/mock 자료 여부, 관측 출처/qualification을 별도 축으로 표현해야 한다. 과거 EXAMPLE 증거를 소급 MEASURED로 바꾸는 수정은 피해야 한다.

## 우선순위와 수락 조건

1. **F01/F03:** 실제 release artifact로 producer 등록→재로딩 후 같은 신원인지 검사한다. rank 불확실성이 전체 원 행/독립 FAIL을 지우지 않는지 검사한다. GPU 없이 가능하다.
2. **F04/F05:** 실제 기동 scope와 group/actor 세대, epoch watermark/종료 ACK를 새 계약에 묶는다. 현재 TP-only 허용 범위를 입력으로 강제하고 다른 topology는 거부한다.
3. **F02/F06/F07/F10:** profile→factory→collector→비교/이월을 하나의 작은 실제 경로로 확인한다. metadata registry의 자체 일치만 검사해서는 부족하다. baseline/candidate의 의미 있는 조건은 양쪽 모두 검사한다.
4. **F08/F09/F13/F14:** 기업 원본 계약→guard 입력→isolated 실행→증거 등급→보고의 경계를 연결한다. 원본 문서/Ref를 고치거나 정책값을 임의 채워 우회하지 않는다.
5. **F11/F12:** 같은 프로세스/준비 자산을 두 번 이상 사용하는 수명주기 시험을 넣는다. 첫 생성/첫 수집/첫 migration 통과만으로 반복 사용 가능하다고 세지 않는다.

TensorRT-LLM은 추가로 runtime backend/target 및 공용 runtime_kind enum에 아직 없다. update의 HTTP 어댑터 등록을 전체 솔루션의 TRT 지원으로 집계하면 안 된다. 이 미구현 범위만으로 SGLang 확장을 포기할 근거가 되지는 않는다. 공통 계약과 실제 선택 경계를 먼저 바로잡는 일이 필요하다.

## 검증 기록과 보존

- 관련 기존 시험204 passed / 1 skipped, pytest2.91초. 새 assert/skip을 추가하거나 기존 시험을 수정하지 않았다. 이 통과가 위 반례를 부정하지 않는다.
- 원자료: [architecture-audit-2](/Users/doha/.codex/visualizations/2026/11/08/01a1b99a-d7c6-79e0-8db2-4cb4b4cc71ed/architecture-audit-2). `inventory.json`, `anchors.json`, `probes.py/json`, `native-full-inputs.json`, `routing-and-origin.json`, `followups.json`, `preview-config-details/config-differences.json`, `regression.*`, `before.json`.
- 예시 receipt, hypothetical profile 추가, publication pause, synthetic preview는 해당 프로세스의 메모리에만 적용했다. producer 등록·실제 지원 목록·기존 파일을 바꾸지 않았다.
- 제품 source/pack/tools/tests/contracts/platform API의 시작 snapshot1268파일을 재대조해 bytes 변경0을 확인했다. 이번 저장소 변경은 이 감사 문서와 PROGRESS 기록뿐이다. 기존 증거·판정·source pin 보존, 커밋·push·GPU·AWS·다운로드 없음.
