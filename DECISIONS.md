# DECISIONS

날짜 · 결정 · 이유 (한 줄씩). SPEC과 참고 문서가 충돌하면 SPEC을 따르고 여기 적는다.

## 결정
- 2026-10-19 · Python 3.11은 uv가 내려받은 인터프리터를 쓴다(사람 승인 "A"). 설치용 네트워크만 사용. · 로컬에 3.14뿐이었음.
- 2026-10-19 · 점검 행 ID는 첫 변환 때 배열 순서로 붙이고 `packs/PACK-BASELINE/id_map.yaml`에 고정한다. 재변환 시 질문 문구 → `subtest_ids`(유일할 때만) 순으로 기존 ID와 대조하고, 못 찾은 새 행은 영역의 다음 번호, 사라진 행은 `retired: true`. · SPEC §2.1 "한 번 붙인 ID는 바꾸지 않는다".
- 2026-10-19 · 자동화 행의 구조화 내용은 원본을 고치지 않고 `tools/overrides/<ID>.yaml`로 덧씌운다. · 재변환해도 구조화가 유지되도록.
- 2026-10-19 · `required_observations`는 SPEC 모양(`[{key,type}]` 목록)을 유지하고 원문은 `required_observations_prose`에 둔다. `applies_when`은 SPEC 모양대로 `prose`를 안에 둔다. · SPEC §3 예시 형태 보존.
- 2026-10-19 · `policy_params`는 구조화된 행만 채우고, 원문(`policy_to_decide`, `enterprise_decisions`)은 `policy_prose`에 보존한다.
- 2026-10-19 · 정책 `scope.include`가 비어 있지 않으면 include에 맞지 않는 점검도 `POLICY_SCOPE`로 미선정한다. · SPEC은 exclude만 명시. 사람 확인("진행").
- 2026-10-19 · REQ-01 관측 키는 SPEC 예시대로 `direct_route_status`, `gateway_route_status` 2개. 원문 관측 3개는 prose에 보존.
- 2026-10-19 · REQ-01 `executor: direct_route`. 실제 실행기는 5단계에서 만든다. 이번에는 `guard.toml`의 `[executors]`에서 `direct_route = "fake"`로 연결한다.
- 2026-10-19 · `rule_table`의 평면 `pass_when_all`/`fail_when_any`는 security 축이다. 다른 축은 `axes.<축>`에 같은 형식으로 둔다. 규칙이 없는 축은 UNKNOWN. · INV-18.
- 2026-10-19 · INV-11의 PASS 관문(정상 대조 성공·모든 챌린지 시도·필수 관측 수집)은 판정 플러그인과 별개로 judge 코어가 강제한다. FAIL 조건이 수집된 관측으로 성립하면 관문과 무관하게 FAIL을 유지한다. · INV-04.
- 2026-10-19 · 정책 예외의 만료 여부는 판정 시각(`--as-of`, 기본값 현재 시각)을 judge 코어가 받아 확인한다. 판정 플러그인은 시계에 접근하지 않는다. · INV-14.
- 2026-10-19 · FakeExecutor 증거와 예시 env/policy는 `data_origin: EXAMPLE`이다. · INV-02.
- 2026-10-19 · HTML은 jinja2 없이 표준 라이브러리로 렌더링한다. ULID도 표준 라이브러리로 직접 구현한다. · 의존성 추가 금지.
- 2026-10-19 · update 모드(2개 환경)의 적용 판단은 환경별로 계산해 더 넓은 쪽(APPLICABLE > CONFLICT > UNDETERMINED > NOT_APPLICABLE)을 쓴다. · 시험을 빼지 않는 방향.
- 2026-10-19 · 변경 사실이 관련 점검으로 가리키면 NOT_APPLICABLE이어도 선정한다(정책 scope 밖은 제외). 변경이 점검을 빼는 경우는 없다. · INV-05 "추가만".
- 2026-10-19 · CONFLICT도 inventory_complete에서는 "판단받지 않음"으로 본다. · INV-13을 보수적으로 적용.
- 2026-10-19 · 정책은 팩 Ref(id·revision·sha256)가 정확히 같아야 select에 쓸 수 있다. policy.yaml은 id·revision만 쓰고 로더가 해시를 채운다.
- 2026-10-19 · NOT_RUN·ABORTED EVD의 data_origin은 min(PLAN의 data_origin, DECLARED). 관측은 전부 `collected: false`, 챌린지는 `attempted: false`. · INV-02, INV-15.
- 2026-10-19 · run은 CheckDef.required_observations 키·challenge_steps만 EVD에 남기고, 실행기가 빠뜨린 것은 미수집·미시도로 채운다.
- 2026-10-19 · 같은 PLAN·점검·side를 다시 run/judge하면 같은 EVD/RES id의 새 revision을 만든다. 보고서는 최신 revision을 Ref로 고정한다.
- 2026-10-19 · 시연용 FakeAdapter(엔진 미기동)를 `adapters/fake.py`에 둔다. 실행기에 넘길 어댑터 자리를 채우는 용도.

## 열린 질문
- (5단계 전) 시험용 Gateway 구성, 고정 모델, 실제 env.yaml/policy.yaml 값 — PROGRESS.md 참조.
- D2 `impact` 값(HIGH/MEDIUM/LOW) — 지금은 전부 null.
