# S3B 실제 vLLM 환경 시험 준비

작성일: 2026-10-21
상태: **정식 S3B 미실행.** CPU 사전 점검(아래 §7)만 수행했다. 이 문서는 실제 환경 시험에 필요한 입력과 절차를 정리한 것이며, 정식 GPU S3B 연결이나 시험 통과의 증거가 아니다.

근거: MVP 이후 개발계획서(스트리밍·정책/상태·vLLM v0.1) §6 S3B와 개발계획서 v1.4 부록 I 선정안. S3A(외부 backend 모드·고정 목적지·자격정보·수집 어댑터)는 시험 전용 가짜 backend로만 확인했다.

## 1. 현재 환경 확인 결과

2026-10-21 Claude가 이 WSL 환경을 읽기 전용으로 확인했다. 설치·다운로드는 하지 않았다.

| 항목 | 결과 |
| --- | --- |
| GPU | `nvidia-smi` 없음, `/usr/lib/wsl/lib`에 CUDA 라이브러리 없음 |
| Docker | 서버 29.1.3 접근 가능, 런타임은 runc만 있음(nvidia 런타임 없음) |
| vLLM | 미설치, vLLM/CUDA 이미지 없음, 실행 중인 vLLM 서버 없음 |
| Kubernetes | 없음 |
| 자원 | CPU 16개, RAM 약 7GiB |

결론: 이 환경에서는 정식 GPU S3B 시험을 할 수 없다. 승인된 GPU LAB이 준비되면 아래 입력을 채운 뒤 진행한다.

## 2. 시험 전에 채울 입력

없는 값은 추측하지 않는다. 확인한 값만 적고 나머지는 "미확인"으로 둔다.

| 입력 | 현재 값 | 확인 방법 |
| --- | --- | --- |
| 장비·GPU·드라이버·OS | 미확인 | 승인 LAB에서 `nvidia-smi`, `uname -a`로 실측. WSL GPU를 추정하지 않음 |
| 엔진 image·버전·digest | 미확인 | v1.4 부록 I 선정안(vLLM v0.31.0)과 실제 실행 image digest 대조. 다르면 기록 |
| 모델·tokenizer revision | 미확인 | 고정 revision 확인. 모델 ID 일치는 weight·revision 증명이 아님 |
| backend 주소·health/metrics 경로·권한 | 미확인 | 환경 담당이 제공한 주소로 최소 접속만 확인 |
| 모델 별칭·chat template·실제 context 처리 | 미확인 | 고정 모델의 응답·설정 대조. context 2048 사전 통제는 이 확인 후 별도 구현 |
| backend 측 미전달·취소 증거원 | 미확인 | 요청 ID가 남는 backend 로그, 격리 구간 지표. 민감정보 비노출 확인 |
| 자격정보 공급 방식 | 미확인 | 환경 변수 또는 권한 600 파일. 대화·로그·CLI 인자에 붙이지 않음 |

## 3. backend 설정 파일 작성

`examples/mvp/backend-vllm.example.json`을 복사해 확인된 값으로 바꾼다. 저장소에 커밋하지 않는 위치에 둔다.

- `base_url`: 승인된 vLLM 주소. 원격이면 https 사용(인증서 검증은 끌 수 없음)
- `model_map`: 정책의 허용 모델명 → 실제 backend 모델 ID
- `credential`: `{"env": "VLLM_API_KEY"}` 또는 `{"file": "/보호된/경로"}` (파일 권한 600)
- `metrics_credential`: metrics endpoint 인증이 필요할 때만 별도 env/file로 설정. 미설정 시 metrics 요청에 추론용 `credential`을 보내지 않음
- `metrics_url`, `metrics_map`: 실제 `/metrics` 응답에서 이름·타입·단위를 확인한 뒤 매핑. 확인 전에는 `verified: false` 유지

## 4. 실행·재현 명령

```bash
cd ~/bobproject/serving-runtime

# 자격정보는 화면에 남지 않게 공급 (예: 환경 변수를 미리 설정한 셸에서 실행)
.venv/bin/python -m serving_runtime mvp --backend vllm --backend-config /보호된/경로/backend.json

# 다른 터미널: 일반·stream·거부 확인
curl -i -H 'Content-Type: application/json' \
  -d '{"model":"test-model","messages":[{"role":"user","content":"hi"}],"max_tokens":64}' \
  http://127.0.0.1:8080/v1/chat/completions
curl -N -H 'Content-Type: application/json' \
  -d '{"model":"test-model","messages":[{"role":"user","content":"hi"}],"max_tokens":64,"stream":true}' \
  http://127.0.0.1:8080/v1/chat/completions
curl -i -H 'Content-Type: application/json' \
  -d '{"model":"test-model","messages":[{"role":"user","content":"hi"}],"max_tokens":129}' \
  http://127.0.0.1:8080/v1/chat/completions
```

시험 시각, 대상 주소(자격정보 제외), image digest, 모델 revision, 요청 ID를 함께 기록한다.

## 5. 실제 수용 시험별 증거 수집

| ID | 확인할 것 | 증거 |
| --- | --- | --- |
| S3-01 | 같은 고정 모델로 일반·stream 실제 추론과 종료 | Gateway 기록(요청·정책·실행 ID), stream `[DONE]`, backend 로그의 같은 요청 ID |
| S3-02 | 금지 모델·출력 초과 사전 거부 | Gateway 기록 `attempted: false` + backend 로그에 해당 요청 ID 없음. backend 증거를 못 얻으면 "Gateway 미전달 확인 / backend 미수신 미확인"으로 기록 |
| S3-03 | 취소·timeout·동시성 초과 | Gateway 최종 결과·슬롯 회수(`/api/status`의 in_flight). 엔진 내부 생성 취소는 backend 로그·지표로만 확인하고 Gateway 기록만으로 단정하지 않음 |
| S3-04 | 모델·상태·선정 지표 | 수집원별 출처·시각·단위, 실제 지표 이름과 `metrics_map` 대조. 응답에 없는 지표는 미지원/확인 불가 |
| S3-05 | 엔진 중단 / 수집만 중단 / 복구 | 추론 오류(502/504)와 수집 오류가 대시보드에서 따로 표시되는지 |
| S3-06 | mock/실제 모드 전환과 이전 기록 | 요청별 `backend_kind`로 구분, 모의 엔진 수신 기록을 실제 증거로 쓰지 않음, 기존 수용 시험 회귀 |

전역 지표 카운터의 전후 비교는 다른 요청이 없는 격리 구간에서만 보조 근거로 쓴다. 모의 시험 결과나 HTTP 200만으로 실제 통과 처리하지 않는다.

## 6. 하지 않는 것

- 승인 없는 vLLM 설치, 모델 다운로드, `docker pull`, 컨테이너·클러스터·운영 환경 변경
- 새 GPU·비용 지출 결정
- Cilium·Tetragon, 인증 시스템, SGLang, 전체 성능 인수로 범위 확대
- S3B 결과를 인증·직통 격리·전체 엔진 옵션·context 사전 통제 완료로 해석

## 7. CPU 사전 점검 결과 (2026-10-21, 정식 S3B 아님)

사용자 승인으로 이 WSL에서 vLLM CPU 이미지를 받아 S3-01~06을 사전 점검했다. 선정 환경과 다르므로 **S3B 통과로 세지 않는다.**

| 항목 | 값 |
| --- | --- |
| 엔진 | `public.ecr.aws/q9t5s3a7/vllm-cpu-release-repo:v0.29.0`, repo digest `sha256:66064cf683152e60eff2e5b18ec2d4ace236fb063c0a241f918691db9caddcf4`, `/version` 응답 `0.29.0` |
| 모델 | `Qwen/Qwen2.5-0.5B-Instruct` revision `7ae557604adf67be50417f59c2c2f167def9a775`, served name `qwen2.5-0.5b-instruct`, 정책 별칭 `test-model` |
| 실행 조건 | CPU(AVX2, GPU 없음), `--dtype bfloat16 --max-model-len 2048 --max-num-seqs 2 --enforce-eager`, `VLLM_CPU_KVCACHE_SPACE=1`, docker `--memory 6g`, 127.0.0.1:8000, API 키 필수(권한 600 파일) |
| 선정 환경과 차이 | GPU 아님, v0.29.0(선정 v0.31.0의 CPU 이미지 없음), 0.5B 소형 모델, eager·bfloat16 |
| 보고서 | `~/.local/state/serving-runtime/s3b-cpu/reports/s3b-cpu-20261021T064056Z.json` (저장소 밖, 비밀값 미포함) |

| ID | 결과 | 관측 |
| --- | --- | --- |
| S3-01 | 통과 | 일반 200·실제 응답, stream 첫 이벤트 0.96초·전체 7.3초·`[DONE]` 종료, 두 요청 모두 `backend_kind: vllm`·final `completed` |
| S3-02 | 통과 | 금지 모델·129 모두 403, Gateway `attempted: false`. 격리 구간의 vLLM `request_success_total`·`prompt_tokens_total` 전후 동일, vLLM 로그에 해당 요청 ID 없음(보조 근거). 화면은 설계대로 "backend 미수신은 미확인" 유지 |
| S3-03 | 통과(Gateway 측) | client 중간 종료 → final `client_disconnected`, slot 회수, 동시 2개 진행 중 세 번째 429, 앞 두 stream 정상 종료. **엔진 내부 생성 취소는 미확인**: 기본 로그에 abort 기록이 없었다 |
| S3-04 | 통과 | `/version` 0.29.0, `/v1/models`로 매핑 모델 확인. 지표 `num_requests_running`·`num_requests_waiting`·`kv_cache_usage_perc`·`time_to_first_token_seconds`·`prompt_tokens_total`·`generation_tokens_total`은 실제 존재, 구버전 `gpu_cache_usage_perc`는 없음(`unsupported_or_absent`) |
| S3-05 | 통과 | metrics만 잘못된 주소 → metrics 수집 error·추론 200 유지. 엔진 중지 → 추론 502·health 수집 error. 재시작 후 health ok·추론 200 |
| S3-06 | 통과 | vLLM 모드 기록은 모두 `backend_kind: vllm`, 같은 data_dir의 mock 모드 요청은 `received`, 이전 vLLM 요청은 mock receipt 없이 `gateway_only` |

비밀값(API 키)은 Gateway 기록 파일에서 발견되지 않았다.

발견 사항:
- 기본 설정에서는 Gateway의 `X-Request-ID`와 엔진 내부 취소가 로그에 남지 않았으나, `--enable-log-requests --enable-request-id-headers`로 2026-10-23 재실행했을 때 허용 요청 ID 기록·거부 요청 ID 부재·취소 요청 `aborted` 로그를 확인했다(보고서 `~/.local/state/serving-runtime/s3b-cpu/reports/s3b-cpu-20261023T055248Z.json`). 이는 CPU 사전 점검 결과이며 정식 GPU S3B 증거가 아니다.
- float32로 처음 띄웠을 때 메모리 한도로 OOM이 났고, 첫 요청은 예열로 약 70초가 걸렸다. 이후 16토큰 응답에 5~7초가 걸렸다.
