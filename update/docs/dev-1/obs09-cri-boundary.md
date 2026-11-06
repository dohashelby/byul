# OBS-09 Kubernetes 로그 사건 경계 (개발 담당, 2026-11-06)

## 원 결과와 원인

2026-11-05 L4 G1의 OBS-09(`RES-01M6NW2896E59KM64C3NGZC5XJ@1`)는 `ARTIFACT_CAPTURE_INCOMPLETE`로 ABORTED, 세 축 UNKNOWN이었다.
7개 요청은 모두 200이었지만 multiline·crlf 두 사례의 정확한 사건을 찾지 못했다(도달: before·cr·reserved·html·after). 원 EVD/RES는 그대로 둔다.

- vLLM `NewLineFormatter`는 메시지 안의 LF를 `CR LF + 같은 native 접두사`로 바꾼다. 참조 parser는 줄 끝 CR을 "이어지는 줄" 표식으로 쓴다(Docker CLI 경로에서 검증).
- Kubernetes CRI 로그 계층(containerd)은 줄마다 LF 앞의 CR 하나를 지운다. `kubectl logs`와 노드 로그 파일이 같다(k3d 실측, `byul-dev1-evidence/20261106-obs09-cri-diagnostic/`).
- 그래서 multiline 사례는 두 개의 독립 사건(둘째 줄도 진짜 접두사)으로, crlf 사례는 CR을 잃은 메시지로 읽혀 정확 사건이 0이 됐다.

## 고친 것

- `KubernetesHTTP.engine_attach`: 시험 동안 엔진 컨테이너 stdout을 `kubectl attach --quiet`(stdin·tty 없음)로 받는다. CRI 줄 처리 전 바이트다.
  시작한 kubectl 자식 하나만 끝내며, 상한(128 KiB)·시한·스스로 끊김을 수집 불완전으로 남긴다.
- `kubernetes_log_integrity.align_attach`: CRI 창의 줄(시각 포함)과 attach 줄을 맞춘다.
  attach 줄에서 끝 CR 하나를 뗀 값이 CRI 줄과 모두 같고, 그런 연속 위치가 정확히 한 곳일 때만 "CRI 시각 + attach 바이트"로 잇는다.
  맞추지 못하거나 attach가 불완전하면 CRI 줄만 두고 `source_complete=false`(UNKNOWN)다. 경계를 추정하지 않는다.
- 파서·분석 계약·판정 규칙·정책은 바꾸지 않았다. 실행기 판본 `0.3.1+k8s.2`. EVD 요약 `kubernetes_source`에 CRI·attach 해시와 정렬 진단을 남긴다.
- 정렬은 stdout만 본다. 창 안에 엔진 stderr 줄이 있으면 정렬이 실패해 UNKNOWN이 된다(기존 stderr 분리 의미와 같다).

## 확인

- 직접 시험: CRI 대역(실제처럼 CR 제거)만으로는 11-05와 같은 도달 목록·UNKNOWN을 재현하고, attach 정렬로 7개 사례 PASS, 덮지 못한 attach·불완전 attach는 PASS가 아니다. 정렬 규칙과 실제 subprocess의 AttachStream 정지·잘림을 따로 시험한다.
- k3d 진단(증거 아님, arm64 이미지는 검토 계약 밖): 실제 vLLM 엔진에서 CRI만으로는 정확 사건 multiline0·crlf0, 정렬 뒤 다섯 사례 모두 1.

## GPU 재실측

G1 묶음에서 같은 OBS-09 명령을 다시 실행한다. lab kubeconfig에 `pods/attach` create가 필요하다(k3d 관리자 컨텍스트는 허용). 판정은 실측 결과대로 둔다.

## 열린 질문

CRI 로그를 줄 단위로 읽는 기업 수집기(노드 로그 파일 tail 등)는 입력 LF를 진짜 접두사를 가진 별도 사건으로 볼 수 있다(k3d CRI만 읽기에서 사건 12 대 10).
참조 수집기의 경계 복원은 이 기업 수집기의 안전을 뜻하지 않는다. 기업 수집 경로를 OBS-09 대상으로 넣을지는 기업 수집기 입력과 함께 정한다.
