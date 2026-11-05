import { Link } from "react-router-dom";
import { useRuntimeEvents } from "../../api/hooks";
import { Badge, OriginBadge } from "../../components/Badges";
import { PageHead, Query, StateView } from "../../components/Misc";
import { Pagination, usePagination } from "../../components/Pagination";
import { groupAlerts } from "../../lib/alerts";
import { isTetragonBlockAttempt, isTetragonSigkill } from "../../lib/tetragon";
import { formatTime } from "./parts";

const color: Record<string, string> = { 높음: "var(--error)", 중간: "var(--warn)", 낮음: "var(--warn)", 정보: "var(--accent)", "미분류": "var(--muted)" };

export function AlertsPage() {
  const events = useRuntimeEvents();
  const groups = groupAlerts(events.data?.items ?? []);
  const pagination = usePagination(groups.length);
  return <div className="page">
    <PageHead title="탐지 알림" />
    <Query q={events}>{() => <>
        <p className="small muted">동일 판정·실행 파일 또는 규칙·엔진 실행 단계별 집계입니다. 탐지는 차단 근거와 구분됩니다.</p>
        {groups.length ? groups.slice(pagination.start, pagination.end).map((g) => <details key={g.key} className="card" style={{ padding: 16, marginBottom: 8 }}>
          <summary><strong style={{ color: color[g.severity] }}>{g.severity}</strong> · {isTetragonBlockAttempt(g.items[0]) && <><Badge tone="caution" title="원시 차단 시도 기록">차단 시도</Badge> · </>}{g.title} · {g.items.length}건 <span className="small muted">{formatTime(g.first)} ~ {formatTime(g.last)}</span></summary>
          <ul>{g.items.map((e) => <li key={e.event_id}>
            {formatTime(e.occurred_at)} · {isTetragonBlockAttempt(e) ? <><Badge tone="caution" title="원시 차단 시도 기록">차단 시도</Badge>{isTetragonSigkill(e) && " · 프로세스 종료"}</> : e.source === "tetragon" ? "프로세스·파일 탐지" : "네트워크 관측"} · <OriginBadge origin={e.data_origin} detail={e.origin_detail} /> · <Link to={`/runtime/events?sel=${encodeURIComponent(e.event_id)}`}>자세히</Link>
          </li>)}</ul>
        </details>) : <StateView kind="empty" detail="관측 이벤트 미수집 상태입니다. 수집 상태를 확인하세요." />}
        <Pagination state={pagination} />
      </>}</Query>
  </div>;
}
