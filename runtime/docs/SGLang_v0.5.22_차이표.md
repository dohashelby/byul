# SGLang v0.5.22 연결 차이 (소스 대조)

기준: 읽기 전용 sparse 소스 `sglang-src/v0.5.22`. 프레임워크가 가리킨 `SGLang_v0.5.22.md` 원문은 이 저장소에 없다. 아래는 Python 서버 기준이며 실제 GPU 실행은 미확인이다.

| 항목 | vLLM 연결 | SGLang v0.5.22 | runtime 조치·미확인 |
| --- | --- | --- | --- |
| chat 출력 한도 | `max_tokens` | `max_completion_tokens` | 경로별 전달. 실제 사용량 미확인 |
| completions 출력 한도 | `max_tokens` | `max_tokens` | 동일. 실제 사용량 미확인 |
| 모델·길이 | `/v1/models` | `/v1/models`의 본 모델에 `max_model_len`, LoRA 항목은 `null` (`python/sglang/srt/entrypoints/http_server.py:1926`) | 본 모델 선택·길이 수집 |
| 상태 | `/health` | `/ready`는 생성 없이 준비 상태 반환, `/health`는 구성에 따라 생성 가능 (`http_server.py:698,705,731`) | 수집은 `/ready`; 시작 중 503 실측 미확인 |
| 버전 | `/version` | `/server_info`에 실행 설정도 포함 (`http_server.py`) | 자동 호출하지 않음, target version 미선언·digest 대조 |
| 로그 | `Received request…` | `SGLANG_LOG_REQUEST_HEADERS`의 허용 헤더와 JSON `request.received`/`request.finished` (`python/sglang/srt/utils/request_logger.py:33,100,175`) | 레벨 0·JSON·x-request-id. 실제 출력과 프롬프트 미기록 미확인 |
| 토큰화 | `/tokenize` | `/tokenize`에 vLLM의 `add_generation_prompt` 옵션 없음 (`http_server.py`) | 경계 실측 시 `usage`와 대조 필요 |
| 실행 | vLLM 비root wrapper | 이미지 기본 명령 `/bin/bash`, `/usr/bin/python3`가 `/opt/sglang/bin/python` 실행 (`docker/Dockerfile`) | Python 서버·UID 2000 시도. 실제 권한·경로 미확인 |
| 포트·지표 | 8000·`/metrics` | 실행 인자 `--port 8000`, `--enable-metrics` (`http_server.py`, `arg_groups/fields/observability.py`) | Service·intent 포트 유지. 실제 지표 미확인 |
| 관리 경로 | 중간 서버 허용 목록 | `/update_weights_from_disk` 등 별도 관리 API (`http_server.py`) | 중간 서버 미허용, 비파괴 노출 확인 필요 |
| 단계 | vLLM 로그 수집 | 이번 범위에서 신호 미연결 | `--phase-timeline` 끔, AWS-11·16·17 판정 unknown |
