# Kubernetes 대표 연결: REQ-10 API 표본

기존 `sampling_validation`의 정책·16개 요청(옵션3개일 때)·관측·rule_table 판정을 재사용하는 전송 구현이다. `[executors] sampling_validation = "kubernetes"`를 명시해야 선택된다. 기존 `real` Docker 구현·CheckDef·팩·판정 의미·스트림 시간 측정은 바꾸지 않는다. 새 점검의 자동화 완료로 집계하지 않는다.

제품 경로는 기존 `guard select → run → judge → report --json → verify`다. 기존 platform의 RPT 변환 경계를 유지한다. 이 묶음의 회귀는 EXAMPLE이며 실제 Kubernetes·GPU 실증과 구분한다. API 제약 거부와 정상 응답이 모두 확인돼도 runner 소비를 증명하지 않아 보안 UNKNOWN은 유지된다. 기능 PASS는 기존 표본 범위이며, 잘못된 옵션의 API 수용만으로 보안 FAIL을 만들지 않는다.

## 명시적 대상과 사전 준비

승인된 시험 환경의 기존 Python 프로브 Pod와 엔진 Pod를 명시적으로 지정한다. 이 구현은 Pod·namespace·label·정책·엔진을 생성하거나 변경·삭제하지 않고, 선택한 namespace/Pod 조회와 프로브 컨테이너의 `python3 -c` 실행만 한다. Docker·로컬 vLLM을 기동하지 않는다. 읽기와 `pods/exec`가 가능한 기존 kubeconfig를 사용하며 새 권한·자격 증명을 만들지 않는다.

`sampling_validation` 설정은 기존 `constraint_source`, `constraint_engine_version`, `max_output_tokens`, `max_total_requests`, `max_probe_tokens`, `max_duration_seconds`, `request_timeout`, `target`을 그대로 쓴다. `kubectl_binary`는 선택적인 기존 kubectl 경로다. 기존 roles와 키 환경변수 이름은 사용할 수 있으나 알 수 없는 설정과 `allow_mutating_routes=true`는 원문을 표시하지 않고 거부한다. 전체 제한 시간은 조회·전송·전후 확인에 공통 적용된다.

대상 JSON의 필수 구조는 다음과 같다. 다음 대문자 값은 양식 자리 표시자이며 실제 입력으로 사용할 수 없다. 실측 UID·digest·시각을 채우고 실제 환경에서만 `lab=false`를 지정한다. 키는 파일에 넣지 않는다.

```json
{
  "lab": false,
  "topology": "kubernetes-owned-direct",
  "engine_kind": "vllm",
  "origins": {"trusted": {"network": "kubernetes-probe"}},
  "engine": {"trusted_url": "http://ENGINE_POD_IPV4:8000", "image_digest": "ENGINE_REPOSITORY@sha256:DIGEST"},
  "kubernetes": {
    "context": "EXPLICIT_CONTEXT",
    "run_id": "guard-owned-trial",
    "engine": {
      "namespace": "engine", "namespace_uid": "UUID", "pod": "EXACT_POD", "pod_uid": "UUID",
      "container": "engine", "container_id": "containerd://HEX64",
      "image": "ENGINE_REPOSITORY@sha256:DIGEST", "image_id": "EXACT_RUNTIME_IMAGE_ID",
      "restart_count": 0, "node": "EXACT_NODE", "pod_ip": "ENGINE_POD_IPV4", "started_at": "RFC3339"
    },
    "probe": {
      "namespace": "probe", "namespace_uid": "UUID", "pod": "EXACT_POD", "pod_uid": "UUID",
      "container": "probe", "container_id": "containerd://HEX64",
      "image": "PYTHON_REPOSITORY@sha256:DIGEST", "image_id": "EXACT_RUNTIME_IMAGE_ID",
      "restart_count": 0, "node": "EXACT_NODE", "pod_ip": "PROBE_POD_IPV4", "started_at": "RFC3339"
    }
  }
}
```

양쪽 Pod의 `guard.run` label도 명시한 run_id와 일치해야 한다. label은 소유 시험 대상 선택을 보조하며 승인 서명이나 UID 대체물이 아니다. 기존 타인 Pod에 label을 붙이거나 권한을 확대해서 통과시키지 않는다. 인계의 curl 전용 Pod는 Python 프로브를 대신할 수 없으며, 실제 시험 전에 고정 digest의 소유 Python 프로브를 준비해야 한다. 이 경로는 containerd·IPv4·HTTP 직접 Pod IP·`GET /health`·`POST /v1/completions`만 지원한다. Gateway/Service/DNS/TLS·호스트 namespace 공유·다른 런타임은 지원 범위 밖이다.

## 결합과 저장

모든 kubectl 호출에 context를 명시한다. 요청 전후 namespace UID, Pod UID, containerID, imageID, restartCount, startedAt, node, Pod IP, Pod/컨테이너 Ready, 종료 여부, 고정 spec image, guard.run을 대조한다. 엔진 spec image는 대상과 ENV image_digest에도 일치해야 한다. 런타임 imageID는 관측된 정확한 값으로 고정하지만 manifest index와 플랫폼별 image의 계보는 증명하지 않는다. ENV 모델 ID는 기존 응답 구조 검증에 쓰며 GPU·driver·worker 실행 사실을 새로 주장하지 않는다.

응답은 기존 구조 검증에만 일시 사용한다. 저장 첨부는 원문 요청·응답·토큰·Secret·Pod 원문·stderr·예외 문구를 포함하지 않는다. 요청 제어 fixture는 canonical JSON SHA로 남기고, 전송한 Python 소스 문자열의 UTF-8 SHA와 명시한 신원·요청별 전후 결합 결과·고정 진단만 남긴다. 이는 파일 bytes SHA와 구분된다. 자격 증명은 기존 stdin 경로로만 전달하며 argv에 넣지 않는다. 부모 stdout/stderr·stdin·전체 실행 시간은 제한한다. 원격 Python 프로브도 요청별 watchdog으로 자신만 종료하므로 kubectl 종료 후 무기한 실행을 남기지 않는다. 전송 지연을 포함한 완료 시각은 부모의 전후 확인과 전체 제한 시간으로 다시 확인한다.

누락·접속 실패·다른 세대·불완전 출력은 고정 진단과 UNKNOWN으로 남긴다. 전후 신원이 확인되지 않은 응답을 정상/수용 관측으로 쓰지 않는다. 뒤의 세대 변경이 앞에서 이미 결합 확인된 관측을 지우지 않는다. `request_dispatch_attempts`는 요청 함수 호출 수이며 실제 HTTP 도달 수가 아니다. 실제 환경의 관측 출처와 ENV/POL의 기존 출처 상한은 코어가 적용한다. 주입 대역은 EXAMPLE이다.

Kubernetes Pod 교체와 컨테이너 재시작은 다른 세대다. [공식 Pod 수명주기](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)와 [kubectl exec](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_exec/)의 명시 컨테이너·stdin 경계를 따른다.

## AWS 재개 조건

us-east-1의 G/VT On-Demand quota는 마지막 확인0 vCPU이며, 기존 Support 사례에 4→8 vCPU 정정 회신을 보냈다. 접수는 증액 승인이 아니다. 실제 적용·AMI·설치 고정 판본·프로브 준비·비용 계획을 확인한 뒤 한 대에서 환경 인수와 대표 제품 경로를 실행한다. 현재 이 코드의 Kubernetes·GPU 실증은0이다.

사용자 한도 $69보다 콘솔 예상 가용액이 작아 계획 상한을 $46.66 + 예비비 $9로 낮췄다. EC2 외 EBS·IPv4·전송·세금·미반영 사용료를 포함해야 하며 가격과 잔고는 기동 직전에 다시 확인한다. EKS/NAT/LB·두 번째 GPU·유휴 개발 서버는 만들지 않는다. AWS 인계 원본의 원문 응답 저장은 실제 실행 전 작업 사본에서 개인정보 보존 규칙에 맞춰 보수해야 하며, 이 묶음에서 원본이나 설치 판본을 바꾸지 않는다.
