import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import type { InvalidItem } from "../api/types";
import { JsonView } from "./JsonView";
import s from "./ui.module.css";

/** serving-runtime의 요약 카드: 제목, 큰 숫자 + 설명, 그 아래 내용. */
export function MetricCard({
  title,
  value,
  caption,
  children,
  onClick,
  label,
  dot,
}: {
  title: ReactNode;
  value?: ReactNode;
  caption?: ReactNode;
  children?: ReactNode;
  onClick?: () => void;
  label?: string;
  dot?: "ok" | "bad" | "warn" | "none";
}) {
  const body = (
    <>
      <span className={s.metricTitle}>
        {dot && <Dot state={dot} />}
        {title}
      </span>
      {value !== undefined && (
        <span className={s.bigNumber}>
          <span>{value}</span>
          {caption && <span className={s.bigCaption}>{caption}</span>}
        </span>
      )}
      {children && <div className={s.metricBody}>{children}</div>}
    </>
  );
  return onClick ? (
    <button type="button" className={s.metric} onClick={onClick} aria-label={label}>
      {body}
    </button>
  ) : (
    <article className={s.metric}>{body}</article>
  );
}

export function Dot({ state }: { state: "ok" | "bad" | "warn" | "none" }) {
  const cls = state === "ok" ? s.dotOk : state === "bad" ? s.dotBad : state === "warn" ? s.dotWarn : "";
  return <span className={`${s.dot} ${cls}`} aria-hidden />;
}

/** serving-runtime의 .section-head: 큰 제목과 오른쪽 도구. */
export function PageHead({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={s.sectionHead}>
      <h2>{title}</h2>
      {children}
    </div>
  );
}

export function WarningBox({ children }: { children: ReactNode }) {
  return (
    <p className={s.warningBox} role="alert">
      {children}
    </p>
  );
}

/** 결과 필터 버튼 줄 (serving-runtime .filters). */
export function Segmented({ value, options, onChange }: { value: string | null; options: { value: string; label: string }[]; onChange: (v: string | null) => void }) {
  return (
    <div className={s.filterBar} role="group">
      <button type="button" className={s.segment} aria-pressed={!value} onClick={() => onChange(null)}>
        전체
      </button>
      {options.map((o) => (
        <button key={o.value} type="button" className={s.segment} aria-pressed={value === o.value} onClick={() => onChange(value === o.value ? null : o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Cards({ children, grid }: { children: ReactNode; grid?: boolean }) {
  return <div className={grid ? s.cardGrid : s.cards}>{children}</div>;
}

export function Banner({ kind, children }: { kind: "example" | "unofficial"; children: ReactNode }) {
  return (
    <div className={`${s.banner} ${kind === "example" ? s.bannerExample : s.bannerUnofficial}`} role="status">
      {children}
    </div>
  );
}

/** 빈 화면·연결 끊김·스키마 검증 실패·그 밖의 오류를 각각 다른 문구로 보여준다. */
export function StateView({ kind, detail }: { kind: "empty" | "disconnected" | "unauthorized" | "invalid" | "not_found" | "error" | "loading"; detail?: ReactNode }) {
  const text: Record<typeof kind, [string, string]> = {
    loading: ["불러오는 중", ""],
    empty: ["데이터가 없습니다", "선택한 기간·필터에 맞는 항목이 없습니다."],
    disconnected: ["runtime 연결 끊김", "runtime 조회 API에 연결하지 못했습니다. RUNTIME_BASE_URL과 runtime 실행 상태를 확인하세요."],
    unauthorized: ["runtime 인증 거부", "runtime이 조회 토큰을 거부했습니다. RUNTIME_VIEWER_TOKEN을 확인하세요."],
    invalid: ["형식 오류", "받은 자료가 계약 스키마 검증을 통과하지 못했습니다."],
    not_found: ["찾을 수 없음", "해당 항목이 없습니다."],
    error: ["오류", "플랫폼 API 요청이 실패했습니다."],
  };
  const [title, body] = text[kind];
  const isError = kind !== "empty" && kind !== "loading";
  return (
    <div className={`${s.state} ${isError ? s.stateError : ""}`} role={isError ? "alert" : undefined}>
      <div className={s.stateTitle}>{title}</div>
      {body && <div className="small">{body}</div>}
      {detail && <div className="small muted">{detail}</div>}
    </div>
  );
}

export function errorKind(error: unknown) {
  if (error instanceof ApiError) {
    const k = error.kind;
    if (k === "network") return "error" as const;
    if (k === "server") return "error" as const;
    return k;
  }
  return "error" as const;
}

/** 조회 결과를 감싼다. 불러오는 중·오류면 상태 화면을, 아니면 children(data)을 그린다. */
export function Query<T>({ q, children }: { q: UseQueryResult<T>; children: (data: T) => ReactNode }) {
  if (q.isPending) return <StateView kind="loading" />;
  if (q.isError) {
    const e = q.error;
    return <StateView kind={errorKind(e)} detail={e instanceof ApiError ? `${e.code}: ${e.message}` : String(e)} />;
  }
  return <>{children(q.data as T)}</>;
}

/** 계약 검증에 실패한 항목. 버리지 않고 따로 보여준다. */
export function InvalidRows({ items, what }: { items: InvalidItem[] | null | undefined; what: string }) {
  if (!items || items.length === 0) return null;
  return (
    <details className="card card-pad">
      <summary>
        <strong style={{ color: "var(--error)" }}>형식 오류 {items.length}건</strong>
        <span className="muted"> · 계약 스키마 검증에 실패한 {what}입니다. 원본은 그대로 보존합니다.</span>
      </summary>
      <div className="stack" style={{ marginTop: 12 }}>
        {items.map((it) => (
          <div key={it.index} className="stack" style={{ gap: 6 }}>
            <div className="small">#{it.index}</div>
            <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
              {it.errors.map((e, i) => (
                <li key={i} className="mono">
                  {e}
                </li>
              ))}
            </ul>
            <JsonView value={it.raw} maxHeight={240} />
          </div>
        ))}
      </div>
    </details>
  );
}

export function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string | null;
  options: { value: string; label: string }[];
  onChange: (v: string | null) => void;
}) {
  return (
    <label className={`${s.filterField} ${value ? s.filterActive : ""}`}>
      <span className={s.filterKey}>{label}</span>
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">전체</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function FilterBar({ children }: { children: ReactNode }) {
  return <div className={s.filterBar}>{children}</div>;
}

export function Legend({ items }: { items: { key: string; label: string; color: string; pattern?: boolean }[] }) {
  return (
    <div className={s.legend}>
      {items.map((i) => (
        <span key={i.key}>
          <span
            className={s.legendSwatch}
            style={{
              background: i.pattern
                ? `repeating-linear-gradient(45deg, ${i.color} 0 2px, transparent 2px 4px)`
                : i.color,
              border: i.pattern ? `1px solid ${i.color}` : undefined,
            }}
          />
          {i.label}
        </span>
      ))}
    </div>
  );
}

export { s as ui };
