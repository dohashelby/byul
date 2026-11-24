# BYUL 런타임

BYUL 런타임은 LLM 서빙 엔진으로 들어가는 추론 요청과 엔진 주변 통신을 제어하고 엔진 내부 행위를 관측합니다. 요청을 중계하기 전에 정책을 검사하고, 엔진과 노드에서 관찰한 이벤트와 차단 결과를 기록합니다. 운영자는 byul platform에서 이 기록을 조회할 수 있습니다.

## 1. 주요 기능

- **요청 제어** — Gateway가 인증과 요청 정책을 확인합니다. 모델, 입력 필드, 토큰·컨텍스트 크기, 동시 요청 한도를 검사하고 허용된 요청만 엔진에 전달합니다. 채팅과 텍스트 생성, 스트리밍을 지원하며 엔진의 응답 형식도 검사합니다.
- **통신 제한** — Cilium 정책을 적용해 엔진 Pod에 접근할 수 있는 주체와 엔진이 연결할 수 있는 목적지를 제한합니다. Hubble에서 통신 기록을 확인할 수 있습니다.
- **엔진 행위 관측** — Tetragon이 프로세스 실행과 파일 접근 이벤트를 수집합니다. 별도로 보호 파일 읽기 차단을 시험할 수 있는 선택형 규칙을 제공합니다.
- **정책 관리와 기록 조회** — 정책 묶음을 생성·검증·적용할 수 있습니다. 요청 판정, 엔진 상태, 관측 이벤트는 조회 API에서 확인할 수 있습니다.

## 2. 아키텍처

추론 요청은 **클라이언트 → Gateway → 서빙 엔진** 순서로 전달됩니다. Kubernetes 배포에서 Gateway는 엔진과 별도 Pod에서 실행됩니다. Cilium은 Pod 간 통신과 외부 연결을 제어하고, Tetragon은 엔진이 실행되는 노드에서 프로세스 실행과 파일 접근을 관측합니다.

| 구성요소 | 실행 위치 | 역할 |
| --- | --- | --- |
| Gateway | 엔진과 별도 Pod | 요청 검사·중계, 응답 검사, 요청 기록과 조회 API 제공 |
| 서빙 엔진 | 엔진 Pod | 모델 추론과 응답 생성 |
| Cilium · Hubble | Kubernetes 클러스터 | 네트워크 정책 적용과 통신 관측 |
| Tetragon | 엔진이 실행되는 노드 | 프로세스 실행과 파일 접근 관측 |
| byul platform | 런타임과 별도 서비스 | 조회 API에서 요청과 운영 기록을 읽어 화면에 표시 |

## 3. 빠른 시작

Python 3.12 이상이 필요합니다. 저장소 루트에서 다음 명령을 실행하면 모의 엔진과 Gateway가 로컬에서 시작됩니다. Gateway 주소는 `127.0.0.1:8080`입니다.

```bash
cd runtime
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m serving_runtime mvp
```

다른 터미널에서 정상 요청을 보냅니다. 응답은 HTTP 200이며, `X-Request-ID` 헤더로 요청을 추적할 수 있습니다.

```bash
curl -i -H 'Content-Type: application/json' \
  -d '{"model":"test-model","messages":[{"role":"user","content":"hi"}],"max_tokens":64}' \
  http://127.0.0.1:8080/v1/chat/completions
```

운영 화면까지 함께 띄우려면 저장소 루트에서 `platform/scripts/run-with-runtime.sh`를 실행하고 `http://127.0.0.1:8700`을 엽니다.

## 4. 파일 구조

아래 경로는 `runtime/` 기준입니다.

| 경로 | 내용 |
| --- | --- |
| `src/serving_runtime/gateway/` | 추론 요청 검사, 엔진 중계, 응답 처리 |
| `src/serving_runtime/`의 `policy/`, `config/`, `target/` | 정책과 묶음, 설정, 엔진 대상 관리 |
| `src/serving_runtime/`의 `enforcement/`, `observation/`, `operations/` | 규칙 생성, 이벤트 처리, 적용 상태 확인 |
| `src/serving_runtime/cli/` | 명령줄 진입점 |
| `deploy/` | 호스트 설치 및 CPU·GPU 랩 배포 |
| `tools/`, `labtools/` | 운영 도구와 실험·인수 도구 |
| `tests/`, `examples/`, `docs/` | 회귀 시험, 설정 예시, 상세 안내 |

## 5. 현재 상태

| 환경 | 확인한 범위 |
| --- | --- |
| 로컬 모의 엔진 | 요청 검사, 허용·거부 응답, 스트리밍과 기록 조회 |
| vLLM CPU · 로컬 kind | 실제 vLLM 요청 중계. 로컬 kind에서 통신 제한과 관측, 시험용 보호 파일의 읽기 차단을 확인했습니다. |
| AWS GPU 랩 · vLLM | 실제 엔진에서 요청 처리·통신 제한·관측을 시험했습니다. 환경별 절차와 확인 범위는 [인수 시험 절차](deploy/gpu-lab/ACCEPTANCE.md)에 있습니다. |
| AWS GPU 랩 · SGLang | 이전 판본에서 일부 요청·관측 시험을 통과했습니다. Tetragon 보호 파일 읽기 차단은 확인하지 못했습니다. |

별도 검증 브랜치에서는 단일 노드·단일 엔진으로 **정상 추론 → 지정 셸 실행 거부(EPERM) → 정상 추론**을 3회 확인했습니다. [셸 실행 차단 검증 기록](https://github.com/dohashelby/byul/blob/3d08eed991190ae2e10a626215627ebb27715f79/runtime/docs/verification/shell-exec-deny.md)

## 6. 남은 목표

- **요청·통신 정책 검증** — 고객 환경별 정상 요청과 통신 경로를 확인하고, 실제 워크로드에서 오차단과 우회 여부를 검증합니다.
- **관측과 재검증** — 행위를 일으킨 프로세스와 관련 요청을 더 정확히 연결하고, 수집이 끊긴 구간을 구분합니다. 엔진이나 정책이 바뀌면 같은 기준으로 다시 검증할 수 있도록 합니다.
- **운영 환경 검증** — 다중 엔진·노드에서의 적용과 성능 영향을 확인합니다.
- **장애 대응과 기록 보존** — 수집 중단, 정책 적용 실패, 기록 저장 실패 시 동작을 정하고 시험합니다. 내보낸 기록의 무결성을 검증하는 절차를 마련합니다.

## 정책 묶음 운영

<details>
<summary>생성·검증·적용·기존 묶음 전환 명령</summary>

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

</details>

## 상세 문서

- [인수 담당 실행 순서](docs/인계_안내.md) · [작업 이력](project-context/STATUS.md)
- [사용법과 설정](docs/상세_안내.md) · [기능별 지원 범위](docs/지원_미지원_목록.md)
- [정책 묶음 입력과 변환 규칙](docs/정책묶음_변환규칙.md) · [서버에서 정책 적용하기](deploy/gpu-lab/정책_수동적용_절차.md)
- [vLLM CPU 랩](deploy/cpu-lab/README.md) · [AWS GPU 랩](deploy/gpu-lab/README.md) · [인수 시험 절차](deploy/gpu-lab/ACCEPTANCE.md)
