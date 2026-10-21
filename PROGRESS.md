# PROGRESS

## 0단계 — 프로젝트 골격 (2026-10-19)
- 한 일: pyproject(uv, Python 3.11), 디렉토리, DECISIONS.md
- 테스트 결과: -
- 다음 단계: 1단계 import_checklist
- 막힌 것: 없음

## 1단계 — import_checklist (2026-10-19)
- 한 일: `tools/import_checklist.py`, `tools/overrides/REQ-01.yaml`, `packs/PACK-BASELINE/{pack.yaml,id_map.yaml,checks/*.yaml}` 77개 생성. ID는 id_map에 고정, 내용 변경 시 CheckDef·팩 revision 증가, 사라진 행은 retired.
- 테스트 결과: `tests/test_import_checklist.py` 9 passed (77개, 재실행 바이트 동일, 문구 변경·순서 변경에도 ID 유지, retired·신규 번호, 매니페스트 해시, 커밋된 팩 = 변환기 출력)
- 다음 단계: 2단계 모델·해시
- 막힌 것: 없음

## 2단계 — 모델·해시 (2026-10-19)
- 한 일: `models/` enum·공통 필드(GuardObject, Ref)·CheckDef·팩 로더(파일 해시 검증)·객체 7종·ULID·해시(§2.3)
- 테스트 결과: 39 passed (7종 YAML 왕복, 해시 고정값, 변조 감지, EVD 판정 필드 금지, INV-02·12·18 모델 수준)
- 다음 단계: 3단계 저장소
- 막힌 것: 없음

## 3단계 — 저장소 (2026-10-19)
- 한 일: `store/sqlite.py` append-only(UPDATE/DELETE 트리거 차단, revision은 latest+1만), 쓸 때 해시·참조 검증, `resolve(ref)` 정확한 revision, 내용 주소 `artifacts/<sha256>`, `verify()` 일괄 검증
- 테스트 결과: 48 passed (INV-16 9개)
- 다음 단계: 4단계 select
- 막힌 것: 없음

## 4단계 — select (2026-10-19)
- 한 일: `core/select.py`(적용 판단 규칙 5개, 선정 규칙, POLICY_SCOPE, NO_EXECUTOR/NO_POLICY_VALUE, inventory_complete), `collectors/file.py`(env.yaml/policy.yaml), `examples/`, `guard.toml`
- 테스트 결과: 81 passed (규칙 1~5, 선정 규칙, INV-05·06·13, 수집기)
- 다음 단계: (5단계 대체) Protocol + FakeExecutor + run
- 막힌 것: 없음

## (5단계 대체) 인터페이스 + FakeExecutor + run (2026-10-19)
- 한 일: `adapters/base.py`(EngineAdapter Protocol), `adapters/fake.py`, `executors/base.py`(Executor Protocol, EvidenceDraft), `executors/fake.py`(pass/direct_200/not_attempted/missing_obs, 항상 EXAMPLE), `core/run.py`
- 테스트 결과: 94 passed (FakeExecutor 4종, INV-10 run 부분, INV-15 run 부분, 코어 엔진 이름 금지)
- 다음 단계: 6단계 rule_table 판정기
- 막힌 것: 없음. 실제 vLLM 어댑터·직통 실행기는 이번 범위 밖

## 6단계 — rule_table 판정기 (2026-10-19)
- 한 일: `judges/base.py`(Judge Protocol, JudgeOutput), `judges/rule_table.py`(eq/ne/in/not_in/lt/le/gt/ge, 축별 표), `core/judge.py`(INV-11 PASS 관문, FAIL 유지, 플러그인 실패→UNKNOWN, 예외 표시, verification_level), 재판정 시 같은 내용이면 revision을 만들지 않음
- 테스트 결과: 151 passed. REQ-01 가짜 증거 pass/direct_200/not_attempted/missing_obs → PASS/FAIL/UNKNOWN/UNKNOWN (`tests/test_req01_scenarios.py`), INV-01·03·04·08·11·14·15·18, INV-10 RES 부분
- 다음 단계: 7단계 report
- 막힌 것: 없음

## 7단계 — report (2026-10-19)
- 한 일: `core/aggregate.py`(집계 4규칙), `core/compare.py`(비교표 9칸 + NOT_COMPARABLE, INV-09 비교 조건), `core/report.py`(RPT: by_check/by_purpose/by_area/not_selected/manual_required/limitations/inputs_manifest, approval 항상 null), `report/html.py`(JSON에서만 렌더링, 한국어 표시), `cli.py`(select/run/judge/report/verify)
- 테스트 결과: **208 passed, 3 deselected(engine) in ~6s**. 집계 10케이스, 비교 19케이스, INV-07·09·17, 보고서 구조, CLI e2e(4 시나리오)
- 실행 확인: 아래 명령으로 `out/report.json`, `out/report.html` 생성. REQ-01 security PASS(level EXAMPLE), 목적·영역은 UNKNOWN(DRAFT 팩 → inventory 미완결, INV-13), official=false
  ```bash
  PLAN=$(uv run guard select --pack packs/PACK-BASELINE --env examples/env.yaml --policy examples/policy.yaml | tail -1)
  uv run guard run --plan $PLAN [--fake-scenario pass|direct_200|not_attempted|missing_obs]
  uv run guard judge --plan $PLAN
  uv run guard report --plan $PLAN --json out/report.json --html out/report.html
  uv run guard verify
  ```
- 막힌 것: 없음

## 불변식 테스트 현황
INV-01~18 모두 `tests/invariants/test_inv_XX.py`에 있다. 엔진이 필요해 skip한 것(`-m engine`):
- INV-03 실제 직통 요청의 연결 거부·타임아웃 → 5단계
- INV-09 두 이미지 실제 비교(`guard update`) → 8단계
- INV-15 실제 어댑터 기동 실패 전파 → 5단계

## 5단계로 넘어가기 전에 사람이 정해야 할 것
1. **시험용 Gateway 구성**
   - 어떤 Gateway를 쓸지(예: nginx/envoy 리버스 프록시 컨테이너 1개, API 키 헤더 검사만). 우리가 Gateway를 *만들지는* 않는다(SPEC §9) — 시험 고정물(fixture)로 기성 이미지를 쓸지 결정.
   - 허용 키·비허용 키(무키/오키) 값, Gateway가 엔진에 전달하는 신원 헤더 여부.
   - 망 구성: Gateway와 엔진을 같은 Docker 네트워크에 두고, "직통"을 무엇으로 볼지(호스트에서 엔진 포트 직접 / 같은 네트워크의 다른 컨테이너에서). REQ-01 관측 `direct_route_status`의 출발점 정의.
   - 시험할 route 목록: `/v1/completions`, `/v1/chat/completions`, `/invocations` 등 (원문 미확인 기준 "/v1만 확인" 방지). 지금 관측 키는 2개 — route별 관측으로 늘릴지(`direct_route_status.<route>`) 결정.
   - 엔진 자체 `--api-key`를 켤지(켜면 직통도 401 → PASS가 "엔진 키" 때문인지 "망 차단" 때문인지 구분 필요).
2. **고정 모델** — CPU/작은 GPU에서 뜨는 작은 모델 1개(id·revision 고정, inventory_sha256 계산 방법). 이미지 digest 고정(`vllm/vllm-openai@sha256:...` 또는 CPU 빌드). GPU 없이 돌릴지 여부.
3. **env.yaml 실제 값** — 예시(`examples/env.yaml`)는 EXAMPLE이다. 실제로는:
   ```yaml
   data_origin: DECLARED
   engine: { name: vllm, version: "0.31.0", source_commit: <hex>, image_digest: "sha256:..." }
   adapter: { name: vllm, version: "0.1.0" }
   runtime: { nodes: 1, tp_groups: 1, gpu_count: <n>, gpu_model: <str>, driver: <str> }
   model: { id: <hf id>, revision: <commit>, inventory_sha256: <hex> }
   facts:
     gateway: { value: true, origin: DECLARED, source: "배포 문서 링크" }
   ```
   `false`로 신고할 사실은 `absence_evidence`(비사용 근거)가 필요하다.
4. **policy.yaml 실제 값** — `values.REQ-01.allowed_callers`(허용 주체), `scope`(첫 실측은 `include: [REQ-01]` 권장), 확정자(`status: CONFIRMED`, `confirmed_by`, `confirmed_at`). 확정 + 팩 REVIEWED + 실측일 때만 `official: true`.
5. **팩 검토** — `packs/PACK-BASELINE/pack.yaml`의 `review`를 누가 REVIEWED로 바꿀지. 지금은 DRAFT라 모든 보호 목적이 UNKNOWN 집계.
6. **REQ-01 판정표 확인** — `tools/overrides/REQ-01.yaml`: 직통 401/403 → 거부, 200 → FAIL, 그 밖(404·연결 거부 등) → UNKNOWN. 404를 거부로 볼지 결정 필요(원문: "연결 실패·404만으로 전체 route 통과 불가").
7. **5단계 의존성** — Docker 제어를 `subprocess`(docker CLI)로 할지, 별도 라이브러리(docker SDK, 의존성 추가 → 승인 필요)를 쓸지.

## 5a Part A — REQ-01 경로별 관측·양성 대조 (2026-10-19)
- 한 일: SPEC §3·§4.5·§6(INV-03 주석)·§7.2·§10 수정, `tools/overrides/REQ-01.yaml` → 팩 revision 2(REQ-01 revision 2), `rule_table` 연산자(nonempty/all_in/any_in/any_between/all_between/superset_of+value_obs, when), FakeExecutor 8 시나리오, `executors/http_probe.py`(표준 라이브러리 분류기), 기존 테스트를 새 관측으로 갱신, pytest basetemp를 `.pytest_tmp/`로
- 테스트 결과: 261 passed. REQ-01 8 시나리오(pass/refused_with_positive_control → PASS, direct_200/direct_200_on_invocations → FAIL, 나머지 → UNKNOWN), INV-03 루프백 실제 연결 거부 분류(skip 해제)
- 다음 단계: Part B Docker 실행 계층
- 막힌 것: 없음

## 5a Part B — Docker 실행 계층 (2026-10-19)
- 한 일: `src/guard/runtime/docker.py` — 주입 가능한 명령 실행기, 모든 호출 timeout 필수, `guard-<run_id>-` 접두어 + `guard.run` 라벨, 포트는 `127.0.0.1::<포트>`만, 비밀은 `-e NAME`(값은 그 subprocess 환경에만), DOCKER_* 설정 금지, `cleanup()`은 라벨로 전부 삭제하고 단계 실패해도 계속, context manager에서 항상 실행
- 테스트 결과: `tests/test_docker_runtime.py` 14 passed (가짜 실행기). runtime은 adapters/·executors/에서만 import(정적 검사)
- 막힌 것: 없음

## 5a Part C — 컨테이너 실험실 (2026-10-19)
- 한 일: `lab/stub_engine/server.py`(표준 라이브러리, /health /v1/models /v1/completions /invocations /openapi.json, 인증 없음), `lab/gateway/default.conf.template`(nginx, `Bearer <허용키>`만 프록시, 나머지 401, 헤더 미기록·미전달), `lab/lab.py`(exposed/isolated 위상, target.json·env.yaml 생성, 키 없음), `lab/policy.yaml`(DRAFT, confirmed_by 비움)
- 이미지: `nginx:1.27-alpine`(새로 받음), `python:3.11-slim`(stub 엔진·probe 공용). digest는 실행 시 읽어 ENV에 기록
- 막힌 것: 없음

## 5a Part D — vLLM 어댑터 + direct_route 실행기 (2026-10-19)
- 한 일: `adapters/vllm.py`(EngineAdapter 전체: start_command/launch_plan[opt-125m, revision 필수, HF_HUB_OFFLINE, /models 읽기 전용, API 키는 VLLM_API_KEY 환경변수], routes_from_openapi/public_routes[정보성·관리·상태변경 분류], probe_request[max_tokens 1], detect_version, read_option, parse_log, ready), `executors/http_probe.py`, `executors/direct_route.py`(등록 경로 → 정상 대조 → 키 3종 Gateway/직통 비교 → 분류 → 양성 대조 → 마스킹된 probe_log.jsonl), guard.toml `direct_route = "real"|"fake"`
- 실제 vLLM 컨테이너는 띄우지 않았다(5b)
- 막힌 것: 없음

## 5a Part E — 테스트 (2026-10-19)
- `uv run pytest` → **315 passed, 4 deselected in ~10.5s** (Docker 불필요). deselected = docker 2 + engine 2
- `uv run pytest -m docker -s` → **2 passed in 56.6s** (로컬 Docker Desktop 29.6.1, aarch64). CLI 전체 경로(select/run/judge/report):
  | 위상 | REQ-01 security | direct_outcomes (무키·오키 대표) | direct_allowed_outcomes(판정 미사용) | gateway(허용 키) | 양성 대조 |
  |---|---|---|---|---|---|
  | exposed | **FAIL** | 3경로 모두 200 (외부 컨테이너·호스트 127.0.0.1 둘 다) | 200 | 200 | true |
  | isolated | **PASS** | 3경로 모두 `timeout` (연결 단계 `TimeoutError`) | `timeout` | 200 | true |
  - isolated에서 실제로 나온 값은 9건 전부 **연결 단계 timeout**이고 `unreachable`(ENETUNREACH/EHOSTUNREACH)은 나오지 않았다(Docker Desktop의 네트워크 간 격리는 패킷을 버리는 방식으로 보임). isolated에는 엔진 공개 포트가 없어 호스트 출발점 직통은 시험 대상이 없다.
  - Gateway 무키·오키는 두 위상 모두 401.
  - 검증 수준 EXAMPLE, official=false. 키는 저장소·증거·보고서·target·env·컨테이너 로그 어디에도 없음(테스트에서 검사).
- 정리 확인: 실행 후 `docker ps -a --filter label=guard.run`, `docker network ls --filter label=guard.run` 모두 0개. `docker ps -a`에는 원래 있던 `ubuntu24.04`, `condescending_margulis`만, `docker network ls`에는 기본 bridge/host/none만 남음.
- skip 해제: INV-03(루프백 실제 연결 거부), INV-15(가짜 Docker 실패 → ABORTED). 남은 skip: INV-09 `guard update` 실제 두 이미지(8단계, 실제 vLLM 필요), INV-15 실제 vLLM 기동 실패(5b)

## 5b 전에 사람이 직접 할 일
1. **서버 사양**
   - GPU 경로(권장): NVIDIA L4/T4/A10 급 1장(VRAM 16GB+이면 opt-125m에 충분), vCPU 4+, RAM 16GB+, 디스크 100GB+(vLLM 이미지 ~10GB 이상 + 모델 + 로그). NVIDIA 드라이버와 nvidia-container-toolkit 설치.
   - CPU 경로: vCPU 8+, RAM 16~32GB, 디스크 60GB+. vLLM CPU 빌드 이미지 필요(아래 3). 느리지만 REQ-01(권한) 시험에는 충분.
2. **Docker 설치** — Docker Engine(서버), 사용자를 docker 그룹에 넣지 말고 rootless 또는 sudo로 운영할지 결정. `docker info`로 Server Version과 (GPU면) `nvidia` runtime 확인.
3. **vLLM 이미지 태그 후보와 확인 방법** (고정 소스 v0.31.0, commit `db9527a46873454610df6dbedf79a36d6bf1a7f6`)
   - 후보: GPU `vllm/vllm-openai:v0.31.0`, CPU는 공식 CPU 릴리스 이미지 또는 `docker/Dockerfile.cpu`로 해당 commit에서 직접 빌드.
   - 확인: `docker manifest inspect vllm/vllm-openai:v0.31.0`로 존재·아키텍처 확인 → `docker pull` 후 `docker image inspect --format '{{json .RepoDigests}}'`로 digest 고정 → `docker run --rm --entrypoint python3 <digest> -c "import vllm; print(vllm.__version__)"`로 버전, 이미지 라벨이나 `pip show vllm`으로 소스 commit이 `db9527a`와 맞는지 확인. 맞지 않으면 해당 commit에서 빌드한 이미지를 쓰고 그 digest를 ENV에 기록.
4. **모델 준비(오프라인)** — 서버에서 `facebook/opt-125m`를 특정 revision(commit hash)으로 미리 받아 HF 캐시 디렉토리에 두고(약 0.5GB), 그 디렉토리를 `/models`로 읽기 전용 마운트. revision 값을 정해 주면 어댑터 `options['revision']`에 넣는다. inventory_sha256 계산 방식(파일별 sha256 목록의 해시) 확인.
5. **방화벽** — 클라우드 보안 그룹·ufw에서 SSH(22)만 **내 IP/32**로 허용, 그 밖의 인바운드 전부 차단. 엔진·Gateway 포트는 서버의 127.0.0.1에만 바인드(우리 런타임이 강제)하므로 외부 개방이 필요 없다.
6. **원격 접속과 DOCKER_HOST** — 코드는 DOCKER_HOST를 설정하지 않고 그대로 통과시킨다. 사람이 셸에서 지정한다:
   - `docker context create guard-5b --docker "host=ssh://<user>@<server-ip>"` → `docker context use guard-5b` (또는 `export DOCKER_HOST=ssh://<user>@<server-ip>`). SSH 키 인증, 비밀번호 로그인 끔.
   - 주의: 원격 Docker에서는 `-v <로컬경로>` 마운트가 **서버의 경로**를 가리킨다. 실험실 파일(stub·probe·nginx 템플릿)과 모델 캐시를 서버의 같은 경로에 두거나, 5b에서 마운트 대신 파일 복사 방식으로 바꿀지 결정이 필요.
   - 127.0.0.1 바인드는 **서버의** 루프백이다. 호스트 출발점 직통 시험은 서버에서 실행해야 의미가 있다(맥에서 원격 컨텍스트로 돌리면 호스트 출발점은 건너뛰거나 SSH 터널을 쓴다).
7. **실제 vLLM의 상태 변경 관리 경로를 직통으로 시험하는 방법과 순서** (`/sleep`, `/wake_up`, `/reset_prefix_cache`, `/reset_mm_cache`, `/load_lora_adapter`, `/unload_lora_adapter`, `/collective_rpc`, `/start_profile`/`/stop_profile`, `/scale_elastic_ep` 등)
   - 원칙: 지금 실행기는 이 경로들에 요청을 보내지 않고 "미시험"으로 남긴다(`allow_mutating_routes = false`). 켜는 것은 사람이 결정한다.
   - 대상: **버리는 전용 엔진 인스턴스**에서만(시험이 끝나면 컨테이너 삭제). 공유·운영 엔진에는 절대 보내지 않는다.
   - 사전 조건: 이 경로들은 vLLM 개발 모드(예: `VLLM_SERVER_DEV_MODE=1`)나 특정 옵션에서만 열리는 것이 많다. ① 개발 모드 **끔**으로 띄워 openapi에 없는지(직통 404가 정상) 먼저 확인 ② 필요할 때만 개발 모드 켠 인스턴스를 따로 띄운다.
   - 순서(덜 파괴적 → 더 파괴적, 각 단계 뒤 `/health`·간단 추론 1회로 건강 확인, 이상하면 컨테이너 재생성):
     1. 읽기 전용 관리 경로(`/is_sleeping`, `/server_info`) — GET
     2. 캐시 초기화(`/reset_prefix_cache`, `/reset_mm_cache`) — 성능 외 영향 적음
     3. 프로파일러(`/start_profile` → 즉시 `/stop_profile`) — 디스크 쓰기 확인
     4. 존재하지 않는 LoRA 이름으로 `/unload_lora_adapter`, 그다음 존재하지 않는 경로로 `/load_lora_adapter` — 인가 거부(401/403)와 handler 도달(400/404/500)을 구분. 실제 파일 경로·원격 URL은 쓰지 않는다
     5. `/sleep`(level 1) → `/is_sleeping` 확인 → `/wake_up` → 추론 1회로 복구 확인. 반드시 한 쌍으로
     6. `/collective_rpc`, `/scale_elastic_ep`, 가중치 갱신 계열은 **보내지 않는다**(임의 메서드 실행·클러스터 변경). 존재 여부(openapi)만 기록하고 수동 검토로 남긴다
   - 판정: 무키·오키 직통이 2xx면 FAIL(이미 처리됨), 401/403이면 거부, 4xx/5xx(인가 외)는 handler 도달로 보고 UNKNOWN. 매 요청 전후 엔진 상태(`/is_sleeping`, 로그)를 증거 파일에 남긴다.
8. **시험 후 서버 삭제 절차** — `docker ps -a --filter label=guard.run`·`docker network ls --filter label=guard.run`로 잔여 0 확인 → 결과(`.guard/`, report.json/html)를 맥으로 회수 → 클라우드 콘솔에서 인스턴스·디스크·스냅샷·고정 IP·보안 그룹 삭제 → 로컬 `docker context rm guard-5b` → SSH known_hosts에서 서버 항목 삭제. 과금 정지 확인.
9. **실측 정책 확정자** — `lab/policy.yaml`은 DRAFT·confirmed_by 비움. 실측 때 사람이 `status: CONFIRMED`, `confirmed_by`, `confirmed_at`을 정한다(팩도 REVIEWED가 아니면 official은 계속 false).
10. **5b에서 에이전트가 실행할 첫 명령** (사람이 DOCKER_HOST/컨텍스트를 설정한 뒤, 읽기 전용 확인만):
    ```bash
    docker context show && docker info --format '{{.ServerVersion}} {{.OperatingSystem}} {{.Architecture}} runtimes={{json .Runtimes}}'
    ```
    그 결과를 사람에게 보여 대상 서버가 맞는지 확인받은 뒤 이미지 확인(3) → 모델 위치 확인(4) 순서로 진행한다.

## 5b — 실제 vLLM(CPU, arm64, Docker Desktop on macOS)으로 REQ-01 재현 (2026-10-19)
### 실행 위치
- 시험 서버가 아니라 **맥**(`idohaui-MacBookAir.local`, Apple M5, 24GB, macOS 26.6.2)에서 실행. `DOCKER_HOST` 미설정, context `desktop-linux`, Docker Desktop 29.6.1. VM 자원: **CPU 10, 메모리 7.75GiB**(MemTotal 8,321,515,520), 커널 6.12.76-linuxkit aarch64. GPU 없음(사람 결정: CPU 경로).
### 이미지·모델
- `vllm/vllm-openai-cpu:v0.31.0` arm64 pull 111초, 1.09GB. digest `sha256:495c84fe30f3572b83a78100a1e8ba423dbb77fc532ecefebe114e6b77327bd9`. entrypoint `vllm serve`.
- 버전 `0.31.0`(`0.31.0+cpu`), commit `gdb9527a46` → **축약 hash 9자리만 고정 commit과 일치, 40자리 전체 미확인**(ENV `source_commit_status: ABBREVIATED_MATCH`, 보고서 limitations 표시).
- 모델 `facebook/opt-125m@27dcfa74d334bc871f3234de431e71c6eeba5dd6`: 다운로드 388초, `~/guard-models` 722MB. inventory **12 files / 753,111,181 bytes / `d677f3999b13aab7bf9a7d203568ca63bc2d3fdc6a102b5efc2c719fd816ae6e`** (`out/5b/model_inventory.json`). BoB 보고서 37개와 다름(확인 대상).
### 기동
- 엔진 ready(신뢰 출발점 `/health` 200)까지 **28~32초**, 오프라인·읽기 전용 마운트로 정상 기동.
- 개발 모드 꺼짐 등록 경로(openapi) **22개**: info 6(`GET /health`, `GET /metrics`, `GET|POST /ping`, `GET /version` — `/openapi.json`은 목록에 없음), 시험 대상 16(`/detokenize`, `/generative_scoring`, `/invocations`, `/is_scaling_elastic_ep`, `GET /kv_event_sources`, `GET /load`, `/tokenize`, `/v1/chat/completions`, `/v1/chat/completions/batch`, `/v1/completions`, `/v1/messages`, `/v1/messages/count_tokens`, `GET /v1/models`, `/v1/responses`, `GET /v1/responses/{response_id}`, `/v1/responses/{response_id}/cancel`), **상태 변경 1(`POST /scale_elastic_ep`) — 보내지 않음**. 전체는 `out/5b/explore/openapi.json`.
### 결과 (`uv run pytest -m engine -s` → **2 passed, 1 failed, 1 skipped in 221s**)
| 위상 | 기대 | **실제 security** | functional | 직통(무키·오키 대표) | 양성 대조 | 소요 |
|---|---|---|---|---|---|---|
| exposed | FAIL | **FAIL** ✅ | UNKNOWN | 16경로 중 200 ×8, 400 ×6, 404 ×2 (외부 컨테이너·호스트 127.0.0.1 동일) | true | ready 31s, run 2.3s, 합계 35s |
| isolated | PASS | **UNKNOWN** ❌ | UNKNOWN | 16경로 전부 `timeout`(연결 단계) | true | ready 29s, run 147s, 합계 177s |
| INV-15(빈 모델 디렉토리) | ABORTED→UNKNOWN | **ABORTED → UNKNOWN** ✅ | — | — | — | abort_reason "engine container exited before ready (last /health: refused)" |
- **직통 분포**
  - exposed: 외부·호스트 각각 키 3종 × (200 ×8, 400 ×6, 404 ×2). 키와 무관하게 같음(엔진 `--api-key` 없음). 200 경로: `/detokenize`, `/invocations`, `/is_scaling_elastic_ep`, `/kv_event_sources`, `/load`, `/tokenize`, `/v1/completions`, `/v1/models`. 400: chat·batch·messages·count_tokens·responses·generative_scoring(본문 `{}` 또는 chat template 없음). 404: `{response_id}` 리터럴 경로 2개.
  - isolated: 외부 컨테이너 키 3종 × 16경로 = **48건 전부 `timeout`(phase connect, TimeoutError)**, `unreachable` 0건, `refused` 0건. 호스트 출발점은 엔진 미공개라 대상 없음.
  - Gateway: 무키·오키 32건 전부 401. 허용 키는 200 ×8, 400 ×6, 404 ×2(엔진 응답 그대로 전달).
  - 양성 대조: 신뢰 출발점 → 엔진 `/health` 200 (두 위상).
- **isolated가 PASS가 아닌 원인(분석)**: 판정표의 다른 PASS 조건은 모두 충족(등록 경로 nonempty, 직통 전부 timeout ∈ 허용 집합, timeout 근거 + 양성 대조 true). 유일한 미충족은 `routes_tested ⊇ routes_registered` — 등록된 상태 변경 경로 `/scale_elastic_ep`을 규칙대로 보내지 않아 미시험. 규칙·기대값은 바꾸지 않았다. DECISIONS "열린 질문"에 선택지 (a)(b)(c)와 근거.
- **functional UNKNOWN(두 위상)**: Gateway 허용 키가 모든 등록 경로에서 200이어야 PASS인데 6경로 400, 2경로 404(요청 본문·경로 템플릿 문제, 엔진 거부 아님). 정상 대조(`/v1/completions` 허용 키 → 200)는 성공. DECISIONS 제안 참조.
- 증거 `data_origin` **MEASURED**(EVD), RES `verification_level` **DECLARED**(적용 조건 gateway 사실이 DECLARED, INV-12), 정책·팩 DRAFT → `official=false`.
- 보고서: `out/5b/{exposed,isolated,inv15}/report.{json,html}`, 관측 요약 `observed.json`, 엔진 로그 끝부분 `engine_log_tail.txt`, 저장소 `.guard/`. 키는 어떤 파일·컨테이너 로그에도 없음(테스트 검사).
### 안전 확인
- 리스너(`lsof -nP -iTCP -sTCP:LISTEN`): 실행 전·중·후 와일드카드 리스너는 macOS 기본 3개(`rapportd *:56365`, `ControlCenter *:7000`, `*:5000`)뿐, **새 0.0.0.0/:: 리스너 없음**. 전후 목록 `out/5b/listeners_{before,after}.txt` 동일.
- 모든 실행 후 `docker ps -a --filter label=guard.run` **0**, `docker network ls --filter label=guard.run` **0**. 엔진 컨테이너 잔존 없음(GPU 없음).
- 상태 변경 경로 요청 0건(`allow_mutating_routes = false`). 판정 규칙·불변식·기대값 변경 없음.
### 기본 테스트
- `uv run pytest` → **320 passed, 6 deselected**(docker 2 + engine 3 + INV-09 skip 1), 약 13초. 5a의 315개 유지 + 5b 단위 테스트 5개.
- 남은 skip: INV-09(8단계)뿐. INV-15 실제 기동 실패는 `-m engine`에서 통과.
### 못 한 것
- isolated에서 REQ-01 PASS 재현(위 원인, 사람 결정 대기).
- 40자리 소스 commit 확인(이미지에 정보 없음).
- GPU 경로(이번엔 CPU만).

## 시험 후 사람이 할 일 (맥)
1. **결과 확인** — 이미 맥에 있다: `out/5b/`(보고서·관측·inventory·openapi·리스너 기록), 각 위상의 `out/5b/<위상>/.guard/`. 다른 곳에 보관하려면 예: `rsync -a out/5b/ <백업경로>/serving-guard-5b/`.
2. **이미지 삭제** (8단계를 바로 하지 않으면):
   ```bash
   docker image rm vllm/vllm-openai-cpu:v0.31.0          # 약 1.1GB (digest 495c84fe…)
   docker image rm nginx:1.27-alpine                       # 5a에서 받음
   # python:3.11-slim은 5a 이전부터 있던 이미지라 지우지 않는다(원하면 수동으로)
   docker image prune                                      # 남은 dangling 레이어 정리(확인 후)
   ```
3. **모델 캐시** — `~/guard-models`(722MB). **8단계를 이 맥에서 할 거면 지우지 말 것**(INV-09 비교 조건: 같은 모델 inventory). 지워도 같은 revision을 다시 받아 `tools/model_inventory.py`로 `d677f399…`이 나오면 동일 자료로 쓸 수 있다. 지우기: `rm -rf ~/guard-models`.
4. **잔여 확인** — `docker ps -a --filter label=guard.run`, `docker network ls --filter label=guard.run`가 비어 있는지(지금 0). Docker Desktop 디스크 사용량: `docker system df`.
5. **정책·팩** — 실측 정책 확정자와 팩 검토자를 정하기 전까지 보고서는 공식 아님.

## 8단계(`guard update` 두 이미지 비교) 준비
- **CPU 이미지 태그(레지스트리 조회, 이미지 미수신)**: `vllm/vllm-openai-cpu`에는 태그 95개가 있고 정식 버전은 **v0.16.0 ~ v0.32.0**(각각 멀티 아키텍처 + `-arm64`/`-x86_64`), nightly-<commit> 다수. **v0.12~v0.15는 없다**(v0.14.0·v0.15.0 `no such manifest`). 참고로 사용자 확인("v0.31.0만 있음")과 달리 v0.16.0 이후 태그는 존재한다.
- `v0.14.0`↔`v0.31.0` 비교는 CPU로는 불가: v0.14.0은 GPU 이미지(`vllm/vllm-openai:v0.14.0`, arm64·amd64)에만 있다 → GPU 서버가 필요하고, 비교 대상도 GPU v0.31.0(arm64 약 10.1GB, amd64 약 9.0GB)이어야 INV-09 조건(같은 GPU·드라이버)이 맞는다.
- **CPU arm64 후보 쌍**(압축 크기, manifest 조회):
  | 쌍 | 기준 | 후보 | 용도 |
  |---|---|---|---|
  | A | `v0.16.0` (~0.72GB, `sha256:8984bf5d…`) | `v0.31.0` (보유, ~1.09GB) | 가장 넓은 변화 폭(CPU에서 가능한 최고령) |
  | B | `v0.30.0` (~1.32GB, `sha256:09e65658…`) | `v0.31.0` (보유) | 직전 버전 대비 회귀 확인(변경 분석과 맞추기 쉬움) |
  | C | `v0.31.0` (보유) | `v0.32.0` (~1.24GB, `sha256:433cbee5…`) | 최신 업데이트 시나리오 |
  | (참고) | `v0.20.0` (~0.82GB) | `v0.31.0` | 중간 폭 |
- **INV-09 비교 조건을 이 맥에서 맞추는 방법**: 같은 모델 inventory(`~/guard-models`, `d677f399…`, 두 버전 모두 읽기 전용 마운트) · 같은 시험 자료(fixture_sha256: 같은 어댑터·같은 probe 요청이면 동일하나, **버전마다 등록 경로가 달라지면 요청 목록이 달라져 fixture_sha256이 달라질 수 있음** → 비교용 고정 경로 목록을 정할지 결정 필요) · 같은 설정(config_sha256: target.json에서 실행마다 바뀌는 IP·run_id를 비교용 해시에서 제외하도록 정리 필요) · 같은 "GPU·드라이버" = 둘 다 null(CPU)인데 **INV-09는 null이면 NOT_COMPARABLE** → CPU 비교를 하려면 `runtime.gpu_model`/`driver` 대신 CPU 실행 환경 식별값(예: `cpu:arm64 docker-desktop vm=10cpu/7.75GiB kernel=6.12.76`)을 비교 조건에 넣는 규칙이 필요(SPEC 결정 사항, 사람 확인).
- 구버전(v0.16 등)은 등록 경로·옵션 이름이 다를 수 있다 → 어댑터 버전 분기 필요 여부를 8단계 시작 시 openapi 실측으로 확인.

## 5b 후속 — TCP 연결 단계 추론 (2026-10-19, 사람 승인)
- 한 일: 직통 (출발점·대상, 키) 조합마다 먼저 TCP 연결만 시도(`http_probe.tcp_probe`, 바이트 미전송). 연결 실패(timeout/refused/unreachable)면 HTTP 없이 모든 등록 경로(상태 변경 포함)를 그 값으로 기록, 증거에 `basis: tcp_connect (inferred)`. 연결되면 비상태변경 경로만 HTTP(`allow_mutating_routes=false` 유지). `routes_tested` = 모든 (대상, 비허용 키) 조합의 직통 결과가 있는 경로. SPEC §4.5·§6(INV-03 주석), REQ-01 override(`observation_notes`), DECISIONS 갱신. 팩 revision 3. **판정표는 변경 없음.** 기능 축은 고치지 않음(열린 질문에 기록).
- 기본 테스트: `uv run pytest` → **326 passed, 6 deselected, 약 13초**(TCP 추론·부분 차단·tcp_probe 단위 테스트 추가, 상태 변경 경로 기대 1건을 승인된 동작으로 갱신)
- `uv run pytest -m engine` → **3 passed, 1 skipped(INV-09) in 93s**
  | 위상 | 변경 전(`out/5b/before_tcp/`) | **변경 후** | 직통 근거 | 소요(ready / run / 합계) |
  |---|---|---|---|---|
  | exposed | FAIL | **FAIL** | TCP 6조합 전부 `connected` → HTTP 96건(외부·호스트 각 48: 200 ×8, 400 ×6, 404 ×2 × 키 3). `/scale_elastic_ep`는 보내지 않음(미시험) | 31s / 2.6s / 40s |
  | isolated | UNKNOWN | **PASS** | TCP 3조합(외부 × 키 3) 전부 `timeout` → 17경로(`/scale_elastic_ep` 포함) × 3 = 51건 `tcp_connect (inferred)`, HTTP 직통 0건 | 28s / 11.4s / 42s |
  | INV-15 | ABORTED→UNKNOWN | **ABORTED→UNKNOWN** | — | — |
  - 양성 대조(신뢰 출발점 → `/health` 200) 두 위상 true. EVD `data_origin` MEASURED, RES 검증 수준 DECLARED, `official=false`.
  - isolated run 시간이 147s → 11.4s로 줄었다(경로별 HTTP 타임아웃 대기 대신 조합당 TCP 1회).
  - 기능 축은 두 위상 모두 그대로 UNKNOWN(Gateway 허용 키 400 ×6, 404 ×2).
- 안전: 실행 전후 와일드카드 리스너 동일(macOS 기본 3개만, `out/5b/listeners_{before,after}_tcp_run.txt`), `guard.run` 컨테이너·네트워크 0개, 상태 변경 경로 HTTP 요청 0건.

## 현재 진행도 (2026-10-19)
- **SPEC §8 수직 슬라이스: 8단계 중 7단계 완료** — 1 import ✅ · 2 models ✅ · 3 store ✅ · 4 select ✅ · 5 vLLM 어댑터 + direct_route (5a 실험실, 5b 실제 vLLM CPU) ✅ · 6 rule_table ✅ · 7 report ✅ · **8 `guard update` 두 이미지 비교 ⏳ 남음**
- **자동화된 점검: 1 / 77** (REQ-01, 실제 vLLM에서 exposed FAIL / isolated PASS 재현). 나머지 76행은 `executor: null`(수동 시험 대상으로 보고서에 나열).
- 불변식 INV-01~18 테스트 전부 있음. 남은 skip은 INV-09 실제 두 이미지 비교(8단계) 1개.
- 공식 보고서 조건 미충족: 정책 DRAFT·확정자 없음, 팩 DRAFT·검토자 없음 → `official=false`.
- 다음: 8단계(비교 태그 쌍과 CPU 비교 조건 규칙 결정 필요 — "8단계 준비" 절), 그 뒤 `reference_baseline_first`·로컬 재현 가능 행 5~10개 자동화.

## 기능 축 — functional_probes 목록 (2026-10-19, 사람 결정)
- 한 일: REQ-01 override에 `functional_probes`(`POST /v1/completions`, 본문 `{model: "{model}", prompt: ping, max_tokens: 1}`, expect [200])와 `functional_scope`. 판정표 기능 축 = "목록의 모든 항목이 기대대로"(관측 `functional_probes_planned`, `functional_probe_results`). 실행기·코어에서 경로 하드코딩 제거(정적 테스트), 정상 대조도 같은 표본. 보고서 행 `functional_scope`와 HTML에 "기능 확인 범위: N / 등록 경로 M (표본)". SPEC §3·§4.5·§4.7·§7.2, DECISIONS 갱신. 팩 revision 4.
- 테스트: `uv run pytest` → **339 passed, 6 deselected, 약 13초**. `uv run pytest -m engine` → **3 passed, 1 skipped(INV-09) in 90s**.
  | 위상 | security | functional | 보고서 표시 |
  |---|---|---|---|
  | exposed | FAIL | **PASS** | 기능 확인 범위: 1 / 등록 경로 17 (표본) |
  | isolated | PASS | **PASS** | 기능 확인 범위: 1 / 등록 경로 17 (표본) |
- 안전: `guard.run` 컨테이너·네트워크 0개, 와일드카드 리스너는 macOS 기본 3개만.
- 진행도 변화 없음: 8단계 중 7단계, 자동화 점검 1/77.

## 경로 분류 4건 + inventory 정의 (2026-10-19, 사람 승인)
- 한 일: `GET /load`·`GET /kv_event_sources` → INFO, `POST /v1/responses/{response_id}/cancel` → MUTATING, 경로 변수는 `guard-probe-nonexistent-id`로 치환(`adapters/vllm.py`). inventory 정의 "snapshot 중 vLLM이 읽는 파일만"(`tools/model_inventory.py --scope vllm` 기본) → opt-125m **7 files / 251,897,335 bytes / `b11bc2ca…`**(guard.toml·ENV 갱신, 전체 12개 값은 `out/5b/model_inventory_all.json`).
- 테스트: `uv run pytest` → **343 passed, 6 deselected, 약 13초**. `uv run pytest -m engine` → **3 passed, 1 skipped(INV-09) in 92s**.
  | 위상 | security | functional | 등록 경로(시험 대상) | 직통 대표 결과 | 미전송(상태 변경) | run / 합계 |
  |---|---|---|---|---|---|---|
  | exposed | **FAIL** | **PASS** | 17 → **15** | 200 ×6, 400 ×6, 404 ×1(`/v1/responses/{response_id}` → 고정 ID로 404) | `/scale_elastic_ep`, `/v1/responses/{response_id}/cancel` | 2.7s / 36s |
  | isolated | **PASS** | **PASS** | 15 | `timeout` ×15 (TCP 추론, cancel·scale 포함) | 같음(HTTP 0건) | 11.5s / 42s |
  - 보고서 표시: "기능 확인 범위: 1 / 등록 경로 15 (표본)". 엔진 로그에 고정 ID 경로 요청 7건(Gateway 허용 키·직통), 실제 객체 영향 없음(404).
- 안전: `guard.run` 컨테이너·네트워크 0개, 와일드카드 리스너는 macOS 기본 3개만.
- 진행도 변화 없음: 8단계 중 7단계, 자동화 점검 1/77. 보류: 8단계 비교 조건(열린 질문 2), 정책 확정자·팩 검토자(5).

## inventory 기준 수정 (2026-10-19, 사람 결정)
- 비교용(INV-09) `model.inventory_sha256` = 마운트한 snapshot 전체(`tools/model_inventory.py` 기본 `--scope all`): **12 files, `d677f399…`** (`out/5b/model_inventory.json`). 참고용 `model.files_read_by_engine` = 엔진이 읽은 파일(`--scope vllm`): **7 files, `b11bc2ca…`** (`out/5b/model_inventory_engine_read.json`), 판정·비교 조건에 쓰지 않음. guard.toml `[vllm]`, ENV, SPEC §4.1, DECISIONS 갱신.
- 테스트: `uv run pytest` → **345 passed, 6 deselected, 약 13초**. `uv run pytest -m engine` → **3 passed, 1 skipped(INV-09) in 86s**: exposed security FAIL / functional PASS, isolated PASS / PASS, "기능 확인 범위: 1 / 등록 경로 15 (표본)" — 이전과 같음. 두 위상 ENV `model.inventory_sha256 = d677f399…`, `files_read_by_engine = {7, b11bc2ca…}`, EVD `comparability.model_inventory_sha256 = d677f399…`(engine 테스트에서 단언).
- 안전: `guard.run` 컨테이너·네트워크 0개, 와일드카드 리스너는 macOS 기본 3개만.

## REQ-03 자동화 (진행 중, 2026-10-19) — 막힘: 사람 결정 대기
- 한 일: 공통 모듈 `executors/http_common.py`, `executors/role_route.py`(REQ-03), `direct_route.py`를 공통 모듈 위로 이전, 어댑터 경로 종류 세분(liveness·docs·diagnostic)과 검증된 안전 프로브 표(`SAFE_PROBES`, 이미지 소스 직접 확인), CheckDef `route_scope`·기능 표본 `as_role`, Gateway 프로필(single·role_open·role_enforced), FakeExecutor 점검별 시나리오, REQ-03 override, 팩 revision 5.
- 테스트: `uv run pytest` → **357 passed, 8 deselected, 약 14초**. `-m engine`: REQ-01 회귀 3 passed(exposed FAIL / isolated PASS / INV-15), REQ-03 **role_open → FAIL ✅**, **role_enforced → UNKNOWN ❌(기대 PASS)**.
- 실제로 나온 값(role_enforced): 비관리 역할(키 없음·잘못된 키·일반) 관리·진단 5경로 전부 401/403, 관리 키는 `/metrics`·`/load`·`/kv_event_sources` 200·안전 프로브 2개 handler_reached, 안전 프로브 전후 `/health` 200·추론 200. 미충족은 `/v1/responses/{response_id}/cancel`이 관리 경로로 범위에 들어왔는데 안전 프로브가 없어 미시험인 것 하나.
- 막힌 것: cancel 경로 종류 결정(DECISIONS 열린 질문). 판정 규칙·기대값은 바꾸지 않았다.
- 자동화 점검 수: 아직 **1/77**(REQ-03 미완).

## REQ-03 자동화 완료 (2026-10-19) — 자동화 점검 **2/77**
- 결정 반영: `/v1/responses/{response_id}/cancel` 종류 → inference(상태 변경 표시 유지) → REQ-03 범위에서 빠짐, REQ-01은 15경로 그대로. REQ-01 handler_reached FAIL 조건 유지 + "검증된 안전 프로브가 있는 경로에서만, 일반 4xx는 FAIL 아님" 단위 테스트(`tests/test_handler_reached.py`).
- 테스트: `uv run pytest` → **359 passed, 8 deselected, 약 14초**. `uv run pytest -m engine` → **5 passed, 1 skipped(INV-09) in 154s**.
  | 점검 | 구성 | security | functional | 범위 표시 | run |
  |---|---|---|---|---|---|
  | REQ-01 | exposed | FAIL | PASS | 기능 확인 범위: 1 / 등록 경로 15 (표본) | 3.8s |
  | REQ-01 | isolated | PASS | PASS | 1 / 15 | 12.5s |
  | REQ-03 | isolated + role_open(위반) | **FAIL** | PASS | 기능 확인 범위: 2 / 등록 경로 5 (표본) | 2.9s |
  | REQ-03 | isolated + role_enforced(안전) | **PASS** | PASS | 2 / 5 | 2.9s |
  - REQ-03 대상 5경로: `/is_scaling_elastic_ep`, `/kv_event_sources`, `/load`, `/metrics`, `/scale_elastic_ep`. role_open에서 일반 키: 진단 3경로 200, 안전 프로브 2경로 handler_reached. role_enforced에서 비관리 역할 전부 401/403(일반 키 403), 관리 키는 진단 200·안전 프로브 handler_reached(증거만).
  - 안전 프로브 전후 엔진 `/health` 200·추론 200(두 구성 모두), 엔진 로그에 실제 스케일 실행 없음.
- 안전: `guard.run` 컨테이너·네트워크 0개, 와일드카드 리스너 macOS 기본 3개만, 키 미기록(테스트 검사).

### 일반화하면서 고친 것 (REQ-01 → REQ-03)
| # | 바꾼 것 | 위치 | 코어 여부 |
|---|---|---|---|
| 1 | 점검별 대상 경로 종류 `route_scope`(include_kinds, exclude_kinds_policy) — 실행기의 `kind != "info"` 고정 제거 | models/checkdef.py, override | **코어(models)** |
| 2 | 기능 표본 역할 `as_role` — "허용 키" 고정 제거 | models/checkdef.py | **코어(models)**, 1과 같은 파일·같은 변경 묶음 |
| 3 | 어댑터 경로 종류 세분(info → liveness·docs·diagnostic), 사용자 상태 변경 경로 집합 | adapters/vllm.py | 아님 |
| 4 | 검증된 안전 프로브 표(`SAFE_PROBES`, 버전·경로·요청·기대 응답·근거 소스) + `ProbeRequest` 필드, `probe_request(engine_version)` | adapters/ | 아님 |
| 5 | 공통 모듈 `http_common.py`(경로 수집·요청·분류·handler_reached·기록·마스킹·준비 대기·안전 확인), `direct_route.py` 이전, `role_route.py` 신규 | executors/ | 아님 |
| 6 | 실행기 설정 `roles`(역할→키 환경변수, `allowed_key_env` 호환), `wrong_key_env` | executors/, guard.toml | 아님 |
| 7 | Gateway 프로필 single·role_open·role_enforced, `lab.up(gateway_profile, gateway_keys)` | lab/ | 아님 |
| 8 | FakeExecutor 점검별 시나리오(`fake.scenarios`) | executors/fake.py | 아님 |
| 9 | REQ-01 판정표 FAIL 조건 `handler_reached` 추가(데이터) | tools/overrides/REQ-01.yaml | 아님(판정표 데이터) |
| 10 | 테스트 도우미(`environment(engine=…)`), 점검별 정책 사본(engine 테스트) | tests/ | 아님 |
- core/·judges/ 변경 0건, rule_table 연산자 추가 0건.

## Step 0 — 저장소 위생과 증거 보존 (2026-10-19)
- 한 일: `.gitignore`, `.gitattributes`(해시 보호 파일 `-text`), `httpx` 제거(`uv lock --offline` 성공, httpx·httpcore·idna 제거), engine 테스트 산출물을 `out/test-runs/<실행별>/`로(보존 증거 `out/5b` 보호 단언), 지원 플랫폼 macOS·Linux 명시와 Windows 알려진 제한 4건 `skipif`, `docs/automation_matrix.csv` CRLF → LF.
- 테스트: `uv run pytest` → **359 passed, 8 deselected, 약 15초**. Windows skip 4건은 macOS에서 그대로 실행됨.
- 확인: `-m engine -k inv15` 실행 전후 `out/5b` 파일 69개 sha256 동일(`out/test-runs/out5b_before.sha`). 단 이번 실행은 **Docker Desktop이 꺼져 있어** 엔진 기동 전에 실패했다 → 엔진 쪽 확인은 Docker를 켠 뒤.
- 주의: 작업 트리의 `out/5b/{exposed,isolated,inv15}`는 커밋 `6d9ad83` 이후 REQ-03 단계의 engine 재실행으로 덮어써진 상태다(커밋에는 그 이전 실행본이 있음). `out/5b/req03-*`는 아직 추적되지 않는다. 어느 것을 보존본으로 할지 사람 결정.

### `.guard/`·`out/` 분류안 (결정은 사람)
| 경로 | 크기 | 현재 추적 | 내용 | 제안 |
|---|---|---|---|---|
| `.guard/` (루트) | 412K | 3파일 | CLI 시연 저장소(FakeExecutor, EXAMPLE) | **무시**(자동 생성물) |
| `out/report.json`, `out/report.html` | 200K | 2 | 1차 세션 가짜 시연 보고서(EXAMPLE) | **무시** 또는 삭제 |
| `out/5b/model_inventory*.json`, `listeners_*.txt` | 24K | 6 | 모델 inventory(비교용·참고용), 리스너 전후 기록 | **보존** |
| `out/5b/explore/` | 396K | 4 | 실측 openapi.json(22경로), 첫 기동 env.yaml·로그 | **보존** |
| `out/5b/before_tcp/` | 136K | 6 | TCP 추론 도입 전 REQ-01 결과(UNKNOWN 근거) | **보존** |
| `out/5b/<실행>/` report·observed·env·target·policy·guard.toml·engine_log_tail | 실행당 ~100K | 일부 | 실측 보고서·관측 요약·엔진 로그(키 없음) | **보존** |
| `out/5b/<실행>/.guard/store.sqlite`, `.guard/artifacts/` | 실행당 ~150K | 일부 | 재판정·`guard verify`에 필요한 원본 객체·증거 파일 | **보존** |
| `out/5b/<실행>/.guard/work/` | 실행당 16~60K | 일부 | 실행기 작업 사본(같은 내용이 artifacts에 해시로 저장됨) | **무시** |
| `out/5b/inv15/empty_models/` | 0 | 0 | INV-15용 빈 디렉토리 | **무시** |
| `out/test-runs/` | — | 0 | 이번부터 engine 테스트 실행별 산출물 | **무시**(이미 `.gitignore`) |

### 사람이 실행할 git 명령 (Step 0, 실행하지 않았음)
```bash
git rm -r --cached .pytest_tmp
git ls-files -z -- '*.pyc' | xargs -0 git rm --cached
git rm --cached .DS_Store reference/.DS_Store
# (분류안 승인 시) git rm -r --cached .guard out/report.json out/report.html
# (분류안 승인 시) git ls-files -z -- 'out/**/.guard/work/*' | xargs -0 git rm --cached
git add .gitignore .gitattributes
git add --renormalize .          # .gitattributes 기준으로 줄바꿈 재정규화(바뀌는 파일 확인)
git status
```

## Step 1 — 정확성 보강 (2026-10-19) — 코드 완료, 엔진 확인 대기
- 1-A 재현 먼저: `tests/test_method_keys.py` 4건(REQ-01 POST만 위반 → 수정 전 PASS, 두 메서드 등록·시험·관측, TCP 추론 근거 메서드별, REQ-03 Gateway가 GET만 막음 → 수정 전 PASS). 수정 전 4건 실패 확인 → 수정 후 통과.
- 1-B: 키 `"METHOD path"` 통일(`http_common.route_key`, 실행기·FakeExecutor·테스트 도우미·SPEC §4.5 예시). 판정표·core·judges 변경 없음.
- 1-C: `select`의 정책 값 타입 검증(`str·int·bool·list[str]`, null·잘못된 타입·미지원 타입 실행 전 거부) — `tests/test_policy_values.py` 19건.
- 1-D: rule_table `value_policy`·`subset_of`·`not_subset_of`(judges), REQ-01 `allowed_callers` ↔ 실행기 역할 시작 전 확인(executors), 정책 값이 결과를 바꾸는 테스트(REQ-01, REQ-03, value_policy).
- 테스트: `uv run pytest` → **394 passed, 8 deselected, 약 15초**.
- **막힌 것: Docker Desktop이 꺼져 있어** `-m engine`(REQ-01·REQ-03 회귀, 경로 개수 변화 확인)과 Step 2(이미지 안 소스 읽기)·Step 3(PLT-01)을 진행하지 못함.
- 알림: `direct_route.DirectRouteExecutor.collect`가 약 130줄로 커졌다(직통 조합·TCP 추론·Gateway·집계·증거 구성). 이번 범위에서 나누지 않았다.

## Step 1 엔진 확인 + Step 2 (2026-10-19, Docker 재개)
- Step 1 `-m engine` → **5 passed, 1 skipped in 162s**: REQ-01 exposed FAIL / isolated PASS(등록 15 — 변화 없음), REQ-03 role_open FAIL / role_enforced PASS(등록 5 — 변화 없음, 범위 안에 메서드가 둘인 경로 없음). 키는 이제 `"METHOD path"`.
- Step 2: `/v1/chat/completions/batch` 상태 변경 아님(소스 확인), `/v1/responses` 상태 변경 표시(서버 설정 따라 저장·background). 어댑터 `ROUTE_REVIEWS`에 근거. REQ-01 재실행 → **exposed FAIL**(등록 15·시험 13: 직통 200 ×5, handler_reached ×2, 400 ×5, 404 ×1, 미전송 2) / **isolated PASS**(15 전부 TCP 추론 timeout).
- 매 실행 후 `out/5b` 69개 파일 해시 동일, `guard.run` 컨테이너·네트워크 0개.

## Step 3 — PLT-01 (2026-10-19) — 진행 중, 막힘: hardened 엔진 기동 실패
- 한 일: 실행기 `executors/container_config.py`(docker inspect, 컨테이너 안 모든 프로세스 `/proc/*/status` CapEff 합집합·UID, sentinel 쓰기 시도 후 즉시 삭제, 기능 표본 via engine), PLT-01 override(허용 목록 판정표: `value_policy`·`subset_of`·`not_subset_of`·`when_policy`), 정책 4값, 실험실 엔진 프로필 3종, FakeExecutor PLT-01 시나리오, 보고서 점검별 한계 문장. 팩 revision 6.
- 테스트: `uv run pytest` → **411 passed, 12 deselected, 약 19초**(PLT-01 단위 15건: hardened PASS·overprivileged FAIL·default FAIL·자식 프로세스 capability 포함·정책 허용 목록에 따라 판정 변화·정책 타입 검증·설정의 컨테이너 이름·Env 미기록·fake 4 시나리오·보고서 한계).
- `-m engine`(실제 vLLM CPU, isolated):
  | 프로필 | security | functional | 실측 |
  |---|---|---|---|
  | overprivileged(위반) | **FAIL** ✅ | PASS | capability 15개(기본 14 + SYS_ADMIN), 마운트 `/guard-sentinel:rw`·`/models:ro`, UID 0, sentinel `/guard-sentinel` succeeded(즉시 삭제)·`/models` denied |
  | default(결과만) | **FAIL** | PASS | 기본 capability 14개, `/models:ro`, UID 0, 루트 파일시스템 쓰기 가능(증거만) |
  | hardened(안전) | **결과 없음 ❌** | — | 엔진 기동 실패: tmpfs noexec로 torch inductor `.so` mmap 실패 (DECISIONS 열린 질문) |
  - 프로세스 5개(vllm PID 1, python, VLLM::EngineCore, VLLM::Worker, exec한 sh) 전부 같은 capability·UID.
  - 모델 inventory 엔진 시험 후 `d677f399…` 그대로(engine 테스트 `test_model_inventory_unchanged_after_engine_runs` 통과). `out/5b` 69개 파일 해시 동일. 와일드카드 리스너 macOS 기본 3개만(rapportd 포트는 재부팅으로 52737). `guard.run` 컨테이너·네트워크 0개.
- 자동화 점검 수: **2/77 유지**(PLT-01은 안전 구성 PASS를 아직 증명하지 못함).
- 팩 revision 5 → 6: run·judge·report는 PLAN의 팩 Ref와 디스크 팩이 같아야 하므로, **revision 1~5 팩으로 만든 이전 PLAN(out/5b·out/test-runs의 실행 포함)은 지금 팩으로 재판정할 수 없다**(git 이력의 팩 사본이 있어야 함).

## REQ-01 대비 코어 변경 (REQ-03 일반화 ~ Step 3)
| # | 위치 | 변경 | 때 |
|---|---|---|---|
| 1 | models/checkdef.py | `route_scope`, 기능 표본 `as_role` | REQ-03 |
| 2 | models/checkdef.py | 기능 표본 `via`, CheckDef `limitations` | Step 3 |
| 3 | core/select.py | 정책 값 타입 검증(`PolicyValueError`) | Step 1-C |
| 4 | core/report.py | 점검별 한계 문장을 보고서 한계에 | Step 3 |
| 5 | judges/rule_table.py | `value_policy`, `subset_of`, `not_subset_of` | Step 1-D |
| 6 | judges/rule_table.py | `when_policy` | Step 3 |
- 합계 **6건**(models 2, core 2, judges 2). 점검 전용 분기 0건(INV-14). 점검별 차이는 전부 override 데이터·실행기·어댑터에 있다.

## 알려진 제한
- 지원 플랫폼 macOS·Linux. Windows 알려진 실패 5건(4건 skip 사유, 경로 표기 1건 대상 미확인).
- 실험실은 Docker Desktop의 리눅스 VM 기준(PLT-01 보고서 한계에 명시), CPU arm64 vLLM.
- 정책·팩 DRAFT → `official=false`.
- 등록된 상태 변경 경로(`/v1/responses`, cancel)는 연결되는 위상에서 미시험.

## PLT-01 자동화 완료 (2026-10-19) — 자동화 점검 **3/77**
- 결정 반영: hardened (c) — `/tmp` noexec 유지, 컴파일 캐시 전용 exec tmpfs `/guard-cache` + `VLLM_CACHE_ROOT`·`TORCHINDUCTOR_CACHE_DIR`(실제 오류로 확인, TRITON 미사용). 정책 `allowed_exec_writable_paths`, 관측 `exec_writable_mounts`. 반대 방향 프로필 `tmp_exec`. 보고서 한계 문장에 정책 값 채우기. 팩 revision 7.
- 테스트: `uv run pytest` → **415 passed, 12 deselected, 약 18초**. `uv run pytest -m engine` → **10 passed, 1 skipped(INV-09) in 334s**.
  | 점검 | 구성 | security | functional | 실측 |
  |---|---|---|---|---|
  | REQ-01 | exposed / isolated | FAIL / PASS | PASS / PASS | 유지 |
  | REQ-03 | role_open / role_enforced | FAIL / PASS | PASS / PASS | 유지 |
  | PLT-01 | hardened(안전) | **PASS** | PASS | capability 0, UID 1000, 마운트 `/models:ro`, exec 쓰기 `/guard-cache`만(허용 목록), 루트 FS 쓰기 거부, ready 45s |
  | PLT-01 | tmp_exec(반대) | **FAIL** | PASS | hardened와 같되 exec 쓰기 `/tmp`(허용 목록 밖) |
  | PLT-01 | overprivileged(위반) | **FAIL** | PASS | capability 15(기본 14 + SYS_ADMIN), UID 0, `/guard-sentinel:rw` 쓰기 성공(즉시 삭제), exec 쓰기 `/`·`/dev`·`/guard-sentinel` |
  | PLT-01 | default(결과만) | FAIL | PASS | 기본 capability 14, UID 0, exec 쓰기 `/`·`/dev` |
  - 보고서 한계: "실행 가능한 쓰기 경로 1개가 정책으로 허용됨 ["/guard-cache"] …", "이 점검은 컨테이너 설정 … Docker Desktop의 리눅스 VM 기준이다."
  - 모델 inventory `d677f399…` 그대로, `out/5b` 해시 동일, `guard.run` 자원 0, 와일드카드 리스너 macOS 기본 3개만.
- 팩 revision 6 → 7: revision 1~6 팩으로 만든 PLAN은 지금 팩으로 재판정할 수 없다(Step 4에서 다룸).
- out/5b: 작업 트리 실행본을 `out/archive/5b-after-req03/`에 보존(.gitignore). 복원은 사람이 git으로.

### REQ-01 대비 코어 변경 (최종 집계)
| # | 위치 | 변경 |
|---|---|---|
| 1 | models/checkdef.py | `route_scope`, 기능 표본 `as_role` (REQ-03) |
| 2 | models/checkdef.py | 기능 표본 `via`, CheckDef `limitations` (PLT-01) |
| 3 | core/select.py | 정책 값 타입 검증 (Step 1-C) |
| 4 | core/report.py | 점검별 한계 문장 + 정책 값 채우기 (PLT-01) |
| 5 | judges/rule_table.py | `value_policy`, `subset_of`, `not_subset_of` (Step 1-D) |
| 6 | judges/rule_table.py | `when_policy` (PLT-01) |
- 합계 **6건**(models 2, core 2, judges 2), 점검 전용 분기 0건. 3행째(PLT-01)는 HTTP가 아닌 새 종류 실행기였는데도 코어 추가는 4건(#2·#4·#6, #5는 Step 1에서 미리)이었다.

## Step 4 — 팩 보존본 (2026-10-19)
- 한 일: `store/packs.py`(put_pack·get_pack), `models/checkdef.build_pack`·`Pack.source`, CLI select 보존본 저장·judge/report 보존본 대체(run은 현재 팩만)·`guard pack import`, 보고서 한계 "팩 보존본 사용(revision N)".
- 테스트: `uv run pytest` → **421 passed, 13 deselected, 약 21초**(`tests/test_pack_snapshot.py` 6건: 왕복·재저장·변조 거부·Ref 불일치 거부·CLI로 팩 revision을 올린 뒤 이전 PLAN judge·report 성공·run 거부·보존본 없으면 중단·pack import 검증).
- 수동 확인: 커밋본 out/5b(팩 rev 4)를 `out/test-runs/rejudge-5b-20261019T111117Z`에 복사해 rev 4 팩(커밋 6d9ad83) import 후 재판정 → 3실행 모두 판정 동일, 차이는 한계 1줄과 RES 판정 플러그인 버전(0.2.0 → 0.3.0)뿐. 원본 out/5b 불변.
- 재판정 가능 범위: 커밋본 out/5b(rev 4) 가능, rev 5 실행(archive·req03-*) 불가, 이후 select는 자동 보존.

### REQ-01 대비 코어 변경 (Step 4 반영)
- 7: models/checkdef.py — `build_pack`, `Pack.path` 선택·`Pack.source` (Step 4)
- 8: core/report.py — 팩 보존본 사용 한계 문장 (Step 4)
- 합계 **8건**(models 3, core 3, judges 2), 점검 전용 분기 0건. store/는 집계 밖(팩 보존본 모듈 추가).

## A — 수동 증거 입력 (2026-10-19)
- 한 일: `guard evidence template`·`guard evidence import`(collectors/manual.py), `declared` 판정기, 실행기 메타데이터 기본 판정기(EVD에 기록, 코어는 그 값만 읽음), run이 수동 증거를 말 없이 덮지 않음(--replace-manual), 보고서 "대체됨"·영역별 "수동 입력 N건 / 자동 측정 M건"·한계 "인증되지 않은 자기 신고", 사람이 입력한 증거 MEASURED 거부. SPEC §4.5·§4.7·§7, DECISIONS 갱신.
- 테스트: `uv run pytest` → **440 passed, 13 deselected, 약 23초**(tests/test_manual_evidence.py 18건 + INV-02 1건).
- 데모: `out/test-runs/demo-manual-20261019T112829Z` — 양식 74건 중 SUP-01 한 건을 채워 import → SUP-01 PASS(DECLARED, 수동 입력), 보고서 상단 영역별 건수와 한계 표시, guard verify OK.
- 코어 변경 누계: 이번 A에서 models 1(필드 묶음), core 3(judge 판정기 선택, run 수동 증거 보호, report 출처·대체·건수), judges 1(declared) → 합계 13건, 점검 전용 분기 0건.
- 다음: B1(input_validation) — 시작 전 멈춤.
- A 이후 `-m engine` 회귀: 판정은 전부 기대대로(REQ-01 exposed FAIL/isolated PASS, REQ-03 open FAIL/enforced PASS, PLT-01 hardened PASS·tmp_exec/overprivileged/default FAIL, INV-15 ABORTED→UNKNOWN, inventory 불변). 단 REQ-01 engine 테스트 2건이 실패 — 원인은 테스트가 `artifacts/` 폴더 전체를 JSONL로 읽어서(Step 4부터 팩 보존본 파일이 같은 폴더에 있음). 테스트를 EVD가 가리키는 증거 파일만 읽도록 고친 뒤 **2 passed**. 최종: 기본 440 passed, engine 전체 10 passed·1 skipped(INV-09). `out/5b` 불변, `guard.run` 자원 0.

## B1 일부 + 보류 (2026-10-20)
- 한 일: `DirectRouteExecutor.collect` 분할(130줄 → 26줄: tcp_precheck·build_direct_requests·aggregate_direct·direct_observations), 공용 `RequestBatch`·`add_functional`·`gateway_matrix`·`send_and_record`를 `http_common`으로(role_route도 사용). 분할 전 골든(`tests/golden/http_executors.json`, 14 시나리오)과 분할 후 동일.
- 테스트: `uv run pytest` → 441 passed. `-m engine` REQ-01·REQ-03 회귀 4 passed(REQ-01 exposed FAIL·isolated PASS, REQ-03 role_open FAIL·role_enforced PASS, 숫자 동일). 코어 변경 0건.
- **REQ-07, REQ-09, REQ-10: 자동화 보류(수동 증거 경로 사용).**
- B2(log_analysis)는 이 세션에서 진행하지 않음.
- 자동화 점검 수: 3/77 유지.



## 재평가 및 업데이트 제품 경로 구현 (2026-10-19)
- 시작 커밋 main/ec4f91b. 별도 codex/gua-update-mvp 작업 트리에서 기존 로컬 변경·out/5b 증거를 보존했다.
- 한 일: 부분 /proc·마운트 수집의 거짓 PASS 방지, 팩 폴더 부재/손상 시 보존본 재판정, UTF-8/LF 팩 생성, 사용자 승인 CPU 프로파일(보안/기능만 비교), 의미 있는 설정 해시, side별 실행 설정·제품 guard update(managed/external), JSON/HTML 기능 비교/비교 불가 필드, 실행 정리 실패 전파, 판정표 사전 검사.
- 실행 경계: application.py가 runtime과 core를 연결한다. lab은 호환 래퍼로 유지하고 샌드박스 자산은 설치 패키지에 포함했다. 기존 객체 7종·append-only·judge 순수 함수 경계는 유지했다.
- 점검: PLT-04(환경변수 이름/마운트 경로), REQ-09(token-array ID/길이 두 경계) 실행기·판정표 추가. 연결된 정의 5개, 신규 2개는 모의 계약 검증이며 실제 엔진 자동화 완료 5/77을 뜻하지 않는다. 팩 revision 10, DRAFT.
- 테스트: 최종 기본 테스트 451 passed, 4 skipped(Windows 네트워크 3·symlink 1), 13 deselected, 159.44s. 최초 수집/팩 회귀 6건은 수정 전 실패를 확인했다. 추가 generic run의 동일 target 재사용도 실패 재현 후 수정했다. Windows 전체 지원 주장은 하지 않는다.
- 배포 확인: uv build --wheel 성공, frozen 의존성으로 새 가상환경에 wheel 설치 후 guard CLI help와 update 명령 확인. Linux PR/main CI 정의 추가; 원격 실행 결과는 별도 확인한다.
- 과거 증거: 복사본 Store.verify에서 exposed·isolated의 참조 artifact 각 1개 누락, inv15 0문제. HEAD/6d9ad83/13bb4d3에서 누락 원본을 찾지 못했다. 기존 증거·해시는 수정하지 않았다.
- 막힌 것: Docker daemon 미실행·모델 캐시 부재로 실제 엔진·두 실제 이미지 실증 미수행. 정책/팩 확정자 미정. AWS 자원은 생성하지 않았다.
- 다음 단계: Linux/macOS에서 실제 두 이미지 update와 신규 2개 점검 실증, 누락 증거 원본 복구 또는 별도 재실증, 정책·팩 검토. 상세 계획/완료 기준은 docs/REVIEW_AND_DEVELOPMENT_PLAN.md, 실행 절차는 docs/RUNBOOK.md.


## 실증 준비·증거 반출 후속 구현 (2026-10-19)
- 시작: codex/gua-update-mvp/4d97dba. 기존 main과 사용자 .assessment 및 out/5b 원본을 보존했다. 구현 묶음 완료 후 테스트를 실행했다.
- 한 일: 읽기 전용 prepare-update(팩/정책/실행기/모델 inventory/Docker/로컬 이미지 아키텍처 검사), evidence export/restore(DB read-only backup/증거/팩 보존본 검증), 5개 점검의 실제 PASS/FAIL/UNKNOWN opt-in 행렬, 두 이미지 시험의 MEASURED/반출·복원 계약 보강.
- 수정: REQ-09 경계 초과 요청도 토큰 예산에 포함, side-only real 설정의 대상 분리 우회 차단, probe 정리 실패 후 COMPLETED/확인된 FAIL 보존 및 재판정 보고서 한계/명령 실패 전파. 빈 cleanup_errors는 생략해 과거 해시를 유지했다.
- 유지: 연결된 점검은 5개, 팩 revision 10/DRAFT. 기존 정의 77개를 추가 자동화하지 않았고, 신규 PLT-04/REQ-09 실증 완료를 주장하지 않는다. Python·의존성 선언은 변경하지 않았다.
- 전체 기본 테스트: 475 passed, 1 failed, 4 skipped, 18 deselected / 110.82초. 새 증거 묶음 시험의 DB 파일 바이트 동일성 가정이 실패했다.
- 수정 후 관련 검증: evidence bundle 10 passed / 12.16초. SQLite backup 헤더 차이를 허용하되 원본 DB 불변·객체/참조/증거 바이트 동일성을 검사했다. 동일 재판정은 멱등, 만료로 판정 내용이 바뀌면 새 revision이 추가되고 과거 참조가 보존됨을 확인했다. 첫 집중 실행에서는 멱등성에 대한 잘못된 기대도 발견해 바로잡았다. 제품 코드를 시험 기대에 맞춰 완화하지 않았다.
- 별도 검증: wheel 빌드 및 frozen 새 환경 설치 성공. -I -X utf8 설치본 CLI로 select/run/judge/report/export/restore/verify/오프라인 재판정/준비 실패 총 10개 명령의 예상 종료 코드와 전체 로그를 확인했다(EXAMPLE). 최초 설치본 로그의 Windows cp949/UTF-8 캡처 차이는 명시적 UTF-8으로 해결했다.
- 실제 엔진 opt-in 확인: 6 skipped, 12 deselected / 0.59초. GUARD_UPDATE_*와 GUARD_ENGINE_CASES 미제공. Docker daemon/API 및 모델 캐시도 없어 Docker stub/실제 엔진/실제 두 이미지 실증은 미실행이다.
- 과거 증거 재확인: 복사본 exposed/isolated 참조 artifact 각각 1개 누락, inv15 문제 0. 원본/해시를 수정하거나 누락 파일을 만들지 않았다.
- 성능: 선택적 기존 libyaml SafeLoader를 사용하되 해시는 매번 검사한다. Windows 기본 검증은 1분 목표를 초과했다. Linux CI 시간은 별도 확인한다. skip 4건은 기존 Windows 네트워크 3·symlink 1이며 새 skip으로 속도를 맞추지 않았다.
- 산출물: docs/VALIDATION.md, examples/engine-cases.json. 테스트 로그는 별도 out/test-runs/followup-20261019에 두고 Git에는 포함하지 않는다.
- 원격 CI: 이 기록 작성 시 push 전이므로 미실행. 작업 브랜치 push에서도 CI가 실행되도록 연결했으며 실제 결과는 해당 커밋의 Actions 기록으로 확인한다.
- 다음/막힌 것: Linux/모델/두 이미지 준비 후 실제 15개 사례와 update 실행. 정책 임계값·금지 목록·담당자·팩 검토·이미지 쌍·AWS 예산은 팀 결정이 필요하다. 기존 CPU 결정은 유지하며 유료 자원은 생성하지 않았다.


### 원격 Linux CI 확인 (2026-10-19, Asia/Seoul)
- 코드 커밋 0c8c61b28dbd8ca12ec2202f0ec91715264b132a의 GitHub Actions push 검증 성공.
- Ubuntu/Python 3.11: `uv run pytest -q` → **480 passed, 18 deselected / 28.15초**. Windows에서 skip된 4건도 Linux에서 통과했다.
- `uv sync --frozen`, `uv build --wheel` 성공. 기본 테스트 1분 목표는 이 Linux CI 환경에서 충족했다.
- 실행: https://github.com/dohashelby/gua/actions/runs/39257453735
- 초안 PR: https://github.com/dohashelby/gua/pull/1 . 실제 엔진·두 실제 이미지 실증은 이 CI에 포함되지 않는다.


## 2026-10-21 — 수동 증거와 업데이트 브랜치 통합
- 부모: main/d9e9729 + codex/gua-update-mvp/42a609d. 별도 통합 브랜치 codex/gua-integration-20261021을 사용해 기존 두 체크아웃을 보존했다.
- 충돌 4파일(PROGRESS, cli, core/run, models/objects)을 양쪽 기능을 보존해 해결했다. 수동 입력/출처·대체 이력/HTTP 분할과 update/CPU/증거 반출/팩 보존본/정리 실패 기록을 함께 유지했다.
- 수정: collected의 문자열·숫자 강제 변환 제거, 중복 check/side 및 관측 키·설명 검증, 첨부 전체 읽기·모든 EVD 생성 후 객체 배치 트랜잭션 저장. 뒤 객체 저장 실패 시 앞 객체 새 revision도 롤백한다. 저장 I/O 실패의 새 미참조 blob은 남을 수 있으나 기존 증거를 삭제하지 않는다.
- CLI: evidence template/import/export/restore를 한 그룹으로 통합. run --replace-manual 연결, 기본 수동 증거 보존과 명시적 대체/보고서 이력 유지. 모델 serializer는 양쪽 부모의 선택 필드를 함께 처리한다.
- 구현 및 테스트 코드 작성 완료 후 첫 전체 검증: Windows/Python 3.11.16, 513 passed, 1 failed, 4 skipped, 18 deselected / 81.20초. 실패는 기존 main 골든이 설정 미확인 target에도 config_sha256을 기대해서 발생했다.
- 수정 후 집중 검증: 20 passed / 4.44초. 골든은 14개 config_sha256 기대값만 null로 조정했고 관측·판정·요청 기록은 바꾸지 않았다. 명시적 비교 조건이 있을 때는 정상 hash를 만드는 양성 계약을 추가했다. 수동 입력·DB 롤백·CLI 대체·양쪽 과거 직렬화 계약도 포함한다.
- 양쪽 부모의 실제 코드로 생성한 EXAMPLE 객체 16개를 fixture로 보존했다(ENV/POL/PLAN/EVD/RES/RPT). 전체 필드·content_sha256을 현재 모델에서 재검증했다. 실제 엔진 결과를 생성한 것이 아니다.
- 배포 검증: uv build --wheel 성공, frozen 별도 환경 설치 후 -I -X utf8 설치본 CLI 15개 명령 성공. 자동→수동 FAIL→기본 run 보존→명시적 대체→재판정→반출/복원→팩 폴더 없는 오프라인 재판정을 확인했다. 보고서 전체 EXAMPLE/official=false.
- 실제 엔진 opt-in: 6 skipped, 12 deselected / 0.53초(GUARD_UPDATE_* 및 GUARD_ENGINE_CASES 미제공). 엔진·두 이미지 실증, 신규 두 점검 실증 완료 주장은 하지 않는다. 유료 자원 생성 없음.
- 팩 revision 10/DRAFT, 연결 정의 5개를 보존한다. REQ-09 코드는 기본 real 설정에서 비활성으로 유지하며 main의 자동화 보류/실증 미완료를 유지한다. 새 정책 확대나 기업 값 확정은 이번 범위가 아니다.
- CI는 codex/** push와 PR에서 실행한다. 이 기록은 push 전 로컬 결과이며 원격 결과는 이후 기록/Actions에서 별도 확인한다.
- 산출물: docs/INTEGRATION.md, 수동 입력/배치/호환성 회귀와 fixture. 원본 증거와 테스트 출력은 Git에 새로 포함하지 않는다. 다음: Linux CI 및 실제 엔진 검증 환경/정책 확정.


### 통합 첫 Linux CI 및 도움말 검사 보정
- cfd7dbe의 첫 Linux CI: 518 passed, 1 failed, 18 deselected / 24.72초. 실패는 --help의 옵션명 중간 ANSI 색상 코드 때문에 생긴 테스트 출력 가정이다. 기능 실행·입력·배치·호환성 검사는 통과했다.
- https://github.com/dohashelby/gua/actions/runs/39544304934
- 도움말 검사는 색상을 강제로 켠 출력에서 ANSI만 제거하고 동일 옵션명을 검사하도록 보정했다. 실제 --replace-manual 실행 계약은 유지한다.
- 수정 후 집중 검증: 20 passed / 4.37초. 제품 소스는 바꾸지 않았다. 이후 원격 전체 검증 결과를 별도로 확인한다.


### 통합 Linux 최종 검증 (2026-10-21)
- ff1d488c21e1ef96e3762ea09a883bbbe055ffc4: Ubuntu/Python 3.11, `uv run pytest -q` → **519 passed, 18 deselected / 32.03초**.
- frozen 설치와 `uv build --wheel` 성공. Windows의 기존 플랫폼 skip 4건도 Linux에서는 통과했다.
- Push CI: https://github.com/dohashelby/gua/actions/runs/39544534499 (성공), PR CI도 성공.
- 초안 PR: https://github.com/dohashelby/gua/pull/2 . 통합 merge commit cfd7dbe는 main/d9e9729와 update/42a609d를 모두 부모로 가진다. 원래 두 브랜치는 변경하지 않았다.
- 이 수치는 기본 논리·모의 계약 검증이며 실제 엔진/두 이미지 실증은 포함하지 않는다.

## 기업 정책 카탈로그 Phase 1 (2026-10-21, Asia/Seoul)
- 기준: 기존 깨끗한 통합 작업 트리 `codex/gua-integration-20261021` / `58b1539a170894ec261c14ee0dac02f286c15a62`. origin fetch 및 ls-remote로 통합 원격 HEAD 동일 확인, 앞섬/뒤짐 0/0, `git pull --ff-only` → Already up to date. main 작업 트리의 미추적 .assessment와 다른 두 브랜치/작업 트리는 보존했다.
- 한 일: 77개 CheckDef의 source.array+question을 원문 draft와 유일 대조하고 id_map의 질문/subtest_ids/retired 및 변환기의 질문→유일 subtest ID 보존 규칙을 확인했다. 원문 모든 정책/수치/범위·충족/실패/미확인 필드, 11개 선언, 로더/select/실행기/판정표/기존 관련 테스트를 정적으로 대조했다. docs/policy_catalog.csv/md를 작성하고 DECISIONS 열린 질문에 POL-P1-01~05를 추가했다. 과거 기록은 보존했다.
- 산정: SUP 16·REQ 21·PLT 12·LIF 16·OBS 12 = 77개. 이번 정규화의 점검별 결정 발생 595 / 연결 595, 고유 항목 303(a 11, b 12, c 165, d 115). 수치 포함 166(도출 선행 신규 c 165, 수치+복잡 164). 원문 numeric_values_pending 40행과 구별하며 적용성 미정 자원별 후보를 포함한다. 이 수는 scalar 값 개수가 아니고 원문 대비 감소율은 만들지 않았다.
- 확인된 현재 구현: 5개 점검·11개 파라미터, 팩 revision 10/DRAFT. 기존 REQ-01/03·PLT-01의 과거 macOS 제한 실측 기록과 신규 PLT-04/REQ-09의 EXAMPLE 모의 계약 기록을 구분했다. 이번 코드 시험/엔진 재실측은 수행하지 않았다. 과거 증거 누락 기록도 그대로 유지했다.
- 다음/미결정: null 미입력 계약, 제안 ID/공유 표·수치 후보의 분해·적용성, 실제 정책 승인/팩 검토 역할, 각 단위/측정·기준선/SLA 및 Phase 2의 b 선택을 사람과 A가 검토한다. 최소 권고는 기존 11개 template에서 키 생략/주석으로 현재 계약을 유지하는 것. Phase 2 미착수; 기업 값·확정자 생성/코드·팩·SPEC·policy/증거 변경·pytest·엔진/운영 시험·유료 자원·의존성 추가·커밋·푸시 없음.
- 작성 후 문서·데이터 검증 명령과 실제 결과는 아래에 덧붙인다. 자동 검증은 원문 해석이나 정책 승인·실측 완료를 대신하지 않는다.

### Phase 1 문서·데이터 정합성 확인 명령과 결과
- Git 기준 명령: `git remote -v`, `git branch -vv`, `git rev-parse HEAD`, `git status --porcelain=v1`, `git worktree list`, `git fetch origin`, `git log --oneline --left-right codex/gua-integration-20261021...origin/codex/gua-integration-20261021`, `git rev-list --left-right --count HEAD...origin/codex/gua-integration-20261021`, `git pull --ff-only`, `git ls-remote origin refs/heads/codex/gua-integration-20261021 refs/heads/main`. 결과: 통합 HEAD/원격 58b1539a170894ec261c14ee0dac02f286c15a62, main d9e9729116eeb16299247ecb9e7df8ff16d882ec, 통합 작업 트리 시작 시 변경 없음, 통합 앞섬/뒤짐 0/0, pull은 Already up to date. 기준을 바꾸는 reset/stash/병합/강제 push는 실행하지 않았다.
- 원문·기준선 준비: 기존 `..\..\.venv\Scripts\python.exe -X utf8 "$env:TEMP\gua-policy-phase1-20261021\inspect.py"` 및 같은 경로 `protect.py`. 기존 PyYAML을 이용해 JSON/YAML을 읽고 추적 파일 330개와 보호 경로 파일 3,932개의 SHA-256 기준선을 저장했다. 같은 경로 read_rows.py/more.py로 정책·수치·범위/충족/실패/미확인 필드와 owner 연결을 읽었고 rg/Get-Content로 실제 로더/소비 코드·기존 테스트를 정적으로 확인했다. 큰 참고 Markdown 전체·pytest·엔진은 실행하지 않았다. 보조 스크립트/기준선은 저장소 밖 TEMP에만 있다.
- 작성 후 검증 명령: `..\..\.venv\Scripts\python.exe -X utf8 "$env:TEMP\gua-policy-phase1-20261021\validate_catalog.py"`. 최초 실행은 마지막 `git diff --check`에서 PROGRESS.md의 EOF 빈 줄 1건을 발견해 종료 코드 1. 추가한 끝 빈 줄만 제거했고 과거 내용은 바꾸지 않았다. 재실행 종료 코드 0, 아래 항목 통과. 정규식은 경로/ID·문서 일치 확인에만 쓰며 정책의 의미/분류는 작성자가 검토한 제안이다.
- 원문/연결: SUP16·REQ21·PLT12·LIF16·OBS12=77, 질문+source.array 유일 대응과 id_map ID/질문/subtest/retired·legacy·policy_prose/충족/실패/미확인 일치. items28/scenarios83은 점검 수에 사용하지 않음. numeric_values_pending40행 확인. 모든 legacy Q가 items.policy_owner에 연결되고 원문/경로 보존.
- 카탈로그: CSV 20열·303개 고유 P-DRAFT ID·595개 유일 점검 연결, 고아/중복/누락 0. a11·b12·c165·d115 및 분류 우선순위 a→c→d→b 확인. 수치166·수치+복잡164. 실제 5개 점검의 필수 파라미터 11개와 values 경로·타입 1:1 연결. 신규 항목에는 현재 경로를 만들지 않음. 이 수는 적용성 미정 분해 후보/복잡한 표를 포함한 이번 정규화 단위이며 원문의 유일 정책 총수나 scalar 개수는 아님.
- 출처/환경: 고유 source_refs 964개(JSON/YAML Pointer955·코드 행9) 모두 실존. MD 원문 Pointer 링크531개·로컬 링크 대상22개 실존. facts 의존성은 실제 등록 키, fixed는 실제 model/runtime/engine 필드, config는 실제 소비 코드와 대조. 미지원 추가 관측은 candidate로 분리.
- MD/CSV: 77개 연결표의 정책 ID 집합과 CSV의 연결이 점검별로 동일. 77개 포괄/제외 장부와 수치166개 질문 장부, 집계·기존11개 값 경로 일치. UTF-8/BOM 없음·새 CSV/MD LF·각 파일 마지막 줄바꿈 확인. 추가 기록도 기존 LF를 따른다. 기존 DECISIONS/PROGRESS 기록은 삽입/추가 구간을 제거한 바이트 SHA-256이 시작 기준선과 같음.
- 변경/보존: `git diff --check` 통과. 추적 파일330개를 시작 해시와 비교해 변경은 DECISIONS/PROGRESS뿐이고, 새 파일은 docs/policy_catalog.csv/md 두 개뿐이다. 보호 경로 src/packs/examples/lab/reference/out/.guard 파일3,932개는 바이트 SHA-256과 파일 집합 동일. 다른 작업 트리·sources/·기존 참고 원문/실행 증거를 쓰는 명령은 실행하지 않았다. 코드·팩/정책·SPEC 변경, pytest/엔진/운영 시험·새 의존성·유료 서버·커밋/푸시 없음.
- 범위 한계: 이 검증은 문서·데이터 정합성이다. 원문 해석의 유일성·기업 값/담당자 확정·기존 CI 재통과·증거 체인 복구·신규 엔진 실측·Phase 2 완료는 확인하지 않았고 통과로 적지 않는다. 기록 추가 후 같은 검증을 재실행했다. 추가 제어문자 검사에서 검증 명령 경로의 이스케이프 제어문자 1건을 발견해 올바른 경로로 고쳤고, 제어문자 금지 확인도 검증에 포함했다. 최종 재검증은 종료 코드 0이며 모든 파일 UTF-8/LF·제어문자 없음이다. 최종 git diff --check/상태 확인과 원격 ls-remote에서 4파일 범위 및 원격 HEAD 불변을 확인했다.

### 사용자 지정 체크리스트 가이드 추가 대조 (2026-10-21)
- 사용자 지시: mg-princess/serving-security-framework의 03_체크리스트와_보완가이드.md를 정책 참고로 사용. 외부 main `git ls-remote` 결과 cdfea2401007091f8e5452d87d4ff2fbfaf1219f, 문서/JSON은 이 커밋으로 고정했다. gua 작업 브랜치/HEAD는 codex/gua-integration-20261021 / 58b1539a170894ec261c14ee0dac02f286c15a62 그대로이며 기존 네 파일 변경을 보존했다.
- 조회: GitHub 연결 도구로 문서 첫 65행과 고정 본문을 취득했다. 1,713,297 byte JSON은 연결 도구에서 본문이 비어 있었고 비인증 raw URL은 404였다. 저장소 밖 TEMP에 `git clone --filter=blob:none --no-checkout --depth 1 --branch main https://github.com/mg-princess/serving-security-framework.git`으로 읽기용 사본을 만들고 `git show cdfea2401007091f8e5452d87d4ff2fbfaf1219f:data/checklist.json` 및 같은 커밋 문서를 읽었다. Git blob SHA(JSON b0215028a52965dd6b39114193b515e18f8d5463, 문서 3f5441400b5717aea5a92b7bcd1184f52e20f39f) 검증. 큰 문서 전체를 모델 입력으로 읽지 않고 정책/해당 항목 부분과 코드로 읽은 JSON만 대조했다.
- 연결/차이: 질문 77개가 모두 바뀌어 문구 직접 일치로는 연결되지 않았다. 양쪽 유일 subtest_ids 73개와 충돌 4개(REQ-19/20 kernel/Graph, LIF-05/07 배포 전환/준비)를 질문 의미·정할 정책·충족 기준으로 수동 대조했다. 원문 분야별 수는 16/21/12/16/12=77이고 보호 목적별 팀 참조 기준 28개·시험 분기 설명 112개다. 질문 외 차이는 enterprise_decisions3·scope1·satisfied_if8·fail_criteria2·unknown_criteria5이며 비교 스크립트의 문자열 차이 수로 기록한다. 내용 해석은 문서 §14에 따로 남겼다.
- 보완: 카탈로그 MD에 팀 참조 방향/기업별 결정 구분·28개 요약·77개 CHK 교차표·차이와 구현 한계를 추가했다. CSV 모든 303개 항목에 고정 커밋의 가이드/CHK/정책 필드 출처를 보강하고 SUP 적재/실제 plugin 그룹·LIF release 계약 네 항목을 보완했다. 수치 도출에서 자원/성능 기준선과 보안/업무 기한의 기준 선결정을 구별했다. DECISIONS 열린 질문 POL-P1-06 추가. ID/분류/연결/기존 values 경로·기업 값·실제 담당자와 원본 reference/framework·팩·코드는 보존한다.
- 검증 명령: 기존 가상환경 Python으로 TEMP의 compare_upstream.py, validate_catalog.py, validate_upstream_reference.py를 실행하고 git diff --check/상태를 확인한다. 실제 종료 결과는 완료 후 아래에 기록한다. pytest/엔진 시험·Phase 2·의존성 추가·커밋/푸시는 수행하지 않는다.
- 완료 검증: 기존 `..\..\.venv\Scripts\python.exe -X utf8`로 TEMP의 compare_upstream.py·validate_catalog.py·validate_upstream_reference.py를 실행해 각각 종료 코드 0을 확인했다. 원문 정책 의미는 수동 검토하고 스크립트는 연결/경로/일치만 확인했다. 77개 CHK 연결(유일 subtest73·수동 대조4), 참조 기준28·분기 설명112, CSV303개 전부의 고정 외부 출처772개를 확인했다. 전체 고유 출처1,736개(로컬/원격 JSON·YAML Pointer1,622·외부 문서 앵커105·코드 행9)가 실존하며 기존11개 파라미터·77개 연결·정규화595·분류a11/b12/c165/d115는 동일하다.
- 최종 보존: 보호 경로3,932개 파일 해시/집합과 기존 진행·결정 기록 바이트 보존 확인. 변경 파일은 기존 허용된 네 파일뿐이고 UTF-8/LF·제어문자 없음·CSV/MD 집계와 링크·git diff --check 통과. 최신 문서를 gua의 이전 변환기로 곧바로 재수입할 경우 질문 변경+subtest 충돌 네 항목에서 ID 연속성이 보장되지 않는 점도 문서에 명시했다. 원문·팩·코드·기업 값·실행 증거를 변경하지 않았으며 pytest/엔진·Phase 2·커밋/푸시는 미수행이다.


## 2026-10-21 — Phase 2 정책 양식 M1
- 시작: codex/gua-integration-20261021 / 58b1539a170894ec261c14ee0dac02f286c15a62. fetch 후 원격과 동일. 미커밋 Phase 1 네 문서의 사본/해시를 별도 ignored out/test-runs/policy-m1-20261021에 보존하고 codex/gua-policy-template-m1 브랜치를 만들었다.
- 승인 범위: guard policy template --pack --out, 값 키 생략+주석, 기존 계약 유지. 구현/테스트 코드를 한 묶음으로 마친 뒤 검증한다. core/models/judges/팩/기업 값/엔진/외부 CHK 확대 없음.
- 구현: CLI 그룹과 collectors/policy_template.py, 팩 Ref 일치 시에만 사용하는 패키지 설명 JSON, 입력 계약 회귀, docs/POLICY_TEMPLATE.md 및 사용 문서. 미입력 기본값·담당자·승인 기록을 만들지 않고 기존 파일을 덮어쓰지 않는다.
- PR 기준 사전 확인: 기존 통합 PR #2는 main 대상 open/draft이며 미병합. 새 head PR은 없음. M1은 통합 브랜치를 base로 하는 의존 초안 PR로 준비한다. GitHub 연결 도구 404 후 Git Credential Manager의 기존 인증을 메모리에서 사용해 공식 API 상태를 읽었다. 인증값은 파일/로그에 남기지 않았다.
- 검증 결과는 구현 묶음 완료 후 아래에 기록한다. 과거 CI를 이번 M1 통과로 사용하지 않는다.

### M1 로컬 검증 및 설치본 확인
- 구현/테스트/문서 묶음 완료 후 첫 기본 실행: `uv run pytest -q --basetemp=.review-tests-policy-m1 --junitxml=out/test-runs/policy-m1-20261021/first.xml --durations=8` → 534 passed, 1 failed, 4 skipped, 18 deselected / 128.94초. 새 UTF-8 검사에서 제목을 '정책 양식'으로 잘못 기대했으나 실제 제목은 '정책 입력 양식'이었다. 테스트 기대 문구만 바로잡았고 판정/입력 계약을 완화하지 않았다. 패키지 설명의 기존 혼합 언어 표현도 한국어로 정리했다.
- 수정 후 기본 전체 실행: `uv run pytest -q --basetemp=.review-tests-policy-m1-final --junitxml=out/test-runs/policy-m1-20261021/final.xml --durations=8` → **535 passed, 4 skipped, 18 deselected / 117.56초**. Python 3.11.16 / Windows. skip은 기존 닫힌 루프백 포트의 refused/timeout 차이 3건과 symlink 권한 1건이다. docker/engine 18건은 기본 마커 제외다. Linux 통과나 1분 목표 충족으로 대체하지 않는다.
- `uv build --wheel --out-dir out/test-runs/policy-m1-20261021/dist` 성공. 별도 환경에 `UV_PROJECT_ENVIRONMENT=out/test-runs/policy-m1-20261021/installed-env uv sync --frozen --no-install-project` 후 `uv pip install --python .../installed-env/Scripts/python.exe --no-deps .../guard-0.1.0-py3-none-any.whl`로 설치했다. 설명 정리 후 wheel 재빌드·재설치도 성공했다.
- 저장소 밖 TEMP 작업 디렉터리에서 설치본 CLI/격리 Python의 8개 명령 성공: 패키지 import 위치 확인→policy template help→실제 11개 양식 생성→기존 파일 보호(예상 종료 1)→빈 정책 select→로더/77개 PLAN·필수 누락 5개 확인→부분 정책 select→누락 4개/Store.verify 확인. ENV는 EXAMPLE 합성 fixture이며 엔진/시험 실행은 하지 않았다. template 자체는 Store·ENV·설정 없이 실행했고 양식 생성 직후 .guard가 없음을 확인했다.
- `python -I`의 import 위치가 설치 환경 site-packages임을 확인했고 wheel에 policy_template_metadata.json이 포함됐다. 현재 팩 설명과 Ref 일치, 다른 팩 선언/미지정 설명, 빈/부분/null/잘못된 타입, [], false, 0, REQ-09 소비자 계약, 출력 보호·UTF-8/LF 회귀를 확인했다.
- 보존: 시작 보호 파일 230개 중 기존 변경은 src/guard/cli.py만이다. core/models/judges 각 0건, 팩 revision 10/DRAFT·정책/예시/실험실/참고/기존 증거 불변. 새 모듈/설명 JSON/회귀는 별도로 추가했다. Phase 1 CSV/MD는 시작 바이트와 동일하며 DECISIONS/PROGRESS는 기존 내용을 prefix로 보존하고 M1 기록만 추가했다. 실행 로그/검사 보조 파일은 ignored out/test-runs/policy-m1-20261021에 있다.
- 원격 Linux CI는 아직 push 전이므로 미확인이다. 로컬 결과와 구분해 push/초안 PR 후 확인한다. 새 정책 자동화·실제 엔진·두 이미지 실증을 M1 완료로 집계하지 않는다.

### M1 원격 Linux CI 확인
- 구현 커밋 945cc6b16a82f0d007309bf71d8bd5a79e8cb269, Ubuntu/Python 3.11: 기본 `uv run pytest -q` → **539 passed, 18 deselected / 33.00초(PR), 29.24초(push)**. Windows skip 4건도 Linux에서는 통과했다. 이 환경에서 기본 1분 목표를 충족했다.
- frozen 의존성 설치와 `uv build --wheel`도 두 실행 모두 성공. 실제 엔진/두 이미지 실증은 포함하지 않는다.
- PR CI: https://github.com/dohashelby/gua/actions/runs/39552555535 / push CI: https://github.com/dohashelby/gua/actions/runs/39552529119 . 공식 GitHub API에서 completed/success와 작업 로그의 통과 수·시간을 확인했다.
- 초안 PR: https://github.com/dohashelby/gua/pull/3 . base codex/gua-integration-20261021 / head codex/gua-policy-template-m1, 선행 PR #2 의존. Phase 1 보존 커밋 701b12f와 M1 구현 커밋 945cc6b를 구분했다. 자동 병합하지 않았다.
- 이 후속 커밋은 검증 기록만 추가한다. 제품 코드·팩·정책·증거는 추가 변경하지 않는다.
