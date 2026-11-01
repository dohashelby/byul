# SUP-04 공통 API snapshot 제품 연결

A-1 공통 `9c863a7` 위에 A-2 확정 `baf2d8b`의 전용 3파일을 커밋 단위로 보존 반영한다. 전용 분석기는 재작성하지 않는다. 기존 logical executor `container_config`에 명시적으로 `kubernetes` 구현을 선택해 기존 CLI와 EVD/RES/RPT 저장 경로에서 A-2 API 소비를 호출한다. 새 CheckDef·정책 키·판정 의미·팩 판본은 추가하지 않는다.

```toml
[executors]
container_config = "kubernetes"
[adapter]
name = "vllm"
[container_config]
target = "existing-request-binding-target.json"
max_duration_seconds = 30
```

현재 이 구현은 `running-image-approval-v1`인 SUP-04만 받는다. 다른 container_config 점검·추가 전송 설정은 I/O 전에 거부한다. 기존 `guard-kubernetes-request-binding-target/1`의 정확 ENV·engine/probe·context·소유 run·모델 매핑을 재사용한다. 새 target/공용 schema나 CPU 전용 실행기를 만들지 않는다. 기존 Docker 구현은 그대로다.

`KubernetesHTTP.snapshot()`은 기존 bounded namespace/Pod 조회와 원본 신원 검증 뒤 정제 snapshot을 제공한다. 같은 target을 두 번 조회하고 A-2 전용 소비부에 전후 쌍을 넘긴다. `kubectl exec`, HTTP, 정상 추론을 실행하지 않는다. 수집 시작/종료 UTC·공통 소스 파일 bytes hash·정규 target/snapshot hash·고정 진단을 기존 첨부에 남기며 원본 Pod·env·argv·hostPath는 저장하지 않는다. 조회는 원자적 cluster snapshot이나 전체 구간 보장이 아니며 시각·소스 hash가 생산자 인증을 증명하지 않는다.

API 전후 신원이 같아도 node containerd 이미지 관계·RepoDigest·manifest/source 계보·승인 주체는 미확인이다. 승인 목록이 일치·불일치·명시 빈 목록인 경우 모두 승인/비승인 관측 미수집·보안 UNKNOWN이다. 정책 누락은 기존 NO_POLICY_VALUE다. A-2의 PROPOSED_CONSUMER와 전체 runtime 승인 dispatch 미연결 설명은 그 한계를 유지하며, 공통 첨부의 dispatch는 API-only 제품 연결을 별도로 표시한다.

후속 snapshot의 교체·restart·미수집·timeout·정리 실패는 첫 정제 신원과 고정 진단을 보존한다. 첨부 실패의 ABORTED와 보안 UNKNOWN은 구분하며 기존 Docker에서 확인한 독립 FAIL·성공 첨부·정확 Ref는 유지한다. 선언만으로 신원을 확인할 수 없어 정적 이월은 기존 None 경계를 재사용한다.

검증은 실제 registry→select/run/judge/report/verify→반출/복원→저장 팩 정확 Ref 재판정을 사용한다. CPU 응답 주입은 EXAMPLE이며 실제 Kubernetes/GPU 수락이 아니다. 실제 SUP-04 완료에는 runtime 이미지 관계 생산자·정상 추론 전송·패키지/import/entrypoint/mount 변경과 배포 gate가 남는다. `/v1/completions` 지원을 REQ11 JSON/SSE 범위에서 추정하지 않고 lifecycle lab gate도 유지한다.
