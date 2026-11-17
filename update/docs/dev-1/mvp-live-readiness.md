# 69개 실제 실행 준비

`tools.mvp_live_readiness`는 현행 팩의 update 대상 전체를 기준·후보로 펼쳐 입력 파일과 실행기 등록을 확인한다. 기존 `mvp_a_input_preflight`의 27개/54쪽 범위와 별도이며, 공통 파일 읽기·타입·실행 결합 함수를 재사용한다. 관측·실행 승인·RES/RPT는 만들지 않는다.

```bash
uv run python -m tools.mvp_live_readiness --write-template /outside/input-sources.json --output /outside/readiness.json
uv run python -m tools.mvp_live_readiness --index /outside/filled-sources.json --output /outside/readiness-next.json
```

색인은 `guard-mvp-live-input-sources/1`이며 `pack_ref`와 `inputs`를 가진다. 각 입력은 `check_id`, `side`(baseline/candidate), `environment`, `policy`, `configuration`이다. 파일 세 개는 각각 `path`와 파일 bytes의 `sha256`을 요구한다. 중복·범위 밖 ID·오래된 팩·추가 필드는 거부한다. 빈 입력이어도 현행 팩의 69개/138쪽을 모두 남긴다.

봉인된 ENV의 MVP 엔진/이미지·GPU 기본 사실, 정책 범위/필수 값/타입, 쪽·점검별 설정, real/kubernetes 구현의 판본별 가용성, target의 정확 ENV Ref, 기존 실행 결합을 확인한다. fake/common-input은 실제 능동 실행 입력으로 받지 않는다. EXAMPLE 파일의 형식이 맞아도 실제 자격으로 올리지 않는다. 파일 결합이 유효해도 `ready_for_execution`, `live_target_verified`, `input_preflight_passed`, `engine_executed`는 false이고 결과는 null이다. 실제 노드에서 기존 `guard prepare-update`가 필요하다.

## 2026-11-16 확인 범위

- main `3aab9c8c`에 기반한 기동/프로파일러 준비의 오래된 공통 헬퍼 SHA를 현재의 정확 bytes로 갱신했다. `/31`·`/32`에 쓰는 private 의존성의 `_bytes`·`_codes`·`_function` 의미를 변경 이력과 대조했다. 변조/심볼릭 링크/재해시 거부 관문과 과거 준비물·실측·생산자 등록은 바꾸지 않는다.
- 직접 36개, CPython 3.12.8 관련 170개, 입력 준비 직접 44개가 통과했다. Linux CPython 3.12.3 전체 기본 suite는 13,773 passed / 기존34 skipped / 실패0이며 pytest1023.07초, 부모1026.256초다. 원 engine/docker/network 기본 제외는 유지했다. 로컬 검증이며 새 PR CI·GPU 실측은 별도다.
- 69개/138쪽의 빈 현재 실환경 입력은 모두 미결합이다. 능동 구현이 닫힌 쪽은 기준19·후보2다. SUP-03은 보안 규칙이 비어 있는 관측 전용이다. 연결/소스 검토·승인된 UNKNOWN 근거와 실제 관측·GPU 등록을 구분한다.
- 사용자 요청으로 정상·위반·미수집·지원 밖의 기존 정의를 사용한 사례309개를 준비했다. 사례나 빈 정책을 실행 결과로 세지 않는다.
- 사용자가 “기업 정책을 확정한 뒤 실증”, 이어 “너가 알아서 확정해줘”라고 위임했다. 기존 사용자 확정11개를 유지하며 소유한 격리 MVP 환경의 정책 결정178개를 정했다. 실제 실행 시각·모델 권한 매핑·자격 사본에 의존하는5개 필드는 명시한 규칙으로 실제 환경에 결합한다. 관측값·기업 외부 환경의 신원·서명 승인을 만들지 않는다. 실행별 POL은 결합 전 DRAFT로 유지한다.

## 실제 촬영 진입 조건

1. 최종 소스 목록과 정확 이미지/모델·판본·인터프리터를 고정한다.
2. 판본별 닫힌 관측 경로는 실제 자격/지원·등록 조건을 충족하거나 기존 승인된 UNKNOWN의 정확 범위를 남긴다. 미승인 공백을 완료로 바꾸지 않는다.
3. 새 비용 승인 범위의 GPU에서 현재 ENV/target/세대/세션·prepared를 만들고 정책 규칙을 실환경에 결합한다. 과거 종료한 Pod의 파일은 재사용하지 않는다.
4. 상태 독립성이 확인된 시험만 기동을 공유한다. 모델·토폴로지·장애/재시작 조건이 달라지면 독립 실행 묶음으로 나눈다.
5. 기존 제품 `guard prepare-update` → `guard update` → `guard verify`의 결과와 실제 EVD/RES/RPT Ref를 보존한다. 여러 묶음의 원본 RPT를 하나인 것처럼 다시 만들지 않는다.
6. 실행/실패/미수집/비적용·승인된 UNKNOWN을 분리해 플랫폼 원본 보고서와 촬영 기록에 연결한다. 실제 실행 전의 설명 영상·EXAMPLE/NOT_RUN 보고서는 촬영 근거에서 제외한다.

새 GPU 시간·비용·대규모 다운로드와 이후 실제 등록은 별도 세션 범위에서 진행한다. 지난 $20/2시간 세션은 정리됐으므로 재사용 가능한 실행 승인이 아니다.
