# 설계 앵커링 감사 — 2026-11-08

사용자 요청은 특정 버전과 엔진에 묶여 이후 대규모 수정이 필요한 구조를 찾는 것이다. A 기능 추가를 중단하고 현재 구현을 감사했다. 목표는 기업이 엔진을 반복 업데이트하면서 영향평가→시험선정→격리실험→보고를 재사용하는 제품이다. 이 문서는 감사 결과이며 새 엔진 지원이나 수정 완료 선언이 아니다.

## 대상과 근거

- 작업 트리 `/Users/doha/Desktop/Buyl/byul-dev1-k8s`, HEAD `8402ff8947e78c85e75d76c62b0bd91d88cc0512`와 기존 미커밋 변경. 사용자 원본·B 소유 코드·과거 증거를 변경하지 않았다.
- `src/guard` Python 345파일의 AST/import/상수/엔진 문자열 목록을 생성하고, 실행 준비·센서·수집·변환·비교·정책·설치·CI의 관련 경로를 추적했다. 345파일의 모든 함수가 의미적으로 검증됐다는 뜻은 아니다.
- 156파일에 vLLM 문자열이 있다. 주석·진단·고유 어댑터도 포함하므로 이 숫자를 결함 개수로 사용하지 않는다. core/models/store/report에는 직접 엔진 문자열이 없어 재사용할 경계가 이미 존재한다.
- 런타임 재현은 격리된 Python 프로세스, 복사한 source fixture, 합성 입력, 일시적인 메모리 patch로 수행했다. 실제 GPU·SGLang·TensorRT-LLM을 실행하거나 새 모델을 내려받지 않았다. 실제 배포 취약점 검증과 구분한다.
- 원자료: [감사 산출물](/Users/doha/.codex/visualizations/2026/11/08/01a1b99a-d7c6-79e0-8db2-4cb4b4cc71ed/architecture-audit). `source-inventory.json`, `anchors.json`, `adapter-interface.json`, `probes.py`, `probes.json`, `multiworker-probe.json`, `comparison-probe.json`, `interface-and-schema-probe.json`.

P1은 반복 업데이트·다중 엔진 제품으로 확대하기 전에 해결할 항목이다. 특히 D01은 TP2 공통 입력 소비 전에 해결해야 한다. P2는 추가 지원을 쌓으며 수정비가 커지는 구조다. 확인된 사실과 예상 영향을 아래에서 구분한다. 전수 보안 검증이나 모든 잠재 결함의 부재를 보증하지 않는다.

## 결과 요약

| ID | 우선 | 앵커 | 확인된 문제 | 재현/근거 |
|---|---|---|---|---|
| D01 | P1·TP2 전 | 단일 worker/rank | request만으로 중복 제거해 다른 worker 행을 없애고 rank=0을 기록하면서 complete=True | 합성 2-worker 재현 |
| D02 | P1 | vLLM 실행 구조 | 공통 managed 준비·sandbox가 vLLM만 허용. EngineAdapter에 없는 vLLM 전용 메서드를 공통 호출자가 사용 | 거부·Protocol 재현 |
| D03 | P1 | 0.31·고정 이미지·단일 프로파일 | 버전/이미지/함수 지식이 센서 밖 수집·실행·Kubernetes 공통 경계까지 분산됨 | 버전 gate·source 주석 재현 |
| D04 | P1 | 구현 이름·검사 ID | 등록/정적 지원/실제 실행의 지원 판단 기준이 다름. 정적 능력 판단에 엔진·버전·모드가 충분히 들어가지 않음 | real/kubernetes 지원 차이 재현 |
| D05 | P1 | 공통 파일·전역 schema | 파일 하나의 변화가 7개 생산자 신원에 전파. 반대로 전역 schema 변경은 기존 생산자 신원에 반영되지 않음 | 7/8 fanout·메모리 schema 재현 |
| D06 | P1 | vLLM 소스 구조 | 공통 소스 diff 기본 repo·규칙·진입점 추출이 vLLM 전용. 엔진별 분석 provider가 없음 | 경로 분류·소스 확인 |
| D07 | P1 | 개발 checkout·tools/tests | 일부 제품 실행 경로가 tools 모듈과 과거 Git snapshot·tests 트리를 직접 요구 | 설치본 ModuleNotFoundError 재현 |
| D08 | P1 | 버전 신원을 비교 조건으로 사용 | 같은 시험 설정에 대상 버전 메타데이터만 달라도 config 해시가 바뀌어 비교 불가 | 순수 비교 함수 재현 |
| D09 | P1 | 기술 프로파일을 기업 정책에 혼합 | 센서 ENV/target/prepared/manifest SHA가 필수 정책값. 공통 판정에 vLLM collector 이름도 들어감 | 정책 정의·검증 코드 확인 |
| D10 | P2 | 최초 실험·팩 이력 | 일부 제품 검증은 고정 5점검, 현황 도구는 팩33/팩32 차이를 수작업 상수로 앎. CI의 신규 엔진/버전 지원 계약 검증이 부족 | 검증·현황·CI 코드 확인 |
| D11 | P2 | 짧은 배치 수집 | 파일/전송/사건 상한과 일괄 JSON 수집. 생산자 이후 요약만으로 앞단 유실을 복구할 수 없음 | 상한·수집 경로 확인 |
| D12 | P2 | 측정 대상 안의 무거운 센서 | hot callback에서 반복 파일 검증·동기 기록. 일부 CPU 보조 관측은 큰 parameter 해시도 수행 | 코드 확인, 성능 영향은 미측정 |

## D01 — 요청 단위 메모리가 worker·attempt 경계를 지운다

[변환기의 상태](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/gpu_input_producers.py:480)는 `received=set()`, `applied={}`를 요청 ID로만 관리한다. 다른 worker에서도 같은 요청·같은 값이면 [received](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/gpu_input_producers.py:519)와 [applied](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/gpu_input_producers.py:528) 행을 버린다. [binding 생성](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/gpu_input_producers.py:543)은 rank를 관측하지 않고 0으로 넣는다. V2 생산자도 같은 변환을 재사용하고 rank를 0으로 둔다.

기존 시험 fixture의 API 1개와 worker 112·113의 동일 소비 행을 넣었다. 출력은 worker112의 runner/sampler 행만 남고, worker113은 binding만 있다. 입력의 rank1 메타데이터도 출력에서는 0이다. 계약은 `source_ended=True`, `supported=True`, `complete=True`, `gaps=[]`를 반환했다. 이는 합성 입력에 대한 계약·변환 결함 재현이며 실제 TP2 측정 결과가 아니다.

단일 L4에서 통과한 생산자 등록이 TP2의 필수 worker/rank 소비 범위를 보증하지 않는다. 요청·attempt·worker 세대·역할·단계를 구분하는 키와 단계별 기대 rank 집합이 필요하다. PP 등의 sampler가 특정 rank에서만 실행되는 차이도 표현해야 하므로 모든 rank에 무조건 같은 단계를 요구해서도 안 된다. 모르는 rank를 0으로 채우지 말고 gap으로 남겨야 한다. 원 `/1` 기록을 다시 해석하거나 고쳐 쓰지 않고 새 계약/생산자로 이행해야 한다.

## D02 — 어댑터 교체가 전체 실행 경로 교체로 이어지지 않는다

[EngineAdapter](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/adapters/base.py:78)는 HTTP·버전·로그 수준의 인터페이스다. SGLangAdapter는 이 Protocol을 만족한다. 그러나 공통 호출자는 Protocol에 없는 `supports_template_profile`, `request_binding_contract`, `native_log_contract`를 사용한다. SGLangAdapter에는 이 메서드들이 없다. `launch_plan`도 공통 Protocol에 없으며 sandbox는 VllmAdapter의 것을 직접 쓴다.

[managed 준비](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/application.py:43)는 엔진 이름·설정 section을 vLLM으로 고정한다. 합성 SGLang ENV는 파일 조회나 엔진 실행 전에 `managed update needs an explicit vllm engine version`으로 거부된다. [sandbox](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/runtime/sandbox.py:252)도 stub/vllm만 받는다. HF snapshot과 vLLM CPU launch 옵션의 지식도 application에 있다.

SGLang 지원을 늘릴 때 어댑터 파일만 추가하면 된다는 경계가 성립하지 않는다. 엔진 launch/identity/지원 사실/소스 분석/관측 변환의 provider를 명시하고, Docker/Kubernetes 수명주기·소유권 검증과 엔진별 launch를 분리해야 한다. 기존 HTTP 공통 실행과 core의 중립 인터페이스는 유지할 수 있다.

## D03 — 버전 고정이 profile 데이터와 공통 코드에 섞여 있다

센서의 [SOURCE_SHA](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/adapters/vllm_lifecycle_sensor.py:39)와 함수 code 검증은 필요한 안전장치다. 문제는 profile 선택을 바꿔도 나머지 계층의 하드코딩이 남는다는 것이다.

- [lifecycle 수집](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/lifecycle.py:237), [engine_sensor 실행](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/engine_sensor.py:278), [KubernetesHTTP](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/runtime/kubernetes_http.py:350)에 0.31 조건이 반복된다.
- Pod/namespace 메타데이터를 읽는 [startup binding](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/runtime/kubernetes_startup_binding.py:10)도 0.31에 묶인다. 복제한 동일 입력에서 0.31은 API 조회까지 도달하고, 0.32는 API 호출 0회로 거부된다. 0.31 완전 성공 재현은 아니며 gate 위치를 확인한 것이다.
- [length cause](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/adapters/vllm_length_cause.py:23)는 데이터 파일로 분리했지만 파일명 하나를 고정 로드하며 다중 선택은 미구현이다. 엔진뿐 아니라 모델·revision·inventory·native library도 그 파일에 묶인다.
- [profiles 목록](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/adapters/vllm_lifecycle_profiles.py:37)은 센서 판본별 source/point/asset 이력을 Python 코드로 누적한다. 새 엔진 릴리스 지원과 센서/기록 schema 판본은 별개의 축인데 이를 선택하는 공통 registry가 없다.

복사한 검토 source에 주석만 추가해도 자격 검증은 거부됐다. 그 거부 자체는 올바르다. 버전 이름을 더 허용하거나 hash를 자동 갱신해서 해결하면 안 된다. 검토된 profile이 정확 engine artifact·backend·runtime·feature·증거 schema를 함께 고르고, 공통 수집 경계가 그 지원 결정을 소비하도록 해야 한다. 현재 코드 전체가 0.31만 지원하는 것은 아니다. HTTP 일부에는 0.32 검토 계약도 이미 있다.

## D04 — 지원 판단이 registry·준비 명령·실행기로 갈라졌다

[readiness](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/readiness_support.py:91)는 VllmAdapter를 직접 만들고, 대부분 `implementation == real`만 인정한다. Kubernetes 예외는 request_binding에 한정된다. 실제 등록된 `direct_route:kubernetes`를 REQ-01에 선택해도 정적 지원 설명은 UNSUPPORTED다. 같은 입력의 real은 SUPPORTED다. 이 재현은 해당 target의 실제 준비 완료를 증명하지 않지만 등록과 정적 지원 정의의 차이는 확인한다.

[EngineSensorExecutor.support](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/engine_sensor.py:190)는 검사 ID·collection_profile·구현 이름을 보고, [native_capability](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/engine_sensor.py:148)는 검사 ID만 받아 일부에 implemented=True를 반환한다. 엔진 이름·버전·이미지·실행 backend·rank topology는 이 판단의 입력이 아니다. 실제 실행은 뒤에서 0.31을 다시 확인한다.

코드 등록, 정적 적합성, 실제 실행 준비, 검증된 지원 범위를 한 boolean으로 합칠 수 없다. feature별 typed capability 결과와 미지원 사유를 만들고 planner/readiness/run/report가 같은 resolver를 사용해야 한다. real/kubernetes는 실행 transport 차이이며 엔진 지원 수준과 분리해야 한다.

## D05 — 생산자 신원의 변경 범위가 기능 경계와 다르다

[기존 생산자](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/gpu_input_producers.py:26)는 관측기와 공통 변환 파일 전체를 해시한다. V2도 그 공통 파일을 해시한다. 디스크를 수정하지 않고 해당 파일 읽기에 주석 bytes를 추가한 메모리 재현에서 등록 생산자 8종 중 7종의 신원이 함께 바뀌었다. 서로 무관한 공급/health/샘플링 보수가 여러 GPU 등록을 한꺼번에 무효화한다.

반대 방향의 결합도 있다. producer는 `contract.SCHEMA`를 동적으로 사용하지만 producer 신원에는 그 계약 파일을 포함하지 않는다. 메모리에서 SCHEMA만 `/999`로 바꾼 재현에서는 기존 producer 신원을 그대로 쓰며 `/999` 기록을 만들고 supported/complete=True를 반환했다. 이는 실제 공격/배포 결과가 아니라 `/2` 이행 시 전역 상수 변경이 낳는 구조적 함정이다. 별도의 전체 reviewed-source 검증이 존재한다는 사실도 함께 고려해야 한다.

신원과 검증 receipt를 producer 구현·필요 의존성·wire schema·지원 범위별 artifact에 결합해야 한다. 원 파일의 무결성 확인은 유지하되 무관한 기능까지 같은 release 단위로 묶지 않는다. 기존 `/1` reader/writer·등록 receipt는 동결하고 새 `/2` 경로를 명시적으로 추가해야 한다.

## D06 — 소스 변경 분석도 엔진 provider가 필요하다

[git_diff](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/adapters/git_diff.py:19)는 기본 cache/repo·규칙 파일·진입점 경로를 vLLM으로 정한다. CLI에서 다른 repo를 넘기는 것만으로 그 엔진의 분석기가 되지 않는다. 예시 경로 시험에서 `scheduler`라는 문자열은 SGLang 경로에서도 SCHEDULER로 분류되지만 SGLang cache/http server·TensorRT executor 경로는 분류되지 않았다. 엔진 실측이나 실제 해당 릴리스 소스 감사는 아니다.

미분류를 넓게 선정하는 기존 fallback은 보수적인 보호장치다. 다만 엔진별 분석 rule set·지원 revision·분류 근거가 없으면 매번 수동 규칙 보수나 넓은 재시험으로 돌아간다. 엔진별 ruleset을 선택·버전 고정하고 CHG에 ruleset 신원을 보존해야 한다. 경로 분류만으로 보안 영향이나 동적 실행 도달을 증명하지 않는 기존 경계는 유지한다.

## D07 — 일부 운영 경로가 저장소·과거 checkout을 제품 의존성으로 삼는다

[engine_sensor의 source-cpu 경로](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/engine_sensor.py:220)는 `tools.b_req18_slot`을 import한다. [frozen component 검증](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/engine_sensor.py:107)은 tools/tests 및 고정 과거 commit의 트리를 요구한다. wheel은 `src/guard`만 패키징한다.

별도 target에 설치한 현재 wheel에서 guard 모듈 import와 기본 capability 조회는 성공하지만 component 검증을 호출하면 `ModuleNotFoundError: No module named tools`다. source-cpu/참조 component 경로의 결함이며 현재 native callback 전체가 설치 불가하다는 뜻은 아니다. template_source_cpu에도 repo의 tools 파일을 요구하는 경로가 있다.

실행에 필요한 helper·profile·asset을 제품 또는 명시적 버전이 있는 sensor package로 배포해야 한다. tests/builders와 개발 worktree는 운영 의존성에서 제거하고 역사 재판정용 frozen reference는 별도 artifact로 보존한다. 설치 검증도 module import에 더해 광고한 실행 모드의 필요한 자원을 확인해야 한다.

## D08 — 버전의 출처 정보가 비교 조건 해시에 들어간다

[execution_conditions](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/runtime/comparison.py:41)는 일부 이름만 제외하고 설정 전부를 비교 조건에 넣는다. sampling은 [constraint_engine_version](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/sampling_validation.py:87)이 각 ENV 버전과 같아야 한다. HTTP 실행은 이 설정을 실제 comparability.config_sha256으로 사용한다.

고정 target·정책/fixture·하드웨어·옵션에서 constraint_engine_version 문자열만 0.31→0.32로 바꾼 순수 함수 재현은 `differences=[config_sha256]`, `PASS/PASS→NOT_COMPARABLE`을 반환했다. 실제 두 엔진을 실행한 결과는 아니며 의미가 같은 시험을 버전 metadata 때문에 비교 불가로 만드는 경로를 확인한 것이다.

대상 신원·출처·profile receipt와 동등 시험 조건을 분리해야 한다. 비교에서 엔진 버전만 제외하면 된다는 뜻은 아니다. 모델 bytes·실제 시험 입력·정책·하드웨어·의미 있는 옵션·관측 의미가 동등한지 확인하는 명시적 비교 계약이 필요하다. `core.compare`의 엄격한 동등성 검사는 유지한다.

## D09 — 기업 정책과 실행 기술 binding이 섞였다

[REQ-14 정책](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/packs/PACK-BASELINE/checks/REQ-14.yaml:19)은 sensor_environment/target/prepared/manifest SHA 네 개를 필수 policy_params로 둔다. [EngineSensorExecutor.validate_policy](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/executors/engine_sensor.py:199)가 이를 다시 요구한다. 엔진·target·수집 세션이 바뀌면 기업 보안 기준과 무관하게 정책 내용도 바뀐다. [declared judge](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/judges/declared.py:52)에는 `guard-vllm-native-info-v1`이라는 단일 collector 이름도 들어 있다.

기업이 허용 센서를 정책으로 명시하는 것은 유효할 수 있다. 문제는 실제 선택된 수집 profile·매 실행의 binding과 기업이 정하는 보안 허용 기준을 같은 필수 values 공간에 강제한 것이다. 다중 엔진·반복 실행 때 정책 생성/확정과 기술 변경이 불필요하게 결합한다. 기업 정책은 허용 기준을, PLAN/실행 manifest는 실제 target/profile/receipt binding을 소유하도록 구분하고 참조 무결성은 유지해야 한다. 기존 정책·팩은 소급 변경하지 않는다.

## D10 — 검증과 현황 관리가 최초 실험의 목록·이력에 묶였다

[update_acceptance.CHECKS](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/update_acceptance.py:19)는 최초 5개 ID를 고정한다. [validate_inputs](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/update_validation.py:99)는 정확히 그 집합만 받는다. 이는 전체 CLI가 5개만 실행한다는 뜻은 아니다. 현재의 acceptance 하네스를 범용 제품 검증으로 재사용하려면 PLAN과 profile의 수락 범위에서 검증 집합을 얻어야 한다.

[현황 도구](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/tools/checklist_status.py:79)도 CURRENT_PACK_REVISION=33과 팩32 이후 달라진 REQ-11을 상수로 안다. 새 팩에서 다른 정의가 바뀌면 사람이 이 이력을 다시 맞춰야 한다. 과거 정확 Ref/정의 hash에서 현행 정의와의 관계를 계산하는 편이 안전하다.

[기본 시험](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/pyproject.toml:36)은 실제 engine 시험을 제외하고, [기본 CI의 통합](/Users/doha/Desktop/Buyl/byul-dev1-k8s/.github/workflows/update.yml:96)은 Docker stub 중심이다. 외부 GPU 실증·receipt는 별도로 존재한다. 단위 시험 수나 wheel import 통과만으로 새 엔진/버전 지원을 승인하지 않아야 한다. advertised capability별 공통 계약 시험, 인접 버전 qualification, 실제 설치본 경로 검증과 receipt 발급 절차가 필요하다.

## D11 — 짧은 batch를 넘어서는 관측 보존 설계가 부족하다

센서는 [프로세스당 16MiB](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/adapters/vllm_engine_callback_sensor.py:91), callback 읽기는 [전체 14MiB](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/runtime/kubernetes_http.py:73), 공통 입력은 [4096 events](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/collectors/gpu_common_inputs.py:27)로 제한한다. 다른 native 프로파일에는 512행/256KiB 상한도 있다. 상한 초과를 gap/거부로 남기는 안전장치는 존재한다.

문제는 짧은 실험을 길게 하거나 worker를 늘리면 수집기의 batch 규모가 먼저 제한되고, 공통 record 단계에서만 요약해도 앞단에서 이미 잘린 raw 사실을 복원할 수 없다는 것이다. 상한을 무조건 올리지 말고 원 seq/cursor·bounded page·손실 표식·범위별 완료 조건을 포함한 수집 방식이 필요하다. A4 요약은 이 중 공통 입력의 한 부분만 해결한다.

## D12 — 센서 영향이 실행 측정과 분리돼 있지 않다

[cache._codes](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/adapters/vllm_cache_callback.py:54)는 hook 자격 확인에서 source를 다시 읽고 해시한다. [Sink](/Users/doha/Desktop/Buyl/byul-dev1-k8s/update/src/guard/adapters/vllm_engine_callback_sensor.py:279)는 호출 경로에서 JSON 직렬화·lock·write를 수행한다. 최근 CPU 전용 연결 observer의 parameter 해시도 고정 lab 범위에서 큰 CPU tensor를 읽는다.

실행에 영향이 있다는 정적 근거이며 영향 크기를 측정한 결과는 없다. GPU tensor를 읽거나 동기화했다고 주장하지 않는다. 서로 다른 버전에서 필요한 sensor 조합이 달라질 때 센서 비용이 lifecycle deadline·latency 비교에 섞일 수 있다. 무결성 검사를 제거하지 말고 immutable startup qualification·교체 감지·경량 runtime guard와 별도 수집을 설계하고, 계측 켜짐/꺼짐의 독립 대조로 허용할 측정 범위를 정해야 한다. 현재 CPU performance=UNKNOWN 경계는 유지한다.

## 고칠 순서와 수락 조건

1. **D01을 먼저 수정하고 TP2 적용을 잠근다.** 두 worker·같은 값·다른 값·한 worker 누락·attempt 변경·rank 미확인의 원 행을 보존하고 예상 범위가 비면 complete가 되지 않아야 한다. 기존 단일 worker `/1` 기록은 동결한다.
2. **D02·D03·D04의 경계를 함께 정리한다.** EngineAdapter를 무작정 비대하게 만들기보다 engine별 provider와 feature capability/profile resolver를 분리한다. 공통 Kubernetes 메타데이터 검증이 engine 버전을 직접 알지 않게 한다. 미검토 source를 실행하거나 PASS로 바꾸지 않는다.
3. **D05·D06·D07을 배포 단위에 맞춘다.** producer/schema별 신원·검증 receipt, engine별 소스 분석, 설치 가능한 sensor artifact를 만든다. 일부 collector 수정 때문에 무관한 생산자 전체를 재등록하지 않아야 한다. 특정 profile을 추가할 때 공통 Python 코드 여기저기를 고치지 않아야 한다.
4. **D08·D09를 새 계약으로 분리한다.** provenance/실행 binding/동등 시험 조건/기업 정책의 역할을 분명히 한다. 과거 EVD/RES/POL/PACK의 hash·Ref·판정 의미는 유지하고 신규 판본으로 이행한다.
5. **D10의 공통 지원 계약 시험을 추가한 후 범위를 늘린다.** vLLM 두 릴리스와 다른 engine의 작은 실제 경로로 위 경계가 성립하는지 확인한다. D11·D12도 긴 요청과 timing 관련 점검의 수락 조건에 넣는다. 이 감사 자체는 그 실제 engine qualification을 수행하지 않았다.

## 유지할 설계

- ENV/POL/PLAN/EVD/RES/RPT/CHG의 분리, append-only·정확 Ref·내용 hash·팩 보존은 유용하다. 전체를 다시 만드는 근거는 없다.
- 미지원 source 거부, EXAMPLE과 실제 관측의 구분, 후속 수집 오류에도 독립 FAIL 보존, 비교 조건이 다를 때 NOT_COMPARABLE은 유지한다.
- 고정 source/image 검토 자체가 나쁜 설계는 아니다. 그 지식을 공통 실행 코드에 흩뜨리고 기능·엔진·버전별 지원 범위를 한 곳에서 선택하지 못하는 것이 문제다.
- 방금 추가한 CPU 실제 연결도 고정 source·모델·실행 설정의 한정 lab 근거다. 이를 범용 multi-engine 구현으로 집계하지 않는다. 같은 방식의 standalone helper를 계속 늘리기 전에 위 경계를 정리해야 한다.

## 마감 기록

관련 기존 계약·runner V1/V2·지원 안내·어댑터 회귀94개 통과(pytest0.55초/부모0.727초). 테스트나 assert는 수정하지 않았다. 이 통과는 위 반례의 부재를 뜻하지 않으며, 원 반례 JSON과 별도로 보존한다.

감사 중 제품 source 345파일은 시작 snapshot과 bytes가 같다. 이번 변경은 감사 문서와 PROGRESS뿐이다. 커밋·push·GPU·AWS·다운로드·새 의존성 추가는 없다. 원 재현 기록은 저장소 밖에 보존하며, 정상 시험 통과를 이 결함들의 부재 증거로 사용하지 않는다.
