// 대상·구성. 삭제된 runtime 대시보드 renderTarget()·renderBaseline()·renderRevalidation()을 옮겼다.
import { useRevalidation, useRuntimeStatus } from "../../api/hooks";
import type { CompareTable } from "../../api/types";
import { Badge } from "../../components/Badges";
import { PageHead, Query, StateView, WarningBox } from "../../components/Misc";
import { Pagination, usePagination, type PaginationState } from "../../components/Pagination";
import { AnyValue } from "../../components/Value";
import { RT_TRIGGER, label } from "../../lib/labels";
import { StateBadge, TableCard, formatTime, rt } from "./parts";

function evidenceSummary(fields: { field: string; expected: unknown; observed: unknown }[]): string {
  return fields.map((f) => `${f.field}: ${f.expected ?? "–"} → ${f.observed ?? "–"}`).join(" · ") || "–";
}

function CompareRows({ table, withTime, pagination }: { table: CompareTable; withTime: boolean; pagination: PaginationState }) {
  return (
    <tbody>
      {table.rows.slice(pagination.start, pagination.end).map((row) => (
        <tr key={row.field}>
          <td className="mono">{row.field}</td>
          <td><AnyValue v={row.expected} /></td>
          <td title={row.note ?? ""}><AnyValue v={row.observed} /></td>
          <td>{row.source ?? "–"}</td>
          {withTime && <td>{formatTime(row.observed_at)}</td>}
          <td><StateBadge value={row.state} /></td>
        </tr>
      ))}
    </tbody>
  );
}

export function RuntimeTargetPage() {
  const q = useRuntimeStatus();
  const reval = useRevalidation();
  const targetPagination = usePagination(q.data?.item?.comparisons.target.rows.length ?? 0, "page_target");
  const baselinePagination = usePagination(q.data?.item?.comparisons.baseline.rows.length ?? 0, "page_baseline");
  const revalidationPagination = usePagination(reval.data?.items.length ?? 0);
  return (
    <div className="page">
      <PageHead title="대상 엔진" />
      <Query q={q}>
        {(res) => {
          if (!res.item) return <StateView kind="invalid" detail={res.invalid?.errors.join(" / ")} />;
          const s = res.item;
          const t = s.target;
          const regLabel: Record<string, string> = {
            not_configured: "미등록 — 대상 확인 없이 동작",
            ok: `등록됨 · ${t.target_id ?? "–"}${t.target_ref ? ` · ${t.target_ref}` : ""}`,
            registered: `등록됨 · ${t.target_id ?? "–"}${t.target_ref ? ` · ${t.target_ref}` : ""}`,
            error: `등록 파일 오류 (${t.error_code ?? "unknown"}) — 추론 요청 보류`,
          };
          let instance = "";
          if (t.instance_state === "not_configured") instance = "Pod 조회 미설정 — 관측 인스턴스를 구분하지 않음";
          else if (t.instance_id) instance = `마지막 확인 인스턴스 ${t.instance_id} · ${t.instance_pod ?? "–"} · 재시작 ${t.instance_restart_count ?? "–"}회 · 조회 ${formatTime(t.instance_observed_at)}`;
          else instance = `인스턴스 확인 불가: ${t.instance_state} — 추정하지 않음`;
          if (t.last_history) instance += ` · 최근 이력 ${t.last_history.event}${t.last_history.reason ? `(${t.last_history.reason})` : ""} ${formatTime(t.last_history.recorded_at)}`;
          const rv = s.revalidation;
          const items = reval.data?.items ?? [];
          return (
            <>
              {s.comparisons.baseline.notes.map((n) => (
                <p key={n} className="notice">{n}</p>
              ))}
              <TableCard title={<>대상 등록 <span className="muted small">{regLabel[t.registration] ?? t.registration}</span></>}>
                {t.gateway_hold && (
                  <div style={{ padding: "0 16px 8px" }}>
                    <WarningBox>신규 추론 요청 보류(503): {t.gateway_hold.reason_code} — {t.gateway_hold.message}</WarningBox>
                  </div>
                )}
                {t.verification.state === "unavailable" && !t.gateway_hold && (
                  <div style={{ padding: "0 16px 8px" }}>
                    <WarningBox>대상 미확인, 요청은 처리 중 · 마지막 확인 시각 {formatTime(t.verification.last_confirmed_at)}</WarningBox>
                  </div>
                )}
                <table className={rt.statusTable}>
                  <thead>
                    <tr><th>필드</th><th>등록값</th><th>현재</th><th>출처</th><th>상태</th></tr>
                  </thead>
                  <CompareRows table={s.comparisons.target} withTime={false} pagination={targetPagination} />
                </table>
                <Pagination state={targetPagination} />
                {s.comparisons.target.rows.length === 0 && <p className={rt.empty}>대상 비교 행이 없습니다.</p>}
                <p className="notice" style={{ padding: "8px 16px 12px" }}>{instance}</p>
              </TableCard>
              <TableCard title="기준·엔진 실행 설정 비교">
                {s.comparisons.baseline.rows.some((row) => row.source === "kubernetes_spec") && (
                  <p className="notice">엔진 실행 설정은 Pod 선언값(kubernetes_spec)입니다. 관측 불가는 unknown이며, 차이가 나면 재검증을 요청합니다.</p>
                )}
                <table className={rt.statusTable}>
                  <thead>
                    <tr><th>필드</th><th>기준</th><th>현재</th><th>출처</th><th>관측 시각</th><th>상태</th></tr>
                  </thead>
                  <CompareRows table={s.comparisons.baseline} withTime pagination={baselinePagination} />
                </table>
                <Pagination state={baselinePagination} />
                {s.comparisons.baseline.rows.length === 0 && (
                  <p className={rt.empty}>{s.comparisons.baseline.state === "not_configured" ? "기준(baseline)이 설정되지 않았습니다." : "기준 비교 행이 없습니다."}</p>
                )}
              </TableCard>
              <div className="row wrap" style={{ justifyContent: "space-between" }}>
                <h3>재검증 요청</h3>
                <span className="muted small">
                  열림 {rv.open_count} · 현재 근거 {rv.current_condition_count} · 손상 줄 {rv.corrupt_lines} · 평가 오류 {rv.evaluation_errors}
                </span>
              </div>
              <TableCard>
                <table className={rt.statusTable}>
                  <thead>
                    <tr><th>트리거</th><th>근거 요약</th><th>열린 시각</th><th>현재 근거</th><th>출처</th></tr>
                  </thead>
                  <tbody>
                    {items.slice(revalidationPagination.start, revalidationPagination.end).map((x) => {
                      const cond = x.event === "superseded" ? "다른 근거로 대체됨, 해소 아님" : x.event === "condition_cleared" ? "해소됨" : "있음";
                      const tone = x.event === "superseded" ? "muted" : x.current_evidence_present ? "error" : "accent";
                      return (
                        <tr key={x.revalidation_id}>
                          <td>{label(RT_TRIGGER, x.trigger)}</td>
                          <td className="mono">{evidenceSummary(x.changed_fields)}</td>
                          <td>{formatTime(x.opened_at ?? x.created_at)}</td>
                          <td><Badge tone={tone}>{cond}</Badge></td>
                          <td><Badge tone={x.origin_detail === "mock" ? "muted" : "neutral"}>{x.origin_detail === "mock" ? "모의" : x.origin_detail ?? "–"}</Badge></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <Pagination state={revalidationPagination} />
                {items.length === 0 && <p className={rt.empty}>재검증 요청 없음. 변경 사항과 수집 상태를 확인하세요.</p>}
              </TableCard>
            </>
          );
        }}
      </Query>
    </div>
  );
}
