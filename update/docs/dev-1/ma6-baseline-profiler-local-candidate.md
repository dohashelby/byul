# M-A6 0.7 V1 profiler local-call candidate

The 0.7 API defines `/start_profile` and `/stop_profile` handlers inside the
`VLLM_TORCH_PROFILER_DIR` conditional. Their async route calls
`AsyncLLM.start_profile/stop_profile`, then `AsyncMPClient.profile_async` and
`_send_input(PROFILE, EngineCoreProfile)`. The core process receives the ZMQ
frames, decodes and queues the request, and invokes
`EngineCoreProc._handle_client_request`, which calls `model_executor.profile`.
`Executor.profile` calls `MultiprocExecutor.collective_rpc('profile',
args=(is_start,))`; broadcast queue delivery reaches
`WorkerProc.worker_busy_loop` and `Worker.profile`. This actual async API path
does **not** call `MPClient.profile` or `EngineCore.profile`.

`vllm_baseline_profiler_local.py` is an unregistered candidate. It verifies
the exact installed source files and live function bodies of one selected
process-local segment before any hook: API (`async_llm`, `core_client`) or
core (`core`, `abstract`, `multiproc_executor`). It never imports the other
process's files. It records request-free CALL/RETURN/RAISE pairs for the two local paths:
`AsyncLLM` → `AsyncMPClient`, and `EngineCoreProc._handle_client_request` →
`Executor.profile` → `MultiprocExecutor.collective_rpc`. The async hooks emit
RETURN only after the original `await` completes; failure and cancellation
produce RAISE. A parent pair requires the original parent's live frame,
owner relationship, same process/thread and equal boolean. The consumer
requires one terminal per call, parent interval nesting, and exact pair metadata. It leaves the worker
busy loop and existing `/32` Worker.profile observer untouched.

The records have no request/header identity. Local owner and call identifiers
are fresh private nonces. They do not establish API-to-core or core-to-worker
causality: matching `is_start`, timestamps or M-A3 startup queue topology are
insufficient. Establishing that claim requires a validated exact transport
message link across ZMQ send/receive, the core queue, broadcast enqueue and
worker dequeue/response, plus an API route/request binding. Full RPC, output,
authentication, and whole-scope gates remain false. Default LIF-14 exposure
and production composition are unchanged; source fixture identity alone is
not an installed-image or GPU qualification.

`test_ma6_baseline_profiler_local.py` exercises actual compiled source bodies
with inert queue collaborators, default and repeated calls, unsupported
method, async failure/cancellation, source tampering and false pairs. Tests
were **not run locally** under the session's no-Python rule; PR CI must run
them on the supported Linux CPython builds.

The optional `LocalProfilerRouter` stages only module objects already present
in `sys.modules`. It accepts the exact API two-module set or core three-module
set, installs each segment once, and never imports missing engine modules.
The default `enabled=False` path installs nothing. The caller injects a raw
record sink, session, exact image, and a binding factory that obtains the
current process-start context on every process. Inherited router hooks pass
through without records until the router restores them, clears staging, and
requires a fresh complete segment and binding. The router rechecks every
staged module against `sys.modules` immediately before install, including
modules staged by earlier calls. `observe_loaded(module)` returns true only for an install
done by that call; `last_diagnostic` distinguishes disabled, partial,
unsupported/unloaded/replaced module, source/binding failure, and installed
states. Failed qualification never invokes the engine or imports the other
process's segment. This remains a staging entry point: the production asset
and common collector wiring are a separate follow-up.

기존 callback 준비 경로의 `baseline_profiler_local=True`는 검토된 immutable image 선택값을 `baseline_profiler_local_image`로 함께 받아야 한다. 이 값은 준비할 대상 선택이며 실제 이미지 실측·인증을 뜻하지 않는다. 기존 profiler_control 옵션과는 같은 지점의 소유권이 겹쳐 함께 켜지 않는다. root의 installer는 실제 로드된 module만 router에 전달하고 engine 모듈을 추가 import하지 않는다. 공유 runner/auxiliary 등 기존 경로는 별도로 보존한다.

프로세스를 fork해 상속한 EngineCallbacks는 부모의 정적 sink를 소유하므로 local profiler를 restage하지 않는다. 상속된 local hook은 원 함수로 통과하고, 자식에서 이 관측을 하려면 자식 소유 sink를 가진 새 installer가 필요하다. 이것을 core/worker 관측 완료로 집계하지 않는다. /15·/31·/32·기본 실행 gate와 원격 RPC·auth·출력 완료의 false는 유지한다.
