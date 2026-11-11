# Selected37 integration validation

This integration connects runtime collection, the Gateway, update checks, and the Platform API/web interface. Validation records distinguish the earlier deployed source from the latest-main integration.

| Scope | Evidence and result |
| --- | --- |
| Original selected37 acceptance | 147 criteria accepted and one conditional criterion in the original acceptance scope. These results retain their original source and configuration boundaries. |
| Earlier integrated deployment | Gateway/API source `db0939f5` and host capture source `ee5f49153965077e2e202a615e29e3f5fd1c39ca` were exercised with vLLM and SGLang. Current target/model bindings, request/check collection, host assets, lifecycle evidence, and supported cache boundaries were checked within their stated scope. |
| Gateway event projection | The reviewed inline-record change removed per-request detail fan-out while retaining bounded source reads, current authorization, deletion handling, and existing event projection. The deployed events API returned 200 within the measured request window. |
| GPU evidence | Compiled vLLM G1 GPU support remained UNKNOWN because the required seven-scope stream set was incomplete. Eager G2 produced actual evidence of request termination across seven scopes and GPU release (`true`). Its separate fields remained `actual_engine_execution_verified=false` and `new_security_verdict=null`. |
| Tetragon | The deployed policies were checked in `monitor_only` mode. Enforcement-mode acceptance was not performed. |
| Automated baseline | PR baseline `a9694223`, on main `3bd6a995`, passed runtime, Platform API/web, shared-contract, UPDATE and isolated wheel-install CI. Follow-up installer changes receive their own affected-boundary verification. GPU execution results retain their original source/configuration scope. |
| UI | Automated web tests and build results belong to their recorded source revisions. A visual browser review of this merged revision has not been performed. |

The integration preserves supported-scope and UNKNOWN outcomes. The original acceptance and later targeted runs do not establish a new complete AWS execution of all 37 checklist items on the latest merged revision. Raw captures and operational identifiers remain in the retained private evidence set.

## Response-path continuity and first server check

The following files have identical bytes at the measured Gateway revision `db0939f5`, the earlier integration `ee5f4915`, and PR baseline `a9694223`:

| File | SHA-256 |
| --- | --- |
| `runtime/src/serving_runtime/mvp/response_privacy.py` | `7119f8c544b353e878c9a859e3f8b350fbc08f82c85305ec28c180c38c8f7353` |
| `runtime/src/serving_runtime/mvp/relay.py` | `29fb13899c5ce6db2fd415a36f28f8a1159a8dad497fd13d8037ca81a02692c7` |

Existing vLLM and SGLang JSON/SSE observations apply to those exact response-path bytes and their recorded profiles. Specific keep-alive and final usage-event cases need explicit records before claiming those cases were exercised.

For the first server deployment of the revised installer:

1. Prepare the updated contract consumers, then the Gateway, before activating Platform API/web from the same release and switching traffic. Enable expanded Platform polling after the updated Gateway is ready. Confirm the installed source, interpreter and configuration; one Tetragon status writer; and current stream heartbeat. Use the installer migration checks for collector replacement and the contract compatibility notes for older consumers.
2. Run vLLM and SGLang sequentially on the single GPU. For each engine, check normal JSON, SSE through DONE, emitted keep-alive/final usage events, requested and unrequested metadata, error handling and a normal follow-up. Keep unsupported or unobserved cases explicit.
3. Record response status, stream completion, field filtering and latency with the same image, model and request settings as the comparison baseline. Retain the line/event-size bounds and failure semantics.
4. Confirm collector freshness and the request/evidence view after rollout. Preserve the compiled-G1 GPU UNKNOWN and the recorded eager-G2 qualification fields. Record enforcement-mode verification separately from monitor-only observations.

These deployment checks are pending; the recorded GPU evidence remains tied to its original source and configuration.
