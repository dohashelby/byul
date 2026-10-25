// 정책·기준 상태. 삭제된 runtime 대시보드 renderPolicyState()를 옮겼다.
import { useRuntimeStatus } from "../../api/hooks";
import { Badge } from "../../components/Badges";
import { PageHead, Query, StateView } from "../../components/Misc";
import { VALIDITY_ITEM, VALIDITY_RESULT, label } from "../../lib/labels";
import { TableCard, formatTime, rt } from "./parts";

export function RuntimePolicyPage() {
  const q = useRuntimeStatus();
  return (
    <div className="page">
      <PageHead title="정책·기준 상태" />
      <p className="notice">이 화면의 정책은 Gateway가 추론 요청을 허용·거부하는 규칙입니다. Cilium·Tetragon 정책은 「통제 적용」에서 따로 봅니다.</p>
      <Query q={q}>
        {(res) => {
          if (!res.item) return <StateView kind="invalid" detail={res.invalid?.errors.join(" / ")} />;
          const pol = res.item.policy;
          const badges: [string, boolean, string][] = [
            ["읽기", pol.loaded, pol.loaded ? "성공" : "실패"],
            ["Gateway 활성", pol.gateway_active, pol.gateway_active ? "활성" : "비활성"],
            ["외부 집행", false, pol.external_enforcement === "not_connected" ? "미연결" : pol.external_enforcement],
          ];
          const store = pol.history_store;
          return (
            <>
              <div className={rt.stateBadges}>
                {badges.map(([k, ok, v]) => (
                  <Badge key={k} tone={ok ? "accent" : "error"}>{`${k}: ${v}`}</Badge>
                ))}
              </div>
              <TableCard title="유효성 항목">
                <table className={rt.statusTable}>
                  <thead>
                    <tr>
                      <th>항목</th>
                      <th>상태</th>
                      <th>결과</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pol.validity_items.map((v) => (
                      <tr key={v.item}>
                        <td>{label(VALIDITY_ITEM, v.item)}</td>
                        <td>{v.state ?? "–"}</td>
                        <td>{label(VALIDITY_RESULT, v.result)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableCard>
              <TableCard title={<>정책 이력 <span className="muted small">{store.state === "ok" ? "저장 정상" : `저장 오류: ${store.last_error ?? "확인 불가"}`}</span></>}>
                <table className={rt.statusTable}>
                  <thead>
                    <tr>
                      <th>시각</th>
                      <th>이벤트</th>
                      <th>정책</th>
                      <th>판본</th>
                      <th>SHA-256</th>
                      <th>이전</th>
                      <th>표시</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pol.history.map((h, i) => (
                      <tr key={i} className={h.same_version_content_changed ? rt.rowChanged : undefined}>
                        <td>{formatTime(h.at)}</td>
                        <td>{h.event}</td>
                        <td>{h.policy_id ?? "–"}</td>
                        <td>{h.policy_version ?? "–"}</td>
                        <td className="mono" title={h.sha256 ?? undefined}>
                          {h.sha256 ? `${h.sha256.slice(0, 12)}…` : "–"}
                        </td>
                        <td>{h.previous_version ?? "–"}</td>
                        <td>{h.same_version_content_changed ? "같은 판본 내용 변경" : "–"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {pol.history.length === 0 && <p className={rt.empty}>정책 이력이 없습니다.</p>}
              </TableCard>
            </>
          );
        }}
      </Query>
    </div>
  );
}
