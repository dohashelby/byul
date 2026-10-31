# Online 공개 HTTP 입력 /1

`guard.runtime.kubernetes_http.ONLINE_INPUT_PROFILE`은
`kubernetes-owned-online-input-http-v1`이다. 기존 공개
`KubernetesHTTP(target, env, duration=..., binary="kubectl", profile=ONLINE_INPUT_PROFILE)`,
`transport("kubernetes-probe", [request], timeout=...)`, `report()`를 사용한다.
B PR95의 확정 e317a42109b30c59edd10a0f91c9d39b3847ad9f 요청 형식을 소비한다.
B callback 원본·전용 snapshot·분석·판정·literal pin은 이 묶음에서 변경하지 않는다.

## 선택 대상과 전송 계약

기존 guard-kubernetes-request-binding-target/1의 exact ENV Ref·모델 repository/revision→API
alias·직접 IPv4 Pod endpoint·소유 run을 재사용한다. vllm/0.31.0만 지원한다.
매 요청 전후 engine/probe namespace UID·Pod UID·containerID·imageID·restartCount·startedAt을
확인한다. 프로브의 선택 컨테이너와 serving worker는 서로 다른 주체다. API 보고 이미지와
고정 수집 코드 검증은 실제 engine/native code·runtime image ancestry·worker 소비의 증명이 아니다.

한 호출에는 아래 요청 하나만 전달한다. case는 `[a-z][a-z0-9-]{0,59}`이며 최대16개다.
같은 case의 primary가 반환한 뒤 정상 after를 보내야 다음 case를 시작할 수 있다.
같은 transport의32자리 소문자 hex token은 동일하고 case 재사용·중간 삽입·병렬 전송은 금지한다.
Online 호출만 직렬화하며 기존 sampling/REQ10 동시성은 유지한다.

| 항목 | 닫힌 지원 범위 |
| --- | --- |
| id | `online-<case>-primary` 또는 `online-<case>-after` |
| method/path | POST `/v1/chat/completions`, 선택 endpoint와 정확히 일치, query/fragment 없음 |
| mode/header | JSON, `X-Request-Id: byul-online-<32lowerhex>-<case>-primary/after` 하나 |
| 공통 body | 정확 API alias인 model, messages, 정수 max_tokens=1, stream=false 또는 생략 |
| messages | 1..16개의 정확 role/content 객체, role=system/user/assistant, content 문자열4096자 이하 |
| primary 추가 필드 | kv_transfer_params, echo, chat_template, chat_template_kwargs, mm_processor_kwargs, media_io_kwargs |
| after | 공통 body만 허용; 보호 필드는 null/false도 허용하지 않음 |
| 옵션 | want_body=true 또는 생략, read_delay_ms=정수0 또는 생략 |

echo는 bool/null, chat_template은 문자열/null, 세 kwargs 필드는 JSON 객체/null이다.
kv_transfer_params는 null 또는 prompt_token_ids만 있는 객체이며, 해당 값은4096개 이하의
signed64 정수 목록이다. bool token은 거부한다. 빈 객체·빈 목록은 명시적 입력으로 허용한다.
원격 engine/address/transfer 설정·multimodal content·SSE·임의 body key/header는 지원하지 않는다.
문자열의 URI/data:/www. 주소는 거부하며, 검증된 model alias 자체는 주소처럼 보여도 유지한다.
입력 token의 실제 모델 vocabulary/domain 적합성과 callback/handler 정책은 B 전용 소비 범위다.
음수 등 제한 내 잘못된 token 입력이 곧 실행 성공이나 확인된 위반을 뜻하지 않는다.

## 한도와 오류

전체 duration은 기존0<duration<=300초, Online 호출 timeout은0<timeout<=30초다.
body 전체는 depth<=12·nodes<=4096·문자열4096자·key128자·정수256bit·유한 float의 JSON이며,
정확 UTF-8 compact wire body는16384bytes 이하다. B의131072bytes 제안 전체를 지원하지 않는다.
기존 고정 stream_probe의16KiB 요청/128KiB 응답, stdin128KiB·probe packet2MiB,
exec watchdog·부모 프로세스/pipe 회수 검사를 유지한다. 수집기·프로필을 완화하지 않는다.

요청 형식/순서/대상 오류는 I/O 전에 고정 KubernetesBindingError로 닫힌다.
전후 세대 교체·timeout·불완전 응답·부모 회수 오류는 기존 gap/cleanup 경로를 따른다.
정상 HTTP400도 전송 결과일 뿐이며 오류 원문·예외는 report에 저장하지 않는다.
after 누락은 collection_gap이다. 이미 반환한 primary 관측은 after 실패로 삭제하지 않는다.
Pod·다른 PID·다른 세션을 종료하지 않으며 소유 exec 프로브만 정리한다.

## 출처와 남은 계측

report는 기존 binding/source hash·case ID·outcome·완전 수집·전후 결합·gap/cleanup에
input_contract_version=1, data_origin, native_consumption_verified=false를 추가한다.
runner 주입·lab·EXAMPLE ENV는 EXAMPLE이며 DECLARED ENV를 MEASURED로 승격하지 않는다.
요청/응답 원문·wire UUID·헤더·token ID 목록은 report에 남기지 않는다. 응답 원문은 기존
transient 반환값에만 있고 B는 자신의 정제 수집 계약과 실제 POL로 증거를 만든다.
B가 EVD 출처를 공통 전송보다 높게 기록하면 안 된다.

HTTP 전송 완료와 native 입력 소비는 별개다. 실제 serving hook에서 고정 B callback을 호출하고,
metadata.online_input의 판본·parser·source/code provenance·실제 request/call/session/PID/start
결합을 확인하는 계측은 다음 별도 묶음이다. 선택 session seal은 eb600b1의 공통 API를 재사용하되
실제 writer 종료/관측 인수는 별도로 검증한다. Pod exec 별도 Python을 serving hook으로 세지 않는다.
기존 native lab/source/image gate와 native0.32 미지원은 유지한다.

직접/인접/설치 검증 뒤 확정 commit·파일 bytes SHA와 지원 범위를 A1 인계에 제공한다.
B는 원래 pin을 보존하며 새 고정 소스를 검토해 소비해야 한다. 런타임에 읽은 hash를 승인하지 않는다.
이 묶음에는 실제 Kubernetes/서버/worker/GPU 실행이나 전체 Online 완료가 없다.
실제 ENV/POL·새 세대·정상 대조·관측/복구/정리 소유권과 실행 조건이 필요하며,
기존 GPU stopped·추가Start중단·기존 시간/비용 경계는 유지한다.
