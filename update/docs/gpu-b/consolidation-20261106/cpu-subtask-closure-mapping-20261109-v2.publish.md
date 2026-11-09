# 기존 CPU 완료 범위 대조 v2 — 게시 전 정리

기준 source `7adc099ec6bf2f7ed2092a59c9bc0237731479f5` 및 원 v1 SHA `e650bce568014bea47e7c41745aa2d7c5edc35a178e35025421e32803ee15473`를 유지한다. CPU 완료 근거10묶음과 모든 기존 Ref·해시·판정/출처 값은 보존했다. 10은 근거 묶음 행 수이며 완료 ID·업무 총수·코드 수정·실행 묶음 수가 아니다.

| 구분 | 남은 범위 |
|---|---|
| GPU1 | 선정 REQ08/09/10 capture·제품 실행과, 기존 승인 단일 GPU 순차 구성에서 SUP16 normal/partial component 역할의 실제 worker·writer·소비 관측 |
| 실제 연동: SUP16 full pair | 현재 factory가 한 호출에서 읽는 **두 개의 실제 live target과 자원 결합**. 두 target의 역할·동시 유효성·실행 신원·수명/자원 및 지원 구성이 필요하다. 기존 승인 단일 GPU 순차 component 구성의 완료 가능 범위에 포함하지 않는다. 필요한 동시 자원·구성은 별도 확정 대상이며 TP2로 자동 분류하지 않는다 |
| TP2 | LIF08 지정 rank 장애/감지/복구, LIF11 실패그룹·후속세대/자원 반환, SUP06 실제 worker/rank shard 및 등록 |

SUP16의 삭제된 정상 Pod/종료된 partial worker를 재생하거나 collect 결과를 주입해 full pair의 두 실제 live target을 대체할 수 없다는 원 한계는 유지한다. 다른 실제 연동 잔여와 GPU1·TP2의 기존 세부 근거는 JSON에 보존했다.

원43행의 CPU_DEVELOPMENT_REMAINING36·ENTERPRISE_INPUT_WAITING1·DONE2·MEASUREMENT_REMAINING4는 **해당 원표의 보존 상태이며, 현재 새로 남은 코드 작업36건을 의미하지 않는다.** 현재 코드 수정 건수 총계와 전체 적용 분기·실행 묶음 총수는 미확정이다. lab2/41·과거한정25/제품증거18·이전로컬관측14는 다른 축이며 25를 일괄 DONE으로 처리하지 않는다.

과거5개(LIF08/12/16·OBS02·SUP08)는 사용자 답변 **미확인**을 유지한다. 새 원자료 문의·검색·시험·실험·AWS/SSM·복원/재판정·정본/제품/manifest 수정·외부 쓰기는 0이다. 자기 out의 v1 JSON/MD만 읽어 파생했다. 실제 GPU b85 결과와 최신509파일 통합판도 구분을 유지한다.

정확 근거 파일: `cpu-subtask-closure-mapping-20261109-v2.publish.json`
게시용 JSON SHA-256: `b1a7f5b8266b0c896139cba25474190c404c4395800fed4ee0e9902c0a04fc00`
경로 치환 전 로컬 v2 JSON SHA-256: `d4271822e4860a19c4e46b4dfc37c4e003f34d8d2f87fd7d2a2811b4e2e20028`

개인 절대경로는 `$PROJECT_ROOT`(프로젝트 루트), `$USER_DOCUMENTS`(사용자 Documents), 필요한 경우 `$WINDOWS_USER_HOME`으로 표시한다. 과거 macOS 홈 alias `$HISTORICAL_MAC_HOME`은 이번 원문에서 치환0건이다. 원 근거 파일 SHA와 source commit은 그대로이며, 치환 위치·원 문자열 SHA·치환값은 `publication-path-alias-changes-v2.json`에 기록한다. 저장소 목적지가 확정되지 않아 상대 링크나 GitHub 링크를 새로 만들지 않았다.
