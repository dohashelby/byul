# M11 수동 PASS 일관성 보강과 캐시 정책 설계

## 1. 상태와 기준

기준은 [M10](POLICY_M10.md)의 `codex/gua-policy-input-m10` / `5c0db825e98709c39d1d31e7b669404a71a172ba`, [초안 PR12](https://github.com/dohashelby/gua/pull/12)다. M11 브랜치는 `codex/gua-policy-m11`이며 M10 미병합 상태에서 base를 M10으로 둔다. 정본은 [SPEC](../SPEC.md)이다.

**제품 구현:** REQ-09의 새 M10 계약에서 수동 보안/기능 PASS와 기존 판정표의 일관성만 보강한다. **캐시 부분은 설계 제안:** 새 정책/관측/실행기 등록이나 M12 실행은 하지 않는다. 기업 실제값·승인·시험 대상도 미정이다.

## 2. M10 검토 사실과 반례

M10 최종 CI는 [push](https://github.com/dohashelby/gua/actions/runs/39762313775)·[PR](https://github.com/dohashelby/gua/actions/runs/39762331777) 모두949통과/18제외·wheel 성공,54.27초/88.13초였다. Windows 최종944통과/5skip/18제외·206.02초, 설치21명령, 이후 A 재검토99통과/42.11초는 **과거 결과**다. M11에서 새로 실행한 수로 합산하지 않는다. Linux1분 목표는 일관되게 충족했다고 판정하지 않았다.

기존 [declared](https://github.com/dohashelby/gua/blob/5c0db825e98709c39d1d31e7b669404a71a172ba/src/guard/judges/declared.py#L82)는 범위·첨부·답변을 검사했지만 [rule_table의 실패/성공 조건](https://github.com/dohashelby/gua/blob/5c0db825e98709c39d1d31e7b669404a71a172ba/src/guard/judges/rule_table.py#L169)을 사용하지 않았다. 기존 [M10 회귀](../tests/test_policy_m10.py)는 신고 FAIL을 직접 넣어 보존을 확인했으므로 다음 상충 PASS는 빠져 있었다. 이 동작이 M10에서 최초 도입됐는지는 단정하지 않는다.

| 합성 수동 입력 | M10 판정표 | M10 수동 결과 | M11 기대 |
|---|---|---|---|
| 유효한 정책/범위·첨부·답변·정상/챌린지, 신고 PASS; accepted_challenges=[token_id], challenge_outcomes.token_id=200 | 보안 FAIL | 보안 PASS | 보안 UNKNOWN, 기능 독립 유지 |
| 같은 전제, functional_probe_results.after_token_id=fail | 기능 FAIL | 기능 PASS | 기능 UNKNOWN, 보안 독립 유지 |

A는 별도 임시 저장소 `gua-m11-review-ydgn_t97`의 실제 양식→import→judge→report에서도 재현하고 Store.verify 오류0·official=false를 확인했다. EXAMPLE/DECLARED 합성 계약이며 실제 엔진 취약점이나 공식 실증이 아니다. M11은 아래 검증 단계에서 직접 재현/수정 후 결과를 따로 기록한다.

## 3. 채택한 REQ-09 계약과 최소 변경

사용자 실행 지시로 `POL-M11-MANUAL-CEILING`을 채택한다. 새 M10 계약의 범위 전제가 유효한 경우 저장된 EVD를 CheckDef의 고정 rule_table로 순수 평가한다. EVD.default_judge를 따라 판정 코어를 재호출하지 않는다.

| 신고 축 값 | 판정표의 같은 보안/기능 축 | M11 수동 값 |
|---|---|---|
| PASS | PASS | 기존 관문을 통과할 때만 PASS 후보 |
| PASS | FAIL | 상충 관측으로 UNKNOWN |
| PASS | UNKNOWN·규칙 없음·미지원·평가 예외 | 근거 부족으로 UNKNOWN |
| FAIL | 어떤 결과/오류 | FAIL 보존 |
| UNKNOWN | 어떤 결과/오류 | UNKNOWN 유지 |

자동 판정표의 FAIL을 수동 FAIL로 자동 복사하지 않는다. 수동 성능 축은 이 보완에서 제외하고 기존 명시 CPU 성능 UNKNOWN을 보존한다. 범위 불충분 시 모든 신고 PASS를 보류하는 기존 input_policy·INV11·첨부·답변 관문도 유지한다. 모든 새 이름이 없는 과거 정의는 기존 계약, 부분 선언은 기존처럼 보류한다.

실행 로직 변경은 [declared](../src/guard/judges/declared.py) 하나, 버전0.8.0이다. 실패 사유는 안전한 `POLICY_CONTEXT: REQ09_MANUAL_OBSERVATION_CONFLICT` 또는 `RULE_UNRESOLVED`로 남기고 의존 예외 원문/관측 전체는 복사하지 않는다. 기존 보고 전달과 escape를 재사용한다. core/models/store/adapter/실행기/HTTP 골격·rule_table0.5.0·input_validation0.3.0·manual-entry0.5.0·양식/3와/1/2 읽기는 그대로다. 패키지 설명 metadata에는 해당 두 경로의 소비 버전/상한 설명만 갱신했다.

EVD/RES 원본·Ref·side·기존 config hash 입력을 바꾸지 않는다. 응답 관측은 EVD content hash에 포함되며 실행 조건으로 재분류하지 않는다. M10의 상충 EVD는 그대로 두고 새 declared0.8.0 RES에서 PASS→UNKNOWN을 기록한다. 정상 EVD는 기존 축을 보존하고 같은 as_of 재판정은 멱등이다.

팩15/DRAFT·REQ09정의5·해시 `19fa4f3fd0c2348f8ad5384bb32eac74071c02bb342d6818b1c7205dfe12da63`,77점검·14정책점검26경로·a23/b4/c161/d115·303결정595연결·자동연결5를 유지한다. 입력 두 카탈로그 행과 설명 metadata는 현재 소비 버전만 갱신한다.

## 4. 캐시 후보의 원문과 구현 경계 — 미구현 제안

| 후보/원문 | 보호 요구 | 현재 상태/설계 범위 |
|---|---|---|
| P-DRAFT-CACHE-PUBLIC-PREFIX, REQ15/16/17 | 공개 prefix 공유 허용과 신뢰 공유 집단, 비공개 혼입 금지 | b/bool 미등록. 제안 키 public_prefix_sharing_allowed. 첫 구현 등록은 후속 |
| P-DRAFT-CACHE-CONTEXT, REQ15/16/17 | 실제 재사용을 구분하는 필수 문맥 | b/list[str] 미등록. 제안 키 required_cache_contexts. M12 권고는 REQ16 한정 |
| [REQ15 원문](../reference/framework/data/checklist.json#/request_state_assessment_draft/14) / [정의](../packs/PACK-BASELINE/checks/REQ-15.yaml) | 신뢰 신원·salt 발급/덮어쓰기·hit 집단 귀속 | revision1, 정책/실행기/판정/구조화 관측 없음. salt 존재만으로 격리 확정 금지 |
| [REQ16 원문](../reference/framework/data/checklist.json#/request_state_assessment_draft/15) / [정의](../packs/PACK-BASELINE/checks/REQ-16.yaml) | 실제 key·객체 변경·hit/출력/승인 세대 | revision1·미등록. key 문자열 변경만으로 재사용 효과를 확정하지 않음 |
| [REQ17 원문](../reference/framework/data/checklist.json#/request_state_assessment_draft/16) / [정의](../packs/PACK-BASELINE/checks/REQ-17.yaml) | 비공개 정보·독자·반복/잡음·채택 통계 기준 | revision1·미등록. 수치/반복·실측 설계가 선행하므로 M12 제외 |

남은 다른 b는 SUP-CODE-MODES(SUP08~11)와 SUP-DERIVED-LOCATIONS(SUP15/16)다. 전자는 실제 loader/import·모드별 승인/도달, 후자는 경로 namespace/권한·파생 세대/완결성이 필요하다. 단순 list로 소비 의미가 해결되지 않으므로 캐시 제한 계약 뒤 각각 별도 설계한다. 실제 파일 경로 prefix 일치나 모드 이름만으로 보호를 판정하지 않는다.

소유권: POL은 전체 승인 기준, ENV는 실제 구성(등록 facts prefix_cache/prefix_cache_or_shared_cache 및 고정 model), 실행 설정은 격리·예산·대상, EVD는 실제 관측의 신고/첨부, RES/RPT는 그 범위의 순수 판정이다. 캐시의 실제 활성 key/소비 객체·신원 귀속은 현재 고정 ENV에 없다. 새 ENV 기본값을 만들지 않는다. 카탈로그의 원문 정책 승인 역할은 실제 담당자 지정/승인 증명이 아니다.

### 4.1 공개 공유 bool의 의미

| 제안 정책 | 계속 필요한 검토 | 금지된 추론 |
|---|---|---|
| false | 같은 허용 집단의 정상 캐시 재사용, 집단 간 공유 제한의 실제 효과 | 기능 비활성·비적용·항상 UNKNOWN으로 자동 처리하지 않음 |
| true | 공개 범위/분류·허용 집단/경로 근거와 비공개 분리 | 비공개 prefix·자격·출력·다른 사용자의 재사용까지 허용하지 않음 |
| 누락/잘못된 타입 | 정책 미결과 관측 부족을 구분 | 실제 상태/승인을 추측하거나 기본값 주입하지 않음 |

M8의 bool 분기 패턴을 복사하지 않는다. true/false 둘 다 정책값일 뿐 PASS 증거가 아니다. 단일 서비스 키 뒤 사용자 신원 전달·검증·집단 배정/강제 salt 덮어쓰기와 cache consumer까지의 귀속이 별도 필요하다. 클라이언트가 준 salt를 신뢰 신원으로 사용하지 않는다.

### 4.2 REQ16의 첫 프로파일과 문맥 목록

M12 권고는 **same_authenticated_group_private**라는 기술 프로파일의 제한적 수동 계약이다. 같은 검증된 공유 집단·같은 입력/workload에서 한 번에 한 필수 문맥만 바꾼다. 공개 prefix/집단 간 공유 허용 여부는 이 프로파일의 판정에 사용하지 않으므로 PUBLIC-PREFIX를 REQ16에 등록하지 않는다. 공개/교차 집단·신원 귀속 자체의 보호 검증은 REQ15 후속이며, 이 프로파일 밖 관측으로 PASS시키지 않는다.

제안 `values.REQ-16.required_cache_contexts`는 required list[str]다. 초기 기술 식별자는 model_generation·adapter_content·media_identity의 비어 있지 않은 고유 부분집합이다. 실제 기업이 요구하는 전체 목록이며 기본값/자동 선정 목록이 아니다. 공백·중복·미지원·임의 alias는 자동 정규화하지 않고 PASS를 보류한다. 복수 문맥도 각 문맥별 증거가 전부 필요하며 목록을 지원 항목만으로 자르지 않는다. 순서는 판정 커버리지에는 의미 없지만 저장된 POL/해시를 정렬해 다시 쓰지 않는다. raw hash 충돌 가능성 자체는 실패가 아니다.

| 식별자(기술 제안) | 실제로 바뀌었다고 신고해야 할 것 | 충분하지 않은 것 |
|---|---|---|
| model_generation | 승인된 모델/배포 세대와 실제 소비 객체의 전후 대응 | 모델 이름/라벨만 변경 |
| adapter_content | 활성 adapter의 실제 내용과 consumer의 전후 대응 | adapter 이름/경로만 변경 |
| media_identity | 활성 media의 실제 식별/소비 문맥과 전후 대응 | URI/선언 hash 필드만 비교 |

각 식별자의 실제 필드/수집 근거가 없으면 UNKNOWN이다. 비활성 기능을 지원한다고 만들거나 기업 목록에서 지워 맞추지 않는다. cache key의 필수 문맥 이름이 들어 있다는 것과 소비자가 실제로 그 객체를 구분했다는 것을 분리한다.

### 4.3 제안 증거 계약과 전수 커버리지

M12의 제안 관측은 `declared_cache_context_evidence` 한 개이며 값은 `guard-cache-context-evidence/1` 구조다. **새 제품 스키마/등록이 아니라 설계 표기**다. 수동 /3 generic required_observations 값 안에 저장하고 판정 시 순수 validator로 검사하는 안을 권고한다.

상위 필드:
- version: 위 계약 식별자
- profile: same_authenticated_group_private
- sharing_group_ref, identity_basis: 비밀값 없는 집단 식별과 신뢰 신원 귀속의 신고/첨부 근거
- records: 각 필수 context_id × same_context/changed_context/post_control의 완전한 행

각 행 필드(정확한 이름으로 M12 구현):
- context_id, case, trial_id, event_id, target_scope, workload_ref
- context_before, context_after: 전후 소비 문맥의 비밀값 없는 불변 식별자
- object_change: unchanged/observed/unknown
- other_conditions: same/changed/unknown
- key_relation: same/different/unknown
- cache_effect: before_context_hit/isolated_after_change/after_context_hit/old_context_reused/unknown
- inference: succeeded/failed/not_attempted/unknown
- observation_detail: 실제 객체·hit 귀속·출력/순서·재생성/재사용 효과의 설명
- evidence: sha256/locator/basis의 첨부 연결 배열

수동 양식에서는 evidence의 attachment/locator/basis를 받을 수 있는 별도 payload를 추가하지 않는다. /3 generic 값의 evidence.sha256는 해당 entry 첨부 bytes의 SHA-256을 신고한다. 수집 파일 경로를 payload에서 열거나 외부 URL을 가져오지 않는다. 파일 hash는 무결성일 뿐 진위/승인 인증이 아니다. 사람은 generic 빈 관측에 이 구조를 입력하고 첨부 hash를 계산한다. 사용성 개선용 자동 행 생성·파일명→hash 변환·별도 payload/양식 개정은 후속 설계로 둔다.

validator는 top/row/reference 필드·타입·enum·빈/공백·중복(context_id,case)·foreign context·첨부 hash 귀속을 검사한다. 같은 trial/target/workload/집단과 일관된 전후 쌍을 요구한다. case마다 event_id는 달라야 하고 사건 순서는 설명/첨부 locator로 신고한다. 임의 timestamp로 순서를 만들어내지 않는다. 같은 event_id의 모순·다른 집단/입력·다중 조건 변경은 전제 불충분이다. 누락이나 invalid는 판정 UNKNOWN 사유로 처리하고 기존 generic import 계약을 전역 변경하지 않는다.

| case | 제한 프로파일에서 필요한 신고 |
|---|---|
| same_context | 전후 식별자 동일, object_change=unchanged, other_conditions=same, key_relation=same, before_context_hit, 추론 succeeded |
| changed_context | 전후 식별자 다름, object_change=observed, other_conditions=same, key_relation=different, isolated_after_change, 추론 succeeded; 실제 객체 변경·옛 문맥 미재사용의 첨부 근거 |
| post_control | 전후 쌍/세대가 앞 행과 같고 변경 후 문맥의 after_context_hit·추론 succeeded, other_conditions=same, key_relation=different; cache 전체 비활성으로 생긴 miss를 성공으로 숨기지 않음 |

key_relation은 같은 trial의 변경 전/후 소비 key 비교를 뜻한다. post_control의 different는 바로 직전 요청과 다른 key라는 뜻이 아니라 옛 문맥 key와 새 문맥 key의 차이다. post_control의 재사용은 새 문맥 key에서 확인한다. 각 행 context_before/after는 비교 쌍이며 same_context만 옛 식별자 둘, 나머지 두 행은 같은 옛/새 쌍이다.

단순 miss/시간 차만으로 isolated_after_change를 충족시키지 않는다. 실제 consumer와 entry 귀속·출력/재생성 근거가 있어야 한다. 상세 설명/첨부는 DECLARED 검토 근거이며 자동 parser/파일내용 진위 인증을 뜻하지 않는다. 교차 context 순서를 임의로 합치거나 같은 event를 다른 case로 재활용하지 않는다.

### 4.4 M12 축별 판정 제안

M11과 같은 **신고 PASS 상한**을 권고한다. 새 REQ16 정책/관측의 완전한 선언에만 적용하고 과거 정의에는 소급하지 않는다. 부분 선언은 PASS를 보류한다.

| 축 | PASS 후보 | FAIL | UNKNOWN |
|---|---|---|---|
| 보안 | 신고 PASS, 전체 정책 항목·3case·프로파일·실제 객체/귀속 신고·첨부가 완결되고 허용된 효과 | 신고 FAIL 유지 | 신고 UNKNOWN 유지, 또는 신고 PASS에서 old_context_reused/상충/누락/미지원/무관측이 있음 |
| 기능 | 신고 PASS, 모든 행의 추론 성공·정상/변경후 대조·증거 완결 | 신고 FAIL 유지 | 신고 UNKNOWN 유지 또는 실패/누락으로 PASS 보류 |
| 성능 | 새 판정 기준 없음 | 기존 신고 FAIL/공통 CPU 처리 유지 | 신규 REQ16 프로파일의 신고 PASS는 성능 기준 부재로 UNKNOWN; 실제 수치 측정/허용값을 만들지 않음 |

M12는 순수 helper의 상한을 declared에 적용하는 수동 계약이다. 신고 PASS를 새로운 자동 FAIL로 바꾸지 않는다. `old_context_reused` 행은 `CACHE_CONTEXT_CONTRADICTION` 등 안전한 사유로 드러내고, 실제 자동 FAIL을 원하면 독립 관측 신뢰/FAIL 전제에 대한 별도 설계를 승인받는다. 다른 문맥/후속 증거 부족이 이미 신고된 FAIL을 지우면 안 된다. 관측 집합 invalid로 평가가 불가해도 신고 FAIL은 보존한다. 기존 첨부·답변·normal/challenge·INV11은 계속 필요하다.

REQ17은 반복·분포·metrics 독자·비공개 정보의 기업 기준이 없어 UNKNOWN 범위를 기록할 뿐 자동화하지 않는다. 단순 cached_tokens나 지연 차로 안전/유출을 확정하지 않는다.

### 4.5 EXAMPLE 구조와 반례 — 미실행

아래는 전체 기업 POL이 아닌 **합성 관측 구조 예시**다. a 반복 SHA도 가짜이며 실제 입력은 그 entry 첨부 bytes의 hash로 바꿔야 한다. 실제 cache/인증/승인이나 자료 진위를 검증한 결과가 아니다.

```yaml
version: guard-cache-context-evidence/1
profile: same_authenticated_group_private
sharing_group_ref: EXAMPLE-group
identity_basis: EXAMPLE-self-reported-auth-mapping-with-attachment-locator
records:
  - context_id: model_generation
    case: same_context
    trial_id: EXAMPLE-trial
    event_id: EXAMPLE-event-1
    target_scope: EXAMPLE-model-worker
    workload_ref: EXAMPLE-private-fixture
    context_before: EXAMPLE-g1
    context_after: EXAMPLE-g1
    object_change: unchanged
    other_conditions: same
    key_relation: same
    cache_effect: before_context_hit
    inference: succeeded
    observation_detail: EXAMPLE-consumer-object-and-cache-entry-before-change
    evidence: [{sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, locator: L1-L4, basis: EXAMPLE-synthetic-link}]
  - context_id: model_generation
    case: changed_context
    trial_id: EXAMPLE-trial
    event_id: EXAMPLE-event-2
    target_scope: EXAMPLE-model-worker
    workload_ref: EXAMPLE-private-fixture
    context_before: EXAMPLE-g1
    context_after: EXAMPLE-g2
    object_change: observed
    other_conditions: same
    key_relation: different
    cache_effect: isolated_after_change
    inference: succeeded
    observation_detail: EXAMPLE-changed-consumer-object-new-entry-and-no-old-generation-reuse
    evidence: [{sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, locator: L5-L8, basis: EXAMPLE-synthetic-link}]
  - context_id: model_generation
    case: post_control
    trial_id: EXAMPLE-trial
    event_id: EXAMPLE-event-3
    target_scope: EXAMPLE-model-worker
    workload_ref: EXAMPLE-private-fixture
    context_before: EXAMPLE-g1
    context_after: EXAMPLE-g2
    object_change: observed
    other_conditions: same
    key_relation: different
    cache_effect: after_context_hit
    inference: succeeded
    observation_detail: EXAMPLE-repeat-new-generation-inference-and-cache-reuse
    evidence: [{sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, locator: L9-L12, basis: EXAMPLE-synthetic-link}]
```

| 설계 ID | 반례/조건 | M12 기대(아직 미실행) |
|---|---|---|
| C01 | 정책 키 누락 | 기존 NO_POLICY_VALUE, NO_EXECUTOR와 구분 |
| C02 | bool/null/혼합 list 등 잘못된 타입 | 기존 select 타입 거부, 범위 밖 제공값도 검사 |
| C03 | []/공백/중복/미지원 context | 신고 PASS 보류, 부분 선택 금지 |
| C04 | PUBLIC-PREFIX=false | 실제 캐시 비활성/비적용으로 만들지 않음 |
| C05 | true와 비공개 집단 간 재사용 | 공개 허용을 확대하지 않음; REQ15/17 보호 완료로 표시 금지 |
| C06 | 클라이언트 salt 또는 같은 서비스 키만 있음 | 신뢰 신원 귀속/같은 집단을 확정하지 않음 |
| C07 | model/adapter 이름·URI만 바뀜 | 실제 소비 객체 변경 미확인, PASS 보류 |
| C08 | key만 달라지고 실제 hit 귀속 없음 | PASS 보류 |
| C09 | changed 후 miss만 있고 post_control 없음 | cache 비활성/실패 가능성, PASS 보류 |
| C10 | old_context_reused+신고 PASS/FAIL/UNKNOWN | 각각 UNKNOWN/FAIL/UNKNOWN |
| C11 | 같은 event_id의 모순/foreign 문맥/다른 집단·입력 | invalid/전제 부족, 신고 PASS 보류·FAIL 보존 |
| C12 | 다른 조건도 함께 바뀜/실제 순서 근거 없음 | 원인/선후 불명, PASS 보류 |
| C13 | 외부/다른entry 첨부 hash·미연결 locator/basis | invalid/미완결, 외부 파일/URL 접근 없음 |
| C14 | 한 문맥만 완결, 다른 필수 문맥 미수집 | 전체 PASS 보류; 목록을 줄이지 않음 |
| C15 | 지연·cached_tokens·hash 충돌 가능성만 관측 | REQ17 안전/유출 확정 금지 |
| C16 | 성능 신고 PASS·비교값 없음/CPU | 신규 프로파일 성능 UNKNOWN·비교 불가/기존 CPU 처리 |

### 4.6 대안·변경 위치·업무 결정

| 선택 | 판단 |
|---|---|
| 목록/bool만 등록 | 무시되는 값과 허위 커버리지 위험 때문에 권고하지 않음 |
| /3 generic 관측 + REQ16 전용 순수 validator/상한 | 첫 구현 권고. core/models/store/실행기/수동 양식 버전 변경 불필요 |
| 기존 SUP policy_evidence 또는 OBS audit_evidence 확장 | 의미와 전용 케이스가 달라 억지 재사용하지 않음 |
| 자동 행 생성·새 파일명 첨부 payload·/4 | 실제 사용성 근거와 호환 설계가 생긴 후 검토 |
| 실캐시 probe·통계·신원 enforcement 자동화 | 대상/소비 관측·정상/금지 시험 권한·예산/중단조건이 선행 |

M12 예상 위치는 REQ16 override→팩/manifest, 새 작은 `manual_cache_context.py`(검사 전용, I/O 없음), declared 호출, metadata/catalog·문서·회귀다. 수동 collector는 generic을 이미 저장하므로 변경하지 않는 안이다. hash 입력에 응답 전체를 넣지 않고 EVD hash·정확한 POL/PLAN/팩 Ref·side를 보존한다. REQ16 새 definition과 declared 버전은 실제 채택 후 올린다. 다른 점검/양식·기존 의미는 유지한다.

미정 업무 질문: 어느 서비스/hop/공유 집단과 인증 근거인가? 같은 서비스 자격 뒤의 사용자를 어떻게 구별하는가? 전체 필수 문맥·실제 consumer/객체/승인 세대와 공개 범위는 무엇인가? 실제 첨부를 누가 수집/검토하는가? 금지 재사용 시험의 대상·권한·예산·정상 대조·중단 조건과 metrics 독자는 누구인가? 원문 정책 승인 역할과 플랫폼/서비스/보안 담당은 제안 역할일 뿐 실제 지정자가 아니다.

## 5. M11 직접 검증

구현·테스트・문서 초안을 모두 마친 뒤 실행했다. 아래는 이번 M11의 직접 결과이며 과거 M10/A 재검토 결과와 합산하지 않는다. 환경은 Windows/CPython3.11.16/uv0.12.11이다. 새 증거는 `out/test-runs/policy-m11-20261022/`에 보존한다.

| 직접 실행 명령/단계 | 결과 |
|---|---|
| `uv sync --frozen` | 19패키지 확인, lock/의존성 변경 없음 |
| `uv run pytest -q --durations=20 --basetemp=.review-tests-policy-m11-20261022 --junitxml=out/test-runs/policy-m11-20261022/full-first.xml` | **993통과/실패0/5skip/18제외,210.14초**. 신규 [M11 회귀](../tests/test_policy_m11.py)49사례 포함. 최초 전체 실행에서 통과하여 불필요한 전체 반복 실행은 하지 않았다. |
| `uv build --wheel --out-dir out/test-runs/policy-m11-20261022/dist` | wheel 성공 |
| `uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file out/test-runs/policy-m11-20261022/requirements.txt` → 별도 venv의 `uv pip sync --python <설치본Python> <requirements.txt>` → `uv pip install --no-deps --python <설치본Python> <wheel>` | frozen 런타임14의존성+wheel 별도 설치 성공 |
| `python -X utf8 -B out/test-runs/policy-m11-20261022/installed_smoke.py` | **29명령 exit0**. 저장소 밖 `%TEMP%/gua-policy-m11-installed-eps9dk1z`에서 -I Python·실제 guard 진입점 사용, PYTHONPATH/PYTHONHOME 차단 |
| `python -X utf8 -B out/test-runs/policy-m11-20261022/verify.py` | 팩79파일·기존out61·참고원문21·코어8·모델7·Store4·어댑터5·실행기11파일 바이트 보존, 기업values/양식/hash 입력 보존, metadata 일치·두 catalog행만 변경. 상대링크42·JSON Pointer3·설계 반례16·EXAMPLE3행·M12 미실행 확인 |

설치 호환 검증은 실제 보존 M10 wheel의 declared0.7.0 소스 바이트를 고정 HEAD와 대조하고 정확한 팩15 해시를 확인한 뒤, **이번 검증에서** 정상 자동/수동 및 두 상충 합성 증거를 생성했다. M10의 실제 import/judge에서 상충 PASS를 재현하고 M11 wheel의 declared0.8.0 소스도 대조했다. 새 CLI judge는 정상 자동/수동 축을 유지하고 두 상충의 해당 축만 UNKNOWN으로 변경했다. 자동 FAIL 보존은 별도 신규 회귀에서 확인했다. 과거 EVD/RES 원본·해시·수동 config hash와 같은 as_of 멱등을 검증했다. 새 설치본의 생성/import·정책 양식·JSON/HTML에서 source=manual·official=false와 안전한 상충 사유도 확인했다. 캐시 실행·실제 엔진 관측은 아니다.

기본 전체의 skip5는 기존 Windows 루프백 거부/timeout 차이3·symlink 권한2, 제외18은 기존 engine/docker marker다. 새 skip/제외를 추가하지 않았다. Windows1분 목표는 미달이다. durations 상위는 기존 pack snapshot4.86초, 수동CLI4.46초, snapshot재판정3.77초 등이며 신규49사례의 JUnit 누적 시간은8.007초였다. 서로 다른 환경이나 과거 실행과의 시간 차이를 제품 성능 변화로 단정하지 않는다. CI에는 `--durations=20` 출력만 추가해 지원 Linux의 느린 구간을 확인한다. 최종 SHA의 Linux 결과·실행 시간·원격/base/diff는 초안 PR에 기록한다.

이번 검증에서 실패/추가 수정은 없었고 판정표 기준·불변식을 완화하지 않았다. 순수 로직/합성 probe/DECLARED 계약과 실제 엔진/두 실제 이미지 검증을 구분한다. 후자·Docker stub·운영/유료 자원 시험은 실행하지 않았다. M12 설계 사례 C01~16은 현재 실행한 제품 테스트 수에 넣지 않는다.

## 6. M12 구현 담당 B에게 전달할 프롬프트 — 미실행

```text
너는 gua 정책 개발 담당 B다. 저장소는 https://github.com/dohashelby/gua.git 이다.
M11 최신 HEAD와 docs/POLICY_M11.md의 캐시 설계/판정 표를 읽고 REQ-16의 제한적 수동 계약을 구현하라.
이 실행 요청은 아래 기술 프로파일을 채택하지만 기업 실제값·담당자·승인·실제 엔진 시험을 승인하지 않는다.

1. 작업 지침·SPEC·PROGRESS·DECISIONS·POLICY_PROMPT·M10/M11과 실제 코드를 대조하라.
   M11이 미병합이면 최신 M11에서 codex/gua-policy-cache-m12를 만들고 base를M11로 둬라.
   병합됐으면 M11이 포함된 최신 기준을 확인하라. 기존 변경/다른작업트리/원문/증거를 보존하라.
   main 직접push·force·자동병합은 하지 마라.

2. P-DRAFT-CACHE-CONTEXT에 REQ-16의 required_cache_contexts:required list[str]만 등록하라.
   PUBLIC-PREFIX와 REQ15/17은 등록하지 마라. 기존 미구현 실행기는 그대로이며 자동점검 수를 늘리지 마라.
   전체 기업 목록을 받되 model_generation/adapter_content/media_identity의 비어있지 않은 고유 부분집합만 첫 프로파일로 지원하라.
   빈/중복/공백/미지원/누락/잘못된타입을 구분하고 자동정규화·부분선택·기본값·기업목록축소를 하지 마라.
   이 식별자들은 기술 제안이지 실제 활성 사실이 아니다.

3. same_authenticated_group_private 프로파일과 declared_cache_context_evidence 관측을 선언하라.
   /3 generic 값에 guard-cache-context-evidence/1 구조를 넣는다. M11 §4.3의 정확한 상위/행/첨부필드·enum을 구현하라.
   context별 same_context/changed_context/post_control의 전체 커버리지와 같은trial/target/workload/집단·전후쌍을 검사하라.
   실제 객체/consumer/승인세대 변화·hit귀속·추론/후속정상 근거를 요구하라. 이름·salt·key필드·miss/지연만으로 PASS하지 마라.
   공개/교차집단이나 다른조건변경은 첫프로파일 밖이다. UNKNOWN으로 한계를 남기고 REQ15보호 완료로 표시하지 마라.

4. 순수 REQ16 전용 validator/상한으로 선언완결성·형태·중복/foreign문맥·사건상충·첨부hash/locator/basis를 검사하라.
   EVD.artifacts hash에 정확히 귀속시키고 payload 경로를 열거나 외부URL을 가져오지 마라.
   /3 generic은 잘못된 값을 저장할 수 있으므로 소비자에서 PASS를 보류하고 전역수동입력계약을 바꾸지 마라.
   모든 새이름이 없는 과거 정의는 legacy, 일부선언은 보류다. 다른점검에 조건을 소급하지 마라.

5. declared의 보안/기능에 M11 §4.4 표의 PASS상한을 적용하라.
   신고PASS+완전충족만 기존PASS후보, 상충/old_context_reused/미수집은UNKNOWN이다.
   신고FAIL은보존하고 신고UNKNOWN은승격하지 마라. 새 자동FAIL이나성능기준은 만들지 마라.
   새 REQ16 프로파일의 성능PASS는 기준부재로UNKNOWN, 기존CPU/다른점검의 처리와FAIL은보존한다.
   기존REQ09 M11보강·SUP/LIF/OBS 계약·INV11·첨부/답변·normal/challenge를 보존하라.
   POLICY_CONTEXT 안전한 사유와 CheckDef 한계를 기존 JSON/HTML에 연결하고 official/escape/출처/비교를 유지하라.

6. collector generic과수동/3·/1/2읽기·Ref/side·append-only·config hash 입력은 유지하라.
   수동성능·실제 비교값을ENV에서자동보충하지 마라. 관측은EVD content hash에이미포함된다.
   새payload·양식/4·POL타입·ENV키·실행기종류·범용DSL·레지스트리·의존성은 만들지 마라.
   필요 최소 judges/순수helper·SPEC·override/팩·설명/카탈로그 변경은 이 요청이 승인한다.
   core/models/store/adapter/HTTP/실행기변경은필요없다는안을우선하고 불가피하면근거와최소대안을특정하라.

7. override에서팩/metadata를생성하고 카탈로그의 실제 REQ16 경로만 연결하라.
   77ID·303결정595연결·원문·다른점검/기존증거·기업values/scope/승인을보존하라.
   팩/REQ16/declared버전과정책경로/분류는실제변경으로재계산하라. M11기준팩15·14점검26경로·a23b4·자동연결5를과거기준으로구분하라.
   빈정책양식은REQ16:{}를유지하고 실제기업값을examples/lab에채우지마라.

8. 구현·테스트코드·문서초안을전부작성한뒤 테스트→실패수정→필요한재검증을하라.
   목록/타입/선언·3case커버리지·각문맥실제변화·키만변경/캐시비활성/순서/집단/foreign첨부·행상충·독립신고FAIL을검증하라.
   old_context_reused+신고PASS→UNKNOWN, 신고FAIL보존, 신고UNKNOWN비승격, 성능UNKNOWN을검증하라.
   /1/2/3·실제import→judge→JSON/HTML·Ref/side/hash·과거객체보존·같은as_of멱등을검증하라.
   실제과거M11 wheel/팩15의REQ09/기존SUP/LIF/OBS와과거REQ16정의를새설치본에서재판정하라.
   frozen sync·기본전체(--durations)·wheel·저장소밖설치본과최종SHA CI를확인하라.
   기존실행증거를덮지말고 새별도경로에명령/환경/통과실패skip제외/시간을남겨라.
   합성EXAMPLE/DECLARED 계약과실제엔진/두이미지검증을구분하라. 후자는실행하지마라.
   1분목표를맞추려테스트/불변식/판정규칙을완화하지마라. 운영/유료자원도실행하지마라.

9. SPEC·POLICY_TEMPLATE·카탈로그·실행안내·PROGRESS·DECISIONS·docs/POLICY_M12.md를실제상태로갱신하라.
   검증후이번변경만커밋하고 성공확인후push·초안PR·작업연결을완료하라.
   작성자정보는기존기록과인증계정으로확인하고 전역설정을임의변경하지마라.
   최종원격HEAD/base/diff/CI를확인하고 한국어로변경/검증/미검증·브랜치/커밋/PR·팀결정/후속범위를보고하라.
   REQ15공개공유/신원과REQ17통계·실제캐시자동실행은후속이며 사용자지시없이실행하지마라.
```
