# byul platform

서빙엔진 보안 솔루션의 통합 웹 화면. update(격리 검증)와 runtime(탐지·차단)의 결과를 **읽기 전용**으로 한곳에서 보여주고, 기업 환경·정책을 **초안(DRAFT) 판본**으로 저장한다. 작업 지시는 `PROMPT.md`, 데이터 형식은 `../contracts/README.md`를 따른다.

```
platform/
├─ api/   FastAPI (Python 3.12, uv) — 계약 검증, runtime·update 어댑터, 저장소, 정적 화면 서빙
└─ web/   React + TypeScript + Vite — 9개 탭 화면
```

## 실행

```sh
# 1. 화면 빌드 (Node는 빌드할 때만 쓴다. 운영에는 Node 프로세스가 없다)
cd platform/web
npm install
npm run build              # → web/dist

# 2. API + 화면
cd ../api
uv sync
uv run byul-platform       # http://127.0.0.1:8700
```

- 서버는 `127.0.0.1`(루프백)에만 연다. 로그인이 없으므로 외부 주소를 주면 시작하지 않는다.
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
| `PLATFORM_DATA_DIR` | 환경·정책 저장 폴더. `<kind>/<id>/<판본 6자리>.json`, 판본마다 새 파일(덮어쓰기 금지) | 임시 폴더에 예시 1판본, 종료 시 삭제 |
| `EVIDENCE_INDEX_PATHS` | `evidence-index` 파일 경로(`:`로 여러 개) | 예시 모드 |
| `BYUL_PLATFORM_PORT` | 포트 | `8700` |

## 예시 모드

위 출처 환경변수가 없으면 그 출처는 `../contracts/examples/`를 읽는다(전부 `EXAMPLE` 등급). 하나라도 예시면 `/api/meta`의 `example_mode`가 `true`이고 화면 위에 "예시 데이터" 띠가 붙는다. 사이드바 아래에 출처별 모드(예시·실제 연결·파일)와 runtime 연결 상태가 보인다.

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
| 환경 / 정책 | `/environments`, `/policies`, `…/new`, `…/:id` | 플랫폼 저장소. 구역별 폼, 서버 스키마 검증 오류를 필드 옆에 표시, 판본 이력과 diff |
| 검증 결과 | `/update/results` | update RPT → `verification-result` |
| 재검증 요청 | `/update/revalidation` | runtime `/api/revalidation` → `revalidation-request` (시작 버튼 없음) |
| 요청 내역 | `/runtime/requests` | runtime 요청 기록 → `runtime-event`(source=gateway) |
| 사건 로그 | `/runtime/events` | Gateway·Hubble·Tetragon 기록 → `runtime-event`. 왼쪽 탐색·가운데 목록·오른쪽 상세(개요·원본·연결) |
| 상태 | `/runtime/status` | `runtime-status`, 구성 비교(`/api/baseline`), 대상 비교(`/api/target`) |
| 증거 목록 | `/evidence` | `evidence-index` |

기간·환경·필터·열린 상세는 모두 쿼리스트링에 있어 새로고침해도 같은 화면이 나온다. 상단바 환경 선택은 그 환경의 `target_ref`로 runtime 자료를 거른다.

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
cd api && uv run --with ../../update pytest  # 57개: 계약 예시 검증, 변환 표, Hubble≠거부 확인, null 보존, 판본 덮어쓰기 금지, GET 전용 클라이언트, 토큰 비노출 …
cd web && npm test             # 18개: 배지 규칙, null 표시, Hubble 행, 형식 오류 행, 연결 끊김 화면, 화면이 GET만 보냄 …
```

update 통합 시험에는 `guard`를 제공하는 update 패키지 overlay가 필요하다.

`api/tests/fixtures/runtime/`은 runtime을 모의 엔진으로 띄워 runtime 자체 시연 도구(`mvp-demo`)로 기록을 만든 뒤 GET 응답을 저장한 것이다. `fixtures/update/`는 `update/out/report.json` 사본이다.

## 이번에 하지 않은 것

로그인·권한, 정책 승인(CONFIRMED 전환), 번들 발행·runtime 배포, 재검증 시작·시험 실행 버튼, 외부 검색 엔진 연동, 알림.
