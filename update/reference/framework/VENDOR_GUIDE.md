# BYUL에서 프레임워크 자료 읽기

현재 기준은 [프레임워크8a0c7fe](https://github.com/mg-princess/serving-security-framework/tree/8a0c7fe42446ff0d3a1f58e2a18cf3069d5c714e)다. `UPSTREAM.json`의52개 파일은 해당 판본의 원bytes와 SHA256을 유지한다. 기준 문서·결과 기록 명세·개발팀 인계·선정 엔진 설명과 아래 검사의 실행 의존성을 수록했다.

## 자료의 역할

- [공통 기준과 개발팀 인계](개발전달/개발팀_인계명세.md)에서 입력·출력·기존 객체 대응·완료 조건을 읽는다. 추가 결과 필드·저장/API 구조는 개발팀 설계 제안이다.
- [결과 기록 최소 명세](개발전달/결과기록_최소명세_초안.md), [필드사전](개발전달/결과기록_필드사전_초안.json), [합성 예시](개발전달/examples/결과기록_합성예시_초안.json)의 공통 의미와 미구현 제안 상태를 구분한다. 합성 예시는 제품 importer의 입력 형식으로 채택하지 않았다.
- 기존 importer는 현재 매핑하는 CHK 원문 필드를 생성 정의에 반영한다. 필수9결과·full branch·요구별 기여 정보는 수록 정본/문서에 보존한다. 이를 범위별 결과 저장·판정·공용 API에 연결하는 작업은 개발팀 후속이다.
- [선택하지 않은 출처·매체 링크](UPSTREAM_LINKS.md)는 수록 문서 안의 상대 참조를 정확한8a 원문으로 안내한다. 사진·PDF·전체 출처 자료는 이 목록에서 읽는다. 수록 원문 자체는 수정하지 않았다.
- [history/0ec5fb5](history/0ec5fb5fa5633b0817468b27684690a66fc7b4f5/)의 검수기록·validation_report·checklist는 당시 자료다. 기존 날짜 있는 기준선과 CPU 원기록도 원bytes·판정·출처를 유지한다.
- `ID_MAPPING.json`과 두20261019 검토 문서는 BYUL이 보관한 이전 기록이다. 현행 원문 목록과 구분한다.

## 수록 검사 실행

BYUL `update/`에서 Python3.12 고정 의존성을 준비한 뒤 실행한다.

```sh
uv sync --frozen
uv run --frozen python reference/framework/verify_framework.py
uv run --frozen python reference/framework/개발전달/verify_assessment_record_examples.py
uv run --frozen python -m unittest discover -s reference/framework/tests -v
uv run --frozen python reference/framework/select_checklist.py --self-test
node reference/framework/verify_explorer.cjs
uv run --frozen python reference/framework/engine_sglang_inventory.py --validate-only
uv run --frozen python reference/framework/engine_sglang_basis_delta.py --historical-checklist reference/framework/history/0ec5fb5fa5633b0817468b27684690a66fc7b4f5/data/checklist.json --check
```

정본·빈양식·문서 관계, 지정 합성 예시·반례, Q 추천, HTML의 데이터/함수, 저장된 정적 목록과 구→현행 기준 차이를 검사한다. Node.js는 HTML 검사에 사용한다. 고정 엔진 소스의 재추출 명령은 별도 source checkout이 필요하다. 실제 활성 API·엔진 동작·GPU/AWS 시험은 해당 환경을 선정한 뒤 수행한다.

## BYUL 생성자료

[2026-10-27 동기화 범위](../../docs/FRAMEWORK_SYNC_20261027.md)를 따른다. PACK27 보존본은 이번 작업에서 BYUL main7e345759의80개 파일로 새로 만들었다. PACK28 보존본은 동일한 main importer로 생성한80개 파일을 담는다. 두 보존본의 생성 출처·개별 bytes/hash는 `tests/fixtures/pack27-checks/SNAPSHOT.json`과 `pack28-checks/SNAPSHOT.json`에 기록했다. 이 보존본은 팩 정의/선정 자료이며 실행 환경·제품 전체를 복원하는 bundle은 별도로 확보한다.
