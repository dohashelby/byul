// 필터·기간·열린 상세를 쿼리스트링에 둔다. 새로고침해도 같은 화면이 나온다.
import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { resolveRange, type PeriodKey, type Range } from "./time";

export function useParam(name: string): [string | null, (v: string | null) => void] {
  const [params, setParams] = useSearchParams();
  const value = params.get(name);
  const set = useCallback(
    (v: string | null) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (v == null || v === "") next.delete(name);
          else next.set(name, v);
          return next;
        },
        { replace: true },
      ),
    [name, setParams],
  );
  return [value, set];
}

export function useParams(): [URLSearchParams, (patch: Record<string, string | null>) => void] {
  const [params, setParams] = useSearchParams();
  const patch = useCallback(
    (p: Record<string, string | null>) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(p)) {
            if (v == null || v === "") next.delete(k);
            else next.set(k, v);
          }
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );
  return [params, patch];
}

const PERIOD_KEYS: PeriodKey[] = ["1h", "24h", "7d", "all", "custom"];

/** 상단바의 기간·환경 선택. 기본 기간은 전체(예시 자료의 시각이 고정돼 있어서). */
export function useGlobalFilters(): {
  period: PeriodKey;
  from: string | null;
  to: string | null;
  env: string | null;
  range: Range;
} {
  const [params] = useSearchParams();
  const raw = params.get("period") as PeriodKey | null;
  const period: PeriodKey = raw && PERIOD_KEYS.includes(raw) ? raw : "all";
  const from = params.get("from");
  const to = params.get("to");
  const env = params.get("env");
  const range = useMemo(() => resolveRange(period, from, to), [period, from, to]);
  return { period, from, to, env, range };
}

/** 다른 탭으로 갈 때 기간·환경 선택을 이어 간다. */
export function carry(params: URLSearchParams, extra: Record<string, string> = {}): string {
  const next = new URLSearchParams();
  for (const k of ["period", "from", "to", "env"]) {
    const v = params.get(k);
    if (v) next.set(k, v);
  }
  for (const [k, v] of Object.entries(extra)) next.set(k, v);
  const s = next.toString();
  return s ? `?${s}` : "";
}
