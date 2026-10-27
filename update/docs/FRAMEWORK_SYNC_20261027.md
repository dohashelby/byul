# 프레임워크 확정본 동기화 — 2026-10-27

[프레임워크 8a0c7fe](https://github.com/mg-princess/serving-security-framework/commit/8a0c7fe42446ff0d3a1f58e2a18cf3069d5c714e)를 BYUL main 7e345759에 반영하고 기존 생성기로 PACK-BASELINE 28/DRAFT를 만들었다.

## 반영 범위

- 기존 핵심 원문 13개, 결과 기록·개발팀 인계 6개, vLLM 진입경로 문서 1개를 원본 그대로 수록했다. [UPSTREAM.json](../reference/framework/UPSTREAM.json)에20개 파일의 고정 판본·해시를 기록했다.
- CHK 77개·내부 ID·선정 ID 목록을 유지했다. 생성된 점검 정의 38개의 내용과 revision을 갱신하고 39개는 바이트를 보존했다. 예제 정책 5개·생성 정책 설명의 팩 참조 정보·현황표를 함께 갱신했다.
- importer·CheckDef·실행기·판정기·overrides·정책/관측 선언과 runtime·platform·contracts 소스는 유지했다.
- 이번 작업에서 main 7e의 PACK27을 80개 파일 source.zip으로 새로 보존하고 생성 시점·출처·파일별 해시를 기록했다. 과거 단계 검사와 golden은 이 보존본에서 원래 기대값으로 확인한다. 현재 PACK28은 생성 폴더·manifest 자체를 검사한다.

## 원문과 개발팀 후속

[개발팀 인계 명세](../reference/framework/개발전달/개발팀_인계명세.md), [결과 기록 최소 명세](../reference/framework/개발전달/결과기록_최소명세_초안.md), [필드사전](../reference/framework/개발전달/결과기록_필드사전_초안.json), [합성 예시](../reference/framework/개발전달/examples/결과기록_합성예시_초안.json)에서 기준과 후속 구현을 확인한다. 각 점검의 기본 보호 조건·환경별 112개 시험 분기·적용 시 남기는 9개 세부 판정은 정본에 보존했다. 범위별 결과의 저장·판정·공용 계약/API·화면 연결은 개발팀 후속이다.

수록 원문의 전체 상대 참조와 검사 명령은 [정확한 upstream 원문](https://github.com/mg-princess/serving-security-framework/tree/8a0c7fe42446ff0d3a1f58e2a18cf3069d5c714e)에서 사용한다. 공유용 설명·HTML 지도·상위 검사 패키지·SGLang 전체 적용표/정적 목록은 이 PR에 복제하지 않았다. SGLang 근거는 [상위 적용 문서](https://github.com/mg-princess/serving-security-framework/blob/8a0c7fe42446ff0d3a1f58e2a18cf3069d5c714e/04_%EC%97%94%EC%A7%84%EA%B3%BC_%EB%B2%84%EC%A0%84%EB%B3%84_%EC%B0%A8%EC%9D%B4.md#sglang-0522-적용성-검토)를 따른다. 이전 검수·validation·체크리스트의 고정 링크/해시는 UPSTREAM의 historical_references에 남겼다. 기존 날짜 있는 기준선과 PACK21·과거 CPU·golden 원본의 바이트는 유지한다.

## 검증

새 최소 변경 사본의 Python3.12.13에서 동기화·생성기·팩 보존·선정·기대 출력 관련 검사 54개가 통과했다(6.94초). Q 보조기의 내장 회귀 12개와 현황표 재생성 확인도 통과했다. 원문 20개는 고정 8a 판본과 바이트·해시가 같고, 과거 참조는 원문 Git 판본으로 대조했다.

과거 검사 입력의 절차 3문구와 판본 기대만 현행에 맞췄다. 원 golden을 보존하고 현재 기대 출력은 절차 문구 33곳만 대응한 뒤 나머지 전체 내용을 비교한다. 실제 엔진/GPU/AWS 시험은 미실행이다.
