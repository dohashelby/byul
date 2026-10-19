# PROGRESS

## 0단계 — 프로젝트 골격 (2026-10-19)
- 한 일: pyproject(uv, Python 3.11), 디렉토리, DECISIONS.md
- 테스트 결과: -
- 다음 단계: 1단계 import_checklist
- 막힌 것: 없음

## 1단계 — import_checklist (2026-10-19)
- 한 일: `tools/import_checklist.py`, `tools/overrides/REQ-01.yaml`, `packs/PACK-BASELINE/{pack.yaml,id_map.yaml,checks/*.yaml}` 77개 생성. ID는 id_map에 고정, 내용 변경 시 CheckDef·팩 revision 증가, 사라진 행은 retired.
- 테스트 결과: `tests/test_import_checklist.py` 9 passed (77개, 재실행 바이트 동일, 문구 변경·순서 변경에도 ID 유지, retired·신규 번호, 매니페스트 해시, 커밋된 팩 = 변환기 출력)
- 다음 단계: 2단계 모델·해시
- 막힌 것: 없음

## 2단계 — 모델·해시 (2026-10-19)
- 한 일: `models/` enum·공통 필드(GuardObject, Ref)·CheckDef·팩 로더(파일 해시 검증)·객체 7종·ULID·해시(§2.3)
- 테스트 결과: 39 passed (7종 YAML 왕복, 해시 고정값, 변조 감지, EVD 판정 필드 금지, INV-02·12·18 모델 수준)
- 다음 단계: 3단계 저장소
- 막힌 것: 없음

## 3단계 — 저장소 (2026-10-19)
- 한 일: `store/sqlite.py` append-only(UPDATE/DELETE 트리거 차단, revision은 latest+1만), 쓸 때 해시·참조 검증, `resolve(ref)` 정확한 revision, 내용 주소 `artifacts/<sha256>`, `verify()` 일괄 검증
- 테스트 결과: 48 passed (INV-16 9개)
- 다음 단계: 4단계 select
- 막힌 것: 없음

## 4단계 — select (2026-10-19)
- 한 일: `core/select.py`(적용 판단 규칙 5개, 선정 규칙, POLICY_SCOPE, NO_EXECUTOR/NO_POLICY_VALUE, inventory_complete), `collectors/file.py`(env.yaml/policy.yaml), `examples/`, `guard.toml`
- 테스트 결과: 81 passed (규칙 1~5, 선정 규칙, INV-05·06·13, 수집기)
- 다음 단계: (5단계 대체) Protocol + FakeExecutor + run
- 막힌 것: 없음

## (5단계 대체) 인터페이스 + FakeExecutor + run (2026-10-19)
- 한 일: `adapters/base.py`(EngineAdapter Protocol), `adapters/fake.py`, `executors/base.py`(Executor Protocol, EvidenceDraft), `executors/fake.py`(pass/direct_200/not_attempted/missing_obs, 항상 EXAMPLE), `core/run.py`
- 테스트 결과: 94 passed (FakeExecutor 4종, INV-10 run 부분, INV-15 run 부분, 코어 엔진 이름 금지)
- 다음 단계: 6단계 rule_table 판정기
- 막힌 것: 없음. 실제 vLLM 어댑터·직통 실행기는 이번 범위 밖
