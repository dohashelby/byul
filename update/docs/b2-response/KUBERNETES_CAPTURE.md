# REQ13 실제 오류응답 필드 하위분기 collector

`tools.b2_response_kubernetes_capture.capture_validation_error(env,target,*,duration=60,
timeout=10,binary='kubectl',allowed_error_fields=None,forbidden_contexts=None)`는 B 내부 API다.
원 full ResponseContentExecutor·공통 팩·registry·판정·A HTTP 코드를 교체하지 않는다.
기존 0.31 CPU handler와 PR73 EXAMPLE 증거는 재사용하고 재실행하지 않는다.

A에 제안하는 전용 profile은 `kubernetes-owned-validation-error-http-v1`이다.
현재 `A_HTTP_SOURCE_SHA256=None`이며 미지원 상태다. 승인된 A 코드 SHA·함수 origin과
`VALIDATION_ERROR_PROFILE` 공개 상수가 모두 고정되기 전에는 constructor·네트워크 요청을
시작하지 않는다. sampling·REQ11 profile·private _call·REQ11 ID는 사용하지 않는다.

공개 ABI는 기존 `KubernetesHTTP(target,env,duration=...,binary=...,profile=...)`,
`transport('kubernetes-probe',[request],timeout=...)`, `report()`를 제안한다.
A가 ENV/target·model mapping·선정 namespace/Pod/container/image/restart/start의 전후 동일성을
검증하고 공개 api_model/target_sha256/endpoint/binding을 제공해야 한다. report는 profile,
binding_sha256, requests의 id/outcome/binding_verified_before_after, collection_gap과
cleanup_errors를 제공해야 한다. 새 공용 계약 확정은 A 소유다.

요청은 폐쇄 case ID 세 개다.

|case ID|경로|내용|
|---|---|---|
|req13-error-before|POST /tokenize|검증된 API alias·hello·add_special_tokens=false·return_token_strs=false|
|req13-error-primary|POST /v1/chat/completions|같은 alias·짧은 user 메시지·짧은 synthetic 비정수 max_tokens·stream=false|
|req13-error-after|POST /tokenize|before와 같은 정상 대조|

각 요청 X-Request-Id는 `byul-req13-error-<uuid>-before/primary/after`다. body의 model은
ENV repository ID로 채우지 않고 검증된 public transport.api_model만 사용한다.
/tokenize는 기존 _token_ids와 strict JSON parser로 실제 tokens/count/max_model_len shape를
확인한다. 이는 CPU tokenizer 경로의 전후 관측이며 모델 추론·GPU·서비스 전체 복구를
증명하지 않는다. before가 정상 shape가 아니면 primary를 보내지 않고 after만 대조한다.

실제 raw body/response/marker/예외 문장은 메모리에서만 처리한다. 기존
observe_error_response에 실제 POL의 allowed_error_fields를 전달하고, forbidden_contexts가
없으면 None을 유지한다. 임의 private-forbid 정책을 추가하지 않는다. helper의 field-path
해시·위반 해시·count/context enum·coverage gap만 반환한다. 기대 status는 기존0.31
validation handler의400이며, 실제400..599 오류의 독립 필드 위반은 status 불일치·after/
cleanup 실패와 별도로 보존한다. 2xx/3xx는 오류 필드 정책으로 투영하지 않는다.
field hash와 독립 violation이 모두 없으면 error_observation은 null이다.
HTTP400은 실제 오류응답 관측이며 native handler 소비·routing은 미검증 false다.

반환 record는 schema/profile/collection_id, 실제 UTC capture 시작·종료, exact ENV Ref,
target SHA·API alias UTF8 SHA, data_origin, attempted_requests, normal_controls.before/after,
error_observation|null, 고정 gaps, cleanup, capture_status를 포함한다. attempted_requests는
공개 transport 호출 시도 횟수이며 HTTP 패킷 전송 확정 횟수가 아니다. 미지원 A에서는0이다.
정책 UNSET gap만 남은 경우에도 실제 수집 실행은 completed일 수 있으며 전체 REQ13
coverage나 보안 PASS를 의미하지 않는다. producer가 기존 전체 범위 미수집을 보존한다.

EXAMPLE transport 주입은 sealed ENV.EXAMPLE에서만 허용하고 반환 origin도 EXAMPLE이다.
실제 경로는 외부 관측 JSON을 입력받지 않는다. 제품 helper의 승인 파일 bytes/origin과
A source/function gate를 전후 검사하고, 각 응답의 상관 ID·선정 세대 report를 확인한다.
이미 검증된 primary 뒤 after/cleanup 오류가 나면 그 관측을 유지하고 incomplete로 반환한다.
collector는 파일·Pod·설정·다른 PID를 만들거나 지우지 않으며 public transport의 소유 probe
정리 보고만 읽는다. 실제 A profile·대상·정책 미확정 상태에서 K8 실행 완료를 주장하지 않는다.

선정 source_profile은 `vllm-031-validation-error`이며 ENV.engine.version의 정확한
`0.31.0`만 지원한다. 다른 판본은 UNSUPPORTED_ENGINE_VERSION과 attempted0으로 남긴다.
REQ11 transport의 다른 이미지 지원을 이번 오류응답 하위분기에 이월하지 않는다.

`allowed_error_fields_sha256`는 실제 전달된 명시 정책 목록의 canonical_json SHA256이며
unset은 null이다. producer가 현재 선정 POL의 동일 값과 비교해야 한다. raw 정책 경로,
marker·body는 artifact에 다시 저장하지 않는다. `request_summaries`는 폐쇄 세 case의
method/path, request_id_sha256, body_sha256, attempted, binding_verified, started_at,
finished_at, raw_response_bytes_sha256만 포함한다. 실제 API 호출 전에는 attempted=false와
시각 null이며 전체 capture UTC 창은 capability 확인도 포함한다.

A의 `body_sha256`는 A 원시 응답 바이트 메타데이터로 request_summaries의
raw_response_bytes_sha256 및 normal_controls.response_sha256에 보존한다.
error_observation.capture_sha256는 기존 helper가 decoded UTF-8 문자열을 재인코딩한 해시다.
두 해시의 동일성을 가정하거나 같은 wire hash로 이름 붙이지 않는다.

고정 코드 직접 회귀는 기존 error contract와 합쳐40 passed / 0.32초다. EXAMPLE의
전후 tokenize·형식오류, alias 분리, 잘못된 response ID/세대 차단, 정책 unset,
후속·cleanup 실패의 독립 위반 보존, 허용되지 않은 origin 주입 거부, source postcheck,
0.32 미지원, 정책·요청해시 바인딩 및 raw/decoded hash 분리를 확인했다.
A profile 미지원·literal None 경로는 실제 transport를 만들기 전에 닫힌다.
기존 실제 CPU handler/PR73 경로는 재실행하지 않았으며 실제 K8 요청은 아직 수행하지 않았다.
