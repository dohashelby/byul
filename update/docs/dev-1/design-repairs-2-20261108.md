# 반복 업데이트 설계 보수 2차 — 2026-11-08

사용자가 승인한 아홉 설계 경계를 기준으로 D01~D12, F01~F14, G01~G07의 CPU 보수를 반영했다. worker/rank와 버전 앵커링을 우선 처리한 뒤 실패·복구, 비교·기업 입력, 수집·설치 경계를 연결했다. 기존 점검 기능 추가는 중단했다. 기존 EVD·RES·팩·승인된 소스 신원은 보존했고 커밋하지 않았다. 현재 문서는 구현 및 검증 기록이며 실제 엔진·GPU 지원 인증서가 아니다. 기본 suite 전체 성공과 배포 수락은 독립 소스 승인 및 실제 엔진 자격 검증이 남아 있어 아직 선언하지 않는다.

## 경계별 변경과 수락 조건

| 경계 | 구현 | 검증할 조건 / 남는 범위 |
|---|---|---|
| R1 엔진·버전·실행기 | adapter provider가 profile에 결합한 실제 factory를 제공. 설치 entry point로 HTTP adapter, source analysis, managed launch, 실행기, 코드 closure를 공급. 준비 검사와 실행이 같은 resolver 사용. runtime HTTP client도 등록 driver의 의미 계약 사용 | 새 엔진과 연속 버전에서 공통 select→run→judge→report 변경 없이 EXAMPLE 실행. catalog만 추가한 native 버전은 실행 불가. SGLang/TRT 실제 native 구현은 별도 필요 |
| R2 actor·배치·인과 | rank=None을 보존. 같은 PID의 rank 발견 단계와 sampler를 프로세스 세대·admission·local sequence로 연결. /3은 group/actor/bootstrap와 요청별 stage 할당을 대조 | 통째로 빠진 그룹, 다른 그룹의 동일 rank, 미확인 rank, 잘못된 요청 할당은 미완료. DP/replica의 한 그룹 사용은 관측된 할당 필요. bootstrap producer의 독립 자격 없이는 선언만으로 완료 불가 |
| R3 소스·자격·관측 등급 | producer 자격은 소스 해시 밖 JSON receipt. 현재 source inventory와 별도 검토 receipt를 분리. lab 실행 허용과 EXAMPLE 출처 분리 | receipt 추가가 producer SHA를 바꾸지 않음. manifest 재생성은 승인 아님. 기본 배포 신규 receipt는 비어 있으며 GPU/timing 자격을 발급하지 않음 |
| R4 계획·비교·이월 | PLAN optional 실행 recipe SHA로 요청 설정·제출 target/manifest bytes·실제 공통 코드 및 provider 코드 closure 고정. EVD 비교는 fixture/조건/공급물 closure 사용. 후보 측 preview도 이월 전에 대조 | 바뀐 recipe는 재선정 요구. 독립 tokenizer/component 변경을 model 변경으로 반영. 관측하지 않은 비교 조건은 null로 두어 NOT_COMPARABLE 유지 |
| R5 기업 입력·공급물·정책 | importer /2가 runtime_kind·tokenizer_revision·정책 유효기간·원본 문서 SHA 보존. /1 replay 유지. model mapping /2는 opaque artifact 신원 지원. 역할별 component 신원·참조 추가 | 원 계약 원본은 artifact로 보존. 정책 만료는 실행 직전 확인. 미지원 별도 공급물 구성은 실행 전 거부. 실제 관리형 launcher가 모든 artifact 형식을 지원한다는 뜻 아님 |
| R6 attempt·복구·자원 | attempt별 work 경로, PLAN OS 잠금, 고유 CAS staging. 저장 실패 시 sealed EVD를 pending journal에 fsync하고 정확한 fragment 재저장. 완료 관측은 빈 ABORTED로 덮지 않음. 기준 정리 실패 후 후보 시작 차단. provider 자원 lease와 영속 fence, 명시 검증 복구 | EXAMPLE 오류 주입으로 FAIL 보존·동시 CAS·lease fence 확인. 기본 lease는 같은 호스트/계정 협력 프로세스 범위. 분산 자원 관리자는 provider hook으로 교체. GPU 반환의 물리적 증명은 미실측 |
| R7 수집 cut·cursor | 비동기 bounded journal, accepted/written/processed/drop/error 상태, cut ACK 및 prefix offset. 요청 전 start cut, 세션 cursor로 과거 4096개가 새 시험을 가리지 않게 변경 | 활성 파일의 EOF는 완료 근거가 아님. ACK·source set·state 불확실성은 gap. 유실·초과는 보존하고 timing은 미자격 |
| R8 과거 호환·반출 | 새 optional field는 미입력 시 직렬화에서 제외. 기존 /1·/2 decoder와 published startup/worker source 고정 유지. sensor binding migration 재적용 거부 | 과거 객체 hash/Ref·source pin과 등록 producer 보존. wheel 설치·반출·복원·tamper·재판정 검사. 기존 Python 런타임 자격은 완화하지 않음 |
| R9 발견·안전 probe | OpenAI 공통 adapter와 vLLM public probe를 검토된 경로/요청 allowlist로 제한 | 미등록 GET·POST가 발견됐다는 이유로 발송하지 않음. 새 의미의 경로는 adapter profile에서 승인된 probe를 공급 |

## 확장 절차

새 엔진/버전은 provider와 semantic profile, 실제 factory, normalize/fixture 및 source closure를 등록한다. HTTP 표준 전송이 지원되는 경우의 사용 가능성과 native 내부 의미 자격은 분리한다. 새로운 actor stage 배치나 컴파일/변환 산출물은 해당 provider와 관측 schema의 버전으로 확장하며 공통 선정·조율·판정·보고는 엔진 이름으로 분기하지 않는다. native profile 추가만으로 실행을 허용하지 않는다.

기존 vLLM 0.31의 고정 source pin은 해당 구현의 자격 범위다. 이를 모든 버전의 공통 기준으로 사용하지 않으며 예전 pin을 새 source에 맞춰 갱신하지 않는다. 미래 엔진의 모든 기능에 대해 구조 변경이 영원히 없다는 보장은 하지 않는다. 현재 계약으로 표현하지 못하는 구성은 사전에 미지원/미확인으로 종료한다.

## 검증 기록

구현·시험 코드·문서 초안과 정적 검토를 완료한 snapshot을 저장한 뒤 CPU 시험을 실행했다. 시험 결과와 수정 내역은 아래에 기록했다. 원자료는 `/Users/doha/.codex/visualizations/2026/11/08/01a1b99a-d7c6-79e0-8db2-4cb4b4cc71ed/design-repair-2/`에 둔다. Linux 보조 검증은 캐시된 Docker CPU 이미지의 Python으로 실행했다. 실제 엔진·GPU를 기동하지 않았고 새 이미지·모델·의존성을 다운로드하지 않았다. EXAMPLE 결과를 MEASURED로 바꾸지 않는다.


### 첫 검증과 보완

- 첫 CPU 회귀: 273 통과, 기존 기본 제외 1. 새 EXAMPLE 시나리오와 기존 INV-01~19, 변경 선정·이월, Store/run, paging, scoped 소비를 포함했다.
- Python 3.12.13 전체: 10,592 통과 / 1,015 실패 / 92 오류 / 1 skip / 19 기본 제외, pytest 610.85초·부모 611.557초. 기존 센서 code contract가 이 interpreter를 승인하지 않은 사례와 실제 호출 호환 회귀가 섞여 있다. 자격 핀을 갱신하지 않았다.
- 이미 설치된 3.12.3에서 cache callback 51건 통과. 이 interpreter로 후속 전체 검증한다. 기존 worker HTTP는 published startup source를 복원해 호환시켰으며 새 범용 transport는 기존의 명시 runner/default injection 경계를 유지한다.
- 기업 변환의 기존 `convert_documents()` 호출은 /1 replay 의미를 유지한다. 신규 `import_inputs()` 및 CLI는 /2를 명시 사용하고 source SHA·tokenizer·공급물 graph·정책 기간을 전달한다. 외부 component schema와 내부 닫힌 acyclic hash graph를 함께 검증한다.
- runtime은 backend 설정·CLI 선택·실제 Gateway client 생성까지 등록 driver를 사용한다. 다른 tokenizer 프로토콜은 driver가 client를 공급하며 공통 Gateway 변경이 필요 없다. 기존 운영 bundle의 엔진별 enforcement profile은 검증된 두 엔진 범위를 유지하며 이번 업데이트 파이프라인의 신규 지원 선언에 포함하지 않는다.
- 기존 시험 두 곳은 사용자 승인된 수락 조건 변경을 반영했다. `test_update_cli`는 정리 실패 시 후보가 시작되지 않고 baseline FAIL·후보 ABORTED가 남는지 더 엄격히 검사한다. `test_a1_req10_consumption_pin`은 현재 inventory의 무결성만 확인하고 별도 review receipt 없이 production gate가 거부해야 한다고 검사한다. B source pins나 기존 EVD/RES는 바꾸지 않았다.


### 3.12.3 전체 회귀와 두 번째 보완 묶음

- 첫 3.12.3 회귀: 11,509 통과 / 195 실패 / 1 기존 skip, pytest 340.30초·부모 341.182초. 베이스 interpreter에 PYTHONPATH로 의존성을 연결해 실행한 준비 환경은 일부 자식 실행이 PYTHONPATH를 제거하면 pydantic을 찾지 못했고, `-B`/별도 pycache 위치는 캐시 변조 방어 시험의 준비를 깨뜨렸다. 독립 가상환경을 기존 로컬 캐시만으로 만들고 generated pyc를 정리해 같은 3.12.3의 정상 캐시 동작으로 재검증한다. 새 의존성 다운로드와 자격 pin 변경은 없다.
- 실제 보완: 구/신 owned Kubernetes transport의 타입 호환을 유지한다. explicit after-baseline 후보 생성은 PLAN에 고정한 도출 절차로 취급하고 실제 Pod 신원은 transition이 관측한 SHA로 실행 전에 고정한다. baseline bytes 변경과 임의 설정 변경은 여전히 재선정을 요구한다. attempt 경로를 가정하던 시험의 오류 주입과 원자료 조회를 실제 attempt 경로에 맞췄다.
- 강제 종료 반례: Python 예외 처리 없이 coordinator가 종료하면 OS 잠금만 풀리고 다음 소유자가 진입했다. 실제 자원 없이 subprocess `os._exit(17)`로 확인한 기록은 `lease-crash-before.json`이다. 이제 자원 생성 전 active claim/owned run label을 fsync하고, 남은 claim은 fence로 전환해 검증 복구 전 재사용을 거부한다. claim과 fence는 복구 이력에 보존한다.
- 안전 probe와 출처 의미가 달라져 vLLM adapter는 0.5.0, OpenAI 계열 adapter는 0.2.0으로 구분했다. 기존 HTTP golden은 SHA `d7444f22…` 그대로 보존하고 /2 비교 파일을 별도 추가했다. 수동 확인한 차이는 23개 필드이며, 합성 관측 5개 사례의 EXAMPLE 등급과 미검토 /multi·미지원 버전 관리 POST의 미발송/fixture SHA/관측 목록에 한정된다. 과거 draft codec의 등급·bytes 보존 시험을 추가했다. 메서드별 인가 단위 시험은 실제 vLLM에 없는 /multi에 대해 명시적인 EXAMPLE adapter 프로필을 공급한다.


### 검증 환경 정리 및 공통 호출 보완

- 기존 잠금의 Python 예외 경로와 hard-exit 경로를 별도 검증한다. 프로세스 종료 뒤 active claim이 있으면 다음 임대는 거부되며, owned label 확인 후 claim/fence를 모두 이력으로 옮긴다.
- factory 선택에 필요한 ENV는 ContextVar의 생성 호출 범위에서만 전달한다. 기존 실행기 설정에 내부 ENV key를 추가하지 않으므로 엄격한 CPU 실행기 설정 검증과 기존 factory signature를 보존한다.
- 공통 request-binding HTTP 및 lifecycle coordinator는 엔진별 native 함수 구현과 분리한다. native factory/정규화가 없으면 full-scope를 승인하지 않으면서 공통 HTTP에서 수집할 수 있는 독립 위반은 버전 분기 전에 가리지 않는다.
- per-deployment group 이름은 자격 profile의 형태 해시에 넣지 않는다. 실제 bootstrap digest에는 그대로 결합하고, qualification은 그룹 수·역할·rank·stage 배치를 유지한 정규형을 사용한다. 같은 구조의 배치 ID 변경 때문에 새 sensor profile이 필요한 앵커링을 제거했다.
- unsupported native 버전은 실행 전 NOT_RUN으로 종료함을 기존 소비 회귀에도 반영한다. 수집 후 오류의 ABORTED 기대와 독립 FAIL·정확 Ref·반출/재판정 검증은 유지한다. cleanup 의존 시험은 후보 미기동·ABORTED 사유 및 기준 FAIL 보존을 강화한다. 합성 vLLM source 시험은 해당 vLLM ENV를 명시하고, 파일 쓰기 거부 검증은 macOS `/var` 별칭을 피해 canonical 임시 경로를 사용한다. 제품의 symlink 거부는 완화하지 않는다.


### 독립 범위 검증

- 기존 합성 fixed-route 시험의 `/v1/systemone`은 실제 vLLM 표준 API로 취급하지 않고 명시된 EXAMPLE adapter의 bounded probe로 분리했다. 기본 adapter는 미검토 경로를 계속 거부한다.
- status-only M10 표와 비교 시험은 원 역사 정의를 명시하고 기존 PASS/FAIL 기대를 유지한다. 현재 정의에서는 HTTP 상태만으로 보안·기능 PASS를 내리지 않는 음성 시험을 추가했다. 실제 의미 있는 소비·거부 증거의 요구를 완화하지 않는다.
- fork로 상속한 observer/journal은 부모 프로세스 mutex를 잡거나 부모 state 파일을 쓰지 않는다. 원 함수의 반환·예외를 그대로 유지하며 새 프로세스의 관측은 별도 bootstrap/observer가 필요하다.


### Linux CPU 재검증과 source review 경계

- 캐시된 vLLM CPU 이미지에서 entrypoint를 Python으로 바꾸고 network none·read-only source·CPU/RAM/PID 제한을 걸었다. 실제 엔진·GPU·새 이미지/패키지 다운로드는 없다. Linux 프로그램/계약 65건이 통과했다. direct RSS 프로그램의 실제 PID/RSS 및 다른 세대 거부 assertion도 통과한 뒤 source 내부 out 기록에서 read-only mount로 중단돼, 별도 output mount로 다시 검증한다.
- 해당 이미지 Python 3.12.15는 aarch64다. 기존 stream-buffer/worker-startup 계약의 승인 대상 linux x86_64와 달라 code qualification이 거부하는 사례는 핀을 바꿔 통과시키지 않는다. macOS에서 지원하지 않는 실제 /proc·renameat2·prctl 시험은 Linux 결과를 별도로 제시한다.
- 기존 ready-endpoint 실패 주입 시험은 관측 미지원 OS에서 원 함수에 새 예외가 생겨야 한다는 잘못된 가정을 제거했다. 원 동작·리소스 정리·미지원 gap·권한/신원 미승격 assertion은 유지한다. 실제 지원 범위를 늘린 것이 아니다.
- 현재 source gate 알고리즘은 기존 B 승인 SHA와 달라 현재 native B 통합의 positive gate는 미승인이다. 자기 승인으로 고치지 않았다. B의 고정 알고리즘 승인과 독립 manifest review receipt가 필요하다. B1 공용 검증 helper의 기존 SHA는 유지하며, B native gate 알고리즘을 한 번 검토한 이후 공통 구현/엔진 profile 변경은 독립 receipt 데이터로 검토할 수 있다. 승인 데이터를 소스 해시에 넣는 순환은 없다.

## 최종 CPU 검증 결과

아래는 source snapshot 13 기준 결과다. 앞선 준비 오류와 중간 회귀 실패 로그도 삭제하지 않았다. 마지막 검증 이후에는 이 문서와 진행·runtime 상태 기록만 갱신했다.

| 범위 | 결과 | 근거 파일 |
|---|---|---|
| 설계·복구·안전 probe·과거 호환·INV-01~19 | 342 통과, 기존 기본 제외 1, 6.57초 | `core-final.log` |
| update 기본 전체, CPython 3.12.3 | 11,692 통과 / 19 실패 / 기존 skip 1; pytest 252.64초, 부모 벽시계 253.149623초 | `regression-final-2.log`, `regression-final-2.xml`, `regression-final-2-result.json` |
| Linux CPU 실제 PID/RSS·renameat2·prctl·소켓/세션 등 | 66 통과, 0 실패, 8.92초 | `linux-final.log`, `linux-final.xml` |
| 기업 입력/API 통합 | 26 통과, 0 실패 | `api-final.log` |
| runtime backend·SGLang·client deadline·대상·Pod·driver | 91 통과, subtest 119 통과, 0 실패, 25.08초 | `runtime-3.log` |
| guard wheel 격리 설치·반출/복원·변조 거부·저장 팩 재판정 | source/wheel/설치 파일 387개 동일, 검증 성공 | `installed-final.log`, `installed-final/summary.json` |
| runtime wheel 격리 설치 | 57개 파일 동일, 새 driver 시험 4 통과 | `runtime-installed.log` |
| 실제 설치 entry point의 SGLang/TRT 예제 provider | 두 엔진 모두 공통 선정→실행→판정→보고 COMPLETED, 판정 PASS/출처 EXAMPLE/official=false | `installed-provider-final.log`, `installed-provider-summary.json` |

전체 기본 suite는 `qualified-venv/bin/python -m pytest -q -n 4 --dist=loadfile --tb=short --basetemp=<산출물>/pytest-final-2 --junitxml=<산출물>/regression-final-2.xml`로 실행했다. 기존 기본 engine/docker/network 제외는 유지했고 새 skip이나 제외를 추가하지 않았다. 최종 결과 원본은 `summary.json`이다.

19개 실패는 다음과 같이 구분한다. Linux 재검증 성공을 macOS 전체 suite의 성공으로 합산하지 않는다.

- 8개는 macOS에 없는 Linux 기능을 직접 실행하는 시험이다. profiler owned trace 4, PLT-06 prepare 1, resource recovery 1, process RSS 1, session keepalive 1이며 해당 사례는 별도 Linux CPU 재검증에서 모두 통과했다. Docker의 entrypoint는 Python, network는 none, source는 읽기 전용이며 엔진을 실행하지 않았다.
- 11개는 현재 source gate 알고리즘에 대한 B의 기존 승인 SHA와 독립 review receipt를 요구하는 positive 통합 시험이다. `test_b_lif07_native_capture` 2, `test_b_lif07_readiness` 1, `test_a1_B_native21_consumption` 2, `test_a1_B160_http_product` 4, `test_b_native20_common_consumption` 2다. B의 승인 핀·시험을 바꾸거나 현재 inventory를 자기 승인해 통과시키지 않았다. 독립 검토가 반영된 뒤 해당 통합 시험의 재검증이 필요하다.

## 추가 G01~G07 수리 확인

| 항목 | 반영한 경계와 확인 |
|---|---|
| G01 부분 저장 후 위반 은폐 | R6: sealed EVD를 영속 pending journal에 남기고 같은 attempt에서 정확히 복구. 앞선 FAIL을 빈 ABORTED로 덮지 않는 오류 주입 회귀 |
| G02 동시 PLAN 첨부 혼입 | R6: PLAN OS 잠금·attempt별 경로·고유 CAS staging, 중복 진입 거부 및 동시 쓰기 회귀 |
| G03 기준 정리 실패 후 후보 기동 | R6: 후보 시작 차단, 기준 FAIL 보존·후보 ABORTED 및 정확한 중단 사유 확인 |
| G04 tokenizer 변경 누락 | R5/R4: 원본·tokenizer revision·역할별 공급물 graph를 변환/선정/recipe에 반영, 과거 /1 replay 유지 |
| G05 발견 GET의 암묵적 실행 | R9: 검토된 profile의 경로·메서드·본문만 probe 생성, 미등록 GET/POST 전송 거부 |
| G06 GPU 수량의 all 축소 | R2/R6: 수량·장치 ID·CPU/RAM/PID 한도를 보존하고 명시 장치만 launch 설정에 반영. 실제 GPU 사용·반환 실측은 미완료 |
| G07 현재 manifest 자동 승인 | R3: inventory 무결성과 독립 review receipt 분리, 미검토 새 manifest 거부. 신규 receipt는 비어 있음 |

기존 D/F 항목과 R1~R9의 대응은 `design-architecture-final-review-20261108.md`의 통합 표를 따른다. 정적 점검·snapshot 대조 결과와 보호 범위는 `preservation-final.json`에 기록했다. 검증 대상 소스 1,501경로의 변경 0, 시작 시점 보호 증거·팩·fixture·golden 267경로의 변경/누락 0, published legacy 모듈 8개 및 B gate 파일 3개 동일, 기존 producer 8종 보존을 확인했다. 현재 source inventory 498파일의 무결성은 확인했지만 이는 독립 승인 receipt가 아니다.

## 확장 검증의 한계와 다음 관문

예제 contribution을 별도 wheel로 빌드해 산출물 안의 격리 환경에 설치했다. 실제 `guard.engine_providers` entry point 로딩부터 SGLang/TensorRT-LLM 예제의 공통 실행·판정·보고, 알 수 없는 다음 버전의 factory 거부, 저장소 무결성까지 확인했다. 이 검증을 위해 공통 파이프라인의 엔진 분기를 추가하지 않았다. runtime도 실제 Gateway의 client 생성과 CLI/config/대상 선택을 driver 등록으로 연결했다. 예제 설치가 실제 SGLang/TRT native 센서나 managed launcher 지원을 뜻하지 않으며 `runtime_qualified=false`다.

남은 관문은 독립 source/알고리즘 검토와 receipt, 실제 엔진별 native factory·bootstrap 관측 producer 및 각 의미 profile의 자격, 두 릴리스 실제 비교, TP/replica/GPU 반환과 계측 on/off 영향 검증이다. 자기 신고 topology·새 catalog 항목만으로 full-scope를 승인하지 않는다. 설치형 확장 계약은 CPU에서 확인했으며, 아직 관측하지 않은 엔진 내부 사실을 공통 PASS로 승격하지 않는다.

작업 기준은 `codex/dev1-p1-producers`, HEAD `8402ff8947e78c85e75d76c62b0bd91d88cc0512`이며 커밋·push하지 않았다. 삭제 요청받은 루트 `AGENTS.md`는 삭제 상태를 유지했다.
