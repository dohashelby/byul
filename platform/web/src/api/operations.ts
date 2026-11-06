// Internal protocol candidate; credentials are managed by operationSession.
export type Expected = { bundle_id: string; approval: "verified"; policy_id?: string | null; mode?: string | null; spec_sha256?: string | null };
export type Readiness = { name: string; method: string | null; expected: Expected; checks: Record<string, boolean>; evidence_sha256: string[];
  monitor_windows?: { node: string; since: string; until: string; continuous: boolean; event_count: number; reviewed_event_count: number; false_positive_count: number }[];
  capability_basis?: "static" | "live_trial" | null;
  capability_reason?: "static_unavailable" | "evidence_required" | "capability_missing" | null };
export type Actor = { id: string; role: "viewer" | "runtime_operator" | "approver" | "agent" };
export type Operation = { id: string; server: string; kind: string; state: string; requested_by: string; approval_required: boolean;
  requested_role?: string; created_at?: string; claimed_at?: string; claimed_by?: string; running_at?: string; expires_at?: string; lease_until?: string;
  expected?: Expected; payload?: Record<string, unknown>; lab_self_approval?: boolean;
  approvals: { token_id: string; role?: string; at?: string; self_approval: boolean }[];
  result?: { code: string; reason_code?: string | null; dry_run: boolean; started_at?: string; finished_at?: string;
    before?: Expected | null; after?: Expected | null; evidence_sha256: string[]; steps: {stage: string; state: string}[] }; error_code?: string };
export type AgentState = { server: string; at?: string; last_poll_at?: string; current: Expected | null; policies: Readiness[];
  bundles?: { bundle_id: string; sha256: string }[] };
export type Operations = { items: Operation[]; agents: AgentState[]; actor?: Actor; lab_self_approval: boolean; monitor_seconds: number };
export async function operationApi<T>(token: string, path = "", body?: unknown): Promise<T> {
  const response = await fetch(`/api/runtime/operations${path}`, { method: body === undefined ? "GET" : "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail?.code ?? result.code ?? `HTTP_${response.status}`);
  return result as T;
}
export const CHECK_LABELS: Record<string, string> = {
  policy: "정책 enabled·차단 동작", evidence: "방식·spec 해시·커널 일치 시험 근거", capability: "차단 능력 확인 (사전 점검 또는 실측 시험)",
  monitor_period: "모니터 기간·오탐 0 또는 개별 검토", collection: "수집 복구 4조건", allowed_mode: "intent 허용 모드",
};
export function enforceReady(agent: AgentState | undefined, row: Readiness | undefined) {
  const age = Date.now() - Date.parse(agent?.at ?? "");
  return !!row && age >= 0 && age < 30_000 && Object.keys(CHECK_LABELS).every(k => row.checks[k] === true);
}
