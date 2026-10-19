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
