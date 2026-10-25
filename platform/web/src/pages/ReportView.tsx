// update 격리 검증 보고서 화면. 삭제된 update HTML 보고서(guard/report/html.py)의 내용을 모두 옮겼고,
// 모양은 runtime에서 가져온 platform 디자인을 따른다. 판정·집계 값은 서버가 RPT에서 꺼낸 그대로 쓴다.
import { useState, type ReactNode } from "react";
import type { ReportCheck, ReportMark, ReportSide, ReportView as Report } from "../api/types";
import { Badge, EnumBadge, OriginBadge, VerdictBadge } from "../components/Badges";
import { DataTable, type Column } from "../components/DataTable";
import { DetailPanel, KeyValue } from "../components/DetailPanel";
import { JsonView } from "../components/JsonView";
import { Banner, Dot, MetricCard, ui } from "../components/Misc";
import { ChartCard, Donut } from "../components/StackedBars";
import { Short, Time, Value } from "../components/Value";
import {
  APPLICABILITY,
  AREA,
  COMPARISON,
  EVIDENCE_SOURCE,
  EXCEPTION_STATUS,
  GAP,
  MODE_LONG,
  NOT_SELECTED,
  PACK_STATUS,
  POLICY_STATUS,
  UNOFFICIAL_REASON,
  VERDICT,
  label,
} from "../lib/labels";
import p from "./pages.module.css";

const AXES = [
  ["security", "보안"],
  ["functional", "기능"],
  ["performance", "성능"],
] as const;

const VERDICT_COLOR: Record<string, string> = {
  PASS: "var(--accent)",
  FAIL: "var(--error)",
  UNKNOWN: "var(--chart-gray)",
  NOT_APPLICABLE: "var(--chart-gray-2)",
};

/** 표시 항목 한 줄 문구(update 보고서와 같은 문구). */
export function markText(m: ReportMark): string {
  switch (m.kind) {
    case "gap":
      return label(GAP, m.value);
    case "manual_declared":
      return "수동 입력(DECLARED, 자기 신고)";
    case "replaced":
      return `대체됨: 이전 ${label(EVIDENCE_SOURCE, m.source)} 증거(rev ${m.revision ?? "–"})${m.reason ? ` — ${m.reason}` : ""}`;
    case "functional_scope":
      return m.label ?? "기능 확인 범위";
    case "exception":
      return `${label(EXCEPTION_STATUS, m.status)} (만료 ${m.expires_at ?? "–"}, ${m.reason ?? "–"})`;
    case "tested_while_undetermined":
      return "적용 미정 상태로 시험함";
    default:
      return m.kind;
  }
}

function SideChips({ side }: { side: ReportSide | null | undefined }) {
  if (!side) return <Value v={null} />;
  return (
    <span className="row wrap" style={{ gap: 4 }}>
      {AXES.map(([k, name]) =>
        side[k] ? (
          <Badge key={k} tone={VERDICT[side[k] as string]?.tone ?? "muted"} title={side[k] ?? undefined}>
            {name} {VERDICT[side[k] as string]?.label ?? side[k]}
          </Badge>
        ) : null,
      )}
    </span>
  );
}

function Marks({ marks }: { marks: ReportMark[] }) {
  if (marks.length === 0) return null;
  return (
    <ul className="small muted" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
      {marks.map((m, i) => (
        <li key={i}>{markText(m)}</li>
      ))}
    </ul>
  );
}

/** 주요 결과 카드 한 장 (update 보고서의 결과 카드). */
function FindingCard({ c, update, onOpen }: { c: ReportCheck; update: boolean; onOpen: () => void }) {
  return (
    <button type="button" className={`card ${p.finding}`} onClick={onOpen}>
      <span className="row" style={{ gap: 8 }}>
        <Dot state={c.headline === "FAIL" ? "bad" : c.headline === "UNKNOWN" ? "none" : "ok"} />
        <span className="mono">{c.check_id}</span>
        <span className="small muted">{c.purpose}</span>
        {update && c.comparison && (
          <span style={{ marginLeft: "auto" }}>
            <EnumBadge table={COMPARISON} value={c.comparison} />
          </span>
        )}
      </span>
      <span className={p.question}>{c.question}</span>
      {update ? (
        <span className="stack" style={{ gap: 4 }}>
          <span className="row small">
            <span className="muted" style={{ width: 28 }}>기준</span>
            <SideChips side={c.baseline} />
          </span>
          <span className="row small">
            <span className="muted" style={{ width: 28 }}>후보</span>
            <SideChips side={c.candidate} />
          </span>
        </span>
      ) : (
        <SideChips side={c.single} />
      )}
      <Marks marks={c.marks} />
    </button>
  );
}

function FindingGroup({ title, note, ids, byId, update, onOpen }: { title: string; note?: string; ids: string[]; byId: Map<string, ReportCheck>; update: boolean; onOpen: (id: string) => void }) {
  const [all, setAll] = useState(false);
  if (ids.length === 0) return null;
  const shown = all ? ids : ids.slice(0, 6);
  return (
    <section className={p.section}>
      <h3 className="row" style={{ gap: 8 }}>
        {title} {ids.length}건 {note && <span className="small muted" style={{ fontWeight: 400 }}>{note}</span>}
      </h3>
      <div className={p.findings}>
        {shown.map((id) => {
          const c = byId.get(id);
          return c ? <FindingCard key={id} c={c} update={update} onOpen={() => onOpen(id)} /> : null;
        })}
      </div>
      {ids.length > 6 && (
        <button type="button" className="btn btn-small" style={{ alignSelf: "flex-start" }} onClick={() => setAll(!all)}>
          {all ? "접기" : `나머지 ${ids.length - 6}건 더 보기`}
        </button>
      )}
    </section>
  );
}

function CheckDrawer({ c, report, raw, onClose }: { c: ReportCheck; report: Report; raw: unknown; onClose: () => void }) {
  const update = report.mode === "update";
  const sides = (update ? (["baseline", "candidate"] as const) : (["single"] as const)).filter((k) => c[k]);
  return (
    <DetailPanel
      title={<span className="mono">{c.check_id}</span>}
      subtitle={c.purpose}
      onClose={onClose}
      tabs={[
        {
          id: "basis",
          label: "판정 근거",
          content: (
            <div className="stack">
              <p style={{ margin: 0, fontWeight: 600 }}>{c.question}</p>
              <KeyValue
                items={[
                  ["영역", `${c.area ?? "–"} ${c.area ? (AREA[c.area] ?? "") : ""}`],
                  ["적용 여부", <EnumBadge table={APPLICABILITY} value={c.applicability} />],
                  ["선정", <Value v={c.selected} />],
                  ["대표 판정", <VerdictBadge verdict={c.headline} />],
                  ["검토 순서", <Value v={c.review_order} mono />],
                  ["영향", <Value v={c.impact} />],
                  ["증거 출처", <Value v={c.evidence_source ? label(EVIDENCE_SOURCE, c.evidence_source) : null} />],
                  ...(update
                    ? ([
                        ["보안 비교", <EnumBadge table={COMPARISON} value={c.comparison} />],
                        ["기능 비교", <EnumBadge table={COMPARISON} value={c.functional_comparison} />],
                        ["비교 사유", c.comparison_reasons.length ? c.comparison_reasons.join(", ") : <Value v={null} />],
                      ] as [string, ReactNode][])
                    : []),
                ]}
              />
              {sides.map((k) => {
                const s = c[k]!;
                return (
                  <div key={k} className="stack" style={{ gap: 6 }}>
                    <h4 className="small muted" style={{ margin: "8px 0 0", fontWeight: 500 }}>
                      {k === "single" ? "결과" : k === "baseline" ? "기준" : "후보"}
                    </h4>
                    <KeyValue
                      items={[
                        ["보안", <VerdictBadge verdict={s.security} />],
                        ["기능", <VerdictBadge verdict={s.functional} />],
                        ["성능", <VerdictBadge verdict={s.performance} />],
                        ["검증 수준", <OriginBadge origin={s.verification_level} />],
                        ["판정(RES)", s.result_id ? <span className="mono">{`${s.result_id}@${s.result_revision}`}</span> : <Value v={null} />],
                      ]}
                    />
                  </div>
                );
              })}
              {c.marks.length > 0 && (
                <div>
                  <h4 className="small muted" style={{ margin: "8px 0 4px", fontWeight: 500 }}>
                    표시
                  </h4>
                  <Marks marks={c.marks} />
                </div>
              )}
            </div>
          ),
        },
        { id: "raw", label: "RPT 원본 행", content: raw ? <JsonView value={raw} /> : <span className="muted">원본 없음</span> },
      ]}
    />
  );
}

export function FullReport({ report, rawChecks, selected, onSelect }: { report: Report; rawChecks: Record<string, unknown> | null | undefined; selected: string | null; onSelect: (id: string | null) => void }) {
  const update = report.mode === "update";
  const byId = new Map(report.checks.map((c) => [c.check_id, c]));
  const selectedRows = report.checks.filter((c) => c.selected);
  const n = report.counts;
  const total = Math.max(1, n.fail + n.unknown + n.pass + n.not_applicable);
  const current = selected ? byId.get(selected) : undefined;

  // 영역별 대표 판정 분포(표시용 개수 세기. 판정은 다시 계산하지 않는다)
  const areaRows = report.by_area.map((a) => {
    const rows = selectedRows.filter((c) => c.area === a.name);
    const count = (v: string) => rows.filter((c) => c.headline === v).length;
    return { ...a, pass: count("PASS"), fail: count("FAIL"), unknown: count("UNKNOWN") };
  });
  const evidence = Object.entries(report.evidence_counts);
  const sum = (k: string) => evidence.reduce((acc, [, c]) => acc + (c[k] ?? 0), 0);
  const notSelectedByReason = new Map<string, string[]>();
  for (const ns of report.not_selected) notSelectedByReason.set(ns.reason ?? "–", [...(notSelectedByReason.get(ns.reason ?? "–") ?? []), ns.check_id ?? "–"]);

  const columns: Column<ReportCheck>[] = [
    { id: "check_id", header: "점검", cell: (c) => <span className="mono">{c.check_id}</span>, sort: (c) => c.check_id },
    { id: "headline", header: "대표 판정", cell: (c) => <VerdictBadge verdict={c.headline} />, sort: (c) => c.headline },
    { id: "question", header: "질문", cell: (c) => <span title={c.question ?? undefined}>{c.question}</span> },
    { id: "purpose", header: "보호 목적", cell: (c) => <span className="muted">{c.purpose}</span>, sort: (c) => c.purpose, hidden: true },
    { id: "applicability", header: "적용", cell: (c) => <EnumBadge table={APPLICABILITY} value={c.applicability} />, sort: (c) => c.applicability },
    ...(update
      ? ([
          { id: "baseline", header: "기준 보안", cell: (c) => <VerdictBadge verdict={c.baseline?.security} />, sort: (c) => c.baseline?.security },
          { id: "candidate", header: "후보 보안", cell: (c) => <VerdictBadge verdict={c.candidate?.security} />, sort: (c) => c.candidate?.security },
          { id: "comparison", header: "비교", cell: (c) => <EnumBadge table={COMPARISON} value={c.comparison} />, sort: (c) => c.comparison },
        ] as Column<ReportCheck>[])
      : ([
          { id: "security", header: "보안", cell: (c) => <VerdictBadge verdict={c.single?.security} />, sort: (c) => c.single?.security },
          { id: "functional", header: "기능", cell: (c) => <VerdictBadge verdict={c.single?.functional} />, sort: (c) => c.single?.functional },
          { id: "performance", header: "성능", cell: (c) => <VerdictBadge verdict={c.single?.performance} />, sort: (c) => c.single?.performance, hidden: true },
        ] as Column<ReportCheck>[])),
    { id: "source", header: "증거 출처", cell: (c) => <Value v={c.evidence_source ? label(EVIDENCE_SOURCE, c.evidence_source) : null} />, sort: (c) => c.evidence_source },
    { id: "marks", header: "표시", cell: (c) => (c.marks.length ? <span className="small muted">{c.marks.map(markText).join(" · ")}</span> : <Value v={null} />), sort: (c) => c.marks.length },
  ];

  return (
    <>
      {!report.official && (
        <div style={{ position: "sticky", top: 52, zIndex: 3, margin: "0 -24px" }}>
          <Banner kind="unofficial">공식 아님 — {report.unofficial_reasons.map((r) => label(UNOFFICIAL_REASON, r)).join(", ")}</Banner>
        </div>
      )}
      <span className="muted mono">
        {report.report_id}@{report.revision} · {report.created_at} · {label(MODE_LONG, report.mode)} · 자료 출처 {report.data_origin}
      </span>

      <div className={ui.cards}>
        <MetricCard title="종합 보안 판정" dot={report.overall === "FAIL" ? "bad" : report.overall === "PASS" ? "ok" : "none"} value={VERDICT[report.overall]?.label ?? report.overall} caption={`선정 ${n.selected}건`}>
          <div className={ui.barList} style={{ width: "100%" }}>
            {(
              [
                ["FAIL", "미충족", n.fail],
                ["UNKNOWN", "미확인", n.unknown],
                ["PASS", "충족", n.pass],
                ["NOT_APPLICABLE", "해당 없음", n.not_applicable],
              ] as const
            ).map(([k, name, v]) => (
              <div key={k} className={`${ui.barRow} ${k === "FAIL" ? ui.isError : ""}`} style={{ cursor: "default" }}>
                <span className={ui.barLabel}>{name}</span>
                <span className={ui.barTrack}>
                  <span className={ui.barFill} style={{ width: `${(v / total) * 100}%`, background: k === "UNKNOWN" || k === "NOT_APPLICABLE" ? "var(--chart-gray)" : undefined }} />
                </span>
                <span className={ui.barCount}>{v}</span>
              </div>
            ))}
            {report.incomplete_areas.length > 0 && <span className="small muted">점검 목록 미완결: {report.incomplete_areas.join(", ")}</span>}
          </div>
        </MetricCard>

        <MetricCard title="영역별 보안 판정" value={report.by_area.length} caption="영역">
          <table className={ui.miniTable}>
            <thead>
              <tr>
                <th>영역</th>
                <th>보안</th>
                <th className={ui.num}>점검 목록 완결</th>
              </tr>
            </thead>
            <tbody>
              {report.by_area.map((a) => (
                <tr key={a.name}>
                  <td>
                    <span className="mono">{a.name}</span> <span className="muted small">{AREA[a.name] ?? ""}</span>
                  </td>
                  <td>
                    <VerdictBadge verdict={a.security} />
                  </td>
                  <td className={ui.num}>{a.inventory_complete ? "예" : "아니오"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </MetricCard>

        <MetricCard title="증거 출처" value={sum("manual") + sum("automated")} caption={`수동 입력 ${sum("manual")} · 자동 측정 ${sum("automated")} · 미실행 ${sum("not_run")}`}>
          <table className={ui.miniTable}>
            <thead>
              <tr>
                <th>영역</th>
                <th className={ui.num}>수동</th>
                <th className={ui.num}>자동</th>
                <th className={ui.num}>미실행</th>
              </tr>
            </thead>
            <tbody>
              {evidence.length === 0 ? (
                <tr>
                  <td className="muted">집계 없음</td>
                  <td />
                  <td />
                  <td />
                </tr>
              ) : (
                evidence.map(([area, c]) => (
                  <tr key={area}>
                    <td className="mono">{area}</td>
                    <td className={ui.num}>{c.manual}</td>
                    <td className={ui.num}>{c.automated}</td>
                    <td className={ui.num}>{c.not_run}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </MetricCard>
      </div>

      <div className={ui.cardGrid}>
        <MetricCard title="수동 시험 필요" value={n.manual_required} caption="선정됐지만 자동 실행 불가" />
        <MetricCard title="미선정" value={n.not_selected} caption="범위 밖" />
        {update && <MetricCard title="후퇴" dot={n.regressed ? "bad" : "none"} value={n.regressed} caption="기준 충족 → 후보 미충족" />}
        <MetricCard title="승인" dot={report.approval ? "ok" : "none"}>
          {report.approval ? (
            <span>
              {(report.approval as { decision: string }).decision} · {(report.approval as { approved_by: string }).approved_by}
            </span>
          ) : (
            <span className="muted">승인 기록 없음 (자동 판정은 승인이 아니다)</span>
          )}
        </MetricCard>
      </div>

      <div className={p.chartRow}>
        <ChartCard title="영역별 판정 분포" total={n.selected} note="선정 점검의 대표 판정">
          <div className={ui.barList}>
            {areaRows.map((a) => {
              const t = Math.max(1, a.pass + a.fail + a.unknown);
              return (
                <div key={a.name} className={ui.barRow} style={{ cursor: "default", gridTemplateColumns: "96px minmax(0,1fr) 92px" }}>
                  <span className={ui.barLabel}>
                    {a.name} {AREA[a.name] ?? ""}
                  </span>
                  <span className={ui.barTrack} style={{ display: "flex" }}>
                    <span style={{ width: `${(a.pass / t) * 100}%`, background: VERDICT_COLOR.PASS }} title={`충족 ${a.pass}`} />
                    <span style={{ width: `${(a.fail / t) * 100}%`, background: VERDICT_COLOR.FAIL }} title={`미충족 ${a.fail}`} />
                    <span style={{ width: `${(a.unknown / t) * 100}%`, background: VERDICT_COLOR.UNKNOWN }} title={`미확인 ${a.unknown}`} />
                  </span>
                  <span className={ui.barCount}>
                    {a.pass} / {a.fail} / {a.unknown}
                  </span>
                </div>
              );
            })}
            <span className="small muted">충족 / 미충족 / 미확인</span>
          </div>
        </ChartCard>
        <ChartCard title="판정 구성" total={n.selected} note="선정 점검">
          <Donut
            items={[
              { key: "PASS", label: "충족", value: n.pass, color: VERDICT_COLOR.PASS },
              { key: "FAIL", label: "미충족", value: n.fail, color: VERDICT_COLOR.FAIL },
              { key: "UNKNOWN", label: "미확인", value: n.unknown, color: VERDICT_COLOR.UNKNOWN },
              { key: "NOT_APPLICABLE", label: "해당 없음", value: n.not_applicable, color: VERDICT_COLOR.NOT_APPLICABLE },
            ]}
            height={200}
          />
        </ChartCard>
      </div>

      <h2 className={ui.blockTitle}>주요 결과</h2>
      {report.focus.regressed.length + report.focus.fail.length + report.focus.unknown.length === 0 ? (
        <p className="notice">미충족·미확인 항목 없음</p>
      ) : (
        <>
          <FindingGroup title="후퇴" note="(기준 충족 → 후보 미충족)" ids={report.focus.regressed} byId={byId} update={update} onOpen={onSelect} />
          <FindingGroup title="미충족" ids={report.focus.fail} byId={byId} update={update} onOpen={onSelect} />
          <FindingGroup title="미확인" note="(근거 부족, 충족으로 집계하지 않음)" ids={report.focus.unknown} byId={byId} update={update} onOpen={onSelect} />
        </>
      )}

      <h2 className={ui.blockTitle}>보호 목적별</h2>
      <DataTable
        columns={[
          { id: "name", header: "보호 목적", cell: (r) => r.name, sort: (r) => r.name },
          { id: "security", header: "보안", cell: (r) => <VerdictBadge verdict={r.security} />, sort: (r) => r.security },
          { id: "complete", header: "점검 목록 완결", cell: (r) => (r.inventory_complete ? "예" : "아니오"), sort: (r) => r.inventory_complete },
        ]}
        rows={report.by_purpose}
        rowKey={(r) => r.name}
        pageSizes={[10, 25, 50]}
      />

      <article className="card card-pad stack">
        <h3>한계</h3>
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {report.limitations.map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      </article>

      <h2 className={ui.blockTitle}>선정된 점검 전체 ({selectedRows.length}건)</h2>
      <DataTable
        columns={columns}
        rows={selectedRows}
        rowKey={(c) => c.check_id}
        onRowClick={(c) => onSelect(c.check_id)}
        selectedKey={selected}
        initialSort={{ id: "check_id", dir: "asc" }}
        storageKey={`report-${report.mode}`}
        pageSizes={[25, 50, 100]}
      />

      <div className={p.grid2}>
        <details className="card card-pad">
          <summary>수동 시험 필요 ({report.manual_required.length}건)</summary>
          <p className="mono small" style={{ marginBottom: 0 }}>
            {report.manual_required.join(", ") || "없음"}
          </p>
        </details>
        <details className="card card-pad">
          <summary>미선정 ({report.not_selected.length}건)</summary>
          {notSelectedByReason.size === 0 ? (
            <p>없음</p>
          ) : (
            [...notSelectedByReason.entries()].map(([reason, ids]) => (
              <p key={reason} className="small">
                <strong>{label(NOT_SELECTED, reason)}</strong> ({ids.length}건)
                <br />
                <span className="mono">{ids.join(", ")}</span>
              </p>
            ))
          )}
        </details>
      </div>

      <details className="card card-pad">
        <summary>승인·추적 정보</summary>
        <div className="stack" style={{ marginTop: 12 }}>
          <KeyValue
            items={[
              ["보고서", <span className="mono">{`${report.report_id}@${report.revision}`}</span>],
              ["content_sha256", <Short value={report.content_sha256} n={16} />],
              ["작성", <Time iso={report.created_at} />],
              ["팩", <EnumBadge table={PACK_STATUS} value={report.pack_review_status} />],
              ["정책", <EnumBadge table={POLICY_STATUS} value={report.policy_status} />],
              ["자료 등급", <OriginBadge origin={report.data_origin} />],
            ]}
          />
          <DataTable
            columns={[
              { id: "kind", header: "종류", cell: (r) => r.kind, sort: (r) => r.kind },
              { id: "id", header: "ID", cell: (r) => <span className="mono">{r.id}</span> },
              { id: "rev", header: "revision", cell: (r) => r.revision },
              { id: "sha", header: "sha256", cell: (r) => <Short value={r.sha256} n={16} /> },
            ]}
            rows={report.inputs_manifest}
            rowKey={(r) => `${r.kind}:${r.id}:${r.revision}`}
            pageSizes={[10, 50, 200]}
          />
        </div>
      </details>

      {current && <CheckDrawer c={current} report={report} raw={rawChecks?.[current.check_id]} onClose={() => onSelect(null)} />}
    </>
  );
}
