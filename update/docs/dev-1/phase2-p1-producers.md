# 2단계 P1: #196 공통 입력 생산자 (개발 담당)

계약은 `docs/dev-1/gpu-common-inputs-196.md`, 분담은 2단계 작업 지시서를 따른다. 생산자는 엔진 안 관측과 계약 기록 변환을 합친 것이다. 지원 생산자 목록(`SUPPORTED_PRODUCERS`) 등록은 GPU 확인 뒤에 한다. 그 전까지 기록은 형식이 맞아도 complete가 아니다.

| 종류 | 소비 점검(B) | 상태 |
|---|---|---|
| request-attempt | LIF-03·10·12 | CPU k3d 생산 확인(2026-11-05). GPU 확인·등록 남음 |
| generation-transition | LIF-05·06·07·09 | CPU k3d 생산 확인(2026-11-05). rank·backend·graph 준비 행은 아직 없음. GPU 확인·등록 남음 |
| process-device-provider | PLT-01·04 | 남음 |
| rank-fault | LIF-08 | 남음(TP2) |
| failed-group-resources | LIF-11 | 남음(TP2) |
| health-worker-causal | OBS-02 | CPU k3d 생산 확인(2026-11-05). vLLM 안에 health와 worker 사이 공유 인과 키가 없음(진단으로 기록). GPU 확인·등록 남음 |
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

## generation-transition

- 관측(`adapters/vllm_transition_callback.py`): vLLM 0.31.0 EngineCore의 세대 전환 진입점을 원 소스 해시로 고정해 감싼다.
  - 대상: `collective_rpc`(메서드가 `reload_weights`일 때만 전환), `add_lora`·`remove_lora`, `reset_prefix_cache`, `pause_scheduler`·`resume_scheduler`·`sleep`·`wake_up`, `set_weight_version`
  - 진입 시 scheduler가 가진 요청을 지문으로 남긴다.
  - 선택 기능 `--generation-transitions`이고, request-attempt 기록과 함께 써야 한다.
- 변환:
  - 세대 이름은 구성요소별 성공 전환 횟수다(`weights-0` → `weights-1`).
  - 단계 배정 시점의 세대가 `consumed`다. 전환 진입 시 요청은 `in_flight`다.
  - 접수 차단 중이 아니면 전환 성공 직후 `service_gate` open이다. 차단 중이면 재개 때 open이다.
  - 실패(RAISE)는 `failure`, 다음 성공은 `recovery`다.
- k3d CPU 확인(run 11, `byul-dev1-evidence/k3d-run11/generation-transition-record.json`, dev 경로를 켠 lab 엔진):
  - 200토큰 요청 A가 진행 중일 때 `/collective_rpc reload_weights`를 보냈다. A는 전환 진입 때 in_flight였다. 이전 세대 24단계, 새 세대 176단계를 소비했고, 계약의 `mixed_generation_consumption` 후보가 나왔다. CPU vLLM은 진행 중 요청을 비우지 않고 새 가중치로 이어서 디코딩한다. 같은 가중치 파일을 다시 읽었으므로 답변 차이는 이 실험으로 판단하지 않는다.
  - `/reset_prefix_cache` 뒤 요청 B는 gate 이후 접수(`accepted`)됐고, `weights-1`·`cache-1`을 소비했다.
  - 계약 검증 gap은 미등록 하나다. 사건은 214개다.
- 읽기 수정: 처음에는 worker 프로세스의 다른 관측기 행(4.4 MB)이 읽기 한도를 먼저 써서 EngineCore 행이 잘렸다. 지금은 고른 행(attempt·transition·GAP)만 한도에 센다.
- 남은 것:
  - rank·모델·backend·graph 준비 완료 행(LIF-07). worker 기동 관측이 필요하다.
  - 실제 서비스 gate(API health와 접수의 결합). health-worker-causal과 함께 만든다.
  - GPU 확인과 등록.

## health-worker-causal

- 관측(`adapters/vllm_health_callback.py`): vLLM 0.31.0 `AsyncLLM.check_health`(API 서버 프로세스)를 원 소스 해시로 고정해 감싼다.
  - 이 함수는 worker에 묻지 않는다. 클라이언트 쪽 표식(`engine_core.resources.engine_dead`)과 출력 처리 task가 살아 있는지만 본다.
  - 관측은 그 두 값을 판단 시점에 그대로 남긴다.
  - coroutine 함수라 반환 시점은 coroutine 생성 시점이다. 판단 값은 진입 시점 표식으로 정해지므로 진입 행을 근거로 쓴다.
  - 선택 기능 `--health-causal`이고, request-attempt와 함께 쓴다.
- 변환:
  - `api_health`: healthy / unhealthy / unknown
  - `worker_state`: EngineCore `update_from_output` 반환(output) 또는 예외(raised)
  - `request_accepted`: EngineCore 접수
  - 세대는 프로세스(PID·시작 ticks)다.
  - causal 지문은 행마다 고유하다. vLLM 안에는 health와 worker 상태를 잇는 공유 키가 없기 때문이다. 이 사실을 `HEALTH_WORKER_CAUSAL_LINK_ABSENT_IN_ENGINE` 진단으로 기록에 남긴다. 소비자는 시간 근접으로 둘을 합치지 않는다(#196 OBS-02 거부 조건).
- k3d CPU 확인(run 12, `byul-dev1-evidence/k3d-run12/health-worker-record.json`):
  - API 서버(PID 1)의 `api_health` healthy 5건이 나왔다. 명시 호출 4건과 kubelet 준비 검사가 함께 잡혔다.
  - EngineCore(PID 74)의 `worker_state` output 3건, `request_accepted` 1건이 나왔다.
  - 계약 gap은 미등록과 공유 인과 키 부재 진단이다.
- 의미: OBS-02(API health와 실제 worker 상태의 인과)는 vLLM 0.31 기본 health 경로로는 확인할 수 없다. 인과를 보려면 worker 왕복이 있는 health(예: runtime의 별도 준비 확인)가 필요하다. runtime 담당과 합의할 항목에 더한다.
