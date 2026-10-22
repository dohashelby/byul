# DEAR. 신가영님
reference 폴더랑 CLAUDE.md랑 *_PROMPT.md로 개발 시작했습니다. 업데이트 검증만이고, 뼈대 완성했고, 이제 정책 자동화하고 있습니다.

## 실행과 현재 상태

- [재평가·구현 결과와 개발 계획](docs/REVIEW_AND_DEVELOPMENT_PLAN.md)
- [설치·audit·update·실증 안내](docs/RUNBOOK.md)
- [기업 정책 양식·필수 정책 입력 전제(M1/M2)](docs/POLICY_TEMPLATE.md)
- [Managed 업데이트 설정](examples/update-managed.toml), [외부 대상 설정](examples/update-external.toml)

guard update CLI와 scoped 실행기 5개가 연결돼 있습니다. 신규 PLT-04·REQ-09는 모의 계약 검증 상태이며,
두 실제 이미지의 업데이트 비교 실증은 아직 완료하지 않았습니다. 기본 guard.toml은 EXAMPLE 시연 설정입니다.


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
