# 기존 자동화 판정·준비 경로 보강

## 2026-10-26 LIF-04 — 지속 지연 수신과 자원 수집, 판정 미등록

기존 `stream_probe`를 재사용하고 optional `read_profile`이 있을 때만 chunk별 지연·명시 close를
수행한다. 전후 정상 요청, 병행 정상 요청, 소유 컨테이너 PID/start·namespace·RSS/cgroup·TCP queue
표본을 제품 `capture-stream-pressure`에서 수집한다. 기존 초기 지연·REQ-11 계약은 유지한다.
소유 socket·파일/전송 대역 회귀는 실제 vLLM 실증과 구별한다. 자세한 준비와 기술 상한은
[RUNBOOK](RUNBOOK.md)의 LIF-04 절과 `examples/stream-pressure-settings.json`을 따른다.

새 관측은 클라이언트 읽기 절차와 aggregate 자원이다. buffer별 귀속·엔진 작업 취소·정리 완료와
기업 예산이 없어 보안/기능/성능은 UNKNOWN이다. 새 빈 실행기나 판정을 등록하지 않으며
77개·보안 규칙 연결13개·관측 전용1개·실행기12종을 유지한다. 과거 팩·실증 기록은 바꾸지 않는다.

## 2026-10-26 수집 실패와 부분 위반 보존

현행 팩27의 PLT-01은 `permission_scan_complete`를 별도 PASS 관문으로 둔다.
inspect·프로세스·mount·sentinel 중 일부가 미수집이어도 이미 확보한 root·capability·
특권·금지 mount/device·쓰기 성공 근거를 보존한다. 누락 필드를 false/빈 목록으로
채워 PASS로 바꾸지 않으며, 후속 정상 요청 실패도 앞선 보안 위반을 지우지 않는다.
새 기업 허용 기준이나 FAIL 의미를 추가하지 않고 기존 전체 수집 전제를 명시한다.
PLT-01만 정의6으로 변경하며 과거 팩의 수집/판정 계약은 소급하지 않는다.

PLT-02는 helper의 고정 `connected`·`denied`·`unknown` 외 outcome을 미수집으로 처리하고
응답 원문을 첨부에 저장하지 않는다. 앞선 독립 위반은 나중 자료 오류로 지우지 않는다.
REQ-02의 소유 파일 수집은 제품 CLI와 기존 도구에서 같은 함수를 사용하며 EXAMPLE을
DECLARED로 승격하지 않는다. 생산자 신뢰·인가·handler/worker 소비는 여전히 미검증이다.

이 변경은 수집 경계와 제품 진입점 보강이다. 77개 점검·보안 규칙 연결13개·관측 전용1개와
실행기12종을 유지하고 실제 엔진 실증이나 새 자동 판정 등록으로 집계하지 않는다.
아래 팩26 이하 기록은 당시 범위다.

## 2026-10-26 REQ-05 — 단일 고정 모델의 runtime 기록 수집, 엔진 판정 연결 보류

PR #14의 소유 파일 수집을 재사용해 제품 명령 `guard capture-model-authorization`를
추가했다. 기본 모드는 입력만 읽고 Docker·출력 파일·Store를 만들지 않는다.
`--execute`는 이미 소유한 Gateway 컨테이너의 regular JSONL 파일만 읽으며 엔진 기동,
요청 발생, 이미지/모델 취득, EVD/RES/RPT 생성은 하지 않는다. 출력은 독립된 수집
첨부이고 REQ-05 자동 실행·판정 등록은 아니다. 팩 26·77개 정의·기존 판정은 유지한다.

### 실제로 수집하고 구분하는 것

- 봉인된 ENV/POL JSON의 내용 해시·팩 Ref와 context의 정확한 ENV/POL Ref를 확인한다.
- `POL.values.REQ-05.principal_model_grants`는 주체명과 요청 모델명의 UTF-8 SHA-256
  쌍을 명시한 목록이다. 기업 값을 채우지 않는다. 이 수집 경계 전용 선언 입력이며
  CheckDef 정책 양식·카탈로그 등록이나 최종 모델 사용 권한 판정 완료를 뜻하지 않는다.
- context는 `guard-fixed-model-authorization-context/1`이다. `run_id`, `environment_ref`,
  `policy_ref`, `runtime_policy`, `observation_session_id`, `target_id`, `instance_id`,
  `window`와 `cases`만 받는다. window는 timezone을 포함한 start/end와 최대 600초,
  cases는 고유 case_id·서버 request_id(32자리 hex)·principal_sha256·
  requested_model_sha256의 2~8개다. 선언상 허용·비허용 사례를 모두 명시해야 한다.
  이 수집용 시간/건수 상한은 기업의 인가 한도를 대신하지 않는다.
- runtime_policy는 `policy_id`·`policy_version`·정책 파일 bytes의 `sha256`이다.
  Guard POL의 정규 문서 해시와 별도로 대조한다. 둘의 연결은 context의 선언이며
  bundle 검증이나 출처 인증을 했다고 표시하지 않는다.
- 소유 라벨·pinned Gateway 이미지·컨테이너/파일 전후 신원·bounded pipe·raw bytes
  해시를 확인한다. 최대 64KiB·64개 객체·한 줄 16KiB이며 CRLF를 해시 전에 바꾸지 않는다.
- 현재 runtime의 `auth.py`·`request_flow.py`·`request_records.py`와 대조했다.
  `allowed_models`와 `decision.model`은 요청 모델 검사다. final 기록의 주체·요청 모델,
  정책·run/session/대상 instance·수집 구간·전달 시도만 결합한다. 중복 final·started만
  존재·주체/세대/정책 교환·누락은 미확인이다. 다른 사례의 누락이 이미 대응된 Gateway
  선언 충돌 진단을 지우지는 않지만, 그 진단도 실제 엔진 모델 위반 FAIL은 아니다.
- 원문 JSONL·주체명·모델 표시명·policy.path·message·request context/body·자격은
  출력에 넣지 않는다. 필요한 metadata와 해시만 보존한다. mock/actual_test 또는
  EXAMPLE 입력이 섞이면 EXAMPLE이며 그 외도 DECLARED를 넘지 않는다.

Gateway 허용/거부·HTTP 200·응답 model·delivery.attempted는 최종 엔진 모델의 선택/사용
증명이 아니다. 현재 그 생산자가 없어 결과는 항상 보안·기능·성능 UNKNOWN,
`producer_authenticated=false`, `final_model_verified=false`,
`actual_consumption_verified=false`, `product_connected=false`, `official=false`다.
alias·LoRA·퇴역·대체 endpoint는 미시험으로 남기고 빈 실행기·판정기는 등록하지 않았다.

### 다음 연결에 필요한 생산자와 완료 관문

runtime 담당자는 서버 생성 request_id와 principal·정책 판본·대상 instance의 결합,
전달 전후의 모델 selector와 upstream ID를 제공해야 한다. 현재 기록을 UPDATE가
읽는 것만으로 새 생산자나 인증된 신원이 생기지 않는다. UPDATE의 엔진 adapter에는
검토된 소스/이미지와 고정 모델 세대에서 실제 선택·generate/worker 사용 지점의
최종 모델 식별과 같은 요청의 대응이 필요하다. 표시명·config snapshot·별도 helper를
그 소비 사건으로 바꾸지 않는다. 신뢰 경계·기업 허용표·퇴역 기준과 승인된 CPU
샌드박스를 확보한 뒤 정상/확인된 위반/미수집/지원 밖을 시험하고 제품 판정에 연결한다.
이번에는 실제 엔진과 runtime·platform·contracts 제품 코드를 변경하지 않았다.

## PLT-07 후속 송신 시험 조건 — 계획, 실행하지 않음

PLT-07 실행기·정책·판정 등록은 하지 않았다. Linux CPU의 소유 샌드박스로 검토할 수
있고 AWS가 필수인 것은 아니다. 실제 사용 환경·기동·비용은 별도 승인 범위다.

| 사례 | 먼저 정할 입력·관측 | 완료로 볼 근거와 미확인 경계 |
|---|---|---|
| 승인 수신처 | 취득/서비스/관측 단계별 실제 송신 PID/start·UID·namespace·정책 집행 위치와 허용 목적지/포트/프로토콜/작업 | 같은 사건의 송신·실제 peer·소유 수신처 수신/종료와 정상 업무 대조. helper의 성공만으로 엔진 송신을 증명하지 않음 |
| 비허용 수신처 | 격리된 소유 수신처 두 번째 endpoint, 명시적 금지 작업, 실제 송신자·집행 정책 판본 | 실제 금지 작업의 성공은 수신처와 송신자 양쪽 근거로 확인. timeout·로그 부재·Hubble DROPPED만으로 차단 성공을 확정하지 않음 |
| DNS·redirect | 통제된 이름/redirect와 최초·최종 주소, 연결/handshake·TLS peer, 적용 정책 | 최종 목적지/작업까지 대조. SDK·exporter·proxy·인증서 또는 실제 경로가 미관측이면 UNKNOWN |
| 초기 정책 부재·만료/단절 | 마지막 유효 정책 유지/만료 시 거부 등 기업이 정한 동작, 유효 시각·실제 적용 판본 | 정한 동작과 실제 집행·허용 정상 업무를 대조. 어떤 fallback이 안전한지 임의로 정책을 선택하지 않음 |

각 사례는 collector 생성 상관 정보·run/정책·실제 송신 프로세스와 소유 수신처의
독립 관측을 결합해야 한다. 동일 namespace/UID의 별도 probe는 다른 실행 주체이므로
동일한 엔진 권한/집행 조건이라고 가정하지 않는다. 연결과 자료 수신·업무 소비도 나눈다.
기술 상한의 첫 제안은 사례당 연결 최대 8개·무해 표식 최대 1KiB·30초이며 기업
허용 기준이 아니다. 공개/운영 목적지·실제 Secret은 사용하지 않는다. DNS/redirect,
송신자 신원 또는 수신측 근거를 준비하지 못하면 해당 범위를 미확인으로 남긴다.
REQ-05 수집기·기존 5개 업데이트 수락 도구의 종료0을 PLT-07 실증 성공으로 세지 않는다.

## 2026-10-26 PR #14 보완 — 센서 오류 격리와 IPC 경계 검증

센서의 정상 호출 뒤 `emit`가 실패해 원래 반환값을 잃는 문제를 보완했다.
`observe_template_call`과 `read_template`는 관측 기록의 일반 예외가 나도 원래 반환값을
보존한다. 원래 함수의 예외 객체와 호출 횟수도 보존하며, 사용자 중단·종료는 다시
던진다. 소스 pin·파일 변경·디코딩·지원 범위 검사는 그대로 실패한다. 판정 실패를
무시하는 처리가 아니라 엔진 동작과 관측 기록의 실패를 분리하는 처리다.

생산자 연결 시 수집 구간마다 `SensorStatus`를 전달하고 `snapshot()`을 내부 native
자료의 선택 필드 `sensor_status`로 넣는다. 시도/실패 횟수와 고정 코드
`TEMPLATE_SENSOR_EMIT_FAILED`만 기록하며, 한 번 실패한 상태는 후속 전달 성공으로
완료가 되지 않는다. 기본 호출도 고정 경고를 내고 예외 본문·경로·template·traceback을
남기지 않는다. 기록 경고 자체가 실패해도 원래 결과와 상태는 보존한다.

내부 parser는 이 선택 필드의 타입·64건 상한·잔존 사건 수·완료 표기·고정 코드를
대조하고, EVD 첨부의 `reported_sensor_status`와 `collection_errors`에 전달한다.
이는 인증되지 않은 생산자의 사건 전달 주장이다. 전달 성공은 read→selection→apply
사슬의 완전성·수집원 인증·실제 소비 증명이 아니다. 과거 status 없는 자료는 새 완료
주장을 얻지 않고, 사건이 하나도 없는 자료는 기존대로 거부한다. 전부 기록하지 못한
경우 native 캡처는 기존 고정 미수집 사유로 남는다. SUP-03의 보안·기능 UNKNOWN과
`template_consumer_verified=false`, 기존 팩 26·판정·공용 계약은 유지한다.

PLT-02의 기존 위반 보존·저장 팩·반출/복원·동일 재판정 회귀를 재사용한다. 추가 검증은
소유권/이미지/기동 신원/host PID/proc mount 미확인 시 health 요청 전 중단,
동일 PID의 start 변경, 나중 컨테이너 재시작으로 인한 기존 위반 소실 방지다.
`/proc` parser 검증은 명시적인 EXAMPLE 파일 트리만 읽는다. Linux CI의 test-owned
socket은 실제 peer credential과 수신 EOF를 확인하며 payload를 보내지 않는다.
Windows는 명시적인 transport 대역을 사용한다. 어느 것도 실제 vLLM IPC 실증은 아니다.

### 다음 체크리스트 구현 순서

| 순위 | 후보 | 재사용과 먼저 확보할 근거 | 완료 관문 |
|---|---|---|---|
| 1 | REQ-05 단일 고정 모델 인가 | HTTP/역할별 요청 수집과 runtime의 `auth.py`·`request_flow.py`·`request_records.py`를 읽기 전용 대조. `allowed_models`와 Gateway `decision.model`은 최종 엔진 선택 증명이 아님 | 명시한 주체×최종 모델·종료 기준, 실제 선택/사용·거부와 요청 결합. alias/퇴역/LoRA 미관측은 UNKNOWN으로 남기고 독립 위반을 보존 |
| 2 | PLT-07 통제된 송신 부분 범위 | 소유 컨테이너·프로세스 신원·bounded 수집 재사용. 별도 송신 프로세스 귀속과 소유 수신처의 실제 수신/종료 근거 필요 | 명시 목적지·작업·정책 집행 위치와 송신자/최종 목적지/수신 결과 결합. 같은 namespace의 helper 연결이나 차단 로그만으로 엔진 송신·차단 완료를 주장하지 않음 |

두 후보는 다음 구현의 우선 검토 대상이며 현재 등록·실행하지 않았다. REQ-05는 신뢰
가능한 최종 모델 생산자와 기업 허용/퇴역 정책, PLT-07은 소유 수신처·집행 정책이
선행 조건이다. AWS가 필수인 것은 아니며 승인된 Linux CPU 환경으로도 범위를 검토한다.
OBS-02/LIF-09는 독립 worker 상태와 안전한 부분 실패 절차가 없어 계속 보류한다.
REQ-02는 실제 인가/rewrite/final-handler 사건 생산자, SUP-03은 소스–이미지 대응과
계측 이미지·cache/세대/요청/generate/worker 연결이 남았다. runtime·platform·contracts는
이번에 수정하지 않았고, 실제 엔진·이미지/모델 취득·유료 시험도 수행하지 않았다.

## 2026-10-26 후속 구현 — 변경 영향 검증·Unix IPC 접근·native 자료 수집

최종 작업 기준은 main `060a27d`다. 작업 중 PR #10의 프레임워크 참조·고정 ID와
PR #12의 승인 변경 통합이 main에 반영됐다. 처음 확인한 PR #6~#9의 main 누락은
이 통합으로 해소됐고 통합 초안 PR #11도 포함됐다. 기존 구현 커밋 `061099e`는
보존하고 최신 main의 참조·고정 ID 위에서 새 브랜치로 적용해 검증한다.
이번 후보는 팩 26/DRAFT·정책 25점검/54경로·보안 판정 규칙 연결 13개·관측 전용 1개·
미연결 63개·실행기 12종이다. 정의 변경은 PLT-02 하나이며 기존 76개 정의 bytes와
기존 판정기는 유지한다. 실제 엔진 시험은 보류 중이다.

### PLT-02의 제한 구현

`unix_ipc:real`은 명시된 보호 Unix SOCK_STREAM 경로와 허용 UID 정책을 받는다.
`probe_uids`에는 허용·비허용 숫자 UID를 명시해야 하며 owner/관측에서 허용값을
채우지 않는다. 소유 guard.run·pinned 이미지·대상 신원을 확인한 뒤 컨테이너 내부
UID:GID를 지정해 zero-payload 연결만 한다. host PID namespace는 지원하지 않는다.
전후 filesystem inode·socket inode·소유 PID/UID/start·namespace와 커널 SO_PEERCRED가
연결될 때만 비허용 UID 접근을 FAIL 근거로 남긴다. 이후 정상 대조 오류나 다른
source 미수집이 이미 확인한 연결 위반을 지우지 않는다.

이것은 운영자가 명시한 보호 접점의 커널 연결 관측이다. 전체 core/worker IPC
발견·SHM/TCP·추상 주소·동일 UID의 비허용 프로세스·무해 명령 소비·TP는 미검증이다.
연결 거부나 정상 추론 성공만으로 전체 보호를 PASS로 표시하지 않는다. 보안은
확인된 접근 FAIL 또는 UNKNOWN이며 기능·성능 PASS도 추가하지 않는다.
원문의 전체 challenge는 시도 완료로 채우지 않고 부분 관측을 따로 기록한다.

OBS-02·LIF-09는 정상 기동 후 수집하는 현재 실행 골격으로 부분 worker 준비 실패를
독립 계측할 수 없어 이번에는 등록하지 않았다. guard 자신의 컨테이너 관리 결과나
health를 대상 worker 준비 완료로 대체하지 않는다. 다음 구현에는 대상의 준비/실패
생산자와 독립 상태·안전한 실패 주입·접수 gate의 대응이 필요하다.

### REQ-02와 SUP-03: 실제 자료 경계와 남은 생산자

`executors/owned_capture.py`는 소유 컨테이너의 명시된 regular file만 nofollow·
전후 fstat·instance·해시·pipe 상한·전체 deadline으로 수집한다. 이것은 파일 bytes의
출처·일관성 확인이며 생산자 진위 인증이 아니다. 원문·Secret 필드가 섞인 자료는
native allowlist를 통과하지 못하며 증거에 저장하지 않는다.

REQ-02의 `tools/capture_route_observations.py`는 기본 모드에서 ENV/입력만 읽고
Docker·엔진·출력을 만들지 않는다. 명시적 `--execute`도 이미 소유한 컨테이너의
자료를 읽는 명령이며 기동·pull은 없다. 자료는 DECLARED 첨부이고 EVD/RES·판정·
자동 등록을 만들지 않는다. runtime `mvp/auth.py`의 권한 판단과
`mvp/request_records.py`의 요청 기록만으로 rewrite와 최종 handler 실행의 연결은
보장되지 않는다. 해당 담당자는 원 method/path·주체 digest·인가 입력/결정·변환 후
method/path·최종 handler/작업/수행·서버 생성 ingress/upstream ID·요청 digest와 누락
검출을 제공해야 한다. 클라이언트 ID·프록시 설정·HTTP 200으로 대체하지 않는다.
현재 생산자가 없으므로 REQ-02 executor/judge는 null을 유지한다.

SUP-03의 `adapters/template_read_sensor.py`는 검토 파일 hash를 확인하고 실제 반환하는
동일 buffer의 raw/UTF-8 universal-newline 문자열 digest를 기록하는 함수와, 검토된 동기
selection/apply 호출 인수·반환·오류의 metadata wrapper를 제공한다. 자동 monkeypatch나
선정 이미지 삽입은 하지 않았다. source hash 일치는 이미지 관계나 함수 진위 인증이
아니다. 현재 이미지와 의존성의 출처 검토·별도 계측 이미지·load/cache generation 및
collector nonce/요청/재시작 연결·generate handoff는 후속이다. 전체 worker 소비 센서는
아니며 EXAMPLE 분석기를 MEASURED로 바꾸지 않았다.

SUP-03에 선택 설정 `native_observation_path`를 준 경우에만 실제 파일 캡처를 시도하고
strict metadata 자료를 별도 EVD artifact로 연결한다. 이 경우 실행기 버전은 0.3.0,
옵션 없는 기존 경로는 0.2.0이다. 실패는 고정 진단과 미수집으로 남기며 기존 관측을
지우지 않는다. 자료의 생산자·소비 진위는 미검증이고 SUP-03의 기존 보안/기능 UNKNOWN·
consumer_verified=false 상한은 그대로다. 선택 자료가 완전해 보여도 자동 PASS는 없다.

이번 변경의 실제 시험 대상은 새로운 IPC 수집과 선택 native 파일 수집이다. 기존
다섯 점검의 실행·판정·기동/비교 경로는 바꾸지 않아 두 이미지 전체 재실증을 요구하지
않는다. native 옵션 없는 REQ-07/SUP-03과 registry·팩 연결은 영향 회귀로 검증한다.
실제 vLLM·AWS 신규 자원·새 모델/이미지는 사용자 보류 결정에 따라 실행하지 않는다.

## 2026-10-26 REQ-02 제한 구현 — EXAMPLE 증거 계약, 제품 연결 보류

기존 `role_route`의 HTTP 응답·프록시 설정과 Gateway 접근 로그에는 인가 입력
path/주체/작업 → rewrite → 최종 handler 실행을 연결하는 신뢰 수집원이 없다.
따라서 REQ-02의 executor/judge는 null로 유지한다. 팩 25·기존 12개 보안 판정
연결과 SUP-03 관측 전용 1개·실행기 11종·나머지 64개 집계는 바뀌지 않는다.

[오프라인 도구](../tools/analyze_route_rewrite.py)는 기업 허용 정책과 기술 표본을
분리한 명시적 EXAMPLE 입력 및 합성 캡처만 읽는다. 단일 외부 origin에서 안전한
`POST /v1/completions`로 가는 원경로/prefix 대조에 한정한다. 팩·정의·POL/ENV/PLAN의
정확한 Ref와 입력 해시는 파일끼리의 대응일 뿐 원본·수집원의 진위 인증이 아니다.
서버가 만든 ingress/private upstream ID, 요청 fingerprint, 인가 주체와 판단 경로,
rewritten path/작업 및 최종 handler 사건을 대조한다. HTTP 200·클라이언트 ID·설정만으로
사슬을 채우지 않는다. 완전한 합성 우회는 별개 사례의 미수집이나 정상 대조 실패로 지우지 않는다.

출력의 `BYPASS_OBSERVED`·`COMPLETE_WITHOUT_BYPASS`·`INCONCLUSIVE`는 **가정한 합성
사슬의 진단**이며 Guard의 PASS/FAIL이 아니다. 제품 상태는 항상 미연결·보안 UNKNOWN이고
EVD/RES/RPT·승인 정책을 생성하지 않는다. 실제 로그와 MEASURED/DECLARED 자료는 거절한다.
기존 출력은 덮어쓰지 않으며 입력/파일 오류에 원문·경로를 반사하지 않는다.
[회귀](../tests/test_route_rewrite_contract.py)는 prefix/명시 차단·가정 우회와
신뢰 필드 누락·서버 ID/사건 교환·출처 변경·미지원 handler·잘린 JSON을 구분한다.

제품 연결의 선행 조건은 팀이 정한 작업별 권한·경로 의미, 검토된 안전 handler와
인가/rewrite/final-handler 수집 위치·source 소유권·상관 ID 전달 통로·누락 검출이다.
이 수집원과 실제 정상/위반 대조가 갖춰진 뒤 기존 CheckDef·challenge 의미를 보존하는
연결 계획을 따로 검토한다. 이번 구현에 새 프록시·실행기·정책값·팩 revision은 없다.

## 2026-10-26 현행 후속 계획 — 실제 엔진 실증 보류

이 절은 다음 구현·실험의 준비 계획이며 실행 결과가 아니다. 현재 집계는
[77개 추적표](CHECKLIST_VALIDATION_STATUS.md), SUP-04의 제한 범위는
[실행 이미지 승인 계약](SUPPLY_IMAGE_APPROVAL.md)을 따른다. 아래 2026-10-23~24의
테스트·실측·HTML 표기는 당시 기록으로 보존하며 현행 팩의 새 실증으로 합산하지 않는다.
현재 RPT JSON이 원본이고 화면·`verification-result/0.2` 변환은 platform 담당이다.

| 관측 공백 | 재사용할 기반 | 새로 확인할 것과 완료 조건 |
|---|---|---|
| OBS-03 로그 완결성 | 고유 합성 표식·원본/사본 수집·저장 로그 재검증 | 기업의 필수 source/branch와 수집 문맥을 먼저 정한다. 요청과 연결된 실제 exception/traceback/dump 사건·필수 사본이 있어야 해당 분기를 검증한다. 4xx·덤프 부재·파일 없음은 비활성 또는 비누출의 증명이 아니다. 독립 위반은 다른 source 미수집으로 지우지 않는다. |
| REQ-10 내부 소비 | sampling API 경계·동시/후속 정상 요청·health | 고정 이미지·실제 활성 runner의 신뢰 가능한 최종 소비 관측을 설계한다. API 수용·5xx·timeout과 내부 소비·다른 요청 영향을 분리하고, 소비 미관측은 보안 UNKNOWN으로 유지한다. |
| REQ-11 출력 귀속 | 두 JSON/SSE 연결·소비 입력 projection·교환 반례 | 원래 요청과 생성 출력을 독립적으로 연결할 오라클·수집 신뢰 경계를 정한다. 클라이언트 ID·usage 개수·임의 생성 문장 일치로 대신하지 않는다. 정상/출력 교환/불완전 수집을 대조하고 오라클 부재 시 기존 보안 PASS 상한을 유지한다. |

새 점검의 재사용 후보는 다음 세 가지다. 이는 구현·지원 완료 선언이 아니다.

- **SUP-03 관측전용·조건부 실증:** 기존 모델 inventory·manifest·파일 해시와 컨테이너 정체성을 재사용할 수 있다. 이번 readonly template bytes·API projection 연결은 독립 worker의 실제 소비 근거가 부족하므로 보안 PASS/FAIL 규칙을 모두 비워 두고 승인 일치·비승인 모두 UNKNOWN으로 남긴다. 단순 비승인 template mount·파일 존재·해시 일치를 소비 위반 FAIL로 해석하지 않는다. config/tokenizer/template 승인 목록·immutable snapshot과 실제 선택/읽기 EVD가 있어야 후속 판정 계약을 검토할 수 있다. 후속 독립 수집 계약은 실제 읽기·파싱·렌더링 소비 프로세스를 먼저 식별하고, loaded bytes digest·컨테이너/프로세스·모델 세대·수집 시각·선정 요청의 연결과 신뢰 출발점을 정해야 한다. 파일 open 목록만으로 성공한 로딩·소비를 확정하지 않는다. 새 독립 소비 센서는 이번 범위 밖이며 관측전용 연결을 자동 보안 판정 완료로 집계하지 않는다.
- **LIF-09:** sandbox의 기동·readiness·ABORTED·정리 관측을 재사용할 수 있다. 제한된 실패 fixture, 실패 worker/단계와 집계 준비 상태·외부 접수 gate의 대응, 수정 후 정상 재기동 관측과 기업 복구 방침이 필요하다. guard 자체의 기동 실패만으로 대상 엔진의 부분 실패 보호를 확인했다고 하지 않는다.
- **REQ-05:** 기존 HTTP·역할별 정상/비허용 요청 수집을 재사용할 수 있다. 호출자×최종 모델 허용표, alias 현재 대상·퇴역 시점/기한·진행 요청 처리 방침, 실제 소비 모델 식별을 먼저 확보한다. 요청 body의 모델 이름만으로 최종 모델 인가를 입증하지 않는다. OBS-04는 정책 등록만으로 생성·송신을 관측할 수 없어 실제 sink·생성/전송 센서 준비를 별도로 검토한다.

다음 세 작업의 완료 기준은 다음과 같다.

1. **계약·시험 입력 확정:** 기업 정책 공란·미지원 프로파일·필수 관측을 구분하고, 정상/위반/미수집/지원 밖 표와 정확한 팩·ENV·POL Ref를 연결한다. 현재 구현에 없는 소비 계측이나 팀 결정을 준비 완료로 표시하지 않는다.
2. **구현 묶음과 오프라인 계약 검증:** 기존 실행기를 재사용해 필요한 관측만 추가하고, 확인된 FAIL 보존·UNKNOWN 상한·예외/상한 도달·정확한 Ref·저장 증거 재판정·플랫폼 투영을 검증한다. 대역의 관측은 EXAMPLE이며 실제 엔진 실증과 구분한다.
3. **실증 재개용 묶음 확정:** [후속 분기 계획](UPDATE_VALIDATION.md#2026-10-26-현행-후속-실증-계획--미수행)의 입력·자원·상태 독립성·증거 분리를 정리하고 실제 기동은 보류한다. 사용자 재개 지시와 준비 조건이 충족된 뒤 각 분기를 실행·기록한다.

U1의 잔여 32경로(복합 책임15·제외 제안17)와 23개 부분 정답의 사람 검토·독립 평가는 별도 트랙이다. 저장된 oracle의 `human_pending`을 유지하고, 이 준비 작업에서는 분류·선정·이월 규칙이나 제외 목록을 바꾸지 않는다. 후속 릴리스의 독립 평가 가능 여부도 별도 확인 전에는 단정하지 않는다.

## SUP-03 소비 사건 계약과 EXAMPLE 오프라인 분석

이 절과 `tools/analyze_template_consumption.py`는 독립 소비 수집원을 설계하기 위한 준비다. 실제 수집원·서명·attestation은 구현하지 않았고, 엔진·Docker·모델·네트워크를 실행하지 않는다. 입력은 `guard-template-consumption-example/1`의 합성 metadata뿐이며 원문 로그를 받지 않는다. 결과 `guard-template-consumption-analysis/1`의 `data_consistency=consistent`는 선언된 사건 사슬의 자료 일관성이다. 사건이 실제로 일어났거나 생산자가 신뢰됨을 뜻하지 않는다. 승인에 없는 선택도 같은 구조 일관성을 가질 수 있으며 보안 FAIL이 아니다. 결과의 `actual_consumption_measured`·`independent_sensor_verified`·`template_consumer_verified`·`challenge_exercised`는 항상 false이고 보안·기능·성능은 항상 UNKNOWN이다. 제품의 POL/PLAN/EVD/RES/RPT를 생성하거나 판정에 연결하지 않는다. 현행 SUP-03 실행기·팩25/정의3·빈 보안/기능 규칙·수동 PASS 상한·원문 challenge 미시행·기존5개 수락 계약은 유지한다.

### 먼저 확인한 역할과 남은 소스 근거

기존 `output/automation-phase2-20261023-1792750927786/image-source/` 추출본의 정적 흐름은 API 초기화의 loader가 template 문자열을 보관하고 renderer가 이를 선택·해석한 뒤 token 입력을 engine client에 전달하는 구조다. 모델 worker가 template 파일을 직접 읽는다고 전제하지 않는다.

- `entrypoints/launchers/api_server/app_state.py:67,88-104,122-129`: `load_chat_template` 결과를 OnlineRenderer와 tokenize handler에 전달한다.
- `entrypoints/chat_utils.py:1491-1547`: 실제 `f.read()` 반환과 `lru_cache`가 있다. 현재 파일의 재해시는 기동 시 보관된 문자열의 증거가 아니다.
- `renderers/hf.py:263-318,778-840,1120-1132`: 명시 template→processor→tokenizer→내장 fallback 선택과 실제 `tokenizer.apply_chat_template` 호출이 있다.
- `renderers/online_renderer.py:751-772`: 재사용 token 분기는 template 해석을 건너뛴다. `entrypoints/openai/chat_completion/serving.py:378-395`의 `engine_client.generate` 전달은 worker 최종 소비 사건과 구별한다.

이는 과거 추출본의 코드 조사다. [기존 소스 기록](evidence/automation-phase2-20261023.json)의 이미지 digest·파일 hash·`source_commit=UNVERIFIED` 한계를 유지한다. 로컬 추출본의 파일 hash 일치나 이 사건 예시는 현재 활성 이미지·프로세스·모델 또는 후보 이미지의 동일 흐름을 인증하지 않는다. 특히 loader·HF renderer 등 후속 조사 파일의 이미지 출처는 현재 대상에 대해 새로 확인해야 한다. 실수집 구현 전 선정 이미지와 tokenizer/transformers 의존성의 소스·캐시·호출 주체를 다시 고정해야 한다.

### 최소 사건과 신뢰 출발점

첫 오프라인 계약은 위 API 경로의 **단일 컨테이너·API 프로세스·모델/tokenizer/renderer 세대**만 다룬다. loader·renderer·handoff는 논리 역할이며 서로 다른 독립 인증 주체라는 뜻이 아니다. 다중 API 프로세스·별도 render server·worker·새 모델 세대의 관계는 이번 parser가 지원하지 않는다. 사건마다 expected instance와 다르면 일관성 공백이다.

| 연결 | 최소 사건 metadata와 경계 |
|---|---|
| 실제 읽기 buffer | `template_read`의 `load_id`·`template_generation`, 성공 반환한 동일 buffer의 `raw_sha256`/`raw_bytes`, 디코딩·개행 처리 후 유효 문자열을 UTF-8로 표현한 `decoded_sha256`/`decoded_bytes`, 명시한 `decode`, 성공/읽기/파싱 오류. 나중에 파일을 다시 열어 계산한 hash·argv·open 성공·API projection으로 대체하지 않는다. 현재 parser는 hash 선언을 대조하며 원시 bytes를 재계산하지 못한다. |
| 인스턴스·세대 | 모든 사건의 컨테이너/이미지 digest, PID와 재사용을 구별하는 `process_start`, 모델/tokenizer snapshot digest와 각 generation, renderer generation. 하나의 수집 session에서 발생한 재시작·모델 교체는 새 capture로 분리한다. generation 이름이나 hash의 신고 자체는 실제 snapshot·프로세스 인증이 아니다. |
| 선택·cache | `template_selected`의 `selection_id`, 앞선 성공한 `load_id`, 선택된 문자열 digest, source 분기, cache hit/miss와 보관한 template generation. 선택 digest는 raw 파일 hash와 섞지 않고 decoded digest와 대조한다. cache hit도 같은 session에서 관측된 앞선 load가 필요하며 기동 전부터 있던 cache는 미수집으로 남는다. 현재 지원 source는 관리 파일 한 종류다. |
| 실제 해석·전달 | `template_applied`의 동일 selection·적용 문자열 digest·성공/parse_error/bypassed와 렌더 입력 digest, `engine_handoff`의 같은 selection·렌더 입력 digest. 단순 open 뒤 실패·선택과 다른 적용·template 우회·다른 입력 전달은 사슬을 완성하지 못한다. handoff는 `generate` 경계이며 worker의 최종 소비·생성 결과 귀속을 입증하지 않는다. |
| 선정 요청·시간·완결성 | 수집자가 만든 nonce의 별도 계획표→내부 request/subrequest→입력 digest를 세 사건에서 정확히 대조한다. 클라이언트가 지정한 ID만으로 인증하지 않는다. 한 session의 단조 시간 window, source별 연속 seq·완결성·닫힘과 사건 순서를 확인한다. 수집자가 임의 재정렬·부족한 source 생략으로 사슬을 보충하면 안 된다. |
| 승인 목록 | 독립 입력한 `EXAMPLE-explicit-policy`의 `allowed_raw_sha256`만 membership에 사용한다. []는 명시적으로 승인 없음이다. 현재 관측값·decoded digest·ENV/API 입력에서 목록을 보충하지 않으며 기업 승인이나 정책 유효성을 인증하지 않는다. |

팀은 수집원·계측 코드와 실행 주체를 어느 신뢰 경계에 둘지, 엔진의 자기 신고를 어디까지 믿는지, 수집원 손실·중단·변조를 어떻게 드러낼지 먼저 결정해야 한다. 동일 엔진 로그를 별도 JSON으로 옮겨도 독립 인증이 되지 않는다. 실제 구현에는 선택된 source/의존성 pin, 읽기 buffer와 반환 문자열을 같은 load 사건으로 묶는 계측 위치, 실제 apply 인수·cache 항목의 수명, collector가 만든 요청과 내부 ID의 결합 경로, 별도 수집 통로와 누락 검출을 검토해야 한다. 이 설계에는 신규 서명·host 인증·원격 attestation 보장이 없다.

### 개인정보·반례·오프라인 도구의 수락 경계

수집원은 합성 fixture만 사용하고 template/token/body/kwargs/응답 원문·host 경로를 저장하지 않는 전처리를 수행해야 한다. parser의 키 allowlist·digest·정수 상한·`EXAMPLE-` opaque ID 제약은 이 준비 입력의 형식을 제한할 뿐 원문에 가명을 붙여 넣는 행위를 탐지하는 개인정보 센서는 아니다. digest도 익명화 보장이 없으므로 실제 고객 입력의 digest를 이번 예시에 반입하지 않는다. CLI는 입력을 읽기만 하고 정해진 결과 projection만 stdout에 쓴다. 잘못된 입력의 필드·값·예외 본문·경로를 출력하지 않는다.

최대 64KiB·64사건·4합성 요청·3논리 source·단조 시간300초, 관리 template bytes 8192 상한이다. schema/unknown field/중복 JSON key/NaN·bool 정수/상한 위반은 입력 거절이며 exit2다. 형식이 유효해도 사슬·source·시간·세대 공백은 `inconsistent`/exit1, 모두 일치하면 `consistent`/exit0이다. 어느 종료 코드도 보안 판정이나 실측 완료가 아니다. `requests[].chain_consistent`는 요청 사슬 내부 연결만을 나타내며 전체 source·시간·인스턴스 공백은 상위 `gaps`와 `data_consistency`를 함께 본다.

작은 합성 [fixture](../tests/fixtures/template-consumption-example.json)의 hash는 실물에서 계산한 근거가 아닌 EXAMPLE 자리값이다. [회귀 코드](../tests/test_template_consumption_analysis.py)는 정상·명시 비승인·cache hit/miss의 동일 구조 일관성, open/읽기 후 parse 실패, A 읽기/B 선택, 오래된 cache, render 우회, 요청 일부 교환, 재시작/PID 재사용, 모델/tokenizer/renderer 교체, 중복/역순/부분 사건·시간/source 공백과 원문 필드 거절을 다룬다. 기존 OBS-03 `log_analysis_contract`는 자유 로그의 표식/필드·로그 정책 판정에 결합돼 이 사건 계약에 직접 재사용하지 않았다. 공통 프레임워크·registry·제품 객체나 판정 규칙은 늘리지 않았다.

실행 형식은 `python -m tools.analyze_template_consumption tests/fixtures/template-consumption-example.json`이다. 이는 로컬 합성 파일 분석이며 실제 수집/기동 명령이 아니다. 구현·테스트 코드를 먼저 고정한 뒤 검증 결과를 별도로 기록한다. 이 절 작성 시점에는 새 parser 테스트를 실행하지 않았다.

제품화 전 **팀 결정**은 template 부분 범위의 장래 판정 의미·승인 주체/유효기간·센서 신뢰 경계·개인정보 보관·필수 source와 실패 처리·새 정의 revision이다. **환경 조건**은 현재 선정 이미지/모델/tokenizer 소스와 immutable snapshot, 안전한 계측 권한·별도 통로, 소유 시험 인스턴스·자원·재시작/정리 조건이다. 전자는 합의가 필요하고 후자는 실제 준비 증거가 필요하다. 어느 한쪽도 이번 parser 구현만으로 충족됐다고 하지 않는다. 실제 엔진 실증 재개·새 수집원 구현·장래 판정 규칙 연결은 별도 후속 범위로 남긴다.

작성 시작: 2026-10-23, 검증: 2026-10-24, Asia/Seoul. 구현 출발점은 `codex/gua-automation-phase3`의 `5a352512bbc24589845438438b0146a0d7653b6e`다. 원격 재확인에서 기존 PR들의 main 병합을 확인했고 `24f7d90a2745c108d7d395e5082a3721a3198f0c`로 fast-forward했다. 둘의 파일 차이는 `.DS_Store`뿐이며 이번 변경은 최신 main 위에 놓는다. 이번에는 새 정책·실행기를 추가하거나 실제 엔진을 기동하지 않는다. 전체 구현·테스트 코드·이 문서 초안을 완료한 뒤 검증하고 결과를 아래에 기록한다.

## 현재 등록과 의미

PACK-BASELINE 20/DRAFT, 활성 점검 77개, 자동 실행기와 판정 연결 11개, 정책 선언 21점검·46경로를 유지한다. REQ-11만 정의 revision 3으로 바뀐다. 어댑터 0.4.0·declared 0.12.0, 카탈로그 322행·614점검 연결(a43/b3/c161/d115)은 그대로다. a/b/c/d는 정책 소비 분류이며 인프라 자동화 가능 등급 A/B/C와 다르다. 등록 수·실행 완료 수·실제 챌린지 완료 수·보안 PASS 수를 합산하지 않는다.

기본 `guard.toml`은 fake 시연 설정이다. 실제 CPU 경로는 `examples/windows-cpu*.toml`의 명시적 real 설정, 실제 ENV와 로컬 모델 snapshot을 별도로 준비해야 한다. 예시 정책의 EXAMPLE/DRAFT와 기업 값·승인 공란을 유지한다. 공식 여부는 보고서의 `official`을 확인하며 이번 보강으로 팩·정책을 승인하지 않는다.

## REQ-11 새 판정 계약

기존 collector는 고정 CPU 이미지·텍스트 chat template에서 두 HTTP 연결의 JSON/SSE 입력 projection, API ID 연속성, usage·프레임 종료·관측 완결성을 검사한다. 클라이언트 ID·token 개수·모델 생성 문구의 일치는 생성 출력 자체의 독립 귀속 근거가 아니다. 출력 문구와 output token_ids만 두 응답 사이에서 교환해도 입력 projection 검사를 통과하는 합성 반례를 회귀로 보존한다. 이는 실제 엔진 유출을 발견했다는 뜻이 아니다.

새 정의의 보안 PASS 규칙은 비워 둔다. 신뢰할 독립 출력 귀속 오라클이 없는 현재 프로파일은 정상 입력 하위 검사가 완결돼도 전체 보안 판정은 UNKNOWN이다. 확인된 타 요청 prompt 소비·ID 위반은 독립 FAIL 규칙으로 유지한다. 기능 축은 기존 정상 대조·필수 관측·챌린지 관문을 따로 적용하고 성능은 UNKNOWN이다. 수동 PASS도 같은 저장 정의의 rule_table 상한을 거쳐 새 정의의 보안 UNKNOWN을 우회하지 못한다.

관측은 `input_projection_verified`, `output_binding_verified=false`, `output_binding_gap`의 미검증 사유로 입증 범위를 구분한다. 기존 `connection_binding_verified`는 입력 projection 호환 관측이다. JSON/HTML의 점검 한계 문장에 입력 하위 검증과 생성 출력 미검증을 표시한다. `COMPLETED`는 collector 호출의 완료, `MEASURED`는 출처이며 출력 귀속 입증이나 모든 챌린지 완료를 뜻하지 않는다.

과거 팩19/REQ-11 revision 2와 그 EVD·RES·보고서는 변경하지 않는다. 저장된 팩19로 재판정하면 당시 규칙을 사용한다. 새 팩20 판정을 과거 결과에 소급 덮어쓰지 않는다. 독립적인 생성 출력 귀속을 지원하려면 출처·수집 신뢰 경계·정상/교환 음성 대조를 먼저 설계하고 새 정의 revision으로 추가해야 한다.

## managed update sentinel

overprivileged 프로파일은 원래 빈 쓰기 가능 sentinel mount를 요구한다. 단일 validate에는 있던 전달을 update에도 연결한다. baseline과 candidate는 새 실행 폴더 안의 서로 다른 전용 빈 디렉터리를 사용하며 기존 경로나 두 side의 writable 경로를 재사용하지 않는다. default/hardened에는 이 mount를 만들지 않는다. 소유 라벨·순차 실행·정리·정제 진단·ABORTED 증거와 정리 실패 이전의 확인된 FAIL을 보존한다.

회귀는 Docker를 메모리 대역으로 바꾸되 실제 sandbox의 `_start_engine` 필수 인자 검사까지 통과시킨다. 이는 실제 Docker/vLLM 기동 성공 검증과 구분한다.

## 실행 전 점검별 지원 안내

`prepare-validation`과 `prepare-update`는 기존 입력·모델·Docker·로컬 이미지·하드웨어 준비 검사에 더해 선택된 점검의 side별 지원 표를 반환한다. `ready`는 기존 준비 검사 결과이며 지원 표 전체가 보안 PASS라는 뜻이 아니다. 지원 표는 SUPPORTED(현재 정적 계약 일치), UNSUPPORTED(확인된 지원 범위 밖), UNKNOWN(판단 정보 부족)와 이유·한계를 제공한다.

지원 표는 ENV·정책·실행 설정을 읽는 정적 안내이며 엔진·HTTP·새 네트워크·store를 만들지 않는다. prepare의 기존 Docker 검사는 읽기 전용 daemon/image 정보 확인이며 pull/기동은 하지 않는다. 같은 버전 문자열만으로 pinned 이미지 전용 REQ-07·REQ-11·OBS-09의 지원을 추론하지 않는다. 형식상 유효하지만 지원하지 않는 기업 정책은 입력 오류와 구분하고 다른 실행 가능한 점검을 임의로 제외하지 않는다.

지원 안내가 있어도 실제 이미지 소스·마운트·consumer·서버 응답·수집 경로는 실행 후 증거로 확인해야 한다. REQ-10 runner 실제 소비, OBS-03 exception dump, 기업 principal/collector와 GPU·성능은 이 작업에서 새로 검증하지 않는다.

최종 검토에서 OBS-03의 미지원 로그 분기·표식 문맥도 형태 오류와 분리했다. 기존 REQ-09/10/13과 같이 설정·예산·소스 목록 검사는 별도로 유지한다. 실제 실행의 정책 거부와 로컬 엔진 소스 연결 조건을 완화하지 않았으며 예외 dump 수집 기능을 추가한 것은 아니다.

## PR 누적 브랜치의 main 반영 상태

직전 평가에서는 PR #2~#18이 미병합 초안이었다. 2026-10-24 재조회에서는 모두 base=main·MERGED이며 병합 시각은 2026-10-23 23:29:21~23:33:46 KST다. 최종 main은 `24f7d90`이고 #18의 head `5a35251`을 포함한다. #1은 닫혔으나 그 코드는 #2의 실제 merge commit에 포함됐다. 이번 초안의 base는 main이다. 기존 기능을 다시 구현하거나 병합 완료 PR을 다시 만드는 작업은 필요 없다.

종전 계획의 선택지는 의존 순서대로 검토·병합하거나 최종 누적 브랜치의 별도 통합 PR을 검토하는 것이었다. 현재는 원격에서 병합이 완료돼 이 선택을 다시 실행하지 않는다. 향후에는 이번 PR의 main 대비 diff·최종 SHA CI·팩/metadata만 확인한다. 이 작업에서는 GitHub PR 병합·닫기·base 변경·rebase·force push를 수행하지 않았고, 원격 main의 독립 `.DS_Store` 변경도 보존했다.

## 다음 두 실제 이미지 업데이트 실증 준비 계획

1. 팀이 baseline/candidate의 서로 다른 실제 repository@sha256와 version을 선정한다. digest·아키텍처·로컬 보유 여부와 모델 호환성을 확인한다. 아직 확인하지 않은 candidate를 지원한다고 쓰지 않는다. 최초 최소 공통 범위는 기존 REQ-01·REQ-03·PLT-01·PLT-04·REQ-09에서 양쪽 실제 지원을 확인해 결정한다. 고정 이미지 전용 점검은 지원 표에 미지원/미확인을 그대로 표시한다.
2. 같은 모델 ID/revision/inventory, CPU/커널/threads/메모리 상한, fixture, 정책값과 실행 설정을 고정한다. 이미지 차이 외 설정 차이는 명시하고 비교 가능 조건을 확인한다. CPU 성능은 UNKNOWN으로 둔다. 지원 범위를 맞추려고 기업 정책을 축소하거나 예시 값을 기업 승인으로 승격하지 않는다.
3. 기존 컨테이너를 삭제·정리 대상으로 삼지 않는다. 필요한 이미지/모델 다운로드, 디스크·WSL2 메모리 여유, 외부 네트워크/시험 대상 권한과 비용 조건을 확인한 뒤 별도 실행 승인을 받는다. 하나씩 순차 실행하고 새 store/output 경로를 사용한다.
4. 각 side에 실제 MEASURED/COMPLETED EVD, 정상 대조·모든 계획 챌린지·필수 관측·Ref/첨부 해시를 요구한다. 이것만으로 수락하지 않는다. 공통 핵심 행의 양쪽 구체 PASS/FAIL과 의도한 NO_CHANGE/REGRESSED/FIXED/STILL_FAILING을 확인한다. UNKNOWN/NOT_COMPARABLE만 있는 실행은 제품 실증 성공으로 세지 않는다. 새로운 정의의 REQ-11 보안 UNKNOWN은 예상 제한으로 별도 기록한다.
5. 첫 비교는 같은 hardened 프로파일·모델·자원 조건을 고정하고 이미지 digest/버전만 바꾼다. 실제 양쪽 PASS/FAIL에 따라 NO_CHANGE/REGRESSED/FIXED/STILL_FAILING을 기록한다. 노출→강화처럼 의미 있는 실행 프로파일을 바꾼 진단은 config hash가 달라져 NOT_COMPARABLE인 것이 정상이다. 이를 이미지 업데이트의 FIXED/REGRESSED로 세지 않는다. 실제 관측이 예상과 다르면 원본을 보존하고 원인을 분석한다. 정리 실패·부분 수집·독립 FAIL 보존도 별도로 확인한다. 구현한 수락 도구와 현재 검증 결과는 [두 이미지 CPU 비교](UPDATE_VALIDATION.md)를 따른다.
6. JSON/HTML의 두 side, 비교 불가 이유·정책/팩 검토 상태·official 여부를 확인한다. 원본 verify→증거 반출→새 위치 복원→저장 팩 재판정에서 의미·원본 해시·멱등을 확인한다. 소유 컨테이너/네트워크만 정리하고 기존 모델/다른 컨테이너/과거 증거를 보존한다.

## 이번 직접 검증 기록

2026-10-24 Windows x64·CPython 3.11.16·uv 0.12.11에서 uv.lock frozen 전용 환경을 사용했다. 기준 Git은 main 24f7d90이고 테스트 대상 변경의 source tree 해시는 [검증 요약](evidence/automation-hardening-20261024.json)에 고정한다. 실제 엔진·Docker stub·GPU·두 이미지 실행은 하지 않았다.

| 단계 | 직접 결과 | 해석 |
|---|---|---|
| 최초 기본 전체 | 2040통과·26실패·5skip·18제외, 223.73초 | 테스트 보조 코드 가정 26건을 수정 |
| 보조 코드 수정 후 전체 | 2066통과·0실패·5skip·18제외, 217.46초 | 기존 제품 보강과 회귀 통과 |
| OBS-03 안내 추가 보수 후 전체 | 2084통과·3실패·5skip·18제외, 224.47초 | 새 준비 회귀의 정책 해시 확정 누락3건 수정 |
| 최종 전체 | 2087통과·0실패·5skip·18제외, 221.7초 | 추가21회귀와 정책 해시·구체 오류 확인을 포함 |
| 격리 wheel 설치 | 패키지78파일 source/wheel/설치본 일치·CLI30회 성공 | -I·저장소 밖·EXAMPLE 대역·실제 프로세스/HTTP/Docker 기동 차단 |

최초 실패19건은 정책 검사 테스트가 실제 클래스 대신 메서드 없는 SimpleNamespace를 전달한 가정, 3건은 side/event 목록에서 존재하지 않는 실행 ID를 찾은 가정, 4건은 테스트 Evidence 생성기의 지원하지 않는 PLAN/ENV keyword 인자 사용이었다. 실제 클래스 바인딩·컨테이너/네트워크 소유 ID·생성 후 Ref 설정으로 테스트를 보수했다. 추가 준비 회귀3건은 정책값 변경 후 다시 seal하지 않아 무결성 관문에서 막혔고, 테스트 정책을 다시 확정하고 구체적인 지원 표·오류 원인까지 확인하도록 수정했다. 정책 상한·실패 판정·필수 검증을 약화하지 않았다.

최종 skip5개는 Windows 루프백3·symlink2의 기존 제약이며 실제 engine/docker 표식18개는 기본 제외다. 둘 다 통과로 세지 않는다. 1분 목표는 달성하지 못했다. 앞선 1~3차 실제 CPU 결과나 이번 각 반복의 통과 수를 합산하지 않는다.

REQ-11 설치본 정상과 생성 출력16개 단독 교환은 보안 UNKNOWN/기능 PASS/성능 UNKNOWN, 타 요청 입력 소비는 보안 FAIL/기능 PASS/성능 UNKNOWN이었다. 전부 EXAMPLE·official=false이며 JSON/HTML에 새 범위와 미검증 이유가 표시됐다. 저장 팩19/정의2의 순수 재판정은 기존 PASS/PASS/UNKNOWN을 유지하고 과거 실측 결과를 변경하지 않았다. 새 제품 기준이 실제 엔진에서 재실증됐다고 해석하지 않는다.

sentinel은 실제 sandbox.up/_start_engine 코드와 memory Docker를 조합한 회귀에서 side 분리·기존 경로 보호·default/hardened·기동 실패·정리 실패·완료된 FAIL 보존을 확인했다. prepare는 side별 exact image 제한, valid unsupported 정책·잘못된 형태, 설정/소스 목록 실패와 다른 점검 보존을 대역으로 확인했다.

코어/models/store·reference·기존 증거/fixture 등 보호 추적175파일과 다른76개 CheckDef는 바이트가 같았다. 외부 복사본 팩 재생성2회는 멱등, metadata46경로 정확, 카탈로그322/614·분류·기업 정책값/범위 불변, 로컬 출처/JSON pointer 유효를 확인했다. 기존 외부 출처772개는 재조회하지 않고 보존했다.

설치 검증 보조 스크립트의 직접 click 의존성과 vendored Group 가정은 Typer 자체 Group으로 수정했다. 새 의존성을 추가하지 않았다. 최종 wheel의 첫 build는 Windows 전역 임시 경로의 PE resource 갱신에서 접근 거부됐고, 작업 전용 TEMP/TMP로 재시도해 성공했다. 제품 실행 실패와 구분한다.

직접 실행 명령의 경로를 축약한 재현 형식은 다음과 같다. OUT은 저장소 밖 새 출력 폴더다. 첫/수정후/최종 로그·JUnit·설치본·원본 실패는 각각 별도 경로로 보존했다.

```powershell
$env:UV_PROJECT_ENVIRONMENT="$OUT\source-venv"
uv sync --frozen
& "$OUT\source-venv\Scripts\python.exe" -X utf8 -m pytest -q --basetemp="$OUT\pytest-final-r3-tmp" --junitxml="$OUT\pytest-final-r3.xml" --durations=15
uv build --wheel --out-dir "$OUT\dist-final"
uv export --frozen --no-dev --no-emit-project --no-hashes --output-file "$OUT\requirements.txt"
uv venv --python 3.11 "$OUT\wheel-venv"
uv pip install --python "$OUT\wheel-venv\Scripts\python.exe" -r "$OUT\requirements.txt" "$OUT\dist-final\guard-0.1.0-py3-none-any.whl"
& "$OUT\wheel-venv\Scripts\python.exe" -I -X utf8 "$OUT\verify_installation.py"
& "$OUT\source-venv\Scripts\python.exe" -X utf8 "$OUT\verify_integration.py" $REPO
```

최종 head SHA의 GitHub 전체 CI·wheel 결과는 이 변경의 초안 PR에서 별도 확인한다. 이 문서의 로컬 테스트 수를 CI 수로 표시하지 않는다. 다음 실행은 앞의 두 실제 이미지 준비 조건과 팀 정책 결정을 충족한 별도 작업이다.
