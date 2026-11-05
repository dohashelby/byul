import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import s from "./ui.module.css";

export const PAGE_SIZE = 25;

/** URL의 페이지를 유지하고, 페이지 이외의 필터가 바뀌면 첫 페이지로 돌아간다. */
export function usePagination(total: number, param = "page", pageSize = PAGE_SIZE) {
  const [params, setParams] = useSearchParams();
  const filterKey = [...params.entries()]
    .filter(([key]) => !/^page(?:_|$)/.test(key) && key !== "sel")
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => `${key}=${value}`)
    .join("&");
  const previousFilter = useRef(filterKey);
  const raw = params.get(param);
  const requested = raw && /^[1-9]\d*$/.test(raw) ? Number(raw) : 1;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(Number.isSafeInteger(requested) ? requested : 1, pages);
  const from = total ? (page - 1) * pageSize + 1 : 0;
  const to = Math.min(page * pageSize, total);
  const setPage = (nextPage: number) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (nextPage <= 1) next.delete(param);
    else next.set(param, String(nextPage));
    return next;
  }, { replace: true });

  useEffect(() => {
    if (previousFilter.current !== filterKey) {
      previousFilter.current = filterKey;
      setPage(1);
    }
  }, [filterKey]);

  return { page, pages, from, to, start: from ? from - 1 : 0, end: to, total, setPage };
}

export type PaginationState = ReturnType<typeof usePagination>;

export function Pagination({ state }: { state: PaginationState }) {
  const { page, pages, from, to, total, setPage } = state;
  if (pages <= 1) return null;
  const numbers = [...new Set([1, page - 1, page, page + 1, pages].filter((n) => n >= 1 && n <= pages))].sort((a, b) => a - b);
  return <nav className={s.tableFoot} aria-label="페이지 이동">
    <span>{from}–{to} / {total}</span>
    <div className={s.pageButtons}>
      <button className="btn btn-small" type="button" disabled={page === 1} onClick={() => setPage(page - 1)}>이전</button>
      {numbers.map((n, i) => <span key={n} className={s.pageNumber}>
        {i > 0 && n > numbers[i - 1] + 1 && <span aria-hidden="true">…</span>}
        <button className="btn btn-small" type="button" aria-label={`${n}페이지`} aria-current={n === page ? "page" : undefined} onClick={() => setPage(n)}>{n}</button>
      </span>)}
      <button className="btn btn-small" type="button" disabled={page === pages} onClick={() => setPage(page + 1)}>다음</button>
    </div>
  </nav>;
}
