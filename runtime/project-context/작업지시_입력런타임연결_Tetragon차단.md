# 작업 지시: ① 입력→런타임 연결 마무리 ② Tetragon 차단 재구성

작성: 2026-11-05, Claude. 대상: Codex.
기준: `byul_runtime`과 main의 `925ca62`(PR #222 머지)다.
이 문서만 보고 작업을 시작할 수 있게 현재 상태, 범위, 단계, 완료 기준을 적는다. 근거는 파일 경로로 남긴다.

## 0. 공통 규칙 (반드시)

- 저장소 `/home/bobgy/bobproject/byul`, 브랜치 `byul_runtime`에서 작업한다.
- **커밋·push 금지**. 사용자가 요청할 때만 한다.
- `contracts/`와 `update/`는 수정하지 않는다. 계약 변경이 필요하면 구현을 멈추고 이유와 필요한 칸을 보고한다.
- `platform/`은 수정해도 된다(사용자 확인). 다만 공용 화면에 영향이 있으면 인계에 적는다.
- 토큰, 키, 서버 IP, 인스턴스 ID, 계정 ID, 실명은 코드·문서·시험·출력에 넣지 않는다. `runtime/project-context/aws-gpu-lab-인계.md`는 커밋하지 않는다.
- 판정 기준(Gateway 결정, exec_verdict 분류)을 몰래 바꾸지 않는다. 바꾸면 이유와 시험을 남긴다.
- 실제 서버(AWS)는 켜지 않는다. 서버 확인이 필요한 것은 5회차 확인 목록(§3)에 적는다.
- 시험 명령:
  - runtime: `cd runtime && TMPDIR=/dev/shm .venv/bin/python -B -m unittest discover -s tests`
  - web: `cd platform/web && npm run typecheck && npm test`
  - api: `cd platform/api && uv run pytest -q --ignore=tests/test_update_input_bundle.py --ignore=tests/test_update_integration_contracts.py`(앞의 두 파일은 기존 `guard` 모듈 의존 문제로 제외)
  - 공통: `git diff --check`
- 끝나면 `runtime/project-context/STATUS.md`에 근거·미확인·다음 작업을 짧게 적는다. 인계에는 변경 파일, 시험 결과(skip 수 포함), 남은 제약, 커밋 안 했음을 넣는다.
- **작업 순서: §1을 먼저 끝내고 §2를 한다.** 한 번에 한 작업만 파일을 수정한다.

---

## 1. 입력→런타임 연결 마무리: 플랫폼 '정책 묶음 내보내기'

### 1.1 현재 상태 (근거)

- **이미 된 것.** runtime의 `bundle build`·`bundle apply`가 동작한다.
  - 코드: `runtime/src/serving_runtime/bundle_tools.py`(`build_bundle`, `apply_bundle`), `runtime/src/serving_runtime/cli.py`
  - 변환 규칙: `runtime/docs/정책묶음_변환규칙.md`
  - 시험: `runtime/tests/test_bundle_tools.py`
  - 사용법: `runtime/deploy/gpu-lab/정책_수동적용_절차.md`
  - 실측: 노트북에서 플랫폼 CONFIRMED 정책(`local-policy-001` r1)으로 build했고, 임시 Gateway가 승인 `verified`로 기동했다. 4회차 실서버에서는 `aws-lab-policy-001` r2(정규 해시 `b37fa34f…`)로 build·apply했다(`runtime/docs/AWS_GPU_서버4회차_결과_20261105.md`).
- **사람이 하는 것.** 지금은 아래를 사람이 손으로 한다.
  1. 플랫폼 API `GET /api/policies/{doc_id}/revisions/{revision}`에서 정책 문서와 해시를 꺼낸다. 응답은 `{document, meta}`이고 `meta.sha256`이 정규 해시다.
  2. 서버 값 파일 `runtime-bundle-server-values/1.0`을 직접 쓴다. 담는 값은 target, 엔진 image digest, model, kubernetes namespace 등이다.
  3. intent 파일을 준비한다.
  4. `bundle build` 명령을 실행한다.
- 플랫폼 정책 승인 흐름은 `platform/api/src/byul_platform/routes/documents.py`에 있다. DRAFT 저장 → `confirm_policy`로 CONFIRMED 새 판본을 만들고, 이때 `approval_ref = {doc_id}-v{revision}`이 자동으로 정해진다. 환경 문서도 같은 파일에 있다(`/environments`).
- 공용 계약상 `environment`에는 runtime-target과 intent 배치 정보가 들어가게 돼 있다(`contracts/README.md` 표, `contracts/schemas/environment.schema.json`). **실제로 어떤 칸이 있는지 먼저 확인할 것.**

### 1.2 만들 것

**A. 플랫폼 API: 묶음 내보내기 엔드포인트**
- 예: `POST /api/policies/{doc_id}/revisions/{revision}/bundle` 또는 `GET …/bundle?environment={env_id}&revision={r}`. 형태는 기존 라우트 관례에 맞춰 정하고 근거를 남긴다.
- 입력은 CONFIRMED 정책 판본, 환경 문서(ID·판본), 선택적 서버 값 덮어쓰기 JSON, 선택적 bundle_id다.
- 동작:
  1. 판본이 CONFIRMED인지, 해시가 저장된 해시와 같은지 확인한다. DRAFT면 409 또는 422로 거부한다.
  2. **서버 값을 환경 문서에서 채운다.** 환경 문서의 runtime-target·intent 관련 칸을 `runtime-bundle-server-values/1.0`으로 옮긴다. 빠진 칸이 있으면 "어떤 칸이 없어 묶음을 만들 수 없다"는 목록으로 거부한다. 추측해서 채우지 않는다. 덮어쓰기 JSON이 있으면 그 값을 쓰되, 응답에 '사람이 입력한 값'이라고 표시한다.
  3. intent는 정책의 `runtime.enforcement.intent`가 있으면 그것을 쓰고, 없으면 환경 문서의 intent 배치 정보를 쓴다. 둘 다 없으면 거부한다.
  4. **변환 코드는 다시 만들지 않는다.** runtime의 `build_bundle`을 그대로 쓴다. 방법은 둘 중 하나를 근거와 함께 고른다.
     - (a) runtime 저장소 경로를 설정으로 받아 `python -m serving_runtime bundle build`를 subprocess로 실행한다. `platform/scripts/run-with-runtime.sh`가 이미 `uv run --with-editable .`로 runtime을 실행하는 방식을 참고한다.
     - (b) platform API 환경에 runtime 패키지를 의존성으로 추가하고 직접 import한다.
     - 권장은 (a)다. 이유는 의존성을 분리할 수 있고, CLI가 실서버와 같은 검증 경로를 타기 때문이다.
  5. 결과 묶음 폴더를 `.tar.gz`로 내려준다. 응답 헤더나 메타에 bundle_id, `bundle.json` SHA-256, 정책 정규 해시, 파일 4개 해시·크기, 서버 값 출처(환경 문서 판본 또는 사람 입력)를 넣는다.
  6. 생성 기록을 플랫폼 저장소에 남긴다. 담는 값은 누가(역할), 언제, 어떤 판본, bundle_id, 해시다. 실명은 쓰지 않는다.
- 시험: CONFIRMED만 허용, 해시 불일치 거부, 환경 칸 누락 시 목록과 함께 거부, 정상 생성 묶음이 `bundle verify`를 통과하는지, 기존 골든과 같은 Gateway 정책 바이트가 나오는지(`deploy/gpu-lab/policy.aws-lab.json` SHA-256 `472a7cca…`).

**B. 플랫폼 화면: [정책 묶음 내보내기] 버튼**
- 위치: 정책 판본 화면(`platform/web`의 정책 상세·판본 목록)과 runtime '적용 정책' 화면 중 사용자가 찾기 쉬운 곳. 두 곳에 두면 같은 동작으로 맞춘다.
- CONFIRMED 판본에서만 버튼을 켠다. DRAFT면 끄고 이유를 보여 준다.
- 환경 선택 → 빠진 서버 값 안내 → 생성 → 다운로드 순서다. 결과로 bundle_id와 해시를 보여 주고, 서버 적용 명령 예시를 복사할 수 있게 한다(sudo 형식, 아래 예).
  ```bash
  sudo -E env KUBECONFIG="$HOME/.kube/config" PATH="$PATH" .venv/bin/python -B -m serving_runtime bundle apply \
    --bundle-dir <받은-묶음> --bundle-root /srv/serving/bundles --context <ctx> --namespace <ns> \
    --kubeconfig "$HOME/.kube/config" --status-url http://127.0.0.1:8080 --token-env-file <토큰파일> --dry-run
  ```
- 용어는 기존 원칙을 따른다. 명사형 전문 용어를 쓰고, 업계 표준어(스트리밍·컨텍스트 등)는 그대로 둔다. 억지 번역은 하지 않는다.
- 시험: 버튼 활성 조건, 누락 칸 안내, 성공 표시.

**C. 범위 밖 (이번에 하지 않음)**
- 플랫폼에서 서버로 원격 전달하고 자동 apply하는 것. 권한·인증이 미결이다(`runtime/docs/결정기록_정책묶음_수신.md`).
- hot reload. 재시작 방식을 유지한다.

### 1.3 완료 기준

- 노트북에서 플랫폼으로 정책을 DRAFT 저장 → 승인(CONFIRMED) → [정책 묶음 내보내기] → 받은 묶음을 `bundle verify` 통과 → 임시 Gateway(`python -m serving_runtime mvp --policy-bundle <묶음> --gateway-port 18990 --engine-port 18991 --data-dir <임시>`)가 `/api/status`에서 `policy_bundle.bundle_id` 일치, `policy.gateway_active=true`, 승인 `verified`가 될 것. 이 과정을 자동 시험이나 재현 스크립트로 남긴다.
- 사람 손이 남는 곳(서버 적용 실행)을 문서에 명시한다.

---

## 2. Tetragon 차단 재구성

### 2.1 현재 상태와 문제 (근거)

- **구현돼 있는 것.**
  - intent 1.1 선택 필드 `tetragon_rule_modes`가 있다. TG-SECRET-01만 enforce할 수 있고 정확한 파일 경로만 받는다(`runtime/src/serving_runtime/enforcement/intent.py`).
  - 렌더러는 TG-SECRET-01 enforce일 때 `security_file_permission` kprobe에 Equal 경로 + Post + `Override(argError -1)`를 만든다(`runtime/src/serving_runtime/enforcement/tetragon.py`).
  - kind L-05b는 monitor → enforce(EPERM) → monitor 순서로 통과했다(`runtime/tools/k8s_lab_check.py`).
  - AWS-20 사례가 있다(`runtime/tools/aws_lab_acceptance.py`).
  - 사전 점검 Override 항목이 있다(`runtime/tools/aws_lab_preflight.py --override-check`, 지금은 unknown과 수동 안내만 낸다).
  - 설계 문서: `runtime/docs/Tetragon_차단_설계.md`
- **4회차 AWS 실패(2026-11-05)**
  - 커널은 `7.0.0-1014-aws`다. `CONFIG_BPF_KPROBE_OVERRIDE=y`이고 Tetragon `override_return: true`다.
  - 그러나 `/sys/kernel/debug/error_injection/list`(1013개 함수)에 `security_` 함수가 0개이고 `lsm: false`다.
  - enforce 규칙은 `load_error`가 났다: `override validation failed: can't override 'security_file_permission' function with kprobe_multi, use --disable-kprobe-multi option`.
  - AWS-20은 `unknown`이다.
  - **규칙을 delete 후 재생성하다 로드에 실패해 TG-SECRET-01 감시가 약 45초 꺼졌다.**
  - 근거: `runtime/docs/AWS_GPU_서버4회차_결과_20261105.md`
- **경로 판정 한계.** "정확한 파일"을 확장자 유무로 판단한다. 그래서 확장자 없는 실제 비밀 파일(예: Kubernetes `token`)을 받지 못한다.

### 2.2 참고한 공식 방식

- Tetragon 공식 문서는 차단에 **Override와 Signal(Sigkill)을 함께** 쓰라고 권한다. Sigkill만으로는 동작을 막는다고 보장하지 못한다.
- 공식 예제에는 `fd_install` 지점에 Sigkill을 거는 것이 있다. 이 방식은 오류 주입 목록이 필요 없다.
  - https://tetragon.io/docs/concepts/enforcement/
  - https://tetragon.cilium.io/docs/getting-started/enforcement/
- **정책 모드**는 monitoring(차단 동작 건너뜀), enforcement, monitor_only 세 가지다. `spec.options`의 `policy-mode`로 정하고, 실행 중에는 `tetra tp set-mode <name> <mode>`(네임스페이스 정책은 `--namespace`)로 **정책을 지우지 않고** 바꾼다. monitor_only 정책은 enforcement로 바꿀 수 없다. 확인은 `tetra tracingpolicy list`의 MODE 열로 한다.
  - https://tetragon.io/docs/concepts/tracing-policy/mode/
- 상용판(Isovalent)은 정책을 네임스페이스·Pod 범위로 좁혀 적용한다. 커뮤니티 경험으로는 차단 전에 monitor로 조건을 검증해야 하고, 맞지 않는 함수에 Override를 걸면 조용히 무력화될 수 있다.
- **설치된 Tetragon 버전(1.7.1)에서 위 옵션과 명령의 정확한 이름을 반드시 직접 확인할 것.** 버전마다 다를 수 있다.

### 2.3 만들 것

**A. 차단 방식(enforce_method)을 고를 수 있게**
- intent에 TG-SECRET-01 차단 방식을 표현한다. 1.1 선택 필드로 하고, 계약 변경 없이 runtime 내부 검증만 한다. 이름은 근거와 함께 정한다.
  - `override_lsm`: 지금 방식. `security_file_permission` Override + Sigkill. 우회가 어렵지만 커널 지원이 필요하다.
  - `sigkill_fd_install`: 공식 예제 형태. 보호 파일이 열려 fd가 설치될 때 Sigkill한다. 오류 주입이 필요 없다. **프로세스가 종료된다.**
  - `override_syscall`: 열기 시스템 호출(커널별 실제 심볼, 예 `__x64_sys_openat`·`sys_openat`)에 Override + Sigkill. 경로 문자열 기반이라 상대 경로·심볼릭 링크 우회 한계가 있다. 렌더 결과와 문서에 이 한계를 표시한다.
- 렌더러는 방식별로 Tetragon 1.7.1 문법 후보를 만든다. Override와 Sigkill은 공식 권고대로 함께 쓴다. 단 `sigkill_fd_install`은 Sigkill만 쓴다.
- 정책 범위: 기존처럼 TracingPolicyNamespaced로 엔진 Pod selector에 한정한다.
- **경로 판정 개선:** 확장자 추정을 없애고, intent에 차단할 정확한 파일 목록을 명시하는 칸(예: `enforce_exact_files`)을 두거나 기존 `secret_paths`의 각 항목에 '파일' 표시를 받는다. 디렉터리나 접두는 enforce에서 거부한다. 기존 intent가 그대로 동작하는지(하위 호환) 시험한다.
- `tetragon_rule_modes`가 없을 때의 렌더 결과는 **바이트 단위로 지금과 같아야 한다**. 기존 골든 시험을 유지한다.

**B. 정책 모드로 전환하고 삭제·재생성을 없앤다**
- 차단 가능한 규칙은 처음부터 차단 동작이 들어간 상태로 렌더하고, `spec.options.policy-mode: monitor`로 배포한다. 탐지 ↔ 차단 전환은 `tetra tp set-mode`(Tetragon Pod에서 `kubectl exec`)로 한다.
- 전환 도구(새 명령 또는 `aws_lab_acceptance.py`·`k8s_lab_check.py` 공통 함수)의 동작:
  1. 현재 상태와 MODE를 기록한다.
  2. set-mode를 실행한다.
  3. `tetra tracingpolicy list`에서 state가 enabled이고 MODE가 기대값인지 확인한다.
  4. 실패하면 이전 모드로 되돌리고, 되돌림도 확인한다.
- 규칙 내용을 바꿔야 해서 교체가 불가피하면 **새 이름으로 먼저 추가 → 로드(enabled) 확인 → 기존 규칙 제거** 순서로 한다. 기존 감시를 먼저 지우지 않는다. 로드 실패 시 새 규칙만 지우고 기존 규칙은 유지한다.
- AWS-20과 L-05b를 이 방식으로 고친다. 4회차의 delete → apply 흐름(`_aws20_same_rule` 등)을 정리한다.

**C. 환경 판별 (사전 점검)**
- `aws_lab_preflight.py`와 공용 함수에 '차단 능력' 항목을 넣는다. 확인할 값은 다음과 같다.
  - 커널 버전
  - `/boot/config-$(uname -r)` 또는 `/proc/config.gz`의 `CONFIG_BPF_KPROBE_OVERRIDE`, `CONFIG_BPF_LSM`
  - `/sys/kernel/security/lsm`. AppArmor·bpf 포함 여부를 본다.
  - `/sys/kernel/debug/error_injection/list`(sudo 필요할 수 있음)에서 `security_file_permission`과 열기 시스템 호출 심볼이 있는지
  - Tetragon Pod `tetra probe`의 override_return·lsm
- 결과로 **쓸 수 있는 차단 방식 목록과 권장 방식**을 낸다. 우선순위는 `override_lsm` 가능 > `override_syscall` 가능 > `sigkill_fd_install`이다. 근거가 없으면 unknown이다. 추측하지 않는다.
- 4회차 값(위 2.1)을 고정 입력으로 하는 시험을 넣는다. 기대 결과는 `override_lsm` 불가, 나머지는 '확인 필요' 또는 가능(근거 있을 때)이다.

**D. Sigkill의 서빙 영향 처리**
- `sigkill_fd_install`과 `override_syscall`은 **엔진 프로세스를 종료**시킨다. 문서·렌더 요약·화면 문구에 "차단 시 엔진 프로세스 종료 → Gateway가 대상 확인 실패로 요청 503(엔진 미전달) → Kubernetes 재시작"을 명시한다.
- 시험 사례는 **엔진이 아닌 시험용 Pod**(예: 기존 `other-client` 또는 새 probe Pod)에 같은 규칙을 걸어 먼저 확인하는 opt-in 사례로 만든다. 엔진 대상 시험은 별도 opt-in 플래그로 둔다.
- AWS-20 판정을 방식별로 나눈다.
  - Override 계열: 읽기 errno(EPERM 등) + Override 이벤트 + 같은 구간 정상 요청 200 + monitor 복귀 뒤 읽기 성공.
  - Sigkill 계열: 프로세스 종료(코드 137 등) + Sigkill 이벤트 + (엔진 대상이면) 503 구간과 재시작 뒤 200 복구 + monitor 복귀.
  - 근거가 빠지면 `unknown`이다.

**E. kind 시험**
- L-05b를 정책 모드 전환 방식으로 바꾼다. 전환 중 감시 공백이 없는지(같은 정책 ID 유지, 이벤트 연속) 확인한다.
- 새 L-05c는 `sigkill_fd_install`을 시험용 Pod에서 확인한다.
- WSL kind 커널에서 실행이 안 되면 사유를 기록하고 단위 시험으로 대신한다. 실제 kind 실행은 Claude가 하므로 실행 명령과 기대 증거를 인계에 적는다.

**F. 화면 (최소)**
- 대시보드 차단 표지는 Override 외에 Sigkill 이벤트도 '차단 시도'로 표시한다(`platform/web/src/lib/tetragon.ts`).
- Sigkill이면 "프로세스 종료" 보조 표시를 붙인다. 근거 없이 '차단'이라고 쓰지 않는 원칙은 유지한다.

**G. (후속, 이번 범위 아님) AppArmor 경로 차단**
- 사용자와 Claude가 함께 제안한 다음 단계다. 엔진 전용 AppArmor 프로필 생성기(intent의 보호·모델 경로 → 프로필), complain → enforce, Pod `securityContext.appArmorProfile`, 사전 점검 AppArmor 활성 확인이다.
- **이번에는 구현하지 말고**, §2 작업 중 공용으로 쓸 수 있는 구조(환경 판별 결과에 AppArmor 칸)만 열어 둔다.

### 2.4 완료 기준

- 단위 시험:
  - 방식별 렌더 골든
  - 필드 없을 때 기존과 바이트 동일
  - 경로 판정(파일 표시 기반)
  - 환경 판별(4회차 입력)
  - 모드 전환(성공·실패 되돌림, 공백 없음)
  - AWS-20 방식별 판정
  - 화면 Sigkill 표지
- 문서:
  - `runtime/docs/Tetragon_차단_설계.md` '구현 상태' 갱신
  - `runtime/docs/지원_미지원_목록.md` F5 선택적 차단 행
  - `runtime/deploy/gpu-lab/` 5회차 확인 순서 초안(아래 §3)

---

## 3. 5회차 서버에서 확인할 것 (문서 초안만, 실행은 Claude·사용자)

1. 묶음 내보내기로 만든 묶음을 `bundle apply`(수정판, dry-run 먼저)로 적용한다. 상태 확인 재연결과 `--existing` 복귀를 실서버에서 확인한다.
2. 차단 능력 사전 점검 결과를 기록한다. LSM 목록·AppArmor 활성, 오류 주입 목록의 열기 시스템 호출 심볼을 본다.
3. 시험용 Pod에서 `sigkill_fd_install`, `override_syscall`을 확인한다. 엔진 대상은 opt-in으로 하고, Gateway 503 → 재시작 → 복구를 확인한다.
4. 정책 모드 전환 중 감시 공백이 없는지 확인한다(4회차 45초 공백 재발 방지).
5. 차단 결과가 대시보드 차단 내역에 실제 자료로 보이는지 사람이 확인한다.

---

## 4. 참고 파일

- 상태: `runtime/project-context/STATUS.md`
- 결과: `runtime/docs/AWS_GPU_서버3회차_결과_20261104.md`, `runtime/docs/AWS_GPU_서버4회차_결과_20261105.md`
- 후보 상태 목록: `runtime/docs/후보상태_기능목록.md`
- 4회차 순서: `runtime/deploy/gpu-lab/서버4회차_확인순서.md`
- 협업 규칙: `runtime/CLAUDE.md`(Codex 구현 → Claude 검수 → 인계)
