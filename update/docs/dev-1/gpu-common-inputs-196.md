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

## `/4` 구현 (2026-11-08·11-09 사용자 결정, M-A2)

`guard-gpu-common-input/4`는 아래 다섯 가지를 함께 담는다. `/1`·`/2`·`/3` 기록은 계속 읽는다. `/2`·`/3`은 범위·actor 확장에 이미 쓰므로 새 형식은 `/4`다(작업지시서 결정4). 실제 GPU 확인 전까지 새 생산자를 등록하지 않는다. 근거: [11-08 결정3절](user-decisions-20261108.md), [11-09 결정2절](user-decisions-20261109.md).

| 항목 | 실제 필드와 동작 |
|---|---|
| 반복 요약 | 모든 행에 `source_seq`(변환 전 순번)와 `repeat`(원 행은 null)를 둔다. 같은 binding·요청·attempt·단계·값의 연속 stage/gpu_fence/kv_owned, transition consumed, health worker_state 네 행 이상을 첫 원 행·중간 요약·끝 원 행으로 묶는다. 값·세대·인과 키가 바뀌면 끊는다. 요약의 `first_seq`·`last_seq`·`first_at_ms`·`last_at_ms`·`count`·`max_values`로 원 행 범위를 남긴다. `seq`는 전송 행 순번이다 |
| 상한 | 요약 후에도 최대4,096행이다. 요약 불가능한 초과 기록은 그대로 거부하며, 소비자는 요약을 count만큼 펼치지 않는다. 전송 순서·원 순번·양 끝 원 행·값·최대값을 검증한다 |
| V2 logit bias | `/4`의 sampler `logit_bias`가 null이 아니면 API 개수와 비교해 차이를 `value_changed_before_consumption` 후보로 낸다. null이면 미관측 gap이다. `/1`의 기존 gap/비교 의미는 보존한다 |
| KV 단위 | `/4`는 과거 `kv_blocks` 대신 엔진 고유 수치 `kv_units`, 단위 `kv_unit`(block/token_slot), 알 때만 양수 `kv_unit_tokens`를 쓴다. 단위를 모르면 수치와 단위당 token 수는 null이며, 버린 수치가 있으면 KV_UNIT_UNOBSERVED를 남긴다. 수치를 token이나 다른 엔진 단위로 환산하지 않는다 |
| 한도 집행 | `limit_enforced` 행에 `enforcement_method`(alloc_refused/preempted/rejected)를 둔다. 미관측 null은 완료를 막는 gap이다. 다른 사건은 null이어야 한다. 관측된 할당 거부와 preemption을 각각 내며 preemption은 기존 retry도 남긴다 |
| 생산자 신원 | 관측기·변환과 공통 감싸기 기반·엔진 단위 데이터·계약/직렬화 파일의 정확 bytes를 SHA에 포함한다. 예전8개 GPU 등록 행은 보존한다. `/4`는 별도 `SUPPORTED_V4_PRODUCERS` 표로 정확 wire 자격을 확인하며 J01 전에는 비어 있다. 예전 등록 SHA를 주장해도 `/4` 자격으로 이월하지 않는다 |

이 구현은 `collectors/gpu_common_input_v4.py`다. 기존 record 함수는 역사 재생을 위해 기본 `/1`을 유지하고 `wire_schema="guard-gpu-common-input/4"`로 새 형식을 만든다. `tools.gpu_a_producers_collect`의 단순 생산자와 공급 읽기 수집은 `/4`를 명시한다. 현재 runner 실수집의 `/3` actor 범위/자격 경로는 보존하며 `/4`로 낮추지 않는다. V2 변환은 명시한 `/4` 호출에서 새 비교를 제공한다.

소비자는 원 행 수를 반영한 event_counts와 KV 단위·집행 방식을 반환한다. 첫·끝 행과 요약의 마지막 시점/값으로 존재·순서·마지막 값을 읽을 수 있다. 요약은 등록·완전성·독립 worker 신원·실제 소비 검증을 대신하지 않는다. 생산자 인증은 항상 false이고 공통 입력만으로 원 질문 전체 완료를 만들지 않는다. GPU 재확인/등록은 J01 후속이다.
