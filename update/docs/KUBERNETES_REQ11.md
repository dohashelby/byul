# Kubernetes REQ-11 제품 연결과 실행 준비

인터페이스는 `A1-kubernetes-req11/1`이며 기준은 A-1 fcb6c40와 A-2 c8f0e61의 확정7파일이다. 공통 작성자는 A-1이다. 기존 logical executor `request_binding`의 전송 구현을 `[executors] request_binding="kubernetes"`로 선택한다. 기존 요청8분기·분석기·rule_table·security UNKNOWN/독립 FAIL·저장 Ref 의미를 유지한다. 새 가짜 실행기나 CPU 전용 제품 경로는 없다.

## 선택·결합·전송

기존 REQ-10 `KubernetesHTTP`의 대상 선택/조회/pipe 경계를 재사용한다. 기존 sampling 프로파일과 pinned v1 HTTP 소스·지원 경로는 유지한다. 새 `kubernetes-owned-request-binding-http-v1`은 표준 `stream_probe.py` 원본을 고정 SHA로 확인해 소유 Python 프로브 Pod에서 실행한다. 기존 JSON/SSE 수집·크기·개별 연결·프레임·timeout·시간 계산을 복제하거나 바꾸지 않는다.

지원은 명시 context의 containerd·IPv4·직접 엔진 Pod HTTP, `POST /tokenize` JSON과 `POST /v1/chat/completions` JSON/SSE, batch당 최대2연결이다. Gateway·Service·DNS·TLS·다른 runtime·일반 endpoint나 배포 생성은 지원하지 않는다. body·키는 stdin에만 전달하며 raw 응답·token·headers·stderr·Pod spec·argv는 저장하지 않는다. 기존 hash/parse projection과 선택 신원·요청별 결합 결과·고정 진단만 기존 EVD 첨부에 저장한다.

target은 기존 `docs/KUBERNETES_SAMPLING.md`의 engine/probe 선택 구조를 그대로 사용하며 다음을 더한다.

```json
{
  "schema": "guard-kubernetes-request-binding-target/1",
  "environment_ref": {"kind":"ENV","id":"EXACT_ENV_ID","revision":1,"sha256":"EXACT_ENV_SHA256"},
  "lab": false,
  "topology": "kubernetes-owned-direct",
  "observed_version": "EXACT_INSTALLED_VERSION",
  "model_mapping": {"repository_id":"EXACT_ENV_MODEL_ID","revision":"EXACT_ENV_MODEL_REVISION","api_model":"EXPLICIT_API_MODEL_NAME"},
  "engine": {"trusted_url":"http://EXACT_ENGINE_POD_IPV4:8000","image_digest":"REPOSITORY@sha256:DIGEST","command":["EXACT","DECLARED","COMMAND"]},
  "origins":{"trusted":{"network":"kubernetes-probe"}},
  "comparison_config":{"launch":{"profile":"cpu-bounded-v1","argv":["EXACT_LAUNCH_ARGV"],"max_model_len":128,"chat_template":{"profile":"guard-text-chat-v1","version":"1","sha256":"EXACT_FIXED_TEMPLATE_SHA","container_path":"/guard-assets/chat/guard-text-chat-v1.jinja","content_format":"string","request_overrides":false}}},
  "kubernetes":{"context":"EXPLICIT_CONTEXT","run_id":"owned-run","engine":{},"probe":{}}
}
```

이 문서의 대문자와 빈 engine/probe 매핑은 자리 표시자이며 실행 입력이 아니다. 각 역할의 필수 필드는 namespace·namespace_uid·pod·pod_uid·container·container_id·image·image_id·restart_count·node·pod_ip·started_at이다. 소유 run은 양쪽 Pod의 guard.run label과 일치해야 한다. 정확 ENV seal/Ref와 image를 함께 확인하며 명시 UID·containerID·imageID·restart 정보를 다른 종류의 hash로 바꾸지 않는다. label·context 이름은 승인 서명이나 cluster/producer 신뢰 증명이 아니다.

모델 저장소 ID/revision은 ENV에 보존한다. API 모델명은 target.model_mapping에서 명시하고 요청/응답 검증에 전달한다. mapping의 repository_id/revision은 ENV와 같아야 한다. 이것은 선언된 매핑이며 실제 worker의 파일·tensor 소비 증명이 아니다. 저장 projection은 mapping SHA를 남긴다.

고정 template의 읽기는 기존 bounded `_READ_TEMPLATE` 소스를 재사용한다. 선택 engine Pod의 정확 command/args, readOnly file mount, 제한된 regular file bytes SHA를 전후 세대 확인과 묶는다. ConfigMap projected symlink 전체를 자동 지원하지 않으며 준비된 regular readOnly file mount가 필요하다. private snapshot hook을 분리했지만 Docker 기본 수집·판정은 동일하다.

## 기존 제품 CLI

단일 환경은 기존 `guard select → run → judge → report --json → verify`와 `evidence export/restore`를 사용한다. ENV/POL/target·config·tmp·DB·출력은 작업자/실행별로 분리한다. 실제 실험일 때만 lab=false를 사용하며 주입 runner는 언제나 EXAMPLE이다. 원문 데이터로 판정하지 않고 기존 입력 projection/독립 위반 근거를 사용한다. output 독립 oracle 부재로 전체 security PASS는 계속 제한한다.

두 환경은 기존 `guard update --baseline ... --candidate ... --policy ... --config ... --output ... --store ...`를 사용한다. 양쪽 request_binding 구현은 kubernetes이고 target 파일·Pod UID·containerID는 달라야 한다. `[update] mode="external"`이며 Kubernetes REQ11 전환 gate를 끌 수 없다. `transition_timeout`(최대300초), `transition_poll_interval`(0.01~5초)은 기술 대기 한도다. 기존 managed Docker 경로는 변경하지 않는다.

gate는 기준판 Ready 확인 → 기준판 실행 → 기준판 API 종료/부재 대기 → 같은 세대의 후보판 Ready 대기 → 후보판 실행 순서다. namespace UID·소유 run·Pod UID·containerID·imageID·시작/재시작 정보가 맞지 않으면 후보 요청을 중단하고 ABORTED EVD·UNKNOWN을 남긴다. 앞서 남은 기준판 EVD·RES·성공 첨부·독립 FAIL은 보존한다. 전환 진단은 EVD와 분리된 CAS 자료로 보존하고 RPT limitations에 hash를 남긴다. 진단 저장 실패도 기존 EVD를 삭제하지 않는다.

생성·삭제·배포 변경·rollback은 operator가 수행한다. 후보는 실제 containerID/started_at가 확보된 Running 세대에서 선택한다. 아직 신원이 없는 Pending Pod를 미래 값으로 채워 선택하지 않는다. 교체·restart·종료 대기 실패는 recovery required이며 자동으로 다른 Pod나 최신 revision을 고르지 않는다. API의 terminated/absence 및 Ready는 node-side 작업 종료나 GPU 반납 증명이 아니다. 직접 `guard run`은 선택한 환경의 요청/증거 경로이며 이 전환 조율은 `guard update` 경로의 범위다.

`prepare-update`는 이 경로에서 Docker daemon을 요구하거나 시작하지 않고 정적 target/ENV/정책/프로파일을 확인한다. 실제 cluster·readiness·worker/GPU를 검증했다고 표시하지 않는다. 같은 이미지 두 개로 하는 CPU 전환 회귀는 두 이미지 업데이트 실증이 아니며 기존 image-pair 준비 gate를 유지한다.

## 오류·누락·정리

잘못된 선언은 원격 호출 전 고정 configuration/binding 진단으로 거부된다. 실제 응답 미수집·timeout·불완전 framing은 기존 UNKNOWN/gap이다. 뒤의 query/세대/collector 오류는 이미 전후 결합된 이전 packet의 독립 FAIL을 지우지 않는다. 첨부 실패는 ABORTED와 저장된 관측·성공 Ref를 함께 보존한다. 부모 pipe/child 회수 미확인은 `KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED`로 분리한다. remote probe는 고정 watchdog으로 자기 Python 프로세스만 종료하며 Pod를 삭제하지 않는다. 이를 Pod/GPU 정리 실증으로 확대하지 않는다.


## 기준판 종료 후 후보 신원 선택

기본 kubernetes_candidate_binding="prebound"는 이미 Running 세대가 확정된 양쪽 target을 사용한다. 단일 GPU에서 후보가 Pending인 순차 배포는 [update] kubernetes_candidate_binding="after_baseline"을 명시한다. candidate target 경로는 다른 파일의 새 경로이며 시작 시 없어야 한다. ENV·이미지·정책은 기존과 같이 먼저 준비하고, Pod UID/containerID/imageID·실제 started_at/restart를 미래 값으로 채우지 않는다.

gate가 기준판 API 종료/부재를 확인한 다음 해당 경로의 완전한 기존 target JSON을 기다린다. operator가 후보를 배포하고 Running 세대를 얻은 뒤 atomic rename 등으로 이 소유 입력 파일을 완성한다. 제품은 파일·Pod·배포를 생성하거나 변경하지 않는다. 아직 파일이 없으면 WAITING_CANDIDATE_BINDING, 불완전 JSON·다른 ENV/모델/이미지·잘못된 context·재사용한 기준판 신원은 RECOVERY_REQUIRED로 후보 접수를 차단한다. 이후에도 exact 세대와 Ready를 확인하며 자동 discovery나 다른 Pod fallback은 없다.

transition_timeout은 기준판 요청 실행이 끝난 뒤 시작하는 API 종료·후보 입력/Ready 대기의 공통 한도다. 스트림 시간 계산이나 요청 timeout을 바꾸지 않는다. 준비 단계는 이 모드에서 후보의 신원을 아직 검증하지 않았다고 구분한다. 선언 미확보를 가짜 ID나 CPU 자료의 MEASURED로 채우지 않는다.

선택한 target의 정규 SHA를 공통 registry의 private 실행 입력으로 넘기고 실행기에서 다시 비교한다. 대기 이후 target 파일이 바뀌면 원격 요청 전에 ABORTED로 남긴다. 경로/원문 target을 새 증거 형식으로 복사하거나 hash를 Pod/image 신원으로 바꾸지 않는다. 앞서 저장한 기준판 EVD·RES·성공 첨부·독립 FAIL을 보존한다. 이 CPU 회귀는 실제 scheduler의 GPU 할당/반납·worker 정리·rollback 실증이 아니다.

## CPU 검증과 실제 실행 재개

소유 kubectl/stdin 인터페이스 runner 대역으로 실제 파싱·source/세대·실행기·Store/판정/RPT·전환을 검증하고, 소유 loopback HTTP에서 동일 stream_probe 원본도 실행한다. A-2 c8f0e61의 exact7파일과 기존 플랫폼 교차시험의 ENV Ref 입력 보수를 통합하며 플랫폼 제품 코드/공용 계약은 유지한다. 2026-10-28 로컬 kind-bob-guard-lab API는 연결 거부 상태였고, 이 작업은 daemon/cluster를 기동·수정하지 않았다. 따라서 실제 Kubernetes와 GPU 실증은0이다.

AWS quota 승인은 환경 생성·GPU 시험의 완료가 아니다. 승인 후 실제 재개에는 비용/자원 승인 확인, exact 설치 image/source/ENV·모델 revision/inventory/API명, 소유 Python probe와 RBAC, namespace/Pod/container 세대·readOnly template·command/args 준비가 필요하다. 기존 CPU image/version 제한은 유지하며 추가로 검토한 단일 GPU amd64 manifest와 기동 선언은 [GPU REQ11 계약](GPU_REQ11.md)에 고정한다. 임의 GPU image를 넣거나 CPU digest 제한을 풀어 지원으로 처리하지 않는다. 검토하지 않은 설치판이면 요청 없이 UNKNOWN으로 남긴다.

한 실행 담당자가 원자료를 수집한다. 준비·정상/거부/미수집 → 기준판 종료 → 후보 Ready → 후보 실행 → EVD/RES/RPT/반출·재판정 → 실제 작업 종료/장치 사용·메모리·자원 반납 확인 → 잔존 자원/비용 확인 순서를 수행한다. 실패 시 후보 접수를 중단하고 exact 이전 증거를 보존한 채 operator가 복구한다. GPU 미실증, 실제 Kubernetes 미확인, 기업 정책 준비 전을 각각 구분한다. sources/와 AWS 인계 원본은 읽기 전용이다.
