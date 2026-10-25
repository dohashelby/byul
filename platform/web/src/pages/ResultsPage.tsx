import { useResult, useResults } from "../api/hooks";
import type { Check, VerificationResult } from "../api/types";
import { Badge, EnumBadge, OriginBadge, VerdictBadge } from "../components/Badges";
import { DataTable, type Column } from "../components/DataTable";
import { DetailPanel, KeyValue } from "../components/DetailPanel";
import { JsonView } from "../components/JsonView";
import { Banner, Cards, InvalidRows, MetricCard, PageHead, Query, StateView } from "../components/Misc";
import { Short, Time, Value } from "../components/Value";
import { APPLICABILITY, AREA, COMPARISON, EVIDENCE_SOURCE, GAP, MODE, PACK_STATUS, SIDE, label } from "../lib/labels";
import { useParams } from "../lib/url";
import p from "./pages.module.css";

const AREAS = ["SUP", "REQ", "PLT", "LIF", "OBS"] as const;
const SIDES = ["single", "baseline", "candidate"] as const;

/** 팩이 DRAFT이거나 자료가 EXAMPLE이면 공식 결과가 아니다. */
export function isUnofficial(r: VerificationResult): boolean {
  return r.pack.review_status === "DRAFT" || r.data_origin === "EXAMPLE";
}

function checkColumns(mode: string): Column<Check>[] {
  const sides = mode === "update" ? (["baseline", "candidate"] as const) : (["single"] as const);
  const cols: Column<Check>[] = [
    { id: "check_id", header: "점검 ID", cell: (c) => <span className="mono">{c.check_id}</span>, sort: (c) => c.check_id },
    { id: "applicability", header: "적용 여부", cell: (c) => <EnumBadge table={APPLICABILITY} value={c.applicability} />, sort: (c) => c.applicability },
    { id: "selected", header: "선정", cell: (c) => <Value v={c.selected} />, sort: (c) => c.selected },
  ];
  for (const sd of sides) {
    for (const axis of ["security", "functional", "performance"] as const) {
      cols.push({
        id: `${sd}.${axis}`,
        header: `${SIDE[sd]} ${axis === "security" ? "보안" : axis === "functional" ? "기능" : "성능"}`,
        cell: (c) => (c.sides[sd] ? <VerdictBadge verdict={c.sides[sd][axis]} /> : <Value v={null} />),
        sort: (c) => c.sides[sd]?.[axis] ?? null,
        hidden: axis !== "security",
      });
    }
  }
  if (mode === "update") cols.push({ id: "comparison", header: "비교", cell: (c) => <EnumBadge table={COMPARISON} value={c.comparison} />, sort: (c) => c.comparison });
  cols.push(
    { id: "gaps", header: "공백", cell: (c) => (c.gaps.length ? c.gaps.map((g) => label(GAP, g)).join(", ") : <Value v={null} />), sort: (c) => c.gaps.length },
    { id: "evidence_source", header: "증거 출처", cell: (c) => <Value v={c.evidence_source ? label(EVIDENCE_SOURCE, c.evidence_source) : null} />, sort: (c) => c.evidence_source },
    { id: "exception", header: "예외 승인", cell: (c) => (c.exception_approved ? <Badge tone="warn">예외 승인됨</Badge> : <Value v={c.exception_approved ?? null} />), sort: (c) => c.exception_approved },
  );
  return cols;
}

export function ResultsPage() {
  const list = useResults();
  const [params, patch] = useParams();
  return (
    <div className="page">
      <Query q={list}>
        {(data) => {
          if (data.items.length === 0)
            return (
              <>
                <div className="card">
                  <StateView kind={data.invalid.length ? "invalid" : "empty"} detail="검증 보고서가 없습니다." />
                </div>
                <InvalidRows items={data.invalid} what="검증 결과" />
              </>
            );
          const reportId = params.get("report") ?? data.items[0].report_ref.id;
          return (
            <>
              <PageHead title="격리 검증 결과">
              <label className="row small">
                <span className="muted">보고서</span>
                <select className="select" value={reportId} onChange={(e) => patch({ report: e.target.value, sel: null })}>
                  {data.items.map((r) => (
                    <option key={r.report_ref.id} value={r.report_ref.id}>
                      {r.created_at} · {label(MODE, r.mode)} · {r.data_origin} · {r.report_ref.id}
                    </option>
                  ))}
                </select>
              </label>
              </PageHead>
              <Report id={reportId} selected={params.get("sel")} onSelect={(id) => patch({ sel: id })} />
              <InvalidRows items={data.invalid} what="검증 결과" />
            </>
          );
        }}
      </Query>
    </div>
  );
}

function Report({ id, selected, onSelect }: { id: string; selected: string | null; onSelect: (id: string | null) => void }) {
  const q = useResult(id);
  return (
    <Query q={q}>
      {(d) => {
        const r = d.item;
        const counts = Object.values(r.summary.evidence_counts);
        const sum = (k: "manual" | "automated" | "not_run") => counts.reduce((a, c) => a + c[k], 0);
        const check = r.checks.find((c) => c.check_id === selected) ?? null;
        return (
          <>
            {isUnofficial(r) && (
              <div style={{ position: "sticky", top: 0, zIndex: 3 }}>
                <Banner kind="unofficial">
                  공식 결과 아님 — {r.pack.review_status === "DRAFT" ? "검토 전 팩(DRAFT)" : ""}
                  {r.pack.review_status === "DRAFT" && r.data_origin === "EXAMPLE" ? " · " : ""}
                  {r.data_origin === "EXAMPLE" ? "예시(EXAMPLE) 자료" : ""}
                </Banner>
              </div>
            )}
            <Cards>
              <MetricCard title="보고서">
                <span className="mono">{r.report_ref.id}</span>
                <Time iso={r.created_at} />
                <span>{label(MODE, r.mode)}</span>
                <OriginBadge origin={r.data_origin} />
                <EnumBadge table={PACK_STATUS} value={r.pack.review_status} />
              </MetricCard>
              <MetricCard title="영역별 보안 판정">
                {AREAS.map((a) => (
                  <span key={a} className={p.areaRow} title={AREA[a]}>
                    {a} <VerdictBadge verdict={r.summary.by_area[a]?.security ?? null} />
                  </span>
                ))}
              </MetricCard>
              <MetricCard title="증거 (수동 / 자동 / 미실행)">
                {counts.length === 0 ? <span>집계 없음</span> : <span>{sum("manual")} / {sum("automated")} / {sum("not_run")}</span>}
              </MetricCard>
              <MetricCard title="승인">{r.approval ? <span>{r.approval.decision} · {r.approval.approved_by}</span> : <span>–</span>}</MetricCard>
            </Cards>
            <details className="card card-pad">
              <summary>한계 {r.limitations.length}건</summary>
              <ul className="small" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                {r.limitations.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </details>
            <div>
              <DataTable
                columns={checkColumns(r.mode)}
                rows={r.checks}
                rowKey={(c) => c.check_id}
                onRowClick={(c) => onSelect(c.check_id)}
                selectedKey={selected}
                initialSort={{ id: "check_id", dir: "asc" }}
                storageKey={`checks-${r.mode}`}
                pageSizes={[25, 50, 100]}
              />
              {check && (
                <DetailPanel
                  title={<span className="mono">{check.check_id}</span>}
                  subtitle={d.raw_checks?.[check.check_id] ? String((d.raw_checks[check.check_id] as { question?: string }).question ?? "") : undefined}
                  onClose={() => onSelect(null)}
                  tabs={[
                    {
                      id: "basis",
                      label: "판정 근거",
                      content: (
                        <div className="stack">
                          <KeyValue
                            items={[
                              ["적용 여부", <EnumBadge table={APPLICABILITY} value={check.applicability} />],
                              ["선정", <Value v={check.selected} />],
                              ["미선정 사유", <Value v={check.not_selected_reason ?? null} />],
                              ["비교", <EnumBadge table={COMPARISON} value={check.comparison} />],
                              ["공백", check.gaps.length ? check.gaps.map((g) => label(GAP, g)).join(", ") : <Value v={null} />],
                              ["증거 출처", <Value v={check.evidence_source ? label(EVIDENCE_SOURCE, check.evidence_source) : null} />],
                              ["예외 승인", <Value v={check.exception_approved ?? null} />],
                            ]}
                          />
                          {SIDES.filter((sd) => check.sides[sd]).map((sd) => {
                            const side = check.sides[sd];
                            return (
                              <div key={sd} className="stack" style={{ gap: 6 }}>
                                <h3>{SIDE[sd]}</h3>
                                <KeyValue
                                  items={[
                                    ["보안", <VerdictBadge verdict={side.security} />],
                                    ["기능", <VerdictBadge verdict={side.functional} />],
                                    ["성능", <VerdictBadge verdict={side.performance} />],
                                    ["검증 수준", <OriginBadge origin={side.verification_level ?? null} />],
                                    ["RES 참조", side.result_ref ? <span className="mono">{`${side.result_ref.id} r${side.result_ref.revision}`}</span> : <Value v={null} />],
                                    ["RES 해시", <Short value={side.result_ref?.sha256} n={16} />],
                                  ]}
                                />
                              </div>
                            );
                          })}
                          <h3>원본 RPT</h3>
                          <KeyValue
                            items={[
                              ["RPT", <span className="mono">{`${r.report_ref.id} r${r.report_ref.revision}`}</span>],
                              ["RPT 해시", <Short value={r.report_ref.sha256} n={16} />],
                              ["파일", <Value v={d.raw_path} mono />],
                            ]}
                          />
                        </div>
                      ),
                    },
                    {
                      id: "raw",
                      label: "RPT 원본 행",
                      content: d.raw_checks?.[check.check_id] ? (
                        <JsonView value={d.raw_checks[check.check_id]} />
                      ) : (
                        <StateView kind="empty" detail="예시 모드에는 RPT 원본이 없습니다." />
                      ),
                    },
                  ]}
                />
              )}
            </div>
          </>
        );
      }}
    </Query>
  );
}
