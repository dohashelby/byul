import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useRequestDetail, useRequests } from "../api/hooks";
import type { RequestRow } from "../api/types";
import { OriginBadge, OutcomeBadge } from "../components/Badges";
import { DataTable, type Column } from "../components/DataTable";
import { DetailPanel, KeyValue } from "../components/DetailPanel";
import { JsonView } from "../components/JsonView";
import { FilterBar, FilterSelect, InvalidRows, PageHead, Query, Segmented, StateView } from "../components/Misc";
import { Short, Time, Value } from "../components/Value";
import { DECISION_RESULT, label } from "../lib/labels";
import { REQUEST_SERIES } from "../components/StackedBars";
import { matchesTarget, uniq, useEnvTarget } from "../lib/filters";
import { inRange } from "../lib/time";
import { carry, useGlobalFilters, useParams } from "../lib/url";

const FILTER_KEYS = ["q", "outcome", "model", "reason", "stream", "run"] as const;

type Saved = { name: string; query: string };

function loadSaved(): Saved[] {
  try {
    return JSON.parse(localStorage.getItem("saved-filters:requests") ?? "[]") as Saved[];
  } catch {
    return [];
  }
}

const columns: Column<RequestRow>[] = [
  { id: "recorded_at", header: "기록 시각", cell: (r) => <Time iso={r.recorded_at} />, sort: (r) => r.recorded_at },
  { id: "outcome", header: "결과", cell: (r) => <OutcomeBadge outcome={r.outcome} />, sort: (r) => r.outcome },
  { id: "model", header: "모델", cell: (r) => <Value v={r.model} />, sort: (r) => r.model },
  { id: "max_tokens", header: "max_tokens", cell: (r) => <Value v={r.max_tokens} />, sort: (r) => r.max_tokens },
  { id: "reason_code", header: "사유 코드", cell: (r) => <Value v={r.reason_code} mono />, sort: (r) => r.reason_code },
  { id: "http_status", header: "클라이언트 HTTP", cell: (r) => <Value v={r.http_status} />, sort: (r) => r.http_status },
  { id: "engine_http_status", header: "엔진 HTTP", cell: (r) => <Value v={r.engine_http_status} />, sort: (r) => r.engine_http_status },
  { id: "stream", header: "스트림", cell: (r) => <Value v={r.stream} />, sort: (r) => r.stream },
  { id: "delivery_attempted", header: "전달 시도", cell: (r) => <Value v={r.delivery_attempted} />, sort: (r) => r.delivery_attempted },
  { id: "backend_kind", header: "백엔드", cell: (r) => <Value v={r.backend_kind} />, sort: (r) => r.backend_kind },
  { id: "environment", header: "환경", cell: (r) => <Value v={r.environment} />, sort: (r) => r.environment },
  { id: "run_id", header: "run_id", cell: (r) => <Short value={r.run_id} n={14} />, sort: (r) => r.run_id },
  { id: "data_origin", header: "자료 등급", cell: (r) => <OriginBadge origin={r.data_origin} detail={r.origin_detail} />, sort: (r) => r.data_origin },
  { id: "request_id", header: "request_id", cell: (r) => <Short value={r.request_id} />, sort: (r) => r.request_id, hidden: true },
];

export function filterRequests(rows: RequestRow[], f: Record<string, string | null>): RequestRow[] {
  const q = f.q?.trim().toLowerCase();
  return rows.filter(
    (r) =>
      (!q || r.request_id.includes(q) || (r.model ?? "").toLowerCase().includes(q)) &&
      (!f.outcome || r.outcome === f.outcome) &&
      (!f.model || r.model === f.model) &&
      (!f.reason || r.reason_code === f.reason) &&
      (!f.stream || String(r.stream) === f.stream) &&
      (!f.run || r.run_id === f.run),
  );
}

export function RequestsPage() {
  const q = useRequests();
  const [params, patch] = useParams();
  const { range } = useGlobalFilters();
  const { env, targetRef } = useEnvTarget();
  const selected = params.get("sel");
  const [saved, setSaved] = useState<Saved[]>(loadSaved);
  const filters = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k)]));

  const all = useMemo(
    () => (q.data?.items ?? []).filter((r) => inRange(r.recorded_at, range) && matchesTarget(r.target_ref, env, targetRef)),
    [q.data, range, env, targetRef],
  );
  const rows = filterRequests(all, filters);

  const persist = (next: Saved[]) => {
    setSaved(next);
    try {
      localStorage.setItem("saved-filters:requests", JSON.stringify(next));
    } catch {
      /* 저장 못 해도 이번 화면에는 남는다 */
    }
  };
  const saveCurrent = () => {
    const query = new URLSearchParams(FILTER_KEYS.flatMap((k) => (filters[k] ? [[k, filters[k]!]] : []))).toString();
    if (!query) return;
    const name = window.prompt("저장할 필터 이름", query);
    if (name) persist([...saved.filter((s) => s.name !== name), { name, query }]);
  };
  const applySaved = (query: string) => {
    const sp = new URLSearchParams(query);
    patch(Object.fromEntries(FILTER_KEYS.map((k) => [k, sp.get(k)])));
  };

  const opt = (vals: string[], tbl?: Record<string, string>) => vals.map((v) => ({ value: v, label: tbl ? label(tbl, v) : v }));

  return (
    <div className="page">
      <Query q={q}>
        {(data) => (
          <>
            <PageHead title="요청 목록">
              <Segmented value={filters.outcome} onChange={(v) => patch({ outcome: v })} options={REQUEST_SERIES.map((x) => ({ value: x.key, label: x.label }))} />
            </PageHead>
            <FilterBar>
              <input
                className="input"
                style={{ width: 240, height: 26 }}
                placeholder="request_id 또는 모델 검색"
                value={filters.q ?? ""}
                onChange={(e) => patch({ q: e.target.value || null })}
                aria-label="검색"
              />
              <FilterSelect label="모델" value={filters.model} onChange={(v) => patch({ model: v })} options={opt(uniq(all.map((r) => r.model)))} />
              <FilterSelect label="사유 코드" value={filters.reason} onChange={(v) => patch({ reason: v })} options={opt(uniq(all.map((r) => r.reason_code)))} />
              <FilterSelect label="스트림" value={filters.stream} onChange={(v) => patch({ stream: v })} options={[{ value: "true", label: "예" }, { value: "false", label: "아니오" }]} />
              <FilterSelect label="run_id" value={filters.run} onChange={(v) => patch({ run: v })} options={opt(uniq(all.map((r) => r.run_id)))} />
              <button className="btn btn-small" type="button" onClick={saveCurrent}>
                필터 저장
              </button>
              {saved.length > 0 && (
                <select className="select" value="" onChange={(e) => e.target.value && applySaved(e.target.value)} aria-label="저장된 필터">
                  <option value="">저장된 필터</option>
                  {saved.map((s) => (
                    <option key={s.name} value={s.query}>
                      {s.name}
                    </option>
                  ))}
                </select>
              )}
              {FILTER_KEYS.some((k) => filters[k]) && (
                <button className="btn btn-small btn-ghost" type="button" onClick={() => patch(Object.fromEntries(FILTER_KEYS.map((k) => [k, null])))}>
                  필터 지우기
                </button>
              )}
            </FilterBar>
            {data.truncated && <div className="small muted">runtime에서 최근 요청만 가져왔습니다(상한 200건).</div>}
            <div>
              <DataTable
                columns={columns}
                rows={rows}
                rowKey={(r) => r.request_id}
                onRowClick={(r) => patch({ sel: r.request_id })}
                selectedKey={selected}
                initialSort={{ id: "recorded_at", dir: "desc" }}
                storageKey="requests"
                empty={<StateView kind="empty" />}
              />
              {selected && <RequestPanel id={selected} onClose={() => patch({ sel: null })} params={params} />}
            </div>
            <InvalidRows items={data.invalid} what="요청 기록" />
            <p className="small muted">지연시간·토큰 수·비용은 runtime이 기록하지 않아 열이 없습니다.</p>
          </>
        )}
      </Query>
    </div>
  );
}

function RequestPanel({ id, onClose, params }: { id: string; onClose: () => void; params: URLSearchParams }) {
  const q = useRequestDetail(id);
  return (
    <Query q={q}>
      {(d) => {
        const r = d.row;
        const e = d.event;
        const evidence = d.evidence ?? [];
        return (
          <DetailPanel
            title={<span className="mono">{id}</span>}
            subtitle={r ? label(DECISION_RESULT, r.decision_result) : undefined}
            onClose={onClose}
            tabs={[
              {
                id: "overview",
                label: "개요",
                content: r ? (
                  <div className="stack">
                    <KeyValue
                      items={[
                        ["결과", <OutcomeBadge outcome={r.outcome} />],
                        ["runtime 판정", <Value v={r.decision_result} mono />],
                        ["기록 시각", <Time iso={r.recorded_at} />],
                        ["모델", <Value v={r.model} />],
                        ["max_tokens", <Value v={r.max_tokens} />],
                        ["사유 코드", <Value v={r.reason_code} mono />],
                        ["규칙", <Value v={r.rule_id} mono />],
                        ["클라이언트 HTTP", <Value v={r.http_status} />],
                        ["엔진 HTTP", <Value v={r.engine_http_status} />],
                        ["스트림", <Value v={r.stream} />],
                        ["전달 시도", <Value v={r.delivery_attempted} />],
                        ["백엔드", <Value v={r.backend_kind} />],
                        ["환경", <Value v={r.environment} />],
                        ["run_id", <Value v={r.run_id} mono />],
                        ["대상", <Value v={r.target_ref} mono />],
                        ["정책", e?.policy_ref ? <span className="mono">{`${e.policy_ref.policy_id} v${e.policy_ref.policy_version}`}</span> : <Value v={null} />],
                        ["자료 등급", <OriginBadge origin={r.data_origin} detail={r.origin_detail} />],
                        ["증거 상태", <Value v={d.evidence_state} />],
                        ["증거 요약", <Value v={d.evidence_summary} />],
                      ]}
                    />
                    <div className="row wrap small">
                      <Link to={`/runtime/events${carry(params, { sel: `req:${id}` })}`}>사건 로그에서 보기</Link>
                      {r.run_id && <Link to={`/runtime/events${carry(params, { run: r.run_id })}`}>같은 run_id 사건</Link>}
                    </div>
                  </div>
                ) : (
                  <StateView kind="invalid" detail={d.event_invalid?.errors.join(" / ")} />
                ),
              },
              {
                id: "raw",
                label: "원본",
                content: <JsonView value={d.native_detail ?? e?.native ?? d.event_invalid?.raw ?? null} />,
              },
              {
                id: "evidence",
                label: `증거 ${evidence.length}`,
                content:
                  evidence.length === 0 ? (
                    <StateView kind="empty" detail="이 요청을 가리키는 증거 목록 항목이 없습니다." />
                  ) : (
                    <div className="stack">
                      {evidence.map((ev) => (
                        <KeyValue
                          key={ev.evidence_id}
                          items={[
                            ["증거 ID", <span className="mono">{ev.evidence_id}</span>],
                            ["경로", <span className="mono">{ev.path}</span>],
                            ["SHA-256", <Short value={ev.sha256} n={16} />],
                            ["수집", <Time iso={ev.collected_at} />],
                            ["자료 등급", <OriginBadge origin={ev.data_origin} />],
                          ]}
                        />
                      ))}
                    </div>
                  ),
              },
            ]}
          />
        );
      }}
    </Query>
  );
}
