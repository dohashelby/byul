# B-1 입력 CPU 분기

## 원 PACK31 REQ-09의 target031 부분 증거 연결

기존 Online 3점검 proposal 경로와 별도로 `b1_input_integration_cpu.py --check REQ-09`
모드를 제공한다. 원 PACK31 REQ-09 revision 8·`input_validation`·필수 정책 4개·구조화 관측
10개·원 질문/설명/챌린지를 그대로 사용한다. 새 팩·실행기 등록·정책 기본값을 만들지 않는다.

```text
python -B tools/b1_input_integration_cpu.py \
  --check REQ-09 --source-profile target031 --source-root EXACT_TARGET031_SOURCE \
  --output-root FRESH_OUTPUT --source-store ORIGINAL_STORE --plan EXISTING_PLAN_ID \
  --pack ORIGINAL_PACK31 --entered-by DECLARED_OPERATOR
```

이 명령은 명시한 CPU 152 의존 환경에서 실행한다. 제품 의존성이나 기본 guard CLI에
torch를 추가하지 않는다. source ENV는 `source-db9527a46873454610df6dbedf79a36d6bf1a7f6`,
image/model 필드는 null이어야 하며 원 Store에서 PLAN의 정확 ENV/POL/PACK Ref를 확인한다.
기존 정책 4개가 없거나 기존 입력 정책 프로파일과 맞지 않으면 수집 전에 닫는다.
단위 시험의 승인 bytes 대역은 EXAMPLE ENV로만 한정한다.

기존 26개 input fixture 중 REQ-09의 mask 5개·shape 2개·offset 5개와 각각의 후속 정상
admission만 한 번 수집한다. source/producer/config/fixture 귀속과 행별 전후 검증,
실제 `process_inputs` 진입, 입력/수용 metadata hash 대응을 확인한 행에만 기존
`input_cpu.project`를 적용한다. mask/offset이 유효값으로 정규화되면 그 행을 위반으로
투영하지 않는다. shape의 `expected=None`은 관측만 한다. 후속 정상은 `hello world`의
2-token source CPU admission이며 HTTP·모델 처리 성공을 뜻하지 않는다.

원 Store는 읽기 전용 bundle로 새 output/store에 복원한 뒤 기존 manual v3 override로
REQ-09 한 항목만 추가한다. 전체 normal control은 `not_attempted`, 원 token/길이 챌린지
2개는 false다. `confirmed_input_violations` 외의 9개 관측은 null/미수집이며 endpoint나
입력 형식의 제출·소비를 창작하지 않는다. 확인된 독립 mask/offset 위반만 신고 security
FAIL이고 나머지는 UNKNOWN이다. 기능·성능·runner 소비·전체 질문은 UNKNOWN으로 남긴다.
수동 EVD는 기존 계약대로 DECLARED이며 기업 승인 진위나 실행 환경 인증을 생성하지 않는다.

정제 첨부에는 source/fixture/config와 count/shape/dtype/hash/offset·관측 enum만 남긴다.
실제 배열·tensor 본문·예외 본문은 저장하지 않는다. 첨부 쓰기나 독립적인 후처리 실패가
이미 확인한 FAIL을 지우지 않는다. 같은 as-of의 judge/report·verify·export/restore와
정확 Ref를 대조하고 원 Store bytes를 보존한다. 원래 3개 gate/component/collector pin은
이 모드 추가로 갱신하지 않으며 root의 별도 고정 소스 승인 후 실제 실행한다.

전용 회귀는 `tests/test_req09_input_integration_cpu.py`다. 기존 Online·다른 입력/샘플링
실측을 반복하거나 합산하지 않는다. 실제 CPU 수집은 collector와 bridge 통합 후 별도로
한 번 수행하며 단위 EXAMPLE 결과를 실제 수집으로 표시하지 않는다.

이 PR은 REQ-10과 REQ-08/09/07 입력 검증 및 관련 SUP-08 요청 gate를 다룬다.
전체 공동 작업 목록은 B 38개이며 다음 묶음은 저장소 밖 byul-b-coordination의
상대 선점 기록을 확인해 선택한다. SUP·캐시·배포를 전체 배정에서 제외하지 않는다.
B-2가 선점한 응답/로그/저장·해석기 파일은 수정하지 않고 별도 worktree/branch를 쓴다. PR43 head
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
export PYTHONPATH="$BYUL_B1_REPO/update/src:$BYUL_B1_REPO/update:$VLLM_SOURCE"
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


## OnlineRenderer target031 연결 준비 (2026-10-29)

`--suite online --source-profile target031`은 선정 db9527a46873454610df6dbedf79a36d6bf1a7f6의
실제 OnlineRenderer와 InputProcessor를 사용한다. 기본 `reference5d`와 기존 입력·sampling·
embedding suite, GPT2/WordLevel context builder의 코드는 유지한다. 기존 Online16 입력과
각 사례의 후속 정상 대조16을 재사용하며 새16시험 개발로 집계하지 않는다.

실제 0.31 정상 1건 feasibility에서 render_chat·preprocess_chat·reuse helper와
InputProcessor 진입, prompt_count3을 확인했다(9.40초). 이 준비 관측은 정식 제품 증거에
집계하지 않는다. `_C`·Triton 부재 경고에도 선택한 CPU admission은 완료됐다.
정식 capture는 vLLM import 전에 VLLM_CACHE_ROOT와 HF cache를 새 소유 workdir 아래로
설정한다. 가중치·모델 연산·네트워크·서비스·GPU는 사용하지 않는다.

0.31에는 BaseRenderer.validate_token_ids가 없고 InputProcessor._validate_model_input의
음수·OOV 검사와 _validate_prompt_len을 직접 추적한다. 함수 부재는 source_features에
present=false로 남긴다. 0.31 reuse helper는 pop 뒤 빈 값에 fallback하며 5d의 bool·echo
검사를 포함하지 않는다. 따라서 target의 reuse-empty/reuse-bool/reuse-echo-ignores-ids는
원본 expected/hash/check_id/stage를 보존하고 별도 source_scope_eligibility를 관측 전용으로
둔다. 나머지 필수 검증은 파싱된 필드 해시·실제 적용 fixture 설정·gate trace와 실제
processor 입력·반환 해시를 통해 귀속한다. typed400 또는 빈 trace만으로 소비를 확정하지 않는다.

capture_online() 한 번의 수집 ID·UTC 시작/종료 시각과16행을 REQ07·08·SUP08이 공유한다.
기업 한도는 설정하지 않고 작은 max_model_len16/vocab32를 DECLARED fixture로 유지한다.
사례별 생성자·실행·후속 관측 및 보조 저장 실패 시 이미 검증된 다른 행과 독립 위반은
보존하고 incomplete를 반환한다. 명시 source 불일치행의 판정 자격과 정제 관측은 분리한다.

```sh
# update/ cwd, 부모 normal Python
PYTHONPATH="src:.:$VLLM_SOURCE" "$CPU_VENV/bin/python" -m tools.b1_input_cpu \
  --suite online --source-profile target031 --source-root "$VLLM_SOURCE" \
  --workdir "$FRESH_ONLINE_CPU_EVIDENCE"
```

정식 target16 수집 결과는 collector·producer 통합 소스 확정 후 기록한다. 기존 참조5d16행·
후속16 원본은 유지한다. installed target은 null이고 최종 runner 소비는 미검증이다.


### 독립 token ID domain 근거

검증 대상 캐시의 max_token_id를 허용 범위의 oracle로 사용하지 않는다. 소유
`tokenizer.json`의 model.vocab·added_tokens ID 합집합에서 count/min/max·정규 해시와
0부터의 연속 여부를 별도 수집한다. `config.json`의 vocab_size는 실제 ModelConfig
값과 대조하고, 실제 InputProcessor에 전달하는 ID의 tokenizer/model membership과
정규 해시를 기록한다. 두 설정 파일 bytes SHA를 capture의 config hash와 연결한다.
문자열 vocabulary와 ID 집합 전체는 제품 첨부에 저장하지 않는다.

0.31 캐시가 보고한32는 기존 effective_max_token_id 진단값으로 보존한다. 독립
vocabulary/model domain0..31 밖의 ID32가 실제 processor에서 수용되었다면 기존
REQ08 FAIL을 보존한다. 독립 domain 근거가 없거나 설정 hash가 다르면 해당 자격은
UNKNOWN이다. 원본16입력·expected·기존 actual1은 유지한다.
