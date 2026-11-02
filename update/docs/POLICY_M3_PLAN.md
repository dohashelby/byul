# 정책 M3 — 증거 대조 계약과 다음 구현 계획

작성일: 2026-10-21 (Asia/Seoul). **설계 제안 / 기업 결정 미결 / 제품 구현 전**.

> M4 후속: 사용자가 10절 프롬프트의 구현을 지시했다. /2 수동 양식·항목별 신고 증거 검증·보고 한계 연결을 구현하며 현재 계약은 [정책 입력/증거 문서](POLICY_TEMPLATE.md), 실제 검증 결과는 [PROGRESS](../PROGRESS.md)를 따른다. 아래 사실/제안/미실행 표시는 M3 당시 기록으로 보존하며, 기업 미결 결정이나 실제 엔진 실증 완료로 바꾸지 않는다.

M2는 승인된 입력 전제·팩 보호·두 정책 등록 범위에서 완료됐다. 다음 구현은 SUP-14/LIF-07의 **정책 항목 → 시험 관측 → 첨부 근거 → 신고 판정** 연결을 먼저 강화하는 것을 권한다. 정책 선언을 더 추가해도 현재 수동 입력은 개별 정책 항목의 충족을 대조하지 않는다. 이 문서 작성은 자동화·실측 완료가 아니다.

표의 **사실**은 검토한 코드·원격 기록, **해석**은 그 한계의 평가, **제안**은 아직 구현·기업 승인되지 않은 계약을 뜻한다.

## 1. 검토 기준과 보존

| 구분 | 확인한 상태 |
|---|---|
| 저장소 | https://github.com/dohashelby/gua.git |
| M2 기준 브랜치·커밋 | `codex/gua-policy-m2` / `572cef91ff1bf48ffa945f4a0a5fee046e4e0b0e` |
| 원격 갱신 | `git fetch origin --prune` 후 로컬/원격 M2 동일, 작업 시작 시 변경 없음 |
| M3 문서 브랜치 | `codex/gua-policy-m3-design`, 위 M2에서 분기 |
| 선행 PR | [#2 통합](https://github.com/dohashelby/gua/pull/2) → [#3 M1](https://github.com/dohashelby/gua/pull/3) → [#4 M2](https://github.com/dohashelby/gua/pull/4); 검토 시 모두 open/draft·미병합 |
| M3 PR의 기반 | 미병합 M2를 포함해야 하므로 `codex/gua-policy-m2`. main에 완료 기능이 있다고 가정하지 않음 |
| 정본·지침 | [SPEC](../SPEC.md), [CLAUDE](../CLAUDE.md), [DECISIONS](../DECISIONS.md), [PROGRESS](../PROGRESS.md) |
| 정책 근거 | [현재 입력 계약](POLICY_TEMPLATE.md), [카탈로그 설명](policy_catalog.md), [303개 후보 CSV](policy_catalog.csv) |

기존 정책 요청의 3점검·null 양식·Phase 1 커밋 금지는 이후 M1/M2 승인 및 이번 문서 커밋 지시와 구분한다. 이번 범위는 문서 네 파일뿐이다. 제품 코드·SPEC·팩·overrides·정책 예시·카탈로그·의존성·기존 증거·다른 작업 트리·sources/를 변경하지 않는다. 시작 추적 파일 340개의 바이트 해시와 원격 상태는 별도 ignored `out/test-runs/policy-m3-design-20261021/`에 기록했다.

## 2. M2 검토 결론과 검증 범위

### 2.1 코드·정의 대조

아래 링크의 코드 행은 M2 기준이다. 변경 가능한 브랜치 이름 대신 검토 커밋을 고정한 주요 근거도 함께 제공한다.

| 항목 | 확인된 사실 | 해석·남은 범위 |
|---|---|---|
| 수동 PASS 전제 | [declared.py](../src/guard/judges/declared.py) `DeclaredJudge.judge`는 required 키 존재를 검사하고 신고 PASS만 UNKNOWN으로 보류한다. 버전 0.2.0 | 값 내용·목록별 증거·승인 진위 검사가 아니다. 이를 M2 회귀로 분류하지 않는다 |
| FAIL·값 구분 | 같은 함수는 신고 FAIL을 유지한다. 키 존재 검사이므로 []/false/0과 생략을 구별하며 optional은 요구하지 않는다 | 빈 목록의 업무 유효성은 확정되지 않았다. [M2 회귀](../tests/test_policy_m2.py)의 presence/FAIL 사례가 계약을 검증한다 |
| 팩 보호 | [write_policy_template](../src/guard/collectors/policy_template.py)는 resolve한 출력이 팩 내부면 build/mkdir 전에 거부한다 | symlink 부모 포함. 밖에서는 배타 생성·UTF-8/LF. 권한 제약은 시험 결과와 구분 |
| 정책 등록 | [SUP-14](../packs/PACK-BASELINE/checks/SUP-14.yaml) `required_checks`, [LIF-07](../packs/PACK-BASELINE/checks/LIF-07.yaml) `required_readiness_items`: required list[str], 각 CheckDef rev2 | 두 점검 executor/judge는 null. 수동 신고의 기본 판정기는 실행기 메타데이터의 declared다 |
| 현재 집계 | PACK-BASELINE rev11/DRAFT, 77개 점검, 정책 선언 7점검·13개. 자동 실행기 연결은 REQ-01/03, PLT-01/04, REQ-09의 5개 | 두 신규 정책 입력은 NO_POLICY_VALUE를 없앨 수 있으나 NO_EXECUTOR는 유지한다. 5개 연결은 실제 엔진 검증 완료 수가 아니다 |
| 설명 정합성 | [개발 도구](../tools/build_policy_template_metadata.py)로 선언/타입/분류/연결을 읽어 메모리에서 만든 결과와 패키지 JSON이 같다 | 런타임 docs 의존 없음. 입력 팩의 전체 Ref·타입이 다르면 상세 설명 미지정 fallback |
| 카탈로그 | 303개 제안 단위, 연결 595개, a13/b10/c165/d115 | 303은 독립 스칼라 정책이나 완료 수가 아니다. 카탈로그/팩 추가 등록 없음 |
| 재판정 | [judge_plan](../src/guard/core/judge.py)은 정확한 PLAN의 최신 EVD를 사용하고 새 내용이면 RES revision을 추가한다 | 기존 EVD/RES 수정 아님. 같은 입력·as_of·판정 결과면 새 RES를 만들지 않는다 |

주요 고정 근거: [필수 키/FAIL 처리](https://github.com/dohashelby/gua/blob/572cef91ff1bf48ffa945f4a0a5fee046e4e0b0e/src/guard/judges/declared.py#L24), [출력 전 보호](https://github.com/dohashelby/gua/blob/572cef91ff1bf48ffa945f4a0a5fee046e4e0b0e/src/guard/collectors/policy_template.py#L84), [통합 회귀](https://github.com/dohashelby/gua/blob/572cef91ff1bf48ffa945f4a0a5fee046e4e0b0e/tests/test_policy_m2.py#L103).

**판단:** 검토 범위에서 M2 완료를 뒤집을 새 결함은 확인하지 못했다. 입력 존재와 정책 충족의 간극, 항목별 증거 및 상세 보고의 부재는 다음 계약 과제다. 전체 저장소의 무결함을 입증했다는 의미는 아니다.

### 2.2 과거 결과와 이번 확인

| 출처·환경 | 실제 결과 | 이번 수행과 구분 |
|---|---|---|
| 과거 M2 로컬 Windows / Python 3.11.16 | 기본 테스트 557 passed, 5 skipped, 18 deselected / 81.45초 | 기존 PROGRESS·JUnit 기록. 이번 작업에서 pytest 재실행하지 않음 |
| 위 5 skip | 루프백 네트워크 3, 파일 symlink 권한 1, 디렉터리 symlink 권한 1 | Windows 환경 제약. 구현 성공으로 포함하지 않음 |
| 최종 M2 Linux CI / Ubuntu·Python 3.11 | [PR run 39556751205](https://github.com/dohashelby/gua/actions/runs/39556751205): 562 passed, 18 deselected / 36.58초, skip 없음. frozen 설치·wheel 성공 | 이번에 공식 API의 completed/success와 작업 로그를 다시 읽음. 신규 로컬 실행 아님 |
| 최종 M2 push CI | [run 39556743272](https://github.com/dohashelby/gua/actions/runs/39556743272): 562 passed, 18 deselected / 35.04초, wheel 성공 | 최종 SHA 572cef9의 별도 실행. b510b6b의 과거 시간과 혼동하지 않음 |
| 과거 설치본 검증 | 저장소 밖 19개 명령이 기대 종료 코드. 실제 M1 wheel에서 정책 누락 수동 PASS → M2 wheel에서 UNKNOWN·RES rev2 | ignored installed-smoke.json의 명령·출력·검증 assertion을 재확인. 합성 EXAMPLE/DECLARED 계약 검증, 새 재실행/실측 아님 |
| 과거 증거 보존 | 위 기록에서 EVD·RES rev1 전체 필드/해시 보존, 팩10 보존본, official=false, Store.verify 정상, 재판정 멱등 | M2의 0.1.0→0.2.0 전환을 입증한다. 제안하는 후속 버전 호환성을 미리 입증하지 않음 |
| Docker stub/실제 엔진/두 실제 이미지 | 기본 제외 18개에 포함되거나 별도 실증 과제 | 이번 및 M2 정책 작업에서 실행하지 않음. AWS·유료 자원·운영 대상 시험 없음 |
| 이번 문서 검증 | 링크·식별자·함수·집계·참조 계약·시나리오 정합성과 변경 파일/해시 대조 | 명령·결과는 PROGRESS의 M3 기록. 제품 테스트 통과 수로 합산하지 않음 |

팩 Ref는 `PACK-BASELINE@11` / `33cab1b2a45e6039d53e5b48bedaa5085b0a5bfb39f42521df24194139ff2ab4`다. 팩은 reviewed=false, 예시/실험실 정책은 DRAFT다. `official`은 [core/report.py](../src/guard/core/report.py)의 기존 정책 CONFIRMED·팩 REVIEWED·EXAMPLE 미포함 조건이다. 이는 서명 검증이나 신고자의 인증을 뜻하지 않는다. 이번에 승인 메타데이터나 실측 자료를 생성하지 않았다.

## 3. 현재 흐름과 데이터 소유권

| 단계·함수 | 소유하는 데이터·정확한 참조 | 현재 경계와 재사용할 부분 |
|---|---|---|
| [load_policy](../src/guard/collectors/file.py) | 입력 YAML을 DECLARED/EXAMPLE POL로 로딩하고 실제 팩 Ref를 연결 | 기업 값과 환경/시험 설정은 별도. 선언 타입 검사는 select에서 수행 |
| [cli.select](../src/guard/cli.py) / [select](../src/guard/core/select.py) | 팩 보존본 저장, ENV/POL 저장, PLAN.inputs에 팩·환경·POL Ref 고정 | 적용/선정과 gap 산출. 범위 밖에 주어진 선언 값도 타입 검사. 시험을 수행하지 않음 |
| [put_pack/get_pack](../src/guard/store/packs.py), [Store.resolve](../src/guard/store/sqlite.py) | 팩 매니페스트·파일과 객체 revision/hash 검증 | 객체 간 참조를 latest로 치환하지 않음. 과거 PLAN에는 그 시점 POL·팩 사용 |
| [build_template](../src/guard/collectors/manual.py) | PLAN의 선정된 check/side, CheckDef 정상 대조·챌린지·관측 문장·빈 신고 칸 | 현재 guard-manual-evidence/1. 정책 항목별 줄이나 증거 연결 칸은 없음 |
| [read_document/validate_document/_validate/import_evidence](../src/guard/collectors/manual.py) | PLAN Ref·선정·side·관측 키/문장·입력 타입·첨부를 확인한 후 DECLARED EVD 생성 | 모든 입력/첨부 읽기와 초안 준비 후 객체 배치 저장. 오류 시 기존 EVD/RES 보존 |
| EVD / execution / artifacts | PLAN·ENV Ref, check_id/side, manual-entry 0.2.0, 입력자 자기 신고, 내용 주소 첨부 | 사람 판정은 declared_verdicts 등 관측. 새 실행이 아니며 MEASURED로 올릴 수 없음 |
| [judge_plan/judge_evidence/pass_gate](../src/guard/core/judge.py) | PLAN의 정확한 POL, 최신 EVD, 실행기 default_judge → declared | declared는 필수 정책 키·첨부·필수 설명을 검사. 공통 코어는 정상 대조·챌린지 시도·구조화 관측을 검사 |
| RES | EVD/POL Ref, 판정기 버전·params hash, 축별 판정·reasons·missing_observations | 기존 판정기 프로토콜과 객체를 재사용. 판정 플러그인은 저장소/네트워크/시계에 접근하지 않음 |
| [build_report](../src/guard/core/report.py) / [render_html](../src/guard/report/html.py) | 최신 EVD를 가리키는 RES, 입력 manifest, 출처·한계·축별 집계 | **현재 RPT SideResult에는 RES.reasons가 복사되지 않는다.** HTML도 항목별 근거를 표시하지 않는다 |
| [cli._plan_and_pack](../src/guard/cli.py) | judge/report/manual 양식·import는 검증한 저장 팩 fallback 가능 | run의 팩 불일치는 거부. 현재 팩으로 과거 정의를 덮어 쓰지 않음 |

**해석:** EVD/RES 안에 정보를 넣었다고 JSON/HTML 사용자에게 설명됐다고 볼 수 없다. 다음 구현에는 판정과 보고의 연결이 필요하다. 기존 Report.limitations를 사용한 최소 표시를 우선 검토하고, 항목별 새 객체/모델은 당장 추가하지 않는 것이 적절하다.

수동 EVD의 comparability는 현재 공란이다. 항목별 정책 증거를 입력해도 실제 두 엔진 이미지의 비교 조건이 확보되는 것은 아니다. 명시적 CPU는 보안·기능 비교만 가능하고 성능은 UNKNOWN, CPU 근거 없는 gpu/driver null은 비교 불가라는 기존 INV-09를 유지한다.

## 4. 정책 항목·증거 계약 제안

이 절은 후속 구현 승인 대상으로 제시하는 **기술 제안**이다. 현재 입력 형식이나 판정 동작이 아니다.

### 4.1 최소 형태와 참조

1. 기존 수동 양식 /1을 읽는 경로를 유지하고 새 생성 양식은 /2로 버전 구분한다. /2의 PLAN 식별 필드는 기존과 동일하게 두고, 별도 `policy`·`pack` 전체 Ref를 추가해 PLAN.inputs와 비교한다.
2. SUP-14/LIF-07 중 해당 required list[str] 선언이 있는 정의만 대상으로 한다. 과거 선언 없는 팩이나 다른 점검에 새 요구를 암묵적으로 적용하지 않는다. 팩 전체 hash 하나만 하드코딩해 후속 revision을 막는 방식도 피한다.
3. check/side마다 `policy_evidence`에 `parameter`와 `records`를 추가한다. 레코드의 최소 필드는 아래 표다. 공통 계층은 두 정책의 업무 의미를 판정하지 않는다.
4. 양식 생성은 **PLAN이 고정한 POL**의 항목 ID만 복사하고 관측·결과·증거 칸은 비운다. POL 미입력과 []는 기존대로 로딩/선정 가능하되 빈 표가 정책 충족을 뜻하지 않는다.
5. import가 새 형식과 연결을 검증한 뒤, 기존 EVD.observations의 예약 수동 관측 `declared_policy_evidence`(value에 계약 버전·parameter·정규화 records, collected 표시)로 저장하는 방안을 권한다. EVD/DB/Ref 종류는 늘리지 않는다. SPEC의 예약 수동 관측 설명은 후속 구현에서 명시적으로 갱신해야 한다.
6. POLICY/팩 Ref는 양식 경계에서 PLAN.inputs와 확인한다. EVD의 PLAN Ref가 이미 이들을 고정하므로 별도 최신 POL 조회나 중복 정책 값 복사를 하지 않는다. 같은 PLAN에서 정책을 바꿔 재판정하는 명령을 추가하지 않는다.

| 레코드 필드 제안 | 의미·검증 |
|---|---|
| item_id | 해당 parameter 목록의 정확한 문자열. gua CheckDef ID로 가정하지 않음 |
| case | SUP-14: normal/failed/error/unsupported/skipped. LIF-07: not_ready/ready. 양식/관측 계약의 시험 구분이며 실제 실행 결과 기본값이 아님 |
| observations | 점검별 원결과·전이·준비 상태·시각 근거 등. 원결과와 최종 Verdict를 같은 필드에 넣지 않음 |
| evidence | 현재 entry의 attachments 중 파일을 지칭하는 입력 경로 + locator + 근거 설명의 목록. import 후 경로를 첨부 sha256으로 정규화 |
| limitation | 읽지 못한 범위·시간 순서 미확인 등. 빈칸을 성공으로 추론하지 않음 |

항목 식별자는 최초 구현에서 정확 일치로 비교한다. 대소문자 변경·trim·자동 slug 생성·중복 제거를 하지 않는다. POL의 공백/중복 문자열 때문에 항목 집합을 일의적으로 정할 수 없다면 새 PASS를 보류하고 모호성을 기록한다. 기존 select의 list[str] 타입 계약을 조용히 강화하지 않는다. 영구 ID/이름 분리와 폐기 방식은 팀 결정 후 확장한다.

동일 check/side/item/case의 중복 입력은 합치지 않는다. 서로 다른 사건이 필요하면 다음 형식 개정에서 사건 식별을 설계한다. 최초 범위는 항목별 단일 정상/단일 각 챌린지의 유한한 관측이다. 모든 항목에 모든 case를 적용하는 커버리지도 **제안**이며, 집합 단위만 가능한 시험은 그 한계와 추가 설계 필요를 표시하고 완전 커버리지 PASS로 주장하지 않는다.

점검별 observations의 최초 필드 제안은 다음과 같다. enum은 관측을 분류하는 어휘이며 기업의 실제 값/기준이 아니다. 생성 시 관측 칸은 비우고, 부분 입력의 생략/null은 미확인으로 보존한다. 제공한 값의 잘못된 타입/허용되지 않은 enum은 형식 오류다.

| 대상 | 관측 필드·타입 제안 | 해석 경계 |
|---|---|---|
| 공통 | trial_id: str, target_scope: str | 같은 시험과 실제 대상 범위를 설명. 서로 다른 trial 간 상태를 한 전이로 조합하지 않음. item별 공통 사건이 반복되면 같은 trial·근거가 일치해야 함 |
| SUP-14 | check_outcome: succeeded/failed/error/unsupported/skipped/unknown; gate_outcome: advanced/blocked/unknown; exception_assessment: none/reported/unknown; transition_detail: str | case는 의도한 주입, check_outcome은 실제 관측이다. 불일치한 주입을 시험 성공으로 보지 않음. none은 단순 빈칸이 아니라 해당 예외 범위를 확인했다는 신고와 첨부 근거가 필요 |
| LIF-07 | item_state: ready/not_ready/unknown; request_outcome: accepted/blocked/not_attempted/unknown; inference_outcome: succeeded/failed/not_attempted/unknown; order_basis: same_clock/independent_order/unknown; order_detail: str | 실제 준비/접수·추론과 선후관계 근거. 시각 숫자만으로 순서를 추정하지 않음. target_scope에 rank/model/worker 범위가 설명돼야 함 |

위 enum 필드는 문자열이다. 여러 항목의 묶음 readiness/관문을 검사할 때는 단일 항목 값뿐 아니라 해당 trial의 필수 항목 집합과 차단/접수 사건의 관계를 함께 확인한다. 원본 파일을 자동 파싱하지 않는 최초 구현의 한계는 DECLARED로 표시한다. 이 필드들이 사건의 진실성이나 항목별 대체 방법의 충분성을 인증하지는 않는다.

### 4.2 형식 오류와 증거 부족

| 상황 | 제안 처리 |
|---|---|
| PLAN/POL/팩 Ref 불일치, 잘못된 side, 비선정 check, 중복 item/case, 미등록 item, 잘못된 필드 타입 | import 전체 거부. 아무 새 EVD 객체도 저장하지 않고 기존 FAIL 포함 과거 자료 보존 |
| record/evidence 목록 공란, 필요한 case 일부 생략 | 입력 초안/부분 증거로 보존 가능. 해당 PASS는 UNKNOWN |
| 선언된 첨부 경로가 실제로 없거나 읽기 실패 | 현재처럼 import 거부. 입력/저장 실패와 정책 미충족을 혼동하지 않음 |
| 존재하는 첨부지만 해당 주장과의 locator·근거가 없음 | 연결 부족으로 UNKNOWN. 첨부 하나 있다고 모든 항목 충족으로 간주하지 않음 |
| 서로 다른 관측이 모순되고 어느 것이 해당 사건인지 확인 불가 | 충돌·불확실성 기록, 해당 PASS 보류. 독립적으로 확인된 위반 및 신고 FAIL은 유지 |
| 정책 키 누락 / [] 의미 미결 / 항목 집합 모호 | 정책값 자체를 FAIL로 만들지 않음. 새 항목 대조 PASS는 보류하고 구체 이유 표시 |
| 과거 /1 EVD 또는 새 예약 관측 부재 | 재판정은 가능. 대상 정책이 선언된 SUP-14/LIF-07의 신규 PASS는 UNKNOWN, 기존 신고 FAIL 유지. 그 외 점검은 기존 계약 유지 |

해시가 맞는 첨부는 같은 파일임을 입증한다. 파일 내용과 신고 observations가 일치한다는 자동 검증·수집자 인증을 입증하지 않는다. 최초 구현은 **DECLARED 자료의 연결/충족 조건 검사**이며 실제 센서 자동화가 아니다. /2의 EVD 역시 DECLARED, 합성 fixture를 쓴 결과는 EXAMPLE 한계를 유지한다.

### 4.3 저장·판정·보고의 최소 변경

- **수집 경계:** [collectors/manual.py](../src/guard/collectors/manual.py)와 작은 수동 관측 검증 보조 함수에서 /1·/2 수용, 스냅샷 정책 로딩, item/case/첨부 연결 검증·정규화를 수행한다. 새 엔진 실행기나 네트워크 호출은 필요 없다.
- **버전:** 양식 /2, manual-entry 0.3.0, declared 0.3.0을 후보로 제안한다. 기존 객체의 문자열 버전/필드를 고치지 않고 새 입력/RES에만 적용한다. 새 실행 config hash에는 정규화한 신규 관측을 포함하되 과거 해시나 update 비교 조건을 바꾸지 않는다.
- **판정:** declared에 두 점검 전용의 작은 순수 함수로 위임한다. 새 범용 DSL 대신 점검별 의미를 명시한다. inputs는 CheckDef·EVD·정책값뿐이다. 새 UNKNOWN/FAIL 이유는 RES.reasons/missing_observations로 기록하며 Gap enum을 늘리지 않는다.
- **코어/모델:** 수집·판정에는 core/models/store 변경 0건이 가능한 안을 우선한다. 보고 연결을 위해서는 **core/report.py 변경이 필요하다.** 현재 RPT가 RES.reasons를 담지 않는다는 이유를 숨기지 않는다.
- **최초 보고:** 해당 check/side의 선택된 RES에 있는 항목별 미확인/위반 이유를 Report.limitations에 식별자와 함께 전달한다. JSON/HTML이 같은 내용을 사용하고 HTML은 기존 escape를 유지한다. 임의 경로·파일 전체 내용·민감한 관측을 출력하지 않는다. 새로운 구조화 항목 표/SideResult 필드는 후속 확장으로 둔다.
- **공식성:** 정책 DRAFT·팩 DRAFT·EXAMPLE 한계 및 미인증 자기 신고 문구를 유지한다. 새 연결 정보가 official을 강제로 true로 만들면 안 된다.
- **재판정:** 기존 PLAN의 정확한 POL/팩과 EVD를 그대로 사용한다. /1 내용이나 빠진 목록을 /2 값으로 보충하지 않는다. 같은 입력/as_of로 다시 판정하면 멱등, 다른 결과면 RES 새 revision. 원본 파일 바이트 해시와 객체 해시는 별개로 유지한다.

이 변경 범위는 이번 M3에서 구현하지 않는다. 특히 CLAUDE의 judges/core 변경 경계에 해당하므로 아래 후속 프롬프트를 사람이 검토해 실행 지시할 때 그 범위를 명시적으로 승인하도록 작성했다.

## 5. 점검별 판정 의미 제안

판정 순서 제안: **기존 신고 FAIL 보존 → 해당 축의 확인된 위반 FAIL → 신고 UNKNOWN 유지 → 신고 PASS에 새 조건 및 기존 공통 PASS 관문 적용**. 원결과가 실패라는 사실만으로 확인된 위반이 되지 않는다. 정책 대상·사건·집행 결과가 연결돼야 한다. 신고 UNKNOWN을 완전한 데이터라는 이유로 PASS로 승격하지 않는다.

### 5.1 SUP-14

- 필수 항목 각각 정상 성공 묶음의 승인/배포 전이와 failed/error/unsupported/skipped 주입의 차단 전이를 근거로 연결한다. 검사 원결과와 gate_transition을 분리한다.
- 오류/미지원/생략이 실제 차단됐고 정상 대조·전체 챌린지·관측/첨부가 충족된 경우, 신고 security PASS를 유지할 수 있는 제안이다. 원검사 실패를 SUP-14 FAIL로 복사하지 않는다.
- **필수 미완료 + 실제 전이 허용 + 그 사건에 적용 가능한 승인 예외 없음이 확인**되면 security FAIL 근거다. 예외 여부 미확인을 ‘없음’으로 추론하지 않는다.
- 기업 예외·위험 수용은 원검사 성공이 아니다. 현재 POL 예외 표시는 Verdict를 바꾸지 않는 INV-08이다. 개별 검사 예외는 이 표시와 별개인 사건 증거이며, 최초 자동 수용은 보류한다. 예외 문구/승인 신고만 있으면 UNKNOWN과 별도 설명, 독립적으로 확인된 위반/신고 FAIL은 보존한다.
- 정상 묶음 성공은 공통 대조 근거이며 전체 서비스 기능/성능 충족의 증거가 아니다. 이 계약의 SUP-14는 security만 구체화하고 functional/performance의 신규 PASS는 추가 계약 전 UNKNOWN으로 제안한다.

### 5.2 LIF-07

- 각 준비 항목의 미완료 상태, 그때의 실제 요청 접수/차단, 완료 상태, 완료 후 실제 정상 추론을 같은 시험·환경·side로 연결한다. rank/model/worker 범위를 기록한다.
- 정상 ready 대조는 하나의 공통 trial에서 필수 항목 전부의 준비 완료가 접수/추론보다 앞서는 근거를 요구한다. 항목마다 서로 다른 실행의 ready를 모아 ‘전부 준비됨’으로 만들지 않는다. not_ready 챌린지는 항목별 trial이 달라도 해당 항목의 미완료와 같은 trial의 실제 차단을 연결해야 한다.
- 준비 미완료 시 실제 차단 + 완료 후 접수/추론 성공 + 전체 필수 항목 관측이 있어야 신고 security/functional PASS를 유지할 수 있다. 두 축은 각각 필요한 근거를 확인한다.
- **필수 준비 미완료와 실제 접수 허용의 선후관계**가 확인되면 security FAIL이다. 시계가 달라 순서를 모르면 UNKNOWN이다. 요청을 보낸 시각과 서버가 접수한 시각도 구분한다.
- health 성공·listener 열림만 있거나, rank/model 준비와 실제 추론이 없으면 UNKNOWN이다. 정상 추론 실패는 자동으로 보안 위반이 아니며 security PASS를 막는 공통 관문과 functional 실패 근거를 분리한다.
- functional FAIL을 도출하려면 ‘정상 입력·준비 완료·서비스 접수’가 확인된 해당 시험에서 기능 실패가 관측돼야 한다. 네트워크/관측 실패는 UNKNOWN이다. performance는 이 계약에서 검증하지 않는다.
- 타임스탬프를 쓰더라도 허용 시간 오차/수용 지연 수치를 지어내지 않는다. 같은 단조 시계의 사건 순서 등 근거를 설명하고, 서로 다른 시계면 확정 순서를 주장하지 않는다.

최초 두 점검 계약은 빈 목록에서 논리적인 ‘전부 만족’을 계산하지 않는 보수적 PASS 보류를 제안한다. []를 기업 정책으로 금지/허용했다는 결정은 아니다. 향후 빈 목록 승인 의미를 추가해도 필수 정상 대조·챌린지의 관문을 건너뛰지 않는다.

## 6. 팀 결정표

모든 행의 담당은 **필요한 역할 제안**이며 실제 담당자 지정/승인은 미결이다. POL-M2-01~03은 기존 열린 질문이고, POL-M3-*는 이를 세분화한 제안 ID다.

| 결정 ID·상태 | 질문·현재 계약 | 선택지 | 권고안·이유 | 결정할 역할 제안 | 미결 중 가능한 일 / 막히는 일 |
|---|---|---|---|---|---|
| POL-M2-01 / 미결 | []는 현재 값이며 키 누락 아님. 어느 업무 조건에서 유효한가? | 비어 있지 않은 필수 집합 / 승인된 조건부 빈 집합 | 최초 항목 대조는 빈 집합 PASS 보류 제안. 업무상 금지로 바꾸지 않음 | 정책 책임·위험 수용 검토 | 빈 상태 보존/UNKNOWN 설계 가능 / 실제 빈 목록 충족 승인 불가 |
| POL-M2-02 / 미결 | 실제 검사/준비 목록·담당·대체 방법·조건부 항목은? 현재 list[str]뿐 | 고정 목록 / 별도 조건·역할 표 | 실제 기업 목록과 책임을 먼저 제공. 복잡 표는 별도 계약 | 공급물 승인·배포/운영 책임 | 합성 fixture/단순 집합 연결 가능 / 운영 목록·조건부 충족 확정 불가 |
| POL-M3-ID / 제안·미결 | 식별자 명명·수정·폐기는 누가 결정하는가? | 기존 문자열 정확 일치 / 영구 ID+표시명 | 첫 구현 정확 일치, 자동 정규화 없음. 영구 체계는 이후 결정 | 정책 관리·개발 검토 | 명확한 입력의 기술 검증 가능 / 이름 변경 자동 이관 불가 |
| POL-M3-EVIDENCE / 제안·미결 | 어떤 원본·locator·대체 증거가 충분한가? | 사건 로그·원결과 / 승인된 대체 증거 | 내용 주소 연결+주장별 설명, 진위/인증은 별도. 승인 역할과 입력자 분리 | 보안 검토·시험 책임 | /2 연결/부족 검증 가능 / 대체 증거의 충분성 자동 승인 불가 |
| POL-M3-EXCEPTION / 제안·미결 | 예외 범위·승인 역할·기한·검사별 허용은? | 최초 자동 예외 수용 없음 / 별도 검토한 예외 계약 | 최초는 예외 상황 UNKNOWN·표시. INV-08 유지 | 위험 수용·승인 책임 | 예외 증거 기록 가능 / 예외를 원검사 성공이나 PASS로 바꾸기 불가 |
| POL-M3-COVERAGE / 제안·미결 | 모든 필수 항목에 각 챌린지를 개별 적용할 수 있는가? | 항목×case / 집합·동등성 근거 기반 | 최초 항목×case, 시험 불가면 한계/UNKNOWN. 거짓 attempted 금지 | 시험 설계·운영 검토 | 계약·입력 사례 가능 / 실행 불가 시험의 PASS 인정 불가 |
| POL-M3-HISTORY / 기술 제안·미결 | 이전 EVD에 신규 요구를 어떻게 적용하는가? 현재 추가 전용 재판정 | 기존 RES 유지하며 새 판정 / 과거 자료 보충 | 새 판정기에서는 대상 정의의 구형 증거 PASS 보류, 원본/RES 보존. 타 정의 소급 금지 | 제품 설계·증거 검토 | 호환/마이그레이션 회귀 설계 가능 / 운영 증거 자동 변환 불가 |
| POL-M2-03 / 미결 | b10 중 다음 등록과 소비 범위는? 현재 전부 미등록 | 승인 도구 입력 / OBS-11 기록 목록 / 나머지 후보 | 아래 2개를 후속 후보로만 제안. 입력 연결의 검증 후 등록 | 제품·정책 검토 | 후보 비교 가능 / 기업 값·자동 실행/다음 팩 승인 불가 |
| POL-M3-AXES / 기술 제안·미결 | 두 점검에서 축별 충분성은? 현재 수동 3축 신고 | 축별 엄격한 제한 / 별도 기능·성능 계약 | SUP security, LIF security/functional의 제한 검증. performance UNKNOWN 제안 | 보안·기능 시험 설계 | 축별 시나리오 가능 / 보안 결과로 성능 PASS 생성 불가 |

**바로 가능한 기술 작업:** /2 입출력·정확 Ref/side 검증, 항목·case·첨부 연결, 단순 목록의 누락/중복/충돌 검증, 저장·재판정 호환, 한계 보고. 실제 기업 값 없이 EXAMPLE fixture로 검증할 수 있다. 위 미결 권고를 제품 기본 동작으로 만들려면 다음 구현 프롬프트의 명시적 승인 범위가 필요하다.

**팀 결정 후 가능한 작업:** 실제 운영 목록·승인자·조건부/대체 증거·예외의 충분성 확정, 빈 집합 충족 인정, 수치 한도/성능 판정, 실제 엔진 시험의 절차·환경 승인. 이번 문서는 이 결정을 대신하지 않는다.

## 7. 남은 b10 비교와 다음 후보 2개

아래 현재 상태는 모두 `policy_params 미등록 / current_param_paths=[] / 자동 소비 없음`이다. 기업 정책 후보와 실제 환경 사실을 혼용하지 않는다.

| 후보 정책 ID / 연결 점검 | 값의 성격·사용 시나리오 | 소비 경로·현재 선택 |
|---|---|---|
| P-DRAFT-SUP-GOV-TOOL / SUP-01·02 | str 기업 기준: 승인 도구·기록 체계. 실제 시스템/권한/변경 결과는 ENV 또는 EVD | **후속 후보 1.** 양식/선정 + 수동 근거 위치 연결. 제품명만으로 승인 분리 판정 불가 |
| P-DRAFT-SUP-CODE-MODES / SUP-08~11 | list[str] 허용 실행 모드 기준. 실제 동적 코드 활성화는 관측 | 보류. 모드 vocabulary·집행점과 관측 매핑 필요 |
| P-DRAFT-SUP-DERIVED-LOCATIONS / SUP-15·16 | list[str] 허용 저장 위치 기준. 실제 캐시·생성물 위치는 관측 | 보류. 경로 정규화·원격 저장소·컨테이너 경계 결정 필요 |
| P-DRAFT-INPUT-ENDPOINTS / REQ-07~10 | list[str] 허용 endpoint 기준. 실제 노출 API와 실제 probe는 별도 | 보류. REQ-09는 현재 엔진 token-array 대조 한 건을 요구. 목록을 추가한다고 각 endpoint를 시험한 것이 아님 |
| P-DRAFT-INPUT-FORMATS / REQ-07~10 | list[str] 허용 텍스트/token 등 형식 기준. 실제 consumer/vocab은 관측·실행 설정 | 보류. REQ-09의 prompt=[0], 실제 모델 vocab 출처·길이+1 예산·정상/후속 대조를 유지할 설계 선행 |
| P-DRAFT-CACHE-PUBLIC-PREFIX / REQ-15~17 | bool 기업 공유 허용. private 혼입 허용이 아님 | 보류. 집단/비공개 자료 경계·격리 관측 없이는 false/true만 판정 불가 |
| P-DRAFT-CACHE-CONTEXT / REQ-15~17 | list[str] 필수 cache key 문맥 기준. 실제 key 구성은 관측 | 보류. 키의 의미·다중 문맥·동등성 관계 설계 선행 |
| P-DRAFT-USAGE-ALLOW / OBS-04·05 | bool 기업 생성/전송 허용. 과거 팀 참조 ‘비사용’은 실제 false가 아님 | 보류. 생성·전송·관측 경계와 변경 승인·재시험 필요. 기본값 금지 |
| P-DRAFT-REPLAY-ALLOW / OBS-07·08 | bool 기업 진단 replay 허용. 실제 미도달은 별도 환경/코드 증거 | 보류. 경로/역할/원본 자료·재생 효과 증거 선행. 기본 false 금지 |
| P-DRAFT-AUDIT-REQUIRED-RECORDS / OBS-11 | list[str] 기업 필수 기록/사건 기준. 실제 사건·도착·누락은 관측 | **후속 후보 2, 조건부.** 항목별 증거 연결 재사용 가능. 수집 기한·시간 불확실성·독립 사건 증거가 없으면 판정 UNKNOWN |

선택 이유는 **근거를 입력·추적하는 사용자 흐름과의 연결 가치**다. SUP 승인 기록과 OBS 필수 사건은 공급물 승인부터 관측 보존까지 다음 확장 범위를 보여준다. 단순 타입이나 추가 선언 수가 선택 이유가 아니다. 이번 또는 바로 다음 구현에서 등록하라는 뜻은 아니다.

| 후보 | 등록으로 얻는 것 / 얻지 못하는 것 | 선행 결정 | 예상 최소 변경·실제 소비 제안 | 의미 있는 검증 |
|---|---|---|---|---|
| 승인 도구 | 선언·빈 양식·선정 누락 안내 / 쓰기권과 승인권 분리 검증은 얻지 못함 | 도구/기록 체계의 식별 방식·책임, SUP-01/02 각각의 사용 의미 | 두 overrides→기존 importer→대상 CheckDef/팩 revision, 카탈로그·설명 JSON·관련 policy Ref·문서/테스트. manual에서 승인 원본/전이 증거 위치 안내; 현재 항목 ID 목록 계약에 도구 str을 억지 적용하지 않음 | 누락/잘못된 타입/다른 팩 fallback, 도구 이름만 있어도 근거 없으면 PASS 아님, 제출자 승인 변경 관측은 별도 계약 |
| 필수 감사 기록 | 요구 사건 목록과 사건별 근거 연결 / 목록만으로 수집 건전성·마감·완결성은 얻지 못함 | OBS-01과의 동일/부분관계 확인, 사건 식별, 수집 기한·시간 기준·중단 대응 승인 | OBS-11 override/팩/카탈로그·설명/수동 연결. 실제 판정은 독립 사건→기대 기록→도착·장애 관측을 묶는 전용 계약 이후 | 사건 발생 불명·기한 없음→UNKNOWN; 발생 확정+누락을 정상 처리함→FAIL; 센서 중단을 정상/PASS로 바꾸지 않음 |

승인 도구의 값 키나 감사 기록의 새 policy 경로는 아직 확정하지 않는다. 관련 예시/실험실 POL의 pack Ref 갱신은 실제 팩을 바꾸는 후속 작업에서만 한다.

## 8. 향후 구현 검증 시나리오

**설계용 EXAMPLE 시나리오이며 이번에 실행한 시험이 아니다.** 기본 신고는 해당 축 PASS, 정상 대조 성공·CheckDef 챌린지 시도·필수 관측 등 기존 관문 충족을 가정한다. 차이가 있는 행에는 명시한다. 아래 PASS는 그 제한된 축의 신고 PASS 유지이며 엔진 실측/기업 공식 승인 아님이다.

| ID | 입력 조건 | 필요한 관측·대조 | 후속 계약의 기대 결과·이유 |
|---|---|---|---|
| M3-S01 | required 키 생략 | 기존 POL snapshot·CheckDef required 선언 | PASS→UNKNOWN/NO_POLICY_VALUE; 기존 신고 FAIL 유지 |
| M3-S02 | 목록 [] / 의미 미결 | 빈 입력을 그대로 보존 | 새 항목 충족 PASS는 UNKNOWN. select 타입 오류·자동 NOT_APPLICABLE로 바꾸지 않음 |
| M3-S03 | 두 항목 중 하나의 관측 누락 | POL 항목 집합과 수집 records의 차집합 | 해당 PASS UNKNOWN, 누락 item/case 이유 |
| M3-S04 | 같은 item/case 중복 | 정확 문자열 키 | import 전체 거부, 합산/최신 선택 없음 |
| M3-S05 | POL에 없는 item 또는 모호한 POL 항목 | 현재 PLAN의 POL 집합 | 미등록 record는 거부; POL 중복/공백 의미 모호는 새 PASS 보류 |
| M3-S06 | 다른 PLAN 또는 POL/팩 revision/hash | 전체 Ref·store.resolve·PLAN.inputs | import 거부. latest 또는 현재 팩으로 자동 교체 금지 |
| M3-S07 | audit의 candidate, 또는 check/side 간 record 혼입 | 모드 side·entry 경계·현재 첨부 연결 | 거부. update 각 side의 일관된 복사 허위 신고까지 자동 탐지한다고 주장하지 않음 |
| M3-S08 | 정상 대조 failed 또는 not_attempted | normal_control과 해당 사건 근거 | 보안 PASS UNKNOWN. LIF 기능은 확인된 기능 실패 조건일 때만 FAIL |
| M3-S09 | 필수 챌린지 하나 not_attempted | CheckDef 원래 단계와 case 증거 | PASS UNKNOWN. 미실행을 attempted=true로 채우지 않음 |
| M3-S10 | SUP 실패 주입·실제 차단 | 원결과 failed + blocked + 정상 성공 전이·모든 case 근거 | 다른 관문도 충족하면 security 신고 PASS 유지. 원검사 실패를 전체 FAIL로 전사하지 않음 |
| M3-S11 | SUP error/unsupported/skipped 각각 차단 | 각 항목×각 case 원결과와 전이 | 각각 S10과 동일. 단일 ‘차단됨’ 설명으로 전 사례 커버 불가 |
| M3-S12 | SUP 필수 미완료를 실제 허용, 적용 예외 없음 확인 | 항목 필수성·전이·동일 사건·예외 범위 근거 | security FAIL. 다른 항목 관측 누락이 있어도 이 위반을 숨기지 않음 |
| M3-S13 | 예외 문구/승인 신고만 있고 원결과·집행 충분성 없음 | 예외와 원검사 결과 분리 | UNKNOWN·예외 별도 표시. 원검사 성공/PASS로 변환 금지 |
| M3-S14 | LIF 미준비 차단·완료 후 정상 추론 | 모든 필수 항목 상태·접수·rank/model·동일 시험 순서 | security/functional 각각의 신고 PASS 유지 가능, performance UNKNOWN |
| M3-S15 | LIF 준비 전 실제 요청 접수 | 필수 항목 미완료와 접수의 확정 순서 | security FAIL; functional/performance는 이를 복사하지 않음 |
| M3-S16 | health 성공 또는 listener 시각만 있음 | rank/model·추론·접수 사건 없음 | security/functional UNKNOWN |
| M3-S17 | 서로 다른 시계의 접수/준비 시각, 순서 불명 | 시계 관계·오차 근거 없음 | UNKNOWN. 임의 오차 수치로 순서를 확정하지 않음 |
| M3-S18 | 신고 FAIL + 정책/첨부/다른 항목 누락 | 유효하게 import된 신고 FAIL | FAIL 유지. 잘못된 형식 배치 거부와 구분 |
| M3-S19 | 신고 UNKNOWN + 모든 case 입력 | 신고 축 값 | UNKNOWN 유지. 완전성만으로 PASS로 승격하지 않음 |
| M3-S20 | 첨부 존재하나 locator/주장 연결 없음 | record별 evidence 참조·근거 | 해당 PASS UNKNOWN. 없는 파일을 명시한 경우는 import 거부 |
| M3-S21 | 모순 관측 + 별개 확정 위반 | 사건/side별 식별·독립 위반 근거 | 모순은 UNKNOWN 사유, 이미 확인된 위반 축 FAIL 유지 |
| M3-S22 | 과거 /1 EVD·기존 PASS | 해당 정책 선언이 있는 옛 팩, 신규 관측 없음 | 새 declared에서 대상 PASS UNKNOWN·새 RES, 이전 EVD/RES bytes/hash 불변 |
| M3-S23 | 과거 정책 선언 없는 팩/다른 점검 | 정확한 저장 팩·정의·기존 관문 | 새 항목 요구 소급 없음. 이전 PASS를 신규 계약 검증 완료로 표현하지 않음 |
| M3-S24 | 같은 EVD/POL/팩·판정기·as_of 두 번 | RES 내용 비교 | 두 번째 새 RES revision 없음, 참조/첨부 무결성 유지 |
| M3-S25 | explicit CPU / cpu 없음+gpu/driver null | 기존 comparability 조건 | CPU 성능 UNKNOWN, 근거 없는 환경 비교 NOT_COMPARABLE. 수동 입력만으로 실측 비교 가능해지지 않음 |
| M3-S26 | item/locator에 HTML 특수문자·합성 첨부 | JSON 한계와 HTML escape | 동일 정보의 안전한 표시, official=false 유지. 원본 전체 내용 노출 없음 |

## 9. 순차 작업과 완료 조건

| 순서 | 구분·산출물 | 수정 예상 지점·선행 조건 | 완료 확인 |
|---|---|---|---|
| 지금 M3 | 설계 문서·결정표·시나리오·복사용 프롬프트 | 이번 네 문서만. 기술/기업 권고 미결 표기 | 링크/ID/함수/집계 정합성, 문서 외 해시 불변, 문서 PR |
| 다음 M4-A | /2 생성·import, 정책 항목/첨부 연결, 구형 양식 호환 | manual collector와 작은 보조 함수, 테스트/SPEC 문구. 아래 구현 범위 승인 필요 | Ref/side/중복/부분 입력·배치 원자성·DECLARED/과거 해시 회귀 |
| 같은 승인 묶음 M4-B | 두 점검의 제한적 순수 검증 및 reasons | declared와 점검별 보조 함수. 빈 집합 보류·축별 범위 등 아래 제안 승인 필요 | S01~25, FAIL 보존·UNKNOWN 비승격·순수 함수·버전 기록 |
| 같은 승인 묶음 M4-C | JSON/HTML 한계 연결·문서·wheel | core/report.py의 limitations 전달, 필요 시 report/html.py의 기존 표시만. 모델 확장 없음 | 동일 사유/출처/공식성 표시, S26, 저장소 밖 wheel·기본 회귀·실제 CI |
| M4 이후 | 실제 기업 목록/승인 확정과 실증 절차 | 정책·운영·보안 책임의 미결 결정. Docker·고정 모델/두 이미지·격리 환경 별도 승인 | 원본 수집·실측 출처·고정 비교 조건. 비용 자원 생성은 별도 요청 |
| 이후 정책 확장 | 위 후보 중 최대 2개 등록의 별도 구현안 | 소비 경로/기업 결정 확인 후 팩·POL Ref·설명·테스트 동시 갱신 | 기존 점검 회귀, 미입력/가짜 근거가 PASS가 되지 않음 |
| 후속 확장 | 조건부 항목/역할 표, 기한·성능, 구조화 보고 표 | 실제 요구와 복잡도 확인 후 별도 설계 | 범용화를 위한 추상화가 아니라 검증된 계약별 회귀 |

기간·완료율은 산정하지 않는다. M4-A~C를 실제 구현할 때는 구현·테스트 코드를 모두 작성한 후 기본 테스트·설치본 검증을 실행하고 실패를 수정한다. 문서 M3 동안 이 구현을 시작하지 않는다.

## 10. 다음 구현자에게 전달할 프롬프트

아래는 **사용자 검토 후 실행할 제안**이다. 이 문서 작성 중 실행하지 않았다.

~~~text
너는 gua 프로젝트의 M4 구현 담당 B다.
저장소: https://github.com/dohashelby/gua.git

먼저 원격을 fetch하고 브랜치·로컬 변경·선행 PR 병합 여부를 확인하라.
M2 기준은 codex/gua-policy-m2 / 572cef91ff1bf48ffa945f4a0a5fee046e4e0b0e다.
M3 설계 브랜치 codex/gua-policy-m3-design의 최신 문서를 포함한 기반에서
codex/gua-policy-evidence-m4 브랜치를 만들어 작업하라.
main에 미병합 기능이 있다고 가정하거나 임의 병합·강제 reset/push하지 않는다.

AGENTS.md/CLAUDE.md, SPEC.md, PROGRESS.md, DECISIONS.md,
docs/POLICY_M3_PLAN.md, docs/POLICY_TEMPLATE.md와 관련 코드를 읽어라.
문서의 사실/미결 기업 결정/구현 제안을 구분한다.

이번 실행 지시는 다음 최소 변경 범위를 승인한다.
- guard-manual-evidence/2 생성·import 및 /1 읽기 호환
- SUP-14.required_checks와 LIF-07.required_readiness_items의
  정책 항목·case·현재 첨부 근거 연결/정규화
- collectors/manual.py와 작은 전용 보조 함수
- declared 판정기 및 두 점검의 작은 순수 검증 보조 함수
- core/report.py에서 해당 RES의 항목별 이유를 기존 Report.limitations로 전달
- 필요 시 report/html.py의 기존 한계 표시 개선
- 관련 테스트·SPEC의 수동 관측 설명·사용 문서/진행/결정 기록
core/report.py 및 judges의 이 변경은 이번 실행 범위에 포함한다.
core/select.py·core/judge.py·models·store 스키마·새 실행기·새 의존성은 변경하지 않는다.
예상 밖 변경이 꼭 필요하면 근거와 최소 대안을 먼저 제시하라.

M3 문서 4~5절의 보수적 기술 제안을 이번 구현 기본으로 채택한다.
- 정확 문자열 item ID. 자동 trim/소문자화/중복 제거/ID 변환 금지.
- 새 항목 대조는 []/모호한 정책 집합에서 PASS 보류.
  기업 정책 자체를 무효/FAIL로 선언하거나 select 타입 계약을 바꾸지 않는다.
- SUP-14 모든 항목의 normal/failed/error/unsupported/skipped,
  LIF-07 모든 항목의 not_ready/ready를 명시적으로 연결한다.
  시험 불가/미실행이면 한계·UNKNOWN이며 거짓 attempted를 채우지 않는다.
- 기존 신고 FAIL 유지 → 확인된 해당 축 위반 FAIL → 신고 UNKNOWN 유지
  → 신고 PASS에 새 조건과 기존 공통 PASS 관문 적용.
- SUP-14 원검사 실패와 관문의 잘못된 통과를 구분한다.
  차단 시험 성공을 SUP-14 FAIL로 복사하지 않는다.
- 예외/위험 수용을 검사 성공으로 보지 않는다.
  최초 구현은 개별 예외 자동 수용을 하지 않고 근거 미확인은 UNKNOWN으로 표시한다.
  현재 POL 예외가 판정을 바꾸지 않는 INV-08을 보존한다.
- LIF-07은 준비 전 차단·준비 후 접수/실제 정상 추론·사건 순서를 대조한다.
  health/listener만으로 PASS 금지. 불명 시계 순서를 임의 오차로 확정하지 않는다.
- SUP-14의 신규 functional/performance PASS는 별도 계약 전 UNKNOWN.
  LIF-07은 security/functional 각각 검증하고 performance는 UNKNOWN.
  기존 CPU 비교 계약 및 타 점검의 축별 동작을 유지한다.

양식의 PLAN/POL/팩 전체 Ref, 선정 check, side, 정책 목록을
PLAN이 고정한 실제 객체·보존 팩과 대조한다.
다른 revision/side·중복·미등록 item·잘못된 타입은 import 배치 전체 거부.
관측/첨부 연결이 불완전한 유효 입력은 저장할 수 있으나 해당 PASS는 UNKNOWN.
첨부 파일 읽기는 기존처럼 객체 저장 전에 완료한다.

새 관측은 기존 EVD.observations의 declared_policy_evidence에 저장한다.
참조한 첨부는 현재 entry에 속해야 하고 파일 sha256과 locator·근거를 연결한다.
사람 입력은 DECLARED, 합성 검증 한계는 EXAMPLE, 미인증 자기 신고 문구를 유지한다.
해시를 신고 내용의 진실성이나 승인 인증으로 표현하지 않는다.
별도 정책 최신값 조회·복사나 기존 PLAN의 POL 바꿔치기 금지.

양식 /2, manual-entry 0.3.0, declared 0.3.0을 사용하되
기존 /1 EVD/RES/버전/해시를 변경하지 않는다.
정책 선언이 있는 대상 정의의 구형 증거는 새 판정에서 PASS를 UNKNOWN으로
보류하고 새로운 RES revision·버전·이유를 남긴다. 기존 FAIL은 유지한다.
선언 없는 과거 팩과 다른 점검에 새 요구를 소급하지 않는다.
같은 입력/as_of 재판정은 멱등이어야 한다.
정규화한 새 입력은 새 실행 config hash에 포함하고 과거 해시는 보존한다.

JSON/HTML에는 해당 check/side/item/case의 부족·위반 사유와 근거 식별자를
기존 Report.limitations를 통해 일관되게 표시한다.
현재 RPT가 RES.reasons를 자동 포함하지 않는다는 점을 확인하고 연결하라.
새 객체/SideResult 필드/범용 DSL/조건표/레지스트리는 만들지 않는다.
미인증 신고·DRAFT·EXAMPLE·공식성·원본 무결성 한계를 유지하고 HTML escape를 검증하라.

실제 기업 목록·계정·승인·기한·수치·조건별/역할별 정책을 만들지 않는다.
선택한 두 후속 정책 후보도 이번에 등록하지 않는다.
팩·카탈로그·기존 예시 정책·외부 원문·기존 실행 증거를 변경하지 않는다.
Docker/실제 엔진/두 이미지 시험·AWS/유료 자원·운영 시스템 요청은 하지 않는다.

구현과 테스트 코드를 한 묶음으로 모두 작성한 뒤 테스트한다.
M3-S01~26의 중요한 계약·실패 사례와 기존 불변식 회귀를 검증하고,
실패 수정 후 필요한 재검증만 수행한다.
기존 증거를 덮지 않도록 임시 Store·새 테스트 경로를 사용한다.
기본 pytest, wheel 빌드, 저장소 밖 설치본의 실제 CLI
select→evidence template/import→judge→report→재판정을 확인하라.
기존 M2의 버전·PASS 기대가 의도적으로 바뀌는 테스트는 새 계약에 맞춰
근거를 설명해 갱신하되, 정책 키 존재·FAIL 보존·비대상 점검 회귀를 삭제하지 않는다.
이전 버전 결과가 필요한 호환 검증은 실제 이전 wheel 또는 명시적 역사 fixture를
사용하고 새 판정 결과를 예전 결과처럼 꾸미지 않는다.
수치 통과만으로 실측·운영 준비를 주장하지 않는다.
직접 실행/기존 기록/원격 CI, skip/제외/환경 실패를 구분한다.

실제 결과로 문서화한 뒤 이번 변경만 명시적으로 stage·커밋·push하고
의존 관계에 맞는 base로 초안 PR을 만든다.
main 직접 push·강제 push·자동 병합은 금지한다.
브랜치/커밋/검증 명령·환경·결과/변경 영향/기업 미결 사항/초안 PR을 보고하라.
~~~
