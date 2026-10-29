# REQ12 외부 ID 재사용·native 선택 관측 /1

REQ12의 SC-18-01/SC-18-03 두 분기, 취소 의미·늦은 결과·자동 retry·결과 소비자/반환 필드
요구를 보존한다. 이 묶음은 기존 request_termination:kubernetes 실행기를 명시 REQ12
collection profile로 확장한다. 별도 CPU 제품이나 외부 capture JSON 실행기를 만들지 않는다.
현재 실제 늦은 chunk 폐기/전달·자동 Gateway/SDK retry는 미확인이며 전체 REQ12 완료가 아니다.
명시 native10의 추가 ASGI invocation 관측은 A1_NATIVE_ATTEMPT_PROFILE.md에 정의하며,
derived 팩 revision2/check revision6의 별도 추가 관측이다. 과거 revision1과 정확 Ref는 보존한다.

공개 HTTP는 REQUEST_ATTEMPTS_PROFILE = kubernetes-owned-request-attempts-http-v1이다.
명시 profile 외 기존 전송의 요청·ID·제한은 유지한다. native8/9 prepared session·정확 ENV·모델
repository/revision/API alias·소유 run과 engine/probe의 전후 namespace/Pod UID/containerID/
image/restart/start, 제한된 시간·출력과 소유 probe 정리를 재사용한다. native lab/source/image
gate를 먼저 확인하며 실제 lab=false를 true로 위장하지 않는다. native0.32는 미지원이다.

```toml
[executors]
request_termination = "kubernetes"
[adapter]
name = "vllm"
[request_termination]
target = "/owned/target.json"
prepared = "/owned/prepared/manifest.json"
max_duration_seconds = 120
```

명시 kubernetes-request-attempt-observation-v1과 REQ-12 조합에만 구현0.2가 dispatch된다.
기존 LIF-10 구현0.1과 판정은 유지한다. derive 명령은
`python -m tools.build_kubernetes_request_attempts_pack --seed packs/PACK-BASELINE --output /owned/new-pack`다.
PACK31 exact Ref와77개·다른76개 정의 bytes·원래 질문/정상/위반/UNKNOWN·두 시나리오를 보존한다.
새 팩은 DRAFT이고 모든 판정 축은 UNKNOWN이다.

필수4 POL: attempt_environment_sha256은 seal된 ENV Ref 문서 SHA,
attempt_target_sha256은 전체 canonical target SHA다. attempt_validity_start/end는 timezone
포함 명시 유효창이며 0초 초과·최대600초다. 관측 hash를 실행 중 승인값으로 채우지 않는다.
기업의 취소 확정·결과 독자/필드·늦은 출력/자동 retry 정책과 실제 신뢰 입력은 별도 필요하다.
이 네 결합 입력이나 HTTP 성공이 그 기업 정책의 승인·보안 판정을 대신하지 않는다.

선정 vLLM0.31.0의 POST /v1/completions·prompt=hello·temperature=0·model=정확 alias만 보낸다.
한 호출 한 요청·각<=30초·전체<=300초다. 순서는 다음과 같다.

1. normal-first/normal-repeat: JSON·max_tokens=1·stream=false, 같은 normal 외부 ID.
2. disconnect: SSE·max_tokens=8·stream=true, 첫1byte 후 소유 client handle만 닫는다.
3. replacement: JSON·max_tokens=1·stream=false, disconnect와 같은 shared 외부 ID의 새 요청.
4. after: JSON·max_tokens=1·stream=false의 별도 후속 정상 요청.

요청 id는 req12-<case>, X-Request-Id는 같은32lowerhex의 byul-req12-<token>-normal/shared/after다.
body.session_id는 byul-req12-<token>-<case>다. 이는 요청에 명시한 서로 다른 **보조 session 표식**이다.
native request/attempt ID나 신원/인가를 대신하지 않으며 prefix·호출 순서로 native ID를 생성·인증하지 않는다.
정상 두 요청의 구조가 미확인이면 challenge 두 요청을 생략한다. client close가 미확인이면
replacement를 생략하고 after를 시도한다. 별도 cancel/admin/worker signal/Pod 조작은 없다.

실제 native assign_request_id RETURN과 살아 있는 selection callback에서 선정 보조 session SHA,
ASGI request/path/POST, 실제 선택 native ID·invocation·PID/start를 유일하게 결합한다.
session 표식만 보고 내부 attempt가 증명됐다고 하지 않는다. 중복·누락은 유일결합 미확인이며
같은 process의 서로 다른 표식에서 native/invocation ID가 겹치면 선언 기반 후보로만 보존한다.
producer 미인증·image_check=False 때문에 confirmed_violation=false를 유지한다.

기존 ASGI BODY_SEND_RETURN/DISCONNECT_RECEIVED/APP_RETURN/RAISE에는 invocation/native attempt
필드가 없다. 동일 외부 ID의 ASGI 이벤트는 attempt_case_id=null로 보존한다. 기록 순서·timestamp·
같은 request hash로 소비자를 정하지 않는다. 같은 hash의 응답이나 정상 JSON도 늦은 A 출력이 B에
전달됐거나 폐기됐다는 증명이 아니다. 새 공통 관측 계약에서 실제 전송 invocation과 native 출력의
귀속을 결합하고 검토된 source/code·parser/소비를 확정해야 이 공백을 닫을 수 있다.

native_attempt_binding_observed는 관측 관계이고 late_delivery_verified/automatic_retry_verified/
request_attempt_scope_complete는 false다. worker state·Core abort와 출력 폐기/전달은 별개이며
LIF10/11 자원 회수 요구도 유지한다. native event 투영은4096개 상한이며 도달 시 미완결로 남긴다.
원문 요청/응답/헤더/토큰/오류/Pod env/argv는 증거에 저장하지 않는다. 정제 hash·enum·bool만 남긴다.
seal 실패에도 같은 session 수집을 시도하며 후속 수집/첨부/정리 실패가 이전 관측을 지우지 않는다.

고정1177파일에서 Windows영향550통과·기존skip1/제외1, 같은 bytes Linux영향551통과·제외1을
확인했다. 인접 LIF10/HTTP/native/registry·불변식과 실제 stdlib 소유loopback5요청을 포함한다.
source/wheel/격리설치178파일 일치와 설치CLI4사례·각 정확6Ref 반출/복원·재판정도 통과했다.
기존 실제 REQ11·CPU/EXAMPLE·설치 성과를 새 source의 실제 실증으로 소급하지 않는다.
원래 GPU 승인창 종료·추가Start0·마지막 확인 stopped·CI 결제 차단과 부모A39/A1담당19 범위를 유지한다.
