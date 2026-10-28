# REQ-10 structured-tools-v1 (B)

기준 main: 4af4d31bc951bc4a6e8bfefe4381e63e5305075c, PACK29/DRAFT.
공식 시험/요청 클래스 기준: vllm-project/vllm commit
5d35ce3934a7804b298559d2d99eec33792e1eb9.
이는 native 센서 참조 db9527a46873454610df6dbedf79a36d6bf1a7f6 및 미정 설치판과 다르다.

공식 `tests/tool_use/test_chat_completion_request_validations.py`의
`test_structured_outputs_with_named_tool_choice_rejected`와
`test_structured_outputs_with_auto_tool_choice_allowed`, 그리고
`vllm/entrypoints/openai/chat_completion/protocol.py:931–971`
`ChatCompletionRequest.check_structured_outputs_count`가 근거다.
JSON/regex/choice 중 둘 이상은 거부한다. JSON과 tool 선택 none/auto/required는 이
검사를 통과하고 named 선택은 거부한다. 이는 backend 지원이나 생성 성공 보장이 아니다.
`check_tool_usage:974`는 tools를 주고 선택을 생략하면 auto로 정규화하며
선택한 named 함수가 tools 목록에 있는지도 확인한다.

이번 입력은 같은 정상 schema/tool로 none/auto/required, named-only 정상 대조와
named-json 금지 조합을 구성한다. `max_tokens=2`는 시험 예산이며 기업 한도가 아니다.
response_format, 다중 제약, 크기·깊이·복잡도는 이 묶음에서 시험하지 않는다.

`structured_tools.py`는 기존 HttpCheckExecutor·전송·EvidenceDraft를 재사용한다.
등록·CLI·팩·기존 override는 수정하지 않았다. 현재 제품 자동 연결은 대기다.
HTTP 200/400은 API 관측이다. 거부 위치·후속 변환 미도달은 증명하지 않는다.
`validate_request_class`는 실제 클래스/정확한 거부 예외를 전달받아 CPU에서 호출할
독립 진입점이다. 회귀의 fake_class는 EXAMPLE 계약 대역이며 실제 vLLM이 아니다.
2026-10-28 후속 작업에서 제품 lock과 분리한 Python 3.12 격리 환경을 준비해 실제
참조 클래스 CPU 검증을 수행했다. B 전체의 환경 준비 기준은
[CPU 실행 원칙](../b-work/CPU_EXECUTION.md)을 따른다.
`project_validator`는 금지 조합의 실제 boundary 수용을 독립 위반으로 투영한다.
`judge_params`는 기존 rule_table 형식이며 보안 PASS 규칙은 없다. 확인된 FAIL은
다른 정상/누락 관측과 독립적이다. 첨부 저장 중 증거 보존은 A 공통 보수 인계가 필요하다.
runner_consumption_verified는 항상 false다. 기존 sampling의 의미와 과거 Ref는 유지한다.

실제 CPU 검증 절차: 정확한 참조 소스 SHA, Python 3.12 환경과 의존성 명세,
ChatCompletionRequest 및 VLLMValidationError 실제 import를 확인한다. 그 환경에서
`validate_request_class(ChatCompletionRequest, VLLMValidationError, model=...)`를 호출하고
설치판·소스 파일 bytes SHA·결과·CPU 벽시계를 저장소 밖 기록한다. 설치 대상 미정 상태에서
참조 소스 CPU 결과나 대역 통과를 설치판 실측으로 보고하지 않는다.

## A에게 전달할 최소 연결 요청

- 공동 검증 SHA/head/base·정확 PACK Ref·남은 실패·sampling_validation 소유권 인계.
- 이 전용 클래스를 명시 선택하는 등록과 CLI 설정. sampling executor를 교체하지 않는다.
- 이 모듈이 반환하는 observations를 해당 분기 CheckDef의 required_observations로
  선언하고 judge_params를 rule_table에 연결하는 전용 override/팩 제안.
  기존 REQ-10 sampling 관측을 이 분기 결과로 덮어쓰지 않는다.
- 사용처: HTTP 결과, controls/planned, validator_violation, runner_consumption_verified.
  미수집 값은 null/collected=false, 지원 미확인은 요청 0건·UNKNOWN이다.
- request-class 실제 결과는 HTTP 결과와 합성하지 않는다. 별도 EVD/실행 Ref와
  정확한 출처로 수집한 뒤 투영한다. 원문/예외 문자열은 저장하지 않는다.
- 독립 FAIL 후 첨부 실패 보존 계약은 A의 공통 보수로 확인한다.

## 실제 엔진 실행 준비와 남은 조건

아직 실제 실행 승인이나 소유 대상이 없으므로 Docker/pull/모델 다운로드를 수행하지 않는다.
다음이 준비되어야 실행할 수 있다: 소유자·설치판/image digest·model·chat template·
endpoint·인증 전달 경로·backend·tool parser·auto tool 활성·정상 생성 가능성·명시 실행 승인.
A 등록 후 정확 소스에서 `guard run --help`로 해당 설정 경계를 확인하고 PLAN을 만든다.
지금은 등록 전이므로 실행 가능한 제품 CLI 명령이 있다고 주장하지 않는다.

승인된 대상에서는 기존 전송에 `StructuredToolsExecutor`를 전달한다.
설정은 source_commit(위 참조), supported=true(실제 기능 확인 후), backend(선정 값),
target(소유 target.json), request_timeout=3이다. existing HttpCheckExecutor.run(check,
env, {}, adapter, workdir)을 사용하며 check의 전용 분기 관측/판정은 A가 확정한다.
정상 before → none/auto/required/named-only → named-json → 정상 after, 총 7건이다.
backend grammar 변환/컴파일·모델 제출 미도달·runner 소비는 별도 실제 계측이 필요하다.
병행 타 요청 생존과 전체 서비스 보호 관측은 아직 준비되지 않았다.

보존: PLAN/ENV/POL/실행 Ref, EVD/RES/RPT와 정제된 첨부를 append-only 저장하고
원본·파일 bytes SHA/정규 객체 해시를 구분한다. 요청/응답 원문·토큰·키는 반출하지 않는다.
OOM/재시작/상태 오염이면 후속 요청을 중단하고 사례별 영향 범위를 분리한다.
운영/공유 workload는 변경하지 않는다. 승인된 소유 프로브만 기존 lifecycle로 정리하며
엔진 중단/복구는 소유자 절차와 명시 권한이 추가로 필요하다. 현재 그 정보도 미정이다.
REQ-10 전체 또는 실제 엔진 준비 완료로 집계하지 않는다.

준비된 실제 CPU 환경의 명령(소스는 읽기 전용):

```sh
python update/tools/req10_request_class.py --source-root "$VLLM_SOURCE" --model "$TEST_MODEL" --output "$NEW_EVIDENCE_JSON"
```

실행 환경에 이번 B 소스의 guard가 설치되어 있어야 한다. 도구는 고정 git HEAD와
vllm 경로의 수정 유무, 실제 import 파일 경로를 확인하고 파일 bytes SHA를 기록한다.
새 출력 파일만 허용하고 import/실행 실패의 원문은 저장하지 않는다.
이 도구의 실행 성공도 전체 클래스 밖의 grammar/backend 경로를 확인하지 않는다.

2026-10-28 후속 실제 CPU 결과와 재현 명령은 [CPU 참조 검증](CPU_REFERENCE.md)에 있다.
참조 클래스 정상 4건 수용·금지 1건 거부, 원문 공식 시험 9개 통과를 확인했다.
