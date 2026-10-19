`CLAUDE.md`, `SPEC.md`, `PROGRESS.md`, `DECISIONS.md`를 먼저 읽어라. 1~4, 6, 7단계는 끝났고 이번 목표는 **5단계의 "서버 없이 가능한 부분"(5a)** 이다. 실제 원격 서버·vLLM 실행(5b)은 이번에 하지 않는다.

## 절대 하지 않는 것
- 원격 서버 접속, 결제·계정·클라우드 API 사용, SSH, `DOCKER_HOST` 원격 설정
- vLLM 이미지·모델 다운로드 (nginx, python slim 같은 작은 기본 이미지의 pull은 허용하되, 시작 전에 어떤 이미지를 받을지 알려라)
- `CLAUDE.md`·SPEC §9 "만들지 않는 것" 목록의 기능
- 새 파이썬 의존성 추가 (필요하면 멈추고 물어라)
- 이 폴더 밖에 파일 만들기. 생성하는 키·토큰은 실행 중 임시로 만들고 파일·로그·커밋에 남기지 마라.

## 시작 전
1. `docker info`로 Docker 사용 가능 여부를 확인해 알려라. 없으면 Part C(컨테이너 실험실)는 만들되 실행 검증은 건너뛰고, 나머지는 진행한다.
2. 아래 Part A~E의 작업 목록과 파일 구조, 사람이 정해야 할 것(있으면)을 보여줘라. 내가 "진행"이라고 하면 시작해라.

## Part A — SPEC 보완 (먼저, 테스트보다 앞서)
현재 REQ-01은 관측이 `direct_route_status`, `gateway_route_status` 정수 두 개뿐이라 프레임워크 원문의 unknown 기준("/v1만 확인했거나 /invocations 등 등록 경로를 확인하지 못했다")보다 느슨하고, 직통이 연결 거부·타임아웃으로 막히는 정상 PASS 형태를 표현할 수 없다. 다음으로 바꿔라. SPEC §3·§4.5·§6(INV-03 주석), REQ-01 override, `DECISIONS.md`를 함께 고치고, 어긋난 기존 테스트도 바로잡아라.

- 관측을 경로별로: `routes_registered: list[str]`, `routes_tested: list[str]`, `direct_outcomes: dict[route, 상태코드(int) | "refused" | "timeout"]`, `gateway_outcomes: dict[route, int]`, `trusted_origin_reachable: bool`(같은 시각에 신뢰 출발점에서 엔진에 닿는지 = 양성 대조).
- 등록 경로 목록은 엔진의 `/openapi.json`에서 읽는다 (그 외에 Gateway 설정에서 알려진 경로).
- PASS 조건 전부: 정상 대조 성공(허용 키로 Gateway 경유 200), 등록된 모든 추론·관리 경로를 시험함(`routes_tested ⊇ routes_registered`), 모든 경로의 직통 결과가 401/403 또는 `refused`/`timeout`, 그리고 `refused`/`timeout`을 근거로 쓰는 경우 `trusted_origin_reachable == true`일 것.
- FAIL: 어떤 경로든 직통 결과가 2xx (허용되지 않은 호출자의 직통 요청이 처리됨). 확인된 FAIL은 다른 누락과 무관하게 유지(INV-04).
- UNKNOWN: 등록 경로 일부만 시험, 양성 대조 실패, 직통이 404/5xx/기타, 관측 누락.
- 이 규칙은 `rule_table`로 표현하기 어려우면 기존 구조를 깨지 말고 `rule_table`에 필요한 최소한의 연산자(예: 집합 포함, 전체/하나라도)를 추가해라. 코어에 REQ-01 전용 분기를 넣지 마라(INV-14).
- FakeExecutor 시나리오를 새 관측에 맞게 갱신하고, 새 경우를 추가해라: `refused_with_positive_control`(PASS), `refused_without_positive_control`(UNKNOWN), `partial_routes`(UNKNOWN), `direct_200_on_invocations`(FAIL).

## Part B — Docker 실행 계층
- `src/guard/runtime/docker.py`: `docker` CLI를 `subprocess`로 부르는 얇은 래퍼. 명령 실행기를 주입할 수 있게 해서 단위 테스트에서는 가짜를 쓴다. 환경변수(`DOCKER_HOST` 등)는 그대로 통과시키되 코드에서 설정하지 않는다. 타임아웃을 필수로 받는다. 컨테이너·네트워크 이름에 실행 ID 접두어를 붙이고 `cleanup`을 항상 실행한다(실패해도 남기지 않음).
- 이 모듈은 `adapters/`·`executors/`에서만 쓴다. 코어는 몰라야 한다.

## Part C — 컨테이너 실험실 (로컬 전용)
`lab/` 아래에 만든다. 목적은 실행기 로직을 vLLM 없이 검증하는 것이다.
- `lab/stub_engine/`: 표준 라이브러리만 쓰는 작은 HTTP 서버. vLLM과 같은 경로 모양을 흉내낸다: `GET /health`, `GET /v1/models`, `POST /v1/completions`, `POST /invocations`, `GET /openapi.json`(위 경로들을 등록 목록으로 반환). 인증은 구현하지 않는다(엔진 `--api-key` 꺼진 상태를 재현).
- `lab/gateway/`: nginx 설정 템플릿. 허용 키가 있는 요청만 엔진으로 프록시하고 나머지는 401. 키는 실행 시 주입한다.
- 위상 두 가지를 만든다:
  - `exposed`: 엔진 포트가 호스트(127.0.0.1에만 바인드)와 같은 Docker 네트워크에 노출 → 직통 가능 → REQ-01 **FAIL**이어야 한다.
  - `isolated`: 엔진은 Gateway와만 통신하는 내부 네트워크, 호스트·외부 출발점에서 직통 불가 → 직통 `refused`/`timeout` + 양성 대조(Gateway 쪽 신뢰 컨테이너에서는 엔진에 닿음) → **PASS**여야 한다.
- 모든 포트는 `127.0.0.1`에만 바인드. 실험실은 로컬 맥에서만 쓴다.
- 요청 출발점은 두 가지: "외부 호출자"(별도 컨테이너, 엔진 네트워크 밖) 와 "신뢰 출발점"(Gateway 컨테이너 안 또는 엔진 네트워크 안).

## Part D — vLLM 어댑터와 direct_route 실행기
- `adapters/vllm.py`: SPEC §7.1 `EngineAdapter` 전체 구현. `start_command`(고정 모델 `facebook/opt-125m`, revision 인자, 오프라인·읽기 전용 모델 마운트), `public_routes`(`/openapi.json` 파싱), `detect_version`, `read_option`, `parse_log`, `ready`. 실제 vLLM 컨테이너 기동은 하지 않는다. 단위 테스트는 가짜 Docker 실행기와 샘플 응답으로 한다.
- `executors/direct_route.py`: 실행기. 1) 등록 경로 목록 확보 2) 허용 키로 Gateway 경유 정상 대조 3) 키 없음·잘못된 키·허용 키로 Gateway와 직통 경로 비교 4) 직통 결과를 `status | refused | timeout`으로 분류(연결 거부, 타임아웃, HTTP 응답을 각각 구분) 5) 양성 대조 6) 증거 파일(요청/응답 요약)을 EVD artifacts로 저장. 요청 본문은 해로운 내용 없이 `max_tokens: 1`의 무해한 요청만. 키 값은 증거·로그·DECISIONS에 남기지 마라(마스킹).
- `guard.toml`의 `[executors]`에서 `direct_route = "real"`(실험실 대상)과 `"fake"`를 선택할 수 있게 한다.

## Part E — 테스트
- 기본 `uv run pytest`는 지금처럼 빠르고(1분 이내) Docker 없이 통과해야 한다. Part A의 판정 변경, Part B·D의 단위 테스트(가짜 Docker 실행기, 상태/거부/타임아웃 분류), INV-03 관련 테스트 보강(연결 거부 + 양성 대조 있음/없음)을 여기에 포함한다. 이번에 skip에서 풀 수 있는 INV-03 테스트는 풀어라.
- Docker가 필요한 테스트는 `@pytest.mark.docker`(`pyproject.toml`에 마커 등록), 기본 실행에서 제외.
- Docker가 있으면 `uv run pytest -m docker`로 두 위상을 실제로 돌려 `exposed → FAIL`, `isolated → PASS`를 `guard select/run/judge/report` CLI 전체 경로로 확인해라. 없으면 못 돌렸다고 PROGRESS.md에 명시하고 거짓으로 통과시키지 마라.

## 열린 질문 6개의 기본값 (바꿀 이유가 있으면 멈추고 물어라)
1. Gateway: nginx. 테스트 키는 실행 시 임의 생성(허용/비허용 2개).
2. 고정 모델: `facebook/opt-125m` + revision 고정. 이미지 digest는 실행 시 읽어 ENV에 기록.
3. env/policy: env는 `DECLARED`, 정책은 `CONFIRMED`. 값은 만들어서 보여주고 내가 확인한다.
4. 팩 검토자: 아직 지정하지 않는다. 팩은 DRAFT 유지.
5. 직통 404: 등록된 경로에서 404면 UNKNOWN.
6. Docker 제어: `docker` CLI를 `subprocess`로.
이 결정들은 `DECISIONS.md`에 적어라.

## 완료 기준
- 기본 `uv run pytest` 전부 통과, 1분 이내. skip은 이유가 적혀 있고 5b(실제 vLLM·원격)에서만 가능한 것만 남는다.
- Part A로 REQ-01이 경로별 관측·양성 대조 규칙으로 판정된다. SPEC과 `DECISIONS.md`가 코드와 일치한다.
- (Docker가 있을 때) `-m docker`에서 `exposed → FAIL`, `isolated → PASS`.
- 실행 후 남은 컨테이너·네트워크가 없다 (`docker ps -a`, `docker network ls`로 확인해서 보고).
- `PROGRESS.md` 마지막에 **"5b 전에 사람이 직접 할 일"**을 정리한다: 서버 사양(CPU/GPU 여부 포함), Docker 설치, 방화벽(내 IP만 허용), 원격 접속·`DOCKER_HOST` 설정 방법, vLLM 이미지 태그 후보와 확인 방법(고정 소스 v0.31.0과 맞는 이미지가 있는지), 필요한 디스크·메모리, 시험 후 서버 삭제 절차, 그리고 5b에서 에이전트가 실행할 첫 명령.
- 마지막 보고에 변경 요약, 테스트 결과(명령과 숫자), 실행하지 못한 것, 열린 질문을 적어라.
