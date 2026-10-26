// 대시보드. serving-runtime "현황" 화면의 배치를 따른다:
// 요약 카드 3개 → 탭이 있는 그래프 2개 → 구성요소 상태 카드.
import { useMemo, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useOverview, useRequests, useRuntimeEvents } from "../api/hooks";
import { EnumBadge, OriginBadge, VerdictBadge } from "../components/Badges";
import { KeyValue } from "../components/DetailPanel";
import { MetricCard, Query, StateView, ui } from "../components/Misc";
import {
  ChartCard,
  Donut,
  EVENT_SERIES,
  HBars,
  REQUEST_SERIES,
  TimeBars,
  TimeLine,
  eventKey,
  type EventKey,
  type RequestKey,
} from "../components/StackedBars";
import { Short, Time, Value } from "../components/Value";
import { APPROVAL_STATE, AREA, COLLECTOR_STATE, OUTCOME, SOURCE, STORE_STATE, label } from "../lib/labels";
import { matchesTarget, useEnvTarget } from "../lib/filters";
import { bucketize, inRange } from "../lib/time";
import { carry, useGlobalFilters } from "../lib/url";
import p from "./pages.module.css";

const AREAS = ["SUP", "REQ", "PLT", "LIF", "OBS"] as const;

export function DashboardPage() {
  const overview = useOverview();
  const requests = useRequests();
  const events = useRuntimeEvents();
  const { range } = useGlobalFilters();
  const { env, targetRef } = useEnvTarget();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const go = (path: string, extra: Record<string, string> = {}) => navigate(`${path}${carry(params, extra)}`);
  const [timeMode, setTimeMode] = useState("total");
  const [mixMode, setMixMode] = useState("results");

  const reqRows = useMemo(
    () => (requests.data?.items ?? []).filter((r) => inRange(r.recorded_at, range) && matchesTarget(r.target_ref, env, targetRef)),
    [requests.data, range, env, targetRef],
  );
  const evRows = useMemo(
    () => (events.data?.items ?? []).filter((e) => inRange(e.recorded_at, range) && matchesTarget(e.target_ref, env, targetRef)),
    [events.data, range, env, targetRef],
  );
  const observations = evRows.filter((e) => e.source !== "gateway");

  const reqChart = bucketize(reqRows, (r) => r.recorded_at, (r) => r.outcome as RequestKey, REQUEST_SERIES.map((x) => x.key), range);
  const evChart = bucketize(evRows, (e) => e.recorded_at, (e) => eventKey(e.outcome), EVENT_SERIES.map((x) => x.key), range);

  // 거부·오류 사유: 최근 요청 중 거부·입력 오류·엔진 거부·오류의 사유 코드별 건수(미완료는 넣지 않는다)
  const DENY_OR_ERROR = new Set(["denied", "invalid", "engine_rejected", "error"]);
  const reasons = new Map<string, number>();
  for (const r of reqRows) if (DENY_OR_ERROR.has(r.outcome) && r.reason_code) reasons.set(r.reason_code, (reasons.get(r.reason_code) ?? 0) + 1);
  const topReasons = [...reasons.entries()].sort((a, b) => b[1] - a[1]);

  // 관측 사건: 규칙(없으면 출처·결과)별 건수. Hubble은 결과가 '관측'이다.
  const rules = new Map<string, number>();
  for (const e of observations) {
    const key = e.rule_ref ?? `${label(SOURCE, e.source)} ${OUTCOME[e.outcome]?.label ?? e.outcome}`;
    rules.set(key, (rules.get(key) ?? 0) + 1);
  }
  const topRules = [...rules.entries()].sort((a, b) => b[1] - a[1]);

  const pickRange = (path: string) => (from: number, to: number) =>
    go(path, { period: "custom", from: new Date(from).toISOString(), to: new Date(to).toISOString() });

  return (
    <div className="page">
      <Query q={overview}>
        {(o) => {
          const status = o.status?.item;
          const counts = o.requests?.counts;
          const max = counts ? Math.max(1, ...Object.values(counts)) : 1;
          const v = o.latest_verification;
          return (
            <>
              <span className="muted mono">
                관측 세션 {status?.observation_session_id?.slice(0, 8) ?? "–"} · 대상 {status?.target.target_ref ?? "–"} · 계약 판정 원본: runtime·update
              </span>

              <div className={ui.cards}>
                <MetricCard title="요청" value={o.requests ? o.requests.total : "–"} caption="전체 요청 기록">
                  {counts ? (
                    <div className={ui.barList} role="group" aria-label="결과별 요청 수" style={{ width: "100%" }}>
                      {REQUEST_SERIES.map((ser) => (
                        <button
                          key={ser.key}
                          type="button"
                          className={`${ui.barRow} ${ser.key === "error" ? ui.isError : ""}`}
                          onClick={() => go("/runtime/requests", { outcome: ser.key })}
                        >
                          <span className={ui.barLabel}>{ser.label}</span>
                          <span className={ui.barTrack}>
                            <span className={ui.barFill} style={{ width: `${(counts[ser.key] / max) * 100}%` }} />
                          </span>
                          <span className={ui.barCount}>{counts[ser.key]}</span>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <span style={{ color: "var(--error)" }}>{o.requests_error?.message ?? "–"}</span>
                  )}
                </MetricCard>

                <MetricCard title="거부·오류 사유" value={topReasons.reduce((a, [, n]) => a + n, 0)} caption={`최근 ${reqRows.length}건 중 거부·오류`}>
                  <table className={ui.miniTable}>
                    <thead>
                      <tr>
                        <th>사유</th>
                        <th className={ui.num}>건수</th>
                      </tr>
                    </thead>
                    <tbody>
                      {topReasons.length === 0 ? (
                        <tr>
                          <td className="muted">거부·오류 없음</td>
                          <td />
                        </tr>
                      ) : (
                        topReasons.slice(0, 5).map(([reason, n]) => (
                          <tr key={reason} style={{ cursor: "pointer" }} onClick={() => go("/runtime/requests", { reason })}>
                            <td className="mono">{reason}</td>
                            <td className={ui.num}>{n}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </MetricCard>

                <MetricCard title="관측 사건" value={observations.length} caption="Hubble·Tetragon 사건">
                  <table className={ui.miniTable}>
                    <thead>
                      <tr>
                        <th>규칙·판정</th>
                        <th className={ui.num}>건수</th>
                      </tr>
                    </thead>
                    <tbody>
                      {topRules.length === 0 ? (
                        <tr>
                          <td className="muted">관측 사건 없음</td>
                          <td />
                        </tr>
                      ) : (
                        topRules.slice(0, 5).map(([rule, n]) => (
                          <tr key={rule}>
                            <td className="mono">{rule}</td>
                            <td className={ui.num}>{n}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </MetricCard>
              </div>

              <div className={p.chartRow}>
                {requests.isError || events.isError ? (
                  <div className="card">
                    <StateView kind="disconnected" />
                  </div>
                ) : (
                  <ChartCard
                    title="시간별 요청·사건"
                    tabs={[
                      { id: "total", label: "전체" },
                      { id: "results", label: "결과별" },
                      { id: "events", label: "runtime 사건" },
                    ]}
                    active={timeMode}
                    onTab={setTimeMode}
                    total={timeMode === "events" ? evRows.length : reqRows.length}
                    note={timeMode === "events" ? "사건 · 탐지·집행 시도·거부 확인 구분" : "최근 요청 · 선택한 기간"}
                    footer={<p className="small muted" style={{ margin: "8px 0 0" }}>구간을 드래그하면 그 구간으로 필터가 걸린 목록으로 이동합니다.</p>}
                  >
                    {timeMode === "total" && <TimeLine buckets={reqChart.buckets} size={reqChart.size} keys={REQUEST_SERIES.map((x) => x.key)} />}
                    {timeMode === "results" && (
                      <TimeBars buckets={reqChart.buckets} size={reqChart.size} series={[...REQUEST_SERIES]} onSelectRange={pickRange("/runtime/requests")} />
                    )}
                    {timeMode === "events" && (
                      <TimeBars<EventKey> buckets={evChart.buckets} size={evChart.size} series={[...EVENT_SERIES]} onSelectRange={pickRange("/runtime/events")} />
                    )}
                  </ChartCard>
                )}
                <ChartCard
                  title="구성 비율"
                  tabs={[
                    { id: "results", label: "요청 결과" },
                    { id: "rules", label: "관측 규칙" },
                  ]}
                  active={mixMode}
                  onTab={setMixMode}
                  total={mixMode === "results" ? (o.requests?.total ?? "–") : topRules.reduce((a, [, n]) => a + n, 0)}
                  note={mixMode === "results" ? "누적 요청 결과" : "관측 사건(규칙별)"}
                >
                  {mixMode === "results" ? (
                    counts ? (
                      <Donut items={REQUEST_SERIES.map((x) => ({ key: x.key, label: x.label, value: counts[x.key], color: x.color }))} />
                    ) : (
                      <StateView kind="disconnected" />
                    )
                  ) : (
                    <HBars rows={topRules.slice(0, 8)} />
                  )}
                </ChartCard>
              </div>

              <h2 className={ui.blockTitle}>구성요소 상태</h2>
              <div className={ui.cardGrid}>
                <MetricCard title="정책" dot={status ? (status.policy.gateway_active ? "ok" : "bad") : "none"} onClick={() => go("/runtime/status")} label="Runtime 상태로 이동">
                  {status ? (
                    <KeyValue
                      items={[
                        ["정책", status.policy.policy_ref ? <span className="mono">{`${status.policy.policy_ref.policy_id} v${status.policy.policy_ref.policy_version}`}</span> : <Value v={null} />],
                        ["SHA-256", <Short value={status.policy.policy_ref?.sha256} n={12} />],
                        ["승인", <EnumBadge table={APPROVAL_STATE} value={status.policy.approval_state} />],
                        ["만료", <Time iso={status.policy.valid_until} />],
                      ]}
                    />
                  ) : (
                    <span style={{ color: "var(--error)" }}>{o.status_error?.message ?? (o.status?.invalid ? "형식 오류" : "–")}</span>
                  )}
                </MetricCard>
                <MetricCard
                  title="수집기"
                  dot={status ? (status.collectors.some((c) => ["stale", "error", "disconnected"].includes(c.state)) ? "bad" : "ok") : "none"}
                  onClick={() => go("/runtime/status")}
                  label="수집기 상태로 이동"
                >
                  {status ? (
                    <KeyValue
                      items={[
                        ...["hubble", "tetragon"].map((src): [string, ReactNode] => {
                          const c = status.collectors.find((x) => x.source === src);
                          return [src === "hubble" ? "Hubble" : "Tetragon", <EnumBadge table={COLLECTOR_STATE} value={c?.state ?? "disconnected"} />];
                        }),
                        ["기록 저장", <EnumBadge table={STORE_STATE} value={status.store.state} />],
                      ]}
                    />
                  ) : (
                    <span>–</span>
                  )}
                </MetricCard>
                <MetricCard title="최신 검증 결과" dot={v ? "ok" : "none"} onClick={() => go("/update/results")} label="검증 결과로 이동">
                  {v ? (
                    <KeyValue
                      items={[
                        ...AREAS.map((a): [string, ReactNode] => [`${a} ${AREA[a]}`, <VerdictBadge verdict={v.summary.by_area[a]?.security ?? null} />]),
                        ["자료 등급", <OriginBadge origin={v.data_origin} />],
                      ]}
                    />
                  ) : (
                    <span className="muted">보고서 없음</span>
                  )}
                </MetricCard>
                <MetricCard
                  title="재검증 요청"
                  dot={o.open_revalidation ? "warn" : "none"}
                  value={o.open_revalidation ?? "–"}
                  caption="열린 요청"
                  onClick={() => go("/runtime/revalidation")}
                  label="재검증 요청으로 이동"
                >
                  {o.revalidation_error && <span style={{ color: "var(--error)" }}>{o.revalidation_error.message}</span>}
                </MetricCard>
              </div>
            </>
          );
        }}
      </Query>
    </div>
  );
}
