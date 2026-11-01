# Kubernetes IPC 공통 전송 v1 — 전용 제품 소비는 별도

기준은 d1fafb0e5c4f3246f4280e85ad40e350ec6ef6cc다. A2 3ecfd5f의 전용 원본, B의 c9 원본 검사, 공동 c9af729와 기존 미커밋·sources/·팩·Ref·실증을 보존한다. 소비 대상은 A2 PLT02의 기존 unix_ipc 분석·독립 FAIL·기존 CLI/정확 Ref 흐름이다. A1이 전용 분석기를 대신 작성하지 않는다.

공개 인터페이스는 기존 REQUEST_BINDING_PROFILE의 `KubernetesHTTP.ipc_snapshot(policy_values, *, actor_specs, connect_timeout, total_timeout)`이다. `kubernetes-owned-unix-ipc-access-v1` / `guard-kubernetes-unix-ipc-snapshot/1`이며, 정확 ENV Ref·target/run·engine와 probe의 namespace UID/Pod UID/container/image/restart/start 검사를 재사용한다. 현재 명시 lab=true와 vLLM0.31만 받으며 모든 결과는 EXAMPLE, producer/worker 신뢰와 whole scope는 false다. Native0.32·기업 운영 프로필을 새로 열지 않는다.

입력은 정확 두 정책 키 protected_unix_paths(중복 없는 절대 POSIX 경로1..8, 각100UTF8bytes)와 allowed_ipc_uids(기존 A2의 숫자 문자열1..16)다. actor_specs는 UID/GID 정수·supplementary groups 최대16개를 각각 명시한2..4개이며 정책 허용·비허용 UID가 각각 필요하다. UID에서 GID/groups/정책을 추정하지 않는다. connect는0초과2초이하, total은0초과300초이하다. 전송은 소유 자식 정리와 후속 세대 조회를 위해 남은 예산에2초 여유를 요구한다. stdout65536bytes, 원본 probe의 proc/fd4096/owner16 상한을 유지한다.

프로그램은 A2 ipc_probe.py의 정확 f096e9d95aed61315b4d129369cd273786fa9dc106ac264d48c670e004269ca2 bytes를 소비한다. 별도 actor supervisor와 입력 계약도 리터럴 SHA로 검사한다. 격리 모듈을 optimize=0으로 컴파일하며 CPython3.12 판본·세 컴포넌트 code SHA를 기록한다. 전송한 program/source bytes와 target이 보고한 code SHA의 출처를 구분한다. 보고된 code SHA나 소켓 metadata는 생산자 인증·실제 worker 신원 증명이 아니다.

collector의 inspect-before → 명시 actor별 zero-payload connect → inspect-after 순서다. 원본 probe 함수를 경로별 호출하여 이전 완료 행을 보존한다. 소유 fork child만 이미 보유한 권한에서 정확 UID/GID/groups로 제한하고 no_new_privs·capability·PID/start/boot·Linux mnt/net/pid/user/time namespace를 전후 관측한다. 비root는 현재 자격과 일치할 때만 실행하며, 제공할 수 없는 자격은 unavailable다. 새 Pod/RBAC/host 권한, host root 실행, worker credential 변경은 없다. 자식의 inherited 비표준 FD를 닫고 actor 자격에서 각 경로 성분을 O_PATH/O_NOFOLLOW로 연다. 커널 connect는 그 leaf FD의 proc 경로를 사용하고 경로/디렉터리/leaf identity를 재확인한다. 원문 경로·본문·env·argv·예외 문자열은 결과에 저장하지 않는다.

자식은 완료한 경로의 정제 누적 JSON frame을 소유 pipe로 전달한다. 후속 deadline/출력 한도/자식 오류가 나도 앞서 받은 frame을 남기고 실제 waitpid 정리 상태를 별도 기록한다. 원본 metadata 실패를 denied로 바꾸지 않으며 kernel connect EACCES/EPERM만 denied다. Inspect 후속 행이 실패해도 완료 행을 유지한다. 전송 부모의 후속 pipe/reap 실패에서는 strict JSON/필드 검증을 끝낸 packet을 보존하되 기존 capture 성공 gate를 그대로 실패시킨다. 세대 교체는 packet 보존과 binding 미확인을 함께 기록한다.

`available`은 공개 전송·후속 세대 확인 완료, `requested_observations_complete`는 명시 요청 범위의 행·자격·정리 수집 완결이다. 어느 값도 정책 PASS나 전체 PLT02 완료가 아니다. A2가 소켓 identity/owner PID-start-UID/SO_PEERCRED/actor namespace와 anchor를 전후 대조해야 한다. 기존 A2 `_parse`는 UID와 다른 supplementary group을 거부하므로 packet.groups를 바꾸어 우회하지 않는다. 일반 groups와 새 envelope를 소비하는 A2 확정 커밋이 필요하며 새 PLT02 Kubernetes registry는 아직 등록하지 않는다.

기존 `normal_requests`의 POST /v1/completions, ping, max_tokens1과 정확 응답 상관은 재사용한다. A2의 새 전용 소비·정상/독립 FAIL·후속 첨부 오류·기존 CLI 반출/복원 재판정이 전체 제품 연결의 남은 단계다. 이번 공통 경계 검증과 기존 CLI 회귀는 별도 근거로 기록한다. 실제 worker/message 소비·동일 UID 비승인 process·abstract Unix/SHM/TCP/TP·전체 IPC inventory는 미지원이다.

실제 실증에는 명시 소유 socket/actor 정책, fresh ENV/POL/source/image·실행 소유권/차례와 새 승인 시간이 필요하다. 원래 GPU 창 종료·추가 Start0·마지막 확인 stopped·기존 총비용46.66USD와 CI 결제 차단을 유지한다. 공통 전송 고정본을 먼저 인계하고, A2 소비 대기 때문에 A1 본인19개 준비된 개발을 멈추지 않는다. 부모 A39/56/4와 A1 19/27/2는 유지하며 완료 수를 추가하지 않는다.
