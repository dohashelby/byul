# OBS-03 선정 Pod stdout 최소수집 관측

원 PACK31 OBS-03 revision 5의 질문·정책 5개·typed 관측 9개·챌린지 3개·관측 설명
1개를 유지한다. 새 모듈은 고정 collector가 돌려준 정제된 관측을 기존 manual v3
override에 연결한다. CLI 등록·임시 팩·판정 기준·새 기업 정책은 추가하지 않는다.

## 입력과 명령

설치한 제품의 Python 3.12에서 다음 명령으로 입력을 먼저 확인한다. 기본 실행은
collector를 import하지 않고 Kubernetes/API 요청이나 출력 경로 생성을 수행하지 않는다.

```text
python -m guard.executors.log_minimization_evidence \
  --store ORIGINAL_STORE --plan EXISTING_PLAN_ID --pack ORIGINAL_PACK31 \
  --target EXACT_HTTP_TARGET_JSON --source-id DECLARED_POD_STDOUT_SOURCE_ID \
  --output FRESH_OUTPUT --entered-by DECLARED_OPERATOR
```

`--execute`를 붙이면 고정 `kubernetes_log_minimization.capture_log_minimization`을
한 번 호출한다. audit/single PLAN과 정확 ENV/POL/PACK Ref를 원 Store에서 읽는다.
target의 정확 ENV Ref와 선정 Pod의 소유 세대·lab/image binding은 A의 기존 공개
계약으로 확인한다. A108의 `VALIDATION_ERROR_PROFILE`을 제공하는 실제 통합 소스에서
HTTP 수행 경계를 확인한다. 해당 프로파일이 없으면 collector는 요청 0회와 gap을
반환하고 결과는 UNKNOWN으로 남긴다. source capability gate를 해제하지 않는다.

선정 `source_id`는 기존 `required_sources`에 명시된 Pod stdout 한 개다. 기업의 목록을
선정 한 개로 축소하거나 다른 사본을 대신 확인했다고 표시하지 않는다. 기존 정책의
type·중복 금지·명시값을 유지하고 `forbidden_log_fields`와 `forbidden_marker_contexts`의
빈 목록도 원 계약대로 받는다. `required_branches`의 normal/error/exception_dump와
선택 debug/trace 요구는 변경하지 않는다.

## 실제 관측과 보존 범위

고정 수집기는 정상 tokenize 전후와 안전한 max_tokens 오류 요청 3개를 사용한다.
실제로 보낸 error_input 표식과 신선한 요청 ID에 연결된 JSON 사건을 메모리에서
기존 `analyze_log_captures`의 STRICT_JSON/exactRID 기준으로 분석한다. 실제로 보내지
않은 caller_input/private_system 표식을 채우지 않으며 전체 projection 왕복 검증을
주장하지 않는다. 해당 문맥과 실제 연결 필드를 확보하지 못한 exception dump는 UNKNOWN이다.

bridge는 정확 POL/target/ENV/source 결합과 `analysis_binding_verified`,
`source_generation_verified`를 확인한다. 실제 금지 필드 또는 금지 error_input의
확정된 enum/count만 security FAIL의 근거로 사용한다. 다른 결과와 기능·성능은 UNKNOWN이며
PASS는 제출하지 않는다. after/cleanup·정제 artifact 쓰기·후처리 실패가 이미 확인한
독립 FAIL을 지우지 않는다. 첨부 쓰기 실패 때 기존 수동 importer의 무첨부 FAIL 보존을 사용한다.

정제 artifact에는 Ref·정책/target 입력 결합 hash·수집 ID/시각·요청 상태·바이트/사건 수·
고정 enum/bool과 gap만 남긴다. 원문 로그·whoami·응답·credential·실제 표식/RID·내용 hash·
pipe hash·임의 필드명 또는 그 hash·전체 projection은 저장하지 않는다. 기존 Store가
계산하는 정제 artifact의 무결성 hash는 원문 로그 hash와 구분한다.

원 `log_fields_seen`은 null/미수집이며 HTTP 기능 관측 2개도 null/미수집이다.
`log_coverage_complete=false`와 정적인 부분 범위 gap, 실제 확인한 source/branch/문맥
enum만 기록한다. 저장 필드 이름을 새 필수 관측으로 만들지 않는다. 원래 정상 대조는
모델 추론까지 완료하지 않았으므로 `not_attempted`로 남기고, 부분 tokenize의 3개
상태와 정상 결과는 artifact/설명에 따로 기록한다. 원 챌린지 전체도 미시도 상태를 유지한다.
`log_sources_tested`는 선정 source의 실제 분기를 나타낸다. 전체
`log_required_branches_tested`는 기업의 모든 required_sources에서 확인한 분기의 교집합이다.
선정 source 외의 필수 사본을 수집하지 않았다면 전체 분기 목록은 비어 있다. 필수 source가
선정 하나인 경우에만 실제 확인한 required_branches를 기업 목록 순서대로 기록한다.

수동 EVD의 출처는 기존 계약대로 DECLARED다. EXAMPLE ENV의 단위 fixture는 artifact와
관측 설명에 EXAMPLE을 명시하며 실제 Kubernetes 관측으로 세지 않는다. 정책·신고 입력자의
진위를 인증하지 않는다. 전체 사본·전체 coverage·모델 기능·caller/private 문맥은 남은 범위다.

## Store와 전용 검증

원 Store를 읽기 전용으로 확인한 뒤 기존 export/restore로 새 output/store에 복원한다.
그 사본에만 manual EVD/RES/RPT를 append하고 같은 as-of로 judge/report·verify·bundle
복원·정확 Ref/보고서 내용 대조를 수행한다. 원 Store와 target 파일 bytes를 전후 확인하며
기존 출력이나 원본 자료를 덮어쓰거나 삭제하지 않는다. 새 common framework를 추출하지 않는다.

```text
uv sync --frozen
python -B -m pytest tests/test_log_minimization_evidence.py -q --basetemp=EXTERNAL_FRESH_TEST_DIR
```

전용 시험은 EXAMPLE capture 한 경계만 대체한다. 원 정의·정확 Ref·정책 보존·privacy·
후기 실패와 독립 FAIL·UNKNOWN 범위·원 Store 보존·복원/공개 CLI를 확인한다.
실제 collector/A108 HTTP 연결은 별도 고정 소스 통합 검증으로 구분한다.
preflight의 lazy import·출력 미생성·torch/vLLM 미수입은 새 Python subprocess에서 확인한다.
같은 pytest 수집 과정에서 collector 시험 모듈이 먼저 import되더라도 이 검증은 독립적으로 동작한다.

## 고정 소스 검증 결과 (2026-10-30)

검토 소스 `60f88b0261a1d79f229ba0c913a54a6725bafd6a`의 collector·bridge와
수정된 UTF-8/전체 분기 교집합/lazy-import 경계를 검증했다. collector 직접 60개,
bridge 직접 30개 및 지적사항 관련 회귀를 재사용했다. 실제 collector와 A 공개 metadata
reader를 EXAMPLE 전송에 연결한 원 manual→EVD→RES→RPT→반출/복원 통합은
2개 통과(0.91초)했다. wheel의 guard 168개 파일이 소스·설치본과 같고 새 모듈을
독립 import했으며 torch/vLLM은 로딩하지 않았다.

A108 고정 소비 소스 `be0eebefef6586dfca75384b229e93530ad7fbfb`에서는 실제
stdlib HTTP 자식으로 loopback 200/400/200 한 번을 확인했다. 로그·metadata는
EXAMPLE 입력이며 실제 Kubernetes 결과는 0회다. UTF-8 수정 후의 통합 소스는
위 직접 회귀로 확인하고 같은 HTTP 실행은 반복하지 않았다.

기존 PR #130 통합 component `4a7d7f6511293d0245ba9678aba7c437642eabd2`의
전체 pin 대상 tree가 검토 소스와 같음을 확인했다. B 전용 11개 도구의 component·
의존 SHA literal만 갱신하고 나머지 AST, 원 helper858·공동 기준선·팩·fixtureac8을
보존했다. 원 strict gate와 REQ09 수집 의존성 확인도 통과했다. 기존 source CPU
실측과 설치 검증을 재사용한다.

원본 실패·수정 회귀·설치 검증·정제된 EXAMPLE Store와 검토 소스는 저장소 밖
`byul-b-record-access-evidence-20261030/obs03-selected`에 보존했다. 실제 대상 실행은
승인된 target/POL과 A 공개 HTTP capability를 갖춘 통합 소스에서 위 명령의
`--execute`로 수행한다. caller/private 문맥, 다른 필수 로그 사본, 모델 정상 대조와
실제 운영 환경은 남은 범위다.
