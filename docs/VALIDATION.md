# 실증 준비와 완료 판정

작성: 2026-10-19, Asia/Seoul. 시작 코드: `codex/gua-update-mvp / 4d97dba`.
이 문서는 이후 준비 도구와 시험 계약을 설명한다. 실제 엔진 실증 완료 기록이 아니다.

## 현재 범위

우선 REQ-01·REQ-03·PLT-01·PLT-04·REQ-09의 실증을 완료한다.
이번에는 새 점검 정의를 늘리지 않았다. 두 신규 점검의 기존 제한 범위도 유지한다.
기존 7종 객체, append-only, core와 runtime 경계, DRAFT/EXAMPLE 보호를 유지한다.
구현을 한 묶음으로 마친 뒤 테스트하고 실패를 수정한다.

## 읽기 전용 실행 준비 검사

```sh
uv sync --frozen
uv run guard prepare-update --pack packs/PACK-BASELINE --baseline /test/env-a.yaml --candidate /test/env-b.yaml --policy /test/policy.yaml --config /test/update.toml > /test/readiness.json
```

성공은 종료 코드 0과 ready=true다. 검사 자체는 store·컨테이너·네트워크를 만들거나 이미지를 pull하지 않는다.
기본 fake 설정은 실제 실증 준비 완료로 인정하지 않는다.
팩 해시, 실제 실행기 설정, 선정된 점검의 정책 누락, 실행기별 설정을 확인한다.
managed 모드에서는 모델 snapshot inventory, 로컬 Linux Docker, 로컬 이미지 digest와 아키텍처를 확인한다.
CPU 프로파일은 Linux/daemon 아키텍처와 일치해야 한다. 나머지 모델명·kernel·threads는 신고값이며
실증 호스트에서 별도로 확인해야 한다. GPU 이름·driver 신고값도 실제 장치 가용성을 계측한 것이 아니다.
external 모드의 버전/digest는 target 메타데이터 대조이며 원격 엔진 신원 증명은 아니다.
준비 검사 성공은 보안 PASS·성능 검증·엔진 기동 성공을 뜻하지 않는다.

필요한 입력: 서로 다른 두 실제 이미지의 pinned digest/버전, 동일 모델 id/revision/inventory,
환경 사실과 하드웨어, 점검 범위를 한정한 정책, offline HF cache, probe/Gateway 로컬 이미지.
지원 이미지는 [managed 예시](../examples/update-managed.toml) 및 runtime/sandbox.py를 따른다.
입력 경로는 실행 CWD 기준이다. 재현용 설정의 target/models_dir에는 절대 경로를 권장한다.

## 5개 점검의 실제 사례 행렬

`examples/engine-cases.json`은 실제 결과가 없는 입력 템플릿이다.
각 점검의 PASS/FAIL/UNKNOWN 사례마다 config/environment/policy 경로와 functional 기대값을 채운다.
경로는 manifest 위치 기준이다. TOML 안의 target은 절대 경로를 사용한다.
각 정책은 정확히 한 점검만 선정하고 모든 실행기를 real로 지정해야 한다.
이미 준비된 독립 시험 컨테이너만 사용한다. 운영 대상은 지정하지 않는다.

| 점검 | 정상 사례 | 위반 사례 | 확인 부족 사례 |
|---|---|---|---|
| REQ-01 | 직통 격리+허용 Gateway 정상 요청 | 무인가 직통 handler 도달 | 경로 수집/정상 대조 누락 |
| REQ-03 | 일반/관리 역할의 실제 경계 | 유효한 관리 경로에 일반 역할 도달 | 안전 프로브 부재/일부 경로 미시험 |
| PLT-01 | 정책에 맞는 non-root·capability·마운트 | 확인된 root/추가 권한/쓰기 위반 | unreadable/종료된 프로세스, 불완전 마운트 |
| PLT-04 | 금지 변수 이름·경로 미전달 | 무해한 GUARD_TEST_CREDENTIAL/표식 경로 전달 | /proc 또는 마운트 읽기 실패 |
| REQ-09 | 정상 token-array 유지+범위 밖 거부 | 확인된 범위 밖 요청의 2xx | 5xx/timeout/정상 대조 실패 |

PLT-01/04는 불완전 수집과 확인된 위반이 함께 있으면 FAIL을 보존한다.
REQ-09는 max_input_tokens+1 요청도 max_probe_tokens 예산 안에 있어야 한다.
실제 vocab 및 정상 [0] token-array 요청의 모델 적합성은 준비자가 입증한다.
정책 한도는 실제 서비스가 적용하는 한도와 연결한다. 임의의 낮은 수치로 FAIL을 만드는 것을 제품 회귀로 해석하지 않는다.

```sh
export GUARD_ENGINE_CASES=/test/engine-cases.json
uv run pytest -q -m engine tests/test_real_case_matrix.py --basetemp=/test/new-case-output
```

설정이 없으면 사유를 남기고 skip한다. 설정을 줬는데 사례가 없거나 fake/미선정/NOT_RUN이면 실패한다.
완료 증거에는 각 사례의 EVD/RES와 기능 판정, MEASURED 여부, 증거 반출·복원 결과가 포함된다.
ABORTED 사례는 설정에 status를 명시하며 MEASURED로 승격하지 않는다.

## 두 이미지 비교

[RUNBOOK](RUNBOOK.md)의 GUARD_UPDATE_* 환경변수를 채운 뒤:

```sh
uv run pytest -q -m engine tests/invariants/test_inv_09.py --basetemp=/test/new-update-output
```

다른 digest, 동일 모델·fixture·실행 조건, 양쪽 COMPLETED/MEASURED EVD와 비교 보고서,
반출·복원 verify를 요구한다. CPU 성능은 계속 UNKNOWN이다.
조건이 다른 시험은 NOT_COMPARABLE과 이유를 확인하며 비교 성공으로 합치지 않는다.
실행 후 probe 정리 실패는 EVD.execution.cleanup_errors에 guard.run 라벨로 남긴다.
보안·기능 증거를 버리지 않고 run/update 종료 코드는 1이며, 재판정 보고서에도 한계가 유지된다.
managed 엔진 자원 정리 실패는 최초 update 보고서의 한계도 함께 보관한다.

## 증거 반출·복원

```sh
uv run guard evidence export --store /test/store --output /test/evidence.zip
uv run guard evidence restore --bundle /test/evidence.zip --destination /test/restored-store
uv run guard verify --store /test/restored-store
uv run guard judge --plan PLAN-ID --store /test/restored-store --pack /absent-pack
uv run guard report --plan PLAN-ID --store /test/restored-store --pack /absent-pack --json /test/rejudged.json
```

반출은 SQLite read-only backup으로 일관된 DB 사본을 만들고 artifacts 및 팩 보존본을 검증한다.
work/target/config/.env를 자동으로 넣지 않는다. DB에 저장된 기존 관측·artifacts는 원본 객체 내용과 artifacts 바이트를 유지한다. SQLite backup의 물리 DB 헤더는 원본 파일과 다를 수 있다.
이 allowlist가 기존 증거 안의 비밀을 탐지/삭제하는 기능은 아니다. 시험용 무해 표식과 수집 시 마스킹을 사용한다.
설정·소프트웨어 버전·최초 JSON/HTML·자원 정리 확인은 비밀 제외 후 별도 보관한다.
체크섬은 무결성용이며 송신자 인증이나 전자서명이 아니다.
복원은 새 경로만 허용하며 경로/중복 파일/크기/체크섬/객체 참조/팩 보존본을 모두 검사한 뒤 공개한다.
기본 해제 용량 한도는 2 GiB이며 --max-bytes로 명시적으로 변경한다.
기존 exposed/isolated의 누락 증거는 원본 복구 전에는 반출 완료로 인정하지 않는다.

## 팀 결정과 후속 작업

| 우선순위 | 산출물 | 완료 확인 | 필요한 결정/조건 |
|---|---|---|---|
| P0 | Linux 설치·readiness 기록 | 설치본 CLI, ready=true, 입력 근거 | 장비·캐시·실제 두 이미지 |
| P0 | 5개 점검 15개 사례 | 위 행렬 실행 결과 및 원본 증거 | 점검별 정책/무해 fixture |
| P0 | 두 실제 이미지 비교 | 양쪽 증거·JSON/HTML·조건/정리 | 이미지 쌍과 모델 확정 |
| P1 | 증거 묶음 검증 | 다른 위치 복원·오프라인 재판정 | 누락 원본 또는 별도 재실증 |
| P1 | 정책·팩 검토 기록 | 담당자·시각·범위의 실제 승인 | 정책 확정자/팩 검토자 |
| P1 | 원격 Linux CI 결과 | 이번 SHA의 workflow 성공 | GitHub Actions 사용 가능 |

정책값·이미지 쌍·담당자는 코드로 임의 확정하지 않았다. DRAFT/EXAMPLE은 공식이 아니다.
AWS는 기존 Linux 절차와 접근·격리·시간/예산·반출·종료 계획을 재사용하며 자원 생성은 별도 승인 대상이다.

5개 점검 실증 후의 확장 후보:

| 후보 | 재사용 | 새 관측/결정 | 완료 시험 |
|---|---|---|---|
| REQ-10 | input_validation, HTTP 정상 대조 | 옵션별 경계·조합·동시 요청 예산 | 한 변수만 바꾼 시험과 후속/병행 정상 요청 |
| OBS-03 | HTTP probe·artifact 저장 | 로그 수집 범위·완전성·무해 표식 | 표식 노출, 무노출, 읽기 실패 구분 |
| REQ-13 | 표식 생성과 probe | 응답/오류 허용 필드 정책 | 허용 정보·표식 누출·오류 경로 |
| REQ-05 | 역할·경로 실행 기반 | 실제 존재하는 모델과 키별 허용표 | 유효한 모델의 허용/거부; 부재 404를 인가로 해석하지 않음 |
| LIF-09 | sandbox·INV-15 | 부분 준비 실패 단계·ready·복구 | 엔진 실패와 도구 ABORTED를 구분하고 복구 확인 |

새 관측/시험 절차는 executors/adapters, 정책과 판정표는 overrides/팩, 회귀는 tests에 둔다.
점검별 분기를 core에 추가하지 않는다. 이 표는 다음 구현 계획이며 신규 자동화 완료 주장이 아니다.


## 이번 직접 검증 결과

코드 커밋 `0c8c61b`의 [Linux CI](https://github.com/dohashelby/gua/actions/runs/39257453735)는
480 passed, 18 deselected / 28.15초 및 wheel 빌드 성공이다.
Windows 전체 실행과 수정 과정은 [PROGRESS](../PROGRESS.md)의 후속 구현 항목에 기록했다.
새 가상환경에 설치한 wheel의 독립 CLI로 반출·복원·오프라인 재판정을 포함한 10개 명령을 확인했다(EXAMPLE).
실제 엔진 opt-in 6건은 설정 부재로 skip했고, 두 실제 이미지나 신규 두 점검의 실증 성공을 의미하지 않는다.
