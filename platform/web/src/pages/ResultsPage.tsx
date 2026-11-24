import { useResult, useResults } from "../api/hooks";
import type { VerificationResult } from "../api/types";
import { InvalidRows, PageHead, Query, StateView } from "../components/Misc";
import { MODE, label } from "../lib/labels";
import { reportSearch } from "../lib/reportRef";
import { useParams } from "../lib/url";
import { FullReport } from "./ReportView";

/** 공식 결과 여부는 update가 판정한 값(계약 `official`)을 그대로 쓴다. */
export function isUnofficial(r: VerificationResult): boolean {
  return !r.official;
}

export function ResultsPage() {
  const list = useResults();
  const [params, patch] = useParams();
  return (
    <div className="page">
      <Query q={list}>
        {(data) => {
          if (data.items.length === 0)
            return (
              <>
                <PageHead title="격리 검증 결과" />
                <div className="card">
                  <StateView kind={data.invalid.length ? "invalid" : "empty"} detail="검증 보고서가 없습니다." />
                </div>
                <InvalidRows items={data.invalid} what="검증 결과" />
              </>
            );
          const requestedId = params.get("report");
          const revision = params.get("revision");
          const sha256 = params.get("sha256");
          const current = requestedId === null ? data.items[0] : data.items.find(({ report_ref: ref }) =>
            ref.id === requestedId && (revision === null && sha256 === null ||
              String(ref.revision) === revision && ref.sha256 === sha256));
          const reportId = requestedId ?? current?.report_ref.id ?? null;
          return (
            <>
              <PageHead title="격리 검증 결과">
                <label className="row small">
                  <span className="muted">보고서</span>
                  <select className="select" value={current ? reportSearch(current.report_ref) : ""} onChange={(e) => {
                    const chosen = new URLSearchParams(e.target.value);
                    patch({ report: chosen.get("report"), revision: chosen.get("revision"), sha256: chosen.get("sha256"), sel: null });
                  }}>
                    {!current && <option value="" disabled>선택한 보고서 없음</option>}
                    {data.items.map((r) => (
                      <option key={reportSearch(r.report_ref)} value={reportSearch(r.report_ref)}>
                        {r.created_at} · {label(MODE, r.mode)} · {r.report_ref.id}@{r.report_ref.revision} · {r.report_ref.sha256.slice(0, 8)}
                      </option>
                    ))}
                  </select>
                </label>
              </PageHead>
              <Report id={reportId} revision={current ? String(current.report_ref.revision) : revision}
                sha256={current?.report_ref.sha256 ?? sha256} selected={params.get("sel")} onSelect={(id) => patch({ sel: id })} />
              <InvalidRows items={data.invalid} what="검증 결과" />
            </>
          );
        }}
      </Query>
    </div>
  );
}

function Report({ id, revision, sha256, selected, onSelect }: { id: string | null; revision: string | null;
  sha256: string | null; selected: string | null; onSelect: (id: string | null) => void }) {
  const q = useResult(id, revision, sha256);
  return <Query q={q}>{(d) => <FullReport report={d.report} rawChecks={d.raw_checks} selected={selected} onSelect={onSelect} />}</Query>;
}
