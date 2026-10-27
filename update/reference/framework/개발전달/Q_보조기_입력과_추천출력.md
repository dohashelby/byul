# Q 보조기 입력과 추천 출력

`select_checklist.py`는 기존 Q 28개의 적용 여부를 추천한다. CHK 77개·112개 조건 묶음의 실행 목록과 시험·판정은 별도다. 이 문서의 작은 입력/출력 계약은 Q 보조기에 적용하며 BYUL의 공통 결과 형식을 확장하지 않는다.

## 환경 상태와 기능 값

- `profile_status`: 실제확인 입력은 `observed` 또는 `실제확인`을 사용한다. `planned`와 `계획값`은 계획이다. 누락·null·다른 문자열·숫자·bool·객체는 실제확인으로 채택하지 않는다. 상태 문자열을 썼다는 사실의 진위는 담당자가 확인한다.
- 기능 값은 JSON `true`, `false`, `null`이다. `1`, `0`, `"true"`, `"false"`를 자동 변환하지 않는다. 계획·미확인 환경의 조건부 값은 미정으로 남긴다. 공통 보호 요구는 유지한다.
- `prefix_cache_or_shared_cache`는 prefix cache 또는 그 밖의 공유 cache 전체를 묶은 값이다. 현재 따로 받는 구성 필드는 `prefix_cache`다. 이 값이 true이고 집계가 false이면 모순이다. 구성값의 타입 오류도 미정이다. 구성값이 null이더라도 **집계 전체**의 비사용·비노출을 확인한 유효 claim이 있으면 비적용 후보로 남길 수 있다. prefix cache만 확인한 근거를 공유 cache 전체의 근거로 사용하지 않는다.
- 나머지 `alternate_sources_or_formats_or_derived_cache`, `remote_acquisition_or_retry`, `shared_or_remote_weights`도 각각 이름에 묶인 범위를 모두 확인한 뒤 false로 둔다. 이 보조기는 구성별 값을 임의로 합성하거나 새 수집 필드를 추정하지 않는다.
- `nodes`, `tp_groups`는 지원하는 vLLM 참조 역할의 양의 정수 또는 null이다. 팀 양식의 1은 계획값이다. 다른 엔진의 역할이 없다는 사실과 역할·값을 모른다는 상태를 구분해 설치 자료로 확인한다. 미제공/null을 0이나 1로 채우지 않는다. 엔진별 새 topology 형식은 실제 지원 범위를 정한 뒤 설계한다.

## 비사용 claim

false를 비적용 후보로 추천하려면 `feature_absence_evidence.<기능키>`에 다음을 기록한다.

|필드|지원 형식·의미|
|---|---|
|feature|검토한 기능키와 동일. OR 집계이면 집계 범위 전체|
|status|`reviewed`|
|reviewer|비어 있지 않은 검토자 식별 문자열|
|reviewed_at|`2026-10-26T10:00:00+09:00` 또는 `2026-10-26T01:00:00Z`처럼 시간대가 있는 RFC3339 시각. 소수초 1~6자리 지원|
|disabled / nonexposed|둘 다 JSON true|
|references|실제 유효 설정·등록/노출 목록·소비 경로를 확인한 자료 위치의 비어 있지 않은 문자열 목록|
|target|현재 입력과 같은 engine/version/source_commit/image_digest 및 environment_snapshot_sha256|

이미지 식별자는 `sha256:` 뒤 64자리 소문자 16진수 또는 `registry/repository@sha256:<digest>`를 지원한다. 태그 이름·임의 문구는 digest로 수용하지 않는다. 시각·digest 형식 확인은 자료의 진위, 미래 시각의 타당성, 현재 유효성, 서명이나 설치 일치를 증명하지 않는다. 모든 환경에 공통 유효기간을 추가하지 않으며 실제 변경과 조직의 검토 기준에 따라 판단한다.

snapshot hash는 **입력 환경 객체에서 최상위 feature_absence_evidence 전체만 뺀 JSON**을 키 정렬·UTF-8·공백 없이 직렬화한 SHA256이다. claim 자신을 제외해 순환을 피한다. 다른 입력·runtime_evidence·환경 ID·정책 참조·설정이 바뀌면 이전 claim의 연결은 맞지 않는다. 외부 참조 파일의 내용이 입력 밖에서 바뀌는 경우는 이 hash로 탐지하지 못하므로 담당자가 references의 실제 내용을 재확인한다.

이미 검토한 입력에 새 claim을 작성할 때만 다음 함수로 hash를 구한다. 기존 claim에 hash를 자동 보충하지 않는다.

```python
from select_checklist import environment_snapshot_sha256
snapshot_hash = environment_snapshot_sha256(environment)
```

구 claim에 hash가 없거나 현재 입력과 다르면 원본은 유지하고 미정으로 남긴다. 현재 비적용 후보의 자동 추천은 고정 vLLM 0.31.0 commit에 한정한다. 다른 엔진은 실제 구현·소비 매핑과 근거를 검토한 뒤 별도 지원한다.

R10/R14는 true이면 사용에 따른 추가 검토, null이면 사용 여부 미정, false이면 비사용 claim 심사로 안내한다. 비사용이 확인돼도 그 기능을 사용하는 환경의 평가 지원이 완료되는 것은 아니다.

## 추천 출력의 연결

기존 출력에 `selection_context`를 붙인다.

|필드|내용|
|---|---|
|environment_snapshot_sha256|위 규칙에 따른 입력 환경 snapshot hash|
|environment_file_bytes_sha256|CLI가 읽은 입력 파일 bytes SHA256. 함수 직접 호출은 기본 null이며, 호출자가 원본 파일의 계산값을 명시할 수 있음|
|catalog_canonical_sha256|추천에 사용한 전체 checklist JSON의 키 정렬·UTF-8·공백 없는 canonical hash. 파일 bytes hash와 구분|
|environment_role|명시한 `current`, `candidate`, `test` 또는 미제공 null|

`profile_id`는 입력 id를 그대로 사용한다. 없는 id·판본·환경 역할을 만들어 넣지 않는다. 추천 출력과 원 환경 파일을 함께 보관하면 입력 bytes·snapshot·기준 내용·역할을 대조할 수 있다. 신규 저장소·공통 API·자동 승인 기능을 만들지 않는다.

```sh
python3 -B select_checklist.py data/environment_template.json --environment-role test > q-recommendation.json
```

출력 보관 위치는 사용자가 정한다. 함수 직접 호출은 `select(catalog, environment, environment_role=None, environment_file_bytes_sha256=None)`이며 원본 파일 해시는 호출자가 제공한 값이다. 입력과 기준은 JSON 객체여야 하고, 파일의 중복 key·NaN/Infinity·수치 overflow·잘못된 인코딩은 거부한다. 어느 경로에서도 원 입력·claim을 변경하지 않는다.

## 재현

```sh
python3 -B select_checklist.py --self-test
python3 -B -m unittest discover -s tests -p 'test_q_selector.py'
```

원 23개 입력은 tests/fixtures/q_selector_legacy_23.json에 내용 그대로 보존했다. 새 hash가 없는 구 claim은 미정으로 처리한다. 별도 합성 claim으로 유효한 후보·설정 변경·OR 전체 검토·시각/digest·현재/후보/시험 역할과 원본 bytes 연결을 검증한다. 후속 R14 3입력도 별도 검증한다. 모든 보안 판정은 미확인, 실행 상태는 미실행이다.
