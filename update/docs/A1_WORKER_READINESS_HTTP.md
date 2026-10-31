# Worker readiness HTTP 공통 입력

상태: 구현·시험·문서 초안을 마쳤으며 고정 소스의 검증은 대기 중이다. 검증 완료 인계의 정확 commit/파일 bytes와 지원 범위를 확인한 뒤 소비한다. 실제 Kubernetes/worker/GPU와 B 전용 제품 연결은 미검증이다. 기존 A1 LIF09에는 이미 Running/Unready engine·Ready probe를 묶은 completion 관측이 있다. 이를 새로 구현하거나 B에게 다른 점검의 profile/RID/private _verify(False)를 쓰게 하지 않는다.

기존 선정 startup Pod 결합을 `kubernetes_startup_binding.read_startup_binding` 경계로 추출하고 LIF09의 old before/after completion 절차·판정·probe 원 bytes를 유지한다. B LIF07 공개 경계는 `guard.runtime.kubernetes_worker_readiness.KubernetesWorkerReadinessHTTP(target, env, *, runner=None, duration=120, binary='kubectl', clock=None)`, `.sample(policy_values, *, sample_id)`, `.report()`다. profile은 `kubernetes-owned-worker-readiness-http-v1`, envelope schema는 `guard-kubernetes-worker-readiness-http/1`이다. 객체당 한 sample이며 자유 요청·재시도·다른 점검 ID는 허용하지 않는다. 미실행 report는 null, 실행 이후는 정제 결과의 독립 복사다.

검토한 닫힌 wire 범위는 같은 sample의 GET /health 전후와 API alias의 hello 한 개·max_tokens1·temperature0·비stream chat 한 번이다. 정확 ENV/target/model mapping·cohort/session/유효창, namespace/Pod/container/image/restart/start/run을 묶고 engine Running/Ready 또는 Unready와 probe Running/Ready를 각각 확인한다. Pending·생성 전·교체/restart는 지원하지 않는다. 기존 generic HTTP의 Ready gate와 source/image/lab·native0.32 미지원은 유지한다.

저장은 status·전송/응답 완전성·크기·strict model shape·request-ID SHA·독립 API Pod readiness와 닫힌 진단만 한다. 원문 요청/응답·본문 hash·비밀·Pod env/argv·임의 예외를 저장하지 않는다. 유효한 probe 문서는 parent returncode/시간초과/출력/후속 API·정리 오류보다 먼저 정제해 보존한다. 미수집 outcome/응답/형상은 null이고 실제 HTTP503·refused·timeout 및 모호한 API Ready와 구분한다. `sampling_complete`는 세 번의 선정 관측·결합·전송이 완전하다는 의미이며 HTTP 정상·서비스 준비 판정이 아니다. canary의 strict JSON/alias/단일 message content 형상 실패도 관측 자체가 완전할 수 있다. 모호한 metadata·부분 수집·후속 오류는 collection_gap으로 남긴다.

wire 순서는 GET `/health`, POST `/v1/chat/completions`, GET `/health`다. chat body는 정확 alias, `messages=[{role:user,content:hello}]`, `max_tokens=1`, `temperature=0`, `stream=false`다. RID는 `byul-lif07-http-{sample_id}-{health-before|canary|health-after}`이며 SHA만 저장한다. 타 method/path/header/body는 지정할 수 없다. 각 요청은10초 이하, 전체는 생성 시 duration(최대300초)과 POL 유효창(최대600초) 안에서 남은 시간을 사용한다. 본문은 기존 pinned stdlib probe의128KiB 상한·UTF8·EOF·count/hash 검사 뒤 버린다. parent 출력도 기존 bounded 전송을 사용한다. probe Python watchdog과 parent 정리를 보존하며 대상 Pod/worker는 종료하지 않는다.

POL 입력은 정확히 `readiness_environment_sha256`, `readiness_target_sha256`, `readiness_session`, `readiness_cohort_sha256`, `readiness_validity_start`, `readiness_validity_end`다. session/sample_id는 각각 소문자32hex, SHA는64hex, 시각은 timezone 포함이다. typed `guard.model-request-mapping/1`의 repository/revision/전체 inventory SHA·API alias를 결합한다. 새 profile은 probe Ready condition도 단일 행을 요구한다. lab=true/vLLM0.31 제한을 유지한다. final API 확인은 HTTP 재시도가 아니며, 교체/restart 이후 HTTP를 이어가지 않는다.

`validate_snapshot(value, *, sample_id, environment_ref, target_sha256, session, cohort_sha256, run_id, binding_sha256, expected_sources)`는 닫힌 필드·타입·요청/스냅샷 순서·정확 외부 결합·상태 정합성을 검사한다. `expected_sources={collector,binding,probe}`의 SHA는 검토한 불변 commit/배포 manifest에서 사전에 고정한다. packet이나 실행 중 읽은 hash를 승인값으로 복사하지 않는다. probe SHA는 원 stream_probe bytes를 고정 wrapper로 감싼 코드의 SHA다. 일치해도 worker/source 신뢰를 인증하지 않는다. 알 수 없는 원문 필드·MEASURED 승격·긍정 신뢰 플래그는 거부하고 오류는 `WORKER_READINESS_SNAPSHOT_INVALID`만 반환한다.

시각은 collector UTC 관측 시각이며 같은 collector 시각의 역행도 gap으로 남긴다. probe/host/native monotonic과 변환·차감하지 않는다. 실제 worker/startup 인과·rank coverage·상위 READY/receiver/service/GPU 완료를 추정하지 않는다. lab 전용 결과는 EXAMPLE이고 producer_authenticated/startup_causal_binding_verified/expected_ranks_verified/runtime_hash_approval/upper_READY_delivery_verified/service_gate_verified/worker_model_GPU_complete/fault_or_delay_activated/whole_scope_complete는 모두 false다.

실제 소비는 A1 LIF09의 공통 startup 결합과 B 전용 새 health/chat 소비를 분리한다. B 전용7파일과 old native21 source gate는 원 bytes로 보존하며 B가 새 고정 source pin/수집·분석·판정을 연결한다. controlled delay/resume/fault·Pod/worker 변경·service gate 활성화는 이 작은 계약에서 구현하거나 승인하지 않는다.

부모 A39/56/4·A1 19/27/2, A157의 LIF12 선언 제품 성과와 실제 미연결 범위, A156 B 실패/원본/kernel3/Ref6, A155 PLT12, 기존팩/Ref/실증을 보존한다. 원GPU창 종료/추가Start0·46.66USD/과거사용량/보존120GiB EBS 비용·CI 결제 차단과 의존성 질문 대기를 유지한다. 이 초안은 새 기동·시간·비용·권한·실제 대상 시험 승인이 아니다.
