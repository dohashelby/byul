# OBS01 필수 사건 기록의 Kubernetes 제품 경계

이 판본은 기존 정식 CLI에서 OBS01의 허용·거부·전후 정상 요청과 별도 소유 기록을 수집한다. 원래 SC23-03 귀속 오류·SC23-02 수집 장애, 필수 접근·관리·승인·소비·배포·보존/기한 요구와 A39/56/4·A1 19/27/2를 유지한다. OBS02 상태 표현과 OBS11 장애 경보도 별도다.

`tools/build_kubernetes_required_event_pack.py`는 검토된 PACK31에서 DRAFT PACK-KUBERNETES-REQUIRED-EVENT-RECORD revision1/OBS01 check4를 만든다. 원래77개 순서·기준·다른76개 정의 bytes는 보존한다. `[executors] required_event_record="kubernetes"`, `[required_event_record] target=<정확 target 경로>, roles={allowed=<환경변수 이름>,denied=<다른 환경변수 이름>}`로 기존 select/run/judge/report/verify/evidence export/restore·정확Ref 재판정 경로를 쓴다. key 값·원문 응답·Pod 환경·예외문은 저장하지 않는다.

필수 정책은 정확 ENV/target SHA, 선정 record session, 사전 검토 source SHA 주장과 기업 event policy SHA, 서로 다른 두 actor SHA, aware 최대600초 창, 최대300000ms 수집 도착 상한, 필수 사건·필드다. actor/target/run/policy는 필수이고 APPROVAL 선택 시 approval SHA도 필수다. event policy는 기업 인가/감사 정책의 판본이며 Guard POL 자신의 hash를 순환 참조하지 않는다. Guard POL 정확 Ref는 기존 코어가 EVD에 별도로 결합한다. 관측한 파일/hash/키를 승인값으로 사용하지 않는다.

전용 공개 전송 프로필 `kubernetes-owned-required-event-http-v1`은 `/v1/chat/completions`의 hello 메시지·max_tokens1·streamfalse·선정 모델만 받는다. obs01-before/denied/after와 해당 byul-obs01 UUID 헤더만 허용한다. before/after의 credential은 같고 denied는 다르다. 별도 안전 정상구조가 확인돼야 denied를 보낸다. 다른 점검 ID, 추가 header/field, private/다른 경로, model 교체, stream·요청/출력/시간 상한 확대는 거부한다. 요청은 소유 probe 프로세스에서만 실행하며 engine/worker 권한을 대신하지 않는다.

기록은 선택한 engine의 `/guard-observation/data/<session>/required-events.json` 한 파일만 기존 nofollow/fstat/파일전후신원·namespace/Pod/container/image/restart/start 검증으로 최대65536bytes·호출당5초 안에 읽는다. 실제 serving Python hook으로 위장하는 별도 exec는 없다. 이 exec는 고정 읽기 helper다. 새 기록 프로필은 lab=true/vLLM0.31에 한정하며 native0.32/실제 배포/신뢰 source 지원을 열지 않는다.

내부 정제 파일은 guard-required-event-records/1의 정확 schema/session/ENV/target/producer source SHA 주장, producer_authenticated=false, source_ended/dropped_events, 최대64개 순번 사건이다. 사건은 event_id SHA·닫힌 사건 종류와 nullable SHA 필드만 가진다. raw/header/detail/임의 field 또는 producer_authenticated=true 주장은 거부한다. source_ended도 미인증 주장이다. 이 판본은 감사 생산자 설치·인증을 제공하지 않는다.

두 수집 시점의 같은 사건은 seq·내용이 같은 경우만 중복 제거한다. request SHA로 현재 실제 probe와 연결하고 선언된 actor/target/run/event policy/source/result를 대조한다. result SHA는 `canonical_json({"outcome":<HTTP 상태>})`의 SHA다. 수집 도착 상한은 같은 수집기 monotonic 시계에서 요청 반환부터 파일 확보까지의 상한이며 producer 시각·보존 증명이나 실제 사건 기한 충족의 독립 증거가 아니다.

누락·다른 귀속·중복·기한 부족·필수 미지원 사건은 명시 관측으로 남긴다. 뒤 파일/정리/첨부 오류가 이전 정제 기록과 후보를 지우지 않는다. 초기 읽기 중단 후 최종 기록을 확보하면 recovered 관측을 남기지만 기존 reader gap도 보존한다. 선언된 actor/대상 불일치와 중복은 confirmed_violation=false 후보다. 접근 응답과 기록 주장은 실제 trusted principal·승인·배포 사건의 독립 증거가 아니므로 producer_authenticated/independent_trigger_verified/scope_complete=false와 모든 판정 축UNKNOWN을 유지한다. 다른 기존 체크에서 확인한 독립FAIL 의미는 바꾸지 않는다.

HTTP/record 부모 정리 오류가 남으면 실행도 ABORTED로 기록한다. 전후 API 응답 구조는 trials의 관측으로만 남기며, OBS01 정상대조 전체의 `succeeded`는 독립 감사 근거가 없는 동안 null이다. 절차 실행이 COMPLETED여도 정상대조·점검의 PASS를 의미하지 않는다.

선택된 관리·승인·공급물 소비·배포 사건은 실제 독립 발생원과 생성·전달·보존 계약이 준비되지 않으면 missing에 남는다. 이 기능만으로 전체 OBS01 두분기나 부모A를 완료 처리하지 않는다. 재개에는 실제 trusted Gateway principal/감사 생산자와 정확 ENV/source/image/정책·정상/위반/누락/교체/복구 절차가 필요하다. 기존GPU창 만료·추가Start0·마지막확인stopped·기존 비용·CI결제차단은 유지한다.

구현·시험·문서 초안을 마친 뒤 고정 checkpoint와 검토된 전체 소비 pin을 만든다. 직접/인접/불변식·동일 bytes Linux·183 이상 설치본 CLI/6Ref 왕복의 실제 결과와 지원 범위는 최종 고정 인계에 기록한다. CPU/EXAMPLE과 새 실제 배포·worker/GPU 실증은 구분한다.
