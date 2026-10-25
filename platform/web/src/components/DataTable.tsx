import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import s from "./ui.module.css";

export type Column<T> = {
  id: string;
  header: string;
  cell: (row: T) => ReactNode;
  /** 정렬 값. 없으면 이 열은 정렬하지 않는다. null은 항상 뒤로 간다. */
  sort?: (row: T) => string | number | boolean | null | undefined;
  hidden?: boolean;
};

type Props<T> = {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  selectedKey?: string | null;
  initialSort?: { id: string; dir: "asc" | "desc" };
  pageSizes?: number[];
  storageKey?: string;
  empty?: ReactNode;
  toolbar?: ReactNode;
};

function readHidden(key: string | undefined, fallback: string[]): string[] {
  if (!key) return fallback;
  try {
    const raw = localStorage.getItem(`cols:${key}`);
    return raw ? (JSON.parse(raw) as string[]) : fallback;
  } catch {
    return fallback;
  }
}

/** 정렬·열 숨기기·페이지 크기·페이지 이동을 지원하는 표. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  selectedKey,
  initialSort,
  pageSizes = [25, 50, 100],
  storageKey,
  empty,
  toolbar,
}: Props<T>) {
  const [sort, setSort] = useState(initialSort ?? null);
  const [hidden, setHidden] = useState<string[]>(() =>
    readHidden(storageKey, columns.filter((c) => c.hidden).map((c) => c.id)),
  );
  const [pageSize, setPageSize] = useState(pageSizes[0]);
  const [page, setPage] = useState(0);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!storageKey) return;
    try {
      localStorage.setItem(`cols:${storageKey}`, JSON.stringify(hidden));
    } catch {
      /* 저장 못 해도 화면은 동작한다 */
    }
  }, [hidden, storageKey]);

  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menu]);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.id === sort.id);
    if (!col?.sort) return rows;
    const get = col.sort;
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const x = get(a);
      const y = get(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return x < y ? -dir : x > y ? dir : 0;
    });
  }, [rows, sort, columns]);

  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const current = Math.min(page, pages - 1);
  const visible = sorted.slice(current * pageSize, current * pageSize + pageSize);
  const shown = columns.filter((c) => !hidden.includes(c.id));

  const toggleSort = (c: Column<T>) => {
    if (!c.sort) return;
    setSort((prev) => (prev?.id === c.id ? { id: c.id, dir: prev.dir === "asc" ? "desc" : "asc" } : { id: c.id, dir: "desc" }));
  };

  return (
    <div className="stack" style={{ gap: 8, minWidth: 0 }}>
      <div className="row">
        <div className="grow row wrap">{toolbar}</div>
        <div className={s.colMenu} ref={menuRef}>
          <button className="btn btn-small" type="button" onClick={() => setMenu((m) => !m)} aria-expanded={menu}>
            열 {shown.length}/{columns.length}
          </button>
          {menu && (
            <div className={s.colMenuList} role="menu">
              {columns.map((c) => (
                <label key={c.id}>
                  <input
                    type="checkbox"
                    checked={!hidden.includes(c.id)}
                    onChange={(e) => setHidden((h) => (e.target.checked ? h.filter((x) => x !== c.id) : [...h, c.id]))}
                  />
                  {c.header}
                </label>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className={s.tableWrap}>
        <table className={`${s.table} ${onRowClick ? s.clickable : ""}`}>
          <thead>
            <tr>
              {shown.map((c) => (
                <th key={c.id} aria-sort={sort?.id === c.id ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}>
                  {c.sort ? (
                    <button type="button" onClick={() => toggleSort(c)}>
                      {c.header}
                      {sort?.id === c.id ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr>
                <td colSpan={shown.length || 1}>{empty ?? <span className="muted">항목이 없습니다</span>}</td>
              </tr>
            ) : (
              visible.map((row) => {
                const key = rowKey(row);
                return (
                  <tr
                    key={key}
                    className={selectedKey === key ? s.selected : undefined}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    tabIndex={onRowClick ? 0 : undefined}
                    onKeyDown={onRowClick ? (e) => e.key === "Enter" && onRowClick(row) : undefined}
                  >
                    {shown.map((c) => (
                      <td key={c.id}>{c.cell(row)}</td>
                    ))}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <div className={s.tableFoot}>
        <span>
          {sorted.length}건 중 {sorted.length === 0 ? 0 : current * pageSize + 1}–{Math.min((current + 1) * pageSize, sorted.length)}
        </span>
        <div className="row">
          <label className="row small">
            페이지 크기
            <select className="select" value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}>
              {pageSizes.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <button className="btn btn-small" type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>
            이전
          </button>
          <span>
            {current + 1} / {pages}
          </span>
          <button className="btn btn-small" type="button" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>
            다음
          </button>
        </div>
      </div>
    </div>
  );
}
