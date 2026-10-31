# B Online 입력 native snapshot callback

B 내부 ABI는 `guard.adapters.vllm_online_input.snapshot_online_input`이다.
A가 선정 source/code와 serving process·요청 세대를 검증한 hook에서 실제 인자·반환·예외를
전달한다. 이 모듈은 hook 설치, ASGI 계측, writer/parser, 파일 I/O, 네트워크, session seal,
공통 registry와 판정을 구현하지 않는다. Pod exec의 별도 Python 실행은 serving 소비 증거에
포함하지 않는다.

```python
snapshot_online_input(point, args, kwargs, *, phase, result=None, exception=None, binding=None)
```

- phase: `CALL`, `RETURN`, `RAISE`.
- point: 아래 폐쇄 목록. target source hook의 정확한 symbol·code hash 확인은 A 소유다.
- args/kwargs: 실제 함수 호출 인자. 원본을 변경하거나 iterator/generator를 소모하지 않는다.
- result/exception: 해당 RETURN/RAISE에서 받은 실제 객체. 문자열 예외·repr은 저장하지 않는다.
- binding: A가 제공하는 이미 해시된 식별자와 pid. callback은 형식을 선별해 전달한다.

|point|관측|
|---|---|
|`OnlineRenderer.render_chat`|파싱된 필드·trust/config, RETURN rendered inputs 또는 typed error code|
|`OnlineRenderer.preprocess_chat`|같은 요청 필드와 실제 렌더 결과의 count/hash|
|`OnlineRenderer.validate_chat_template`|실제 template 인자 hash·trust flag·반환/예외|
|`online_renderer._reused_prompt_token_ids`|CALL의 reuse 필드와 RETURN ID count/hash|
|`protocol.validate_request_mm_kwargs`|실제 MM kwargs hash·trust flag·반환/예외|
|`InputProcessor.process_inputs`|실제 processor 입력과 admitted 반환 ID count/hash, native RID hash|
|`InputProcessor._validate_model_input`|실제 model-input ID count/hash와 domain|
|`InputProcessor._validate_prompt_len`|실제 prompt length와 config|

반환 schema는 `byul-online-input-snapshot/1`이며 `point`, `phase`, `status`, `metadata`,
`binding`을 포함한다. status는 snapshot 수집의 collected/partial/unavailable이다.
source-qualified/live/공식 증거 완료 상태는 A의 source·세대·session 검증과 B runner가
별도로 확인한다. A payload의 `metadata.online_input`에 이 반환값 전체를 넣는 형상은
연결 제안이며 공통 계약으로 등록되지 않았다.

metadata의 주요 키는 다음과 같다.

- `parsed_fields[field]`: present, explicitly_set, kind, status, canonical_sha256.
  실제 파싱된 객체의 chat_template/chat_template_kwargs/mm_processor_kwargs/media_io_kwargs/
  echo/kv_transfer_params 값을 정규 JSON hash로만 기록한다. 원래 HTTP body와의 보존 여부는
  요청 측 field hash와 대조한다. reuse helper가 필드를 pop하므로 CALL 관측을 사용한다.
- `trust_request_chat_template`, `trust_request_mm_kwargs`,
  `effective_config.max_model_len/model_vocab_size/runner_type`, `reported_cache_max_token_id`.
- `processor_input`, `admitted`, `reused`, `rendered_inputs`: 실제 ID sequence의
  status/count/canonical_sha256/integer_ids/min/max. 원문 ID sequence는 출력하지 않는다.
- `token_id_domain`, `admitted_token_id_domain`: 실제 tokenizer.get_vocab() ID 합집합의
  count/min/max/정규 hash·0부터의 연속 여부, 실제 model.get_vocab_size(), 요청 ID membership.
  get_vocab()은 added-token ID도 포함한다. cached max 값은 독립 domain의 oracle로 쓰지 않는다.
  전자는 실제 입력, 후자는 실제 반환의 membership이며 둘의 요청 hash를 분리한다.
- `native_request_id_sha256`, `native_request_binding_matches`, `return_kind`,
  `native_error_code`, `exception_type`. HTTP/native code만으로 admission을 추정하지 않는다.

binding의 허용 키는 `asgi_request_id_sha256`, `native_request_id_sha256`, `call_id_sha256`,
`session_id_sha256`, `process_start_sha256`, `pid`다. hash는 소문자 SHA256만 받는다.
인증·source/PID/start/session/ASGI/native RID의 실제 연결은 A와 runner가 검증한다.
동일 호출의 CALL/RETURN call_id 및 InputProcessor 인자 RID와 binding의 일치를 확인하고,
확인한 CALL field hash와 RETURN admitted hash를 같은 요청으로 연결한다.

이 snapshot은 field hash만으로 template의 실제 효과를 주장하지 않는다. A가 검증한
render CALL/RETURN과 정상 대조를 연결한다. text-only admission을 최종 MM override 소비로
확대하지 않는다. domain/config/getter가 불명확하면 unavailable로 보존한다. 관측 실패는
고정 diagnostic 또는 unavailable로 반환하며 실제 함수의 반환/예외를 바꾸지 않는다.

기술 수집 상한은 ID sequence262144개, vocabulary entry262144개, field JSON65536bytes,
node4096개/depth12, rendered input8개다. 큰 integer는 ID hash에서256bit까지 수집한다.
이는 기업·모델 수락 한도가 아니라 관측 작업량의 상한이다. 초과는 unavailable이며 요청을
거부하거나 입력을 절단하지 않는다. getter 호출과 ID 집합 정렬 비용은 vocabulary 크기에
비례하며 source-qualified 실제 객체에서만 사용한다. output에는 token 문자열·본문·secret,
ID 집합 전체·예외 본문이 없다.

unit의 EXAMPLE 객체는 ABI·비변경·비소모·누락 처리 회귀다. 실제0.31 CPU 객체 직접 시험은
별도 owned `/tmp` 산출물로 기록한다. 실제 serving process의 A hook·capability/session 결합과
Kubernetes 수집·판정·저장·복구/정리 완료 여부는 해당 실행에서 검증한다.

## 초기 고정 코드 직접 검증 (2026-10-29)

- 모듈: `src/guard/adapters/vllm_online_input.py`, SHA256
  `bda42db95d8b0bb1ba58c1fca8e5e93886f4f8ef87d595d486203e9acbce808e`.
- EXAMPLE unit: 18 passed / 0.04초. 인자 비변경, iterator 비소모, slots/불명확 property,
  예외 원문 제거, source-specific field hash, domain 분리, 관측 상한을 확인했다.
- 실제 선정0.31 CPU 객체 직접 검증: 8.97초, 결과
  `/private/tmp/byul-online-native-direct-c19qrttn/summary.json`.
  source db9527a·Torch2.13.0·Transformers5.17.0·Tokenizers0.23.3·Pydantic2.14.1 환경에서
  기존 local-config builder의 실제 OnlineRenderer/InputProcessor를 사용했다.
- 정상 render/processor 반환, 독립domain0..31 밖의32 전달·수용과 cached32 분리,
  실제 template typed400, MM/길이 VLLMValidationError의 RAISE를 관측했다.
  실제 input/admitted hash와 native RID hash 비교가 일치했으며 source와 module bytes를
  전후 확인하고 renderer를 종료했다. cache/config는 새 소유 `/tmp`에 남겼다.
- source guard는 실제 vLLM 원본만 검증했다. 새 module 추가와 충돌하는 기존 CPU 제품
  full-guard gate를 변경하거나 완화하지 않았고, 기존 `-2` CPU 정식 증거를 재실행하지 않았다.

위 직접 시험은 실제 객체를 callback에 전달하는 경계 시험이다. serving hook, ASGI binding,
Kubernetes 실행 상태는 모두 미검증으로 기록했다. 최종 source-qualified A hook 등록과
session/capability, 실제 serving 요청 세대 결합은 A/B 통합 실행의 잔여 조건이다.

### 길이 검증의 실제 조건

`effective_config.runner_type`은 실제 ModelConfig 필드에서 generate/pooling/draft만
선별하고 불명확하면 unavailable로 남긴다(db9527a `config/scheduler.py:21`).
`InputProcessor._validate_prompt_len`·`_validate_model_input`의 `metadata.prompt_type`은
실제 인자의 decoder/encoder를 선별하며, 없거나 다른 값이면 unavailable이다.
선정 chat decoder에서 `length == max_model_len`의 거부 조건은 실제 runner_type이
`generate`인 경우다(`input_processor.py:544`). 더 큰 길이의 분기와 구분한다.
이 callback은 violation bool을 만들지 않으며 encoder/MM의 별도 길이 조건으로
확대하지 않는다. template/MM은 기존 실제 trust·parsed/gate 반환 관측을 유지한다.


길이 관측 보완의 최종 module SHA256은
`bd15958029e91ba189c2fe588009a97ddd4d3d339e32824e5e5016a52fa736b1`이다.
unit48개가0.06초에 통과했다. 실제0.31 public 생성자로 만든 generate·pooling
InputProcessor 두 객체에서 decoder16/16 동등 경계를 직접 확인했다(10.10초).
`_validate_prompt_len`과 `_validate_model_input` 모두 generate는 실제
VLLMValidationError RAISE, pooling은 실제 RETURN을 관측했고 callback의
runner_type·prompt_type은 각 실제 객체·인자와 일치했다. 결과는
`/private/tmp/byul-online-native-length-locor3k_/summary.json`에 보존했다.
초기 bda42d module/actual 기록은 유지하며 기존 CPU16+후속16은 재실행하지 않았다.
