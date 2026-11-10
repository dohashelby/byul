# 선정 default-text 배포와 응답 metadata

`tools/check_selected_profile.py --policy policy.json`은 선정37개 검증에 사용하는 `selected-default-text/1.0` 배포 조건을 확인한다. 기존 정책 loader의 정상 결과와 정확 정책 SHA를 소비한다. `status=ok`, `supported=true`, 종료값0이어야 이 프로필로 배포한다. 미지원입구·잘못된 정책은 `unsupported`·종료값1과 해당 필드를 반환한다. `--report-out`은 덮어쓰기 없이 0600 JSON을 생성한다. A0/preflight는 `validate_selected_profile(load_policy(path))` 결과를 그대로 소비할 수 있다.

선정 프로필은 runtime `profile=default`와 빈 extra_fields를 지원한다. 기존 default sampling 예시의 bounded validate `temperature/top_p/seed/stop`도 해당 타입으로 검사한다. 그 외 extra_fields는 token/KV 재사용, 코드판본·processor 선택, structured/tools, native 옵션, 반환 옵션을 다시 열 수 있으므로 선정 배포에서 거부한다. 이 검사는 일반 policy loader나 `zeroday-lab`의 확장 기능을 변경하지 않는다.

성공응답의 `sanitize_response_metadata(dict, requested_fields=frozenset())`는 프로토콜 envelope의 알려진 metadata를 처리한다.

|반환 필드|검증된 실제 요청 옵션|
|---|---|
|prompt, prompt_text|return_prompt_text|
|prompt_token_ids, input_token_ids, output_token_ids, token_ids|return_token_ids 또는 return_tokens_as_token_ids|
|prompt_logprobs, prompt_log_probs|prompt_logprobs|
|logprobs|logprobs|
|private_debug, kv_transfer_params|반환하지 않음|

선정 default-text는 위 반환 옵션을 열지 않으므로 미요청 metadata를 제거한다. 기존 다른 프로필에서 이미 검증한 명시 옵션은 caller가 `requested_fields`에 전달한다. `prompt_logprobs=0`도 요청한 옵션이다. 값의 `is not None and is not False`를 확인하며 0을 단순 truthy 검사로 잃지 않는다.

helper는 vendor 객체·배열을 포함한 metadata에서 위 정확 키를 재귀 제거한다. 실제 OpenAI choices의 choice/message/delta에 있는 생성 content/text/reasoning/reasoning_content와 함수 arguments 값은 불투명한 생성/사용자 내용으로 보존한다. metadata 안의 단순 content 유사 키를 생성 content로 승격하지 않는다. usage 토큰 수, stream delta/종결, 표준 필드와 비민감 vendor 필드는 보존하며 vendor 전체를 승인했다고 표시하지 않는다. metadata 깊이는 64로 제한하며 정제 불가는 ResponsePrivacyError(ValueError)로 relay에 전달해 안전 오류로 처리한다. JSON/SSE parsing·기존 bytes 상한·frame 보존과 호출 연결은 relay가 담당한다.
