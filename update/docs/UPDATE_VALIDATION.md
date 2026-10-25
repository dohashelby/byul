# 두 이미지 CPU 업데이트 비교 수락 도구

## 2026-10-26 현행 후속 실증 계획 — 미수행

**사용자 지시로 실제 vLLM 기동·추론·두 이미지 시험은 보류한다.** 아래는 현재 CLI와 점검 정의를 읽고 작성한 준비 계획이며 명령도 실행하지 않았다. 과거 팩21·22의 성공/실패·EVD·보고서 bytes·HTML 경로는 아래 원문대로 보존한다. 현행 팩의 기준은 [77개 추적표](CHECKLIST_VALIDATION_STATUS.md)이고 과거 실측을 현재 팩의 실측으로 승격하지 않는다.

최초 재개 묶음은 기존 REQ-01·REQ-03·PLT-01·PLT-04·REQ-09와 SUP-04의 분기별 독립 시험이다. SUP-03은 승인 입력·지원 프로파일·관측전용 collector·계약 검증이 갖춰진 경우에만 실증을 추가한다. 이번 readonly template bytes·API projection은 독립 worker 소비 센서가 아니므로 승인 일치·비승인 모두 보안 UNKNOWN이다. 새 독립 소비 센서 구현은 이번 범위 밖이며, 관측전용 연결을 자동 보안 판정 완료나 실제 소비 검증으로 표시하지 않는다.

| 점검 | 정상 대조·위반 시험 계획 | 미수집·지원 밖에서 보존할 것 |
|---|---|---|
| REQ-01 | 같은 무해 요청을 허용 Gateway 경유와 비허용 직통에서 대조하고 등록/시험 경로·출발점·정상 추론을 연결 | 미시험 상태 변경 경로·HTTP 미송신과 TCP 차단 근거를 구분. 도달성/등록/정상 대조 부족은 UNKNOWN |
| REQ-03 | 검토된 digest·경로의 안전 probe로 허용 관리자와 일반 호출자의 실제 수행/거부 대조 | 미검토 이미지·관리 경로·효과 미확인은 UNKNOWN. 시험 범위를 맞추기 위해 실제 역할 정책을 축소하지 않음 |
| PLT-01 | 제한 권한과 전용 무해 sentinel을 쓰는 위반 구성의 유효 권한·mount·정상 요청 대조 | 프로세스 스캔·집행·sentinel 누락과 다른 호스트/GPU 범위를 분리. 소유하지 않은 경로나 파일을 사용하지 않음 |
| PLT-04 | 명시적 마운트 금지와 별도로 무해한 비어 있지 않은 후보의 absent/denied/readable을 확인 | symlink·directory·누락·명시[]를 구분. 파일 본문·실제 자격 유효성/사용을 읽기 권한 관측으로 대신하지 않음 |
| REQ-09 | 정책상 정상 입력과 현재 지원 token ID·길이·endpoint/형식 경계를 대조. 기본/off와 검토된 opt-in 원인 수집을 별도 case로 기록 | 2xx 오류 객체·400·timeout을 실제 소비나 피해로 바꾸지 않음. 프로파일/정체성/원인 근거 부족은 UNKNOWN |
| SUP-04 | 같은 소유 엔진의 pinned RepoDigest 승인 일치/불일치를 각각 명시한 EXAMPLE 정책 사본으로 대조 | 승인 일치도 전체 공급물 보안 PASS 아님. tag-only·identity 불일치·수집 실패는 UNKNOWN, 이미 확인한 비승인 실행 FAIL은 후속 오류로 지우지 않음 |
| SUP-03 관측전용·조건부 실증 | readonly template bytes·API projection을 승인 일치/불일치 사본으로 비교하되 두 경우 모두 보안 UNKNOWN. 독립 실제 소비 EVD가 있어야 후속 판정 계약 검토 가능 | 비승인 template mount·존재/해시/inventory만으로 FAIL을 만들지 않음. config/tokenizer·secondary/resolver/redirect·새 기동 challenge와 공급물 전체는 미검증 |

### 입력·상태·자원 분리

- baseline/candidate의 서로 다른 실제 repository@sha256, 엔진 버전·아키텍처·소스/검토 프로파일, 동일 모델 ID/revision/inventory를 먼저 확인한다. CPU/threads·커널·메모리·문맥·fixture·기동 조건을 고정하고 명시하지 않은 차이는 비교 가능하다고 추정하지 않는다. 성능은 미검증으로 둔다.
- 정책은 승인 기업 원본과 EXAMPLE 실험 사본을 구분한다. 승인값을 ENV·관측에서 복사하지 않는다. side·case마다 정책/PLAN/EVD/출력 경로를 분리하고 정확한 Ref를 기록한다. baseline/candidate가 같은 POL을 공유할 수 있는 업데이트 비교와, 위반 정책을 다르게 준 개별 진단 시험을 합산하지 않는다.
- 동일 이미지·모델·기동 조건의 관측은 상태 독립성·사례별 증거 분리가 확인될 때만 기동을 공유한다. 정상 요청·건강/identity 전후 상태와 실패·정리 여부를 기록하고 OOM·재시작·모델/기동 변경·독립성 불확실 시 새 인스턴스를 사용한다. SUP-04의 metadata 수집 전후 identity만으로 그 뒤 정상 추론 구간까지 입증했다고 쓰지 않는다.
- 기존 컨테이너·kind·모델·과거 output을 건드리지 않는다. 소유 자원만 정리하고 재개 전 자원 여유·필요한 이미지/모델·상한·수집 권한을 확인한다. 값이 미정이면 해당 분기를 준비 대기로 남긴다.

### 현재 CLI에 근거한 미실행 템플릿

아래는 `update/`에서 입력과 신규 외부 경로를 확정한 뒤 사용할 형식이다. 지금 실행하거나 `<...>`를 예시 승인값으로 채우지 않는다. `prepare-*`도 읽기 전용 Docker 확인을 수행하므로 이번 계획 작성에서는 호출하지 않았다.

```powershell
uv run guard prepare-validation --pack packs/PACK-BASELINE --env "<ENV.yaml>" --policy "<CASE-POL.yaml>" --config "<CFG.toml>"
uv run guard validate --pack packs/PACK-BASELINE --env "<ENV.yaml>" --policy "<CASE-POL.yaml>" --config "<CFG.toml>" --store "<NEW-CASE-STORE>" --output "<NEW-CASE-OUTPUT>"
uv run guard prepare-update --pack packs/PACK-BASELINE --baseline "<BASELINE.yaml>" --candidate "<CANDIDATE.yaml>" --policy "<COMMON-POL.yaml>" --config "<CFG.toml>"
uv run python -m guard.update_validation --pack packs/PACK-BASELINE --baseline "<BASELINE.yaml>" --candidate "<CANDIDATE.yaml>" --policy "<COMMON-POL.yaml>" --config "<CFG.toml>" --output "<NEW-FIVE-CHECK-OUTPUT>" --execute
```

현재 `update_acceptance.CHECKS`·`validate_cpu_cases.CHECKS`는 기존 5개에 고정돼 있다. SUP-04·조건부 SUP-03을 이 5개 수락 도구의 성공 건수에 끼워 넣지 않는다. 추가 점검은 우선 별도 case의 `validate`와 아래 증거 검증으로 기록하며, 확장 수락 계약이 필요하면 별도 정의·검증한다. `guard update` 종료0과 수락 성공도 구분한다.

### 실증 재개 후 증거와 완료 판단

각 case의 명령·종료 코드·시간·소스 snapshot·팩/정책/설정·PLAN·EVD/RES·자료 출처를 남긴다. 원본 RPT JSON과 비교 결과를 보존하고 `guard verify` → `guard evidence export` → 새로운 위치의 `guard evidence restore` → 복원 검증 → 저장 팩의 `guard judge`·`guard report --json` 순서로 원본 불변과 판정/비교 의미 일치를 확인한다. 재판정 시각과 원래 실행 시각, 사용한 `--as-of`를 구분한다.

BYUL 투영은 기존 platform의 RPT→`verification-result/0.2` 변환기를 사용하고 스키마·정확한 report/RES Ref·manifest·null/UNKNOWN·official·팩/정책 상태를 확인한다. 계약 예시 검사만으로 실제 RPT 연동을 대체하지 않는다. U1 상세의 계약 표현 미결은 원본 RPT와 함께 별도 한계로 남기고 임의 필드·새 생산자를 만들지 않는다. `report.html`·`--html`을 복구하지 않으며 OBS 증거 HTML은 별개다.

완료는 실제 시험한 분기와 증거로만 기록한다. FAIL/UNKNOWN도 결과 그대로 보존하고, 전부 NO_CHANGE라면 실제 악화/개선 탐지는 미입증이다. 부분관측·실험용 정책·수집 실패·공급물 전체·실제 소비·성능·GPU·기업 승인의 미검증 범위를 한 쪽 요약에 명시한다. 본 절의 모든 실험은 현재 미수행이다.

## 후보 길이 거부 원인·자격 후보 후속

> 2026-10-25: PR #30·#31은 main(`fb0c103`)에 병합됐다. 아래 수락 성공은 `length_cause_evidence = true`(opt-in)로 실행한 결과이며, 옵션 없는 기본 실행은 아래 "PR #30 당시" 기록처럼 후보 REQ-09가 UNKNOWN으로 남아 수락에 실패할 수 있다. PLT-04는 후보 단독 시험(비어 있지 않은 EXAMPLE 후보)과 두 이미지 update(후보 `[]`, 부분 범위)를 구분한다.

**이번 후속의 두 실제 이미지 update 실증은 수락 성공이다.** 기준은 당시 미병합이던 PR #30의 `d6b42ec1ec8c2c2da9b624dcaa7fcc40104bd39f`다. 먼저 후보 단독 다섯 사례로 REQ-09의 길이 거부 원인과 비어 있지 않은 PLT-04 후보를 확인한 뒤, 같은 최종 제품으로 별도의 두 이미지 업데이트·반출·복원·저장 팩 재판정을 통과했다. 아래 PR #30 당시 팩22 수락 실패와 원본 증거는 과거 기록으로 보존한다. 판정표·기업 정책·수락 기준은 완화하지 않았다.

REQ-09는 선택적 `length_cause_evidence` 수집으로 모호한 HTTP400에 원인 근거를 더한다. 단순 상태 코드·`max_tokens=0` 오류 문장이나 구성 신고만으로 verified를 만들지 않는다. 검토한 source pin과 실제 대상의 이미지/버전·모델 신원, 실제 모델 길이128 조건, 제출 요청과 제한 계산·정상 전후 대조의 연결을 확인하지 못하면 UNKNOWN을 유지한다. 이는 위험한 소비나 피해를 계측하는 새 센서가 아니며 원인 첨부의 출처·무결성과 본시험 결과를 구분한다. 옵션이 없는 과거 실행과 저장된 EVD를 고치거나 새 보장을 소급하지 않는다.

```toml
[input_validation]
# 기존 vocab_size/vocab_source/max_probe_tokens/request_timeout 설정과 함께 사용한다.
length_cause_evidence = true
```

기본 실행기는 0.5.0, 명시적으로 옵션을 켠 실행기는 0.6.0이며 어댑터 0.4.2·팩22·판정표는 유지한다. 새 `vllm-cpu-length-cause/1`은 위 후보 digest의 0.32.0·기존 OPT-125m revision/inventory·CPU bounded 문맥128만 검토했다. 판정 기준인 `max_input_tokens=127`을 바꾸지 않고, 제출128·출력요청1·모델한도128·플랫폼 cap과 정상 대조를 연결해 출력 여유0의 원인을 확인한다. 같은 소유 컨테이너의 실제 trusted network ID/IP:8000과 HTTP 대상, 이미지·Cmd·마운트·기동 신원을 전후 대조한다. 플랫폼 관측은 별도 Python이며 API 서버 메모리의 직접 측정이 아니다.

검토된 이미지·모델·소스 해시·네이티브 라이브러리·엔진 버전은 코드가 아니라 `src/guard/adapters/reviewed_profiles/vllm-cpu-length-cause-1.json`에 있다(데이터 분리 완료). **새 이미지 지원은 아직 아니다**: 로더는 이 파일 하나만 고정해 읽고, `adapters/vllm.py`에도 `engine_version == "0.32.0"` 조건과 버전별 안전 probe 이미지 표가 남아 있어, 프로파일 파일을 추가하는 것만으로는 새 이미지가 선택되지 않는다. 여러 프로파일 선택과 새 이미지 연결은 후속 보강이다. 프로파일이 맞지 않는 이미지에서는 원인 근거를 확인하지 않고 UNKNOWN을 유지한다.

추가 Python 관측은 호출당 30초·16KiB로 제한한다. 해당 이미지의 기본 native preload도 일반 허용하지 않고 `reviewed-cpu-native-libraries/1`의 정확한 설정과 두 라이브러리 바이트 SHA를 검증한다. 다른 preload·값이 있는 다른 `LD_*`·Python 경로 주입은 관측 Python 기동 전에 거부한다. 소스/플랫폼/cap 불일치·대상 변경·정상 대조 부족도 UNKNOWN을 유지한다. 원인 첨부에는 허용 필드·고정 코드·해시만 남기며 원본 Env·호스트 마운트 경로·예외 문자열은 저장하지 않는다. 현재 수집 설정과 다른 버전/모델에 이 근거를 확대하지 않는다.

PLT-04는 소유한 hardened 컨테이너의 `/guard-cache` tmpfs에 무해한 EXAMPLE 파일을 준비해 `[absent, denied]` 묶음·읽기 권한이 있는 파일·symlink·directory를 각각 시험했다. 기존 모델·마운트·컨테이너를 바꾸지 않았다. 컨테이너의 관측 프로세스 4개(수집 프로브 제외)의 주체·전후 안정된 메타데이터를 확인했으며 실행기는 후보 본문을 열지 않았다. 이 수를 모델 worker나 TP 그룹 수로 해석하지 않는다. 단순 마운트·mode bits만으로 접근을 추정하지 않고 지원하지 않는 경로 형태는 UNKNOWN으로 보존했다. readable은 후보 파일의 유효 읽기 권한이지 실제 자격 내용·유효성·서비스에서의 사용 인증은 아니다. 기존 명시적 마운트 금지 위반과 후보 관측은 계속 분리한다.

후보 단독 harness는 종료0·364.875초(부모365.753초), 기록한 170개 명령은 모두 종료0·timeout0이다. 각 사례는 원본6=복원6→재판정7객체이며 RES 전체·보고 행이 같고 원본 바이트를 보존했다. 다섯 EVD는 실제 MEASURED, 정책 EXAMPLE/DRAFT·팩 DRAFT·official=false, 성능 UNKNOWN이다. 상세 근거는 `<output>/run/<case>/report.json/html`, `evidence.zip`, `evidence-readable/`, `summary.json`에 있다.

| 사례 | 보안/기능 | 실제 근거 | EVD ID |
|---|---|---|---|
| REQ-09 길이 원인 | PASS/PASS | 입력128·요청출력1·모델한도128·CPU cap·정상 전후 대조·동일 대상·13개 소스 SHA로 `REVIEWED_CPU_CONTEXT_EXHAUSTION` | EVD-01M5PYDYSWKRP9CSEX0MC2G8R6 |
| PLT-04 absent·denied | PASS/PASS | `PATH_ABSENT` / `EFFECTIVE_READ_PERMISSION`·worker 주체/목록 안정 | EVD-01M5PYG3SN5YD3W9TK3H8GVF8W |
| PLT-04 readable | FAIL/PASS | EXAMPLE 파일의 `candidate_readable` 확인 | EVD-01M5PYH0BFSVSTVD3WW7ASWB9E |
| PLT-04 symlink | UNKNOWN/PASS | `SYMLINK_NOT_FOLLOWED`·지원 범위 한계 | EVD-01M5PYHW48GRVFYQZCHDDF02MA |
| PLT-04 directory | UNKNOWN/PASS | `NONREGULAR_SCOPE_UNVERIFIED`·지원 범위 한계 | EVD-01M5PYJQWWKGVZ3XGEC83NBXGP |

길이 HTTP400 본문은 이전과 같지만 새 원인 증거가 추가됐다. 정책127·실제 입력128·요청출력1을 그대로 유지하며 `min(128-128, 1, None, sys.maxsize)=0`으로 출력 여유가 소진되어 generate 호출 전에 거부되는 소스 경로를 확인했다. 소비·피해 센서는 계속 없어 `challenge_consumption`은 unknown, `confirmed_input_violations=[]`다. PLT-04의 위 FAIL은 도구 실패가 아닌 읽기 가능한 후보의 보안 판정이고 두 UNKNOWN도 수집 실패와 구분한다.

### 추가 두 이미지 update 수락

Windows 11·Docker Desktop Linux/WSL2·Intel Core Ultra 7 255H에서 기존 OPT-125m revision `27dcfa74d334bc871f3234de431e71c6eeba5dd6`·inventory `b11bc2cafab45720afd3d2f19c8250352977bc216efb88fc0afb9d90edc2aea6`을 재사용했다. CPU2/threads2·메모리4GiB·문맥128·hardened 조건과 기존 정책값을 유지하고 실행 설정에 `length_cause_evidence=true`만 추가했다. 이미지·모델을 새로 다운로드하지 않았다.

- 기준 0.31.0: `vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b`
- 후보 0.32.0: `vllm/vllm-openai-cpu@sha256:827ef38187f9b52f2bb838695ef815ec5b65f4bf3e4c061b002b2b0a19cb0701`

| 점검 | 기준 보안/기능 | 후보 보안/기능 | 보안 / 기능 비교 | 기준 / 후보 EVD ID |
|---|---|---|---|---|
| REQ-01 | PASS/PASS | PASS/PASS | NO_CHANGE / NO_CHANGE | EVD-01M5PYYE1705R2Z2T0TP9S03X0 / EVD-01M5PZ9QPTFT6DW73DTK9QG7JK |
| REQ-03 | PASS/PASS | PASS/PASS | NO_CHANGE / NO_CHANGE | EVD-01M5PZ20NCKCA98A8EQK40AYM6 / EVD-01M5PZD447Y3JVJMN34KSGD9G4 |
| REQ-09 | PASS/PASS | PASS/PASS | NO_CHANGE / NO_CHANGE | EVD-01M5PZ2CA1ZQWNP3E0HNP7Q7A2 / EVD-01M5PZDF7G0M4SDMK46C2YCAP2 |
| PLT-01 | PASS/PASS | PASS/PASS | NO_CHANGE / NO_CHANGE | EVD-01M5PZ2PYDJGMDHR0QAJWTBBE2 / EVD-01M5PZES2GSS9TH8CTYHZ0HAGN |
| PLT-04 | PASS/PASS | PASS/PASS | NO_CHANGE / NO_CHANGE | EVD-01M5PZ2YCA6ZCPD9PDRJ8XE2FJ / EVD-01M5PZEZWQ1WXG4C0H9M2FN5JK |

`guard update`는 종료0·828.407초, `python -m guard.update_validation`의 부모 프로세스는 종료0·856.331초다. 원본·복원 후 모두 `accepted=true`·`evidence_contract_valid=true`·`version_upgrade_verified=true`, 문제0개다. verify·반출·두 복원·저장 팩 재판정·보고는 모두 종료0, 원본25=복원25→재판정26객체, RES 전체와 비교 동일·원본 저장소·입력 불변을 확인했다.

실측 원본 보고서는 `<output>/update-run/updates/UPDATE-01M5PYP133CXCJCJ8RXVBE1N2B/report.json/html`의 `RPT-01M5PZF7MSSK4S0TTHNF5VWXBD`이고, 증거 반출본은 `<output>/update-run/evidence.zip`이다. 복원은 `restored/`·`replay-store/`, 저장 팩 재판정 보고서는 `replay-report.json/html`의 `RPT-01M5PZFQMG40D97QHMM011KK4C`다. `<output>/update-run/summary.json`에 수락·원본/재판정 참조와 파일 SHA가, `commands.json`에 명령·종료 코드가 있다. 제품 출처는 `<output>/source-release.zip`과 아래 최종 wheel이며 기존 실패 실증의 원본과 분리한다.

10개 EVD는 MEASURED, REQ-01/03의 RES는 DECLARED이며 나머지 RES는 MEASURED다. 정책은 EXAMPLE/DRAFT·팩22는 DRAFT이므로 official=false·배포 승인은 아니다. 양쪽 보안/기능 FAIL·UNKNOWN은 0개이나 성능은 모두 UNKNOWN이다. 비교는 전부 NO_CHANGE여서 실제 변화의 REGRESSED/FIXED 탐지는 이번 실증으로 입증되지 않았다. 이 update의 PLT-04는 `credential_candidate_paths=[]`인 환경변수/마운트 금지의 부분 범위이며, 비어 있지 않은 후보 네 사례는 위 후보 단독 시험이다. 실제 자격 내용·유효성·사용과 위험한 입력의 실제 소비·피해 센서는 미검증이다. REQ-01의 일부 관리 경로는 HTTP를 보내지 않고 TCP 차단을 근거로 삼는 기존 한계도 유지한다.

구현·테스트 코드·문서 초안 완료 뒤 frozen Python3.11.16으로 검증했다. 최종 Linux 기본 **2905통과·실패/오류/skip0·기존18제외·pytest226.54초(부모228.489초)**, Windows 집중151통과·3.18초(부모4.885초), wheel 제품94파일 소스/설치본 일치·설치 검증21.255초다. 최종 wheel SHA는 `9b2a29f3edd38979998007412dbd483cd43ef6ccb9c9d8da7e4d8e8f9c9eae73`다. 기존2845사례 삭제0·신규60·신규skip0, 기존 테스트 원문·판정기/팩/정책 등 보호129파일을 보존했다. 추가 native/loader 관측 조건 보강 전 측정은 최종 측정과 별도 보존한다. PACK21 원본과 PACK22의 후보 UNKNOWN은 각각 새 복원본에서 재판정해 RES10개/보고 의미를 보존했으며 이후 보강은 optional reader에만 적용됐다. `<output>/preservation-after.json`에서 기존5컨테이너·이미지·모델·입력·제품·팩·과거18557파일의 바이트/상태 보존과 세 소유 실행의 자원 잔여0을 확인했다. 이는 시작/종료 대조이며 완전한 중간 감사 추적이나 호스트 인증은 아니다.

새 이미지/모델 다운로드·기존 서비스 변경·운영 자격 내용/실사용·GPU/성능·다른 모델·전체77개 자동화·기업 승인은 이번 범위에 포함하지 않는다.

## REQ-09·PLT-04 판정 의미 보완 후보

**PR #30 당시 기록: 새 판정 계약의 두 이미지 실증은 수락 실패였다.** 두 실제 이미지 실행·반출/복원·저장 팩 재판정은 완료했지만 후보 REQ-09의 보안 UNKNOWN으로 `accepted=false`였다. `guard update`는 종료0·531.687초, 수락 harness는 종료1·558.134초다. 다섯 점검의 양쪽 보안/기능에 구체 PASS/FAIL을 요구하는 기존 기준을 충족하지 못했으며 판정·정책을 완화하지 않았다. PR #29는 main `641f12f`에 병합됐고 당시 계약은 PACK22/DRAFT·REQ-09 정의6·PLT-04 정의3이다. 이 절의 원본·미검증 범위는 당시 기록이며 위 후속의 새 원인 증거·수락 성공과 구분한다.

환경은 Windows 11·Docker Desktop Linux/WSL2 `6.18.33.2-microsoft-standard-WSL2`, Intel Core Ultra 7 255H·x86_64다. 양쪽 CPU2/threads2·memory4GiB/swap 포함5GiB·shm512MiB·float32·max_model_len128·KV64MiB·hardened/isolated 조건을 사용했다. 기존 OPT-125m revision `27dcfa74d334bc871f3234de431e71c6eeba5dd6`·inventory `b11bc2cafab45720afd3d2f19c8250352977bc216efb88fc0afb9d90edc2aea6`을 재사용했다. 이미지·모델을 새로 다운로드하지 않았다.

- 기준 API 0.31.0: `vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b`
- 후보 API 0.32.0: `vllm/vllm-openai-cpu@sha256:827ef38187f9b52f2bb838695ef815ec5b65f4bf3e4c061b002b2b0a19cb0701`

S/F는 보안/기능이다. 10개 EVD는 MEASURED이며 REQ-01/03의 Result 검증 수준은 DECLARED, 나머지는 MEASURED다. 정책은 EXAMPLE/DRAFT·팩은 DRAFT이므로 official=false·approval=null이며 성능은 모두 UNKNOWN이다.

| 점검 | 기준 S/F | 후보 S/F | 보안 / 기능 비교 | 기준 / 후보 EVD ID |
|---|---|---|---|---|
| REQ-01 | PASS/PASS | PASS/PASS | NO_CHANGE / NO_CHANGE | EVD-01M5PT0TN9TVZ0HQHCFTN7D0VA / EVD-01M5PT7ZG99N1DC4WMWDVMJJP7 |
| REQ-03 | PASS/PASS | PASS/PASS | NO_CHANGE / NO_CHANGE | EVD-01M5PT3W3XT4TP9ZTZR3K8Q2AJ / EVD-01M5PTB6B4Q3TX17S9HRV3WYXB |
| REQ-09 | PASS/PASS | UNKNOWN/PASS | UNKNOWN / NO_CHANGE | EVD-01M5PT41MXXH815JJ549VKM9AX / EVD-01M5PTBCKKX2FZ1HZWA1Y7Q9MJ |
| PLT-01 | PASS/PASS | PASS/PASS | NO_CHANGE / NO_CHANGE | EVD-01M5PT460N6WZCSQES0B6VS2RE / EVD-01M5PTBHYVZNWFCY655SAPS5SJ |
| PLT-04 | PASS/PASS | PASS/PASS | NO_CHANGE / NO_CHANGE | EVD-01M5PT4ABGVVBGZRPGN3AYY2X0 / EVD-01M5PTBRBGA60MJKGPHYP0YES4 |

**UNKNOWN의 원인:** 후보 REQ-09(EVD-01M5PTBCKKX2FZ1HZWA1Y7Q9MJ)의 token ID 거부는 verified지만 길이 거부는 `REJECTION_CAUSE_UNVERIFIED`다. 양쪽 길이 요청은 HTTP400이며 후보 본문은 `max_tokens must be at least 1, got 0`이었다. 실제 제출 `max_tokens=1`과 구별하고, 목표 길이 제한의 거부인지 다른 clamp 원인인지 확정하지 못했다. 정상 전후 대조·응답 본문·필수 관측은 모두 수집됐으므로 수집 실패가 아니라 검토한 요청/응답/소스 분기의 범위 한계다. 양쪽 실제 consumer/피해 센서는 없어 `challenge_consumption`의 두 case는 unknown, `confirmed_input_violations=[]`다. 기준의 PASS는 검토된 거부 근거에 의한 제한 판정이며 실제 소비 계측이 아니다. **보안·기능 FAIL은 0개**다. 마스킹 샘플은 정규화될 수 있으므로 원문과 구별하며 capture 메타데이터의 원본 SHA·바이트 수·파싱 상태를 함께 본다.

PLT-04는 새 EXAMPLE 정책의 `credential_candidate_paths: []`로 요청한 후보가 없는 환경변수/마운트 금지의 부분 범위만 시험했다. 양쪽 금지 마운트 위반은 없고 후보 계획은 []·결과는 {}다. 후보 파일의 존재·부재·읽기 권한을 실제 시험한 결과가 아니다. 기존 기업 정책/과거 POL에 빈 목록을 보충하거나 기업의 실제 후보 목록을 줄이지 않는다. 비어 있지 않은 후보의 동일 worker 주체·메타데이터 관측, 실제 자격 내용/유효성/사용·동적 provider는 이번 실측으로 검증하지 않았다.

verify·반출·두 복원·저장 팩 judge/report는 모두 종료0이다. 원본25=복원25객체·재판정26객체, 판정/비교 일치·원본 불변을 확인했다. `evidence_contract_valid=true`지만 후보 REQ-09의 구체 보안 판정이 없어 원본·재판정 수락은 false다. `<output>/run/summary.json`·`run-result-details.json`·원본 JSON/HTML·bundle에 근거가 있다. `preservation-after.json`에서 기존 컨테이너·이미지·모델·입력·과거 실증 보존과 두 실제 대상의 신원·소유 자원 정리를 확인했다. 이는 완전한 중간 감사 추적이나 호스트 인증은 아니다.

실측 소스는 `<output>/source-release.zip`과 `preservation-before-release.json`의 제품93파일 SHA로 식별한다. 이후 `vllm.py`의 소스 인용 좌표 두 문자열만 `89–97→89–96`, `93–101→93–100`으로 정정했다. 함수·요청·판정 동작과 원본 EVD는 그대로이며 `rejection-source-review.json`에 소스10파일의 SHA·행 범위를 대조했다. 최종 코드의 `source-verified.zip`·`full-linux-verified.xml`·`dist-verified` 검증과 이번 원 실측을 구분하며 정정 후 엔진 재실행으로 표시하지 않는다.

좌표 정정 후 최종 Linux/Python3.11.16 기본은 **2845통과·실패/오류/skip0·기존18제외·121.61초(부모122.859초)**다. `python -X utf8 -m pytest -q --basetemp=<output> --junitxml=<output>/full-linux-verified.xml --durations=50`으로 실행했고 기존2737사례 삭제0·신규108사례·신규skip0을 확인했다. wheel 제품93파일의 소스/설치본 일치와 오프라인 검증은 통과·8.038초이며 SHA256은 `60c2a538bf7d5c4d043203188d46290717d2fe1f5651fb379c95705367294872`다. 최종 코드의 과거 PACK21 복원·재판정은 11.453초·수락 통과, 원본4339파일·RES10개·보고 의미를 보존했다. 초기 집중/전체 실패에서 경로 목록 처리·수동 정책 상한·순수성 경계와 버전/카탈로그/과거 팩 fixture를 고쳤으며 기존 사례·불변식과 제외 조건을 완화하지 않았다. 좌표 정정 전 전체 2845통과·137.49초(부모138.293초)와 위 실제 엔진 실행은 별도 기록이다. 실제 변화의 REGRESSED/FIXED 탐지·후보 길이 거부 원인·위험한 소비/피해·비어 있지 않은 PLT-04 후보 접근·GPU/성능·다른 엔진/모델·77개 전체 자동화·기업 승인·호스트 인증은 미검증이다. REQ-01 일부 상태 변경 경로의 HTTP 미실행·TCP 차단 근거 범위도 유지한다.

PR #29 통합 당시 기록: PR #28에서 pinned CPU 이미지 0.31.0/0.32.0의 기존 다섯 점검 실행·수락·반출/복원·저장 팩 재판정을 통과했다. [실측 결과](#실측-결과--c-후속-2026-10-24)는 팩21을 사용한 해당 PR 구현의 실제 실행 기록이고, [v1 통합 후보 검증](#v1-통합-후보-검증)은 PR #24·#25·#28을 합친 당시 후보의 별도 검증 상태다. 이후 main 병합과 팩22의 판정 의미 보완·공식 승인을 구분한다.

도구 구현 당시 기록: 2026-10-24, Asia/Seoul. 출발점은 `codex/gua-automation-hardening`의 `f4541ecfdc843c474b4c0c2c7ead1399c606a9d6`이다. 당시 PR #19는 미병합 초안, 원격 main은 `24f7d90a2745c108d7d395e5082a3721a3198f0c`였다. 아래 구현 검증 절의 미실측·팩20·정책21점검/46경로는 그 시점의 기록이며 후속 성공을 취소하는 현행 안내가 아니다.

## 범위와 수락의 의미

고정 최소 범위는 REQ-01·REQ-03·PLT-01·PLT-04·REQ-09다. PR #29 통합 당시 범위는 팩21/DRAFT·활성77·자동연결11·정책22점검/49경로·카탈로그322행/614연결이었고 당시 통합은 새 정책·실행기·팩 revision·의존성·객체 종류를 추가하지 않았다. 현행 판정 의미 보완은 위 팩22·정책22점검/50경로·카탈로그323행/615연결을 따른다. REQ-11 rev3의 보안 UNKNOWN과 독립 FAIL 보존은 유지한다. REQ-15의 제한 수동 계약은 이미 등록됐지만 실제 캐시 실행기는 없다. REQ-10 하위 runner 소비, OBS-03 exception dump, REQ-11 독립 출력 귀속, 실제 캐시 자동화·GPU·성능은 이번 범위 밖이다.

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
