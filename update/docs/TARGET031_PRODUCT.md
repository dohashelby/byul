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
VLLM_TARGET_DEVICE=cpu CUDA_VISIBLE_DEVICES= VLLM_PLUGINS= HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH="src:.:$TARGET_SOURCE" "$TARGET_CPU/bin/python" tools/req10_tools_cpu.py --source-profile vllm-031 --source-root "$TARGET_SOURCE" --output "$NEW_JSON"
VLLM_TARGET_DEVICE=cpu CUDA_VISIBLE_DEVICES= VLLM_PLUGINS= HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH="src:.:$TARGET_SOURCE" "$TARGET_CPU/bin/python" tools/req10_tools_cpu_pipeline.py --source-profile vllm-031 --source-root "$TARGET_SOURCE" --output-root "$NEW_DIRECTORY"
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
포함한다. 정식 overlay pin은 producer bytes
`cc3a855716218daffbd64457d619770eea70f40da5b209c23475a20b90144f4b`로
고정한 뒤 실제 도구를 실행했다.

2026-10-29 실제 target CPU 도구 2개를 완료했다.

- direct: `/private/tmp/byul-target031-product-direct-20261029-1.json`.
- pipeline: `/private/tmp/byul-target031-product-core-20261029-1/`.
  COMPLETED/DECLARED, 수집 관측 5개·정제 첨부 1개, 부모 벽시계 7.14초.
- none/auto/required는 요청·sampling·backend·JSON 변환에 진입한 뒤 수용됐다.
  named-only는 schema 없이 backend에 진입한 뒤 수용됐다. named-json은 요청
  validator에서 거부됐고 같은 동기 호출의 sampling/backend/JSON 변환 진입은 없었다.
- 각 사례 뒤 정상 auto/schema 변환 5건은 모두 수용·JSON 변환 진입했다.
  controls 9개 모두 pass, security UNKNOWN·functional PASS·runner=false를 유지한다.
- `store.verify()` 오류 0, 보존한 exact PACK/EVD Ref로 재판정한 RES Ref 동일,
  RPT inputs_manifest의 exact EVD·RES Ref 포함을 확인했다. `postverify-1.json`에 저장했다.
- PACK: `PACK-B-REQ10-031-CPU-PROPOSAL` revision 1,
  SHA256 `27da82f96dd8b5d5e4f9ccc7efb45a8be9c9a9d118294fddf9f1a7617745f7d1`.
- EVD: `EVD-01M631YV2BYFDZM3CTPVFPNVS2` revision 1,
  SHA256 `efaf3defe320300b9beab71038b866bbcc0974dd59bdb7bbfdbbfb3ecbd64344`.

정확 target 의존성 환경은 Python 3.12.13, torch 2.13.0, xgrammar 0.2.7,
transformers 5.17.0, pydantic 2.14.1이다. 최초 원문 CPU 설치 148개 freeze는
`/private/tmp/byul-target031-exact-evidence-20261029/dependencies.freeze.txt`에 유지했다.
기존 b1 도구가 사용하는 개발용 pytest를 추가한 뒤 151개 snapshot은
`/private/tmp/byul-target031-product-dependencies-20261029/with-pytest.freeze.txt`에
별도로 보존했다. 추가 후 uv pip check도 전체 151개 호환을 확인했다.

첫 직접 도구 실행은 PYTHONPATH에서 target source의 tools 패키지가 local 도구보다
먼저 선택되어 import에 실패했다. local `src:.`을 먼저 둔 재현 명령으로 수정했다.
다음 실행에서는 개발용 pytest가 없어 중단됐고, 격리 환경에 이를 추가한 뒤 성공했다.
두 사전 실패에서는 출력 증거를 생성하지 않았다. vLLM 원문이나 소스 검증 gate는
변경하지 않았다. import 중 host FFmpeg/opencv AVF class 중복 경고를 관측했지만
직접 5조합·후속 변환과 core 연결은 모두 종료코드 0으로 완료했다.

같은 frozen producer의 기본 reference 경로도 별도 기존 5d CPU 환경에서 실제 실행했다.
`/private/tmp/byul-reference-product-smoke-20261029-1.json`에 5d identity·기존 5행,
정상 4/금지 1·기존 controls 4개와 후속 필드 없는 기존 형식을 확인했다.
두 vLLM source checkout은 전후 clean을 유지했다.
