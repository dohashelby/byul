# Online native 공통 계측 /9

명시적 `vllm-native-lifecycle-metadata/9`·`guard-native-lifecycle-events/9`를 추가한다.
기존 기본 /8의 source bytes·설정·bootstrap·관측 해석과 /1..7 수용 범위를 유지한다.
다음 기존 제품 명령에서만 선택한다.

```text
guard prepare-lifecycle-sensor --profile vllm-native-lifecycle-metadata/9 --output <새 소유 경로>
```

prepare는 엔진을 기동하지 않는다. 새 manifest는 기존 assets/1 형식 안에서 /9에 한해
controller, 고정 /8 base, 고정 B callback, sitecustomize, settings의5개 파일을 가진다.
base는2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd,
B callback은bd15958029e91ba189c2fe588009a97ddd4d3d339e32824e5e5016a52fa736b1이다.
manifest 해시를 다시 계산해도 라이브러리의 고정 bytes 검사를 바꿀 수 없다.
원래 B b0ff9fae·abd0a9f2의 소스/시험/문서를 원본 추적 커밋으로 소비한다.

## Serving 설치와 지점

운영자가 검토된5개 자산을 기존 소유 `/guard-lifecycle` read-only source mount와 private data
mount에 배치하고, 기존 sitecustomize·GUARD_LIFECYCLE_CONFIG·ASGI middleware 연결로 선택한다.
새 controller는 시작 시 고정 base/callback bytes와 callback 실행 code를 확인하고 private module로
직접 실행한다. 이 프로세스에서만 hook이 설치된다. 별도 Pod exec의 Python은 serving hook이 아니다.
이미 선정 vLLM module이 import됐거나 source/code·함수 형태가 다르면 설치/해당 hook을 닫고
원래 native 실행을 유지한다. 직접 경로·임의 import module·임의 callback은 설정으로 받지 않는다.
검토된 CPython3.12.3/3.12.14·optimize0과 vllm0.31.0·정확 source
db9527a46873454610df6dbedf79a36d6bf1a7f6만 대상이다. 다른 Python patch/구현은 /9 설치를 닫는다.

| module | source bytes SHA256 | actual point |
| --- | --- | --- |
| vllm.renderers.online_renderer | 1ada26191425fd9d56c3525949f999826dad9f2c82df94ad586a1cf6f67b089e | OnlineRenderer.render_chat, preprocess_chat, validate_chat_template; online_renderer._reused_prompt_token_ids |
| vllm.entrypoints.generate.base.protocol | 67ede411b35a8344ddb87bfaaf1a1abea8acd39a4d23655e34032015505167c0 | protocol.validate_request_mm_kwargs |
| vllm.v1.engine.input_processor | d79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b | InputProcessor.process_inputs, _validate_model_input, _validate_prompt_len |

CheckedLoader는 실제 loader의 source와 실행 code가 같은지 비교하고, 각 실제 함수 code도
고정 literal과 확인한 뒤 변경을 한 번에 적용한다. protocol hook은 renderer가 함수 alias를
import하기 전에 설치한다. async는 실제 await의 반환/예외, sync는 실제 반환/예외를 관측한다.
인자·iterator·원래 반환/예외를 바꾸지 않는다. callback 관측 실패는 native 예외를 대체하지 않는다.
reuse pop은 원래 함수 실행 전 CALL을 먼저 관측한다.

## 요청·세대·출처

같은 살아 있는 POST `/v1/chat/completions` ASGI 문맥의 정확 단일 X-Request-Id에서 시작한다.
기존 context의 PID·closed·invocation 검사를 재사용하며 종료 뒤 상속된 task나 다른 요청은 묶지 않는다.
각 실제 호출에 하나의 call_id hash를 만들고 CALL과 RETURN/RAISE에 동일 binding을 전달한다.
session과 process start는 B native ASCII JSON digest 규칙, ASGI/native RID는 원래 문자열 bytes SHA다.
InputProcessor의 실제 request_id 인자 hash를 중첩 validation 호출에 결합한다. 같은 입력에서
유도한 source-correlated 결합이며 독립 인증·인가·최종 worker/GPU 소비 증명은 아니다.
재사용된 ASGI ID·여러 native 호출의 완전성/유일성 판단은 B 전용 분석이 유지한다.

기존 MODULE `code_sha256`은 실제 filename·line·bytecode·예외표를 포함한 의미를 유지한다.
새 `online_code_sha256`과 `python312-source-relative-code/1`은 검증한 같은 실행 code의
모든 nested co_filename만 해당 repository-relative filename으로 대체해 기존 code_digest를 계산한다.
두 hash는 서로 다른 값이다. 같은 source라도 CPython patch에 따라 compile graph가 다르므로
고정 RUNTIME_CONTRACTS에 두 compiler의 module/function/callback code literal을 별도로 둔다.
값은 검토된 immutable source의 offline compile에서 얻었고 실제 대상 code를 읽어 승인하지 않았다.
controller는 실제 interpreter 이름/정확 판본으로 이미 고정된 계약을 선택하며 미등록 판본을 거부한다.
MODULE의 online_runtime을 기록하고 parser는 한 PID/start 기록에서 compiler 혼합을 거부한다.
수집 호스트와 다른 검토 compiler의 기록도 해당 literal로 재판정할 수 있다.
B가 새 POINT_CONTRACTS/MODULE_CONTRACTS 및 RUNTIME_CONTRACTS의 source/function/module literal을
검토해 새 필드를 소비해야 한다. 관측 시 읽은 hash로 pin을 승인하지 않는다.
callback code는 고정 `guard/adapters/vllm_online_input.py` filename으로 compile한 실행 module
graph의 hash이며 `online_callback_provenance`에 source bytes/code와 함께 별도 기록한다.

## Writer·parser·수집

metadata.online_input은 B의 `byul-online-input-snapshot/1` 전체를 담는다. 정제된 고정 key·enum·
hash·bool·count·256bit signed min/max만 전용 validator로 허용한다. 본문·토큰 목록·URL·
예외 문자열·임의 metadata는 허용하지 않는다. 기존 모든 판본에 임의 dictionary/string을
허용하는 방식으로 parser를 완화하지 않는다. source·callback MODULE이 먼저 확인돼야 하고
실제 파일 START의 session/PID/start와 binding을 비교한다. call 재사용·세대 교체는 거부한다.
닫히지 않은 CALL/종결만 있는 관측은 incomplete 수로 남으며 전체 coverage를 참으로 만들지 않는다.

/9 writer는 process당2048행·2MiB, 행당 parser16KiB를 상한으로 한다. 기존 판본의512행·256KiB는
유지한다. 공통 파일 packet 검증의 `owned-jsonl-2m-v1`은 명시적 새 읽기 계약이며, Kubernetes는
선정 /9 prepared일 때만 해당 세션의 고정 pid/start 파일에서 사용한다. 이전 프로필·다른 세션·
임의 명령·256KiB 기본 계약은 확장하지 않는다. 기존 전후 소유 Pod 세대·nofollow/fstat·시간/출력
한도·부모 probe 정리를 재사용한다. /9도 최대32개 파일의 기존 session census 범위 안에 있다.
자원 소모/실제 getter 지연과 수집 영향은 실제 serving 시험에서 별도 확인해야 한다.

기존 capture-lifecycle-observations·capture-lifecycle-session 경로로 읽고, 선택한 session seal을
재사용한다. seal은 writer tail의 닫힘이지 serving worker/GPU 종료가 아니다. Kubernetes의
lab=true·exact ENV/source/image gate, image_check=False와 native0.32 미지원은 유지한다.
callback provenance만으로 image_digest_verified·MEASURED·실제 인수·PASS를 생성하지 않는다.

## 검증과 남은 범위

고정된 실제 함수 body/리터럴 code, B callback, writer/parser, 같은 계약·이전 판본·불변식 및
Linux/설치 검증을 구분해 인계한다. EXAMPLE fixture는 실제 serving 실행이 아니다.
B PR95의 현재 metadata/binding/code proposal 전체를 이미 지원한다고 가정하지 않는다.
새 source pin과 canonical code 소비는 B가 확정·검증해야 하며 B 전용 판단 코드는 변경하지 않는다.
실제 source/image/wheel/model/ENV/POL·정상/위반/누락/교체/restart·후속 수집/첨부 오류와
EVD/RES/RPT·정확 Ref 왕복은 해당 제품 실행에서 마감한다. 실제 조건 없는 부분은 미완료다.
GPU는 stopped·추가Start0이며 원래 시간/비용 창을 초기화하지 않는다.
