// 통제 적용. 삭제된 runtime 대시보드 renderCilium()·renderLabReport()를 옮겼다.
import { useParams as useRouteParams } from "react-router-dom";
import { useLabReport, useRuntimeStatus } from "../../api/hooks";
import { Badge } from "../../components/Badges";
import { KeyValue } from "../../components/DetailPanel";
import { PageHead } from "../../components/Misc";
import { useRuntimeProblems } from "./problems";
import { StateBadge, Subtabs, TableCard, formatTime, rt } from "./parts";

export function RuntimeEnforcePage() {
  const { sub = "cilium" } = useRouteParams();
  const status = useRuntimeStatus().data?.item;
  const lab = useLabReport().data?.item;
  const problems = useRuntimeProblems();
  const collector = status?.collectors.find((c) => c.source === "cilium_policy");
  const detail = collector?.detail?.kind === "cilium_policy" ? collector.detail : null;
  const policies = detail?.policies ?? [];
  const configured = collector?.state === "ok" && (detail?.object_count ?? 0) > 0;
  const l02 = lab?.report?.cases.find((c) => c.id === "L-02");
  const realDrops = status?.observations.real_hubble_dropped ?? 0;
  const blocked = l02?.status === "passed" || realDrops > 0;
  const report = lab?.report;
  return (
    <div className="page">
      <PageHead title="통제 적용" />
      <p className="notice">정책이 설정됐다는 것과 실제 차단이 확인됐다는 것은 다릅니다. 실제 결과는 lab 시험 결과로 확인합니다.</p>
      <Subtabs
        tabs={[
          { to: "/runtime/enforce/cilium", label: "Cilium 정책 적용 상태", count: problems?.ciliumCount, badge: problems?.cilium, active: sub === "cilium" },
          { to: "/runtime/enforce/lab", label: "kind lab 시험 결과", count: problems?.labCount, badge: problems?.lab, active: sub === "lab" },
        ]}
      />
      {sub === "cilium" && (
        <TableCard
          title={
            <>
              Cilium 정책 적용 상태{" "}
              <span className="muted small">
                {collector
                  ? `${collector.state} · 마지막 조회 ${formatTime(collector.last_attempt_at)} · 최근 성공 ${formatTime(collector.last_success_at)}${collector.last_error ? ` · ${collector.last_error}` : ""}`
                  : "미설정"}
              </span>
            </>
          }
          extra={
            <div className={rt.stateBadges}>
              <Badge tone={configured ? "accent" : "error"}>{configured ? `설정됨 (정책 객체 ${detail?.object_count}개)` : "정책 객체 확인 불가"}</Badge>
              <Badge tone={blocked ? "accent" : "error"}>
                {blocked
                  ? `저장된 실제 차단 근거 있음 (${l02?.status === "passed" ? "L-02 통과" : ""}${l02?.status === "passed" && realDrops ? " · " : ""}${realDrops ? `Hubble DROPPED ${realDrops}건` : ""})`
                  : "실제 차단 확인 안 됨"}
              </Badge>
            </div>
          }
        >
          <table className={rt.statusTable}>
            <thead>
              <tr><th>정책</th><th>객체 상태</th><th>조건</th></tr>
            </thead>
            <tbody>
              {policies.map((pol) => (
                <tr key={pol.name}>
                  <td className="mono">{pol.name}</td>
                  <td title={pol.last_updated ?? ""}>
                    {pol.node_total > 0
                      ? `node ${pol.node_total} · enforcing ${pol.node_enforcing} · 오류 ${pol.node_errors}`
                      : pol.status_present
                        ? "status 있음 · node 상태 없음"
                        : "객체만 확인"}
                  </td>
                  <td>
                    {pol.conditions.map((c) => `${c.type ?? "조건"}=${c.status ?? "–"}${c.reason ? ` (${c.reason})` : ""}`).join(" · ") || "status/조건 없음"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {policies.length === 0 && (
            <p className={rt.empty}>
              {collector ? (collector.last_error ? `정책 조회 실패: ${collector.last_error}` : "CiliumNetworkPolicy 객체가 없습니다.") : "Cilium 정책 상태 수집원이 설정되지 않았습니다."}
            </p>
          )}
        </TableCard>
      )}
      {sub === "lab" && (
        <TableCard
          title={
            <>
              kind lab 시험 결과{" "}
              <span className="muted small">{lab?.state === "ok" && report ? `${report.overall} · ${report.run_id}` : `${lab?.state ?? "–"}${lab?.error ? ` · ${lab.error}` : ""}`}</span>
            </>
          }
          extra={
            <KeyValue
              items={
                report
                  ? [
                      ["실행", <span className="mono">{report.run_id}</span>],
                      ["환경", `${report.context} · ${report.namespace}`],
                      ["완료", formatTime(report.finished_at)],
                      ["전체", <StateBadge value={report.overall} />],
                    ]
                  : [
                      ["상태", lab?.state ?? "–"],
                      ["경로", <span className="mono">{lab?.configured_path ?? "미설정"}</span>],
                    ]
              }
            />
          }
        >
          <table className={rt.statusTable}>
            <thead>
              <tr><th>시험</th><th>상태</th><th>요약·증거</th></tr>
            </thead>
            <tbody>
              {(report?.cases ?? []).map((c) => (
                <tr key={c.id}>
                  <td className="mono">{`${c.id} · ${c.title}`}</td>
                  <td><StateBadge value={c.status} /></td>
                  <td title={c.limitation ?? c.note ?? ""}>
                    {c.summary}
                    {c.evidence.length ? ` · 증거: ${c.evidence.join(" · ")}` : ""}
                    {c.note && <div className={rt.sub}>{c.note}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!report && (
            <p className={rt.empty}>
              {lab?.state === "not_configured"
                ? "lab 보고서가 설정되지 않았습니다. (runtime을 --lab-report 경로로 실행하면 표시됩니다)"
                : lab?.state === "missing"
                  ? "lab 보고서 파일을 찾지 못했습니다."
                  : `lab 보고서를 읽지 못했습니다: ${lab?.error ?? "알 수 없는 오류"}`}
            </p>
          )}
        </TableCard>
      )}
    </div>
  );
}
