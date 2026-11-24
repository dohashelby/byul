# byul platform

서빙엔진 보안 솔루션의 통합 웹 화면. update(격리 검증)의 실행·결과 조회와 runtime(탐지·차단)의 결과를 한곳에서 보여주고, 기업 환경·정책을 **초안(DRAFT) 판본**으로 저장한다. 초기 구현 지시는 `PROMPT.md`, 데이터 형식은 `../contracts/README.md`를 따른다.

```
platform/
├─ api/   FastAPI (Python 3.12, uv) — 계약 검증, runtime·update 어댑터, 저장소, 정적 화면 서빙
└─ web/   React + TypeScript + Vite — 9개 탭 화면
```

## 실행

저장소 루트에서 같은 커밋의 소스, Python 3.12, `uv`, Node로 준비한다.

```sh
(cd update && uv sync --frozen)
(cd platform/web && npm ci && npm run build)
(cd platform/api && uv sync --frozen && uv run --frozen --with-editable ../../update byul-platform)
```

- 서버는 기본적으로 로그인과 CSRF 검사를 사용하며 `127.0.0.1:8700`에서 시작한다. 고객 인수에서는 초기 관리자 비밀번호 변경과 별도 영속 데이터 디렉터리를 준비한다.
- API에 등록 입력 변환용 Guard가 연결되어야 하며, 별도 실행용 Python에서도 같은 Guard 소스를 찾을 수 있어야 한다. 설치 환경을 변경하면 서버를 다시 시작한다.
- `web/dist`가 없으면 `/api/*` 밖의 경로는 503 `WEB_NOT_BUILT`를 돌려준다.
- 화면 개발: API를 띄운 채 `cd web && npm run dev` (Vite가 `/api`를 8700으로 넘긴다).

### runtime과 함께 실행

runtime Gateway는 자체 화면이 없다(2026-10-25 제거). 화면은 이 platform 하나다.

```sh
platform/scripts/run-with-runtime.sh               # runtime :8080(모의 엔진) + platform :8700
RUNTIME_DEMO=1 platform/scripts/run-with-runtime.sh  # runtime 시연 요청과 저장소의 합성 관측 자료도 넣는다
```

기존 시험 기록은 있으면 자동 연결한다: runtime 기본 기록 폴더(`~/.local/state/serving-runtime/mvp`), kind lab 보고서 폴더(`~/.local/state/serving-runtime/k8s-lab/reports`). 다른 위치면 `RUNTIME_DATA_DIR`·`RUNTIME_LAB_REPORT`, Cilium 상태는 `RUNTIME_CILIUM=1`. runtime 화면은 삭제된 runtime 대시보드와 같은 구성·문구이며 3초마다 갱신한다.

update는 자체 화면(HTML 보고서)이 없다(2026-10-25 제거). 실행 스크립트는 `update/out`의 RPT JSON을 기본으로 읽고(`UPDATE_REPORTS_DIR`로 바꿈), 검증 결과 탭이 update 보고서의 모든 내용(종합 판정·주요 결과·영역/보호 목적·증거 출처·한계·전체 표·수동 시험·미선정·승인/입력 목록)을 보여준다.

runtime 기록은 `platform/.runtime-data`에 쌓인다(git 제외). 조회 보호를 켠 runtime이면 `RUNTIME_VIEWER_TOKEN`을 함께 넘긴다.

## 환경변수

| 이름 | 뜻 | 없을 때 |
| --- | --- | --- |
| `RUNTIME_BASE_URL` | runtime 조회 API 주소(예: `http://127.0.0.1:8080`). **GET `/api/*`만** 부른다 | 예시 모드 |
| `RUNTIME_VIEWER_TOKEN` | runtime 조회 토큰(`Authorization: Bearer`). 로그·응답·화면에 내지 않는다 | 토큰 없이 조회 |
| `UPDATE_REPORTS_DIR` | update RPT JSON 폴더. 하위 폴더까지 `kind: RPT`인 JSON을 읽는다(예: `update/out`) | 예시 모드 |
| `BYUL_UPDATE_DIR` | 검증 실행에 사용할 update 작업 폴더. Pack·입력 변환 도구·TOML 내부 상대 경로의 기준 | 저장소의 `update/` |
| `BYUL_UPDATE_PYTHON` | guard를 설치한 Python 실행 파일. CLI를 셸 없이 별도 프로세스로 실행한다 | `BYUL_UPDATE_DIR/.venv/bin/python` |
| `BYUL_UPDATE_CONFIG` | 서버 관리자가 준비한 검증 실행 TOML. 브라우저에서 선택·변경하지 않는다. 상대 경로는 update 폴더 기준 | `examples/gpu-bounded.toml` |
| `BYUL_UPDATE_REQUIRED_CHECKS` | 양쪽 또는 단일 환경에서 지원을 확인할 추가 필수 점검 ID를 쉼표로 나열 | 준비 결과가 지정한 선택 범위 |
| `PLATFORM_DATA_DIR` | 환경·정책 저장 폴더. `<kind>/<id>/<판본 6자리>.json`, 판본마다 새 파일(덮어쓰기 금지) | 임시 폴더에 예시 1판본, 종료 시 삭제 |
| `EVIDENCE_INDEX_PATHS` | `evidence-index` 파일 경로(`:`로 여러 개) | 예시 모드 |
| `BYUL_PLATFORM_PORT` | 포트 | `8700` |
| `BYUL_ACCOUNTS_FILE` | 로그인 계정 파일(0600). 처음 실행 때 `admin`/`admin`을 만든다 | `PLATFORM_DATA_DIR/accounts/accounts.json`, 그것도 없으면 임시 폴더(종료 시 삭제) |
| `BYUL_REQUIRE_PASSWORD_CHANGE` | `1`이면 변경이 필요한 계정은 비밀번호를 바꾸기 전에 다른 API를 못 쓴다 | 끔(개발 중) |
| `BYUL_LOGIN_DISABLED` | `1`이면 로그인을 끈다(이전 동작, 개발용) | 로그인 켬 |
| `BYUL_SESSION_IDLE_SECONDS` / `BYUL_SESSION_MAX_SECONDS` | 세션 미사용 만료 / 최대 유지 | 7200 / 43200 |
| `BYUL_COOKIE_SECURE` | `1`이면 세션 쿠키에 Secure를 항상 붙인다(HTTPS 앞단 프록시 뒤) | HTTPS 요청일 때만 |

## 로그인

회원가입·비밀번호 찾기는 없다. 관리자가 명령어로 계정을 발급하고, 화면에는 로그인만 있다. 자세한 절차는 [로그인과 계정 관리](docs/로그인_계정관리.md).

| 역할 | 할 수 있는 일 |
| --- | --- |
| `admin` | 모든 조회, 운영 요청 목록 보기, 계정 관리(명령어) |
| `approver` | 조회 + 운영 요청 승인 |
| `runtime_operator` | 조회 + 운영 요청 |
| `viewer` | 조회만 |
| `agent` | 사람 로그인 불가. 서버 에이전트는 기존 Bearer 토큰(`BYUL_OPERATION_TOKENS`)을 그대로 쓴다 |

## 예시 모드

위 출처 환경변수가 없으면 해당 출처는 `../contracts/examples/`를 읽는다(전부 `EXAMPLE` 등급). 하나라도 예시면 `/api/meta`의 `example_mode`가 `true`다. 첫 화면과 Runtime 영역에는 예시인 출처 이름을 표시하며, Runtime 사이드바 아래에 출처별 모드와 연결 상태를 보여준다. 격리 검증 결과에는 보고서 자료 등급과 공식 상태, 측별 실행 상태·증거 등급·원본 한계를 표시한다. 실제 관측을 설명하는 문구는 완료된 `MEASURED` 증거를 사용한다.

### 실제 반출 증거 연결

runtime의 오프라인 [증거 목록 생성 도구와 운영 절차](../runtime/docs/증거목록_생성과플랫폼연결.md)로 만든 목록을 연결한다. AWS 접속 없이 tgz/폴더의 해시·비밀 검사·계약 검증을 수행한다.

```sh
cd /home/bobgy/bobproject/byul/platform/api
EVIDENCE_INDEX_PATHS=/tmp/byul-evidence-run12-run13/evidence-index.json uv run byul-platform
# 여러 목록은 ':'로 연결한다. 기존 개발 스크립트도 같은 환경변수를 받는다.
```

계약 항목 경로는 index 옆 `files/` 기준이다. 실제 파일 출처는 regular file 존재·경계·링크를 검사한다. 목록당 최대 32 MiB이며 파일 내용은 목록 조회 시 읽지 않는다. evidence 연결 후에도 runtime/update/store가 예시이면 그 이름은 배너에 남고 개별 `EXAMPLE` 등급도 유지한다.

## 데모 자료 (그래프 확인용 가짜 기록)

```sh
cd platform/api
uv run python -m byul_platform.demo                       # → platform/.demo-data (지난 24시간, 요청 약 600건)
BYUL_EXAMPLES_DIR=../.demo-data uv run byul-platform
```

`--hours`, `--requests`, `--seed`로 양과 구간을 바꾼다. 전부 `EXAMPLE`/`synthetic`이고 계약 스키마 검증을 통과해야 만들어진다. runtime 사건은 runtime 내부 기록 모양으로 만든 뒤 실제 변환 코드로 바꾸므로 변환 규칙(Hubble≠거부 확인 등)을 그대로 따른다. `platform/.demo-data`는 git에 넣지 않는다.

## 화면

디자인은 serving-runtime 대시보드(`src/serving_runtime/mvp/static/`, 커밋 `ea3a78c`)를 그대로 따른다: 보라 강조색 하나와 회색 단계, 빨강·주황은 오류·경고에만, 얇은 테두리의 평평한 카드, 그룹으로 나눈 사이드바, 범위 칩이 있는 상단바, 큰 숫자 요약 카드, 탭이 있는 그래프 카드, 오른쪽에서 열리는 상세 드로어, OS를 따르고 선택을 기억하는 밝은·어두운 모드. 색 토큰은 `web/src/styles/tokens.css` 한 곳에 있다.

| 탭 | 주소 | 출처 |
| --- | --- | --- |
| 대시보드 | `/dashboard` | 요청 집계(runtime `/api/summary`), 정책·수집기 상태, 최신 검증 결과, 열린 재검증 수, 시간대별 요청·사건 그래프 |
| 환경 / 정책 | `/environments`, `/policies`, `…/new`, `…/:id` | 플랫폼 저장소. 구역별 폼, 서버 스키마 검증 오류를 필드 옆에 표시, 판본 이력과 diff. 프레임워크 입력(환경 12개 조건·기능 사용 여부와 비사용 근거, 보호 요구 36개·승인 조건)은 선택형 질문으로 받고 `web/src/lib/frameworkCatalog.ts`가 항목을 정한다 |
| 검증 결과 | `/update/results` | update RPT → `verification-result` |
| 검증 실행 | `/update/execute` | 기존 `guard prepare-validation/validate`, `guard prepare-update/update` 실행·상태·로그·보고서 연결 |
| 재검증 요청 | `/runtime/revalidation` (이전 `/update/revalidation`) | runtime `/api/revalidation` → `revalidation-request` (시작 버튼 없음) |
| 요청 내역 | `/runtime/requests` | runtime 요청 기록 → `runtime-event`(source=gateway) |
| 사건 로그 | `/runtime/events` | Gateway·Hubble·Tetragon 기록 → `runtime-event`. 왼쪽 탐색·가운데 목록·오른쪽 상세(개요·원본·연결) |
| 상태 | `/runtime/status` | `runtime-status`, 구성 비교(`/api/baseline`), 대상 비교(`/api/target`) |
| 증거 목록 | `/evidence` | `evidence-index` |

기간·환경·필터·열린 상세는 모두 쿼리스트링에 있어 새로고침해도 같은 화면이 나온다. 상단바 환경 선택은 그 환경의 `target_ref`로 runtime 자료를 거른다.

### 웹에서 검증 실행

Update 사이드바의 **검증 → 검증 실행**에서 단일 환경 검증 또는 두 판본 비교를 선택한다. 플랫폼에 등록한 정책·환경과 각각의 저장 판본을 선택한 뒤 **준비 점검 → 검증 실행** 순서로 진행한다. 정책이 참조하는 정확한 환경 판본을 기본 선택한다. 두 판본 비교는 기준·후보 환경의 서로 다른 판본을 선택하고, 기준 환경에 연결된 정책 하나를 공통 기준으로 사용한다. 다른 대상에 연결된 후보 환경이나 정책의 환경 참조와 맞지 않는 기준 판본은 거부한다.

Pack과 TOML은 서버에서 결정하며 화면에 입력란을 두지 않는다. Pack은 저장된 정책의 `verification.pack`을 따르고 서버의 `update/packs/<id>`와 정확한 판본이 일치해야 한다. 다르면 정책이나 Pack을 자동으로 올리지 않고 원인을 표시한다. 실행 TOML은 서버 관리자가 `BYUL_UPDATE_CONFIG`로 설정한다. 기본 예제는 실제 모델 경로·실행 설정으로 준비해야 한다. 브라우저 요청은 문서 ID·저장 판본만 받으며 파일 경로·명령 문자열·임의 CLI 옵션은 거부한다.

Docker·GPU·모델·대상 커넥터·키 환경변수는 CLI를 실행할 때와 같이 **플랫폼 서버**에서 사용할 수 있어야 한다. 기존 `update/tools/import_byul_inputs.py`의 `/2` 변환을 사용하고, 후보 환경은 정책 참조를 변경하지 않고 동일한 ENV 필드 매핑만 적용한다.

로그인 사용 시 관리자가 준비 점검·실행하고 다른 역할은 기록을 조회한다. 기존 세션·CSRF 검사를 적용한다. 준비 점검은 엔진을 기동하지 않고 측·점검별 지원 결과를 표시한다. 실행 직전에는 준비 상태, 필수 점검 지원, 선택한 판본 해시, 정책 유효 기간을 다시 검사한다. 저장된 원본 문서의 정규 JSON, 정확한 문서 Ref, 변환 결과·매핑, Pack·설정 복사본을 고정한다. TOML의 직접 참조 파일도 지문에 포함하며 기준 종료 후에만 확정할 수 있는 후보 대상은 Guard 전환 단계에서 결합한다. 입력이 달라지면 다시 준비 점검을 요청한다. 기존 파일 입력 실행 이력은 조회할 수 있다.

실행은 백그라운드에서 진행하며 같은 저장 경로의 프로세스들은 잠금을 공유한다. Guard 자식 프로세스도 잠금을 이어받으며 정상 서버 종료는 정리가 끝날 때까지 기다린다. 비정상 종료 뒤에는 보존한 실행 종료 코드와 유효한 RPT로 복구할 수 있는 기록을 회수한다. 종료 확인이 없는 기록은 `interrupted`로 남긴다.

기록·로그·고정 입력·보고서·Guard 객체는 `PLATFORM_DATA_DIR/update-runs/`에 저장한다. `PLATFORM_DATA_DIR`이 없으면 임시 저장하고 서버 종료 시 삭제한다. 로그는 비밀값을 가리고 마지막 64 KiB를 표시한다. 생성한 RPT는 `UPDATE_REPORTS_DIR`의 기존 보고서와 함께 검증 결과에서 조회한다. 상세 링크는 RPT의 ID, 판본, SHA-256을 지정한다. 인수 시 그 링크를 열어 원본 정책 판본·양측 자료 등급과 판정을 확인한 뒤 서버를 다시 시작해 같은 Ref를 확인한다.

## 지키는 것

- 화면에 내보내는 계약 문서는 모두 `../contracts/schemas`로 검증한다. 검증에 실패한 항목은 버리지 않고 "형식 오류"로 따로 보여준다.
- runtime·update 내부 형식은 `api/src/byul_platform/sources/`에만 있다. 변환은 contracts README "변환 규칙" 표를 따른다(Hubble은 `DROPPED`도 `observed`, 표에 없는 Tetragon 조합은 `unknown`).
- 판정·집계는 update 값을 그대로 옮긴다. 지연시간·토큰 수·비용·심각도처럼 원본에 없는 값은 없다. `null`은 "–"로 보인다.
- 정책은 `DRAFT`로만 저장한다(`CONFIRMED`는 422). 판본 해시는 정규 JSON(정렬 키·UTF-8·공백 없음) SHA-256.

## 타입

화면 응답 타입은 손으로 쓰지 않는다. FastAPI OpenAPI 문서에 계약 스키마를 components로 넣고 거기서 만든다.

```sh
cd web && npm run gen:types    # api의 OpenAPI → src/api/openapi.json → src/api/schema.d.ts
```

## 시험

```sh
cd api && uv run --with ../../update pytest  # 96개: 계약 예시 검증, 변환 표, Hubble≠거부 확인, null 보존, 판본 덮어쓰기 금지, GET 전용 클라이언트, 토큰 비노출 …
cd web && npm test             # 21개: 배지 규칙, null 표시, Hubble 행, 형식 오류 행, 연결 끊김 화면, 화면이 GET만 보냄 …
```

update 통합 시험에는 `guard`를 제공하는 update 패키지 overlay가 필요하다.

`api/tests/fixtures/runtime/`은 runtime을 모의 엔진으로 띄워 runtime 자체 시연 도구(`mvp-demo`)로 기록을 만든 뒤 GET 응답을 저장한 것이다. `fixtures/update/`는 `update/out/report.json` 사본이다.

## 이번에 하지 않은 것

재검증 요청의 자동 실행 연결, 외부 검색 엔진 연동, 알림.
