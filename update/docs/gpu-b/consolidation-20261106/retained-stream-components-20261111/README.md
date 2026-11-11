# GB12 보관 원본 스트림 부품

고정 원본 GB12의 read-only sender·strict receiver, 정식 Session Manager 감독 코드와 기존 인스턴스 기동 연결부의 검토 원문을 보존한다. 원 파일 bytes·SHA는 [source-manifest.json](source-manifest.json), 검증의 시점과 범위는 [verification-scope.json](verification-scope.json)에 있다.

`gb12_stream.py`의 CLI는 원 archive 경로·14,636,464B·SHA를 고정한다. 새 요청 UUID와 실제 guest boot, 동일 FD의 source UID1000·0600·단일 링크·파일/부모 경로 신원, canonical H/D/F frame·순서·총량·SHA를 확인한다. frame 단위는12,000B다. receiver는 새 private 출력의 FD를 재해시하고 완전한 footer와 EOF 뒤 배타 게시한다. 실제 SessionManager 감독 코드는 native CLI·원격 명령·서버 session 종료를 확인한 뒤 `finish()`를 호출해야 한다.

Deadline은 기존 absolute CLOCK_MONOTONIC 끝값을 소비한다. 인증·연결 전 만든 원900초를 각 host boot·clock과 결합해야 하며 이 부품은 새900초를 시작하지 않는다. guest clock mapping·기존 관리 문서·세션 권한·정리 경로 결합은 운영 담당 범위다.

`session_recovery.py`는 기존 관리 문서 `AWS-StartNonInteractiveCommand`와 정식 AWS CLI/plugin을 사용한다. 전송용 첫 인증 전에 만든 원900초, 보수적인 guest boot/clock mapping, 최초 수락 관측65초, 고정 in-memory sender와 원격 종료 marker, 로컬 CLI 회수·EOF·정확한 server session 종료를 연결한 뒤 receiver를 게시한다. Terminating·일시적0행은 같은 기한 안에서 읽기 재관측한다. 자기 SessionId만 Terminate1회로 정리하고 최초 오류와 불확실성을 보존한다. `invoke_session_recovery.py`는 고정 파일·entry dispatch와 실제 정지 영수증의 nonce를 기존 child outcome에 연결한다. 이 source/후보·CPU의 [집중 검토 원문](session-supervisor-focused-review.json)을 함께 보존했다. 실제 StartSession 권한·RunAs·전송 성공은 CPU 결과에 포함하지 않는다.

`start_existing_once.py`는 검토된 invoker·loader와 기존 driver.start()를 연결한다. dependency는 이전 회수 source map의 동일 원문이며 driver.main/execute를 호출하지 않는다. R1에서 기존 인스턴스 start1은 실제 수락됐고 health는 통과했으나 원 압축 codec의3MiB 상한으로 archive GET 이전에 실패했다. [실제 운영 기록](../SUP16_ACTUAL_OBSERVATIONS_20261111.md)에 원 오류·정지 감시 실패·후속 실제 정지를 구분했다.

v1 합성 CPU30의 실패2를 보존했고 v2에서 해당 결함을 고쳤다. v2 protocol30·실제 로컬 FD8은 당시 source의 결과다. v3는 기존 접근 guard와 frame 단위 변경의 새 영향19를 확인했다. 독립 검토에서 새 부모 FD 상태 조회 실패의 정리 누락을 찾아 v4에서 두 조회를 정리 보호 구간으로 옮기고 새 실패 주입2를 확인했다. 이 숫자는 제품 점검 완료 수가 아니며, 기존 결과를 v4 전체 suite 통과로 바꾸지 않는다.

이 공개 묶음은 부품 준비 범위다. 실제 GB12 스트림 전송·전체 SHA/CRC·복원·Ref 검증 및 SUP-16 정상/부분 파일/복구 실증은 아직 미완료다. 원 FAIL·UNKNOWN·이전 partial·팩·제품 source 목록을 유지한다.
