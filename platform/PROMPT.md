# 통합 플랫폼 1차 구현 프롬프트

> 이 문서는 초기 구현 지시의 기록이다. 2026-11-15 사용자 요청으로 platform의 Update 검증 실행을 확장했다. 해당 기능은 기존 `guard prepare-validation/validate`·`guard prepare-update/update`를 호출하며, 아래의 초기 범위에서 제외한 update 실행 버튼에 대한 제한을 대체한다. runtime 조회 클라이언트·공용 계약·update 판정 구현의 경계는 유지한다. 현재 사용법은 `README.md`의 “웹에서 검증 실행”을 따른다.
> 같은 날 후속 요청으로 등록한 환경·정책의 저장 판본을 선택하도록 변경했다. Pack은 정책에 고정된 판본을 서버에서 확인하고, TOML은 서버 설정으로 관리한다. 브라우저에는 파일 경로·Pack·TOML 입력을 노출하지 않는다.

이 문서를 AI 코딩 에이전트에게 그대로 준다. 작업 위치는 byul 저장소의 `platform/` 폴더다.

---

## 0. 목표

서빙엔진 보안 솔루션의 **통합 웹 화면**을 만든다. 왼쪽 사이드바에 기능별 탭이 있고, 탭을 누르면 오른쪽 본문이 그 화면으로 바뀐다. 1차 목표는 다음 두 가지다.

1. update(격리 검증)와 runtime(탐지·차단)의 결과를 **읽기 전용**으로 한곳에서 보여준다.
2. 기업 환경·정책을 입력해 **초안(DRAFT)으로 저장**한다. runtime에 배포하는 기능은 이번에 만들지 않는다.

## 1. 먼저 읽을 것

| 파일 | 왜 |
| --- | --- |
| `../contracts/README.md` | 세 프로젝트가 주고받는 데이터 형식과 원칙. **이 화면이 읽고 쓰는 데이터는 전부 이 계약을 따른다** |
| `../contracts/schemas/*.schema.json` | 입력 검증과 TypeScript 타입의 원천 |
| `../contracts/examples/` | 개발용 예시 데이터. 전부 `EXAMPLE` 등급 |
| `../runtime/docs/내부_기록형식.md` §2·§9 | runtime 조회 API(`/api/*`)가 돌려주는 필드의 의미 |
| `../update/SPEC.md` §2.4·§4.6·§4.7 | 판정 enum, 결과(RES)·보고서(RPT)의 의미와 집계 규칙 |

`../update/`, `../runtime/`, `../contracts/`의 파일은 **수정하지 않는다.** 필요한 변경이 있으면 멈추고 묻는다.

## 2. 기술 스택과 폴더

- **화면:** React + TypeScript + Vite. 정적 파일로 빌드한다. Next.js와 Node 서버는 쓰지 않는다.
- **API:** Python 3.12 + FastAPI + pydantic v2, `uv`로 관리한다. API 서버가 빌드된 정적 파일도 서빙한다. 운영에 Node 프로세스는 없다.
- **타입:** FastAPI가 만든 OpenAPI 문서에서 TypeScript 타입을 생성해 쓴다(`openapi-typescript`). 화면 코드에 응답 타입을 손으로 다시 쓰지 않는다.
- **의존성:** 아래 목록 밖의 라이브러리를 추가하려면 먼저 묻는다.
  - Python: `fastapi`, `uvicorn`, `pydantic`, `httpx`, `jsonschema`, `pytest`
  - Web: `react`, `react-dom`, `react-router-dom`, `@tanstack/react-query`, `recharts`, `openapi-typescript`, `vite`, `vitest`, `@testing-library/react`
  - CSS는 CSS 변수와 CSS Modules로 직접 쓴다. UI 키트는 쓰지 않는다.

```
platform/
├─ api/                      # FastAPI
│  ├─ pyproject.toml
│  ├─ src/byul_platform/
│  │  ├─ main.py             # 앱, 라우터 등록, 정적 파일 서빙
│  │  ├─ config.py           # 환경변수 설정
│  │  ├─ contracts.py        # ../contracts/schemas 로딩·검증, 정규 JSON 해시
│  │  ├─ store.py            # 환경·정책 저장소 인터페이스 + 파일 구현
│  │  ├─ sources/
│  │  │  ├─ runtime.py       # runtime 조회 API 읽기 전용 클라이언트
│  │  │  └─ update.py        # update 보고서 JSON 읽기 → verification-result 변환
│  │  └─ routes/             # overview, environments, policies, verification, runtime, evidence
│  └─ tests/
└─ web/                      # React + Vite
   ├─ package.json
   ├─ src/
   │  ├─ app/                # 레이아웃(사이드바+상단바), 라우터
   │  ├─ pages/              # 탭별 화면
   │  ├─ components/         # 표, 상세 패널, 지표 카드, 상태 배지, 필터 바
   │  ├─ api/                # 생성된 타입 + 조회 훅
   │  └─ styles/             # 색·간격 토큰
   └─ tests/
```

## 3. 화면 구성

### 3.1 전체 틀 — 레퍼런스: Langfuse

**레퍼런스 화면:** [Langfuse 대시보드](https://clickhouse.com/blog/the-agentic-data-stack)

- **왼쪽 사이드바:** 너비 고정(약 240px). 그룹 제목 아래에 탭을 둔다. 지금 탭은 강조 표시한다. 아래쪽에 연결 상태(runtime 연결됨/끊김, 데이터 등급)를 작게 보여준다.
- **상단바:** 지금 화면 이름, 기간 선택(1시간, 24시간, 7일, 직접 지정), 환경 선택(`environment_id`), 새로고침 시각.
- **본문:** 위에 요약 지표 카드 줄, 아래에 그래프와 표.
- **주소:** 탭마다 URL을 둔다(`/dashboard`, `/runtime/requests` 등). 새로고침해도 같은 화면이 나온다. 선택한 기간과 필터도 쿼리스트링에 둔다.

사이드바 탭:

```
개요
  대시보드               /dashboard
설정
  환경                   /environments
  정책                   /policies
Update (격리 검증)
  검증 결과              /update/results
  재검증 요청            /update/revalidation
Runtime (탐지·차단)
  요청 내역              /runtime/requests
  사건 로그              /runtime/events
  상태                   /runtime/status
증거
  증거 목록              /evidence
```

### 3.2 대시보드 — 레퍼런스: Langfuse 홈

첫 화면에서 전체 현황을 보여준다.

- **지표 카드(위 한 줄):**
  - runtime 요청 수와 결과별 수(허용·거부·입력 오류·엔진 거부·오류·미완료). 출처는 `/api/summary`.
  - 정책 상태: 적용 중인 `policy_ref`, 승인 상태, 만료 시각
  - 수집기 상태: Hubble·Tetragon 각각 `ok`/`stale`/`error`/`disconnected`
  - 최신 검증 결과: 영역별(SUP·REQ·PLT·LIF·OBS) 보안 판정
  - 열린 재검증 요청 수
- **그래프(아래):**
  - 시간대별 요청 수를 결과별로 쌓은 막대그래프. 기록 시각(`recorded_at`)을 기간에 맞춰 묶는다.
  - 시간대별 runtime 사건 수(`detected`, `enforce_attempted`, `denied` 구분)
- 카드와 그래프를 누르면 해당 탭으로 필터를 건 채 이동한다.

### 3.3 요청 내역 — 레퍼런스: Helicone

**레퍼런스 화면:** [Helicone 요청 화면](https://www.helicone.ai/changelog/20241003-request-redesign)

- **위:** 검색창(request_id, 모델), 필터 버튼(결과, 모델, 사유 코드, 스트림 여부, 환경, run_id), 저장된 필터.
- **본문:** 요청 표. 열은 다음과 같다.
  - 기록 시각, 결과 배지, 모델, `max_tokens`, 사유 코드(`reason_code`), 클라이언트 HTTP 상태, 엔진 HTTP 상태, 스트림 여부, 전달 시도 여부, 백엔드 종류, 환경, run_id
- **지연시간·토큰 수·비용 열은 만들지 않는다.** runtime이 이 값을 기록하지 않는다. 없는 값을 계산하거나 채우지 않는다.
- 열 정렬, 열 숨기기, 페이지 크기 선택, 페이지 이동을 지원한다.
- 행을 누르면 오른쪽에 상세 패널이 열린다(3.4와 같은 형태). 내용은 `/api/requests/{id}`와 `/evidence`.

### 3.4 사건 로그 — 레퍼런스: Better Stack

**레퍼런스 화면:** [Better Stack 로그 화면](https://betterstack.com/community/guides/logging/go/zap/)

**왼쪽 탐색 → 가운데 목록 → 오른쪽 상세**의 3단 구성이다. 지금처럼 오류를 작은 박스에 몰아넣지 않고, 목록에서 골라 자세히 본다.

- **위:** 시간대별 사건 수 막대그래프(결과별 색). 그래프 구간을 드래그하면 그 구간으로 필터가 걸린다.
- **가운데 목록:** `runtime-event` 한 줄이 한 행이다. 열은 발생 시각, 출처(gateway/hubble/tetragon…), 분류(request/network/process/file…), 결과 배지, 규칙(`rule_ref`), 단계(`phase`), 대상. 결과 배지 색은 3.9의 규칙을 따른다.
- **오른쪽 상세 패널:** 탭 3개를 둔다.
  - **개요:** 공통 필드를 표로(시각 두 개, 출처, enforcer, 정책 판본, scope의 연결 키, 자료 등급과 `origin_detail`)
  - **원본:** `native.record`를 접고 펼 수 있는 JSON 보기로. 줄 번호, 복사 버튼을 둔다. 값은 텍스트로만 넣는다(HTML로 해석하지 않는다).
  - **연결:** 같은 `request_id`, 같은 `run_id`, 같은 `policy_ref`의 다른 사건·요청·검증 결과로 가는 링크
- 상세 패널은 닫을 수 있고, 열린 사건 ID는 URL에 남는다.

### 3.5 Runtime 상태

`runtime-status`를 보여준다.

- 대상: `target_ref`, 등록 상태, 인스턴스 ID와 상태
- 정책: 적용 중인 판본, 승인 상태, 유효 기간, 번들 ID
- 수집기: 출처별 상태와 마지막 성공 시각. `stale`·`error`·`disconnected`는 눈에 띄게 표시한다.
- 저장소 상태
- 구성 비교(`/api/baseline`)와 대상 비교(`/api/target`): 필드별 기대값·관측값·상태(`match`/`mismatch`/`unknown`/`not_declared`) 표

### 3.6 검증 결과(Update)

`verification-result`를 보여준다. 표는 3.3, 상세는 3.4와 같은 형태를 쓴다.

- **위:** 보고서 선택(생성 시각·모드·자료 등급), 영역별 판정 카드, 수동/자동 증거 수, `limitations` 목록(접을 수 있게)
- **표:** 점검 하나가 한 행. 열은 점검 ID, 적용 여부, 선정 여부, side별 보안·기능·성능 판정, 비교 결과(update 모드), 공백(`gaps`), 증거 출처, 예외 승인 표시
- **상세 패널:** 판정 근거와 원본 RPT·RES 참조. 원본에 없는 설명을 덧붙이지 않는다.
- 팩이나 정책이 `DRAFT`이거나 자료가 `EXAMPLE`이면 화면 위에 "공식 결과 아님" 띠를 고정으로 띄운다.

### 3.7 재검증 요청

`revalidation-request` 목록. 트리거, 대상, 정책 판본, 바뀐 필드(기대값→관측값), 상태, 대체 관계를 보여준다. **"재검증 시작" 버튼은 만들지 않는다.** 다음 단계에서 정한다.

### 3.8 환경·정책 입력

- **목록:** 환경·정책별 최신 판본, 상태(DRAFT/CONFIRMED), 수정 시각
- **편집 화면:** 계약 스키마(`environment`, `policy`)의 필드를 구역별 폼으로 나눈다.
  - 환경: 대상, 엔진, 모델, 실행 자원, 배치, 사실(`facts`)
  - 정책: 기본 정보·유효 기간, 검증 정책(`verification`), Gateway 정책(`runtime.gateway`), 집행 의도(`runtime.enforcement`, JSON 편집기), exec 예외
- `facts`의 각 값에는 값·출처 등급·근거를 함께 입력한다. 값을 `false`로 둘 때 "비사용 근거" 칸이 비어 있으면 경고한다(계약 원칙).
- 저장할 때 서버가 JSON Schema로 검증한다. 오류는 필드 옆에 보여준다.
- **저장은 항상 새 판본(revision)이다.** 기존 판본을 덮어쓰지 않는다. 판본 사이의 차이(diff)를 볼 수 있게 한다.
- 이번 단계에서는 `DRAFT`로만 저장한다. `CONFIRMED` 전환과 정책 번들 발행은 다음 단계다.

### 3.9 공통 표시 규칙

- **결과 배지 색:** 허용(`allowed`)=중립, 거부 확인(`denied`)=초록 계열(막았다는 뜻), 탐지(`detected`)=주황, 집행 시도(`enforce_attempted`)=노랑, 오류(`error`)=빨강, 미확인(`unknown`)=회색. **탐지와 집행 시도를 "차단됨"으로 표시하지 않는다.**
- **판정 배지:** PASS=초록, FAIL=빨강, UNKNOWN=회색, NOT_APPLICABLE=흐린 회색. UNKNOWN을 PASS처럼 보이게 하지 않는다.
- **자료 등급:** `MEASURED`/`DECLARED`/`EXAMPLE` 배지를 값 옆에 붙인다. `EXAMPLE`은 항상 눈에 띄게 표시한다.
- **값 없음:** `null`은 "–"로, 미수집은 "미수집"으로 표시한다. 0이나 빈칸으로 바꾸지 않는다.
- **한국어 표시:** 데이터의 영문 enum은 화면에서만 한국어로 바꾼다. 변환표는 한 파일에 모은다.
- **시각:** 사용자의 시간대로 보여주고, 마우스를 올리면 원래 UTC 값을 보여준다.
- **테마:** 밝은 테마와 어두운 테마를 모두 지원한다. 색은 CSS 변수로만 정한다. 레퍼런스 3개는 어두운 테마가 기본이다.
- **빈 화면·오류 화면:** 데이터가 없을 때, 연결이 끊겼을 때, 스키마 검증에 실패했을 때를 각각 다른 문구로 보여준다.

## 4. 데이터 출처와 API

| 화면 | 1차 출처 | API |
| --- | --- | --- |
| 요청 내역, Runtime 상태 | runtime 조회 API(`RUNTIME_BASE_URL`, 조회 토큰 `RUNTIME_VIEWER_TOKEN`)를 **GET으로만** 호출 | `GET /api/runtime/requests`, `/requests/{id}`, `/status`, `/summary` … |
| 사건 로그 | runtime 기록을 `runtime-event`로 변환. 변환 규칙은 contracts README의 "변환 규칙" 표 | `GET /api/runtime/events` |
| 검증 결과 | `UPDATE_REPORTS_DIR`의 RPT JSON을 `verification-result`로 변환 | `GET /api/update/results`, `/results/{id}` |
| 재검증 요청 | runtime `/api/revalidation` | `GET /api/update/revalidation` |
| 환경·정책 | 플랫폼 자체 저장소 `PLATFORM_DATA_DIR` | `GET/POST /api/environments`, `/api/policies`, `/{id}/revisions` |
| 증거 | `evidence-index` 파일 | `GET /api/evidence` |

- 위 환경변수가 없으면 `../contracts/examples/`를 읽는 **예시 모드**로 뜬다. 예시 모드는 화면 전체에 "예시 데이터" 띠를 띄운다.
- 화면에 내보내는 응답은 모두 계약 스키마로 검증한다. 검증에 실패한 항목은 버리지 말고 "형식 오류" 행으로 따로 보여준다.
- runtime·update와의 연결은 `sources/` 아래 어댑터에만 둔다. 라우터와 화면은 계약 형식만 안다.
- 저장소는 인터페이스(`EnvironmentStore`, `PolicyStore`) 뒤에 둔다. 1차 구현은 파일이다(판본마다 새 JSON 파일, 덮어쓰기 금지). 나중에 Postgres로 바꿀 수 있게 한다.
- 정책 판본 해시는 contracts README의 정규 JSON 해시 규칙을 따른다.

## 5. 지켜야 할 것

1. **runtime과 update의 상태를 바꾸는 요청을 보내지 않는다.** runtime에는 GET만 보낸다. 추론 요청(`POST /v1/chat/completions`)은 절대 보내지 않는다.
2. **없는 값을 만들지 않는다.** 지연시간, 비용, 심각도, 위험 점수처럼 원본에 없는 값은 화면에도 없다.
3. **비밀을 다루지 않는다.** 토큰은 환경변수로만 받고 로그·응답·화면에 내지 않는다. 프롬프트·응답 본문은 원본에도 없으니 화면에서도 찾지 않는다.
4. **사용자에게 받은 값은 텍스트로만 렌더링한다.** `dangerouslySetInnerHTML`을 쓰지 않는다.
5. **API는 기본으로 `127.0.0.1`에만 연다.** 로그인은 이번 범위가 아니므로 외부 주소로 열지 않는다.
6. 판정·집계 규칙을 화면에서 다시 계산하지 않는다. update가 낸 값을 그대로 보여준다.

## 6. 이번에 하지 않는 것

- 로그인·권한, 정책 승인(CONFIRMED 전환), 정책 번들 발행·runtime 배포
- 재검증 시작, update 시험 실행 버튼
- Kibana·Elasticsearch·OpenSearch 연동
- 알림, 이메일, 외부 전송

## 7. 작업 순서와 완료 기준

1. `api/`·`web/` 뼈대, 사이드바+상단바 레이아웃, 9개 탭 라우팅(빈 화면)
2. 예시 모드: `../contracts/examples/`를 읽어 모든 탭에 데이터 표시
3. 요청 내역 표 + 상세 패널(Helicone형), 사건 로그 3단 화면(Better Stack형)
4. 대시보드 지표 카드와 그래프(Langfuse형)
5. 검증 결과, 재검증 요청, Runtime 상태, 증거 화면
6. 환경·정책 입력 폼, 스키마 검증, 판본 저장과 diff
7. runtime 실제 연결(`RUNTIME_BASE_URL`)과 update 보고서 폴더 연결

**완료 기준**
- 예시 모드로 9개 탭이 모두 뜨고, 각 탭에서 예시 데이터가 보인다.
- `uv run pytest`(api)와 `npm test`(web)가 통과한다. 다음 테스트를 포함한다.
  - 계약 예시 전부가 API 응답 검증을 통과한다.
  - runtime Gateway 판정 → 사건 `outcome` 변환이 contracts README 표와 같다.
  - Hubble 사건이 "거부 확인"으로 표시되지 않는다.
  - `null` 값이 0이나 빈칸으로 표시되지 않는다.
  - 정책 저장이 기존 판본을 덮어쓰지 않는다.
  - runtime 클라이언트가 GET 외의 메서드를 보내지 않는다.
- `npm run build` 결과를 FastAPI가 서빙하고, `uv run byul-platform` 하나로 화면이 열린다.
- `platform/README.md`에 실행 방법, 환경변수, 예시 모드를 적는다.

## 8. 멈추고 물어볼 때

- 계약 스키마에 없는 필드가 화면에 필요할 때. 계약을 바꿀지 사람이 정한다.
- 2절 목록 밖의 의존성이 필요할 때
- runtime이나 update 쪽 코드를 바꿔야 할 때
- 레퍼런스 구성과 이 문서가 충돌할 때. 이 문서를 따르고, 충돌 내용을 보고한다.

## 9. 검증 보고서 한국어 문구 작성 프롬프트

아래 프롬프트는 점검별 문구를 설계하거나 리뷰할 때 사용한다. 플랫폼은 검토된 문구를 구조화된 사유 코드에 연결해 표시한다.

> 처음 읽는 운영자가 BYUL 검증 보고서의 판정과 근거를 이해하도록 한국어 문구를 작성한다.
>
> 입력에는 점검 질문, 점검 대상과 판본, 기록된 판정, 비교 상태, 구조화된 사유 코드, 확인된 관측값, 미확인 관측 및 범위가 있다. 각 사실은 입력에 있는 범위에서만 쓴다.
>
> 1. 제목 한 줄에는 기록된 판정과 판정에 직접 연결된 사건을 적는다. 비교 보고서는 확인된 사실의 주체가 기준인지 후보인지 드러낸다. 비교 불가 또는 비교 미확인은 화면에 계속 표시한다.
> 2. 근거의 첫 문단은 적용 정책과 시험에서 일어난 일을 순서대로 설명한다. 둘째 문단은 그 일이 실제로 일어났다고 볼 수 있는 직접 증거와 확인 방법을 설명한다. 입력에 없는 정책이나 시험 단계는 생략한다.
> 3. 미확인은 누락된 관측이나 판정 범위를 적는다. 판정만 남은 보고서는 기록된 결과와 세부 관측 기록의 부재를 밝힌다. 파일 존재, 열기, 읽기, 로드 및 실제 사용은 각각 관측이 뒷받침하는 수준까지 표현한다.
> 4. 코드 필드명, 내부 식별자, 전체 해시, 기술 원문, RES·EVD 참조는 상세 근거에 둔다. 기존 판정과 원본 JSON을 보존하고 영어 판정 문자열을 정규식으로 해석하지 않는다.
>
> 출력은 제목, 확인된 근거, 미확인 범위 순서다. 데이터가 있는 부분만 쓴다.
>
> 예시 — SUP-08에서 기준의 보안 PASS, 후보의 보안 FAIL, 후보의 실제 모듈 로드 및 해시 일치가 기록된 경우
>
> **기준은 보안 충족, 후보에서는 요청에 지정한 코드가 로드됨**
>
> 정책은 요청을 보낸 사람이 서버에서 사용할 코드 버전을 지정하는 것을 금지합니다. 후보 vLLM 0.30.0은 시험 요청에서 지정된 버전의 코드를 실제로 불러왔습니다.
>
> 요청 직후 생긴 로드 기록을 해당 코드 파일과 대조했습니다. 로드 기록과 저장된 모듈의 해시가 일치했습니다.
>
> 기준 vLLM 0.7.0은 보안 충족으로 기록됐습니다. 이 보고서에는 그 판정의 세부 관측 기록이 없습니다.

새 항목의 문구는 해당 점검의 정책·판정 기준과 구조화된 관측을 대조한 뒤 추가한다. 관측 누락, 과거 보고서, 비교 불가에서 확인되지 않은 동작을 단정하지 않는지 테스트한다.
