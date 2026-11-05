import { useMemo } from "react";
import { Link, useLocation } from "react-router-dom";
import { useRequests, useRuntimeEvents } from "../api/hooks";
import type { RequestRow, RuntimeEvent } from "../api/types";
import { OriginBadge } from "../components/Badges";
import { Query } from "../components/Misc";
import { Pagination, usePagination } from "../components/Pagination";
import { Time } from "../components/Value";
import { matchesTarget, useEnvTarget } from "../lib/filters";
import { GATEWAY_REASON } from "../lib/labels";
import { inRange } from "../lib/time";
import { isTetragonBlockAttempt, isTetragonSigkill } from "../lib/tetragon";
import { carry, useGlobalFilters, useParams } from "../lib/url";
import ui from "../components/ui.module.css";
import p from "./pages.module.css";

/** 표시 집계에만 사용한다. 탐지와 미확인 전달 상태는 차단 내역에 넣지 않는다. */
export function blockedRows(requests: RequestRow[], events: RuntimeEvent[]) {
  return {
    requests: requests.filter((r) => r.delivery_attempted === false && ["denied", "invalid", "error"].includes(r.outcome)),
    network: events.filter((e) => e.source === "hubble" && e.observation?.verdict?.toLowerCase() === "dropped"),
    files: events.filter(isTetragonBlockAttempt),
  };
}

export function BlockedPage() {
  const requests = useRequests();
  const events = useRuntimeEvents();
  const { range } = useGlobalFilters();
  const { env, targetRef } = useEnvTarget();
  const [params, patch] = useParams();
  const { hash } = useLocation();
  const selected = params.get("section") ?? hash.replace(/^#blocked-/, "");
  const active = selected === "network" || selected === "files" ? selected : "requests";
  const rows = useMemo(() => blockedRows(
    (requests.data?.items ?? []).filter((r) => inRange(r.recorded_at, range) && matchesTarget(r.target_ref, env, targetRef)),
    (events.data?.items ?? []).filter((e) => inRange(e.recorded_at, range) && matchesTarget(e.target_ref, env, targetRef)),
  ), [requests.data, events.data, range, env, targetRef]);

  const pagination = usePagination(rows[active].length);
  const sectionClass = `card card-pad ${p.blockedSection}`;
  const eventLink = (e: RuntimeEvent) => `/runtime/events${carry(params, { sel: e.event_id })}`;
  return <div className="page"><Query q={requests}>{() => <Query q={events}>{() => <>
    <div className={`${ui.cardTabs} ${p.blockedTabs}`} role="tablist" aria-label="차단 내역 구역">
      {([ ["requests", "엔진에 전달되지 않은 요청", rows.requests.length], ["network", "네트워크 차단", rows.network.length], ["files", "파일 차단 시도", rows.files.length] ] as const).map(([id, label, count]) =>
        <button key={id} type="button" role="tab" className={ui.cardTab} aria-selected={active === id} aria-controls={`blocked-${id}`} onClick={() => patch({ section: id, page: null })}>{label} {count}건</button>
      )}
    </div>
    {active === "requests" && <section id="blocked-requests" className={sectionClass} aria-label="엔진에 전달되지 않은 요청">
      <h2>엔진에 전달되지 않은 요청 <span className={p.blockedCount}>{rows.requests.length}건</span></h2>
      {rows.requests.length ? <table className={p.blockedTable}><thead><tr><th>시각</th><th>요청 ID</th><th>사유</th><th>자료</th></tr></thead><tbody>
        {rows.requests.slice(pagination.start, pagination.end).map((r) => <tr key={r.request_id}><td><Time iso={r.recorded_at} /></td><td><Link className="mono" to={`/runtime/requests${carry(params, { sel: r.request_id })}`}>{r.request_id}</Link></td><td>{r.reason_code ? GATEWAY_REASON[r.reason_code] ?? `사유 확인 필요 (${r.reason_code})` : "사유 미기록"}</td><td><OriginBadge origin={r.data_origin} detail={r.origin_detail} /></td></tr>)}
      </tbody></table> : <p className="small muted">선택한 기간·환경에 엔진 미전달 거부·입력 오류·처리 오류 요청이 없습니다.</p>}
      <Pagination state={pagination} />
    </section>}
    {active === "network" && <section id="blocked-network" className={sectionClass} aria-label="네트워크 차단">
      <h2>네트워크 차단 (Hubble DROPPED) <span className={p.blockedCount}>{rows.network.length}건</span></h2>
      {rows.network.length ? <table className={p.blockedTable}><thead><tr><th>시각</th><th>출발 → 도착</th><th>판정·사유</th><th>자료</th></tr></thead><tbody>
        {rows.network.slice(pagination.start, pagination.end).map((e) => <tr key={e.event_id}><td><Time iso={e.occurred_at ?? e.recorded_at} /></td><td><Link to={eventLink(e)}>{e.observation?.src_pod ?? "–"} → {e.observation?.dst_pod ?? e.observation?.dst_ip ?? "–"}</Link></td><td>DROPPED{e.observation?.reason ? ` · ${e.observation.reason}` : ""}</td><td><OriginBadge origin={e.data_origin} detail={e.origin_detail} /></td></tr>)}
      </tbody></table> : <p className="small muted">선택한 기간·환경에 Hubble DROPPED 기록이 없습니다. 수집 상태도 확인하세요.</p>}
      <Pagination state={pagination} />
      <p className="small muted">Hubble의 DROPPED 관측 기록입니다. 정책 귀속·수신 대조 전에는 통제 결과로 확정하지 않습니다.</p>
    </section>}
    {active === "files" && <section id="blocked-files" className={sectionClass} aria-label="파일 차단 시도">
      <h2>파일 차단 시도 (Tetragon Override·Sigkill) <span className={p.blockedCount}>{rows.files.length}건</span></h2>
      {rows.files.length ? <table className={p.blockedTable}><thead><tr><th>시각</th><th>파일 경로</th><th>규칙·행위</th><th>자료</th></tr></thead><tbody>
        {rows.files.slice(pagination.start, pagination.end).map((e) => <tr key={e.event_id}><td><Time iso={e.occurred_at ?? e.recorded_at} /></td><td><Link className="mono" to={eventLink(e)}>{e.observation?.path ?? "경로 미기록"}</Link></td><td>{e.observation?.rule_id ?? "규칙 미기록"} · {isTetragonSigkill(e) ? "Sigkill · 프로세스 종료" : "Override"}</td><td><OriginBadge origin={e.data_origin} detail={e.origin_detail} />{e.origin_detail === "synthetic" && <span className="small muted"> 예시 자료</span>}</td></tr>)}
      </tbody></table> : <p className="small muted">선택한 기간·환경에 Tetragon 파일 차단 시도 기록이 없습니다. 탐지 이벤트는 포함하지 않습니다.</p>}
      <Pagination state={pagination} />
      <p className="small muted">원시 행위는 차단 시도 기록입니다. Sigkill은 프로세스를 종료하며 실제 거부·서빙 영향은 별도 시험으로 확인합니다.</p>
    </section>}
  </>}</Query>}</Query></div>;
}
