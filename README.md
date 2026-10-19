# DEAR. 신가영님
reference 폴더랑 CLAUDE.md랑 *_PROMPT.md로 개발 시작했습니다. 업데이트 검증만이고, 뼈대 완성했고, 이제 정책 자동화하고 있습니다.

## 실행과 현재 상태

- [재평가·구현 결과와 개발 계획](docs/REVIEW_AND_DEVELOPMENT_PLAN.md)
- [설치·audit·update·실증 안내](docs/RUNBOOK.md)
- [Managed 업데이트 설정](examples/update-managed.toml), [외부 대상 설정](examples/update-external.toml)

guard update CLI와 scoped 실행기 5개가 연결돼 있습니다. 신규 PLT-04·REQ-09는 모의 계약 검증 상태이며,
두 실제 이미지의 업데이트 비교 실증은 아직 완료하지 않았습니다. 기본 guard.toml은 EXAMPLE 시연 설정입니다.


실증 준비 검사·증거 반출/복원·실제 5개 점검 사례 행렬: [실증 안내](docs/VALIDATION.md).
