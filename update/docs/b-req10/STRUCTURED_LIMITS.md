# REQ-10 구조화 출력 크기·깊이·복잡도 CPU 분기

PR43 조합 분기 소스/회귀/증거는 보존한다. 별도 B 브랜치에서 확장한다.
제품 등록·sampling override·공통 모델·판정기는 수정하지 않는다.
참조 vLLM: 5d35ce3934a7804b298559d2d99eec33792e1eb9.

`sampling_params.py:242 check_json_nesting`은 구현 상한 64, structural tag 추가
포장 여유 16을 검사한다. dict/list 계층을 반복 순회하고 문자열이면 JSON 파싱 후
검사한다. `backend_xgrammar.py:481 validate_xgrammar_grammar`는 깊이·지원 제약을
검사한 뒤 xgrammar Grammar 변환에 진입한다. `generate/base/protocol.py:245
validate_structural_tag_payload`는 요청 검증 중 깊이와 backend를 확인한다.
공식 `test_chat_error.py::test_deeply_nested_structural_tag_rejected`의 200중
array는 시험 입력이며 기업 깊이·자원 한도가 아니다.

15개 유한 사례: JSON dict/string 깊이 63/64/65, description 32/4096자,
anyOf 대안 2/16개, xgrammar 미지원 uniqueItems, response_format 및
structured_outputs의 정상/200중 array structural tag. 크기·복잡도 사례는 작은
관측 표본이다. 기업 한도·자원 예산 충족은 UNKNOWN이다.

실제 ChatCompletionRequest, SamplingParams, StructuredOutputsParams,
validate_xgrammar_grammar를 사용한다. sys.setprofile로 원문 수정 없이
nesting_check/backend_validator/json_conversion/tag_conversion 진입을 기록한다.
양성 대조에서 변환 진입을 확인해야 동일 동기 호출 안에서의 비진입을 해석한다.
이를 GPU·모델 제출·서비스 전체의 미도달로 확대하지 않는다. 각 사례 뒤 정상
backend 변환을 호출한다. 이는 parser/변환 후속 대조이며 추론·다른 요청 생존
대조가 아니다. grammar 변환은 tokenizer compiler/model 실행과 다르다.

project/judge_params는 기존 rule_table에 제공할 B 전용 자료다. 참조 금지 입력이
해당 경계에서 수용되면 독립 FAIL 후보를 남긴다. 후속 오류는 이를 지우지 않는다.
누락·미지원·기업 한도 부재는 UNKNOWN이며 보안 PASS 규칙은 없다.
입력 정규 해시·bytes·사례 ID만 기록하며 원문·예외 문자열은 저장하지 않는다.

재현(제품 lock과 분리한 기존 cpu-venv 재사용):

```sh
export VLLM_TARGET_DEVICE=cpu CUDA_VISIBLE_DEVICES= VLLM_PLUGINS=
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export PYTHONPATH="$VLLM_SOURCE:$BYUL_REPO/update/src"
"$CPU_VENV/bin/python" "$BYUL_REPO/update/tools/req10_structured_limits.py" --source-root "$VLLM_SOURCE" --output "$NEW_EVIDENCE_JSON"
```

설치판 미정은 참조 CPU 시험을 막지 않는다. A에 필요한 연결은 기존 인계에 이
전용 관측/판정 자료와 실행 도구 선택을 추가하는 최소 범위다. 실제 대상 시험에는
설치판/backend·엔진 예산·소유자·계측·중단/복구 권한을 고정해야 한다. 실제 compile,
모델 제출·타 요청 영향 계측이 없으므로 실제 실행만 남았다고 보고하지 않는다.

## 실행 결과

2026-10-28 실제 CPU 15사례와 각 후속 정상 변환 15건을 완료했다(4.24초).
깊이 63/64는 변환 진입 후 수용, 65는 중첩 검사에서 거부되어 이 동기 호출의
json_conversion에 진입하지 않았다. 두 structural tag 정상 대조는 tag_conversion에
진입했고 200중 array는 nesting_check 뒤 backend_validator/tag_conversion 진입 없이
거부됐다. uniqueItems는 미지원으로 변환 전에 거부됐다. 작은 크기/대안 표본은
변환·수용됐지만 자원/기업 한도 충족은 UNKNOWN이다. 15건 후속 변환은 모두 수용됐다.

공식 깊이 시험의 원문 AST 노드와 원래 parametrize를 격리 pytest 모듈에서 실행했다.
MODEL_NAME만 로컬 fixture-model로 지정하고 실제 클래스·예외를 사용했다. 원문
전체 suite나 엔진 conftest 실행과 구분한다. 2 passed, pytest 4.21초/부모 5.54초.
직접 대역·기존 sampling/tool 회귀는 93 passed, pytest 0.93초/부모 1.06초.
기록은 저장소 형제 폴더의 structured-limits-cpu-1.json, limits-official-1.txt,
limits-contracts-1.txt, limits-snapshot-1.json에 있다. 참조 CPU에서 확인된 위반은
없고 전체 REQ-10 보안 판정은 여전히 UNKNOWN이다.

## 0.31.0 source CPU 적용 관측 — 2026-10-29

기존 15개 corpus의 source별 적용을 확인했다. 입력·크기·개수를 추가하지 않았으며
새 15개 시험 개발로 집계하지 않는다. 제품 `structured_limits.py`와 기존 default
reference 기대값·함수 계약은 유지한다. `tools/req10_structured_limits.py`에 명시적
`--source-profile reference|vllm-031` 선택을 추가했고 기본값은 reference다.

0.31 source는 `db9527a46873454610df6dbedf79a36d6bf1a7f6`이다. 실제 모듈
`vllm.sampling_params`에서 check_json_nesting, MAX_STRUCTURED_OUTPUT_JSON_NESTING,
STRUCTURAL_TAG_WRAPPER_NESTING 세 심볼의 부재를 확인했다. 결과 source_features는
해당 모듈·심볼을 inspected_selected_source/present=false로 기록해 미수집과 구분한다.
이 확인의 범위는 해당 세 심볼이며, engine/backend/library 전체의 깊이 제한은 별도다.

0.31 depth/tag 입력의 기대값은 null, rule은 target031_depth_observation으로 기록한다.
기존 5d의 깊이 64 기대값을 이월하지 않고 target 전용 projection을 사용한다.
크기·복잡도 입력도 관측으로 보존하며 기업 크기·깊이·복잡도 정책은 UNSET이다.
uniqueItems 사례만 0.31 backend_xgrammar.py의
has_xgrammar_unsupported_json_features에 명시된 array/uniqueItems 지원 규칙을
expected=rejected와 함께 정확한 source commit·파일·심볼로 연결한다.

실행 경계는 기존 own-checkout/guard bytes gate, 전체 source git archive bytes 검사,
실제 import·함수 source 경로 검사, Python 3.12/CPU 확인, 최적화 모드 거부를 사용한다.
0.31은 기존 전용 source profile의 torch 2.13.0/xgrammar 0.2.7/transformers 범위 검사도
적용한다. file bytes·의존성 metadata를 사례 실행 전에 수집하고 실행 뒤 source를
다시 확인한다. 원문 입력·예외 문자열을 저장하지 않는다. 등록·CLI·팩·공통 계약은
변경하지 않았다.

직접·인접 회귀 **51 passed / pytest 2.17초**. depth65 수용이 reference에서는 원래
위반 후보지만 target에서는 기대 미확정 관측인 점, uniqueItems 독립 위반 보존,
fixture identity, 기본 프로파일과 -O/-OO 사전 거부를 검증했다.

실제 0.31 결과 `/private/tmp/byul-req10-limits031-20261029-1.json`:

- 기존 JSON 깊이 63/64/65의 dict/string 6건은 backend·JSON 변환에 진입한 뒤 수용.
- description 32/4096와 alternatives 2/16도 JSON 변환에 진입한 뒤 수용.
- response_format/structured_outputs의 tag 깊이 1/201 4건은 요청 validator,
  tag payload validator, backend, tag 변환에 진입한 뒤 수용.
- uniqueItems는 backend에서 JSON 변환 진입 전에 거부. 각 사례 뒤 정상 JSON 변환
  **15건 모두 수용**. target 기대값으로 확인된 위반 후보는 0이며 보안 PASS 규칙은 없다.
- Python 3.12.13, torch 2.13.0, pydantic 2.14.1, xgrammar 0.2.7.
  부모 벽시계 **5.71초**, 기업 정책 UNSET·installed_target=null·runner=false 유지.

기본 reference도 별도 환경에서 실제 smoke를 수행해 기존 15행 기대값과 후속 15건을
확인했다. 결과 `/private/tmp/byul-req10-limits-reference-smoke-20261029-1.json`,
source 5d35ce3, torch 2.15.0/xgrammar 0.2.8, 부모 **5.81초**다. 두 기록은 각 source와
의존성 조합의 직접 CPU 관측이다. 설치 이미지와 조건이 고정된 업데이트 비교 쌍은
별도로 준비한다. 실제 모델·엔진·GPU·원격 계측은 실행하지 않았다.

측정 도구 bytes SHA256:
`bc2d8a545284ac6001b8855cc6e3a196d08e93624dadfe9c4bc8ca8cc5c39176`.

```sh
VLLM_TARGET_DEVICE=cpu CUDA_VISIBLE_DEVICES= VLLM_PLUGINS= HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH="src:.:$VLLM_SOURCE" "$CPU_VENV/bin/python" tools/req10_structured_limits.py --source-profile vllm-031 --source-root "$VLLM_SOURCE" --output "$NEW_EVIDENCE_JSON"
```
