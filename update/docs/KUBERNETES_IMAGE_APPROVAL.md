# SUP-04 Kubernetes 실행 이미지 소비 준비

기준은 `c9af729`와 기존 `A1-kubernetes-req11/1` target이다. 전용 진입점은
`guard.executors.running_image_approval.collect_kubernetes_running_image_approval(ctx, before, after)`이다.
새 target 형식·kubectl wrapper·공용 스키마·실행기 종류를 만들지 않는다.
공통 생산자·전송·registry 연결은 A-1 담당이며 이 변경에는 포함하지 않는다.

`before`와 `after`는 기존 `{pod, namespace}` 쌍이다. ENV seal/정확 Ref,
명시 context·소유 run, namespace 이름/UID, Pod UID, containerID, imageID,
restartCount, startedAt, Running/Ready를 기존 `verify_owned_pod`로 대조한다.
실패 시 고정 진단만 저장하며 이미 얻은 전 snapshot의 정제 신원을 보존한다.
Pod 원문·annotations·환경·argv·Secret·stderr·예외 원문은 결과에 넣지 않는다.

현재 확인하는 것은 **API 전후 신원 일치**다. API가 `imageID`로 pinned
RepoDigest와 같은 문자열을 보고해도 node runtime의 실제 RepoDigest 관계나
manifest/source 계보를 확인했다고 하지 않는다. 외부 dict의 `verified=true`
신고도 받아들이지 않는다. 해당 공통 생산자가 없으면
`RUNNING_IMAGE_RUNTIME_RELATION_UNAVAILABLE`, 승인/비승인 미수집,
`running_image_identity_verified=false`를 보존한다. 정책 정상·불일치·명시 빈
목록 모두 API 자료만으로 FAIL이나 PASS를 만들지 않는다. 정책 미입력은 기존
`NO_POLICY_VALUE`이며, 빈 목록을 미입력으로 바꾸지 않는다.

기존 Docker의 실제 image ID→RepoDigest 관측·승인 분석·확정 위반 FAIL과
후속 실패 보존은 유지한다. 새 소비부는 원문 challenge를 모두 미실행으로
남기고 정상 추론을 시도하지 않는다. 현재 SUP-04의 `/v1/completions`를
REQ11 전송의 `/tokenize`·`/v1/chat/completions` 지원으로 추정하지 않는다.
정상 추론 연결에는 별도 공통 지원 계약이 필요하다.

A-1은 같은 구간의 수집주체·고정 소스/판본·시각·오류/불완전 진단과
정확 ENV/run/owner에 결합된 node runtime 이미지 관계를 제공해야 한다.
이 자료가 준비되기 전에는 계보·소비를 주장하는 보충값을 만들지 않는다.
관계가 확인된 출처에 한해서 기존 승인 분석과 독립 FAIL 보존을 연결한다.
실제 환경을 lifecycle의 `lab=true`로 바꾸어 수집 gate를 우회하지 않는다.

CPU 회귀는 공통 bounded kubectl 응답 파싱과 전용 소비, 기존 제품
CLI→EVD→RES→RPT→verify→반출/복원→정확 Ref 재판정을 확인한다.
시험에서 공통 응답/소비 연결만 주입하며 전용 CPU 제품 경로나 가짜 실행기를
등록하지 않는다. 모두 EXAMPLE이고 production dispatch와 실제 Kubernetes/GPU
수락은 미완료다. 수집시각·생산자 인증·전체 구간/worker 소비도 입증하지 않는다.

SUP-04 전체 완료에는 패키지/import/native library/entrypoint/mount 덮어쓰기와
변경 사본의 실제 사용·배포 gate가 남는다. 이 부분 준비를 전체 SUP-04,
A-2 20개, 부모 A39개 완료로 집계하지 않는다.
