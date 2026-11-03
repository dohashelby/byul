# 담당 A 기록 (엔진 내부와 요청 경로)

A가 맡은 점검 13개: REQ-08, REQ-09, REQ-10, REQ-14, REQ-16, REQ-17, REQ-18, REQ-19, REQ-20, REQ-21, LIF-13, SUP-03, SUP-08. 시작 기준은 main `2f28942`이다. 이 폴더의 내용은 GPU 실측이 아니며, 실측 결과는 `update/docs/evidence/gpu-a-*.json`에 따로 남긴다.

## 0단계(GPU 켜기 전) 결과

| 항목 | 상태 | 문서 |
|---|---|---|
| A 작업 브랜치 | `codex/gpu-a-pre-gpu-20261102` | — |
| 비용 장부 | 양식만 만듦 | `COST_LEDGER.md` |
| 엔진 준비 점검표 | GPU를 켠 뒤 채움 | `ENVIRONMENT.md` |
| REQ-16·17 정책 초안 | DRAFT, `confirmed_by` 비움. 팩 rev 32 형식·타입 검사 통과 | `policy-a-draft.yaml` |
| REQ-17 통계 설계 | 확정. 사례 생성기와 계약 시험 추가 | `REQ-17_DESIGN.md`, `tools/gpu_a_req17_cohort.py` |
| REQ-14 거부 원인 | 소스에서 특정, CPU로 재현, 보수 | `REQ-14_REJECTION.md` |
| 센서 보수 3건 | 구현·계약 시험 완료(사용자 승인 2026-11-02). GPU 확인 전 | 아래 |
| REQ-16 lab 문맥 | 어댑터 내용·가중치 세대 바꾸기와 미디어 사례 실행기 구현, 계약 시험 완료(2026-11-03). 멀티모달 모델 선정과 GPU 확인 전 | `REQ-16_CONTEXTS.md`, `tools/gpu_a_lab_variants.py` |
| AWS 구성 | 팀 인계 묶음 기준 세대별 배포 덧씌우기 도구와 절차서, 콜백 점검 입력 조립 도구(2026-11-03). G-A3 REQ-08·09·SUP-08은 Docker 의존으로 결정 필요 | `AWS_RUNBOOK.md`, `tools/gpu_a_aws_lab.py`, `tools/gpu_a_run_inputs.py` |

## 센서 보수 (A 소유 파일, GPU 확인 전)

| 번호 | 내용 | 파일 | 풀리는 것 |
|---|---|---|---|
| 1 | SHA `bb903dff…`(`gpu_model_runner.py`)만 정확한 크기 335,342바이트까지 읽는다. 다른 파일은 128 KiB 그대로 | `adapters/vllm_cache_callback.py` | REQ-14 async 출력 관측 설치(#192) |
| 2 | 원본 바이트 SHA 확인은 호출마다 하고, SHA가 맞은 같은 바이트의 compile·digest만 재사용한다 | `adapters/vllm_cache_callback.py` | 호출당 확인 2.1ms → 0.04ms(`kv_cache_manager`), 18.8ms → 0.15ms(`gpu_model_runner`), 로컬 macOS 측정 |
| 3 | 엔진에 설치하는 콜백 센서가 판정에 쓰이지 않는 블록 단위 함수(`BlockPool.get_cached_block`, `BlockHashToBlockMap.get_one_block`)를 감싸지 않는다 | `adapters/vllm_engine_callback_sensor.py` | REQ-17 적중 길이에 비례하던 계측 편향과 기록 예산 초과 |
| 4 | async 출력(`AsyncGPUModelRunnerOutput`)이면 `get_output()`을 부르지 않고 그 안의 원래 출력에서 요청 ID를 읽는다(2026-11-03) | `adapters/vllm_distributed_callback.py` | GPU 기본 async 스케줄링에서 0개였던 REQ-14 출력 대조. #192 없이 진행 |

- 2는 처음 후보(파일 메타데이터가 같으면 재사용)보다 엄격하게 구현했다. 파일 교체 감지는 지금처럼 바이트 SHA 비교다.
- 3은 엔진 센서의 설치 목록만 바꿨다. 범용 관측기 `vllm_cache_callback.TARGETS`와 그 계약 시험은 그대로다.
- 센서 소스가 바뀌었으므로 GPU에서는 `prepare-engine-callbacks` 자산을 새로 만들어야 한다. 첫 실증 자산은 `load_prepared`가 거부한다.

## 1단계 정책 값

2026-11-03 사용자 결정으로 아래 점검은 EXAMPLE 값으로 시험한다(`policy-a-draft.yaml`). 결과는 EXAMPLE 등급으로 남기고 기업 승인값으로 쓰지 않는다. SUP-03의 템플릿 SHA는 GPU에서 서빙 템플릿을 재서 넣는다.

| 점검 | 정책 키 |
|---|---|
| REQ-08 | `reused_token_field`, `reused_token_allowed`, `rejection_codes` |
| REQ-09 | `max_input_tokens`, `rejection_codes`, `allowed_endpoints`, `allowed_input_formats` |
| REQ-10 | `allowed_sampling_options`, `rejection_codes` |
| SUP-03 | `allowed_template_sha256` |
| SUP-08 | `code_selection_fields`, `code_selection_allowed`, `rejection_codes` |
| REQ-14·18·19·20·21, LIF-13 | 실행마다 정하는 `sensor_*_sha256` 4개만 |

REQ-16은 네 문맥을 모두 이번 범위에 넣는다(2026-11-03 사용자 결정). 시험 방법, 구현, lab 배포 요건은 `REQ-16_CONTEXTS.md`에 있다.

## 기록 규칙

- 증거 묶음 이름은 `GA01`, `GA02` …. 첫 실증의 `G01`~`G33`, B의 `GB..`와 겹치지 않는다.
- 요약 결과는 `update/docs/evidence/gpu-a-<점검>-<날짜>.json`. 원자료 묶음은 비밀값을 뺀 뒤 저장소 밖에 두고 위치와 ZIP SHA256을 요약에 적는다.
- 점검마다 이슈에 시작 보고(점검 ID, 세대, 계획한 분기)와 결과 보고(관측한 분기, 남은 분기, 증거 위치, PR)를 남긴다.
