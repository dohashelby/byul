# 2단계 생산자 계약 과제 (개발 담당, 2026-11-05)

#196 공통 입력 계약(`guard-gpu-common-input/1`, `collectors/gpu_common_inputs.py`)은 소비자(B)와 맞춘 형식이다.
아래 두 가지는 생산자 쪽에서 드러났지만 계약의 의미를 바꾸므로 혼자 고치지 않는다. B와 합의한 뒤 schema 판본을 올려 반영한다.
그 전까지 현재 계약 그대로 기록하고, 남는 gap은 그대로 둔다.

## Q1. 긴 요청이 사건 상한 4,096에 닿는다

### 관측
- GPU 세션 `a-p1-20261105-3`(요청 14개, 짧은 출력)에서 행 대부분이 단계마다 반복되는 행이었다.
  - request-attempt 2,383개 중 `stage` 757·`gpu_fence` 771·`kv_owned` 771. 요청 하나당 약 165개다.
  - generation-transition 826개 중 `consumed` 819.
  - health-worker-causal 620개 중 `worker_state` 599.
- 세션 `a-p1-20261105-4`(2048 토큰·KV 압박, 1,300 토큰 요청)는 request-attempt 기록이 4,096을 넘어 계약이 `GPU_INPUT_EVENTS_INVALID`로 거부했다.
  세대 설정을 줄인 `-5`(400 토큰 두 개)로 확인했다. 계약 상한은 바꾸지 않았다.
- 출력 토큰 수에 비례해 단계 행이 늘어난다. 실제 서비스 길이(수천 토큰)의 요청 몇 개만으로도 상한을 넘는다.

### 선택지
| 안 | 내용 | 계약 영향 | 의견 |
|---|---|---|---|
| A. 단계 요약 행 | 같은 요청·attempt의 연속 `stage`/`gpu_fence`/`kv_owned`(transition의 `consumed`, health의 `worker_state`)를 첫·끝 시각·개수·최대값 한 행으로 묶는다 | 행 종류 또는 `count`·`last_at_ms` 필드 추가 → schema `/2` | 권장. B 판정에 필요한 것은 각 단계의 존재·순서·마지막 값이며, 반복 행 자체는 쓰지 않는 것으로 안다(B 확인 필요) |
| B. 기록 분할 | 한 세션을 여러 기록(쪽)으로 나누고 쪽 번호·이전 쪽 해시로 잇는다 | 봉투에 쪽 정보 추가 → `/2` | 원 행을 모두 보존한다. 소비자가 쪽을 이어 읽어야 한다 |
| C. 요청 선택 | 생산자의 `requests=` 선택으로 시험 요청 몇 개만 기록한다 | 없음(지금도 가능) | 당장 쓸 수 있다. 다만 부하·동시성 분기는 요청 수를 줄여야 해 범위가 좁아진다 |
| D. 상한 상향 | 4,096을 키운다 | 상수 변경 | 크기만 미룬다. 권장하지 않음 |

### 지금 하는 것
- C를 쓴다. 시험 트래픽은 요청 수와 출력 길이를 상한 안에서 정하고, 넘으면 계약 거부를 결과(`contract_error`)로 남긴다.
- A(또는 B)는 B 담당 소비자와 합의한 뒤 구현한다. 생산자 변경이므로 소스 SHA가 바뀌어 GPU 재확인·재등록이 필요하다.

### B에게 물을 것
1. LIF-03·10·12, LIF-05·06·07·09, OBS-02 판정이 반복 단계 행의 개별 시각·값을 쓰는가, 아니면 존재·순서·마지막 값만 쓰는가?
2. A안을 받는다면 요약 행이 지켜야 할 것(예: 값이 바뀐 지점마다 행을 끊는다, 끝 행은 반드시 원 행)은 무엇인가?

## Q2. V2 runner의 logit bias는 sampler 단계에서 읽히지만 계약이 gap을 고정으로 붙인다

- V1 runner는 InputBatch CPU 배열에 요청별 logit bias가 없어 sampler 단계 값을 읽지 못한다. 그래서 계약은 API 요청에
  logit bias가 있으면 `GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:sampler_applied:logit_bias`를 무조건 붙이고 값을 비교하지 않는다.
- V2 runner(`vllm.v1.worker.gpu`)는 `LogitBiasState.num_logit_bias`(CPU 배열)에 요청별 개수를 둔다. V2 관측기는 이 값을 `sampler_applied.logit_bias`로 기록한다.
- 제안: sampler 행의 `logit_bias`가 null이 아닐 때는 gap 대신 API 값과 비교(다르면 `value_changed_before_consumption` 후보)하고, null이면 지금처럼 gap을 붙인다.
  V1 기록에는 영향이 없다(V1 sampler 행은 항상 null). 판정을 완화하는 것이 아니라 관측이 있을 때 비교를 더하는 변경이지만, 계약 의미 변경이라 B 확인 뒤 반영한다.
