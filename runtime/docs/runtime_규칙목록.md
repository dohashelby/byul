# runtime 규칙 목록

이 목록은 update 담당자가 runtime 사건의 `rule_ref`를 `check_id`에 연결할 때 쓰는 확인 자료다. **코드 기준은 HEAD `9d93ff2`**이며, `check_id`는 확인되지 않았으므로 모두 `update 담당 확인 필요`로 남긴다. 계약 결과는 `contracts/README.md`의 변환표를 그대로 적었다. `runtime-event`를 만들지 않는 렌더 산출물·상태·재검증 요청에는 결과를 추정하지 않았다.

## Gateway 판정

근거: `runtime/src/serving_runtime/mvp/validator.py`, `request_flow.py`, `relay.py`, `policy_state.py`, `target.py`, `http_handler.py`. 아래 표는 추론 요청·transport가 실제 Gateway 요청 기록의 `decision.reason_code`로 만드는 값이다. 승인·인증·대상 파일을 읽는 내부 상태 코드(`POLICY_APPROVAL_REF_MISSING`, `AUTH_CONFIG_NOT_FOUND`, `TARGET_NOT_FOUND` 등)는 최종 요청 판정에서 각각 `POLICY_APPROVAL_*`, `AUTH_UNAVAILABLE`, `TARGET_UNAVAILABLE`로 정규화되므로 별도 사건 ID로 넣지 않았다. `rule_id`가 `없음`인 실패는 platform 변환 시 `reason_code`가 `runtime-event.rule_ref`가 된다. `DENIED` 행은 현재 코드에서 backend 전달 전에 끝나므로 계약 결과가 `denied`이고, `ERROR`는 차단 성공이 아닌 `error`다.

| ID (`reason_code`) | `rule_id` | source/enforcer | 의미 | 계약 결과 | 계획서 참조 | update `check_id` |
| --- | --- | --- | --- | --- | --- | --- |
| `REQUEST_ALLOWED` | 없음 | Gateway / `gateway` | 요청 검증과 정책 판정을 통과했다. | final `ALLOWED` → `allowed`; final이 없으면 `unknown` | §4.2, §7.1 | update 담당 확인 필요 |
| `POLICY_UNAVAILABLE` | 없음 | Gateway / `gateway` | 시작 시 정책을 유효하게 읽지 못했다. | `ERROR` → `error` | §4.7, §7.3 | update 담당 확인 필요 |
| `POLICY_NOT_YET_VALID` | 없음 | Gateway / `gateway` | 정책 유효 시작 시각 전이다. | `ERROR` → `error` | §4.7, §7.3 | update 담당 확인 필요 |
| `POLICY_EXPIRED` | 없음 | Gateway / `gateway` | 정책 유효 기간이 끝났다. | `ERROR` → `error` | §4.7, §7.3 | update 담당 확인 필요 |
| `POLICY_REVOCATION_UNKNOWN` | 없음 | Gateway / `gateway` | 철회 목록 상태를 확인할 수 없다. | `ERROR` → `error` | §4.7, §7.3 | update 담당 확인 필요 |
| `POLICY_REVOKED` | 없음 | Gateway / `gateway` | 현재 정책과 일치하는 철회 항목이 있다. | `ERROR` → `error` | §4.7, §7.3 | update 담당 확인 필요 |
| `POLICY_APPROVAL_UNVERIFIED` | 없음 | Gateway / `gateway` | 승인 참조나 승인 목록 항목이 없어 승인을 확인하지 못했다. | `ERROR` → `error` | §4.7, §7.3 | update 담당 확인 필요 |
| `POLICY_APPROVAL_REJECTED` | 없음 | Gateway / `gateway` | 승인 참조·해시·기간 대조가 명시적으로 실패했다. | `ERROR` → `error` | §4.7, §7.3 | update 담당 확인 필요 |
| `POLICY_APPROVAL_ERROR` | 없음 | Gateway / `gateway` | 승인 목록 읽기·형식 확인 오류이며 마지막 확인 승인도 쓸 수 없다. | `ERROR` → `error` | §4.7, §7.3 | update 담당 확인 필요 |
| `AUTH_UNAVAILABLE` | 없음 | Gateway / `gateway` | 인증 설정이 손상됐거나 읽을 수 없다. | `ERROR` → `error` | §3.4, §4.2 | update 담당 확인 필요 |
| `AUTH_REQUIRED` | `authentication` | Gateway / `gateway` | 필요한 bearer 인증값이 없다. | `DENIED`·미전달 → `denied` | §3.4, §4.2 | update 담당 확인 필요 |
| `AUTH_INVALID` | `authentication` | Gateway / `gateway` | bearer 값이 맞지 않거나 Authorization 헤더가 모호하다. | `DENIED`·미전달 → `denied` | §3.4, §4.2 | update 담당 확인 필요 |
| `ROLE_NOT_ALLOWED` | `route_roles` | Gateway / `gateway` | 주체 역할이 추론 route에 허용되지 않는다. | `DENIED`·미전달 → `denied` | §3.4, §4.2 | update 담당 확인 필요 |
| `PRINCIPAL_MODEL_NOT_ALLOWED` | `principal_allowed_models` | Gateway / `gateway` | 주체별 모델 허용 목록에 요청 모델이 없다. | `DENIED`·미전달 → `denied` | §4.2 | update 담당 확인 필요 |
| `TARGET_UNAVAILABLE` | `target_registration` | Gateway / `gateway` | 대상 등록을 유효하게 읽지 못했거나, 등록에 `kubernetes`가 있는데 Pod 조회가 정상이 아니어서(조회 안 함·없음·여러 개·실행 안 됨) 판정을 보류한다. | `ERROR` → `error` | §4.1 | update 담당 확인 필요 |
| `TARGET_MISMATCH` | `target_registration` | Gateway / `gateway` | 정책의 `target_id`와 등록 대상이 다르거나, 관측한 engine kind·version·model ID·image digest가 등록값과 다르다. | `ERROR` → `error` | §4.1, §7.3 | update 담당 확인 필요 |
| `TARGET_UNVERIFIED` | `target_registration` | Gateway / `gateway` | 등록에 선언한 값을 아직 확인하지 못했다(engine_info 수집 실패·미보고 등). `model.revision`은 제외. | `ERROR` → `error` | §4.1, §7.3 | update 담당 확인 필요 |
| `MODEL_NOT_ALLOWED` | `allowed_models` | Gateway / `gateway` | 정책의 모델 허용 목록 밖이다. | `DENIED`·미전달 → `denied` | §4.2, §4.10 | update 담당 확인 필요 |
| `MAX_TOKENS_EXCEEDED` | `output_max_tokens` | Gateway / `gateway` | 요청 출력 토큰 수가 정책 상한을 넘는다. | `DENIED`·미전달 → `denied` | §4.10 | update 담당 확인 필요 |
| `CONTEXT_LIMIT_EXCEEDED` | `max_context_tokens` | Gateway / `gateway` | 입력 토큰과 출력 요청 합이 context 상한을 넘는다. | `DENIED`·미전달 → `denied` | §4.10 | update 담당 확인 필요 |
| `CONTEXT_CHECK_UNAVAILABLE` | 없음 | Gateway / `gateway` | backend token count를 얻거나 검증할 수 없다. `/tokenize` HTTP 실패면 endpoint와 상태만 요청 context와 오류 메시지에 남기고 본문은 저장하지 않는다. | `ERROR` → `error` | §4.10, §7.3 | update 담당 확인 필요 |
| `CONCURRENCY_LIMIT` | `max_concurrent_requests` | Gateway / `gateway` | 동시 요청 slot 상한에 도달했다. | `DENIED`·미전달 → `denied` | §4.2, §7.1 | update 담당 확인 필요 |
| `CONNECTION_LIMIT` | `max_connections` | Gateway / `gateway` | 동시 client 연결 상한에 도달했다. | `ERROR` → `error` | §4.2, §7.1 | update 담당 확인 필요 |
| `UNSUPPORTED_TRANSFER_ENCODING` | 없음 | Gateway / `gateway` | 요청의 `Transfer-Encoding`을 지원하지 않는다. | `INVALID` → `invalid` | §4.2, §4.10 | update 담당 확인 필요 |
| `INVALID_CONTENT_LENGTH` | 없음 | Gateway / `gateway` | Content-Length가 없거나 잘못됐거나 실제 body보다 길다. | `INVALID` → `invalid` | §4.2, §4.10 | update 담당 확인 필요 |
| `UNSUPPORTED_MEDIA_TYPE` | 없음 | Gateway / `gateway` | media type이 `application/json`이 아니다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `BODY_TOO_LARGE` | 없음 | Gateway / `gateway` | 요청 body가 정책 크기 상한을 넘는다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `MALFORMED_JSON` | 없음 | Gateway / `gateway` | UTF-8 JSON으로 해석할 수 없다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `DUPLICATE_KEY` | 없음 | Gateway / `gateway` | JSON 객체에 중복 key가 있다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `INVALID_REQUEST` | 없음 | Gateway / `gateway` | 최상위 요청이 JSON 객체가 아니다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `UNSUPPORTED_ALIAS` | 없음 | Gateway / `gateway` | 지원하지 않는 alias(`max_completion_tokens`)를 썼다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `UNSUPPORTED_FIELD` | 없음 | Gateway / `gateway` | 허용 필드 밖이다. `zeroday-lab`의 `extra_fields`는 chat route에만 적용하며 completions에서는 선언돼 있어도 이 코드로 거부한다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `INVALID_MODEL` | 없음 | Gateway / `gateway` | `model`이 비어 있거나 문자열이 아니다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `INVALID_MESSAGES` | 없음 | Gateway / `gateway` | `messages` 배열·role·content 모양이 지원 범위 밖이다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `INVALID_PROMPT_TYPE` | 없음 | Gateway / `gateway` | completions `prompt`가 단일 문자열이 아니다. 문자열 배열·token-id 배열도 이 코드로 거부한다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `INVALID_MAX_TOKENS` | 없음 | Gateway / `gateway` | `max_tokens`가 양의 정수가 아니다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `INVALID_STREAM` | 없음 | Gateway / `gateway` | `stream`이 boolean이 아니다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `N_UNSUPPORTED` | 없음 | Gateway / `gateway` | `n`이 정책의 정수 범위를 벗어난다. | `INVALID` → `invalid` | §4.10 | update 담당 확인 필요 |
| `INVALID_EXTRA_FIELD` | 없음 | Gateway / `gateway` | 시험 profile의 추가 필드가 선언된 제약을 어긴다. | `INVALID` → `invalid` | §4.10의 LAB 확장 | update 담당 확인 필요 |
| `REQUEST_HEADER_TIMEOUT` | 없음 | Gateway / `gateway` | 절대 header 읽기 시간이 끝났다. | `INVALID` → `invalid` | §4.2, §7.1 | update 담당 확인 필요 |
| `REQUEST_BODY_TIMEOUT` | 없음 | Gateway / `gateway` | 절대 body 읽기 시간이 끝났다. | `INVALID` → `invalid` | §4.2, §7.1 | update 담당 확인 필요 |
| `CONNECTION_IO_TIMEOUT` | 없음 | Gateway / `gateway` | 파싱 뒤 연결 I/O 제한 시간을 넘었다. | `INVALID` → `invalid` | §4.2, §7.1 | update 담당 확인 필요 |
| `BACKEND_CREDENTIAL_MISSING` | 없음 | Gateway / `gateway` | 설정된 backend credential 원문을 얻지 못했다. | `ERROR` → `error` | §3.4, §4.2 | update 담당 확인 필요 |
| `BACKEND_CREDENTIAL_INVALID` | 없음 | Gateway / `gateway` | backend credential을 안전한 HTTP header로 쓸 수 없다. | `ERROR` → `error` | §3.4, §4.2 | update 담당 확인 필요 |
| `BACKEND_MODEL_UNMAPPED` | 없음 | Gateway / `gateway` | 허용된 정책 모델에 backend 모델 매핑이 없다. | `ERROR` → `error` | §4.2, §4.6 | update 담당 확인 필요 |
| `STORE_UNAVAILABLE` | 없음 | Gateway / `gateway` | started 기록을 저장하지 못해 relay 전에 중단했다. | `ERROR` → `error` | §4.8, §7.3 | update 담당 확인 필요 |
| `ENGINE_AUTHORIZATION_FAILED` | 없음 | engine 응답 / `gateway` | upstream 401·403으로 Gateway의 backend 자격/구성 오류다. | `ERROR` → `error` | §4.2, §7.3 | update 담당 확인 필요 |
| `ENGINE_CAPACITY_REJECTED` | 없음 | engine 응답 / `gateway` | upstream이 429로 용량 부족을 알렸다. | `ENGINE_REJECTED` → `engine_rejected` | §4.2, §7.1 | update 담당 확인 필요 |
| `ENGINE_REJECTED_REQUEST` | 없음 | engine 응답 / `gateway` | upstream이 429 외 4xx로 요청을 거부했다. | `ENGINE_REJECTED` → `engine_rejected` | §4.2, §7.1 | update 담당 확인 필요 |
| `ENGINE_BAD_RESPONSE` | 없음 | engine 응답 / `gateway` | 비정상 status·JSON·SSE 응답이다. | `ERROR` → `error` | §4.2, §7.3 | update 담당 확인 필요 |
| `ENGINE_TIMEOUT` | 없음 | engine 통신 / `gateway` | engine 연결·응답 제한 시간을 넘었다. | `ERROR` → `error` | §4.2, §7.3 | update 담당 확인 필요 |
| `ENGINE_UNREACHABLE` | 없음 | engine 통신 / `gateway` | engine 연결에 실패했다. | `ERROR` → `error` | §4.2, §7.3 | update 담당 확인 필요 |
| `CLIENT_DISCONNECTED` | 없음 | client/stream / `gateway` | stream 중 client 연결이 끊겼다. | `ERROR` → `error` | §4.2, §7.1 | update 담당 확인 필요 |
| `STREAM_DEADLINE` | 없음 | engine stream / `gateway` | 전체 stream 요청 deadline을 넘었다. | `ERROR` → `error` | §4.2, §7.1 | update 담당 확인 필요 |
| `STREAM_IDLE_TIMEOUT` | 없음 | engine stream / `gateway` | stream 청크가 idle 제한 안에 오지 않았다. | `ERROR` → `error` | §4.2, §7.1 | update 담당 확인 필요 |
| `STREAM_RELAY_ERROR` | 없음 | engine stream / `gateway` | stream relay 중 I/O 오류가 났다. | `ERROR` → `error` | §4.2, §7.3 | update 담당 확인 필요 |
| `STREAM_TRUNCATED` | 없음 | engine stream / `gateway` | `[DONE]` 전에 stream이 끝났다. | `ERROR` → `error` | §4.2, §7.3 | update 담당 확인 필요 |

## Tetragon 규칙과 내부 rule reference

근거: `runtime/src/serving_runtime/enforcement/tetragon.py`, `events.py`, `execpolicy.py`. 렌더된 file policy는 기본적으로 `Post`이고 `TG-DENY-01`만 `Post`와 `Override(-EPERM)`를 함께 낸다. 계약은 실제 거부 성공을 추정하지 않고 `action_mode: enforce`를 `enforce_attempted`로만 변환한다.

| ID | source/enforcer | 의미 | 계약 결과 | 계획서 참조 | update `check_id` |
| --- | --- | --- | --- | --- | --- |
| `TG-EXEC-01` | Tetragon adapter / `tetragon` | `process_exec`를 식별하는 adapter 규칙이다. manifest는 렌더하지 않고 아래 `exec_verdict`로 분류한다. | verdict별 아래 표; enforce 입력이면 `enforce_attempted` | §4.5, §4.11, §4.13 | update 담당 확인 필요 |
| `TG-ASSET-01` | Tetragon file / `tetragon` | 모델 디렉터리 쓰기(mask 2)를 관측한다. | monitor `detected`; enforce `enforce_attempted` | §4.5, §4.9, §4.13 | update 담당 확인 필요 |
| `TG-ASSET-02` | Tetragon file / `tetragon` | tokenizer 디렉터리 쓰기(mask 2)를 관측한다. | monitor `detected`; enforce `enforce_attempted` | §4.5, §4.9, §4.13 | update 담당 확인 필요 |
| `TG-CACHE-01` | Tetragon file / `tetragon` | cache root 쓰기(mask 2)를 관측하고 세대를 분류한다. | monitor current `observed`, other/outside `detected`, 세대 없음 `unknown`; enforce `enforce_attempted` | §4.5, §4.9, §4.13 | update 담당 확인 필요 |
| `TG-SECRET-01` | Tetragon file / `tetragon` | 선언한 secret 경로 읽기(mask 4)를 관측한다. | monitor `detected`; enforce `enforce_attempted` | §4.5, §4.13 | update 담당 확인 필요 |
| `TG-DENY-01` | Tetragon file override / `tetragon` | `enforce_probe`의 정확한 시험 경로 쓰기에 `Override(-EPERM)`을 시도한다. | `enforce_attempted` | §4.5, §4.13, §9.2 RT-SYS-03 | update 담당 확인 필요 |
| `SP3-EXEC` | ExecPolicy rule ref / `tetragon` | 모든 단계 기반 실행 분류에 붙는 내부 규칙 참조다. | 해당 `exec_verdict`의 결과 | §4.11 | update 담당 확인 필요 |
| `CACHE-01` | ExecPolicy/cache rule ref / `tetragon` | cache root와 현재 세대를 구분하는 공통 참조다. | file/exec 분류 결과를 따름 | §4.9 | update 담당 확인 필요 |
| `CACHE-02` | cache rule ref / `tetragon` | cache root 안이지만 현재 generation 밖(`other`)인 쓰기에 추가된다. `assets.cache_mount_path`를 쓰는 구성(AWS lab: 현재 세대만 `/cache`에 마운트)에서는 이 사건이 생기지 않으며, 근거는 마운트 구조 확인(`deploy/gpu-lab/README.md`)과 호스트 `enforce cache-check`다. | monitor `detected` | §4.9 | update 담당 확인 필요 |
| `CACHE-03` | ExecPolicy rule ref / `tetragon` | cache 아래의 실행 파일에서 실행된 경우 추가된다. | monitor `detected`(`exec_from_cache`) | §4.9, §4.11 | update 담당 확인 필요 |

### `exec_verdict`

| ID | source/enforcer | 의미 | 계약 결과 | 계획서 참조 | update `check_id` |
| --- | --- | --- | --- | --- | --- |
| `allowed_in_phase` | ExecPolicy / `tetragon` | 신뢰 가능한 단계 근거에서 그 단계 허용 실행 파일이다. | `observed` | §4.11, §7.4 | update 담당 확인 필요 |
| `exception` | ExecPolicy / `tetragon` | 시간·실행 파일·부모·단계·image 조건을 만족하는 승인 예외다. | `observed` | §4.11, §7.4 | update 담당 확인 필요 |
| `outside_allowlist` | ExecPolicy / `tetragon` | 신뢰 가능한 단계의 허용 목록이나 승인 예외로 덮이지 않는다. 승인 예외가 없는 maintenance-only 단계도 포함한다. | monitor `detected`; enforce `enforce_attempted` | §4.11, §7.4 | update 담당 확인 필요 |
| `suspect_binary` | ExecPolicy / `tetragon` | intent의 명시적 `suspect_binaries`에 들어 있는 실행 파일이다. 이 목록은 허용 실행 파일과 겹칠 수 없다. | monitor `detected`; enforce `enforce_attempted` | §4.11, §7.4 | update 담당 확인 필요 |
| `exec_from_cache` | ExecPolicy / `tetragon` | cache root 아래 파일 실행이다. | monitor `detected`; enforce `enforce_attempted` | §4.9, §4.11 | update 담당 확인 필요 |
| `phase_unknown` | ExecPolicy / `tetragon` | 사건 시각에 해당하는 단계 근거가 없다. | `unknown` | §4.11, §7.4 | update 담당 확인 필요 |
| `phase_signal_unverified` | ExecPolicy / `tetragon` | engine signal만으로 허용·예외 범위를 넓힐 수 없어 확인이 필요하다. | `unknown` | §4.11, §7.4 | update 담당 확인 필요 |

## Cilium 렌더 정책

근거: `runtime/src/serving_runtime/enforcement/cilium.py`. 모두 `CiliumNetworkPolicy` 후보이고 annotation은 `candidate-cluster-unapplied-unverified`다. 선택 endpoint에 ingress/egress 절을 만들면 그 방향에서 열거하지 않은 통신은 이 후보의 기본 deny 대상이지만, 실제 cluster에서는 다른 정책과의 결합 결과를 다시 확인해야 한다. 정책 객체 자체는 `runtime-status` 자료이며 `runtime-event.outcome`을 만들지 않는다. 통신을 Hubble에서 보더라도 verdict와 무관하게 계약 결과는 `observed`, enforcer는 `null`이다.

| ID(정책 이름) | source/enforcer | 허용·제한 의미 | 계약 결과 | 계획서 참조 | update `check_id` |
| --- | --- | --- | --- | --- | --- |
| `runtime-engine-ingress` | Cilium 후보 / 직접 사건 없음 | Gateway는 engine API TCP port로, observer는 management port로만 engine에 들어온다. 1.0 intent는 두 역할이 API port를 함께 쓴다. | 직접 outcome 없음; Hubble flow는 `observed` | §4.3, §4.12 | update 담당 확인 필요 |
| `runtime-engine-egress` | Cilium 후보 / 직접 사건 없음 | DNS와 선언된 engine 송신 CIDR/FQDN·port만 연다. FQDN이 있으면 DNS 이름 관측 규칙도 렌더한다. | 직접 outcome 없음; Hubble flow는 `observed` | §4.3, §4.12 | update 담당 확인 필요 |
| `runtime-gateway-egress` | Cilium 후보 / 직접 사건 없음 | Gateway에서 engine API TCP port와 DNS로만 나가게 한다. | 직접 outcome 없음; Hubble flow는 `observed` | §4.3, §4.12 | update 담당 확인 필요 |
| `runtime-acquire-egress` | Cilium 후보 / 직접 사건 없음 | acquire 역할만 승인한 artifact endpoint/CIDR/FQDN·port와 DNS에 나가게 한다. | 직접 outcome 없음; Hubble flow는 `observed` | §4.12 | update 담당 확인 필요 |
| `runtime-observer-{index}-egress` | Cilium 후보 / 직접 사건 없음 | 각 observer가 engine management listener, DNS, 선택 kube-apiserver(`observer_kube_api_port` 하나, 기본 6443)와 observer 허용 시험 receiver에만 나가게 한다. | 직접 outcome 없음; Hubble flow는 `observed` | §4.12 | update 담당 확인 필요 |
| `runtime-receiver-{name}-ingress` | Cilium 후보 / 직접 사건 없음 | 시험 receiver가 `allowed_from`에 선언된 acquire/observer 역할의 지정 port만 받게 한다. | 직접 outcome 없음; Hubble flow는 `observed` | §4.12 | update 담당 확인 필요 |

## Hubble 관측

| ID | source/enforcer | 의미 | 계약 결과 | 계획서 참조 | update `check_id` |
| --- | --- | --- | --- | --- | --- |
| 모든 Hubble flow verdict | Hubble / `null` | 출발·목적지·port·verdict·정책 귀속을 관측한다. `DROPPED`도 수신 대조 전에는 거부 성공으로 확정하지 않는다. | 항상 `observed` | §4.14, §6.3 | update 담당 확인 필요 |
| `runtime-hubble-loss/1.0` | Hubble collector / 직접 사건 없음 | `lost_events` 응답을 flow와 분리해 손실 수·source·최근 시각으로 집계한다. observations/runtime-event에는 넣지 않는다. 현재 freshness window의 손실은 collector `partial`이며, 필드 미보고는 `null`, 0은 처리한 줄에서 loss 레코드를 못 봤다는 뜻일 뿐 무손실 증거가 아니다. | 직접 outcome 없음; `runtime-status` | §4.14 | update 담당 확인 필요 |

## 재검증 trigger

근거: `runtime/src/serving_runtime/mvp/revalidation.py`. 이 항목들은 `runtime-event`가 아니라 `byul-revalidation-request/0.2`로 변환된다. 따라서 `outcome`과 enforcer가 없으며, event는 `opened`, `condition_cleared`, `condition_reappeared`, `superseded` 중 하나다. status는 현재 구현에서 `open`을 유지하고 `current_evidence_present`로 현재 조건 존재를 구분한다.

| ID(trigger) | source/enforcer | 의미 | 계약 결과 | 계획서 참조 | update `check_id` |
| --- | --- | --- | --- | --- | --- |
| `asset_changed` | asset integrity / 없음 | manifest 대조에서 변경·누락·읽기 불가·symlink·목록 밖 항목·순회 오류가 확인됐다. | 직접 outcome 없음; `revalidation-request` | §4.9, §8.2 | update 담당 확인 필요 |
| `config_drift` | engine API baseline / 없음 | 비교 행의 source가 `engine_api`이고 state가 `drift`다. 조회 실패·stale은 해소 근거가 아니다. | 직접 outcome 없음; `revalidation-request` | §4.4, §8.2 | update 담당 확인 필요 |
| `policy_content_changed` | policy history / 없음 | 같은 `policy_id`·`policy_version`의 정책 bytes SHA-256이 바뀌었다. | 직접 outcome 없음; `revalidation-request` | §4.7, §8.2 | update 담당 확인 필요 |

## 매핑 시 주의

- `rule_id`가 없는 Gateway 실패는 platform이 `reason_code`를 `rule_ref`로 쓴다.
- Hubble `DROPPED`는 `denied`가 아니며, Tetragon enforce 사건도 계약상 `enforce_attempted`다.
- Cilium 정책 이름은 후보 렌더 식별자일 뿐 적용·효과 확인 결과가 아니다.
- `related_checks`와 재검증 요청의 `evaluation_refs`는 현재 빈 배열이다. 이 문서가 그 값을 임의로 채우지 않는다.
