import type { RuntimeEventList } from "../../api/types";

const NAMES: Record<string, string> = { gateway: "Gateway 요청", hubble: "Hubble", tetragon: "Tetragon", management_listener: "관리 요청", engine_health: "엔진 상태" };

export function EventWindowNote({ data }: { data: Pick<RuntimeEventList, "truncated" | "source_windows"> | undefined }) {
  const windows = Object.entries(data?.source_windows ?? {});
  if (!windows.length) return data?.truncated ? <p className="notice">최근 사건 일부를 조회했습니다. 조회 범위의 사건 수를 표시합니다.</p> : null;
  return <p className="small muted" aria-label="사건 조회 범위">{windows.map(([source, w]) =>
    `${NAMES[source] ?? source} ${w.returned}건 조회 / 원천 전체 ${w.total ?? (w.total_lower_bound != null ? `최소 ${w.total_lower_bound}건 · 최근 조회 범위` : "미확인")}${w.limit ? ` · 조회 상한 ${w.limit}건` : ""}${w.truncated === true ? " · 일부만 표시" : w.truncated === null ? " · 잘림 여부 미확인" : ""}`
  ).join(" | ")} · 수집 공백·손실은 수집 상태에서 확인합니다.</p>;
}
