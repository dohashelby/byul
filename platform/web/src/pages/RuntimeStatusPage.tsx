import { useComparisons, useRuntimeStatus } from "../api/hooks";
import type { ComparisonTable } from "../api/types";
import { EnumBadge } from "../components/Badges";
import { DataTable } from "../components/DataTable";
import { KeyValue } from "../components/DetailPanel";
import { InvalidRows, PageHead, Query, StateView } from "../components/Misc";
import { AnyValue, Short, Time, Value } from "../components/Value";
import { APPROVAL_STATE, COLLECTOR_STATE, COMPARE_STATE, REGISTRATION, STORE_STATE, label } from "../lib/labels";
import p from "./pages.module.css";

const ALERT_STATES = new Set(["stale", "error", "disconnected"]);

function Comparison({ title, table }: { title: string; table: ComparisonTable }) {
  return (
    <section className={p.section}>
      <div className="row">
        <h2>{title}</h2>
        <span className="small muted">상태: {table.state ?? "–"}</span>
      </div>
      <DataTable
        columns={[
          { id: "field", header: "필드", cell: (r) => <span className="mono">{r.field}</span>, sort: (r) => r.field },
          { id: "expected", header: "기대값", cell: (r) => <AnyValue v={r.expected} /> },
          { id: "observed", header: "관측값", cell: (r) => <AnyValue v={r.observed} /> },
          { id: "state", header: "상태", cell: (r) => <EnumBadge table={COMPARE_STATE} value={r.state} />, sort: (r) => r.state },
          { id: "source", header: "출처", cell: (r) => <Value v={r.source} /> },
        ]}
        rows={table.rows}
        rowKey={(r) => r.field}
        empty={<StateView kind="empty" detail={table.state === "not_configured" ? "runtime에 비교 기준이 설정되지 않았습니다." : "비교 행이 없습니다."} />}
      />
      {(table.notes ?? []).map((n) => (
        <div key={n} className="small muted">
          · {n}
        </div>
      ))}
    </section>
  );
}

export function RuntimeStatusPage() {
  const status = useRuntimeStatus();
  const comps = useComparisons();
  return (
    <div className="page">
      <PageHead title="Runtime 상태" />
      <Query q={status}>
        {(res) =>
          !res.item ? (
            <>
              <div className="card">
                <StateView kind="invalid" />
              </div>
              <InvalidRows items={res.invalid ? [res.invalid] : []} what="runtime 상태" />
            </>
          ) : (
            <div className={p.grid2}>
              <section className="card card-pad stack">
                <h2>대상</h2>
                <KeyValue
                  items={[
                    ["target_ref", <Value v={res.item.target.target_ref} mono />],
                    ["등록 상태", label(REGISTRATION, res.item.target.registration)],
                    ["인스턴스 ID", <Value v={res.item.target.instance_id} mono />],
                    ["인스턴스 상태", label(REGISTRATION, res.item.target.instance_state)],
                    ["관측 세션", <Value v={res.item.observation_session_id} mono />],
                    ["상태 생성 시각", <Time iso={res.item.generated_at} />],
                  ]}
                />
              </section>
              <section className="card card-pad stack">
                <h2>정책</h2>
                <KeyValue
                  items={[
                    ["정책", res.item.policy.policy_ref ? <span className="mono">{`${res.item.policy.policy_ref.policy_id} v${res.item.policy.policy_ref.policy_version}`}</span> : <Value v={null} />],
                    ["파일 SHA-256", <Short value={res.item.policy.policy_ref?.sha256} n={16} />],
                    ["로드", <Value v={res.item.policy.loaded} />],
                    ["Gateway 활성", <Value v={res.item.policy.gateway_active} />],
                    ["승인 상태", <EnumBadge table={APPROVAL_STATE} value={res.item.policy.approval_state} />],
                    ["유효 기간 끝", <Time iso={res.item.policy.valid_until} />],
                    ["번들 ID", <Value v={res.item.policy.bundle_id} mono />],
                  ]}
                />
              </section>
              <section className="card card-pad stack">
                <h2>수집기</h2>
                <DataTable
                  columns={[
                    { id: "source", header: "출처", cell: (c) => <span className="mono">{c.source}</span>, sort: (c) => c.source },
                    {
                      id: "state",
                      header: "상태",
                      cell: (c) => (
                        <span style={ALERT_STATES.has(c.state) ? { fontWeight: 700 } : undefined}>
                          <EnumBadge table={COLLECTOR_STATE} value={c.state} />
                          {ALERT_STATES.has(c.state) ? " ⚠" : ""}
                        </span>
                      ),
                      sort: (c) => (ALERT_STATES.has(c.state) ? 0 : 1),
                    },
                    { id: "last", header: "마지막 성공", cell: (c) => <Time iso={c.last_success_at} />, sort: (c) => c.last_success_at },
                  ]}
                  rows={res.item.collectors}
                  rowKey={(c) => c.source}
                  initialSort={{ id: "state", dir: "asc" }}
                />
              </section>
              <section className="card card-pad stack">
                <h2>저장소</h2>
                <KeyValue items={[["기록 저장소", <EnumBadge table={STORE_STATE} value={res.item.store.state} />]]} />
              </section>
            </div>
          )
        }
      </Query>
      <Query q={comps}>
        {(c) => (
          <div className={p.grid2}>
            <Comparison title="구성 비교 (baseline)" table={c.baseline} />
            <Comparison title="대상 비교 (target)" table={c.target} />
          </div>
        )}
      </Query>
    </div>
  );
}
