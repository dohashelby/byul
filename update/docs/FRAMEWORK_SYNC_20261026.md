# 프레임워크 기준 동기화 — 2026-10-26

- byul 기준: `d119ebe446144ae707e6f13ad11eb0d786788217`.
- 확정 원문: [serving-security-framework 0ec5fb5](https://github.com/mg-princess/serving-security-framework/tree/0ec5fb5fa5633b0817468b27684690a66fc7b4f5). 로컬 미커밋 확장은 포함하지 않았다.
- PACK-BASELINE 22 → 23, 77개 내부 ID 유지, 신규·퇴역 0개, 검토 상태 DRAFT. 기업 정책·배포 승인은 생성하지 않았다.
- 원문 15개 파일을 해당 커밋과 바이트 단위로 맞췄다. 체크리스트 SHA-256은 `7f857208af8071b9934695f32b8753000f0f7b79aa0d47d1bd42945184875ef0`.
- 2026-10-19 논리·사용성 검토 2개 파일은 과거 기록으로 보존했다. 최신 근거로 사용하지 않는다. 원문 README의 별도 근거 링크는 위 고정 커밋에서 확인한다.

## 생성 정의와 호환성

기존 질문·시험 식별자와 최신 행의 시험 식별자·검토 순서를 대조해 77개 대응을 등록했다. 이후에는 고정 CHK ID를 우선한다. 질문이 같거나 시험 식별자가 겹쳐도 기존 ID를 재배정하지 않는다. 중복 고정 ID는 쓰기 전에 거부한다.

`framework_id`, `framework_purpose`, `mechanism_ids`를 보존한다. 실행용 `purpose`와 부목적은 기존 선정·집계 계약을 유지한다. 최신 목적 분류를 실행용 집계에 적용하는 작업은 별도 개발 범위다. 호환 대응은 `tools/framework_compatibility.yaml`에서 관리한다.

77개 질문·적용 설명·필요 관측 설명·보완 안내가 갱신됐다. 실행기, 판정 플러그인·규칙, 구조화 관측값, 정책 매개변수, 구조화 적용 조건, 개발팀 overrides는 유지했다. 원문 설명이 바뀌어도 실행 규칙이 자동으로 확대되지는 않는다. 모든 정의 revision은 1씩 증가했다. 예제 정책의 팩 참조와 배포용 정책 설명 메타데이터도 23으로 갱신했다. 실제 저장된 과거 결과·정책·증거는 변경하지 않았다. 과거 PACK21 재판정 테스트는 당시 정의 77개와 ID 맵을 보존해 원래 해시로 검증한다.

## 판정 문구가 달라진 19개

| 내부 ID | 원문 CHK ID | 변경 필드 |
|---|---|---|
| LIF-10 | CHK-LIF-010 | satisfied_if |
| LIF-13 | CHK-LIF-013 | satisfied_if, fail_criteria, unknown_criteria |
| OBS-01 | CHK-OBS-001 | satisfied_if, fail_criteria, unknown_criteria |
| OBS-02 | CHK-OBS-002 | satisfied_if |
| OBS-04 | CHK-OBS-004 | unknown_criteria |
| OBS-06 | CHK-OBS-006 | satisfied_if, fail_criteria, unknown_criteria |
| OBS-10 | CHK-OBS-010 | satisfied_if, fail_criteria, unknown_criteria |
| OBS-11 | CHK-OBS-011 | satisfied_if, fail_criteria, unknown_criteria |
| PLT-07 | CHK-PLT-007 | unknown_criteria |
| PLT-08 | CHK-PLT-008 | satisfied_if, fail_criteria, unknown_criteria |
| REQ-05 | CHK-REQ-005 | satisfied_if, fail_criteria, unknown_criteria |
| REQ-08 | CHK-REQ-008 | satisfied_if |
| REQ-09 | CHK-REQ-009 | satisfied_if, fail_criteria, unknown_criteria |
| REQ-14 | CHK-REQ-014 | satisfied_if |
| REQ-21 | CHK-REQ-021 | satisfied_if |
| SUP-06 | CHK-SUP-006 | satisfied_if, unknown_criteria |
| SUP-10 | CHK-SUP-010 | satisfied_if |
| SUP-11 | CHK-SUP-011 | satisfied_if |
| SUP-14 | CHK-SUP-014 | satisfied_if, fail_criteria, unknown_criteria |

상세 전후 값은 [항목별 변경 JSON](evidence/framework-sync-20261026.json)에 보존했다. 표현 정리와 조건 보완이 함께 있으므로 19개 모두 새로운 판정 규칙이라고 해석하지 않는다. 예컨대 REQ-09의 개발팀 판정 규칙은 그대로이며 최신 원문의 판정 설명만 갱신됐다.

## 후속 개발 범위

최신 원문의 추가 증거·분기 요구를 자동 수집·수동 입력·판정에 얼마나 반영했는지는 별도 적용 검토가 필요하다. 이번 작업은 기존 실행·판정 코드 보존을 우선한다. 변경 영향 기반 선정(U1), 최신 목적 체계에 따른 집계 개편, 추가 엔진 시험은 수행 범위에 포함하지 않았다.

## 검증

- 기본 전체: `python -m pytest -q` → **2905 passed, 18 deselected, 112.36초**. Docker·실제 엔진 시험 제외. 부모 프로세스 별도 연속 타이머는 기록하지 않았다.
- 고정 ID 변경·재정렬·중복 시험 식별자 및 중복 ID 거부 회귀 통과. 재생성 바이트 동일성 통과.
- 원문 15개 파일의 SHA-256 확인. runtime·실행기·판정기·override 파일 변경 없음. `src` 아래 변경은 생성된 정책 메타데이터의 팩 revision·hash뿐이다.
- 과거 PACK21의 원본 해시·반출·복원·재판정 검사 통과. 승인·실제 엔진 결과를 생성하지 않았다.
- 초기 실행의 로컬 포트 제한, 목적 분류 호환 문제, 과거 팩 재구성 문제를 확인하고 위 보완 후 재검증했다. 판정 기대값·무결성 검사를 완화하지 않았다.
- `git diff --check`: 확정 원문의 의도된 Markdown 줄바꿈 공백 14줄을 보존했다. 참조 원문 외 변경에는 공백 오류가 없다.
