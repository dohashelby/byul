# 개발 담당 26개 2단계 GPU 실행 묶음 (묶음 7, 2026-11-05)

사용자 결정(DECISIONS 2026-11-04)에 따라 개발 담당 점검은 2단계 GPU lab에서 GPU 서빙 이미지로 한 번에 실측한다. 이 문서는 그 실행 묶음이다. 1단계의 k3d CPU 결과는 경로 진단이며 실측 수에 넣지 않는다.

## 1. 전제

| 항목 | 값 | 확인 방법 |
|---|---|---|
| 환경 | AWS L4 단일 GPU, Linux amd64, K3s, NVIDIA device plugin | `kubectl get node -o jsonpath='{..allocatable.nvidia\.com/gpu}'` = 1 |
| 엔진 이미지 | `vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b` (0.31.0, linux/amd64; `docs/gpu-a/ENVIRONMENT.md`) | Pod `imageID` |
| Gateway 이미지 | runtime `deploy/gateway/Dockerfile` 빌드. 1단계 k3d에서 쓴 `serving-gateway@sha256:5e79946d…`는 arm64라 다시 빌드한다 | 레지스트리 digest |
| 모델 | `facebook/opt-125m@27dcfa74…`와 guard chat template(1단계와 같음) | `env` 하위 명령의 inventory |
| 도구 | `tools/dev1_k3d_cpu_lab.py --profile gpu …`. 이름공간 `byul-dev1-gpu-lab`, 세대 `gpu-l4`, 별칭 `byul-dev1-lab`. 같은 run의 모든 하위 명령에 `--profile gpu`를 준다 | 렌더 기록 `profile: gpu` |
| ENV 출처 | GPU 이름·드라이버는 운영자가 넘기므로 ENV는 DECLARED다. 정책은 모두 EXAMPLE/DRAFT, `confirmed_by` 비움 | ENV `data_origin` |

## 2. 실행 전에 한 번만 하는 대조 (기동 0)

1. **native lifecycle 센서 소스**: 1단계처럼 고정 모듈 26개의 SHA를 GPU 이미지 설치 파일과 대조한다. 다르면 REQ-02 native·PLT-10은 실행하지 않고 원인을 기록한다.
   ```bash
   kubectl -n byul-dev1-gpu-lab exec -i <engine-pod> -c engine -- sha256sum -c < paths.txt
   ```
   `paths.txt`는 `prepare-lifecycle-sensor`의 `manifest.json` `source_sha256`에서 만든다. 형식은 `<sha>  /usr/local/lib/python3.12/dist-packages/<모듈 경로>.py`이고, 설치 경로는 이미지에서 먼저 확인한다.
2. **OBS-09 로그 계약**: GPU 이미지의 `vllm/logger.py`·`vllm/logging_utils/formatter.py` SHA가 검토된 값(`4e26afd9…`, `cc2153d8…`)과 같은지 본다. 같으면 GPU 이미지 digest를 `VllmAdapter.native_log_contract` 검토 목록에 더하는 커밋을 먼저 한다(개발 담당 공통 파일). 다르면 OBS-09는 UNKNOWN으로 두고 실행하지 않는다.
3. **REQ-11 소비 소스 계약**: 같은 이미지에서 `tools.verify_req10_consumption`이 보는 고정 소스와 관리 템플릿 신원이 지원되는지 확인한다.

## 3. 기동 묶음 (단일 GPU)

기동 한 번에 같은 엔진 설정을 쓰는 점검을 묶는다. 세션을 봉인하는 센서 점검은 기동마다 하나다.

| 기동 | render 옵션 | 점검 | 하위 명령(1단계와 같음) |
|---|---|---|---|
| G1 기본 + Gateway 4-Pod | 없음, `gateway --chat-temperature` | REQ-01(격리, 이어서 `engine-ingress` 삭제로 열림), REQ-03, REQ-11, REQ-13(엔진+Gateway), OBS-09(2번 통과 시), OBS-06, PLT-07(수신기 기동 직후) | `http-check`, `route-target`, `record-access`, `egress` |
| G2 센서 + Gateway | `--lifecycle` | REQ-02(Gateway 기록 + native 복합) | `route-record --prepared` |
| G3 센서 + API 키 | `--lifecycle --api-key-secret` | PLT-10 | `revocation` |
| G4 usage 금지 | `--usage forbidden` | OBS-04 | `usage --check OBS-04` |
| G5 usage 허용 | `--usage allowed` | OBS-05(1단계 CPU에서는 warm-up 정지. GPU에서 재현되면 엔진 문제로 기록) | `usage --check OBS-05` |
| G6~ engine_sensor | PR #215 인계 | REQ-18(V2), REQ-19(`--lab-kernel-checks`), REQ-20(graph 켬·끔 두 세대), LIF-13(daemon 자산), REQ-15(Gateway salt) | `tools/gpu_a_run_inputs.py check-inputs`, `tools/b_req18*`·`b_req20*`·`b_lif13*` |
| TP2 (새 승인 필요) | 2장 eager, REQ-21은 `--restart-always` | REQ-14, REQ-21(사례: before → switch engine_restart → after) | PR #215 인계 |

순서: G1 → G4 → G5 → G2 → G3 → G6~. 앞 묶음은 센서가 없어 세션 봉인 문제가 없다.

각 점검의 완료 기준은 GPU 담당과 같다: 선정 → 실행 → EVD → RES → RPT → 반출 → 복원 → 저장 팩 재판정. 관측하지 못한 분기는 원인과 함께 UNKNOWN으로 남긴다.

## 4. 2단계에서도 실행하지 않는 것

| 점검 | 이유 | 필요한 것 |
|---|---|---|
| REQ-06 | 적용 제외(사용자 결정). runtime Gateway가 `/v1/responses`를 열지 않음 | Gateway가 열면 다시 적용 |
| REQ-04 | 적용 제외(사용자 결정 2026-11-05). runtime Gateway가 신원 header를 넘기지 않음 | Gateway가 신원을 전달해 인가에 쓰면 다시 적용 |
| OBS-01 | (실행함) G1에서 파생 DRAFT 팩 `PACK-DEV1-GATEWAY-OBS01@1`과 `obs01` 하위 명령으로 실행 | 정본 팩 개정은 현황 정리에서 |
| PLT-11, PLT-12, OBS-11, OBS-06 사본 목록, REQ-03 관리 경로, REQ-17 한도 | 기업·runtime 입력 | `docs/phase1/dev-1.yaml` 메모의 입력 |
| REQ-16 | 판정 기준 확정(2026-11-05). 멀티모달 모델 선정·내려받기 승인 대기 | 승인 뒤 G6에 추가 |

## 5. 비용 추정

| 구성 | 단가 근거 | 시간 | 추정 |
|---|---|---|---|
| g6.2xlarge(L4 1장) On-Demand | $1.20208/h(`docs/gpu-a/COST_LEDGER.md`, 현행 청구 단가 미확인) | 준비 30분 + G1~G5 각 15분 + G6~ 60분 ≈ 2시간 45분 | 약 $3.3 (EBS 제외) |
| TP2(L4 2장 이상) | 단가 미확인 | 1시간 | 새 승인 때 제시 |

1단계 작업 지시서 기준 GPU 잔여 예산은 $56.68이다. 보존 EBS 비용(약 $0.056/h)은 별도다.

## 6. 1단계 k3d에서 미리 고친 것 (2단계 실행이 의존함)

- Kubernetes 경로 전송의 64개 초과 묶음 분할(REQ-01 열린 경우).
- `--middleware`는 `모듈.객체` 형식(vLLM 0.31).
- 엔진 Pod만 고르는 수집 target(OBS-06).
- Gateway 정책 1.2 스키마와 bundle 항목 스키마 일치(REQ-13 Gateway).
- usage 정상 대조 식별자 별칭(OBS-04·05).
- #196 공통 입력 계약(`docs/dev-1/gpu-common-inputs-196.md`). 생산자는 2단계에 만든다.

## GPU 실행 준비 보수(2026-11-05)

- `dev1_k3d_cpu_lab`의 eager 선언을 실제 `--enforce-eager` 인자로 고정하고 렌더 기록은 실제 인자를 따른다. 과거 CPU 원자료는 바꾸지 않는다. REQ-20 graph on/off 대조는 별도 세대다.
- lifecycle 센서를 붙일 때 이미 더한 GPU 메모리 비율 등 기동 옵션을 유지한다. 이전 구현은 기본 ARGS로 다시 덮어써 GPU의 `--gpu-memory-utilization 0.5`가 빠졌다.
- 렌더·입력·불변식 묶음 170 통과/기존1제외(pytest 1.59초, 부모 1.758초). 새 GPU 실측에는 실제 이미지 digest·소스 pin·최종 렌더 bytes를 다시 묶는다. 최근 보존된 GPU 생산자 세대는 `c1c9f6fd…`이며 위 계획서의 `a4a4c043…`와 같은 이미지로 간주하지 않는다.
