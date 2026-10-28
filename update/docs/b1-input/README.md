# B-1 입력 CPU 분기

배정은 REQ-10 → REQ-08/09/07이다. B-2의 응답/로그/저장과 SUP·캐시·배포는
수정하지 않는다. 별도 worktree/branch를 사용한다. PR43 head
8f05655c09bcee7156c17093a47b482a56abe2f4와 이전 CPU 증거를 보존하고 후속 PR46에
전용 변경을 올린다. 공통 코드·sampling_validation·registry·팩은 변경하지 않는다.
참조는 vLLM 5d35ce3934a7804b298559d2d99eec33792e1eb9다. 실제 설치판 및
native 센서 참조 db9527a46873454610df6dbedf79a36d6bf1a7f6와 구분한다.

## 실제 renderer/admission 경로

REQ-08: `BaseRenderer.render_cmpl`→HfRenderer→`InputProcessor.process_inputs`에서
일반 text·재사용 IDs·음수/OOV/길이 초과·text+IDs를 대조한다. 실제 선택 값의
canonical hash와 길이만 저장한다. SDK 내부 수용을 공개 endpoint 노출·허용 주체·
기업 승인이나 최종 runner 소비로 확대하지 않는다.

REQ-09: `_validate_model_input`의 embeds/mask 대응과 `process_inputs`의
routed_experts_prompt_start를 실행한다. 3행 embedding에 mask 길이 0/1/2/3/4,
offset -1/0/3/4/2**127을 대조한다. 1차원·hidden-size 불일치는 관측만 하고
consumer 전체 dtype/shape 규칙을 만들지 않는다. admission과 실제 embedding
연산을 구분한다. 공식 근거 `tests/test_request_input_bounds.py:61–122`의 Mock
processor를 실제 설치 경로로 보고하지 않는다. 이 도구는 실제 config/renderer/
InputProcessor 생성자를 쓰고 private attribute 설정·대역·메서드 patch는 쓰지 않는다.

REQ-07: 실제 render_chat에 고정 template와 작은 literal override를 전달한다.
정상 template, 렌더 후 15-token 경계/16-token 초과, 짧은 override를 비교한다.
model max length 16/vocab 32는 로컬 fixture 설정(DECLARED)이며 기업 한도가 아니다.
template override API 허용·상위 호출자 검사·공급 template 안전성은 이 시험으로
완료하지 않는다. 위험 Jinja·대규모 루프·비용 부하는 쓰지 않는다.

각 사례 후 일반 text를 같은 renderer/processor에 제출한다. CPU admission의
후속 대조이며 추론/타 요청 생존이 아니다. 원문 수정 없는 sys.setprofile로 함수
진입을 관측한다. 요청·토큰·응답·예외 본문은 저장하지 않는다. 독립 위반을 기존
rule_table fail_when_any용 전용 관측으로 투영한다. 누락·오류·정책 부재로 보안
PASS를 만들지 않는다.

## CPU 환경과 명령

B-1 전용 Python 3.12 venv에 이전 CPU freeze를 --torch-backend cpu로 설치한다.
제품 pyproject.toml/uv.lock은 보존한다. CPU torch, 작은 로컬 GPT2 config와 직접
만든 WordLevel tokenizer만 사용한다. 모델 weights나 엔진은 만들거나 다운로드하지 않는다.

```sh
export VLLM_TARGET_DEVICE=cpu VLLM_LOGGING_LEVEL=CRITICAL CUDA_VISIBLE_DEVICES= VLLM_PLUGINS=
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export PYTHONPATH="$VLLM_SOURCE:$BYUL_B1_REPO/update/src"
"$CPU_VENV/bin/python" "$BYUL_B1_REPO/update/tools/b1_input_cpu.py" --source-root "$VLLM_SOURCE" --workdir "$FRESH_B1_EVIDENCE_DIR"
```

소스 SHA·실제 import 경로·깨끗한 참조·CPU torch를 검사하고 새 workdir만 허용한다.
실패는 예외 type/부족 모듈/파일명·함수·행만 남긴다. 증거와 fixture 설정·tokenizer는
저장소 밖 B-1 폴더에 둔다. 파일 bytes 해시와 입력 정규 객체 해시를 구분한다.

A 대기는 제품 연결에만 적용한다. 정확 공동 SHA, HTTP/정제·첨부 실패 보존 계약,
전용 선택 등록, CheckDef 관측/판정 연결이 필요하다. 인계 후 CLI→실행→EVD→RES/RPT를
검증한다. 실제 대상은 설치판·모델·기능·소유자·승인과 소비/영향 계측이 추가로 필요하다.
현재 그 관측이 없어 실제 실행만 남았다고 보고하지 않는다.

## REQ-10 추가 sampling CPU 경계

같은 도구의 `--suite sampling`은 실제 CompletionRequest/ChatCompletionRequest/
BatchChatCompletionRequest/ResponsesRequest의 stop-string 개수, SamplingParams.n,
BeamSearchParams와 chat→beam 변환, stop-token/bad-word 중복 정규화 및 실제
WordLevel tokenizer를 통한 bad-word 개수 검사를 실행한다. 사례는 18개다.
프로세스 내부 VLLM_MAX_STOP_STRINGS/VLLM_MAX_N_SEQUENCES/VLLM_MAX_NUM_BAD_WORDS를
4로 고정하고 4/5를 대조한다. 이는 공식 작은 cap override 방식의 기술 fixture이며
기업 한도나 원래 배포 기본값 변경이 아니다. 실제 V2 runner의 1024/128 소비는
이 CPU admission 시험으로 확인하지 않는다. sampling-api-v1은 그대로 둔다.
명령은 위 도구에 `--suite sampling`을 더하고 새 workdir를 사용한다.

## REQ-09 serialized embedding 경계

`--suite embeddings`는 실제 `renderers/embed_utils.py::safe_load_prompt_embeds`를
실행한다. 직접 만든 CPU zero tensor를 메모리에서 torch.save한 뒤 base64로 전달한다.
외부 파일·임의 pickle 코드는 사용하지 않는다. 실제 로더는 weights_only=True로 읽는다.
정상 3×4 float32, 1×3×4 squeeze, hidden width 불일치, 1차원/4차원, integer/bool/
complex 거부, float64→model float32 변환, feature-disabled 거부의 10사례다.
각 사례 뒤 정상 로더 호출을 확인한다. 입력은 bytes 해시·크기만 기록한다.
이 지원 경로에서의 shape/dtype 거부와, 검사를 우회한 내부 EngineInput의 수용은
별도로 기록한다. 후자가 곧 공개 API 우회·실제 모델 오염의 증거는 아니다.

입력 묶음은 후속으로 26사례까지 확장했다. REQ-08은 실제 CompletionRequest의
build_tok_params를 거친 재사용 token의 left/right 2-token 절단을 확인한다.
REQ-07은 실제 ChatCompletionRequest.build_chat_params/build_tok_params를 거친
kwargs suffix 15/16-token 대조를 추가한다. 이 설정은 명시한 작은 시험 입력이며
기업의 재사용 token 허용·절단/override 승인 정책을 대신하지 않는다.

## 실제 OnlineRenderer core 검증 확장

`--suite online`은 실제 OnlineRenderer.render_chat→preprocess_chat→renderer와
InputProcessor 경로를 실행한다. 기존 PR46의 입력 기능 검증 공백이므로 같은 PR에
추가한다. request의 kv_transfer_params.prompt_token_ids 재사용을 정상/빈 list/bool/
음수/OOV/길이 초과로 비교하고 echo 시 재사용 키 제거·일반 렌더 경로를 확인한다.
실제 template 허용 gate와 trusted kwargs 길이, 기본 MM kwargs/code_revision 거부도
확인한다. opt-in의 text-only 수용은 실제 MM processor/코드 판본 소비를 의미하지 않는다.

Typed ErrorResponse의 native code400/422를 관측한 경우에만 해당 core의 반환 거부로
분류한다. 문자열 오류 본문은 저장하지 않는다. VLLMValidationError와 예상 밖 실행
오류는 구분하고 함수 진입·최종 admission 해시·재사용 키 제거 여부만 기록한다.
원문 수정·mock renderer/engine 없이 실제 생성자를 사용한다. 이것은 공개 HTTP routing,
engine-aware model/identity 검사 또는 최종 worker 소비의 증명이 아니다.
