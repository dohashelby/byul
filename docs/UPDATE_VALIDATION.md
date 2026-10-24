# 두 이미지 CPU 업데이트 비교 수락 도구

작성: 2026-10-24, Asia/Seoul. 출발점은 `codex/gua-automation-hardening`의 `f4541ecfdc843c474b4c0c2c7ead1399c606a9d6`이다. 작업 시작 시 PR #19는 미병합 초안, 원격 main은 `24f7d90a2745c108d7d395e5082a3721a3198f0c`였다. 이번 작업은 기존 다섯 점검의 두 이미지 비교를 준비·실행·보존·재판정하는 도구 구현이다. 실제 두 엔진 비교 성공 기록은 아니다.

## 범위와 수락의 의미

고정 최소 범위는 REQ-01·REQ-03·PLT-01·PLT-04·REQ-09다. 새 정책·실행기·의존성·객체 종류를 추가하지 않는다. 팩20/DRAFT, 활성77·자동연결11·정책21점검/46경로·카탈로그322행/614연결을 유지한다. REQ-11 rev3의 보안 UNKNOWN과 독립 FAIL 보존도 그대로다. REQ-10 추가 실증, OBS-03 exception dump, REQ-15 등록, GPU·성능은 이번 범위 밖이다.

`accepted=true`는 다섯 점검의 양쪽 원본 증거·판정·비교·반출/복원·저장 팩 재판정 계약이 충족됐다는 뜻이다. 확인된 FAIL·STILL_FAILING도 유효한 시험 결과다. 배포 허가나 모든 보안 점검 통과를 뜻하지 않는다. 반대로 명령 종료0, MEASURED, COMPLETED만으로 수락하지 않는다. UNKNOWN만 있는 비교도 수락하지 않는다. 예시 정책은 EXAMPLE/DRAFT이므로 보고서 official=false이며 기업 승인값을 만들지 않는다.

## 준비할 입력

1. 서로 다른 두 **로컬** Linux CPU 이미지의 실제 pinned digest와 실제 엔진 버전. 같은 digest의 다른 태그는 두 이미지가 아니다. 같은 버전의 다른 digest는 `image-comparison`, 다른 버전은 `version-upgrade`로 구분한다. 이름만 바꿔 후보 ENV를 만들지 않는다.
2. 동일한 모델 ID·revision·전체 snapshot inventory와 실제 CPU 프로파일(gpu_count=0, Linux/architecture/model/kernel/threads). 모델 경로의 config·tokenizer·weights를 사전에 준비한다. 지원 여부는 실제 버전/구성의 prepare 결과로 확인한다.
3. 동일한 모델·fixture·실행 자원·엔진·게이트웨이·보안 프로파일. Windows 예시는 [windows-cpu-update.toml](../examples/windows-cpu-update.toml)이며 `models_dir`를 실제 절대 경로로 바꿔 별도 파일로 저장한다.
4. 다섯 점검만 선택한 정책. [update-validation-policy.yaml](../examples/update-validation-policy.yaml)은 기존 EXAMPLE 값의 범위 분리 예시이며 기업 정책이 아니다. 실제 승인 목록을 지원 범위에 맞춰 임의로 줄이지 않는다. 추가 점검은 별도 실행으로 분리한다.
5. 실행에 충분한 Docker Desktop Linux/WSL2 메모리·swap·디스크. 예시의 메모리 제한은 가용 메모리나 기동 성공의 보장이 아니다. 기존 서비스와 자원 충돌을 먼저 확인한다.

도구는 이미지·모델을 내려받거나 서버를 만들지 않는다. 실행 요청은 명시적 `--execute`로만 받는다. baseline 정리 후 candidate를 순차 기동하며 제품의 소유 리소스 정리만 사용한다. 사용자의 다른 컨테이너를 중지·삭제하지 않는다. timeout 때는 원본 산출물과 소유 라벨을 확인하고 해당 실행 리소스만 사람이 점검한다.

## Windows 실행 순서

PowerShell 예시다. 실제 입력은 `C:\guard-trial\inputs`에 준비하고, 출력은 그 밖의 존재하지 않는 폴더로 지정한다. 체크아웃에서 `uv sync --frozen`을 마친 Python 환경을 사용한다. wheel 설치 환경에서도 `python -m guard.update_validation`을 사용할 수 있다.

```powershell
# 기본은 준비 검사: 엔진 기동·store 생성 없음. Docker 읽기 조회는 가능.
uv run python -m guard.update_validation `
  --pack packs/PACK-BASELINE `
  --baseline C:\guard-trial\inputs\baseline.yaml `
  --candidate C:\guard-trial\inputs\candidate.yaml `
  --policy examples/update-validation-policy.yaml `
  --config C:\guard-trial\inputs\update.toml `
  --output C:\guard-trial\prepare-01

# 위 준비 결과와 점검별 양쪽 SUPPORTED를 확인한 후 별도 새 출력 사용.
uv run python -m guard.update_validation `
  --pack packs/PACK-BASELINE `
  --baseline C:\guard-trial\inputs\baseline.yaml `
  --candidate C:\guard-trial\inputs\candidate.yaml `
  --policy examples/update-validation-policy.yaml `
  --config C:\guard-trial\inputs\update.toml `
  --output C:\guard-trial\run-01 --execute

# 기존 산출물을 읽기 전용으로 분석. 새 입력이나 엔진은 사용하지 않음.
uv run python -m guard.update_validation `
  --analyze-existing C:\guard-trial\run-01 `
  --output C:\guard-trial\analysis-01
```

종료0은 준비 모드에서는 ready, 실행/분석에서는 accepted다. 준비 부족은 종료2, 실행 실패·수락 실패는 종료1이다. 입력 형태 오류도 성공으로 세지 않는다. 표준 출력과 `summary.json`의 mode/stage/accepted를 함께 확인한다. 기존 경로 덮어쓰기와 원본·팩·모델 내부 출력은 거부한다. 준비 모드의 ready는 엔진 시험 결과가 아니다.

프로파일 차이를 조사하려면 기존 실행 분석에 `--diagnostic`을 붙인다. 증거 계약의 유효성과 비교 가능 여부를 분리하되 diagnostic은 항상 accepted=false다. 의미 있는 실행 설정이 다르면 config hash가 달라지고 NOT_COMPARABLE이 정상이다. 노출→강화 진단을 이미지 버전 변경의 FIXED로 세지 않는다.

## 구현 경계와 보존

- `guard.update_validation`: prepare-update → update → verify → export/restore → judge/report를 기존 제품 CLI로 연결한다. 새 엔진 실행 파이프라인을 복제하지 않는다. 판정 시각을 한 번 고정하고 update/judge/report의 `--as-of`에 전달한다. 기본 update의 현재 시각 동작은 유지한다.
- `guard.update_acceptance`: 파일 I/O 없이 원본 PLAN·양쪽 ENV·POL·EVD·RES·RPT의 정확한 Ref/해시와 저장 판정표를 대조한다. 열 개 원본 EVD/RES의 정상 대조·필수 관측·챌린지·정리·구체 보안/기능 판정을 확인하고 기존 비교 함수와 보고서의 일치를 검사한다. 원본 RPT manifest를 기준으로 과거/재판정 revision을 구분한다.
- `guard.validation_evidence`: Store를 열어 쓰지 않고 SQLite immutable 읽기, 팩 보존본·첨부 바이트·bundle/복원 무결성을 확인한다. 미체크포인트 WAL/journal과 링크 경로는 거부한다. 기존 단일 CPU 사례 도구도 이 읽기 함수를 공유한다.
- `tools/validate_update.py`는 소스 체크아웃용 얇은 진입점이다. 제품 모듈은 tools나 tests에 의존하지 않아 wheel에 포함된다.

원본 store는 재판정하지 않는다. `restored/`는 반출 바이트 검증용으로 보존하고 별도 `replay-store/`에서 저장 팩으로 재판정한다. 두 복원본은 원본 객체 그래프와 일치해야 하며 replay는 원본 객체를 그대로 보유하고 새 RES/RPT를 추가한다. 원본 보고서와 재판정 보고서를 별도로 보존한다. 현재 디스크의 새 팩을 과거 PLAN에 적용하지 않는다.

`inputs.json`, `commands.json`, `summary.json`은 검증 도구의 실행 메타데이터다. 일곱 증거 객체를 대체하지 않는다. 원시 자격값·설정 본문·Docker stderr를 명령 기록에 복사하지 않는다. 실제 target.json 신원과 해시도 원본 ENV에 대조한다. SHA는 변조 감지 수단이며 collector/호스트 인증이나 서명이 아니다. 실행 진단 파일은 EVD 첨부와 구분한다. CPU 보안/기능만 비교하고 성능은 계속 UNKNOWN이다.

## 이번 검증 결과

구현·테스트 코드·문서 초안을 모두 완료한 뒤 테스트를 시작했다. Windows x64/Python3.11.16/uv0.12.11의 별도 frozen venv에서 최종 기본 전체 **2199 passed / 0 failed / 0 errors / 7 skipped / 18 deselected, 269.40초**다. skip은 Windows 루프백3개·심볼릭 링크 권한4개이며 Docker/engine18개는 기본 제외다. 1분 목표는 미달이다. 제외/skip과 통과 수를 합산하지 않는다.

실제 실행 명령은 `python -X utf8 -m pytest -q --basetemp=<이번 외부 출력>/pytest-final --junitxml=<이번 외부 출력>/pytest-final.xml --durations=15`다. 실행 Python은 `UV_PROJECT_ENVIRONMENT`를 별도 경로로 둔 `uv sync --frozen` 환경이며 공유 editable venv를 덮어쓰지 않았다. 출력 경로만 이 문서에서 치환했고 실제 argv·전체 로그·JUnit·wheel은 저장소 밖 이번 작업 출력에 보존했다.

첫 전체는 2171통과/8실패/21준비오류/6skip/18제외였다. 신규 테스트의 중복 plan 인자, 변경 ENV의 미봉인, 빠진 engine.name을 바로잡았다. timeout 표시는 종료0보다 우선하도록 도구도 보강했다. 검증 조건은 완화하지 않았다. 해당 회귀는 harness37통과/1skip, 증거I/O21통과/1skip 후 최종 전체를 다시 통과했다.

최종 wheel을 별도 venv에 설치하고 외부 CWD/Python `-I`에서 소스=wheel=설치본 **81파일** 일치와 새 모듈3개·CLI 도움말5개를 확인했다. 합성 원본의 반출/복원/저장 팩 재판정과 설치본 읽기 전용 분석·진단 수락 거부가 통과했다. 검사 중 실제 process/network/engine 호출을 막았다. wheel SHA256은 `9c74c02cabd8cf310e5790fb08fd11ccf8790c2644f72fb9aeeb3625cee0e546`다.

기준 f4541ec 대비 보호250파일·점검 정의77개·기존 원문/과거 증거·정책값·코어/모델/저장소/실행기/판정기/런타임이 그대로다. 팩을 임시 위치에 두 번 재생성해 바이트 동일, 팩/카탈로그/정책 metadata 정합을 확인했다. 기존 kind2개의 ID/실행 상태도 전후 동일하다. 직접 검증의 [선별 요약](evidence/update-validation-20261024.json)을 제공한다. 합성 테스트와 실제 엔진 증거를 구분하며 현재 두 이미지 실증을 성공으로 기록하지 않는다.

2026-10-24 읽기 전용 로컬 조회에서는 Docker Linux/x86_64, CPU16, Docker 총 메모리8,025,436,160 bytes를 확인했다. 이는 여유 메모리가 아니다. 로컬 vLLM 이미지는 `vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b` 한 종류다. 두 번째 호환 이미지·실제 후보 ENV가 준비되지 않아 **두 이미지 실제 실행은 미수행**이다. 기존 `bob-guard-lab-worker`·`bob-guard-lab-control-plane` 컨테이너는 실행 중이며 그대로 보존했다. Docker/WSL/컨테이너를 시작·중지하거나 이미지·모델을 수신하지 않았다.

다음 실증의 산출물은 준비 결과, 양쪽 실제 신원, 고정 입력 해시, 정상 대조/챌린지·첨부를 포함한 EVD/RES, JSON/HTML 비교 보고서, evidence.zip, 별도 복원·재판정 결과다. 다섯 점검 전체의 보안/기능이 구체 PASS/FAIL이고 비교가 실제 결과와 일치해야 한다. 실제 후보 버전 선정·로컬 준비·자원 사용 조율은 팀이 결정하며 도구가 값을 추정하지 않는다.
