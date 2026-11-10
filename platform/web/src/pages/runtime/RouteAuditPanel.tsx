import { useQuery } from "@tanstack/react-query";
import { getJson } from "../../api/client";
import { RUNTIME_REFRESH_MS } from "../../api/hooks";
import { Query } from "../../components/Misc";
import { TableCard, formatTime, rt } from "./parts";

type Audit = {
  state: "ok" | "not_configured" | "unsupported" | "invalid";
  item: null | {
    generated_at: string;
    status: { state: string; attempted_count: number; recorded_count: number; overflow_count: number; write_failure_count: number; last_write_error_class: string | null; last_write_failure_at: string | null };
    window: { returned: number; truncated: boolean; corrupt_lines: number; read_error_class: string | null; scanned_bytes: number; max_bytes: number };
    items: { event_id?: string; request_id: string; recorded_at: string; method: string; path: string; response_code: number; reason: string; scope: Record<string, string | null>; actor: { state: "unattributed" } }[];
  };
};

export function RouteAuditPanel() {
  const query = useQuery({ queryKey: ["runtime", "route-audit"], queryFn: () => getJson<Audit>("/api/runtime/route-audit?limit=100"), refetchInterval: RUNTIME_REFRESH_MS });
  return <Query q={query}>{(result) => <TableCard title="지원하지 않는 경로의 접근 감사">
    {!result.item ? <p className={rt.empty}>{result.state === "not_configured" ? "실제 runtime 감사 연결이 설정되지 않았습니다." : result.state === "unsupported" ? "연결된 runtime이 감사 조회를 지원하지 않습니다." : "감사 자료 형식을 확인하지 못했습니다."}</p> : <>
      <p className="small" style={{padding: "8px 16px"}}>현재 프로세스: 시도 {result.item.status.attempted_count} · 저장 {result.item.status.recorded_count} · 기록 한도 초과 {result.item.status.overflow_count} · 저장 실패 {result.item.status.write_failure_count}<br />
        최근 파일 창 {result.item.window.returned}건 · 전체 건수 미집계{result.item.window.truncated ? " · 조회 범위 잘림" : ""} · 손상 줄 {result.item.window.corrupt_lines} · 읽은 범위 {result.item.window.scanned_bytes}/{result.item.window.max_bytes} bytes
        {result.item.status.last_write_error_class && <><br />최근 저장 오류 {result.item.status.last_write_error_class} · {formatTime(result.item.status.last_write_failure_at)}</>}
        {result.item.window.read_error_class && <><br />조회 오류 {result.item.window.read_error_class}</>}
      </p>
      <table className={rt.statusTable}><thead><tr><th>시각·실행</th><th>경로</th><th>결과</th><th>대상·주체</th></tr></thead><tbody>
        {result.item.items.map((entry) => <tr key={entry.event_id ?? `${entry.request_id}:${entry.recorded_at}`}>
          <td>{formatTime(entry.recorded_at)}<br />{entry.scope.environment ?? "환경 미귀속"} · {entry.scope.run_id ?? "실행 미귀속"}</td>
          <td className="mono">{entry.method} {entry.path}</td><td>{entry.response_code} · {entry.reason}</td>
          <td>{entry.scope.target_id ?? "대상 미귀속"}<br />주체 미귀속<br /><span className="mono">{entry.event_id ?? entry.request_id}</span></td>
        </tr>)}
      </tbody></table>
      {result.item.items.length === 0 && <p className={rt.empty}>현재 파일 창에 저장된 경로 거부 기록이 없습니다. 위 저장 실패·한도 초과·조회 오류를 함께 확인하세요.</p>}
    </>}
  </TableCard>}</Query>;
}
