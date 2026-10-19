# serving-guard 작업 규칙

이 폴더는 "서빙엔진 보안 검증 솔루션"을 새로 만드는 작업 디렉토리다. 세션을 시작하면 이 파일이 자동으로 읽힌다.

## 정본과 참고
- **`SPEC.md`가 정본이다.** 객체 7종, 불변식 INV-01~18, 파이프라인, 플러그인 인터페이스, 개발 순서(§8)가 모두 여기 있다.
- `reference/framework/` : 점검 77행의 원천 데이터(`data/checklist.json`)와 환경·결과 템플릿. **필요할 때만 찾아본다.**
  - `03_체크리스트와_보완가이드.md`는 37만 자다. **통째로 읽지 않는다.** 필요한 행만 grep으로 찾는다.
  - 점검 데이터는 `data/checklist.json`을 코드로 읽는다(문서를 파싱하지 않는다).
- `reference/bob-design/` : 이전 프로젝트(BoBProject)의 설계 문서. **구조 참고용이다.** 여기 있는 서명 승인, 감사 outbox, CAS, 객체 20여 종, 웹 UI를 만들지 않는다(SPEC §9).
- SPEC과 참고 문서가 충돌하면 SPEC을 따르고 `DECISIONS.md`에 한 줄 적는다.

## 기술 기준
- Python 3.11, `uv` 사용, 패키지명 `guard`, `src/guard/` 레이아웃(SPEC §10).
- pydantic v2, typer, pyyaml, httpx, pytest. 그 외 의존성은 추가 전에 사람에게 묻는다.
- 모든 객체는 append-only. 해시 규칙은 SPEC §2.3.

## 작업 방식
1. SPEC §8 순서대로, **한 단계씩** 만들고 테스트가 통과한 뒤 다음 단계로 간다.
2. 불변식은 구현보다 **테스트를 먼저** 쓴다(`tests/invariants/test_inv_XX.py`). 구현이 불변식을 어기면 구현을 고친다.
3. 코어(`core/`, `models/`, `store/`)에 엔진 이름(`vllm` 등)을 쓰지 않는다. 엔진별 코드는 `adapters/`에만.
4. 실행하지 않은 시험의 결과를 만들거나 예시 값을 `MEASURED`로 표시하지 않는다(INV-02).
5. 전체 테스트는 1분 안에 끝나야 한다. 실제 엔진이 필요한 테스트는 `@pytest.mark.engine`으로 분리하고 기본 실행에서 뺀다.
6. 막히거나 SPEC에 없는 결정이 필요하면 **추측하지 말고 멈추고 질문한다.** 질문은 `DECISIONS.md`의 "열린 질문"에도 적는다.
7. 단계가 끝날 때마다 `PROGRESS.md`에 "한 일 / 테스트 결과 / 다음 단계 / 막힌 것"을 짧게 갱신한다.

## 하지 않는 것 (SPEC §9)
전자서명·registry, 감사 outbox, 웹 UI·로그인, 게이트웨이 구현, LLM 판단 보조, vLLM 외 엔진 어댑터 구현, 77행 전부 자동화.
