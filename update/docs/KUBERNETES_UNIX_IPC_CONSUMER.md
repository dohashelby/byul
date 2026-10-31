# PLT-02 Kubernetes 전용 소비 v1

고정 공통 입력은 ab84db55a1acb2602b4a9f6841166d2aeca1dcc6의
`KubernetesHTTP.ipc_snapshot(policy_values, *, actor_specs, connect_timeout, total_timeout)`이다.
profile은 `kubernetes-owned-unix-ipc-access-v1`, schema는 `guard-kubernetes-unix-ipc-snapshot/1`이다.
A-1의 wrapper/등록/공유 팩은 별도이며, 이 변경으로 우회 CLI나 새 등록을 만들지 않는다.

공개 전용 함수는 `guard.executors.kubernetes_unix_ipc.validate_inputs`와
`collect_kubernetes_unix_ipc(ctx, *, actor_specs, connect_timeout, total_timeout)`이다.
논리 실행기는 unix_ipc, 대상 ID는 PLT-02, collection_profile은 위 명시 profile이어야 한다.
기존 PACK31과 Docker 프로필은 보존하며 새 프로필의 공통 정식 등록을 A-1에 요청한다.

정책은 기존 두 키 protected_unix_paths와 allowed_ipc_uids만 받는다. actor UID/GID/groups는
관측에서 만들지 않고 시험 소유자가 별도 명시한다. 실제 supplementary groups를 그대로
공통 decode와 전용 자격 대조에서 검증하며 기존 `_parse`의 groups 제한은 바꾸지 않는다.
원본 ipc_probe bytes도 바꾸지 않는다. 기존 `path_connection` 분석을 공유하되, 새 소비는
exact ENV/target/binding/policy/source/program, actor와 collector의 서로 다른 PID·동일
namespace/boot/time-domain, UID/GID/groups/capability, 소켓 anchor와 owner PID/start/UID,
SO_PEERCRED의 전후 일치를 추가 확인한다.

각 경로에서 완료한 pre/actor/post 근거가 있는 연결만 connection_verified다. 후속 경로의
불완전·timeout·child 정리 실패는 앞선 확인 결과를 지우지 않는다. collector-after가 없더라도
앞선 경로의 inspect-after와 actor 전후·소켓 anchor·Pod 전후가 모두 있으면 그 경로를 보존한다.
collector-after가 실제로 바뀌거나 Pod 전후 확인이 없으면 해당 귀속은 미확인이다.
후속 부모 cleanup 실패도 packet을 남기지만, post-generation 확인 자체가 실패한 경우에는
연결을 확정하지 않는다. metadata 조회만 실패한 행을 kernel 접속 거부로 만들지 않는다.

정상 대조는 기존 normal_requests와 고정 POST /v1/completions/ping/max_tokens1이다.
정확 개수·응답 ID·구조화 본문을 확인하고 원문을 저장하지 않는다. 앞서 확인한 독립 위반은
정상 대조·후속 report·optional 첨부 오류로 지우지 않는다. 공통 wrapper는 반환 draft를 보존하고
`ctx._kubernetes_ipc_cleanup_errors`와 transport cleanup 상태를 별도 합쳐야 한다.

공통 결과와 판정은 EXAMPLE, lab=true, vLLM0.31, 명시 CPython3.12 경계다. 보고된 code SHA는
공통 parser가 형식을 확인한 관측 metadata이며 생산자/이미지 인증이나 worker 신뢰 근거가 아니다.
unix_ipc_trials/unix_ipc_violations와 기존 rule_table 의미를 유지하고 ipc_scope_complete는 false다.
전체 보안 PASS는 없으며, 실제 worker/message 소비·같은 UID의 비허용 프로세스·abstract Unix·SHM·TCP·TP·
기업 운영 인가·native0.32를 지원한다고 해석하지 않는다.

공통 CLI/registry/공유 팩 연결 후 EVD/RES/RPT·verify·반출/복원·저장팩 정확 Ref를 확인해야 한다.
전용 CPU/설치 검증만으로 이 정식 등록이나 실제 환경 실증 단계를 마감하지 않는다.
