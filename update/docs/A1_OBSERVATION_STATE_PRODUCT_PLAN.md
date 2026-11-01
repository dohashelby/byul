# OBS02 상태 대조 제품 묶음 초안

기준은 PR119의 ab84db55a1acb2602b4a9f6841166d2aeca1dcc6다. 공동 c9af729, 기존 미커밋/worktree/원본/sources와 A2·B bytes, 과거 실제 Ref를 보존한다. 부모 A39/56/4와 A1 19/27/2의 범위는 그대로다.

이번 실제 제품 소비자는 A1의 OBS02 SC-23-04다. 기존 요청·native 세션·정확 ENV/target/engine+probe 세대와 CLI/EVD/RES/RPT/반출·복원·저장팩 재판정을 사용한다. profiler SC-23-05는 B의 서비스/backend 요구와 실제 수집 종료 근거가 별도로 필요하다. 대상 승인·검사·소비 SC-23-03도 이번 health 관측으로 완료하지 않는다.

먼저 닫힌 OBS02 공개 요청으로 GET /health 전후, 정상 JSON 전후, 한 요청의 SSE client disconnect를 연결한다. 모두 명시된 단일 engine/API model에 고정된1또는8token 요청이며 무해한 소유 client만 닫는다. 기존 LIF10 native 선택·seal_session/capture와 준비 native8~11의 source/image/lab gate를 재사용한다. 다른 점검 ID/private HTTP 우회·새 Pod/worker 종료·자동 retry·profiler 활성화·소스 게이트 완화는 없다.

필요 관측은 HTTP listener 응답/완결·정상 출력 구조·client close·native handler/request/scheduler/forward/output/collector의 각각의 사건·주체·session/request/call/PID/start/source·세대·시간 범위다. health200을 모델 준비, 요청 접수를 실제 계산·최종 전달, client close를 worker/GPU 회수로 올리지 않는다. native 사건 자체도 독립 생산자 인증이나 실제 image/worker 신원의 대체가 아니다.

정책은 정확 ENV/target·선택 세션·유효창, 기업이 명시한 상태 의미와 기한·필수 독립 관측 범위를 구분한다. 미입력된 기업 상태 의미/독립 worker/기록 집행점은 UNKNOWN·미완료 사유로 유지하며 새 PASS/FAIL 규칙을 임의로 만들지 않는다. 정상대조 역시 단지 HTTP200만으로 전체 상태 대조 성공을 기록하지 않는다.

구현 순서는 공개 HTTP 요청 계약과 선택된 native 소비 → 기존 실행기/명시 DRAFT 팩/registry와 증거 흐름 → 정상·실패/누락·교체/restart·후속 seal/capture/첨부/정리 오류 → 고정 source와 전체 소비 pin·영향/설치/정확 Ref 검증이다. 현재 공개 dispatcher와 observation_state:kubernetes, PACK-KUBERNETES-OBSERVATION-STATE builder·정제 native projection·기존CLI/정확Ref 시험을 작성했다. 아직 고정소스 검증 전이며 실행하지 않은 시험의 통과를 기록하지 않는다. 코드/시험/문서 묶음 완료 전 검증하지 않고 검증 중 source를 바꾸지 않는다.

새 실제 Kubernetes/GPU 기동은 수행하지 않는다. 원승인창 종료·추가 Start0·마지막 확인 stopped·기존 총46.66USD 상한과 CI 결제 차단을 유지한다. 새 실제 조건은 별도 시간 승인·fresh ENV/POL/source/image·실제 소유권/차례·정상대조와 정리 책임이다.

제품 계약 v1: POL은 observation_state_environment_sha256, observation_state_target_sha256, observation_state_validity_start/end, observation_state_required_components의 정확5개다. required component는 http_listener/request_selection/asgi_application/native_request_state/native_forward/output_collector이며1개이상 명시한다. component별 observed/bound event 수와 state_truth_verified=false를 기록한다. 기업의 상태 의미·완료 기한이나 보안 허용표를 이 값에서 추정하지 않는다.

관측은 state_http_trials/native_bindings/native_events/asgi_events/output_events/output_links/component_status/conflict_candidates와 scope/independent_worker/state_semantics=false다. Legacy ASGI request hash만 있는 행에는 case_id=null과 후보ID를 남긴다. Native10의 현재 invocation·physical process 결합과11의 실제 collector/object provenance는 해당 고정 profile에서만 기록하며 client receipt/독립worker 진실로 올리지 않는다. 다른 recording의 native hash만 일치하는 사건은 후보다. /11 공통 projector의 기존 REQ12 전용 집계 플래그를 OBS02 판정에 쓰지 않는다.

재현은 tools/build_kubernetes_observation_state_pack.py --seed reference/pack-seeds/PACK-BASELINE-31 --output <새팩> 후 기존 select/run/judge/report/verify/evidence export/restore다. guard.toml의 [executors] observation_state="kubernetes" 및 [observation_state] target/prepared, 선택 kubectl_binary/max_duration_seconds만 받는다. 외부 관측 JSON·민감 응답·새 실행기 fixture 우회는 없다. 원본 PACK31·다른76개 bytes·원래 세 분기 의미를 보존한다.
