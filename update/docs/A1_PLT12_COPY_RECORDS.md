# PLT12 사본 처리 기록 소비 초안

상태: 구현·시험 코드 초안. 아직 검증 source/hash를 고정하지 않았고 기능·설치·정확 Ref 검증과 확정 커밋/전달은 마감 전이다. 실제 사본 멤버십·처리·재사용 집행은 미연결이며 전체 PLT12/부모 A 완료를 주장하지 않는다.

소비자는 기존 `required_event_record:kubernetes` 실행기다. PLT12의 명시 `kubernetes-credential-copy-record-observation-v1` 선택에서만 새 기록 계약을 사용하며, OBS01 /1의 사건·역할·요청 의미는 유지한다. 공통 `RequiredEventReader`의 선정 소유 session 파일 읽기와 ENV/target/Pod/container/image/restart/start 경계를 재사용한다. A-2의 credential 후보 관측·파일 판정은 수정하지 않는다.

```toml
[executors]
required_event_record = "kubernetes"
[adapter]
name = "vllm"
[required_event_record]
target = "/approved/target.json"
copy_inventory = "/approved/nonsecret-copy-inventory.json"
```

`copy_inventory`는 조사할 자격 파일이 아니라, 비민감 선언을 담은 로컬 입력 JSON이다. POL의 `copy_inventory_sha256`에 원 bytes를 먼저 고정한다. 실행 중 읽은 hash를 승인값으로 만들지 않는다. 기존 OBS01과 함께 같은 ENV/target을 쓰면 그 역할 설정을 유지할 수 있지만 PLT12는 해당 역할 키를 읽거나 HTTP/issuer/reuse 요청을 보내지 않는다.

입력 inventory schema는 `guard-credential-copy-inventory/1`이다. `credential_family_id`, `policy_sha256`, `copies`를 명시한다. 각 copy는 UUID 형태의 비민감 `copy_id`, 세대, location/recipient ID, 닫힌 location 종류, 요구 처리 방법, timezone-aware 기한, 명시 보존 예외 또는 null을 가진다. 보존 예외는 별도 UUID·만료·접근 제한 목록을 가진다. `restrict` 요구에는 예외/제한이 필요하다. 실제 위치·수신자·기한·방법·예외는 사용자 결정이며 기본값을 만들지 않는다. 빈 inventory는 실제 사본 부재나 비사용의 증거가 아니다.

기록 schema는 `guard-credential-copy-records/1`이다. 고정 ENV Ref/target/session/source/inventory/policy hash, producer UUID, snapshot 세대 UUID, 기록 시각, 선언된 complete와 사건을 갖는다. 사건은 `COPY_INVENTORY_CLAIM`, `TREATMENT_CLAIM`, `REUSE_CLAIM`, `ISSUER_CLAIM`으로 구분하며 copy/세대/location/recipient/actor와 시각·seq·결과를 명시한다. UUID 라벨은 실제 자격 값과 무관하게 정해야 하며 자격 값 또는 자격 hash를 ID로 넘기지 않는다. 실제 경로·환경/Secret 이름·원문 요청/응답·비밀·임의 예외 원문을 기록할 필드는 없다. producer 인증·실제 처리 완료·MEASURED 승격을 주장하는 필드는 허용하지 않는다.

기록 위치는 기존 OBS01 reader의 `/guard-observation/data/<copy_record_session>/required-events.json`으로 닫혀 있다. PLT12 record와 OBS01 record는 각자 선택한 schema/session으로 검사한다. 수집한 파일의 바이트·소유 Pod 결합은 해당 기록에 적힌 사본 멤버십이나 처리·issuer 권한을 인증하지 않는다.

projection은 같은 사건의 반복 snapshot을 중복 집계하지 않고, ID/seq 상충·다른 사본 세대/위치/수신자·producer/snapshot 교체·선언 시계/지연·누락/부분을 구분한다. 선언된 기한/방법 충돌, 실패, 제한 보존 일치, 재사용 허용/거부와 issuer 선언은 각각 보존한다. 실제 처리·금지된 재사용의 확인으로 올리지 않는다. readable/absent·mount·Secret 등록이나 issuer 거부만으로 폐기 처리의 PASS를 주지 않는다.

정상 기록 소비의 실행 상태는 COMPLETED일 수 있지만 정상대조는 attempted=false/succeeded=null이며 모든 판정 축은 UNKNOWN이다. 기술적 수집/정합성 공백과 후속 정리 오류는 ABORTED로 구분하고 앞선 유효 snapshot·관측과 기존 독립 FAIL을 보존한다. file/producer 선언은 EXAMPLE 또는 DECLARED 범위를 유지하며 자기 complete/source hash만으로 MEASURED가 되지 않는다.

필수 POL 입력은 `copy_environment_sha256`, `copy_target_sha256`, `copy_inventory_sha256`, `copy_policy_sha256`, `copy_record_source_sha256`, `copy_record_session`, aware 시작/종료, 선언 기록의 최대 지연이다. 유효창은 600초 이내, inventory는 최대64 copy, snapshot은 최대256 event와 기존 bounded 파일 상한을 사용한다. 이 상한은 기업의 폐기 기한·허용 기준이 아니다.

팩 생성기는 원 PACK31의 PLT12 질문·정상대조·절차·PASS/FAIL/UNKNOWN 문구와 나머지76개 정의 bytes를 보존한다. 기존 select→run/수집→EVD→RES→RPT→verify→반출/복원·저장팩 정확 Ref 재판정 경로를 사용한다.

실제 생산자·신뢰·사본의 멤버십/위치/수신자, 처리 기한/방법/보존 예외, 허용 수집 범위, 실제 reuse enforcer·issuer·정상대조와 복구/정리 책임은 기존 질문의 미확정 입력이다. 실제 사본 내용 취득·교체/회수/삭제·권한 변경·자격 재사용은 이 읽기 전용 기록 묶음의 수행 결과가 아니다. 원 source/image/lab gate·native0.32 미지원과 원GPU창종료/추가Start0·기존 비용/EBS·CI 경계는 유지한다.
