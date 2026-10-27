# CPU vLLM 로컬 API 점검

이 디렉터리는 새 Docker 컨테이너 `byul-vllm-cpu-031`의 vLLM v0.31.0 CPU 이미지를 통합 저장소 runtime Gateway로 점검하기 위한 비밀값 없는 입력이다. 정식 GPU S3B 결과가 아니며, 서버를 시작한 뒤 `tools/cpu_vllm_check.py` 한 번으로 CPU-01~CPU-14를 실행한다. 기존 `s3b-vllm-cpu`(v0.29.0) 컨테이너는 삭제하거나 변경하지 않고 정지 상태로 둔다.

이 노트북 CPU에는 AVX2와 AVX-VNNI가 있지만 AVX-512가 없다. 따라서 `vllm/vllm-openai-cpu:v0.31.0-x86_64` 이미지가 시작하지 못할 수 있다. 그 경우 이 CPU 실행은 runtime 기능 실패가 아니라 환경 제약에 따른 `not_run`으로 기록한다.

`backend.json`에는 CPU 사전점검 때 만든 저장소 밖 vLLM 키 파일 `~/.local/state/serving-runtime/s3b-cpu/vllm-api-key`의 경로만 있고 키 값은 없다. 시작 전에 파일이 존재하고 group/other 읽기 권한이 없는지 확인한다. 이 파일은 기존 컨테이너의 API 키와 일치해야 하므로 새 값으로 교체하지 않는다.

```bash
test -f "$HOME/.local/state/serving-runtime/s3b-cpu/vllm-api-key"
test "$(stat -c '%a' "$HOME/.local/state/serving-runtime/s3b-cpu/vllm-api-key")" = 600
```

`target.json`과 묶음의 대상 등록은 `engine.kind=vllm`, 기대 판본 `0.31.0`, `/v1/models`의 served model ID `qwen2.5-0.5b-instruct`를 선언한다. 실제 컨테이너가 기동하면 `/version`의 관측 문자열이 정확히 `0.31.0`인지 반드시 확인해야 하며, 기동 전에는 live 확인으로 간주하지 않는다. `model.id`는 정책 별칭 `test-model`이 아니라 `engine_info`가 실제 엔진에서 수집하는 served model ID 목록의 항목이다. revision은 이 API들로 확인할 수 없고 대상 보류 조건도 아니므로 선언하지 않았다.

## 1. 최초 한 번: 저장소 밖 인증 파일 만들기

아래 명령은 원문 토큰을 `~/.local/state/serving-runtime/cpu-vllm/tokens.env`에만 0600으로 저장하고, Gateway 인증 설정에는 `serving-runtime auth hash-token`이 출력한 SHA-256만 넣는다. 명령행 인자, 이 저장소, 점검 보고서에는 토큰 원문을 넣지 않는다.

```bash
cd /home/bobgy/bobproject/byul/runtime
CPU_LAB_STATE="$HOME/.local/state/serving-runtime/cpu-vllm"
install -d -m 0700 "$CPU_LAB_STATE" "$CPU_LAB_STATE/data" "$CPU_LAB_STATE/evidence"
umask 077
.venv/bin/python - <<'PY' > "$CPU_LAB_STATE/tokens.env"
import secrets
print("RUNTIME_INFERENCE_TOKEN=" + secrets.token_urlsafe(32))
print("RUNTIME_VIEWER_TOKEN=" + secrets.token_urlsafe(32))
PY
chmod 0600 "$CPU_LAB_STATE/tokens.env"
set -a
. "$CPU_LAB_STATE/tokens.env"
set +a
INFERENCE_HASH="$(printf '%s\n' "$RUNTIME_INFERENCE_TOKEN" | .venv/bin/python -m serving_runtime auth hash-token)"
VIEWER_HASH="$(printf '%s\n' "$RUNTIME_VIEWER_TOKEN" | .venv/bin/python -m serving_runtime auth hash-token)"
printf '%s\n' \
  '{' \
  '  "schema_version": "runtime-auth/1.0",' \
  '  "auth_ref": "CPU-VLLM-LOCAL-AUTH",' \
  '  "principals": [' \
  '    {' \
  '      "principal_id": "cpu-vllm-inference",' \
  '      "roles": ["inference"],' \
  "      \"token_sha256\": \"$INFERENCE_HASH\"," \
  '      "allowed_models": ["test-model"]' \
  '    },' \
  '    {' \
  '      "principal_id": "cpu-vllm-viewer",' \
  '      "roles": ["viewer"],' \
  "      \"token_sha256\": \"$VIEWER_HASH\"" \
  '    }' \
  '  ],' \
  '  "route_roles": {' \
  '    "POST /v1/chat/completions": ["inference"],' \
  '    "POST /v1/completions": ["inference"],' \
  '    "GET /api/*": ["viewer"]' \
  '  }' \
  '}' > "$CPU_LAB_STATE/auth.json"
chmod 0600 "$CPU_LAB_STATE/auth.json"
unset RUNTIME_INFERENCE_TOKEN RUNTIME_VIEWER_TOKEN INFERENCE_HASH VIEWER_HASH
```

묶음의 구조와 각 파일의 bytes/SHA-256은 다음 명령으로 독립 확인한다.

```bash
cd /home/bobgy/bobproject/byul/runtime
.venv/bin/python -m serving_runtime bundle verify --bundle-dir deploy/cpu-lab/bundle
```

## 2. v0.31.0 컨테이너 생성·시작

기존 key 파일에서 Docker 전용 env-file을 저장소 밖에 0600으로 만든다. 키 값은 명령행 인자나 출력에 넣지 않는다. 모델 cache는 기존 v0.29.0 점검 때 받은 것을 그대로 읽는다.

```bash
CPU_LAB_STATE="$HOME/.local/state/serving-runtime/cpu-vllm"
CPU_VLLM_KEY="$HOME/.local/state/serving-runtime/s3b-cpu/vllm-api-key"
CPU_VLLM_ENV="$CPU_LAB_STATE/vllm-031.env"
install -d -m 0700 "$CPU_LAB_STATE"
test -f "$CPU_VLLM_KEY"
test "$(stat -c '%a' "$CPU_VLLM_KEY")" = 600
umask 077
{
  printf 'VLLM_API_KEY='
  tr -d '\r\n' < "$CPU_VLLM_KEY"
  printf '\n'
} > "$CPU_VLLM_ENV"
chmod 0600 "$CPU_VLLM_ENV"
```

같은 이름의 실패한 새 컨테이너가 남아 있을 때만 아래 정리 명령을 쓴다. 기존 `s3b-vllm-cpu`에는 적용하지 않는다.

```bash
docker stop byul-vllm-cpu-031 2>/dev/null || true
docker rm byul-vllm-cpu-031 2>/dev/null || true
```

새 컨테이너를 정확한 digest와 기존 실행 설정으로 만든다. 포트가 같으므로 기존 `s3b-vllm-cpu`가 정지 상태인지 먼저 확인한다.

```bash
test "$(docker inspect -f '{{.State.Running}}' s3b-vllm-cpu)" = false
docker run -d \
  --name byul-vllm-cpu-031 \
  --env-file "$CPU_VLLM_ENV" \
  -e VLLM_CPU_KVCACHE_SPACE=1 \
  -e HF_HOME=/hf \
  -e HF_HUB_OFFLINE=1 \
  -e TRANSFORMERS_OFFLINE=1 \
  -v "$HOME/.local/state/serving-runtime/s3b-cpu/hf-cache:/hf" \
  -p 127.0.0.1:8000:8000 \
  --memory 6g \
  --memory-swap 12g \
  --shm-size 1g \
  --entrypoint vllm \
  vllm/vllm-openai-cpu:v0.31.0-x86_64@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b \
  serve \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --revision 7ae557604adf67be50417f59c2c2f167def9a775 \
  --served-model-name qwen2.5-0.5b-instruct \
  --dtype bfloat16 \
  --max-model-len 2048 \
  --max-num-seqs 2 \
  --enforce-eager \
  --enable-log-requests \
  --enable-request-id-headers \
  --host 0.0.0.0 \
  --port 8000
```

첫 번째 터미널에서 `/health`가 200이 될 때까지 기다린다. 첫 기동은 CPU 모델 로딩 때문에 수 분 걸릴 수 있다. 컨테이너가 AVX-512 등 CPU 명령어 문제로 종료되면 로그의 오류 class와 exit 상태만 기록하고 점검을 `not_run`으로 끝낸다. 원시 로그를 보고서에 복사하지 않는다.

```bash
cd /home/bobgy/bobproject/byul/runtime
.venv/bin/python - <<'PY'
import json
import time
from urllib.request import urlopen

deadline = time.monotonic() + 600
while True:
    try:
        with urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
            if response.status == 200:
                print("vLLM ready")
                break
    except OSError:
        pass
    if time.monotonic() >= deadline:
        raise SystemExit("vLLM health timeout")
    time.sleep(2)
with urlopen("http://127.0.0.1:8000/version", timeout=5) as response:
    version = json.load(response).get("version")
if version != "0.31.0":
    raise SystemExit(f"unexpected live vLLM version: {version!r}")
print("live vLLM version confirmed: 0.31.0")
PY
```

두 번째 터미널에서 새 Gateway를 18180에 foreground로 시작한다. 8080, 8088, 8700, 5173, 18080은 사용하지 않는다.

```bash
cd /home/bobgy/bobproject/byul/runtime
CPU_LAB_STATE="$HOME/.local/state/serving-runtime/cpu-vllm"
.venv/bin/python -m serving_runtime mvp \
  --backend vllm \
  --backend-config deploy/cpu-lab/backend.json \
  --policy-bundle deploy/cpu-lab/bundle \
  --auth-config "$CPU_LAB_STATE/auth.json" \
  --gateway-port 18180 \
  --data-dir "$CPU_LAB_STATE/data"
```

## 3. 일회 점검 한 명령

세 번째 터미널에서 아래 한 명령을 실행한다. 각 실행은 0700인 새 run 폴더를 만들고 그 안에 0600 JSON·Markdown 보고서를 쓴다. 보고서에는 토큰·프롬프트·응답 본문·원시 Docker 로그를 쓰지 않는다.

```bash
cd /home/bobgy/bobproject/byul/runtime
.venv/bin/python -B tools/cpu_vllm_check.py \
  --token-env-file "$HOME/.local/state/serving-runtime/cpu-vllm/tokens.env" \
  --evidence-dir "$HOME/.local/state/serving-runtime/cpu-vllm/evidence"
```

기본 실행은 CPU-01~CPU-14만 요청한다. CPU-15 logprobs는 과거 CPU 엔진 정지 이력 때문에 명시한 경우에만 요청한다. 현재 제공 정책은 요구대로 `runtime-mvp/1.1`이며 `logprobs`를 허용하지 않으므로, 이 묶음에서 `--include-logprobs`를 주면 CPU-15는 `not_run`이고 전체 결과도 통과가 아니다. 추후 `runtime-mvp/1.2` 시험 정책이 `logprobs`를 허용할 때만 실제 요청하며, 클라이언트 전체 제한시간 뒤에도 `/health`를 확인한다.

```bash
.venv/bin/python -B tools/cpu_vllm_check.py \
  --token-env-file "$HOME/.local/state/serving-runtime/cpu-vllm/tokens.env" \
  --evidence-dir "$HOME/.local/state/serving-runtime/cpu-vllm/evidence" \
  --include-logprobs --logprobs-timeout 45
```

엔진 로그 증거는 `docker logs --since ... byul-vllm-cpu-031`에서 Gateway request ID와 route에 맞는 형식(chat은 `chatcmpl-<id>`, 단일 prompt completions는 `cmpl-<id>-0`)이 실제로 발견될 때만 `observed`다. 형식을 확인하지 못하면 추정하여 통과시키지 않고 `unknown`으로 보고한다.

## 4. 종료

Gateway 터미널에서 `Ctrl+C`로 Gateway를 먼저 종료한 뒤 컨테이너를 멈춘다.

```bash
docker stop byul-vllm-cpu-031
```

새 컨테이너를 다시 만들 필요가 있을 때만 `docker rm byul-vllm-cpu-031`을 실행한다. 기존 `s3b-vllm-cpu`는 삭제하지 않는다.

토큰을 폐기하려면 Gateway를 종료한 상태에서 저장소 밖 `auth.json`과 `tokens.env`를 지운다. 이 삭제는 점검 도구가 자동 수행하지 않는다.
