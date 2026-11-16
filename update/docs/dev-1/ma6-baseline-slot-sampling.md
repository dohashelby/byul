# 초기 V1 slot→sampling metadata 관측 후보 (2026-11-16)

REQ-18은 실제 runner의 sampling 제한 상태를 묻는다. 초기 V1의
`SamplingMetadata`·`Sampler`에는 `allowed_token_ids`/mask가 없다. 기존 V1 mask
관측이나 V2 slot/count 경로를 이 판본에 붙이지 않는다. baseline engine_sensor
실행 gate·판정 규칙·공용 계약은 바꾸지 않는다.

## 정확한 대상과 실제 입력

기존 upstream clone HEAD `5204ff5c3feeb96e8a6eea65dfcb78395f90d4d8`의 원본
source를 사용한다. fixture는 Apache-2.0 원본 bytes이며 이미지 검증·GPU
실측·생산자 등록으로 승격하지 않는다. 새 bytecode 지문을 만들지 않고 기존
CacheCallbackObserver의 exact file/code/globals/descriptor 자격을 재사용한다.

| module | 정확 SHA-256 | 직접 함수 |
|---|---|---|
| `v1.worker.gpu_input_batch` | `89770d22d9398a061d96e4aa547a8afbacaf0eeff589e1dcf3df45503a4ab40c` | `InputBatch.make_sampling_metadata` |
| `v1.worker.gpu_model_runner` | `1ac3fb18b2adad3609dcf58a1656d6fdd4a568eea6740258e00f6a43c7d4b813` | `GPUModelRunner._prepare_sampling` |
| `v1.sample.sampler` | `9082d291c73104024f5658d5858393aedc192d3d4a7ebfd3d415a249c4d4f019` | `Sampler.forward`·`sample`·`apply_temperature` |
| `model_executor.models.qwen2` | `10015a92c8ed412de2cb33ade19067fd71f628a7ac5e45792feced88b60e37c6` | `Qwen2ForCausalLM.sample` |
| `model_executor.models.llama` | `723d0728fd985f9a9b0197fc888f6e74de65ff2c84ab15c18a17d6d2b6016144` | `LlamaForCausalLM.sample` |

선정한 함수는 decorator가 없는 직접 함수다. Torch nn.Module의 일반 호출
경계를 건드리지 않고 실제 `Sampler.forward` 진입을 기록한다. 다른 model
클래스는 이 후보의 지원 밖이다. 하위 `TopKTopPSampler`의 native/FlashInfer
선택과 GPU 연산은 이 hook의 확인 범위가 아니다. 그 source는
`eb5b602f5ebd06ac116f927a608db7e2f20234d4fea233d6b6e3feec69d5501c`로
별도 fixture에 보존한다.

adapter 자극은 A `(temperature=1, top_p=1, top_k=1, seed=0)`와 B
`(temperature=1, top_p=1, top_k=-1, seed=0)`다. 초기 SamplingParams의
`_verify_args`는 top_k=0을 거부하고 -1만 비활성 값으로 받는다. 정확 source
`c43343b437b675d4ed5dd16479636db1f621a3a408a30b81817923913419aa62`의
실제 검증 함수로 이 입력과 0 거부 회귀를 둔다. 비교용 summary의 비활성 top_k
정규 값은 기존 consumption과 같은 0이며 원 자극을 0으로 보내지 않는다.
온도/top_p에는 정수 요약과 정확한 float hex를 함께 남겨 반올림 허용 기준을
만들지 않는다. baseline의 `restricted`는 실제 지원하는 이 제한으로 계산하고,
allowed_token_ids는 존재 여부와 `supported=False`만 남긴다.

## 기존 관측기와 소비 연결

- `baseline_slot_sampling=True`로 기존 engine callback 준비 경로에서만 켠다.
  기본 False이며 old V1/V2 관측, 원본 lifecycle profile과 gate는 보존한다.
  충돌할 수 있는 별도 runner-consumption/execution-link 모드와 동시 준비는
  거부한다. lifecycle catalog/capture 파일은 수정하지 않는다.
- 동일 SlotObserver·HMAC key·process/start context·weak batch identity를 쓴다.
  metadata/model/sampler/tensor 신원도 같은 무작위 observer namespace의 weak
  session object 지문이다. raw ID·주소·prompt·token/logit 배열을 읽거나 쓰지
  않는다. arbitrary property도 실행하지 않는다.
- batch metadata 반환을 nested `_prepare_sampling` 반환과 연결한다. 이후
  source-qualified model.sample과 그 안의 Sampler.forward가 같은 실제
  metadata object를 받은지, sampler object가 맞는지 확인한다. forward 안의
  `sample`과 `apply_temperature` 인자도 같은 object 지문으로 대응한다.
- host 선언 temperature/top_p/top_k, 실제 metadata의 all_greedy/all_random/
  no_top_p/no_top_k/no_penalties bool, 제한 tensor object 신원과 skip_copy만
  기록한다. SamplerOutput의 token 값은 읽지 않는다.
- 기존 project_slot은 이 사슬의 부분 사실을 붙이되 source/process/parent/
  metadata 객체가 다르거나 여러 transfer가 가능한 경우 사슬을 만들지 않는다.
  reuse row와 연결할 때는 해당 batch의 clear/movement epoch도 대조한다.
  객체 동일성을 GPU 제한 값·copy 완료·최종 출력 귀속으로 승격하지 않는다.

`gpu_values_verified`·`copy_completion_verified`·
`sampler_restriction_values_verified`·전체 scope는 False다. 실제 GPU tensor 값과
copy/stream 완료, 하위 선택 kernel의 소비·출력 lifetime은 남은 관측이다.
이 source/fixture 개발은 그 실측을 기다리지 않고 준비할 수 있는 독립 분기다.

## 검증 상태

로컬 Python·pytest·편집 helper를 실행하지 않았다. 회귀는 PR Linux CI에서
실행한다. 정확 전체 source의 function code object를 native import 없이 구성하고,
Tensor/backend 경계만 EXAMPLE collaborator로 대체한다. 실제 생산자가 만든
기본 off·정상·unsupported·condense·skip_copy·Qwen2/Llama transfer·source/process/
parent/object 변조 행을 기존 consumer에 직접 넣는다. opaque tensor trap은 값·
포인터·tolist 접근을 거부한다. 양성 사슬도 EXAMPLE이며 GPU 소비 PASS가 아니다.
