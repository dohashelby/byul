# REQ-14 async 출력 후보의 GPU 거부 원인과 보수 (2026-11-02, GPU 전)

대상: Draft [dohashelby/byul#192](https://github.com/dohashelby/byul/pull/192)의 `AsyncGPUModelRunnerOutput.get_output` 관측 후보. 첫 실증 G29에서 wrap 사전 검사가 `WORKER_STARTUP_SOURCE_UNAVAILABLE`로 거부했다(대상 source SHA는 고정값과 일치).

## 원인

콜백 센서는 감쌀 함수를 고르기 전에 `vllm_cache_callback._codes()`로 원본 소스를 다시 읽는다. 이 함수는 `vllm_worker_startup_contract.artifact(path, expected)`를 기본 상한 `limit=131072`(128 KiB)로 불렀다. 파일 크기가 상한을 넘으면 SHA를 비교하기 전에 `WORKER_STARTUP_SOURCE_UNAVAILABLE`을 낸다.

| 콜백 대상 모듈 | 0.31.0 크기 | 상한 128 KiB |
|---|---|---|
| `vllm.v1.core.kv_cache_manager` | 41,801 | 통과 |
| `vllm.v1.core.block_pool` | 36,404 | 통과 |
| `vllm.v1.worker.gpu_model_runner` (#192가 추가) | 335,342 | 거부 |

0.31.0 크기와 SHA는 로컬에 있던 `vllm/vllm-openai-cpu:v0.31.0` 이미지 안의 파일을 네트워크 없이 읽어 확인했다. 세 파일의 SHA가 모두 제품 고정값과 같다(`gpu_model_runner.py`는 `bb903dff…`). GPU 이미지의 같은 파일은 GPU에서 다시 확인한다.

재현(CPU, 같은 `artifact` 함수, SHA 일치 파일): 131,072바이트는 통과, 131,073바이트부터 `WORKER_STARTUP_SOURCE_UNAVAILABLE`.

#192의 CPU 시험(`tests/test_gpu_async_output_projection.py`)은 `DistributedObserver._snapshot`을 직접 부르고 `wrap()`·`_codes()`를 지나지 않아 이 상한을 만나지 않았다.

## 상한만 풀면 생기던 문제

감싼 함수는 호출 전후로 `qualified()`를 한 번씩 부르고, 그때마다 `_codes()`가 원본을 다시 읽고 compile한 뒤 모든 code object의 digest를 냈다. 로컬 macOS arm64, Python 3.12.3, 로컬 vLLM 소스 체크아웃(v0.28.1rc0-453-ga1541f5742)에서 잰 1회 비용은 `kv_cache_manager.py`(39KB) 2.1ms, `block_pool.py`(34KB) 1.7ms, `gpu_model_runner.py`(344KB) 18.8ms였다. `get_output`은 출력 스텝마다 불리므로 상한만 풀면 스텝마다 약 38ms가 worker에 더해진다.

## 보수 (`adapters/vllm_cache_callback.py`)

1. **SHA에 묶은 정확한 크기:** `SOURCE_BYTES = {bb903dff…: 335342}`. 이 SHA의 파일만 335,342바이트까지 읽는다. 다른 파일과 다른 크기는 지금처럼 거부한다. `vllm_worker_startup_contract.py`의 기본 상한은 바꾸지 않았다.
2. **compile 결과 재사용, 바이트 확인은 매번:** 호출마다 파일을 다시 읽어 SHA를 고정값과 비교하는 것은 그대로다. SHA가 맞은 같은 바이트의 compile·digest 결과만 `(모듈, 경로, 고정 SHA)` 단위로 재사용한다. 같은 바이트의 compile 결과는 같으므로 감지 방식은 바뀌지 않는다. 처음 후보로 적었던 파일 메타데이터 비교는 쓰지 않았다.

보수 뒤 1회 확인 비용(같은 환경): `kv_cache_manager.py` 0.04ms, `gpu_model_runner.py` 0.15ms.

계약 시험(`tests/test_cache_callback.py`):
- 128 KiB를 넘는 SHA 일치 파일은 `SOURCE_BYTES`가 없거나 1바이트 작으면 거부, 정확하면 감싼다.
- 같은 크기로 내용만 바꾼 파일도 다음 호출에서 잡힌다.
- 반복 호출에서 compile은 다시 하지 않고 원본 읽기는 호출 전후마다 한다.

## 출력 대조가 0개였던 이유와 #192 없이 보는 경로 (2026-11-03)

CPU 실측(opt-125m)은 출력 대조 4개, GPU G27~G29는 모두 0개였다. vLLM 0.31.0은 pooling·일부 speculative decoding·미지원 executor 같은 예외가 없으면 async 스케줄링을 기본으로 켠다(`config/vllm.py`). 그러면 worker가 `handle_output`에 넘기는 것은 `AsyncGPUModelRunnerOutput`이고, 이 객체에는 `req_ids` 속성이 없다. 기존 센서는 출력의 `req_ids`만 읽었으므로 대조할 출력이 없었다.

이 객체는 만들 때 받은 `ModelRunnerOutput`을 `_model_runner_output`에 두고, `get_output()`은 장치 복사를 기다린 뒤 그 같은 객체를 돌려준다. 그래서 요청 ID는 `get_output()` 전후가 같다.

| 경로 | 방법 | 상태 |
|---|---|---|
| 1. 동기 세대 | 엔진을 `--no-async-scheduling`으로 띄운다. 출력이 `req_ids`를 가진 `ModelRunnerOutput`으로 바로 온다 | 코드 변경 없음. 기본 설정이 아니므로 그 세대의 결과는 동기 분기로만 기록한다 |
| 2. async 출력의 원래 출력 읽기 | 이미 설치되는 `WorkerProc.handle_output`·`enqueue_output` 지점에서, 출력이 `vllm.v1.worker.gpu_model_runner`의 `AsyncGPUModelRunnerOutput`·`AsyncGPUPoolingModelRunnerOutput`이면 `_model_runner_output.req_ids`를 읽는다. `get_output()`은 부르지 않는다. 행에 `output_ids_from_pending_async_output`을 남긴다 | `adapters/vllm_distributed_callback.py`. 새 감싸기 지점 없음, 판정 규칙 그대로 |
| 3. `get_output` 감싸기(#192) | 335KB 파일의 메서드를 감싼다 | 경로 2가 GPU에서 되면 필요 없다 |

경로 2의 계약 시험(`tests/test_cache_callback.py`):
- 고정 소스의 `WorkerProc`가 async 출력을 queue에 넣을 때 센서는 `get_output()`을 부르지 않는다.
- 출력 요청 ID는 같은 RPC의 예정 요청 ID와 같다.
- vLLM 원래 경로(`enqueue_output`)만 `get_output()`을 한 번 부른다.
- 다른 모듈의 이름만 같은 클래스는 읽지 않는다.

## #192 처리

- 경로 2로 GPU 1차에서 대조가 1개 이상 나오면 #192를 닫는다.
- 0개면 #192 브랜치로 만든 자산을 후보 세대로 한 번 띄운다. 그 전에 #192는 main과의 충돌(소스 해시 목록, `MODULES` 줄)을 풀고 `wrap()`을 지나는 시험을 더해야 한다.
- 어느 경로든 중간 tensor·TP 분기가 남아 REQ-14는 full_scope가 아니다.

## GPU에서 확인할 것

1. GPU 이미지(`vllm/vllm-openai@sha256:a4a4c04…`) 안 `vllm/v1/worker/gpu_model_runner.py`의 크기 335,342와 SHA `bb903dff…`, 기동 로그의 async 스케줄링 사용 여부.
2. 기본(async) 세대에서 `handle_output` 행의 `output_ids_from_pending_async_output=true`, 출력 귀속 대조 1개 이상.
3. `--no-async-scheduling` 세대에서 출력 귀속 대조 1개 이상. 두 세대의 결과를 섞지 않는다.
