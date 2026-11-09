/** Display existing request-bound native projections; do not infer sensor health. */
export const NATIVE_SCOPES = {
  asgi_transport: "전송 종결", frontend_request: "프런트엔드 요청", engine_core: "엔진 코어",
  scheduler_state: "스케줄러 상태", worker_batch: "워커 배치", kv_references: "KV 참조", gpu_transfers: "GPU 전송·참조",
} as const;
export type NativeVerdict = "확인" | "위반" | "미확인";
type Obj = Record<string, unknown>;
const obj = (x: unknown): Obj => x !== null && typeof x === "object" && !Array.isArray(x) ? x as Obj : {};
const list = (x: unknown): unknown[] => Array.isArray(x) ? x : [];
const number = (x: unknown): number | null => typeof x === "number" && Number.isFinite(x) && x >= 0 ? x : null;
const text = (x: unknown): string | null => typeof x === "string" && x.length <= 256 ? x : null;
const known = (x: unknown): x is keyof typeof NATIVE_SCOPES => typeof x === "string" && Object.hasOwn(NATIVE_SCOPES, x);
export interface NativeTermination {
  verdict: NativeVerdict;
  scopes: { scope: string; label: string; verdict: NativeVerdict; elapsedMs: number | null }[];
  deadlineMs: number | null;
  capturedAt: string | null;
  source: string | null;
  basis: string | null;
  historical: boolean;
  captureState: string | null;
  reasons: string[];
}

export function nativeTermination(requestId: string, receipts: unknown, backendKind?: string | null): NativeTermination | null {
  const candidates = list(receipts).filter(x => Object.hasOwn(obj(x), "projection"));
  if (!candidates.length) return null;
  const parsed = candidates.map(value => {
    const entry = obj(value), p = obj(entry.projection), n = obj(p.native_projection);
    const context = obj(obj(p.request).context);
    const bound = entry.request_id === requestId && context.request_id === requestId;
    const engine = backendKind ?? context.backend_kind;
    const expected = engine === "vllm" ? Object.keys(NATIVE_SCOPES)
      : engine === "sglang" ? Object.keys(NATIVE_SCOPES).filter(scope => scope !== "engine_core") : [];
    const engineBound = expected.length > 0 && (context.backend_kind === undefined || context.backend_kind === engine);
    const required = list(p.required_scopes);
    const deadline = number(p.configured_deadline_ms);
    const violations = list(n.activation_violations).map(obj);
    const failures = violations.filter(v => known(v.scope) && expected.includes(v.scope) && Number.isSafeInteger(v.recording) && Number(v.recording) >= 0
      && Number.isSafeInteger(v.seq) && Number(v.seq) >= 0
      && (v.code === "REQUEST_STATE_RETAINED_AFTER_DEADLINE" ? number(v.elapsed_ns) !== null && deadline !== null && Number(v.elapsed_ns) > deadline * 1e6
        : v.code === "REQUEST_STREAM_PENDING_AFTER_DEADLINE" && v.confirmed_violation === true && v.configured_deadline_ms === deadline));
    const valid = bound && engineBound && p.schema === "byul-runtime-termination/1" && obj(p.runtime_native_binding).state === "bound"
      && n.binding_observed === true && typeof p.native_termination_verified === "boolean"
      && typeof p.worker_gpu_release_verified === "boolean" && typeof n.whole_scope_complete === "boolean"
      && typeof n.worker_gpu_release_verified === "boolean" && Array.isArray(n.activation_violations)
      && violations.length === failures.length && deadline !== null && Number.isSafeInteger(deadline) && deadline > 0 && deadline <= 300000
      && required.length > 0 && required.every(scope => known(scope) && expected.includes(scope)) && new Set(required).size === required.length
      && ["MEASURED", "DECLARED", "EXAMPLE"].includes(String(p.source_data_origin))
      && (p.closure_basis === "live_checkpoint" ? p.checkpoint_coverage_verified === true && n.checkpoint_coverage_verified === true : p.closure_basis === undefined && entry.session_sealed === true);
    const scopes = Object.entries(NATIVE_SCOPES).filter(([scope]) => expected.length === 0 || expected.includes(scope)).map(([scope, label]) => {
      const rows = list(n.scope_status).map(obj).filter(r => r.scope === scope);
      const row = rows[0] ?? {}, elapsed = number(row.completion_elapsed_ns);
      const violated = valid && failures.some(v => v.scope === scope);
      const confirmed = valid && rows.length === 1 && row.required === true && row.evidence_complete === true
        && row.termination_verified === true && elapsed !== null && elapsed / 1e6 <= deadline!;
      return { scope, label, verdict: (violated ? "위반" : confirmed ? "확인" : "미확인") as NativeVerdict, elapsedMs: !bound || elapsed === null ? null : elapsed / 1e6 };
    });
    const fail = valid && failures.length > 0;
    const pass = valid && required.length === expected.length && list(n.scope_status).length === expected.length && scopes.every(s => s.verdict === "확인")
      && p.native_termination_verified === true && p.worker_gpu_release_verified === true
      && n.whole_scope_complete === true && n.worker_gpu_release_verified === true;
    return { verdict: (fail ? "위반" : pass ? "확인" : "미확인") as NativeVerdict, scopes,
      deadlineMs: bound ? deadline : null, capturedAt: bound ? text(entry.capture_observed_at) : null, source: bound ? text(p.source_data_origin) : null, basis: bound ? (entry.session_sealed === true && p.closure_basis === undefined ? "sealed_capture" : text(p.closure_basis)) : null,
      historical: entry.historical_receipt === true, captureState: text(entry.capture_state),
      reasons: bound ? failures.map(v => String(v.code)) : ["요청 UUID 불일치"] };
  });
  // Preserve any matching negative evidence; never let a later PASS hide it.
  const selected = parsed.find(p => p.verdict === "위반") ?? parsed.find(p => p.verdict === "미확인") ?? parsed[0];
  return { ...selected, scopes: selected.scopes.map(row => {
    const values = parsed.flatMap(p => p.scopes.filter(s => s.scope === row.scope));
    return values.find(s => s.verdict === "위반") ?? values.find(s => s.verdict === "미확인") ?? row;
  }) };
}
