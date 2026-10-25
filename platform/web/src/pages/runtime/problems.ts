// 영역별 경고 수. 삭제된 runtime 대시보드 renderNav()와 같은 규칙으로 센다(어느 화면에서든 문제가 보이게).
import { useLabReport, useRequestSummary, useRevalidation, useRuntimeEvents, useRuntimeStatus } from "../../api/hooks";

export type RuntimeProblems = {
  requests: number;
  policy: number;
  target: number;
  observe: number;
  collectorProblems: string[];
  tetragonFlagged: number;
  hubbleCount: number;
  tetragonCount: number;
  cilium: number;
  ciliumCount: number;
  lab: number;
  labCount: number;
  overview: number;
};

const FLAGGED = ["outside_allowlist", "suspect_binary", "exec_from_cache", "phase_unknown", "phase_signal_unverified"];

export function useRuntimeProblems(): RuntimeProblems | null {
  const status = useRuntimeStatus().data?.item;
  const summary = useRequestSummary().data;
  const reval = useRevalidation().data;
  const lab = useLabReport().data?.item;
  const events = useRuntimeEvents().data?.items;
  if (!status || !summary) return null;

  const requests = summary.counts.error;
  const approval = status.policy.approval_state;
  const policy = (status.policy.gateway_active ? 0 : 1) + (["not_configured", "verified"].includes(approval) ? 0 : 1);
  const openReval = (reval?.items ?? []).filter((x) => x.status === "open").length;
  const target = status.target.comparison_counts.mismatch + (status.target.registration === "error" ? 1 : 0) + openReval;
  const collectorProblems = status.collectors
    .filter((c) => !["ok", "disconnected"].includes(c.state) || (c.state === "disconnected" && c.source.startsWith("observation_feed")))
    .map((c) => `${c.source}: ${c.state}`);
  const observations = (events ?? []).filter((e) => e.observation);
  const tetragon = observations.filter((e) => e.source === "tetragon");
  const tetragonFlagged = tetragon.filter((e) => {
    const o = e.observation!;
    return o.kind === "process_exec" && (o.exec_verdict ? FLAGGED.includes(o.exec_verdict) : o.allowed_binary === false);
  }).length;
  const cilium = status.collectors.find((c) => c.source === "cilium_policy");
  const ciliumProblem = cilium && cilium.state !== "ok" ? 1 : 0;
  const ciliumCount = cilium?.detail?.kind === "cilium_policy" ? cilium.detail.policies.length : 0;
  const failedCases = lab?.report ? lab.report.cases.filter((c) => c.status === "failed").length : 0;
  const labProblem = failedCases + (lab && !["ok", "not_configured"].includes(lab.state) ? 1 : 0);
  return {
    requests,
    policy,
    target,
    observe: collectorProblems.length,
    collectorProblems,
    tetragonFlagged,
    hubbleCount: observations.filter((e) => e.source === "hubble").length,
    tetragonCount: tetragon.length,
    cilium: ciliumProblem,
    ciliumCount,
    lab: labProblem,
    labCount: lab?.report?.cases.length ?? 0,
    overview: requests + policy + target + collectorProblems.length + ciliumProblem + labProblem,
  };
}
