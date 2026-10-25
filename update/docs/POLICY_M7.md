# M7 OBS-11 정책·수동 증거·판정 계약

## 범위와 기준

M6 `b404a6d847f932edda926c0185572ac315ae7ef1`에서 사용자가 지시한 첫 OBS-11 프로파일을
구현한다. [M6 설계](POLICY_M6_PLAN.md)는 당시 제안 기록이며 현재 구현의 구체 조건은 이 문서와
[SPEC](../SPEC.md)을 따른다. 실제 기업 정책값·기한/SLA·승인·원본 진위는 확정하지 않는다.
자동 시험이 아닌 **DECLARED 근거의 연결·조건 검사**다. 실제 collector/엔진 제어·운영 중단·
다른 RES의 자동 조회/수정/삭제/집계 제외를 하지 않는다.

## 정책과 소유권

| 값 경로(values.OBS-11 아래) | 타입·의미 | 의미 부족 처리 |
|---|---|---|
| required_records | required list[str], 기업의 필수 기록 식별자 | 빈/공백/중복은 정책 집합 미확인, gua Check ID로 가정하지 않음 |
| collection_deadline_ms | required dict[str,int], 발생부터 수신까지 C>0 | 누락/범위 오류는 관련 시간/누락 확정 보류 |
| max_clock_uncertainty_ms | required dict[str,int], 비교 두 시각의 실제오차 합계에 허용하는 U≥0 | 실제 오차를 U/0으로 보충하지 않음 |
| detection_deadline_ms | required dict[str,int], 수집 기한 t0+C부터 식별까지 D≥0 | 누락/범위 오류는 적시 식별 충족 보류 |
| alert_deadline_ms | required dict[str,int], 수집 기한 t0+C부터 지정 수신점 도착까지 A≥0 | 누락/범위 오류는 경보 부재 FAIL 확정 보류 |

map의 키는 required_records와 대응한다. 빈/부분 map·불명 집합·알 수 없는 map 키는 유효한
전체 정책으로 인정하지 않고 이유/UNKNOWN으로 남긴다. 명확한 다른 항목의 확인 위반은 보존한다.
타입 검사는 [select](../src/guard/core/select.py), 의미는 [전용 순수 helper](../src/guard/manual_audit.py)다.
dict[str,int]는 문자열 키/정확한 정수 값만 허용하며 bool·null·숫자 문자열·float는 거부한다.
키 생략은 기존 NO_POLICY_VALUE, 의미 부족은 새 Gap/해당 없음으로 바꾸지 않는다.

허용 기준은 POL, 실제 시각/오차·발생/수신·장애·경보는 EVD, 주입 계획/조회 방법은 실행/시험
기록이다. 새 ENV 키나 기업 담당/승인을 만들지 않는다. 양식은 OBS-11:{}이며 실제 키/값을
기본으로 넣지 않는다. 팩13/DRAFT·OBS11정의2·10점검20경로·a19/b8/c161/d115,
77 ID·303결정595연결·자동 연결5개다. OBS01/09/10에 새 정책을 소급하지 않는다.
수치 후보의 a 이동은 소비 구조의 구현이며 도출/숫자/승인 완료가 아니다.

## 버전별 입력과 저장

| 양식 | PLAN·POL·팩 검증 | 추가 payload |
|---|---|---|
| guard-manual-evidence/1 | 기존 PLAN 참조 계약 유지 | policy_evidence와 audit_evidence 숨김 거부 |
| guard-manual-evidence/2 | 정확한 PLAN·POL·팩 Ref 유지 | 기존 SUP/LIF policy_evidence만, audit는 거부 |
| guard-manual-evidence/3 (새 생성) | 정확한 PLAN·POL·팩 Ref | 기존 policy_evidence와 대상 OBS11 audit_evidence |

manual-entry0.4.0, declared0.5.0. 기존 guard-policy-evidence/1은 유지한다. 새 audit_evidence는
contract:guard-audit-evidence/1 및 records 목록이며 EVD.observations.declared_audit_evidence에
저장한다. 대상은 OBS11과 위5개 required 타입 선언을 가진 정의다. 다른 점검/과거 선언에는
새 전제를 소급하지 않는다. /3 일반 점검에는 OBS payload를 만들지 않는다.

각 row는 record_id/case/observations/evidence/limitation만 가진다. record_id는 정확한 POL
식별자, case는 normal/collection_fault/clock_uncertain이고 pair는 유일하다. trial/event/대상/경로로
사건을 식별한다. 같은 항목의 서로 다른 case에 같은 사건을 재사용하면 사건 구분 불충분이다.
새 양식의 관측·attempted는 모두 null, evidence는 []다. 정책 이름을 관측 성공으로 채우지 않는다.

| observations 필드 | 타입·용도 |
|---|---|
| trial_id, event_id, target_scope, path_id | 문자열 또는 null, 정확하고 모호하지 않은 식별자 |
| profile_applicability / profile_detail | confirmed/unknown/unsupported 또는 null / 설명. 필수 누락 모두에 경보가 필요한 프로파일의 적용 근거 |
| attempted, case_detail, fault_method | bool/null, 시도 설명, collector_interrupt/write_denied/unknown/null. case별 실제 시도 |
| occurrence, occurrence_source, collection_source, independence_detail | confirmed/unknown/null 및 출처·독립성 설명. 이름/파일 수/해시 차이만으로 인증하지 않음 |
| record | received/absent/unknown/null |
| occurred_at, received_at | 발생/수신 시각 또는 null |
| window_start_at, window_end_at, collection_window, window_source, window_detail | 기록 부재 조회의 시작/종료, complete/incomplete/unknown/null, 독립 조회 출처·범위/완전성 설명 |
| clock_basis, clock_source, time_errors_ms | bounded_common_time/unknown/null, 시계 출처, 각 제공 시각 이름→실제 비음수 정수오차. bool·알 수 없는 시각키 거부 |
| collector, missing_handling, detected_at | healthy/interrupted/write_denied/unknown/null, identified/treated_normal/unknown/null, 식별 시각 |
| alert, alert_received_at, alert_receiver, alert_source | received/absent/unknown/null, 지정 수신점의 실제 수신 시각/대상/조회 출처 |
| alert_window_start_at, alert_window_end_at, alert_window, alert_window_detail | 별도 경보 부재 조회의 시작/종료·완전성·범위 설명 |
| dependent | test_id, execution_status(COMPLETED/ABORTED/NOT_RUN/unknown/null), security(PASS/FAIL/UNKNOWN/null), depends_on_record(bool/null), independent_failure(bool/null) |
| recovery | restored/not_restored/not_attempted/unknown/null, 복구 사실. 복구 SLA 미검증 |

시간은 **따옴표로 감싼 RFC3339 문자열**, timezone 필수·소수초3자리까지다. -00:00·offset 범위
오류·정밀도 초과를 거부하고 자동 timezone 추가/절삭/반올림하지 않는다. 실제 오차0도 명시한
시계 근거가 필요하며 누락 오차를0으로 채우지 않는다. 빈 문자열은 시각이 아니며 null을 쓴다.
자유 설명·누락/null/unknown은 불충분 관측으로 저장하고 성공으로 취급하지 않는다.

evidence의 각 원소는 claim/attachment/locator/basis이며 현재 entry 첨부만 가리킨다. claim은
profile/occurrence/independence/collection/window/clock/collector/detection/alert/alert_window/
dependent/dependent_failure/recovery 중 하나다. 저장 시 attachment 대신 sha256을 보존한다.
locator/basis가 없으면 그 주장을 충족하지 못한다. 발생과 고장 난 수집 로그를 같은 근거로
순환 증명하지 않는다. importer는 첨부 연결·형태를 검사하며 원본 내용을 자동 해석/인증하지 않는다.
기록/경보 부재 조회 출처는 고장 수집 출처와 분리하고 독립 범위 설명/현재 첨부를 요구한다.

잘못된 field/type/enum·시각·pair·Ref·side·항목·다른entry 첨부는 배치 오류다. 예약된
declared_* 관측을 CheckDef.required_observations로 우회할 수 없다. 값이 없는 draft의
audit_evidence도 지원 check/side/선언·형태를 검사하고 구형 버전 숨김은 거부한다.
모든 entry·첨부 읽기/준비 후 저장하며 기존 객체/blob을 덮지 않는다. I/O 실패 시 참조 없는 새
내용 주소 blob이 남을 수 있는 기존 한계는 유지한다. 새 관측은 의미 있는 config hash에 포함한다.

## 시간·case·판정

오차가 ea/eb인 시각 a/b의 차이는 [b-a-ea-eb,b-a+ea+eb]다. 실제 합계가 U 이내이고 근거가
있을 때만 쓴다. 상한≤B면 기한 내, 하한>B면 기한 후, 겹침이면 UNKNOWN이다. 음수 차이나
순서 미확인을0으로 고치지 않는다. 수집은 B=C, 식별/경보는 각각 B=C+D/B=C+A다.
부재는 조회 시작의 상한이 발생 하한 이전/동일이고 종료 하한이 기한 상한을 넘어야 한다.
경보는 별도 시작/종료·수신점·완전한 조회를 요구한다. 현재 시각에 기대어 창을 닫지 않는다.

| case | security 신고PASS를 유지할 수 있는 조건 |
|---|---|
| normal | 프로파일/독립 발생/시계·현재 첨부, 실제 시도/설명, 기한 내 해당 기록 수신 |
| collection_fault | 확인 누락, 실제 주입 방법과 collector 상태 일치, 기한 후 적시 누락 식별·경보 수신, 종속 UNKNOWN 또는 독립 근거FAIL 유지, 복구 근거 |
| clock_uncertain | 유한 오차로 기한 겹침이 확인된 수신·실제 시도, 대상이 UNKNOWN 또는 독립 근거FAIL을 보존했다는 연결. 시간 충족 자체를 확정하지 않음 |

전체 필수 항목×case와 기존 INV11이 충족돼야 전체security PASS를 유지한다. 정책/근거/일반case의
시각 누락은 clock_uncertain 성공이 아니다. 기능/성능 신고PASS는 UNKNOWN 상한이며 신고FAIL은
보존한다. 상위 CPU 등의 기존 제약은 그대로다. 신고UNKNOWN을 PASS로 승격하지 않는다.

확인된 누락 자체는 관측 미충족이다. OBS11의 새 security FAIL은 다음 전제를 별도로 요구한다.

- 유효한 프로파일/해당 항목 C/U·독립 발생·완전한 부재 창 + 직접 연결된 treated_normal 또는
  해당 기록에 의존하는 실행된 종속 시험의 PASS. 다른 항목의 D/A 누락은 이 위반을 지우지 않는다.
- 위 누락 + 필요한 A·지정 수신점·별도 경보 부재 창·연결 근거. D나 다른 항목 부족으로 지우지 않는다.

지연만·장애만·미발견·미종료·경보 생성만으로 FAIL을 만들지 않는다. 모순은 해당 사실의 전제에
한정한다. 같은 사건의 확인된 발생 구간 불일치, 같은 종속 시험의 결과 충돌 등은 관련 주장을 보류하고
독립 위반은 유지한다. 서로 다른 기록의 수신 시각 차이는 사건 모순이 아니다. 근거 불충분한 다른
시계 신고를 확인 위반의 반증으로 쓰지 않는다. 실제 첨부 내부 모순까지 자동 분석하지는 않는다.

## 보고·호환·검증

AUDIT_COLLECTION: 이유는 기존 RPT.limitations에 전달한다. record/case·안전한 코드·첨부 해시
식별자·정책 키/미확인 한계를 표시하고 원본 경로·locator/basis 본문은 그대로 노출하지 않는다.
HTML escape·출처 중 가장 약한 수준·미인증 신고·DRAFT/official 계산을 유지한다. 정책값은
실제 인증/승인이나 실측이 아니며 다른 RES의 자동 전파가 검증됐다고 표시하지 않는다.

[계약 테스트](../tests/test_policy_m7.py)는 M6 T01~24와 조회 시작·수신점·case 시도·형식별 참조·
원자성·구형 정의·hash/멱등·JSON/HTML·update side 분리를 검증한다. 실제 M5 wheel/팩12로
생성한 /2 객체와 새 설치본 /3을 별도 임시 위치에서 확인한다. 직접 실행과 과거 기록·환경 제약은
[PROGRESS](../PROGRESS.md)에 구분해 남긴다. 실제 엔진/두 이미지·collector/운영 실증은 하지 않는다.
