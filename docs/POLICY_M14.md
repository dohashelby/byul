# M14 REQ-15 — 제한된 캐시 집단 수동 증거 계약

## 구현 범위와 의미

사용자가 [M13 §3~5](POLICY_M13.md)의 제안을 채택하도록 지시하여 REQ-15의
`private_groups_server_overwrite` 수동 계약을 구현했다. 실제 캐시 실행기·새 정책 타입·수동 양식 /4는
추가하지 않았다. **사람의 신고 PASS가 구조와 의미의 최소 전제를 갖췄는지 검사하는 상한**이다.
기존 신고 FAIL/UNKNOWN은 유지하며 새 자동 FAIL을 만들지 않는다. 신원·정책 승인·첨부 내용의 진위를
도구가 인증하는 기능이 아니다. 실제 기업의 그룹·정본·책임자·승인·시험 권한은 미정이다.

기준은 `49526fb6caa9b3885ac912930ac635d069a48e5a`의 PACK-BASELINE 20/DRAFT다.
현재 팩은 **21/DRAFT**, REQ-15 정의는 **2**, declared는 **0.13.0**이다. 팩 Ref SHA256은
`14b3288136b37c8221d4e72d021a6cf353e8996b98e8da08f92ba7c73cbcbeb6`이다.
나머지 76개 CheckDef와 원문 자료는 보존한다. 과거 PLAN에는 그 PLAN의 저장 팩을 사용한다.

정책 선언은 21점검·46경로에서 **22점검·49경로**, 카탈로그는 322결정·614연결을 유지하며
**a46/b2/c161/d113**이다. 자동 실행기 연결은 **11개**로 같다. 신규 수동 계약을 실제 엔진 실행이나
새 자동 실행 점검으로 집계하지 않는다. 과거 단계의 계수는 해당 문서의 당시 기록이다.

## 세 정책 경로

| 경로 | 타입·의미 | 첫 지원 범위 |
|---|---|---|
| `values.REQ-15.public_prefix_sharing_allowed` | required bool; 공개 prefix 공유 허용에 대한 기업 기준 | false만 지원. true는 지원 밖 UNKNOWN이며 false로 바꾸거나 비적용으로 만들지 않음 |
| `values.REQ-15.isolated_cache_groups` | required list[str]; 서로 비공개 캐시 공유를 금지할 기업 전체 목록 | 비공백·고유 최소 두 집단. 일부 목록으로 줄이거나 EVD에서 보충하지 않음 |
| `values.REQ-15.cache_identity_policy_ref` | required str; 신원→집단·신뢰 경계·주입/덮어쓰기 책임 정본의 정확한 버전 참조 | 비공백 참조와 EVD의 정확한 일치만 검사. 외부 문서 열기·승인 인증 없음 |

bool false만으로 격리 관계·신원·승인을 추론하지 않는다. GROUP의 원래 신원×공유 허용 관계표와 SALT의
책임표를 list/str로 모두 구현한 것이 아니다. 기존 세 카탈로그 ID와 REQ-15/16/17의 원문 연결·역할·출처는
그대로 두고 **REQ-15 한 경로씩만** 연결했다. 원래 복잡한 형태와 미구현 한계는 classification_reason에
보존한다. 공개 true·교차 공유 예외·공유 서비스 자격 뒤 사용자·안전 거부 방식·REQ-17은 후속 범위다.

잘못된 타입은 기존 select 타입 오류다. 필수 키 누락은 NO_POLICY_VALUE이며 빈 양식은 로딩 가능하다.
키 존재·빈 목록·false를 곧바로 PASS로 해석하지 않는다. 재판정에서는 타입·의미 부족도 신고 PASS를
UNKNOWN으로 보류하며 이미 신고한 FAIL/UNKNOWN을 지우지 않는다.

## 입력 절차와 구조

1. `guard policy template --pack <팩 경로> --out <새 정책.yaml>`은 `REQ-15: {}`와 설명만 만든다.
2. 기업이 세 정책값·범위·정본을 검토해 입력하고 기존 select로 PLAN을 만든다.
3. `guard evidence template --plan <PLAN> --out <새 증거.yaml>`의 기존 /3 generic
   `required_observations`에서 `declared_cache_group_evidence`의 value에 아래 구조를 입력한다.
4. 실제 검토 가능한 첨부를 entry의 attachments에 지정하고 각 evidence에 해당 **정확한 bytes SHA256**,
   locator, basis를 입력한다. `collected: true`는 사람의 수집 신고다.
5. 기존 evidence import → judge → report로 보안·기능·성능을 구분한다.

상위 필드는 `version, profile, identity_policy_ref, entrypoint_ref, target_scope, workload_ref,
classification, identities, records`로 고정한다. version은 `guard-cache-group-evidence/1`, profile은
`private_groups_server_overwrite`, classification은 private/unknown이며 PASS 후보는 private만이다.

identity 필드는 `group_id, principal_ref, credential_kind, identity_source, verification_status,
binding_basis, evidence`다. POL 전체 그룹마다 정확히 한 대표 주체가 필요하고 그룹 간 principal은 달라야 한다.
distinct_authenticated_credential·verified만 첫 후보이며 shared_service_credential/client_salt_only/unknown은
지원 밖 또는 미확인이다. identity_source/binding_basis의 문구를 실제 인증으로 해석하지 않는다.

record 필드는 `source_group_id, target_group_id, case, trial_id, event_id, actor_principal_ref,
client_group_claim, salt_action, derived_group_id, consumer_ref, key_relation, cache_effect,
consumed_group_id, source_entry_ref, consumed_entry_ref, request_outcome, inference,
observation_detail, evidence`다. 정확한 enum은 [순수 helper](../src/guard/judges/manual_cache_group.py)의
ENUMS/IDENTITY_ENUMS와 [M13 설계 표](POLICY_M13.md#41-정확한-구조-제안)를 따른다.

추가/누락 필드·중복·foreign·잘못된 타입·빈/공백/앞뒤 공백을 자동 정규화하지 않는다.
enum unknown은 유효한 미확인 상태다. 식별자·신원/첨부 근거의 unknown은 미입력 표식으로 보류한다.
신규 표식이 전혀 없는 과거 REQ-15 정의는 legacy이며 일부/잘못된 선언은 partial로 보류한다.
다른 점검에는 이 계약을 소급 적용하지 않는다.

## 전수 쌍과 여섯 case

POL 전체 그룹 G에 대해 서로 다른 **모든 ordered A→B**, 역방향 B→A까지 요구한다.
쌍마다 별도 trial, 여섯 행의 같은 trial/source entry, 전체 고유 event, 같은 consumer가 필요하다.
한 entrypoint/target/workload에 한정된다. target의 consumed entry는 source entry와 다르고
cross_group_probe/forged_salt/omitted_salt/post_control에서 일치해야 한다.

| case | 대표 주체·derived/consumed group | claim / salt | source key 대비 관계·실제 효과 |
|---|---|---|---|
| source_warmup | A | absent / trusted_injected | same·warmed·A source entry 소비 |
| same_group_reuse | A | absent / trusted_injected | same·self_group_hit·동일 A source entry |
| cross_group_probe | B | absent / trusted_injected | different·cross_group_isolated·B entry |
| forged_salt | B | source_group / trusted_overwritten | different·cross_group_isolated·같은 B entry |
| omitted_salt | B | absent / trusted_injected | different·cross_group_isolated·같은 B entry |
| post_control | B | absent / trusted_injected | different·self_group_hit·앞 B entry 재사용 |

request_outcome은 전부 accepted를 요구한다. 안전하게 blocked하는 구현은 유효한 대안일 수 있으나
첫 지원 프로파일 밖 UNKNOWN이며 취약/FAIL로 단정하지 않는다. source key·현재 소비 key의 관계이므로
post_control의 different는 바로 직전 B key와의 비교가 아니다. 단순 miss·지연·salt/key 차이·캐시 비활성만으로
정상 재사용과 격리를 입증할 수 없다. observation_detail과 첨부 locator/basis는 실제 key→lookup→entry→consumer와
출력·사건 순서에 대한 사람의 설명이며 도구가 첨부 내용까지 자동 해석·인증하지 않는다.

첨부는 **현재 EVD entry**의 artifacts.sha256 집합에 속해야 한다. payload의 파일 경로나 URL을 읽지 않는다.
LF/CRLF를 정규화해 다른 bytes를 원본 해시로 주장하지 않는다. 같은 bytes가 다른 entry에도 존재할 수 있으므로
SHA membership만으로 side·사건·신원 진위를 인증하지 않는다. import의 PLAN/Ref/side 보호와 사람이 검토하는
locator/basis를 함께 유지한다.

## 축별 판정과 보고

| 축 | 원본 신고 PASS의 상한 | 원본 FAIL/UNKNOWN |
|---|---|---|
| 보안 | 정책·범위·전체 쌍/6case·대표 신원·실제 소비 group/entry·첨부·정상 관문 완결 시 PASS 후보 | 보존. 상충/누락/지원 밖을 새 자동 FAIL로 바꾸지 않음 |
| 기능 | 보안 전제와 모든 inference=succeeded | 보존 |
| 성능 | 기준 없음으로 UNKNOWN | 비CPU의 원본 FAIL/UNKNOWN 보존. 명시 CPU는 기존 공통 규칙에 따라 UNKNOWN |

독립 격리가 완결됐다면 inference failed/not_attempted/unknown만으로 보안 후보를 지우지 않으나 기능 PASS는
보류한다. 정상 대조 실패·미시도 챌린지·필수 관측/설명·첨부 부족은 기존 INV-11/declared 관문이 추가로 적용된다.
dependency 오류도 상수 사유 코드로 PASS를 보류하고 신고 FAIL을 보존한다.

POLICY_CONTEXT:CACHE_GROUP_* 상수 코드와 제한 범위가 JSON/HTML에 표시된다.
raw credential/token/salt·payload·의존 예외를 사유에 복사하지 않는다. 정책 목록·정본 참조는 CheckDef의
정책 문맥/한계로 표시하고 HTML escape를 유지한다. EXAMPLE/DRAFT는 official=false다.
generic 관측 전체는 EVD hash에 포함되며 config hash에 응답 전체를 추가하지 않는다.
재판정은 기존 EVD/RES를 바꾸지 않고 새 판정 버전 RES를 추가하고 같은 as_of는 멱등이다.

## 검증 계획과 실제 결과의 구분

신규 [회귀](../tests/test_policy_m14.py)는 G01~G20의 다음 계약을 대상으로 작성했다.

- 세 타입/누락·false만·true·그룹 최소/중복/공백과 정책을 줄이지 않는 처리.
- 대표 신원·정본 Ref·foreign group/principal·client salt/shared credential·전체 역방향/6case.
- 실제 derived/consumed group/entry·post hit·trial/event/consumer·현재 entry 첨부 귀속.
- enum unknown과 형태 오류, inference의 축 분리, raw FAIL/UNKNOWN·CPU·정상/챌린지/설명/첨부 관문.
- /1/2/3 audit/update import·정확 LF/CRLF bytes·side·원본/Ref/hash·동일 as_of 재판정·JSON/HTML.
- 새 위치 반출/복원·저장 팩 judge/report 및 원본 보존.

설치된 wheel 검증은 저장소의 CI 검증 경로에서도 REQ-15 등록·미완결/legacy/partial·독립 FAIL을 확인한다.
최종 묶음의 Windows 기본 전체는2551통과/0실패/13skip/18제외·258.46초다. 새 설치본의 완결·누락·foreign·신고FAIL·partial/legacy와 이전 실제 wheel/팩20에서 만든 REQ15 legacy 세 축·REQ16 축 분리의 저장 팩 재판정을 확인했다. 실제 명령과 범위는 [묶음 검증](POLICY_RELIABILITY_BATCH.md#검증-기록), [PROGRESS](../PROGRESS.md)와 초안 PR을
따른다. 이 문서의 회귀 목록을 실제 엔진 검증으로 집계하지 않는다. 실제 엔진·Docker stub·두 이미지 비교·기업 정책
승인·운영/유료 자원·실제 캐시 관측은 이번 M14 검증 범위가 아니다.

## 팀 결정과 후속

기업 전체 격리 그룹, 교차 공유 예외 여부, 공개/비공개 분류 정본, 신원→집단·주입/덮어쓰기 책임 정본의
정확한 버전과 승인 역할이 필요하다. 대표 주체·진입점·consumer의 실제 trace를 수집/검토할 사람과
시험 권한·정상 대조·중단 조건도 정해야 한다. 값이 미정이어도 설계·순수 합성 회귀·설치본 검증은 가능하다.
public true/복잡한 허용 관계/공유 자격 뒤 사용자/안전 거부의 별도 프로파일과 REQ-17의 통계·위협·성능 기준은
추가 설계 후 구현한다. 자동 캐시 실행기는 별도 계측·안전 시험 계획과 함께 검토한다.
