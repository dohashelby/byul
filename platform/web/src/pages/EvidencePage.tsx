import { Link, useSearchParams } from "react-router-dom";
import { useEvidence } from "../api/hooks";
import { OriginBadge } from "../components/Badges";
import { DataTable } from "../components/DataTable";
import { InvalidRows, PageHead, Query, StateView } from "../components/Misc";
import { Short, Time, Value } from "../components/Value";
import { carry } from "../lib/url";

export function EvidencePage() {
  const q = useEvidence();
  const [params] = useSearchParams();
  return (
    <div className="page">
      <PageHead title="증거 목록" />
      <Query q={q}>
        {(data) => (
          <>
            <DataTable
              columns={[
                { id: "collected_at", header: "수집 시각", cell: (e) => <Time iso={e.item.collected_at} />, sort: (e) => e.item.collected_at },
                { id: "evidence_id", header: "증거 ID", cell: (e) => <span className="mono">{e.item.evidence_id}</span>, sort: (e) => e.item.evidence_id },
                { id: "producer", header: "생산", cell: (e) => e.producer, sort: (e) => e.producer },
                { id: "source", header: "출처", cell: (e) => e.item.source, sort: (e) => e.item.source },
                { id: "path", header: "경로", cell: (e) => <span className="mono">{e.item.path}</span> },
                { id: "media", header: "형식", cell: (e) => e.item.media_type },
                { id: "bytes", header: "크기(B)", cell: (e) => e.item.bytes, sort: (e) => e.item.bytes },
                { id: "sha", header: "SHA-256", cell: (e) => <Short value={e.item.sha256} /> },
                { id: "collector", header: "수집기", cell: (e) => <span className="mono">{`${e.item.collector.name} ${e.item.collector.version ?? "–"}`}</span> },
                {
                  id: "window",
                  header: "구간",
                  cell: (e) =>
                    e.item.window ? (
                      <span>
                        <Time iso={e.item.window.from} /> ~ <Time iso={e.item.window.until} />
                      </span>
                    ) : (
                      <Value v={null} />
                    ),
                },
                {
                  id: "refs",
                  header: "참조",
                  cell: (e) =>
                    e.item.refs?.length ? (
                      <span className="row wrap" style={{ gap: 6 }}>
                        {e.item.refs.map((r) =>
                          r.startsWith("req:") ? (
                            <Link key={r} to={`/runtime/requests${carry(params, { sel: r.slice(4) })}`} className="mono">
                              {r.slice(0, 16)}…
                            </Link>
                          ) : (
                            <span key={r} className="mono">
                              {r}
                            </span>
                          ),
                        )}
                      </span>
                    ) : (
                      <Value v={null} />
                    ),
                },
                { id: "origin", header: "자료 등급", cell: (e) => <OriginBadge origin={e.item.data_origin} /> },
                { id: "index", header: "목록 파일", cell: (e) => <Value v={e.index_path} mono />, hidden: true },
              ]}
              rows={data.items}
              rowKey={(e) => `${e.index_path}:${e.item.evidence_id}`}
              initialSort={{ id: "collected_at", dir: "desc" }}
              storageKey="evidence"
              empty={<StateView kind="empty" detail="증거 목록이 비어 있습니다." />}
            />
            <InvalidRows items={data.invalid} what="증거 목록 항목" />
            <p className="small muted">해시는 내용 일치 확인용이며 저장 관리자까지 포함한 변조 방지가 아닙니다.</p>
          </>
        )}
      </Query>
    </div>
  );
}
