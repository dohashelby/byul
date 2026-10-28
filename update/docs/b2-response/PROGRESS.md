# B-2 개발 기록 — 2026-10-28

한 일: 인계 baseline/ownership와 SPEC 객체·CheckDef·EVD/RES·불변식 및 contracts 경계를 확인했다.
다른 세션의 미커밋은 보존하고 별도 clone/브랜치/가상환경에서 작업했다.
REQ-13 기존 실행기를 재작성하지 않고 검증 오류 응답과 지정 로그의 독립 관측 helper를 추가했다.
A PR44 진행본과 대조하고 공통 파일 인계를 요청했다. override/팩/registry는 변경하지 않았다.

검증: 전용 계약+기존 REQ-13 회귀 **71 passed / pytest 147.54s**.
그 뒤 escaped JSON key 표식 관측을 보완하고 최종 전용 계약+기존 로그 수집/parser 계약
**59 passed / pytest 0.23s / 부모 0.357s**로 재검증했다.
기존 response_content는 이 보완의 호출자가 아니므로 71개를 다시 반복하지 않았다.
wheel 빌드 및 helper 포함 확인, diff whitespace 검사를 통과했다.
최종 원격 HEAD의 CI 상태는 PR과 저장소 밖 인계 JSON에 기록한다.

실제 CPU: vLLM v0.31.0 `db9527a46873454610df6dbedf79a36d6bf1a7f6`의
manifest 고정 정의를 Python 3.12.14에서 실행했다. 실제 FastAPI 0.115.12,
Starlette 0.46.2, Pydantic 2.14.1, regex 2024.11.6을 격리 설치했다.
최종 코드 실행·관측 assert·원문 미포함 검사 **통과 / 부모 0.077s**.
엔진 package 전체 import/HTTP routing/추론/Docker/model/GPU/AWS는 실행하지 않았다.
실제 참조 정의 실행 사실과 EXAMPLE 입력·주입 경계를 구분하고 제품 EVD를 만들지 않았다.

REQ-13 관측: 검증 오류의 반환값에는 error_input만 있고 system 표식은 없었다.
HTTP 오류의 합성 경로·주소는 응답에서 제거됐다.
log_error_stack=false 지정 logger에는 해당 표식이 없고 true에는 오류 입력 또는 경로·주소가 남았다.
이는 참조 분기의 제한된 사실이며 모든 정보 부재·모든 로그 안전·기업 정책 PASS가 아니다.

후속 OBS-03: 실제 RequestLogger.log_inputs의 INFO에는 prompt 표식이 없고 DEBUG에는 있었다.
기업의 debug 사용/열람 허용 정책은 미정이며 임의 정책으로 등록하지 않았다.
OBS-09: 실제 formatter + 기존 reference consumer에서 정상/줄바꿈/CR 세 사례 모두 한 사건으로
분석됐다. 합성 timestamp·logger 설정이며 실제 collector·생산자 신원 인증은 아니다.
OBS-11의 잘림/누락/시각/독립 FAIL 보존은 기존 capture 계약 회귀를 재사용했다.

막힌 연결: A의 확정 통합 SHA·팩 Ref·response_content 인계 및 실제 설치 대상 미정.
OBS-06~08·10~12의 외부 저장소·주체별 권한·replay consumer·보존 정책/사본 목록도 미제공이다.
각 준비 절차와 필요한 관측은 storage-handoff.md에 있다. 코드 완료와 외부 실증 완료를 구별한다.

다음: A의 고정 소스에서 기존 실행→EVD→RES/RPT와 정제·첨부 회귀를 검증한다.
그 뒤 제공된 첫 저장소의 OBS-06 조회/반출 하위 결과부터 주체·정책·대상을 고정한다.
상세 CPU 관측·실패했던 이전 도구 실행의 수정 이력·snapshot·의존성 목록은
저장소 밖 `byul-b2-evidence-20261028/`에 보존한다.

## 갱신 배정 후 PR47 마감

공유 기록 `Projects/DEV/byul-b-coordination/{current,next}.json`을 `.lock` mkdir로
짧게 보호하여 B-2 분기를 선점했다. B-1의 REQ-10·SUP-10 파일은 수정하지 않았다.

직접 결함: 완전한 JSON 문자열 token 안의 escaped 표식이 중복 키/후속 잘림으로
소실되던 경우를 보완했다. 구조는 UNKNOWN 자료로 유지하며 독립 위반만 보존한다.
공식 시험의 원래 assertion을 재작성하지 않고 dependency adapter로 실행했다.
handler param/loc·endpoint 경로·크기 제한과 sanitizer의 **30 passed / 0.262s**, skip 0.
첫 harness의 pytest import-file identity 수집 오류를 수정했으며 원래 assertion은 그대로다.

전용·인접 계약 **64 passed / pytest 0.23s / 부모 0.361s**.
마지막 harness 경로 수정 뒤 adapter 직접 계약 **2 passed / 0.13s**를 재검증했다.
실제 출력 logger 정상/stream delta/stream complete × INFO/DEBUG 6사례 모두에서
합성 출력 표식을 관측했다. 실제 생성·전체 streaming HTTP·기업 허용 정책의 검증은 아니다.
입력 logger의 INFO 무표식을 전체 logger 안전으로 확대하지 않는다.
원문 미포함 assertion도 통과했다. 상세 자료는 `pr47-followup-*`, `pr47-upstream-2/summary.json`이다.

같은 기능의 후속 구현·회귀·실제 CPU 검증을 열린 PR47에 모았다.
제품 연결은 A 확정 SHA/팩 Ref/파일 인계가 필요하다. 응답 수신자·debug 로그 정책은 미정,
OBS-06~08·10~12의 실제 저장·IAM·consumer·보존 집행은 외부 조건이 필요하다.
이 대기를 다른 준비된 B 분기의 CPU 작업 대기로 확대하지 않는다.
