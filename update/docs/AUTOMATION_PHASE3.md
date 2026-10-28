# 자동화 3차: 연결 귀속과 실제 로그 수집 경계

> 2026-10-27 저장 경계 보수: 현재 `request_binding` 0.3.0의 `request-binding-response-projection/1`은 원문 요청·응답·SSE event·token 배열·렌더 문구·API/연결 ID 대신 hash와 허용 metadata를 저장한다. 기존 `analyze_response`는 완전한 transient 자료로 계속 판정하고 반환값의 원문 필드만 저장 직전에 투영한다. 수집기·JSON/SSE 분석·시간 측정·구간 중첩 계산과 fixture 비교 hash는 유지한다. 저장 시간 필드는 기존 측정값만 직렬화하며 잘못된 타입/비유한 값은 null이다. 원문 body의 JSON parse 상태는 저장 metadata이며 SSE 검증 결과를 대신하지 않는다. 한 응답의 잘못된 outcome은 그 응답의 수집 공백으로 한정해 다른 연결의 유효 위반을 보존한다. 출력 귀속은 여전히 미확인이고 보안 PASS를 만들지 않는다. 아래는 최초 3차 구현 당시 기록이며 과거 팩·증거·Ref는 보존한다.

기준은 `1952533efa0c4cd292fdbaede9f0abda7977fb30`, 작업 브랜치는 `codex/gua-automation-phase3`다. [실행 프롬프트](AUTOMATION_PHASE3_PROMPT.md)의 REQ-11·OBS-09 두 제한 프로파일을 구현한다. 전체 구현 후 기본 회귀·실제 CPU·저장 증거·설치본을 검증했다. 직접 결과와 제한은 아래에 기록한다.

## 범위와 정책

| 점검 | 입력 | 지원하는 시험 범위 | 별도로 남는 범위 |
|---|---|---|---|
| REQ-11 | result_binding, client_request_id_trust | originating_connection / untrusted_correlation, 직접 엔진 일반 JSON·SSE 두 연결 | 기업 신원·독자 권한·Gateway·SDK·child·취소·재시도·저장 결과·worker 내부 병렬성 |
| OBS-09 | collector_profile, required_trusted_fields, client_request_id_trust | guard-vllm-native-info-v1, process/level/source, untrusted_correlation | 기업 SIEM·사용자/actor/status·커스텀 formatter·원시 daemon 바이트·prompt DEBUG |

값의 형태 검증과 지원 프로파일 판별을 분리한다. 다른 비어 있지 않은 기업 정책값은 형태상 유효해도 이 수집 경로가 입증하지 못하면 UNKNOWN이다. process/level/source도 로그가 제공하는 진단 필드이지 기업 승인·신원 인증을 의미하지 않는다. 원래 카탈로그의 복합 기업 정책 317행은 보존하고 현재 실행 가능한 입력 투영 5행을 덧붙인다.

## 실행 경로

환경·DRAFT 정책 → select → 제품 registry의 실제 실행기 → owned Docker probe → EVD 원 관측·첨부 → 저장된 rule_table 또는 declared 상한 → 축별 RES → JSON/HTML RPT 흐름을 사용한다. 코어·객체·저장소는 바꾸지 않는다. REQ-07/REQ-11의 고정 template 읽기 전용 신원 확인은 작은 공통 helper로 재사용한다.

REQ-11은 별도 HTTP/1.1 연결 두 개와 공통 송신 barrier를 사용한다. 생성 ID/다른 body ID/같은 body ID/같은 헤더 ID의 4쌍을 JSON·SSE에서 각각 실행한다. 실제 소비 prompt IDs와 렌더 문구를 독립 tokenize 결과에 대조한다. 같은 외부 ID 자체는 유출 근거가 아니다. 클라이언트 수집 구간 중첩은 CPU max_num_seqs=1의 worker 내부 병렬 실행을 입증하지 않는다. SSE 종료·HTTP framing 종료·엄격 UTF-8·실제 캡처 해시를 따로 기록한다.

OBS-09는 실제 INFO request logger → NewLineFormatter → Docker CLI UTF-8 디코딩 → guard 참조 수집기 → 구조화 JSON·별도 escaped HTML EVD 첨부를 사용한다. formatter의 LF→CRLF+동일 prefix 변환 때문에 LF만 구분하고 CR을 보존한다. 본문의 key=value·HTML·외부 ID를 trusted field로 승격하지 않는다. 원시 daemon 바이트 digest로 표시하지 않으며 대체 디코딩·경계 불명확은 충분한 증거가 아니다. 별도 HTML 첨부와 주요 RPT의 판정/제한 연결을 구분한다.

## 명시적 CPU 프로파일

[별도 설정](../examples/windows-cpu-binding-logs.toml)은 기존 CPU chat 설정에 `logging_profile='request-info-v1'`을 명시한다. 고정 argv와 logging env·소스 SHA를 실제 기동 및 비교 조건에 기록한다. 임의 config/class/path를 받지 않는다. 기존 기본 completion/chat 예제의 기동 인자는 보존한다.

고정 CPU 이미지 `vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b`, vLLM0.31.0, facebook/opt-125m revision `27dcfa74d334bc871f3234de431e71c6eeba5dd6`, CPU2/threads2/RAM3GiB/RAM+swap5GiB/shm512MiB/PID256/FP32/context128/KV64MiB를 사용한다. 실제 모델 inventory는 실행 시 다시 확인한다. 엔진은 하나씩이며 기존 kind 컨테이너는 보존한다.

요청 timeout은 최대120초, 새 요청 dispatch 총예산300초다. 별도의 Docker 기동·읽기 전용 inspect·정리 시간을 벽시계300초라고 주장하지 않는다. REQ-11은 최대32요청/출력16token/두 연결/읽기 지연250ms, 수집기는 응답128KiB/event64/line16KiB/header8KiB/요청body16KiB로 제한한다. OBS-09는 최대12요청/ID512bytes/수집128KiB/event128로 제한한다. Docker CLI 캡처 후 크기 검사와 전송 단계 메모리 상한은 구분한다.

[예시 정책](../examples/automation-binding-logs-policy.yaml)은 EXAMPLE/DRAFT이며 기업 기본값·승인이 아니다. ENV와 실제 model 경로를 준비해 `guard prepare-validation`을 먼저 실행하고, `guard validate`를 새 store/output에 실행한다. 기존 정책의 다른 값은 변경하지 않는다. 모든 예시·실증은 팩/정책 DRAFT에 따라 official=false이며 성능은 UNKNOWN이다.

## 검증 기록

직접 실행한 이번 결과다. [선별 증거 JSON](evidence/automation-phase3-20261023.json)은 두 시도의 EVD·RES·RPT Ref, 첨부 SHA/bytes, 실행 시점별 소스 SHA와 최종 소스 SHA를 분리해 기록한다. 선별 JSON SHA는 b65b4742cdc94e374f84515ccc4db4a7e0e5aa5a464e14253ced0ef6a4d9791a다. 원시 store·전체 로그·JSON/HTML·bundle·명령은 저장소 밖 새 출력에 보존한다. 선별 JSON만으로 전체 bundle을 복원하거나 기업 승인 진위를 입증하지 않는다.

환경은 Windows11 x64·Intel Core Ultra7 255H·Docker Desktop Linux/WSL2·CPython3.11.16·동결 의존성 전용 venv다. GPU는 사용하지 않았다. 모델7파일 inventory는 b11bc2cafab45720afd3d2f19c8250352977bc216efb88fc0afb9d90edc2aea6이며 이미지·모델 revision·CPU 상한은 위와 같다. source commit은 UNVERIFIED이며 실제 이미지의 파일 SHA와 줄 번호로 계약을 한정한다.

| 실제 시도 | 점검 | 보안 | 기능 | 성능 | 직접 확인 |
|---|---|---|---|---|---|
| 01 | REQ-11 | PASS | PASS | UNKNOWN | tokenize2+추론18=20요청, JSON4쌍/SSE4쌍의 실제 소비 IDs/text·연결 귀속 |
| 01 | OBS-09 | PASS | PASS | UNKNOWN | 7개 ID 실제 INFO 도달·CRLF 보존·parser/표시 내용. 아래 표시 digest 결함은 별도 보수 |
| 01 | PLT-01 | PASS | PASS | UNKNOWN | 명시적 logging/template CPU 강화 구성·정상 요청, nonroot/RO/capdrop 유지 |
| 02 | OBS-09 | PASS | PASS | UNKNOWN | 같은 정책·7개 사례와 기록 display SHA=실제 UTF-8 첨부 SHA까지 재검증 |

REQ-11의 A/B는 각각20토큰이며 서로 다른 ID열/렌더 문구다. 18추론의 입력20/출력8/합계28 usage가 일치했고 SSE8개는 각11event·finish1·DONE1·framing EOF를 확인했다. 외부 ID가 같은4쌍도 각자의 입력을 소비했다. 송신 후 클라이언트 수집 구간 중첩388~724ms는 worker 병렬성을 뜻하지 않는다. 첫 정상 응답은41.6초, 수집 전체69.0초, validate 전체259.266초였다. 이 값은 성능 기준이 아니다.

OBS-09 첫 시도는15개 native event 중 정확히 연결된7개 시험 사건을 확인했다. CRLF4회가 보존됐고 multiline/CRLF 사례는 각각 물리2행에서 원래 메시지로 복구됐다. 같은 decoded CLI 텍스트의 재파싱 사건과 저장 projection이 일치했다. process/level/source·전후 container 신원·실제 logging env/argv·이미지를 확인했다. actor/status·daemon 원시 바이트·기업 SIEM을 인증하지 않는다.

첫 시도 HTML의 표시 내용과 content-address 첨부 SHA는 정확하지만 samples.display_sha256은 메모리LF를, 실제 파일은 WindowsCRLF를 가리키는 결함을 교차 검토에서 찾았다. 원본 EVD/RES/HTML과 PASS를 재작성하지 않고 고정 UTF-8 바이트 저장으로 보수했다. 마지막 회귀와 새 시도02에서 기록 digest=실제 첨부 digest를 확인했다. 수정은 OBS-09 HTML 저장에 한정돼 REQ-11·PLT-01 실측은 반복하지 않았으며 시도별 소스 SHA로 영향 범위를 남긴다.

원 캡처 body digest와 저장 첨부 digest도 구분한다. 비밀값 제거 과정에서 일반 JSON은 재직렬화되며 원 바이트 그대로 보존했다고 주장하지 않는다. 첫 시도 일반 응답10개는 원 캡처 JSON 형식으로 재구성해 digest/길이가 정확히 일치했고 SSE8개는 저장 본문 자체가 캡처 digest와 일치했다. EVD 첨부 SHA/bytes는 실제 저장 파일을 가리킨다.

두 시도 모두 제품 prepare-validation·validate·verify·evidence export·새 위치 restore·복원 verify·현재 외부 팩이 없는 경로의 저장 팩 judge·새 JSON/HTML·verify가 성공했다. 예외 없는 POL에 PLAN.created_at을 고정해 재판정했으며 원본 객체·보고서·모델·store 바이트를 보존했다. 기존5컨테이너의 ID/상태와 kind2개 실행 상태를 유지하고 추가 관리 컨테이너/network를 정리했다. 출력은 시도별로 구분하고 실제 엔진은 하나씩이었다.

기본 전체 명령은 python -X utf8 -m pytest -q --durations=12 --basetemp [외부]/pytest-temp-r3 --junitxml [외부]/pytest-r3.xml이다. HTML 마지막 바이트 보수 전 **2016 passed, 5 skipped, 18 deselected, JUnit suite 455.55초**였다. 마지막 보수 후 tests/test_log_integrity.py와 tests/test_log_integrity_parser.py는 **55 passed, JUnit suite 3.42초**다. 최종 SHA 전체 Linux CI는 PR에서 별도 확인한다. 직접 실제 엔진과 unit/fake 통과 수를 합산하지 않는다.

첫 전체는2006통과·9실패였다. 닫힌 FD 접근7·공통 template helper의 가상시계 전달 누락1·새 정책 기대 목록1을 고쳤다. 관련114+152+74회귀 후 전체를 다시 실행했다. 상한·불변식·판정 기준은 완화하지 않았다. skip5는 Windows 루프백 refused/timeout 분류3과 symlink 권한/지원2이며, 제외18은 Docker/engine 표식이다. 이번 실측은 제외18개 모두를 대신하지 않는다. Windows1분 목표는 미달이며 과거 증거 복사·subprocess·CLI I/O가 느린 구간이다.

최종 wheel은 새 frozen requirements·해시 확인·새 venv·외부cwd·Python -I에서 소스=wheel=설치77파일, 새 실행기/단독 stream probe/고정 template, 정책46경로,17개 CLI 기대 코드와 readonly 준비를 확인했다. Docker container/network/image 목록도 전후 같다. wheel SHA는 6b156426879e541f39d1a21a99ec287d4221e28c71b15a9a310deb9746c8f741다. 첫 wheel 통과는 마지막 HTML 보수 전 기록으로 보존하고 두 번째를 최종으로 구분한다.

PACK19/REQ-11rev2/OBS-09rev2 해시는7753f6dc7dcad28985b97d33153f26f07673cc0b41bf28cb0adb65858f172e5a다. 팩 재생성2회 바이트, metadata46경로, 카탈로그317행 prefix+5=322행/614연결·분류a43/b3/c161/d115를 확인했다. 다른75점검·core/models/store·참고·과거 증거 등 보호167추적파일의 Git 내용은 같다. 기존 원격 출처772개는 보존했으며 다시 받지 않았고 로컬 출처/JSON pointer만 해석 검증했다.

시도02 이후의 독립 검토에서도 시도01의 당시 export 86첨부·저장 테이블 내용·10객체 Ref가 현재 store와 같았다. 시도 간 SQLite 파일 전체 바이트의 영구 baseline을 따로 남기지는 않았으므로, 이 대조와 각 실행의 재판정 전후 바이트 보존 결과를 구분한다.

모든 보고서는 EXAMPLE/DRAFT 정책·팩 미검토에 따라 official=false다. 관측MEASURED, 정책/환경 출처, RES verification_level을 구분하고 CONFIRMED/REVIEWED를 자동 승격하지 않는다.

## 남은 작업

기업 principal·권한·결과 공유·실제 수집기/보존/SIEM 기준은 팀이 확정해야 한다. REQ-10 runner 하위 소비, OBS-03 예외 dump, 임의 template/processor, GPU·성능·두 다른 실제 이미지 비교는 별도 묶음이다. 이번 표본 PASS는 77개 전체나 모든 운영 환경의 인증을 뜻하지 않는다.
