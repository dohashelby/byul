`SPEC.md`와 `CLAUDE.md`를 먼저 끝까지 읽어라. 이어서 `reference/framework/data/checklist.json`의 구조(`supply_assessment_draft` 등 5개 배열의 한 행, `items`의 한 항목)와 `environment_template.json`, `result_template.json`을 코드로 훑어 확인해라. `reference/` 아래 다른 문서는 필요할 때만 열어라(03 문서는 통째로 읽지 마라).

이번 세션의 목표는 SPEC §8의 **1, 2, 3, 4, 6, 7단계**를 만드는 것이다.
- **5단계(실제 vLLM 어댑터와 직통 요청 실행기)와 8단계는 이번에 만들지 않는다.** 대신 `EngineAdapter`, `Executor` 인터페이스(Protocol)와, 미리 정한 값을 돌려주는 `FakeExecutor`까지만 만들어서 `select → run → judge → report`가 가짜 증거로 끝까지 도는 것을 보여라.
- 실제 Docker·GPU·네트워크를 사용하지 마라.

진행 순서
1. 계획: 시작 전에 단계별 작업 목록, 파일 구조, 내가 정해줘야 할 것(있으면)을 먼저 보여줘라. 내가 "진행"이라고 하면 시작해라.
2. 1단계: `tools/import_checklist.py` — 77행을 `packs/PACK-BASELINE/checks/*.yaml`로 변환. ID는 SPEC §2.1의 영역코드-순번이며 재실행해도 같아야 한다. 구조화하지 못하는 필드(`applies_when`, `required_observations`)는 원문(`prose`)을 보존하고 `executor: null`로 둔다.
3. 2~4단계: 모델(Pydantic), 해시, 저장소, `guard select`(적용 판단 규칙 5개, 선정 규칙).
4. 6~7단계: `rule_table` 판정기, 집계·비교 규칙, 보고서 JSON과 HTML.
5. 각 단계마다 해당 불변식 테스트를 먼저 쓰고, 통과시킨 뒤 `PROGRESS.md`를 갱신하고 다음 단계로 가라.
6. 자동화 대상 점검은 `REQ-01`(Gateway 우회 직통 요청, 요청 영역 1번 행) 하나다. `FakeExecutor`는 이 행에 대해 (a) 정상 통과 (b) 직통이 200 응답(FAIL) (c) 챌린지 미시도 (d) 관측 누락 네 가지 증거를 만들 수 있어야 하고, 각각 PASS / FAIL / UNKNOWN / UNKNOWN으로 판정되는 것을 테스트로 보여라.

완료 기준
- `uv run pytest`가 1분 안에 전부 통과한다 (엔진 필요 테스트는 제외).
- 불변식 INV-01~18 각각에 대응하는 테스트가 있다 (엔진이 필요해 지금 못 하는 것은 이유를 적고 `skip`).
- `uv run guard select / run / judge / report`가 `FakeExecutor`로 REQ-01을 처음부터 끝까지 실행하고 JSON·HTML 보고서가 생성된다.
- 마지막에 `PROGRESS.md`에 5단계로 넘어가기 위해 사람이 정해야 할 것(시험용 Gateway 구성, 고정 모델, env.yaml/policy.yaml 예시)을 정리해라.

지켜야 할 것: SPEC §9의 "만들지 않는 것"은 만들지 마라. SPEC과 어긋나는 결정이 필요하면 멈추고 물어라. 파일을 이 폴더 밖에 만들지 마라.
