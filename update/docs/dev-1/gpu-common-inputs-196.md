# #196 GPU 공통 센서 입력 공개 계약 (묶음 6, 2026-11-05)

GPU 실증 담당이 #196에서 요청한 공통 입력의 형식을 공통 계층에 정했다. 코드는 `src/guard/collectors/gpu_common_inputs.py`, 시험은 `tests/test_gpu_common_inputs.py`다.

이 계약은 **생산자가 아니다**. 1단계에는 GPU가 없어 지원 생산자 목록(`SUPPORTED_PRODUCERS`)이 비어 있었다. 2단계에는 실제 GPU 확인과 현재 소스 SHA 대조를 마친 여덟 생산자 이름·판본만 등록했다. 목록에 없는 생산자나 소스가 다른 기록은 형식이 맞아도 `supported=False`, `complete=False`이며 소비자는 그 기록으로 PASS를 만들 수 없다. 등록된 생산자의 진단·누락 gap도 그대로 적용한다.

## 공통 봉투 `guard-gpu-common-input/1`

| 항목 | 내용 |
|---|---|
| 생산자 | 이름·판본·소스 SHA-256. 목록의 SHA와 다르면 지원 아님 |
| 범위 | 실제 ENV Ref, target SHA-256, prepared SHA-256, 세션(32 hex). 하나라도 다르면 거부 |
| 세대 결합 | Pod UID, container ID, restart 횟수, worker PID(1 이상), start ticks, rank. 행마다 이 목록의 번호를 가리킨다. 같은 세대 중복은 거부 |
| 행 | 종류별 닫힌 필드. 문자열은 닫힌 식별자 또는 SHA-256만 받는다. 비밀 원문은 들어올 수 없다 |
| 완전성 | 원 기록 끝 도달, 누락 수, 생산자 진단 코드 |

결과는 다음 값을 따로 낸다.
- `supported`, `complete`
- `gaps`: 누락·지원 불일치
- `violation_candidates`: 위반 후보. 확정이 아니며, 뒤의 수집 오류가 지우지 않는다.
- `producer_authenticated`: 항상 False

## 종류와 소비 점검

| 종류 | 소비 점검 | 완료 주장에 필요한 행 | 형식만으로 내는 위반 후보 |
|---|---|---|---|
| `request-attempt` | LIF-03·10·12 | admitted, finished, gpu_fence, kv_released | 새 attempt 없는 retry, 끝난 attempt의 소비 |
| `generation-transition` | LIF-05·06·07·09 | in_flight, consumed, service_gate | 같은 요청·구성요소의 세대 혼합 소비 |
| `rank-fault` | LIF-08 | fault_applied, fault_released, detected, recovered | acknowledgment 없는 주입 |
| `failed-group-resources` | LIF-11 | inventory_before, inventory_after | 해제 기록 없이 실패 뒤 목록에 남은 자원 |
| `process-device-provider` | PLT-01·04 | gpu_process, device_access | 없음(정책 대조는 소비자) |
| `health-worker-causal` | OBS-02 | api_health, worker_state | 없음 |
| `profiler-control` | LIF-14·OBS-03 | api_call, rpc, worker_control, output_complete | 없음 |

#196의 거부 조건과 대응한다.
- RSS·집계 queue·NVML 숫자만 있으면 `request-attempt` 필수 행이 없어 완료가 아니다.
- Pod Ready나 모델 별칭만으로는 `generation-transition`의 rank 준비·service gate 행이 없어 완료가 아니다.
- 파일 생성만으로는 rank fault의 ack 행이 없어 후보로 남는다.
- shm 개수나 프로세스 종료만으로는 자원별 전후 목록이 없어 완료가 아니다.
- 공통 process 기록 v1의 false 고정 값은 바꾸지 않는다. 새 종류 `process-device-provider`는 지원 생산자가 생기기 전까지 완료가 되지 않는다.

## 2단계에서 할 일

1. GPU lab에서 종류별 생산자를 만든다. 대부분 기존 native lifecycle 센서 계열을 확장한다.
2. 그 판본·소스 SHA를 `SUPPORTED_PRODUCERS`에 더한다.
3. GPU 담당 소비자가 `validate()` 결과를 읽어 기존 판정에 연결한다(GPU 담당 파일).
4. 행 형식을 실제 생산자에 맞춰 바꿔야 하면 schema 판본을 올리고 이 문서를 고친다.

## `/4` 확정 사항 (2026-11-08·11-09 사용자 결정, 미구현)

`guard-gpu-common-input/4`는 아래 다섯 가지를 한 번에 담는다. `/1`·`/2`·`/3` 기록은 계속 읽는다. `/2`·`/3`은 다른 확장에 이미 사용 중이므로 새 확장은 `/4`로 구분한다(작업지시서 결정 4). 생산자 소스가 바뀌어 등록이 풀리므로 TP2 GPU 세션에서 한 번에 다시 확인·등록한다. 근거: [11-08 결정 3절](user-decisions-20261108.md), [11-09 결정 2절](user-decisions-20261109.md).

| 항목 | 내용 | 엔진 중립 이유 |
|---|---|---|
| 반복 행 요약 | 같은 요청·attempt·단계의 연속 `stage`·`gpu_fence`·`kv_owned`(transition `consumed`, health `worker_state`)를 묶는다. 값이 바뀔 때마다 끊고, 첫·끝 행은 원 행으로 남긴다. 요약 행은 첫·끝 시각, 개수, 최대값을 가진다. 상한 4,096은 그대로다. | 출력 길이에 비례하는 행 수를 엔진과 무관하게 줄인다 |
| V2 logit bias 비교 | sampler 행 `logit_bias`가 null이 아니면 API 값과 비교한다(다르면 `value_changed_before_consumption` 후보). null이면 지금처럼 gap. | 관측이 있을 때만 비교를 더한다 |
| KV 단위 | KV 값은 엔진 고유 단위 그대로 두고 단위 필드(예: `kv_unit`: `block`·`token_slot`)와 알면 단위당 token 수(예: `kv_unit_tokens`)를 함께 적는다. 단위를 모르면 값도 null이다. | vLLM은 블록, SGLang은 token slot(page) 단위다 |
| 한도 집행 | `limit_enforced`는 모든 한도 집행이다. 집행 방식 필드(예: `alloc_refused`·`preempted`·`rejected`)를 둔다. SGLang retraction은 이 사건(`preempted`)과 `retry`를 함께 낸다. | vLLM은 할당 거부, SGLang은 retraction으로 집행한다 |
| 생산자 신원 | `source_sha256`에 관측기·변환 파일과 함께 공통 감싸기 기반 파일(예: `adapters/vllm_cache_callback.py`)을 넣는다. | 기반이 바뀌면 자동으로 미지원이 된다 |

필드 이름은 구현 때 정하고 이 표를 고친다. 소비자(GPU 담당)는 `/1`~`/4`를 모두 읽고, `/4` 요약 행을 원 행처럼 존재·순서·마지막 값 판단에 쓸 수 있어야 한다.
