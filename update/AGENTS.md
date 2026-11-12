# BYUL UPDATE Codex 작업 지침 (serving-guard)

이 폴더는 서빙엔진 업데이트 보안 검증 솔루션(`guard`)의 작업 디렉토리다. 기업은 엔진 새 판본이 나올 때마다 이 솔루션으로 같은 점검을 다시 실행한다. 예를 들어 vLLM 0.31→0.32→0.33이나 SGLang 0.5.22→0.5.23으로 올릴 때마다 검사한다.

이 파일은 Claude용 `CLAUDE.md`와 같은 규칙(1~11절)에 Codex 실행 메모(12절)를 더한 것이다. 규칙을 바꾸면 두 파일을 함께 고친다. 둘이 다르면 `CLAUDE.md`를 따르고 이 파일을 맞춘다. 정본 설계는 `SPEC.md`, 결정과 열린 질문은 `DECISIONS.md`, 진행 기록은 `PROGRESS.md`다.

## 1. 가장 먼저: 엔진·판본에 묶이지 않는다 (2026-11-09 사용자 결정)

- 지원 대상 엔진은 vLLM, SGLang(반드시 확장), TensorRT-LLM(가능하면 확장)이다. 어떤 계층도 vLLM이나 특정 판본(예: `0.31.0`)을 전제하지 않는다. TensorRT-LLM 때문에 중대한 설계 문제가 생기면 TensorRT-LLM은 포기할 수 있다(2026-11-09 사용자 지시). SGLang은 포기하지 않는다.
- 이 결정에 맞춰 SPEC §0 범위표·§7.1·§12 D7을 고쳤다(2026-11-09). `DECISIONS.md` 2026-11-02의 "내부 센서·Kubernetes 수집기는 vLLM 0.31 전용"은 이 결정으로 대체됐다.
- **지금 범위**(2026-11-09 사용자 결정, [결정 기록](docs/dev-1/user-decisions-20261109.md)):
  - SGLang은 구조 준비까지만 한다. 엔진 provider, 판본 profile, 공통 입력 계약 의미(2절), 엔진 식별 계약(11절)이 여기에 해당한다.
  - SGLang 생산자·설치기·수집 도구·lab·GPU 실측·등록은 하지 않는다. SGLang request-attempt 후보(로컬 브랜치 `claude/sglang-request-attempt`, 병합하지 않음)는 보류한다.
  - vLLM을 먼저 완성한다(완성 기준은 5절).
- 계층별로 엔진 지식을 둘 수 있는 곳:

| 계층 | 엔진·판본 지식 | 규칙 |
|---|---|---|
| `core/`·`models/`·`store/`·`judges/` | 없음 | 엔진 이름·판본 문자열을 쓰지 않는다. `tests/test_engine_neutral_core.py`가 core·models·store를 검사한다. 수동 로그 정책의 수집 프로파일 이름(`guard-vllm-native-info-v1`)은 `log_policy_profiles.py`의 계약 데이터로 옮겨졌다. |
| 판정 입력 | 엔진 중립 계약만 | 판정은 엔진 원시 관측 행을 직접 읽지 않는다. 공통 입력 계약(`collectors/gpu_common_inputs.py`)의 `validate()` 결과처럼 엔진과 무관한 형식을 읽는다. |
| `executors/`·`runtime/`·`collectors/`의 변환 | 없음 | 엔진·판본 확인은 지원표 조회로만 한다. `engine.name != 'vllm'`, `version != '0.31.0'` 같은 문자열 비교를 새로 쓰지 않는다. |
| `adapters/` | 엔진·판본별 데이터 | 관측 지점, 소스 고정값, HTTP 경로표, 프로세스 역할 이름, 로그 형식, 기동 명령, 변경 경로표는 여기에만 둔다. |
| `packs/` | 점검 정의는 엔진 공통 | 엔진별 구현 메모에는 엔진·판본을 적고, 판정 규칙에는 엔진 조건을 넣지 않는다. |

- **판본은 코드가 아니라 데이터다.**
  - 새 판본을 지원한다는 것은 같은 관측 코드에 그 판본의 고정값(파일 SHA-256·크기·대상 함수)과 지원표 행을 더하는 것이다. 판본마다 코드를 복사하지 않는다.
  - 고정값이 맞지 않으면 감싸지 않고 진단(gap)을 남긴다. 엔진 동작은 그대로 둔다. 다른 판본을 받으려고 고정값 검사를 넓히거나 끄지 않는다.
  - 새 판본 고정값은 감싼 함수의 코드가 이전 판본과 같은지 함수 단위로 확인한 뒤 더한다. 다르면 관측 의미를 다시 검토한다.
  - 저장소 태그 소스로 만든 고정값은 실제 이미지의 설치 파일 bytes와 대조하기 전까지 후보다.
- **계약은 의미로 정의한다.**
  - 계약 사건·필드는 vLLM 함수 이름이 아니라 의미로 정한다. 단위가 있는 값은 단위를 명시한다.
  - 엔진 고유 사실이 계약 의미에 맞지 않으면 값을 지어내거나 다른 엔진의 의미로 바꾸지 않는다. null과 진단 코드로 남긴다.
  - 계약 의미를 바꿔야 하면 schema 판본을 올리는 안으로 올리고 `DECISIONS.md` 열린 질문에 적는다.
- **기존 vLLM 고정 코드는 옮길 대상이다.**
  - 엔진·판본 지원은 판본 profile 목록(`adapters/assets/engine_profiles.json`, 조회는 `adapters/engine_profiles.py`의 `supports`·`require`·`require_transport`)과 엔진 provider(`adapters/engine_provider.py`의 `EngineProvider`·`VllmProvider`, 설치 패키지는 `guard.engine_providers` entry point)가 정한다. 새 엔진·판본은 profile 행과 provider를 더해 지원하고 공통 코드는 고치지 않는다. 실행기·수집 파일 27개에 있던 `vllm`·`0.31.0` 비교는 24개 파일에서 이 조회로 바뀌었다(2026-11-09 A 설계 보수).
  - 남은 고정 지점(옮길 대상, 새로 더하지 않는다):
    - `runtime/kubernetes_http.py`·`runtime/kubernetes_startup_binding.py`·`runtime/kubernetes_worker_readiness.py`의 판본 비교. 이 파일 bytes가 증거의 수집기 신원과 B 검토 원본 대조에 쓰여, B 검토(source gate)와 함께 옮긴다.
    - vLLM 소스를 직접 분석하는 CPU 실행기: `executors/engine_sensor.py`의 source slot, `profiler_lifecycle_cpu.py`, `structured_tools_cpu.py`, `template_source_cpu.py`, `sampling_consumption_cpu.py`, `multimodal_gather_cpu.py`. vLLM 구현이므로 provider(`adapters/`) 쪽으로 옮긴다.
    - 관측기 `adapters/vllm_*.py`의 0.31.0 소스 고정값은 엔진·판본별 데이터라 그대로 두되, 새 판본은 고정값 행을 더해 지원한다.
  - 이름이 `vllm_`로 시작해도 엔진 공통 기반인 파일이 있다. 예를 들어 `adapters/vllm_cache_callback.py`는 감싸기·자격 확인 기반이다. 이런 파일에 엔진별 값을 넣지 않고, 관측기마다 자기 고정값 표를 넘긴다.
  - 등록된 생산자 신원이 걸린 파일의 이름 변경이나 분리는 별도 결정으로 한다.
- **결과는 엔진·판본별로 따로 둔다.** 한 엔진·판본의 CPU·GPU 결과를 다른 엔진·판본의 결과로 옮기거나 합치지 않는다.

## 2. 엔진 확장 방식

- **생산자** = 엔진 안 관측기(`adapters/`, 엔진·판본별) + 계약 기록 변환(`collectors/`).
  - 엔진마다 별도 파일로 만든다.
  - 새 생산자 이름에는 엔진을 넣는다(예: `guard-engine-callback-sglang-request-attempt`). 이미 등록된 vLLM 생산자 이름은 신원이라 바꾸지 않는다.
- **공통 입력 계약** `guard-gpu-common-input/1`([설계](docs/dev-1/gpu-common-inputs-196.md)):
  - 생산자 이름·판본·소스 SHA로 지원 여부를 정한다(`SUPPORTED_PRODUCERS`).
  - `supported`·`complete`·`gaps`·`violation_candidates`를 따로 내고, `producer_authenticated`는 항상 False다.
  - 등록은 그 엔진·판본의 실제 이미지 대조와 GPU 확인을 마친 생산자만 한다.
- **공통 입력 계약 `/2`**(확정, 미구현. [설계 "/2 확정 사항"](docs/dev-1/gpu-common-inputs-196.md)): 아래를 한 번의 판본 변경으로 넣는다. 생산자 SHA가 바뀌므로 TP2 GPU 세션에서 한 번에 다시 확인·등록한다.
  - 반복 행 요약과 V2 logit bias 비교(2026-11-08 결정)
  - KV 값은 엔진 고유 단위 그대로 두고 단위 필드(와 알면 단위당 token 수)를 함께 적는다.
  - `limit_enforced`는 모든 한도 집행이며 집행 방식 필드를 둔다(할당 거부·retraction 등).
  - 생산자 신원(소스 SHA)에 공통 감싸기 기반 파일을 넣는다.
- **관측은 읽기만 한다.**
  - property를 실행하지 않는다.
  - 요청 ID는 session 키 HMAC 지문으로만 남긴다.
  - 프롬프트·토큰·출력·비밀 원문을 읽지 않는다.
  - 엔진 동작을 바꾸지 않는다.
- **새 점검·관측의 판정 입력**: 계약 밖 관측이 필요하면, 엔진 원시 행을 판정에 바로 연결하기보다 계약 종류를 늘리는 쪽을 먼저 검토한다.
- **엔진마다 다른 실행 구조를 확인한다.** 프로세스 구성(TP·DP·PP rank, scheduler·worker 배치), KV 단위, 한도 집행 방식이 엔진마다 다르다. 세대 결합과 rank 의미를 엔진별로 확인하고 다른 엔진의 구조를 가정하지 않는다.
- **시험**:
  - 엔진별 고정 자료는 `tests/fixtures/<engine>-<version>/`에 둔다. 실제 태그 소스 bytes와 라이선스를 넣는다.
  - 그 자료로 고정값과 대상 함수 자격을 시험한다.
  - 같은 요청 흐름이면 엔진이 달라도 같은 계약 사건 순서가 나오는지 대조한다.
- **TensorRT-LLM**: 외부 HTTP 점검부터 시작하고, 내부 관측은 가능 범위를 조사한 뒤 정한다. 관측할 수 없는 분기는 지원 밖 UNKNOWN으로 남기며 PASS나 NOT_APPLICABLE로 바꾸지 않는다.

## 3. 업데이트 검사 (U1)

- 변경 유형별 시험 범위는 `docs/UPDATE_SELECTION.md`가 SPEC "U1" 절의 세부 정본이다.
  - 엔진 정식 릴리스는 전체 시험하고 소스 diff를 보고한다.
  - 패치 릴리스는 diff로 선정하고 나머지는 이월한다.
  - 모델·환경·정책 변경은 증분 감사와 이월로 처리한다.
- 소스 변경 경로표는 엔진별로 둔다(현재 `adapters/assets/vllm_change_rules.yaml` 하나). vLLM 표를 다른 엔진에 쓰지 않는다.
- 정식/패치 판별은 엔진과 무관하게 같은 규칙이다. SGLang도 셋째 자리만 오르면 패치 릴리스다(2026-11-09 사용자 결정). 그 엔진의 소스 diff 경로표가 없으면 diff로 시험을 줄일 근거가 없으니 전체 시험한다.
- 두 이미지 비교는 같은 엔진의 두 판본끼리 한다. 기준·후보를 같은 엔진 재사용으로 대신하지 않는다.

## 4. 정본과 참고

- **`SPEC.md`가 정본이다.** 객체 7종, 불변식 INV-01~19, 파이프라인, 플러그인 인터페이스, 개발 순서(§8)가 있다. 2026-11-09 결정은 SPEC §0·§7.1·§12에 반영돼 있다.
- `reference/framework/`: 점검 77행의 원천 데이터(`data/checklist.json`)와 환경·결과 템플릿이다. **필요할 때만 찾아본다.**
  - `03_체크리스트와_보완가이드.md`는 37만 자다. 통째로 읽지 않고, 필요한 행만 grep으로 찾는다.
  - 점검 데이터는 `data/checklist.json`을 코드로 읽는다(문서를 파싱하지 않는다).
  - 엔진별 적용 검토는 `엔진별_적용검토/`에 있다. 프레임워크 원본의 `SGLang_v0.5.22.md`는 이 사본에 아직 없어 동기화가 필요하다.
- `reference/bob-design/`: 이전 프로젝트 설계 문서이고 구조 참고용이다. 서명 승인, 감사 outbox, CAS, 객체 20여 종, 웹 UI를 만들지 않는다(SPEC §9).
- SPEC과 참고 문서가 충돌하면 SPEC을 따르고 `DECISIONS.md`에 한 줄 적는다.
- 입력 항목의 판단 기준은 프레임워크와 솔루션이 실제로 요구하는 값이다. 공용 계약에 필드가 없다는 것을 값을 빼거나 미루는 이유로 쓰지 않는다. 계약 변경은 후속 제안으로 적는다.

## 5. 점검 범위

- 체크리스트는 77개다. SUP-01·02·14, LIF-15, OBS-07·08·10·12의 8개는 update 범위에서 제외한다(2026-11-02 사용자 결정).
- 나머지 69개는 수동 증거로 남기지 않고 전부 자동화 대상이다.
  - 실행기 수는 점검 절차의 자극·관측 위치로 정한다.
  - 점검별 차이는 프로파일로, 대상 시스템별 차이는 커넥터로 흡수한다.
  - 관측이 안 되는 분기는 UNKNOWN으로 남긴다.
- **현재 작업지시서:** [작업지시서 3차 — vLLM MVP(1차 완성)](docs/WORK_ORDER_VLLM_MVP.md). 담당·순서·세션은 이 문서를 따른다. [2차](docs/WORK_ORDER_VLLM_1.md)의 남은 항목은 3차로 옮겼다.
- **vLLM 완성 기준**(2026-11-09 사용자 결정, 판본 쌍은 2026-11-12 결정으로 변경):
  - 작업지시서의 MVP 판본 표(기준 판본·후보 판본)에서 69개를 GPU lab 제품 경로(`guard update`, EVD→RES→RPT)로 실행하고 비교한다. 판본은 그 표에만 적고, 이 파일과 코드는 특정 판본을 전제하지 않는다.
  - 두 판본 지원은 고정값·지원표 행 추가로만 한다. 실제 회귀(SUP-08 코드 판본 선택)가 `REGRESSED`로 나와야 한다.
  - 1차 범위에서 뺀 점검(REQ-02, OBS-01·06·11, PLT-07·10·11·12, OBS-09 기업 수집기 범위, PLT-08 TLS 구성)은 실행 경로를 준비하고 UNKNOWN으로 남긴 상태를 완성으로 본다.
  - 영향도(`impact`)는 2026-11-08 초안(HIGH 33·MEDIUM 38·LOW 6)으로 확정했다. 새 팩 판본에 반영한 뒤 공식(official) 재판정을 한다.
- 현황은 `uv run python -m tools.checklist_status`로 재생성한다(`docs/CHECKLIST_VALIDATION_STATUS.md`). 현황표의 DONE은 lab 단계 완료이고, 보안 PASS·기업 승인·원 질문 전체 완료가 아니다.
- 1단계 분담(2026-11-04)은 실행기 파일 소유로 나눈다.
  - 개발 담당 26개, GPU 실증 담당 43개다.
  - core·models·judges·registry·cli·packs·공통 계층과 현황 도구는 개발 담당이 고친다.
  - 담당별 상태 파일은 `docs/phase1/<담당>.yaml`이며, 각 담당은 자기 파일만 고친다.

## 6. 기술 기준

- Python 3.12만 지원한다(`>=3.12,<3.13`). `uv`를 쓰고, 패키지명은 `guard`, 레이아웃은 `src/guard/`다(SPEC §10).
- 대상 엔진 이미지 안의 Python은 별개 환경이다. 그 판본은 엔진·판본별 고정값으로 다룬다.
- 의존성은 pydantic v2, typer, pyyaml, httpx, pytest다. 그 외 의존성은 추가 전에 사람에게 묻는다. 엔진 패키지(vllm·sglang·tensorrt_llm)를 update의 의존성으로 넣지 않는다.
- 모든 객체는 append-only다. 해시 규칙은 SPEC §2.3을 따른다.
- 소스 고정 목록: 제품 소스(`src/guard`·`packs`·`uv.lock`·`pyproject.toml`·`reference/framework`)를 바꾸면 `uv run python tools/verify_req10_consumption.py --write`로 `release/reviewed-source.json`을 다시 만들고 diff를 검토한다. 나중에 머지하는 쪽이 다시 만든다.

## 7. 작업 방식

1. SPEC §8 개발 순서를 따른다. **사용자 지시(2026-10-19):** 합의한 구현 묶음을 마친 뒤 테스트 → 실패 수정 → 필요한 재검증 순서로 한다. 묶음 안의 각 단계마다 테스트를 반복하지 않는다. 불변식 회귀는 `tests/invariants/test_inv_XX.py`에 유지하고, 구현이 불변식을 어기면 구현을 고친다.
   - **사용자 지시(2026-11-09):** 전체 기본 suite는 로컬에서 돌리지 않고 PR의 Linux CI(Python 3.12.3)에 맡긴다. 로컬에서는 바꾼 부분의 직접 시험과 빠른 정합성 검사(`tools.checklist_status --check`, 계약 검사)만 한다. CI 결과가 나오기 전에는 전체 시험이 통과했다고 말하지 않는다.
2. 실행하지 않은 시험의 결과를 만들거나 예시 값을 `MEASURED`로 표시하지 않는다(INV-02).
3. **사용자 지시(2026-10-24): 60초를 넘겨도 중단하지 않고, 시간 초과 실패 gate를 추가하지 않는다.**
   - 전체 suite의 pytest 시간과 부모 프로세스 벽시계를 각각 기록한다.
   - 기존 사례·assert·기대값·불변식을 완화하거나 새 skip·제외로 통과시키지 않는다.
   - 실제 엔진이 필요한 테스트의 `@pytest.mark.engine`과 기존 기본 제외 조건은 유지한다.
4. 새 결정이 필요한 변경만 보류하고 질문한다. 독립적으로 할 수 있는 작업은 계속한다. 팀 결정이 필요한 질문은 `DECISIONS.md` "열린 질문"에도 적는다.
5. 단계가 끝날 때마다 `PROGRESS.md`에 "한 일 / 테스트 결과 / 다음 단계 / 막힌 것"을 짧게 갱신한다.
6. **문서**
   - `docs/`에는 현행 문서만 둔다. 끝난 계획·통합 메모는 요점을 PROGRESS·DECISIONS에 남기고 지운다(원문은 git 이력에 있다).
   - 코드·시험이 이름으로 읽는 문서는 CI 경로 목록(`../.github/workflows/update.yml`, `../.github/update-ci-paths.txt`)에 남아 있어야 한다(`tests/test_workflow_paths.py`).

## 8. 하지 않는 것 (SPEC §9)

전자서명·registry, 감사 outbox, 웹 UI·로그인, 게이트웨이 구현, LLM 판단 보조.

## 9. 진행 방식

- 새 실행기 종류나 `core/`·`models/`·`judges/` 변경이 현재 요청이나 승인된 계획에 포함되는지 확인한다. 포함된 범위는 다시 묻지 않고 진행하고, 포함되지 않은 변경은 보류하고 질문한다.
- 근거가 없으면 UNKNOWN이다. 상태를 바꾸는 요청은 보내지 않는다(검증된 안전 프로브만). 판정 규칙을 결과에 맞춰 고치지 않는다.
- 안전 요청 형식·경로와 기술 설정의 기본값은 보수적으로 정하고, 오래 유지되는 결정만 `DECISIONS.md`에 한 줄 적는다.
  - 기업의 허용 기준·한도·승인값은 임의로 채우지 않는다.
  - 예시 값은 `EXAMPLE`로 구분하고, 미입력·명시적 빈 값의 기존 의미를 바꾸지 않는다.
- 다음 변경이 필요하면 보류하고 질문한다: 승인 범위 확대, 또는 승인 범위 밖의 SPEC 제품 계약·불변식·판정 의미·수락 기준·의존성·비용·운영 대상·기존 사용자 자원 변경.
- 실제 `FAIL`·`UNKNOWN`·`NOT_COMPARABLE`은 원본과 원인을 기록하고 승인된 검증을 계속한다. 예상과 다르다는 이유로 정책·시험 조건·판정 기준을 바꾸지 않는다.

## 10. 개발 효율 원칙

- **실행기는 점검마다 만들지 않는다.**
  - 기존 관측과 실행기의 재사용을 먼저 검토한다. 정책값이나 판정 조건만 다르면 판정 데이터만 더한다.
  - 기존 수집으로 확인할 수 없는 사실을 주장하지 않는다.
  - 같은 수집 기반을 공유해도 점검별 정책·증거·판정·검증 범위는 구분한다.
- **소스를 확정한 뒤 검증한다.**
  - 구현·테스트·문서·정적 검토와 모든 담당자의 수정을 마친 뒤 소스 snapshot과 파일 해시를 확정한다.
  - 확정 뒤 수정은 영향 범위를 기록하고 직접 테스트·같은 계약 테스트·불변식 회귀부터 재검증한다.
  - 공통 계약·불변식에 영향이 있거나 영향 범위가 불명확하면 전체 검증을 다시 한다.
- **문서·주석만 바뀌면 전체 시험을 반복하지 않는다.**
  - `git diff --stat`과 실제 diff로 `src/`·`packs/`·`tools/`·`tests/`·`pyproject.toml`·`uv.lock`·CI·빌드 설정 변경을 확인한다.
  - 주석 변경은 AST 등으로 실행 로직이 같은지 확인한다.
- **실제 엔진 시험은 묶어서 한다.**
  - 같은 이미지 digest·모델·기동 설정의 시험은 상태 독립성과 사례별 분리를 확인한 뒤 한 번의 기동으로 묶는다.
  - 재시작·OOM·모델 변경·상태 독립성이 불확실하면 새로 기동한다.
- **작업 크기를 유형별로 잡는다.**
  - 정책값 / 기존 관측 판정 / 새 관측 / 새 실행기 / 엔진 내부 원인 증명은 필요한 검증이 다르다.
  - 모든 변경에 두 이미지 전체 실증을 요구하지 않는다.
- 시간은 구현·테스트·엔진 준비·수집으로 나눠 기록한다. 테스트 삭제·새 skip·기대값 완화로 시간을 줄이지 않는다.
- **사용자 지시(2026-10-26):**
  - 독립적인 CPU 구현·회귀는 계속하고, Kubernetes 전체 이식이나 GPU 준비를 선행 조건으로 삼지 않는다.
  - GPU는 준비된 시험 묶음과 비용 승인 범위에서만 쓴다.
  - CPU·대역 결과를 Kubernetes·GPU 실증으로 승격하지 않는다.
  - `sources/`와 AWS 인계 원본은 읽기 전용이다.

## 11. BYUL 경계

- **update 작업에서 `../runtime/`은 고치지 않는다.**
- **공용 계약(`../contracts/`)은 제안만 한다.** 스키마·enum·생산자·표시 소유권 변경은 사용자 검토 뒤 반영한다. `../contracts/README.md`와 실제 스키마가 외부 교환 형식의 기준이다. guard 내부 모델은 유지하고 경계에서 변환한다.
- **엔진 식별 계약**(2026-11-09 사용자 승인, `../contracts/README.md` 변경 이력):
  - `environment.target.engine.name`과 `runtime_kind`는 소문자 식별자 형식(`^[a-z][a-z0-9_.-]{0,63}$`)이다. 정해진 목록이 아니어서 엔진이 늘어도 계약을 고치지 않는다. 정규 값은 `vllm`·`sglang`·`tensorrt-llm`이고 모의 엔진은 `mock`이다. update의 provider·판본 profile·생산자 이름도 같은 식별자를 쓴다.
  - 환경 변환(`tools/import_byul_inputs.py`의 `convert_documents()`)의 기본은 `guard-byul-input-import/2`이며 아래 값을 guard 객체에 직접 넣는다. `/1`은 과거 결과 재생용이며, 이 값들을 원본(`source-environment.json`)에 보존하고 `limitations`에 표시한다.
    - `EngineInfo`: `runtime_kind`·`frontend`(`engine-server`·`triton`)
    - `RuntimeInfo`: `tp_size`·`pp_size`·`dp_size`
    - `ModelInfo`: `tokenizer_revision`·`components`
    - `Policy`: `valid_from`·`valid_until`·`source_document_sha256`
  - 판정 결과의 공백은 둘로 나눈다.
    - 엔진 자체를 지원하지 않으면 `ADAPTER_UNSUPPORTED`
    - 엔진은 지원하지만 그 판본의 고정값·지원표가 없으면 `ENGINE_VERSION_UNSUPPORTED`
  - update 비교에서 한쪽만 해당하면 쪽별 `sides.<side>.gaps`에 둔다.
- RPT JSON이 원본이다. verification-result 변환과 화면은 platform 담당이다. `report/html.py`·`--html`을 복구하지 않는다(OBS 증거 HTML은 별개다).
- 공용 계약의 null·UNKNOWN·정확한 Ref·정규 문서 해시·파일 bytes 해시·자료 출처를 보존한다. 계약 문서에 본문·토큰·Secret을 넣지 않는다.
- 재검증 요청 수신은 엔진 기동 승인이 아니다. 현재 플랫폼의 읽기 전용 조회 경계를 유지한다.

## 12. Codex 실행 메모

### 작업 공간

- 작업은 `update/`에서 한다. 다른 담당과 같은 작업 트리·venv·DB를 함께 쓰지 않는다. 병렬 작업은 각자 git worktree를 쓴다.
- 같은 작업 트리에서 pytest를 동시에 두 번 돌리지 않는다. 임시 폴더가 충돌한다.
- 시험·설치·파일 기록이 필요한 작업은 처음부터 쓰기 가능한 스레드에서 시작한다.

### 자주 쓰는 명령

```bash
uv sync --frozen
uv run python -m pytest -q                              # 기본 suite(실제 엔진 시험은 기본 제외)
uv run python -m pytest -q tests/test_<대상>.py          # 직접 시험부터
uv run python -m tools.checklist_status --check         # 현황표가 정본과 맞는지
uv run python tools/verify_req10_consumption.py --write # 제품 소스를 바꾼 뒤 소스 고정 목록 재생성
```

BYUL 루트에서 쓰는 명령:

```bash
uv run --no-project --with jsonschema python contracts/tools/check_contracts.py
```

platform 교차 시험(`platform/api`에서 실행, update·runtime은 읽기만):

```bash
uv run --frozen --with-editable ../../update --with-editable ../../runtime python -m pytest -q
```

### 결과 읽기

- 최종 판정은 Linux CI(`.github/workflows/update.yml`)다. 로컬에서 직접 시험을 돌릴 때는 CI와 같은 CPython 3.12.3을 쓴다(`uv run --python 3.12.3 ...`, 기존 `.venv`를 바꾸지 않으려면 `UV_PROJECT_ENVIRONMENT`로 별도 환경 지정). 다른 3.12.x에서는 관측기 code 자격 확인이 그 interpreter를 승인하지 않아 수백 개가 실패한다. CI는 모든 PR에서 돌고, `main`·`codex/**`·`byul_update` 브랜치 push에서는 update·contracts·platform/api 경로가 바뀔 때 돈다. `update/docs/` 아래 문서만 바뀐 push는 건너뛴다.
- macOS 로컬에서는 일부 시험이 원래부터 실패한다.
  - Linux x86_64 전용 조건 때문이다(관측기 code 사용 불가 등).
  - 저장소 안의 임시 출력을 거부하는 시험도 있다(`LIFECYCLE_OUTPUT_IN_REPOSITORY` 등).
  - platform 묶음 내보내기 시험 5개(`BUNDLE_BUILD_FAILED`)도 로컬에서 원래 실패한다.
  - 실패가 나오면 변경 전 커밋에서 같은 시험 ID가 같은 이유로 실패하는지 대조한다. 같으면 환경 실패로 기록하고, 다르면 이번 변경의 결함으로 고친다.
- 고정값·자격 확인 시험이 "pinned source untracked/ignored" 류로 실패하면, 새 파일을 git에 추가했는지와 `release/reviewed-source.json`을 다시 만들었는지 먼저 확인한다.

### 커밋과 인계

- 커밋·푸시는 사용자가 요청한 범위에서만 한다. `main`에 직접 커밋하지 않고 브랜치(`codex/<주제>`)를 만든다.
- 인계할 때 아래를 짧게 정리한다.
  - 변경 범위와 새 파일
  - 실행한 시험 명령과 결과(pytest 시간·벽시계, 환경 실패 대조 결과)
  - 남은 제약과 미확인
  - 이번 작업의 Git 기준점과 커밋 여부
- 리뷰 지적은 코드·요구사항·재현 근거로 확인한다. 타당하면 고치고 다시 시험한다. 동의하지 않으면 근거를 적는다.
