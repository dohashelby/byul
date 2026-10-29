# REQ13 공개 전송 /1

공개 상수 `guard.runtime.kubernetes_http.VALIDATION_ERROR_PROFILE` =
`kubernetes-owned-validation-error-http-v1`을 추가했다.
`KubernetesHTTP(target, env, duration=..., binary="kubectl", profile=VALIDATION_ERROR_PROFILE)`,
`transport("kubernetes-probe", [request], timeout=...)`, `report()`의 기존 ABI를 사용한다.
별도 private 호출이나 REQ11 ID를 사용하지 않는다. B PR97 확정
875ede64a8c20f8b9fe0f1282fbf18fa028fff3f의 수집 요청과 맞춘다.

정확한 guard-kubernetes-request-binding-target/1 ENV Ref·모델 repository/revision→API alias,
직접 IPv4 HTTP Pod endpoint, 소유 run과 engine/probe namespace UID·Pod UID·containerID·
imageID·restartCount·startedAt의 전후 확인을 재사용한다. env.engine은 vllm/0.31.0이어야 한다.
이는 exact image 선언/API 보고 값과 수집 코드 pin의 검증이다. 실제 engine source/code·
runtime image ancestry·native handler 소비·GPU·정식 제품 전체 REQ13 검증은 별도다.
native0.32 gate, 기존 두 HTTP 프로필 및 lifecycle lab/source/image 제한은 유지한다.

한 호출에 아래 요청 하나만 허용한다. 모두 POST·mode=json·want_body=true(또는 생략)이며
body model은 public api_model과 정확히 같아야 한다. 임의 헤더/본문/시간 지연 필드는 금지한다.

| id | path | 고정 body의 model 외 필드 |
| --- | --- | --- |
| req13-error-before | /tokenize | prompt=hello, add_special_tokens=false, return_token_strs=false |
| req13-error-primary | /v1/chat/completions | messages=[{role:user,content:hello}], max_tokens=b2error-<token 앞12hex>, stream=false |
| req13-error-after | /tokenize | before와 동일 |

헤더는 X-Request-Id 하나이며 `byul-req13-error-<32lowerhex>-before/primary/after`다.
같은 transport의 token은 동일해야 한다. before가 첫 요청이며 각 case는 한 번만 허용한다.
primary는 before 뒤, after는 before 뒤(primary 생략 가능)에만 허용하고 after 뒤는 닫힌다.
A가 HTTP200을 정상 tokenizer 관측으로 판정하지 않는다. B는 strict tokens/count/max_model_len
정상 대조가 실패하면 primary를 보내지 않고 after만 시도한다.

전체 duration은 기존 0<duration<=300초, 각 timeout은 기존 0<timeout<=120초다.
고정 stream_probe bytes와 자기 exec watchdog·parent cleanup 검사를 재사용한다.
JSON response는 기존128KiB, 전체 probe packet2MiB, stdin128KiB 상한이다. 새 profile은 SSE,
임의 path·prompt·header·숫자 max_tokens·다른 엔진 판본을 열지 않는다. Pod/config/PID를
만들거나 지우지 않으며 소유 exec probe만 종료한다.

원문 body/response/marker는 transient 반환값에만 있다. report는 고정 case ID·outcome·
capture_complete·binding_verified_before_after와 binding hash·collection_gap·cleanup_errors를
반환하며 원문·헤더·예외를 저장하지 않는다. B가 기존 정제 helper에 실제 POL을 전달하고
field path/violation hash·enum/count/coverage gap만 증거로 저장한다. 원시 응답 bytes SHA와
decoded-text capture SHA의 의미를 구분한다. A는 B 전용 정책·판정·snapshot을 변경하지 않는다.

직접/인접 회귀와 설치 검증 뒤 고정 commit/이 파일 및 kubernetes_http.py의 bytes hash를
A1 인계에 제공한다. B는 자신의 literal source pin과 소비 검증을 갱신해야 한다. 실행 시 읽은
hash를 승인값으로 사용하지 않는다. 이 전송 완료가 Online 계측이나 실제 Kubernetes 인수 완료는 아니다.
실제0.31 ENV/POL·새 engine/probe 세대·정상대조·복구/정리 소유자와 승인된 실행 조건이 필요하다.
GPU stopped·추가Start중단을 유지한다.
