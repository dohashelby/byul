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
