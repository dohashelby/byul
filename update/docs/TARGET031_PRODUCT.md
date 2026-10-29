# REQ-10 vLLM 0.31.0 CPU producer 연결

`structured_tools_cpu.py`에 고정 target 소스 전용 `capture_target031`과
`StructuredToolsTarget031CPUExecutor`를 추가했다. 기본 `capture_reference`,
`StructuredToolsCPUExecutor`, SOURCE_COMMIT 5d35ce3 및 과거 Ref는 유지한다.

| 프로파일 | 고정 소스 | 직접 호출 범위 |
| --- | --- | --- |
| reference 기본 | 5d35ce3934a7804b298559d2d99eec33792e1eb9 | 기존 5조합 |
| vllm-031 명시 선택 | db9527a46873454610df6dbedf79a36d6bf1a7f6 | 5조합과 각 사례 뒤 정상 변환 5건 |

공통 내부 capture는 명시한 두 commit/profile 조합만 받는다. HEAD 자동 선택은 없다.
기존 git archive bytes·tracked/ignored 파일 검증과 실제 import·함수 경로 검사에
expected_commit을 전달한다. vLLM 원문 소스는 수정하지 않는다.

0.31 경로는 실행 전 xgrammar==0.2.7, torch==2.13.0,
transformers>=5.10.4,<5.18.0을 확인한다. 버전 판정에는 해당 CPU 의존성 환경의
packaging을 사용한다. 제품 pyproject.toml/uv.lock에는 의존성을 추가하지 않는다.
실제 target cpu.txt 전체 설치·freeze·uv pip check 결과는 저장소 밖에 보존한다.

전용 executor는 입력 model과 ENV.model.id, ENV.engine.version의
`reference-db9527a46873454610df6dbedf79a36d6bf1a7f6` 일치 및 ENV 해시를
수집 전에 검증한다. source가 다른 ENV에는 관측을 귀속하지 않는다.

정상 4조합과 후속 정상 변환 5건을 controls/planned에 기록한다. 후속 변환 누락·실패는
기능 PASS를 차단한다. 금지 named-json의 요청 클래스 수용은 후속 오류와 독립적으로
위반 관측을 유지한다. 로컬 첨부 실패도 기존 incomplete_artifact_capture로 facts를
보존한다. 실제 request→sampling→xgrammar validator/JSON 변환 진입은 row별로 저장하며
원문 요청·예외 문자열은 기록하지 않는다.

출처는 `target031_source_cpu_request_sampling_xgrammar_conversion`이며,
installed_target=null, server_preprocessing_verified=false,
runner_consumption_verified=false를 유지한다. origin은 DECLARED 상한이며
EXAMPLE ENV는 EXAMPLE을 유지한다. 설치 이미지·모델 생성·tokenizer grammar compiler·
runner·서비스 전체 생존은 이 직접 CPU 호출 범위 밖이다.

두 도구는 `--source-profile reference|vllm-031`을 받으며 기본값은 reference다.
기존 자기 checkout/두 소스 bytes 고정 gate를 유지한다. 새 producer bytes는 주세션이
정식 overlay pin에 반영한 뒤 실행한다.

```sh
VLLM_TARGET_DEVICE=cpu CUDA_VISIBLE_DEVICES= VLLM_PLUGINS= HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH="$TARGET_SOURCE:src:." "$TARGET_CPU/bin/python" tools/req10_tools_cpu.py --source-profile vllm-031 --source-root "$TARGET_SOURCE" --output "$NEW_JSON"
VLLM_TARGET_DEVICE=cpu CUDA_VISIBLE_DEVICES= VLLM_PLUGINS= HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH="$TARGET_SOURCE:src:." "$TARGET_CPU/bin/python" tools/req10_tools_cpu_pipeline.py --source-profile vllm-031 --source-root "$TARGET_SOURCE" --output-root "$NEW_DIRECTORY"
```

A 연결 제안은 기존 executor 계약을 그대로 사용한다. 전용 이름
`structured_tools_target031_cpu`를 명시적으로 등록하고 cfg의 source_root/model을
전달한다. source용 ENV와 전용 CheckDef/팩을 선택한다. 관측 키 5개와 기존
structured_tools.judge_params를 사용한다. pipeline 도구는 별도
PACK-B-REQ10-031-CPU-PROPOSAL/DRAFT와 process-local 등록으로 기존 core
EvidenceDraft→EVD→RES→RPT를 검증한다. 공통 registry/CLI/팩은 변경하지 않는다.

## 검증 상태

코드 묶음 직접·인접·기존 source pin/metadata 회귀: 138 passed, pytest 4.87초.
기존 5d pin/metadata 계약과 0.31 ENV·의존성·후속 변환·독립 FAIL·첨부 실패 분기를
포함한다. 정식 overlay pin 인계 후 실제 도구 CPU 실행을 진행한다.
