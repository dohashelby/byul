# 기존 자동화 판정·준비 경로 보강

작성 시작: 2026-10-23, 검증: 2026-10-24, Asia/Seoul. 구현 출발점은 `codex/gua-automation-phase3`의 `5a352512bbc24589845438438b0146a0d7653b6e`다. 원격 재확인에서 기존 PR들의 main 병합을 확인했고 `24f7d90a2745c108d7d395e5082a3721a3198f0c`로 fast-forward했다. 둘의 파일 차이는 `.DS_Store`뿐이며 이번 변경은 최신 main 위에 놓는다. 이번에는 새 정책·실행기를 추가하거나 실제 엔진을 기동하지 않는다. 전체 구현·테스트 코드·이 문서 초안을 완료한 뒤 검증하고 결과를 아래에 기록한다.

## 현재 등록과 의미

PACK-BASELINE 20/DRAFT, 활성 점검 77개, 자동 실행기와 판정 연결 11개, 정책 선언 21점검·46경로를 유지한다. REQ-11만 정의 revision 3으로 바뀐다. 어댑터 0.4.0·declared 0.12.0, 카탈로그 322행·614점검 연결(a43/b3/c161/d115)은 그대로다. a/b/c/d는 정책 소비 분류이며 인프라 자동화 가능 등급 A/B/C와 다르다. 등록 수·실행 완료 수·실제 챌린지 완료 수·보안 PASS 수를 합산하지 않는다.

기본 `guard.toml`은 fake 시연 설정이다. 실제 CPU 경로는 `examples/windows-cpu*.toml`의 명시적 real 설정, 실제 ENV와 로컬 모델 snapshot을 별도로 준비해야 한다. 예시 정책의 EXAMPLE/DRAFT와 기업 값·승인 공란을 유지한다. 공식 여부는 보고서의 `official`을 확인하며 이번 보강으로 팩·정책을 승인하지 않는다.

## REQ-11 새 판정 계약

기존 collector는 고정 CPU 이미지·텍스트 chat template에서 두 HTTP 연결의 JSON/SSE 입력 projection, API ID 연속성, usage·프레임 종료·관측 완결성을 검사한다. 클라이언트 ID·token 개수·모델 생성 문구의 일치는 생성 출력 자체의 독립 귀속 근거가 아니다. 출력 문구와 output token_ids만 두 응답 사이에서 교환해도 입력 projection 검사를 통과하는 합성 반례를 회귀로 보존한다. 이는 실제 엔진 유출을 발견했다는 뜻이 아니다.

새 정의의 보안 PASS 규칙은 비워 둔다. 신뢰할 독립 출력 귀속 오라클이 없는 현재 프로파일은 정상 입력 하위 검사가 완결돼도 전체 보안 판정은 UNKNOWN이다. 확인된 타 요청 prompt 소비·ID 위반은 독립 FAIL 규칙으로 유지한다. 기능 축은 기존 정상 대조·필수 관측·챌린지 관문을 따로 적용하고 성능은 UNKNOWN이다. 수동 PASS도 같은 저장 정의의 rule_table 상한을 거쳐 새 정의의 보안 UNKNOWN을 우회하지 못한다.

관측은 `input_projection_verified`, `output_binding_verified=false`, `output_binding_gap`의 미검증 사유로 입증 범위를 구분한다. 기존 `connection_binding_verified`는 입력 projection 호환 관측이다. JSON/HTML의 점검 한계 문장에 입력 하위 검증과 생성 출력 미검증을 표시한다. `COMPLETED`는 collector 호출의 완료, `MEASURED`는 출처이며 출력 귀속 입증이나 모든 챌린지 완료를 뜻하지 않는다.

과거 팩19/REQ-11 revision 2와 그 EVD·RES·보고서는 변경하지 않는다. 저장된 팩19로 재판정하면 당시 규칙을 사용한다. 새 팩20 판정을 과거 결과에 소급 덮어쓰지 않는다. 독립적인 생성 출력 귀속을 지원하려면 출처·수집 신뢰 경계·정상/교환 음성 대조를 먼저 설계하고 새 정의 revision으로 추가해야 한다.

## managed update sentinel

overprivileged 프로파일은 원래 빈 쓰기 가능 sentinel mount를 요구한다. 단일 validate에는 있던 전달을 update에도 연결한다. baseline과 candidate는 새 실행 폴더 안의 서로 다른 전용 빈 디렉터리를 사용하며 기존 경로나 두 side의 writable 경로를 재사용하지 않는다. default/hardened에는 이 mount를 만들지 않는다. 소유 라벨·순차 실행·정리·정제 진단·ABORTED 증거와 정리 실패 이전의 확인된 FAIL을 보존한다.

회귀는 Docker를 메모리 대역으로 바꾸되 실제 sandbox의 `_start_engine` 필수 인자 검사까지 통과시킨다. 이는 실제 Docker/vLLM 기동 성공 검증과 구분한다.

## 실행 전 점검별 지원 안내

`prepare-validation`과 `prepare-update`는 기존 입력·모델·Docker·로컬 이미지·하드웨어 준비 검사에 더해 선택된 점검의 side별 지원 표를 반환한다. `ready`는 기존 준비 검사 결과이며 지원 표 전체가 보안 PASS라는 뜻이 아니다. 지원 표는 SUPPORTED(현재 정적 계약 일치), UNSUPPORTED(확인된 지원 범위 밖), UNKNOWN(판단 정보 부족)와 이유·한계를 제공한다.

지원 표는 ENV·정책·실행 설정을 읽는 정적 안내이며 엔진·HTTP·새 네트워크·store를 만들지 않는다. prepare의 기존 Docker 검사는 읽기 전용 daemon/image 정보 확인이며 pull/기동은 하지 않는다. 같은 버전 문자열만으로 pinned 이미지 전용 REQ-07·REQ-11·OBS-09의 지원을 추론하지 않는다. 형식상 유효하지만 지원하지 않는 기업 정책은 입력 오류와 구분하고 다른 실행 가능한 점검을 임의로 제외하지 않는다.

지원 안내가 있어도 실제 이미지 소스·마운트·consumer·서버 응답·수집 경로는 실행 후 증거로 확인해야 한다. REQ-10 runner 실제 소비, OBS-03 exception dump, 기업 principal/collector와 GPU·성능은 이 작업에서 새로 검증하지 않는다.

최종 검토에서 OBS-03의 미지원 로그 분기·표식 문맥도 형태 오류와 분리했다. 기존 REQ-09/10/13과 같이 설정·예산·소스 목록 검사는 별도로 유지한다. 실제 실행의 정책 거부와 로컬 엔진 소스 연결 조건을 완화하지 않았으며 예외 dump 수집 기능을 추가한 것은 아니다.

## PR 누적 브랜치의 main 반영 상태

직전 평가에서는 PR #2~#18이 미병합 초안이었다. 2026-10-24 재조회에서는 모두 base=main·MERGED이며 병합 시각은 2026-10-23 23:29:21~23:33:46 KST다. 최종 main은 `24f7d90`이고 #18의 head `5a35251`을 포함한다. #1은 닫혔으나 그 코드는 #2의 실제 merge commit에 포함됐다. 이번 초안의 base는 main이다. 기존 기능을 다시 구현하거나 병합 완료 PR을 다시 만드는 작업은 필요 없다.

종전 계획의 선택지는 의존 순서대로 검토·병합하거나 최종 누적 브랜치의 별도 통합 PR을 검토하는 것이었다. 현재는 원격에서 병합이 완료돼 이 선택을 다시 실행하지 않는다. 향후에는 이번 PR의 main 대비 diff·최종 SHA CI·팩/metadata만 확인한다. 이 작업에서는 GitHub PR 병합·닫기·base 변경·rebase·force push를 수행하지 않았고, 원격 main의 독립 `.DS_Store` 변경도 보존했다.

## 다음 두 실제 이미지 업데이트 실증 준비 계획

1. 팀이 baseline/candidate의 서로 다른 실제 repository@sha256와 version을 선정한다. digest·아키텍처·로컬 보유 여부와 모델 호환성을 확인한다. 아직 확인하지 않은 candidate를 지원한다고 쓰지 않는다. 최초 최소 공통 범위는 기존 REQ-01·REQ-03·PLT-01·PLT-04·REQ-09에서 양쪽 실제 지원을 확인해 결정한다. 고정 이미지 전용 점검은 지원 표에 미지원/미확인을 그대로 표시한다.
2. 같은 모델 ID/revision/inventory, CPU/커널/threads/메모리 상한, fixture, 정책값과 실행 설정을 고정한다. 이미지 차이 외 설정 차이는 명시하고 비교 가능 조건을 확인한다. CPU 성능은 UNKNOWN으로 둔다. 지원 범위를 맞추려고 기업 정책을 축소하거나 예시 값을 기업 승인으로 승격하지 않는다.
3. 기존 컨테이너를 삭제·정리 대상으로 삼지 않는다. 필요한 이미지/모델 다운로드, 디스크·WSL2 메모리 여유, 외부 네트워크/시험 대상 권한과 비용 조건을 확인한 뒤 별도 실행 승인을 받는다. 하나씩 순차 실행하고 새 store/output 경로를 사용한다.
4. 각 side에 실제 MEASURED/COMPLETED EVD, 정상 대조·모든 계획 챌린지·필수 관측·Ref/첨부 해시를 요구한다. 이것만으로 수락하지 않는다. 공통 핵심 행의 양쪽 구체 PASS/FAIL과 의도한 NO_CHANGE/REGRESSED/FIXED/STILL_FAILING을 확인한다. UNKNOWN/NOT_COMPARABLE만 있는 실행은 제품 실증 성공으로 세지 않는다. 새로운 정의의 REQ-11 보안 UNKNOWN은 예상 제한으로 별도 기록한다.
5. 첫 비교는 보안 hardened→hardened의 구체 NO_CHANGE를, 반례 fixture를 승인한 경우 노출→강화의 FIXED 또는 강화→노출의 REGRESSED를 확인한다. 실제 관측이 예상과 다르면 원본을 보존하고 원인을 분석한다. 정리 실패·부분 수집·독립 FAIL 보존도 별도로 확인한다.
6. JSON/HTML의 두 side, 비교 불가 이유·정책/팩 검토 상태·official 여부를 확인한다. 원본 verify→증거 반출→새 위치 복원→저장 팩 재판정에서 의미·원본 해시·멱등을 확인한다. 소유 컨테이너/네트워크만 정리하고 기존 모델/다른 컨테이너/과거 증거를 보존한다.

## 이번 직접 검증 기록

2026-10-24 Windows x64·CPython 3.11.16·uv 0.12.11에서 uv.lock frozen 전용 환경을 사용했다. 기준 Git은 main 24f7d90이고 테스트 대상 변경의 source tree 해시는 [검증 요약](evidence/automation-hardening-20261024.json)에 고정한다. 실제 엔진·Docker stub·GPU·두 이미지 실행은 하지 않았다.

| 단계 | 직접 결과 | 해석 |
|---|---|---|
| 최초 기본 전체 | 2040통과·26실패·5skip·18제외, 223.73초 | 테스트 보조 코드 가정 26건을 수정 |
| 보조 코드 수정 후 전체 | 2066통과·0실패·5skip·18제외, 217.46초 | 기존 제품 보강과 회귀 통과 |
| OBS-03 안내 추가 보수 후 전체 | 2084통과·3실패·5skip·18제외, 224.47초 | 새 준비 회귀의 정책 해시 확정 누락3건 수정 |
| 최종 전체 | 2087통과·0실패·5skip·18제외, 221.7초 | 추가21회귀와 정책 해시·구체 오류 확인을 포함 |
| 격리 wheel 설치 | 패키지78파일 source/wheel/설치본 일치·CLI30회 성공 | -I·저장소 밖·EXAMPLE 대역·실제 프로세스/HTTP/Docker 기동 차단 |

최초 실패19건은 정책 검사 테스트가 실제 클래스 대신 메서드 없는 SimpleNamespace를 전달한 가정, 3건은 side/event 목록에서 존재하지 않는 실행 ID를 찾은 가정, 4건은 테스트 Evidence 생성기의 지원하지 않는 PLAN/ENV keyword 인자 사용이었다. 실제 클래스 바인딩·컨테이너/네트워크 소유 ID·생성 후 Ref 설정으로 테스트를 보수했다. 추가 준비 회귀3건은 정책값 변경 후 다시 seal하지 않아 무결성 관문에서 막혔고, 테스트 정책을 다시 확정하고 구체적인 지원 표·오류 원인까지 확인하도록 수정했다. 정책 상한·실패 판정·필수 검증을 약화하지 않았다.

최종 skip5개는 Windows 루프백3·symlink2의 기존 제약이며 실제 engine/docker 표식18개는 기본 제외다. 둘 다 통과로 세지 않는다. 1분 목표는 달성하지 못했다. 앞선 1~3차 실제 CPU 결과나 이번 각 반복의 통과 수를 합산하지 않는다.

REQ-11 설치본 정상과 생성 출력16개 단독 교환은 보안 UNKNOWN/기능 PASS/성능 UNKNOWN, 타 요청 입력 소비는 보안 FAIL/기능 PASS/성능 UNKNOWN이었다. 전부 EXAMPLE·official=false이며 JSON/HTML에 새 범위와 미검증 이유가 표시됐다. 저장 팩19/정의2의 순수 재판정은 기존 PASS/PASS/UNKNOWN을 유지하고 과거 실측 결과를 변경하지 않았다. 새 제품 기준이 실제 엔진에서 재실증됐다고 해석하지 않는다.

sentinel은 실제 sandbox.up/_start_engine 코드와 memory Docker를 조합한 회귀에서 side 분리·기존 경로 보호·default/hardened·기동 실패·정리 실패·완료된 FAIL 보존을 확인했다. prepare는 side별 exact image 제한, valid unsupported 정책·잘못된 형태, 설정/소스 목록 실패와 다른 점검 보존을 대역으로 확인했다.

코어/models/store·reference·기존 증거/fixture 등 보호 추적175파일과 다른76개 CheckDef는 바이트가 같았다. 외부 복사본 팩 재생성2회는 멱등, metadata46경로 정확, 카탈로그322/614·분류·기업 정책값/범위 불변, 로컬 출처/JSON pointer 유효를 확인했다. 기존 외부 출처772개는 재조회하지 않고 보존했다.

설치 검증 보조 스크립트의 직접 click 의존성과 vendored Group 가정은 Typer 자체 Group으로 수정했다. 새 의존성을 추가하지 않았다. 최종 wheel의 첫 build는 Windows 전역 임시 경로의 PE resource 갱신에서 접근 거부됐고, 작업 전용 TEMP/TMP로 재시도해 성공했다. 제품 실행 실패와 구분한다.

직접 실행 명령의 경로를 축약한 재현 형식은 다음과 같다. OUT은 저장소 밖 새 출력 폴더다. 첫/수정후/최종 로그·JUnit·설치본·원본 실패는 각각 별도 경로로 보존했다.

```powershell
$env:UV_PROJECT_ENVIRONMENT="$OUT\source-venv"
uv sync --frozen
& "$OUT\source-venv\Scripts\python.exe" -X utf8 -m pytest -q --basetemp="$OUT\pytest-final-r3-tmp" --junitxml="$OUT\pytest-final-r3.xml" --durations=15
uv build --wheel --out-dir "$OUT\dist-final"
uv export --frozen --no-dev --no-emit-project --no-hashes --output-file "$OUT\requirements.txt"
uv venv --python 3.11 "$OUT\wheel-venv"
uv pip install --python "$OUT\wheel-venv\Scripts\python.exe" -r "$OUT\requirements.txt" "$OUT\dist-final\guard-0.1.0-py3-none-any.whl"
& "$OUT\wheel-venv\Scripts\python.exe" -I -X utf8 "$OUT\verify_installation.py"
& "$OUT\source-venv\Scripts\python.exe" -X utf8 "$OUT\verify_integration.py" $REPO
```

최종 head SHA의 GitHub 전체 CI·wheel 결과는 이 변경의 초안 PR에서 별도 확인한다. 이 문서의 로컬 테스트 수를 CI 수로 표시하지 않는다. 다음 실행은 앞의 두 실제 이미지 준비 조건과 팀 정책 결정을 충족한 별도 작업이다.
