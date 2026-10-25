import { useResult, useResults } from "../api/hooks";
import type { VerificationResult } from "../api/types";
import { InvalidRows, PageHead, Query, StateView } from "../components/Misc";
import { MODE, label } from "../lib/labels";
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
          const reportId = params.get("report") ?? data.items[0].report_ref.id;
          return (
            <>
              <PageHead title="격리 검증 결과">
                <label className="row small">
                  <span className="muted">보고서</span>
                  <select className="select" value={reportId} onChange={(e) => patch({ report: e.target.value, sel: null })}>
                    {data.items.map((r) => (
                      <option key={r.report_ref.id} value={r.report_ref.id}>
                        {r.created_at} · {label(MODE, r.mode)} · {r.data_origin} · {r.report_ref.id}
                      </option>
                    ))}
                  </select>
                </label>
              </PageHead>
              <Report id={reportId} selected={params.get("sel")} onSelect={(id) => patch({ sel: id })} />
              <InvalidRows items={data.invalid} what="검증 결과" />
            </>
          );
        }}
      </Query>
    </div>
  );
}

function Report({ id, selected, onSelect }: { id: string; selected: string | null; onSelect: (id: string | null) => void }) {
  const q = useResult(id);
  return <Query q={q}>{(d) => <FullReport report={d.report} rawChecks={d.raw_checks} selected={selected} onSelect={onSelect} />}</Query>;
}
