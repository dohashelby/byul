import { useMemo, useState } from "react";
import { useRequests, useRuntimeStatus } from "../api/hooks";
import type { RequestRow } from "../api/types";
import { Badge, EnumBadge, OriginBadge } from "../components/Badges";
import { DataTable, type Column } from "../components/DataTable";
import { FilterBar, FilterSelect, InvalidRows, PageHead, Query, Segmented } from "../components/Misc";
import { Time, Value } from "../components/Value";
import { REQUEST_RESULT, label } from "../lib/labels";
import { REQUEST_SERIES } from "../components/StackedBars";
import { RequestDrawer } from "./runtime/RequestDrawer";
import { ScopeBadges } from "./runtime/parts";
import { matchesTarget, uniq, useEnvTarget } from "../lib/filters";
import { inRange } from "../lib/time";
import { useGlobalFilters, useParams } from "../lib/url";

const FILTER_KEYS = ["q", "outcome", "model", "reason", "stream", "run"] as const;

type Saved = { name: string; query: string };

function loadSaved(): Saved[] {
  try {
    return JSON.parse(localStorage.getItem("saved-filters:requests") ?? "[]") as Saved[];
  } catch {
    return [];
  }
}

function columns(sessionId: string | null | undefined): Column<RequestRow>[] {
  return [
    // runtime 대시보드 요청 목록과 같은 열·순서
    { id: "recorded_at", header: "시각", cell: (r) => <Time iso={r.recorded_at} />, sort: (r) => r.recorded_at },
    {
      id: "request_id",
      header: "요청 ID",
      cell: (r) => (
        <span className="mono" title={r.request_id}>
          {r.request_id.slice(0, 8)}
          {sessionId && r.observation_session_id && r.observation_session_id !== sessionId && (
            <span style={{ marginLeft: 6 }}>
              <Badge tone="muted">이전 실행</Badge>
            </span>
          )}
        </span>
      ),
      sort: (r) => r.request_id,
    },
    { id: "scope", header: "환경·실행", cell: (r) => <ScopeBadges environment={r.environment} runId={r.run_id} />, sort: (r) => r.environment },
    {
      id: "model",
      header: "모델",
      cell: (r) => (
        <>
          <Value v={r.model} />
          {r.stream && (
            <span style={{ marginLeft: 6 }}>
              <Badge tone="muted">stream</Badge>
            </span>
          )}
        </>
      ),
      sort: (r) => r.model,
    },
    { id: "max_tokens", header: "max_tokens", cell: (r) => <Value v={r.max_tokens} />, sort: (r) => r.max_tokens },
    { id: "outcome", header: "결과", cell: (r) => <EnumBadge table={REQUEST_RESULT} value={r.outcome} />, sort: (r) => r.outcome },
    { id: "reason_code", header: "사유", cell: (r) => <span className="mono muted">{r.reason_code ?? "–"}</span>, sort: (r) => r.reason_code },
    // platform 추가 열(기본 숨김)
    { id: "http_status", header: "클라이언트 HTTP", cell: (r) => <Value v={r.http_status} />, sort: (r) => r.http_status, hidden: true },
    { id: "engine_http_status", header: "엔진 HTTP", cell: (r) => <Value v={r.engine_http_status} />, sort: (r) => r.engine_http_status, hidden: true },
    { id: "delivery_attempted", header: "전달 시도", cell: (r) => <Value v={r.delivery_attempted} />, sort: (r) => r.delivery_attempted, hidden: true },
    { id: "backend_kind", header: "백엔드", cell: (r) => <Value v={r.backend_kind} />, sort: (r) => r.backend_kind, hidden: true },
    { id: "data_origin", header: "자료 등급", cell: (r) => <OriginBadge origin={r.data_origin} detail={r.origin_detail} />, sort: (r) => r.data_origin, hidden: true },
  ];
}

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
  const status = useRuntimeStatus();
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
                columns={columns(status.data?.item?.observation_session_id)}
                rows={rows}
                rowKey={(r) => r.request_id}
                onRowClick={(r) => patch({ sel: r.request_id })}
                selectedKey={selected}
                initialSort={{ id: "recorded_at", dir: "desc" }}
                storageKey="requests"
                empty={<p className="muted" style={{ margin: 0, padding: "24px 0", textAlign: "center" }}>{filters.outcome ? `${REQUEST_RESULT[filters.outcome]?.label ?? filters.outcome} 요청이 없습니다.` : "아직 기록된 요청이 없습니다."}</p>}
              />
              {selected && <RequestDrawer id={selected} onClose={() => patch({ sel: null })} />}
            </div>
            <InvalidRows items={data.invalid} what="요청 기록" />
            <p className="small muted">지연시간·토큰 수·비용은 runtime이 기록하지 않아 열이 없습니다.</p>
          </>
        )}
      </Query>
    </div>
  );
}

