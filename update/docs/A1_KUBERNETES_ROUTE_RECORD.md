# REQ02 소유 기록 파일 제품 연결 /1

`KubernetesRouteRecordExecutor`, registry `route_record:kubernetes`,
collection profile `kubernetes-owned-route-record-v1`는 기존 CLI의
select→run→judge→report→verify→evidence export/restore를 사용한다.
별도 CPU 제품이나 외부 관측 JSON import를 추가하지 않는다.

`python -m tools.build_kubernetes_route_pack --seed packs/PACK-BASELINE --output /owned/new-pack`
으로 PACK31 exact Ref에서 DRAFT PACK-KUBERNETES-ROUTE-RECORD@1을 생성한다.
77개 질문·판정 기준과 나머지76개 정의 bytes를 보존한다. POL.scope.include에 REQ-02를
선택하고 다음 여섯 값을 명시한다.

- route_environment_sha256: seal된 ENV Ref 문서 해시
- route_target_sha256: 전체 target canonical JSON SHA256
- route_session: 사전 선정 소유32hex session
- route_file_sha256: 수집 전 검토해 제공한 파일 bytes SHA256
- route_validity_start/end: timezone 포함 유효창

`[executors] route_record="kubernetes"`, `[route_record] target="/owned/target.json"`
을 기존 guard.toml에 설정한다. 선택값은 실행에서 읽은 hash로 자동 승인하지 않는다.
target은 기존 guard-kubernetes-request-binding-target/1 exact ENV/model mapping/engine+probe
선언을 재사용한다. 실제 reader는 engine namespace/Pod/container/image/restart/start 전후를
검증한다. probe HTTP·정상/위반 인가 요청은 보내지 않는다. native reader의 lab=true
gate는 유지하며 이 profile은 native나 source-qualified worker를 증명하지 않는다.

경로는 `/guard-route/data/<session>/route-observations.json` 하나다.
기존 고정 stdlib nofollow/fstat reader·5초 exec watchdog·전체 최대300초·파일65536bytes
상한을 재사용한다. 원문 stdout/stderr/예외·임의 경로·Pod env/argv는 저장하지 않는다.
기존 strict guard-route-native-observations/1 schema로만 검증하고 식별자·경로·작업 이름은
SHA256로 투영한다. 요청/주체 hash와 method/decision/outcome enum만 유지한다.
정규 record 해시와 파일 bytes 해시는 별개다.

성공은 기록 수집의 성공이다. 생산자 인증·독립 probe ledger·기업 허용표·최종 handler의
인과 연결이 없으므로 record의 allow/deny/entered 주장으로 PASS 또는 신뢰된 FAIL을
만들지 않는다. 기존 rule_table의 빈 규칙으로 모든 축 UNKNOWN을 유지한다.
출처는 EXAMPLE/DECLARED 상한이며 실제 ENV나 lab=false만으로 MEASURED로 올리지 않는다.

누락·malformed·pin/유효창 불일치·전후 세대 교체·불완전 pipe/file은 ABORTED와 고정 gap으로
남긴다. 후속 첨부 오류와 parent cleanup 미확인은 별도 기록한다. 성공한 metadata를
정리 실패 때문에 지우지 않는다. native0.32 지원 및 기존 lab/source/image gate는 그대로다.

이 단계는 REQ02 전체 완료가 아니다. 실제 Gateway/생산자·정상/거부 요청 ledger·
principal/operation/rewrite POL과 serving handler 귀속을 확보하고 별도 실증해야 한다.
GPU stopped 및 추가 Start 중단을 유지한다. CPU/EXAMPLE 회귀·설치본과 실제 실증을 구분한다.
