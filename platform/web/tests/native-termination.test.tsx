import { describe, expect, it } from "vitest";
import actual from "./fixtures/native-termination-actual.json";
import { screen } from "@testing-library/react";
import { nativeTermination, NATIVE_SCOPES } from "../src/lib/nativeTermination";
import { NativeTerminationDetail } from "../src/pages/runtime/RequestDrawer";
import type { RuntimeRequestDetail } from "../src/api/types";
import { renderAt } from "./helpers";
const id = "a".repeat(32);
function receipt() {
  return { request_id: id, historical_receipt: true, capture_observed_at: "2026-11-09T00:00:00Z", capture_state: "active", session_sealed: false,
    projection: { schema: "byul-runtime-termination/1", request: { context: { request_id: id, backend_kind: "vllm" } }, runtime_native_binding: { state: "bound" },
      source_data_origin: "MEASURED", actual_engine_execution_verified: false, closure_basis: "live_checkpoint", checkpoint_coverage_verified: true,
      configured_deadline_ms: 5000, required_scopes: Object.keys(NATIVE_SCOPES), native_termination_verified: true, worker_gpu_release_verified: true,
      native_projection: { binding_observed: true, checkpoint_coverage_verified: true, whole_scope_complete: true, worker_gpu_release_verified: true,
        activation_violations: [] as Record<string, unknown>[], scope_status: Object.keys(NATIVE_SCOPES).map(scope => ({ scope, required: true, evidence_complete: true, termination_verified: true, completion_elapsed_ns: 45080752 })) } } };
}
describe("request-bound native termination", () => {
  it("displays exact seven-scope actual projection without upgrading outer assurance", () => {
    const result = nativeTermination(actual.request_id, [actual], "vllm");
    expect(result?.verdict).toBe("확인");
    expect(result?.scopes.find(s => s.scope === "gpu_transfers")?.elapsedMs).toBe(45.080752);
    expect(actual.projection.actual_engine_execution_verified).toBe(false);
  });
  it("renders all scopes and timing without changing outer assurance", () => {
    const r = receipt(), before = structuredClone(r);
    const view = renderAt("/request", "/request", <NativeTerminationDetail d={{ request_id: id, backend_kind: "vllm", native: { engine_receipts: [r] } } as unknown as RuntimeRequestDetail} />);
    expect(screen.getByRole("heading", { name: "요청별 native 종결 · 확인" })).toBeTruthy();
    expect(screen.getByText("보존된 요청 근거 · MEASURED")).toBeTruthy();
    expect(screen.getByText("5000 ms")).toBeTruthy();
    expect(view.container.querySelectorAll("tbody tr")).toHaveLength(7);
    expect(screen.getAllByText("45.081 ms")).toHaveLength(7);
    expect(screen.getByRole("link", { name: "현재 센서 수집 상태" })).toBeTruthy();
    expect(r).toEqual(before);
  });
  it.each(["string-bool", "missing-scope", "duplicate-scope", "deadline", "coverage", "uuid", "context-uuid", "unknown-origin", "malformed-violation"])("keeps %s evidence UNKNOWN", fault => {
    const r = receipt(), p = r.projection;
    if (fault === "string-bool") Object.assign(p, { native_termination_verified: "true" });
    if (fault === "missing-scope") p.native_projection.scope_status.pop();
    if (fault === "duplicate-scope") p.native_projection.scope_status.push(p.native_projection.scope_status[0]);
    if (fault === "deadline") p.configured_deadline_ms = 1;
    if (fault === "coverage") Object.assign(p, { checkpoint_coverage_verified: "true" });
    if (fault === "uuid") r.request_id = "b".repeat(32);
    if (fault === "context-uuid") p.request.context.request_id = "b".repeat(32);
    if (fault === "unknown-origin") p.source_data_origin = "live";
    if (fault === "malformed-violation") p.native_projection.activation_violations.push({ scope: "worker_batch", code: "invented" });
    expect(nativeTermination(id, [r])?.verdict).toBe("미확인");
  });
  it("retains validated FAIL across conflicting later positive receipts", () => {
    const bad = receipt();
    bad.projection.native_projection.activation_violations.push({ scope: "worker_batch", code: "REQUEST_STATE_RETAINED_AFTER_DEADLINE", recording: 1, seq: 10, elapsed_ns: 6000000000 });
    bad.projection.native_projection.whole_scope_complete = false;
    const result = nativeTermination(id, [receipt(), bad, receipt()]);
    expect(result?.verdict).toBe("위반");
    expect(result?.scopes.find(s => s.scope === "worker_batch")?.verdict).toBe("위반");
  });
  it("does not use another UUID's negative evidence", () => {
    const bad = receipt();bad.request_id = "b".repeat(32);
    bad.projection.native_projection.activation_violations.push({ scope: "worker_batch", code: "REQUEST_STATE_RETAINED_AFTER_DEADLINE", recording: 1, seq: 10, elapsed_ns: 6000000000 });
    expect(nativeTermination(id, [bad])?.verdict).toBe("미확인");
  });
  it("keeps ambiguous receipts UNKNOWN", () => {
    const partial = receipt();partial.projection.native_termination_verified = false;
    expect(nativeTermination(id, [receipt(), partial])?.verdict).toBe("미확인");
    expect(nativeTermination(id, [])).toBeNull();
  });
});

function sglangReceipt() {
  const r = receipt(), p = r.projection;
  p.request.context.backend_kind = "sglang";
  p.required_scopes = p.required_scopes.filter(scope => scope !== "engine_core");
  p.native_projection.scope_status = p.native_projection.scope_status.filter(row => row.scope !== "engine_core");
  return r;
}
describe("engine-bound native scope sets", () => {
  it("confirms SGLang six scopes without a phantom engine-core row", () => {
    const r = sglangReceipt(), before = structuredClone(r);
    const result = nativeTermination(id, [r], "sglang");
    expect(result?.verdict).toBe("확인");
    expect(result?.scopes).toHaveLength(6);
    expect(result?.scopes.some(row => row.scope === "engine_core")).toBe(false);
    const view = renderAt("/request", "/request", <NativeTerminationDetail d={{ request_id: id, backend_kind: "sglang", native: { engine_receipts: [r] } } as unknown as RuntimeRequestDetail} />);
    expect(view.container.querySelectorAll("tbody tr")).toHaveLength(6);
    expect(r).toEqual(before);
  });
  it.each(["missing", "extra", "duplicate", "unknown-backend", "backend-mismatch", "unsupported-violation"])("keeps SGLang %s evidence UNKNOWN", fault => {
    const r = sglangReceipt(), p = r.projection;
    if (fault === "missing") p.native_projection.scope_status.pop();
    if (fault === "extra") p.required_scopes.push("engine_core");
    if (fault === "duplicate") p.native_projection.scope_status.push(p.native_projection.scope_status[0]);
    if (fault === "unsupported-violation") p.native_projection.activation_violations.push({ scope: "engine_core", code: "REQUEST_STATE_RETAINED_AFTER_DEADLINE", recording: 1, seq: 10, elapsed_ns: 6000000000 });
    expect(nativeTermination(id, [r], fault === "unknown-backend" ? "other" : fault === "backend-mismatch" ? "vllm" : "sglang")?.verdict).toBe("미확인");
  });
  it("preserves FAIL priority and handles mixed engine receipts conservatively", () => {
    const good = sglangReceipt(), bad = structuredClone(good);
    bad.projection.native_projection.activation_violations.push({ scope: "worker_batch", code: "REQUEST_STATE_RETAINED_AFTER_DEADLINE", recording: 1, seq: 10, elapsed_ns: 6000000000 });
    expect(nativeTermination(id, [good, bad, good], "sglang")?.verdict).toBe("위반");
    expect(nativeTermination(id, [good, receipt()], "sglang")?.verdict).toBe("미확인");
    expect(nativeTermination(id, [receipt()], "other")?.verdict).toBe("미확인");
    expect(nativeTermination(id, [receipt()], "sglang")?.verdict).toBe("미확인");
    const partial = receipt(); partial.projection.native_projection.scope_status = partial.projection.native_projection.scope_status.filter(row => row.scope !== "engine_core");
    expect(nativeTermination(id, [partial], "vllm")?.verdict).toBe("미확인");
  });
});
