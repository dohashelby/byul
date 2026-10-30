# PLT07 확정 A2 egress 소비의 정식 Kubernetes 제품 연결

A1 확정49d81f8/PR136 위에 A2 확정9482a75f0f51af021e3b652f2617f74d040572f0/PR135의 전용3파일을 원본 bytes로 통합한다. 기존2596337의 공개 egress v1·program/source/helper/file-reader pins와 A2 분석을 재사용하며 새 transport나 전용 분석을 만들지 않는다. 원PACK31 PLT07의5분기(SC14-01/14-04/22-01/22-02/24-01)와 Q-R05-09/Q-C10/Q-C11/Q-C07을 유지한다. 선정 UID/netns TCP 한 사건의 기계적 증거가 실제 취득/runtime/usage/exporter 생산자나 전체분기 완료를 뜻하지 않는다.

정식 등록은 `boundary_egress:kubernetes`, wrapper는 `KubernetesEgressProductExecutor`/version0.1.0이다. 고정 A2 `validate_inputs(check, values, *, actor_spec, connect_timeout, total_timeout)`와 `collect_kubernetes_boundary_egress(ctx, *, receiver_target, actor_spec, connect_timeout, total_timeout)`를 그대로 호출한다. 공통 method는 `KubernetesHTTP.egress_snapshot(policy_values, *, receiver_target, actor_spec, connect_timeout, total_timeout)`, profile `kubernetes-owned-explicit-egress-v1`, schema `guard-kubernetes-egress-snapshot/1`, 전용 summary `kubernetes-boundary-egress-consumer-v1`이다.

```toml
[executors]
boundary_egress = "kubernetes"
[adapter]
name = "vllm"
[boundary_egress]
target = "/owned/exact-source-target.json"
receiver_target = "/owned/exact-receiver-target.json"
actor_spec = {uid=2000,gid=2000,groups=[55,2000]}
connect_timeout = 0.1
total_timeout = 10
max_duration_seconds = 30
```

수치는 EXAMPLE 형식이며 기업 정책이 아니다. actor/connect/total/receiver는 필수이고 UID/GID/groups·목적지 기본값은 없다. cfg는 위 입력과 kubectl_binary만 받는다. JSON 두 파일은 각각65536bytes 이내 strict 객체로 읽는다. wrapper 전체 시간은 기본120/최대300초, `total_timeout+1 < max_duration_seconds`다. 실제 공통 작업 전체 budget은 기존 connect<=2 및 connect+1<total<=30초로 API/exec 모두 제한한다. 명시 현재 helper 신원과 다르면 UID를 바꾸지 않고 미확인으로 남긴다.

POL13은 기존11개 이름과 source_target_sha256/receiver_target_sha256 두 필수 str이다. container 신원은 전체 containerd://64hex이며 기존 Docker CID/POL11 형식을 변경하지 않는다. 정확 ENV Ref/source·receiver target/model mapping/run/context·양쪽 namespace/Pod/container/image/restart/start와 소유 receiver의 source command/PID-start/FD-listener/file prefix·device/inode·수신 상관/tuple/32bytes·정책과 operator 창을 원래 관문으로 검증한다. supplementary groups·source gate·parser를 변조하지 않는다.

`tools/build_kubernetes_boundary_egress_pack.py --seed packs/PACK-BASELINE --output <새폴더>`는 검토 PACK31에서 PACK-KUBERNETES-BOUNDARY-EGRESS revision1 DRAFT와 PLT07 revision6/profile/POL2추가만 연결한다. 원래 질문·정상/challenge·5분기·functional_probes=[]·rule_table/보안 nonempty FAIL·다른76개 정의 bytes를 보존한다. 기존 select→run→EVD→RES→RPT→verify→반출/복원·저장팩 정확 Ref 재판정을 쓴다. 기존 boundary_egress:real도 유지한다. 공통 미등록 확인 시험은 이제 새 명시 factory와 기존 Docker factory를 함께 확인한다.

명시 금지 UID/netns TCP connect와 독립 receiver receipt의 필수 신원/양끝/상관/시각이 갖춰진 사건만 A2의 원래 forbidden_egress_successes·egress_trials로 소비해 security FAIL을 유지한다. ACK timeout/mismatch·최종 clock·후속 pipe/report/첨부/정리 오류가 이미 확인된 독립FAIL을 지우지 않는다. 누락·다른 귀속·교체/restart·필수근거 불일치·거부/timeout은 UNKNOWN이고 전체 차단 PASS가 아니다. normal attempted=false/succeeded=null, functional/performance UNKNOWN, egress_scope_complete=false다.

wrapper는 A2 collection_errors·공통 요청 metadata 불완전/진단·public report gap/누락·A2 record와 transport cleanup을 구분해 모은다. 기술 수집/정리 불완전은 ABORTED지만 관측/FAIL은 보존한다. 기존 cleanup CLI run exit1도 유지한다. 임의 예외문·원문 요청/응답/marker·업무 payload·비밀/자격hash·Pod env/argv를 저장하지 않는다.

지원은 lab=true/vLLM0.31/CPython3.12 EXAMPLE이다. API 이미지/선정 source·코드 metadata를 producer 인증/노드 이미지/manifest ancestry/실제worker 신뢰로 올리지 않는다. 실제 worker/SDK·취득/cache/usage/telemetry·DNS/redirect/TLS/proxy/전체송신/기업 인가·native0.32는 별도 미완료다. 수신기는 이미 소유·기동된 같은 cluster/context/run fixture만 사용하며 새 Pod/server/운영 sink를 만들거나 Start하지 않는다.

검증 source는 `7b6b660e09f61cee812fda57491443a2e10c78ee`, 검토 소비 pin은 `cd128d54fa6ca2487f9d0928f13ac023ebbaba0b`다. Windows 영향470통과/기존skip1/미선정1(147.80초/parent154.651), 동일1331파일 Linux471통과/미선정1(46.44초/parent47.349), 전체소비351파일·원본104파일을 확인했다. source/wheel/설치243파일 일치28.261초, 격리 설치본 CLI4사례(completed/denied/later-transport/attachment)15.231초·정확24Ref 반출/복원·저장팩 재판정을 확인했다. A2 원본3파일과 기존 KubeHTTP 전체bytes·REQ02/native/IPC/egress 공통source·원Docker/판정은 그대로다. 실행 중 source 변경·새skip/제외는0이다.

현재 전체suite/실제socket·Kubernetes·worker·GPU 또는 전체5분기 통과로 확대하지 않는다. 최종 문서 마감 커밋과 검증source/wheel source·정확HEAD CI를 구분하며 필수CI5는 성공 전 미확인이다. 공통 입력·전용 소비·정식 제품 연결은 제공됐지만 실제 실증·전체5분기는 미완료다. 부모A39/56/4·A1 19/27/2·A2 20/29/2와c9/sources/기존미커밋/worktree/팩/Ref/실증·원GPU창종료/추가Start0/기존46.66USD/EBS·CI결제 차단을 유지한다.
