# DEAR. 신가영님
reference 폴더랑 CLAUDE.md랑 *_PROMPT.md로 개발 시작했습니다. 업데이트 검증만이고, 뼈대 완성했고, 이제 정책 자동화하고 있습니다.

## 실행과 현재 상태

- [재평가·구현 결과와 개발 계획](docs/REVIEW_AND_DEVELOPMENT_PLAN.md)
- [설치·audit·update·실증 안내](docs/RUNBOOK.md)
- [기업 정책 양식·필수 정책 입력 전제(M1/M2)](docs/POLICY_TEMPLATE.md)
- [Managed 업데이트 설정](examples/update-managed.toml), [외부 대상 설정](examples/update-external.toml)

현행 [자동화 2차](docs/AUTOMATION_PHASE2.md)는 REQ-07의 고정 템플릿 적용 후 입력 점검을 더해 자동 실행기 연결 9개, 정책 19점검·41경로, PACK-BASELINE 18/DRAFT로 구성합니다. REQ-07 정의는 revision 2, 어댑터는 0.3.0, 수동 판정기는 declared/0.11.0입니다. 카탈로그는 317행·609연결이며 원문 정책 결정 전체의 자동화 완료 수는 아닙니다. [작업 프롬프트·완료 계약](docs/AUTOMATION_PHASE2_PROMPT.md)을 함께 확인합니다.

2차 구현·회귀·실제 CPU 검증을 수행했습니다. 기본 전체 1,781통과·5 skip·18제외, 마지막 어댑터 보수 관련 128회귀 통과입니다. 실제 127 경계와 32 반례의 판정, 기존 UNKNOWN/FAIL 보존, 증거 반출·복원·저장 팩 재판정은 [직접 기록](docs/AUTOMATION_PHASE2.md#검증-기록)과 [선별 증거](docs/evidence/automation-phase2-20261023.json)를 따릅니다. [Windows CPU chat 설정](examples/windows-cpu-chat.toml)과 [시험 정책](examples/automation-chat-policy.yaml)은 EXAMPLE/DRAFT입니다. OPT의 지시 학습·system 비공개성을 보장하지 않으며 성능 UNKNOWN·official=false를 유지합니다.

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
