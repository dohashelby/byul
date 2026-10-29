# SUP-04 Kubernetes 실행 이미지 소비 준비

기준은 `c9af729`와 기존 `A1-kubernetes-req11/1` target이다. 전용 진입점은
`guard.executors.running_image_approval.collect_kubernetes_running_image_approval(ctx, before, after)`이다.
새 target 형식·kubectl wrapper·공용 스키마·실행기 종류를 만들지 않는다.
공통 생산자·전송·registry 연결은 A-1 담당이며 이 변경에는 포함하지 않는다.

전용 실행 진입은 `KubernetesImageApprovalExecutor`이며 기존 logical
`container_config`와 `Ctx`·`_finish`·저장/판정 경로를 재사용한다. 공통
`KubernetesHTTP.owned_snapshot()`의 기존 `{pod, namespace}` 쌍을 두 번 받아
소비한다. A-1의 이 공개 생산자·registry·추가 관측 CheckDef가 준비되기 전에는 실제 제품 수락
미완료다. 생산자가 없으면 ABORTED 고정 진단을 반환하며 Docker나 target 선언을
대신 사용하지 않는다. 원문 API 응답의 조회/정제 코드를 전용 파일에 복제하지 않는다.

새 진입은 `kubernetes_image_api_identity_stable/before/after` 추가 관측에 API
신원을 보존한다. 두 번째 조회·세대 결합·첨부 실패는 ABORTED와 기존 관측을
함께 남긴다. 이 관측으로 승인/비승인·실제 worker 소비나 보안 PASS를 만들지
않으며 원래 7개 승인/기능 관측과 판정 규칙은 유지한다. runner 주입은 target의
lab=false와 관계없이 EXAMPLE이다. 정상 추론은 이 API-only 진입에서 미시도다.

현재 팩31은 새 3키를 정의하지 않아 기존 EVD 정규화에서 제외한다. 공통 코어를
우회하지 않고 A-1에 최소 관측 정의 등록을 요청한다. 전용 시험의
`PACK-EXAMPLE-KUBE-IMAGE/1`은 원래 SUP04 CheckDef에 이 3키만 더한 별도
EXAMPLE/DRAFT 팩이며, 현행 팩31이나 정식 제품 지원으로 대신 표시하지 않는다.
정본 팩의 파일·생성기·공유 builder는 변경하지 않는다. 기존 판정 조건은 같다.

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
