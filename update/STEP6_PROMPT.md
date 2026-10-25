외부 검토 의견과 REQ-03 결과를 반영한 다음 작업이다. 먼저 `PROGRESS.md`, `DECISIONS.md`, `docs/automation_matrix.md`, `STEP5B_PROMPT.md`의 안전 규칙을 다시 읽어라. 아래 Step 0~3을 순서대로 한다. Step 0~2는 범위가 정해져 있으니 바로 진행하고, 각 Step이 끝날 때마다 짧게 요약만 보고한 뒤 다음 Step으로 넘어가라. 규칙의 의미를 바꿔야 하거나 기대와 다른 결과가 나오면 멈추고 물어라.

공통 규칙 (이전 지시 그대로 유지)
- 판정 규칙·불변식 INV-01~18·기대값을 결과에 맞추려고 고치지 마. 고쳐야 할 이유가 생기면 근거와 함께 멈추고 물어라.
- core/·judges/ 에 점검 전용 분기를 넣지 마(INV-14). 새 의존성 추가 금지. 단 아래 Step 0의 미사용 의존성 제거는 허용.
- 기본 `uv run pytest`는 Docker 없이 1분 안에 통과. 엔진 테스트는 `-m engine`, Docker 테스트는 `-m docker`.
- 상태를 바꾸는 요청, 호스트 민감 경로 마운트, 외부 송신 금지. 일회용 컨테이너와 프로젝트 안의 빈 임시 디렉토리만.
- **git commit / push / git rm 은 실행하지 마.** 파일만 준비하고, 마지막 보고에 내가 직접 실행할 git 명령을 적어줘.

## Step 0 — 저장소 위생과 증거 보존 (외부 검토에서 확인된 사실만 반영)
확인된 사실: git이 `__pycache__`/`.pyc` 87개와 `.pytest_tmp` 1,758개를 추적 중이다. `.gitignore`와 `.gitattributes`가 없다. Windows에서 클론하면 줄바꿈 변환으로 팩 파일 해시가 어긋나 테스트 수집이 실패한다. `httpx`는 pyproject에 있지만 코드에서 import하지 않는다(테스트의 금지 목록에만 나옴). `tests/test_vllm_engine.py`가 결과 폴더를 `rmtree` 후 재생성한다.
1. `.gitignore` 작성: `.venv/ .pytest_tmp/ .pytest_cache/ __pycache__/ *.pyc .DS_Store` 와 로컬 작업 저장소. **`.guard/`와 `out/`은 바로 무시 목록에 넣지 말고**, 둘의 크기와 내용을 보고해서 "보존할 실측 증거(예: out/5b의 최종 보고서·inventory·로그)"와 "자동 생성물"을 구분하는 안을 제시해. 최종 결정은 내가 한다.
2. `.gitattributes` 작성: 해시로 보호되는 파일(`packs/**`, 증거·보고서 JSON, `*.yaml` 팩 파일)은 줄바꿈 변환이 일어나지 않게(`-text` 또는 `eol=lf`). 근거를 DECISIONS.md에 적어.
3. 미사용 `httpx` 의존성 제거(`pyproject.toml`, `uv.lock`). 제거 후 `uv sync`와 전체 테스트가 통과해야 한다. 오프라인에서 lock 갱신이 안 되면 멈추고 알려줘.
4. 테스트가 `out/5b` 같은 **보존 대상 증거 폴더를 삭제·덮어쓰지 않게** 바꿔라. 테스트 산출물은 `.pytest_tmp` 또는 실행별 하위 폴더로. 바뀐 뒤 `-m engine` 결과가 원본 증거를 건드리지 않는지 확인.
5. 지원 플랫폼을 README/DECISIONS에 "macOS·Linux"로 명시. 외부 검토에서 Windows에서 5건 실패(연결 거부 대신 timeout 3, 경로 표기 1, 심볼릭 링크 권한 1)가 보고됐다. 이번에는 고치지 말고 Windows 전용 문제로 표시(skip 사유 또는 알려진 제한)만 해.
6. 내가 실행할 git 명령(추적 제거 `git rm -r --cached ...`, 커밋 전 확인용 `git status`)을 보고에 정리.

## Step 1 — 정확성 보강 (외부 검토가 지적했고 내가 코드에서 직접 확인한 두 가지)
확인된 사실: `executors/http_common.py` 308·315행 부근이 `routes.setdefault(r.path, r)` 로 **경로만** 키로 쓴다. 실측 22경로 중 `/ping`은 GET과 POST가 둘 다 있다. 같은 경로에서 한 메서드만 시험하고 PASS가 나올 수 있다. 또 `policy_values`는 `exclude_kinds_policy` 외에는 실행기·판정기에서 소비되지 않고, `select`는 필수 키의 존재만 확인해서 `allowed_callers: null`이나 문자열도 통과한다.

1-A. 먼저 **재현 테스트부터** 써라(구현 전에 실패해야 한다): 가상 세계에서 `GET /multi → 401`, `POST /multi → 200`일 때 POST 직통을 시도하지 않은 채 security가 PASS가 되는 경우를 재현. 구현 후에는 FAIL 또는 최소 UNKNOWN(미시험)이 되어야 한다. 추가로: 같은 URL의 두 메서드가 모두 등록·시험·관측되는지, TCP 연결 단계 추론의 근거가 메서드별로 기록되는지.
1-B. 등록·시험·관측 키를 `METHOD path`로 통일. 영향 범위(어댑터 경로 표, 안전 프로브 표, route_scope, 관측 키 `routes_registered/routes_tested/direct_outcomes/...`, 보고서 표시, 기존 override)를 먼저 목록으로 보이고 진행. REQ-01·REQ-03의 기존 결과(exposed=FAIL, isolated=PASS, role_open=FAIL, role_enforced=PASS)가 유지되는지 `-m engine`으로 확인(경로 개수가 늘어날 수 있으니 숫자 변화는 보고).
1-C. 정책값 검증: `PolicyParam.type`의 지원 타입 집합(`str`, `int`, `bool`, `list[str]` 등 지금 쓰는 것만)을 정하고, `select`에서 null·잘못된 타입·알 수 없는 타입을 **실행 전에** 오류로 거부. 오류 메시지는 점검 ID·키·기대 타입을 포함.
1-D. 정책값 소비 최소 기능: (i) `rule_table`에 기대값을 정책 값에서 읽는 `value_policy`와 `subset_of` 연산자를 추가(PLT-01에서 필요). (ii) REQ-01의 `allowed_callers`는 지금 실행기 설정의 역할 표와 연결이 없다. **최소한으로**: 실행기 설정의 역할 이름들이 정책의 `allowed_callers`와 일치하는지(허용 주체로 취급할 역할, 비허용 주체로 취급할 역할)를 시험 시작 전에 검증하고, 불일치면 실행 거부. (iii) "정책 값을 바꾸면 시험 또는 판정 결과가 실제로 달라진다"는 테스트를 정책 값 종류마다 최소 1개(예: REQ-03의 허용 종류 정책, REQ-01의 허용 주체, PLT-01의 허용 capability 목록은 Step 3에서).
범용 정책 언어는 만들지 마. 복잡한 판정이 필요한 점검은 순수 함수 Judge로 따로 구현하는 쪽이 낫다는 외부 의견을 받아들이되, 지금은 구현하지 않는다.

## Step 2 — 경로 분류 안전 확인 (직전 요청, 아직 유효)
`/v1/chat/completions/batch`와 `/v1/responses`를 **대상 이미지 안의 소스**(openai/ 아래 해당 api_router.py 등)에서 읽어 저장·작업 생성 같은 상태 변경 가능성을 확인해라. 확인될 때까지 둘 다 mutating 표시(보내지 않음, 또는 소스로 검증한 안전 프로브만)로 바꾸고 근거 소스 위치를 어댑터 표에 적어. 바꾼 뒤 REQ-01을 `-m engine`으로 다시 돌려 exposed=FAIL, isolated=PASS가 유지되는지 확인해서 숫자로 보고해.

## Step 3 — PLT-01 자동화 (직전 계획 승인 + 결정)
네가 제시한 PLT-01 계획(container_config 실행기, 읽기 전용 수집, overprivileged/hardened 두 프로필)을 승인한다. 결정:
1. FAIL = 정책 허용 목록 밖 권한·마운트·장치가 **실제 실행 구성**(docker inspect, 유효 capability)에 있음. DECISIONS.md에 "허용 목록이 판단 기준이다"를 명시. `default` 프로필(기본 capability)도 한 번 돌려서 결과만 보고해.
2. 실험실 정책 허용 목록은 제안대로(`allowed_capabilities: []`, `allowed_mounts: ["/models:ro"]`, `allowed_devices: []`, `require_non_root: true`). tmpfs `/tmp`와 HOME 지정은 허용. 엔진이 안 뜨면 그대로 보고하고 멈춰.
3. 유효 capability는 `/proc/1/status`만 보지 말고 **컨테이너 안 모든 프로세스의 `/proc/*/status` CapEff를 읽어 합집합**으로 판단(PID 1이 엔진 본체가 아닐 수 있고 worker는 자식 프로세스). exec는 읽기 전용 명령만.
4. sentinel 쓰기 시도 3곳 승인. 단 **모델 마운트는 모든 프로필에서 `:ro`**를 유지하고, 위반 구성은 별도의 빈 sentinel 디렉토리로만 만들어. 테스트 종료 후 모델 inventory(비교용 `d677f399…`)가 그대로인지 확인해 보고.
5. 실행기는 대상 컨테이너 이름을 설정으로 받게 해서 실험실 밖 환경에도 쓸 수 있게 해라.
6. Step 1에서 만든 `value_policy`, `subset_of`, 정책 타입 검증을 PLT-01이 실제로 소비하는지(허용 목록을 바꾸면 결과가 바뀌는지) 테스트로 보여줘.
7. 한계를 보고서에 한 줄: 이 점검은 컨테이너 설정만 보며 Docker Desktop의 리눅스 VM 기준이다.

## 이번에 하지 않는 것 (비판적으로 보류한 외부 의견)
- `guard update` 제품 흐름 연결(버전별 기동·target 전달): 수동 증거 입력 경로와 CPU 비교 기준 결정 뒤에 한다. 지금 시작하지 마.
- 예외 판별·probe 준비 확인의 중복 통합: 회귀 위험 대비 이득이 작아 보류.
- 실행기 내부 함수 분리: REQ-03 일반화에서 `http_common.py`로 이미 분리됨. 추가 분리는 이번 범위가 아님. 단 Step 1 작업 중 한 함수가 지나치게 커지면 그때 알려줘.
- 과거 팩 revision 보존(재판정용 팩 사본): 필요성은 인정한다. PLT-01 이후 별도 작업으로 다룬다. 다만 Step 3에서 팩 revision이 또 올라가면, 그 시점에 이전 PLAN을 재판정할 수 없게 되는 범위를 PROGRESS.md에 한 줄로 기록해줘.
- 어댑터의 `read_option/parse_log/ready` 삭제: 하지 않는다.

## 완료 기준과 보고
- 기본 `uv run pytest` 전부 통과(1분 이내). `-m engine`/`-m docker`로 REQ-01(exposed FAIL/isolated PASS), REQ-03(role_open FAIL/role_enforced PASS), PLT-01(overprivileged FAIL/hardened PASS)을 숫자로 보고.
- PROGRESS.md: 자동화 점검 3/77, Step별 변경 요약, REQ-01 대비 코어 변경 건수(`core/`, `models/`, `judges/`), 알려진 제한.
- 마지막에: 내가 실행할 git 명령, 남은 열린 질문, 실행하지 못한 것.
