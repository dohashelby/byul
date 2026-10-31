# REQ-10 실제 validator·xgrammar 관측 연결 제안

B 전용 producer는 `structured_tools_cpu.StructuredToolsCPUExecutor`이며 기존
`run(check, env, policy_values, adapter, workdir) -> EvidenceDraft` 계약을 따른다.
A가 정식 등록·CLI 설정·전용 CheckDef/팩을 연결한다. 기존 numeric sampling을 유지한다.

`source_root`, `model`만 받으며 관측 JSON을 입력받지 않는다. 실행마다 참조 git HEAD
`5d35ce3934a7804b298559d2d99eec33792e1eb9`, vllm 경로 clean 상태,
실제 import 및 함수 source 경로, Python 3.12·CPU torch를 확인한다.
실행 뒤 git 상태도 다시 확인하고 실제 파일 bytes SHA·의존성 버전을 첨부한다.
0.31.0 설치 대상은 별도 신원이며 `installed_target=null`로 보존한다.

고정 schema/tools 5조합을 실제 요청 클래스 → `to_sampling_params` →
`validate_xgrammar_grammar`로 호출한다. 기존 `observe_call`로 요청 validator,
sampling 변환, backend validator, xgrammar JSON 변환 진입을 관측한다.
none/auto/required는 JSON 변환을 정상 대조로 사용하고 named-only는 제약 없이
backend validator까지 도달하는 대조다. named-json 수용은 후속 변환 오류와
독립적인 위반 관측으로 남긴다. 요청·응답 원문과 예외 문자열은 저장하지 않는다.

측정 범위는 이 동기 CPU 호출이다. 서버 tool parser 전처리·tokenizer compiler·
모델 제출·runner 소비·서비스 생존은 별도 관측을 요구한다.
`runner_consumption_verified=false`, `server_preprocessing_verified=false`를 유지한다.
정제된 첨부와 observations는 같은 live 호출에서 생성한다.

A 최소 연결 제안:

- 전용 executor 이름 `structured_tools_cpu`를 선택 등록하고 source_root/model 설정을 전달한다.
- dedicated reference CPU ENV/PLAN과 CheckDef의 관측 키를 확정한다. 현재 draft origin은
  DECLARED 상한이며 EXAMPLE ENV는 EXAMPLE을 유지한다. reference 실행을 배포 실측으로
  올리지 않는다. 참조 CPU 전용 ENV에서의 최종 origin은 A가 기존 계약으로 확정한다.
- 기존 `structured_tools.judge_params()` rule_table과 연결한다. controls_planned는
  이 CPU 프로파일의 4개 정상 사례이며 HTTP 프로파일의 before/after와 섞지 않는다.
- `structured_tools_cpu_rows`, validator_violation, controls/planned, runner flag를
  EVD에 보존한다. 기존 core의 정식 EVD→RES→RPT 흐름을 사용한다.
- 독립 위반 관측과 첨부 실패 보존은 현 공통 core 계약에 맡긴다. CPU import 불가면
  ABORTED/관측 없음이며 기존 원본 실패를 성공으로 바꾸지 않는다.

직접 수집 도구는 `tools/req10_tools_cpu.py --source-root ... --output 새파일`이다.
직접 도구/EXAMPLE 계약 시험 통과와 정식 제품 CLI 연결 완료는 각각 기록한다.

## 참조와 0.31.0 대상의 차이

참조 git describe는 `v0.33.0rc0-41-g5d35ce393`다. 0.31.0 대상
`db9527a46873454610df6dbedf79a36d6bf1a7f6`와 요청 validator
`check_structured_outputs_count`, `check_tool_usage`의 AST는 동일하다.
`to_sampling_params`, `validate_xgrammar_grammar`의 AST는 다르다.
따라서 위 요청 경계의 동일성 확인과 대상 backend의 실측 검증을 구분한다.
이번 producer의 pin은 기존 참조 5d35ce3를 유지한다.

로컬 첨부 생성 실패에도 이미 수집된 observations를 반환한다. 공통
`incomplete_artifact_capture`를 재사용해 ABORTED/ARTIFACT_CAPTURE_INCOMPLETE로
기록하며 독립 위반 관측을 보존한다.

재현용 `tools/req10_tools_cpu_pipeline.py`는 저장소 밖 새 output-root에 전용
DRAFT proposal pack과 DECLARED reference ENV/POL을 생성하고 해당 프로세스에서만
executor를 연결한다. 기존 core select/run/judge/report로 실제 CPU 호출의
5개 수집 관측·정제 첨부 1개를 EVD→RES→RPT에 보존한다. 제품 영구 registry/CLI
연결은 A 인계 항목으로 남긴다. 실제 배포·runner 소비 완료로 집계하지 않는다.

## 2026-10-29 검증 기록

- 직접·인접·전체 불변식: 223 passed, 1 deselected. pytest 1.66초,
  부모 실행 도구 벽시계 1.87초. sandbox 소켓 bind 제한은 승인 후 재실행해 통과했다.
- 실제 Python 3.12.13, torch 2.15.0(macOS CPU), xgrammar 0.2.8,
  pydantic 2.14.1. 기존 제품 lock은 유지했다. 첫 import는 openai_harmony 누락으로
  실패했고 공식 common.txt 의존성을 격리 환경에 설치한 뒤 성공했다.
- 실제 5조합: none/auto/required는 request/sampling/backend/json_conversion 진입 후 수용.
  named-only는 schema 없음/backend 진입 후 수용. named-json은 request_validator 진입 후
  거부했으며 같은 호출의 sampling/backend/json_conversion 진입을 관측하지 않았다.
- 최종 소스 실제 core 연결: COMPLETED/DECLARED, 수집 관측 5개, 첨부 1개,
  보안 UNKNOWN·기능 PASS·runner=false. CPU pipeline 부모 벽시계 5.30초.
  별도 DRAFT pack·process-local 등록 제안으로 정식 객체 저장과 Ref 연결을 검증했다.
- 결과 폴더 `/private/tmp/byul-validator-core-20261029-2/`에 ENV/POL/PLAN/EVD/RES/RPT
  JSON, proposal-pack 원본, store와 summary.json을 보존했다. producer bytes SHA는
  실제 관측 첨부에 기록된다. 직접 관측은 `/private/tmp/byul-validator-actual-20261029-2.json`에 있다.
