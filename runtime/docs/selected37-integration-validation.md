# Selected37 integration validation

This integration connects runtime collection, the Gateway, update checks, and the Platform API/web interface. Validation records distinguish the earlier deployed source from the latest-main integration.

| Scope | Evidence and result |
| --- | --- |
| Original selected37 acceptance | 147 criteria accepted and one conditional criterion in the original acceptance scope. These results retain their original source and configuration boundaries. |
| Earlier integrated deployment | Gateway/API source `db0939f5` and host capture source `ee5f49153965077e2e202a615e29e3f5fd1c39ca` were exercised with vLLM and SGLang. Current target/model bindings, request/check collection, host assets, lifecycle evidence, and supported cache boundaries were checked within their stated scope. |
| Gateway event projection | The reviewed inline-record change removed per-request detail fan-out while retaining bounded source reads, current authorization, deletion handling, and existing event projection. The deployed events API returned 200 within the measured request window. |
| GPU evidence | Compiled vLLM G1 GPU support remained UNKNOWN because the required seven-scope stream set was incomplete. Eager G2 produced actual evidence of request termination across seven scopes and GPU release (`true`). Its separate fields remained `actual_engine_execution_verified=false` and `new_security_verdict=null`. |
| Tetragon | The deployed policies were checked in `monitor_only` mode. Enforcement-mode acceptance was not performed. |
| Latest-main integration | The subsequent merge onto main `3bd6a99511b628b6824a7bafd2de0cb7daa614bc` requires its own CPU, API, web, shared-contract, and historical-source checks. Earlier execution results apply only where source and configuration equivalence is established. |
| UI | Automated web tests and build results belong to their recorded source revisions. A visual browser review of this merged revision has not been performed. |
| Publication | At publication, GitHub CI was pending and remote merge had not been performed. Subsequent CI results belong in the pull request. |

The integration preserves supported-scope and UNKNOWN outcomes. The original acceptance and later targeted runs do not establish a new complete AWS execution of all 37 checklist items on the latest merged revision. Raw captures and operational identifiers remain in the retained private evidence set.
