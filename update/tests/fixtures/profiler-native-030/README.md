# vLLM candidate profiler source fixtures

Unmodified Apache-2.0 source bytes from the existing clean vLLM v0.30.0 clone,
commit `ced6857afa0ea7b2e3f0846a62e1394e90f15607`. Upstream SPDX notices are
preserved. These are source fixtures, not a GPU execution or installation proof.

| Fixture | Upstream path | SHA-256 |
|---|---|---|
| wrapper.py.txt | vllm/profiler/wrapper.py | b1d3fff5035031d86ead1925d541cd07f084b6711875b592401b5528e3f730f4 |
| gpu_worker.py.txt | vllm/v1/worker/gpu_worker.py | 6994436e4547c0996ab555b3e814e8ccea26b650dfc1b378b97d640745309347 |
| api_router.py.txt | vllm/entrypoints/serve/profile/api_router.py | b943c3a2201134e2d0b38bfa86d00e8b5685f5be6a54f6c7178dee784c1b78e5 |

The test executes selected exact function bodies over an EXAMPLE backend. Its
returns and wrapper flags never certify native collection, trace output or RPC.
