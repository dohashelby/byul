# OBS01 필수 사건 기록 제품 연결 계획

확정 ec4df8829c875831d5cbbe1b61f234cdcb4fd31f에서 이어간다. 대상은 OBS-01의 SC-23-03(다른 대상/실행 귀속), SC-23-02(수집 장애·누락) 두 분기다. A-1 19개/27분기/필수2결과, 부모 A39/56/4를 유지한다. OBS02 상태 표현과 OBS11 수집 경보를 이 묶음 완료로 대체하지 않는다.

실제 소비자는 기존 guard select/run/judge/report/verify/evidence export/restore와 정확 Ref 재판정 경로다. 내부 정책 초안은 정확 ENV/target SHA·aware 최대600초 창·수집 기한·필수 사건 및 필드를 받는다. 필수 사건은 접근 허용/거부·관리·승인·공급물 소비·배포의 닫힌 목록이며, 선택된 항목을 수집 불가능하다고 삭제하거나 정상 HTTP로 완료 처리하지 않는다. actor·target·run·policy는 필수 필드이며 APPROVAL은 approval ID도 필요하다. 실제 정책값과 trusted principal을 생성하지 않는다.

구현 순서는 다음과 같다.

1. REQ02의 고정 소유 파일 reader·전후 대상 세대·lab/source/image·시간/출력 상한을 검토한다. 기존 source pin과 B/A2 bytes는 유지한다. 일반 JSON 파일을 제품 명령의 실행 자료로 받지 않는다.
2. 기존 안전 프로브의 허용/거부 사건 발생과 해당 기록 생산자의 주체·대상·run/request·정책/승인 ID를 독립적으로 연결할 수 있는 닫힌 계약을 고정한다. 현재 native8~11의 request·collector·ASGI 관측만으로 trusted actor나 승인 일치를 추정하지 않는다. 실제 생성원이 없는 사건은 미지원/필수 근거 부족을 명시한다.
3. 등록된 전송·수집·native session 경계에서 실제 수집을 수행하는 정식 실행기와 strict parser를 연결한다. 정상·독립 확인된 위반·누락·교체/restart·수집 중단/기한 내 복구·후속 첨부/정리 오류에서 이전 사실을 보존한다. 독립 사건 발생/신뢰 기록이 없는 경우는 UNKNOWN이다.
4. 원래77개와 다른76개 bytes를 보존하는 DRAFT 팩에서 정책·관측·규칙을 명시한다. source고정 후 직접/인접/설치본CLI·정확6Ref 왕복을 검증하고 확정 커밋/bytes SHA/판본/지원범위를 인계한다.

대략 3묶음(계약/수집, 제품/판정, 고정 검증·설치·인계) 규모다. trusted Gateway principal/실제 승인·배포 기록의 안전한 수집점·기업 필수 사건/기한/보존 조건은 외부 의존성이다. 이 입력이 없어도 다른 준비된 A-1 개발은 계속한다.

현재는 내부 policy validator와 이 계획만 초안이다. profile/producer/parser/registry/CLI 지원과 검증은 아직 등록하지 않았다. OBS01 전체·다른 체크·실제 serving/worker/GPU 완료로 기록하지 않는다. 기존 원래GPU창 종료·추가Start0·마지막확인stopped·CI결제차단과 기존 증거를 유지한다.

이후 구현 초안: 명시 OBS01 공개 HTTP profile과 별도 고정 소유 record reader, 정제 schema/parser·현재 probe/선언 record 대조·제품 registry·DRAFT77개 팩·CLI/정확Ref/누락·교체·후속오류 시험을 작성했다. 실제 감사 생산자 설치/인증이나 관리/승인/배포의 독립 발생원은 아직 제공하지 않는다. 지원할 범위와 미지원은 A1_REQUIRED_EVENT_RECORD_PRODUCT.md에 고정하고, 소스 checkpoint 뒤 실제 영향/설치 검증을 진행한다.
