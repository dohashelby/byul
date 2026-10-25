`CLAUDE.md`, `SPEC.md`, `PROGRESS.md`(특히 "5b 전에 사람이 직접 할 일"), `DECISIONS.md`, `guard.toml`, `lab/`을 먼저 읽어라. 5a까지 끝났고(pytest 315개 통과, 로컬 Docker에서 exposed→FAIL / isolated→PASS 확인) 이번 목표는 **5b: 실제 vLLM 엔진으로 REQ-01을 같은 두 위상에서 재현**하는 것이다.

## 실행 위치와 전제
- 이 세션은 **시험용 서버 위에서 직접** 돌고 있다. 로컬 Docker(`unix:///var/run/docker.sock`)를 쓰므로 `DOCKER_HOST`는 설정하지 않는다. 설정돼 있으면 멈추고 알려라.
- 이 서버는 일회용이다. 단, **서버 삭제·방화벽·계정·시스템 설정은 사람이 한다.** 너는 시스템 설정을 바꾸지 않는다.

## 절대 하지 않는 것
- 방화벽·SSH 설정·사용자 계정·시스템 패키지(apt 등) 변경, `sudo` 사용(필요하면 이유와 명령을 보여주고 승인받는다)
- 외부에서 접근 가능한 포트 열기. 모든 포트는 `127.0.0.1`에만 바인드(런타임이 강제). 실행 전후 `ss -tlnp`로 새 리스너가 `0.0.0.0`/`::`에 생기지 않았는지 확인해 보고한다.
- HF 토큰·API 키·SSH 키를 파일·로그·증거·DECISIONS에 남기기. opt-125m은 공개 모델이라 토큰이 필요 없다. 요구하면 멈춰라.
- 새 파이썬 의존성 추가(필요하면 멈추고 물어라), vLLM을 소스에서 빌드하기(이미지가 안 맞으면 멈추고 보고)
- 상태 변경 관리 경로 요청 (`allow_mutating_routes = false` 유지)
- 이 폴더 밖에 파일 만들기. 단, 모델 캐시는 `~/guard-models/`에 두는 것을 허용한다(읽기 전용 마운트 대상).
- 시험 결과를 맞추려고 판정 규칙을 고치기. 실제 결과가 예상과 다르면 **다른 그대로 보고**하고 원인을 분석해라.

## 시작 전 (읽기 전용 점검 후 내 승인을 받는다)
아래를 실행하고 결과를 요약해서 보여줘라. 그 외에는 아무것도 바꾸지 않는다.
```bash
uname -a; hostname; echo "DOCKER_HOST=${DOCKER_HOST:-<unset>}"
docker context show
docker info --format '{{.ServerVersion}} {{.OperatingSystem}} {{.Architecture}} runtimes={{json .Runtimes}}'
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv 2>&1 | head -5
df -h ~ | tail -2; free -h | head -2
ss -tlnp 2>/dev/null | head -20
docker ps -a --format '{{.Names}} {{.Image}} {{.Status}}'
```
그리고 다음 작업 목록, 받을 이미지의 이름·예상 크기, 내가 정해야 할 것을 보여줘라. 내가 "진행"이라고 하면 시작한다. GPU가 없으면 CPU 경로가 가능한지(공식 CPU 이미지가 v0.31.0에 있는지 `docker manifest inspect`로 확인)만 알려주고 멈춰라. 없으면 소스 빌드는 하지 말고 나에게 선택지를 줘라.

## Part 1 — 이미지 고정과 일치 확인
1. `docker manifest inspect vllm/vllm-openai:v0.31.0`로 아키텍처 확인 → pull(큰 이미지, 내 승인 후) → `RepoDigests`로 digest 기록.
2. 컨테이너 안에서 vLLM 버전과 빌드 commit 정보를 확인해 고정 소스 commit `db9527a46873454610df6dbedf79a36d6bf1a7f6`와 맞는지 판단한다 (`import vllm; vllm.__version__`, 이미지 라벨, 설치 메타데이터 등 확인 가능한 것을 모두 시도). **commit이 확인되지 않거나 다르면 "일치 미확인/불일치"로 ENV와 보고서의 limitations에 그대로 기록하고 나에게 알려라.** 일치로 단정하지 마라.
3. digest는 ENV `engine.image_digest`에, 확인 방법과 결과는 `DECISIONS.md`에 적는다.

## Part 2 — 모델 사전 다운로드와 inventory
1. 모델: `facebook/opt-125m`, revision `27dcfa74d334bc871f3234de431e71c6eeba5dd6`(BoBProject FUNC-003 실측 보고서의 값). 이 revision이 지금도 받아지는지 먼저 확인하고, 안 되면 멈추고 보고한다.
2. 다운로드는 **이 단계에서만 인터넷을 허용**한다. 호스트에 패키지를 설치하지 말고, 받은 vLLM 이미지의 파이썬(`huggingface_hub`)을 일회용 컨테이너로 실행해 `~/guard-models/`에 받는다. 이후 모든 시험은 `HF_HUB_OFFLINE=1`과 읽기 전용 마운트로 실행한다.
3. `tools/model_inventory.py`: 파일별 `상대경로·크기·sha256` 목록을 정렬해 직렬화하고(SPEC §2.3의 정렬 JSON 규칙) 전체 SHA-256(`inventory_sha256`)을 계산한다. 파일 수와 해시를 ENV `model.*`에 기록한다. BoBProject 보고서의 파일 수 37과 비교해 다르면 그대로 보고한다(틀렸다는 뜻이 아니라 확인 대상).

## Part 3 — 실제 vLLM 위상 (lab 확장)
`lab/`의 두 위상(`exposed`, `isolated`)에서 stub 엔진 자리를 실제 vLLM으로 바꾼다. Gateway(nginx), 외부 호출자, 신뢰 출발점 구성은 5a와 같다.
- 엔진 기동은 `adapters/vllm.py`의 `start_command`/`launch_plan`을 쓴다 (코드에서 엔진 인자를 따로 만들지 마라). 모델 마운트는 읽기 전용, 오프라인, `--api-key` 없음, `VLLM_SERVER_DEV_MODE` 설정 안 함, 포트는 컨테이너 안에서만.
- GPU면 `--gpus all`. 엔진 로딩에는 넉넉한 `ready` 타임아웃을 주되 상한이 있어야 한다. 기동 실패·타임아웃은 ABORTED/UNKNOWN으로 이어져야 한다(INV-15).
- `guard.toml`: `[adapter] name = "vllm"`로 바꿀 수 있게 하고 vLLM용 설정 섹션(`[vllm]`: 이미지 digest, 모델 디렉토리, revision, GPU 사용 여부)을 추가한다. 5a의 fake/stub 경로가 계속 동작해야 한다.
- 실제 registered 경로는 엔진의 `/openapi.json`에서 읽는다. 개발 모드가 꺼져 있을 때 어떤 경로가 등록되는지 **실측해서 보고**하고, 정보성 제외 목록과 어긋나는 것이 있으면 `DECISIONS.md`에 제안으로 적어라(스스로 바꾸지 말고).

## Part 4 — 실행과 기대, 그리고 정직한 보고
각 위상을 `guard select / run / judge / report` CLI 전체 경로로 실행한다.
- 기대: `exposed` → REQ-01 **FAIL**, `isolated` → **PASS**.
- 실제 결과가 다르면(예: 등록 경로에 상태 변경 경로가 있어 미시험이라 UNKNOWN, 리눅스에서 `timeout` 대신 `unreachable`, Gateway가 일부 경로에서 다르게 동작) 그대로 보고하고 원인을 분석해라. **판정 규칙·불변식·기대값을 결과에 맞춰 고치지 마라.** 규칙에 결함이 의심되면 `DECISIONS.md`의 "열린 질문"에 근거와 함께 적고 멈춰서 물어라.
- 증거의 `data_origin`: 실제 엔진을 실제로 실행해 얻은 값이므로 `MEASURED`, ENV의 사람이 쓴 값은 `DECLARED`. 정책은 DRAFT 유지, 팩은 DRAFT 유지 → `official=false`가 정상이다.
- 보고에 포함: 직통 결과 분포(경로별·키별·상태/timeout/unreachable 건수), 양성 대조, 등록된 경로 목록, 이미지 digest와 commit 일치 여부, 모델 inventory, 소요 시간.

## Part 5 — 테스트
- 기본 `uv run pytest`는 지금처럼 Docker/GPU 없이 1분 안에 통과해야 한다 (5a의 315개 유지).
- 실제 vLLM이 필요한 테스트는 `@pytest.mark.engine`으로 분리하고 `uv run pytest -m engine`으로 실행한다. 포함: 두 위상의 판정 확인, 그리고 **INV-15 실제 어댑터 기동 실패 전파**(존재하지 않는 모델 경로로 기동 → ABORTED → UNKNOWN, 컨테이너 정리) — 이번에 skip을 푼다.
- 남는 skip은 INV-09(8단계 두 이미지 비교)뿐이어야 한다.
- 모든 실행 후 `docker ps -a --filter label=guard.run`, `docker network ls --filter label=guard.run`이 0개인지 확인하고 보고한다. 엔진 컨테이너가 남아 GPU 메모리를 잡고 있으면 안 된다.

## 마무리
1. `PROGRESS.md`에 5b 결과를 적어라 (명령, 숫자, 실제로 나온 값, 못 한 것).
2. `PROGRESS.md`에 **"시험 후 사람이 할 일"**을 갱신해라: 결과 회수 명령(`.guard/`, `out/`를 맥으로 `rsync`), 서버 삭제 체크리스트(인스턴스·디스크·스냅샷·고정 IP·보안 그룹), 모델 캐시를 지워도 되는지.
3. 다음 단계(8단계: 두 이미지 `v0.14.0`↔`v0.31.0`로 `guard update` 비교)에서 필요한 것을 정리해라: 두 이미지 pull 크기, 비교 가능성 조건(INV-09: 같은 모델 inventory·설정·GPU·드라이버)을 이 서버에서 어떻게 충족할지.
4. 마지막 보고: 변경 요약, 테스트 결과(명령과 숫자), 실행하지 못한 것, 열린 질문. 서버를 지우는 일은 내가 하니 끝났다고 알려라.
