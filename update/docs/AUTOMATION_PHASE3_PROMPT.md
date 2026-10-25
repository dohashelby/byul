# 자동화 3차 실행 프롬프트

너는 gua 정책 자동화 구현 담당자다. 기준은 `1952533efa0c4cd292fdbaede9f0abda7977fb30`, 통합 브랜치는 `codex/gua-automation-phase3`이며 초안 PR의 기준은 `codex/gua-automation-phase2`다. 사용자가 다음 정책 자동화 단계의 진행을 확인했다. 다음 범위를 구현·문서화하고, 전체 구현을 마친 뒤 테스트·실패 수정·검증을 수행해 커밋·푸시·초안 PR까지 완료한다.

## 공통 계약

- SPEC.md, CLAUDE.md, PROGRESS.md, DECISIONS.md와 실제 코드·저장 증거를 먼저 확인한다. 문서의 완료 주장은 검증 대상이다.
- 기존 로컬 변경·과거 팩과 증거·모델 snapshot·kind 등 관련 없는 컨테이너를 보존한다. sources/는 읽기 전용이다. 코어·객체 7종·추가 전용 저장소·불변식은 유지하며 새 의존성을 추가하지 않는다.
- 비용 발생 서버·운영 시스템·GPU 시험·임의 엔진 재구성은 하지 않는다. 승인된 고정 CPU 이미지와 작은 모델로 엔진 하나씩 실행한다. 이미지·모델 revision·파일 무결성·자원·실제 argv/env를 기록한다.
- 새 코드·회귀 테스트 작성·팩/메타데이터/예제/문서 초안을 한 묶음으로 마친 뒤 테스트한다. 실행하지 않은 결과나 예제를 MEASURED로 표시하지 않는다. 직접 실행과 과거 기록, unit/fake/stub/실제 엔진을 구분한다.
- 확인한 독립 보안 위반은 뒤의 수집 실패로 지우지 않는다. 미수집·미지원·잘림은 UNKNOWN이며 기능·성능 축을 따로 판단한다. 판정기는 엔진·실행기·I/O·시각에 의존하지 않는다.

## REQ-11

`request-connection-binding-v1`은 고정 text-chat CPU 프로파일에 직접 연결하는 두 클라이언트의 일반 JSON과 SSE 귀속을 확인한다. 기업 principal·권한·Gateway·SDK·child·취소·재시도·저장 결과·worker 내부 동시 실행은 범위 밖이다.

정책은 `result_binding`, `client_request_id_trust`를 명시적으로 입력받는다. 지원 조합은 `originating_connection`, `untrusted_correlation`이며 기업 신원·공유 정책 전체를 대체하지 않는다. 다른 비어 있지 않은 값은 유효한 정책 형태지만 해당 시험 프로파일에서는 UNKNOWN이다.

서버 생성 ID, 서로 다른 body ID, 같은 body ID, 같은 헤더 ID와 다른 body ID를 각각 일반/SSE 두 연결에서 시험한다. ID 충돌 자체나 생성 문구의 우연 일치는 보안 실패 근거가 아니다. `/tokenize`와 고정 렌더 문구를 소비 측 prompt IDs/text에 정확히 대조한다. 두 연결의 송신·수집 구간을 기록하되 클라이언트 구간 중첩을 worker 내부 병렬 처리로 표시하지 않는다.

별도 stdlib 수집기는 최대 두 연결, HTTP/1.1, 공통 송신 barrier, 전체 deadline, 바이트·event·line·header·요청 본문·출력·읽기 지연 상한을 강제한다. 엄격 UTF-8, 부분 frame, 종료 신호와 HTTP framing 종료를 구분하고 원 캡처 digest와 저장용 정제 digest를 혼동하지 않는다. 비밀은 stdin으로 전달하며 예외 원문을 노출하지 않는다.

다른 연결의 정확한 소비 입력이나 한 연결의 다른 요청 ID 귀속을 확인하면 독립 FAIL이다. 단순 프로토콜 불완전·중복 종료·미지원 스키마만으로 사용자 간 유출을 단정하지 않는다. 모든 정상 대조·필수 관측·챌린지·실제 이미지/고정 template 신원 확인이 있어야 표본 범위 PASS를 허용한다.

## OBS-09

`guard-vllm-native-info-v1`은 고정 이미지의 실제 INFO request logger와 NewLineFormatter를 읽고, 실제 Docker CLI 로그에서 guard 참조 수집기를 거쳐 구조화 JSON·별도 escaped HTML 첨부까지 연결한다. 기업 수집기·SIEM·원시 daemon 바이트·사용자 principal·권한 인증은 주장하지 않는다.

`collector_profile`, `required_trusted_fields`, `client_request_id_trust`를 입력받는다. 지원 trusted field는 process/level/source이며 실제 로그가 제공하지 않는 actor/status를 본문에서 만들어내지 않는다. 본문과 외부 request ID는 신뢰하지 않는 데이터로 취급한다.

별도 명시적 `logging_profile='request-info-v1'`만 허용한다. 고정 env와 `--enable-log-requests --max-log-len 512`를 기동·비교 조건에 기록하며 임의 logging config/class/path를 받지 않는다. 기존 기본 프로파일은 보존한다.

LF·CR·CRLF·예약 필드처럼 보이는 문자열·HTML 문자열을 무해한 body request_id에 넣어 실제 logger 도달 여부를 확인한다. CRLF가 헤더에서 거부됐다는 주장은 하지 않는다. LF만 나누어 실제 formatter의 CRLF/prefix 경계를 보존하고, 불명확한 경계나 대체 디코딩 문자는 UNKNOWN으로 남긴다. 외부 데이터에서 trusted field를 승격하지 않으며 HTML은 escape한다. 엔진 자체 발행 cardinality와 수집기 event 경계를 구분한다.

실제 owned container의 image/label/argv/env/StartedAt를 전후 확인한다. CLI UTF-8 디코딩 출처와 수집 window·상한·원문/정제 digest를 명시한다. JSON/HTML 첨부는 EVD artifact이며, 주요 RPT가 상세 artifact를 자동 표시한다고 주장하지 않는다.

## 통합·검증·인계

두 실행기를 제품 registry에 등록하고 overrides에서 팩을 재생성한다. 원래 복합 기업 정책의 분류는 보존하고 지원하는 입력 투영 5개만 카탈로그에 추가한다. 선언 경로 metadata, 예시 DRAFT 정책, 별도 CPU 설정, automation matrix를 갱신한다. 수동 PASS에는 같은 순수 정책 검증·판정표 상한을 적용하고 이전 CheckDef 의미는 유지한다.

기본 전체 테스트와 의미 있는 계약·실패 회귀를 실행하고 skip/제외/실패 원인을 공개한다. 실제 엔진 실행은 별도 산출물에 저장하고 제품 verify·반출·새 위치 복원·저장 팩 재판정·JSON/HTML·재검증을 확인한다. 기존 결과·파일·모델·자원 상태가 바뀌지 않았는지 확인한다.

새 frozen 환경의 wheel 설치·외부 경로 실행·소스 파일 바이트 일치·CLI·읽기 전용 prepare를 확인한다. 실제 엔진 생성/정리와 자원 불변 검사 시간은 겹치지 않게 한다. 팩 재생성·카탈로그 연결·소스 증거·과거 추적 파일 불변을 확인하고 선별 요약만 Git에 넣는다.

최종 문서에는 사실/해석/남은 범위, 직접 명령·환경·결과·시간·원인, official=false·팩/정책 DRAFT, 신뢰 경계와 새 산출물을 적는다. 안전한 일반 commit/push와 초안 PR을 만들고 최종 SHA의 CI 및 원격 일치를 확인한다. merge·force push는 하지 않는다.
