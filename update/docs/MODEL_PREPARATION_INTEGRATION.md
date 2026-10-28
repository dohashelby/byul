# A-1 모델 준비 공통 연결

A-2 c7bf2973의 확정7파일을 그대로 소비한다. 그 원형 문서 MODEL_PREPARATION.md의 helper 준비 상태와 이 공통 연결 상태를 구분한다. A-1의 Kubernetes REQ11 e4fe2f2가 공통 기준이며 실제 Kubernetes/GPU 실증은 없다. A-2 전용 파일·B 전용 실행기는 직접 수정하지 않는다.

## 요청 모델명과 비교

공통 HTTP 경계는 target.model_mapping에 A-2의 기존 guard.model-request-mapping/1 전체 매핑을 선택적으로 받는다. ENV Ref·repository_id·revision·inventory_sha256가 정확한 ENV와 같아야 한다. served_model_name은 요청 템플릿의 {model} 및 해당 정상 응답의 expected_model에 함께 전달한다. 명시 매핑의 누락·null·불일치는 readiness/probe I/O 전에 고정 MODEL_MAPPING_INVALID로 거부한다. 매핑 미입력은 과거 ENV.model.id 동작을 유지한다.

공통 functional_requests의 private dict 소비 객체가 검증된 응답 모델명을 functional_results까지 전달한다. 기존 실행기의 함수 호출·CheckDef와 A-2 전용 소스를 고치거나 복제하지 않는다. probe_templates 및 REQ11의 기존 요청 생성도 같은 별칭을 쓴다. B 전용 sampling 등의 개별 본문 생성까지 이식한 것은 아니다.

REQ11 request_binding:kubernetes는 동일 전체 매핑을 소비하고 guard.model-request-mapping/1의 검증기를 재사용한다. 기존 최소3필드 매핑은 이미 공개된 v1 transport 호환 입력으로 보존하며 새 모델 준비에는 A-2 전체 매핑을 사용한다. 저장소 ID를 API 이름으로 덮어쓰거나 API 표시명을 실제 tensor/worker 소비 증명으로 사용하지 않는다.

전체 매핑의 정규 hash는 provenance에 남긴다. 비교 조건에는 schema·repository_id·revision·inventory_sha256·served_model_name·container_mount의 정규 projection hash만 넣는다. ENV Ref·호스트 절대 위치는 대상 결합이며 비교 조건에서 제외한다. 양쪽 환경 Ref가 달라도 같은 조건을 비교할 수 있고 별칭/모델/논리 mount가 다르면 기존 비교 gate가 차이를 본다. 매핑이 없으면 과거 execution-v1 자료 구조와 hash는 유지한다. 원문 매핑·호스트 경로·요청/응답·토큰은 첨부/config에 복사하지 않는다.

## 기존 Kubernetes 조회와 모델 mount 선언

REQ11 전송의 정확 Namespace/Pod query 결과에서 최초/최종 검증된 Ready snapshot을 메모리로만 A-2 analyze_model_mount에 전달한다. kind/name·완전한 bounded transport·ENV·namespace/Pod UID·containerID·imageID·restart·시작 시각·소유 run을 확인한다. 기존 lifecycle target 형태로 선택 신원을 투영해 기존 검증기를 사용하며 새 원격 조회기를 복제하지 않는다.

결과는 기존 probe_log.jsonl 첨부 안의 summary.model_mount에 저장한다. hostPath/Directory·mountPath 대응·readOnly 선언과 owner hash만 남긴다. readOnly=false도 선언이며 새 FAIL/PASS 규칙은 없다. 후속 gap·교체·불완전 조회는 해당 mount 구간을 미수집으로 남기고 기존 독립 REQ11 FAIL·성공 관측·첨부를 보존한다. whole_run_coverage_verified와 worker_access_verified는 false다. A-2 원형 interface_status 값은 보존하고 실제 소비 연결과 검증 HEAD는 A-1 인계 자료에 기록한다.

기존 lifecycle target은 lab=true와 정확5필드 계약을 유지한다. 따라서 REQ11 lab=false의 mount 보조 관측은 현재 미수집이다. 실제 실행을 위해 이를 임의로 lab=true로 바꾸지 않는다. 기존 REQ11 요청/세대 검증과 mount 보조 관측의 지원 경계를 구분한다.

## 기존 수집 CLI의 선택 입력

capture-kubernetes-lifecycle-session에 --model-mapping FILE을 선택 입력으로 추가한다. target 형식·lab=true 제한·고정 읽기 helper·기존 실행 승인 경계는 그대로다. 매핑은 별도 파일에서 제한 크기로 읽고 기본 dry-run에서도 ENV 결합을 검증한다. --execute 시 기존 reader.snapshot이 kind/name/transport를 확인한 전후 Pod/Namespace를 동일 A-2 분석기에 전달하고 기존 세션 JSON에 model_mount 투영을 붙인다. 원문 snapshot은 저장하지 않는다.

collector의 매핑 미입력은 이전 조회/출력 경로를 유지한다. 후속 mount 조회 실패는 먼저 수집한 세션의 recordings·성공 source metadata를 지우지 않는다. 출력 저장 실패 시 기존 CLI의 안전한 capture 반환과 기존 파일 보존 경계를 재사용한다. 세션 JSON 저장 자체와 REQ11 EVD 첨부 저장은 별도이며 둘 다 실제 engine 소비나 보안 PASS를 뜻하지 않는다.

## 검증과 실제 재개

소유 runner 대역으로 요청/응답 별칭, 잘못된 ENV·repo·revision·inventory, 기존 미입력, readOnly 양쪽 선언, mount 변화·잘못된 kind·Pod restart·후속 pipe/수집 실패, 기존 EVD/RES/RPT와 성공 첨부·독립 FAIL·정확 Ref 반출/복원을 검증한다. A-2 전용 mapping/snapshot/mount 및 기존 HTTP·REQ11·Kubernetes·비교·불변식 회귀를 같은 고정 소스에서 확인한다.

실제 Kubernetes API는 연결 거부 상태였으며 daemon/cluster/기존 컨테이너를 변경하지 않는다. AWS/GPU/이미지 pull/모델 다운로드는 실행하지 않는다. GPU 재개에는 quota/비용/자원·실행 승인, 검토된 GPU image/source/consumer pin, 실제 배포 세대와 모델 inventory/API 별칭, worker 소비와 종료/자원 반납 관측이 필요하다. CPU image 제한은 유지한다. 기업 Gateway/호출자 API·모델 허용표는 준비 전이며 대신 정하지 않는다.
