# 두 이미지 CPU 업데이트 비교 수락 도구

현행: PR #28에서 pinned CPU 이미지 0.31.0/0.32.0의 기존 다섯 점검 실행·수락·반출/복원·저장 팩 재판정을 통과했다. [실측 결과](#실측-결과--c-후속-2026-10-24)는 해당 PR 구현의 실제 실행 기록이고, [v1 통합 후보 검증](#v1-통합-후보-검증)은 PR #24·#25·#28을 합친 후보의 별도 검증 상태다. main 반영·공식 승인과 구분한다.

도구 구현 당시 기록: 2026-10-24, Asia/Seoul. 출발점은 `codex/gua-automation-hardening`의 `f4541ecfdc843c474b4c0c2c7ead1399c606a9d6`이다. 당시 PR #19는 미병합 초안, 원격 main은 `24f7d90a2745c108d7d395e5082a3721a3198f0c`였다. 아래 구현 검증 절의 미실측·팩20·정책21점검/46경로는 그 시점의 기록이며 후속 성공을 취소하는 현행 안내가 아니다.

## 범위와 수락의 의미

고정 최소 범위는 REQ-01·REQ-03·PLT-01·PLT-04·REQ-09다. 현행은 팩21/DRAFT, 활성77·자동연결11·정책22점검/49경로·카탈로그322행/614연결이다. 이번 통합은 새 정책·실행기·팩 revision·의존성·객체 종류를 추가하지 않는다. REQ-11 rev3의 보안 UNKNOWN과 독립 FAIL 보존도 그대로다. REQ-15의 제한 수동 계약은 이미 등록됐지만 실제 캐시 실행기는 없다. REQ-10 하위 runner 소비, OBS-03 exception dump, REQ-11 독립 출력 귀속, 실제 캐시 자동화·GPU·성능은 이번 범위 밖이다.

`accepted=true`는 다섯 점검의 양쪽 원본 증거·판정·비교·반출/복원·저장 팩 재판정 계약이 충족됐다는 뜻이다. 확인된 FAIL·STILL_FAILING도 유효한 시험 결과다. 배포 허가나 모든 보안 점검 통과를 뜻하지 않는다. 반대로 명령 종료0, MEASURED, COMPLETED만으로 수락하지 않는다. UNKNOWN만 있는 비교도 수락하지 않는다. 예시 정책은 EXAMPLE/DRAFT이므로 보고서 official=false이며 기업 승인값을 만들지 않는다.

## 준비할 입력

1. 서로 다른 두 **로컬** Linux CPU 이미지의 실제 pinned digest와 실제 엔진 버전. 같은 digest의 다른 태그는 두 이미지가 아니다. 같은 버전의 다른 digest는 `image-comparison`, 다른 버전은 `version-upgrade`로 구분한다. 이름만 바꿔 후보 ENV를 만들지 않는다.
2. 동일한 모델 ID·revision·전체 snapshot inventory와 실제 CPU 프로파일(gpu_count=0, Linux/architecture/model/kernel/threads). 모델 경로의 config·tokenizer·weights를 사전에 준비한다. 지원 여부는 실제 버전/구성의 prepare 결과로 확인한다.
3. 동일한 모델·fixture·실행 자원·엔진·게이트웨이·보안 프로파일. Windows 예시는 [windows-cpu-update.toml](../examples/windows-cpu-update.toml)이며 `models_dir`를 실제 절대 경로로 바꿔 별도 파일로 저장한다.
4. 다섯 점검만 선택한 정책. [update-validation-policy.yaml](../examples/update-validation-policy.yaml)은 기존 EXAMPLE 값의 범위 분리 예시이며 기업 정책이 아니다. 실제 승인 목록을 지원 범위에 맞춰 임의로 줄이지 않는다. 추가 점검은 별도 실행으로 분리한다.
5. 실행에 충분한 Docker Desktop Linux/WSL2 메모리·swap·디스크. 예시의 메모리 제한은 가용 메모리나 기동 성공의 보장이 아니다. 기존 서비스와 자원 충돌을 먼저 확인한다.

도구는 이미지·모델을 내려받거나 서버를 만들지 않는다. 실행 요청은 명시적 `--execute`로만 받는다. baseline 정리 후 candidate를 순차 기동하며 제품의 소유 리소스 정리만 사용한다. 사용자의 다른 컨테이너를 중지·삭제하지 않는다. timeout 때는 원본 산출물과 소유 라벨을 확인하고 해당 실행 리소스만 사람이 점검한다.

## 두 이미지의 기동 준비와 동일 시험 자료

`[vllm].generation_readiness_timeout = 180`은 명시적 CPU 기동 준비다. health·version 확인 뒤 신뢰 네트워크에서 동일 모델로 최대 1토큰 생성 요청을 한 번 보내고, 완전한 정상 응답을 확인한다. timeout 허용 범위는 1~300초이며 실패를 재시도하지 않는다. 실패하면 기존 ABORTED 경로로 기록한다. 본시험의 30초 제한·정상 대조·판정 규칙은 그대로다. `generation-readiness.json`은 마스킹한 별도 진단이며 EVD가 아니다. 비교 조건에는 동일한 준비 요청·시간 상한만 넣고 실제 응답·경과시간·진단 해시는 넣지 않는다. 이 옵션이 없으면 기존 기동 경로를 유지한다.

`[direct_route]`와 `[role_route]`의 `comparison_routes`는 양쪽 실제 경로의 정렬된 합집합이다. 예시 파일은 검토한 0.31.0/0.32.0 이미지 쌍을 위한 목록이다. 실제 등록 목록은 각 OpenAPI 결과대로 보존하며, 목록 밖의 새 경로·OpenAPI 수집 실패·상태 변경 허용 설정은 실행을 중단한다. 합집합의 추가 경로는 양쪽에 같은 요청으로 적용한다. 어댑터가 안전성을 확인하지 못한 상태 변경 요청은 보내지 않으며 기존 TCP 차단 추론·미시험 UNKNOWN 처리를 유지한다.

고정 fixture 실행기는 실제 해석한 method·path·body·headers·basis·핸들러 기대 조건·역할·정상 대조·TCP 절차 전체를 첨부에 남겨 해시한다. 제출 묶음도 이 자료와 대조한다. source 근거는 별도 provenance로 보존한다. 수신 결과를 해시에 섞거나 경로/basis 차이를 지워 비교 가능하게 만들지 않는다. 새 실행기 버전은 direct_route/0.7.0·role_route/0.5.0이다. 옵션 없는 legacy 형식과 과거 해시는 유지하며, 비교 함수·팩·판정표는 수정하지 않는다.

0.32.0의 관리 안전 probe는 `vllm/vllm-openai-cpu@sha256:827ef38187f9b52f2bb838695ef815ec5b65f4bf3e4c061b002b2b0a19cb0701`에만 추가한다. 양쪽 이미지의 elastic_ep 라우터 SHA는 `2d6805874a868eb63747903f5e10331855b7c277d46a4cef3dca7f20a2e631db`다. 빈 JSON은 L43~49에서 필수 필드 누락 400으로 종료하여 L62~64의 엔진 scaling에 도달하지 않는다. 선행 Content-Type 검증·middleware·예외 응답도 대조했다. 미검토 버전·digest에 이 근거를 확장하지 않는다.

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

## 도구 구현 당시 검증 결과 (후속 실측 이전)

구현·테스트 코드·문서 초안을 모두 완료한 뒤 테스트를 시작했다. Windows x64/Python3.11.16/uv0.12.11의 별도 frozen venv에서 최종 기본 전체 **2199 passed / 0 failed / 0 errors / 7 skipped / 18 deselected, 269.40초**다. skip은 Windows 루프백3개·심볼릭 링크 권한4개이며 Docker/engine18개는 기본 제외다. 1분 목표는 미달이다. 제외/skip과 통과 수를 합산하지 않는다.

실제 실행 명령은 `python -X utf8 -m pytest -q --basetemp=<이번 외부 출력>/pytest-final --junitxml=<이번 외부 출력>/pytest-final.xml --durations=15`다. 실행 Python은 `UV_PROJECT_ENVIRONMENT`를 별도 경로로 둔 `uv sync --frozen` 환경이며 공유 editable venv를 덮어쓰지 않았다. 출력 경로만 이 문서에서 치환했고 실제 argv·전체 로그·JUnit·wheel은 저장소 밖 이번 작업 출력에 보존했다.

첫 전체는 2171통과/8실패/21준비오류/6skip/18제외였다. 신규 테스트의 중복 plan 인자, 변경 ENV의 미봉인, 빠진 engine.name을 바로잡았다. timeout 표시는 종료0보다 우선하도록 도구도 보강했다. 검증 조건은 완화하지 않았다. 해당 회귀는 harness37통과/1skip, 증거I/O21통과/1skip 후 최종 전체를 다시 통과했다.

최종 wheel을 별도 venv에 설치하고 외부 CWD/Python `-I`에서 소스=wheel=설치본 **81파일** 일치와 새 모듈3개·CLI 도움말5개를 확인했다. 합성 원본의 반출/복원/저장 팩 재판정과 설치본 읽기 전용 분석·진단 수락 거부가 통과했다. 검사 중 실제 process/network/engine 호출을 막았다. wheel SHA256은 `9c74c02cabd8cf310e5790fb08fd11ccf8790c2644f72fb9aeeb3625cee0e546`다.

기준 f4541ec 대비 보호250파일·점검 정의77개·기존 원문/과거 증거·정책값·코어/모델/저장소/실행기/판정기/런타임이 그대로다. 팩을 임시 위치에 두 번 재생성해 바이트 동일, 팩/카탈로그/정책 metadata 정합을 확인했다. 기존 kind2개의 ID/실행 상태도 전후 동일하다. 직접 검증의 [선별 요약](evidence/update-validation-20261024.json)을 제공한다. 합성 테스트와 실제 엔진 증거를 구분하며 이 도구 구현 시점에는 두 이미지 실증을 성공으로 기록하지 않았다.

2026-10-24 읽기 전용 로컬 조회에서는 Docker Linux/x86_64, CPU16, Docker 총 메모리8,025,436,160 bytes를 확인했다. 이는 여유 메모리가 아니다. 로컬 vLLM 이미지는 `vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b` 한 종류다. 두 번째 호환 이미지·실제 후보 ENV가 준비되지 않아 **두 이미지 실제 실행은 미수행**이다. 기존 `bob-guard-lab-worker`·`bob-guard-lab-control-plane` 컨테이너는 실행 중이며 그대로 보존했다. Docker/WSL/컨테이너를 시작·중지하거나 이미지·모델을 수신하지 않았다.

당시 다음 실증에 요구한 산출물은 준비 결과, 양쪽 실제 신원, 고정 입력 해시, 정상 대조/챌린지·첨부를 포함한 EVD/RES, JSON/HTML 비교 보고서, evidence.zip, 별도 복원·재판정 결과다. 다섯 점검 전체의 보안/기능이 구체 PASS/FAIL이고 비교가 실제 결과와 일치해야 한다. 실제 후보 버전 선정·로컬 준비·자원 사용 조율은 팀이 결정하며 도구가 값을 추정하지 않는다.

## 과거 C 실측 실패 (PR #27)

[PR #27](https://github.com/dohashelby/gua/pull/27)은 main `00c2976172a3b200aacd492ddf3c8b5399b65658`로 두 실제 CPU 이미지를 실행한 과거 실패 기록이다. 기준/후보는 아래 후속 성공과 같은 pinned 이미지·기존 OPT 모델이며 제품 코드를 변경하지 않았다. `guard update`는 종료0·720.156초였지만 수락 도구는 종료1·746.168초였다. 후보 REQ-01은 정상 응답 30,133ms timeout으로 보안/기능 UNKNOWN, 후보 REQ-03은 안전 probe 미지원으로 등록5개 중4개만 시험해 보안 UNKNOWN·기능 PASS였다. REQ-01/03은 fixture 차이로 NOT_COMPARABLE, 나머지 세 점검은 양쪽 PASS/PASS·NO_CHANGE였다. 원본25=복원25객체·재판정26객체에서 원본·판정·비교는 보존됐다. 이 실패의 원본 출력과 PR을 보존하며 아래 성공 실행으로 덮어쓰지 않는다.

## 실측 결과 — C 후속 (2026-10-24)

**실증 성공.** 두 실제 이미지 실행·수락 검증·반출/복원·저장 팩 재판정이 모두 통과했다. `guard update`와 `python -m guard.update_validation --execute` 모두 종료0이며, 읽기 전용 기존 실행 재분석도 accepted=true다. 이전 C의 실패 실행은 덮어쓰지 않았다. 원본 JSON/HTML·bundle·명령/종료 코드·사람 확인용 REVIEW는 저장소 밖 `<output>`에 보존한다.

환경은 Windows 11 Pro·Docker Desktop 4.95.0/Engine 29.8.2·Linux WSL2 6.18.33.2-microsoft-standard-WSL2, x86_64·Intel Core Ultra 7 255H(논리16)다. 양쪽 CPU2/threads2·memory4GiB/swap포함5GiB·shm512MiB·float32·max_model_len128·KV64MiB·hardened/isolated/role_enforced가 동일하다. 아래 두 이미지는 이미 로컬에 있었으며 이번에는 이미지 pull·새 모델 다운로드를 하지 않았다.

- 기준 API 0.31.0: `vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b`
- 후보 API 0.32.0: `vllm/vllm-openai-cpu@sha256:827ef38187f9b52f2bb838695ef815ec5b65f4bf3e4c061b002b2b0a19cb0701`
- 모델: 기존 facebook/opt-125m revision `27dcfa74d334bc871f3234de431e71c6eeba5dd6`, inventory `b11bc2cafab45720afd3d2f19c8250352977bc216efb88fc0afb9d90edc2aea6`(7파일/251897335 bytes).
- 실행 코드는 main `00c2976172a3b200aacd492ddf3c8b5399b65658` 위 미커밋 수정본이었다. base SHA만으로 시험 코드를 식별하지 않는다. `<output>/preservation-before.json`의 소스 파일별 SHA와 소스/설치 89파일이 일치한 wheel SHA `cf77954a3c84db03ccf6b2706373c351c48b817c64203c77e0c96eb81d4ec11f`로 식별하며, 종료 후 제품 소스 바이트 불변을 확인했다.

S/F는 보안/기능이며 비교 두 축 모두 아래 결과다. 실제 EVD 10개는 MEASURED, PACK21/DRAFT와 EXAMPLE/DRAFT 정책으로 보고서 official=false다.

EVD의 수집 출처와 Result의 검증 수준은 별개다. 이 실행의 REQ-01/03 양쪽 `RES.verification_level`은 비교 조건에 포함된 신고 관측의 출처 때문에 DECLARED이고, REQ-09·PLT-01·PLT-04는 MEASURED다. 아래 PASS는 해당 고정 프로파일의 규칙 판정이며 기업 정책 확인·팩 검토·수집 호스트 인증을 대신하지 않는다.

| 점검 | 기준 S/F | 후보 S/F | 비교 | 기준 / 후보 EVD ID |
|---|---|---|---|---|
| REQ-01 | PASS/PASS | PASS/PASS | NO_CHANGE | EVD-01M5PKHEG805VBJJ5HQJ3SZG8A / EVD-01M5PKRPVRE24S58BMSR9Q9WCP |
| REQ-03 | PASS/PASS | PASS/PASS | NO_CHANGE | EVD-01M5PKMGS0M687Q18689BP4XNN / EVD-01M5PKVY0XJQY95ABWZTX9SW9R |
| REQ-09 | PASS/PASS | PASS/PASS | NO_CHANGE | EVD-01M5PKMQ07Y1WC99EFB3SS3526 / EVD-01M5PKW4YQ6YMASVSEAX27GP0B |
| PLT-01 | PASS/PASS | PASS/PASS | NO_CHANGE | EVD-01M5PKMW734R8T8AE0XRCZ847N / EVD-01M5PKWAF76610BFMVGYB4T8Z3 |
| PLT-04 | PASS/PASS | PASS/PASS | NO_CHANGE | EVD-01M5PKN0P78R1FJE5KQFZNXBPZ / EVD-01M5PKWF36N8AMTZ1P6WYGHJVF |

REQ-01 정상 대조는 기준/후보 HTTP200·150/127ms로 응답했다. 기동 준비 생성은 각각 1회·52.046/23.922초이며 본시험 제한은 여전히 30초다. 등록/시험 경로는 15/15와 16/16으로 다르지만 동일 합집합 요청 자료의 fixture SHA는 양쪽 `d2711fe72298cf649424f26577d8a1b37a45e90500fac81ecda738b2307c30ed`다. 후보에만 등록된 `/v1/systemone`을 기준 등록 목록에 추가하지 않았다. 후보의 빈 본문은 structured_decisions/protocol.py L26~29 필수 필드 검증에서 HTTP400으로 거부되어 api_router.py L51~56의 서비스 호출에 도달하지 않음을 소스로 확인했다.

여기서 시험 경로 수는 모든 HTTP handler의 실제 호출 수가 아니다. 미검토 상태 변경 경로 `/v1/responses`·`/v1/responses/{response_id}/cancel`에는 HTTP를 보내지 않았으며, 엔진 직통 TCP 연결 차단을 근거로 해당 경로도 직통 도달하지 못함을 확인한 기존 절차다. HTTP handler 자체의 정상 동작·변경 작업 거부를 별도로 입증하지 않는다.

REQ-03은 양쪽 등록5/시험5·미시험0이다. `POST /scale_elastic_ep` 빈 JSON의 관리 역할은 검증400을 기존 규칙으로 handler_reached로 기록하며, 비관리 역할은 401/403이다. fixture SHA는 양쪽 `ab3045e48461bada452591fefe4d55ebe021d3f49e7e7153de2d6bab28217ae8`다. source·실제 등록 목록은 증거에 남기고 요청 자료가 같은지를 해시·제출 묶음·첨부 바이트 검증으로 확인했다.

보안·기능 UNKNOWN/FAIL은 없다. 위 10개 EVD의 성능 UNKNOWN만 CPU 범위의 의도된 미검증이다. 수집 실패를 설계 한계로 바꿔 기록하지 않았다. 원본25=복원25객체, 재판정26객체에서 원본 객체·판정·비교가 같다. 기존 kind2개의 ID/Running/StartedAt/restart, 모델·팩·입력·이전 C 실행 바이트를 보존하고 이번 소유 자원은 정리했다.

직접 측정 시간은 harness 557.684초, update 540.469초, 복원 verify 1.211초(종료0), 기존 실행 재분석 2.188초(종료0)다. 구현을 모두 마친 뒤 Windows 신규 회귀 152통과/실패·skip0·4.37초(부모5.032초), Linux 기본 2716통과/실패·skip0/기존18제외·134.20초(부모134.880초), wheel 설치 검증을 통과했다. 신규 해시 변이 테스트의 입력이 원래 값과 같던 오류 1건만 고쳤고 기존 테스트 기대값·판정표·팩·비교 함수는 바꾸지 않았다. 60초 초과는 사용자가 허용했으며 시간을 기록했다.

이번 결과는 이 두 이미지·모델·프로파일의 보안/기능 비교 도구 동작을 입증한다. **전부 NO_CHANGE이므로 실제 이미지 차이를 REGRESSED/FIXED로 탐지한다는 것은 이번 실증으로 증명되지 않았다.** 성능 수치·GPU·다른 엔진/모델·기업 승인·77개 전체 자동화·호스트/collector 인증도 미검증이다. 준비 생성 후 이번 정상 요청은 timeout 없이 완료됐으나 과거 지연의 엔진 내부 원인을 프로파일링해 확정한 것은 아니다.

## v1 통합 후보 검증

**v1 기술 완료 후보.** main `00c2976172a3b200aacd492ddf3c8b5399b65658` 위 `codex/gua-v1-integration`에 PR #24의 판정 의존 예외 보호, #25의 테스트 준비·격리·시간 기록, #28의 기동 준비·pinned 안전 probe·고정 fixture를 통합했다. #26 폼 프로토타입은 제외하고 #27 과거 실패·원본은 보존했다. 구현·문서 초안을 모두 마친 뒤 다음을 직접 실행했으며 실패 수정이나 기존 기대값 변경은 필요하지 않았다.

| 직접 검증 | 결과 | pytest / 부모 벽시계 |
|---|---|---|
| WSL2 Ubuntu·Python 3.11.16 기본 suite | 2737통과·실패/오류/skip0·기존 engine/docker 18제외 | 130.61초 / 131.498초 |
| Windows·Python 3.11.16 집중 회귀 7파일 | 175통과·실패/오류/skip0 | 12.19초 / 13.719초 |
| 새 wheel의 격리 설치·오프라인 계약 | 소스/wheel/설치 89파일 일치·변조 거부·저장 팩 재판정 통과 | 해당 없음 / 9.692초 |
| 기존 실측 분석 및 통합판의 새 복원·judge/report | 수락 통과·원본25=복원25·재판정26·기존10 RES 및 보고 의미 전체 동일 | 해당 없음 / 30.920초(검증 본문30.640초) |

Linux 기본 명령은 `python -X utf8 -m pytest -q --basetemp <output>/pytest-full --junitxml <output>/full-linux.xml --durations=50`이다. 집중 회귀는 `test_declared_dependency_errors.py`, `test_fixture_isolation.py`, `test_generation_readiness.py`, `test_route_fixture.py`, `test_fixed_route_execution.py`, `test_vllm_pinned_safe_probes.py`, `test_fixed_fixture_saved_integrity.py`이며 별도 임시 경로에서 실행했다. 설치본은 `uv build --wheel` 뒤 `python tools/verify_installed.py --wheel <output>/dist/guard-0.1.0-py3-none-any.whl --output <output>/installed-verification`으로 검증했다. 전체 명령·종료 코드·시간·JUnit은 저장소 밖 `<output>`에 둔다. 60초 초과를 기록했고 시간 실패 gate·새 skip/제외·assert/불변식 완화는 없다.

실제 엔진은 이번에 다시 기동하지 않았다. PR #28의 최종 커밋 `3fcece80cd6d7ff3d6afcef005e1d321e1fd0941` 대비 통합판 제품 차이는 `judges/declared.py`의 수동 판정 의존 오류 보호뿐이며 실행기·어댑터·기동·자동 rule table·비교·저장 코드·팩·의존성 선언은 바이트가 같다. #28 실측 시 제품 89파일과 그 최종 커밋의 일치는 기존 외부 `final-provenance.json`으로 연결된다. 통합판 소스/wheel/설치 89파일도 동일하며 새 wheel SHA는 `199183c7f093c7add1096276b2a25547bf182d0629c520138a0527134f12dd78`다.

통합판 Python으로 `python -m guard.update_validation --analyze-existing <historical>/run --output <output>/evidence-recheck/analysis-original`을 실행한 다음, 서로 다른 새 디렉터리 두 개에 원본 bundle을 복원했다. 첫 복원본은 bundle·저장 파일·CLI `verify` 불변을 확인했다. 두 번째에서는 실제 `guard judge`와 `guard report`를 원래 PLAN·`--as-of 2026-10-24T09:20:28.207337+00:00`·의도적으로 존재하지 않는 디스크 팩 경로로 실행해 저장 팩을 사용했다. 단순히 과거 재판정 파일을 재분석한 것과 구분한다. 수락 재검증·원본 25객체 전체·기존 10 RES·세 축·비교·출처·집계·refs가 같으며 새 RPT 하나만 추가됐다. 원본 전체 디렉터리의 전후 파일 SHA도 같다. 외부 `evidence-recheck/summary.json`·명령 로그·소스 출처·`REVIEW.md`에 근거를 보존한다.

따라서 두 실제 이미지 실행·수락·반출/복원·저장 팩 재판정의 v1 도구 동작 조건을 기존 실측과 통합 회귀로 뒷받침한다. main 병합·팩 REVIEWED·기업 정책 CONFIRMED·approval은 별도 검토 상태다. 기존 5점검 양쪽 보안/기능 PASS·모두 NO_CHANGE·성능 UNKNOWN·DRAFT·official=false 및 위 HTTP 미시험/DECLARED 출처 범위는 유지한다. 실제 변화 탐지·GPU·다른 엔진/모델·전체 77개 자동화·기업 보안 적합성·호스트/collector 인증은 이번 완료 주장에 포함하지 않는다.
