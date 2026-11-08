# PLT-02 실제 IPC 소유 공통 입력 (개발 담당, B 의존 반환 6, 2026-11-08)

B 요청(`gpu-b/consolidation-20261106/DEPENDENCY_RETURNS.md` 6번): 기존 pathname/actor UID 연결 관측과 별도로, 실제 serving
프로세스의 listener/accepted socket 소유·상대·roster를 실제 접점·소유 프로세스·namespace·PID/start·FD·커널 inode로 돌려줄 것.
파일 inode와 socket 커널 inode를 섞지 않을 것.

## 반환물

| 항목 | 내용 |
|---|---|
| 계약 종류 | `gpu_common_inputs.KINDS['ipc-ownership']` (소비 점검 PLT-02). 행: `process`, `unix_socket` |
| 관측기 | `collectors/ipc_ownership_observer.py` `OBSERVER` — 엔진 컨테이너 안 `python3 -I -S -c`, 읽기 전용 |
| 생산자 | `collectors/ipc_ownership_producer.py` — `guard-engine-ipc-ownership` 판본 1, 소스 SHA = 관측기+생산자 bytes |
| 전송 | `KubernetesHTTP.engine_ipc_ownership()` — request-binding·engine-callback 전송, 관측 앞뒤 소유 Pod 바인딩 확인 |
| 요약 | `ipc_ownership_producer.summarize(record)` — 역할별 listener 수, 연결 방향(연결한 역할 → accept한 역할), 상대 미해결 수 |
| 등록 | **미등록**. GPU 엔진에서 확인한 뒤 `SUPPORTED_PRODUCERS`에 더한다. 그 전 기록은 `GPU_INPUT_PRODUCER_UNSUPPORTED`로 complete가 아니다 |

## 무엇을 관측하나

- 프로세스: 컨테이너 PID 이름공간의 PID·시작 ticks(읽기 앞뒤 동일만 안정)·실효 UID·net 이름공간 inode·역할.
  역할은 vLLM 프로세스 제목으로만 정한다: `api_server`(PID 1 `vllm serve` 또는 `VLLM::APIServer`), `engine_core`(`VLLM::EngineCore`),
  `worker`(`VLLM::Worker…`, TP/PP/DP 번호가 제목에 있으면 rank), `utility`(multiprocessing resource_tracker 등), `other`.
  제목·명령줄 원문은 남기지 않는다(제목 SHA-256만).
- socket: 프로세스 fd의 `socket:[inode]`와 커널 sock_diag(AF_UNIX)의 상태·이름·VFS·상대 inode.
  `listen`(TCP_LISTEN) / `accepted`(이름 있는 연결: 그 경로의 listener가 accept) / `connected`(이름 없는 연결: 연결한 쪽·socketpair) / `unconnected`.
  경로는 `path_sha256`, 경로 파일의 (dev, ino)는 `path_file_sha256`로 따로 둔다. `peer_binding`은 상대 socket을 가진 바인딩이며 컨테이너 안에 없으면 None.
- 위반 후보(판정 아님): `listener_owned_outside_serving_roles`(utility·other가 listen), `peer_outside_container_roster`(연결 상대가 컨테이너 안에 없음).
- `source_ended`: 모든 프로세스 안정 + sock_diag 끝까지 + 상한·읽기 실패 없음 + 바인딩 동일.

## 무엇을 관측하지 않나

메시지 내용·실제 명령 소비·sender/reader의 요청 단위 귀속(PLT-06), 허용 UID 정책 판정, 다른 컨테이너·노드의 상대 프로세스.
utility 프로세스를 worker로 부르지 않으며, listener 소유만으로 그 프로세스가 메시지를 소비했다고 주장하지 않는다.

## 확인

- k3d 실제 vLLM 0.31.0 CPU 엔진(arm64, EXAMPLE): API 서버·EngineCore·Worker·utility 4프로세스 안정, unix socket 20개, sock_diag 완결.
  listener 역할별 2·2·2, 연결 EngineCore→API 서버 2·EngineCore→Worker 2·Worker→EngineCore 2, 상대 미해결 0.
  관측 출력은 `tests/fixtures/ipc_ownership_k3d_20261108.json`.
- 시험 `tests/test_ipc_ownership.py` 17개: 실제 roster·방향 복원, 등록 시 complete, socket inode와 경로 파일 신원 분리,
  불완전 관측(sock_diag·프로세스 변경·상한)·바인딩 변경은 source_ended=false, 컨테이너 밖 상대·utility listener는 후보만,
  안정 프로세스 없음은 기록 없음, 형식 오류 거부, TCP 등 비unix socket 제외, 전송 프로필 제한.
- 계약 의존 시험 168개 통과.

## B 연결 방법(제안)

`kubernetes_unix_ipc.collect_kubernetes_unix_ipc`가 기존 actor 연결 시험과 같은 바인딩 사이에서 `engine_ipc_ownership()`을 부르고,
`record()`·`summarize()` 결과를 별도 관측으로 둔다. 기존 groups·actor 판정·독립 FAIL은 바꾸지 않는다. B 소비자 수정은 B가 한다.
