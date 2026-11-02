# M13 REQ-16 unknown 일관성과 REQ-15 정책 계약 설계

## 1. 상태와 기준

기준은 M12 `55a24b24ad605aaa944b2a499dbd366ab2e48482` / [초안 PR14](https://github.com/dohashelby/gua/pull/14)다. 미병합 상태이므로 `codex/gua-policy-cache-m13`은 M12를 base로 둔다. fetch한 main `1778c15`는 .DS_Store만 변경했고 이번 정책 작업에 통합하지 않았다. [SPEC](../SPEC.md)이 정본이다.

**현재 구현**은 REQ16의 허용 enum unknown과 축별 판정 일관성 보완뿐이다. **REQ15·M14는 미구현 설계 제안**이다. 기업 실제 정책값·담당자·승인·실제 시험 권한은 미정이며 임의로 만들지 않는다.

M12의 Linux1144통과/18제외(49.07·46.52초), Windows1139통과/5skip/18제외(299.01초), 설치본38명령은 [M12 과거 결과](POLICY_M12.md)다. 이번 직접 결과와 합산하지 않는다.

## 2. REQ16 확인 사실과 채택한 수정

M12 [판정기](https://github.com/dohashelby/gua/blob/55a24b24ad605aaa944b2a499dbd366ab2e48482/src/guard/judges/manual_cache_context.py#L44)는 enum에 unknown을 두면서 먼저 모든 행 문자열에 `_text`를 적용했고 이 함수는 unknown을 거부했다. [M12 회귀](../tests/test_policy_m12.py)의 inference 반례는 failed만 다뤘다. 앞선 A 검토의 합성6조건에서 failed/not_attempted는 보안PASS/기능UNKNOWN, unknown은 RECORD_INCOMPLETE로 두 축UNKNOWN이었다. 실제 엔진 취약점이나 첨부 진위가 확인된 사례가 아니다.

사용자 실행 지시로 POL-M13-UNKNOWN-AXES를 채택했다. [helper](../src/guard/judges/manual_cache_context.py)는 비enum 식별/근거 문자열, enum 형태, 지원 enum, 의미 충족을 구분한다. [declared](../src/guard/judges/declared.py)는0.9.1이다. 공통 `_text`의 unknown 표식 거부는 유지하고 enum 필드에만 유효 상태값을 허용한다.

| 입력 상태 | 해석/사유 | 신고 PASS의 영향 |
|---|---|---|
| 누락·타입 오류·빈/공백·앞뒤 공백 | 형태 미완결, RECORD_INVALID/RECORD_INCOMPLETE 등 | 구조·전제 부족으로 보류 |
| 비어 있지 않은 미지원 enum 문자열 | ENUM_INVALID | 보류 |
| 허용 enum unknown | 유효한 미확인 관측 | 해당 축 의미 검사로 보류 |
| 신원·집단·식별자·상세·첨부 근거의 unknown | 미입력/근거 부족 표식 | 계속 보류 |

아래 표는 **격리·구조·첨부·전체 문맥·INV11·정상/챌린지 전제가 모두 충족된 원본 신고 PASS**에만 해당한다.

| inference | 보안 | 기능 | 성능 |
|---|---|---|---|
| succeeded | PASS 후보 | PASS 후보 | UNKNOWN |
| failed | PASS 후보 | UNKNOWN | UNKNOWN |
| not_attempted | PASS 후보 | UNKNOWN | UNKNOWN |
| unknown | PASS 후보 | UNKNOWN | UNKNOWN |

추론 미확인은 독립된 격리 근거를 지우지 않지만 격리 근거를 대신하지도 않는다. object_change/other_conditions/key_relation/cache_effect의 unknown은 보안·기능을 보류한다. old_context_reused는 상충 사유이며 새 자동 FAIL을 만들지 않는다. 신고 FAIL/UNKNOWN·명시 CPU·첨부/답변·INV11·REQ09/SUP/LIF/OBS는 유지한다. 기능에 안전한 INFERENCE_UNRESOLVED를 기록하며 payload·예외 원문을 복사하지 않는다.

과거 RES의 UNKNOWN과 원본 EVD 신고 UNKNOWN은 다르다. 동일 M12 EVD의 원본 신고PASS가 새 판정에서 보안PASS 후보가 될 수 있지만 원본 신고UNKNOWN은 승격하지 않는다. 기존 EVD/RES를 수정하지 않고 새 판정 버전 RES에 기록한다. 같은 as_of는 멱등이다.

팩16/DRAFT/REQ16정의2·해시 `b21ba9ae28343897622eedbb043ad82615901ca7acebd16a8cbfa73bbd6aeec6`·77점검·15정책점검27경로·303결정595연결·a24/b3/c161/d115·자동실행5를 보존한다. 수동 구조/1·양식/3·/1/2읽기·Ref/side·config hash 입력·기업 values/scope/승인은 그대로다. 코어·모델·Store·collector·adapter·HTTP·실행기·새 의존성 변경은 없다. 카탈로그의 직접 소비3행과 설명 metadata만 현재 버전으로 갱신한다.

## 3. REQ15 원문·정책 경계 — 미구현

[REQ15 원문](../reference/framework/data/checklist.json#/request_state_assessment_draft/14)과 [현재 정의](../packs/PACK-BASELINE/checks/REQ-15.yaml)는 신뢰 신원→공유 집단→salt/key→실제 hit/소비와 클라이언트 위조·누락을 대조하도록 요구한다. salt 존재나 단일 서비스 키만으로 격리를 확정하지 않는다. REQ15정의1은 policy/관측/실행기/판정 미등록이다.

| 기존 후보 | 현재 분류 | 필요한 의미 |
|---|---|---|
| P-DRAFT-CACHE-PUBLIC-PREFIX | b/bool | 공개로 승인된 범위의 집단 간 공유 허용 여부 |
| P-DRAFT-CACHE-GROUP | d/신원×공유 집단 허용 관계 | 기업이 금지·허용한 실제 관계. 공개 bool에서 추론하지 않음 |
| P-DRAFT-CACHE-SALT | d/신원 인증·salt 발급/덮어쓰기/검사 책임표 | 신뢰 경계와 신원/집단 귀속의 승인 정본 및 실제 효과 |
| P-DRAFT-CACHE-MULTIUSER | d | 공유 서비스 자격 뒤 사용자 전달/격리. 첫 프로파일은 미지원 |
| REQ16/CACHE-CONTEXT | a/REQ16만 | 같은 집단 내 객체 문맥 변경 계약으로 현재 보존 |
| REQ17/통계·성능 | c/d·미구현 | 위협 조건·비공개 정보·지표 독자·반복/오차/기준의 별도 결정 |

POL은 승인해야 할 기준의 신고이고 ENV는 실제 구성, 실행 설정은 대상/격리/예산, EVD는 관측/첨부, RES/RPT는 제한된 계약의 판정이다. EVD 자유 서술을 기업 승인 관계로 사용하지 않는다. POL의 DRAFT/참조 문자열도 도구가 인증한 승인이 아니다.

### 3.1 공개 공유 bool

| 정책 | 필요한 검토 | 금지 추론 |
|---|---|---|
| false | 같은 허용 집단의 정상 재사용·금지 집단 간 비공개 분리 | 캐시 비활성·NOT_APPLICABLE·자동 성공으로 처리 금지 |
| true | 공개 분류/허용 집단·대상·승인 근거와 비공개 분리 | 사설 입력·자격·출력·다른 사용자 전체 공유 허용으로 확대 금지 |
| 누락 | 정책 미결 | ENV/관측/기본값에서 보충 금지 |
| null/다른 타입 | 기존 select 타입 오류와 저장값 재판정 의미 부족 구분 | false 변환 금지 |

### 3.2 M14 선택 비교

| 안 | 장점/선행조건 | 판단 |
|---|---|---|
| 비공개 격리 + 신뢰 경계의 server overwrite만 첫 지원 | bool false와 별도 전체 격리집단/신원정책을 명시하면 기존 타입과 /3로 제한 수동 계약 가능 | 권고 |
| 공개 공유 true도 함께 지원 | 공개/비공개 분류, 허용 관계/객체·변경 세대·승인 정본과 혼입 반례가 추가로 필요 | 후속 |
| bool만 등록 | 실제 공유 관계/신원 책임의 소비 계약이 없음 | 채택하지 않음 |
| 범용 관계 DSL/새 POL 타입·ENV·/4 | 모든 관계를 표현할 수 있으나 현재 필요한 복잡성과 호환 영향이 큼 | 첫 구현에서 제외 |

**M14 권고 프로파일** `private_groups_server_overwrite`는 한 entrypoint/target/workload, distinct authenticated credential을 가진 그룹별 신고 대표 주체1개, 기업이 명시적으로 서로 격리하도록 정한 전체 그룹 목록에 한정한다. 그룹 전체 사용자/다른 진입점·공유 서비스 자격 뒤 사용자전달을 검증한 것으로 확대하지 않는다. 해당 제한을 CheckDef와 보고서에 표시해야 한다.

제안 POL 키(모두 REQ15 required, 현재 미등록):
- `public_prefix_sharing_allowed: bool`: 첫 프로파일은 false만 지원한다. true는 프로파일 밖 UNKNOWN이며 정책을 false로 바꾸지 않는다.
- `isolated_cache_groups: list[str]`: 같은 그룹 내부는 정상 재사용하고 **서로 다른 목록 그룹 간 비공개 재사용은 금지**하는 명시 정책이다. bool false에서 이 관계를 추론하지 않는다. 기업 전체 검토 목록이며 비공백/고유·최소2개의 기술 전제가 필요하다. 2는 비자명한 교차 집단 검토의 구조 최소이며 기업 예산·성능 수치가 아니다. 하나/미지원/누락을 EVD에서 보충하거나 목록을 줄이지 않는다.
- `cache_identity_policy_ref: str`: 그룹 귀속·신뢰 경계·salt 발급/덮어쓰기 책임을 사람이 검토할 기업 정본의 정확한 버전 참조다. 비공백 문자열과 EVD 참조의 일치만 자동 대조한다. 외부 문서를 열거나 내용/승인 진위를 인증하지 않는다. 정본·책임·집단 매핑이 실제로 불명확하면 사람이 PASS를 신고할 근거가 없다.

이는 기존 GROUP·SALT 결정의 **제한적 구조화 제안**이다. 전체 임의 허용 관계표나 인증 책임표를 문자열로 완전히 표현했다고 보지 않는다. GROUP에는 list[str]의 격리목록 경로, SALT에는 str 정본참조 경로, PUBLIC-PREFIX에는 bool 경로를 연결하는 안이다. M14가 이 기술안을 채택할 때만 기존 행의 제안 형태·분류를 제한 소비 의미로 갱신하고 원래 복잡한 요구의 출처/형태/미구현 한계를 classification_reason과 설계 문서에 보존한다. 정책 항목을 새로 지어내거나 원문 연결을 줄이지 않는다. MULTIUSER는 d로 남는다.

3경로를 그대로 채택할 경우 M14 예상은 정책점검16개/30경로·a27/b2/c161/d113,303/595·자동실행5다. 이는 **미구현 예상**이며 M13의 실제 계수가 아니다. 팩/정의 revision은 M14 실제 생성 결과로 계산한다.

## 4. REQ15 증거·판정 설계 — 미구현

제안 관측은 `declared_cache_group_evidence`, 구조 식별자는 `guard-cache-group-evidence/1`다. /3 generic 값에 dict를 저장하고 REQ15 전용 순수 helper에서 검증하는 안이다. /1/2 읽기는 기존처럼 유지하며 새 payload/양식·객체·실행기·I/O를 만들지 않는다. 모든 새 선언이 없는 과거 정의는 legacy, 일부/잘못된 선언은 보류다.

### 4.1 정확한 구조 제안

상위 필드:
`version, profile, identity_policy_ref, entrypoint_ref, target_scope, workload_ref, classification, identities, records`.
- version/profile은 위 식별자와 private_groups_server_overwrite.
- identity_policy_ref는 POL cache_identity_policy_ref와 정확히 일치.
- classification은 private/unknown. 첫 프로파일 PASS는 private만.
- entrypoint_ref/target_scope/workload_ref는 한정된 시험의 비밀값 없는 신고 식별자.
- identities는 POL 전체 그룹마다 정확히1개 대표 주체, records는 아래 전수 case.

identity 행 필드:
`group_id, principal_ref, credential_kind, identity_source, verification_status, binding_basis, evidence`.
- credential_kind enum: distinct_authenticated_credential / shared_service_credential / client_salt_only / unknown. 첫 프로파일은 distinct_authenticated_credential만.
- verification_status enum: verified / unknown. verified도 인증 결과의 **사람 신고**이며 도구 인증이 아님.
- group_id는 POL 목록과 정확히 대응하며 principal_ref는 그룹 사이에서 달라야 한다.
- identity_source/binding_basis는 정본의 신원→집단→신뢰 경계/책임과 실제 trace 연결 설명. 같은 서비스 키/클라이언트 salt만으로 채우지 않는다.
- evidence는 해당 EVD entry 첨부의 sha256/locator/basis 배열.

record 필드:
`source_group_id, target_group_id, case, trial_id, event_id, actor_principal_ref, client_group_claim, salt_action, derived_group_id, consumer_ref, key_relation, cache_effect, consumed_group_id, source_entry_ref, consumed_entry_ref, request_outcome, inference, observation_detail, evidence`.

| 필드 | 허용 enum |
|---|---|
| case | source_warmup / same_group_reuse / cross_group_probe / forged_salt / omitted_salt / post_control |
| client_group_claim | absent / own_group / source_group / unknown |
| salt_action | trusted_injected / trusted_overwritten / client_controlled / unknown |
| key_relation | same / different / unknown |
| cache_effect | warmed / self_group_hit / cross_group_isolated / forbidden_cross_group_reuse / unknown |
| request_outcome | accepted / blocked / unknown |
| inference | succeeded / failed / not_attempted / unknown |

자동 정규화하지 않는다. unknown은 허용 enum의 미확인 상태로 읽고 해당 축 PASS를 보류한다. 비enum 식별/근거의 unknown·빈/공백·타입 오류는 여전히 미완결이다. 추가/누락 필드·중복·foreign 그룹/주체·첨부 hash 외부 귀속은 invalid/UNKNOWN이다. 이름이 같은 것만으로 실제 객체/entry 귀속을 인증하지 않는다.

### 4.2 전수 case와 귀속

기업 목록 G의 모든 서로 다른 **순서 있는 쌍 A→B**마다6case를 요구한다. 역방향 B→A를 생략하지 않는다. 각 쌍의6행은 동일 trial, source entry, 고정 entrypoint/target/workload를 사용한다. 모든 행 event_id는 고유하고 시간/순서는 상세·첨부 locator에 신고한다. 다른 쌍은 별도 trial로 분리하고 실제 사건을 임의로 재사용하지 않는다.

| case | 행위자·신뢰 집단 | salt/claim·key·실제 소비의 PASS 후보 전제 |
|---|---|---|
| source_warmup | A의 대표 주체·derived A | absent/trusted_injected, key same, warmed, consumed A, source entry와 소비 entry 동일 |
| same_group_reuse | A·derived A | absent/trusted_injected, key same, self_group_hit, 동일 source entry 소비 |
| cross_group_probe | B·derived B | absent/trusted_injected, key different, cross_group_isolated, B entry 소비·A source entry와 다름 |
| forged_salt | B·derived B | source_group/trusted_overwritten, key different, cross_group_isolated, B entry 소비 |
| omitted_salt | B·derived B | absent/trusted_injected, key different, cross_group_isolated, B entry 소비 |
| post_control | B·derived B | absent/trusted_injected, key different, self_group_hit, 앞 cross_group_probe의 B entry 재사용 |

request_outcome은 첫 프로파일에서 전부 accepted를 요구한다. key_relation은 그 쌍의 A source key와 현재 소비 key를 비교하며 post_control도 바로 직전 key 비교가 아니다. consumed_group_id·principal 매핑·derived group을 함께 대조한다. consumer_ref는 같은 실제 소비 위치를 신고해야 하며 설명/첨부가 실제 key→lookup→entry→소비/출력·사건 순서를 연결해야 한다.

이 프로파일은 신뢰 경계에서 위조를 덮어쓰는 방식만 지원한다. 위조 입력을 안전하게 거부하는 제품은 유효한 대안일 수 있으나 첫 프로파일에서는 UNKNOWN/지원 밖으로 남기고 취약/FAIL로 단정하지 않는다. 거부·추론 실패·cache 비활성만으로 성공할 수 없도록 정상 재사용과 후속 B hit가 필요하다. 단순 miss/지연·salt 문자열 존재·그룹 이름만으로 격리를 인증하지 않는다.

### 4.3 축별 상한 제안과 보고

| 축 | 신고 PASS의 후보 조건 | 실패·미확인 |
|---|---|---|
| 보안 | false 프로파일·명시 POL 관계/정본·전체 G/대표 주체/6case·derived/실제 소비 집단·첨부/정상 관문 완결 | 조작 집단 수용·forbidden_cross_group_reuse·누락·unsupported/unknown은 UNKNOWN |
| 기능 | 보안 계약의 전제 및 모든 행 추론 succeeded·정상/후속 hit | 실패/미시도/unknown은 UNKNOWN |
| 성능 | 새 기준 없음 | 신고PASS는 UNKNOWN, rawFAIL/UNKNOWN·기존CPU 우선 규칙 유지 |

모든 축의 rawFAIL은 보존하고 rawUNKNOWN을 승격하지 않는다. 새 자동FAIL을 만들지 않는다. REQ16과 마찬가지로 구조/격리 관측이 완결된 경우 추론 미확인만으로 독립 보안을 지우지 않는 안이다. 실제 정상 대조 실패는 공통 INV11이 PASS를 보류한다.

JSON/HTML에는 실제 POL 분기·정본참조의 자기 신고 한계·그룹별 대표 주체/한 진입점 범위·미검증 신원/첨부 진위·성능/REQ17·지원 밖 public/shared-service/거부 방식·누락/상충을 안전한 POLICY_CONTEXT 코드로 전달한다. credential/token/salt 원문·payload·의존 예외 원문을 복사하지 않는다. 세부 설명/trace는 EVD에 남고 HTML escape/official/비교는 기존 계약이다. 첨부 SHA는 무결성일 뿐 승인/신원 진위가 아니다. generic 관측은 EVD hash에 포함하며 config hash에 응답 전체를 추가하지 않는다.

### 4.4 합성 정책/관측 조각 — 미실행

아래는 **미등록 설계의 EXAMPLE 조각**이며 실제 POL·기업 전체 집단·승인·실측이 아니다. 기록·양식 파일을 제품 examples/lab에 새로 생성하지 않는다. identities/records가 비어 있어 PASS 예시가 아니다.

```yaml
# EXAMPLE: proposed M14 policy values, not current registered fields.
values:
  REQ-15:
    public_prefix_sharing_allowed: false
    isolated_cache_groups: [EXAMPLE-A, EXAMPLE-B]
    cache_identity_policy_ref: EXAMPLE-IDENTITY-POLICY@revision1
```

```yaml
version: guard-cache-group-evidence/1
profile: private_groups_server_overwrite
identity_policy_ref: EXAMPLE-IDENTITY-POLICY@revision1
entrypoint_ref: EXAMPLE-single-gateway-entry
target_scope: EXAMPLE-worker
workload_ref: EXAMPLE-private-fixture
classification: private
identities: []  # EXAMPLE 미입력: POL 각 그룹의 대표 주체·첨부 필요
records: []     # EXAMPLE 미입력: A→B와 B→A 각각6case 필요
```

### 4.5 M14 반례 계획 — 제품 시험 미실행

| ID | 조건 | 기대 |
|---|---|---|
| G01 | bool 누락/null/문자열/0 | 정책 누락·기존 타입 오류 구분, 재판정 rawFAIL 보존 |
| G02 | false만 있고 그룹/정본 누락 | 관계/승인 추론 금지, PASS 보류 |
| G03 | true를 false 프로파일에 제출 | UNKNOWN·지원 밖, 정책 변환/NOT_APPLICABLE 금지 |
| G04 | 그룹목록 []/1개/중복/공백 | 구조 최소/전체목록 미완결 |
| G05 | foreign 그룹·대표 주체·정본 Ref 불일치 | PASS 보류 |
| G06 | 단일 서비스 키 뒤 사용자만 다름 | 최초 shared-service 프로파일 미지원 |
| G07 | client_salt_only 또는 verified=unknown | 신원/귀속 미확인 |
| G08 | A→B는 있지만 B→A 없음 | 전체 ordered pair 커버리지 부족 |
| G09 | 위조 또는 누락 case 미수집 | 전체 case 부족 |
| G10 | 위조를 받아 derived A·소비 A | 상충 UNKNOWN, rawFAIL/UNKNOWN 보존 |
| G11 | key/salt만 다르고 실제 entry 소비 미확인 | PASS 보류 |
| G12 | miss뿐·post_control 없음·cache 전체 비활성 | 정상 재사용 부족 |
| G13 | 위조 요청을 안전하게 blocked | 처음 지원 밖 UNKNOWN, 자동FAIL 금지 |
| G14 | 다른 entry/side 첨부 hash·빈 locator/basis | 현재 entry 귀속/근거 미완결 |
| G15 | 같은 사건 재활용·다른 trial/consumer | invalid/전제 부족 |
| G16 | private 이외 분류·공개 분류 정본 없음 | 최초 프로파일 밖, 비공개 공유 확대 금지 |
| G17 | 기능 inference unknown + 독립 격리 완결 | 보안 기존 후보/기능UNKNOWN, rawUNKNOWN 승격 금지 |
| G18 | 과거 정의·일부 신규선언 | legacy/partial 구분, 타 점검 영향 없음 |
| G19 | 원본/Ref/side/hash·같은 as_of | 원본 보존·새RES·멱등·기존 비교 규칙 |
| G20 | 성능값 없음·명시CPU·예산/권한 없음 | 성능UNKNOWN·실제시험 미수행·기준/권한 추정 금지 |

## 5. 변경 위치·업무 결정

M14 후보는 REQ15 override→팩/manifest·설명 metadata/catalog, 작은 REQ15 전용 순수 helper, declared 호출, 관련 테스트/문서다. 기존 cache_context helper의 의미를 합치거나 SUP/OBS payload를 확장하지 않는다. core/models/store/collector/adapter/실행기·새 타입/DSL/레지스트리/의존성·양식/4는 필요 없다는 안이다. 다른 경계 변경이 불가피하면 근거와 최소 대안을 별도로 검토한다.

팀 결정:
- 기업의 전체 격리 그룹과 공유 허용 관계가 이 제한 규칙과 맞는가? 교차 공유 예외가 있으면 임의로 목록을 잘라 맞추지 않는다.
- 공개/비공개 분류 정본과 버전·승인 역할은 무엇인가?
- 신원→집단과 주입/덮어쓰기 경계·책임의 정확한 정본 Ref는 무엇인가? 실제 담당자/승인 증빙을 도구가 만들지 않는다.
- 대표 주체/진입점/consumer와 실제 key·entry·출력/순서 첨부를 누가 수집·검토하는가?
- 공유 서비스 자격 뒤 여러 사용자와 거부 방식은 후속에서 어떻게 검증할 것인가?
- 실제 시험 대상·권한·예산·정상 대조·중단조건·지표 독자는 누구인가?

설계/합성 회귀는 업무값 없이 작성할 수 있다. 실제 보호나 공식승인으로 승격할 수는 없다. REQ17의 위협/반복/오차/수용 기준과 SUP 코드모드/파생위치 정책은 각각 별도 설계 후속이다. 기간이나 완료율은 산정하지 않는다.

## 6. M13 직접 검증

모든 구현·테스트 코드·설계/문서 초안을 마친 뒤 Windows/CPython3.11.16/uv0.12.11에서 아래를 직접 실행했다. 증거/도우미는 ignored `out/test-runs/policy-m13-20261023/`에 새로 보존한다.

| 명령/단계 | 이번 직접 결과 |
|---|---|
| `uv sync --frozen` | 19패키지 확인, lock/의존성 변경 없음 |
| `uv run pytest -q --durations=20 --basetemp=.review-tests-policy-m13-20261023 --junitxml=out/test-runs/policy-m13-20261023/full-first.xml` | **1279통과/실패0/5skip/18제외,370.58초**. 신규140사례 포함, 신규사례 JUnit 누적11.229초 |
| `uv build --wheel --out-dir out/test-runs/policy-m13-20261023/dist` | wheel 성공 |
| `uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file out/test-runs/policy-m13-20261023/requirements.txt` → `uv venv ... --python 3.11` → 별도환경 `uv pip sync --python <설치본Python> <requirements.txt>` → `uv pip install --no-deps --python <설치본Python> <wheel>` | frozen 런타임14의존성+wheel 별도 설치 성공 |
| `python -X utf8 -B out/test-runs/policy-m13-20261023/installed_smoke.py` | **38명령 exit0**, 저장소 밖 Python -I/실제 CLI, PYTHONPATH/PYTHONHOME 제거 |
| `python -X utf8 -B out/test-runs/policy-m13-20261023/verify.py` | 팩79파일·reference21·core8/models7/store4/adapters5/executors11·기존out61·M12기존검증자료120파일 바이트 보존, 카탈로그3행만 소비버전/근거 변경, metadata 정합, 상대링크7·JSON Pointer1·설계반례20·EXAMPLE YAML2·M14미실행 확인 |

기본 전체는 첫 실행에서 통과해 불필요하게 반복하지 않았다. skip5는 기존 Windows 닫힌 루프백의 timeout 차이3·symlink 권한2이며 제외18은 기존 engine/docker marker다. 새 skip/제외는 없다. Windows1분 목표는 미달이다. 서로 다른 환경/실행의 시간 차이를 제품 성능 변화로 단정하지 않는다. 최종 SHA의 Linux CI·시간·원격/base/diff는 초안 PR에 기록한다.

설치 검증은 실제 보존 M12 wheel의 declared0.9.0·캐시 helper를 고정 M12 커밋 바이트와 대조하고 정확한 팩16 해시를 확인했다. **이번 실행에서** 이 M12 설치본으로 update PLAN의 정상 baseline·inference unknown candidate 및 기존 SUP/LIF/OBS·미등록 REQ15 증거를 생성했다. M12의 RECORD_INCOMPLETE/보안UNKNOWN을 실제 import/judge에서 재현하고, 새0.9.1은 해당 REQ16 candidate 보안만PASS로 바꾸며 기능UNKNOWN·나머지 축/점검·원본 Ref/hash/config를 보존했다. 같은 as_of는 멱등이고 JSON/HTML에서 기능 미확인·manual·official=false를 확인했다. 새 mixed 입력에서는 원본 신고UNKNOWN 비승격·독립 신고FAIL 보존도 확인했다.

같은 실제 M12 설치본이 팩16으로 만든 REQ09 정상 자동/수동 증거와 팩15로 만든 legacy REQ16 PLAN/양식/EVD/RES를 새 설치본에서 재판정해 보존했다. 다른 팩을 기존 PLAN에 대입하지 않았다. REQ09 상충/FAIL과 /1/2/3·LF/CRLF 정확 첨부 bytes·side·관문·CPU·의존오류는 신규/기존 기본전체 회귀에 포함된다. 최종 설치본의 Python/JSON61파일도 소스와 바이트 일치했다.

제품/회귀 검증에서 실패나 추가 수정은 없었으며 판정/불변식을 완화하지 않았다. 개발용 metadata 생성 보조스크립트의 import 경로1건은 생성 단계에서 보완했다. G01~20은 미구현 M14 설계 사례로 이번 제품 통과 수에 넣지 않는다. 실제 엔진·Docker stub·두 실제 이미지·운영·유료 자원 시험과 M14는 실행하지 않았다.

## 7. M14 구현 담당 B에게 전달할 프롬프트 — 미실행

```text
너는 gua 정책 개발 담당 B다. 저장소는 https://github.com/dohashelby/gua.git 이다.
이번 요청은 M13의 REQ15 제안을 채택하여 제한적 수동 정책 계약을 구현하는 M14다.
기업 실제 정책값/승인/시험 권한과 실제 엔진 시험은 승인하지 않는다.

1. AGENTS/CLAUDE·SPEC·PROGRESS·DECISIONS·M11/M12/M13과 코드를 읽어라.
   M13 최종 HEAD/CI를 확인하고 미병합이면 최신 M13에서 codex/gua-policy-cache-m14를 만들고 base=M13로 둬라.
   병합됐다면 M13을 포함한 최신 기준을 확인하라. 기존 변경/작업트리/원문/증거를 보존하라.
   main 직접push·force·자동병합·임의reset/clean을 하지 마라.

2. docs/POLICY_M13.md §3~4의 private_groups_server_overwrite 계약을 구현하라.
   REQ15에만 required public_prefix_sharing_allowed:bool, isolated_cache_groups:list[str],
   cache_identity_policy_ref:str을 등록하라. bool false만 첫 지원이며 true는 지원 밖 UNKNOWN이다.
   전체 격리 그룹목록과 정본 Ref를 명시 POL로 받아라. false에서 허용 관계/승인을 추론하지 마라.
   타입 오류/누락/빈·공백·중복/1개·foreign·미정과 지원 밖을 구분하고 정규화/부분선택/기본값을 만들지 마라.

3. /3 generic dict 관측 declared_cache_group_evidence와 guard-cache-group-evidence/1을 사용하라.
   M13 §4.1의 정확한 상위/identity/record 필드·타입·enum과 §4.2의6case를 구현하라.
   POL 전체 G의 ordered A→B와 역방향 각각6case·대표주체/신원정책Ref·entrypoint/target/workload·
   동일trial/source entry·고유event·derived group→실제 consumer/consumed group/entry 연결을 검사하라.
   위조는 server overwrite, 누락은 trusted injection, post_control은 B의 실제 후속 hit를 요구한다.
   단순miss/지연/key/salt 차이로 보호를 확정하지 마라. 안전거부·공유서비스자격은 첫 지원 밖이다.
   허용 enum unknown은 미확인 상태로 처리하고 식별자/신원/첨부 근거 표식을 유효 증거로 보지 마라.

4. 작은 REQ15 전용 순수 helper와 declared PASS상한을 사용하라.
   수동 신고PASS+완결만 기존PASS후보, 누락/상충/조작/지원밖은UNKNOWN이다.
   rawFAIL/UNKNOWN을 보존하고 새 자동FAIL을 만들지 마라. inference는 기능에 추가로 성공을 요구한다.
   성능PASS는UNKNOWN, 비CPU rawFAIL/UNKNOWN과 기존CPU 성능UNKNOWN 규칙을 유지하라.
   기존 REQ16 M13·REQ09 M11·SUP/LIF/OBS·INV01~18·첨부/답변/정상/챌린지를 보존하라.
   의존 오류로 신고FAIL을 지우지 말고 안전한 POLICY_CONTEXT/미확인 사유를 JSON/HTML로 전달하라.

5. 첨부는 현재 EVD entry의 sha256/locator/basis로 연결하라. payload 경로/URL을 읽지 마라.
   POL/EVD의 ref일치는 진위/승인 인증이 아니다. 신원·책임·entry/첨부 진위는 DECLARED 한계를 명시하라.
   한진입점·그룹별 대표주체 표본 범위와 미검증 공개공유/공유자격/거부방식/REQ17을 보고하라.
   원문 credential/token/salt·payload/의존 예외를 사유에 복사하지 마라.
   official/escape·Ref·side·append-only·config hash 입력·같은as_of 멱등을 보존하라.
   새payload/양식/4·ENV/POL타입·범용DSL/레지스트리·실행기·의존성을 만들지 마라.
   모든 새 표식이 없는 과거 정의는 legacy, 부분선언은 PASS보류다. 타 점검에 소급하지 마라.

6. override에서팩/manifest·metadata를 생성하라. 카탈로그 기존 PUBLIC-PREFIX/GROUP/SALT 세 행의
   실제 REQ15 경로만 연결하고 원문/기존check_ids 연결을 보존하라.
   GROUP의 제한 list, SALT의 str 정본참조는 전체 복잡한 관계/책임표 완료가 아님을 설명하라.
   원래 proposed_shape/미구현 요구는 classification_reason·출처·설계문서에 보존하라.
   MULTIUSER·REQ16/17 신규등록은 하지 마라. 현재 M13 계수와 M14 실생성 계수를 구분하라.
   제안 그대로라면 정책16점검30경로·a27b2c161d113·303/595·자동실행5가 예상되며 실제로 재계산하라.
   기업values/scope/승인은 보존하고 필요한 pack Ref만 갱신하라. 빈 양식은 REQ15:{}와 설명만 생성하라.

7. 구현·테스트코드·문서초안을 모두 작성한 뒤 테스트→수정→필요 재검증을 하라.
   G01~20의정상/반례·모든순서쌍/역방향/6case·추가/중복/foreign·신원정책Ref/consumer/entry/
   첨부귀속·true/false/누락/오류·unknown/부분선언·독립rawFAIL·기능/성능/CPU를 검증하라.
   실제 /1/2/3 import→judge→JSON/HTML·audit/update side·원본/ref/hash·멱등과 LF/CRLF bytes를 확인하라.
   실제 과거 M13 wheel/팩으로 만든 REQ16/기존REQ15/SUP/LIF/OBS/REQ09 증거를 새 설치본에서 대조하라.
   없다면 정확한 고정 커밋 설치본을 별도 생성하고 이번 생성과 과거 실행 기록을 구분하라.
   frozen sync·기본전체(--durations)·wheel·저장소밖별도설치 CLI·최종SHA CI를 확인하라.
   기존증거를 덮지말고 M14전용위치에 명령/환경/통과실패skip제외/시간/원인을 기록하라.
   테스트1분목표를맞추려제외/skip·판정·불변식을완화하지 마라.

8. SPEC·PROGRESS·DECISIONS·POLICY_TEMPLATE·카탈로그·README/RUNBOOK·docs/POLICY_M14.md를 갱신하라.
   현재구현/미구현·기업정책값/기술제안·직접검증/과거기록·합성/실제엔진을 분리하라.
   실제엔진·Docker stub·두실제이미지·운영·유료자원을 시험하지 마라.
   public true·전체관계표·공유자격 뒤 사용자·안전거부·REQ17/SUP후속은 별도다.

9. 이번변경만 staged diff/바이트/공백을 확인하고 커밋 성공·SHA 확인 후 push·초안PR·작업연결을 하라.
   실패하면 다음 공개단계로 넘어가지 마라. 작성자는 인증계정/기존기록으로 확인하고 전역설정을 바꾸지 마라.
   최종 원격HEAD/base/diff/CI와 변경/검증/미검증·브랜치/커밋/PR·팀미결/후속을 한국어로 보고하라.
   M15 등 후속은 사용자 검토 후 별도 지시에서 진행한다.
```
