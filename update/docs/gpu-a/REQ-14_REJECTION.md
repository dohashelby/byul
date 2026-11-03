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

## #192에 남은 일

- main에 이 보수가 들어간 뒤 #192를 다시 맞추고 소스 해시 목록을 새로 만든다. #192 자체 코드는 바꿀 필요가 없다. `DistributedObserver.wrap()`이 같은 `_codes()`를 지난다.
- #192에 `wrap()`을 지나는 시험을 더한다. 지금 시험은 `_snapshot`만 본다.

## GPU에서 확인할 것

1. GPU 이미지(`vllm/vllm-openai@sha256:a4a4c04…`) 안 `vllm/v1/worker/gpu_model_runner.py`의 크기 335,342와 SHA `bb903dff…`.
2. wrap 사전 검사 통과, `get_output` 반환 뒤 `output_request_sha256` 수집, 출력 귀속 대조 1개 이상.
3. 센서를 켠 세대와 끈 세대의 스텝 시간 차이를 기록한다.
