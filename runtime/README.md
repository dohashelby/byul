# serving-runtime

서빙엔진(vLLM·SGLang) 앞에서 추론 요청을 정책대로 허용·거부하고, 그 결과와 엔진·통신·파일 관측을 기록해 조회 API로 내보내는 런타임 통제 모듈입니다. 화면은 byul `platform/`이 맡습니다.

- 기준 문서: 개발계획서 v1.4(개발 기준 설계안). 연동 규격·정식 환경 등 남은 결정이 있습니다.
- 언어: Python 3.12, 표준 라이브러리만 사용(`dependencies = []`).
- 상세 설명·옵션·한계는 [`docs/상세_안내.md`](docs/상세_안내.md), 인수 담당 실행 순서는 [`docs/인계_안내.md`](docs/인계_안내.md)에 있습니다.

정책 묶음 명령(`cd runtime`에서 실행):

```text
.venv/bin/python -B -m serving_runtime bundle build --policy POLICY.json --policy-sha256 SHA256 --server-values SERVER.json --intent INTENT.json --output-parent DIR --bundle-id ID
.venv/bin/python -B -m serving_runtime bundle verify --bundle-dir DIR/ID
sudo -E env KUBECONFIG="$HOME/.kube/config" PATH="$PATH" .venv/bin/python -B -m serving_runtime bundle apply --bundle-dir DIR/ID --bundle-root ROOT --context CONTEXT --namespace NAMESPACE --kubeconfig "$HOME/.kube/config" --status-url http://127.0.0.1:8080 --token-env-file "$HOME/runtime-acceptance.env" --dry-run
sudo -E env KUBECONFIG="$HOME/.kube/config" PATH="$PATH" .venv/bin/python -B -m serving_runtime bundle apply --bundle-dir DIR/ID --bundle-root ROOT --context CONTEXT --namespace NAMESPACE --kubeconfig "$HOME/.kube/config" --status-url http://127.0.0.1:8080 --token-env-file "$HOME/runtime-acceptance.env"
sudo -E env KUBECONFIG="$HOME/.kube/config" PATH="$PATH" .venv/bin/python -B -m serving_runtime bundle apply --existing ID --bundle-root ROOT --context CONTEXT --namespace NAMESPACE --kubeconfig "$HOME/.kube/config" --status-url http://127.0.0.1:8080 --token-env-file "$HOME/runtime-acceptance.env" --dry-run
sudo -E env KUBECONFIG="$HOME/.kube/config" PATH="$PATH" .venv/bin/python -B -m serving_runtime bundle apply --existing ID --bundle-root ROOT --context CONTEXT --namespace NAMESPACE --kubeconfig "$HOME/.kube/config" --status-url http://127.0.0.1:8080 --token-env-file "$HOME/runtime-acceptance.env"
```

입력 형식·거부 규칙은 [`docs/정책묶음_변환규칙.md`](docs/정책묶음_변환규칙.md), 서버 적용과 수동 대체 절차는 [`deploy/gpu-lab/정책_수동적용_절차.md`](deploy/gpu-lab/정책_수동적용_절차.md)에 있습니다. `--dry-run`도 묶음 루트의 읽기 권한이 필요해 sudo로 실행합니다. `--existing ID`는 루트에 배치된 묶음을 재검증하고 복사 없이 전환합니다. apply는 rollout 뒤 연결 실패마다 새 임시 port-forward를 열고 viewer 토큰이 필요한 경우에만 사용합니다. 조회 불가이면 새 링크를 유지하며 수동 확인을 요구합니다. 생성 승인 목록은 자기 신고 사본이며 승인 검증이 아닙니다. 새 전환 경로는 로컬 가짜 환경에서만 시험했습니다.

Tetragon 보호 파일 읽기의 선택적 차단은 intent 1.1에서만 명시합니다. 예시의 경로는 가짜 파일이며 실제 적용 전 Pod 안의 파일 유형과 정상 사용 여부를 확인해야 합니다.

```json
{
  "tetragon_mode": "monitor",
  "protected_paths": {"secret_paths": ["/opt/serving/secret/read-probe.txt"]},
  "tetragon_rule_modes": {"TG-SECRET-01": "enforce"}
}
```

위 조각을 기존 1.1 intent의 해당 필드에 넣고 `.venv/bin/python -B -m serving_runtime enforce render --intent INTENT.json --out OUT_DIR`로 후보를 확인합니다. 출력 요약의 `차단 규칙 1개(미검증)`은 실제 차단 확인이 아닙니다. `tetragon_mode=enforce_probe`는 별도의 `TG-DENY-01` 시험 쓰기만 제어합니다. 새 intent는 기존 묶음에 수동 교체하지 않고 승인 원본과 일치시킨 뒤 `bundle build`·`bundle verify`를 다시 실행합니다. 첫 차단의 `argError`는 기존 L-05와 대조할 `-EPERM(-1)`입니다. 자세한 제약과 되돌리기는 [`docs/Tetragon_차단_설계.md`](docs/Tetragon_차단_설계.md)에 있습니다.

로컬 kind에서 가짜 파일 monitor→enforce→monitor를 시험할 때만 다음 opt-in 명령을 실행합니다. 이 작업에서는 실제 kind를 실행하지 않았습니다.

```bash
.venv/bin/python -B tools/k8s_lab_check.py --kube-context kind-runtime-lab --namespace serving-lab --state-dir ~/.local/state/serving-runtime/k8s-lab --secret-enforce-check
```

L-05b는 마지막에 monitor 정책을 다시 적용하고 같은 파일 읽기 성공·`Post`를 확인합니다. 전체 lab 정책도 정리하려면 `--cleanup`을 추가합니다. 보고서의 L-05b 단계별 errno, 같은 Pod UID·경로·read mask·규칙 ID의 원시 `Post`/`Override` 이벤트, 차단 구간 정상 요청 200을 대조합니다. 이벤트나 errno가 빠지면 확인 불가, 예상과 다른 차단 또는 정상 요청 실패는 실패입니다.

```text
클라이언트 → Gateway (인증·정책·context 상한 판정, 요청 ID 발급)
                │  허용만 중계
                ├→ 모의 엔진 (기본)  /  외부 vLLM  /  외부 SGLang(연결 준비)
                ↓
       요청 기록 · 엔진 상태/지표 · Hubble/Tetragon 관측 · 대상 추적
                ↓
       조회 API (GET /api/*) → byul platform 화면
```

## 현재 상태 (2026-11-02)

| 구분 | 확인 수준 |
| --- | --- |
| 요청 통제 Gateway (chat·completions, 스트리밍, 인증, 출력·context 상한, 필드 제한) | 모의 엔진, **실제 vLLM v0.31.0 CPU** |
| 대상 등록·정책 묶음·승인 목록 | 모의·가짜, 실제 vLLM CPU, 로컬 kind(Pod 교체) |
| Cilium 통신 제한 · Hubble · Tetragon 관측/선택 차단 | 로컬 kind(WSL) |
| SGLang 연결 | 가짜 SGLang만 |
| AWS GPU lab 배포·인수 시험(AWS-01~08) | AWS GPU dev에서 1회 통과. 정식(reference) 시험·속도 측정은 미실행이며, 기능 완성을 뜻하지 않음 |
| AWS-09~11 준비 | 실행·보호 파일 읽기·정상 동작 대조와 호스트 엔진 로그→단계 시간표 연결을 로컬 구현. 실제 AWS 단계 패턴·판정은 미확인. 허용 목록 확장 전 1차 관찰 필요 |
| 시험 | 로컬 unittest 554건 중 112건 환경상 skip·실패 0, 수용 시험 39개, CPU vLLM 점검 CPU-01~14 통과 |

**아직 안 한 것:** 정식 GPU 환경 시험(S3B·reference AWS-01~08)과 속도 측정, 실제 SGLang, logprobs, 운영 인증 연동(D-02)·계정/역할(D-03). 기능별 상세 확인 수준은 [`docs/지원_미지원_목록.md`](docs/지원_미지원_목록.md)에 있습니다.

AWS-09~11 실행 옵션은 `tools/aws_lab_acceptance.py --cases AWS-09,AWS-10,AWS-11`이며, 순서와 판정은 [`deploy/gpu-lab/ACCEPTANCE.md`](deploy/gpu-lab/ACCEPTANCE.md)에 적었다. 단계 위험·재생 결과는 [`docs/AWS_GPU_단계_사전분석.md`](docs/AWS_GPU_단계_사전분석.md)에 있다.

호스트 수집기는 현재 엔진 Pod UID의 `kubectl logs --timestamps`만 단계 시간표로 바꾸며, Gateway는 관측 폴더의 시간표를 읽는다. 수집 시각이 15초 오래되면 단계는 `unknown`·`phase_unknown`으로 돌아가고 요청은 계속 처리한다. 다른 Pod의 의심 경로 실행은 단계와 무관하게 `suspect_binary` 탐지다. 캐시 경로·허용 목록·예외는 엔진 selector에만 적용한다. 서버에서는 1차 단계별 실행 관찰 → 허용 목록 검토 → 2차 AWS-09~11 판정 순서를 지킨다.

## AWS dev 시험에서 겪은 문제

### 코드·설정을 고친 문제

| 문제 | 조치 |
| --- | --- |
| 엔진 요청 로그가 꺼져 AWS-01~03이 `unknown` | 통합 patch에 `--enable-log-requests`, `--enable-request-id-headers` 추가 |
| 실제 intent를 묶음에 못 넣음(묶음 검사는 `schema_version`, intent는 `schema`; 가짜 파일 시험으로 못 잡음) | `policy_bundle.py` 수정, 실제 형식 시험 추가 |
| 죽은 Pod까지 세어 재시작 뒤 `multiple_targets` 503 | `target.py`와 시험 도구 수정 |
| Gateway 응답 대기 5초로 상한값 요청을 504로 오판할 위험 | 60초로 조정 |
| GPU 이미지 실행 파일이 `/usr/bin/python3.12` | intent `phase_binaries`에 추가, 단계별 판정은 미확인 |

### 시험 전 검토에서 고친 판정

| 문제 | 조치 |
| --- | --- |
| AWS-03이 Gateway 기록만 믿음 | 엔진 로그와 상한값 대조 추가 |
| AWS-04·05가 연결 실패 이유를 안 봄 | 시간 초과와 Hubble `dropped`만 인정 |
| AWS-06이 권한 오류로도 통과 | `EROFS`만 인정 |
| AWS-07이 회복 뒤 실제 요청을 안 봄 | 정상 요청 200 필수 |
| AWS-08이 재시작 직후 1번만 요청 | 엔진 정보 회복 뒤 정해진 시간 안에서 재시도 |
| AWS-01이 stream을 안 봄 | stream 요청 추가 |

### 설치 중 겪은 문제

| 문제 | 해결법 | 후속 |
| --- | --- | --- |
| `01-host.sh`의 `sudo -v`가 이 Ubuntu 이미지에서 실패 | AWS 묶음 원본 대신 서버 사본에서 그 줄만 삭제하고 차이 보관 | 묶음 담당자에게 공유 필요 |
| 설치 중 자동 업데이트(`unattended-upgrades`)가 패키지 잠금을 잡음 | 끝난 뒤 이번 실행이 만든 `config.toml`을 옮기고 서버 사본에서 재실행 | 묶음 담당자에게 공유 필요 |
| 서버 재시작 때 GPU 플러그인이 늦어 엔진 Pod 하나 실패 | 자동으로 새 Pod가 뜸, 실패 Pod는 제외하도록 코드 수정 | 문서에 절차 추가 |
| 후보 규칙 `runtime-receiver-fixture-ingress`가 AWS-05와 충돌 | 적용에서 제외 | 문서에 기록 |

### 실행 중 실수

| 실수 | 영향 | 조치 |
| --- | --- | --- |
| `pkill` 패턴이 원격 셸까지 끊어 증거 복사 실패, 그대로 Stop | 보고서를 다음 실행 때까지 못 가져옴 | 다음 실행 때 회수, 파일 확인 후 Stop으로 절차 변경 |
| 복사 명령 출력이 복사본에 섞임 | 복사본 생성 실패 | 다시 복사하고 건수 확인 |
| 기본 정책 자동 복원이 경로 문제로 실패 | 기본 정책이 약 1분 빠짐, runtime 규칙은 적용 상태 | 바로 수동 복원, 원본과 일치 확인 |
| 서버 켜기 전 엔진 로그 옵션 대조 안 함 | 켠 뒤 발견해 수정 때문에 Stop | 수정 후 재실행 |

상세 근거: [`deploy/gpu-lab/ACCEPTANCE.md`](deploy/gpu-lab/ACCEPTANCE.md).

## 빠른 시작

`runtime/` 폴더에서 실행합니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .

# 시험
.venv/bin/python -B -m unittest discover -s tests
.venv/bin/python -B -m serving_runtime mvp-acceptance

# 모의 엔진으로 Gateway 실행 (:8080, 모의 엔진 :8088)
.venv/bin/python -m serving_runtime mvp
```

화면은 byul 루트에서 `platform/scripts/run-with-runtime.sh`로 runtime(모의 엔진)과 platform(:8700)을 함께 띄웁니다. 다른 Gateway를 보려면 platform을 `RUNTIME_BASE_URL=<Gateway 주소>`로 실행하고, 조회 보호를 켰다면 `RUNTIME_VIEWER_TOKEN`도 넘깁니다. platform은 runtime 주소를 하나만 받습니다.

## 실행 환경별 안내

| 환경 | 위치 |
| --- | --- |
| 실제 vLLM v0.31.0 (CPU, 이 PC) 점검 | [`deploy/cpu-lab/README.md`](deploy/cpu-lab/README.md) · `tools/cpu_vllm_check.py` |
| 로컬 kind lab (Cilium·Hubble·Tetragon) | [`docs/상세_안내.md`](docs/상세_안내.md) "통신·행위 통제 준비" · `tools/k8s_lab_check.py` |
| AWS GPU lab 배포·인수 시험 | [`deploy/gpu-lab/README.md`](deploy/gpu-lab/README.md) · [`deploy/gpu-lab/ACCEPTANCE.md`](deploy/gpu-lab/ACCEPTANCE.md) · `tools/aws_lab_acceptance.py` |

## 문서

| 문서 | 내용 |
| --- | --- |
| [`docs/상세_안내.md`](docs/상세_안내.md) | 기능별 설명·CLI 옵션·응답 코드·기록 형식·한계 (이전 README 전체) |
| [`docs/인계_안내.md`](docs/인계_안내.md) | 인수 담당 직접 실행 순서·기본 복원·체크리스트 |
| [`docs/지원_미지원_목록.md`](docs/지원_미지원_목록.md) | 기능·엔진별 확인 수준 |
| [`docs/인수시험_대응표.md`](docs/인수시험_대응표.md) | 계획서 시험 항목과 현재 근거 연결 |
| [`docs/runtime_규칙목록.md`](docs/runtime_규칙목록.md) · [`docs/내부_기록형식.md`](docs/내부_기록형식.md) | 사유 코드·탐지 규칙, 기록 형식 |
| `docs/결정기록_*.md` | 인증 연동, 엔진 UID, 정책 묶음 수신 결정 |
| [`project-context/STATUS.md`](project-context/STATUS.md) | 작업 이력과 미확인 사항 |

## 협업·검수

사용자 요청을 Claude가 정리하고 Codex가 구현·시험한 뒤, Claude가 검수해 최종 보고합니다. Codex 사용 한도 중에 Claude가 구현한 부분은 STATUS에 "Codex 교차 검수 전"으로 표시합니다.
