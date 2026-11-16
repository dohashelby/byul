import { useRuntimeEvents } from "../../api/hooks";
import { Badge, OriginBadge } from "../../components/Badges";
import { DetailPanel, KeyValue } from "../../components/DetailPanel";
import { JsonView } from "../../components/JsonView";
import { FilterBar, FilterSelect, Query } from "../../components/Misc";
import { Pagination, usePagination } from "../../components/Pagination";
import { inRange } from "../../lib/time";
import { matchesTarget, useEnvTarget } from "../../lib/filters";
import { useGlobalFilters, useParams } from "../../lib/url";
import { ObservationOriginBadge, ScopeBadges, TableCard, formatTime, rt } from "./parts";

const pod = (namespace: string | null, name: string | null) => name ? namespace ? `${namespace}/${name}` : name : "확인 안 됨";
const verdictLabel = (value: string | null) => value?.toLowerCase() === "dropped" ? "차단" : value?.toLowerCase() === "forwarded" ? "통과" : value ?? "확인 안 됨";

export function HubbleEvents() {
  const events = useRuntimeEvents();
  const [params, patch] = useParams();
  const { range } = useGlobalFilters();
  const { env, targetRef } = useEnvTarget();
  const search = params.get("q") ?? "";
  const verdict = params.get("verdict");
  const terms = search.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const all = (events.data?.items ?? []).filter((event) => event.source === "hubble" && event.observation
    && inRange(event.occurred_at ?? event.recorded_at, range) && matchesTarget(event.target_ref, env, targetRef));
  const rows = all.filter((event) => {
    const o = event.observation!;
    const text = [event.event_id, event.scope.environment, event.scope.run_id, o.src_namespace, o.src_pod,
      o.dst_namespace, o.dst_pod, o.dst_ip, o.dst_port, o.protocol, o.reason, o.verdict].join(" ").toLowerCase();
    return (!verdict || o.verdict?.toLowerCase() === verdict) && terms.every((term) => text.includes(term));
  }).sort((a, b) => Number(b.observation!.verdict?.toLowerCase() === "dropped") - Number(a.observation!.verdict?.toLowerCase() === "dropped")
    || (Date.parse(b.occurred_at ?? b.recorded_at) || 0) - (Date.parse(a.occurred_at ?? a.recorded_at) || 0));
  const pagination = usePagination(rows.length);
  const selected = all.find((event) => event.event_id === params.get("sel"));
  return <>
    <FilterBar>
      <input className="input" style={{ width: "min(440px, 100%)", height: 32 }} aria-label="네트워크 검색"
        placeholder="Pod·포트·사유 또는 이벤트 ID 검색" value={search}
        onChange={(event) => patch({ q: event.target.value || null, sel: null })} />
      <FilterSelect label="결과" value={verdict} onChange={(value) => patch({ verdict: value, sel: null })}
        options={[{ value: "dropped", label: "차단" }, { value: "forwarded", label: "통과" }]} />
      {(search || verdict) && <button className="btn btn-small btn-ghost" onClick={() => patch({ q: null, verdict: null, sel: null })}>필터 지우기</button>}
    </FilterBar>
    <Query q={events}>{() => <TableCard>
      <table className={rt.statusTable}>
        <thead><tr><th>시각</th><th>환경</th><th>출발</th><th>도착</th><th>결과</th><th>사유</th><th>기록</th></tr></thead>
        <tbody>{rows.slice(pagination.start, pagination.end).map((event) => {
          const o = event.observation!;
          const dropped = o.verdict?.toLowerCase() === "dropped";
          return <tr key={event.event_id}>
            <td>{formatTime(event.occurred_at)}</td>
            <td>{event.scope.environment ?? "확인 안 됨"}</td>
            <td>{pod(o.src_namespace, o.src_pod)}</td>
            <td>{pod(o.dst_namespace, o.dst_pod)}<br /><span className="mono small">{o.dst_port ?? "–"}/{o.protocol ?? "–"}</span></td>
            <td><Badge tone={dropped ? "warn" : "neutral"} title={o.verdict ?? undefined}>{verdictLabel(o.verdict)}</Badge>{o.verdict && <div className="small mono muted">{o.verdict}</div>}</td>
            <td className="mono">{o.reason ?? "기록 없음"}</td>
            <td><button className="btn btn-small" onClick={() => patch({ sel: event.event_id })}>기록 보기</button>
              {event.origin_detail === "synthetic" ? <Badge tone="warn">합성</Badge> : event.data_origin !== "MEASURED" ? <OriginBadge origin={event.data_origin} detail={event.origin_detail} /> : null}</td>
          </tr>;
        })}</tbody>
      </table>
      <Pagination state={pagination} />
      {rows.length === 0 && <p className={rt.empty}>선택한 기간·환경과 검색 조건에 맞는 네트워크 기록이 없습니다.</p>}
    </TableCard>}</Query>
    {selected?.observation && <DetailPanel title="네트워크 기록" subtitle={<span className="mono">{selected.event_id}</span>}
      onClose={() => patch({ sel: null })} tabs={[
        { id: "detail", label: "기록", content: <div className="stack">
          <KeyValue items={[
            ["시각", formatTime(selected.occurred_at)],
            ["결과", `${verdictLabel(selected.observation.verdict)} · ${selected.observation.verdict ?? "–"}`],
            ["사유", selected.observation.reason ?? "확인 안 됨"],
            ["출발", pod(selected.observation.src_namespace, selected.observation.src_pod)],
            ["도착", pod(selected.observation.dst_namespace, selected.observation.dst_pod)],
            ["포트", `${selected.observation.dst_port ?? "–"}/${selected.observation.protocol ?? "–"}`],
          ]} />
          <ScopeBadges environment={selected.scope.environment} runId={selected.scope.run_id} />
          <ObservationOriginBadge origin={selected.origin_detail} />
        </div> },
        { id: "raw", label: "원본 기록", content: <JsonView value={selected} /> },
      ]} />}
  </>;
}
