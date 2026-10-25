import { useRevalidation } from "../api/hooks";
import { EnumBadge, OriginBadge } from "../components/Badges";
import { DataTable } from "../components/DataTable";
import { InvalidRows, PageHead, Query, StateView } from "../components/Misc";
import { AnyValue, Short, Time, Value } from "../components/Value";
import { REVAL_EVENT, REVAL_STATUS, TRIGGER, label } from "../lib/labels";
import { matchesTarget, useEnvTarget } from "../lib/filters";

/** 재검증 요청 목록. 재검증 시작 버튼은 이번 단계에서 만들지 않는다. */
export function RevalidationPage() {
  const q = useRevalidation();
  const { env, targetRef } = useEnvTarget();
  return (
    <div className="page">
      <PageHead title="재검증 요청" />
      <Query q={q}>
        {(data) => (
          <>
            <DataTable
              columns={[
                { id: "created_at", header: "시각", cell: (r) => <Time iso={r.created_at} />, sort: (r) => r.created_at },
                { id: "status", header: "상태", cell: (r) => <EnumBadge table={REVAL_STATUS} value={r.status} />, sort: (r) => r.status },
                { id: "event", header: "마지막 사건", cell: (r) => label(REVAL_EVENT, r.event), sort: (r) => r.event },
                { id: "trigger", header: "트리거", cell: (r) => label(TRIGGER, r.trigger), sort: (r) => r.trigger },
                { id: "target", header: "대상", cell: (r) => <Value v={r.target_ref} mono />, sort: (r) => r.target_ref },
                {
                  id: "policy",
                  header: "정책 판본",
                  cell: (r) => (r.policy_ref ? <span className="mono">{`${r.policy_ref.policy_id} v${r.policy_ref.policy_version}`}</span> : <Value v={null} />),
                },
                {
                  id: "changed",
                  header: "바뀐 필드 (기대값 → 관측값)",
                  cell: (r) =>
                    r.changed_fields.length === 0 ? (
                      <Value v={null} />
                    ) : (
                      <span className="stack" style={{ gap: 2 }}>
                        {r.changed_fields.map((f) => (
                          <span key={f.field} className="mono">
                            {f.field}: <AnyValue v={f.expected} /> → <AnyValue v={f.observed} />
                          </span>
                        ))}
                      </span>
                    ),
                },
                { id: "superseded_by", header: "대체", cell: (r) => <Value v={r.superseded_by} mono /> },
                { id: "id", header: "ID", cell: (r) => <span className="mono">{r.revalidation_id}</span>, sort: (r) => r.revalidation_id },
                { id: "fingerprint", header: "근거 지문", cell: (r) => <Short value={r.fingerprint} />, hidden: true },
                { id: "origin", header: "자료 등급", cell: (r) => <OriginBadge origin={r.data_origin} detail={r.origin_detail} /> },
              ]}
              rows={data.items.filter((r) => matchesTarget(r.target_ref, env, targetRef))}
              rowKey={(r) => r.revalidation_id}
              initialSort={{ id: "created_at", dir: "desc" }}
              storageKey="revalidation"
              empty={<StateView kind="empty" detail="재검증 요청이 없습니다." />}
            />
            <InvalidRows items={data.invalid} what="재검증 요청" />
            <p className="small muted">재검증은 자동으로 시작하지 않습니다. 시작 방법은 다음 단계에서 정합니다.</p>
          </>
        )}
      </Query>
    </div>
  );
}
