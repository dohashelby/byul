// 현황 화면의 GPU 지표. 수집 중단(stale)·오류는 0이 아니라 관측 불가로 보인다.
import { useGpuStatus } from "../../api/hooks";
import { Dot } from "../../components/Misc";
import { fmt, gpuChipState } from "../../lib/gpu";
import p from "../pages.module.css";
import { formatTime } from "./parts";

export function GpuPanel() {
  const q = useGpuStatus();
  const gpu = q.data?.item;
  const [stateText, origin] = q.isError ? ["관측 불가 · GPU 상태 조회 실패", "off" as const] : gpuChipState(gpu);
  const dot = origin === "real" ? "ok" : !gpu || gpu.state === "not_configured" ? "none" : "bad";
  return (
    <section className="card card-pad stack" style={{ gap: 10 }} aria-label="GPU 상태">
      <div className="row" style={{ gap: 8, justifyContent: "space-between" }}>
        <h2 className="row" style={{ gap: 8, margin: 0, fontSize: 15 }}>
          <Dot state={dot} />
          GPU
        </h2>
        <span className="small muted">
          {stateText}
          {gpu?.collected_at ? ` · 수집 ${formatTime(gpu.collected_at)}` : ""}
        </span>
      </div>
      {gpu?.state === "stale" && (
        <p className="small" style={{ color: "var(--error)", margin: 0 }}>수집기가 멈춰 마지막 값이 오래됐습니다(현재 값 아님)</p>
      )}
      {gpu?.gpus && gpu.gpus.length > 0 ? (
        <table className={p.blockedTable}>
          <thead>
            <tr><th>#</th><th>모델</th><th>사용률</th><th>메모리</th><th>온도</th><th>전력</th></tr>
          </thead>
          <tbody>
            {gpu.gpus.map((g) => (
              <tr key={g.index} style={gpu.state === "stale" ? { opacity: 0.55 } : undefined}>
                <td>{g.index}</td>
                <td>{g.name}</td>
                <td>{fmt(g.utilization_percent, "%")}</td>
                <td>{`${fmt(g.memory_used_mib, "")} / ${fmt(g.memory_total_mib, " MiB")}`}</td>
                <td>{fmt(g.temperature_c, "°C")}</td>
                <td>{fmt(g.power_w, " W", 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="small muted" style={{ margin: 0 }}>
          {!gpu || gpu.state === "not_configured" ? "GPU 수집기가 연결되지 않았습니다(Gateway --gpu-state)." : "표시할 GPU 값이 없습니다."}
        </p>
      )}
    </section>
  );
}
