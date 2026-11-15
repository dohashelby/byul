# M-A7 실제 파일·실행 결합 확인

팩36의 실행 준비값 분리에 맞춰 입력 파일과 결합을 확인한다. 현재 준비물은 `ma7-local-materialization-20261113.json`의 v8이며 prepared18개·독립 세션18개·사례10파일/2,078개·REQ-17 분할 그룹2개다. 이전 묶음과 실제 팩34 SUP-08 EVD/RES/RPT는 보존한다.

`tools/mvp_a_input_preflight.py`는 현재 팩과 M-A27개·양쪽54행을 기준으로 다음을 확인한다.

- 입력 색인의 팩 Ref·점검/쪽·중복과 파일 bytes SHA, 정규 파일·크기·읽는 동안 파일 신원.
- 봉인된 ENV의 해시·판본 표의 해당 쪽·GPU 기본 사실, 정책의 팩·선정 범위·필수 값/타입.
- 쪽별 설정 병합과 현재 `guard-run-binding/1`·`/2` 계산. `[run_binding.<점검>]` 누락과 정책에 남은 실행 결합 키는 기존 관문에서 거부한다.
- target의 ENV Ref가 있으면 같은 ENV와 대조하고, 읽은 target/prepared/manifest·B2 결합 파일이 계산 중 바뀌지 않았는지 확인한다.

`file_binding_valid`는 이 범위의 파일·형식·결합 확인이다. 실제 엔진/수집기 자격·현재 Pod·소유 범위·정상 요청은 별도다. 도구는 네트워크·kubectl·엔진·`guard run`을 실행하지 않고 모든 행의 `live_target_verified`, `input_preflight_passed`, `ready_for_execution`을 false, `actual_results`를 null로 유지한다. EXAMPLE ENV는 별도 차단 사유를 남긴다. 기업 기준값과 빈 입력을 채우지 않는다.

## 입력 색인

`schema: guard-ma7-input-sources/1`, 현행 팩의 정확 `pack_ref`, `inputs` 배열이다. 각 행은 `check_id`, `side`(baseline/candidate), `environment`, `policy`, `configuration`만 받는다. 파일 참조 세 개는 각각 `path`와 파일 bytes `sha256`이다. 지정하지 않은 점검/쪽도 보고서에 남기며 `ACTUAL_INPUT_FILES_MISSING`으로 표시한다. 한 쪽 오류가 다른 쪽이나 누락 행을 지우지 않는다.

```text
uv run python -m tools.mvp_a_input_preflight --index <실제-파일-색인.json> --output <새-보고서.json>
```

정책 scope의 빈 include·와일드카드·exclude는 기존 선정 함수의 의미를 그대로 따른다. 결과 파일은 기존 파일을 덮어쓰지 않는다. 실제 실행 직전에는 제품이 파일과 실행 결합을 다시 계산·대조한다.

## 2026-11-15 실제 파일 결과

- 팩36 로컬 SUP-08 모델/코드/기동 감사: 60.173초, 오류0개. 보고서는 `ma8-local-preflight-pack36-20261115.json`이다.
- 기존 실제 SUP-08 ENV/target 두 쪽의 팩36 계약 조립과 파일 결합 확인: 2쪽 유효. 전체 결과는 `ma7-file-preflight-20261115.json`이다.
- 이 두 target은 삭제한 2026-11-14 Pod의 과거 입력이다. 현재 실환경 자격·새 실행으로 올리지 않는다. 나머지 M-A52쪽은 실제 입력이 없으며, 전체69개 기준으로는 SUP-08 외136쪽의 수집·결합이 남는다.
- 새 관측기 등록·M-B4 재개·새 GPU/클라우드 실행은 하지 않았다. 현재 source/자격과 실제 ENV/target을 마련한 뒤 실행 직전 사전 점검을 다시 해야 한다.

같은 실행기를 쓰는 점검이 다른 target/prepared를 요구하면 현재 제품은 별도 실행 설정으로 조립해야 한다. 점검별 실행기 설정 덮어쓰기(RB2-S)나 E 정책 키 네 개의 이동은 이번 도구가 구현하지 않는다.

## M-A3 준비물의 J01 연결

`tools/mvp_a_rank_input_plan.py`는 외부에서 전달한 M-A3 준비 보고서의 여섯 manifest/settings/14자산 bytes·세션·MVP 이미지와 미실행 데이터 디렉터리를 대조하고 새 J01 계획 사본을 만든다. 기존69개 점검 행·M-B 입력·정책값·A18개 준비물·사례10파일/2,078개를 바꾸지 않는다. 관측 기록이 있거나 이미 결합/완료한 입력은 미입력으로 되돌리지 않는다.

현재 연결은 [준비물 연결 보고서](ma7-rank-input-link-20261115.json)에 있다. 기준·후보 각각 delay/death/exception의 독립 세션3개를 포함한다. 준비 자산 파일 검증과 실제 런타임 자격을 구분하고, ENV/target/ARM2 제품 binding/공통 입력/실제 결과는 null과 미완료 이유로 남긴다. 과거 ARM1 원자료를 사후 제품 입력으로 만들지 않는다. M-B4 관측 개발·새 GPU 기동/비용 승인을 뜻하지 않는다.

실제 현재 환경이 확보되면 M-A3 쪽 준비 도구로 ARM 전에 제품 입력을 결합한 후, 실제 새 원 기록을 미등록 공통 부분 입력으로 변환한다. 전체 TP2 그룹/탐지/상위 전달 기한/복구 수락·등록은 별도 미완료다. 이 도구는 네트워크·엔진·정책 확정을 수행하지 않는다.
