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

| 문맥 | prime·control | 바꾸기(`switch`) | probe·post | 엔진 구성 |
|---|---|---|---|---|
| `cache_salt` | salt A | 없음 | salt B로 probe, salt A로 post | 지금 세대. G29에서 이 문맥은 full_scope였고 새 센서 자산으로 다시 잰다 |
| `adapter_content` | LoRA `byul-lora`, 내용 A | `lora_reload` B: 같은 경로의 파일을 내용 B로 바꾸고 `load_inplace`로 다시 올림 | 같은 이름·경로·prompt | `--enable-lora --max-lora-rank 8`, `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True` |
| `model_generation` | 원래 가중치 | `weights_reload` gen2: `/collective_rpc`로 `reload_weights(weights_path=gen2)` | 같은 prompt | `VLLM_SERVER_DEV_MODE=1` |
| `media_identity` | 같은 문장 + 이미지 seed X | 없음 | 같은 문장 + 이미지 seed Y. uuid를 같게 둔 사례도 따로 | 멀티모달 모델(선정 필요) |

- 대조: control은 prime과 같은 문맥으로 적중해야 합니다. post는 probe 뒤 같은 prompt로 적중해야 합니다. 둘 중 하나라도 아니면 그 문맥은 잰 것으로 세지 않습니다.
- 판정: 기존 규칙 그대로입니다. 첫 블록 키가 같은데 probe가 적중하면 위반입니다.
- 바꾸기 실패: 실패한 `switch` 뒤의 사례는 보내지 않고 결과는 UNKNOWN입니다.

## 구현 (2026-11-03)

| 파일 | 내용 |
|---|---|
| `executors/engine_callback_projection.py` | REQ-16 사례에 `adapter`, `media`(`seed`, 선택 `uuid`)와 role `switch`(`lora_reload`·`weights_reload`, `variant`)를 더함. 다른 점검은 받지 않음. 닫힌 lab 선언 검사 `validate_lab_contexts`, 결정적 16×16 PNG `media_png`, content part `message_content` |
| `executors/engine_sensor.py` | `switch`나 `adapter` 사례, 또는 lab 선언이 있으면 `target.lab`이 참이어야 함. 전송에 선언을 맡긴 뒤 사례 순서대로 바꾸기와 요청을 보냄. `lab_switches`를 결과에 남김 |
| `runtime/kubernetes_http.py` | `declare_callback_lab`: lab 대상 + 엔진 네임스페이스 이름·UID·`byul.lab/owned=true` 확인. `engine_callback_switch`: 선언된 변형만 사용. LoRA는 고정 프로그램 `LORA_VARIANT_COPY`로 엔진 컨테이너 안에서 파일을 바꾼 뒤 `/v1/load_lora_adapter`에 선언된 이름·경로로만 보냄. 가중치는 `/collective_rpc` `reload_weights`에 선언된 경로로만 보냄. 채팅 요청의 model은 서빙 별칭과 선언된 어댑터 이름만, content는 문자열이거나 문장 + 생성 PNG 한 개만 받음 |
| `tools/gpu_a_lab_variants.py` | 엔진 컨테이너 안에서 표준 라이브러리만으로 LoRA 두 벌(`lora --model-dir --out --seed`)과 두 번째 가중치 세대(`weights --model-dir --out`, `model.norm.weight`를 1.0으로)를 만듦. 내려받지 않음 |

계약 시험 `tests/test_req16_lab_contexts.py`:
- 사례·선언 검사
- 미디어 part
- 소유 표시가 없거나 lab이 아니면 선언 거부
- 선언 밖 바꾸기·재적재 요청 거부
- 어댑터 이름은 선언 뒤에만 사용
- 같은 키 적중은 위반, 미적중은 위반 아님
- 바꾸기 실패와 소유 미확인은 UNKNOWN
- 복사 프로그램이 같은 경로에서 내용만 바꾸고, 링크·빠진 파일은 거부
- 변형 도구 결과

생성한 LoRA와 두 번째 가중치 파일은 0.31.0 이미지의 `safetensors`로 읽히는 것을 로컬에서 확인했습니다.

## 배포 요건 (lab 전용)

- 엔진 네임스페이스에 `byul.lab/owned=true` 표시, 실행 대상(target)에 `"lab": true`.
- 엔진 컨테이너에 쓰기 가능한 `/guard-lora`와 `/guard-weights`(emptyDir). 변형은 `tools/gpu_a_lab_variants.py`를 엔진 컨테이너 안에서 실행해 만듭니다. 두 번째 가중치 세대는 모델 크기만큼 공간이 필요합니다(0.5B 기준 약 1GB).
- `adapter_content` 세대: `--enable-lora --max-lora-rank 8`, `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True`.
- `model_generation` 세대: `VLLM_SERVER_DEV_MODE=1`. 개발용 API가 열리므로 소유 lab 밖에서는 띄우지 않습니다.
- 사례 manifest의 `lab_contexts` 예:
  ```json
  {"lora": {"name": "byul-lora", "active_path": "/guard-lora/active",
            "variants": {"A": "/guard-lora/variants/a", "B": "/guard-lora/variants/b"}},
   "weights": {"variants": {"gen2": "/guard-weights/gen2"}}}
  ```

## 정할 것

- `media_identity`의 멀티모달 모델과 고정 revision: 내려받기 크기·비용 승인이 필요합니다. 실행기·전송 쪽 지원은 끝났습니다.
- 요청의 미디어 `uuid`가 캐시 식별자로 쓰이는지: GPU에서 같은 uuid·다른 이미지 사례로 확인합니다.
- `load_inplace`와 `reload_weights` 뒤 vLLM이 접두 캐시를 비우는지: 소스상으로는 보이지 않으며, GPU 결과가 이 질문에 대한 답입니다.

## 세대 계획

| 세대 | 문맥 |
|---|---|
| V1 센서 + 런타임 LoRA 갱신 | `cache_salt`, `adapter_content` |
| 멀티모달 모델 | `media_identity` |
| 개발 모드 가중치 갱신 | `model_generation` |

LoRA 세대와 개발 모드 세대는 한 엔진으로 합칠 수 있지만, 결과를 나눠 보기 위해 세대 기록은 따로 남깁니다.

세대마다 환경·target·준비 자산·manifest를 따로 만들고 결과를 섞지 않는다.

## 이미지 뒤 텍스트 사례 준비 (2026-11-04, CPU 계약 검증)

`tools.gpu_a_run_inputs check --check REQ-16 --cases req16-full`에 선택 인자
`--media-text-after 'after0 after1 ...'`를 주면 기존 23사례 중 media 8사례만
`text → generated PNG → text` 순서로 만든다. 생략하면 기존 사례를 유지한다.
manifest의 `media.text_after`에 고정되어 정책의 manifest SHA에도 반영된다.
다른 점검·preset·외부 사례 파일에는 이 옵션을 묵시적으로 적용하지 않는다.

전송은 생성 PNG 한 개와 앞뒤 텍스트만 허용한다. 두 텍스트의 합은 실행기 nonce를
포함해 4096자 이하다. 외부 이미지 URL·두 번째 이미지·임의 part·초과 텍스트는 거부한다.
이 옵션은 이미지가 마지막 캐시 블록에만 놓여 실제 이미지 블록 재사용을 볼 수 없었던
2026-11-03 실측의 후속 사례다. 토큰화와 블록 위치는 다음 GPU 실행에서 확인해야 한다.

기존 `project_cache_context`의 판정 의미는 바꾸지 않았다. 같은 첫 블록과 공통 텍스트
접두 적중만으로 이미지 소비를 확정할 수 없으므로, media 판정 기준과 실제 이미지 블록
소비 증거를 검토해야 한다. 기존 EVD/RES와 LoRA·모델 세대 FAIL은 그대로 보존한다.
이 문서의 추가 사례 준비는 새 GPU 실측이나 media 전체 범위 완료가 아니다.
