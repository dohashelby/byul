# M9 입력 endpoint·입력 형식 정책 계약 설계

후속 구현: 사용자 실행 지시로 단일쌍 기술 제안을 채택한 [M10 구현·검증](POLICY_M10.md)을 참고한다. 아래 내용과 “미실행” 표시는 M9 설계 시점의 기록이며 기업 실제값·승인 채택은 아니다.

## 1. 상태와 검토 기준

**2026-10-22 설계 제안 / 제품 미구현.** 문서 작성 승인은 아래 M10 기술 프로파일이나 기업의 실제
허용값·시험 대상·승인을 채택한 것이 아니다. 정본은 [SPEC](../SPEC.md)이며 제품 동작을 바꾸지 않는다.
M10의 명시적 실행 지시에서 채택한 범위만 구현한다. 이 문서 끝의 프롬프트도 이번에는 실행하지 않는다.

- 기준: `codex/gua-policy-m8` / `c77636cd4ecf5cc7fada8e053c95a10110c453f2`.
- fetch 후 로컬/원격 0/0·clean, [M8 PR #10](https://github.com/dohashelby/gua/pull/10)은 open/draft·미병합이다.
  M9 브랜치는 `codex/gua-policy-input-m9-design`, PR base는 M8이다. main의 과거 상태를 최신 M8로 간주하지 않는다.
- 현재 팩은 14/DRAFT, 해시 `c5e35da1b52e4438b1427e8c76b3ad767b415a35936479c841631df165e8dad0`.
  77점검·14정책점검/24경로·303결정/595연결·a21/b6/c161/d115·자동 실행/판정 연결5다. M9에서 그대로 보존한다.
- 현재 버전: declared0.6.0, manual-entry0.4.0·수동/3, input_validation0.2.0, rule_table0.4.0.

### M8 검토 결과와 검증 수준

| 근거 | 확인 결과 | 해석과 한계 |
|---|---|---|
| [M8 계약](POLICY_M8.md), [정책 분기 함수][branch], [M8 회귀](../tests/test_policy_m8.py) | required bool·false/누락 구분, 비활성 문맥의 각 축 PASS 보류·FAIL 보존·과거 선언 비소급·보고 연결 | 합의한 **수동 정책 문맥**은 연결됨. 기업 승인·실제 usage/replay 상태·내용 진위는 검증하지 않음 |
| M8 최종 [PR CI](https://github.com/dohashelby/gua/actions/runs/39586507246) / [push CI](https://github.com/dohashelby/gua/actions/runs/39586490000)의 공식 API·작업 로그 | 각각850 passed/18 deselected,33.21초/47.19초, frozen sync·wheel 성공 | **과거 M8 실행**이며 M9 실행 수가 아님. 지원 Linux 기본1분 목표 충족 |
| [PROGRESS](../PROGRESS.md)의 M8 로컬 XML·설치 기록 | 전체817통과/28실패 뒤 테스트 fixture/표시 가정 수정, 최종 관련262통과/1skip; 실제 M7 wheel/팩13→M8 설치24명령 통과 | 과거 실패를 남은 제품 결함으로 집계하지 않음. Windows 전체1분 목표·루프백/symlink 제약과 Linux 결과를 구분 |
| 앞선2026-10-22 A 재검토의 XML | M8만98 passed/4.05초 | 앞선 별도 실행. 이번 M9 문서 작업에서 다시 실행한 것으로 표시하거나 합산하지 않음 |

이번 정적 대조에서 M8 계약을 깨는 새 회귀를 확정하지 않았다. 네 bool 경로가 실제 관측을 대체하지 않는
한계, 점검별 공유 정책 일관성 미검증은 문서에 명시된 범위 제외다. 이 상태는 입력 정책의 자동화 완료를
뜻하지 않는다. M9에서 확정된 제품 결함을 고치거나 코드 리팩터링을 끼워 넣지 않는다.

## 2. 원문과 현재 구현 대응

근거는 정본 원문 JSON의 아래 행과 현재 CheckDef다. 네 행의 enterprise_decisions는 endpoint·텍스트/직접
token 형식·template override·옵션/길이/출력 한도·정상 대조를 요구하고, vocab/버퍼/옵션 관계 같은 구현상
제약은 기업 정책으로 완화하지 못한다고 명시한다.

| 점검·원문 | 원문 보호 요구와 필요한 관측 | 현재 구현 | 이번 설계와의 경계 |
|---|---|---|---|
| REQ-07 [원문](../reference/framework/data/checklist.json#/request_state_assessment_draft/6) / [정의](../packs/PACK-BASELINE/checks/REQ-07.yaml) | 렌더 후 실제 입력에도 제한 유지; 원 입력/template·렌더 출력/token 수·검사 위치 | revision1, 정책/실행기/판정기 미등록, 구조화 관측 없음 | endpoint/형식 목록만으로 렌더 전후 일치·검사 시점을 판정할 수 없음. 자동 연결은 후속 |
| REQ-08 [원문](../reference/framework/data/checklist.json#/request_state_assessment_draft/7) / [정의](../packs/PACK-BASELINE/checks/REQ-08.yaml) | 재사용 ID 경로의 상위 호출자·도달·렌더 생략·필수 검사; kv_transfer_params/prompt_token_ids·실제 consumer | revision1, 정책/실행기/판정기 미등록 | 외부 `prompt:[정수]` 정상 요청과 내부/재사용 token 경로를 합치지 않음. 필드 존재·renderer 분기만으로 도달/비도달 확정 금지 |
| REQ-09 [원문](../reference/framework/data/checklist.json#/request_state_assessment_draft/8) / [정의](../packs/PACK-BASELINE/checks/REQ-09.yaml) | 모델 유효 ID/길이·위험한 소비 전 제한·후속 정상 요청; 메모리/상태/다른 요청 영향 | revision4, required max_input_tokens/int·rejection_codes/list[str], input_validation+rule_table, 구조화 관측5개 | 자동 연결은 있으나 정상 token-array와두 경계에 한정. 이번 M10 권고의 유일한 등록 대상 |
| REQ-10 [원문](../reference/framework/data/checklist.json#/request_state_assessment_draft/9) / [정의](../packs/PACK-BASELINE/checks/REQ-10.yaml) | sampling 옵션/조합·실제 runner/worker 제약·병행/후속 정상 요청; health만으로 복구 확정 금지 | revision1, 정책/실행기/판정기 미등록 | REQ-09 두 challenge를 sampling·worker 영향 검증으로 재사용하지 않음 |

**원문과 제한 구현의 차이:** 현재 REQ-09는 2xx 경계 요청을 `accepted_challenges`로 모아 FAIL 처리한다.
그 결과만으로 원문의 비허용 메모리 접근·상태 오염을 실제 관측했다고 말할 수 없다. 모의 계약은
[test_new_checks](../tests/test_new_checks.py)의400→PASS·200/206→FAIL·timeout/500→UNKNOWN 등이다.
실제 모델과 vocab_size의 일치, 음수 ID, 모든 endpoint/형식/경계 조합·동시 요청 영향은 미검증이다.
이것은 명시한 제한 프로파일이며 M9에서 기존 FAIL 기준을 조용히 완화하거나 넓히지 않는다.

## 3. 정책 단위·소유권·범위

| 기존 카탈로그 ID | 현재 연결 | 현재 상태 | M10 후보 경로(제안) |
|---|---|---|---|
| P-DRAFT-INPUT-ENDPOINTS | REQ-07/08/09/10 | b, list[str] 제안, current_param_paths=[] | values.REQ-09.allowed_endpoints: required list[str] |
| P-DRAFT-INPUT-FORMATS | REQ-07/08/09/10 | b, list[str] 제안, current_param_paths=[] | values.REQ-09.allowed_input_formats: required list[str] |

카탈로그 연결은 원문 요구의 대응이며 네 점검 동시 등록이나 값 자동 복사를 뜻하지 않는다.
M10 제안은 REQ-09 대상 engine 인터페이스의 **실제 승인된 전체 허용 목록**이다. Gateway·다른 서비스·다른
점검의 값을 자동으로 동일시하지 않는다. 같은 범위의 상충 정책인지 서로 다른 범위인지 업무 검토가 필요하다.

| 자료 | 소유 내용 | 소유하지 않는 내용 |
|---|---|---|
| CheckDef/팩 | 점검 절차·정상 표본·필수 관측·판정 규칙·지원 프로파일 | 기업 실제 허용 목록/승인 |
| POL | 해당 범위의 endpoint·형식 허용 기준, 기존 max_input_tokens/rejection_codes | 실제 등록/도달·tokenizer 유효 범위·실행 성공 |
| ENV | 모델 판본/inventory·실제 구성/하드웨어, 등록된 facts의 근거 | 새 endpoint/형식 정책 기본값. 이번에는 ENV 키를 추가하지 않음 |
| 실행 설정 | target·신뢰 출발점·vocab_size와 출처·max_probe_tokens·timeout | 기업 한도·승인, 실제 모델 일치의 자동 인증 |
| EVD | 실제 요청·시도·전후 대조·응답·시험 범위·첨부·미수집·출처 | 정책 의사나 승인/진위의 자동 확정 |
| RES/RPT | 저장된 증거의 순수 재판정·축별 범위/미확인·출처/공식 여부 | 네트워크 재시험·승인·미시험 조합의 충족 추론 |

원문의 정책 역할은 연결 items의 “적용 조직의 정책 승인 담당”이다. 예를 들어 REQ-09는
[items/14](../reference/framework/data/checklist.json#/items/14/policy_owner)와
[items/15](../reference/framework/data/checklist.json#/items/15/policy_owner)에 연결된다.
실제 담당자는 미정이며 플랫폼·보안·서비스 검토 역할은 아래 질문을 전달할 **제안 역할**이다.

## 4. endpoint·형식·관계의 식별 계약 제안

### 4.1 서로 다른 집합

허용 endpoint E와 실제 등록 route R, 정상 대조 N, 금지 입력 시험 C, 실제 시험 T, 미지원/미시험 U를
구분한다. `POL.scope`는 점검 ID 범위다. E는 정상 업무 입력의 허용 기준이며 C나 R을 지우는 필터가 아니다.
허용된 endpoint에서도 잘못된 token ID/길이의 음성 대조를 보내야 하고, E 밖 경로를 전부 미선정 처리하면
금지 경로의 보호 검증을 숨긴다. M10은 R의 전수 discovery나 E 밖 endpoint 시험을 추가하지 않고 한계를 표시한다.

M10의 canonical endpoint는 `METHOD path` 한 쌍, 정상 표본의 `FunctionalProbe.id`를 사용한다.
대문자 METHOD·공백 하나·문자 그대로의 절대 path를 정확히 비교한다. origin/port/자격정보는 endpoint에 넣지
않고 target 실행 설정에 둔다. engine/Gateway hop은 `FunctionalProbe.via`로 별도 확인한다.
새 helper에 엔진 이름이나 `/v1/...` 경로를 하드코딩하지 않고 기존 CheckDef 정상 표본을 기준으로 삼는다.

| 입력/차이 | M10 제안 처리 |
|---|---|
| 실제 CheckDef의 단일 정상 표본과 정확히 같은 METHOD/path | 프로파일 후보. 현재 팩의 표본은 POST /v1/completions |
| 소문자 method·앞뒤 공백·중복 항목 | 자동 trim/대문자화/중복 제거하지 않고 지원 불충분으로 중단 |
| 끝 슬래시·path 대소문자 차이·별칭 | 같은 경로로 추정하지 않음 |
| query/fragment·절대 URL·경로 변수·와일드카드·percent encoding·dot segment | 첫 프로파일에서 지원하지 않음. URL/라우터 정규화·실제 discovery 확장은 후속 |
| Gateway와 engine에서 같은 문자열 | 같은 노출 경계의 증거로 합치지 않음. 첫 프로파일은 engine 직통만 |

### 4.2 형식과 실제 소비

`token_ids`는 M10의 기술 식별자 제안이다. **정상 표본이 `prompt:[0]`인 일반 정수 배열 제출 형식**만 뜻한다.
0은 기존 합성 정상 표본의 원소이며 기업 허용값/모델 vocab 크기가 아니다. bool False를 정수0으로 취급하지
않도록 `type(x) is int`를 확인한다. template/messages·text·tokenizer 전후·재사용 ID/kv_transfer·내부
consumer·다른 body 키 조합은 첫 프로파일에 포함하지 않는다. 실제 parser가 무엇을 소비했는지는 추가 관측이 필요하다.
이름이 비슷한 형식을 alias로 합치거나 거부 응답만으로 유효한 정상 요청이었다고 가정하지 않는다.

### 4.3 두 목록이 관계표를 대신하지 못하는 경우

EXAMPLE 관계 `E={e1,e2}`, `F={f1,f2}`, 승인 관계 `R={(e1,f1),(e2,f2)}`에서 독립 목록의 곱집합은
승인되지 않은 `(e1,f2),(e2,f1)`도 만든다. 현재 list[str]는 이 관계를 표현하지 못한다.
첫 M10은 **E와F가 각각 정확히1개인 경우만** 받아 곱집합 추론을 피한다. 더 많은 값이 있으면 지원하는
한 쌍만 골라 실행·PASS 처리하지 않고 요청 전에 중단한다. 기업 목록을 임의로 잘라 맞추라고 안내하지 않는다.
관계표·endpoint별 예산/권한·여러 hop은 새 계약 설계가 선행하며 문자열 안 JSON으로 숨기지 않는다.

## 5. 최소 M10 프로파일 제안

이 절은 미채택 기술 권고다. 실제 기업의 목록이 더 넓으면 프로파일 확장이 먼저이며 값을 축소해 PASS를
만들어서는 안 된다. 기존 `str/int/bool/list[str]/dict[str,int]`만 사용하고 새 타입·DSL·레지스트리를 만들지 않는다.

1. REQ-09에 두 required list[str]를 추가한다. 기존 max_input_tokens/rejection_codes는 그대로다.
2. 같은 정의의 두 새 required 관측 `input_endpoints_tested:list[str]`, `input_formats_tested:list[str]`가
   있어야 새 계약으로 식별한다. 두 새 정책·관측의 정확한 선언 묶음에만 적용하고 부분 선언은 오류/미지원으로
   PASS를 보류한다. 둘 다 없는 과거 정의에는 새 전제를 소급하지 않는다.
3. 공백·중복·빈/복수 원소·지원 밖 식별자는 의미 불충분이다. 허용 endpoint 목록은 CheckDef의 단일 정상
   표본 id와 정확히 같고, 형식 목록은 `["token_ids"]`여야 한다. 정상 표본은 `via=engine`, POST·literal path,
   정확한 `prompt=[0]` 정수 배열·현재의 model/max_tokens 형태여야 한다. M10에서 기존 표본은 바꾸지 않는다.
4. 실제 실행은 `validate_policy`에서 정책·표본·기존 수치/시험 예산을 모두 확인한다. 실패하면 target 읽기·
   readiness·Docker 생성·어떤 probe 호출보다 먼저 중단한다. 의미 오류는 기존 core 예외 전파로 ABORTED/UNKNOWN,
   키 누락은 기존 NO_POLICY_VALUE→NOT_RUN/UNKNOWN이다. 새 Gap/NOT_APPLICABLE을 만들지 않는다.
5. collect에서는 기존5개 요청(정상3·경계2, readiness 별도)을 유지한다. 두 새 관측은 실제 준비/시도한
   정상 표본에서 endpoint와 제출 형식을 산출한다. 정책값을 복사해 관측 성공을 채우지 않는다. collect에
   도달하지 못하면 미수집이며, 정상 전 대조 실패 시 뒤의 경계 시험을 했다고 쓰지 않는다.
6. 기존 rule_table의 eq/value_policy로 보안·기능 PASS에 두 관측=정책 **전체 목록**을 각각 추가 요구한다.
   두 관측이 첫 프로파일의 단일 표본/형식을 가리키는지의 조건도 둔다. 빈=빈·부분 일치·복수 원소로
   통과하지 않게 정의 데이터의 고정 프로파일 기대값과 비교한다. 기존 fail_when_any는 그대로여서 새 범위
   근거 누락이 확인된 기존 FAIL을 가리지 않는다. 성능 규칙은 추가하지 않는다. 또한 RuleTableJudge의
   최종 PASS에만 공통 helper의 정책/프로파일/선언 완결성 상한을 적용한다. 실행 전 검사를 거치지 않는
   직접/오프라인 재판정도 부분 선언·미지원 정책을 legacy PASS로 통과시키지 못하게 한다. 기존 연산자나
   다른 점검 판정은 바꾸지 않고 안전한 POLICY_CONTEXT 사유를 남긴다.
7. 수동 /3의 기존 generic required_observations 칸에 두 관측을 받는다. declared도 같은 순수 프로파일 검사와
   관측의 정확한 list[str]·collected 여부·전체 목록 일치를 요구한다. 관측이 잘못된 타입으로 저장됐어도 PASS는
   보류한다. 기존 첨부·답변·INV11·신고 FAIL/UNKNOWN은 유지한다. 새로운 항목별 payload나 /4는 없다.
   REQ09는 자동 점검이므로 [수동 양식 옵션][manual-template]의 `--include-automated`와 비공백
   `override_reason`이 필요하다. 이 기존 보호를 제거하거나 자동 점검을 수동으로 숨기지 않는다.
8. 수동 config hash에는 해당 새 계약의 두 관측(값·수집 여부)을 조건부로 포함한다. 정책은 PLAN/POL Ref로
   고정한다. 다른 점검·과거 정의의 hash 입력까지 바꾸지 않는다. 새 plugin 버전으로 의미 변경을 남기되 과거
   EVD/RES·해시를 덮지 않는다. 수동 입력을 자동 실측/원본 인증으로 표시하지 않는다.

빈 양식은 `REQ-09:{}`를 유지한다. 아래는 **EXAMPLE 합성 문맥 조각**으로, 전체 POL도 기업 승인 목록도
아니며 기존 필수 수치/거부 코드·팩 Ref는 생략했다. examples/lab에 복사하거나 기본값으로 넣지 않는다.

```yaml
# EXAMPLE: M10 제안의 형태 설명, 현재 M9에는 미등록
data_origin: EXAMPLE
status: DRAFT
values:
  REQ-09:
    allowed_endpoints: ["POST /v1/completions"]
    allowed_input_formats: ["token_ids"]
confirmed_by: null
confirmed_at: null
```

## 6. 실제 호출·증거·hash·보고 설계

### 현재 경로와 수정 위치

```mermaid
flowchart LR
    A[팩·POL·ENV] --> B[select 타입·누락 검사]
    B --> C[run_plan / _run_one]
    C --> D[InputValidationExecutor.validate_policy]
    D --> E[HttpCheckExecutor readiness]
    E --> F[functional_requests: CheckDef 표본]
    F --> G[collect: 신뢰 출발점 엔진 요청]
    G --> H[EVD 관측·첨부·fixture/config]
    H --> I[rule_table 또는 수동 declared]
    I --> J[코어 PASS 관문·RES·보고]
```

근거: [선정 타입][select] → [점검별 POL 전달][run] → [플러그인 호출/예외][run-one] →
[요청 전 validate_policy와 readiness][http-run] → [표본 요청 생성][functional] →
[두 경계/대조][input] → [증거/hash][finish] → [재판정][judge]·[판정표][rule].
현재 REQ-09는 `requires_route_scope=False`이고 `functional_requests`가 직접 요청을 만든다.
어댑터 [probe_request][adapter]와 `probe_templates`는 일반 경로 프로브용이며 이 collect에서 호출되지 않는다.
새 검사를 어댑터에만 넣으면 실제 경로를 보호하지 못한다. M10은 기존 경로와 호출 수를 늘리지 않는다.

### 관측과 첨부

| 값 | 출처·의미 | 미관측/한계 |
|---|---|---|
| input_endpoints_tested(제안) | 준비/시도한 정상 표본의 METHOD/path 목록. 첫 프로파일1개 | 등록 route 전수나 Gateway 노출의 목록이 아님 |
| input_formats_tested(제안) | 실제 제출한 정상 표본의 일반 token-array 형식 목록 | 엔진 내부 parser/재사용 경로의 소비 인증이 아님 |
| 기존 challenge_outcomes/accepted_challenges/challenges_planned | token_id·length의 실제 응답/수집·음성 대조 계획 | 기대값·정책으로 응답을 채우지 않음 |
| 기존 functional_probe_results/planned·normal_control·challenges | 정상 전후 대조·실제 시도 | health만·4xx만으로 정상 충족을 만들지 않음 |
| probe_log.jsonl fixture/요약 | control·cases·POL·vocab_source와 새 지원 범위/미검증 한계 | 첨부 해시로 원본/승인 진위를 인증하지 않음 |

현재 [run._normalize][normalize]는 CheckDef.required_observations에 없는 관측을 버린다. 실행기에서 새 키만
추가하고 override 선언을 빼먹으면 재판정에 도달하지 않는다. 두 새 키의 선언·수집·판정표·수동 입력·보고를 함께 검증한다.
[FakeExecutor][fake]는 REQ01/03/PLT01만 지원하므로 기본 fake를 REQ09 검증으로 세지 않는다. REQ09의
가짜 container_probe 주입 시험은 별도 합성 계약이고 Docker stub·실제 엔진과 구분한다.

### 실행 설정·재판정·비교

현재 [HTTP _finish][finish]는 fixture와 execution_conditions(config)를 각각 해시하고 [core/run][run-one]이
draft.config로 executor.config_sha256을 만든다. REQ09 fixture에는 POL·control·cases·vocab_source가 이미 포함된다.
M10은 새 범위도 fixture에 명시하되 비교 hash에서 빼거나 정렬/필터로 다른 범위를 같게 만들지 않는다.
임시 경로·시각·세션 ID를 의미 있는 hash에 넣지 않는다. 실패 전 config stub과 미확보 comparability는
성공 실행의 동일 조건으로 만들지 않는다. [수동 config][manual-config]의 generic 관측 누락은 새 두 키에 한해 보완 제안한다.

PLAN은 한 POL을 고정하므로 update 양쪽에 서로 다른 정책을 암묵적으로 주입하지 않는다. 별도 PLAN/POL로
범위를 바꾼 결과는 단순 엔진 버전 회귀라고 할 수 없다. [비교][compare]는 확인된 model inventory·fixture·config와
GPU/driver 또는 명시적 CPU 프로파일이 같아야 한다. CPU는 보안/기능만, 성능은 UNKNOWN이다.

| 비교 상황 | 제안 처리 |
|---|---|
| 양쪽 같은 M10 팩/정책/단일쌍·자료·설정·하드웨어가 확인됨 | 기존 비교 표 적용. UNKNOWN은 개선/회귀로 단정하지 않음 |
| 모델·기한/예산·허용 범위·control·fixture·vocab 출처가 다름/미확인 | 기존 hash/조건 차이로 NOT_COMPARABLE. 실제 목록/시험 범위를 함께 설명 |
| 한쪽 미지원·정상 전 대조 실패·ABORTED/NOT_RUN·수동 comparability 없음 | 행 결과와 미시험/실행 실패를 보존. 조건 부족이면 비교 불가 |
| M8 팩/옛 증거와 M10 새 정의 | 과거 EVD/RES 그대로 유지, 새 required 전제를 과거에 소급하지 않음. 옛 실행의 정해진 범위를 새 전수 검증으로 표시하지 않음 |

보고는 기존 [정책 한계 치환/declared 이유][report]를 재사용한다. 허용 목록·실제 시험한 한 쌍·미시험 범위·
직통 hop·parser/원문 요구의 미검증·수동 신고 여부를 명시하고 official/출처/escape를 유지한다.
현재 report는 declared 판정에만 세 prefix를 전달한다. 따라서 M10은 core/report.py 한 파일에서
rule_table의 안전한 POLICY_CONTEXT 사유도 같은 방식으로 전달하도록 제한적으로 보강할 필요가 있다.
RPT 모델·선정/실행/판정 코어·비교 규칙은 바꾸지 않는다. 실제 새로운 자동 점검을 추가한 것으로 집계하지 않는다.
해당 조건의 근거는 [현재 사유 전달 분기][report-reasons]다. helper가 실패 상태를 판단기에 그대로 예외로 던져
기존 FAIL까지 UNKNOWN으로 지우지 않도록, 실제 실행은 예외 중단·판정기는 상태/사유로 PASS만 제한하는 경계를 둔다.

## 7. 판정 표와 반례(모두 설계 기대, 미실행)

기존 FAIL은 보존하고 어떤 표 행도 정책 자체의 부족을 새 FAIL로 만들지 않는다. 자동/수동의 검증 수준을
분리한다. 아래 “PASS 후보”도 공통 INV11과 기존 원칙을 모두 통과해야 하며 집계 PASS/공식 승인을 뜻하지 않는다.

| ID | 정책 상태 | 관측 상태·정상 대조 | 축 | 기대 결과/실행 상태 | 근거·보류 이유 |
|---|---|---|---|---|---|
| T01 | 새 required 키 누락 | 실행 전 | 전축 | NO_POLICY_VALUE→NOT_RUN/UNKNOWN | 의미 검증 전에 기존 선정 관문; 요청0 |
| T02 | null·str·bool·숫자/혼합 list | 범위 밖으로 준 값도 포함 | 전축 | select 타입 오류 | 자동 변환 금지 |
| T03 | []·공백·중복·앞뒤 공백 | 실행 전 | 전축 | real ABORTED/UNKNOWN, 수동 PASS 보류 | 빈=비적용 아님; readiness 포함 요청0 |
| T04 | 미등록/별칭·query·절대URL·동등해 보이는 변형 | 실행 전 | 전축 | T03과 동일 | 보수적인 식별 범위, 경로 정규화 추측 금지 |
| T05 | 두 목록 중 하나가 복수 또는 조합 관계 미지원 | 지원 한 쌍도 목록에 있음 | 전축 | T03과 동일 | 일부만 골라 실행/PASS 금지; 기업 목록 축소 금지 |
| T06 | 단일쌍이나 CheckDef 표본/via/body와 불일치 | 실행 전 | 전축 | T03과 동일 | 실제 호출과 연결 안 된 정책; target 접근 전 중단 |
| T07 | 지원 단일쌍 | 실제 범위 관측=전체POL, 두 경계 모두 허용 거부코드, 전후 정상 성공 | 보안/기능 | PASS 후보/PASS 후보 | 기존 두 경계 범위만. parser/모든 입력 조합 검증 아님 |
| T08 | T07 | 경계200 또는206 수신 | 보안 | 기존 FAIL 유지 | accepted_challenges 비어 있지 않음. 메모리 손상 실측으로 확대하지 않음 |
| T09 | T07 | 경계timeout·5xx·POL에 없는400 등 | 보안 | UNKNOWN | 허용된 거부의 확인 부족; 무응답은 PASS 아님 |
| T10 | T07 | 정상 전 대조 실패, 뒤의 경계 미시도 | 보안/기능 | UNKNOWN/기존 기능FAIL 또는UNKNOWN | 응답이 있으나 불허 코드면 기능FAIL, 무응답은UNKNOWN; 정상/시도 관문 유지 |
| T11 | T07 | 정상 후 대조 실패 + 확인된 경계2xx | 보안/기능 | FAIL/기존 기능FAIL 또는UNKNOWN | 후속 부족으로 독립 보안FAIL을 지우지 않음 |
| T12 | T07 | 새 범위 관측 미수집/빈/부분/정책과불일치, 다른 값은 성공형 | 보안/기능 | PASS 보류→UNKNOWN | 두 목록 전체 일치와 고정 단일 프로파일 조건; 빈=빈 PASS 금지 |
| T13 | T12 | 별도 accepted_challenges 또는 기능 실패가 확인됨 | 해당 축 | 기존 FAIL 유지 | INV04; 새 PASS 전제를 FAIL의 전제로 붙이지 않음 |
| T14 | T07 | 수동 신고PASS·두 범위 관측 정확·첨부/답변/정상/챌린지 충족 | 신고 축 | 기존 선언 PASS 후보 | DECLARED일 뿐 자동 parser 관측/승인 아님, 비교값 없으면 비교 불가 |
| T15 | T07 또는미지원 | 수동 관측 값이 str/bool/null/미수집, 근거/관문 부족 | 신고PASS 축 | UNKNOWN | generic 입력 저장 계약을 전역 변경하지 않고 새 소비자에서 보류 |
| T16 | 어떤 정책 상태 | 신고FAIL 또는신고UNKNOWN | 해당 축 | FAIL 유지/UNKNOWN 비승격 | 기업 승인/예외로 바꾸지 않음 |
| T17 | 과거 정의의 두 새 정책/관측 모두 없음 | 과거 봉인 증거 | 기존 축 | 과거 계약 그대로 재판정 | 새조건 비소급, 원본/Ref/해시 보존 |
| T18 | 새 정의가 일부만 선언됨 | 직접 플러그인/불완전팩 경로 | 해당 축 | 새 PASS 허용 안 함 | 완전 부재와 불완전 확장을 구분; 실수로 legacy fallback 금지 |
| T19 | T07, 명시CPU | 범위·대조 충족 | 성능 | UNKNOWN | INV09, 새로운 성능 규칙 없음 |
| T20 | 양쪽 조건 같거나다름 | §6의 비교 전제 | 보안/기능 비교 | 기존 표 또는NOT_COMPARABLE | side 혼용/임의 hash 보충 금지 |
| T21 | T07 | 새 범위 관측만 바꾼 수동 입력 | 실행 hash/이력 | 새의미 hash·새EVD, 원본 보존 | 같은 as_of 재판정 멱등, 시각/파일경로 변화만으로 흔들지 않음 |
| T22 | 지원 목록·scope 설정 | REQ07/08/10 등 미구현 분기 | 선정/보고 | 미지원·NO_EXECUTOR 유지 | 목록으로 자동 제외/자동 연결/검증 승격 금지 |

위 T01~22는 다음 구현의 계약 테스트 시나리오다. M9에서 네트워크·엔진·합성 실행 테스트를 새로 수행한 결과가 아니다.

## 8. 변경 지점·대안·최소 범위

| 선택 | 구현 비용/정확성 | 판단 |
|---|---|---|
| 네 점검에 목록만 선언, real·수동 소비는 미연결 | 키 입력은 쉬워도 무시되는 값/과도한 PASS·등록=자동화 오해 | 권고하지 않음 |
| REQ09 한정 단일쌍 검증·오프라인/수동 관측 대조·보고 | 현재 정상 표본과 두 경계를 보존하고 목록과 호출을 실제 연결. 넓은 정책은 중단 | **M10 권고** |
| endpoint별 형식/권한/예산 관계표·여러 executor/renderer 통합 | 새 타입·관측·route/consumer 지원·규칙 확장이 필요 | 실제 요구·증거 확보 후 별도 설계 |

M10에서 등록할 대상은 **REQ-09만**이다. 다음은 후보이며 M9 제품 변경은0이다.

| 후보 파일/함수 | 최소 책임 | 보존/검증 |
|---|---|---|
| tools/overrides/REQ-09.yaml → pack checks/manifest | 두 required 정책·두 required 관측, 양 축 PASS 대조 규칙·구체 한계 | 정상 표본/두 경계·기존FAIL·다른76점검 보존, 재생성 동일/멱등 |
| 새 src/guard/input_policy.py(제안) | engine 중립 순수 프로파일/목록/관측 비교. exact선언·legacy/partial 구분 | I/O·시계·ENV·Store·값 보충·엔진 이름/경로 하드코딩·범용 레지스트리 금지 |
| executors/input_validation.validate_policy/collect | 요청0 선행검사·실제 표본에서 두 관측/fixture 범위 생성 | 표본·network hop·5요청/기존 예산·응답 분류 유지 |
| judges/declared.judge | 동일 helper로 수동 신규 계약의 PASS 상한 | 기존 FAIL/UNKNOWN·관문·다른점검·역사정의 유지 |
| judges/rule_table.judge | 실행기를 거치지 않는 재판정에도 새 정책/프로파일·부분선언의 최종 PASS 상한 적용 | 기존 OPS/FAIL 우선/다른점검/원래 축 규칙 보존 |
| core/report.build_report의 prefix 전달 조건 | rule_table의 안전한 POLICY_CONTEXT도 전달 | 이 한 파일 외 공통 코어 변경 없음, 원문 요청·비밀값 미노출 |
| collectors/manual.import_evidence의 config 구성 | 새계약 두 generic 관측의 값/수집 여부를 조건부 hash 입력 | /1/2/3·기존payload·배치오류/Ref/side·다른정의hash보존 |
| docs/policy_catalog.csv·metadata generator·입력/실행 문서 | 실제 REQ09 두경로만 a로 연결, 설명/미구현공유점검·검증 수준 명시 | 303ID/595연결·원문·기업값·scope·승인보존 |
| tests/test_policy_m10.py·기존REQ09/수동/불변식 회귀 | T01~22, 진짜과거wheel 재판정·설치본·동일입력·실패/제로요청 | 엔진 없는 논리·가짜프로브·실제엔진 수준 구분 |

core/report의 사유 전달 한 곳을 제외한 core/select/run/judge/compare·models/store·adapter·의존성은
수정할 필요가 없다는 것이 현재 제안이다. rule_table은 새 연산자를 만들지 않고 기존 규칙과 작은 PASS 상한을
사용한다. 공통 helper는 real·오프라인 rule_table·declared의 의미 중복을 줄이며 범용 정책 기반 클래스가 아니다.
기존 HTTP 골격 변경도 불필요하다. “코어0파일”을 목표로 사유가 보고서에서 사라지는 설계를 선택하지 않는다.

권고 버전(제안): input_validation0.3.0, declared0.7.0, manual-entry0.5.0, rule_table0.5.0.
/3과 기존 두 증거 payload 버전은 유지한다. M8에서 시작해 정확히 이 범위만 등록하면 팩15/DRAFT·REQ09정의5,
14정책점검/26경로·a23/b4/c161/d115·자동연결5가 예상된다. 숫자는 채택 후 실제 재계산하며 현재 완료 수로 적지 않는다.
새 키 누락은 NOT_RUN으로 명시되므로 기존 기업 policy 파일에 값을 자동 채워 호환시키지 않는다.
examples/lab은 필요한 팩 참조만 갱신하고 실제 새 기업값은 비워 둔다. 옛 팩 보존본과 POL을 함께 사용해 재판정한다.

## 9. 열린 결정·산출물·완료 조건

| ID | 구분·권고 | 필요한 사람/선행 조건 | M9에서 남길 산출물 |
|---|---|---|---|
| POL-M9-SCOPE | 기술제안: REQ09한정·engine 단일정상표본/형식. 복수목록은 중단 | 다음 M10 실행 지시에서 명시 채택 | §4~8·T01~22·M10 프롬프트 |
| POL-M9-PAIR | 기술제안: 두 list는 단일쌍만, 일반 관계표는후속 | 여러조합 업무요구·관계/수치/권한 필요성 검토 | 곱집합 반례·제외 범위 |
| POL-M9-OBS | 기술제안: 실제 제출범위의 두 관측·기존 fixture/수동generic/hash | 원본 parser 진위/내부 소비 관측은 별도 | §6호출/이력·부분선언 실패 조건 |
| POL-M9-VALUES | 업무미결: 실제 target interface의 전체 허용목록·단일쌍 해당 여부 | 정책 승인 담당·서비스/보안 검토 역할 제안 | 담당 미지정 질문. 허용목록 임의 축소 금지 |
| POL-M9-BOUNDARY | 업무미결: Gateway/직통·tenant·상위 호출자·정상/금지 시험 권한 | 플랫폼·보안 시험 책임자 제안, 대상승인 | 엔진 일반token과재사용ID/renderer 대응·미검증 범위 |
| POL-M9-VALIDITY | 사실/업무미결: 모델/vocab/옵션 유효 범위·근거·예산/정상 대조 | 모델·플랫폼 담당 제안. 실제값/기한 추정 금지 | 기존 제약 보존·실증 선행자료 |

팀에 전달할 구체 질문: (1) 어느 서비스/hop/호출자 범위의 승인 목록인가? (2) 실제 전체목록이 단일쌍인가,
아니면 endpoint별 관계가 필요한가? (3) text/messages/직접ID/재사용ID 각각 어디까지 허용하고 누가 승인하는가?
(4) 모델/vocab·최종 소비·렌더 전후·비노출의 근거는 무엇인가? (5) 금지 입력 시험 대상·예산·정상 대조·중단 조건과
검토자는 누구인가? 실제 담당자·기업 값·승인·실행 조건은 이 문서에서 확정하지 않는다.

M9 완료 증거는 네 문서만 변경, 모든 기존 제품/팩/CSV/정책/증거 바이트 보존, 코드/원문 위치·문서 링크·예시·
표/프롬프트/수치의 정합성 검증이다. 문서 전부 작성 후 검증하고 오류를 수정한다. 로컬 pytest/새wheel·엔진을
반복 실행하지 않고 과거 결과와 문서 검증을 구분한다. 커밋 후 자동 CI는 최종 SHA 기준으로 확인해 PR/종료 보고에 남긴다.

## 10. 다음 구현 담당 B에게 전달할 M10 프롬프트(미실행)

```text
너는 gua 정책 개발 담당 B다. 저장소는 https://github.com/dohashelby/gua.git 이다.
M9 최신 HEAD·문서와 docs/POLICY_M9_PLAN.md를 읽고 아래 제한 프로파일을 구현하라.
이 실행 요청은 M9의 기술제안 POL-M9-SCOPE/PAIR/OBS를 채택하지만 기업 실제값·승인이나 실제 엔진 시험을 승인하는 것은 아니다.

1. 작업 지침·SPEC·PROGRESS·DECISIONS·M8/M9 문서와 실제 코드를 먼저 대조하라.
   M9 PR이 미병합이면 M9 최신HEAD에서 codex/gua-policy-input-m10을 만들고 PR base를 M9로 지정하라.
   병합됐다면 M9가 포함된 기준을 확인하라. 기존 변경/다른작업트리/증거를 보존하고 main 직접push·force·자동병합은 하지 마라.

2. REQ09만 allowed_endpoints와 allowed_input_formats required list[str]를 등록하라.
   현재 CheckDef 단일 engine POST 정상 token-array 표본과 정확히 대응하는 목록 각1개만 지원한다.
   token_ids는 일반 prompt 정수배열 기술식별자다. 기존 normal probe/method/path/body/두경계는 바꾸지 마라.
   endpoint는 CheckDef 표본에서 유도하고 helper에 엔진 이름/경로를 하드코딩하지 마라.
   공백·중복·빈/복수·별칭/query/URL/경로변수·지원밖형식·잘못된정수/분기·표본불일치를 자동 정규화/부분선택하지 마라.
   기업 목록이 더 넓으면 프로파일 확장이 먼저다. 기업목록을 잘라 맞추거나 기본값을 넣지 마라.
   REQ07/08/10은 등록·자동 연결하지 말고 재사용ID·renderer·sampling을 일반 token-array와 합치지 마라.

3. 새 정책2개와 input_endpoints_tested/input_formats_tested required list[str] 관측2개의 완전한 선언에만
   새 계약을 적용하라. 모두없는 과거 정의는 legacy로 보존하고 일부만있는 확장은 legacy fallback으로 PASS시키지 마라.
   작은 engine중립 순수 helper를 real·declared에서 재사용하라. 새 타입/DSL/플러그인종류/객체/ENV키/의존성은 금지다.

4. 실제 run 경로의 InputValidationExecutor.validate_policy에서 정책·정상표본·기존 수치/예산을 모두 선행검사하라.
   의미오류/미지원이면 target읽기·readiness·Docker생성·모든probe 전에 중단하고 기존 core로 ABORTED/UNKNOWN을 남겨라.
   키누락은 기존NO_POLICY_VALUE→NOT_RUN이다. NOT_APPLICABLE/자동제외/정책위반FAIL을 새로 만들지 마라.
   정상 호출과 경계시험은 기존 trusted engine 경로/5개 collect요청을 유지한다(readiness 별도).

5. collect가 실제 준비/시도한 표본에서 두 범위 관측과 fixture 설명을 만들게 하라. 정책을 관측으로 복사하지 마라.
   override의 두 관측 선언과 함께 연결하라(core._normalize가 미선언키를 버린다).
   기존 rule_table 연산자로 보안/기능 PASS에 두 관측과 정책전체목록의 정확일치 및 고정 단일프로파일 조건을 추가하라.
   빈=빈·부분일치·잘못된타입으로 PASS되지 않게 하라. rule_table의 최종 PASS에도 공통 helper의 정책/프로파일/선언
   완결성 상한을 적용해 직접·오프라인 재판정을 보호하라. 기존 fail_when_any·OPS·다른 점검은 그대로 보존하고
   성능규칙은 추가하지 마라. 공통 판정/비교 코어를 수정하지 마라.

6. 수동/3의 기존 generic 관측 칸을 사용하라. declared에도 같은 정책·표본·관측 의미의 PASS상한을 적용하고
   기존첨부/답변/INV11·신고FAIL·UNKNOWN 비승격을 유지하라. 새payload나/4는 만들지 마라.
   자동점검REQ09의 수동양식은 --include-automated로 만들고 override_reason을 입력하라. 기존대체보호를 우회하지 마라.
   판정기에서는 helper 오류를 상태/사유로 받아 기존결과 중 PASS만 제한하라. helper 예외로 기존FAIL을 지우지 마라.
   해당 신규계약에서만 두 관측의 값/collected를 manual config hash에 포함하고 다른점검/옛정의의 입력은 보존하라.
   /1/2읽기·정확PLAN/POL/팩Ref·side·과거객체/해시·멱등을 유지하라.
   실제 변경에 맞춰 input_validation/declared/manual-entry/rule_table 버전을 올리고 의미를 기록하라
   (제안0.3.0/0.7.0/0.5.0/0.5.0).

7. 기존 POLICY_CONTEXT와 CheckDef 한계 표시를 재사용해 허용목록·실제한쌍·직통hop·미시험범위·DECLARED/EXAMPLE·
   parser/원문 보호 요구·실제기업값/승인 미검증을 JSON/HTML에 명시하라. official·escape·축분리·비교조건을 유지하라.
   core/report의 전달 조건만 보강해 rule_table의 안전한 POLICY_CONTEXT도 포함하라. 그 외 core/models/store/
   adapter/HTTP골격은 바꾸지 마라. 이 요청은 해당 제한적 코어/판정기/수동hash 보강도 승인한다.
   추가 변경이 불가피하면 필요한 이유와 가장 작은 대안을 먼저 특정하라.

8. override→팩/메타데이터 생성·카탈로그를 일치시켜라. 두 ID/303결정595연결·다른76점검/원문/증거 보존,
   실제 REQ09 두경로만 등록하라. 예상은 팩15·REQ09정의5·14점검26경로·a23/b4/c161/d115·자동연결5이지만
   최신기준에서 실제 재계산하라. 다른공유점검은 미등록임을 명시하라.
   examples/lab은 필요한팩참조만 갱신하고 기업값·scope·출처·승인을 보존하라.

9. 구현·테스트코드·문서를 한묶음으로 모두 작성한 뒤 테스트→실패수정→필요한재검증을 하라.
   M9 T01~22, 실호출경로와 모든probe0 선행거부, 정상/거부/2xx/timeout/5xx·후속정상, 수동/오프라인범위가드,
   부분선언/empty=empty/타입/부분목록 반례, 기존FAIL보존, 참조/side/hash/멱등·JSON/HTML을 검증하라.
   실제 과거M8 wheel/팩14로 만든자동/수동 EVD/RES를 새설치본으로 재판정해 원본/과거동작을 보존하라.
   frozen sync·기본전체·wheel·저장소밖설치본을 확인하고 지원Linux1분 목표·Windows제약·skip/제외를 구분하라.
   기본fake는REQ09를지원하지않으므로 합성container_probe 주입과 구분하라. 실제엔진/두이미지/운영/유료자원은 실행하지 마라.
   실패를 숨기려고 규칙/불변식/테스트를 완화하지 말고 과거결과와 직접실행을 합산하지 마라.

10. SPEC·POLICY_TEMPLATE·카탈로그·실행안내·PROGRESS·DECISIONS와 docs/POLICY_M10.md를 실제상태로 갱신하라.
    검증 후 이번변경만 커밋·푸시·초안PR로 제출하고 최종SHA의CI·원격HEAD·base/diff를 확인해 PR을 작업에 연결하라.
    한국어로 변경/검증/미검증·코어변경여부·브랜치/커밋/PR·기업결정과 후속관계표/실증과제를 보고하라.
```

## 근거 링크(검토 HEAD 고정)

[branch]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/judges/declared.py#L26
[select]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/core/select.py#L35
[run]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/core/run.py#L105
[run-one]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/core/run.py#L148
[http-run]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/executors/http_common.py#L206
[functional]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/executors/http_common.py#L359
[input]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/executors/input_validation.py#L25
[finish]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/executors/http_common.py#L248
[judge]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/core/judge.py#L76
[rule]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/judges/rule_table.py#L122
[adapter]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/adapters/vllm.py#L186
[normalize]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/core/run.py#L78
[fake]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/executors/fake.py#L120
[manual-config]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/collectors/manual.py#L368
[compare]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/core/compare.py#L22
[report]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/core/report.py#L98
[report-reasons]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/core/report.py#L171
[manual-template]: https://github.com/dohashelby/gua/blob/c77636cd4ecf5cc7fada8e053c95a10110c453f2/src/guard/cli.py#L199
