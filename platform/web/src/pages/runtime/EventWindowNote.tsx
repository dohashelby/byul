import type { RuntimeEventList } from "../../api/types";

const NAMES: Record<string, string> = { gateway: "Gateway 요청", hubble: "Hubble", tetragon: "Tetragon", management_listener: "관리 요청", engine_health: "엔진 상태" };

export function EventWindowNote({ data }: { data: Pick<RuntimeEventList, "truncated" | "source_windows"> | undefined }) {
  const windows = Object.entries(data?.source_windows ?? {});
  if (!windows.length) return data?.truncated ? <p className="notice">일부 기록만 표시합니다.</p> : null;
  const partial = data?.truncated || windows.some(([, w]) => w.truncated === true);
  const uncertain = windows.some(([, w]) => w.truncated == null);
  return <>
    {partial && <p className="notice">일부 기록만 표시합니다.</p>}
    {!partial && uncertain && <p className="notice">조회 결과가 전체인지 확인할 수 없습니다.</p>}
    <details className="small muted"><summary>조회 범위</summary><p aria-label="사건 조회 범위">{windows.map(([source, w]) =>
    `${NAMES[source] ?? source} ${w.returned}건 조회 / 원천 전체 ${w.total ?? (w.total_lower_bound != null ? `최소 ${w.total_lower_bound}건 · 최근 조회 범위` : "미확인")}${w.limit ? ` · 조회 상한 ${w.limit}건` : ""}${w.truncated === true ? " · 일부만 표시" : w.truncated === null ? " · 잘림 여부 미확인" : ""}`
  ).join(" | ")} · 수집 공백·손실은 수집 상태에서 확인합니다.</p></details>
  </>;
}
