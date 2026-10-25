import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useResults, useRuntimeEvents } from "../api/hooks";
import type { RuntimeEvent } from "../api/types";
import { OriginBadge, OutcomeBadge } from "../components/Badges";
import { DataTable, type Column } from "../components/DataTable";
import { DetailPanel, KeyValue } from "../components/DetailPanel";
import { JsonView } from "../components/JsonView";
import { InvalidRows, PageHead, Query, StateView } from "../components/Misc";
import { ChartCard, EVENT_SERIES, TimeBars, eventKey, type EventKey } from "../components/StackedBars";
import { Short, Time, Value } from "../components/Value";
import { CATEGORY, OUTCOME, PHASE, SOURCE, label } from "../lib/labels";
import { countBy, matchesTarget, useEnvTarget } from "../lib/filters";
import { bucketize, inRange } from "../lib/time";
import { carry, useGlobalFilters, useParams } from "../lib/url";
import p from "./pages.module.css";

const FACETS = [
  { key: "source", title: "출처", get: (e: RuntimeEvent) => e.source, table: SOURCE },
  { key: "category", title: "분류", get: (e: RuntimeEvent) => e.category, table: CATEGORY },
  { key: "outcome", title: "결과", get: (e: RuntimeEvent) => e.outcome, table: Object.fromEntries(Object.entries(OUTCOME).map(([k, v]) => [k, v.label])) },
  { key: "rule", title: "규칙", get: (e: RuntimeEvent) => e.rule_ref, table: {} as Record<string, string> },
] as const;

function target(e: RuntimeEvent): string | null {
  const r = e.native.record as Record<string, unknown>;
  const pick = (k: string) => (typeof r[k] === "string" ? (r[k] as string) : null);
  if (e.source === "gateway") return e.scope.request_id;
  if (e.source === "hubble") {
    const port = typeof r.dst_port === "number" ? `:${r.dst_port}` : "";
    return [pick("src_pod"), "→", pick("dst_pod") ?? pick("dst_ip") ?? "", port].filter(Boolean).join(" ") || null;
  }
  return pick("binary") ?? pick("path") ?? pick("pod");
}

export function eventMatches(e: RuntimeEvent, f: Record<string, string | null>): boolean {
  return (
    (!f.source || e.source === f.source) &&
    (!f.category || e.category === f.category) &&
    (!f.outcome || e.outcome === f.outcome) &&
    (!f.rule || e.rule_ref === f.rule) &&
    (!f.run || e.scope.run_id === f.run) &&
    (!f.request || e.scope.request_id === f.request) &&
    (!f.policy || e.policy_ref?.sha256 === f.policy)
  );
}

const columns: Column<RuntimeEvent>[] = [
  { id: "occurred_at", header: "발생 시각", cell: (e) => <Time iso={e.occurred_at} />, sort: (e) => e.occurred_at },
  { id: "source", header: "출처", cell: (e) => label(SOURCE, e.source), sort: (e) => e.source },
  { id: "category", header: "분류", cell: (e) => label(CATEGORY, e.category), sort: (e) => e.category },
  { id: "outcome", header: "결과", cell: (e) => <OutcomeBadge outcome={e.outcome} />, sort: (e) => e.outcome },
  { id: "rule_ref", header: "규칙", cell: (e) => <Value v={e.rule_ref} mono />, sort: (e) => e.rule_ref },
  { id: "phase", header: "단계", cell: (e) => <Value v={e.phase ? label(PHASE, e.phase) : null} />, sort: (e) => e.phase },
  { id: "target", header: "대상", cell: (e) => <Value v={target(e)} mono />, sort: (e) => target(e) },
  { id: "data_origin", header: "자료 등급", cell: (e) => <OriginBadge origin={e.data_origin} detail={e.origin_detail} />, sort: (e) => e.data_origin },
  { id: "recorded_at", header: "기록 시각", cell: (e) => <Time iso={e.recorded_at} />, sort: (e) => e.recorded_at, hidden: true },
];

const FILTER_KEYS = ["source", "category", "outcome", "rule", "run", "request", "policy"] as const;

export function EventsPage() {
  const q = useRuntimeEvents();
  const [params, patch] = useParams();
  const { range } = useGlobalFilters();
  const { env, targetRef } = useEnvTarget();
  const selected = params.get("sel");
  const filters = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k)]));

  const inScope = useMemo(
    () => (q.data?.items ?? []).filter((e) => inRange(e.recorded_at, range) && matchesTarget(e.target_ref, env, targetRef)),
    [q.data, range, env, targetRef],
  );
  const rows = inScope.filter((e) => eventMatches(e, filters));
  const chart = bucketize(rows, (e) => e.recorded_at, (e) => eventKey(e.outcome), EVENT_SERIES.map((x) => x.key), range);
  const current = (q.data?.items ?? []).find((e) => e.event_id === selected) ?? null;

  return (
    <div className="page">
      <Query q={q}>
        {(data) => (
          <>
            <PageHead title="runtime 사건" />
            <ChartCard
              title="시간별 사건"
              total={rows.length}
              note={`사건${chart.untimed ? ` · 시각 없는 ${chart.untimed}건 제외` : ""} · 구간을 드래그하면 필터`}
            >
              <TimeBars<EventKey>
                buckets={chart.buckets}
                size={chart.size}
                series={[...EVENT_SERIES]}
                height={150}
                onSelectRange={(from, to) => patch({ period: "custom", from: new Date(from).toISOString(), to: new Date(to).toISOString() })}
              />
            </ChartCard>
            <div className={`${p.three} ${selected ? "" : p.noPanel}`}>
              <nav className="card" aria-label="사건 필터">
                <div className={p.facets}>
                  {FACETS.map((f) => {
                    const counts = countBy(inScope, f.get);
                    return (
                      <div key={f.key}>
                        <div className={p.facetTitle}>{f.title}</div>
                        {[...counts.entries()]
                          .sort((a, b) => b[1] - a[1])
                          .map(([v, n]) => {
                            const on = filters[f.key] === v;
                            return (
                              <button key={v} type="button" className={`${p.facetItem} ${on ? p.facetOn : ""}`} onClick={() => patch({ [f.key]: on ? null : v })} aria-pressed={on}>
                                <span>{v === "∅" ? "–" : (f.table as Record<string, string>)[v] ?? v}</span>
                                <span className={p.count}>{n}</span>
                              </button>
                            );
                          })}
                      </div>
                    );
                  })}
                  {(["run", "request", "policy"] as const).map((k) =>
                    filters[k] ? (
                      <div key={k}>
                        <div className={p.facetTitle}>{k === "run" ? "run_id" : k === "request" ? "request_id" : "정책 해시"}</div>
                        <button type="button" className={`${p.facetItem} ${p.facetOn}`} onClick={() => patch({ [k]: null })}>
                          <Short value={filters[k]} n={14} />
                          <span>✕</span>
                        </button>
                      </div>
                    ) : null,
                  )}
                </div>
              </nav>
              <DataTable
                columns={columns}
                rows={rows}
                rowKey={(e) => e.event_id}
                onRowClick={(e) => patch({ sel: e.event_id })}
                selectedKey={selected}
                initialSort={{ id: "occurred_at", dir: "desc" }}
                storageKey="events"
                pageSizes={[50, 100, 200]}
                empty={<StateView kind="empty" />}
              />
              {selected &&
                (current ? (
                  <EventPanel event={current} all={data.items} params={params} onClose={() => patch({ sel: null })} />
                ) : (
                  <div className="card">
                    <StateView kind="not_found" detail={selected} />
                  </div>
                ))}
            </div>
            <InvalidRows items={data.invalid} what="runtime 사건" />
          </>
        )}
      </Query>
    </div>
  );
}

function EventPanel({ event: e, all, params, onClose }: { event: RuntimeEvent; all: RuntimeEvent[]; params: URLSearchParams; onClose: () => void }) {
  const results = useResults();
  const sameRequest = e.scope.request_id ? all.filter((x) => x.scope.request_id === e.scope.request_id && x.event_id !== e.event_id) : [];
  const sameRun = e.scope.run_id ? all.filter((x) => x.scope.run_id === e.scope.run_id && x.event_id !== e.event_id) : [];
  const samePolicy = e.policy_ref ? all.filter((x) => x.policy_ref?.sha256 === e.policy_ref!.sha256 && x.event_id !== e.event_id) : [];
  const policyResults = e.policy_ref
    ? (results.data?.items ?? []).filter(
        (r) => r.byul_policy_ref?.policy_id === e.policy_ref!.policy_id && r.byul_policy_ref?.policy_version === e.policy_ref!.policy_version,
      )
    : [];
  const runResults = e.scope.run_id ? (results.data?.items ?? []).filter((r) => r.plan_ref.id === e.scope.run_id) : [];

  const link = (path: string, extra: Record<string, string>) => `${path}${carry(params, extra)}`;

  return (
    <DetailPanel
      inline
      title={<OutcomeBadge outcome={e.outcome} />}
      subtitle={<span className="mono">{e.event_id}</span>}
      onClose={onClose}
      tabs={[
        {
          id: "overview",
          label: "개요",
          content: (
            <KeyValue
              items={[
                ["발생 시각", <Time iso={e.occurred_at} />],
                ["기록 시각", <Time iso={e.recorded_at} />],
                ["출처", label(SOURCE, e.source)],
                ["분류", label(CATEGORY, e.category)],
                ["결과", <OutcomeBadge outcome={e.outcome} />],
                ["집행 주체", <Value v={e.enforcer} />],
                ["규칙", <Value v={e.rule_ref} mono />],
                ["단계", <Value v={e.phase ? label(PHASE, e.phase) : null} />],
                ["대상", <Value v={e.target_ref} mono />],
                ["대상 인스턴스", <Value v={e.target_instance_id} mono />],
                [
                  "정책 판본",
                  e.policy_ref ? (
                    <span className="mono">
                      {e.policy_ref.policy_id} v{e.policy_ref.policy_version} <Short value={e.policy_ref.sha256} n={12} />
                    </span>
                  ) : (
                    <Value v={null} />
                  ),
                ],
                ["환경", <Value v={e.scope.environment} />],
                ["run_id", <Value v={e.scope.run_id} mono />],
                ["관측 세션", <Value v={e.scope.observation_session_id} mono />],
                ["request_id", <Value v={e.scope.request_id} mono />],
                ["자료 등급", <OriginBadge origin={e.data_origin} detail={e.origin_detail} />],
                ["원래 출처 값", <Value v={e.origin_detail} mono />],
                ["관련 점검", e.related_checks?.length ? e.related_checks.join(", ") : <Value v={null} />],
              ]}
            />
          ),
        },
        {
          id: "raw",
          label: "원본",
          content: (
            <div className="stack">
              <div className="small muted">native.schema_version: {e.native.schema_version ?? "–"}</div>
              <JsonView value={e.native.record} />
            </div>
          ),
        },
        {
          id: "links",
          label: "연결",
          content: (
            <div className="stack">
              <LinkGroup
                title="같은 request_id"
                items={[
                  ...(e.scope.request_id ? [{ to: link("/runtime/requests", { sel: e.scope.request_id }), text: `요청 내역 ${e.scope.request_id.slice(0, 12)}…` }] : []),
                  ...sameRequest.map((x) => ({ to: link("/runtime/events", { sel: x.event_id }), text: `${label(SOURCE, x.source)} · ${x.event_id.slice(0, 20)}…` })),
                ]}
              />
              <LinkGroup
                title="같은 run_id"
                items={[
                  ...(e.scope.run_id ? [{ to: link("/runtime/events", { run: e.scope.run_id }), text: `사건 ${sameRun.length + 1}건 모두 보기` }] : []),
                  ...runResults.map((r) => ({ to: link("/update/results", { report: r.report_ref.id }), text: `검증 결과 ${r.report_ref.id}` })),
                ]}
              />
              <LinkGroup
                title="같은 policy_ref"
                items={[
                  ...(e.policy_ref ? [{ to: link("/runtime/events", { policy: e.policy_ref.sha256 }), text: `같은 정책 해시 사건 ${samePolicy.length + 1}건` }] : []),
                  ...policyResults.map((r) => ({ to: link("/update/results", { report: r.report_ref.id }), text: `검증 결과 ${r.report_ref.id} (정책 ID·판본 일치)` })),
                ]}
              />
              <p className="small muted">시간이 가깝다는 것만으로는 연결하지 않습니다. 연결 키가 같은 기록만 보여줍니다.</p>
            </div>
          ),
        },
      ]}
    />
  );
}

function LinkGroup({ title, items }: { title: string; items: { to: string; text: string }[] }) {
  return (
    <div>
      <h3 style={{ marginBottom: 4 }}>{title}</h3>
      {items.length === 0 ? (
        <span className="small muted">연결된 기록 없음</span>
      ) : (
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
          {items.map((i) => (
            <li key={i.to}>
              <Link to={i.to}>{i.text}</Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

