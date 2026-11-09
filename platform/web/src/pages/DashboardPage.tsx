import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useOverview, useRequests, useRuntimeEvents } from "../api/hooks";
import { OriginBadge, VerdictBadge } from "../components/Badges";
import { KeyValue } from "../components/DetailPanel";
import { MetricCard, Query, StateView, ui } from "../components/Misc";
import { ChartCard, Donut, EVENT_SERIES, REQUEST_SERIES, TimeBars, TimeLine, eventKey, type EventKey, type RequestKey } from "../components/StackedBars";
import { AREA, GATEWAY_REASON } from "../lib/labels";
import { matchesTarget, useEnvTarget } from "../lib/filters";
import { bucketize, inRange } from "../lib/time";
import { carry, useGlobalFilters } from "../lib/url";
import p from "./pages.module.css";
import { GpuPanel } from "./runtime/GpuPanel";

const AREAS = ["SUP", "REQ", "PLT", "LIF", "OBS"] as const;
const DAY = 24 * 60 * 60 * 1000;

export function DashboardPage() {
  const overview = useOverview();
  const requests = useRequests();
  const events = useRuntimeEvents();
  const { range } = useGlobalFilters();
  const { env, targetRef } = useEnvTarget();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [requestMode, setRequestMode] = useState("total");
  const go = (path: string, extra: Record<string, string> = {}) => navigate(`${path}${carry(params, extra)}`);
  const reqRows = useMemo(() => (requests.data?.items ?? []).filter((r) => inRange(r.recorded_at, range) && matchesTarget(r.target_ref, env, targetRef)), [requests.data, range, env, targetRef]);
  const evRows = useMemo(() => (events.data?.items ?? []).filter((e) => inRange(e.recorded_at, range) && matchesTarget(e.target_ref, env, targetRef)), [events.data, range, env, targetRef]);
  const observationRows = evRows.filter((e) => e.source === "hubble" || e.source === "tetragon");
  const blockedRequests = reqRows.filter((r) => r.delivery_attempted === false && ["denied", "invalid", "error"].includes(r.outcome)).length;
  const dropped = evRows.filter((e) => e.source === "hubble" && e.observation?.verdict?.toLowerCase() === "dropped").length;
  const process = evRows.filter((e) => e.source === "tetragon" && e.observation?.kind === "process_exec").length;
  const files = evRows.filter((e) => e.source === "tetragon" && e.observation?.kind === "file_access").length;
  const processDetected = evRows.some((e) => e.source === "tetragon" && e.observation?.kind === "process_exec" && e.outcome === "detected");
  const fileDetected = evRows.some((e) => e.source === "tetragon" && e.observation?.kind === "file_access" && e.outcome === "detected");
  const reqChart = bucketize(reqRows, (r) => r.recorded_at, (r) => r.outcome as RequestKey, REQUEST_SERIES.map((x) => x.key), range);
  const reqDayChart = bucketize(reqRows, (r) => r.recorded_at, (r) => r.outcome as RequestKey, REQUEST_SERIES.map((x) => x.key), range, DAY);
  const evChart = bucketize(observationRows, (e) => e.recorded_at, (e) => eventKey(e.outcome), EVENT_SERIES.map((x) => x.key), range);
  const openRequests = (from: number, to: number) => go("/runtime/requests", { period: "custom", from: new Date(from).toISOString(), to: new Date(to).toISOString() });
  const reasons = new Map<string, number>();
  for (const r of reqRows) if (["denied", "invalid", "engine_rejected", "error"].includes(r.outcome) && r.reason_code) reasons.set(r.reason_code, (reasons.get(r.reason_code) ?? 0) + 1);
  const topReasons = [...reasons.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  const resultCounts = new Map(REQUEST_SERIES.map((series) => [series.key, reqRows.filter((r) => r.outcome === series.key).length]));

  return <div className="page"><Query q={overview}>{(o) => {
    const status = o.status?.item;
    const connected = status?.collectors.filter((c) => c.state === "ok").length ?? 0;
    const total = status?.collectors.length ?? 0;
    const verification = o.latest_verification;
    return <>
      {(requests.isError || events.isError) && <StateView kind="disconnected" detail="실시간 관측 장애로 일부 집계가 누락될 수 있습니다. 수집 상태를 확인하세요." />}
      <section className={p.statusPanel} aria-labelledby="control-observation-heading">
        <div className={p.statusPanelHead}><h2 id="control-observation-heading">통제·관측 현황</h2><span className="small muted">선택한 기간 기준</span></div>
        <div className={p.fiveCards}>
        <MetricCard title="요청 통제 (Gateway)" value={blockedRequests} caption="엔진 미전달" dot={blockedRequests ? "bad" : "ok"} onClick={() => go("/runtime/blocked", { section: "requests" })} label="차단 내역의 엔진 미전달 요청 보기"><span className="small muted">엔진에 전달되지 않은 거부·입력 오류 등</span></MetricCard>
        <MetricCard title="네트워크 차단" value={dropped} caption="DROPPED · 정보" dot="none" onClick={() => go("/runtime/blocked", { section: "network" })} label="차단 내역의 네트워크 차단 보기"><span className="small muted">네트워크 이벤트 (Hubble)의 DROPPED 기록</span></MetricCard>
        <MetricCard title="프로세스 이벤트" value={process} caption={processDetected ? "탐지 · 주의" : "관측 · 정보"} dot={processDetected ? "warn" : "none"} onClick={() => go("/runtime/observe/tetragon")} label="프로세스 이벤트 보기"><span className="small muted">프로세스·파일 이벤트 (Tetragon): 실행 기록</span></MetricCard>
        <MetricCard title="파일 이벤트" value={files} caption={fileDetected ? "탐지 · 주의" : "관측 · 정보"} dot={fileDetected ? "warn" : "none"} onClick={() => go("/runtime/observe/tetragon")} label="파일 이벤트 보기"><span className="small muted">프로세스·파일 이벤트 (Tetragon): 파일 접근 기록</span></MetricCard>
        <MetricCard title="수집 상태" value={`${connected}/${total}`} caption={total && connected === total ? "정상" : "주의"} dot={total && connected === total ? "ok" : "warn"} onClick={() => go("/runtime/observe/collectors")} label="수집 상태 보기"><span className="small muted">정상 수집기 / 전체 수집기</span></MetricCard>
        </div>
      </section>
      <GpuPanel />
      {status && <details className="card" style={{ padding: 16 }}><summary>요청 통제 정책 자세히</summary><KeyValue items={[["Gateway 활성", status.policy.gateway_active ? "활성" : "비활성"], ["사용자별 캐시 분리", <span style={status.policy.cache_isolation.mode === "per_principal" && !status.policy.cache_isolation.ready ? { color: "var(--error)" } : undefined}>{status.policy.cache_isolation.mode === "off" ? "비활성 (사용자 간 prefix cache 공유)" : status.policy.cache_isolation.mode === "per_principal" ? status.policy.cache_isolation.ready ? "활성" : `준비 불가(${status.policy.cache_isolation.unavailable_reason_code ?? "unknown"})` : "미확인"}</span>]]} /></details>}
      <div className={`${p.chartRow} ${p.dashboardChartRow}`}>
        <div className={p.chartSide}>
          <ChartCard
            title="시간별 요청"
            tabs={[
              { id: "total", label: "전체" },
              { id: "results", label: "결과별" },
            ]}
            active={requestMode}
            onTab={setRequestMode}
            total={reqRows.length}
            note="최근 요청"
          >
            {requestMode === "total"
              ? <TimeLine buckets={reqDayChart.buckets} size={reqDayChart.size} keys={REQUEST_SERIES.map((x) => x.key)} onSelectRange={openRequests} />
              : <TimeBars buckets={reqChart.buckets} size={reqChart.size} series={[...REQUEST_SERIES]} onSelectRange={openRequests} />}
          </ChartCard>
          <ChartCard title="시간별 관측 이벤트">
            <div className={p.chartSubhead}><span className="small muted">관측 이벤트 {observationRows.length}건</span></div>
            <TimeBars<EventKey> buckets={evChart.buckets} size={evChart.size} series={[...EVENT_SERIES]} onSelectRange={(from, to) => go("/runtime/events", { period: "custom", from: new Date(from).toISOString(), to: new Date(to).toISOString() })} />
          </ChartCard>
        </div>
        <div className={p.chartSide}>
          <ChartCard title="구성 비율" note="선택한 기간의 요청 결과" total={reqRows.length}>
            <Donut items={REQUEST_SERIES.map((series) => ({ ...series, value: resultCounts.get(series.key) ?? 0 }))} />
          </ChartCard>
          <ChartCard title="거부·오류 사유" footer={<p className="small muted">최근 요청 중 거부·오류 상위 5개 사유</p>}>
            {topReasons.length ? <ol className={p.reasonList}>{topReasons.map(([reason, count], index) => {
              const label = GATEWAY_REASON[reason] ?? "사유 확인 필요";
              const undelivered = label.endsWith(" (엔진 미전달)");
              return <li key={reason}><button type="button" onClick={() => go("/runtime/requests", { reason })}>
                <span className={`${p.reasonRank} ${index === 0 ? p.reasonRankFirst : ""}`} aria-hidden="true">{index + 1}</span>
                <span className={p.reasonLabel}>{undelivered ? label.slice(0, -" (엔진 미전달)".length) : label}{undelivered && <small className={p.reasonTag}>엔진 미전달</small>}</span>
                <span className={p.reasonCount}><strong>{count}</strong>건</span>
              </button></li>;
            })}</ol> : <p className="small muted">거부·오류 사유가 없습니다. 요청 내역에서 전체 결과를 확인하세요.</p>}
          </ChartCard>
        </div>
      </div>
      <h2 className={ui.blockTitle}>최신 업데이트 검증</h2>
      <div className={ui.cardGrid}>
        <MetricCard title="최신 검증 결과" dot={verification ? "ok" : "none"} onClick={() => go("/update/results")} label="업데이트 검증 결과 보기">{verification ? <div className="stack">{AREAS.map((a) => <div key={a}>{AREA[a]} <VerdictBadge verdict={verification.summary.by_area[a]?.security ?? null} /></div>)}<OriginBadge origin={verification.data_origin} /></div> : <span className="muted">검증 결과가 없습니다. 검증 결과에서 새 보고서를 확인하세요.</span>}</MetricCard>
        <MetricCard title="재검증 요청" value={o.open_revalidation ?? "–"} caption="미처리 요청" dot={o.open_revalidation ? "warn" : "none"} onClick={() => go("/runtime/revalidation")} label="재검증 요청 보기" />
      </div>
    </>;
  }}</Query></div>;
}
