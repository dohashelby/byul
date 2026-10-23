# 정책 입력 양식과 수동 증거

`guard policy template`은 팩에 선언된 `policy_params`를 읽어 빈 기업 정책 YAML을 만든다.
환경·PLAN·Store·Docker·`guard.toml` 없이 사용할 수 있다. 시험 실행이나 기업 정책 승인은 아니다.

```sh
uv run guard policy template --pack packs/PACK-BASELINE --out my-policy.yaml
```

설치한 wheel도 `guard policy template --pack /absolute/path/to/pack --out my-policy.yaml`로 사용한다.
출력 파일은 새로 만들어야 한다. 기존 파일·symlink·디렉터리는 덮어쓰지 않고 실패한다.
입력 팩 내부(부모 symlink를 해석한 실제 경로 포함)는 쓰기 전에 거부해 manifest를 보존한다.
팩 밖의 필요한 부모 디렉터리만 만들며 본문은 UTF-8/LF다. 같은 팩은 같은 양식 바이트를 생성한다.

## 빈 값과 실제 입력

현재 M10 팩은 14개 점검·26개 선언을 갖는다(M1 5개·11개, M2/M4 7개·13개, M5/M6 9개·15개, M7 10개·20개). 생성기는 이 이름이나 개수에 한정되지 않으며,
다른 팩의 활성 점검 선언도 읽는다. retired 점검과 선언 없는 점검은 값 안내에 넣지 않는다.
기본 `scope.include`와 `scope.exclude`는 모두 빈 목록이므로 양식에 표시한 14개로 점검 범위를 축소하지 않는다.

양식은 각 점검을 `REQ-01: {}`처럼 비워 두고 파라미터 이름·타입·필수 여부·설명·결정 역할·
소비 범위·검증 한계를 주석으로 안내한다. 실제 파라미터 키와 기업 값은 생성하지 않는다.
입력하려면 해당 `{}`를 지우고 그 아래에 필요한 키와 **기업이 결정한 값**을 들여쓰기한다.
별도 `policy import` 명령은 없으며 기존 로더와 `guard select --policy`를 사용한다.

| 입력 | 기존 계약 |
|---|---|
| 선정된 점검의 선언된 필수 키 생략 | `NO_POLICY_VALUE`; 미입력인 채로 로딩·선정 가능 |
| 선언된 키의 `null` 또는 잘못된 타입 | select의 타입 오류; 정책 범위 밖의 주어진 값도 검사 |
| int에 bool·숫자 문자열 | 타입 오류 |
| `[]`, `false`, `0` | 미입력이 아닌 값. 타입 검사 후 의미는 소비자별로 검증 |
| 선언되지 않은 카탈로그 항목 | M1에서 등록하지 않음. 전체 후보에 gap이 생기는 것은 아님 |

`confirmed_by`와 `confirmed_at`의 null은 기존 승인 메타데이터의 공란이다.
`values` 파라미터의 null과 다르다. 양식은 `data_origin: DECLARED`, `status: DRAFT`이며
실제 담당자·승인·공식 정책 기본값을 채우지 않는다. 빈 양식이 로딩된다는 사실은 실행 준비나 PASS가 아니다.

REQ-09는 여전히 양의 `max_input_tokens`, 한도+1 요청까지 포함하는 시험 예산,
비어 있지 않은 `"400"`/`"413"`/`"422"` 거부 코드 목록을 요구한다. 0이나 빈 목록을
미입력 기본값으로 넣지 않는다. 실제 `vocab_size`와 출처는 실행 설정이며 기업 한도가 아니다.

## 설명의 출처와 일치 조건

[정책 카탈로그](policy_catalog.md)의 분류 a 23개 결정(26개 선언 경로)에서 설명·일반적인 원문 역할·
소비 경로·검증 기록만 추려 패키지 내부 `collectors/policy_template_metadata.json`에 보존했다.
생성기는 저장소의 `docs/`·`reference/`를 런타임 필수 경로로 읽지 않는다.
공통 모델·팩에 설명 필드를 추가하지 않았다.

설명은 입력 팩 Ref(id·revision·manifest 해시)가 정확히 같고 파라미터 타입도 같을 때만 사용한다.
다른 팩·개정판은 기존 설명을 재사용하지 않고 미지정으로 표시한다. 팩 자체의 정책 문맥·
한계·실행기/판정기 연결은 그대로 주석에 나타내지만 파라미터별 실제 소비가 확인됐다고 주장하지 않는다.
새 팩에서도 양식 생성은 가능하며, 추가 설명을 제공하려면 해당 팩과의 연결을 별도로 검토해야 한다.
주석은 정책값이나 판정 규칙이 아니며 description 데이터에 기업 수치·자격 값을 넣지 않는다.

기존 REQ-01/03·PLT-01의 과거 제한 실측 기록과 PLT-04·REQ-09의 모의 계약 검증을 구분한다.
M1의 팩은 revision 10이며 M2/M4는 revision 11/DRAFT, M5는 revision 12/DRAFT다. 자동 실행기 연결 정의는 5개를 유지한다. 새 두 정책 등록은 자동 실행·실제 엔진·두 이미지 실증을 추가한 것이 아니다.

## 검증과 후속 범위

`tests/test_policy_template.py`는 선언 기반 생성, 기존 로더/선정의 빈·부분 입력,
잘못된 타입·소비자 제약, 설명의 팩 일치, 여러 줄 주석의 YAML 경계, 출력 파일 보호를 검사한다.
기본 테스트·wheel·저장소 밖 설치본의 실제 결과는 [PROGRESS](../PROGRESS.md)의 M1/M2 기록에 남긴다.

M3의 검토 결과와 후속 구현 제안은 [정책·증거 대조 계획](POLICY_M3_PLAN.md)에 정리했다. M3는 설계 문서 작업이며 새 정책/판정 구현이나 실측 완료가 아니다. 다음은 SUP-14/LIF-07의 항목별 근거 연결을 우선하고, b 후보 등록·복잡한 허용표·수치 도출·외부 CHK는 별도 검토한다. 실제 엔진 시험은 별도 환경과 계획이 필요하며 기업 정책값은 확정하지 않는다.


## M2의 두 필수 목록과 수동 판정

| 값 경로 | 원소 의미 | 현재 소비 범위 |
|---|---|---|
| values.SUP-14.required_checks | 기업이 식별하는 필수 검사 ID/이름 | 타입·키·양식 + M4/M5 신고된 항목별 원결과/집행/첨부 대조 |
| values.LIF-07.required_readiness_items | 기업이 식별하는 필수 준비 항목 ID/이름 | 타입·키·양식 + M4/M5 신고된 준비/접수/추론/순서/첨부 대조 |

두 목록을 gua 점검 ID 목록으로 가정하거나 역할×대상×조건 표를 문자열로 숨기지 않는다. 실제 항목·담당·빈 목록의 업무 유효성은 기업 결정이다. 값이 있어도 자동 검사/준비 관측은 수행하지 않으며 NO_EXECUTOR는 유지된다.

`declared` 0.2.0은 신고된 PASS라도 해당 CheckDef의 required 정책 키가 빠지면 UNKNOWN으로 보류하고 누락 키를 이유에 남긴다. 이 관문은 기존 11개 정책의 수동 override에도 적용된다. []/false/0은 키 누락이 아니며, 키 존재는 값 내용의 정책 충족 증거가 아니다. 첨부·필수 관측 설명·정상 대조·챌린지 관문은 그대로이고 이미 확인된 FAIL도 유지한다.

과거 수동 PASS에 필수 정책 누락이 있었다면 새 판정기 재판정은 UNKNOWN으로 달라질 수 있다. EVD·기존 RES/해시를 고치지 않고 새 RES revision·플러그인 버전·보류 이유를 남긴다. 유효한 기존 정책과 필수 키 없는 점검의 동작은 유지한다.

## 설명 자료 갱신 절차 (개발용)

1. 검토된 정책만 overrides에 등록하고 로컬 원문으로 팩을 재생성한다.
2. 카탈로그의 실제 values 경로·분류 a·타입·출처·소비 범위를 대조한다.
3. 아래 도구로 지정한 패키지 설명 파일을 갱신한다. 미연결·중복·미선언·타입 불일치는 실패한다.

```sh
uv run python tools/import_checklist.py
uv run python tools/build_policy_template_metadata.py --pack packs/PACK-BASELINE --catalog docs/policy_catalog.csv --out src/guard/collectors/policy_template_metadata.json
```

이 도구는 개발자가 지정한 설명 파일을 교체할 수 있다. 기업 정책 파일의 배타 생성 계약과 구분한다. 같은 입력은 같은 설명 바이트를 만든다. 런타임은 여전히 docs/reference를 읽지 않고 wheel의 JSON만 사용한다.

번들 설명은 현재 팩 Ref에만 연결한다. 이전 팩을 명시적으로 읽으면 선언 기반 양식은 생성하지만 상세 설명은 미지정일 수 있다. 과거 PLAN의 정확한 팩 보존본 재판정과는 별개이며 오래된 해시 검사를 완화하지 않는다.

## M3 검토 기록과 다음 구현 프롬프트

아래는 M2 당시의 검토 요청이다. [M3 설계안](POLICY_M3_PLAN.md)에는 코드 흐름·미결 결정·시나리오와 후속 구현 프롬프트를 보존했다. /2 양식·declared 0.3.0·보고 연결의 현재 M4 구현 계약은 아래를 따른다. M3의 사실/제안은 당시 기록으로 유지한다.

> gua의 M2 최신 브랜치·커밋과 실제 PR 상태를 확인하고 정책 입력 전제와 정책 내용의 충족 판정을 구별하라. SUP-14/LIF-07의 빈 목록 허용 조건·검사/준비 식별자·실제 목록·책임 역할에 필요한 팀 질문을 정리하고, 수동 증거와 정책 항목을 어떻게 대조할지 설계안을 작성하라. 남은 b 10개 중 다음 후보는 최대 2개만 제안하고 실제 소비 경로·선행 결정·최소 변경·의미 있는 실패 검증을 명시하라. 기업 값·담당자·승인·실측을 만들지 말고, 이번에는 새 팩/실행기/판정 규칙을 구현하지 말라. core/models 변경 필요성과 실제 엔진 환경 과제를 구분해 사람이 검토할 계획·질문·다음 구현 프롬프트를 제시하라. M2의 PASS 보류/FAIL 보존·과거 증거·팩 Ref 일치 계약을 유지하라.

## M4: 수동 정책 항목과 증거 연결

M4는 수동 양식 /2·manual-entry 0.3.0·declared 0.3.0을 제공한다. 기업 정책 입력 명령 자체는 기존과 같고 팩은 revision11/DRAFT·77점검·7점검/13정책 선언·자동 실행기 연결5를 유지한다. 두 점검에 실제 엔진 실행기를 추가한 것이 아니다.

```sh
uv run guard evidence template --plan PLAN-ID --out manual-v2.yaml
uv run guard evidence import manual-v2.yaml
uv run guard judge --plan PLAN-ID --as-of 2026-10-21T00:00:00Z
uv run guard report --plan PLAN-ID --json report.json --html report.html
```

양식의 plan·policy·pack Ref는 그대로 보존한다. 두 점검의 policy_evidence.records는 PLAN이 고정한 POL의 실제 항목 ID와 case를 사용한다. 관측 칸·verdicts·시험 시도·첨부는 실행/확인한 사실만 사람이 입력한다. null/빈 목록은 성공 결과가 아니다.

아래는 **EXAMPLE 입력 형태 조각**이며 완성 정책/시험 결과가 아니다. 전체 양식은 위 CLI에서 생성한다.

```yaml
policy_evidence:
  parameter: required_checks
  records:
    - item_id: EXAMPLE-enterprise-check
      case: failed
      observations:
        trial_id: null
        target_scope: null
        check_outcome: null
        gate_outcome: null
        exception_assessment: null
        transition_detail: null
      evidence:
        - attachment: EXAMPLE-log.txt
          locator: null
          basis: null
      limitation: null
```

attachment는 같은 entry.attachments에 기재한 실제 경로여야 한다. import는 파일을 읽고 sha256으로 정규화한다. locator는 파일 내 사건 위치, basis는 그 사건과 주장 간 연결의 설명이다. 보고서에는 item/case·이유 코드·첨부 해시 식별자만 전달하고 원본 경로·basis·locator 본문을 복사하지 않는다. 해시는 파일 동일성 근거이며 신고 내용·입력자·승인 진위를 인증하지 않는다.

| 대상 | case와 관측 |
|---|---|
| SUP-14 | normal/failed/error/unsupported/skipped. check_outcome: succeeded/failed/error/unsupported/skipped/unknown; gate_outcome: advanced/blocked/unknown; exception_assessment: none/reported/unknown; transition_detail: 근거 설명 |
| LIF-07 | not_ready/ready. item_state: ready/not_ready/unknown; request_outcome: accepted/blocked/not_attempted/unknown; inference_outcome: succeeded/failed/not_attempted/unknown; order_basis: same_clock/independent_order/unknown; order_detail: 순서 근거 |
| 공통 | trial_id·target_scope는 실제 시험·범위의 신고 식별자. 관측 enum은 결과 분류 어휘로 기업 기본값이 아님. 각 필드는 null/생략이면 미확인, 제공한 잘못된 타입/enum은 오류 |

SUP-14는 각 항목×case의 실제 원결과와 관문을 연결한다. 실패 주입을 차단했으면 원검사 실패를 SUP-14 FAIL로 복사하지 않는다. 필수 미완료를 실제 통과시켰고 해당 예외 없음이 근거와 함께 신고됐으면 security FAIL이다. 예외 신고만으로 원검사 성공/PASS로 바꾸지 않는다. 정상 성공 묶음은 동일 trial/대상이어야 한다. functional/performance PASS는 이 계약에서 UNKNOWN이다.

LIF-07의 item_state는 **요청 접수/차단 당시**의 준비 상태를 뜻한다. order_basis와 order_detail은 그 순서를 뒷받침한 자기 신고이며 원본 시각을 자동 파싱한 결과가 아니다. health/listener만으로 필수 rank/model 준비·실제 접수를 입증하지 못한다. 정상 ready 대조는 필수 항목 모두 동일 trial/대상에서 준비 후 접수·추론 성공을 보여야 한다. 미준비 접수가 확인되면 보안 FAIL이며, 추론 관측이 없더라도 그 위반을 지우지 않는다. 기능 FAIL은 같은 정상 ready 묶음의 접수/준비가 확인된 기능 실패에 한정한다. 시계 순서가 unknown이면 관련 PASS/위반 확정을 보류한다. performance는 UNKNOWN이다.

**오류/미확인:** 다른 PLAN/POL/팩 revision/hash, 모드에 맞지 않는 side, 비선정 check, 미등록 item, 중복 item/case, 잘못된 타입·필드·첨부 소속은 배치 전체 오류다. 누락 case·빈 evidence·관측 null/unknown·locator/basis 부족·미확인 한계는 저장하되 PASS를 UNKNOWN으로 보류한다. 선언한 파일의 부재/읽기 실패는 기존처럼 오류다. 같은 사건의 모순은 해당 사건을 미확인으로 처리하고 별개 사건의 위반은 유지한다.

**빈 정책과 호환:** select의 기존 타입 검증은 유지한다. []·공백/중복 식별자가 있는 정책 집합은 신규 항목 PASS를 보류하며 기업 정책 자체의 FAIL/해당 없음으로 바꾸지 않는다. /1 입력은 읽고 과거 EVD/RES/해시는 수정하지 않는다. 해당 정책 선언이 있는 정의의 구형 증거에는 새 항목 근거가 없으므로 재판정 PASS가 UNKNOWN으로 달라질 수 있다. 선언 없는 과거 정의·다른 점검은 기존 계약이다. 같은 입력/as_of 재판정은 멱등이며 새 관측은 새 실행 config hash에 포함한다.

**한계:** 이 기능은 DECLARED 자료의 연결·충족 조건 검사다. 첨부 진위·승인 인증·조건부/역할별 항목·예외 충분성·기업 실제 목록·빈 목록 유효성·기한/성능 수치는 확정하지 않는다. 실제 엔진/두 이미지 실증과 정책 후보 추가 등록은 별도다. POL/팩 DRAFT·EXAMPLE과 미인증 자기 신고 문구 및 기존 official 계산을 유지한다.

## M5 승인 기록 체계와 충돌 처리

values.SUP-01.approval_record_system과 values.SUP-02.approval_record_system은 각각 required str다. 기업이 사용할 승인 도구·기록 체계의 이름/참조를 입력한다. 시스템 비밀값·인증정보·권한표를 넣는 필드가 아니다. 두 값의 자동 복사나 동일성 요구는 없으며 양식은 둘 다 {}로 비워 둔다.

누락 키는 NO_POLICY_VALUE이며 null/타입 오류는 select가 거부한다. 빈/공백 문자열은 str 타입을 만족하지만 declared 0.4.0의 대상 수동 PASS를 UNKNOWN으로 보류한다. POLICY_CONTEXT 이유와 정책 치환 한계 문장을 JSON/HTML에 표시한다. 정상 문자열은 정책 문맥만 채운 것이며 실제 권한·변경/배포 결과·감사 기록의 별도 증거 및 기존 PASS 관문이 필요하다. 신고 FAIL·UNKNOWN 비승격을 유지한다. 해당 required str 선언이 없는 과거 팩에는 새 문맥 전제를 요구하지 않는다.

수동 증거 /1 입력과 /2 생성은 그대로다. 승인 체계는 항목 목록/case가 아니며 policy_evidence를 만들지 않는다. manual-entry 0.3.0, policy-evidence/1은 유지한다. declared만 0.4.0이다.

M4의 “같은 사건의 모순”은 M5에서 판정 근거별로 구분한다. LIF-07 추론 결과 충돌은 독립적으로 확인된 준비 전 수락 FAIL을 지우지 않는다. 수락/차단 모순은 해당 위반 확정을 보류하고, 순서 미확인은 FAIL을 만들지 않는다. 모순이 있는 사건의 PASS 보류와 별개 사건의 FAIL 보존은 유지한다. 변경 원인·OBS-11 선행 결정은 [M5 계획](POLICY_M5_PLAN.md)을 따른다.

## M7 OBS-11 정책·수동 /3

OBS-11은 required_records:list[str]와 사건별 collection_deadline_ms/max_clock_uncertainty_ms/detection_deadline_ms/alert_deadline_ms:dict[str,int] 네 map을 선언한다. 의미·숫자 범위·case별 관측·조회 창·경보 조건은 [M7 계약](POLICY_M7.md)을 따른다. 실제 기업 숫자나 예외를 기본값으로 채우지 않으며 양식은 OBS-11:{}다. 신규 map 타입은 문자열 키·정수 값만 허용하고 bool은 정수가 아니다. 부분/빈 정책의 의미 부족은 전용 판정에서 UNKNOWN으로 남긴다.

새 수동 양식은 /3, manual-entry0.4.0, declared0.5.0이다. /2는 정확 POL/팩 Ref와 기존 SUP/LIF policy_evidence를 계속 검증하며 /1·/2의 audit_evidence는 거부한다. 기존 M4/M5 절은 당시 도입 계약이며 /3의 OBS 확장은 M7 계약을 따른다. 새 OBS 관측은 guard-audit-evidence/1로 별도 보존하며 기존 guard-policy-evidence/1은 유지한다. M7 당시 팩13/DRAFT이며 OBS-11은 수동 점검이다. 자동 실행기 연결5개·official 계산·기존 축/실측 범위는 확대하지 않는다.

## M8 usage·replay bool 문맥

현재 팩14/DRAFT의 OBS04/05 usage_allowed와 OBS07/08 replay_allowed는 required bool이다.
네 양식은 {}이며 기업 기본값은 생성하지 않는다. null/0/1/문자열은 bool로 바꾸지 않고 false는 누락이 아니다.
OBS04 false, 나머지 true가 검토 문맥이고 다른 값/키 누락은 신고 PASS를 UNKNOWN으로 보류한다.
활성 문맥에도 기존 수동 관측·첨부·INV11이 필요하며 FAIL·UNKNOWN을 보존한다.
정책값만으로 실제 비사용·부재·승인·권한·NOT_APPLICABLE을 만들지 않는다.

declared0.6.0, manual-entry0.4.0·수동/3 및 /1·/2 읽기는 유지한다. 새 항목 payload는 없다.
분기 표·공유 정책의 점검별 범위/일관성 한계·구형 정의 계약은 [M8 문서](POLICY_M8.md)를 따른다.
기업이 실제 값·승인·사용 범위와 증거를 준비해야 하며 자동 실행기 연결5개는 그대로다.

## M10 REQ-09 endpoint·형식

두 required 목록은 실제 대상 인터페이스의 전체 승인 기준이며 각각 단일 원소만 지원한다. endpoint는 CheckDef 정상 engine POST의 METHOD path와 정확히 같고 형식은 token_ids다. 기업 정책을 줄여 맞추지 않는다. 빈 양식 REQ-09:{}는 유지하며 값 누락은 NOT_RUN, 잘못된 타입은 선정 오류, 빈/복수/미지원/표본 불일치는 요청 전 ABORTED다. 기존 수치·거부 코드·vocab 출처·예산도 필요하다.

수동 /3은 `guard evidence template --include-automated`와 비공백 override_reason을 사용한다. input_endpoints_tested·input_formats_tested는 실제 시험 범위의 generic 관측이며 두 목록 전체와 일치해야 PASS 후보가 된다. 수동 신고는 DECLARED다. /1·/2 읽기와 기존 참조/side 보호를 보존한다. [M10](POLICY_M10.md)의 검증과 한계를 따른다.

## M11 REQ-09 수동 PASS와 관측의 일관성

새 M10 계약의 REQ-09는 수동 신고 PASS라도 고정된 판정표가 같은 보안/기능 축에서 PASS 후보를 인정해야 유지한다. accepted_challenges에 수락된 경계가 있거나 정상 결과 fail 등 상충이 있으면 해당 신고 PASS를 UNKNOWN으로 보류한다. 관측/규칙 부족·평가 오류도 보류하며 신고 FAIL/UNKNOWN과 성능 처리는 보존한다. /3·/1/2 읽기·자동대체 이유·Ref/side·기존 hash 입력은 그대로다. [M11 계약/검증과 캐시 설계](POLICY_M11.md)를 참고한다. 캐시 정책은 아직 양식에 등록하지 않는다.
