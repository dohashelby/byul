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

## 6단계 — rule_table 판정기 (2026-10-19)
- 한 일: `judges/base.py`(Judge Protocol, JudgeOutput), `judges/rule_table.py`(eq/ne/in/not_in/lt/le/gt/ge, 축별 표), `core/judge.py`(INV-11 PASS 관문, FAIL 유지, 플러그인 실패→UNKNOWN, 예외 표시, verification_level), 재판정 시 같은 내용이면 revision을 만들지 않음
- 테스트 결과: 151 passed. REQ-01 가짜 증거 pass/direct_200/not_attempted/missing_obs → PASS/FAIL/UNKNOWN/UNKNOWN (`tests/test_req01_scenarios.py`), INV-01·03·04·08·11·14·15·18, INV-10 RES 부분
- 다음 단계: 7단계 report
- 막힌 것: 없음

## 7단계 — report (2026-10-19)
- 한 일: `core/aggregate.py`(집계 4규칙), `core/compare.py`(비교표 9칸 + NOT_COMPARABLE, INV-09 비교 조건), `core/report.py`(RPT: by_check/by_purpose/by_area/not_selected/manual_required/limitations/inputs_manifest, approval 항상 null), `report/html.py`(JSON에서만 렌더링, 한국어 표시), `cli.py`(select/run/judge/report/verify)
- 테스트 결과: **208 passed, 3 deselected(engine) in ~6s**. 집계 10케이스, 비교 19케이스, INV-07·09·17, 보고서 구조, CLI e2e(4 시나리오)
- 실행 확인: 아래 명령으로 `out/report.json`, `out/report.html` 생성. REQ-01 security PASS(level EXAMPLE), 목적·영역은 UNKNOWN(DRAFT 팩 → inventory 미완결, INV-13), official=false
  ```bash
  PLAN=$(uv run guard select --pack packs/PACK-BASELINE --env examples/env.yaml --policy examples/policy.yaml | tail -1)
  uv run guard run --plan $PLAN [--fake-scenario pass|direct_200|not_attempted|missing_obs]
  uv run guard judge --plan $PLAN
  uv run guard report --plan $PLAN --json out/report.json --html out/report.html
  uv run guard verify
  ```
- 막힌 것: 없음

## 불변식 테스트 현황
INV-01~18 모두 `tests/invariants/test_inv_XX.py`에 있다. 엔진이 필요해 skip한 것(`-m engine`):
- INV-03 실제 직통 요청의 연결 거부·타임아웃 → 5단계
- INV-09 두 이미지 실제 비교(`guard update`) → 8단계
- INV-15 실제 어댑터 기동 실패 전파 → 5단계

## 5단계로 넘어가기 전에 사람이 정해야 할 것
1. **시험용 Gateway 구성**
   - 어떤 Gateway를 쓸지(예: nginx/envoy 리버스 프록시 컨테이너 1개, API 키 헤더 검사만). 우리가 Gateway를 *만들지는* 않는다(SPEC §9) — 시험 고정물(fixture)로 기성 이미지를 쓸지 결정.
   - 허용 키·비허용 키(무키/오키) 값, Gateway가 엔진에 전달하는 신원 헤더 여부.
   - 망 구성: Gateway와 엔진을 같은 Docker 네트워크에 두고, "직통"을 무엇으로 볼지(호스트에서 엔진 포트 직접 / 같은 네트워크의 다른 컨테이너에서). REQ-01 관측 `direct_route_status`의 출발점 정의.
   - 시험할 route 목록: `/v1/completions`, `/v1/chat/completions`, `/invocations` 등 (원문 미확인 기준 "/v1만 확인" 방지). 지금 관측 키는 2개 — route별 관측으로 늘릴지(`direct_route_status.<route>`) 결정.
   - 엔진 자체 `--api-key`를 켤지(켜면 직통도 401 → PASS가 "엔진 키" 때문인지 "망 차단" 때문인지 구분 필요).
2. **고정 모델** — CPU/작은 GPU에서 뜨는 작은 모델 1개(id·revision 고정, inventory_sha256 계산 방법). 이미지 digest 고정(`vllm/vllm-openai@sha256:...` 또는 CPU 빌드). GPU 없이 돌릴지 여부.
3. **env.yaml 실제 값** — 예시(`examples/env.yaml`)는 EXAMPLE이다. 실제로는:
   ```yaml
   data_origin: DECLARED
   engine: { name: vllm, version: "0.31.0", source_commit: <hex>, image_digest: "sha256:..." }
   adapter: { name: vllm, version: "0.1.0" }
   runtime: { nodes: 1, tp_groups: 1, gpu_count: <n>, gpu_model: <str>, driver: <str> }
   model: { id: <hf id>, revision: <commit>, inventory_sha256: <hex> }
   facts:
     gateway: { value: true, origin: DECLARED, source: "배포 문서 링크" }
   ```
   `false`로 신고할 사실은 `absence_evidence`(비사용 근거)가 필요하다.
4. **policy.yaml 실제 값** — `values.REQ-01.allowed_callers`(허용 주체), `scope`(첫 실측은 `include: [REQ-01]` 권장), 확정자(`status: CONFIRMED`, `confirmed_by`, `confirmed_at`). 확정 + 팩 REVIEWED + 실측일 때만 `official: true`.
5. **팩 검토** — `packs/PACK-BASELINE/pack.yaml`의 `review`를 누가 REVIEWED로 바꿀지. 지금은 DRAFT라 모든 보호 목적이 UNKNOWN 집계.
6. **REQ-01 판정표 확인** — `tools/overrides/REQ-01.yaml`: 직통 401/403 → 거부, 200 → FAIL, 그 밖(404·연결 거부 등) → UNKNOWN. 404를 거부로 볼지 결정 필요(원문: "연결 실패·404만으로 전체 route 통과 불가").
7. **5단계 의존성** — Docker 제어를 `subprocess`(docker CLI)로 할지, 별도 라이브러리(docker SDK, 의존성 추가 → 승인 필요)를 쓸지.
