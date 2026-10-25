// 상단바 환경 선택은 그 환경의 target_ref로 runtime 자료를 거른다(contracts의 연결 키).
import { useDocList } from "../api/hooks";
import { useGlobalFilters } from "./url";

export function useEnvTarget(): { env: string | null; targetRef: string | null } {
  const { env } = useGlobalFilters();
  const list = useDocList("environments");
  const targetRef = env ? (list.data?.items.find((e) => e.doc_id === env)?.target_ref ?? null) : null;
  return { env, targetRef };
}

/** 환경을 골랐는데 target_ref를 아직 모르면 아무것도 맞지 않는 것으로 본다. */
export function matchesTarget(value: string | null | undefined, env: string | null, targetRef: string | null): boolean {
  if (!env) return true;
  return targetRef != null && value === targetRef;
}

export function countBy<T>(items: T[], key: (t: T) => string | null | undefined): Map<string, number> {
  const m = new Map<string, number>();
  for (const it of items) {
    const k = key(it) ?? "∅";
    m.set(k, (m.get(k) ?? 0) + 1);
  }
  return m;
}

export function uniq(values: (string | null | undefined)[]): string[] {
  return [...new Set(values.filter((v): v is string => typeof v === "string"))].sort();
}
