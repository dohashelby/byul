# Pack 34 MVP 입력 연결

새 정책은 서버가 PACK-BASELINE@34를 지정한다. 판본을 바꾸는 요청은 거부하고,
과거 정책 ID의 기존 Pack과 저장된 판본·결과는 보존한다. 새 입력을 위한 Pack 선택 UI는 없다.
정책 저장은 DRAFT이며 실제 Gateway·엔진 설정이나 시험을 실행하지 않는다.

## 입력 경계

- `GET /api/inputs/catalog`: 실제 Pack의 키·타입·필수 여부와 기존 엔진 지원표를 읽는다.
- `POST /api/inputs/policy-preview`: 초안의 연결값·충돌·부족한 값·미연결 답변을 반환한다. 저장·실행하지 않는다.
- `POST /api/policies`: 같은 변환을 실행한 뒤 새 판본으로 저장한다. 누락은 보존하되 형식 오류·JSON 충돌은 거부한다.
- 카드값은 `framework_values`에 보존하고 실행값은 `verification.values[check_id][key]`에 넣는다.
  저장 후 카드 편집은 이전 자동 연결값만 갱신한다. 수동 JSON 변경과 충돌하면 둘 다 보존하고 저장을 막는다.
- 유지 선택은 같은 정책 ID의 마지막 CONFIRMED 판본에서 실제 값을 가져온다. 원본 판본·해시를 note에 남긴다.
  이전 값이 없으면 미정이며, 확인 필요·null·측정 후 결정 값을 False·빈 목록으로 바꾸지 않는다.
- 해시·세션·prepared 등 시스템 생성값은 새 고객 입력란으로 만들지 않는다. 준비 공백에만 표시한다.

## 판본별 표시

`update/src/guard/adapters/assets/engine_profiles.json`의 기존 지원표를 읽는다.
버전 문자열 분기를 프론트엔드에 두지 않는다. `profiles` 행에 명시한
`input_fields_not_applicable: ["조건키.필드키"]`만 해당 판본에서 숨긴다.
이는 시험 지원 정보이지 실제 엔진 기능 부재의 증명이 아니다.
features 목록에 없거나 판본 행이 없는 경우에는 공통 필드를 유지하고 미확인으로 표시한다.
정책 카드와 공통 정책값은 판본에 따라 삭제하지 않는다. 숨겨진 환경 답변도 원본에서 삭제하지 않는다.

현재 지원표에는 판본별 필드의 명시 비적용 목록이 없다. 그러므로 실제 목록이 검토되기 전에는
두 MVP 판본의 필드가 같을 수 있다. 서로 다르게 보이게 하려고 비적용 규칙을 만들어 넣지 않는다.
지원표의 향후 M-A6 자료에 명시 비적용이 추가되면 같은 UI가 적용한다.
기준·후보 환경은 별도 저장하므로 한쪽에서 필요한 필드는 그 환경에서 입력한다.

## 외부 준비 항목

- main의 실제 Pack은 현재 33이다. 이 PR은 Pack·센서·판정 정의를 변경하지 않는다.
  실제 Pack 34가 등록되기 전에는 연결을 실행하지 않고 준비 미완료를 표시한다.
- Pack 로딩에는 기존 update 패키지가 필요하다. `uv run --with ../../update ...`로 실행한다.
  패키지가 없으면 상태를 미확인으로 표시한다. 새 플랫폼 의존성을 추가하지 않았다.
- 11-08 §6-1의 REQ-13·REQ-17·OBS-05 확정값은 실제 Pack에 존재하는 키만 재사용한다.
  명시 입력이나 확인 필요 답변은 덮어쓰지 않는다.
- REQ-03의 세부 작업 범위와 LIF-13은 현재 정본 키로 정확히 표현된 정책 파일이 필요하다.
  `general_allowed_kinds`를 임의로 확대하거나 센서 해시를 기업 정책으로 만들어 넣지 않는다.
- `BYUL_MVP_POLICY_FILE`에 기존 BYUL 정책 문서(JSON/YAML)를 지정하면 위 5개 점검의
  `verification.values`를 재사용한다. Pack 34 참조와 계약 형식이 맞는 비예시 자료만 읽으며,
  실제 Pack에 존재하는 키만 연결한다. LIF-13의 센서 해시는 다른 환경에 재사용하지 않는다.
  기준값 재사용은 사용자 승인 상태나 문서의 EXAMPLE 출처를 승격하지 않는다.
- SUP-08의 `approved_code_revisions`는 실제 Pack 정의에 존재할 때만 편집·연결한다.

## 검증

API는 Linux CPython 3.12에서 `uv run --with ../../update --with ../../runtime pytest`로 확인한다.
프론트엔드는 `npm test`, `npm run build`를 사용한다.
`test_mvp_inputs.py`의 Pack 34는 임시 폴더에 만든 합성 계약으로,
정본 Pack 34·GPU 실측·공식 판정을 의미하지 않는다. 저장된 문서를 기존
`import_byul_inputs.convert_documents`에 전달해 최종 guard Policy 값까지 확인한다.
