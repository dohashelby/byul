// 그래프. serving-runtime 대시보드(Chart.js)와 같은 모양을 recharts로 그린다:
// 탭이 있는 카드, 큰 숫자, x축 격자 없음, 둥근 사각형 범례, surface-2 툴팁.
import { useState, type ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Bucket } from "../lib/time";
import { PERIODS, formatLocal } from "../lib/time";
import { useGlobalFilters, useParams } from "../lib/url";
import { Legend } from "./Misc";
import s from "./ui.module.css";

export type Series<K extends string> = { key: K; label: string; color: string; pattern?: boolean };

const AXIS = { fill: "var(--muted)", fontSize: 12 };

/** serving-runtime의 .chart-card: 제목, 탭, 큰 숫자, 그래프. */
export function ChartCard({
  title,
  tabs,
  active,
  onTab,
  total,
  note,
  children,
  footer,
}: {
  title: string;
  tabs?: { id: string; label: string }[];
  active?: string;
  onTab?: (id: string) => void;
  total?: ReactNode;
  note?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <article className={`card ${s.chartCard}`}>
      <h3 className={s.chartHead}>{title}</h3>
      {tabs && (
        <div className={s.cardTabs} role="tablist" aria-label={title}>
          {tabs.map((t) => (
            <button key={t.id} type="button" role="tab" className={s.cardTab} aria-selected={t.id === active} onClick={() => onTab?.(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
      )}
      {total !== undefined && (
        <p className={s.bigNumber}>
          <span>{total}</span>
          {note && <span className={s.bigCaption}>{note}</span>}
        </p>
      )}
      {children}
      {footer}
    </article>
  );
}

type TooltipPayload = { dataKey?: string | number; value?: number; payload?: { start: number } };

function TimeTooltip<K extends string>({ active, payload, series, size }: { active?: boolean; payload?: TooltipPayload[]; series: Series<K>[]; size: number }) {
  if (!active || !payload?.length) return null;
  const start = payload[0]?.payload?.start ?? 0;
  return (
    <div className={s.tooltip}>
      <div style={{ marginBottom: 4, fontWeight: 600 }}>
        {formatLocal(new Date(start).toISOString())} ~ {formatLocal(new Date(start + size).toISOString()).slice(11)}
      </div>
      <table>
        <tbody>
          {series.map((ser) => {
            const p = payload.find((x) => x.dataKey === ser.key);
            if (!p) return null;
            return (
              <tr key={ser.key}>
                <td>
                  <span className={s.legendSwatch} style={{ background: ser.color }} />
                  {ser.label}
                </td>
                <td className={s.num}>{p.value ?? 0}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** 그릴 기록이 없을 때. 기간이 걸려 있으면 그 기간을 보여주고 전체로 되돌릴 수 있게 한다. */
function EmptyChart({ height }: { height: number }) {
  const { period, from, to } = useGlobalFilters();
  const [, patch] = useParams();
  const range =
    period === "custom"
      ? `${from ? formatLocal(from) : "처음"} ~ ${to ? formatLocal(to) : "지금"}`
      : PERIODS.find((p) => p.key === period)?.label;
  return (
    <div className={s.empty} style={{ height, display: "flex", flexDirection: "column", justifyContent: "center", gap: 8, padding: 0 }}>
      <span>{period === "all" ? "기록이 없습니다" : `선택한 기간(${range})에 기록이 없습니다`}</span>
      {period !== "all" && (
        <span>
          <button type="button" className="btn btn-small" onClick={() => patch({ period: null, from: null, to: null })}>
            기간 전체로 보기
          </button>
        </span>
      )}
    </div>
  );
}

const empty = (height: number) => <EmptyChart height={height} />;

type ChartEvent = { activeLabel?: string | number; activePayload?: { payload: { start: number } }[] } | null;

/** 시간대별 쌓은 막대. 구간을 드래그하면 그 구간으로 필터를 건다. */
export function TimeBars<K extends string>({
  buckets,
  size,
  series,
  height = 240,
  onSelectRange,
}: {
  buckets: Bucket<K>[];
  size: number;
  series: Series<K>[];
  height?: number;
  onSelectRange?: (from: number, to: number) => void;
}) {
  const [drag, setDrag] = useState<{ a: number; b: number } | null>(null);
  const total = buckets.reduce((acc, b) => acc + series.reduce((x, ser) => x + (b[ser.key] as number), 0), 0);
  if (buckets.length === 0 || total === 0) return empty(height);
  const startOf = (e: ChartEvent): number | null => {
    const p = e?.activePayload?.[0]?.payload?.start;
    if (typeof p === "number") return p;
    const hit = buckets.find((b) => b.label === e?.activeLabel);
    return hit ? hit.start : null;
  };
  const patterns = series.filter((x) => x.pattern);
  return (
    <>
      <div className={s.chartBox} style={{ height, userSelect: "none" }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={buckets}
            margin={{ top: 4, right: 4, bottom: 0, left: -20 }}
            onMouseDown={(e: ChartEvent) => {
              const t = startOf(e);
              if (t != null && onSelectRange) setDrag({ a: t, b: t });
            }}
            onMouseMove={(e: ChartEvent) => {
              const t = startOf(e);
              if (drag && t != null) setDrag({ ...drag, b: t });
            }}
            onMouseUp={() => {
              if (drag && onSelectRange) onSelectRange(Math.min(drag.a, drag.b), Math.max(drag.a, drag.b) + size);
              setDrag(null);
            }}
          >
            <defs>
              {patterns.map((x) => (
                <pattern key={x.key} id={`hatch-${x.key}`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <rect width="6" height="6" fill={x.color} fillOpacity={0.3} />
                  <line x1="0" y1="0" x2="0" y2="6" stroke={x.color} strokeWidth="3" />
                </pattern>
              ))}
            </defs>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={{ stroke: "var(--grid)" }} minTickGap={24} />
            <YAxis allowDecimals={false} tick={AXIS} tickLine={false} axisLine={false} />
            <Tooltip content={<TimeTooltip series={series} size={size} />} cursor={{ fill: "var(--surface-2)" }} isAnimationActive={false} />
            {series.map((x) => (
              <Bar key={x.key} dataKey={x.key} stackId="a" fill={x.pattern ? `url(#hatch-${x.key})` : x.color} isAnimationActive={false} />
            ))}
            {drag && drag.a !== drag.b && (
              <ReferenceArea
                x1={buckets.find((b) => b.start === Math.min(drag.a, drag.b))?.label}
                x2={buckets.find((b) => b.start === Math.max(drag.a, drag.b))?.label}
                fill="var(--accent)"
                fillOpacity={0.12}
              />
            )}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div style={{ marginTop: 8 }}>
        <Legend items={series.map((x) => ({ key: x.key, label: x.label, color: x.color, pattern: x.pattern }))} />
      </div>
    </>
  );
}

/** 시간대별 합계 선(면 채움). serving-runtime "시간별 요청 · 전체" 탭과 같은 모양. */
export function TimeLine<K extends string>({ buckets, size, keys, height = 240 }: { buckets: Bucket<K>[]; size: number; keys: readonly K[]; height?: number }) {
  const data = buckets.map((b) => ({ start: b.start, label: b.label, total: keys.reduce((a, k) => a + (b[k] as number), 0) }));
  if (data.every((d) => d.total === 0)) return empty(height);
  return (
    <div className={s.chartBox} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={{ stroke: "var(--grid)" }} minTickGap={24} />
          <YAxis allowDecimals={false} tick={AXIS} tickLine={false} axisLine={false} />
          <Tooltip content={<TimeTooltip series={[{ key: "total", label: "요청", color: "var(--accent)" }]} size={size} />} cursor={{ stroke: "var(--border)" }} isAnimationActive={false} />
          <Area type="monotone" dataKey="total" stroke="var(--accent)" strokeWidth={2} fill="var(--accent-soft)" fillOpacity={1} dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** 구성 비율 도넛 (cutout 68%, 오른쪽 범례). */
export function Donut({ items, height = 240 }: { items: { key: string; label: string; value: number; color: string }[]; height?: number }) {
  if (items.every((i) => i.value === 0)) return empty(height);
  return (
    <div className="row" style={{ height, alignItems: "center" }}>
      <div className={s.chartBox} style={{ height, flex: 1, minWidth: 0 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={items} dataKey="value" nameKey="label" innerRadius="68%" outerRadius="100%" startAngle={90} endAngle={-270} stroke="var(--surface)" strokeWidth={2} isAnimationActive={false}>
              {items.map((i) => (
                <Cell key={i.key} fill={i.color} />
              ))}
            </Pie>
            <Tooltip
              isAnimationActive={false}
              content={({ active, payload }) =>
                active && payload?.length ? (
                  <div className={s.tooltip}>
                    {String(payload[0].name)} {String(payload[0].value)}
                  </div>
                ) : null
              }
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="stack" style={{ gap: 6, flex: "none" }}>
        {items.map((i) => (
          <span key={i.key} className="small muted nowrap">
            <span className={s.legendSwatch} style={{ background: i.color, width: 22 }} />
            {i.label}
            <span style={{ marginLeft: 8, color: "var(--text-2)" }}>{i.value}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/** 가로 막대(규칙별 건수). */
export function HBars({ rows, height = 240 }: { rows: [string, number][]; height?: number }) {
  if (rows.length === 0) return empty(height);
  const data = rows.map(([name, value]) => ({ name, value }));
  return (
    <div className={s.chartBox} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid horizontal={false} stroke="var(--grid)" />
          <XAxis type="number" allowDecimals={false} tick={AXIS} tickLine={false} axisLine={false} />
          <YAxis type="category" dataKey="name" tick={{ ...AXIS, fill: "var(--text-2)" }} tickLine={false} axisLine={false} width={130} />
          <Tooltip cursor={{ fill: "var(--surface-2)" }} isAnimationActive={false} content={({ active, payload }) => (active && payload?.length ? <div className={s.tooltip}>{String(payload[0].payload.name)} {String(payload[0].value)}</div> : null)} />
          <Bar dataKey="value" fill="var(--accent)" barSize={14} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// 요청 결과 계열 — serving-runtime과 같은 순서·색 (허용 강조색, 거부·입력 오류는 보라 단계, 오류만 빨강)
export const REQUEST_SERIES = [
  { key: "allowed", label: "허용", color: "var(--viz-allowed)" },
  { key: "denied", label: "정책 거부", color: "var(--viz-denied)" },
  { key: "invalid", label: "입력 오류", color: "var(--viz-invalid)" },
  { key: "engine_rejected", label: "엔진 거부", color: "var(--viz-engine-rejected)" },
  { key: "error", label: "처리 오류", color: "var(--viz-error)" },
  { key: "unknown", label: "미완료", color: "var(--viz-unknown)" },
] as const;

export type RequestKey = (typeof REQUEST_SERIES)[number]["key"];

// 사건 계열 — 거부 확인은 통제 결과 강조색, 탐지는 주황, 집행 시도는 주황 빗금(탐지와 구분), 나머지는 회색
export const EVENT_SERIES = [
  { key: "denied", label: "거부 확인", color: "var(--accent)" },
  { key: "detected", label: "탐지", color: "var(--viz-detected)" },
  { key: "enforce_attempted", label: "집행 시도", color: "var(--viz-enforce)", pattern: true },
  { key: "other", label: "기타(관측·허용 등)", color: "var(--viz-other)" },
] as const;

export type EventKey = (typeof EVENT_SERIES)[number]["key"];

export function eventKey(outcome: string): EventKey {
  return outcome === "detected" || outcome === "enforce_attempted" || outcome === "denied" ? outcome : "other";
}
