# 자동화 2차: 변환 후 입력과 관측 계약

기준은 `1d1463274529708ae5fe5e04f42c134ba5a10ec3`(1차·초안 PR16)이고 작업 브랜치는 `codex/gua-automation-phase2`다. 원격 1차와 같은 커밋에서 시작했으며 main의 관계없는 변경은 합치지 않는다. [실행 프롬프트](AUTOMATION_PHASE2_PROMPT.md)를 문서로 만든 뒤 해당 범위를 구현·검증했다.

## 이번 묶음에서 바꾸는 것

- REQ-07을 고정 text-chat template 프로파일의 자동 점검으로 등록한다. 실제 렌더 token IDs와 추론의 실제 소비 prompt_token_ids, 원 입력/변환 후 경계, source에서 확인한 사전 거부 위치를 대조한다.
- 별도 CPU chat 프로파일로 REQ-13의 private_system 챌린지를 실제 시험할 수 있게 한다. 기존 completion 정책값·FAIL·이전 증거는 보존한다.
- CPU 사례 행렬의 세 번째 구성을 UNKNOWN 고정 기대에서 **짧은 관측 시간의 증거 계약**으로 구분한다. 판정을 바꾸지 않고 충분한 관측 PASS·미수집 UNKNOWN·독립 FAIL 보존을 검증한다.

새 실행기는 9번째 자동 연결이다. 77개 전체·모든 template/processor/내용 제한·기업 승인·GPU·두 실제 이미지 비교 완료를 뜻하지 않는다. REQ-11 일반/SSE 귀속과 OBS-09 실제 로그 변환 경로는 다음 묶음으로 남긴다.

## 고정 chat 시험 프로파일

기존 `examples/windows-cpu.toml`은 바꾸지 않는다. 별도 [windows-cpu-chat.toml](../examples/windows-cpu-chat.toml)은 `[vllm] chat_template_profile='guard-text-chat-v1'`을 명시하고 REQ-13 실행 endpoint를 `/v1/chat/completions`로 선택한다. REQ-09와 기존 completion 점검은 기존 endpoint를 유지한다. CPU2·threads2·RAM3GiB·RAM+swap5GiB·shm512MiB·PID256·context128·FP32·KV64MiB 상한도 유지한다.

자산은 [고정 template](../src/guard/adapters/assets/chat/guard-text-chat-v1.jinja)이고 SHA-256은 `e5b96cc7595d53305284a40f579747da846f01b566ad571ad2cb67526bdadc2e`다. 모델 snapshot 밖 `/guard-assets/chat/guard-text-chat-v1.jinja`에 읽기 전용 마운트한다. 패키지의 정해진 자산만 허용하고 누락·변조·임의 Jinja·다른 경로·요청 template trust 활성화는 이 프로파일에서 허용하지 않는다. 해시 보호 자산은 `.gitattributes`에서 줄바꿈 변환을 막는다.

기동 argv에 `--chat-template`와 `--chat-template-content-format string`을 넣고 요청 template trust flag는 넣지 않는다. 비교 설정에는 profile/version/SHA/container path/content format/request override 불허와 실제 argv를 넣는다. 패키지 설치 위치나 호스트 임시 절대 경로가 이 template 비교 조건을 바꾸지 않는다. 어댑터 버전은 0.3.0이며 기존 기본/legacy 인자는 보존한다.

이 자산은 messages를 텍스트로 형식화하는 **시험용**이다. OPT가 지시 학습됐거나 system 메시지를 비밀로 지킬 것이라는 주장을 하지 않는다. source가 제공하는 chat endpoint 활성화와 실제 정상 추론 성공은 따로 확인한다.

## 실제 이미지 소스에서 확인한 계약

기존 pinned image `vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b`의 0.31.0 소스를 기동 없이 중지 컨테이너에서 읽었다. 검토용 본인 자원만 정리했고 기존 컨테이너는 보존했다. source commit 자체는 UNVERIFIED이며 이미지의 실제 파일 bytes/SHA와 코드 위치로 근거를 한정한다.

- `entrypoints/launchers/cli_args.py`: 고정 template 파일·content format과 request template trust 기본 false.
- tokenize protocol과 serving: messages→서버 template 처리, token IDs/count/max_model_len 반환, `add_generation_prompt=true`, `add_special_tokens=false`의 chat 비교.
- chat completion serving: render된 engine_input을 generate에 전달하고 `return_token_ids` 요청 시 실제 RequestOutput의 prompt_token_ids를 반환.
- renderer: 신뢰가 꺼진 요청 template·template kwargs의 override를 사전 거부.
- renderer params: 렌더 후 입력 길이와 context/output 조건의 검사 위치.

어댑터는 이 source/이미지의 구체적인 오류 형식·숫자·위치를 확인한 경우에만 사전 거부 근거를 제공한다. 같은 버전 문자열의 다른 이미지, 임의 400·인증 오류·미지원 오류·5xx·timeout을 제한 성공으로 보지 않는다. 자세한 소스 파일 SHA와 줄 번호는 이번 저장소 밖 실증 산출물 `chat-image-source-review.json`에 보존한다.

## REQ-07 정책과 증거

| 정책 키 | 뜻 | 시험 설정과의 구분 |
|---|---|---|
| allowed_template_sha256 | 기업이 허용한 template bytes의 SHA 목록 | 빈값/중복/형태 오류는 거부. 목록에 있다고 승인 진위가 인증되는 것은 아님 |
| max_rendered_input_tokens | 변환 후 실제 입력의 채택한 token 한도 | 양의 정수이며 기업 값에 임의 상한을 두지 않음. probe budget을 넘으면 기준을 줄이지 않고 미검증으로 남김 |
| request_template_override_allowed | 요청 override 허용 여부 | false를 검증하는 고정 프로파일만 지원. true는 유효한 정책이지만 해당 검토 문맥은 미지원 |

[별도 DRAFT 예시 정책](../examples/automation-chat-policy.yaml)은 REQ-07을 추가하고 고정 읽기 전용 자산 마운트를 PLT-01의 예시 허용 목록에 명시한다. REQ-13 정책 값은 1차와 같다. 새 자산 마운트를 기업 승인으로 간주하거나 런타임이 자동 허용하지 않는다. 127은 context128·output1의 진단 fixture 기준이며 기업 기본값이 아니다. 32의 작은 반례 정책은 별도 실행에서만 사용한다.

실행기는 자신의 managed engine label·실제 image/argv·읽기 전용 마운트와 container 안 template SHA를 전후 확인한다. host 경로·원시 inspect/env·예외 문자열은 증거에 저장하지 않는다. 실제 template과 원래 모델 파일은 수정하지 않는다.

4개 챌린지는 정상 렌더/소비, 정확한 정책 경계 안, 원 입력은 경계 안이지만 렌더 후 경계+1인 입력, 두 고정 무해 override다. `/tokenize`의 실제 IDs와 실제 chat 응답의 prompt_token_ids를 정확히 대조한다. 같은 개수라도 다른 IDs, model/choices/finish/usage 오류, 출력 잘림, 신원 변경은 충분한 소비 근거가 아니다. 일반 콘텐츠 의미·arbitrary processor·내장 fallback·멀티모달은 별도다.

수집된 `max_consumed_prompt_tokens`는 실제 일치한 소비 응답만으로 계산하며 POL 숫자와 직접 대조한다. `tested_rendered_input_limit`은 정확한 양쪽 표본과 실제 챌린지 송신이 있을 때의 측정 경계다. 저장 증거를 다른 POL 문맥에서 평가할 때, 낮아진 예산의 초과 소비는 FAIL이고 시험하지 않은 새 경계를 기존 true 플래그로 PASS 처리하지 않는다. 정상/경계/후속 기능 표본을 실제 추론과 소비 IDs로 확인한다. 성능은 UNKNOWN이다.

`declared/0.11.0`은 새 정의의 수동 신고 PASS에도 같은 순수 정책 검증과 고정 판정표 상한을 적용한다. 빈 목록·잘못된 숫자·미지원 override 문맥·불충분한 관측의 신고 PASS는 UNKNOWN이고 raw FAIL/UNKNOWN과 이전 정의의 판정은 보존한다. 판정기는 엔진/실행기/네트워크/시각/I/O에 의존하지 않는다.

## 제품 실행

REQ-07의 요청 timeout은 120초 이내의 유한 양수로 제한하며 별도 chat 예제는 120초다. 전체 새 요청 송신 마감은 300초이고 매 요청에 `min(요청 timeout, 남은 시간)`을 적용한다. Docker 기동·정리 시간까지 벽시계 300초를 보장하는 설정은 아니다. 기업 입력 token 기준·요청 수·입출력·CPU/메모리 상한은 그대로다. REQ-13 등 다른 실행기의 30초 설정은 변경하지 않는다.

첫 실제 시도는 30초 설정에서 첫 생성이 30.095초에 timeout되고 후속 정상 응답은 79ms에 돌아왔다. 양쪽 입력 한도 사례의 원래 REQ-07 UNKNOWN/UNKNOWN과 EVD·RES·보고서·반출물은 보존한다. 재시도나 사전 warmup으로 그 실행의 정상 대조를 대체하지 않고, 120초 관측 설정을 명시한 새 시도로 별도 검증한다. 이는 POL이나 판정표를 PASS에 맞추는 변경이 아니다.

실제 CPU ENV·repository@digest·원래 model revision/inventory와 snapshot 경로를 준비한다. 별도 TOML의 models_dir를 실제 절대 경로로 바꾸고 다음 명령을 사용한다. 준비 검사는 기동/이미지 수신을 하지 않는다.

```text
guard prepare-validation --pack packs/PACK-BASELINE --env env.yaml --policy automation-chat-policy.yaml --config windows-cpu-chat.toml
guard validate --pack packs/PACK-BASELINE --env env.yaml --policy automation-chat-policy.yaml --config windows-cpu-chat.toml --store out/test-runs/chat-unique/store --output out/test-runs/chat-unique/run
```

한 번에 엔진 하나와 필요한 Gateway/일시 probe만 사용하고 기존 kind 등 다른 컨테이너는 유지한다. 각 실행은 새 출력 폴더에 저장한다. REQ-07의 진단 token 반환 요청과 REQ-13의 반환 금지 시험은 서로 다른 검증 질문이며 실제 비밀 입력을 쓰지 않는다. 실행 성공 exit0은 보안 전체 PASS라는 뜻이 아니다. 모든 예시/실증은 EXAMPLE/DRAFT·official=false다.

## 시간 제한 행렬과 과거 증거

두 번째 실제 시도에서 정상·경계127·후속의 정확한 token 소비와 기능 PASS를 확인했지만 보안은 UNKNOWN이었다. 원인은 vLLM의 실제 오류 직렬화가 입력 제한 메시지 뒤에 `(parameter=input_tokens, value=128)`을 붙이는데 어댑터가 이 정밀 형식을 아직 지원하지 않은 것이다. 실제 이미지의 `exceptions.py.__str__`와 오류 응답·정제 경로를 읽어 숫자·parameter·전체 문구가 정확히 일치하는 계약으로 보수했다. 일반 400이나 접두사 일치로 완화하지 않는다. 두 번째 시도의 원본 UNKNOWN도 보존한다.

같은 두 번째 시도의 별도 입력 한도32는 33토큰을 실제 소비해 보안 FAIL·기능 PASS였다. 이 정상 수신/독립 위반 경로는 입력 거부 오류 해석을 사용하지 않는다. 따라서 마지막 보수의 실제 재검증은 미해결된 127 경계 사례만 새 세 번째 시도에서 수행하고, 기존 32 사례의 FAIL과 저장 팩 재판정·증거는 그대로 보존한다.

1차의 원래 `cpu-cases-r4`와 [선별 요약](evidence/automation-phase1-20261023.json)은 불변이다. 원래 보안 기대 14/15·exit1을 현재 판정에 맞춰 재작성하지 않는다. PLT-01의 inspect/exec와 정상 대조에는 별도 30초 상한/최소값이 있어 1e-6 설정이 모든 수집에 적용되지 않는다. 다른 HTTP 경로 역시 시간만으로 항상 미수집이라고 단정할 수 없다.

새 행렬은 첫 두 고정 기대를 유지하고 세 번째 관측 계약의 결과를 별도로 기록한다. 자료 부족의 PASS는 거부하고 충분한 정상/필수 관측/전체 챌린지·고정 CheckDef/rule_table와 정확히 일치한 실제 PASS는 보존한다. 확인된 FAIL을 UNKNOWN으로 지우지 않는다. 기존 결과를 분석하는 모드는 원본을 읽기 전용으로 검사하고 새 출력에 독립 결과를 저장한다. 결정적인 수집 실패는 합성 I/O 회귀로 따로 확인한다.

## 검증 기록

직접 실행한 2차 결과다. [선별 증거 JSON](evidence/automation-phase2-20261023.json)은 실제 EVD·RES·RPT Ref, 첨부 해시·정확한 소비 token 해시·실제 이미지 소스 SHA·최종 소스 파일 해시를 기록한다. 원시 store·JSON/HTML·반출물·명령 로그는 저장소 밖의 별도 출력에 보존했다. 선별 JSON만으로 전체 bundle의 독립 복원이나 기업 승인 진위를 입증하지 않는다.

환경: Windows11 x64·Docker Desktop Linux/WSL2·CPython3.11.16 64bit·동결 의존성 전용 venv. 이미지와 CPU 자원은 위 고정값, 모델은 facebook/opt-125m revision `27dcfa74d334bc871f3234de431e71c6eeba5dd6`, 7파일 inventory `b11bc2cafab45720afd3d2f19c8250352977bc216efb88fc0afb9d90edc2aea6`다.

| 실제 시도/점검 | 보안 | 기능 | 성능 | 확인한 것 |
|---|---|---|---|---|
| REQ-07 | PASS | PASS | UNKNOWN | 최종03: 정상·127 경계·후속 실제 IDs 일치, 렌더128 사전 거부·고정 override 거부와 template SHA 전후 확인 |
| REQ-13 | FAIL | PASS | UNKNOWN | 최종03: engine/gateway private_system 실제 시도·정상 전후 대조. 1차 정책을 바꾸지 않아 반환 옵션/필드의 확인된 FAIL 유지 |
| PLT-01 | PASS | PASS | UNKNOWN | 최종03: 명시된 readonly fixture 마운트를 포함한 강화 구성·정상 요청 확인 |
| REQ-07 별도 한도32 | FAIL | PASS | UNKNOWN | 시도02: 33토큰 실제 소비의 독립 위반. 이 경로는 마지막 수정한 입력 거부 해석을 사용하지 않아 반복하지 않음 |

기본 전체 명령은 `python -m pytest -q --durations=12 --basetemp <새 출력>/pytest-temp-r4 --junitxml <새 출력>/pytest-r4.xml`: **1,781 passed, 5 skipped, 18 deselected, JUnit suite 364.50초**다. 마지막 오류 직렬화 보수 후 `python -m pytest -q tests/test_chat_profile.py tests/test_template_validation.py tests/test_template_policy_contracts.py --basetemp <새 출력>/pytest-final-focus-temp --junitxml <새 출력>/pytest-final-focus.xml`은 **128 passed, JUnit suite 7.20초**다. 최종 커밋 전체 Linux CI는 PR의 해당 SHA 실행 기록에서 별도 확인한다.

기본 제외 18개는 engine/docker 표식 시험이다. skip5개는 Windows 닫힌 루프백 포트의 refused/timeout 분류 3개와 symlink 권한·지원 2개다. 통과로 세지 않는다. 이번 별도 실측을 제외된 모든 엔진/서브 시험의 실행으로 대체 집계하지 않는다. 전체 기본 실행은 Windows에서 저장소의 1분 목표를 넘는다. fixture I/O와 기존 subprocess 테스트 시간은 후속 최적화 대상으로 남기며 테스트를 숨겨 목표를 맞추지 않았다.

첫 전체는 1758통과·7실패였다. 시험용 Ref 그래프 미연결4·cp949 기본 인코딩1·새 등록 기대값2를 고쳤고, 집중 재검증에서 추가로 드러난 현재 카탈로그/REQ-07 기대값을 갱신했다. 정책·판정표·실제 증거는 실패를 맞추기 위해 바꾸지 않았다. 실제 시도01의 cold30초 UNKNOWN, 시도02의 직렬화 미지원 UNKNOWN도 보존했다.

모든 실제 시도에서 제품 verify·반출·새 위치 복원·verify·현재 외부 팩이 없는 경로에서 저장 팩 재판정·새 JSON/HTML·verify가 통과했다. 원본 객체/보고서/바이트·모델7파일·기존 컨테이너의 ID/상태를 보존하고 새 관리 자원을 정리했다. 소유 엔진은 매번 하나씩이었다.

1차 matrix/1의 원래 success=false·14/15·exit1을 보존한 읽기 전용 분석은 observation_contract_success=true, 원본590파일 바이트 불변이다. 저장 팩17/원본·복원·bundle과 대조했다. PLT-01의 실제 충분한 관측 PASS를 UNKNOWN으로 지우지 않는다.

팩18/REQ-07rev2 해시 `4d00aec5587635ab777807c07509dcba57fa23c34705591e1aa35bc110feff54`, metadata41경로, 카탈로그314행 prefix+3행의 보존·출처 pointer와 2회 재생성을 확인했다. 기존76점검·core/models/store·참고 자료·과거 추적 산출물 등179파일의 기준 바이트는 같다.

최종 wheel은 저장소 밖의 새 frozen 환경·Python -I에서 71개 소스/wheel/설치본 파일의 집합과 바이트, 고정 template, 17개 CLI의 기대 종료 코드와 readonly chat 준비를 확인했다. 동시에 실행 중인 실제 시험으로 전후 Docker ID 비교가 변했던 중간 r2 검사는 실패로 보존했고, 실제 기동이 없는 r4에서 동일한 계약으로 성공했다.

남은 범위: 임의 template/processor/fallback/멀티모달/기업 기준·승인, REQ-10 하위 소비, OBS-03 예외 dump, REQ-11 일반/SSE 귀속, OBS-09 실제 수집기 변환, CPU 성능·GPU·두 다른 실제 이미지 업데이트 비교다. 모든 보고서는 EXAMPLE/DRAFT·팩DRAFT에 따라 official=false이며 관측 MEASURED와 입력 사실/최종 verification_level을 구분한다.
