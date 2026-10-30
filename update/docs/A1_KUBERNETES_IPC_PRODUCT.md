# PLT02 고정 A2 소비의 Kubernetes 정식 제품 연결

기준은 A1 확정5e7507b/PR132이며 A2 확정fac0907302d78c271d0b571d2413f7a8f4b295e5/PR131을 커밋 단위로 통합한다. A2 전용4파일 bytes를 보존하고 A1은 wrapper/registry·검토 파생팩·CLI/증거·고정 소비 검사만 맡는다. 공통 입력은 이미 제공된 ab84db55a1acb2602b4a9f6841166d2aeca1dcc6의 `KubernetesHTTP.ipc_snapshot(policy_values, *, actor_specs, connect_timeout, total_timeout)`이다. 이전3ec 보수 d1fafb0도 이력/원본으로 보존하며 중복 적용하지 않는다.

논리 실행기 `unix_ipc`, 등록 `unix_ipc:kubernetes`, wrapper `KubernetesIPCProductExecutor`/version0.1.0이다. A2의 `validate_inputs`/`collect_kubernetes_unix_ipc`를 그대로 호출한다. source profile `kubernetes-owned-unix-ipc-access-v1`, snapshot schema `guard-kubernetes-unix-ipc-snapshot/1`, 전용 요약 `kubernetes-unix-ipc-consumer-v1`을 유지한다. 공통 actor/contract/program·원본probe pins와 legacy parser/group 제한은 수정하지 않는다. 기존 ab84 source의 미등록 placeholder 상수/함수는 수집 경로나 소비 검증이 호출하는 관문이 아니며 원본 pins와 함께 보존한다. 현재 정식 등록의 근거는 이 registry factory·명시 CheckDef·canonical CLI 검증이다.

```toml
[executors]
unix_ipc = "kubernetes"
[adapter]
name = "vllm"
[unix_ipc]
target = "/owned/target.json"
actor_specs = [{uid=2000,gid=2000,groups=[55,2000]}, {uid=1000,gid=1000,groups=[44,1000]}]
connect_timeout = 0.1
total_timeout = 3
max_duration_seconds = 30
```

위 숫자는 EXAMPLE 입력 형식이며 기업 허용값이 아니다. actor_specs·connect_timeout·total_timeout은 필수이고 UID/GID/groups 기본값은 없다. 허용/비허용 서로 다른 UID를 포함한2~4 actor, 각 groups 최대16개를 기존 공통 validator로 확인한다. 실제 groups를 바꾸거나 원래 `_parse`에 맞추기 위해 제거하지 않는다. 그룹 정렬은 동일 explicit 집합의 canonical 순서일 뿐 새 권한을 만들지 않는다.

정책은 기존 `protected_unix_paths`1~8개와 `allowed_ipc_uids`1~16개뿐이다. actor/collector/owner 관측에서 허용 목록을 생성하지 않는다. connect_timeout은 기존0<초<=2, IPC total은 기존 공통 최대300초 안에서 전체 transport의2초 여유 및 고정 normal3초 예산을 남겨야 한다. wrapper max_duration은 기본120/최대300초이며 `total_timeout+2+3 < max_duration_seconds`를 요구한다. 새 60초 시험 실패 gate나 기업 시간 정책은 없다.

정확 ENV Ref·target canonical SHA·API model mapping·run/context·engine/probe namespace/Pod UID·containerID/imageID·restart/start를 기존 REQUEST_BINDING_PROFILE에서 확인한다. 환경 모델 repository/revision·inventory와 API 별칭은 기존 공통 모델 매핑을 그대로 사용한다. 전용 함수는 엄격한 envelope/source/program/policy/actor/경로 검사를 재사용한다. 보고된 CPython code SHA는 metadata이며 source/image/producer/실제 worker 인증으로 승격하지 않는다.

`tools/build_kubernetes_unix_ipc_pack.py --seed packs/PACK-BASELINE --output <새폴더>`는 검토 PACK31 exact Ref에서 PLT02 revision6/collection profile만 연결하는 PACK-KUBERNETES-UNIX-IPC revision1 DRAFT를 만든다. 원질문·기준·두POL·고정 completion 정상표본·judge/독립FAIL·분기와 다른76개 정의 bytes를 보존한다. 기존 select→run→judge→report→verify→evidence export/restore→저장팩 정확 Ref 재판정을 쓴다. 별도 CPU registry·collector 복제·우회 importer는 없다.

선정 경로의 전후 socket inode/owner PID-start-UID, actor와 collector의 서로 다른 PID·namespace/boot/time-domain·UID/GID/groups/capability 및 peer/anchor가 갖춰진 연결만 `connection_verified`다. kernel 시도 없는 metadata 실패는 접속 거부가 아니다. 실제 groups·전후 packet/owner 조건은 A2 전용 소비가 판단하고 A1이 재작성하지 않는다.

뒤 경로·normal 응답·report·optional 첨부·parent/child 정리 오류가 앞선 확인된 `unix_ipc_violations`를 지우지 않는다. A1 wrapper는 전용 summary의 collection_errors와 public transport gap, `_kubernetes_ipc_cleanup_errors`와 최종 public cleanup을 별도로 합친다. 기술 수집/정리 불완전은 ABORTED로 기록하며 기존 독립 security FAIL은 유지한다. 정상 HTTP가 반환됐으나 모델/응답이 맞지 않는 경우와 전송/수집 불완전도 구분한다. 첨부 쓰기 실패는 기존 공통 `_finish` 경계로 처리한다.

자료 출처는 지원하는 lab=true/vLLM0.31/CPython3.12 범위의 EXAMPLE이다. 주입·실험 결과를 DECLARED/MEASURED로 올리지 않고, 미지원 설정·lab=false/native0.32는 actor/normal 실행 전에 거부한다. 전체 보안 PASS 규칙은 없으며 정상 선정 표본에서도 UNKNOWN, 명시 경로·비허용 UID의 확인된 연결은 원래 독립 FAIL, 기능/성능은 UNKNOWN이다. ipc_scope_complete·worker_subject_verified는 false다.

실제 worker/message 소비, 같은 UID의 비허용 프로세스, abstract Unix·SHM·TCP·TP·전체IPC inventory·기업 운영 인가는 미지원이다. helper/probe actor와 실제 serving worker의 권한을 구분한다. 원문 response/요청·메시지·비밀·Pod env/argv·임의 예외 원문을 저장하지 않는다. 정제 source/capture/actor/path SHA와 결과 metadata 및 EVD/RES/RPT 정확 Ref만 소비한다.

현재 구현·직접/인접 회귀·문서 초안 종료 후 source 고정 준비 단계다. 새 정식 CLI/설치/전체통과·실제 실증을 아직 주장하지 않는다. 부모A39/56/4·A1 19/27/2·A2 20/29/2·c9와 원본/worktree/sources/·과거 팩/Ref/실증을 유지한다. 원GPU창 종료/Start0·기존46.66USD/과거 사용량·보존120GiB EBS 과금·CI결제 차단을 유지하며 새 기동/기간/비용/IAM을 승인하지 않는다.
