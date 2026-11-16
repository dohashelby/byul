# M-A6 baseline V1 REQ-19: bounded attention metadata candidate

This adapter records actual source-qualified `_prepare_inputs` call outcomes,
its two-element return, and the same weak metadata object's later
`FlashAttentionImpl.forward` call and returned output identity. It is not
registered in an installer/profile, and changes no existing collector,
projector, CheckDef, or engine-sensor gate. Root owns subsequent shared-path
integration.

The exact tag-source checkout is vLLM 0.7.0,
`5204ff5c3feeb96e8a6eea65dfcb78395f90d4d8` (candidate pins, not installed-image
verification). Reviewed fixture SHA-256 values:

| Source | SHA-256 |
| --- | --- |
| `vllm/v1/worker/gpu_model_runner.py` | `1ac3fb18b2adad3609dcf58a1656d6fdd4a568eea6740258e00f6a43c7d4b813` |
| `vllm/v1/attention/backends/flash_attn.py` | `8fe8b9b56dfbefd5db474df21f81c9cbda11f9a49cb611e7d41a0ffc298627ee` |
| `csrc/cache_kernels.cu` (audit only) | `63393506877eb333debb5df93b6f4f9f396d7e61ae90ef62fa2e67a4c069efad` |

The callback reuses the existing exact file/code/globals/descriptor qualifier;
bytecode is computed from the exact source by the existing qualifier in Linux
CI, with no invented ABI digest. Object IDs are observer-random, session-keyed
HMACs over weak object tokens. Requests are bounded, session-keyed HMACs.
No raw request ID, tensor value, token array, pointer, exception text, property,
`int()` or `.item()` is read by the new observation code. The consumer accepts
only closed typed fields and expected process/session/source scope, unique
CALL→RETURN/RAISE pairs and stable call owners; unmatched or ambiguous transfer
cannot establish a metadata chain. This does not authenticate serialized rows.

Concrete partial facts include the scheduler's declared per-request counts,
batch mapping, declared runner capacities, native return structure, selected
profiling/regular/cascade Python branch, unchanged metadata scalar values, and
whether forward returned the provided output. The declared arithmetic relations
are not actual device buffer bounds. `num_input_tokens` is recorded at both
endpoints but excluded from stability: `execute_model` intentionally sets the
padded count after `_prepare_inputs` returns. No V2 kernel range formula is
applied to V1.

Only exact nonnegative bounded Python integers are accepted. Real NumPy scalar
values (including possible `max_seq_len`/`common_prefix_len`) remain
`UNQUALIFIED_INTEGER_SCALAR` until an exact native interface is qualified;
this bundle adds no NumPy dependency or type-name conversion. Metadata-none
profiling returns the provided output without selecting a backend. Regular and
cascade outcomes are actual Python source returns; the transitive backend
implementation remains unverified.

The C++ audit source derives cache-copy token count from `slot_mapping.size(0)`
and skips negative slots; source identity alone cannot prove live nonnegative
slot values or upper bounds. Installed C/native dispatch identity, the separately
packaged FlashAttention implementation, tensor extents/strides/capacity, buffer
initialization, nonblocking copy completion and GPU execution completion are
explicit gaps. Every kernel-safety/full-question flag stays false. No GPU or
installed operator was executed.

`test_ma6_baseline_attention_metadata.py` executes the exact original function
code objects with inert **EXAMPLE** host-array/tensor/backend collaborators.
These collaborators only enable CPU control-flow tests; their arithmetic and
operator selections do not qualify NumPy, Torch, CUDA or FlashAttention. Tests
feed actual callback producer rows directly into the new consumer for default
regular, cascade and profiling outcomes, unsupported scalar/property access,
source/process/owner/time/field/metadata tampering, missing terminal records,
substituted metadata and preserved backend exceptions. Local Python/pytest was
not run; Linux CI must validate this new file, including on observer Python
3.12.8. No new workflow edit is made here because root owns CI integration.

기존 callback 준비 경로에 `baseline_attention_metadata=True`를 명시하면 이 두 source 지점만 opt-in으로 설치한다. 기본값은 False이며 설정·manifest·asset bytes에 남는다. 같은 GPUModelRunner 모듈의 sampler 지점은 module별 point 목록에 함께 보존하고, 원 sampler/graph/기존 V2 gate를 열지 않는다. 직접 회귀는 실제 원 함수→공유 소유 sink→typed partial consumer를 연결하며 native library/dispatch/범위/초기화/완료·전체 질문의 false를 유지한다.
