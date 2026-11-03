# REQ-16 네 문맥 시험 설계 (2026-11-03, 이번 범위)

사용자 결정(2026-11-03): `required_cache_contexts`의 네 문맥(`cache_salt`, `adapter_content`, `media_identity`, `model_generation`)을 모두 이번 범위에서 잰다. 정책에서 문맥을 빼서 full_scope를 만들지 않는다. 판정 규칙(`project_cache_context`)은 바꾸지 않는다: 같은 첫 블록 키로 문맥만 바뀐 probe가 적중하면 위반이다.

## vLLM 0.31.0 캐시 키에 들어가는 것

`vllm/v1/core/kv_cache_utils.py`(0.31.0 이미지에서 확인)의 블록 해시 추가 키:

| 추가 키 | 내용 | 들어가지 않는 것 |
|---|---|---|
| `("lora", lora_name, lora_path)` | LoRA 이름과 경로 | 어댑터 가중치 내용 |
| `("mm", identifier, offset)` | 미디어 식별자 | — |
| `("cache_salt", salt)` | 첫 블록에만 | — |
| prompt embeds | 직접 넣은 임베딩 | — |

모델 가중치·세대는 키에 들어가지 않는다. 그래서 같은 프로세스 안에서 가중치나 어댑터 내용이 바뀌면, 바뀌기 전 KV가 같은 키로 재사용될 수 있는지가 시험 대상이다.

## 문맥별 시험

| 문맥 | prime·control | probe(문맥만 바꿈) | 엔진 구성 | 상태 |
|---|---|---|---|---|
| `cache_salt` | salt A | salt B, 같은 prompt | 지금 세대 | G29에서 이 문맥은 full_scope. 새 센서 자산으로 다시 잼 |
| `adapter_content` | LoRA `byul-lora`(경로 P, 내용 A) | 어댑터를 내리고 같은 경로 P에 내용 B를 쓴 뒤 같은 이름으로 다시 올림 | `--enable-lora`, `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True`, 런타임 load/unload API | 실행기·도구 개발 필요 |
| `media_identity` | 이미지 X | 같은 문장, 다른 이미지 Y. 요청이 미디어 uuid를 정할 수 있으면 같은 uuid로도 시험 | 멀티모달 모델 세대(모델 선정 필요) | 실행기 개발·모델 결정 필요 |
| `model_generation` | 가중치 세대 1 | 실행 중 가중치를 세대 2로 바꾼 뒤 같은 prompt | `VLLM_SERVER_DEV_MODE=1`의 개발용 가중치 갱신 API(`/init_weight_transfer_engine`, `/update_weights` 등) | 갱신 방법 조사·실행기 개발 필요 |

대조: 각 문맥마다 control은 prime과 같은 문맥으로 적중해야 하고(정상 대조), post는 원래 문맥으로 돌아와 적중해야 한다. control이 적중하지 않으면 그 문맥은 잰 것으로 세지 않는다.

## 개발할 것 (A 소유 파일)

1. **사례 필드**(`engine_callback_projection.validate_cases`, `engine_sensor`):
   - `adapter`: 요청 `model`을 manifest에 적힌 어댑터 이름 중 하나로 바꾼다.
   - `media`: 실행기가 seed로 같은 이미지를 다시 만들 수 있는 작은 PNG를 생성해 content part로 붙인다. 원본 이미지는 증거에 남기지 않고 해시만 남긴다.
   - prompt 4,096자 상한은 그대로 둔다.
2. **사례 사이의 lab 동작:** role `switch`와 `action`(`lora_reload`, `weights_update`)
   - `target.lab`이 참이고 소유 네임스페이스(`byul.lab/owned=true`)일 때만 실행한다. `lab_activation`과 같은 소유 확인을 쓴다.
   - 동작마다 요청·응답 원문 해시와 전후 세대를 기록한다. 동작이 실패하면 그 문맥은 UNKNOWN이다.
3. **어댑터 생성 도구:** Qwen2.5-0.5B의 q_proj·v_proj에 맞는 rank 8 LoRA 두 벌(내용 A·B)을 seed로 만든다. 엔진 이미지 안의 torch·safetensors로 만들고, 아무것도 내려받지 않는다.
4. **계약 시험:** 어댑터 내용 교체·이미지 교체·가중치 교체 각각에 대해 같은 키 적중은 위반, 다른 키·미적중은 통과, 동작 실패는 UNKNOWN.

## 정할 것

- `media_identity`의 멀티모달 모델과 고정 revision: 내려받기 크기·비용 승인이 필요하다. L4 24GB에서 도는 소형 VL 모델이 후보다.
- `model_generation`의 가중치 갱신 방법: 개발용 weight transfer는 송신 쪽 프로세스가 필요하다. 같은 노드 안에서 끝나는 방법을 먼저 조사한다.
- 요청이 정한 미디어 uuid가 캐시 식별자로 쓰이는지: 0.31.0 소스에서 더 확인하고 GPU에서 확인한다.

## 세대 계획

| 세대 | 문맥 |
|---|---|
| V1 센서 + 런타임 LoRA 갱신 | `cache_salt`, `adapter_content` |
| 멀티모달 모델 | `media_identity` |
| 개발 모드 가중치 갱신 | `model_generation` |

세대마다 환경·target·준비 자산·manifest를 따로 만들고 결과를 섞지 않는다.
