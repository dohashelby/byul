# DEAR. 신가영님
reference 폴더랑 CLAUDE.md랑 *_PROMPT.md로 개발 시작했습니다. 업데이트 검증만이고, 뼈대 완성했고, 이제 정책 자동화하고 있습니다.

## 실행과 현재 상태

- [정책·로그 증거·설치 재현성 보강](docs/POLICY_RELIABILITY_BATCH.md): 현재 팩21/DRAFT·REQ-15 revision 2·정책22점검/49경로·자동 연결11개. 로그 수집/저장 증거 재검증, REQ-15 수동 계약, 설치본 CI와 준비 안내를 연결한다. 실제 캐시 격리 실행기·두 이미지 실증과 구분한다.

- [두 이미지 CPU 업데이트 비교 수락 도구와 현재 검증](docs/UPDATE_VALIDATION.md): 준비·명시적 실행·증거 반출/복원·저장 팩 재판정. 두 실제 이미지 실증은 후보 이미지 미준비로 미수행이다.

- [재평가·구현 결과와 개발 계획](docs/REVIEW_AND_DEVELOPMENT_PLAN.md)
- [설치·audit·update·실증 안내](docs/RUNBOOK.md)
- [기업 정책 양식·필수 정책 입력 전제(M1/M2)](docs/POLICY_TEMPLATE.md)
- [Managed 업데이트 설정](examples/update-managed.toml), [외부 대상 설정](examples/update-external.toml)

직전 [자동화 보강](docs/AUTOMATION_HARDENING.md)의 당시 기준은 PACK-BASELINE 20/DRAFT·REQ-11 revision 3·자동 연결 11개·정책 21점검/46경로·카탈로그 322행/614연결·adapter 0.4.0·declared/0.12.0이다. 현재 declared는 0.13.0이며 REQ-11의 입력 projection 하위 검증과 생성 출력 귀속 미검증 구분·전체 보안 PASS 보류를 유지한다. 이번 묶음도 실제 엔진을 기동하지 않는다. 과거 1~3차 CPU 실측과 새 합성 회귀를 합산하지 않는다.

[3차 실제 CPU 기록](docs/AUTOMATION_PHASE3.md)과 [선별 증거](docs/evidence/automation-phase3-20261023.json)는 당시 팩19의 이력이다. 새 판정 계약을 과거 결과에 소급하지 않는다. [Windows CPU JSON/SSE·native INFO 설정](examples/windows-cpu-binding-logs.toml)과 [11점검 시험 정책](examples/automation-binding-logs-policy.yaml)은 EXAMPLE/DRAFT다. 기본 guard.toml은 fake 시연 설정이며 실제 모델 snapshot·ENV·정책은 별도 준비한다. prepare-*의 ready와 점검별 지원, 실제 실행 결과는 서로 다른 정보다.

Windows CPU 단일 관리 기동 `guard prepare-validation`·`guard validate`와 8개 실행기의 기존 직접 검증은 [자동화 1차](docs/AUTOMATION_PHASE1.md)의 과거 결과로 보존합니다. 기존 5개와 REQ-10·REQ-13·OBS-03의 당시 지원 범위·미확인 분기도 해당 기록을 따릅니다. 두 실제 이미지의 업데이트 비교는 별도 검증이며 기본 guard.toml은 EXAMPLE 시연 설정입니다.

[Windows CPU 설정](examples/windows-cpu.toml)과 [8개 점검 시험 정책](examples/automation-policy.yaml)은 EXAMPLE/DRAFT 예시입니다. 실제 ENV·모델 snapshot 경로·기업 정책을 확인해야 하며 누락한 관측을 PASS로 채우지 않습니다.


실증 준비 검사·증거 반출/복원·실제 5개 점검 사례 행렬: [실증 안내](docs/VALIDATION.md).


수동 증거와 업데이트 기능의 통합 및 --replace-manual 사용: [통합 안내](docs/INTEGRATION.md).

### M10 REQ-09 입력 범위

REQ-09의 endpoint·형식 정책은 단일 engine POST/token_ids 프로파일에서 실행·재판정·수동/3·JSON/HTML에 연결한다. 빈 양식은 값을 만들지 않으며 기존 정책에 두 required 키가 없으면 미입력으로 남는다. 실제 기업 목록을 축소해 시험에 맞추지 않는다. 합성 계약 검증과 실제 엔진 검증을 구분한다. [M10 구현·검증](docs/POLICY_M10.md)을 참고한다.

### M11 수동 PASS 일관성과 다음 캐시 계약

REQ-09의 수동 보안/기능 PASS는 같은 관측의 판정표와 일치할 때만 유지한다. 상충·평가 불가는 UNKNOWN으로 기록하며 기존 신고FAIL/UNKNOWN·과거증거를 보존한다. [M11 구현·캐시 설계·M12 프롬프트](docs/POLICY_M11.md)를 참고한다. 캐시 정책은 이번에 등록하지 않았다.

### M12 REQ-16 필수 캐시 문맥 수동 계약

required_cache_contexts와 문맥별3case의 수동 증거 검증·판정·JSON/HTML 연결을 추가했다. 제한된 DECLARED 계약이며 실제 캐시/신원 보호 자동화는 아니다. 팩16/DRAFT·자동실행5를 유지하고 [M12 입력 절차와 검증 결과](docs/POLICY_M12.md)를 제공한다.

### M13 캐시 문맥 unknown 판정과 REQ15 설계

REQ16의 허용 unknown 상태를 형태 오류와 구분한다. 보안 관측이 완결된 경우 추론 미확인만으로 보안 근거를 지우지 않고 기능 PASS를 보류한다. [M13 구현·검증·REQ15 계약과 M14 프롬프트](docs/POLICY_M13.md)를 제공한다. REQ15 정책은 설계 제안이며 아직 등록하지 않았다.
