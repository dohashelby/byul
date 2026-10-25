import { useEffect, useState, type ReactNode } from "react";
import s from "./ui.module.css";

export type PanelTab = { id: string; label: string; content: ReactNode };

/** 상세 패널. 기본은 serving-runtime처럼 오른쪽에서 열리는 드로어이고,
 *  inline이면 3단 화면(사건 로그)의 오른쪽 칸에 그대로 놓인다. 닫을 수 있다. */
export function DetailPanel({
  title,
  subtitle,
  tabs,
  onClose,
  actions,
  inline,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  tabs: PanelTab[];
  onClose: () => void;
  actions?: ReactNode;
  inline?: boolean;
}) {
  const [active, setActive] = useState(tabs[0]?.id);
  const current = tabs.find((t) => t.id === active) ?? tabs[0];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const panel = (
    <aside className={`${s.panel} ${inline ? "" : s.drawer}`} aria-label="상세">
      <div className={s.panelHead}>
        <div className="grow">
          <h2 className={s.panelTitle}>{title}</h2>
          {subtitle && <div className="small muted">{subtitle}</div>}
        </div>
        {actions}
        <button type="button" className={s.iconButton} onClick={onClose} aria-label="상세 닫기">
          ×
        </button>
      </div>
      {tabs.length > 1 && (
        <div className={s.tabs} role="tablist">
          {tabs.map((t) => (
            <button key={t.id} type="button" role="tab" className={s.tab} aria-selected={t.id === current?.id} onClick={() => setActive(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
      )}
      <div className={s.panelBody} role="tabpanel">
        {current?.content}
      </div>
    </aside>
  );
  if (inline) return panel;
  return (
    <>
      <div className={s.backdrop} onClick={onClose} aria-hidden />
      {panel}
    </>
  );
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className={s.kv}>
      {items.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}
