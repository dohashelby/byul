# 2단계 P1: #196 공통 입력 생산자 (개발 담당)

계약은 `docs/dev-1/gpu-common-inputs-196.md`, 분담은 2단계 작업 지시서를 따른다. 생산자는 엔진 안 관측과 계약 기록 변환을 합친 것이다. 지원 생산자 목록(`SUPPORTED_PRODUCERS`)에는 GPU 확인(2026-11-05)을 마친 생산자의 소스 SHA만 등록했다. 등록은 생산자 지원일 뿐 판정 PASS가 아니며, 각 기록의 진단·누락 gap은 그대로 남는다. 생산자 소스(관측기·`gpu_input_producers.py`)가 바뀌면 SHA가 달라져 다시 미지원이 된다.

| 종류 | 소비 점검(B) | 상태 |
|---|---|---|
| request-attempt | LIF-03·10·12 | CPU k3d·GPU 확인, 등록(2026-11-05). 선점(retry)·KV 한도 경로는 GPU KV 압박 분기에서 확인 |
| generation-transition | LIF-05·06·07·09 | CPU k3d 생산 확인(2026-11-05). GPU 확인, 등록(2026-11-05). rank·graph 준비 행은 아직 없음 |
| process-device-provider | PLT-01·04 | 구현·CPU k3d 확인. GPU 확인·지원 등록은 남음(아래 절) |
| rank-fault | LIF-08 | 남음(TP2) |
| failed-group-resources | LIF-11 | 남음(TP2) |
| health-worker-causal | OBS-02 | CPU k3d 생산 확인(2026-11-05). GPU 확인, 등록(2026-11-05). vLLM 안에 health와 worker 사이 공유 인과 키가 없음(진단으로 남아 complete 아님) |
| profiler-control | LIF-14·OBS-03 | CPU k3d 생산 확인(2026-11-05). GPU 확인, 등록(2026-11-05). 세 프로세스를 잇는 공유 키 없음(진단으로 남아 complete 아님) |

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
  - GPU 확인과 등록: 2026-11-05 완료(아래 GPU 절).

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

## profiler-control

- 관측(`adapters/vllm_profiler_callback.py`): vLLM 0.31.0의 다음 지점을 원 소스 해시로 고정해 감싼다.
  - API `AsyncLLM.start_profile`·`stop_profile`
  - EngineCore `EngineCore.profile`
  - worker `Worker.profile`, `TorchProfilerWrapper._start`·`_stop`
  - gpu_worker·wrapper 해시는 native/15 profiler 센서가 검토한 값과 같다.
  - `_stop` 반환 뒤 출력 폴더의 파일 이름 지문과 크기만 남긴다. 내용은 읽지 않는다.
  - 선택 기능 `--profiler-control`이다.
- 센서 설치 정리: 같은 모듈(async_llm, core)에 선택 관측이 여럿 겹친다. 그래서 선택 관측의 설치를 지점별로 관측기를 고르는 한 함수로 합쳤다(`_instrument_optional`).
- 변환:
  - 사건: `api_call`, `rpc`, `worker_control`, `backend_state`, `stop_failed`, `output_complete`(파일 목록 지문·파일 수), `next_collection`
  - causal 지문은 행마다 고유하다. API → EngineCore → worker로는 `is_start`와 접두어만 가고 요청 ID가 없다. 이를 `PROFILER_CAUSAL_ID_ABSENT_IN_ENGINE` 진단으로 남긴다.
- k3d CPU 확인(run 13, `byul-dev1-evidence/k3d-run13/profiler-control-record.json`):
  - 시작·중지를 두 번 했다. API(PID 1)·EngineCore(PID 74)·worker(PID 112)에서 사건 24개가 나왔다.
  - 두 번 모두 시작 → 중지 → 출력 완료 → 다음 수집 순서였다.
  - API 서버도 자기 torch profiler를 돌려 같은 출력 폴더에 쓴다. 그래서 파일 목록만으로는 어느 프로세스의 파일인지 가를 수 없다(한계).
  - 처음 두 번은 중지 때 엔진이 OOMKilled됐다(4 GiB·6 GiB 한도, 센서를 끈 대조에서도 같음). vLLM 기본 설정(with_stack·gzip·device time 표)의 CPU lab 메모리 사용 때문이다. lab에서는 그 세 옵션을 끄고 6 GiB로 실행했다. 로그는 `k3d-run13/engine-attempt{1,2}-oom.log`에 있다.
- 남은 것:
  - backend 실제 수집 상태와 trace callback(LIF-14). torch profiler 안쪽 관측이 필요하다.
  - 프로세스별 출력 파일 구분. 출력 폴더를 프로세스별로 나누거나 trace 이름의 rank 접미어를 쓴다.
  - GPU 확인과 등록: 2026-11-05 완료(아래 GPU 절).

## GPU 확인(2026-11-05, AWS g6.2xlarge L4, 인계 01~03 재실행 뒤)

- 구성: 세대 `v1-producers`(V1 runner·prefix caching·개발 경로·torch profiler, MP backend 없음)와 KV 압박 분기 `v1-producers-kv`(max_model_len 512·KV 블록 40). 준비 자산은 다섯 선택 관측과 SUP-03(`--supply-root /models --supply-watch`)을 함께 켰다.
- 요청: `tools/gpu_a_producers_traffic.py`(시험 Pod 안, 표준 라이브러리), 수집: `tools/gpu_a_producers_collect.py`(소유 Pod 바인딩 확인 → 한 번 읽기 → 종류별 계약 기록과 `validate()` 결과).
- 원자료: `byul-dev1-evidence/gpu-20261105/byul-a/a-p1-20261105-{1..5}`(묶음 SHA256 `8bb4682d…e95d`). 모두 lab 관측이며 MEASURED 판정이 아니다.

| 세션 | 내용 | 결과 |
|---|---|---|
| -1 | MP backend 포함 첫 시도 | REQ-14 분산 관측 행이 프로세스 파일 16 MiB를 먼저 채워 모든 기록이 잘림(`*_READ_TRUNCATED`). 세대에서 MP를 빼고 읽기 한도를 14 MiB(pipe 16 MiB)로 올림 |
| -2 | 같은 요청, 태그 `byul-gp-…` | runner-consumption만 API 행과 runner 행이 이어지지 않음. 센서는 요청 ID 안의 `byul-cb-<32 hex>`만 지문으로 쓰고, 그 밖의 ID는 vLLM이 붙이는 무작위 접미사까지 지문에 들어간다. 요청 태그 형식을 고침 |
| -3 | 전체 요청 21단계 모두 HTTP 200 | 여섯 기록 모두 `source_ended` 참 |
| -4 | KV 압박 첫 시도(2048·160블록·1,300토큰) | 계약 사건 상한 4,096 초과(`GPU_INPUT_EVENTS_INVALID`)와 파일 16 MiB 도달. 계약 상한은 그대로 두고 분기를 줄임 |
| -5 | KV 압박(400토큰 동시 두 개) | `retry` 1(선점), `limit_enforced` 117 |

- 세션 -3 기록(계약 gap은 미등록 `GPU_INPUT_PRODUCER_UNSUPPORTED` 외 아래만):
  - request-attempt: 요청 14개, 사건 2,383. 13개 finished, 스트림 끊김 1개 abort, 14개 모두 KV 반환.
  - generation-transition: 사건 826. 진행 중 요청 하나가 `/collective_rpc reload_weights` 진입 때 in_flight였고 `mixed_generation_consumption` 후보가 나왔다(CPU run 11과 같은 모양). `model_loaded`·`backend_ready`·`service_gate` 행이 함께 나왔다.
  - health-worker-causal: 사건 620. `HEALTH_WORKER_CAUSAL_LINK_ABSENT_IN_ENGINE` 진단(설계대로).
  - profiler-control: 사건 12. API·EngineCore·worker 사슬(`api_call`·`rpc`·`worker_control`·`backend_state`·`output_complete`). `PROFILER_CAUSAL_ID_ABSENT_IN_ENGINE` 진단(설계대로).
- 세션 -5: profiler를 쓰지 않아 profiler-control 기록은 바인딩 0개로 계약이 형식 오류로 거부했다. 수집 도구가 이를 결과(`contract_error`)로 남긴다. generation-transition은 전환을 일으키지 않아 필수 사건이 없다.
- 남은 것:
  - `SUPPORTED_PRODUCERS` 등록: 여섯 생산자(request-attempt·generation-transition·health-worker-causal·profiler-control·runner-consumption·supply-reads)를 세션 -3 기록의 생산자 SHA와 같은 현재 소스 SHA로 등록했다. `tests/test_gpu_common_inputs.py`가 등록 값과 현재 소스 SHA의 일치를 확인한다.
  - process-device-provider, rank-fault·failed-group-resources(TP2 필요).
  - 긴 요청은 단계마다 사건이 쌓여 계약 상한 4,096에 닿는다. 단계 사건 요약 방식은 계약 소비자와 합의할 과제다.

## process-device-provider CPU 준비(2026-11-05)

- `collectors/process_device_observer.py`는 노드 PID 이름공간의 `/proc`에서 소유 Pod UID와 container ID가 함께 있는 cgroup만 고른다. NSpid 사슬·시작 ticks·실효 UID·cgroup 해시·열린 장치 fd·자격 환경 변수 이름·마운트 사용을 읽는다. 읽기 전후 프로세스 신원과 cgroup이 같아야 행을 쓴다.
- NVML host PID와 NSpid 첫 값을 연결해 `gpu_process`를 만든다. 엔진 컨테이너 안 장치 열기 시험은 ioctl 없이 O_RDWR로 열고 바로 닫는다. 같은 실효 UID의 결과만 해당 프로세스에 적용하며, 이미 열린 fd는 별도 근거로 보존한다.
- 환경 변수·마운트 읽기 실패나 잘림은 `unknown`과 진단으로 남긴다. 자격 후보 32개 또는 장치 64개 상한을 넘기면 누락을 숨기지 않는다. Secret 값·파일 본문은 저장하지 않으며 자격 세대는 미관측(null)이다.
- `collectors/process_device_producer.py`에서 #196 형식으로 변환한다. 관측기와 변환을 별도 모듈로 두어 기존 등록 생산자 여섯 종의 소스 SHA는 유지한다.
- 수집: `tools.gpu_a_producers_collect --node-view sudo`(GPU 호스트) 또는 `--node-view pod:<namespace>/<name> --node-dev-root /host/dev`(k3d 노드 관측 Pod). 제품용 노드 관측 주체의 배포는 별도 범위다.
- CPU 원자료 `byul-dev1-evidence/k3d-run19/`: 기존 run18 엔진의 컨테이너 프로세스 4개(namespace PID 1·75·76·113)를 골랐다. CPU 노드라 NVML·GPU device가 없어 필수 행은 비었고 `source_ended=false`다. lab 관측 Pod는 수집 뒤 삭제했다. 이 원자료는 Claude 작성 시점 소스의 결과이며 후속 보수 후 최종 소스와 구분한다.
- GPU 확인·지원 등록 전이다. CPU 관측이나 합성 장치 데이터로 PLT-01·04 완료 또는 PASS를 주장하지 않는다. 사건 상한·V2 logit bias 계약 과제는 `phase2-contract-questions.md`에 기록했다.
- 인계 후 최종 소스 검증: 직접·공통 계약·불변식 289 통과/기존 1 제외(pytest 1.46초, 부모 1.634초). 최종 소스로 같은 run18 엔진을 다시 읽어 namespace PID 1·75·76·113과 NVML/GPU 미관측 gap을 재확인했다. 임시 `byul-codex-node-view` 네임스페이스는 삭제 확인했고 기존 engine/probe는 유지했다. 원자료는 `byul-dev1-evidence/20261105-codex-takeover/process-device-final.json`이며 이 생산자 SHA는 `442ea49b…93451`이다.
