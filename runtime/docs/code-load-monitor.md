# 추론 중 코드 파일 열기 탐지 — TG-CODE-01

2026-11-11 구현. monitor 전용 후보이며 실제 커널 적재·실서버 탐지는 미확인이다.
SC-11-01/02의 공통 행동을 파일 열기 시도로 관측한다. 요청 필드명, 네트워크 다운로드 여부,
`trust_remote_code` 값과 독립적이다. 파일 열기는 import 성공·코드 실행·공격 성공을 증명하지 않는다.

## 조사와 설계 결정

- `enforcement/tetragon.py::_policy`는 namespaced Pod selector와
  `security_file_permission(file, mask)` kprobe를 렌더링한다. TG-ASSET-01/02는 모델/토크나이저
  경로 Prefix와 쓰기 mask 2, TG-CACHE-01은 엔진에 보이는 캐시 경로 Prefix와 mask 2다.
  TG-SECRET-01은 읽기 mask 4, 설정에 따라 Equal/Override 또는 `lsmhooks.file_open`,
  `fd_install`, `sys_openat` 형식을 사용한다. 이들 기존 규칙의 동작은 유지했다.
- 기존 렌더러가 사용하던 경로 연산자는 Prefix/Equal이다. 추가한 Postfix는
  [Tetragon v1.7.1 selector 문서](https://github.com/cilium/tetragon/blob/v1.7.1/docs/content/en/docs/concepts/tracing-policy/selectors.md)의
  file 지원 연산자다. 한 selector의 필터는 AND, selector 간은 첫 일치 OR다.
  파일 열기 시점은 `security_file_open` kprobe로 수집한다. BPF LSM 활성화를 새로 요구하지 않는다.
- Prefix와 Postfix를 다른 **spec 인자 슬롯**으로 결합했다. 같은 함수 인자 0(file)을 두 번
  수집하고 `args:[0]`/`args:[1]`로 선택한다. 1.7.1 문서는 index별 필터 제한을 안내하므로
  동일 index에 두 연산자를 중복 선언하지 않는다.
  [인자 슬롯 구성 소스](https://github.com/cilium/tetragon/blob/v1.7.1/pkg/sensors/tracing/generickprobe.go)와
  [선택자 해석 소스](https://github.com/cilium/tetragon/blob/v1.7.1/pkg/selectors/kernel.go)를 확인했다.
  kernel identity를 켜도 file 2개 + 기존 identity data 3개로 최대 5필드 이내다.
  경로 값은 selector당 8개씩 나누고 디렉터리 경계 `/`를 붙인다. kernel >=5.3을 전제로 한다.
  이 형식의 서버 dry-run, 정책 loaded 상태와 양성 이벤트는 D4에서 검증해야 한다.
- `ExecPolicy`/`phase_signals.py`의 기존 timeline을 재사용한다. loading/compiling/inferring별
  실행 허용 목록 TG-EXEC-01은 **새 프로세스 실행**을 분류하며, 기존 Python 프로세스의 import를
  승인하는 목록이 아니다. 코드 규칙은 그 목록/예외 판정을 사용하지 않고 단계 증거만 복사한다.
- vLLM 패턴은 CPU 0.29 로그의 `Application startup complete.`, SGLang은 GPU 0.5.22 로그의
  `The server is fired up and ready to roll!` 등을 사용한다. 선택적 health 200은 초기화 뒤 경계를 보완한다.
  로그·health는 단계 관측 근거이며 암호학적으로 신뢰된 엔진 상태가 아니다. `engine_signal`은
  기존과 같이 `phase_trusted:[]`로 남긴다. 관리 경로의 `declared`/`test_controller`와 구분한다.
  GPU vLLM 일반화·로그 조작·시각 지연의 한계도 기존 수집기와 같다.
- 기존 live collector는 상태 running, heartbeat 15초 미만, 이벤트 Pod UID 일치를 확인한다.
  단계가 없거나 수집 오류/오래된 heartbeat/Pod 재생성이면 unknown이다. 단계 중첩도 unknown이다.
  loading/compiling/maintenance 단독 단계는 observed로 남기며 탐지로 세지 않는다.

| 대안 | 커널/출력 이벤트량 | 구현·오탐·미탐 | 결정 |
| --- | --- | --- | --- |
| 경로 범위 + 확장자 | 모든 open에서 훅을 거치지만 Pod/Prefix/Postfix 일치만 출력 | 상태 저장이 작고 이미지 내부 지연 import 제외. 캐시 JIT 등 정상 코드도 검토 대상이며 범위 밖은 미탐 | 이번 1차 |
| 기동 코드 집합 기준선 | 기동 import 전체를 수집·보관해야 함 | Pod/컨테이너 재시작·수집 공백별 기준선 관리 필요. 추론 중 정상 지연 import는 새 파일 오탐, 기동 때 들어온 위험 코드 재사용은 미탐 | 보류 |
| 경로 + 기준선 | 범위 내 이벤트량은 같음 | 상태 관리 증가, 기동에 본 캐시 코드를 승인하면 위험을 숨김 | 보류 |

## 범위와 판정

기본 경로는 `/cache/`, `/tmp/`, `/var/tmp/`, `/dev/shm/`, `/root/.cache/`, `/home/`와
intent의 model_dir/cache_dir, assets의 엔진 캐시 감시 경로·tokenizer_dir다.
중복 하위 경로는 제거한다. `.py`, `.pyc`, `.pyo`, `.so`로 끝나는 파일을 수집한다.
`/cache/hf/modules`, `/cache/home/.cache/huggingface/modules`,
`/root/.cache/huggingface/modules`, `/home/<user>/.cache/huggingface/modules`가 포함된다.

임의 위치의 `HF_MODULES_CACHE`는 렌더러 호스트의 환경변수를 읽어 추정하지 않는다.
**엔진 컨테이너에서 사용하는 실제 값**이 기본 경로 밖이면 내부 enforcement intent 1.1에
`"code_load_watch_paths": ["/actual/hf/modules"]`를 추가한 뒤 같은 intent로 렌더·관측한다.
최대 8개 추가 절대 경로, 경로당 UTF-8 255바이트, 제어문자·상위 탐색·루트 `/` 금지이며 렌더 시
최종 Prefix의 UTF-8 256바이트 제한도 검사한다. 기존 intent는 추가 필드 없이 작동한다.

두 GPU lab intent의 라벨은 `app=serving-engine`, 컨테이너 이름은 `engine`, 모델/캐시는
`/models`/`/cache`다. SGLang manifest는 HOME=/cache/home, HF_HOME=/cache/hf,
XDG_CACHE_HOME=/cache/xdg이며 모델 마운트는 readOnly, 캐시는 쓰기 가능이다.
vLLM 실제 환경변수/마운트는 다음 실서버 회차에서 확인한다.

`security_file_open`은 읽기뿐 아니라 쓰기 open도 포함한다. 따라서 코드 생성·편집도 경고할 수 있다.
모델 디렉터리는 readOnly라도 내려받은 코드의 출처가 승인됐다는 뜻이 아니므로 포함한다.
반대로 `/usr/local/.../site-packages`, `/usr/lib`, `/opt` 등 범위 밖의 지연 import에는 경고하지 않는다.
이는 이미지 무결성/읽기 전용 보장 판정이 아니다. 실제 readOnlyRootFilesystem·마운트 검증은 별도다.
특히 SGLang 엔진 manifest는 rootfs 전체 읽기 전용을 선언하지 않는다.
홈 아래 설치한 정상 venv, 추론 중 Triton/JIT 캐시 `.so`는 경고할 수 있다. 단계를 임의로 compiling으로
바꾸거나 Python 전체를 예외 처리하지 않고 경로·시각·단계 근거로 검토한다. 자동 허용 학습은 없다.

탐지 조건: 엔진 namespace/라벨/Pod 이름·UID/정확한 container_name/프로세스 PID·binary,
단독 inferring 단계, 유효한 범위 내 코드 경로와 monitor open 증거가 모두 있어야 한다.
identity index가 구성된 경로에서는 기존 `bind_event`의 confirmed도 필요하다.
경로를 최대 4096자로 제한하고 제어·포맷 문자를 `?`로 치환한다. 잘림·제어문자·상위 탐색·상대 경로,
Tetragon unresolved flags·두 file 복사 불일치는 unknown이며 정리된 문자열로 승인하지 않는다.
Pod UID/container_name, 선택적으로 기존 identity의 컨테이너 세대·커널 프로세스 정보를 native에 남긴다.

## 수집과 플랫폼 연결·계약

`Tetragon -> tools/tetragon_stream.py -> k8s_observation_feed.redact_tetragon_line
-> tetragon.jsonl/heartbeat/gaps -> ObservationFeed -> parse_tetragon(+ExecPolicy, bind_event)
-> observations.jsonl -> Gateway GET /api/observations -> 플랫폼 RuntimeSource
-> observation_event -> checklist_links.coverage` 순서다. CLI ingest도 같은 parse 경로를 사용한다.

스트림은 명시적 정책 목록을 사용하므로 운영자가 기존 목록에 `runtime-tg-code-01`을 포함해야 한다.
정책 렌더만으로 스트림 범위가 자동 확대되지는 않는다. Gateway의 적용 정책 비교에도 새 정책이 포함되므로
렌더한 정책·적용 확인·수집 범위를 함께 맞춰야 한다. 기존 `tetragon_kernel_identity:true` 환경은
identity snapshot에도 해당 정책이 포함되어야 한다. 하나라도 미확인이면 관측 가능을 주장하지 않는다.

개별 사건은 `native.record.code_load_verdict`의 detected/observed/unknown을 외부 계약 outcome에 대응한다.
단계·경로·사유는 기존 `phase`, `observation.phases/path/reason`, 원본 귀속은 허용된 `native.record`에 둔다.
계약 최상위/observation에 새 필드를 추가하지 않았다. CHK-SUP-008의 rules에 TG-CODE-01,
sources에 tetragon을 추가하고 기존 request_code_selection은 유지했다. 내부 체크 참조는 SUP-08이다.
점검 범위의 관련 사건 집계이며 보안 통과/공격 성공 판정은 아니다.

이벤트가 없는 수집 공백은 이벤트를 만들어 메우지 않는다. 기존 collector 상태의 error/stale/never,
heartbeat, open_gap/last_gap, redaction_loss_detected와 정책 적재/identity 증거를 함께 확인한다.
feed가 처리하는 사건에 heartbeat가 없거나 열린 공백이 있으면 code_load_verdict도 unknown이다.
연속 수집 실패는 기존 상태 경로로 관측 불가가 된다. 재연결은 과거 공백의 복구를 증명하지 않는다.
CLI 단건 변환은 전달받은 사건의 분류만 하며 수집 기간 전체에 대한 무탐지를 선언하지 않는다.

외부 runtime-event/status/report의 필드·enum, AWS 케이스 ID를 늘리지 않는다.
새 규칙은 policy-mode monitor와 Post만 가지며 Override/Sigkill을 렌더하지 않는다.

## D4(16회차) 실서버 보조 확인 절차 — 아직 실행하지 않음

**별도 확인 절차로 유지한다.** 새 AWS 케이스를 만들지 않는다. 현행 AWS-01~20의 의미나 자동 판정을
바꾸지 않고, 기존 Tetragon 케이스의 보조 증거 설명에 이 문서와 실제 증거를 연결할 수 있다.
`runtime-lab-report` cases 배열에는 새 ID/필드를 넣지 않는다. 보고서 전체 계약 검증 후에만 게시한다.

1. vLLM·SGLang 각각 격리된 D4 환경에서 운영자가 수행한다. 현재 엔진 이미지·Pod UID·container ID,
   라벨, HF_MODULES_CACHE(설정 시)/HF_HOME/HOME의 **경로만** 확인한다. 환경 전체 덤프는 저장하지 않는다.
   모듈 캐시는 설정값 또는 `${HF_HOME:-$HOME/.cache/huggingface}/modules`이며 기본 범위 밖이면
   code_load_watch_paths를 추가한다. 승인 이미지 내부 Python 모듈 경로·읽기 전용 상태도 확인한다.
2. 새 렌더 결과만 검토하고 server dry-run → monitor 적용 → 정책 loaded/enabled, Pod selector,
   Post만 있는지 확인한다. 커널 함수·두 file 슬롯 AND 필터가 거부되면 중단하고 **관측 불가**로 기록한다.
   기존 수집 정책 목록에 runtime-tg-code-01을 추가하고 재시작에 따른 공백도 남긴다.
3. timeline collector running·fresh·Pod UID 일치·단독 inferring을 확인한다. 평상시 정상 요청이 성공하고
   Tetragon collector/identity/health sentinel이 정상인지 확인한다. 아래 probe는 추론 서비스가 준비된
   엔진 컨테이너 **안**에서 엔진과 같은 UID/Python으로 실행한다. 별도 시험 프로세스임을 기록한다.
4. 실제 모듈 캐시에 겹치지 않는 하위 디렉터리를 만들고 무해한 `.py`를 만든 뒤 import한다.
   아래는 컨테이너 안에서 실행할 Python 예시다. 로컬에서 이 문서 작성 중에는 실행하지 않았다.

```python
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

modules = Path(os.environ.get("HF_MODULES_CACHE") or
               str(Path(os.environ.get("HF_HOME") or
                        str(Path.home() / ".cache/huggingface")) / "modules"))
modules.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="byul-codeload-", dir=modules) as directory:
    path = Path(directory) / "harmless.py"
    path.write_text("VALUE = 42\n", encoding="utf-8")
    # Creation also opens .py: require a second matching open during import.
    started = datetime.now(timezone.utc).isoformat()
    spec = importlib.util.spec_from_file_location("byul_codeload_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ended = datetime.now(timezone.utc).isoformat()
    assert module.VALUE == 42
    print(json.dumps({"path": str(path), "pid": os.getpid(),
                      "import_from": started, "import_until": ended, "value_ok": True}))
```

5. 생성 시점의 open과 구별해 import_from~import_until 구간의 동일 path/PID(커널 PID와 컨테이너 PID의
   기존 identity 매핑 포함), Pod UID·container 세대 사건을 확인한다. 탐지 예측은 TG-CODE-01,
   access=open, phase=[inferring], action_mode=monitor, code_load_verdict=detected,
   플랫폼 outcome=detected·related_checks에 SUP-08이다. Python import 성공·정상 추론/health 유지도 별도로
   확인해 monitor가 차단하지 않았음을 확인한다. event가 없거나 귀속/단계가 unknown이면 성공으로 세지 않는다.
6. 정상 대조는 같은 준비 상태에서 승인 이미지 내부 표준 라이브러리 `.py`(예: `pathlib.__file__`)를
   새 프로세스에서 읽는 import로 실행한다. 실제 코드 파일 경로와 import 성공을 확인하고 해당 경로의
   TG-CODE-01 탐지가 없어야 한다. 읽기 전용 이미지에 대조 파일을 새로 쓰지 않는다.
   캐시의 비코드 파일(.txt)을 여는 대조도 수행한다. 음성 판정에는 정책/스트림 범위와 수집 정상 증거가 필요하다.
7. 기동 대조는 다음 격리 재기동 중 timeline이 명시적으로 loading 또는 compiling일 때 같은 probe를 실행한다.
   해당 단계가 없는 짧은 구간에 억지로 실행하거나 단계 문서를 조작하지 않는다. 원시 open은 남아도
   code_load_verdict=observed, outcome=observed여야 한다. 시점을 확보하지 못하면 미확인으로 남긴다.
8. 별도 짧은 수집 중단 대조에서 공백/상태가 관측 불가로 드러나는지 확인하고 복구한다. 공백 동안
   이벤트가 없다는 것을 미탐 없음으로 보고하지 않는다. probe 디렉터리·생성 pycache는 위 context 종료 시
   정리하며, 기존 모듈 캐시는 수정·삭제하지 않는다. 각 환경의 실제 적용 정책·timeline·collector 상태·
   정제 이벤트·probe 시각/결과·플랫폼 사건/점검 집계·복구 여부를 증거로 묶는다.

우리 환경에서는 SC-11-01/02의 실제 공격 성립을 재현한 것이 아니다. 이 절차는 무해한 파일을 사용한
행동 탐지 시험이며 요청에서 공격자 코드를 내려받거나 엔진 기존 프로세스가 이를 실행했다는 증거가 아니다.
이미 메모리에 올라온 코드, 확장자 없는 코드/다른 접미사(.so.6 등), archive/zip import, memfd,
감시 범위 밖 경로·경로 해석 한계, 수집 공백, 추론 중 합법 JIT는 남는 제약이다.

## 로컬 검증 결과

기준 byul_runtime / f4ea84c9, 커밋·push 없음. 새 기능 runtime/tests/test_codeload.py와
플랫폼 checklist_links의 네트워크 없는 시험은 **34 passed, 2 deselected**다.
두 deselected는 기존 플랫폼 TestClient endpoint/snapshot 시험이다. 생성된 runtime-event에 대한
현행 Contracts 검증, vLLM/SGLang 경로·단계·귀속·unknown·공백·경로 정리·SUP-08 집계를 포함한다.
기존 정책 바이트 해시는 새 TG-CODE-01 manifest만 제외하면 기존 값 그대로 통과했다.

요청한 CI 기본 명령은 저장소 루트에서 다음과 같이 사용했다.

```bash
PYTHONPATH=runtime:runtime/tests:runtime/src:update/src:platform/api/src uv run --project platform/api --frozen --with-editable ./runtime --with-editable ./update python -m pytest -q runtime/tests --import-mode=importlib
```

임시파일 제약 때문에 TMPDIR/UV_CACHE_DIR는 사용자가 지정한 scratchpad/codex-codeload 내부로 지정하고
PYTHONDONTWRITEBYTECODE=1, UV_LINK_MODE=copy, `-p no:cacheprovider`를 사용했다.
빈 uv 캐시에서 첫 호출은 PyPI DNS 실패였다. 기존 로컬 캐시를 scratch 안에 복사한 뒤 UV_OFFLINE=1로 실행했다.
전체 명령은 기존 test_selected_platform_server의 TestClient 진입에서 멈췄다. 15초 faulthandler 스택은
Starlette TestClient → AnyIO run_sync_from_thread/future 대기를 가리키며, 이 hang의 원인은 확정하지 못했다.
같은 파일의 다른 시험을 포함한 재시도도 멈춰 파일 9건을 제외하고 마무리했다.

마지막 실행은 위 명령에 `--tb=short --ignore runtime/tests/test_selected_platform_server.py`를 추가했으며
**1539 passed, 130 skipped, 49 failed, 931 subtests passed**다. 49건 모두 소켓 생성/bind/setsockopt 등의
`PermissionError: [Errno 1] Operation not permitted`이고, 전체 완전 통과로 간주하지 않는다.
git diff --check 통과. 실제 공격·AWS/kind/실제 클러스터·커널 정책 적재 시험은 수행하지 않았다.

로그는 `/tmp/claude-1000/-home-bobgy-bobproject-byul/19673862-6388-464e-b937-b09b0f6543a7/scratchpad/codex-codeload/`의
`targeted-final.log`, `runtime-verified.log`, `runtime-diagnostic.log`에 있다.
