# REQ13 오류 필드 제품 adapter

`guard.adapters.kubernetes_response_error`는 기존 `tools/b2_response_kubernetes_capture.py`의 직접 수집과 producer `_project`/`_control` 정제를 설치 제품으로 옮긴다. 기존 tool·정책·판정 규칙·helper 4개 bytes 핀은 보존한다. helper 출처는 설치된 `guard/` 경로를 기준으로 확인한다.

공개 API:

- `capture_validation_error(env, target, *, duration=60, timeout=10, binary='kubectl', allowed_error_fields=None, forbidden_contexts=None, _transport_factory=None)` → 기존 capture record
- `project_error_capture(record, env, target, api_model, allowed_fields)` → 기존 정제 projection 또는 `None`
- `verify_product_helpers()` / `verify_a_capability()` → 승인 bytes·함수 출처 확인

`PROFILE`/`CAPTURE_SCHEMA`는 `byul-req13-error-kubernetes-capture/1`, `VALIDATION_ERROR_PROFILE`/`CAPTURE_PROFILE`은 `kubernetes-owned-validation-error-http-v1`, `PROJECTION_PROFILE`은 기존 producer profile이다. source profile은 `vllm-031-validation-error`다.

독립 승인 A108 HTTP 파일 SHA256은 `3da64c61ccceda9899e57d022b3ff7afaee3a85906d118a9309f368dd8f38566`이다. 현재 B base의 구 HTTP는 요청 전에 차단된다. A108 동일 bytes의 공개 transport 소비는 root의 별도 통합 worktree에서 확인한다. 기존 tool의 component 전체 gate는 변경하지 않는다.

요청은 실제 API alias로 `/tokenize` 정상→고정 형식 오류 `/v1/chat/completions`→`/tokenize` 정상 3건뿐이다. 정책은 전달된 POL 값을 사용한다. 명시 `[]`와 미입력을 구분하며, 다른 정책으로 분석한 record는 projection에서 거부한다. 정제 record에는 field path hash·정책 위반 enum/hash·전후 대조·요청/세대 binding·수집 gap만 남긴다. 응답 본문·원문 field key·token·표식·예외 본문은 저장하지 않는다.

확인된 독립 오류 필드 위반은 후기 대조·정리·보조 source 재확인 오류에도 보존한다. 부분 수집과 정상 tokenize 결과는 전체 REQ13 PASS 또는 inference functional PASS를 만들지 않는다. HTTP 오류 필드 관측과 native handler/runner 최종 소비를 구분한다. transport 주입은 EXAMPLE ENV에만 허용한다.

공통 parent 등록과 원 CheckDef의 정제 `response_violations` 연결은 A 승인 대상이다. 해당 extension이 없는 원 CheckDef는 parent가 요청 전에 닫는다. 이 adapter는 registry·pack·공통 판정을 변경하지 않는다. 단위시험은 EXAMPLE transport 및 설치 경계 검증이며 실제 Kubernetes 실행은 별도 승인 대상이다.
