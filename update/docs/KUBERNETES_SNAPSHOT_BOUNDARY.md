# A-1 공통 Kubernetes snapshot 소비 경계

공동 base는 `c9af729cd1893eda14d0bb222a8fab46c2a6d7b8`다. 기존 `A1-kubernetes-req11/1`의 내부 `{pod, namespace}` 입력과 `guard.model-request-mapping/1`을 유지한다. 과거 인계의 `/2`는 공용 schema 판본이 아니다. 이 문서는 새 외부 교환 계약이나 실제 GPU 검증 결과가 아니다.

`guard.runtime.kubernetes_binding.project_owned_snapshot(pod, namespace, expected, run_id, require_ready=True)`는 기존 `verify_owned_pod`로 원본 API 응답의 소유 신원을 먼저 확인한 뒤 정제한 깊은 복사본을 반환한다. `KubernetesHTTP.model_snapshots`와 `KubernetesLifecycleReader.snapshot()`이 소비하며, 기존 A-2 `analyze_model_mount`에 같은 형태로 전달된다. 새 전송 계층이나 A-2 전용 분석기를 복제하지 않는다.

정제 입력에 남는 범위는 다음과 같다.

| 위치 | 관측 필드 |
| --- | --- |
| namespace | kind, metadata의 name·uid·deletionTimestamp |
| Pod metadata | name·namespace·uid·deletionTimestamp, guard.run label |
| Pod spec | nodeName·hostNetwork·hostPID·hostIPC, 선택 container의 name·image |
| mount 선언 | 선택 container의 volumeMounts: name·mountPath·readOnly·subPath·subPathExpr·mountPropagation; volumes: name·hostPath.path/type |
| Pod status | phase·podIP, Ready condition의 type/status, 선택 container의 name·containerID·imageID·restartCount·ready·running.startedAt |

다른 container·annotation·관리 metadata·env·command/args·Secret/ConfigMap 참조 내용·상태 message/reason은 전달하지 않는다. mount 중복·subPath·쓰기 허용·경로 불일치는 기존 A-2 소비 의미를 유지한다. 이 입력의 hostPath는 일시적인 선언 비교용이므로 저장하지 않는다. EVD에는 기존 분석 결과·소유 신원 hash만 연결한다. command/args 확인이 필요한 기존 template 읽기는 `_verify`의 일시 원본으로 계속 수행하며 결과에 저장하지 않는다.

context·정확 ENV Ref·소유 run·namespace/Pod UID·containerID·imageID·restartCount·startedAt는 기존 target과 함께 결합한다. Pod `spec.image`는 선언된 pin이고 `status.containerStatuses.imageID`는 Kubernetes가 보고한 값이다. Docker config ID, RepoDigests, manifest index→플랫폼 manifest 계보, node runtime 검증으로 바꾸지 않는다. 기존 `native_image_reference`의 `node_runtime_image_verified`와 `manifest_ancestry_verified`, `image_check=False`도 유지한다. A-2 SUP-04가 이 자료를 소비하더라도 실행 이미지 승인 전체나 worker의 실제 bytes 소비를 증명하지 않는다.

REQ-11 HTTP 경로는 기존 검토된 containerd·IPv4·직접 Pod HTTP의 JSON/SSE 범위다. HTTP의 `lab=false`와 lifecycle target의 `lab=true` 제한은 서로 다르며 기존 gate를 완화하지 않는다. lifecycle reader는 Running이지만 Ready가 아닌 소유 세대의 실패 metadata를 수집할 수 있고, A-2 mount 분석기는 Ready를 따로 요구한다. 누락·교체·restart·잘못된 종류/신원·불완전 수집은 기존 gap/UNKNOWN이며 앞서 얻은 독립 FAIL과 성공 첨부를 지우지 않는다.

0.31/0.32 GPU REQ-11 요청 소비 계약과 native 계측 지원은 구분한다. 0.32 native 계측은 미지원이다. 실제 cluster·설치판·GPU·worker 종료/회수·출력 oracle 확인과 기업 정책은 별도 조건이다. 합성 runner 회귀는 EXAMPLE이며 전체 A39개·A-1 19개 완료로 집계하지 않는다.
