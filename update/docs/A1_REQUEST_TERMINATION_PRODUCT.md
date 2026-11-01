# LIF10 소유 연결 종료·native 관측 제품 연결 /1

LIF10 원래 SC-18-01과 일반/stream·대기/계산/출력·취소/연결 종료/timeout/stop,
Core/worker/전송/상태/KV·GPU 참조의 종결·기한 요구를 유지한다. 이 묶음은 준비된
한 SSE 소유 client 연결 종료를 기존 CLI의 실행→관측→EVD/RES/RPT→verify→
반출/복원·정확 Ref 재판정에 연결한다. 전체 LIF10·A1 19개·부모 A39개 완료가 아니다.

기존 HTTP 전송과 native prepared session을 재사용한다. 새로운 절차는
`request_termination:kubernetes`, collection profile
`kubernetes-request-termination-observation-v1`이다. 공개 전송은
`KubernetesHTTP(..., profile=TERMINATION_PROFILE)`의
`kubernetes-owned-termination-http-v1`만 사용한다. 기존 다른 프로필의 경로·ID·권한은 유지한다.

```toml
[executors]
request_termination = "kubernetes"
[adapter]
name = "vllm"
[request_termination]
target = "/owned/http-target.json"
prepared = "/owned/prepared/manifest.json"
max_duration_seconds = 120
```

`python -m tools.build_kubernetes_request_termination_pack --seed reference/pack-seeds/PACK-BASELINE-31 --output /owned/new-pack`
은 exact PACK31에서 LIF10 연결만 바꾼 DRAFT 팩을 만든다. 기존 77개, 다른76개 정의 bytes,
원래 질문·정상/위반/UNKNOWN 기준을 보존한다. POL.scope.include에 LIF-10을 선정하고
아래6개 입력을 제공한다. 관측에서 기업 정책값을 생성하지 않는다.

| POL key | 형식과 의미 |
| --- | --- |
| termination_environment_sha256 | seal된 정확 ENV Ref의 정규문서 SHA256 |
| termination_target_sha256 | 전체 대상 canonical JSON SHA256 |
| termination_validity_start/end | timezone 포함 명시 유효창, 0초 초과·최대600초 |
| termination_max_delay_ms | 명시 정수 기한, 0..300000, bool 불허 |
| termination_required_scopes | 중복 없는 명시 list[str], 아래7범위만 허용 |

범위는 asgi_transport/frontend_request/engine_core/scheduler_state/worker_batch/
kv_references/gpu_transfers다. `[]`는 명시적인 요구 scope 없음이고 회수 PASS가 아니다.
누락은 입력 오류다. HTTP의 상대 clock을 native monotonic 시각에서 빼지 않는다.

선정 vLLM0.31.0·정확 ENV/모델 repository/revision/API alias·소유 run·직접 IPv4 Pod와
engine/probe의 namespace/Pod UID/containerID/imageID/restart/start를 기존 경계에서 묶는다.
native8/9 prepared session의 기존 lab/source/image gate를 먼저 적용한다. lab=false는
현재 native preflight에서 HTTP 실행 전에 닫히며 실제 대상을 lab=true로 위장하지 않는다.
native0.32와 임의 producer/외부 capture JSON 입력은 지원하지 않는다.

절차는 POST /v1/completions, prompt=hello, temperature=0, 정확 alias로 고정한다.
JSON before/after는 max_tokens=1·stream=false이고 disconnect는 max_tokens=8·stream=true다.
id는 lif10-before/disconnect/after, X-Request-Id는 같은32lowerhex의
byul-lif10-<token>-before/disconnect/after다. 한 호출 한 요청·각<=30초·전체<=300초,
before 최초·after 최종·중복 불허다. before 정상 JSON 구조가 없으면 disconnect를 생략하고
after를 시도한다. 별도 cancel/admin route·worker signal·Pod/EC2 조작은 없다.

disconnect는 기존 pinned stream probe에 slow-reader-v1/chunk_bytes=1/interval_ms=1/
close_after_reads=1을 전달한다. 첫1byte 읽기 후 해당 client handle만 닫는다.
정확 SSE HTTP200·body bytes/hash·1회 읽기와 정수 순서 시각·client_close 신호를 확인한
경우에만 의도한 불완전 스트림을 일반 수집 누락과 구분한다. client handle 종료는
서버 취소 접수·Core 중단·worker 계산·GPU 전송 완료 증명이 아니다.

후속 seal 실패에도 같은 선정 session 수집을 시도한다. seal은 다른 session/PID나
worker를 종료하지 않는다. 정제 native projection은 같은 recording의 살아 있는
선택 callback과 assign_request_id RETURN, 선정 ASGI disconnect를 유일하게 결합한다.
invocation·POST 경로·내부 request hash를 요구하며 APP_RETURN/RAISE 뒤 늦은 disconnect를
새 중단으로 결합하지 않는다. 물리 process context·boot/time namespace/offsets가 확인된
같은 clock domain에서만 경과를 계산한다. 다른 clock의 elapsed는 null이다.

ASGI 반환, frontend state, Core/scheduler 존재, worker batch행, 요청별 KV group을
별개 관측으로 남긴다. source-correlated active state가 명시 기한 뒤에 남으면
deadline_conflict_candidates에 보존하지만 producer 미인증·image_check=False·전체
회수 범위 미확인 때문에 confirmed_violation=False다. pool/global empty나 공유 weights를
요청별 회수로 바꾸지 않는다. termination_scope_complete와 worker_gpu_release_verified는
false이며 모든 판정 축은 UNKNOWN이다. 신뢰할 독립 근거가 준비되기 전에 PASS/FAIL로 올리지 않는다.

원문 요청/응답/header/token/오류/Pod env/argv는 증거에 저장하지 않는다. HTTP·native·정제
summary의 hash/enum/count만 보존한다. 전후 교체/restart·누락·후속 수집/첨부/정리 실패는
앞선 관측을 지우지 않는다. 실행 ABORTED와 판정 UNKNOWN은 구분한다.

고정1165파일에서 Windows영향521통과·기존skip1/제외1, 같은 bytes Linux영향522통과·제외1,
source/wheel/격리설치176파일 일치와 설치CLI4사례·각 정확6Ref 왕복을 확인했다.
실제 stdlib 소유 loopback3요청을 포함하며 실제 serving/worker/GPU·운영자 정책/신뢰 결합은 미실증이다.
기존 실제 REQ11 wheel04f7856이나 CPU/EXAMPLE 성과를 이 소스의 실제 실행으로 소급하지 않는다.
REQ12의 재시도/늦은 출력, LIF11 그룹 장애, 다른 취소/timeout/stop과 profiler/decoder는 별도
개발·소비·실증이 필요하다. 원래 GPU 승인창 종료·추가Start0·stopped와 CI 결제 차단을 유지한다.
