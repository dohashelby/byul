// runtime 화면 공용 부품. 삭제된 runtime 대시보드(static/app.js)의 같은 이름 함수들을 옮겼다.
import { Link, useSearchParams } from "react-router-dom";
import type { ReactNode } from "react";
import type { Collector } from "../../api/types";
import { Badge } from "../../components/Badges";
import { stateTone } from "../../lib/labels";
import { formatLocal } from "../../lib/time";
import { carry } from "../../lib/url";
import r from "./runtime.module.css";

/** runtime stateCell: 상태 값을 그대로 보여주는 배지(색은 runtime 규칙). */
export function StateBadge({ value }: { value: string | null | undefined }) {
  if (value == null) return <span className="muted">–</span>;
  return (
    <Badge tone={stateTone(value)} title={value}>
      {value}
    </Badge>
  );
}

/** runtime scopeBadges: 환경·실행 배지. 과거 기록에 없으면 미기록. */
export function ScopeBadges({ environment, runId }: { environment: string | null | undefined; runId: string | null | undefined }) {
  return (
    <span className={r.scopeBadges}>
      <Badge tone={environment ? "neutral" : "muted"} title={environment ?? undefined}>
        {environment ? `환경 ${environment}` : "환경 미기록"}
      </Badge>
      <Badge tone={runId ? "neutral" : "muted"} title={runId ?? undefined}>
        {runId ? `실행 ${runId.length > 18 ? `${runId.slice(0, 18)}…` : runId}` : "실행 미기록"}
      </Badge>
    </span>
  );
}

/** runtime observationBadge: 합성·실제·미확인. */
export function ObservationOriginBadge({ origin }: { origin: string | null | undefined }) {
  if (origin === "synthetic") return <Badge tone="warn">합성</Badge>;
  if (origin === "real") return <Badge tone="neutral">실제</Badge>;
  return <Badge tone="muted">{origin ?? "미확인"}</Badge>;
}

export function formatTime(iso: string | null | undefined): string {
  return iso ? formatLocal(iso) : "–";
}

export function Subtabs({ tabs }: { tabs: { to: string; label: string; count?: number | null; badge?: number; title?: string; active: boolean }[] }) {
  const [params] = useSearchParams();
  return (
    <div className={r.subtabs} role="tablist">
      {tabs.map((t) => (
        <Link key={t.to} to={`${t.to}${carry(params)}`} role="tab" aria-selected={t.active} className={`${r.subtab} ${t.active ? r.subtabActive : ""}`} title={t.title}>
          {t.label}
          {t.count != null && <span className={r.subtabCount}>{t.count}</span>}
          {t.badge ? <span className={r.subtabBadge}>{t.badge}</span> : null}
        </Link>
      ))}
    </div>
  );
}

export function TableCard({ title, extra, children, empty }: { title?: ReactNode; extra?: ReactNode; children: ReactNode; empty?: ReactNode }) {
  return (
    <article className={`card ${r.tableCard}`}>
      {title && <h3>{title}</h3>}
      {extra && <div className={r.pad}>{extra}</div>}
      {children}
      {empty}
    </article>
  );
}

/** runtime observationState: 관측 연결 칩의 문구와 모양. */
export function observationState(origins: Record<string, number> | undefined, collector: Collector | undefined): [string, "off" | "synthetic" | "real"] {
  const detail = collector?.detail && collector.detail.kind === "observation_feed" ? collector.detail : null;
  const writer = detail?.writer;
  if (collector && collector.state !== "ok") {
    if (writer && writer.configured === false) return [`관측 불가 (writer heartbeat 미설정 · ${collector.state})`, "off"];
    if ((collector.last_error ?? "").includes("heartbeat") || (collector.last_error ?? "").includes("writer")) return ["관측 불가 (writer heartbeat 오류)", "off"];
    return [`관측 불가 (${collector.state})`, "off"];
  }
  if (detail) {
    if (detail.loss_detected === true) return ["실시간 수집 · Hubble 손실 감지", "synthetic"];
    if (detail.rejected > 0 && detail.out_of_window === detail.rejected && writer?.configured) return ["수집 정상 · 실행 전 이벤트 제외", "real"];
    if (detail.rejected > 0 && detail.accepted === 0) return ["관측 불가 (손상 줄)", "off"];
    if (detail.rejected > 0) return ["실시간 수집 · 일부 제외", "synthetic"];
    if (collector?.state === "ok" && writer?.configured && detail.idle) return ["수집 정상 · 새 이벤트 없음", "real"];
    if (collector?.state === "ok" && writer?.configured) return ["실제 수집(실시간)", "real"];
  }
  if (collector?.state === "ok") return ["실시간 기록 · writer 생존 미확인", "off"];
  if ((origins?.real ?? 0) > 0) return ["실제 기록 · 실시간 미연결", "off"];
  if ((origins?.synthetic ?? 0) > 0) return ["합성 데이터", "synthetic"];
  return ["미연결", "off"];
}

export function ObservationChip({ label, state }: { label: string; state: [string, "off" | "synthetic" | "real"] }) {
  const cls = state[1] === "off" ? r.chipOff : state[1] === "synthetic" ? r.chipSynthetic : r.chipReal;
  return <span className={`${r.chip} ${cls}`}>{`${label} 관측 · ${state[0]}`}</span>;
}

export { r as rt };
