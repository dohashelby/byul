# M-A6 0.7 V1 profiler local-call candidate

The 0.7 API defines `/start_profile` and `/stop_profile` handlers inside the
`VLLM_TORCH_PROFILER_DIR` conditional. Their async route calls
`AsyncLLM.start_profile/stop_profile`, then `AsyncMPClient.profile_async` and
`_send_input(PROFILE, EngineCoreProfile)`. The core process receives the ZMQ
frames, decodes and queues the request, and invokes
`EngineCore._handle_client_request`, which calls `model_executor.profile`.
`Executor.profile` calls `MultiprocExecutor.collective_rpc('profile',
args=(is_start,))`; broadcast queue delivery reaches
`WorkerProc.worker_busy_loop` and `Worker.profile`. This actual async API path
does **not** call `MPClient.profile` or `EngineCore.profile`.

`vllm_baseline_profiler_local.py` is an unregistered candidate. It verifies
the exact installed source files and live function bodies of one selected
process-local segment before any hook: API (`async_llm`, `core_client`) or
core (`core`, `abstract`, `multiproc_executor`). It never imports the other
process's files. It records request-free CALL/RETURN/RAISE pairs for the two local paths:
`AsyncLLM` → `AsyncMPClient`, and `EngineCore._handle_client_request` →
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
