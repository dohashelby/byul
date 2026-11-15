# 기준 V1 direct Torch profiler 원관측 (2026-11-15)

M-A6 LIF-14의 기준 판본 후속이다. `WorkerProfiler`·
`TorchProfilerWrapper`가 없는 초기 V1에서 같은 이름의 wrapper 사건을
만들지 않는다. 기존 `/15` source bytes·runtime 표·의미는 보존한다.
실제 GPU·backend 실측이나 공통 생산자 등록 완료를 주장하지 않는다.

## 정확 소스와 관측 경계

기존 깨끗한 upstream clone HEAD
`5204ff5c3feeb96e8a6eea65dfcb78395f90d4d8`와 기존 baseline fixture를
재사용한다. Worker source는 11991 bytes,
`7bc08a4e47bac5e1d68e68f540f014e3f9244c0f8d773f0dd7f5b959da5afb7a`다.
태그 source 후보이며 설치 이미지 bytes·GPU 결과로 승격하지 않는다.

소스 사슬은 `api_server.start_profile/stop_profile` →
`AsyncLLM.start_profile/stop_profile` → `engine_core.profile_async` →
`EngineCore.profile` → `executor.profile` → `Worker.profile`이다. Worker는
생성 때 `self.profiler = torch.profiler.profile(...)`을 보관하고 이 객체의
`start()`·`stop()`을 직접 호출한다. wrapper ID·wrapper flag가 없다.

새 adapter는 정확한 `Worker.profile` 원 code·descriptor·설치 source를
자격 확인한 뒤 한 번씩 호출한다. start 기본값 True와 native truthiness는
그대로 둔다. bool 밖 인자는 원 동작을 유지하되 관측 `is_start=null`이다.
원 반환·예외 객체를 보존하며 exception 메시지·trace 본문·파일명 원문은
기록하지 않는다. 관측 실패는 원 함수를 대체하지 않는다.

- backend 신원은 실제 `torch.profiler.profile` 객체에 부여한 process-local
  nonce다. worker nonce와 별개이며 wrapper 신원으로 바꾸지 않는다.
- backend 활성 관측은 기존 `/15`와 같은 독립 C builtin 조건을 사용한다.
  `torch.autograd._profiler_enabled`와 `torch._C._autograd._profiler_enabled`의
  동일성·builtin 여부·module 이름을 확인한다. Python 대체 probe와 EXAMPLE
  backend는 이 자격을 받지 못한다.
- 동일 실제 backend·native thread에서 정상 start가 False→True로 바뀐
  경우만 새 generation을 만든다. 이전 성공 start가 없는 stop·backend 교체·
  thread 변경은 세대에 연결하지 않는다. stop RETURN/RAISE와 활성 지속 또는
  비활성 관측은 독립적으로 남긴다. 보안 FAIL·전체 scope 완료로 바꾸지 않는다.

## trace 경로의 정확한 공백

초기 Worker는 `VLLM_TORCH_PROFILER_DIR`을 생성자에서 동적으로 읽고 local
`torch_profiler_trace_dir`을 `tensorboard_trace_handler`에 넘긴 뒤 버린다.
`profiler_config.torch_profiler_dir` 필드가 없다. 현재 환경값을 소비된 callback
경로로 삼거나 Worker에 필드를 합성하지 않는다.

기존 `/15`의 bounded nofollow/stable identity trace inventory 함수를 그대로
재사용하되, 인자는 **명시적으로 선언된 소유 디렉터리 scope**다. Worker
속성을 주장하는 입력이 아니다. `/guard-profiler-traces/<prepared session>`의
안정된 파일 SHA·크기·trace event 수만 남긴다. 새 trace 파일이 생겨도
`callback_directory_binding_verified`·`trace_output_completed_verified`는 False다.
callback 경로를 증명하려면 정확 설치 Torch callback source/closure 자격 또는
생성자의 실제 소비 경로 관측이 더 필요하다.

## request-free composition `/32`

`vllm-native-lifecycle-metadata/32`는 Worker source 하나와 빈 base target표만
갖는다. 기존 configured template·`/31`의 정확 private dependency reader/loader
bytes를 재사용하고 새 standalone adapter와 원본 `/15` helper를 각각 SHA로
고정한다. 새 private code는 nofollow·path/fd identity·전체 bytes 해시 확인 뒤
직접 컴파일한다. 기존 `/15` configure/install을 호출하거나 그 runtime 표를
넓히지 않는다. 기반의 CPython 3.12.3·3.12.8 검사를 유지한다.

기존 CheckedLoader/Writer/process context/seal을 재사용한다. fork 뒤에는 전체
process binding과 weak backend 신원·generation을 새로 만든다. Middleware는
header/scope를 읽지 않고 원 app을 호출한다. request/scheduler hook과
ASGI/NATIVE/SELECTION/기존 PROFILER 사건은 설치·수락하지 않는다. HTTP 200을
backend 성공으로 쓰지 않는다.

기존 `vllm_profiler_metadata`는 별도의 direct-worker sample/cycle projection을
추가한다. 기존 backend/inventory validator를 재사용하고 원 `/15` decoder와
wrapper 의미는 바꾸지 않는다. capture의 `summary.baseline_profiler`에 실제
native 호출 결과·자격 있는 backend generation·선언된 directory 사실을 남긴다.
API/RPC/worker의 cross-process 인과·전체 scope·등록은 미확인으로 보존한다.

## 검증 상태와 회귀

로컬 Python·pytest·helper 실행은 하지 않았다. 검증은 PR Linux CI다.
`test_ma6_baseline_profiler.py`는 정확 전체 source에서 원 function code object를
구성해 native module import 없이 실행한다. 실제 default/nonbool 호출·stop
원 예외·source 변조·sink 실패·복원, 실제 producer→기존 consumer 흐름을
검사한다. generation 양성 시험은 EXAMPLE state-machine probe를 명시적으로
주입하며 실제 C builtin 실측을 주장하지 않는다. 별도 시험은 Python probe가
module 이름을 가장해도 거부되는 것을 확인한다. 기존 실제 파일 inventory는
선언된 임시 소유 scope에 연결해 파일 본문/이름 비공개와 callback 공백을
검사한다. 자산 bytes/재해시/symlink·process binding·금지 event 변조도 거부한다.
