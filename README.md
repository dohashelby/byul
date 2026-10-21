# serving-runtime
BoB serving-engine security runtime solution

- 개발계획서 v1.4(문서 상태: 개발 기준 설계안)가 전달됐으며, 직접 개발할 모듈의 언어는 Python으로 선택했습니다. 연동 규격·실제 환경 등은 미결입니다.
- Codex는 구현·수정·시험을, Claude는 검수를 담당합니다.

## 개발 현황 한눈에 (2026-10-21)

| 구분 | 내용 | 확인 수준 |
| --- | --- | --- |
| 요청 통제 Gateway | 정책 파일 검증, 허용/정책 거부/입력 오류 판정, 서버 발급 요청 ID, 검증된 필드만 중계 | 모의 엔진·실제 vLLM(CPU) |
| 스트리밍 (S1) | SSE 즉시 중계, 시작·청크 무응답·전체 시간 제한, 동시 처리 2개 초과 시 429, 시작/최종 결과 분리 | 모의 엔진·실제 vLLM(CPU) |
| 정책·상태 (S2) | 정책 schema 1.1(기간·승인 근거), 철회 파일, 정책 이력, 기준/현재 비교, 수집원 상태 | 모의 엔진·fixture |
| 외부 vLLM 연결 (S3A) | 설정 파일로 고정된 목적지·모델 별칭·자격정보, health·모델·버전·지표 수집 | 가짜 backend·실제 vLLM(CPU) |
| 기록·조회 | 요청별 시작/최종 JSONL 기록(프롬프트 원문 미저장), 조회 API, 증거 JSON 내려받기 | 모의 엔진·실제 vLLM(CPU) |
| 대시보드 | 현황·요청 목록·상세 증거·정책/기준 상태, 3초 갱신, 읽기 전용 | 브라우저 수동 확인 |
| 시험 | unittest 116개, 수용 시험 31개 사례(`mvp-acceptance`), 시연 명령(`mvp-demo`) | 전부 통과 |

**확인 수준의 뜻**
- 모의 엔진·가짜 backend: 저장소 안 시험용 서버로 확인했습니다.
- 실제 vLLM(CPU): 이 PC에서 vLLM CPU v0.29.0과 0.5B 모델로 S3-01~06을 사전 점검했습니다. 결과는 `docs/S3B_실제환경_시험준비.md` §7에 있습니다.
- 계획상의 **정식 S3B(승인 GPU 환경·vLLM v0.31.0·선정 모델) 시험은 아직 하지 않았습니다.**

**아직 하지 않은 것**
- 정식 S3B 실제 환경 시험
- 요청 단위 backend 수신 증거: vLLM 기본 로그에 Gateway 요청 ID가 남지 않습니다.
- 엔진 내부 생성 취소 확인
- context 2048 사전 통제: tokenizer 확인 전입니다.
- API 인증, 엔진 포트 직접 접근 차단, Cilium·Tetragon 통합, SGLang

## 런타임 통제 MVP (로컬)

로컬 PC에서 정책 파일을 읽고, 추론 요청을 허용하거나 거부하고, 그 결과와 증거를 브라우저 대시보드에서 조회합니다. backend는 기본값인 **모의 엔진**과, 설정 파일로 지정하는 **외부 vLLM** 중 하나를 사용합니다.

```text
시험 클라이언트 → Gateway :8080 → 모의 엔진 :8088  (기본, --backend mock)
                     │          └→ 외부 vLLM     (--backend vllm --backend-config)
                     ↓
          요청 결과 기록 · 수신 증거 · 상태/지표 수집
                     ↓
          조회 API → 브라우저 대시보드 (읽기 전용)
```

이 MVP는 요청 통제·기록·조회 경로를 다룹니다. 커널 통제, 네트워크 격리, API 인증은 구현하지 않았으며 이를 입증하지 않습니다. 엔진 포트(8088, 외부 vLLM 포트)로 직접 보내는 요청은 막지 않습니다.

### 준비

WSL Ubuntu의 Python 3.12 기준입니다. 저장소 루트에서 한 번만 실행합니다. 외부 실행 라이브러리 의존성은 없습니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

### 실행

저장소 루트에서 실행합니다. 정책 파일 기본 경로(`examples/mvp/policy.json`)가 현재 폴더 기준이기 때문입니다.

```bash
.venv/bin/python -m serving_runtime mvp
```

`Browser: http://127.0.0.1:8080/`이 출력되면 터미널을 열어 둔 채 Windows 브라우저에서 그 주소를 엽니다. 종료는 `Ctrl+C`입니다. 서버는 `127.0.0.1`에만 열립니다.

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `--policy PATH` | `examples/mvp/policy.json` | 정책 파일. 변경은 재실행으로 적용 |
| `--gateway-port N` | `8080` | Gateway·대시보드 포트 |
| `--engine-port N` | `8088` | 모의 엔진 포트 |
| `--data-dir PATH` | `~/.local/state/serving-runtime/mvp` | 기록 저장 위치 |
| `--relay-timeout 초` | `5` | 엔진 연결·응답 헤더 시작 대기 상한. 일반 요청에서는 기존과 같이 소켓 작업별 대기 상한 |
| `--stream-idle-timeout 초` | `30` | stream 응답의 청크 간 무응답 상한 |
| `--request-deadline 초` | `300` | stream 요청 전체 처리 상한 |
| `--max-concurrency N` | `2` | 일반·stream 공통 동시 처리 상한. 초과 요청은 대기 없이 429 |
| `--revocations PATH` | 미설정 | 철회 스냅샷. 설정했지만 읽기·검증 실패 시 신규 요청 503 |
| `--baseline PATH` | 미설정 | 승인 기준 파일(읽기 전용) |
| `--manifest PATH` | 미설정 | 실행 선언값 파일(읽기 전용, 자동 승격 없음) |
| `--collector-interval 초` | `5` | 모의 엔진 상태·정보 수집 주기 |
| `--collector-stale-after 초` | `15` | 마지막 성공 관측값을 오래됨으로 표시하는 기준 |

### 정책 1.1과 상태 판정

기존 `runtime-mvp/1.0` 정책은 그대로 로딩합니다. `runtime-mvp/1.1`은 선택 필드 `valid_from`·`valid_until`(시간대가 포함된 ISO8601), `approval_ref`(표시 전용)를 추가합니다. 기간 필드가 없으면 무기한입니다. 1.0 문서에 이 필드를 넣으면 기존 엄격 검증에 따라 `UNKNOWN_FIELD`로 거부합니다. 예시는 `examples/mvp/policy-v1.1.json`입니다.

철회 파일 형식은 다음과 같습니다. 현재 정책의 ID·판본·파일 SHA-256이 모두 같은 항목만 철회로 판정합니다. 철회 파일을 설정하지 않으면 유효성 판단에서 제외하고 화면에 **철회 확인 미설정**으로 표시합니다. 설정한 파일을 읽거나 검증할 수 없으면 상태를 확인할 수 없으므로 신규 요청을 유효하게 보지 않습니다.

```json
{
  "schema_version": "runtime-mvp-revocations/1.0",
  "revoked": [{
    "policy_id": "local-policy-001",
    "policy_version": "2",
    "sha256": "64자리 정책 파일 SHA-256",
    "revoked_at": "2026-10-21T12:00:00+09:00",
    "reason": "운영자 철회 사유"
  }]
}
```

정책 상태는 서로 섞지 않습니다. `loaded`는 파일 읽기·schema 검증 성공, `gateway_active`는 신규 요청 시점의 기간·철회 판정 통과, `external_enforcement`는 현재 `not_connected`입니다. 시작 시 정책 관측을 `policy_history.jsonl`에 추가하며 같은 ID·판본의 내용 해시가 바뀌면 별도로 표시합니다. 진행 중인 stream은 시작 때 선택한 정책 스냅샷을 사용하므로 이후 만료 시각이 지나도 일괄 종료하지 않습니다.

기간 전·만료·확인된 철회·철회 확인 불가는 각각 `POLICY_NOT_YET_VALID`, `POLICY_EXPIRED`, `POLICY_REVOKED`, `POLICY_REVOCATION_UNKNOWN` 503이며 엔진을 호출하지 않습니다. `approval_ref`는 표시만 하고 유효성을 검증하지 않습니다. 서명 검증, 승인 서비스 연동, hot reload, 외부 집행 확인은 구현하지 않았습니다.

baseline과 manifest는 평면 `fields` 객체를 사용합니다. manifest도 아래와 같은 `runtime-mvp-baseline/1.0` 형식을 쓰며 출처는 `declared_manifest`로 구분합니다.

```json
{
  "schema_version": "runtime-mvp-baseline/1.0",
  "fields": {
    "engine.kind": "mock",
    "engine.version": "mock-1.0",
    "model.id": "test-model",
    "option.max_model_len": 2048
  }
}
```

비교 상태는 `match`(일치), `drift`(차이), `missing`(수집 결과에 필드 없음), `stale`(마지막 성공이 신선도 초과), `error`(수집 실패), `not_collected`(수집원 없음)입니다. 모의 엔진 응답은 `data_origin: mock`으로 표시하며 실제 엔진 관측이라고 주장하지 않습니다. 수집 실패 시 마지막 성공값과 시각을 유지하고 0이나 정상값으로 바꾸지 않습니다. 관측값은 baseline으로 저장하거나 승격하지 않습니다. 특히 **model.id 일치는 weight 해시·revision·메모리 로딩을 증명하지 않습니다.**

### 외부 vLLM backend 모드 (S3A)

기본은 기존 모의 엔진 모드입니다. 외부 backend 모드는 Gateway만 띄우고 모의 엔진을 실행하지 않으며, 실제 엔진의 시작·종료도 관리하지 않습니다.

```bash
.venv/bin/python -m serving_runtime mvp --backend vllm --backend-config backend.json
```

설정 파일은 `runtime-backend/1.0` 형식이며 예시는 `examples/mvp/backend-vllm.example.json`입니다(가짜 주소, 키 없음).

- `base_url`: backend 주소(http/https, 경로 접두 허용). 요청 본문·헤더(`Host`, `X-Forwarded-*` 등)로 바꿀 수 없고, 클라이언트 헤더는 backend로 전달하지 않습니다. 3xx redirect는 따라가지 않고 502로 처리하며, https 인증서 검증은 끌 수 없습니다.
- `model_map`: 정책 허용 모델명 → backend 모델 ID. 매핑 없는 허용 모델은 backend를 호출하지 않고 503 `BACKEND_MODEL_UNMAPPED`입니다.
- `credential`: `{"env": "변수명"}` 또는 `{"file": "경로"}` 중 하나. 키 값은 설정 파일·CLI에 넣지 않습니다. 설정했는데 값이 없으면 503 `BACKEND_CREDENTIAL_MISSING`(backend 미호출), 파일이 다른 사용자에게 읽히면 `insecure_file` 상태로 표시합니다. 키는 기록·API·화면·보고서에 남기지 않습니다.
- `health_path`·`models_path`·`version_path`·`metrics_url`·`metrics_map`·`timeouts`: 상태·지표 수집과 시간 제한. health 200은 "연결 가능"이며 추론 정상을 뜻하지 않습니다.

증거 구분: 외부 backend 요청은 기록에 `backend_kind: "vllm"`으로 남고, 모의 엔진 수신 기록을 증거로 쓰지 않습니다. backend 측 수신 기록원이 아직 연결되지 않았으므로 거부 요청은 "Gateway 미전달 확인, backend 미수신은 미확인", 허용 요청은 "Gateway 전달·응답 수신, backend 측 수신 기록 미연결"로 표시합니다. 엔진 내부 생성 취소는 확인하지 않습니다(`backend_cancel: "not_observed"`).

vLLM 근거 표기: 경로(`/v1/chat/completions`, `/health`, `/v1/models`, `/version`, `/metrics`), SSE 종료 `data: [DONE]`, 지표 이름 후보(`vllm:num_requests_running`, `vllm:num_requests_waiting`, `vllm:kv_cache_usage_perc` — 구버전 `vllm:gpu_cache_usage_perc`, `vllm:time_to_first_token_seconds`, `vllm:e2e_request_latency_seconds`, `vllm:prompt_tokens_total`, `vllm:generation_tokens_total`)는 공식 문서 latest(https://docs.vllm.ai/en/latest/usage/metrics/) 기준 **후보**이며, v1.4 선정 고정 버전(v0.31.0)의 실제 응답으로 확인하지 않았습니다. 그래서 `metrics_map`의 `verified`는 항상 `false`입니다. 응답에 없는 지표는 `unsupported_or_absent`로 표시합니다.

S3A 시험은 시험 전용 가짜 backend(`src/serving_runtime/mvp/fake_backend.py`, `data_origin: fake_backend_test`)로 수행했으며 **실제 vLLM 연결 완료가 아닙니다.** 실제 환경 시험(S3B) 준비는 `docs/S3B_실제환경_시험준비.md`를 참고합니다.

### 실제 vLLM(CPU)으로 실행하기

GPU가 없는 PC에서 실제 vLLM과 연결해 보는 방법입니다. **정식 S3B(승인 GPU 환경)가 아닌 사전 점검용**이며, 2026-10-21에 이 방법으로 S3-01~06을 확인했습니다. 이미지(압축 약 1.8GB)와 모델(약 1GB)을 내려받습니다.

| 항목 | 사용한 값 |
| --- | --- |
| 엔진 이미지 | `public.ecr.aws/q9t5s3a7/vllm-cpu-release-repo:v0.29.0` (digest `sha256:66064cf683152e60eff2e5b18ec2d4ace236fb063c0a241f918691db9caddcf4`) |
| 모델 | `Qwen/Qwen2.5-0.5B-Instruct` revision `7ae557604adf67be50417f59c2c2f167def9a775` |
| 상태 폴더 | `~/.local/state/serving-runtime/s3b-cpu/` (저장소 밖, 키·설정 파일 권한 600) |

1. API 키를 파일로 만들고 vLLM을 띄웁니다. 준비까지 2~3분 걸립니다. float32는 메모리 6GiB 안에서 OOM이 나서 bfloat16을 씁니다.

```bash
D=~/.local/state/serving-runtime/s3b-cpu; mkdir -p -m 700 $D/hf-cache
umask 077; python3 -c "import secrets;print(secrets.token_urlsafe(32))" > $D/vllm-api-key
printf 'VLLM_API_KEY=%s\n' "$(cat $D/vllm-api-key)" > $D/vllm.env
docker run -d --name s3b-vllm-cpu -p 127.0.0.1:8000:8000 --memory 6g --shm-size 1g \
  --env-file $D/vllm.env -e VLLM_CPU_KVCACHE_SPACE=1 -e HF_HOME=/hf -v $D/hf-cache:/hf \
  public.ecr.aws/q9t5s3a7/vllm-cpu-release-repo:v0.29.0 \
  --model Qwen/Qwen2.5-0.5B-Instruct --revision 7ae557604adf67be50417f59c2c2f167def9a775 \
  --served-model-name qwen2.5-0.5b-instruct --dtype bfloat16 --max-model-len 2048 \
  --max-num-seqs 2 --enforce-eager --host 0.0.0.0 --port 8000
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health   # 200이면 준비 완료
```

2. `$D/backend.json`을 `examples/mvp/backend-vllm.example.json` 형식으로 만듭니다. `model_map`은 `{"test-model": "qwen2.5-0.5b-instruct"}`로, `credential`은 `{"file": "<$D 절대 경로>/vllm-api-key"}`로 둡니다. CPU는 느리므로 `timeouts.relay_timeout_s`를 120 정도로 늘립니다.

3. Gateway를 외부 vLLM 모드로 띄웁니다. 아래처럼 띄우면 터미널이나 Claude를 닫아도 계속 실행됩니다. PC를 재시작하거나 WSL을 종료하면 꺼집니다.

```bash
cd ~/bobproject/serving-runtime
setsid nohup .venv/bin/python -u -m serving_runtime mvp --backend vllm --backend-config $D/backend.json \
  --gateway-port 18080 --data-dir $D/data > $D/gateway.log 2>&1 < /dev/null &
```

브라우저에서 `http://127.0.0.1:18080`을 엽니다. 위쪽에 "외부 vLLM backend · 실제 연결 검증 전" 배지가 보입니다.

4. 끄거나 다시 켜는 명령입니다.

```bash
kill $(ss -ltnp | grep '127.0.0.1:18080 ' | grep -o 'pid=[0-9]*' | cut -d= -f2)   # Gateway 끄기
docker stop s3b-vllm-cpu      # vLLM 끄기 (메모리 약 4.5GiB 반환)
docker start s3b-vllm-cpu     # vLLM 다시 켜기 (준비 2~3분)
```

CPU에서는 예열 뒤 16토큰 응답에 5~7초가 걸립니다. 이 결과를 GPU 성능이나 정식 S3B 통과로 해석하지 않습니다.

### 요청 보내기

다른 터미널에서 보냅니다. 응답 헤더 `X-Request-ID`가 서버가 발급한 요청 ID입니다.

```bash
# 허용: 200
curl -i -H 'Content-Type: application/json' \
  -d '{"model":"test-model","messages":[{"role":"user","content":"hi"}],"max_tokens":64}' \
  http://127.0.0.1:8080/v1/chat/completions

# 출력 한도 초과: 403 정책 거부, 모의 엔진에 전달하지 않음
curl -i -H 'Content-Type: application/json' \
  -d '{"model":"test-model","messages":[{"role":"user","content":"hi"}],"max_tokens":129}' \
  http://127.0.0.1:8080/v1/chat/completions

# 스트리밍: 이벤트를 도착하는 즉시 표시하고 [DONE]까지 확인
curl -N -H 'Content-Type: application/json' \
  -d '{"model":"test-model","messages":[{"role":"user","content":"hi"}],"max_tokens":64,"stream":true}' \
  http://127.0.0.1:8080/v1/chat/completions
```

지원하는 요청은 `POST /v1/chat/completions` 하나입니다. `model`(허용 목록), `messages`(role은 system/user/assistant, content는 문자열), `max_tokens`(생략 시 128), `stream`(bool, true면 SSE), `n`(1만)을 받습니다. `stream_options`를 포함한 나머지 필드는 허용하지 않습니다.

| 상황 | HTTP | 결과 |
| --- | --- | --- |
| 허용, 모의 엔진 응답 정상 | 200 | 허용 |
| 동시 처리 상한 초과 | 429 + `Retry-After: 1` | 정책 거부, 모의 엔진 미호출 |
| 미허용 모델, `max_tokens` 128 초과 | 403 | 정책 거부 |
| JSON·타입 오류, 미지원 필드·잘못된 `stream` 타입 | 400 | 입력 오류 |
| 본문 64KiB 초과 / Content-Type 오류 | 413 / 415 | 입력 오류 |
| 정책 파일 없음·검증 실패 | 503 | 처리 오류 (정책 준비 실패) |
| 정책 기간 전·만료·철회·철회 확인 불가 | 503 | 처리 오류 (원인별 코드, 모의 엔진 미호출) |
| 기록 저장 불가 | 503 | 처리 오류 (모의 엔진 미호출) |
| 모의 엔진 연결 실패 / 시간 초과 | 502 / 504 | 처리 오류 |

stream은 backend SSE 바이트를 전체 버퍼링하지 않고 즉시 전달합니다. HTTP 200이 시작된 뒤에도 `[DONE]` 없는 종료, 청크 무응답, 전체 제한 초과, client 연결 종료, 중계 오류는 최종 기록에서 성공이 아닌 `ERROR`로 남습니다. Gateway는 이때 backend 연결을 닫지만, 현재 확인 가능한 취소 증거는 모의 엔진의 `evidence_kind: "mock"` 기록뿐이며 실제 vLLM 내부 생성 취소를 입증하지 않습니다. 또한 context 2048 제한은 tokenizer·chat template 확인 전이므로 아직 적용하지 않았고 문자 길이로 대신하지 않습니다.

### 시연하기

터미널 두 개를 사용합니다.

1. 첫 번째 터미널에서 `.venv/bin/python -m serving_runtime mvp`로 서버를 실행하고 그대로 둡니다.
2. 두 번째 터미널에서 `.venv/bin/python -m serving_runtime mvp-demo`를 실행합니다. 사례별 기대·실제 결과 표와 저장된 결과 JSON 경로가 출력됩니다. Gateway 주소를 바꿨다면 `--gateway URL`, 기록 위치를 바꿨다면 `--data-dir PATH`를 함께 지정합니다.
3. 브라우저에서 `http://127.0.0.1:8080`을 열고 **현황 숫자 → 정책 거부 행 클릭 → “Gateway 미전달, 엔진 미수신 확인” → 증거 내려받기** 순서로 확인합니다.
4. 첫 번째 터미널에서 `Ctrl+C`로 서버를 종료한 뒤 같은 명령으로 재실행합니다. 이전 기록이 유지되고 요청 목록에 **이전 실행** 태그가 나타나는지 확인합니다.

### 대시보드

- **현황**: 결과별 건수, 정책 판본·SHA-256·로딩 시각, 모의 엔진 연결 점검, 기록 저장 상태·손상 줄 수와 위치. 네트워크·프로세스·파일·GPU 관측은 "미연결"로 표시합니다.
- **정책·기준 상태**: 읽기/Gateway 활성/외부 집행 배지, 기간·철회·승인 근거, 최근 정책 이력 50건, baseline 비교, 수집원별 최근 성공·오류·`data_origin`을 표시합니다.
- **요청 목록**: 결과별 필터. 행을 누르면 상세가 열리며, 재실행 전 요청에는 "이전 실행" 태그가 붙습니다.
- **상세**: 판정·적용 규칙, 전달 여부, Gateway 기록(시작→최종), 모의 엔진 수신 증거, 증거 JSON 내려받기.
- 3초마다 갱신하며, 서버와 연결이 끊기면 경고 배너를 띄우고 이전 값을 흐리게 표시합니다.

거부 요청의 "미수신 확인"은 Gateway가 중계하지 않았다는 기록과, 같은 실행 중인 모의 엔진의 수신 기록 조회를 함께 대조한 결과입니다. 엔진 조회 실패나 이전 실행의 요청은 "확인 불가"로 표시합니다.

### 기록

`~/.local/state/serving-runtime/mvp/` 아래에 저장합니다. 저장소 밖이므로 커밋되지 않습니다.

- `gateway_records.jsonl`: 요청별 시작·최종 판정 기록
- `engine_receipts.jsonl`: 모의 엔진 수신 기록
- `policy_history.jsonl`: 시작 시 정책 로딩 성공·실패, 현재·직전 참조와 같은 판본 내용 변경 기록

프롬프트·응답 원문은 저장하지 않습니다. 실행할 때 이전 기록을 지우지 않으며, 초기화하려면 서버를 끈 뒤 위 폴더를 직접 삭제합니다.

### 시험

```bash
.venv/bin/python -B -m unittest discover -s tests -v
```

HTTP 시험은 로컬 소켓이 필요합니다. 소켓을 막은 샌드박스에서는 해당 시험을 건너뜁니다.

### 수용 시험

MVP-01~11, S1-01~06, S2-01~06, S3A-01~08을 임시 데이터와 빈 포트에서 자체 실행하고 JSON·Markdown 보고서를 남깁니다. S3A 사례는 시험 전용 가짜 backend를 사용하며 보고서에 그 사실을 표시합니다.

```bash
.venv/bin/python -m serving_runtime mvp-acceptance
```

기본 보고서 위치는 `~/.local/state/serving-runtime/mvp/acceptance/`입니다. 다른 위치는 `--report-dir PATH`로 지정합니다. 사례가 하나라도 실패하면 종료 코드는 1입니다.

### 문제 해결

- **`python3` 또는 `.venv/bin/python` 명령을 찾을 수 없음**: Ubuntu에 Python 3가 설치됐는지 `python3 --version`으로 확인합니다. `.venv/bin/python`이 없다면 저장소 루트에서 준비 절의 가상환경 생성과 설치 명령을 다시 실행합니다.
- **`mvp-demo`가 서버에 연결하지 못함**: 첫 번째 터미널의 `mvp` 서버가 계속 실행 중인지 확인하고, 포트를 바꿨다면 `mvp-demo --gateway http://127.0.0.1:바꾼포트`로 맞춥니다.
- **브라우저에서 연결 거부**: 서버가 실행 중인지 확인합니다. 실행한 터미널을 닫으면 서버도 꺼집니다.
- **포트 충돌 (`Address already in use`)**: 이미 실행 중인 서버를 끄거나 `--gateway-port`·`--engine-port`로 다른 포트를 지정합니다.
- **모든 요청이 503**: 대시보드 정책 카드의 오류 사유를 확인합니다. 저장소 루트가 아닌 곳에서 실행하면 정책 파일을 찾지 못합니다.

### 진행 상태

| 단계 | 내용 | 상태 |
| --- | --- | --- |
| M0 | 실행 골격·포트·브라우저 접속 | 완료 |
| M1 | 정책 로딩·검증, 요청 검사, 모의 엔진 | 완료 |
| M2 | Gateway 중계·요청 ID·양쪽 기록·오류 처리 | 완료 |
| M3 | 조회 API·대시보드 | 완료 (Codex 교차 검수: 필수 수정 없음) |
| M4 | 재실행·손상 처리 보완, 시연 명령·수용 시험·보고서 | 완료 (MVP-01~11 수용 시험 통과) |
| S1 | SSE 증분 중계·3종 시간 제한·동시성 2·종료 결과 기록 | 완료 (Claude 소켓 허용 환경 unittest 96개·MVP-01~11·S1-01~06 전부 통과, 건너뜀 0, 직접 코드 검수 필수 지적 없음) |
| S2 | 정책 이력·기간/철회 유효성·기준 비교·수집 상태 | 완료 (Claude 소켓 허용 환경 unittest 104개·MVP-01~11·S1-01~06·S2-01~06 전부 통과, 건너뜀 0, 직접 코드 검수 필수 지적 없음) |
| S3A | 외부 vLLM backend 모드·고정 목적지·자격정보·수집 어댑터 | 완료 — 가짜 backend 시험만 (unittest 115개·수용 시험 31개 사례 전부 통과). 실제 vLLM 연결 아님 |
| S3B | 고정 vLLM·모델 실제 환경 시험 | 정식 S3B 미실행(승인 GPU 환경 없음). CPU 사전 점검(vLLM v0.29.0 CPU·0.5B 모델)에서 S3-01~06 통과, 엔진 내부 취소·backend 요청 ID 증거는 미확인 (`docs/S3B_실제환경_시험준비.md` §7) |

실제 vLLM 환경 시험(S3B), 인증, 직통 접근 제한, Cilium·Tetragon 통합, context 2048 사전 통제는 이후 범위입니다.

## 협업·검수

사용자 요청을 Claude가 정리하고 Codex가 구현·시험합니다. Codex는 작업을 마치면 변경 범위·새 파일·시험 명령과 결과·남은 제약·Git 기준점·커밋 여부를 인계하고, Claude가 대화형 세션에서 직접 검수합니다. 필요한 수정·재시험은 Codex가 맡고 Claude가 다시 검수해 최종 보고합니다.

자동 검수 스크립트는 기본 작업 흐름에서 사용하지 않습니다.
