# PR243 런타임 계약 호환 확인

대상은 공개 main `3bd6a99511b628b6824a7bafd2de0cb7daa614bc`와 PR243 기준 `a96942236dd76f5cb6b024c04c6a9a11daaf20f0`이다. 이 문서는 생산자·소비자 코드 및 직접 시험 결과를 정리한 기술 인계 자료다. 계약 담당자의 별도 승인을 기록한 문서는 아니다.

|변경|생산·소비 경로|기존 자료 → 현행 소비자|신규 플랫폼 계약 문서 → 3bd 고정 strict schema|
|---|---|---|---|
|`runtime-event`의 `engine_health`|runtime `mvp/engine_health_events.py` → 플랫폼 `sources/runtime.py:observation_event` → 사건 목록·상세|기존 source 값 유지, 원본 main 예시 전수 검증 통과|구판 계약의 source enum이 거절|
|관측 `scope_ref`|플랫폼 `sources/identity_scope.py`의 확인된 context/namespace/Pod UID/container 세대 해시 → 웹 `lib/eventScope.ts`|필드 선택사항. 없으면 세대 일치를 새로 만들지 않음|구판 `observation.additionalProperties=false`가 새 필드 거절|
|요청 상세 `query_window`|runtime `mvp/query.py`의 제한 조회 → 플랫폼 `request_detail_doc` → 선택 필드의 OpenAPI/웹 타입|필드 누락·null 허용. 기존 main 상세 문서 유효|구판 루트 strict schema가 새 필드 거절. 잘린 검색의 UUID 부재를 전체 이력의 미수신으로 확정하지 않음|
|상태 하한·native detail|runtime 제한 조회·native observer → 플랫폼 `summary`, `runtime_status`, `_collector_detail` → 실제 Dashboard 조회 범위·`RuntimeObservePage`|하한 필드는 선택사항, 기존 collector detail 분기 보존. 구판 원본 status 유효|새 하한 필드와 `native_receipt` detail을 구판 strict schema가 거절. reader liveness·producer freshness·과거 receipt를 각각 보존|
|lab `L-05b/L-05c`|runtime `mvp/lab_report.py`, opt-in lab 도구 → 플랫폼 `lab_report_doc` → 저장 회차 표시|기존 L-01~L-08/AWS-01~20 값 유지. 구판 원본 lab 보고서 유효|구판 ID pattern이 거절. unknown/failed/limitation을 그대로 전달하며 현재 통제 PASS로 승격하지 않음|

현재 schema ID는 `/0.1`, 문서 `schema_version`은 종전 값을 유지한다. 새 플랫폼이 생성한 계약 문서는 위의 새 enum·필드·ID를 포함할 때 3bd 고정 strict schema 검증을 통과하지 않는다. 구판 원본 예시는 현행 스키마가 수용한다. 같은 버전 문자열만으로 양방향 strict schema 호환을 가정할 수 없다. 이 시험의 입력은 플랫폼 계약 문서이며, 새 raw Gateway 응답을 구판 플랫폼 코드가 어떻게 변환하는지까지 실행한 시험은 아니다.

배포는 같은 릴리스 묶음으로 준비한다. 새 계약을 검증할 소비자 스키마·코드를 먼저 준비하고, Gateway 새 버전을 준비한 뒤, 같은 릴리스의 API·Web을 가동하고 트래픽을 전환한다. 준비와 설치 순서를 서비스 가동·트래픽 전환 순서와 구분한다. 혼합 버전의 전체 호환성이 직접 검증되지 않은 조합은 완전 호환으로 표시하지 않는다. 구 Gateway와 새 플랫폼의 legacy fallback이 동작하는 경우에도 전체 이력 읽기에 따른 지연 위험이 있으므로, 실제 운영 전 해당 조합의 조회 비용을 확인한다.

고정 main의 스키마·원본 예시는 `platform/api/tests/fixtures/contracts-main-3bd6a995/`에 원문 그대로 보관했다. `provenance.json`의 commit·경로·SHA를 검사한다. `test_contract_revision_compatibility.py`는 구판 자료의 현행 수용, 신규 플랫폼 계약 문서의 3bd strict schema 거절, 선택 필드 누락, unknown 보존을 직접 검사한다. 기존 `test_engine_health_events.py`, `test_runtime_query_window.py`, `test_runtime_conversion.py`가 실제 변환·검색 완전성·native detail·선택 lab ID를 검사한다. 이번 수정은 스키마와 수락 범위를 변경하지 않는다.

실제 `/runtime/overview`와 `/dashboard`는 `DashboardPage.tsx`를 사용한다. `pages/runtime/RuntimeOverviewPage.tsx`는 라우터·제품 import가 없고 기존 단독 시험에서만 렌더링된다. 이번에는 실제 Dashboard에 조회된 기록의 기간·환경·대상, 요청 부분 이력의 하한, 원천별 사건 조회 상한을 연결했다. 카드·비율은 조회된 행의 집계다. GPU 패널·로그인·네비게이션·필터 동작은 기존 경로를 유지한다. `RuntimeCheckScope`의 선정37 화면은 현재 조회 사건·연결 증거와 지원 조건을 표시하며 실시간 보안 PASS/FAIL 판정으로 전환하지 않는다.

검증: 웹 전체 215개 통과·TypeScript 검사·production build 통과, API·호환 계약 관련 92개 통과, 계약13개/예시11종 오류0. 실제 브라우저 시각 확인과 AWS 실행은 이번 확인 범위에 포함하지 않았다.
