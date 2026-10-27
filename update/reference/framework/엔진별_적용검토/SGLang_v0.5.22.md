# SGLang v0.5.22 프레임워크 적용성 검토

검토일: 2026-10-24 · 상태: **소스 기반 적용 검토 / 실제 환경 선정·시험 대기**

공식 [v0.5.22 release](https://github.com/sgl-project/sglang/releases/tag/v0.5.22)의 commit `baa9befa381511cd4d56f83980951e5a45fffc6d`를 고정해 기존 77개 CHK와 일곱 구성요소를 대조했다. 프레임워크 기준판은 `729216b1ce76fd40c99c50cc4770fd86bc8310a7`, 정본 `data/checklist.json` SHA256은 `7f857208af8071b9934695f32b8753000f0f7b79aa0d47d1bd42945184875ef0`다.

공통 요구는 SGLang의 주요 실행 흐름에도 대응한다. 다만 session 객체의 행위별 인가와 지속 KV 사본의 보호 범위는 프레임워크 담당자의 추가 검토가 필요하다. 이 검토는 제품별 적용 프로파일의 첫 사례이며 SGLang 전체 지원·보안성 검증 완료로 기록하지 않는다.

<a id="sglang-criteria-basis-20261027"></a>
## 적용 기준의 판본 — 2026-10-27 대조

아래 77개 적용표의 기준은 2026-10-24의 `729216b1ce76fd40c99c50cc4770fd86bc8310a7`·checklist SHA256 `7f857208af8071b9934695f32b8753000f0f7b79aa0d47d1bd42945184875ef0`다. 당시 행·기준 hash·분류·미확인 판정은 그대로 보존했다. 현행 적용에는 `data/checklist.json` SHA256 `cf4903efe9a60543ceff5276a09ac2c40bb16d54658b21e5deafb03c948f8da7`의 문구·절차를 사용한다. 각 행의 `original_criteria_ref`는 현재 03 문서로 이동하는 링크이며, 당시 기준의 식별은 위 commit/hash와 각 행의 `criteria_sha256`을 따른다.

[기준 차이 JSON](SGLang_v0.5.22_기준차이_20261027.json)은 고정 구판과 현행의 77개 ID 집합·당시 profile hash를 대조했다. CHK 기록 39개에 필드 변경이 있으며, 기존 profile의 5필드 기준 hash는 7개에서 달라졌다. `test_branch_conditions`가 바뀐 항목은 LIF-004·LIF-012 두 개다. 39개 중 나머지 변경에는 시험 방법·정상 대조·참조 문구 등이 포함된다. 현행 기준에 대한 SGLang 소스/실행 재검토는 선정 환경에서 진행한다.

| 기존 profile hash가 달라진 CHK | 현행 적용에서 먼저 확인할 변화 |
| --- | --- |
| SUP-006 | 적재 일치와 LIF-007/009의 전체 접수 준비·재개 확인을 연결 |
| SUP-008 | 필드 지원·노출·정책을 먼저 조사하고 코드 소비 분기의 적용 상태를 구분 |
| REQ-012 | 취소 대상·늦은 결과와 LIF-010/011의 실제 작업·자원 회수를 연결 |
| LIF-004 | debug 응답 보관 기능의 실제 활성·비사용·미정을 기준으로 분기 선정 |
| LIF-012 | 자동 retry 주체·원/새 attempt·잔존/비용을 확인하고 수동 재호출과 구분 |
| OBS-005 | usage 보고 허용 여부에 따른 적용과 적용 후 정책값·증거 부족을 구분 |
| OBS-007 | replay consumer의 배치·도달·정책을 확인하고 실행 허용/거부를 대조 |

차이의 재현은 `python3 engine_sglang_basis_delta.py --historical-checklist /path/to/checklist-7f857208.json --check`로 수행한다. 구판은 위 SHA256의 원본을 준비한다. 이 검사는 JSON 필드와 hash를 대조하며 77개 엔진 동작을 다시 검증하지 않는다. HTTP 정적 후보 v2는 같은 SGLang 고정 소스에 관한 별도 근거 보완이다.

## HTTP 정적 후보 목록 — 2026-10-27 보완

현행 목록은 [HTTP 정적 후보 v2](SGLang_v0.5.22_HTTP_정적후보_v2.json)다. [기존 80행 JSON](SGLang_v0.5.22_HTTP_route목록.json)은 당시 상위 수준 decorator 함수만 추출한 제한 목록으로 bytes/source pin을 보존했다. v2는 조건부 dumper/root 3함수와 포함 router 2모듈의 3함수를 보완해 고유 함수 후보 86개·decorator 등록 선언 91개를 기록한다. 기존 80행의 함수·줄·선언은 새 후보에 일대일 대응한다.

root 두 함수의 상호 배타 조건과 HEAD·동적 경로·include 조건은 각각 보존했다. 실제 API 수와 동시 활성 경로 수는 미확인이며, [목록 안내](SGLang_v0.5.22_HTTP_목록안내.md)에 고정 원문·권한 선언·계수·SHA256·재생성/검사·실제시험 인계를 정리했다. CHK-REQ-001/003과 CHK-PLT-008의 기존 의미를 유지하며 선정 환경의 route/method·역할·정상 호출·실제 집행 증거에 연결한다. 이번 보완은 아래 77개 CHK의 미실행·미확인 상태를 유지한다.

## 검토 환경과 판정 상태

비교 후보는 **단일 노드 NVIDIA GPU·텍스트 모델 1개·TP·일반/스트리밍·Radix prefix cache**다. 실제 모델·이미지·GPU·TP 크기·유효 설정·조직 정책·승인은 미정이다. HiCache·session·Responses 저장·custom callable·weights daemon·LoRA/live update·PD/PP·profiler 등은 설치판의 등록·노출·실제 소비 조건으로 각각 선정한다. CUDA Graph·compile도 선언 옵션 이름만으로 활성/비사용을 확정하지 않는다.

- 77개 모두 실행 `not_run`, 판정 `unverified`. 통과·미충족·비적용 확정은 각각 0개다.
- 아래 수는 대응 조사 분류다. 구현률이나 엔진 지원률로 사용하지 않는다.
- 코드·선언 기본값은 source 근거다. 실제 gate·동작·기한·GPU 참조 수명·정상 성능은 시험 증거가 필요하다.
- gua의 기존 vLLM 실행기를 SGLang 지원/통과 근거로 승계하지 않았다.

| 대응 조사 분류 | CHK 수 |
| --- | ---: |
| 외부 집행과 대응 | 28 |
| 엔진 경로 대응 | 35 |
| 선택 기능 대응 | 10 |
| 동일 기능 매핑 보류 | 3 |
| 부분 대응 | 1 |

## 프레임워크 구성별 적용

| 구성 | 재사용할 내용 | SGLang에서 남는 일 |
| --- | --- | --- |
| 보안 목표 | 사용자 입력/출력·모델 weights·KV/session·운영 자격·서비스·평가 증거를 기밀성/무결성/가용성/책임 추적성에 연결한다. | KV가 외부 파일/저장소에 지속되는 경우와 session 객체를 실제 보호 자산에 추가 식별한다. |
| 거버넌스 | 정책 소유·승인·겸임·예외 만료·필수 검사 미확인 처리 규칙을 재사용한다. | SGLang 운영자, Gateway/신원, 모델/저장소, 관측 담당에게 기존 책임을 배정한다. 구체 인원·기한은 미정이다. |
| 위험 관리 | 정상/실패 흐름의 위협→통제→증거→잔여 위험 연결을 재사용한다. | native generate/session, cache_salt, 선택 callable·HiCache·weights daemon을 별도 접점으로 기록한다. 모든 선택 backend 분석은 남는다. |
| 보안 기능과 보호 요구 | 여덟 기능·36개 요구·77개 CHK의 기존 보호 의미를 출발 기준으로 대응한다. | session 객체 인가의 검사 분기와 지속 KV의 저장 보호/보존 범위가 명세 수준에서 더 필요하다. 아래 보완 후보를 따른다. |
| 환경 확인 | 활성·노출·소비·집행 주체에 따른 적용/조건부/미정 선정 규칙을 재사용한다. | 명령행 선언 기본값과 모델/장치별로 해석한 유효값을 구분한다. 현재 실제 설치 환경은 미선정이다. |
| 생명주기 | 반입→배포→준비→실행→변경→종결/복구→폐기의 인계 규칙을 재사용한다. | scheduler ready/warmup·abort/정리·daemon 수명·session close·선택 KV 저장소를 실제 인계 대상으로 연결한다. |
| 보호 요구의 적용과 검증 | 정상 대조·최소 증거·세 판정과 관리 결정 분리를 재사용한다. | SGLang adapter·계측·실행 증거는 개발/시험 담당이 만든다. 현재 77개 실행 판정은 모두 미확인이다. |

## 프레임워크 담당자가 검토할 부분

이미 정의된 조직 정책값·최소 증거·승인/예외 절차를 다시 만들 필요는 없다. 이번 엔진 대조에서 명세의 범위가 구체적으로 부족한 두 후보를 아래에 남긴다. 기존 CHK ID와 확정 판정 기준은 유지했다.

### SG-PROFILE-01 · native session 객체의 허용 행위

- 기존 범위: 1.2 작업·대상 인가, 4.1 귀속, 1.7 철회, 6.4 회수는 재사용할 수 있다. REQ-006 적용 조건은 저장 Responses/결과 객체이며 session cache를 포함한다고 확정돼 있지 않다.
- 남은 부분: open/이어쓰기/close에 사용자·공유 집단·session ID·허용 행위가 어떻게 연결되는지 기존 CHK 분기로 명시하는 작업이 남는다.
- 권고: session 사용 환경에 한해 A/B session의 이어쓰기·종료·중복 ID와 종료 중 진행 요청을 대조하는 적용 분기 초안을 검토한다. 기존 REQ-006 기준을 조용히 확대하거나 새 CHK를 즉시 신설하지 않는다.
- 개발·시험 담당: 실제 session 기능/도달성·Gateway identity/owner 연결·scheduler 상태 수명 확인, 시험/계측 구현

### SG-PROFILE-02 · 지속 KV 사본의 저장 보호와 보존

- 기존 범위: 4.2/4.3과 REQ-015/016은 공유 집단·문맥 재사용을 다룬다. 1.4는 engine 권한을 다룬다. 5.4/5.7과 OBS-006/012는 현재 기록·진단·usage 원본/사본을 명시한다.
- 남은 부분: HiCacheFile 같은 KV 저장 bytes의 비허용 독자 접근·내용 변조·필요 암호화·보존/삭제를 기존 CHK만으로 충분히 평가할 명시적 범위·증거가 부족하다.
- 권고: HiCache 등 지속 저장을 사용한다면 KV 사본까지 공통 저장 자료 범위를 넓힐지 요구 개정안으로 결정한다. 저장소/암호화 값을 미리 정하거나 현재 OBS 통과 결과를 KV 보호로 전용하지 않는다. 기본 비지속 Radix 구성에는 저장 분기를 강제하지 않는다.
- 개발·시험 담당: 선택 KV backend·실제 파일/원격 object·writer/reader/ACL·변조/삭제/복구 관측, 저장소 통제 구현

### SG-PROFILE-03 · 동일 기능 부재와 일부 분기의 차이

- 기존 범위: 02/04의 기능 부재·매핑 미확인 분리와 분기별 결과 규칙이 이미 있다.
- 남은 부분: SGLang별 적용 기록에서 SUP-008·OBS-004/005의 대응 미확인과 LIF-013의 release 분기 차이를 아직 실제 설치 자료로 확정하지 못했다.
- 권고: 기존 규칙을 이 profile에 적용한다. 프레임워크 공통 절차를 추가로 만들 필요는 없고, 설치 대상이 선정되면 미확인/비적용의 근거와 분기 결과를 확정한다.
- 개발·시험 담당: 실제 schema/설치 확장/reporter/daemon 제어 경로·도달성 조사와 실행 증거 제공

조직 정책 담당자는 실제 독자·공유 집단·역할·암호화 필요성·예산/기한·잔여 위험 수용을 확정한다. 프레임워크 담당자는 그 값을 임의로 채우지 않고 적용 범위·요구 간 중복/공백·충분한 증거·개정 필요성을 검토한다.

## 77개 CHK 대응표

각 행은 기존 기준의 적용 대상을 찾은 기록이다. 공통 기준은 링크한 기존 CHK를 그대로 따른다. 필요한 증거는 이 profile에서 새 필수 기준을 만든 것이 아니라 기존 정상 대조·판정에 대응시켜 받을 자료다. source 근거 번호는 아래 고정 commit 파일로 연결된다.

### 공급물

| CHK · 대응 분류 | SGLang 대응과 한계 | 개발·운영 담당이 확보할 실제 증거 |
| --- | --- | --- |
| [CHK-SUP-001](../03_체크리스트와_보완가이드.md#CHK-SUP-001)<br>외부 집행과 대응 | 승인 기록·역할 분리는 조직 저장소/배포 절차에 대응한다. checksum 함수는 승인 권한 분리를 대신하지 않는다.<br>[VERIFY](#source-verify) · [LOAD](#source-load) | 실제 제출/승인 계정, 승인 저장소 ACL, 승인 변경 거부·정상 승인과 독립 이력 |
| [CHK-SUP-002](../03_체크리스트와_보완가이드.md#CHK-SUP-002)<br>외부 집행과 대응 | 배포·승격 권한은 CI/CD·수동 배포 집행점에서 확인한다.<br>[LAUNCH](#source-launch) | 배포 주체×모델×환경 허용표, 허용 밖 배포 거부·실제 실행 식별 |
| [CHK-SUP-003](../03_체크리스트와_보완가이드.md#CHK-SUP-003)<br>엔진 경로 대응 | weights 외 config·tokenizer·template와 추가 모델을 소비 목록에 연결한다.<br>[LOAD](#source-load) · [CONFIG](#source-config) · [RENDER](#source-render) | 선정 모델의 실제 읽은 경로/판본·digest·승인 목록; 선택 draft/LoRA는 사용 시 추가 |
| [CHK-SUP-004](../03_체크리스트와_보완가이드.md#CHK-SUP-004)<br>외부 집행과 대응 | Python 환경·kernel·transformers/torch/SDK까지 실제 이미지/패키지와 대조한다.<br>[LAUNCH](#source-launch) · [GPU](#source-gpu) · [JINJA](#source-jinja) | 실행 image digest/entrypoint·설치 의존성·승인 목록의 일치 |
| [CHK-SUP-005](../03_체크리스트와_보완가이드.md#CHK-SUP-005)<br>외부 집행과 대응 | loader와 checksum 전후의 파일·symlink·mount 교체 통제를 대조한다.<br>[LOAD](#source-load) · [VERIFY](#source-verify) | 실제 mount/파일 권한, 무해 교체 시도, 사용 시점 실제 bytes 또는 교체 거부 |
| [CHK-SUP-006](../03_체크리스트와_보완가이드.md#CHK-SUP-006)<br>엔진 경로 대응 | worker 적재와 선택 IPC weights의 실제 payload를 승인 대상에 연결한다.<br>[COMMIT](#source-commit) · [WEIGHTLOAD](#source-weightload) | rank별 실제 모델/shard·적재 대상·승인 식별; daemon 사용 시 config/세대/transport payload |
| [CHK-SUP-007](../03_체크리스트와_보완가이드.md#CHK-SUP-007)<br>선택 기능 대응 | compile cache index·하위 compiler·생성 graph의 writer와 소비자를 구분한다.<br>[COMPILE](#source-compile) · [COMPILEDIR](#source-compiledir) | 실제 compile 활성·경로·writer/reader, 비허용 변조 거부와 정상 재사용 |
| [CHK-SUP-008](../03_체크리스트와_보완가이드.md#CHK-SUP-008)<br>동일 기능 매핑 보류 | 선정 텍스트 API에서 요청별 코드 revision 선택과 동일한 필드→코드 소비 연결은 이번 조사에서 확보하지 못했다. custom callable은 SUP-012로 연결한다.<br>[INPUT](#source-input) · [CHATGATE](#source-chatgate) · [CALLABLE](#source-callable) | 활성 API schema와 확장 전체의 코드 판본 선택 전달 조사; 부재 확인 전에는 비적용 확정 보류 |
| [CHK-SUP-009](../03_체크리스트와_보완가이드.md#CHK-SUP-009)<br>엔진 경로 대응 | model config의 trust_remote_code 전달과 선언 기본값을 대조한다.<br>[CONFIG](#source-config) · [MODELARGS](#source-modelargs) | 유효 설정·선정 모델의 실제 추가 코드 소비; 금지 코드 미실행과 승인 모델 정상 로딩 |
| [CHK-SUP-010](../03_체크리스트와_보완가이드.md#CHK-SUP-010)<br>엔진 경로 대응 | 플러그인 entry point 발견·필터·hook 적용을 실제 설치 그룹별로 연결한다.<br>[PLUGIN](#source-plugin) | 설치 entry point/distribution·실제 발견/로드 결과·허용 목록·필터 제외 정상 대조 |
| [CHK-SUP-011](../03_체크리스트와_보완가이드.md#CHK-SUP-011)<br>선택 기능 대응 | HiCache 동적 backend import는 general plugin 발견과 별도 소비 경로다.<br>[DYNAMIC](#source-dynamic) | 선택 backend/module/class·실행 모드별 실제 import·비허용 import 거부 |
| [CHK-SUP-012](../03_체크리스트와_보완가이드.md#CHK-SUP-012)<br>엔진 경로 대응 | PT legacy tar fallback, 선택 callable/dump/daemon reader를 각각 조사한다.<br>[PT](#source-pt) · [CALLABLE](#source-callable) · [CALLGATE](#source-callgate) · [CALLARGS](#source-callargs) · [UNPICKLE](#source-unpickle) · [DUMP](#source-dump) | 선택 reader·설치 torch/dill·유효 gate·무해 비허용 객체의 실제 부수 동작/거부와 정상 소비 |
| [CHK-SUP-013](../03_체크리스트와_보완가이드.md#CHK-SUP-013)<br>엔진 경로 대응 | chat template renderer와 transformers의 실제 판본·sandbox 계약을 연결한다.<br>[JINJA](#source-jinja) · [RENDER](#source-render) | 공급 template 출처·실제 renderer/의존성·금지 동작 거부·정상 render |
| [CHK-SUP-014](../03_체크리스트와_보완가이드.md#CHK-SUP-014)<br>외부 집행과 대응 | 조직의 필수 검사·보류 gate를 배포/사용 집행점에 연결한다.<br>[LOAD](#source-load) · [LAUNCH](#source-launch) | 필수 검사 채택·실패/오류/생략 묶음의 보류와 정상 묶음 허용; 승인/위험수용 기록 구분 |
| [CHK-SUP-015](../03_체크리스트와_보완가이드.md#CHK-SUP-015)<br>엔진 경로 대응 | 다운로드·compile cache의 재사용 식별과 변경 후 재생성을 조사한다.<br>[LOAD](#source-load) · [COMPILE](#source-compile) · [COMPILEDIR](#source-compiledir) | 변경 전후 모델/도구/옵션·cache 식별·실제 재사용/재생성; 하위 compiler도 포함 |
| [CHK-SUP-016](../03_체크리스트와_보완가이드.md#CHK-SUP-016)<br>엔진 경로 대응 | 다운로드·compile 생성 중단 뒤 부분 파일의 소비를 조사한다.<br>[HF](#source-hf) · [COMPILE](#source-compile) · [COMPILEDIR](#source-compiledir) | 생성 단계별 중단 기록·완료 표지·실제 읽은 파일·정상 재시작; 파일 존재만으로 완료 판정 금지 |

### 요청·상태

| CHK · 대응 분류 | SGLang 대응과 한계 | 개발·운영 담당이 확보할 실제 증거 |
| --- | --- | --- |
| [CHK-REQ-001](../03_체크리스트와_보완가이드.md#CHK-REQ-001)<br>외부 집행과 대응 | Gateway 경유·직통과 SGLang 인증 middleware의 실제 설치/노출을 함께 확인한다.<br>[AUTH](#source-auth) · [HTTP](#source-http) | 실제 ingress/직통 경로·키 정책·허용/비허용 호출의 handler 수행 여부 |
| [CHK-REQ-002](../03_체크리스트와_보완가이드.md#CHK-REQ-002)<br>엔진 경로 대응 | route match·Request path·root_path 변환과 권한 등급의 연결을 대조한다.<br>[AUTH](#source-auth) · [TLS](#source-tls) | 프록시 재작성 전후 경로·최종 route·정규화·인증 결과; 비허용 handler 미수행 |
| [CHK-REQ-003](../03_체크리스트와_보완가이드.md#CHK-REQ-003)<br>엔진 경로 대응 | ADMIN_OPTIONAL과 키 조합, health/metrics 예외를 실제 역할 정책에 대조한다.<br>[AUTH](#source-auth) · [ADMIN](#source-admin) · [UPDATE](#source-update) | route×키/역할×노출 표, 일반 사용자 관리 작업 거부·관리 정상 작업; API 상태코드와 실제 효과 |
| [CHK-REQ-004](../03_체크리스트와_보완가이드.md#CHK-REQ-004)<br>외부 집행과 대응 | SGLang의 공용 키와 Gateway가 검증한 최종 사용자 신원을 구분한다.<br>[AUTH](#source-auth) · [BASE](#source-base) | 외부 신원/header 제거·재생성·최종 인가 주체; 임의 사용자 값과 검증 신원 비교 |
| [CHK-REQ-005](../03_체크리스트와_보완가이드.md#CHK-REQ-005)<br>엔진 경로 대응 | base model:adapter 선택과 native/관리 변경 경로의 최종 모델을 연결한다.<br>[BASE](#source-base) · [UPDATE](#source-update) · [LOAD](#source-load) | 호출자 허용표·최종 모델/adapter·사용 종료/alias 변경 뒤 실제 선택·거부 |
| [CHK-REQ-006](../03_체크리스트와_보완가이드.md#CHK-REQ-006)<br>선택 기능 대응 | enable_response_store와 response ID 조회·취소에 대응한다. session API를 이 항목 충족으로 합치지 않는다.<br>[STORE](#source-store) · [OBJECT](#source-object) · [STOREARGS](#source-storeargs) | A/B 저장 객체·owner·공유/이어쓰기/취소 정책·경유/직통 실제 객체 영향 |
| [CHK-REQ-007](../03_체크리스트와_보완가이드.md#CHK-REQ-007)<br>엔진 경로 대응 | chat template kwargs·render·최종 입력 제한을 연결한다.<br>[CHATGATE](#source-chatgate) · [RENDER](#source-render) · [VALIDATE](#source-validate) | render 전후 입력·최종 token/길이·필수 정책 집행; 정상 template 대조 |
| [CHK-REQ-008](../03_체크리스트와_보완가이드.md#CHK-REQ-008)<br>엔진 경로 대응 | native input_ids와 OpenAI chat input_ids의 template 우회 경로가 있다.<br>[INPUT](#source-input) · [CHAT](#source-chat) · [TOKENIZE](#source-tokenize) | 외부 재사용 token 금지 정책의 거부/제거와 모델 미소비; 정책 변경 시 우회 입력 유효성·정책 검사 |
| [CHK-REQ-009](../03_체크리스트와_보완가이드.md#CHK-REQ-009)<br>엔진 경로 대응 | context/절단/total-token 검사와 최종 모델 입력을 대조한다. sampling용 vocab 검사를 모든 input_ids 검증으로 확대하지 않는다.<br>[VALIDATE](#source-validate) · [TOKENIZE](#source-tokenize) · [SAMPLING](#source-sampling) | 선정 모델 vocab·길이·변환 후 token의 실제 소비/거부·backend별 정상 범위 |
| [CHK-REQ-010](../03_체크리스트와_보완가이드.md#CHK-REQ-010)<br>엔진 경로 대응 | SamplingParams.verify의 값·grammar 조합과 실제 runner 소비를 연결한다.<br>[SAMPLING](#source-sampling) · [TOKENIZE](#source-tokenize) | 수치/개수/조합 경계·정상 생성·오류 이후 후속 요청; 선택 custom processor 별도 |
| [CHK-REQ-011](../03_체크리스트와_보완가이드.md#CHK-REQ-011)<br>엔진 경로 대응 | OpenAI 변환·stream 분기와 tokenizer 응답 대기를 요청 귀속에 연결한다.<br>[BASE](#source-base) · [WAIT](#source-wait) | 동시 A/B 요청·내외부 ID·stream chunk·실제 수신자와 결과; native API도 포함 |
| [CHK-REQ-012](../03_체크리스트와_보완가이드.md#CHK-REQ-012)<br>엔진 경로 대응 | 연결 종료→abort 전달→scheduler 후처리와 늦은 결과 귀속을 연결한다.<br>[WAIT](#source-wait) · [ABORT](#source-abort) · [SCHEDABORT](#source-schedabort) | 취소 대상/시도 ID·늦은 출력·새 요청 수신 결과; 자원 회수는 LIF-010에서 별도 |
| [CHK-REQ-013](../03_체크리스트와_보완가이드.md#CHK-REQ-013)<br>엔진 경로 대응 | prompt_token_ids·native 추가 반환·오류 envelope를 endpoint별 독자 정책에 대조한다.<br>[RETURN](#source-return) · [RETURNCHOICE](#source-returnchoice) · [INPUT](#source-input) · [ERROR](#source-error) · [BASE](#source-base) | 정상/stream/오류 응답 필드·합성 내부 marker·수신자 정책; 정상 답변 인용과 추가 입력 반환 구분 |
| [CHK-REQ-014](../03_체크리스트와_보완가이드.md#CHK-REQ-014)<br>엔진 경로 대응 | scheduler IPC와 TP communicator의 중간 자료·최종 출력 귀속을 추적한다.<br>[IPC](#source-ipc) · [ALLREDUCE](#source-allreduce) | 실제 TP rank/요청/세대·전달 payload와 최종 출력; PD/PP는 선택 시 별도 |
| [CHK-REQ-015](../03_체크리스트와_보완가이드.md#CHK-REQ-015)<br>엔진 경로 대응 | 요청 cache_salt가 Radix key에 반영된다. 인증 신원→공유 집단 결정은 Gateway/환경 증거와 연결한다.<br>[TOKENIZE](#source-tokenize) · [KEY](#source-key) · [KEYMATCH](#source-keymatch) · [RADIX](#source-radix) | 검증 신원·신뢰 집단/주입 규칙·client salt 위조/누락·실제 교차 집단 hit/소비 |
| [CHK-REQ-016](../03_체크리스트와_보완가이드.md#CHK-REQ-016)<br>엔진 경로 대응 | Radix key·extra_key·salt와 모델/adapter 변경의 cache 문맥을 대조한다.<br>[KEY](#source-key) · [KEYMATCH](#source-keymatch) · [RADIX](#source-radix) · [HICACHE](#source-hicache) | 실제 cache 종류별 key·모델/판본/설정/adapter 문맥·호환/비호환 재사용 결과 |
| [CHK-REQ-017](../03_체크리스트와_보완가이드.md#CHK-REQ-017)<br>엔진 경로 대응 | 공유 Radix hit 경로를 위협 조건하의 시간 관측 시험에 연결한다.<br>[RADIX](#source-radix) | 비공개 prefix·관측자/반복 예산·통계 기준·hit/지연 원인·허용/금지 추론 결과 |
| [CHK-REQ-018](../03_체크리스트와_보완가이드.md#CHK-REQ-018)<br>엔진 경로 대응 | SGLang batch filter/merge의 sampling 상태 이동에 대응한다.<br>[BATCH](#source-batch) | 요청→slot 이동·grammar/mask/logit 상태·독립 정상 결과; 선택 runner별 관측 |
| [CHK-REQ-019](../03_체크리스트와_보완가이드.md#CHK-REQ-019)<br>엔진 경로 대응 | 선택 attention/backend와 실제 설치 kernel의 dtype/shape 분기에 대응한다.<br>[GPU](#source-gpu) | 실제 GPU/backend/kernel 판본·경계 입력·메모리 계측·초기화/오류·정상 연산 |
| [CHK-REQ-020](../03_체크리스트와_보완가이드.md#CHK-REQ-020)<br>선택 기능 대응 | decode CUDA Graph capture와 input buffer copy에 대응한다.<br>[GRAPH](#source-graph) · [GRAPHINPUT](#source-graphinput) | 실제 graph 활성/mode·current input/유효 길이·출력 소비 시점·buffer 재사용·GPU 완료 |
| [CHK-REQ-021](../03_체크리스트와_보완가이드.md#CHK-REQ-021)<br>선택 기능 대응 | custom all-reduce의 graph buffer 등록과 dispose에 대응한다.<br>[ALLREDUCE](#source-allreduce) · [ALLCLOSE](#source-allclose) | 선택 communicator·등록/실행/종료/재기동 handle·세대/owner·GPU 완료와 최종 참조 해제 |

### 실행 기반

| CHK · 대응 분류 | SGLang 대응과 한계 | 개발·운영 담당이 확보할 실제 증거 |
| --- | --- | --- |
| [CHK-PLT-001](../03_체크리스트와_보완가이드.md#CHK-PLT-001)<br>외부 집행과 대응 | engine/subprocess의 실제 UID·mount·장치·capability를 최소 권한 표에 대조한다.<br>[LAUNCH](#source-launch) | 필요 권한표·실제 process/namespace·무해 비허용 파일/장치 접근 거부·정상 TP |
| [CHK-PLT-002](../03_체크리스트와_보완가이드.md#CHK-PLT-002)<br>외부 집행과 대응 | scheduler ZMQ·선택 weight daemon socket의 peer 접근을 채널별 확인한다.<br>[IPC](#source-ipc) · [SOCKET](#source-socket) · [WEIGHTPEER](#source-weightpeer) | 주소/상위 경로·owner/mode·namespace·동일/다른 UID 비허용 peer의 연결과 실제 메시지 소비 |
| [CHK-PLT-003](../03_체크리스트와_보완가이드.md#CHK-PLT-003)<br>외부 집행과 대응 | HF 다운로드 또는 로컬 반입 주체의 실제 자격 범위를 대조한다.<br>[HF](#source-hf) · [LOAD](#source-load) | SDK/provider identity·최종 저장소/객체·허용/비허용 접근 감사; 비밀값은 제외 |
| [CHK-PLT-004](../03_체크리스트와_보완가이드.md#CHK-PLT-004)<br>외부 집행과 대응 | subprocess 생성 시 worker가 받는 env·파일·mount 자격을 조사한다.<br>[LAUNCH](#source-launch) | 부모→worker의 자격 참조/수신자·필요성·실제 접근; 원문 secret 수집 금지 |
| [CHK-PLT-005](../03_체크리스트와_보완가이드.md#CHK-PLT-005)<br>외부 집행과 대응 | 취득 SDK의 만료/누락 자격과 cache/anonymous/provider 대체를 조사한다.<br>[HF](#source-hf) · [LOAD](#source-load) | 선정 SDK 판본·만료/누락 별 실제 선택 자격/공급원·허용 대체 정책·취득 결과 |
| [CHK-PLT-006](../03_체크리스트와_보완가이드.md#CHK-PLT-006)<br>외부 집행과 대응 | 동일 호스트 ZMQ·TP·reader가 공유하는 실제 UID/권한 경계를 기록한다.<br>[IPC](#source-ipc) · [SOCKET](#source-socket) · [UNPICKLE](#source-unpickle) | 채널별 serialization·허용 peer·UID/command 영향 범위·비허용 peer 차단과 잔여 위험 |
| [CHK-PLT-007](../03_체크리스트와_보완가이드.md#CHK-PLT-007)<br>외부 집행과 대응 | 다운로드·실행·OTLP 등 실제 송신 목적지를 운영 송신 정책에 대조한다.<br>[HF](#source-hf) · [OTLP](#source-otlp) | 프로세스별 DNS/연결·허용 목적지/작업·금지 무해 송신 거부·필요 취득/추적 정상 동작 |
| [CHK-PLT-008](../03_체크리스트와_보완가이드.md#CHK-PLT-008)<br>외부 집행과 대응 | HTTP TLS 인수와 로컬 IPC/TP 각각의 전송 구간을 구분한다.<br>[TLS](#source-tls) · [IPC](#source-ipc) · [ALLREDUCE](#source-allreduce) | 호출자→Gateway→engine 및 IPC/TP 구간별 peer/보호 정책·실제 인증/암호화/OS 통제 |
| [CHK-PLT-009](../03_체크리스트와_보완가이드.md#CHK-PLT-009)<br>선택 기능 대응 | OTLP gRPC/HTTP exporter와 collector→sink를 따로 연결한다.<br>[OTLP](#source-otlp) | 실제 exporter/SDK·endpoint·gRPC insecure 설정·망/인증/암호화·collector 이후 sink 구간 |
| [CHK-PLT-010](../03_체크리스트와_보완가이드.md#CHK-PLT-010)<br>외부 집행과 대응 | 정적 API key 및 외부 issuer/Gateway의 신규 접근 철회 계약을 대조한다.<br>[AUTH](#source-auth) · [HTTP](#source-http) | 실제 철회 주체/시각·전파 기한·key 갱신/재시작 조건·기한 뒤 신규 요청 거부 |
| [CHK-PLT-011](../03_체크리스트와_보완가이드.md#CHK-PLT-011)<br>외부 집행과 대응 | 열린 stream·native session·IPC handle의 철회 후 처리를 각각 선정한다.<br>[WAIT](#source-wait) · [SESSION](#source-session) · [WEIGHTLIVE](#source-weightlive) | 철회 전 확보한 연결/객체/handle·유지/종료 정책·기한 뒤 실제 접근 |
| [CHK-PLT-012](../03_체크리스트와_보완가이드.md#CHK-PLT-012)<br>외부 집행과 대응 | 배포·worker·파일/Secret 사본과 이전 key의 재사용 가능성을 조사한다.<br>[AUTH](#source-auth) · [LAUNCH](#source-launch) | 자격 사본 위치/수신자·교체/폐기·기존 사본으로 실제 신규/기존 접근 결과 |

### 관측·기록

| CHK · 대응 분류 | SGLang 대응과 한계 | 개발·운영 담당이 확보할 실제 증거 |
| --- | --- | --- |
| [CHK-OBS-001](../03_체크리스트와_보완가이드.md#CHK-OBS-001)<br>외부 집행과 대응 | 엔진 request 로그와 외부 승인/배포/관리 이력을 하나의 실행 식별로 연결한다.<br>[LOGGER](#source-logger) · [LOAD](#source-load) · [ADMIN](#source-admin) | 필수 사건 정책·주체/대상/실행 ID·실제 발생 사건과 확보 기록·승인 대상 일치 |
| [CHK-OBS-002](../03_체크리스트와_보완가이드.md#CHK-OBS-002)<br>엔진 경로 대응 | 준비·warmup·watchdog·shutdown·선택 profiler 상태를 실제 실행과 대조한다.<br>[READY](#source-ready) · [WARMUP](#source-warmup) · [WATCH](#source-watch) · [SHUTDOWN](#source-shutdown) · [PROFILE](#source-profile) | 상태 기록과 rank 준비/실패/작업 종료/수집 종료의 독립 관측·시각 |
| [CHK-OBS-003](../03_체크리스트와_보완가이드.md#CHK-OBS-003)<br>엔진 경로 대응 | request log level·crash dump·trace/profiler별 수집 필드를 조사한다.<br>[LOGARGS](#source-logargs) · [LOGGER](#source-logger) · [OTLP](#source-otlp) · [PROFILE](#source-profile) | 유효 설정·sink별 실제 원본/사본·합성 민감 marker·필수 사건 유지 |
| [CHK-OBS-004](../03_체크리스트와_보완가이드.md#CHK-OBS-004)<br>동일 기능 매핑 보류 | srt에서 usage_stats/telemetry/do_not_track 탐색과 관측 코드를 대조했으나 vLLM형 제품 사용 통계 reporter 대응은 확보하지 못했다. OTel/요청 token usage를 동일시하지 않는다.<br>[LOGARGS](#source-logargs) · [OTLP](#source-otlp) | 선정 설치 패키지·entrypoint·내장 reporter/외부 송신 조사, 프로세스 초기/주기 생성·송신 관측 뒤 비사용 여부 판단 |
| [CHK-OBS-005](../03_체크리스트와_보완가이드.md#CHK-OBS-005)<br>동일 기능 매핑 보류 | 허용 usage 보고도 같은 reporter 매핑이 먼저 필요하다. 팀 참조의 금지 정책은 유지한다.<br>[LOGARGS](#source-logargs) · [OTLP](#source-otlp) | 기능 조사 결과·조직이 보고를 허용한 경우에만 필드/목적지/주기·실제 payload 대조 |
| [CHK-OBS-006](../03_체크리스트와_보완가이드.md#CHK-OBS-006)<br>외부 집행과 대응 | request/crash/profiler dump와 collector 저장 사본의 독자·암호화는 저장소 집행점에서 확인한다.<br>[LOGARGS](#source-logargs) · [LOGGER](#source-logger) · [PROFILE](#source-profile) | 사본별 실제 reader/ACL·조회/반출·자료 등급별 암호화/복호화 권한·정상 조회 |
| [CHK-OBS-007](../03_체크리스트와_보완가이드.md#CHK-OBS-007)<br>선택 기능 대응 | debug dump loader 등 실제 배치된 consumer의 읽기권·실행권을 구분한다.<br>[DUMP](#source-dump) | 배치/도달성·실행 계정·읽기 전용 역할의 실행 시도·승인 도구 정상 소비 |
| [CHK-OBS-008](../03_체크리스트와_보완가이드.md#CHK-OBS-008)<br>선택 기능 대응 | dump reader의 역직렬화와 실행 권한을 선택 consumer별로 확인한다.<br>[DUMP](#source-dump) · [PT](#source-pt) | 변조한 무해 dump의 소비/거부·파일/통신/코드 부수 동작·격리 권한·정상 자료 처리 |
| [CHK-OBS-009](../03_체크리스트와_보완가이드.md#CHK-OBS-009)<br>엔진 경로 대응 | native/OpenAI request log의 사용자 입력과 신뢰 필드·사건 경계를 대조한다.<br>[LOGGER](#source-logger) · [BASE](#source-base) | 로그 형식/실제 sink parser·입력 개행/ID/header 변형·최종 소비된 주체/대상/상태 |
| [CHK-OBS-010](../03_체크리스트와_보완가이드.md#CHK-OBS-010)<br>외부 집행과 대응 | 최종 기록 저장소의 writer/reader/admin 권한과 독립 탐지를 연결한다.<br>[LOGGER](#source-logger) | 실제 ACL·비허용 변경/삭제 거부·필수 사건 탐지/기한·별도 보존 증거 |
| [CHK-OBS-011](../03_체크리스트와_보완가이드.md#CHK-OBS-011)<br>외부 집행과 대응 | 엔진→collector→sink 수집 건전성과 판정 종속 관계를 연결한다.<br>[LOGGER](#source-logger) · [OTLP](#source-otlp) | 장애/누락/시각 오류 주입·식별/경보 시각·대체 증거·확인 위반 유지/의존 시험 미확인 |
| [CHK-OBS-012](../03_체크리스트와_보완가이드.md#CHK-OBS-012)<br>외부 집행과 대응 | 실제 로그·진단·usage 사본/백업의 보존·삭제 계약을 확인한다.<br>[LOGARGS](#source-logargs) · [LOGGER](#source-logger) · [PROFILE](#source-profile) | 사본 목록·보존/예외/삭제 정책·실제 조회/복구 결과; KV 파일은 별도 범위 검토 필요 |

### 생명주기

| CHK · 대응 분류 | SGLang 대응과 한계 | 개발·운영 담당이 확보할 실제 증거 |
| --- | --- | --- |
| [CHK-LIF-001](../03_체크리스트와_보완가이드.md#CHK-LIF-001)<br>엔진 경로 대응 | 다운로드·loader·선택 compile의 단계별/누적 비용과 준비 조건을 연결한다.<br>[HF](#source-hf) · [LOAD](#source-load) · [COMPILEDIR](#source-compiledir) · [WARMUP](#source-warmup) | 실제 모델/설정·단계별 시간/CPU/RAM/GPU/디스크/전체 비용·정상 준비·반환; 값은 미정 유지 |
| [CHK-LIF-002](../03_체크리스트와_보완가이드.md#CHK-LIF-002)<br>외부 집행과 대응 | HF 등 실제 취득 SDK/도구의 재시도 경로·예산을 확인한다.<br>[HF](#source-hf) | 실제 SDK/환경·취득 실패별 횟수/시간/누적 비용·부분 파일 정리·정상 재취득 |
| [CHK-LIF-003](../03_체크리스트와_보완가이드.md#CHK-LIF-003)<br>엔진 경로 대응 | concurrency/queue/token pool/chunking과 native/추가 반환 비용을 대조한다.<br>[BUDGET](#source-budget) · [INPUT](#source-input) · [SAMPLING](#source-sampling) | 해석 후 유효 예산·요청별/전체 자원·대기/공정성·정상 요청 성능; 금액/기한 임의 입력 금지 |
| [CHK-LIF-004](../03_체크리스트와_보완가이드.md#CHK-LIF-004)<br>엔진 경로 대응 | stream 대기·출력 생성·연결 종료와 진단 buffer 비용을 연결한다.<br>[BASE](#source-base) · [WAIT](#source-wait) · [LOGARGS](#source-logargs) | 느린 수신·종료 전후 queue/buffer/RAM/GPU·정리 조건·동시 정상 요청 영향 |
| [CHK-LIF-005](../03_체크리스트와_보완가이드.md#CHK-LIF-005)<br>외부 집행과 대응 | Gateway 접수 중단과 engine 종료→새 rank 준비→재접수를 인계한다.<br>[SHUTDOWN](#source-shutdown) · [READY](#source-ready) · [WARMUP](#source-warmup) | 구/신 image·model·세대·진행 요청 방침·실제 gate·혼합 없는 후속 정상 요청 |
| [CHK-LIF-006](../03_체크리스트와_보완가이드.md#CHK-LIF-006)<br>선택 기능 대응 | live weights·LoRA·관리 update route의 원자성/세대 일관성을 별도 선정한다.<br>[UPDATE](#source-update) · [COMMIT](#source-commit) | 선택 update/rolling 방식·요청별 최종 model/세대·중간 실패·rollback·cache 상태 |
| [CHK-LIF-007](../03_체크리스트와_보완가이드.md#CHK-LIF-007)<br>엔진 경로 대응 | scheduler 준비와 HTTP warmup/status 전환을 실제 접수 gate에 연결한다.<br>[READY](#source-ready) · [LAUNCH](#source-launch) · [WARMUP](#source-warmup) | 필수 rank/모델/graph 준비·skip 유효값·준비 전 거부/보류·준비 후 정상 추론 |
| [CHK-LIF-008](../03_체크리스트와_보완가이드.md#CHK-LIF-008)<br>엔진 경로 대응 | forward watchdog·subprocess 사망 감지를 장애 종류별 통지에 연결한다.<br>[WATCH](#source-watch) · [LAUNCH](#source-launch) | rank 예외/사망/지연/통신 정지별 실제 상위 통지·기한·불완전 결과 처리 |
| [CHK-LIF-009](../03_체크리스트와_보완가이드.md#CHK-LIF-009)<br>엔진 경로 대응 | subprocess 초기화·scheduler ready·warmup의 부분 실패를 대조한다.<br>[LAUNCH](#source-launch) · [READY](#source-ready) · [WARMUP](#source-warmup) | rank별 실패 주입·상위 상태·gate·성공 오기록 여부·후속 정상 기동 |
| [CHK-LIF-010](../03_체크리스트와_보완가이드.md#CHK-LIF-010)<br>엔진 경로 대응 | tokenizer disconnect/abort와 scheduler의 단계별 반환을 연결한다.<br>[WAIT](#source-wait) · [ABORT](#source-abort) · [SCHEDABORT](#source-schedabort) · [SESSION](#source-session) | 대기/계산/출력 단계별 abort·실제 GPU 작업 중단·req/KV/session 참조/자원 회수와 기한 |
| [CHK-LIF-011](../03_체크리스트와_보완가이드.md#CHK-LIF-011)<br>엔진 경로 대응 | engine·worker·communicator·선택 daemon 종료와 재기동을 연결한다.<br>[SHUTDOWN](#source-shutdown) · [ALLCLOSE](#source-allclose) · [WEIGHTLIVE](#source-weightlive) | PID/socket/SHM/GPU/handle별 실제 반환·최종 참조·실패 뒤 정상 재기동 |
| [CHK-LIF-012](../03_체크리스트와_보완가이드.md#CHK-LIF-012)<br>외부 집행과 대응 | Gateway/앱/엔진의 추론 retry 실제 사용과 이전 작업 수명을 확인한다.<br>[BASE](#source-base) · [ABORT](#source-abort) | retry 금지 유효 설정/경로·실제 재시도 미발생; 허용 시 이전/새 시도·중복 결과/비용·회수 |
| [CHK-LIF-013](../03_체크리스트와_보완가이드.md#CHK-LIF-013)<br>부분 대응 | weight daemon은 있으나 조사한 socket 프로토콜에는 전체 release 요청이 없다. daemon 종료·consumer 사망 감시·client fallback 부분을 대응한다.<br>[WEIGHTARGS](#source-weightargs) · [WEIGHTPROTO](#source-weightproto) · [WEIGHTLOAD](#source-weightload) · [WEIGHTPEER](#source-weightpeer) · [WEIGHTLIVE](#source-weightlive) | release 호출 분기는 설치/확장 도달성 근거로 비적용 검토; daemon 종료 권한·다른 consumer·재취득/fallback 승인·현재 참조/GPU 수명은 별도 미확인 |
| [CHK-LIF-014](../03_체크리스트와_보완가이드.md#CHK-LIF-014)<br>선택 기능 대응 | profiler stop의 backend별 처리와 종료 계약을 연결한다.<br>[ADMIN](#source-admin) · [PROFILE](#source-profile) | 선택 backend·stop 응답/실제 수집 중단·저장/전송/자원 반환·후속 정상 서비스 |
| [CHK-LIF-015](../03_체크리스트와_보완가이드.md#CHK-LIF-015)<br>외부 집행과 대응 | 이전 배포 묶음으로 돌아갈 때 현재 승인·철회·유효 설정을 외부 gate와 대조한다.<br>[LOAD](#source-load) · [LAUNCH](#source-launch) | 복귀 대상 실물·현재 승인/철회·rollback 주체·검증 전 gate·후속 모델/요청 |
| [CHK-LIF-016](../03_체크리스트와_보완가이드.md#CHK-LIF-016)<br>외부 집행과 대응 | 일반 재배포 복원과 선택 상태 복원 자료의 출처·잔존 상태를 분리한다.<br>[LOAD](#source-load) · [SHUTDOWN](#source-shutdown) · [HICACHEIO](#source-hicacheio) | 실제 backup/복원 범위·변조 무해 자료 거부·이전 cache/session 잔존·검증 전 서비스 gate |

## 중요한 대응 차이

1. **관리 API:** 일반/관리 key 설정과 ADMIN_OPTIONAL 조합을 route별 역할 정책으로 대조한다. key 설정만으로 사용자별 모델·session·저장 객체 인가가 완성됐다고 판정하지 않는다.
2. **입력과 반환:** native `/generate`와 OpenAI chat의 input_ids 경로를 함께 포함한다. prompt_token_ids 추가 반환·오류 필드도 endpoint별로 대조한다. 기존 REQ-008/013의 팀 참조 정책은 유지한다.
3. **공유 cache:** client가 보낸 salt와 검증된 신원 기반 공유 집단은 별개다. Radix key에 salt가 있다는 사실과 실제 교차 집단 재사용 거부를 구분한다.
4. **파일·메시지 해석:** PT legacy tar fallback, 선택 custom callable의 dill, dump reader, allowlist 기반 daemon reader를 각각 확인한다. 특정 reader 설정을 다른 reader의 안전성으로 전용하지 않는다.
5. **공유 weights:** daemon/client/off의 수명이 다르고, 조사한 daemon 메시지는 query_config/fetch_state/ping이다. LIF-013의 전체 release 호출 부분은 설치 확장까지 확인해 분기 처리하며, shutdown·fallback·기존 GPU 참조의 결과는 따로 남긴다.
6. **내장 usage:** 이번 source 조사에서는 vLLM형 reporter를 대응하지 못했다. OpenAI 응답 token usage·OTel 추적과 제품 사용 통계 보고를 섞지 않는다. SUP-008과 OBS-004/005는 미확인을 유지한다.

## 사용 순서와 역할

1. 프레임워크 담당자가 비교 후보와 위 두 보완 후보의 범위를 검토한다. SGLang 전체 기능을 기본 필수로 늘리지 않는다.
2. 개발·운영 담당이 실제 image/commit·모델·유효 설정·route/선택 backend·통제 위치를 채운다. source 대응이 다른 곳은 그 행만 갱신한다.
3. 정책 담당이 기존 정책 입력을 확정한다. 미정은 기존 거버넌스의 보류·책임자·기한·후속 처리 규칙으로 넘긴다.
4. 개발·시험 담당이 적용된 분기의 adapter/계측·정상 대조와 증거를 만든다. 프레임워크 담당은 기준에 따른 판정·미확인·보완 요청·재확인 조건을 검토한다.
5. source commit·모델/설정·auth/route·reader·cache/daemon·GPU backend·collector·배포/복구 방식이 바뀌면 영향 행과 연결된 정상/실패 흐름을 재확인한다. 기존 결과를 자동 승계하지 않는다.

## 고정 소스 근거

각 파일 SHA256과 CHK별 기준 hash는 [동일 JSON](SGLang_v0.5.22.json)에 기록했다. 아래는 조사한 시작점/구간이며 모든 호출 경로·의존성 검증 완료를 뜻하지 않는다.

<a id="source-auth"></a>
- **AUTH** · [python/sglang/srt/utils/auth.py:38–213](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/utils/auth.py#L38-L213): route별 NORMAL/ADMIN_OPTIONAL/ADMIN_FORCE와 키 조합을 처리한다. health/ready/metrics prefix와 OPTIONS의 허용 분기가 있다. 키는 최종 사용자 신원과 구분한다.

<a id="source-http"></a>
- **HTTP** · [python/sglang/srt/entrypoints/http_server.py:2670–2685](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L2670-L2685): 키·강제 관리 endpoint 조건에 따라 인증 middleware를 설치한다.

<a id="source-admin"></a>
- **ADMIN** · [python/sglang/srt/entrypoints/http_server.py:1009–1219](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L1009-L1219): cache·외부 corpus·HiCache·profiler·trace·GC 관리 route와 권한 등급이 등록된다.

<a id="source-update"></a>
- **UPDATE** · [python/sglang/srt/entrypoints/http_server.py:1265–1649](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L1265-L1649): weights 변경·메모리 점유 반환/복귀·LoRA 관리 접점이 있다. 별도 weight-cache daemon의 release 프로토콜과 구분한다.

<a id="source-load"></a>
- **LOAD** · [python/sglang/srt/model_loader/loader.py:478–600](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/model_loader/loader.py#L478-L600): 실제 weights 형식·파일 목록·다운로드·선택적 checksum 검증을 결정한다. 이 검증만으로 조직 승인이나 사용 시점 불변성을 입증하지 않는다.

<a id="source-commit"></a>
- **COMMIT** · [python/sglang/srt/model_loader/loader.py:845–910](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/model_loader/loader.py#L845-L910): 모델 준비와 weights 적재·후처리 경로가 구분된다.

<a id="source-config"></a>
- **CONFIG** · [python/sglang/srt/configs/model_config.py:492–600](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/configs/model_config.py#L492-L600): model config·하위 config를 revision/trust_remote_code 인수와 함께 읽는다. 호출된 SDK와 실제 소비 대상은 설치판에서 확인한다.

<a id="source-modelargs"></a>
- **MODELARGS** · [python/sglang/srt/arg_groups/fields/model.py:90–110](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/arg_groups/fields/model.py#L90-L110): 모델 loader 추가 설정과 trust_remote_code 선언 기본값 False를 확인했다.

<a id="source-verify"></a>
- **VERIFY** · [python/sglang/srt/utils/model_file_verifier.py:74–98](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/utils/model_file_verifier.py#L74-L98): manifest에 열거된 파일의 SHA256 일치를 검사한다. 승인 목록 전체성과 manifest 변경 권한은 외부 증거가 필요하다.

<a id="source-download"></a>
- **DOWNLOAD** · [python/sglang/srt/model_loader/weight_utils.py:165–175](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/model_loader/weight_utils.py#L165-L175): 다운로드 파일 lock 경로를 사용한다. 저장소 자격·실제 파일 ACL은 운영 환경에서 확인한다.

<a id="source-hf"></a>
- **HF** · [python/sglang/srt/model_loader/weight_utils.py:621–714](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/model_loader/weight_utils.py#L621-L714): HF weights 취득 함수와 snapshot 다운로드 경로를 확인할 지점이다. 설치 huggingface_hub의 재시도·자격 선택은 추가 조사 대상이다.

<a id="source-pt"></a>
- **PT** · [python/sglang/srt/model_loader/weight_utils.py:1398–1415](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/model_loader/weight_utils.py#L1398-L1415): PT reader는 weights_only=True를 먼저 시도하고 legacy tar RuntimeError 조건에서 False로 재시도한다. 설치 torch와 공급물 허용 정책을 함께 확인한다.

<a id="source-compile"></a>
- **COMPILE** · [python/sglang/srt/compilation/backend.py:94–143](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/compilation/backend.py#L94-L143): compile cache의 읽기·쓰기·compiler 연결 경로가 있다. .py 이름의 cache index는 ast.literal_eval로 읽는다.

<a id="source-compiledir"></a>
- **COMPILEDIR** · [python/sglang/srt/compilation/backend.py:407–474](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/compilation/backend.py#L407-L474): cache hash·rank·model_tag별 경로 및 graph 코드 생성 경로가 있다. 실제 선택 여부와 모든 하위 compiler cache는 설치 구성에서 확인한다.

<a id="source-plugin"></a>
- **PLUGIN** · [python/sglang/srt/plugins/__init__.py:35–141](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/plugins/__init__.py#L35-L141): sglang.srt.platforms/general plugin 발견·SGLANG_PLUGINS 필터·로드·hook 적용 경로가 있다. 설치된 그룹별 조사 필요.

<a id="source-dynamic"></a>
- **DYNAMIC** · [python/sglang/srt/mem_cache/storage/backend_factory.py:21–165](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/mem_cache/storage/backend_factory.py#L21-L165): 동적 storage backend의 module_path/class_name import 경로가 있다. general plugin 필터의 보호 범위와 별도다.

<a id="source-callable"></a>
- **CALLABLE** · [python/sglang/srt/sampling/custom_logit_processor.py:15–48](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/sampling/custom_logit_processor.py#L15-L48): 요청용 custom logit processor의 callable 역직렬화는 dill.loads를 사용한다.

<a id="source-callgate"></a>
- **CALLGATE** · [python/sglang/srt/managers/tokenizer_manager.py:1417–1425](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/managers/tokenizer_manager.py#L1417-L1425): custom logit processor 활성 여부를 검사하는 지점이다.

<a id="source-callargs"></a>
- **CALLARGS** · [python/sglang/srt/arg_groups/fields/exec_.py:68–71](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/arg_groups/fields/exec_.py#L68-L71): enable_custom_logit_processor 선언 기본값 False. 실제 실행의 유효값과 거부 결과는 미수집.

<a id="source-jinja"></a>
- **JINJA** · [python/sglang/srt/parser/jinja_template_utils.py:73–80](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/parser/jinja_template_utils.py#L73-L80): template AST 분석은 transformers의 _compile_jinja_template에 의존한다. 실제 renderer 안전성은 설치 의존성과 함께 확인한다.

<a id="source-chatgate"></a>
- **CHATGATE** · [python/sglang/srt/entrypoints/openai/serving_chat.py:1038–1048](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/openai/serving_chat.py#L1038-L1048): 요청 chat_template override에 trust_request_chat_template 조건을 검사한다.

<a id="source-chat"></a>
- **CHAT** · [python/sglang/srt/entrypoints/openai/serving_chat.py:1458–1476](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/openai/serving_chat.py#L1458-L1476): input_ids 입력이면 template 토큰화 경로를 건너뛰는 분기가 있다.

<a id="source-render"></a>
- **RENDER** · [python/sglang/srt/entrypoints/openai/serving_chat.py:1705–1759](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/openai/serving_chat.py#L1705-L1759): template kwargs와 render/encode 경로를 연결한다. 공급 template 허용과 최종 입력 정책을 따로 판정한다.

<a id="source-input"></a>
- **INPUT** · [python/sglang/srt/managers/io_struct.py:180–300](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/managers/io_struct.py#L180-L300): native generate의 text/input_ids/input_embeds와 추가 반환·custom processor 입력 필드가 있다.

<a id="source-validate"></a>
- **VALIDATE** · [python/sglang/srt/managers/tokenizer_manager.py:1308–1375](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/managers/tokenizer_manager.py#L1308-L1375): context length·절단·total token 조건을 검사한다. 이 구간을 input_ids 전체의 vocab 검증 증거로 사용하지 않는다.

<a id="source-tokenize"></a>
- **TOKENIZE** · [python/sglang/srt/managers/tokenizer_manager.py:1535–1625](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/managers/tokenizer_manager.py#L1535-L1625): sampling normalize/verify와 tokenized request 구성·cache_salt·session 전달이 이어진다.

<a id="source-sampling"></a>
- **SAMPLING** · [python/sglang/srt/sampling/sampling_params.py:229–305](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/sampling/sampling_params.py#L229-L305): sampling 수치·logit_bias token·grammar 조합 검증이 있다. 모든 backend의 실행 안전성은 별도다.

<a id="source-base"></a>
- **BASE** · [python/sglang/srt/entrypoints/openai/serving_base.py:40–135](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/openai/serving_base.py#L40-L135): model:adapter 선택과 OpenAI 요청 변환·stream 분기·예외 반환을 처리한다. 최종 사용자별 인가는 외부 정책과 대조한다.

<a id="source-return"></a>
- **RETURN** · [python/sglang/srt/entrypoints/openai/serving_chat.py:2008–2235](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/openai/serving_chat.py#L2008-L2235): stream에서 내부 prompt_token_ids를 추출하고 선택 조건에 따라 sglext.input_ids로 추가 반환한다. 정상 응답의 입력 인용과 구분한다.

<a id="source-returnchoice"></a>
- **RETURNCHOICE** · [python/sglang/srt/entrypoints/openai/serving_chat.py:2451–2492](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/openai/serving_chat.py#L2451-L2492): 비스트리밍 choice의 prompt_token_ids 추가 반환 경로다. 실제 허용 필드는 조직 정책과 대조한다.

<a id="source-error"></a>
- **ERROR** · [python/sglang/srt/entrypoints/http_server.py:2173–2177](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L2173-L2177): native 오류 envelope에서 str(e)를 반환한다. 실제 노출 필드는 합성 오류 시험으로 확인한다.

<a id="source-store"></a>
- **STORE** · [python/sglang/srt/entrypoints/openai/serving_responses.py:204–213](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/openai/serving_responses.py#L204-L213): Responses 저장 dict·lock·활성 설정을 사용한다. session cache와 구분한다.

<a id="source-object"></a>
- **OBJECT** · [python/sglang/srt/entrypoints/openai/serving_responses.py:1581–1628](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/openai/serving_responses.py#L1581-L1628): 저장 response 조회·취소 함수는 ID 기반 lookup을 사용한다. 여기에는 사용자 principal 인수가 없으며 전체 경유 인가는 설치 환경에서 확인한다.

<a id="source-storeargs"></a>
- **STOREARGS** · [python/sglang/srt/arg_groups/fields/serving.py:26–31](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/arg_groups/fields/serving.py#L26-L31): Responses 저장 선언 기본값 False. 메모리 저장·크기/TTL 계약은 활성화할 때 조사한다.

<a id="source-wait"></a>
- **WAIT** · [python/sglang/srt/managers/tokenizer_manager.py:1950–1978](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/managers/tokenizer_manager.py#L1950-L1978): 응답 대기 중 연결 종료를 확인하고 abort를 요청한다.

<a id="source-abort"></a>
- **ABORT** · [python/sglang/srt/managers/tokenizer_manager.py:2240–2260](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/managers/tokenizer_manager.py#L2240-L2260): 요청 abort 전달 경로다. HTTP 취소 응답은 GPU 작업·자원 회수 완료의 증거와 구분한다.

<a id="source-schedabort"></a>
- **SCHEDABORT** · [python/sglang/srt/managers/scheduler.py:5458–5523](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/managers/scheduler.py#L5458-L5523): 대기열·선택 상태별 abort와 자원 반환 경로가 다르다. grammar queue는 이후 prefill 처리 설명이 있어 즉시 모든 작업 종료로 해석하지 않는다.

<a id="source-key"></a>
- **KEY** · [python/sglang/srt/mem_cache/radix_cache.py:59–77](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/mem_cache/radix_cache.py#L59-L77): RadixKey에 token_ids/extra_key/cache_salt가 포함된다.

<a id="source-keymatch"></a>
- **KEYMATCH** · [python/sglang/srt/mem_cache/radix_cache.py:166–175](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/mem_cache/radix_cache.py#L166-L175): extra_key/cache_salt 호환성을 비교한다. salt 자체는 인증된 사용자 소유권 증거가 아니다.

<a id="source-radix"></a>
- **RADIX** · [python/sglang/srt/mem_cache/radix_cache.py:364–508](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/mem_cache/radix_cache.py#L364-L508): cache match/insert와 요청 key 구성이 연결된다.

<a id="source-batch"></a>
- **BATCH** · [python/sglang/srt/sampling/sampling_batch_info.py:466–646](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/sampling/sampling_batch_info.py#L466-L646): batch filter/merge 시 sampling 상태를 다루는 경로를 확인한다. 실제 grammar/mask/logit 상태 전부의 이행은 선택 backend에서 추적한다.

<a id="source-gpu"></a>
- **GPU** · [python/sglang/srt/layers/attention/flashinfer_backend.py:77–106](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/layers/attention/flashinfer_backend.py#L77-L106): 선택 GPU backend의 외부 kernel 의존성 조사 시작점이다. 이 파일 검토로 native kernel 메모리 안전성을 확정하지 않는다.

<a id="source-graph"></a>
- **GRAPH** · [python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py:1117–1240](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py#L1117-L1240): CUDA Graph capture 및 shape별 capture 경로다.

<a id="source-graphinput"></a>
- **GRAPHINPUT** · [python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py:1404–1450](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py#L1404-L1450): graph 재실행용 입력 buffer 복사 지점이다. 출력 소비 완료·동시 재사용은 실제 계측이 필요하다.

<a id="source-allreduce"></a>
- **ALLREDUCE** · [python/sglang/srt/distributed/device_communicators/custom_all_reduce.py:230–258](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/distributed/device_communicators/custom_all_reduce.py#L230-L258): 일반·graph 통신 buffer 등록 경로다. 실제 선택 communicator를 확인한다.

<a id="source-allclose"></a>
- **ALLCLOSE** · [python/sglang/srt/distributed/device_communicators/custom_all_reduce.py:332–340](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/distributed/device_communicators/custom_all_reduce.py#L332-L340): dispose와 공유 buffer 반환을 호출한다. GPU 완료·최종 참조 해제는 별도 증거가 필요하다.

<a id="source-ipc"></a>
- **IPC** · [python/sglang/srt/managers/scheduler_components/ipc_channels.py:25–88](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/managers/scheduler_components/ipc_channels.py#L25-L88): scheduler의 ZMQ 수신·RPC·출력 및 선택 metrics IPC 채널을 구성한다.

<a id="source-socket"></a>
- **SOCKET** · [python/sglang/srt/utils/network.py:400–436](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/utils/network.py#L400-L436): ZMQ endpoint bind/connect 경로다. 주소·소유자·namespace·접근 가능 peer를 설치 환경에서 확인한다.

<a id="source-unpickle"></a>
- **UNPICKLE** · [python/sglang/srt/utils/common.py:3417–3441](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/utils/common.py#L3417-L3441): SafeUnpickler allowlist와 torch storage 특별 처리 경로가 있다. 모든 IPC reader가 이 함수를 쓰는지는 채널별 추가 확인한다.

<a id="source-tls"></a>
- **TLS** · [python/sglang/srt/entrypoints/http_server.py:2726–2747](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L2726-L2747): HTTP serving의 SSL 인수 전달 지점이다. 실제 API/IPC/TP 모든 구간의 보호는 배포 증거로 대조한다.

<a id="source-otlp"></a>
- **OTLP** · [python/sglang/srt/observability/trace.py:277–290](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/observability/trace.py#L277-L290): gRPC exporter는 insecure=True, HTTP exporter는 endpoint를 전달한다. 선택 프로토콜·구간 보호·collector 이후 sink는 별도로 대조한다.

<a id="source-logargs"></a>
- **LOGARGS** · [python/sglang/srt/arg_groups/fields/observability.py:30–70](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/arg_groups/fields/observability.py#L30-L70): log_requests 선언 기본값 False, 입력/출력을 담는 log level 및 선택 crash dump folder를 제공한다.

<a id="source-logger"></a>
- **LOGGER** · [python/sglang/srt/utils/request_logger.py:89–173](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/utils/request_logger.py#L89-L173): native/OpenAI 요청 로깅과 입력 토큰 decode/model_dump 경로다. 실제 writer·reader·sink 필드는 운영 증거가 필요하다.

<a id="source-dump"></a>
- **DUMP** · [python/sglang/srt/debug_utils/dump_loader.py:32–85](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/debug_utils/dump_loader.py#L32-L85): debug dump reader의 torch.load(weights_only=False) 소비 지점이다. 도구의 실제 배치·사용·계정은 미확인이다.

<a id="source-profile"></a>
- **PROFILE** · [python/sglang/srt/managers/scheduler_components/profiler_manager.py:318–395](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/managers/scheduler_components/profiler_manager.py#L318-L395): profiler 중지·각 backend 결과 처리 경로다. backend별 종료 계약과 실제 수집 중단을 구분한다.

<a id="source-budget"></a>
- **BUDGET** · [python/sglang/srt/arg_groups/fields/schedule.py:27–94](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/arg_groups/fields/schedule.py#L27-L94): 동시 실행/대기 수·token pool·chunking·scheduling 정책 입력을 제공한다. None 선언값을 무제한 또는 유효 예산으로 단정하지 않고 해석 후 값을 수집한다.

<a id="source-ready"></a>
- **READY** · [python/sglang/srt/entrypoints/engine.py:989–1025](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/engine.py#L989-L1025): scheduler 준비 대기와 watchdog 연결 지점이다.

<a id="source-launch"></a>
- **LAUNCH** · [python/sglang/srt/entrypoints/engine.py:1121–1368](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/engine.py#L1121-L1368): subprocess와 선택 daemon 기동 흐름이다. 각 rank 준비·부분 실패·실제 mount/env는 별도 확인한다.

<a id="source-warmup"></a>
- **WARMUP** · [python/sglang/srt/entrypoints/http_server.py:2412–2501](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L2412-L2501): warmup과 준비 상태 전환 흐름이다. skip 설정과 실제 접수 gate를 대조한다.

<a id="source-watch"></a>
- **WATCH** · [python/sglang/srt/utils/watchdog.py:103–223](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/utils/watchdog.py#L103-L223): 실행 지연 감시와 subprocess 사망 감지 경로가 있다. 종류별 통지 기한은 실제 시험으로 확인한다.

<a id="source-shutdown"></a>
- **SHUTDOWN** · [python/sglang/srt/entrypoints/engine.py:1438–1477](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/engine.py#L1438-L1477): engine shutdown과 선택 daemon 종료 호출이다. 전체 PID·socket·GPU 자원 반환 및 다음 기동은 미시험이다.

<a id="source-weightargs"></a>
- **WEIGHTARGS** · [python/sglang/srt/arg_groups/fields/model.py:371–397](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/arg_groups/fields/model.py#L371-L397): weight_cache_mode=off/daemon/client, 선언 기본값 off. engine 소속 daemon과 standalone daemon의 수명이 다르다.

<a id="source-weightproto"></a>
- **WEIGHTPROTO** · [python/sglang/srt/weight_cache/daemon.py:556–624](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/weight_cache/daemon.py#L556-L624): query_config/fetch_state/ping을 처리하며 이 프로토콜에는 release 요청이 없다. shutdown은 model/entries 정리·empty_cache를 호출한다.

<a id="source-weightload"></a>
- **WEIGHTLOAD** · [python/sglang/srt/weight_cache/ipc_loader.py:85–124](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/weight_cache/ipc_loader.py#L85-L124): daemon mode의 disk fallback 금지와 client mode의 fallback 분기가 있다.

<a id="source-weightpeer"></a>
- **WEIGHTPEER** · [python/sglang/srt/weight_cache/ipc_loader.py:451–500](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/weight_cache/ipc_loader.py#L451-L500): socket lstat 종류·UID 검사, 부재와 연결 오류를 구분한다. 동일 UID의 모델별 소비 권한은 별도 확인한다.

<a id="source-weightlive"></a>
- **WEIGHTLIVE** · [python/sglang/srt/weight_cache/ipc_loader.py:183–228](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/weight_cache/ipc_loader.py#L183-L228): daemon PID 감시·사망 시 client 종료 경로가 있다. 실제 PID/참조 수명과 GPU 작업 완료는 미확인이다.

<a id="source-sessionhttp"></a>
- **SESSIONHTTP** · [python/sglang/srt/entrypoints/http_server.py:1652–1673](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L1652-L1673): native open_session/close_session route가 있다. 일반 응답 저장 객체와 별개다.

<a id="source-session"></a>
- **SESSION** · [python/sglang/srt/session/session_controller.py:370–455](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/session/session_controller.py#L370-L455): session ID 기반 조회·생성·종료와 진행 요청의 종료 지연·cache 반환 처리를 한다. tenant 소유권과 종료 권한은 전체 경유 구조로 확인한다.

<a id="source-hicache"></a>
- **HICACHE** · [python/sglang/srt/mem_cache/hicache_storage.py:386–420](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/mem_cache/hicache_storage.py#L386-L420): 선택 HiCacheFile의 저장 경로와 모델·TP/PP/CP suffix를 구성한다.

<a id="source-hicacheio"></a>
- **HICACHEIO** · [python/sglang/srt/mem_cache/hicache_storage.py:484–541](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/mem_cache/hicache_storage.py#L484-L541): KV cache raw bytes 파일의 read/write 경로다. 파일 접근·내용 변조·보존 정책을 cache key 검사와 구분한다.
