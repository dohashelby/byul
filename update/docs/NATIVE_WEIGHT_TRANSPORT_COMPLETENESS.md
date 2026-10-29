# Native 가중치 파일 수집과 transport 불완전 상태

기존 `capture-native-weight-files` 경로는 native 선택 기록과 독립 파일 해시를
연결한다. 파일별 세대·경로·해시 관측과 수집 전체의 완결성을 구분한다.

공통 reader의 `collection_gap=true` 또는 `parent_cleanup_unverified=true`는
최상위 `collection_gap=true`와 고정 진단 `WEIGHT_FILE_TRANSPORT_INCOMPLETE`로
전파한다. 마지막 `report()`가 실패하거나 해당 boolean 형식이 잘못되면
`WEIGHT_FILE_TRANSPORT_REPORT_UNAVAILABLE`와 안전한 최소 transport metadata를
남긴다. 예외 원문을 저장하지 않는다. 이미 수집한 파일 packet과 개별
`capture_verified`, 승인 reference와의 일치·불일치 관측은 보존한다.

명시적으로 주입된 reader의 report는 기존처럼 소비하지 않으며 EXAMPLE이다.
report가 없는 기존 reader의 fallback도 유지한다. 새 Kubernetes 공통 reader의
parent cleanup 근거를 소비하는 보수이며 다른 transport의 미수집 정리 상태를
확인된 사실로 바꾸지 않는다.

파일 해시와 transport 완결성은 실제 worker/tensor 소비, 공급물 승인 또는
전체 실행 범위를 증명하지 않는다. 기존 security UNKNOWN, EXAMPLE/DECLARED와
`actual_consumption_verified=false` 등을 유지한다. session seal은 선택된 기록
종료의 별도 계약이며 이 수집기가 serving worker/GPU 종료로 채택하지 않는다.
기존 lab/source/image/native0.32 관문·공통 수집·CLI·팩·판정은 변경하지 않는다.
