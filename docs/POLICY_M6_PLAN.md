# M6 — OBS-11 정책·증거·판정 계약 설계

## 1. 상태와 검토 기준

이 문서는 **구현 전 설계 제안**이다. M6 문서 작성 승인은 아래 정책·타입·판정·버전 변경이나
기업 운영 기준의 승인이 아니다. 다음 구현 프롬프트는 §11에 있으며 이번에는 실행하지 않는다.
[SPEC](../SPEC.md)의 현재 계약과 불변식 INV-01~18은 유지한다.

- 기준: `codex/gua-policy-m5` / `e353cd02b2709ab063355d881092ce249fa1b8e9`.
- fetch 후 로컬/원격 차이 0/0, 작업 트리 clean. [M5 PR #7](https://github.com/dohashelby/gua/pull/7)은
  open/draft·미병합이므로 `codex/gua-policy-obs11-m6-design`의 base는 M5다.
- 원격 main은 `d9e9729116eeb16299247ecb9e7df8ff16d882ec`. main에 M5가 반영됐다고 가정하지 않는다.
- 현재 팩: PACK-BASELINE@12/DRAFT, 해시 `f3cada3bc4d193f8f4e834a11adea8e4387e1e340a79f19705b3fa44d1e0783c`.
  77점검, 9점검/15파라미터, 자동 실행/판정 연결 5개, 카탈로그 303결정/595연결·a14/b9/c165/d115.
- 현재 버전: declared 0.4.0, manual-entry 0.3.0, 양식 `guard-manual-evidence/2`,
  기존 항목 관측 `guard-policy-evidence/1`. OBS-01/11은 정책 선언·실행기·판정기 연결이 없다.
- M6 변경은 이 문서·[M5 후속 링크](POLICY_M5_PLAN.md)·[결정 기록](../DECISIONS.md)·
  [진행 기록](../PROGRESS.md)뿐이다. 제품·SPEC·테스트·팩·CSV·설명 JSON·정책·증거·원문은 보존한다.

POLICY_PROMPT.md의 3점검/Phase 1 커밋 금지·null 양식은 과거 단계다. M1~M5의 실제 계약을
대조하며, 이번 사용자의 문서 커밋·푸시·초안 PR 지시를 따른다. 실제 기업 값은 여전히 정하지 않는다.

## 2. M5 검토 결과와 증거 수준

| 확인된 사실 | 근거 | 해석·한계 |
|---|---|---|
| LIF-07은 수락 모순과 추론/순서 방법 차이를 분리해 확인된 준비 전 수락 FAIL을 보존 | [policy_items.evaluate](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/src/guard/judges/policy_items.py#L10), [회귀](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/tests/test_policy_m5.py#L39) | 같은 사건의 수락/차단 충돌·순서 미확인·첨부 미연결은 확정 위반으로 쓰지 않는다. 모순 PASS는 보류 |
| SUP-01/02의 required str·공백 보류·버전 0.4.0·과거 정의 호환 | [DeclaredJudge](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/src/guard/judges/declared.py#L23), [회귀](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/tests/test_policy_m5.py#L87) | 승인 시스템 이름은 문맥이며 권한·승인 진위 증명이 아니다 |
| 정책 문맥 이유를 RPT 한계에 전달, HTML escape·official false 검증 | [build_report](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/src/guard/core/report.py#L125), [회귀](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/tests/test_policy_m5.py#L143) | 설명/항목 검증을 실제 엔진·승인 체계 실증으로 세지 않는다 |
| 과거 객체 보존·재판정·타입/키/FAIL 계약을 유지하는 범위의 변경 | [M5 계획](POLICY_M5_PLAN.md), [진행 기록](../PROGRESS.md) | 이번 정적 검토에서 M5 완료를 뒤집을 중대한 회귀는 확인하지 못했다. 저장소 전체 무결함을 입증한 것은 아니다 |

| 검증 출처 | 결과 | M6와 구분 |
|---|---|---|
| M5 최종 Ubuntu/Python 3.11 CI | [PR 39567720670](https://github.com/dohashelby/gua/actions/runs/39567720670): 659 passed / 18 deselected, 43.52초; [push 39567715343](https://github.com/dohashelby/gua/actions/runs/39567715343): 659 passed / 18 deselected, 42.36초. 실패·skip 없음, frozen sync·wheel 성공 | M5 최종 SHA의 과거 실행. M6의 새 실행 수가 아님 |
| M6 프롬프트 작성 전의 별도 M5 재검토 | tests/test_policy_m5.py 32 passed, 1.38초 | 앞선 대화의 재검토 실행. 이번 M6 문서 작성 중 재실행하지 않음 |
| M5 설치본 검증 기록 | 저장소 밖 22명령, 실제 M4 wheel+팩11 UNKNOWN → M5 보안 FAIL, 과거 EVD/RES 보존·멱등·verify | ignored installed-smoke.json의 과거 합성 계약 검증. 새 설치본 실증 아님 |
| M6 | 문서 완성 후 참조·정합성·바이트/계수 검증, 커밋 후 자동 CI 확인 | 실제 명령·결과는 PROGRESS의 M6 기록. 새 OBS 실행/판정 테스트는 없음 |

기존 기본 제외 18개는 Docker/실제 엔진 시험이다. 과거 macOS 실측 3점검과 신규 모의 검증
2점검은 같은 수준이 아니다. 과거 exposed/isolated 증거의 참조 artifact 각 1개 누락도
M6에서 복구하지 않았다. M6는 실제 승인·관측 체계, 두 실제 이미지, AWS 실증을 수행하지 않는다.

## 3. OBS-01과 OBS-11의 의미 경계

정본 근거는 원문 [OBS-01 행](../reference/framework/data/checklist.json#/observability_assessment_draft/0),
[OBS-11 행](../reference/framework/data/checklist.json#/observability_assessment_draft/10)과 변환된
[OBS-01](../packs/PACK-BASELINE/checks/OBS-01.yaml), [OBS-11](../packs/PACK-BASELINE/checks/OBS-11.yaml)이다.
원문 `#/...`는 0 기반 JSON Pointer다. legacy는 추적용이며 점검 통합이나 정책 동일성의 증거가 아니다.

| 경계 | OBS-01 | OBS-11 | 공유/독립 권고 |
|---|---|---|---|
| 질문·범위 | 필수 접근/관리/승인 사건의 주체·대상·실행 연결 | 필수 관측 경로의 중단·누락·시간 불확실성을 정상 처리하지 않는가 | 서로 다른 CheckDef·RES 유지 |
| 정책 | 필수 사건·식별값·책임 역할 | 필수 기록·기한·시간 오차·장애 대응 | 목록 자동 복사/동일 강제 없음 |
| 정상 대조 | 허용/거부 사건이 시험 ID로 서로 연결 | 필수 시험 사건이 기한 안에 도착 | 원본 사건·대상 ID는 공유 가능, 각 정상 대조는 별도 확인 |
| 챌린지 | Gateway/배포/엔진/승인 기록을 주체·대상·실행·정책판과 대조 | 합성 사건, collector 중단/쓰기 거부 중 하나, 누락/경보/종속 판정 확인 후 복구 | 같은 첨부라도 무엇을 뒷받침하는지 별도 연결 |
| 필수 관측 | 필드·원 기록/시각·실행/정책/승인 ID | 독립 발생 증거·수집 상태/시각·누락 경보/평가 상태 | 로그 수신만으로 승인 일치·수집 건전성 확정 불가 |
| 실패 | 발생이 확인된 필수 기록 부재 또는 잘못된 주체/대상 연결 | 확인된 필수 누락을 정상 처리하거나 필요한 경보를 내지 않음 | 같은 누락이라도 서로 다른 실패 조건 |
| 미확인 | 필수 사건/필드 미정, 발생/수집 경로 미확인 | 기한/시계 기준·발생 미확인, 누락 시 종속 시험 미확인 | 불충분 근거를 PASS나 해당 없음으로 바꾸지 않음 |
| legacy | Q-R05-15·Q-C11 / SC-23-03·SC-23-02 | Q-C11 / SC-23-02 | 공유 Q-C11/SC-23-02만으로 정책을 합치지 않음 |

**권고 POL-M6-REL:** 우선 OBS-11의 목록/규칙을 독립 관리한다. 사건 식별자·현재 첨부를 재사용할 수
있으나 OBS-01 연결의 완전성, OBS-11 수집/불확실성 처리의 완전성을 각각 증명한다. 공유 카탈로그
후보가 OBS-09/10/11에 연결됐다는 이유로 다른 점검에 새 필수값을 일괄 소급하지 않는다.
정책상 동일/부분공유 여부는 미결이며 실제 팀 승인으로 기록하지 않는다.

## 4. 정책 분해와 최소 지원 형태 제안

원문 정책 결정 역할은 카탈로그의 “적용 조직의 정책 승인 담당”이다. 아래 역할은 필요한 역할의
제안이며 실제 담당자·승인자를 지정한 것이 아니다. 모든 후보는 [CSV](policy_catalog.csv)의
proposed_policy_id로 추적하며 M6에서는 분류/경로/타입/내용을 바꾸지 않는다.

| 후보 ID (P-DRAFT- 접두 생략) / 현재 분류 | 정책 의미·소유 데이터 | 최초 구현 권고·소비·누락 처리 | 기업 결정 / 기술 준비 |
|---|---|---|---|
| AUDIT-REQUIRED-RECORDS / b | POL의 필수 사건/기록 식별 목록 | 필수. required_records:list[str] 후보. 목록 없거나 빈/모호하면 항목 집합 UNKNOWN | 실제 범위·명명·OBS-01 관계 / 정확 일치·중복/빈 집합 검증 |
| AUDIT-REQUIRED-COLLECTION-DEADLINE / c | POL의 사건별 수집 완료 기한 | 필수. collection_deadline_ms:dict[str,int] 후보. 해당 기준 없으면 기한/누락 확정 보류 | 단위·기산점·도출 근거·실제 값 / 경계·부분 입력 계약 |
| AUDIT-CLOCK / c | POL의 사건별 허용 시간 불확실성 한도 | 필수. max_clock_uncertainty_ms:dict[str,int] 후보. 허용 한도와 실제 오차/시계 출처 없으면 시간 판정 UNKNOWN | 허용 오차 근거 / 허용 한도와 EVD 실제 오차 분리 |
| AUDIT-DETECT / c | POL의 사건별 누락 식별 기한 | 최초 필수 범위. detection_deadline_ms:dict[str,int] 후보. 미정이면 적시 식별/무식별 확정 보류 | 기산점·위반 의미·실제 값 / 수집 완료 기한과 별도 계산 |
| AUDIT-ALERT / c | POL의 사건별 필요한 누락 경보 기한 | 최초 필수 범위. alert_deadline_ms:dict[str,int] 후보. 필요성/기산점/기한 없으면 미경보 FAIL 확정 불가 | 필요 조건·경보 수신점·실제 값 / 경보 생성과 전달 구분 |
| AUDIT-RECOVERY / c | POL의 경로별 복구 기한 | 기한 검증은 후속. 최초에는 복구 사실·한계만 EVD로 보존, 복구 적시성 미검증 표시 | 복구의 정의·기한·영향 / 복구 확인·실패 기록 형태 |
| AUDIT-OUTAGE / d | POL의 사건 중요도별 운영 유지/제한/중단·승인 절차 | 후속. 최초는 관측/종속 판정 표시만, 운영 제어는 구현하지 않음 | 대응/권한·위험 수용 / 운영 미결을 자동 지속/중단으로 바꾸지 않는 경계 |
| AUDIT-FORMAT / d | POL의 신뢰 필드·기록/해석 계약 | 전체 형식 정책은 후속. 최초 첨부의 사건/대상 식별과 근거 범위는 필수 | 허용 형식·필드 의미 / 구조 검증·오류/누락 구분 |
| AUDIT-ROLES / d | POL의 writer/reader/admin·독립 검토 권한표 | 권한 집행 검증은 후속. 최초 독립 발생/조회 근거의 출처 설명은 필수 | 실제 역할·독립 근거 승인 / 자기 신고·인증 한계 표시 |
| AUDIT-INTEGRITY / d | POL의 무단 변경/삭제 제한·독립 변조 탐지 요구 | 전체 무결성 통제는 후속. 최초 현재 첨부 hash/locator/basis 연결은 필수 | 신뢰/변조 탐지 요구 / hash 일치와 작성자 인증 구분 |
| AUDIT-CAPACITY / c | POL의 저장 용량·도출 기준 | 후속. 실제 사용량은 ENV/관측, 시험 크기 상한은 실행 설정 | 부하·보존·실제 값 / 실행 예산과 정책의 구분 |
| AUDIT-LOSS / c | POL의 중요도별 유실 허용/검출 기준 | 후속. 필수 기록 누락을 허용 손실률로 정상화하지 않음 | 필수 의무와 유실 기준 관계 / 누락·불확실성 보존 |

출처는 각 후보의 CSV source_refs와 원문 OBS-11의 policy_to_decide/enterprise_decisions/
numeric_values_pending이다. 현재 12후보 모두 policy_params 미등록이며 자동 소비가 없다.
미등록 후보 전부에 현재 NO_POLICY_VALUE가 발생한다고 표현하지 않는다.

### 4.1 소유권과 타입의 실제 한계

현재 [Policy.values](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/src/guard/models/objects.py#L171)는 dict의 Any 값을 보관할 수 있지만
[check_policy_value](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/src/guard/core/select.py#L35)가 지원하는 것은 str/int/bool/list[str]뿐이다.
보관 가능성과 검증 지원은 다르다. 사건별 수치 표는 현재 타입으로 의미 있게 검증할 수 없다.

| 자료 | 위치·소유자 | 섞으면 안 되는 것 |
|---|---|---|
| 무엇을 요구하는가·허용 한도 | POL, 정책 승인 담당이 결정 | 실제 도착/오차·실험 예산을 정책값으로 복사하지 않음 |
| 배치된 수집 경로·실제 시계 구성 | ENV 또는 출처를 가진 EVD | 없는 ENV 키를 현재 지원 사실로 주장하지 않음 |
| 시험 대상·주입 방법·관측 예산·원본 조회 방법 | 실행 설정/시험 기록 | 기업 기한·운영 대응을 실행 설정으로 대체하지 않음 |
| 발생·도착·장애·실제 오차 상한·수신·종속 상태 | EVD, 사람 신고는 DECLARED | 자기 신고 boolean/시간을 자동 실측이나 진위 보증으로 승격하지 않음 |
| 자료 부족/위반/준수 평가 | 순수 judge → RES | 수집기나 보고서가 시험 사실을 만들어내지 않음 |

**기술 권고 POL-M6-SHAPE(미승인):** 최초에는 required_records와 사건별 정수 map 4개를
서로 다른 키로 둔다. `dict[str,int]` 한 타입만 추가하는 제한 확장을 제안한다. 현재 지원은 아니다.
bool을 int로 받지 않고 문자열 키/정수 값만 타입 검사하며, 공백·음수·범위·키 집합은 의미 검증한다.
수집 기한은 양수, 나머지는 명시된 비음수 정수만 허용하는 제안이며 0을 미입력으로 취급하지 않는다.
실제 0의 업무 유효성/경계 의미는 POL-M6-TIME 결정 대상이고 기본값으로 넣지 않는다.

| 형태 선택지 | 장점 | 비용·채택 조건 |
|---|---|---|
| 권고: required_records + 사건별 정수 map | c 후보의 서로 다른 기한을 보존. 현재 POL 객체/카탈로그의 독립 경로/설명 생성 구조에 맞음. 새 DB/객체 불필요 | 키 집합 중복이 생기므로 각 map 키를 필수 목록과 대조하는 공통 순수 검증 1곳이 필수. 부분 입력은 보관 가능하되 관련 PASS 보류. 미등록 키는 거부 |
| 단일 사건별 규칙 행 목록 | 항목별 값을 한 행에 모아 중복 키 집합 감소 | 새 구조 타입·중첩 경로/카탈로그 설명 연결·양식 검증을 함께 설계해야 함. 최초 변경 폭이 더 큼 |
| 단일 공통 수치 4개 | 기존 int 타입 재사용 가능 | 모든 사건에 같은 기준이라는 기업 확인이 있을 때만 별도 제한 프로파일로 고려. 편의상 사건별 정책을 축소하지 않음 |

권고를 채택해도 숫자 도출/승인·실제 값은 미결이다. c/d를 a로 옮기는 일은 새 소비자·도출
설명이 검토되고 실제 선언이 추가될 때만 한다. M7의 정확한 선언/분류 계수는 구현 후 재산정하며
M6의 9점검/15경로·a14/b9/c165/d115를 미리 바꾸지 않는다.

## 5. 최소 수동 관측 계약 제안

### 5.1 경계·버전·식별

M7은 실제 collector를 제어하는 실행기가 아니라 **저장된 자기 신고 근거를 대조하는 기능**으로
제한한다. 자동 실측·전사 수집 건전성·실제 모든 종속 RES의 전파를 보증하지 않는다.

- 제안 양식 `/3`에 OBS-11 전용 `audit_evidence`를 추가하고, 저장은 기존 EVD.observations의
  `declared_audit_evidence` 한 항목으로 한다. 제안 관측 계약명은 `guard-audit-evidence/1`이다.
  객체 종류·DB·Verdict는 추가하지 않는다. manual-entry 0.4.0, declared 0.5.0을 다음 버전 후보로 둔다.
- 기존 `/2`에 새 필드를 끼워 넣으면 구형 importer가 무시할 수 있으므로 `/3`을 권고한다.
  `/1`·`/2` 읽기는 유지하되 새 audit_evidence를 숨겨 전달하면 거부하는 전환을 제안한다.
  기존 policy_evidence의 계약 `guard-policy-evidence/1`은 변경하지 않고 `/3`에서도 그대로 사용한다.
- PLAN·POL·팩의 정확한 Ref, 선정 check/side, 현재 항목 첨부를 고정한다. 식별자는 자동 trim/변환하지 않는다.
  `(record_id, case)`는 양식 내 유일하며 같은 case의 사건은 `trial_id/event_id/target_scope/path_id`로 묶는다.
  최초에는 항목×case당 사건 하나다. 여러 반복 사건·복잡 조건/역할별 기록은 후속 범위다.
- 새 구조는 해당 OBS-11 선언이 있는 정의에만 적용한다. 구형 증거에 없는 새 관측은 새 RES에서
  UNKNOWN 사유가 되지만 EVD/기존 RES는 보존한다. 정책 선언 없는 과거 팩의 동작에 소급하지 않는다.

### 5.2 최소 필드와 주장별 근거

| 필드 묶음 / 제안 타입 | 의미·조건 |
|---|---|
| record_id / str, case / normal·collection_fault·clock_uncertain | 정확한 필수 항목·고정 시험 case. case 이름은 업무 기본값이 아닌 시험 어휘 제안 |
| trial_id, event_id, target_scope, path_id / str | 발생 사건·시험 대상·관측 경로를 연결. 정상/주입을 같은 사건으로 합치지 않음 |
| occurrence / confirmed·unknown; occurrence_source / str | 별도 발생 근거. 확인 문자열만으로 충분하지 않으며 독립 근거·이유 연결 필수 |
| record / received·absent·unknown; collection_source / str | 대상 기록 도착 주장. absent는 완전한 종료 관측과 기한 조건 없으면 확정 누락이 아님 |
| occurred_at, received_at, window_end_at / RFC3339 UTC 또는 null | 기산점·수신·실제 관측 종료 시각. 미도착 received_at은 null이며 시계 현재값으로 보충하지 않음 |
| clock_basis / bounded_common_time·unknown; clock_source / str | 공통 시간축 변환과 오차 근거. 서로 다른 시계/재기동 monotonic 값을 직접 빼지 않음 |
| time_errors_ms / dict[str,int] | 제공한 각 시각의 실제 절대 오차 상한. 비음수·bool 금지·0은 명시 근거 필요. POL의 허용 상한과 구분 |
| collection_window / complete·incomplete·unknown | 경로/대상/필수 기록을 빠짐없이 조회한 종료 창의 주장. 조회 범위·조회 경로·완전성·독립성 근거 필요 |
| collector / healthy·interrupted·write_denied·unknown | 실제 상태. 계획한 중단 방법은 실행 설정, 관측된 상태는 EVD |
| missing_handling / identified·treated_normal·unknown; detected_at / timestamp 또는 null | 누락 표시·처리와 실제 식별 시각. 로그 미발견만으로 treated_normal을 추정하지 않음 |
| alert / received·absent·unknown; alert_received_at·alert_window_end_at / timestamp 또는 null; alert_window / complete·incomplete·unknown | 지정 수신점 도착·완전한 부재 조회. 생성 로그만으로 전달/수신을 증명하지 않음 |
| dependent / {test_id:str, execution_status:COMPLETED·ABORTED·NOT_RUN·unknown, security:PASS·FAIL·UNKNOWN 또는 null} | 해당 누락 증거를 필요로 하는 종속 시험의 신고된 결과. 현재 Store의 실제 RES를 자동 조회/수정하는 계약이 아님 |
| recovery / restored·not_restored·not_attempted·unknown | 주입 시험의 복구 관측. SLA 판정은 후속이며 복구 실패는 한계에 남김 |
| evidence / list[{claim,attachment,locator,basis}] | claim은 occurrence/collection/window/clock/collector/detection/alert/dependent/recovery 중 하나. 현재 entry 첨부에만 연결; 저장 시 attachment 대신 sha256 |
| limitation / str 또는 null | 근거 제한. 관련 PASS를 보류하되 독립 확인된 위반은 보존 |

독립성은 파일 개수나 파일 해시 차이로 판정하지 않는다. 같은 고장 수집 경로의 “발생했다” 로그는
독립 발생 증거가 아니다. 별도 송신/동작 결과 등 승인된 독립 근거와 경로 설명을 연결해야 한다.
한 파일에 여러 주장이 있어도 locator/basis로 각각 연결할 수 있다. importer는 연결·형태만 검증하며
실제 작성자·서명·증거 진위를 인증하지 않는다. 이를 EVD/RPT의 한계로 표시한다.

### 5.3 case별 필요한 사실

| case / 제안 적용 | 완전성 판단에 필요한 것 | 요구하지 않거나 다른 의미인 것 |
|---|---|---|
| normal / 필수 항목마다 | 독립 발생·정상 경로/대상·기한 내 수신·시각/오차·정상 대조와 첨부 연결 | 장애/누락 경보·복구를 가짜 성공으로 채우지 않음 |
| collection_fault / 필수 항목마다 | 실제 중단 또는 쓰기 거부 주입 시도·독립 발생·기한 후 조회 창·확정 누락·누락 식별·필요 경보 수신·종속 UNKNOWN 처리·복구 근거 | 모든 장애 방법을 시도했다고 가정하지 않음. 하나만 검증했다면 그 범위를 표시 |
| clock_uncertain / 필수 항목마다 | 독립 발생·알려진 유한 오차 구간이 기한과 겹친다는 근거·그 불확실성을 시험 대상이 UNKNOWN 처리한 근거·챌린지 시도 연결 | 이 case의 기한 충족/누락 자체는 확정하지 않음. 성공은 불확실성을 올바르게 표시한 처리 범위만 |

case 집합·주입 범위는 POL-M6-COVERAGE의 미승인 권고다. 최초 판정이 요구하는 것은 조건별
필수 사실이며 모든 case의 모든 필드를 무조건 채우게 하지 않는다. clock_uncertain이라는 이름만
붙이고 시간 근거가 없는 경우는 이 case를 성공으로 인정하지 않는다. 정상 대조의 시간 근거까지
미확인인 경우 역시 전체 PASS를 보류한다. 정상 대조/챌린지는 기존 EVD 공통 필드에도 사실대로 기록한다.

null/생략/unknown은 성공이 아닌 불충분 관측이며 해당 이유를 남겨 저장한다. 잘못된 필드/타입/
enum·시간 형식·항목/side·중복 record/case·다른 Ref·현재 entry 밖 첨부는 배치 오류로 제안한다.
서로 다른 근거의 사실 충돌은 덮어쓰지 않고 관련 주장만 UNKNOWN으로 평가한다. 발생 시각 역전처럼
의미가 모순되는 신고는 자동 보정하지 않는다. 판단에 필요한 사실이 충돌하면 그 위반을 확정하지 않지만
다른 사건·다른 독립 전제의 확인된 FAIL은 지우지 않는다.

### 5.4 문서용 빈 형태와 합성 예시

아래는 **현재 guard에 넣을 수 없는 /3 설계 예시**다. 양식 생성기는 실제 Ref를 채워야 한다.
정책의 미입력은 현재처럼 키 생략이며 `values.OBS-11: {}`다. POL 숫자에 null 기본값을 넣지 않는다.
관측의 null은 미확인으로 허용한다. 아래 예시는 제품·Store에 저장하지 않는다.

```yaml
template: guard-manual-evidence/3
plan: {id: "<PLAN id>", revision: 1, sha256: "<exact hash>"}
policy: {kind: POL, id: "<POL id>", revision: 1, sha256: "<exact hash>"}
pack: {kind: PACK, id: "<pack id>", revision: 1, sha256: "<exact hash>"}
checks:
  - check_id: OBS-11
    side: single
    # 기존 verdicts/normal_control/challenges/notes/operator/attachments 필드는 유지
    audit_evidence:
      contract: guard-audit-evidence/1
      records:
        - record_id: EXAMPLE-required-record
          case: collection_fault
          observations:
            trial_id: null
            event_id: null
            target_scope: null
            path_id: null
            occurrence: unknown
            occurrence_source: null
            record: unknown
            collection_source: null
            occurred_at: null
            received_at: null
            window_end_at: null
            clock_basis: unknown
            clock_source: null
            time_errors_ms: {}
            collection_window: unknown
            collector: unknown
            missing_handling: unknown
            detected_at: null
            alert: unknown
            alert_received_at: null
            alert_window_end_at: null
            alert_window: unknown
            dependent: {test_id: null, execution_status: unknown, security: null}
            recovery: unknown
          evidence: []
          limitation: null
```

EXAMPLE 합성 조건 C=100ms, D=20ms, A=30ms, U=2ms는 계산 예시이며 기업 기준이 아니다.
발생 중심 t0=0ms, 발생/수신 각각의 오차 1ms일 때 수신98ms는 구간 상한100ms이므로 기한 내,
수신100ms는 [98,102]ms로 불확실, 수신103ms는 하한101ms이므로 기한 후다.
이 예시에서 누락을 확정하려면 독립 발생·완전한 관측 창·수신 부재 근거가 별도로 필요하다.

## 6. 시간·누락·판정의 제안 알고리즘

### 6.1 시간 기준

다음은 POL-M6-TIME의 기술 제안이며 승인된 업무 기준이 아니다.

1. 시각은 근거가 있는 공통 UTC 축의 정수 밀리초로 정규화한다. 소수초3자리 초과는 자동 절삭/반올림하지 않고
   제안 형식의 오류로 거부한다. timezone 없는 시각·유한하지 않은
   오차 상한·근거 없는 시계 보정은 채택하지 않는다. 판정기는 현재 시각에 접근하지 않는다.
2. collection C는 독립 확인한 발생 시각 t0부터 수신까지다. detection D와 alert A는 각각
   **수집 기한 t0+C부터** 식별/지정 수신점 도착까지의 독립 예산으로 제안한다.
   실제 늦은 식별 시각부터 A를 세어 경보 지연을 숨기지 않는다. 생성/전달·복구 기한도 합산하지 않는다.
3. 각 시각 t는 중심과 오차 e의 구간 [t-e,t+e]로 다룬다. 기준 b와 대상 x의 차이는
   `[x-b-(ex+eb), x-b+(ex+eb)]`다. 합성 오차가 해당 POL 상한 U 이내이고 근거가 있을 때만 비교한다.
4. 예산 B에 대해 차이의 상한≤B면 기한 내, 하한>B면 기한 후, 그 외는 UNKNOWN이다.
   경계 동등은 포함하는 제안이며 오차 0은 명시 근거가 있을 때만 인정한다. 음수 차이/역순은 보정하지 않는다.
5. 완전한 관측 창 종료의 하한이 기한 상한을 넘고, 독립 발생·대상 기록 부재·조회 범위가 확인돼야
   누락을 확정한다. collector 중단만으로 하류에 기록이 없다고 확정하지 않는다.
6. 경보 부재에는 지정 수신점의 별도 완전한 조회 창과 경보 기한 경과가 필요하다. 빈 파일·조회 실패·
   미종료만으로 부재라 하지 않는다. alert_window_end_at을 별도로 사용하며 collection_window와
   alert_window의 완전성 근거를 구분한다.

수집 기한은 B=C, 식별/경보는 발생 기준으로 각각 B=C+D, B=C+A로 환산할 수 있다. D/A의
기산점·경계 동등·U의 합성 의미는 팀 판단 전에 확정하지 않는다. 사건 순서만으로 경과 시간을
얻을 수 없으며 상한 없는 시간 불확실성을 0이나 허용값으로 채우지 않는다.

### 6.2 판정·축·의존 경계

- 기록의 관측 충족 상태는 진단 이유/누락 정보로 표현하고 새 Verdict를 만들지 않는다. OBS-11의 RES와 구분한다.
- 새로 도출할 security FAIL은 해당 필수 항목의 유효한 정책 전제·독립 발생·확정 누락에 더해,
  이를 정상/종속 PASS 처리한 직접 근거 또는 필요한 경보의 확정 부재가 있을 때로 제한하는 제안이다.
  지연만·주입한 장애만·로그를 찾지 못한 사실만으로 FAIL을 만들지 않는다.
- PASS는 사람의 security PASS를 유지하는 상한이며 UNKNOWN을 승격하지 않는다. 모든 필수 항목에
  case별 충분한 근거와 기존 정상 대조 성공·모든 challenge 시도·구조화 필수 관측 관문 INV-11을 요구한다.
  collection_fault의 기록 미충족과 OBS-11의 올바른 장애 처리 충족은 함께 존재할 수 있다.
- clock_uncertain의 적절한 UNKNOWN 처리는 시간 확정을 주장하지 않고 불확실성 처리 범위만 충족한다.
  일반 case의 시각 누락·정책 불명은 여전히 PASS를 막는다. 불확실성에 대한 잘못된 PASS를 자동 FAIL로
  도출하는 것은 최초 범위 밖으로 두고 UNKNOWN 이유로 남긴다. 확장하려면 실패 기준과의 별도 합의가 필요하다.
- 최초 신규 도출은 security만이다. functional/performance의 신고 PASS는 UNKNOWN 상한, 기존 신고
  FAIL은 유지한다. 상위 CPU 비교 등의 기존 제약을 바꾸지 않고 시간 예산 검증을 성능 PASS로 옮기지 않는다.
- dependent는 사람이 제공한 종속 시험 결과 서술을 검사한다. 누락에 의존하는 판단은 UNKNOWN이
  필요하지만 독립 근거의 기존 FAIL은 지우지 않는다. 실제 RES를 수정하거나 집계에서 빼지 않는다.
  외부 결과/의존 관계가 불명확하면 전파 검증 완료로 하지 않고 UNKNOWN을 남긴다.
- 비선정은 EVD/RES를 만들지 않고 미실행/ABORTED는 UNKNOWN, 적용 불명은 기존 PLAN대로 처리한다.
  빈 목록·미확인·장애에서 NOT_APPLICABLE을 만들지 않는다. 예외는 표시하며 FAIL을 바꾸지 않는다.

## 7. 판정표와 의미 있는 계약 테스트

공통 전제 P: 해당 항목의 필수 의무·기한/시계 의미·필요 경보 조건이 확정되고 정확한 POL/팩 Ref에
고정돼 있음. 아래는 **예상 계약**이며 실행 결과가 아니다. `유지 가능*`는 해당 case의 security
상한일 뿐이다. 전체 항목×case·필수 근거·기존 INV-11을 모두 만족하고 신고가 PASS일 때만 전체
security PASS를 유지할 수 있다. 기능/성능 신규 상한은 UNKNOWN이며 신고 FAIL은 보존한다.

| ID / 사례 | 필요한 전제·독립 관측 | OBS-11 security 제안 | 기록 충족 / 종속 처리 | 이유 후보·완료를 입증할 테스트 |
|---|---|---|---|---|
| T01 정상 기한 내 수신 | P, 독립 발생·정상 경로·수신/시계·첨부, normal 성공 | 유지 가능* | 충족 / 종속 결과 자동 변경 없음 | NORMAL_LINKED; normal만 채우면 전체 PASS는 안 됨 |
| T02 사건 발생 미확인 | 수신 로그만 있고 독립 발생 없음 | UNKNOWN | 미확인 / 검증 미완료 | OCCURRENCE_UNCONFIRMED; 발생 불명에서 부재 FAIL 금지 |
| T03 기한/시간 기준 미정 | C/U 또는 의미가 없거나 시계 출처 없음 | UNKNOWN | 시간 충족 미확인 / UNKNOWN 필요 범위 표시 | TIME_POLICY_UNRESOLVED; 누락 값 임의 보충 금지 |
| T04 관측 창 미종료 | 발생은 확인, 수신 없음, 창 incomplete/종료가 마감 이전 | UNKNOWN | 부재 미확인 / 관련 판단 UNKNOWN | WINDOW_OPEN; 판정 시각 경과만으로 누락 확정 금지 |
| T05 정확한 경계 전/동일 | 오차 근거 포함 구간 상한≤C | 유지 가능* | 기한 충족 / 자동 전파 없음 | TIME_WITHIN; 상한이 C인 경계 포함 검사 |
| T06 경계 후 수신 | 구간 하한>C, 해당 기록은 나중에 수신 | UNKNOWN, 지연만으로 FAIL 아님 | 기한 충족 안 됨 / 관련 판단 UNKNOWN | COLLECTION_LATE; 지연과 보호 실패 분리 |
| T07 경계 겹침·일반 case | 알려진 유한 오차 내 차이 구간이 C와 겹침 | UNKNOWN | 기한 미확인 / UNKNOWN | TIME_UNCERTAIN; 중심값만으로 PASS/FAIL 금지 |
| T08 정책 허용 오차 초과 | 실제 합성 오차>U | UNKNOWN | 기한 미확인 / UNKNOWN | CLOCK_OUT_OF_PROFILE; U로 실제 오차를 잘라내지 않음 |
| T09 clock_uncertain 주입 올바른 처리 | P, 의도된 겹침이 근거로 확인, 대상 UNKNOWN·시도/정상 대조 연결 | 유지 가능*, 시간 자체는 미확인 | 시간 미확인 / UNKNOWN 처리 확인 | UNCERTAINTY_HANDLED; 명칭만 적은 case와 구분 |
| T10 불확실성에 잘못된 PASS | 겹침 근거·대상 PASS 확인, 확정 누락은 아님 | 최초 UNKNOWN·보호 실패 한계 표시 | 미확인 / PASS 처리 문제 표시 | UNSUPPORTED_CERTAINTY; 추가 FAIL 규칙은 별도 승인 |
| T11 누락 확정만 존재 | P, 독립 발생·기한 경과·완전한 별도 조회·부재 근거 | UNKNOWN | 관측 미충족 / UNKNOWN 필요 | REQUIRED_RECORD_MISSING; 누락 사실만으로 OBS FAIL 금지 |
| T12 누락을 정상 처리 | T11 + 명시적 정상/종속 PASS 처리의 연결된 직접 근거 | FAIL | 관측 미충족 / 잘못된 PASS, RES 수정 없음 | MISSING_TREATED_NORMAL; 다른 항목 gap이 이 FAIL을 지우지 않음 |
| T13 필요한 경보 확정 부재 | T11 + 경보 필요 조건·A·수신점 창 종료/완전성·부재 근거 | FAIL | 관측 미충족 / 종속 UNKNOWN이어도 경보 실패 유지 | REQUIRED_ALERT_ABSENT; 알림 창 미종료와 구분 |
| T14 적절한 장애 처리 | 실제 fault 시도·T11·기한 내 식별/수신 경보·종속 UNKNOWN·복구 근거 | 유지 가능* | 관측 미충족 / UNKNOWN 처리 확인 | OUTAGE_HANDLED; 누락을 기록 충족으로 바꾸지 않음 |
| T15 장애 여부 자체 미확인 | 중단 계획만 있고 상태 근거 없음 | UNKNOWN | 미확인 / 검증 미완료 | COLLECTOR_UNCONFIRMED; attempted와 성공 관측 구분 |
| T16 경보 생성만 확인 | 수신 근거 또는 별도 부재 창 불충분 | UNKNOWN | 관측 미충족 가능 / 경보 전달 미확인 | ALERT_UNCONFIRMED; 생성 로그로 전달 성공 주장 금지 |
| T17 식별/경보 기한 초과 | 각 기산점·시간 근거 확인 | UNKNOWN; 확정 무경보/정상화이면 T12/13 FAIL | 적시 처리 미충족 / 관련 판단 UNKNOWN | HANDLING_LATE; 복구 기한으로 대체 금지 |
| T18 일부 항목/case/근거 부족 | 정책 목록은 명확, 필수 사실/첨부 연결 부족 | UNKNOWN, 독립 T12/13 FAIL은 보존 | 해당 기록 미확인 / 전파 미검증 | CASE_OR_BASIS_MISSING; 한 첨부로 모든 주장 충족 가정 금지 |
| T19 같은 사건의 사실 충돌 | 충돌하는 발생/수신/정상 처리 근거 | 해당 전제 UNKNOWN | 해당 주장 미확인 | EVENT_CONFLICT; 독립 사건의 T12/13 FAIL은 유지 |
| T20 빈/중복/공백 식별자·map 불일치 | 타입은 맞아도 집합 불명/키 누락 | 새 PASS 보류; 명확한 다른 항목 위반 보존 | 목록/해당 정책 미확인 / 적용 제외 아님 | POLICY_SET_UNRESOLVED; []와 키 생략·null 타입 오류 구분 |
| T21 신고 FAIL·UNKNOWN | 충분/불충분 관측 조합 | 신고 FAIL 보존·UNKNOWN 비승격; 새 확인 위반이면 UNKNOWN→FAIL 가능 | 출처/관문 유지 | FAIL_KEPT; 근거 변경 없이 재판정 멱등 |
| T22 구형 증거/과거 팩 | 실제 이전 wheel·보존 팩으로 만든 EVD/RES | 신규 선언 정의의 근거 없는 PASS는 UNKNOWN, 선언 없는 과거 정의는 기존 동작 | 원본 객체·해시 보존 | LEGACY_EVIDENCE; 실제 과거 산출물과 수정한 현재 fixture 구분 |
| T23 형식·Ref·첨부 오류 | 다른 POL/팩/side·중복 pair·잘못된 enum/type·다른 entry 첨부 | import 배치 거부, RES 미생성 | 기존 Store 그대로 | INPUT_INVALID; 마지막 entry 오류에도 앞 entry를 저장하지 않음 |
| T24 미실행/비선정·정상 대조 실패 | 기존 PLAN/ExecStatus와 시도 기록 | 기존 코어대로 미실행 UNKNOWN·비선정 미생성·PASS 관문 보류 | 상태/원인 분리 | CORE_GATES; 확인 FAIL은 정상 대조 실패로 지우지 않음 |

T12와 T13의 위반 전제는 분리한다. T12에 필요 없는 다른 항목의 정책값/경보 시각이 없다는 이유로
직접 확인된 정상 처리 위반을 없애지 않는다. T13에서는 A/경보 필요성·독립 창이 없으면 부재
위반을 확정하지 않는다. T09는 “불명확한 사실을 명확하다고 승격”하는 것이 아니라 불확실성이
근거로 확정된 주입 시험의 적절한 UNKNOWN 처리를 확인하는 범위다.

## 8. 현재 호출 경로와 최소 변경 후보

현재: [정책 양식](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/src/guard/collectors/policy_template.py#L31) → 로더/POL →
[select](../src/guard/core/select.py) → [build_template/import_evidence](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/src/guard/collectors/manual.py#L69)
→ EVD → [judge_evidence](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/src/guard/core/judge.py#L76) →
[DeclaredJudge.judge](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/src/guard/judges/declared.py#L27) → RES →
[build_report](https://github.com/dohashelby/gua/blob/b404a6d847f932edda926c0185572ac315ae7ef1/src/guard/core/report.py#L125) → JSON → [HTML](../src/guard/report/html.py).

| 후보 파일/함수 | 다음 구현에서의 최소 책임 | 보존할 경계·검증 |
|---|---|---|
| core/select.py: SUPPORTED_POLICY_TYPES/check_policy_value | 승인 후 dict[str,int]의 얕은 타입 검사 한 분기. 기업 규칙·엔진 로직을 넣지 않음 | 기존 4타입·null/범위 밖 타입 검사·bool 금지·미입력 gap 유지 |
| 새 manual_audit.py의 순수 보조 함수 (후보명) | OBS-11 정책 의미·case 구조·주장별 첨부 정규화/저장 형태 검증 | 기존 manual_policy.py를 OBS까지 억지로 범용화하지 않음. 파일/네트워크/Store 접근 없음 |
| collectors/manual.py: build_template/_validate/import_evidence | /3 생성·정확 Ref·신규 payload를 관측/config hash에 보존, 배치 오류의 원자성 | /1·/2 읽기, 기존 SUP/LIF payload·현재 첨부 경계·과거 EVD 보존 |
| 새 judges/audit_collection.py의 순수 evaluate (후보명) | 항목별 시간/누락/정상 처리/경보 조건 → 축 상한·확인 FAIL·이유 | 현재 시계·엔진·파일 조회 금지. 시간 계산과 정책 해석을 reporter에 넣지 않음 |
| judges/declared.py | 대상 선언이 있는 OBS에 전용 평가 호출·기존 관문/FAIL 계약 조합·버전 기록 | SUP/LIF·SUP01/02·신고 FAIL·UNKNOWN·필수 키 처리 순서 회귀 |
| core/report.py: build_report | 새 AUDIT_COLLECTION: 이유를 기존 limitations에 전달하는 작은 확장 | 새 SideResult/RES 종류·실제 종속 RES 자동 수정 없음. 원문 경로/본문 노출 금지 |
| tools/overrides/OBS-11.yaml, 로컬 importer, 팩 manifest | 승인된 5개 경로·수동 범위 한계, revision/DRAFT 갱신 | 77 ID·다른76점검·실행기 null/자동 연결5·로컬 원문 보존 |
| CSV·카탈로그 문서·설명 JSON·개발 갱신 도구 | 선언/소비/도출 한계 연결과 exact Ref 설명 | 후보 ID/원문 역할/공유 연결 보존. 필요 없는 개발 도구 변경은 하지 않음 |
| tests/test_policy_m7.py 등 | T01~24의 의미 있는 계약·실제 구형 설치본·JSON/HTML·변경 없음 검증 | 구현을 복제하는 테스트·실시간 sleep·외부 collector 주입은 불필요 |

새 helper 2개는 형태/정규화와 순수 판정을 분리하기 위한 후보이며 플러그인/레지스트리 종류를
늘리기 위한 것이 아니다. 기존 객체 모델·Store·core/judge·비교 로직·엔진 어댑터·의존성 변경은
권고하는 최초 범위에 필요하지 않다. 필요성이 새로 드러나면 별도 근거/검토 범위로 남긴다.

## 9. 열린 결정과 실제 차단 범위

모든 상태는 **제안·미결**이다. 실제 회사 숫자/담당/승인 공란은 합성 계약 구현까지 막지 않는다.
다음 실행 지시가 아래 기술 권고를 명시적으로 채택하면 같은 승인을 반복 요청할 필요는 없다.
업무 의미·불변식이 충돌하거나 다른 선택지를 채택해야 할 때만 그 쟁점을 확인한다.

| 결정 ID | 질문·선택지 / 권고 | 필요한 역할·영향 | 결정 전 가능한 일 / 결정 전 불가능한 일 |
|---|---|---|---|
| POL-M6-REL | OBS-01과 동일/일부 공유/별도? 최초 별도+명시적 사건/첨부 재사용 권고 | 정책 승인·감사/플랫폼 검토. 범위/키·중복 검증 영향 | 의미 대응표·합성 ID / 실제 목록 동일성·자동 복사 승인 |
| POL-M6-SHAPE | 사건별 정수 map / 단일 행 목록 / 승인된 공통 프로파일? 첫 map 4개+목록 권고 | 개발·정책 검토. select 타입/양식/설명 변화 | 구조/오류 테스트 설계 / 미승인 새 타입·선언 배포 |
| POL-M6-TIME | 각 기산점·단위·경계·오차 합성 의미는? ms·구간·수집 기한 기준 D/A 권고 | 정책 승인·관측/시계 담당. 기한 충족/누락 확정 영향 | 계산 예시/경계 테스트 / 실제 SLA·오차 기준 확정 |
| POL-M6-EVIDENCE | 어떤 발생/조회/수신 근거가 독립·완전한가? 별도 경로+주장별 연결 권고 | 보안/감사·시험·수집 담당. 부재/정상 처리 확정의 선행 | 빈 양식·근거 범위·부족 이유 / 특정 시스템의 진위/독립성 자동 승인 |
| POL-M6-COVERAGE | 어떤 case·장애 방법·반복/조건부 항목을 요구하는가? 최초3case·중단/쓰기 거부 중 하나 권고 | 시험/운영·정책 검토. PASS의 커버리지 한계 | case표·설계 / 미시험 방법까지 커버했다고 주장 |
| POL-M6-ALERT | 어떤 누락에 경보가 필요한가·수신점은? 최초 대상은 모든 필수 누락에 경보가 필요한 제한 프로파일 권고 | 정책 승인·관제/수집 담당. T13의 실패 전제 | 필요 조건/수신·부재 창 설계 / 조건부·면제 여부 추정 |
| POL-M6-AXES | security만 제한 도출/추가 축? 최초security 권고, 원문에 없는 실패 확대 없음 | 정책/보안·제품 검토. raw FAIL/UNKNOWN와 충돌 방지 | 판정표·상한 방식 / 성능·모든 종속 시험의 준수 주장 |
| POL-M6-HISTORY | /3·독립 관측 계약·버전 전환을 채택할까? /1·/2 읽기+기존 payload 유지 권고 | 개발·사용자/시험 검토. 구형 import·재판정 | 전환/설치본 테스트 설계 / 실제 구형 산출물을 고쳐서 호환이라 주장 |
| POL-M6-OPS | 장애 때 유지/제한/중단과 실제 승인자는? 기업별 결정, 최초 운영 제어 범위 밖 | 운영·위험 수용/승인 담당. 실제 적용 선행 | 미정 표시·복구 사실/한계 / 기본 지속·즉시 중단·실제 운영 시험 |

최초 구현 차단 조건은 기술 프로파일 채택(REL/SHAPE/HISTORY), 시간/경보의 의미(TIME/ALERT),
근거와 case의 범위(EVIDENCE/COVERAGE/AXES)다. 실제 기업 목록·수치는 합성 구현에서는 비워
둘 수 있고 입력 시 기존 NO_POLICY_VALUE/의미 UNKNOWN으로 다룬다. 운영 적용에는 OPS와
실제 근거/숫자 승인까지 필요하다. 복구 SLA·권한 집행·형식/무결성 통제·실제 RES 의존 전파는
최초 구현을 막는 선행 기능으로 묶지 않고 후속 작업으로 분리한다.

## 10. 이행 순서와 완료 확인

| 순서·분류 | 산출물 | 완료 확인 |
|---|---|---|
| M6 지금 / 설계 제안 | 이 문서·열린 결정·M7 프롬프트, 현재 자산 보존 | 링크/코드 위치/예시·판정표 정합성, 문서 4개만 변경, 전체 시작 파일 바이트/팩/CSV 일치 |
| 다음 / 팀이 채택할 기술 범위 | §9의 최초 프로파일 채택 기록. 실제 값은 공란 가능 | 다음 실행 지시/결정 기록에서 어떤 의미를 채택했는지 식별 가능 |
| M7 / 채택 후 구현 | 제한 map 타입·5개 정책 경로·/3·OBS 전용 정규화/판정·RPT 이유 | T01~24, 기존 불변식/타입/5개 실행기·SUP/LIF·과거 팩 회귀 |
| M7 / 전체 구현 후 검증 | 기본 pytest·frozen sync·wheel·저장소 밖 실제 CLI·과거 설치본 재판정 | 직접 실행/과거 기록 구분, old EVD/RES 보존·verify·멱등·official false |
| 후속 / 운영 기준·실증 | 실제 기업 목록/수치·독립 원본·격리 시험 계획 | 승인된 대상/근거·비용/자원 범위가 정해진 뒤 별도 실증 |
| 후속 / 확장 | 조건부/역할별 규칙·복구 SLA·OBS01 공유 정책·권한/무결성·실제 의존 전파 | 각 별도 원문 요구·실행 증거·호환성·실패 전파 검증 |

## 11. 다음 구현 담당 B에게 전달할 프롬프트 초안

아래는 **사람이 검토한 뒤 실행할 M7 제안**이다. M6에서 자동 실행·전달하지 않는다.

```text
너는 gua 정책 개발 담당 B다. 저장소는 https://github.com/dohashelby/gua.git 이다.
이번 M7은 docs/POLICY_M6_PLAN.md의 OBS-11 최초 수동 검증 프로파일을 구현하는 작업이다.

먼저 AGENTS/CLAUDE/SPEC/POLICY_PROMPT/PROGRESS/DECISIONS와 M5/M6 계획, 실제 코드를 읽어라.
git status/fetch와 M5/M6 PR 병합 상태를 확인해 M6를 포함하는 기준에서 새 브랜치를 만들어라.
M6 미병합이면 codex/gua-policy-obs11-m6-design을 base로 codex/gua-policy-obs11-m7을 사용한다.
기존 사용자 변경/다른 작업 트리/원문/증거를 보존하고 강제 조작·main 직접 push·자동 병합은 하지 마라.

승인 확인:
- 이 프롬프트를 실행하라는 사용자의 지시가 있으면 아래 최초 기술 프로파일 채택으로 기록하라.
  동일 기술 범위의 core/select·judges·report 제한 변경에 승인을 다시 요청하지 마라.
- OBS01과 독립 목록, required_records+사건별 정수map4개, ms/구간 계산·수집기한 기준 D/A,
  경계 포함, /3+구형 읽기, normal/collection_fault/clock_uncertain 3case를 대상으로 한다.
- 최초 프로파일은 대상 필수 누락 모두에 경보가 필요한 경우로 제한한다. 조건부/면제는
  자동 적용하거나 모든 회사의 기준으로 삼지 말고 범위 밖·UNKNOWN으로 표시하라.
- 실제 사건 목록·숫자·승인·독립 근거의 업무 적정성은 확정하지 않는다. 합성 EXAMPLE로 구현하라.
- 실행 지시가 이 권고와 다르거나 SPEC/불변식에 충돌하면 그 차이를 구체적으로 확인하라.

구현 묶음:
1. core/select.check_policy_value에 dict[str,int]의 얕은 타입 지원만 추가하라.
   기존4타입·키 누락·null/범위 밖 타입 검사·bool/int 구별을 유지하고 의미 검증과 분리하라.
2. OBS-11에 required_records:list[str], collection_deadline_ms/max_clock_uncertainty_ms/
   detection_deadline_ms/alert_deadline_ms:dict[str,int]의 5개 required 경로를 선언하라.
   실제 값·기본값을 채우지 말고 정책 집합/정수map의 정확 키·부분 입력·공백·음수 의미는
   M6 §4의 순수 공통 검사로 처리하라. 잘못된 타입과 단순 미입력을 구분하라.
   로컬 importer로 대상 정의와 팩 revision/DRAFT를 갱신하고 다른76점검·기존 ID를 보존하라.
   CSV의 5개 해당 결정과 경로/소비/도출 한계를 대조해 설명 JSON을 재생성하라.
   OBS09/10·OBS01에 필수 키를 일괄 소급하지 말고 계수는 실제 결과로 재산정하라.
3. manual_audit.py 등 작은 순수 보조 함수에 정책 의미/OBS 전용 형태·주장별 근거 정규화를 둬라.
   기존 manual_policy.py와 SUP/LIF 어휘를 불필요하게 범용화하거나 새 레지스트리를 만들지 마라.
4. guard-manual-evidence/3을 생성하고 /1·/2 읽기를 보존하라. 구형 형식에 audit_evidence를
   숨기면 거부하고, 정확 PLAN/POL/팩 Ref·check/side·현재 첨부 경계를 검증하라.
   기존 항목 payload는 그대로, 새 payload는 declared_audit_evidence·guard-audit-evidence/1로
   기존 EVD에 저장하고 의미 있는 값이 실행 config hash에 포함되게 하라.
   모든 entry/첨부 사전 검증 뒤 저장하는 원자성과 이전 객체·blob 보존을 유지하라.
   manual-entry는0.4.0, declared는0.5.0 후보로 전환하되 기존 버전이 더 높으면 의미에 맞게 조정하라.
5. judges/audit_collection.py 등 전용 순수 평가기로 M6 §5~7 계약을 구현하라.
   현재 시각·네트워크·파일/Store 조회 없이 EVD/POL로 구간/누락/경보/정상 처리를 판단하라.
   기록 누락 자체는 관측 미충족이며, OBS11 FAIL은 독립 발생·확정 누락에 더한 정상화/
   종속 PASS 처리 또는 필요한 경보의 확정 부재로 제한하라. 부재 창·실제 오차를 임의 보충하지 마라.
   각 실패에 필요한 전제를 구분해 다른 항목 gap이 확인된 FAIL을 지우지 않게 하라.
   충분한 전체case와 기존INV11일 때만 신고security PASS를 유지하고 UNKNOWN 비승격·신고FAIL을 보존하라.
   신규 functional/performance 상한은UNKNOWN이며 독립 신고FAIL은 보존하라. 기존 CPU 제약은 유지하라.
   /3에 저장된 종속 결과는 신고의 대조 범위다. 실제 다른 RES를 수정/삭제/제외하지 마라.
6. declared의 기존 관문과 조합하고 AUDIT_COLLECTION: 이유를 기존 Report.limitations로 전달하라.
   항목/case·필요 정책·출처/해시·UNKNOWN 이유·미검증 범위를 표시하고 HTML escape를 유지하라.
   민감한 원본 경로/locator/basis 본문을 보고서에 그대로 노출하지 마라.
   MEASURED/정책승인/공식성을 만들지 마라. 정상/장애 주입 여부는 사실대로 기록하라.
7. SPEC/POLICY_TEMPLATE/RUNBOOK/카탈로그/DECISIONS/PROGRESS를 최종 구현과 맞춰 갱신하라.
   examples/lab은 필요시 pack revision만 갱신하고 값·scope·승인 공란은 보존하라.

이번 범위 밖: 새 실행기/실제collector 제어·엔진/유료 자원·운영 시험·새 의존성·새 객체/DB·
core/judge 및 비교 규칙 변경·전자서명/인증·운영 중단 자동 결정·모든 정책 자동화.

모든 구현과 의미 있는 테스트 작성을 먼저 끝내고 한 번에 검증→실패 수정→필요 재검증하라.
M6 T01~24의 계약, 기존 INV01~18·SUP/LIF/SUP01/02·누락/타입/원자성·상한/FAIL 보존을 검증하라.
특히 의도된 clock_uncertain 처리와 일반case의 근거 누락이 같은 PASS로 처리되지 않게 하라.
지원 Linux 기본 테스트는 기존1분 예산을 확인하고 실제시간 sleep 대신 결정적 시간 자료를 써라.
구현을 그대로 복제하는 테스트나 과거 증거 덮어쓰기는 하지 마라.

frozen sync·wheel을 빌드하고 저장소 밖 설치본에서 정책양식→선정→수동/3→import→judge→JSON/HTML을 확인하라.
실제 M5 wheel과 팩12로 만든 구형 /2 EVD/RES를 새 설치본에서 재판정해 원본·해시/멱등·verify를 확인하라.
다른 팩을 현재 fixture 몇 필드만 고쳐 과거 실증이라 주장하지 마라. 구형산출물이 없으면 미검증 범위를 적어라.
실제 실행/저장 기록·pass/fail/skip/제외/환경 한계를 분리해 보고하라.

완료하면 이번 변경만 문서화·커밋·푸시·초안 PR을 만들고 현재 작업에 PR을 연결하라.
실제 상위 병합 상태에 맞는 base와 원격 diff/HEAD를 검증하고 최종 CI 결과까지 확인하라.
최종 보고에는 구현 범위·남은 기업 결정·실행 명령/결과·버전/팩/계수·브랜치/커밋/PR을 적어라.
```

## 12. M6 문서 검증 범위

모든 문서를 작성한 뒤 한 묶음으로 검사한다. 상대 링크·명시 코드 위치/함수·원문 JSON Pointer·
정책 후보12개·현재 버전/계수/팩 hash·예시 YAML 형태·계산 예시·판정표와 다음 프롬프트의 일치·
시작 추적348개 중 허용 문서3개를 제외한345개 바이트 보존·변경4파일 허용 범위·git diff --check를 확인한다.
문서 검증 보조 도구/로그는 ignored out/test-runs/policy-m6-20261021에 두며 제품 테스트를 추가하지 않는다.
기본 pytest는 로컬에서 반복하지 않고, 최종 문서 커밋의 자동 CI 실행은 별도로 확인한다.

## M7 구현 후속

사용자가 M7 프롬프트의 제품 구현을 지시했다. §9의 최초 기술 권고를 채택한 구현과 조회 시작/종료·경보 수신점·프로파일 적용·case 시도 근거의 구체화는 [M7 계약](POLICY_M7.md)을 따른다. 이 문서의 M6 검증/미결 기록은 당시 상태로 보존하며 실제 기업 수치·근거 진위·운영 대응 승인은 여전히 별도다.
