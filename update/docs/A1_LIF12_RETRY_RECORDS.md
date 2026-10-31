# LIF12 자동 retry 선언 기록 소비

상태: 구현·시험 코드 초안. 아직 source/hash 고정·기능·설치·정확 Ref 검증과 확정 전달은 마감하지 않았다. 실제 retry 생산자·발동·비발생·잔존 작업 종결·반환·비용 집행은 미연결이며 전체 LIF12/부모 A 완료를 주장하지 않는다. 원 LIF12/SC18-03의 전체 정상·수동대조·자동도전과 허용/금지·중복/잔존/누적 비용 요구를 유지한다.

소비자는 기존 `request_termination:kubernetes` 실행기다. LIF12의 명시 `kubernetes-automatic-retry-record-observation-v1`에서만 별도 선언 계약을 선택한다. 기존 LIF10 종료·REQ12의 다섯 수동 요청과 native 관측 절차는 유지한다. 새 실행기 종류를 등록하거나 retry를 구현하지 않는다. 파생 `PACK-KUBERNETES-AUTO-RETRY` rev1/LIF12 rev4는 원 질문·기준과 다른76개 정의 bytes를 보존한다.

```toml
[executors]
request_termination = "kubernetes"
[adapter]
name = "vllm"
[request_termination]
target = "/approved/target.json"
retry_scope = "/approved/nonsecret-retry-scope.json"
```

기존 LIF10/REQ12와 같은 target/설정을 쓰면 그 `prepared`를 유지할 수 있다. LIF12는 해당 prepared·자격·HTTP·실제 retry 경로를 읽거나 실행하지 않는다. `retry_scope`는 관측 대상 코드/모델/자격 파일이 아니라 사전 검토한 비민감 메타데이터 입력이다. bounded regular/no-follow FD와 전후 identity·바이트 SHA로 읽고 POL의 `retry_scope_sha256`과 비교한다. 읽힌 해시를 실행 중 승인값으로 만들지 않는다.

POL의 닫힌 입력은 `retry_environment_sha256`, `retry_target_sha256`, `retry_scope_sha256`, `retry_policy_sha256`, `retry_record_source_sha256`, `retry_session`, `retry_validity_start/end`, `retry_policy_mode`, `retry_max_record_delay_ms`다. mode는 명시 `UNRESOLVED/FORBID/ALLOW`이며 기업 기본값이 아니다. 창은 timezone-aware·최대600초, 선언 지연 한도는0~600000ms다. 사전 정책 SHA는 사건이 주장하는 기업 정책 판본의 결합 키이며 실제 권한이나 효력을 인증하지 않는다.

scope schema는 `guard-automatic-retry-scope/1`이다. run ID는 기존 Kubernetes 라벨 계약을 그대로 사용한다. producer/source/code/비민감 구성 SHA, 명시 native source SHA 또는 null, image digest, 정확 model revision/inventory SHA와 선정 subjects·limits를 갖는다. 각 subject는 독립 UUID의 subject/actor/request-family와 `NORMAL/MANUAL_CONTROL/AUTOMATIC_CHALLENGE`, PID/start ticks·worker 세대·clock-domain SHA를 선언한다. ENV Ref가 모델 repository 등 전체 ENV 내용을 함께 고정한다. 같은 family의 actor/scenario는 바뀌지 않으며 다른 subject/worker에 대한 실제 귀속은 별도 미확인이다.

limits의 `max_attempts`, `max_overlap_ms`, `max_remaining_ms/remaining_anchor`, `max_return_ms/return_anchor`, `max_cost`는 미제공이면 명시 null이다. remaining anchor는 `RETRY_DECISION/NEW_ATTEMPT_START`, return anchor는 `CALL_START/TRIGGER`를 한도와 함께 지정한다. max_cost는 단위·정수 amount·비민감 counter source SHA·epoch UUID를 모두 고정한다. 단위는 `attempt/token/gpu_ms/cpu_ms/usd_micros`이며 단위 간 변환·합계를 하지 않는다. 범위는 기술적 수집 상한 subject/attempt64·snapshot event256·파일65536bytes이고 기업 허용 한도를 대신하지 않는다. ALLOW의 미제공 한도는 미확인으로 보고한다.

record schema는 `guard-automatic-retry-records/1`이다. ENV Ref/target/session/run/source/code/scope/policy·producer·snapshot 세대·recorded_at·declared_complete를 고정한다. 사건은 `CONFIGURATION_CLAIM`, `CALL_CLAIM`, `ATTEMPT_START_CLAIM`, `ATTEMPT_END_CLAIM`, `TRIGGER_CLAIM`, `RETRY_DECISION_CLAIM`, `PREVIOUS_WORK_CLAIM`, `COST_SAMPLE_CLAIM`, `RETURN_CLAIM`으로 닫힌 data 형식을 각각 사용한다. event UUID/seq·subject/actor/family·attempt/parent·PID/start·worker/domain·monotonic 또는 null·aware 시각·`INITIAL/MANUAL/AUTOMATIC/UNKNOWN`을 구분한다.

native 사건 Ref가 없으면 null이다. 명시할 때는 사전에 native source SHA를 scope에 고정하고 recording SHA/event seq/session/run/source/PID/start/request-ID SHA 또는 null을 함께 검사한다. 이 메타데이터 Ref는 실제 recording 해석·생산자 인증·자동 retry 원인의 확인이 아니다. 자기 trust/MEASURED/실제 완료 필드·원문 요청/응답·자격·Pod env/argv·임의 예외를 허용하지 않는다. UUID는 자격/본문과 무관한 라벨이고 SHA는 비민감 code/config/counter source 판본과 허용 request-ID 식별에만 쓴다. 자격이나 prompt/response 해시를 여기에 재사용하지 않는다.

기록은 OBS01 `RequiredEventReader`의 `/guard-observation/data/<retry_session>/required-events.json`에서 before/after로 읽고 전후 namespace/Pod/container/image/restart/start를 재검사한다. 해당 파일의 바이트·소유 Pod 결합은 선언된 producer PID·worker·정책·사건 의미를 인증하지 않는다. reader는 writer를 기동하거나 seal하지 않으며 `declared_complete`를 실제 생산자 종료로 올리지 않는다. 실제 생산자 연결·선정 세션 종료/정리 프로토콜은 현재 미연결이다.

projection은 정상·수동대조·자동도전 family를 나누고 수동 시작을 자동 attempt 수에 넣지 않는다. 원/새 attempt와 trigger/decision은 명시 ID·actor/family로 결합하며 자기 parent 순환·중복 start/end·다른 귀속·구성/세대 상충과 누락을 구분한다. 모든 결합은 선언 후보다. `declaration_internal_*_ms`는 동일 subject/PID-start/worker/domain의 선언 내부 정합성 계산이며 실제 관측 시간이 아니다. 다른 PID가 같은 domain SHA를 적어도 overlap/remaining/return 시계를 계산하지 않는다. 비용도 같은 단위/source/epoch의 선언만 비교하고 reset 뒤의 같은 counter는 다시 비교하지 않는다. 앞선 유효 선언·후보는 후속 오류 때문에 사라지지 않는다.

정상 선언 수집의 execution은 COMPLETED일 수 있지만 정상대조 attempted=false/succeeded=null, producer/자동원인/비발생/이전 작업 종결/비용 집행/전체 범위는 false이고 모든 판정은 UNKNOWN이다. 기술적 누락·상충·후속 수집/첨부/정리 오류는 ABORTED로 나눈다. 선언된 금지 retry·비용/attempt/시간 한도 초과를 확인된 실제 FAIL로 올리지 않는다. 기존 다른 경로의 독립 FAIL은 유지한다.

실제 retry 주체/source/config/발동 조건과 허용/금지·attempt/중복·잔존·반환·비용 한도 및 생산자 신뢰/운영 책임은 미확정이다. 기존 질문은 대기 상태로 유지하고 다시 묻지 않는다. 정책에 기본 허용/금지 값을 채우거나 retry를 새로 구현하지 않는다. 기술 초안과 독립 개발을 진행하되 실제 생산자 연결이 없는 관측은 선언/UNKNOWN으로 남긴다.

기준 부모는 A156 `3feae2234a4516e024882fd2206c062d52e1e287`이며 기존 A155 PLT12 성과, B154 원7파일/고정 게이트·실패·Linux kernel3단계·Ref6개, 기존팩/Ref/실증은 보존한다. 부모A39/56/4·A1 19/27/2와 원GPU창 종료/추가Start0·46.66USD/120GiB EBS 비용·의존성 질문/CI 외부 차단을 유지한다. 이 설계는 기동·비용·권한 확대 승인이 아니다.
