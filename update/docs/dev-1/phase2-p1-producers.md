# 2단계 P1: #196 공통 입력 생산자 (개발 담당)

계약은 `docs/dev-1/gpu-common-inputs-196.md`, 분담은 2단계 작업 지시서를 따른다. 생산자는 엔진 안 관측과 계약 기록 변환을 합친 것이다. 지원 생산자 목록(`SUPPORTED_PRODUCERS`) 등록은 GPU 확인 뒤에 한다. 그 전까지 기록은 형식이 맞아도 complete가 아니다.

| 종류 | 소비 점검(B) | 상태 |
|---|---|---|
| request-attempt | LIF-03·10·12 | CPU k3d 생산 확인(2026-11-05). GPU 확인·등록 남음 |
| generation-transition | LIF-05·06·07·09 | 남음 |
| process-device-provider | PLT-01·04 | 남음 |
| rank-fault | LIF-08 | 남음(TP2) |
| failed-group-resources | LIF-11 | 남음(TP2) |
| health-worker-causal | OBS-02 | 남음 |
| profiler-control | LIF-14·OBS-03 | 남음 |

## request-attempt

- 관측(`adapters/vllm_attempt_callback.py`): vLLM 0.31.0 EngineCore의 `Scheduler.add_request`·`schedule`·`_preempt_request`·`update_from_output`·`_free_request`와 `KVCacheManager.allocate_slots`·`free`를 원 소스 해시로 고정해 감싼다.
  - 엔진 콜백 센서의 선택 기능이다(`guard prepare-engine-callbacks --request-attempts`). 매 단계 scheduler를 감싸므로 켠 세션에서만 동작한다.
  - scheduler 소스는 159,016 bytes라 기본 읽기 한도(128 KiB)를 넘는다. SHA와 정확한 크기를 함께 고정했다(`SOURCE_BYTES`).
- 사건 대응:

| 계약 사건 | 근거 |
|---|---|
| admitted·queued | `add_request` 반환 |
| budget·dequeued·stage | `schedule` 반환의 요청별 배정 토큰과 단계 예산(`max_num_scheduled_tokens`) |
| kv_owned·limit_enforced | `allocate_slots` 반환(블록 수, None이면 한도 집행) |
| retry | `_preempt_request`. 선점 뒤 재계산을 같은 요청의 새 attempt로 센다(원인 preempted) |
| gpu_fence | `update_from_output` 반환. worker 단계 출력이 host에 도착함 |
| finished·abort | `_free_request` 진입 시 요청 상태 |
| kv_released | `KVCacheManager.free` 반환 |

- 변환(`collectors/gpu_input_producers.py`):
  - 원 코드가 앞뒤로 그대로였던 행만 쓴다.
  - 세대 결합은 소유 Pod 바인딩과 기록 파일의 EngineCore PID·시작 ticks다(`engine_attempt_events`).
  - `source_ended`는 고른 요청마다 종료와 KV 반환이 모두 있고 읽기가 잘리지 않았을 때만 참이다.
  - GPU 메모리 bytes는 읽지 않는다(`gpu_bytes`는 None).
- k3d CPU 확인(run 10, `byul-dev1-evidence/k3d-run10/request-attempt-record.json`):
  - 태그 요청 3개(정상 2, 스트림 중 연결 끊김 1)에서 사건 63개가 나왔다.
  - 정상 두 개는 finished, 끊긴 하나는 abort였고, 세 요청 모두 KV 반환까지 이어졌다.
  - 계약 검증 결과 gap은 `GPU_INPUT_PRODUCER_UNSUPPORTED` 하나(미등록)다. 위반 후보는 0이다.
  - 첫 기동에서는 scheduler 설치가 `ENGINE_CALLBACK_INSTALL_UNAVAILABLE`로 거부됐다(소스 크기 한도). 원 자산은 `k3d-run10/callbacks-attempt1-assets`에 있다.
- 남은 것: GPU 이미지에서 같은 확인을 하고 등록한다. 선점 경로(retry)와 KV 한도 경로(limit_enforced)는 CPU 단일 요청 규모에서 나오지 않았으므로, GPU에서 동시 요청과 작은 KV로 따로 만든다.
