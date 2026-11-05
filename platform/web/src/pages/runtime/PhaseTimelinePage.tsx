import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { usePhaseTimeline, useRuntimeEvents } from "../../api/hooks";
import type { PhaseTimelineResponse, RuntimeEvent } from "../../api/types";
import { OriginBadge, Badge } from "../../components/Badges";
import { Legend, PageHead, Query, StateView } from "../../components/Misc";
import { ChartCard } from "../../components/StackedBars";
import { alertSeverity, EXEC_VERDICT, PARENT_CHECK, PHASE, SEVERITY_ORDER } from "../../lib/labels";
import { formatTime, Subtabs } from "./parts";
import s from "./runtime.module.css";

const PHASE_COLORS: Record<string, string> = {
  loading: "var(--accent-soft)",
  compiling: "var(--chart-gray-2)",
  inferring: "var(--accent-3)",
};
const POINT_COLORS = ["var(--error)", "var(--warn)", "var(--accent-2)", "var(--chart-gray)", "var(--chart-gray)"];
const LEGEND = [
  { key: "loading", label: "모델 적재", color: PHASE_COLORS.loading },
  { key: "compiling", label: "컴파일", color: PHASE_COLORS.compiling },
  { key: "inferring", label: "추론", color: PHASE_COLORS.inferring },
  { key: "high", label: "높음", color: POINT_COLORS[0] },
  { key: "medium", label: "중간", color: POINT_COLORS[1] },
  { key: "low", label: "낮음", color: POINT_COLORS[2] },
  { key: "info", label: "정보·미분류", color: POINT_COLORS[3] },
];

function TimelinePlot({ data, events }: { data: PhaseTimelineResponse; events: RuntimeEvent[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(700);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const resize = () => setWidth(node.getBoundingClientRect().width || 700);
    resize();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(resize);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const entries = data.entries;
  const starts = entries.map((entry) => Date.parse(entry.from));
  const ends = entries.map((entry) => entry.until ? Date.parse(entry.until) : Date.now());
  const start = Math.min(...starts);
  const end = Math.max(...ends, start + 1000);
  const x = (time: number) => 8 + Math.max(0, Math.min(1, (time - start) / (end - start))) * (width - 16);
  const points = events.filter((event) => event.source === "tetragon" && event.observation && event.occurred_at && Date.parse(event.occurred_at) >= start && Date.parse(event.occurred_at) <= end);

  return <>
    <div ref={ref} className={s.phasePlot}>
      <svg className={s.phaseSvg} viewBox={`0 0 ${width} 74`} role="img" aria-label={`엔진 실행 단계 ${entries.length}개와 실행 기록 ${points.length}건`}>
        <rect x="8" y="16" width={width - 16} height="26" rx="3" fill="var(--surface-2)" />
        {entries.map((entry, index) => {
          const left = x(Date.parse(entry.from));
          const right = x(entry.until ? Date.parse(entry.until) : end);
          return <rect key={index} x={left} y="16" width={Math.max(2, right - left)} height="26" rx="3" fill={PHASE_COLORS[entry.phase] ?? "var(--chart-gray-2)"}>
            <title>{PHASE[entry.phase] ?? "단계 확인 필요"} · {formatTime(entry.from)} ~ {formatTime(entry.until)}</title>
          </rect>;
        })}
        {points.map((event, index) => {
          const observation = event.observation!;
          const severity = alertSeverity(observation.exec_verdict, observation.rule_id, event.source);
          const record = event.native.record as Record<string, unknown>;
          const parent = typeof record.parent_check === "string" ? PARENT_CHECK[record.parent_check] ?? "부모 확인 필요" : "부모 확인 필요";
          return <circle key={event.event_id} cx={x(Date.parse(event.occurred_at!))} cy={55 + (index % 3) * 5} r="5" fill={POINT_COLORS[SEVERITY_ORDER[severity]]} stroke="var(--surface)" strokeWidth="1.5">
            <title>{severity} · {observation.binary ?? observation.path ?? "실행 파일 미확인"} · {observation.exec_verdict ? EXEC_VERDICT[observation.exec_verdict] ?? "판정 확인 필요" : "판정 확인 필요"} · {parent}</title>
          </circle>;
        })}
      </svg>
      <div className={s.phaseLabels} aria-hidden="true">
        {entries.map((entry, index) => {
          const left = x(Date.parse(entry.from));
          const right = x(entry.until ? Date.parse(entry.until) : end);
          return right - left >= 72 ? <span key={index} className={s.phaseLabel} style={{ left, width: right - left }}>{PHASE[entry.phase] ?? "기타 단계"}</span> : null;
        })}
      </div>
    </div>
    <div className={s.phaseAxis}><span>{formatTime(new Date(start).toISOString())}</span><span>{formatTime(new Date(end).toISOString())}</span></div>
    <Legend items={LEGEND} />
    {points.length === 0 && <p className="small muted">해당 구간 프로세스 이벤트 없음. <Link to="/runtime/observe/tetragon">프로세스·파일 이벤트 보기</Link></p>}
  </>;
}

export function PhaseTimelinePage() {
  const timeline = usePhaseTimeline();
  const events = useRuntimeEvents();
  return <div className="page">
    <PageHead title="엔진 실행 단계" />
    <Subtabs tabs={[
      { to: "/runtime/observe/collectors", label: "수집 상태", active: false },
      { to: "/runtime/observe/hubble", label: "네트워크 이벤트 (Hubble)", active: false },
      { to: "/runtime/observe/tetragon", label: "프로세스·파일 이벤트 (Tetragon)", active: false },
      { to: "/runtime/observe/phases", label: "엔진 실행 단계", active: true },
    ]} />
    <Query q={timeline}>{(data) => <>
      <p className="small muted">엔진 실행 단계별 프로세스 판정 기준.{data.entries.some((entry) => entry.basis === "engine_signal") ? " 단계 근거 미검증 (엔진 로그)." : ""}</p>
      <div className="row wrap">
        {data.data_origin === "synthetic" && <OriginBadge origin="EXAMPLE" detail="synthetic" />}
        <Badge tone={data.collector.state === "running" && data.collector.fresh ? "accent" : data.collector.state === "example" ? "warn" : "muted"}>
          {data.collector.state === "running" && data.collector.fresh ? "수집 정상" : data.collector.state === "example" ? "예시 시간표" : data.collector.state === "stale" ? "수집 지연" : data.collector.state === "missing" ? "시간표 없음" : "수집 확인 필요"}
        </Badge>
        <span className="small muted">마지막 기록 {formatTime(data.collector.written_at)}</span>
      </div>
      {data.entries.length ? <ChartCard title="단계와 실행 기록">
        <TimelinePlot data={data} events={events.data?.items ?? []} />
      </ChartCard> : <StateView kind="empty" detail={data.collector.state === "missing" ? "단계 시간표 미수집 상태입니다. 엔진 로그 수집 설정을 확인하세요." : "단계 구간 미수집 상태입니다. 수집 상태를 확인하세요."} />}
      {data.extraction.limitations.length > 0 && <details><summary>자료의 한계</summary><ul>{data.extraction.limitations.map((value, index) => <li key={index}>{value}</li>)}</ul></details>}
    </>}</Query>
  </div>;
}
