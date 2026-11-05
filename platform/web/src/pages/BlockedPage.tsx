import { useEffect, useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useRequests, useRuntimeEvents } from "../api/hooks";
import type { RequestRow, RuntimeEvent } from "../api/types";
import { OriginBadge } from "../components/Badges";
import { Query } from "../components/Misc";
import { Time } from "../components/Value";
import { matchesTarget, useEnvTarget } from "../lib/filters";
import { GATEWAY_REASON } from "../lib/labels";
import { inRange } from "../lib/time";
import { isTetragonOverride } from "../lib/tetragon";
import { carry, useGlobalFilters } from "../lib/url";
import p from "./pages.module.css";

/** 표시 집계에만 사용한다. 탐지와 미확인 전달 상태는 차단 내역에 넣지 않는다. */
export function blockedRows(requests: RequestRow[], events: RuntimeEvent[]) {
  return {
    requests: requests.filter((r) => r.delivery_attempted === false && ["denied", "invalid", "error"].includes(r.outcome)),
    network: events.filter((e) => e.source === "hubble" && e.observation?.verdict?.toLowerCase() === "dropped"),
    files: events.filter(isTetragonOverride),
  };
}

export function BlockedPage() {
  const requests = useRequests();
  const events = useRuntimeEvents();
  const { range } = useGlobalFilters();
  const { env, targetRef } = useEnvTarget();
  const [params] = useSearchParams();
  const selected = params.get("section");
  const rows = useMemo(() => blockedRows(
    (requests.data?.items ?? []).filter((r) => inRange(r.recorded_at, range) && matchesTarget(r.target_ref, env, targetRef)),
    (events.data?.items ?? []).filter((e) => inRange(e.recorded_at, range) && matchesTarget(e.target_ref, env, targetRef)),
  ), [requests.data, events.data, range, env, targetRef]);

  useEffect(() => {
    if (["requests", "network", "files"].includes(selected ?? "")) document.getElementById(`blocked-${selected}`)?.scrollIntoView?.();
  }, [selected, requests.isSuccess, events.isSuccess]);

  const sectionClass = (id: string) => `card card-pad ${p.blockedSection} ${selected === id ? p.blockedSelected : ""}`;
  const eventLink = (e: RuntimeEvent) => `/runtime/events${carry(params, { sel: e.event_id })}`;
  return <div className="page"><Query q={requests}>{() => <Query q={events}>{() => <>
    <section id="blocked-requests" className={sectionClass("requests")} aria-label="엔진에 전달되지 않은 요청">
      <h2>엔진에 전달되지 않은 요청 <span className={p.blockedCount}>{rows.requests.length}건</span></h2>
      {rows.requests.length ? <table className={p.blockedTable}><thead><tr><th>시각</th><th>요청 ID</th><th>사유</th><th>자료</th></tr></thead><tbody>
        {rows.requests.map((r) => <tr key={r.request_id}><td><Time iso={r.recorded_at} /></td><td><Link className="mono" to={`/runtime/requests${carry(params, { sel: r.request_id })}`}>{r.request_id}</Link></td><td>{r.reason_code ? GATEWAY_REASON[r.reason_code] ?? `사유 확인 필요 (${r.reason_code})` : "사유 미기록"}</td><td><OriginBadge origin={r.data_origin} detail={r.origin_detail} /></td></tr>)}
      </tbody></table> : <p className="small muted">선택한 기간·환경에 엔진 미전달 거부·입력 오류·처리 오류 요청이 없습니다.</p>}
    </section>
    <section id="blocked-network" className={sectionClass("network")} aria-label="네트워크 차단">
      <h2>네트워크 차단 (Hubble DROPPED) <span className={p.blockedCount}>{rows.network.length}건</span></h2>
      {rows.network.length ? <table className={p.blockedTable}><thead><tr><th>시각</th><th>출발 → 도착</th><th>판정·사유</th><th>자료</th></tr></thead><tbody>
        {rows.network.map((e) => <tr key={e.event_id}><td><Time iso={e.occurred_at ?? e.recorded_at} /></td><td><Link to={eventLink(e)}>{e.observation?.src_pod ?? "–"} → {e.observation?.dst_pod ?? e.observation?.dst_ip ?? "–"}</Link></td><td>DROPPED{e.observation?.reason ? ` · ${e.observation.reason}` : ""}</td><td><OriginBadge origin={e.data_origin} detail={e.origin_detail} /></td></tr>)}
      </tbody></table> : <p className="small muted">선택한 기간·환경에 Hubble DROPPED 기록이 없습니다. 수집 상태도 확인하세요.</p>}
      <p className="small muted">Hubble의 DROPPED 관측 기록입니다. 정책 귀속·수신 대조 전에는 통제 결과로 확정하지 않습니다.</p>
    </section>
    <section id="blocked-files" className={sectionClass("files")} aria-label="파일 차단 시도">
      <h2>파일 차단 시도 (Tetragon Override) <span className={p.blockedCount}>{rows.files.length}건</span></h2>
      {rows.files.length ? <table className={p.blockedTable}><thead><tr><th>시각</th><th>파일 경로</th><th>규칙·행위</th><th>자료</th></tr></thead><tbody>
        {rows.files.map((e) => <tr key={e.event_id}><td><Time iso={e.occurred_at ?? e.recorded_at} /></td><td><Link className="mono" to={eventLink(e)}>{e.observation?.path ?? "경로 미기록"}</Link></td><td>{e.observation?.rule_id ?? "규칙 미기록"} · Override</td><td><OriginBadge origin={e.data_origin} detail={e.origin_detail} />{e.origin_detail === "synthetic" && <span className="small muted"> 예시 자료</span>}</td></tr>)}
      </tbody></table> : <p className="small muted">선택한 기간·환경에 Tetragon Override 파일 차단 시도 기록이 없습니다. 탐지 이벤트는 포함하지 않습니다.</p>}
      <p className="small muted">Override는 차단 시도 기록입니다. 실제 EPERM 결과는 검증 시험 (Lab)에서 확인합니다.</p>
    </section>
  </>}</Query>}</Query></div>;
}
