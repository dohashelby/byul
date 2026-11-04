# 담당 A AWS GPU 1차 절차

기준 구성: 팀 AWS 인계 묶음 `aws-handoff-20261026`(ZIP SHA256 `717fa446…c5445`, 2026-11-03 사용자 결정).
- **서버:** 서울 g6.2xlarge(dev), L4 1장, Ubuntu 24.04, kubeadm 단일 노드, containerd, Cilium·Hubble·Tetragon
- **엔진:** `serving-lab` 네임스페이스의 `serving-engine` Deployment, vLLM 0.31.0 digest 고정, Qwen2.5-0.5B, served model `test-model`, `max_model_len` 2048, `max_num_seqs` 2, GPU 메모리 비율 0.70, UID 2000:0
- **인계 원본은 바꾸지 않습니다.** A 실행에 필요한 것은 `tools/gpu_a_aws_lab.py`가 템플릿 위에 덧씌웁니다. 템플릿 SHA가 `09cd45a4…`가 아니면 렌더링을 거부합니다.

첫 GPU 실증(K3s, 별칭 `byul-gpu-lab`, `max_model_len` 128~1024, GPU 메모리 비율 0.35~0.4, eager)과 구성이 다르므로 새 환경 세대로 기록합니다.

## 전제

1. 인계 01·02·03이 끝나 있습니다: 노드 Ready, GPU allocatable 1, GPU 계산 smoke와 정상·stream 응답 확인, 시험 예외 회수 확인.
2. `~/byul-aws-lab/images.lock` 첫 줄이 엔진 이미지(`docker.io/vllm/vllm-openai@sha256:…`)입니다.
3. 모델이 `/srv/serving/models/7ae5576…`에 준비돼 있습니다. 인계 03이 만듭니다.
4. A 전용 서버입니다. B 배포와 같은 서버를 쓰지 않습니다. 한 GPU에 엔진은 하나만 띄웁니다.
5. 서버에 byul 저장소를 받아 두고 `update/`에서 `uv sync --frozen`을 마쳤습니다. 커밋을 기록합니다.
6. Ubuntu 24.04 AMI의 ubuntu 사용자는 `sudo -v`가 비밀번호를 요구해 인계 01이 첫 줄에서 멈춥니다(2026-11-03 실측). 스크립트를 고치지 않고 `sudo -v`만 `sudo -n true`로 넘기는 래퍼를 PATH 앞에 두고 실행했으며, 그 사실을 증거에 남겼습니다(`RESULTS-20261103.md`).

## 세대

| 세대 | `--generation` | 점검 | 덧씌우는 것 |
|---|---|---|---|
| G-A1 | `v1-callback` | REQ-17(새 세션에서 맨 먼저), REQ-16 `cache_salt`, REQ-18(V1), REQ-14 async 경로 | 콜백 센서 마운트·`PYTHONPATH`, `VLLM_USE_V2_MODEL_RUNNER=0`, `--enable-prefix-caching`, `--distributed-executor-backend mp`(TP1에서도 REQ-14 워커 지점이 불리게 함) |
| G-A2 | `v1-callback-sync` | REQ-14 동기 경로 | G-A1 + `--no-async-scheduling` |
| G-A3 | `v2-default` | REQ-08, REQ-09, REQ-10, SUP-08 | 없음(라벨·`enableServiceLinks`만) |
| G-A3 template | `v2-template` | SUP-03 | 고정 `guard-text-chat-v1.jinja` 읽기 전용 마운트와 `--chat-template` |
| G-A4 | `v1-req16-lab` | REQ-16 `adapter_content`, `model_generation` | 센서 마운트·V1·prefix caching(MP 없음) + `--enable-lora --max-lora-rank 8 --max-loras 1`, 런타임 LoRA 갱신·개발 모드 환경 변수, `/guard-lora`·`/guard-weights` |

모든 세대에 공통으로 덧씌우는 것:
- Pod 라벨 `guard.run=<run_id>`, `byul.a/generation=<세대>`
- `enableServiceLinks: false`
- 시험 Pod: 라벨 `app: engine-test-probe`, 이미 받은 엔진 이미지를 GPU 없이 사용

인계 템플릿에는 `--enforce-eager`가 없어 CUDA Graph가 켜진 상태가 기본입니다. 첫 실증과 같은 eager가 필요할 때만 `--enforce-eager`를 붙이고 세대 기록에 남깁니다.

## 세대 하나의 순서

`RUN`은 세대마다 새로 정합니다(예: `a-g1-20261104-1`). 아래 명령은 AWS 호스트의 ubuntu 사용자로 실행합니다.

1. **센서 자산** (센서 세대만)
   ```bash
   cd ~/byul/update
   uv run guard prepare-engine-callbacks --output ~/byul-a/$RUN/prepared
   ```
   - LIF-08을 함께 돌릴 때만 `--lab-faults`를 붙입니다.
   - Pod용 사본을 같은 바이트로 둡니다:
     ```bash
     sudo install -d -m 0750 -o 2000 -g 0 /srv/byul-a/$RUN/engine-callbacks
     sudo cp -a ~/byul-a/$RUN/prepared/. /srv/byul-a/$RUN/engine-callbacks/
     sudo chown -R 2000:0 /srv/byul-a/$RUN/engine-callbacks
     sudo chmod -R u=rX,g=rX,o= /srv/byul-a/$RUN/engine-callbacks
     sudo install -d -m 0700 -o 2000 -g 0 /srv/byul-a/$RUN/lifecycle-data
     ```
   - 실행기는 `~/byul-a/$RUN/prepared`의 사본을 읽습니다. 두 사본의 파일 SHA가 같은지 확인합니다.
2. **렌더링**
   ```bash
   uv run python -m tools.gpu_a_aws_lab --handoff-dir ~/byul-aws-lab --generation <세대> --run-id $RUN \
     --engine-image "$(head -1 ~/byul-aws-lab/images.lock)" --out ~/byul-a/$RUN/render
   ```
   `render.json`의 템플릿 SHA, 세대, 출력 SHA를 세대 기록에 남깁니다.
3. **엔진 교체**
   ```bash
   kubectl -n serving-lab scale deployment/serving-engine --replicas=0   # 이전 Pod가 사라진 것을 확인
   kubectl apply -f ~/byul-a/$RUN/render/engine.yaml
   kubectl -n serving-lab rollout status deployment/serving-engine --timeout=25m
   ```
   - Recreate 배포라 이전 Pod가 먼저 내려갑니다.
   - 기동이 실패하면 events, OOM, 이미지 pull, 권한, CUDA 오류를 원문으로 남깁니다. 권한을 늘려 해결하지 않습니다.
4. **REQ-16 lab만**
   ```bash
   kubectl label namespace serving-lab byul.lab/owned=true
   E=$(kubectl -n serving-lab get pod -l guard.run=$RUN,app=serving-engine -o name)
   kubectl -n serving-lab exec -i $E -c engine -- python3 -I - lora --model-dir /models --out /guard-lora/variants --seed <seed> < tools/gpu_a_lab_variants.py
   kubectl -n serving-lab exec -i $E -c engine -- python3 -I - weights --model-dir /models --out /guard-weights/gen2 < tools/gpu_a_lab_variants.py
   ```
   두 출력 JSON(파일 SHA 포함)을 세대 기록에 남깁니다. 실행 대상(target)에는 `"lab": true`를 둡니다.
5. **시험 Pod와 시험 예외**
   ```bash
   kubectl apply -f ~/byul-a/$RUN/render/probe.yaml
   kubectl -n serving-lab wait --for=condition=Ready pod/engine-test-probe-$RUN --timeout=5m
   kubectl apply -f ~/byul-aws-lab/engine-test-access.yaml   # 시험 직전에만
   ```
6. **실행 대상과 입력** (`tools/gpu_a_run_inputs.py`)
   ```bash
   kubectl get namespace serving-lab -o json > ~/byul-a/$RUN/namespace.json
   kubectl -n serving-lab get pod -l guard.run=$RUN,app=serving-engine -o jsonpath='{.items[0]}' > ~/byul-a/$RUN/engine-pod.json
   kubectl -n serving-lab get pod engine-test-probe-$RUN -o json > ~/byul-a/$RUN/probe-pod.json
   uv run python -m tools.gpu_a_run_inputs env --render ~/byul-a/$RUN/render/render.json \
     --gpu-model "$(nvidia-smi --query-gpu=name --format=csv,noheader)" \
     --driver "$(nvidia-smi --query-gpu=driver_version --format=csv,noheader)" --out ~/byul-a/$RUN/env.json
   uv run python -m tools.gpu_a_run_inputs target --env ~/byul-a/$RUN/env.json --render ~/byul-a/$RUN/render/render.json \
     --namespace ~/byul-a/$RUN/namespace.json --engine-pod ~/byul-a/$RUN/engine-pod.json --probe-pod ~/byul-a/$RUN/probe-pod.json \
     --context "$(kubectl config current-context)" --out ~/byul-a/$RUN/target.json
   uv run python -m tools.gpu_a_run_inputs check --check REQ-17 --env ~/byul-a/$RUN/env.json --target ~/byul-a/$RUN/target.json \
     --prepared ~/byul-a/$RUN/prepared --cases ~/byul-a/$RUN/req17-cohort.json --out ~/byul-a/$RUN/REQ-17
   ```
   - target은 두 Pod의 `guard.run`·세대 라벨, 역할 라벨(`serving-engine`, `engine-test-probe`), 엔진 이미지가 렌더 기록·환경과 같아야 만들어집니다. 만든 뒤 A 전송 계층 검사로 바로 확인합니다.
   - `check`는 점검마다 실행합니다(사례 목록이 점검 하나의 role만 받습니다). 사례: REQ-14 `req14-normal`, REQ-16 `req16-salt`(G-A1)·`req16-lab`(G-A4), REQ-18 `req18-slot`, REQ-17은 `python -m tools.gpu_a_req17_cohort --seed <값> --out ~/byul-a/$RUN/req17-cohort.json`의 출력.
   - 출력 폴더의 `policy.yaml`(sensor 해시 4개 채움, 그 점검만 범위), `guard.toml`, `manifest.json`, `commands.txt`(select → run → judge → report)를 씁니다.
   - Pod가 다시 시작되거나 바뀌면 target과 입력을 다시 만듭니다. 이전 입력으로 돌리면 바인딩 불일치로 멈춥니다.
7. **점검 실행:** 세대 표의 점검을 돌리고 확인 항목(`ENVIRONMENT.md`, `REQ-17_DESIGN.md` 4절, `REQ-14_REJECTION.md`, `REQ-16_CONTEXTS.md`)을 봅니다.
8. **정리**
   ```bash
   kubectl delete -f ~/byul-aws-lab/engine-test-access.yaml
   ```
   - 예외를 지운 뒤 시험 Pod에서 엔진 `/health`가 시간 초과되는지 확인합니다(인계 03과 같은 대조).
   - 시험 Pod를 지우고 증거를 반출합니다.
   - REQ-16 lab이었다면 `kubectl label namespace serving-lab byul.lab/owned-`.
   - 다음 세대가 없으면 엔진을 scale 0하고 EC2를 Stop합니다.
   - 인계 03을 다시 돌려야 하면, 먼저 인계 03이 렌더링한 `engine.yaml`을 다시 적용해 덧씌운 볼륨·환경 변수를 걷어냅니다. 03은 남은 추가 볼륨이 있으면 멈춥니다.

## 1차 순서와 시간

| 순서 | 내용 | 예상 |
|---|---|---|
| 0 | 엔진 컨테이너 안 `gpu_model_runner.py` 크기 335,342와 SHA `bb903dff…`, Python 3.12.3, async 기본값 확인 | 10분 |
| 1 | G-A1 | 기동 10~20분 + 점검 30~60분 |
| 2 | G-A2 | 기동 + 15분 |
| 3 | G-A3 | 기동 + 30분 |
| 4 | G-A4 | 기동 + 변형 생성 10분 + 30분 |

- 세대마다 기동 시간이 듭니다(첫 기동은 이미지·컴파일 캐시 때문에 더 깁니다). dev 단가 $1.20208/h 기준 1차 전체는 4~6시간, 약 $5~8로 봅니다. 추정이며 비용 장부(`COST_LEDGER.md`)에 실제 값을 적습니다.
- 증거를 반출한 뒤 Stop합니다.

## 2026-11-03 후속 실행과 남은 경계
- **G-A3:** `input_validation:kubernetes`를 구현하고 REQ-08·09·SUP-08을 실제 서버에서 실행했습니다. `python -m tools.gpu_a_run_inputs http-check --check <ID> --env <env.json> --target <target.json> --out <새 폴더>`로 REQ-08·09·10·SUP-03·08 입력을 만듭니다. SUP-03은 `v2-template` 세대를 먼저 준비합니다. select → run → judge → report는 기존 CLI 그대로입니다.
- input validation은 선언한 한 endpoint·정상 body·유한 token 배열·정확한 추가 필드 fixture만 받습니다. 실행기 어댑터는 공백 있는 원 ID를 전송용 ID로 변환하고 응답에서 복원합니다. 고정 `stream_probe.py`와 다른 전송 프로파일의 허용 범위는 바꾸지 않았습니다.
- **REQ-16:** Qwen2-VL-2B-Instruct를 준비한 G-A4 후속에서 `req16-full` 23사례로 네 문맥을 한 실행에서 관측했습니다. media의 공유 텍스트 접두를 위반으로 읽는 투영 한계와 실제 이미지 블록 재사용 미관측을 원자료 분석에 남겼습니다. 기존 판정은 보존합니다.
- **큰 모델의 lab 가중치 공간:** 현재 렌더러의 `guard-weights` 상한은 4Gi입니다. 위 VL 시험 Pod는 측정 완료 39초 뒤 이 상한 초과로 Evicted됐습니다. 멀티모달 변형 재실행 전 모델 파일 합계와 변형 공간을 확인하고, 상한을 명시·검증할 렌더 입력 및 새 환경 기록을 준비해야 합니다. 0.5B용 4Gi 설정을 큰 모델에 그대로 사용하지 않습니다.

- **다중 GPU:** REQ-14의 TP2 분기와 REQ-21은 L4 1장 구성에서 할 수 없습니다. 인계 묶음도 두 번째 GPU 서버를 범위에서 뺐습니다. 별도 구성 승인이 필요합니다.
- **2단계:** `NATIVE-20261103.md`의 실제 CUDA 구성요소·zero-copy daemon 시험을 수행했습니다. 기존 수동 증거 경로로 보존한 DECLARED 관측이며, EXAMPLE 정책과 전체 UNKNOWN을 유지합니다. 실제 서빙 경로에 묶인 자동 수집기·계보·메모리 읽기 계측은 아직 미구현입니다. 이 구성요소 시험을 자동 제품 경로 완료로 세지 않습니다.
