# serving-runtime
BoB serving-engine security runtime solution

- 개발계획서 v1.4(문서 상태: 개발 기준 설계안)가 전달됐으며, 직접 개발할 모듈의 언어는 Python으로 선택했습니다. 연동 규격·실제 환경 등은 미결입니다.
- Codex는 구현·수정·시험을, Claude는 검수를 담당합니다.

## 개발 현황 한눈에 (2026-10-24)

| 구분 | 내용 | 확인 수준 |
| --- | --- | --- |
| 요청 통제 Gateway | 정책 파일 검증, 허용/정책 거부/입력 오류 판정, 서버 발급 요청 ID, 검증된 필드만 중계 | 모의 엔진·실제 vLLM(CPU) |
| 스트리밍 (S1) | SSE 즉시 중계, 시작·청크 무응답·전체 시간 제한, 동시 처리 2개 초과 시 429, 시작/최종 결과 분리 | 모의 엔진·실제 vLLM(CPU) |
| 정책·상태 (S2) | 정책 schema 1.1(기간·승인 근거), 철회 파일, 정책 이력, 기준/현재 비교, 수집원 상태 | 모의 엔진·fixture |
| 외부 vLLM 연결 (S3A) | 설정 파일로 고정된 목적지·모델 별칭·자격정보, health·모델·버전·지표 수집 | 가짜 backend·실제 vLLM(CPU) |
| 기록·조회 | 요청별 시작/최종 JSONL 기록(프롬프트 원문 미저장), 조회 API, 증거 JSON 내려받기 | 모의 엔진·실제 vLLM(CPU) |
| 대시보드 | 현황·요청 목록·상세 증거·정책/기준 상태, 3초 갱신, 읽기 전용 | 브라우저 수동 확인 |
| context 사전 통제 | 정책 `max_context_tokens`(예: 2048): backend `/tokenize`로 입력 토큰을 세어 입력+`max_tokens` 초과 시 403, 셀 수 없으면 503(모두 엔진 미전달) | 모의 엔진·가짜 backend·실제 vLLM(CPU) |
| 모델 파일 무결성 | 승인 자산 목록(경로·SHA-256·크기)과 디스크 파일 주기 비교, 변경·누락·목록 밖 파일 표시 | 단위·HTTP 시험, 실제 모델 파일 목록 생성 확인 |
| 재검증 요청 | 자산 변경·engine API 구성 drift·같은 판본 정책 내용 변경을 JSONL 요청으로 열고 조회·내보내기 | 단위 시험 완료; 검증 도구 연동 형식 D-03 미합의 |
| 통신·행위 통제 준비 (P2 사전 작업) | Cilium 규칙 후보 생성, Tetragon 파일 규칙 후보·이벤트 변환, 관측 API·대시보드 | 로컬 kind에서 Cilium 통제·Hubble 실제 관측 통과, Tetragon 감시는 귀속 한계와 함께 확인, 차단은 불안정 |
| 시험 | unittest 157개, 수용 시험 31개 사례(`mvp-acceptance`), 시연 명령(`mvp-demo`), CPU 사전 점검 도구 | Claude 소켓 허용 환경 157개 전부 통과; 사전 점검 S3-01~06 통과(요청 로그 필수·엔진 재시작 포함) |

**확인 수준의 뜻**
- 모의 엔진·가짜 backend: 저장소 안 시험용 서버로 확인했습니다.
- 실제 vLLM(CPU): 이 PC에서 vLLM CPU v0.29.0과 0.5B 모델로 S3-01~06을 사전 점검했습니다. 결과는 `docs/S3B_실제환경_시험준비.md` §7에 있습니다.
- 계획상의 **정식 S3B(승인 GPU 환경·vLLM v0.31.0·선정 모델) 시험은 아직 하지 않았습니다.**

**아직 하지 않은 것**
- 정식 S3B 실제 환경 시험
- 정식 환경의 요청 단위 backend 증거·엔진 내부 취소: CPU 사전 점검에서는 vLLM `--enable-log-requests --enable-request-id-headers`로 확인했으나 GPU 고정 버전에서 재확인이 필요합니다.
- context 통제의 고정 버전 확인: `/tokenize` 형식은 vLLM v0.29.0 CPU에서만 확인했습니다.
- 모델 파일 쓰기 차단(읽기 전용 마운트·권한)과 엔진 메모리 로딩 확인: 지금은 디스크 해시 비교만 합니다.
- 정식 GPU·Kubernetes 1.36.5 환경의 Cilium·Hubble·Tetragon 검증: 현재 결과는 WSL2의 kind v1.37.0과 시험 전용 가짜 backend 기준입니다.
- Tetragon 차단의 불안정 원인 규명과 pod·process 귀속 재확인
- API 인증, SGLang

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

### 코드 구조

- `app.py`: Gateway 구성요소를 생성·연결하고 상태 조합과 시작·종료를 담당합니다.
- `http_handler.py`: HTTP 응답 도우미, 정적 파일 allowlist, API·POST 경로 분기를 담당합니다.
- `request_flow.py`: 채팅 요청의 검사·기록·중계 흐름과 동시성 슬롯을 관리합니다.
- `relay.py`: 일반 응답과 SSE stream을 backend에서 클라이언트로 중계합니다.
- `engine_client.py`: 토큰 수·상태·정보·지표·모의 수신 증거를 backend에서 조회합니다.
- `request_records.py`: 요청 기록을 구성하고 JSONL 저장 상태와 요청별 context를 관리합니다.
- `backend.py`: backend 설정을 검증하고 고정 목적지 연결과 헤더를 생성합니다.
- `collectors.py`: 상태 수집 주기와 snapshot·baseline 비교를 관리합니다.
- `revalidation.py`: 재검증 조건 판정, JSONL 이벤트 상태·조회 요약·배타적 내보내기를 담당합니다.
- `policy.py`·`policy_state.py`: 정책 문서 검증과 기간·철회·이력 판정을 담당합니다.
- `store.py`·`query.py`: JSONL 입출력과 요청 요약·상세 조회를 담당합니다.

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

### context 사전 통제

정책 schema `runtime-mvp/1.1`의 선택 필드 `max_context_tokens`를 넣으면 켜집니다(예: `examples/mvp/policy-v1.1-context.json`, 2048). 값은 `output_max_tokens`보다 커야 합니다.

- Gateway는 일반·stream 생성 요청을 보내기 전에 backend의 `POST /tokenize`(`model`, `messages`, `add_generation_prompt: true`)로 입력 토큰 수(`count`)를 받습니다. vLLM 모드에서는 고정 `base_url` 경로 접두와 Authorization을 생성 요청과 동일하게 적용하며, 문자 길이로 추정하지 않습니다.
- 입력 토큰 + 적용 `max_tokens`가 한도를 넘으면 일반·stream 모두 403 `CONTEXT_LIMIT_EXCEEDED`이며 생성 요청을 보내지 않습니다.
- 연결 실패·backend 오류뿐 아니라 HTTP 200 응답에 `count`가 없거나 bool·음수이거나 JSON을 해석할 수 없는 모든 경우도 통과시키지 않고 503 `CONTEXT_CHECK_UNAVAILABLE`으로 기록합니다. backend 예외 메시지는 응답·요청 기록에 남기지 않습니다.
- 기록과 상세 화면에 입력 토큰·한도·판정이 남습니다. 토큰 세기 요청에는 `<요청 ID>-tokenize` 요청 ID를 붙입니다.
- 모의 엔진과 가짜 backend의 `/tokenize`는 단어 수 기반의 결정적 계산이며 실제 tokenizer가 아닙니다. 실제 vLLM v0.29.0 CPU에서는 짧은 질문 36토큰 허용, 2129토큰 입력 거부(생성 미전달)를 확인했습니다. 선정 GPU 고정 버전의 응답 형식은 다시 확인해야 합니다.

### 모델 파일 무결성 점검

승인한 모델·tokenizer 파일 목록과 디스크 파일을 주기적으로 비교합니다. 해시 비교이며 쓰기를 막거나 엔진이 메모리에 올린 내용을 증명하지 않습니다.

```bash
# 현재 파일로 목록 초안 만들기 (관측값이므로 사람이 검토한 뒤 승인 목록으로 사용)
.venv/bin/python -m serving_runtime assets snapshot --root /path/to/model --out assets.json
# Gateway에 연결 (기본 60초마다 점검)
.venv/bin/python -m serving_runtime mvp --asset-manifest assets.json [--asset-interval 60]
```

- 목록 형식 `runtime-asset-manifest/1.0`: `root`(절대경로)와 `files[{path, sha256, size}]`. 경로는 `root` 안 상대경로만 허용하며, `root` 자체가 심볼릭 링크면 목록 로드·생성·검사를 거부합니다. `assets snapshot` 출력은 기존 파일·깨진 링크를 덮어쓰지 않고 0600 모드로 원자적 생성합니다.
- 결과는 수집원 표의 `asset_integrity` 행에 일치·변경·누락·읽기 불가·링크·목록 밖 파일·목록 밖 링크·순회 오류 수로 표시하며, 하나라도 다르면 `intact: false`입니다. 순회 오류는 상대경로·오류 종류를 최대 50개 남기며, 해당 오류가 있으면 `snapshot`은 부분 목록을 쓰지 않고 실패합니다. 목록을 매번 다시 읽으므로 목록 파일 오류도 수집 오류로 드러납니다.
- 심볼릭 링크는 따라가지 않습니다. 항목의 `root` 아래 모든 상위 구성 요소를 확인하고 파일을 `O_NOFOLLOW`로 열어, 중간 디렉터리가 `root` 밖을 가리키는 링크인 경우도 읽지 않고 `symlink`로 보고합니다. Hugging Face 캐시의 `snapshots/<revision>` 폴더는 파일이 모두 링크라서 거부되므로, 실제 파일이 있는 폴더(예: `--local-dir`로 받은 모델 폴더)를 지정합니다.
- 자산 검사는 다른 health·metrics 수집원과 분리된 백그라운드 스레드에서 수행합니다. 검사 결과에 `duration_s`를 남기고, 이전 검사가 끝난 시점부터 `--asset-interval` 후에 다음 검사를 시작합니다.

### 재검증 요청

Gateway는 수집 결과에서 승인 상태와 다른 근거를 발견하면 `<data-dir>/revalidation_requests.jsonl`에 `runtime-revalidation-request/1.0` 이벤트를 추가합니다. 초기 트리거는 자산 무결성 실패, baseline의 `engine_api` 출처 drift, 시작 시 정책 이력의 같은 ID·판본 내용 변경입니다. 같은 근거는 SHA-256 fingerprint로 중복을 막습니다. 관측된 scope에서 근거가 완전히 사라졌을 때만 같은 요청 ID에 `condition_cleared`를 기록하고, 같은 scope의 근거가 달라지면 기존 요청에 `superseded`와 새 요청 ID를 남깁니다. 해소되거나 대체된 근거가 다시 나타나면 `condition_reappeared`를 기록합니다. `superseded`는 다른 근거로 대체된 상태이지 해소가 아닙니다. 요청 `status`는 계속 `open`이며 이 기능은 승인·요청 종료·검증 실행·외부 전송을 하지 않습니다.

`GET /api/revalidation`과 읽기 전용 대시보드에서 요청별 최신 이벤트·현재 근거 존재 여부·출처, 대체 상태("다른 근거로 대체됨, 해소 아님"), 손상 줄과 평가 오류를 확인할 수 있습니다. 열린 요청 수는 과거 요청을 포함해 유지하고, 현재 근거가 존재하는 요청 수는 `current_condition_count`로 따로 제공합니다. `/api/status`와 내보내기 문서에도 두 집계를 함께 표시하며, 검증 도구 전달 파일은 기존 파일을 덮어쓰지 않습니다.

```bash
.venv/bin/python -m serving_runtime revalidation export \
  --data-dir ~/.local/state/serving-runtime/mvp --out revalidation-export.json
```

내보내기 형식은 문서 안에 **검증 도구 연동 형식 초안(D-03 미합의)**으로 명시합니다. 수집·판정·저장 실패는 재검증 상태에 오류로 남지만 Gateway 추론 경로를 중단하지 않습니다.

### 외부 vLLM backend 모드 (S3A)

기본은 기존 모의 엔진 모드입니다. 외부 backend 모드는 Gateway만 띄우고 모의 엔진을 실행하지 않으며, 실제 엔진의 시작·종료도 관리하지 않습니다.

```bash
.venv/bin/python -m serving_runtime mvp --backend vllm --backend-config backend.json
```

설정 파일은 `runtime-backend/1.0` 형식이며 예시는 `examples/mvp/backend-vllm.example.json`입니다(가짜 주소, 키 없음).

- `base_url`: backend 주소(http/https, 경로 접두 허용). 요청 본문·헤더(`Host`, `X-Forwarded-*` 등)로 바꿀 수 없고, 클라이언트 헤더는 backend로 전달하지 않습니다. 3xx redirect는 따라가지 않고 502로 처리하며, https 인증서 검증은 끌 수 없습니다.
- `model_map`: 정책 허용 모델명 → backend 모델 ID. 매핑 없는 허용 모델은 backend를 호출하지 않고 503 `BACKEND_MODEL_UNMAPPED`입니다.
- `credential`: `{"env": "변수명"}` 또는 `{"file": "경로"}` 중 하나. 키 값은 설정 파일·CLI에 넣지 않습니다. 설정했는데 값이 없으면 503 `BACKEND_CREDENTIAL_MISSING`, 공백·제어문자나 HTTP 헤더에 쓸 수 없는 문자가 있으면 503 `BACKEND_CREDENTIAL_INVALID`로 backend를 호출하지 않습니다. 파일이 다른 사용자에게 읽히면 `insecure_file` 상태로 표시합니다. 키는 기록·API·화면·보고서에 남기지 않습니다.
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
umask 077; python3 -c "import secrets,sys;sys.stdout.write(secrets.token_urlsafe(32))" > $D/vllm-api-key
printf 'VLLM_API_KEY=%s\n' "$(cat $D/vllm-api-key)" > $D/vllm.env
docker run -d --name s3b-vllm-cpu -p 127.0.0.1:8000:8000 --memory 6g --shm-size 1g \
  --env-file $D/vllm.env -e VLLM_CPU_KVCACHE_SPACE=1 -e HF_HOME=/hf -v $D/hf-cache:/hf \
  public.ecr.aws/q9t5s3a7/vllm-cpu-release-repo:v0.29.0 \
  --model Qwen/Qwen2.5-0.5B-Instruct --revision 7ae557604adf67be50417f59c2c2f167def9a775 \
  --served-model-name qwen2.5-0.5b-instruct --dtype bfloat16 --max-model-len 2048 \
  --max-num-seqs 2 --enforce-eager --enable-log-requests --enable-request-id-headers \
  --host 0.0.0.0 --port 8000
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health   # 200이면 준비 완료
```

`--enable-log-requests --enable-request-id-headers`는 Gateway 요청 ID의 backend 수신 여부와 취소 요청의 `aborted` 로그를 요청 단위 증거로 남기기 위한 옵션입니다. 2026-10-23 v0.29.0 CPU 재실행에서 이 로그에 프롬프트 원문은 남지 않음을 확인했습니다.

2. `$D/backend.json`을 `examples/mvp/backend-vllm.example.json` 형식으로 만듭니다. `model_map`은 `{"test-model": "qwen2.5-0.5b-instruct"}`로, `credential`은 `{"file": "<$D 절대 경로>/vllm-api-key"}`로 둡니다. CPU는 느리므로 `timeouts.relay_timeout_s`를 120 정도로 늘립니다.

3. Gateway를 외부 vLLM 모드로 띄웁니다. 아래처럼 띄우면 터미널이나 Claude를 닫아도 계속 실행됩니다. PC를 재시작하거나 WSL을 종료하면 꺼집니다.

```bash
cd ~/bobproject/serving-runtime
setsid nohup .venv/bin/python -u -m serving_runtime mvp --backend vllm --backend-config $D/backend.json \
  --gateway-port 18080 --data-dir $D/data > $D/gateway.log 2>&1 < /dev/null &
```

브라우저에서 `http://127.0.0.1:18080`을 엽니다. 위쪽 배지는 health가 정상이면 "외부 vLLM 모드 · 연결됨 · 정식 GPU 시험(S3B) 전", 아니면 문제 색의 "외부 vLLM 모드 · 연결 안 됨 · 정식 GPU 시험(S3B) 전"으로 표시됩니다.

4. 끄거나 다시 켜는 명령입니다.

```bash
kill $(ss -ltnp | grep '127.0.0.1:18080 ' | grep -o 'pid=[0-9]*' | cut -d= -f2)   # Gateway 끄기
docker stop s3b-vllm-cpu      # vLLM 끄기 (메모리 약 4.5GiB 반환)
docker start s3b-vllm-cpu     # vLLM 다시 켜기 (준비 2~3분)
```

CPU에서는 예열 뒤 16토큰 응답에 5~7초가 걸립니다. 이 결과를 GPU 성능이나 정식 S3B 통과로 해석하지 않습니다.

CPU 사전 점검 도구의 기본 실행은 `.venv/bin/python tools/s3b_cpu_precheck.py`이며, 엔진 중단·복구까지 허용할 때 `--allow-engine-restart`, S3-01~03에 backend 요청·취소 로그를 필수 증거로 요구할 때 `--require-backend-request-log`를 붙입니다(두 옵션은 함께 쓸 수 있음). 종료 코드는 모든 사례 통과 0, 하나라도 실패 1, 키·실행 대상 사전 확인 실패 2, 미실행 사례가 있는 부분 점검 3입니다. 도구는 공백·개행·제어문자 등이 든 키 파일을 값 노출 없이 거부합니다.

이 실제 vLLM 모드의 요청은 `127.0.0.1:18080`으로 보냅니다. 아래 "요청 보내기" 예시의 `8080`은 모의 엔진 모드입니다.

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

stream은 backend SSE 바이트를 전체 버퍼링하지 않고 즉시 전달합니다. HTTP 200이 시작된 뒤에도 `[DONE]` 없는 종료, 청크 무응답, 전체 제한 초과, client 연결 종료, 중계 오류는 최종 기록에서 성공이 아닌 `ERROR`로 남습니다. Gateway는 이때 backend 연결을 닫습니다. vLLM v0.29.0 CPU를 요청 로그 옵션과 함께 실행한 사전 점검에서는 취소 요청의 `aborted` 로그를 확인했지만, 이는 정식 GPU S3B 증거가 아닙니다. context 제한은 아래 "context 사전 통제"를 참고합니다.

### 시연하기

터미널 두 개를 사용합니다.

1. 첫 번째 터미널에서 `.venv/bin/python -m serving_runtime mvp`로 서버를 실행하고 그대로 둡니다.
2. 두 번째 터미널에서 `.venv/bin/python -m serving_runtime mvp-demo`를 실행합니다. 사례별 기대·실제 결과 표와 저장된 결과 JSON 경로가 출력됩니다. Gateway 주소를 바꿨다면 `--gateway URL`, 기록 위치를 바꿨다면 `--data-dir PATH`를 함께 지정합니다.
3. 브라우저에서 `http://127.0.0.1:8080`을 열고 **현황 숫자 → 정책 거부 행 클릭 → “Gateway 미전달, 엔진 미수신 확인” → 증거 내려받기** 순서로 확인합니다.
4. 첫 번째 터미널에서 `Ctrl+C`로 서버를 종료한 뒤 같은 명령으로 재실행합니다. 이전 기록이 유지되고 요청 목록에 **이전 실행** 태그가 나타나는지 확인합니다.

### 대시보드

- **현황**: 결과별 건수, 정책 판본·SHA-256·로딩 시각, 모의 엔진 연결 점검, 기록 저장 상태·손상 줄 수와 위치. 네트워크·프로세스·파일 관측은 저장 자료에 따라 "미연결"·"합성 데이터"·"실제 수집"으로, GPU 관측은 "미연결"로 표시합니다.
- **통신·행위 관측**: Hubble 통신과 Tetragon 프로세스·파일 최신 기록을 각각 표시합니다. 합성 자료는 "합성" 배지가 붙고 허용 목록 밖 실행 파일은 강조됩니다.
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
- `observations.jsonl`: CLI로 변환한 Hubble·Tetragon 관측 기록(`data_origin` 필수)

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
| S3B | 고정 vLLM·모델 실제 환경 시험 | 정식 S3B 미실행(승인 GPU 환경 없음). CPU 사전 점검(vLLM v0.29.0 CPU·0.5B 모델)에서 S3-01~06 통과했고 요청 로그 옵션 재실행으로 요청 ID·취소 로그를 확인함. 정식 GPU 증거 아님 (`docs/S3B_실제환경_시험준비.md` §7) |

실제 vLLM 환경 시험(S3B), 인증과 정식 환경의 직통 접근 제한은 이후 범위입니다. Cilium·Hubble·Tetragon의 kind 최종 실행 결과와 남은 제약은 아래에 구분해 적습니다.

## 통신·행위 통제 준비 (P2 사전 작업)

이 단계의 산출물은 **Cilium 1.20.3 / Tetragon 1.7.1 문법 기준 후보**로 시작했습니다. 최종 확인 환경은 WSL2(커널 6.18.33.2-microsoft-standard-WSL2)의 kind v0.33.0 단일 노드(kindest/node v1.37.0), Cilium 1.20.3+Hubble, Tetragon 1.7.1입니다. Tetragon에는 `deploy/lab/tetragon-values.yaml`을 적용했습니다. kind 노드 버전은 개발계획서 v1.4에서 선정한 Kubernetes 1.36.5와 다릅니다.

준비된 `runtime-lab` kind 클러스터에서의 lab 실행 순서는 다음과 같습니다. Gateway는 기존 규칙대로 pod 안의 `127.0.0.1:8080`에만 바인딩되며, 점검 도구가 `kubectl exec`로 같은 pod 안에서 요청합니다. 도구는 현재 context도 정확히 `kind-runtime-lab`인지 먼저 확인하고, 기본적으로 적용한 정책을 유지합니다(`--cleanup`을 주면 정책만 정리).

```bash
docker build -f deploy/lab/Dockerfile -t serving-runtime-lab:dev .
kind load docker-image --name runtime-lab serving-runtime-lab:dev
kubectl --context kind-runtime-lab apply -f deploy/lab/manifests/
helm upgrade tetragon cilium/tetragon --version 1.7.1 -n kube-system --reuse-values -f deploy/lab/tetragon-values.yaml
.venv/bin/python -B tools/k8s_lab_check.py \
  --context kind-runtime-lab --namespace serving-lab \
  --state-dir ~/.local/state/serving-runtime/k8s-lab
```

위 `helm upgrade` 단계의 `tetragon-values.yaml`은 export를 `serving-lab`의 `PROCESS_EXEC`·`PROCESS_EXIT`와 네 lab 정책의 `PROCESS_KPROBE`로 한정해 호스트 이벤트 폭증을 막습니다. WSL+kind에서는 kprobe의 pod namespace가 비므로 kprobe allowlist는 namespace 대신 policy name을 사용하고, denylist는 health check와 `cilium`·`kube-system` namespace만 제외해 빈 namespace를 버리지 않습니다. 필터 필드는 Tetragon 1.7.1 공식 문서 기준 후보입니다.

최종 실행에서 L-01 기준선이 통과했습니다. L-02는 Gateway 경유 요청과 DNS를 허용하면서 other-client의 엔진 직통 연결과 엔진의 외부 연결을 실제로 차단했습니다. L-03은 Hubble의 실제 `DROPPED` 흐름을 `enforce ingest --origin real`로 변환해 통과했습니다.

L-04 Tetragon 감시에서는 TG-ASSET-01 쓰기, TG-SECRET-01 읽기, 허용 목록 밖 실행을 실제 로그에서 확인했습니다. 다만 이 환경에서는 kprobe 이벤트의 pod·process 귀속이 비어 있었고(`pod_info` 조회 실패), 정책 이름·경로·접근 종류·시각으로 판정했습니다. exec 이벤트는 pod 정보 재시도로 수 초~수십 초 늦게 export됐습니다.

L-05 TG-DENY-01은 적용 직후 5회 모두 `EPERM`이었지만, 별도 관측에서는 적용 약 1분 뒤 쓰기가 다시 성공했고 podSelector 없는 클러스터 범위 정책은 처음부터 차단하지 못했습니다. WSL 커널의 modify-return 제한 경고가 의심되지만 원인은 확정하지 못했으므로, 이 환경의 Tetragon 차단 실효는 신뢰할 수 없습니다. L-06 정리는 옵션을 지정하지 않아 실행하지 않았고 전체 결과는 `partial`입니다.

보고서는 저장소 밖 `~/.local/state/serving-runtime/k8s-lab/reports/k8s-lab-20261023T202226Z-162136.{json,md}`에 있습니다. 이는 로컬 kind/WSL 결과로 정식 GPU·Kubernetes 1.36.5 환경 검증이 아니며, 엔진도 시험 전용 가짜 backend입니다.

엄격한 `runtime-enforcement-intent/1.0` JSON 예시는 `examples/enforcement/intent.example.json`입니다. 알 수 없는 필드, 빈 label, 잘못된 port, 상대경로를 거부합니다. 생성 파일은 지정한 출력 폴더의 고정 파일명만 사용합니다.

```bash
.venv/bin/python -m serving_runtime enforce render \
  --intent examples/enforcement/intent.example.json --out /tmp/runtime-enforcement
```

- Cilium 후보는 engine ingress를 Gateway·metrics collector의 engine TCP port로 한정하고, engine egress를 DNS TCP/UDP 53과 명시한 FQDN/CIDR 목적지로, Gateway egress를 engine TCP port와 DNS로 한정합니다. 선택된 endpoint에 ingress/egress 절이 존재하므로 해당 방향은 그 허용 규칙 외 default deny가 됩니다.
- Tetragon 후보는 `security_file_permission`의 모델 쓰기(`TG-ASSET-01`), 민감 경로 읽기(`TG-SECRET-01`), cache 쓰기 관측(`TG-CACHE-01`)을 만듭니다. `monitor`에는 Override가 없습니다. `enforce_probe`의 `TG-DENY-01`만 지정 `probe_path` 쓰기에 `Override argError -1` 후보를 만듭니다. 최종 WSL+kind 확인에서도 실제 쓰기 차단이 안정적으로 유지되지 않았습니다.
- `TG-EXEC-01` 정책은 따로 만들지 않습니다. Tetragon 기본 `process_exec` 이벤트의 binary를 intent의 `allowed_engine_binaries`와 비교해 어댑터가 `allowed_binary`를 판정합니다. 목록이 비어 있거나 intent 없이 변환하면 판정은 `null`입니다.

공식 출력 모양을 흉내 낸 fixture는 `tests/fixtures/enforcement/`에 있으며 실제 수집 자료가 아닙니다. 호출자가 `synthetic` 또는 `real`을 반드시 지정해야 하고 Tetragon `arguments`는 저장하지 않습니다.

```bash
.venv/bin/python -m serving_runtime enforce ingest --kind hubble \
  --input tests/fixtures/enforcement/hubble_flows.synthetic.jsonl \
  --origin synthetic --data-dir /tmp/runtime-observations
.venv/bin/python -m serving_runtime enforce ingest --kind tetragon \
  --input tests/fixtures/enforcement/tetragon_events.synthetic.jsonl \
  --origin synthetic --data-dir /tmp/runtime-observations \
  --intent examples/enforcement/intent.example.json
```

변환 성공 건은 `<data-dir>/observations.jsonl`에 추가됩니다. 형식 오류·미지원 이벤트는 원문 대신 줄 번호와 사유로 거부 집계합니다. MVP를 같은 `--data-dir`로 실행하면 `GET /api/observations`와 대시보드에서 source·kind·verdict·rule ID·자료 출처별 건수, 최신 50건, 손상 줄을 읽을 수 있습니다.

## 협업·검수

사용자 요청을 Claude가 정리하고 Codex가 구현·시험합니다. Codex는 작업을 마치면 변경 범위·새 파일·시험 명령과 결과·남은 제약·Git 기준점·커밋 여부를 인계하고, Claude가 대화형 세션에서 직접 검수합니다. 필요한 수정·재시험은 Codex가 맡고 Claude가 다시 검수해 최종 보고합니다.

자동 검수 스크립트는 기본 작업 흐름에서 사용하지 않습니다.
