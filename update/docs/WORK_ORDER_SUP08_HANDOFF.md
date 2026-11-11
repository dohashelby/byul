# SUP-08 코드 선택 PoC 다음 AI 작업지시서

사용자가 원하는 시연은 **vLLM 0.7.0에서는 SUP-08이 PASS인데 0.30.0으로 업데이트하려고 검증하면 FAIL이 되고, 제품 비교 결과에 REGRESSED가 표시되는 것**이다. 0.31.0 기본 설정의 차단 동작도 별도 대조한다. 이 결과는 실측 목표이며, 현재 확인된 결과가 아니다.

다음 AI는 클로드가 커밋한 구현을 이어받아 **모델 선정 → 실행 배선 보수 → 무해한 시험 자료 준비 → GPU 실측 → 제품 보고서와 증거 반출**까지 진행한다. 기존 전체 개발 계획은 [vLLM 1차 완성 작업지시서](WORK_ORDER_VLLM_1.md)를 유지하고, 이 지시서는 방금 진행하던 SUP-08 PoC에 집중한다.

## GitHub 브랜치에서 최신 작업 이어받기

사용자가 현재 변경의 커밋·푸시를 요청했다. 재개 브랜치는 **`codex/sup08-code-selection-poc`**이다. 아래 ZIP 인계 기록은 커밋 전 스냅샷이며, 최신 브랜치 checkout에는 두 도구의 변경과 이 작업지시서가 함께 들어간다. 이전 ZIP 패치를 최신 checkout에 다시 적용하지 않는다.

```bash
git clone --branch codex/sup08-code-selection-poc https://github.com/dohashelby/byul.git byul-sup08
cd byul-sup08
git log -3 --oneline
git status --short
```

코드 수정 후 직접 시험 5파일을 CPython 3.12.3/macOS arm64에서 재실행해 **134 passed in 6.38s**를 확인했다. 명령은 아래 ZIP 인계 절에 적힌 5파일 시험과 같다. 추가 옵션 전부·과거 렌더 기록 호환성·실제 엔진 동작·GPU·전체 Linux CI를 검증한 결과는 아니다. 제품 구현은 계속 작업 중이며, 모델·실행 표식·GPU 실측의 남은 범위는 그대로다.

## 2026-11-11 다른 컴퓨터 인계 시점의 실제 상태

**이 절은 ZIP을 생성한 시점의 기록이다.** 현재 Git 복원·시험 상태는 위 GitHub 브랜치 절을 먼저 따른다. 사용자가 현재까지의 작업을 다른 컴퓨터의 AI에게 넘기도록 지시해 구현을 중단했다. GPU 실측·모델 다운로드·시험 fixture 생성은 아직 시작하지 않았다.

### Git과 작업 중 변경

- 현재 작업 브랜치는 **`codex/sup08-code-selection-poc`**, HEAD는 **`b5cad9f09698ff4d629a0f113463f5d055c27e09`**다. 클로드의 두 커밋을 그대로 이어받아 새 브랜치만 만들었다.
- `git fetch origin`을 완료했다. 로컬 `origin/main`은 `7a985052`, HEAD와의 공통 조상은 `a60d2f08b198ed9e62af0507e7c9732ba99bfb41`이다. main 통합은 하지 않았다.
- **새 코드 변경은 미커밋·미푸시·수정 후 미시험 상태다.** 다음 두 파일의 diff를 먼저 읽고 이어서 보수한다.

| 작업 중 파일 | 지금 넣은 변경 | 다음에 확인할 것 |
|---|---|---|
| `tools/gpu_a_aws_lab.py` | 렌더러와 CLI에 `engine_version`, `model_id`, `model_host_snapshot`, `code_revision`, 추가 인자 JSON을 넣었다. 모델 hostPath 교체와 코드 판본 인자를 연결하고 렌더 기록에 `launch` 메타데이터를 남긴다. | 새 입력 검증·기존 CLI 호환성·이미지별 인자 지원을 직접 시험한다. 기본 엔진 판본은 여전히 0.31.0이므로 구판 실행 때 명시해야 한다. |
| `tools/gpu_a_run_inputs.py` | ENV가 렌더 기록의 판본·모델 ID·기동 메타데이터를 읽도록 수정했다. 모델 ID 불일치를 거부하고, 사용자 지정 모델 hostPath와 코드 선택용 transport profile을 연결했다. | 과거 렌더 기록에는 새 필드가 없어 재생 호환성이 깨질 수 있다. 기존 `--model-id` 사용 흐름·다른 모델 시험도 확인한다. 선언된 판본과 실제 설치 판본을 대조하는 단계는 아직 없다. |

`launch.runner`의 `engine-default`, `async_requested`의 null, `prefix_cache`의 null은 알 수 없는 기본 동작을 추정하지 않기 위한 값이다. 실제 이미지 관측으로 보완해야 한다. 렌더 기록의 schema 번호는 아직 `/1`이므로 새 필드의 필수 여부와 과거 기록 처리도 정리한다.

관측기·분류기·팩은 이번 이어받기에서 수정하지 않았다. 캐시 파일과 실제 import·실행의 구분, 거부 응답 전 실행을 놓치는 문제, 실행 표식의 수집·귀속은 **설계 검토만 했으며 구현하지 않았다.** `reviewed-source.json`도 이번 세션에서는 다시 만들지 않았다.

`PROGRESS.md`와 이 작업지시서는 로컬 문서 변경이다. 루트 `AGENTS.md` 삭제와 `.claude/`는 이전부터 있던 무관한 변경으로 그대로 보존했고 전송 묶음에서 제외했다.

### 실제로 실행한 시험

코드 두 파일을 수정하기 **전**에 다음 직접 시험을 실행했다.

```bash
cd <새 컴퓨터의 저장소 경로>/update
UV_PROJECT_ENVIRONMENT=/tmp/byul-sup08-py3123 uv run --python 3.12.3 python -m pytest -q \
  tests/test_code_selection_observation.py \
  tests/test_gpu_a_run_inputs.py \
  tests/test_gpu_a_aws_lab.py \
  tests/test_kubernetes_input_validation.py \
  tests/test_executor_extensions.py
```

결과는 **134 passed in 7.74s**, CPython **3.12.3**, macOS arm64다. 부모 프로세스 전체 벽시계는 별도로 측정하지 않았다. 이 결과를 현재 미커밋 수정본의 통과 결과로 쓰면 안 된다. 새 시험은 추가하지 않았고, 수정 후 시험·전체 suite·PR CI도 실행하지 않았다.

현재 컴퓨터에서 `uv`는 `/Users/doha/.local/bin/uv`에 있어 위 명령은 그 절대 경로로 실행했다. 별도 환경 `/tmp/byul-sup08-py3123`에 의존성 20개를 설치했으며 기존 프로젝트 `.venv`는 교체하지 않았다. 새 컴퓨터에서는 `uv`를 설치하거나 실제 경로로 바꾸고 환경을 다시 만든다.

### 모델 조사 결과와 우선 후보

**우선 검증할 후보는 `microsoft/Phi-3-vision-128k-instruct`다. GPU 호환성이 확정된 선정 모델은 아직 아니다.**

- HF API에서 확인한 모델 커밋: **`ed2772fabe9dc9acd0caad54b62761d92520cc44`**. gated=false이고 safetensors 정보는 BF16 파라미터 4,146,621,440개다. 가중치·inventory를 내려받거나 계산하지 않았다.
- 해당 커밋의 `preprocessor_config.json`에 `AutoProcessor: processing_phi3_v.Phi3VProcessor`와 `AutoImageProcessor: image_processing_phi3_v.Phi3VImageProcessor`의 `auto_map`이 있다.
- vLLM 0.7.0의 `Phi3VProcessingInfo.get_hf_processor()`는 `num_crops`만 받는다. 0.30.0의 같은 파일에서는 이 제한형 override가 사라졌다. 따라서 요청 인수 전달 차이를 확인할 우선 후보로 잡았다. 이것만으로 0.7.0 PASS·0.30.0 FAIL을 확정하지 않는다.
- Phi3VProcessor와 그 상대 import 파일을 승인·비승인 두 판본으로 만들고, import 때 무해한 판본 표식만 남기는 방안을 검토했다. 오프라인 HF 캐시에 소유 시험 저장소를 구성하는 방법은 **아직 구현·실행하지 않았다.** 공개 HF 저장소를 생성하거나 기존 모델 저장소를 바꾸지도 않았다.
- Qwen2-VL·LLaVA·LLaVA-OneVision은 조사한 0.30.0 구현에서 구체 processor 클래스를 지정한다. 단순 `auto_map` 변경만으로 동적 AutoProcessor 경로가 선택된다고 가정하지 않는다.

조사에 사용한 원문은 다음과 같다. 다음 AI는 여기서부터 실제 이미지의 설치본·모델 라이선스·의존성·정상 추론을 확인한다.

- [공식 보안 권고](https://github.com/vllm-project/vllm/security/advisories/GHSA-h3rc-6mm3-gc2m): 영향 범위 `>=0.7.3,<0.31.0`, 수정 `>=0.31.0`. 본문은 영향 범위를 bisect하지 않았다고 적는다. 이 페이지에는 알려진 CVE가 없다고 표시되어 있으므로 과거 클로드 설명의 CVE 번호를 그대로 사용하지 않는다.
- [Phi-3 모델 API](https://huggingface.co/api/models/microsoft/Phi-3-vision-128k-instruct)
- [고정 커밋의 전처리기 설정](https://huggingface.co/microsoft/Phi-3-vision-128k-instruct/raw/ed2772fabe9dc9acd0caad54b62761d92520cc44/preprocessor_config.json)
- [고정 커밋의 processor 코드](https://huggingface.co/microsoft/Phi-3-vision-128k-instruct/raw/ed2772fabe9dc9acd0caad54b62761d92520cc44/processing_phi3_v.py)
- [vLLM 0.7.0 Phi3V 구현](https://raw.githubusercontent.com/vllm-project/vllm/v0.7.0/vllm/model_executor/models/phi3v.py)
- [vLLM 0.30.0 Phi3V 구현](https://raw.githubusercontent.com/vllm-project/vllm/v0.30.0/vllm/model_executor/models/phi3v.py)
- [vLLM 0.30.0 processor 로더](https://raw.githubusercontent.com/vllm-project/vllm/v0.30.0/vllm/transformers_utils/processor.py)

### AWS와 새 컴퓨터 접근

사용자가 Chrome의 AWS 콘솔에 로그인했고 서울 리전에서 다음 상태를 읽기 조회했다.

- 인스턴스: **`i-04f510eaa76c2bc89`**, 이름 **`byul-dev1-gpu`**.
- 유형: **`g6.2xlarge`**, 가용 영역 `ap-northeast-2c`, 상태 **stopped**.
- 보안 그룹·키 페어 이름: `byul-dev1-gpu-ssh`·`byul-dev1-gpu`.
- **이번 세션에서 인스턴스를 시작하지 않았다.** SSH 접속, 배포, 다운로드, 보안 그룹 변경도 하지 않았다. GPU 실측·새 증거 반출·이번 회차 비용 산정은 없다.

새 컴퓨터는 별도로 AWS 콘솔 로그인과 기존 승인된 접속 수단을 준비해야 한다. 현재 브라우저 로그인 세션과 SSH 키는 전송 묶음에 들어 있지 않다. 로컬에 기존 키 파일이 존재한다는 것까지만 확인했으며 키 내용은 읽지 않았다. 이 작업지시서만으로 SSH 접속이 된다고 가정하지 말고, 새 공인 IP·현재 접근 허용 범위·기존 키를 안전하게 확인한다.

### 다른 컴퓨터에서 복원

동봉 ZIP은 미푸시 커밋 2개를 담은 `commits.bundle`, 작업 중 두 도구와 PROGRESS 변경을 담은 `working-tree.patch`, 최신 작업지시서 사본, 파일 해시 목록과 짧은 README로 구성한다. 비밀값·SSH 개인키·로그인 세션·`.venv`·`.claude`·원본 대화 기록은 포함하지 않는다.

**ZIP을 푼 다음 새 checkout에서 아래 순서로 복원한다.** GitHub 저장소 접근 권한과 `a60d2f08` 공통 조상이 필요하다. 기존 작업 checkout을 쓰려면 먼저 그 checkout의 변경을 보존하고 동일 패치를 중복 적용하지 않는다.

```bash
TRANSFER=/absolute/path/to/unpacked/byul-sup08-handoff
git clone --no-checkout https://github.com/dohashelby/byul.git byul-sup08
cd byul-sup08
git bundle verify "$TRANSFER/commits.bundle"
git fetch "$TRANSFER/commits.bundle" \
  refs/heads/codex/sup08-code-selection-poc:refs/heads/codex/sup08-code-selection-poc
git switch codex/sup08-code-selection-poc
git apply --check "$TRANSFER/working-tree.patch"
git apply "$TRANSFER/working-tree.patch"
cp "$TRANSFER/files/update/docs/WORK_ORDER_SUP08_HANDOFF.md" update/docs/WORK_ORDER_SUP08_HANDOFF.md
git status --short
```

복원 후 HEAD가 `b5cad9f0`인지, 도구 두 파일과 PROGRESS가 수정 상태인지, 이 작업지시서가 존재하는지 확인한다. ZIP의 `SHA256SUMS`와 실제 파일 해시를 대조한다. 현재 컴퓨터의 `/Users/doha/...` 경로는 새 저장소 경로로 바꾸며, 원본 클로드 대화 파일 없이 이 문서와 코드 diff만으로 이어갈 수 있다.

**다음 AI의 첫 작업은 미커밋 diff 검토 → 수정 후 직접 시험 및 호환성 보수 → Phi-3 후보의 실제 이미지 검증 → 실행 표식 관측 구현이다.** 그 뒤 아래 실행 절차대로 GPU 실측과 제품 비교를 진행한다. 준비가 완료되기 전에 서버부터 켜지 않는다.

## 사용자 지시와 인계 기준

- 클로드 세션의 실행 지시: **“모델 선정까지 알아서 하고, 바로 GPU 실증 돌려”** — 2026-11-11 15:47 KST. 이후 Codex에서 구현을 시작했고, 현재는 사용자 요청으로 다른 컴퓨터에 인계한다.
- 모델 후보 제안만 하고 종료하거나, 이미 받은 모델 선정·GPU 실행 승인을 다시 묻지 않는다. 실제 접근 불가나 승인 범위를 넘는 비용·자원 변경이 있으면 필요한 결정만 구체적으로 확인하고, 독립적인 준비는 계속한다.
- 마지막 클로드 세션은 위 지시 직후 사용량 한도에 걸려 종료됐다. 모델 조사 결과와 새 GPU 실측 결과는 남기지 못했다.
- 작업 루트: `/Users/doha/Desktop/byul`, 제품 작업 디렉터리: `/Users/doha/Desktop/byul/update`.
- 클로드 종료 시점 브랜치: `claude/work-order-vllm-1`, HEAD: `b5cad9f09698ff4d629a0f113463f5d055c27e09`. 현재 Codex 브랜치와 미커밋 변경은 위 최신 상태를 따른다.
- 로컬 원격 추적 브랜치 `origin/claude/work-order-vllm-1`보다 2커밋 앞이다. 최신 두 커밋은 클로드 기록상 미푸시 상태다. 실제 원격 상태는 시작할 때 재확인한다.
- 로컬 `origin/main`은 `7a985052`이며 현재 브랜치와 갈라져 있다. 원격 갱신 후 실제 차이를 확인하고, main 전체를 이 브랜치의 내용으로 덮어쓰지 않는다.
- 기존 작업 트리 변경: 루트 `AGENTS.md` 삭제와 미추적 `.claude/`. 이번 PoC와 무관한 기존 변경이므로 복구·삭제·일괄 커밋하지 않는다.

먼저 `update/CLAUDE.md`와 `update/AGENTS.md`를 읽는다. 설계는 `update/SPEC.md`, 결정은 `update/DECISIONS.md`, 진행 기록은 `update/PROGRESS.md`를 따른다. 아래 상대 경로는 별도 표기가 없으면 `update/` 기준이다.

## 클로드가 끝낸 구현

| 커밋 | 완료한 변경 | 이어받을 때의 의미 |
|---|---|---|
| `d11d4a4f` | 판본 profile, 모듈 목록 수집, 중첩 요청 필드, SUP-08 판정, DRAFT 팩 생성 도구 | 실제 엔진 확인 전의 구현 단계다. B 담당 실행기 변경을 포함한다. |
| `b5cad9f0` | trust 설정별 AWS generation 두 개, 정책에서 실행 설정으로 관측값 전달, 정책 초안 | 모델별 값 4개가 REPLACE 마커다. 여러 판본 실행에 필요한 모든 배선이 끝난 것은 아니다. |

핵심 파일은 다음과 같다.

| 파일 | 현재 역할 |
|---|---|
| `src/guard/adapters/assets/engine_profiles.json` | 0.7.0·0.30.0의 `native_input`만 지원표에 추가. 해당 판본의 다른 점검까지 지원한다는 뜻은 아니다. |
| `src/guard/runtime/kubernetes_http.py` | 일부 고정 판본 비교를 profile 조회로 전환. `engine_code_modules()`가 엔진 컨테이너의 동적 모듈 캐시에서 폴더명·파일명·크기·SHA-256을 수집한다. |
| `src/guard/executors/request_field_probe.py` | `mm_processor_kwargs.code_revision`과 이미지 입력을 구성하고 요청 전후 목록으로 `consumed`·`ignored`·`accepted_unverified`를 분류한다. |
| `src/guard/executors/kubernetes_input_validation.py` | 중첩 필드를 포함한 입력 검증 요청을 허용한다. |
| `tools/build_code_selection_pack.py` | PACK-BASELINE에서 SUP-08만 revision 5로 바꾼 PACK-CODE-SELECTION@1을 새 외부 폴더에 만든다. 나머지 76개 정의 bytes는 유지한다. |
| `tools/gpu_a_aws_lab.py` | `v2-code-selection-trust`는 `--trust-remote-code`를 추가하고 `v2-code-selection-notrust`는 추가하지 않는다. |
| `tools/gpu_a_run_inputs.py` | 정책 초안의 `code_selection_observation`을 SUP-08 설정에 전달한다. |
| `docs/gpu-a/policy-code-selection-draft.yaml` | `EXAMPLE`·`DRAFT` 정책. 승인 커밋, 저장소 ID, 비승인 판본, 이미지 data URI가 미정이다. |
| `tests/test_code_selection_observation.py` | 지원표·요청 구성·캐시 목록 분류·팩·FakeEngine 제품 경로·실행 설정 시험. |

클로드 최종 보고는 코드 선택 관련 시험 묶음 51개와 run-inputs·Kubernetes·executor 묶음 83개 통과다. 이는 이전 세션의 로컬 시험 기록이며 GPU 실측·Linux 전체 CI 통과 기록이 아니다. 새 변경 뒤 필요한 직접 시험을 다시 실행한다.

## 먼저 해결할 실행상 공백

다음 항목은 최초 인계 때 확인한 공백이다. 1·2번의 배선 보수를 시작했으나 시험 전이므로 완료로 보지 않는다. REPLACE 마커만 채우고 GPU를 켜면 안 된다.

1. **환경에 실제 판본을 기록한다.** `tools/gpu_a_run_inputs.py:environment()`는 `engine.version='0.31.0'`을 고정한다. 0.7.0·0.30.0 이미지로 실행해도 잘못 봉인된다. 실제 설치 판본을 확인하고 명시적 실행 입력·렌더 기록에서 ENV로 전달하도록 보수한다. 같은 함수의 runner·prefix-cache 사실도 세대 이름에서 추정하므로 새 generation의 실제 구성과 대조한다. 과거 ENV는 소급 수정하지 않는다.
2. **모델 교체와 코드 판본 고정을 배포까지 연결한다.** AWS 렌더러에는 `--model-revision`이 있지만 모델 ID·경로·코드 판본 전용 입력은 없다. 원본 템플릿은 기존 Qwen 텍스트 모델을 전제로 한다. 선정 모델의 가중치·tokenizer·processor·chat template·마운트·기동 인자가 실제로 연결되도록 덧씌우기와 기록을 준비한다. AWS 인계 원본은 읽기 전용으로 둔다.
3. **구판 이미지의 기동 명령을 확인한다.** generation의 `v2-`라는 이름은 0.7.0에서도 V2 runner가 돈다는 근거가 아니다. 이미지별 entrypoint·CLI 옵션·응답 계약·Python·Transformers 판본을 확인한다. 지원이 필요한 엔진 지식은 provider·판본 profile 쪽에 둔다.
4. **캐시 파일과 실제 실행을 구분한다.** 현재 관측은 디스크의 동적 모듈 캐시 목록이다. 새 `.py` 파일이 생겼다는 사실만으로 import나 실행이 증명되지는 않는다. 선택 판본·파일 해시와 무해한 실행 표식을 요청 시도에 연결하고, 수집기가 직접 import해서 표식을 만든 것이 아님을 확인한다. 판정 의미를 바꿔야 하면 기존 SPEC·점검 계약과 대조해 검토한다.
5. **응답 거부 전에 코드가 실행됐을 가능성을 확인한다.** `classify_modules()`는 HTTP가 거부 코드이면 목록 차이를 판정에 반영하지 않는다. 거부 응답 전에 비승인 모듈이 사용되는 경우를 놓치지 않는지 직접 시험을 추가하고 확인한다.
6. **trust 비활성화를 PASS로 단정하지 않는다.** 동적 처리기가 필수인 모델은 `--trust-remote-code`를 끄면 기동 또는 정상 요청부터 실패할 수 있다. 정상 대조가 성립하지 않으면 기동 실패·미확인으로 기록하고 PASS를 만들지 않는다.
7. **실제 캐시 경로와 저장소 매핑을 확인한다.** 현재 스크립트는 `<root>/<repository>/<revision>/*.py`를 읽는다. 선정 이미지의 Transformers가 쓰는 경로·이름 변환·외부 코드 저장소 참조·중첩 파일 구조가 일치하는지 실제 설치본으로 대조한다.
8. **단일 점검 실행과 업데이트 비교를 모두 준비한다.** `http-check`는 정책·설정·target만 만든다. PACK-CODE-SELECTION을 명시한 실행 명령과 baseline/candidate별 설정, 순차 배포·새 Pod 바인딩을 별도로 준비해야 한다.

## 작업 순서

### 1. 상태 재확인과 직접 시험 기준 확보

Git 상태와 최신 원격 기준을 기록한다. 통합이 필요하면 기존 변경을 보존하는 방식으로 진행하고, `codex/` 접두사의 작업 브랜치를 사용한다. SUP-08 관련 두 커밋의 실제 diff는 `git show d11d4a4f`와 `git show b5cad9f0`로 확인한다.

로컬 직접 시험은 CPython 3.12.3을 사용한다. 공유 `.venv` 교체가 필요하면 별도의 `UV_PROJECT_ENVIRONMENT`를 지정한다. 같은 작업 트리에서 pytest를 동시에 실행하지 않는다.

```bash
cd <새 컴퓨터의 저장소 경로>/update
uv run --python 3.12.3 python -m pytest -q \
  tests/test_code_selection_observation.py \
  tests/test_gpu_a_run_inputs.py \
  tests/test_gpu_a_aws_lab.py \
  tests/test_kubernetes_input_validation.py \
  tests/test_executor_extensions.py
```

변경 전 실패가 있으면 시험 ID와 원인을 기록해 변경 후 결과와 대조한다. 전체 기본 suite는 PR의 Linux CI에 맡기고, 로컬에서 반복 실행하지 않는다.

### 2. 모델과 두 코드 판본 확정

공식 advisory `GHSA-h3rc-6mm3-gc2m`, vLLM 태그 소스, 모델 저장소와 각 이미지의 실제 설치본을 확인한다. 사용자가 의도한 0.7.0·0.30.0·0.31.0 조합이 해당 모델의 같은 처리 경로에서 비교 가능한지 확인해야 한다. advisory의 영향 범위만으로 0.7.0 PASS를 확정하지 않는다.

모델은 다음 조건으로 직접 선정한다.

- 기존 단일 L4 lab 자원에서 구동 가능하고 정상 이미지 추론이 된다.
- `auto_map`의 동적 processor 경로를 실제로 사용한다. 단순히 멀티모달이라는 이유로 선정하지 않는다.
- 동일한 모델 데이터·tokenizer·승인 코드 판본을 0.7.0과 0.30.0에서 사용해 비교할 수 있다.
- 승인·비승인 코드 판본을 소유한 시험 저장소나 재현 가능한 자료로 고정할 수 있다.
- 비승인 판본은 호출 시각·판본을 식별할 수 있는 무해한 표식만 남긴다. 실제 비밀 접근·명령 실행·외부 유출을 피해 증명으로 사용하지 않는다.

모델 ID, immutable revision, 파일 inventory SHA-256, `auto_map`, processor 파일 SHA, 라이선스, 이미지별 의존성 판본과 선정 근거를 남긴다. 외부 저장소 게시나 기존 자원 변경이 추가로 필요하면 실제 필요한 변경만 확인한다. 모델과 기동 구성이 달라야 한다면 그 차이를 명시하고 비교 가능성을 먼저 검토한다.

### 3. 실행 배선과 관측 보수

앞 절의 공백을 해결하고 직접 시험을 한 묶음으로 실행한다. 특히 다음 사례를 검증한다.

- 실제 0.7.0·0.30.0·0.31.0이 올바른 ENV에 봉인된다.
- 캐시에 파일만 존재하고 실제 사용하지 않은 상황을 실행 증거로 부풀리지 않는다.
- 비승인 코드 사용 뒤 요청이 400 등으로 끝나는 경우도 놓치지 않는다.
- 실행 전부터 비승인 캐시가 있거나 다른 요청과 섞이면 이번 요청에 귀속하지 않는다.
- 관측 불가·자료 잘림·잘못된 캐시 경로·정상 대조 실패가 PASS로 바뀌지 않는다.
- 신원·Pod UID·restart count·정확한 이미지 digest 바인딩 검사가 유지된다.

정책 초안의 REPLACE 마커 4개를 구체화한다. 재사용할 초안은 유지하고 실행별 사본에 확정값을 넣어도 된다. 실행 사본에는 마커가 없어야 한다. `approved_code_revisions`에는 시험자가 승인한 코드의 실제 40자리 커밋을 넣으며, 기업 승인값으로 표시하지 않는다.

팩 생성 도구는 새 외부 폴더를 요구한다. 예를 들어 아직 없는 출력 디렉터리를 지정해 다음처럼 생성한다.

```bash
uv run --python 3.12.3 python -m tools.build_code_selection_pack \
  --seed packs/PACK-BASELINE \
  --output /tmp/byul-sup08-code-selection-pack
```

실제 실행에는 이 팩의 경로·정확한 Ref를 사용한다. PACK-BASELINE 자체를 덮어쓰지 않는다. 코드·시험이 팩33을 전제하는 부분은 최신 main 통합 때 다시 확인한다.

### 4. GPU 실행 준비와 실측

[AWS 절차서](gpu-a/AWS_RUNBOOK.md), [환경 점검표](gpu-a/ENVIRONMENT.md), [비용 장부](gpu-a/COST_LEDGER.md)를 확인한다. 문서의 인스턴스·IP·단가는 과거 기록이다. 현재 계정·소유 인스턴스·상태·접근 수단·비용과 다른 작업의 사용 여부를 읽기 조회로 먼저 확인한다.

사용자는 모델 선정과 GPU 실행을 이미 지시했다. 기존 자원과 승인 범위 안에서 진행하고, 증설·새 유료 자원·공유 서버 교체처럼 별도 결정이 필요한 변경만 묻는다. 과거 다른 세션의 $4·$8 또는 TP2 $20를 이번 PoC의 새 예산으로 간주하지 않는다. 접근이 안 되면 정확한 실패 원인과 필요한 접근 정보를 남기면서 가능한 로컬 준비를 마친다.

모델 다운로드·팩·입력·시험이 준비된 뒤 GPU를 사용한다. 단일 GPU에서는 각 세대를 순차 실행하고 세대마다 고유 run ID·캐시·target·출력 경로를 둔다.

| 순서 | 구성 | 확인할 결과 |
|---|---|---|
| 1 | 0.7.0, 선정 모델, 고정 정책·fixture·기동 조건 | 정상 이미지 요청 성공과 비승인 코드 선택 차단·미반영을 실측한다. PASS가 성립하는지 확인한다. |
| 2 | 0.30.0, 같은 모델·정책·fixture와 비교 가능한 조건 | 비승인 코드 판본이 실제 사용됐는지 관측한다. 위반이 입증되면 SUP-08 FAIL이어야 한다. |
| 3 | 제품의 두 판본 업데이트 실행 | 1·2의 조건으로 양쪽을 실행하고 비교 가능하면 PASS→FAIL의 REGRESSED를 보고서에서 확인한다. |
| 4 | 0.31.0, 기본 요청 mm kwargs 거부 설정 | 정상 요청 성공과 비승인 선택 요청의 실행 전 거부를 별도 확인한다. |
| 5 | trust 비활성 정상 대조 | 선정 모델에서 정상 기동·요청이 가능한 경우에 수행하고 실제 결과를 기록한다. |

0.31.0 opt-in은 필요 시 후속 대조로 분리한다. `trust_remote_code`와 `trust_request_mm_kwargs`를 다른 설정으로 기록하고, 비교쌍의 한쪽에만 설정을 바꿔 판본 차이인 것처럼 설명하지 않는다.

현재 관측기는 공격 전부터 비승인 폴더가 있으면 미확인으로 남긴다. 재실행은 시험 소유 범위에서 새 캐시·새 프로세스를 사용하고, 기존 공용 캐시는 삭제하지 않는다. 정상 요청으로 processor 캐시가 먼저 채워져 자극이 해당 경로를 타지 못하는 경우도 실제 처리 경로를 확인한다.

### 5. 제품 경로와 비교 결과 보존

먼저 각 판본을 `guard select → run → judge → report`로 실행해 정상 대조·관측·판정의 연결을 확인한다. 이후 `guard update --pack … --baseline … --candidate … --policy … --config … --output …`로 두 판본 비교를 실행한다. 정확한 옵션은 현재 `src/guard/cli.py`와 `guard update --help`를 확인한다.

- baseline/candidate는 서로 다른 실제 이미지 digest와 올바른 엔진 판본이어야 한다.
- 같은 모델 inventory·정책·fixture를 사용하고 기동 설정 차이를 통제한다. `core/compare.py`는 모델 inventory, fixture, config, GPU/driver 등의 비교 가능성을 검사한다.
- 실제 업데이트 실행은 `[sides.baseline]`·`[sides.candidate]` 설정을 준비한다. 같은 단일 GPU의 순차 실행은 기존 Kubernetes transition과 `after_baseline` 바인딩 방식을 검토하고 필요한 연결을 완성한다.
- 두 단독 실행의 결과 문자열을 붙여서 제품의 REGRESSED 실측이라고 하지 않는다. 비교가 성립하지 않으면 `NOT_COMPARABLE`과 차이 원인을 보존한다.
- REGRESSED는 같은 점검의 PASS→FAIL을 뜻한다. “옛날에 고친 취약점이 재발했다”는 역사적 의미를 전제하지 않는다.
- 실제 결과가 목표와 다르면 원인을 조사하고 그대로 기록한다. 시험·정책·기대값을 약하게 바꾸어 목표 결과를 만들지 않는다.

이 팩과 정책은 DRAFT·EXAMPLE이므로 보고서는 `official=false`를 유지한다. GPU 증거의 MEASURED 여부와 정책의 EXAMPLE 여부는 구분한다. 69개 정본 실측 완료나 제품 전체 완성으로 보고하지 않는다.

## 시험과 검토

배선·관측 수정 뒤 직접 시험에 더해 바뀐 범위의 기존 시험을 실행한다. 후보는 `test_golden_http_executors.py`, `test_vllm_adapter.py`, `test_request_binding.py`, `test_role_route.py`, `test_compare.py`, `test_update_cli.py`, `test_kubernetes_deferred_binding.py`다. 모든 후보를 무조건 반복하지 않고 변경 영향에 맞춰 선택한다.

빠른 정합성 검사는 다음을 사용한다.

```bash
uv run --python 3.12.3 python -m tools.checklist_status --check
uv run --python 3.12.3 python -m pytest -q tests/test_engine_neutral_core.py tests/test_workflow_paths.py
```

제품 소스·팩 등 고정 대상이 바뀌면 `uv run --python 3.12.3 python tools/verify_req10_consumption.py --write`로 목록을 다시 만들고 diff를 검토한다. 작성자가 독립 검토 receipt를 직접 넣지 않는다. SUP-08 실행기는 B 담당이고 수집기 bytes도 증거 신원에 걸리므로, 변경 범위와 최종 manifest SHA를 검토 가능한 형태로 준비한다. 병합에는 B 검토와 최종 Linux CI를 적용한다. 승인 대기와 무관한 개발·준비는 계속한다.

`../runtime/`은 수정하지 않고 `../contracts/` 변경은 제안으로 분리한다. SGLang·TensorRT-LLM·전체 69개 개발로 작업 범위를 넓히지 않는다. 커밋·푸시는 현재 사용자가 허용한 범위를 확인하며, main에 직접 커밋하지 않는다.

## 완료 조건과 인계 결과

작업 결과에는 다음을 남긴다.

1. **재현 입력**: 선정 모델·두 코드 판본·이미지 digest·의존성·실행 인자·완성된 정책 사본·팩 Ref·실행 명령.
2. **판본별 증거**: 정상 요청 전후, 시험 요청의 해시·응답, 캐시 목록 원문과 해시, 실제 사용·실행 표식, Pod·컨테이너 바인딩, 각 단계 시각.
3. **제품 산출물**: ENV·POL·PLAN·EVD·RES·RPT의 정확한 Ref, 두 판본 비교 RPT와 `official=false`, 실제 판정과 비교 가능성 근거.
4. **재현 안내**: 한 문서에 실행 순서와 판본별 결과 표를 남긴다. 코드 선택만 확인한 범위와 실제 실행까지 확인한 범위를 구분한다.
5. **검증 기록**: 직접 시험 명령·성공/실패 수·pytest 시간·벽시계, CI 상태, B 검토 상태, 변경 Git 기준점·커밋·푸시 여부.
6. **정리 기록**: 증거 반출 전후 SHA 대조, 시험 예외 회수와 차단 대조, 소유 probe 정리, 사용한 엔진 중지, GPU 프로세스 상태, 전용 EC2를 시작했다면 stopped 확인, 비용 장부 갱신. 다른 작업 자원·볼륨·스냅샷은 보존한다.

기존 승인 범위에서 실행할 수 있으면 실측과 정리까지 마친다. 모델 호환·처리 경로·비교 가능성 때문에 목표 결과가 성립하지 않으면 실제 실패를 보존하고 원인과 다음 가능한 조치를 명시한다. 접근·추가 자원 결정이 필요한 경우에는 준비 완료 범위, 정확한 차단 요인, 사용자에게 필요한 최소 조치를 남긴다.

## 근거 위치

- [최신 진행 기록](../PROGRESS.md)의 2026-11-11 SUP-08 항목.
- [vLLM 전체 개발 작업지시서](WORK_ORDER_VLLM_1.md)의 파일 소유·소스 검토·완성 기준.
- 커밋 `d11d4a4f54e61bdc103ed0ceb9b6bc18de55ecf6` 및 `b5cad9f09698ff4d629a0f113463f5d055c27e09`.
- 클로드 원본 대화: `/Users/doha/.claude/projects/-Users-doha-Desktop-byul/4fe426ba-6e68-4f8d-a93b-4912187b846b.jsonl`. 최신 사용자 지시와 15:46 KST 최종 구현 보고를 확인했다. 실행 계정 정보나 비밀값은 지시서에 옮기지 않는다.
