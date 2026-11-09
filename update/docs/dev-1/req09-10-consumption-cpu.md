# REQ-09·10 소비 경계 CPU 지원 (A측, B 의존 반환 8의 부분 구현, 2026-11-08)

이 문서는 최초 구성요소 지원 범위의 기록이다. 같은 날 실제 OPT worker·logits·grammar 및 랜덤 소형 Qwen2-VL의
raw image→실제 vLLM worker→모델 입력까지 추가한 결과는 `consumption-links-cpu.md`를 따른다. 선정 2B snapshot·GPU 확인은 남아 있다.

기존 token-only `input_validation`, API `sampling-api-v1`, 등록된 V1/V2 runner 관측을 보존한다.
이번 두 계약은 **실제 라이브러리 구성요소를 CPU에서 호출한 EXAMPLE 근거**이며 admitted worker·GPU·실제 모델 소비 완료가 아니다.

| 점검 | 지원 계약·생산자 | 실제 코드 경로 |
|---|---|---|
| REQ-10 | `guard-sampling-consumption-cpu/1`, `executors/sampling_consumption_cpu.py`, `tools/sampling_consumption_capture.py` | SamplingParams → 실제 Request/Scheduler.schedule → InputBatch → SamplingMetadata → 실제 Sampler |
| REQ-09 | `guard-mm-gather-cpu/1`, `executors/multimodal_gather_cpu.py`, `tools/multimodal_gather_capture.py` | MultiModalDataParser의 typed embedding → MultiModalFeatureSpec/PlaceholderRange → 실제 GPUModelRunner._gather_mm_embeddings |

## REQ-10의 관측과 한계

vLLM 0.31.0 참조 소스의 8개 모듈 bytes SHA를 실행 전후 대조한다. 실제 설치 모듈이 다르면 실행을 거부한다.
SamplingParams와 Scheduler가 내보낸 객체의 동일성·요청 ID, 각 단계의 temperature/top_p/top_k·frequency/presence/repetition penalty·허용 token 개수를 대조한다.
Sampler에는 이 InputBatch가 실제로 만든 SamplingMetadata를 넘기며 반환 출력은 고정 EXAMPLE logits의 독립 기대와 대조한다.

사례 8개: 정상 greedy, 허용 token 1개, 제한 요청 뒤 같은 slot의 정상 요청, top-k=1과 top-p/temperature,
penalty, 음수 temperature, top-p>1, top-k<-1. 성공 5사례의 Sampler 출력 대조가 일치하고, 잘못된 세 옵션은 실제
`VLLMValidationError`로 typed 단계에서 거부되어 scheduler/Sampler에 가지 않는다.
원 최초 capture의 예외 분류는 ValueError만 받아 이 세 건이 execution_error였으며 원본을 보존했다.
정확한 vLLM 예외형을 받도록 보수했으며 일반 실행 오류를 거부 성공이나 위반으로 만들지 않는다.

KV 용량 16블록과 logits는 합성이다. CPU helper가 실제 scheduler 출력에서 CachedRequestState를 직접 만든다.
worker의 `_update_states`가 실행된 것은 아니다. OPT는 이 CPU 구성요소 구성의 실제 ModelConfig만 제공하며 모델 weights/forward를 사용하지 않았다.
stop/max_tokens의 전체 집행, min_tokens processor, JSON/regex grammar, V2 Sampler, 실제 모델 logits·배포 요청/attempt·worker 세대는 이 계약 범위 밖이다.
`admitted_worker_consumption_verified=False`를 유지한다.

## REQ-09의 관측과 한계

parse/inputs/V1 runner의 고정 bytes SHA를 실행 전후 대조한다. 합성 embedding의 hidden size=8은 **EXAMPLE 시험 형상**이며 선정 모델의 규칙이 아니다.
실제 parser가 만든 embedding을 실제 feature·placeholder에 연결하고, CPU 상태 협력 객체를 둔 실제 `_gather_mm_embeddings`를 호출한다.
worker 생성자·모델·선정 processor를 만들지 않았다. `GPUModelRunner.__new__`로 만든 객체는 실제 worker로 편입된 프로세스가 아니다.

사례 6개: embedding 2행, 중간 token을 제외한 mask, 진행 구간의 embedding 일부 선택, encoder cache 누락, hidden size 불일치, MM feature가 없는 text 기본 기록.
반환 tensor의 형상·내용 대조와 같은 storage의 선택 구간 여부, 실제 반환 token mask를 확인한다. cache 누락과 hidden 불일치는 원 구현의 해당 예외 원인과 형을 확인한다.
text 기본 기록은 embedding이 없으며 실제 생산자 출력으로 소비자에 들어가도 MM 소비 완료가 되지 않는다.

`model_binding=None`을 명시하고 이를 텍스트 모델·임의 MM 모델 이름으로 채운 기록은 거부한다.
불변 snapshot의 MM model/processor/tokenizer/config·모델별 shape/channel/dtype/hidden 규칙, raw image processor.apply,
API→typed MM→실제 worker 세대→모델 inputs_embeds의 끝까지 연결은 남아 있다. 모델 선정·내려받기 승인이 먼저 필요하다.
`selected_processor_worker_complete=False`, `admitted_worker_consumption_verified=False`를 유지한다.

## 공통 소비 규칙과 B 연결

생산자 파일 bytes SHA·원 source 전후 SHA·닫힌 형식·사례 집합을 검증한다. 단계별 값 변경·출력 대조 실패·잘못된 embedding 수용은 후보이고,
뒤의 수집 중단이 이 후보를 지우지 않는다. 형식 오류·일반 실행 오류·원인 미확인 거부는 공백이다.
`component_scope_complete`는 해당 CPU 구성요소 사례 집합의 수집 상태이며 보안 PASS·원 질문 완료가 아니다.

실제 행은 `tests/fixtures/{sampling_consumption,multimodal_gather}_k3d_20261108.json`이다.
B는 기존 component 기록과 별도로 각 `project()` 반환을 소비한다. 이 helper 기록을 admitted native 행이나 등록된 GPU producer로 바꾸지 않는다.
공통 생산자 지원 목록, B 기존 판정, 원 GPU Ref·FAIL은 수정하지 않았다. 새 다운로드·제품 의존성·AWS 실행은 없다.

관련 회귀 결과와 임시 Pod 정리는 PROGRESS에 기록한다.

macOS Python 3.12.13 직접·소스 고정 회귀 42개 통과(pytest 0.76초, 부모 0.913초).
검증 중 고정 파일 변경 0. 전체 suite·Linux CI는 실행하지 않았다. 기존 테스트·assert·제외 조건은 수정하지 않았다.
