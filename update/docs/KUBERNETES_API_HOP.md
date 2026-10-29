# 한 구간의 Kubernetes API 전송 관측 입력

A2의 기존 `api_transport` 소비를 위한 공통 경계다. 공개 호출은
`KubernetesHTTP.api_hop_snapshot(policy_values, timeout)`이며 기존
`REQUEST_BINDING_PROFILE`의 정확 ENV/target·API 모델 별칭·context/run·양쪽
엔진/프로브 세대와 `normal_requests`의 고정 수집기를 재사용한다. 새 전송 코드나
별도 CPU 제품을 만들지 않는다. 이 메서드는 판정하지 않고 body 없는 관측만 반환한다.

공통 profile은 `explicit-owned-kubernetes-api-plaintext-v1`, 입력의 `api_hop_scope`는
`guard_probe_to_owned_kubernetes_direct_api`다. 다음9개 필드를 정확히 제공해야 한다.

| 필드 | 형식/결합 |
| --- | --- |
| api_hop_scope | 위 고정 구간 이름 |
| api_environment_sha256 | 정확 ENV Ref의 문서 sha256 |
| api_target_sha256 | 현재 전체 target의 정규 JSON sha256 |
| api_binding_sha256 | 기존 context/run·엔진/프로브 전체 binding의 정규 JSON sha256 |
| api_ip / api_port | 현재 선택 Pod IPv4와 명시 포트; int 포트에서 bool 제외 |
| plaintext_forbidden | 명시 bool; 누락·null·관측에서 보충 금지 |
| validity_start / validity_end | 시간대가 있는 명시 유효창; 시작보다 종료가 늦어야 함 |

이 binding hash는 Docker network/endpoint ID가 아니다. 기존 Docker POL의
`api_container_id`, `api_network_id`, `api_endpoint_id`를 복사하거나 namespace UID를
이 이름으로 재명명하지 않는다. 새 profile과 기존 Docker profile은 서로 거부한다.
기업의 금지 여부·기본값·예외·유효창을 자동 생성하지 않는다.

정책 구문·세 hash·IP/포트·호출 profile·현재 유효창을 원격 I/O 전에 확인한다.
그 뒤 한 건의 기존 `POST /v1/completions`·모델 별칭·ping/max_tokens1 정상 대조만
실행한다. 전후 세대·완전 EOF/UTF8/비절단/길이/hash와 기존 기능 응답 구조를 검증한다.
API 응답 모델명은 구조 대조이며 실제 worker·가중치 소비를 증명하지 않는다.

출력은 `guard-kubernetes-api-hop-capture/1`이다. `available`은 정책 결합·전후 세대와
관측자 UTC의 시작/종료가 같은 유효창 안에 있음을 뜻한다. 정상 성공이나 보안 PASS가
아니다. `normal_response_schema_verified`, 초기 HTTP 결과·완전성, 정리 여부와 구분한다.
유효창이 후속 만료되면 초기 응답 사실은 남지만 available/window flag는 false다.
세대가 그 구간에서 교체되면 그 응답은 안정된 구간으로 채택하지 않는다. 앞선 별도
호출의 packet은 수정하지 않는다. 부모 정리 공백은 별도 cleanup flag/고정 코드로 남긴다.
관측자 UTC는 원격 source clock의 독립 인증이 아니다.

body·preview·응답 header·Pod/spec·환경/argv·임의 예외는 출력하지 않는다. body는 기존
구조 검증에만 잠시 사용한다. 수집/정상대조/transport/실제 probe source hash를 구분하며,
파일 hash는 생산자의 인증이나 host-runtime의 독립 증명이 아니다. 주입 runner/lab/예제
ENV는 EXAMPLE이고 저장 JSON/dict를 재입력해 verified 측정으로 올리는 경로가 없다.

현재 PACK31·PLT08 revision5의 Docker 필수 POL과 판정·과거 Ref는 유지한다. 새 정책의
형식은 공통 API 입력 계약이며 현재 팩에 정식 등록된 POL schema가 아니다. 기존 select는
Docker 필수값이 없는 새 입력에 NO_POLICY_VALUE를 남긴다. A1의 versioned CheckDef/정책
통합과 A2의 확정 전용 소비·registry·CLI 왕복을 함께 마감하기 전에는 정식 PLT08로
실행됐다고 표시하지 않는다. 기존 필수값을 dummy로 채우거나 관문을 우회하지 않는다.

A2는 같은 live 호출 안에서 명시 금지·정확 유효창·세대·고정 HTTP source·완전 정상
응답이 모두 맞는 한 구간만 기존 독립 FAIL 의미로 소비해야 한다. 평문 허용·거부·누락은
전체 PASS가 아니다. 암호학적 peer/TLS/CA·실제 배포 caller/Gateway/Service·IPC/TP·
다중 GPU의 두 필수 결과와 전체 보호 범위는 각각 미완료로 남긴다.
