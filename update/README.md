# BYUL UPDATE

> 2026-10-27 프레임워크 동기화: 프레임워크 8a0c7fe와 PACK28/DRAFT를 반영하고, 고정 ID 77개를 유지했다. 기존 실행기·판정기, 정책 25개 점검의 54개 입력 경로, 보안 규칙 연결 13개와 관측 전용 1개의 범위는 유지한다. [변경·검사·개발팀 후속](docs/FRAMEWORK_SYNC_20261027.md)과 [수록 원문 안내](reference/framework/VENDOR_GUIDE.md)를 따른다. 아래 날짜별 팩/실측 수치는 당시 기록이다.

> 2026-10-26 작업 후보: 팩27/DRAFT·정책25점검/54경로·보안 규칙 연결13개·관측 전용1개·실행기12종이다. PLT-01 부분 수집의 독립 위반과 미수집 관문을 보존하며 REQ-02 파일 수집은 설치 CLI에서도 제공한다. PLT-02는 Unix IPC 접근 부분 범위이며 실제 엔진 시험은 보류했다. 기존 점검 재실증은 변경 영향이 있을 때만 해당 범위를 수행한다. 아래 팩26 이하·GUA PR의 현행 표기는 당시 기록이다. [관측 보강](docs/AUTOMATION_HARDENING.md)를 따른다.

## 2026-10-26 통합 작업 기준

- BYUL `main d119ebe`의 JSON 전용 보고서·플랫폼 표시 결정을 보존하고 GUA `8e23bd2`의 U1·Python 3.12를 선택 이관했다. 이관 후 검증 결과는 PROGRESS의 같은 날짜 항목을 따른다.
- 현행 정의는 PACK-BASELINE 25/DRAFT·77점검·정책 24점검/52경로다. 실행·보안 판정 규칙 연결 12점검과 SUP-03 관측 전용 1점검을 구분한다. 실행기 종류는 기존 11종을 재사용하며 SUP-04는 실행 이미지 승인 부분관측이고 SUP-03은 readonly template 신원·API 입력 대응만 수집한다. 전체 공급물 PASS나 독립 소비 검증 완료를 만들지 않는다. [77개 분기별 추적표](docs/CHECKLIST_VALIDATION_STATUS.md)는 코드·원천 자료에서 재생성한다. 연결 수는 전체 분기 검증 완료 수가 아니다.
- UPDATE는 RPT JSON을 생성하며 `platform/api/src/byul_platform/sources/update.py`가 `verification-result/0.2`로 변환한다. `--html`과 자체 보고서 HTML은 복구하지 않는다. OBS 증거 첨부 HTML은 유지한다.
- [공용 경계·입력 변환](docs/BYUL_INTEGRATION.md)과 `../contracts/README.md`를 따른다. 공용 스키마·생산자·표시 규칙 소유는 임의로 바꾸지 않는다.
- U1의 내부 구현과 회귀 사례는 이관했지만 사람 정답 승인·독립 정확성 평가는 미완료다. `triggers`·`carried_over`·`previous_result`·`change_summary`의 공용 상세 표현은 공동 검토 대상이며 원본 RPT를 보존한다.
- 사용자 요청으로 이번 실제 vLLM 실증은 보류한다. Docker daemon 미기동·가용 RAM 제약을 기록하며 과거 GUA 실증을 이번 코드의 실측으로 표시하지 않는다. 기본 시연 설정의 fake와 실제 CPU 실행을 구분한다.

현재 설치·개발·CI의 Python 지원 범위는 **3.12(`>=3.12,<3.13`)**이며 `uv sync --frozen`을 사용합니다. vLLM 대상 이미지의 내부 Python은 별도 환경입니다. 아래 과거 실험의 3.11 표기·원본 증거·팩은 당시 사실대로 보존하며, 3.11용 제품을 별도로 유지하지 않습니다. 전환 검증 범위와 결과는 [설치본 검증](docs/INSTALLED_VERIFICATION.md#python-312-전환-검증)을 확인합니다.

## SUP-04 실행 이미지 승인 후속

[제한 계약과 설정](docs/SUPPLY_IMAGE_APPROVAL.md)을 따른다. 기존 container_config에 실행 프로파일을 추가했으며 실제 vLLM 실증은 보류한다. 기존 76개 정의와 과거 팩은 보존한다.

## GUA 팩22 실증 이력과 지원 조건

**2026-10-25 현행**: PR #30·#31이 main(`fb0c103`)에 병합됐다. 실측 결과는 실행 조건별로 다르므로 나눠 읽는다.

| 실행 | 조건 | 결과 |
|---|---|---|
| PR #30 기본 실행 | `length_cause_evidence` 없음(input_validation 0.5.0) | 후보 REQ-09 보안 UNKNOWN(길이 거부 원인 미확정) → 수락 실패(`accepted=false`). 옵션 없이 실행하면 지금도 같은 결과일 수 있다 |
| PR #31 후속 실행 | `length_cause_evidence = true`(opt-in, 0.6.0), 검토 프로파일과 일치하는 0.32.0 CPU 이미지 | 후보 REQ-09 PASS, 다섯 점검 양쪽 PASS·NO_CHANGE → 수락 성공(`accepted=true`) |
| PLT-04 후보 단독 시험 | 비어 있지 않은 EXAMPLE 후보 | absent·denied PASS / readable FAIL / symlink·directory UNKNOWN |
| 두 이미지 update의 PLT-04 | 후보 목록 `[]` | 부분 범위(파일 접근 미실측) |

성능 UNKNOWN·DRAFT·official=false·실제 변화 탐지 미증명은 그대로다. 상세는 [후속 실측](docs/UPDATE_VALIDATION.md#후보-길이-거부-원인자격-후보-후속)이다. 당시 U1은 설계 단계였다. 아래는 과거 팩22 실측이며, 현행 U1 구현·팩23의 새 실측으로 소급하지 않는다.

2026-10-24 PR #30 당시 기록: PR #29의 v1 통합 결과는 main `641f12f`에 반영됐다. 이 브랜치는 REQ-09의 상태 코드와 실제 소비, PLT-04의 마운트 금지와 후보 접근을 구별한다. [새 팩22 실측](docs/UPDATE_VALIDATION.md#req-09plt-04-판정-의미-보완-후보)은 양쪽 실제 실행·반출/복원·재판정을 완료했지만 후보 REQ-09의 보안 UNKNOWN 때문에 수락 실패(`accepted=false`)다. `guard update` 종료0을 수락 성공으로 세지 않는다. [PR #29 통합 검증](docs/UPDATE_VALIDATION.md#v1-통합-후보-검증)과 아래 PR #28 성공은 팩21의 이력이며 새 계약에 소급하지 않는다. 공식 출시·기업 승인은 별도다.

- 현행 정의는 PACK-BASELINE 22/DRAFT·77개 점검 정의·정책 22점검/50경로·자동 실행/판정 연결 11개다. REQ-09 revision 6·PLT-04 revision 3·adapter 0.4.2·declared 0.14.0이며 다른 점검 정의는 유지한다. 연결 수는 실제 전체 분기 검증 수가 아니다. [정책·로그 증거·설치 재현성 보강](docs/POLICY_RELIABILITY_BATCH.md)은 실제 캐시 격리 실행기를 포함하지 않는다.

- REQ-09는 HTTP 2xx와 오류 객체를 실제 입력 소비로 간주하지 않는다. 확인된 거부와 소비·피해 근거를 구별하고 근거가 없으면 UNKNOWN을 유지한다. PLT-04는 기존 `forbidden_mount_paths`의 명시적 금지 위반 FAIL을 보존하며 선택 정책 `credential_candidate_paths`로 동일 worker 주체의 메타데이터·읽기 권한을 별도 관측한다. 후보 파일 본문을 읽거나 상위 마운트만으로 실제 자격 노출을 주장하지 않는다. [정책 의미와 입력](docs/policy_catalog.md#현행-req-09plt-04-정책-의미)·[실행 안내](docs/RUNBOOK.md)를 따른다.

- (PR #30 기본 실행 기록) 새 실측에서 REQ-09는 기준 보안/기능 PASS, 후보 UNKNOWN/PASS이며 나머지 4개는 양쪽 PASS/PASS다. 후보의 HTTP400 길이 응답은 검토한 거부 원인을 확정하지 못했다. PLT-04는 EXAMPLE의 명시적인 후보 빈 목록을 사용했으므로 파일 접근은 실측하지 않았다. 성능 UNKNOWN·DRAFT·official=false를 유지한다. 실측 뒤 소스 인용 좌표 두 문자열만 정정했으며 원본 실측과 최종 코드 검증의 출처를 구분한다.

- [두 이미지 CPU 실측 결과](docs/UPDATE_VALIDATION.md#실측-결과--c-후속-2026-10-24): PR #28에서 vLLM API 0.31.0/0.32.0의 기존 5개 점검을 양쪽 실제 실행해 보안·기능 PASS, 수락·반출/복원·저장 팩 재판정 일치를 확인했다. 비교는 모두 NO_CHANGE이므로 실제 변화 탐지는 미증명이다. 성능은 UNKNOWN이며 팩·정책 DRAFT로 `official=false`다.

- EVD 10개의 `data_origin=MEASURED`는 실제 수집 출처다. REQ-01/REQ-03 Result의 `verification_level=DECLARED`와 구분한다. REQ-01 일부 상태 변경 경로에는 HTTP를 보내지 않고 TCP 차단 근거를 사용했으며 모든 handler의 실제 실행이나 기업 승인을 증명하지 않는다. 후보 미준비로 두 이미지 시험을 하지 못했던 앞선 단계는 [당시 기록](docs/UPDATE_VALIDATION.md#이번-검증-결과)에 남아 있다.

- [재평가·구현 결과와 개발 계획](docs/REVIEW_AND_DEVELOPMENT_PLAN.md)
- [설치·audit·update·실증 안내](docs/RUNBOOK.md)
- [기업 정책 양식·필수 정책 입력 전제(M1/M2)](docs/POLICY_TEMPLATE.md)
- [Managed 업데이트 설정](examples/update-managed.toml), [외부 대상 설정](examples/update-external.toml)

직전 [자동화 보강](docs/AUTOMATION_HARDENING.md)의 당시 기준은 PACK-BASELINE 20/DRAFT·REQ-11 revision 3·자동 연결 11개·정책 21점검/46경로·카탈로그 322행/614연결·adapter 0.4.0·declared/0.12.0이다. 현행 REQ-11도 입력 projection 하위 검증과 생성 출력 귀속 미검증 구분·전체 보안 PASS 보류를 유지한다. 과거 1~3차 CPU 실측·PR #28 실측·통합판 합성 회귀를 합산하지 않는다.

[3차 실제 CPU 기록](docs/AUTOMATION_PHASE3.md)과 [선별 증거](docs/evidence/automation-phase3-20261023.json)는 당시 팩19의 이력이다. 새 판정 계약을 과거 결과에 소급하지 않는다. [Windows CPU JSON/SSE·native INFO 설정](examples/windows-cpu-binding-logs.toml)과 [11점검 시험 정책](examples/automation-binding-logs-policy.yaml)은 EXAMPLE/DRAFT다. 기본 guard.toml은 fake 시연 설정이며 실제 모델 snapshot·ENV·정책은 별도 준비한다. prepare-*의 ready와 점검별 지원, 실제 실행 결과는 서로 다른 정보다.

Windows CPU 단일 관리 기동 `guard prepare-validation`·`guard validate`와 8개 실행기의 기존 직접 검증은 [자동화 1차](docs/AUTOMATION_PHASE1.md)의 과거 결과로 보존합니다. 기존 5개와 REQ-10·REQ-13·OBS-03의 당시 지원 범위·미확인 분기도 해당 기록을 따릅니다. 두 실제 이미지의 업데이트 비교는 별도 검증이며 기본 guard.toml은 EXAMPLE 시연 설정입니다.

[Windows CPU 설정](examples/windows-cpu.toml)과 [8개 점검 시험 정책](examples/automation-policy.yaml)은 EXAMPLE/DRAFT 예시입니다. 실제 ENV·모델 snapshot 경로·기업 정책을 확인해야 하며 누락한 관측을 PASS로 채우지 않습니다.


실증 준비 검사·증거 반출/복원·실제 5개 점검 사례 행렬: [실증 안내](docs/VALIDATION.md).


수동 증거와 업데이트 기능의 통합 및 --replace-manual 사용: [통합 안내](docs/INTEGRATION.md).

## 과거 정책 개발 단계의 기록

아래 M10~M13의 팩 revision·구현 예정 문구는 각 단계 당시 상태다. 현행 정의와 실증 범위는 위 요약을 따른다.

### M10 REQ-09 입력 범위

REQ-09의 endpoint·형식 정책은 단일 engine POST/token_ids 프로파일에서 실행·재판정·수동/3·JSON/HTML에 연결한다. 빈 양식은 값을 만들지 않으며 기존 정책에 두 required 키가 없으면 미입력으로 남는다. 실제 기업 목록을 축소해 시험에 맞추지 않는다. 합성 계약 검증과 실제 엔진 검증을 구분한다. [M10 구현·검증](docs/POLICY_M10.md)을 참고한다.

### M11 수동 PASS 일관성과 다음 캐시 계약

REQ-09의 수동 보안/기능 PASS는 같은 관측의 판정표와 일치할 때만 유지한다. 상충·평가 불가는 UNKNOWN으로 기록하며 기존 신고FAIL/UNKNOWN·과거증거를 보존한다. [M11 구현·캐시 설계·M12 프롬프트](docs/POLICY_M11.md)를 참고한다. 캐시 정책은 이번에 등록하지 않았다.

### M12 REQ-16 필수 캐시 문맥 수동 계약

required_cache_contexts와 문맥별3case의 수동 증거 검증·판정·JSON/HTML 연결을 추가했다. 제한된 DECLARED 계약이며 실제 캐시/신원 보호 자동화는 아니다. 팩16/DRAFT·자동실행5를 유지하고 [M12 입력 절차와 검증 결과](docs/POLICY_M12.md)를 제공한다.

### M13 캐시 문맥 unknown 판정과 REQ15 설계

REQ16의 허용 unknown 상태를 형태 오류와 구분한다. 보안 관측이 완결된 경우 추론 미확인만으로 보안 근거를 지우지 않고 기능 PASS를 보류한다. [M13 구현·검증·REQ15 계약과 M14 프롬프트](docs/POLICY_M13.md)를 제공한다. REQ15 정책은 설계 제안이며 아직 등록하지 않았다.
