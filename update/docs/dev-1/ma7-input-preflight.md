## 2026-11-15 기준 fresh /28·worker /2 로컬 준비

후속8cc795231d5a3eba1e42f3c462f60113e337800b70542bc2b6dead6eebea9123 소스로 새 기준 /28 준비물과 동일 세션 worker /2 준비물을 /home/youngjo/byul-ma7-fresh-worker-inputs-20261115에 만들었다. 정확 SDK·키 자산 SHA/owner/0600·세션·독립 source 지문을 검토했다. 공개 요약은 작업 폴더 MA7_FRESH_WORKER_PREPARATION_20261115.json이며 비밀키는 포함하지 않는다.

이 파일들은 현재 실제 ENV/target·Pod/CID/세대·ARM2와 미결합이다. worker /2는 읽기 전용 수신/응답 검토이고 새로운 장애 제어점/공통 변환·전체 TP2 수락/등록을 열지 않는다. 후보의 같은 후속 관측·현재 V1 모델 기동/GPU 자격과 실제 사전 점검은 미완료다. 과거 GPU 자료·실측/승인 예외를 새 조건으로 이월하지 않는다. M-B4 보관·새 비용/다운로드 사전 승인 경계는 유지한다.

# M-A7 실제 파일·실행 결합 확인

팩36의 실행 준비값 분리에 맞춰 입력 파일과 결합을 확인한다. 현재 준비물은 `ma7-local-materialization-20261113.json`의 v8이며 prepared18개·독립 세션18개·사례10파일/2,078개·REQ-17 분할 그룹2개다. 이전 묶음과 실제 팩34 SUP-08 EVD/RES/RPT는 보존한다.

`tools/mvp_a_input_preflight.py`는 현재 팩과 M-A27개·양쪽54행을 기준으로 다음을 확인한다.

- 입력 색인의 팩 Ref·점검/쪽·중복과 파일 bytes SHA, 정규 파일·크기·읽는 동안 파일 신원.
- 봉인된 ENV의 해시·판본 표의 해당 쪽·GPU 기본 사실, 정책의 팩·선정 범위·필수 값/타입.
- 쪽별 설정과 `[check_config.<점검>]`을 제품 실행과 같은 `for_check()`로 병합한 뒤 현재 `guard-run-binding/1`·`/2`를 계산한다. `[run_binding.<점검>]` 누락과 정책에 남은 실행 결합 키는 기존 관문에서 거부한다.
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

같은 실행기를 쓰는 점검의 다른 target/prepared는 RB2-S의 `[check_config.<점검>]`에 선언한다. 사전 점검은 공통 설정→쪽별 설정→점검별 설정 순으로 병합하며, 실제로 선택된 파일이 없거나 ENV Ref가 다르면 파일 결합 유효로 표시하지 않는다. E 정책 키 네 개의 이동은 이 도구의 범위가 아니다.

## M-A3 준비물의 J01 연결

`tools/mvp_a_rank_input_plan.py`는 외부에서 전달한 M-A3 준비 보고서의 여섯 manifest/settings/14자산 bytes·세션·MVP 이미지와 미실행 데이터 디렉터리를 대조하고 새 J01 계획 사본을 만든다. 기존69개 점검 행·M-B 입력·정책값·A18개 준비물·사례10파일/2,078개를 바꾸지 않는다. 관측 기록이 있거나 이미 결합/완료한 입력은 미입력으로 되돌리지 않는다.

현재 연결은 [준비물 연결 보고서](ma7-rank-input-link-20261115.json)에 있다. 기준·후보 각각 delay/death/exception의 독립 세션3개를 포함한다. 준비 자산 파일 검증과 실제 런타임 자격을 구분하고, ENV/target/ARM2 제품 binding/공통 입력/실제 결과는 null과 미완료 이유로 남긴다. 과거 ARM1 원자료를 사후 제품 입력으로 만들지 않는다. M-B4 관측 개발·새 GPU 기동/비용 승인을 뜻하지 않는다.

실제 현재 환경이 확보되면 M-A3 쪽 준비 도구로 ARM 전에 제품 입력을 결합한 후, 실제 새 원 기록을 미등록 공통 부분 입력으로 변환한다. 전체 TP2 그룹/탐지/상위 전달 기한/복구 수락·등록은 별도 미완료다. 이 도구는 네트워크·엔진·정책 확정을 수행하지 않는다.


## LIF-08 제품 파일 입력 조립 (2026-11-15 후속)

`tools/mvp_rank_product_inputs.py`는 현재 sealed ENV·target·prepared manifest·공통 rank-fault 원 파일·기존 LIF-08 정책의 다섯 파일을 요구한다. 해당 쪽의 정확 이미지/판본 표·TP2·ENV Ref·정규 target/prepared 해시·세션·정책 범위/필수 값/실행 결합 소유 키를 검사한 뒤 원 bytes 그대로 복사하고 `guard.toml`을 만든다. 정책의 상태·값·확정자·유효 기간을 채우거나 바꾸지 않는다. `/1`·`/4`의 지원/완전성 공백은 보존한다.

```powershell
uv run python -m tools.mvp_rank_product_inputs --side <baseline 또는 candidate> --environment <현재-ENV.json> --target <현재-target.json> --prepared <사전결합-prepared-manifest.json> --common-input <새-공통입력.json> --policy <기존-LIF08-정책.yaml> --output <새-출력폴더>
```

실행기는 `rank_failure:common-input`이다. 현재 main에 그 구현이 없으면 `implementation_available=false`, `run_binding_preflight_valid=false`로 기록한다. #276의 파일 소비 구현을 포함한 소스에서만 실행 결합 사전 대조를 할 수 있다. 이 값도 실환경/엔진 자격은 아니며 `live_target_verified`, `input_preflight_passed`, `ready_for_execution`, `engine_executed`는 항상 false다. 원 기록 수집과 별개인 파일 소비는 최대 DECLARED, EXAMPLE은 그대로 남긴다. 제품 경로에서도 부분 관측은 UNKNOWN이며 전체 TP2 수락/등록을 대체하지 않는다.

[현재 후속 계획](ma7-rank-product-link-20261115.json)에 미결합 준비물6개와 필요한 파일 역할을 연결했다. 실제 ENV/target·ARM2 원 기록·LIF-08 정책 묶음이 없어 실제 입력 조립은 미완료다. 과거 SUP-08 파일의 두 쪽 bytes 결합은 재확인했지만 삭제한 Pod의 과거 구성이고 새 V1 실행 입력이 아니다. 기존 M-A27개54쪽 색인에서 SUP-08 두 쪽만 파일 결합이 유효하고 나머지52쪽은 파일 부재다. LIF-08 두 쪽은 이 기존 M-A 색인의 범위 밖이므로 별도 미완료로 관리한다.

#277의 결정14 변경에 따라 새 실행은 두 쪽 V1을 선택한다. 과거 기준 V0 자료/예외는 이력으로 보존한다. 같은 SDK bytes라도 V1 대상 함수/정확 핀·기동/모드·현재 owner 자격은 별도 확인해야 한다. 새 GPU/클라우드/비용·대용량 다운로드·M-B4 재개는 하지 않았다.
