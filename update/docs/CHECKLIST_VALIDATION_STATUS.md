# 체크리스트 구현·실험 추적

이 문서와 CSV는 `tools/checklist_status.py`의 생성물이다. 수정할 정본은 팩·registry·기존 자동화표·명시된 선별 실험 기록이다.

현행 팩 revision 29의 정의 77개, 정책 등록 27점검/74경로, 실행·보안 판정 규칙 연결 15개, 관측 전용 1개, 나머지 미연결 61개다. 연결 수는 실제 전체 분기 검증 완료 수가 아니다.

- [분기별 전체 CSV](checklist_validation_status.csv): 현재 정의·필수 관측·정상/위반/미수집/지원 밖 계획·선행 조건·기업 결정·과거 EVD/RES를 연결한다.
- 이번 구현 묶음은 사용자 지시에 따라 실제 vLLM 실증을 보류한다. `current_pack_actual_status=DEFERRED_NOT_MEASURED`이며, 과거 기록이나 단위 시험을 현행 팩의 새 실측으로 승격하지 않는다.
- `historical_pack_revisions`와 EVD/RES는 구조화된 과거 CPU 선별 기록(팩17·18·19)에서만 읽는다. 팩21·22의 후속 두 이미지 결과는 원래 자동화표의 `historical_verification`·`remaining_scope`와 [실측 문서](UPDATE_VALIDATION.md)를 함께 따른다.
- 이 선별 JSON은 독립 복원 가능한 원본 bundle 전체가 아니다. 추적된 EVD Ref만으로 현행 코드 재현·무결성 검증·기업 승인을 주장하지 않는다.
- A/B/C는 과거 환경 가능성 분류다. Windows 지원 확정·현재 시험 완료·미지원의 비적용 판정으로 사용하지 않는다.
- CSV의 모든 시험 계획은 아직 실행 결과가 아니다. 정상 기대값도 현행 계약의 증거 관문을 통과해야 하며, REQ-11은 출력 귀속 미검증 시 보안 UNKNOWN을 유지한다.

- 보안 PASS·FAIL 규칙이 모두 비어 있는 관측 전용 프로파일은 실행·판정 규칙 연결 수에서 제외한다. 파일 존재·설정·API 입력 대응은 독립 worker 소비 증명이 아니며, 이런 관측만으로 보안 PASS·FAIL을 확정하지 않는다.

| 실험 묶음 | 점검 수 |
|---|---:|
| ACCESS_AUTHORIZATION | 6 |
| CACHE_IDENTITY | 3 |
| INPUT_RESPONSE | 13 |
| LIFECYCLE_RECOVERY | 14 |
| LOG_OBSERVABILITY | 11 |
| PLATFORM_CREDENTIAL_NETWORK | 15 |
| SUPPLY_APPROVAL | 15 |

우선순위는 연결된 점검의 관측 공백(P1), 로컬 신규 구현(P2), 추가 인프라(P3), 기업 환경(P4)이다. 기존 점검 재실증은 실행기·정의·설정·시험 자료·환경의 변경 영향이 있을 때만 해당 범위를 수행한다. 팩 revision 변경만으로 기존 5개 전체 재실증을 요구하지 않는다. 어려운 계측 하나가 독립적인 신규 점검을 모두 막지는 않는다.

REQ-05는 최종 모델·alias·퇴역 방침, LIF-09는 대상의 부분 실패와 외부 접수 gate, OBS-04는 실제 생성·송신 관측을 먼저 확정한다. 기존 실행기 재사용 후보가 곧 해당 점검의 지원이나 실행 완료를 뜻하지 않는다.

실험 완료 시 현재 팩·이미지 digest·모델·정책·분기별 EVD/RES·재현 명령·원본 RPT·플랫폼 계약 검증·미검증 범위를 별도 실험 기록으로 남긴 뒤 정본에 연결한다. 본 추적 도구는 엔진·네트워크·테스트를 실행하지 않는다.

재생성: `uv run python -m tools.checklist_status`

변경 확인: `uv run python -m tools.checklist_status --check`
