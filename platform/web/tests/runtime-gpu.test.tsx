import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RuntimeStatus } from "../src/api/types";
import { gpuChipState } from "../src/lib/gpu";
import { RuntimeOverviewPage } from "../src/pages/runtime/RuntimeOverviewPage";
import { SOURCE, mockApi, renderAt } from "./helpers";

const STATUS = Object.values(
  import.meta.glob("../../../contracts/examples/runtime-status.json", { eager: true, import: "default" }),
)[0] as RuntimeStatus;
const L4 = { index: 0, name: "NVIDIA L4", utilization_percent: 37, memory_used_mib: 18120, memory_total_mib: 23034, temperature_c: 54, power_w: 71.5 };

function routes(gpu: unknown) {
  return {
    "/api/runtime/status": { item: STATUS, invalid: null, source: SOURCE },
    "/api/runtime/summary": { counts: { allowed: 0, denied: 0, invalid: 0, engine_rejected: 0, error: 0, unknown: 0 }, total: 0, corrupt_lines: 0, generated_at: STATUS.generated_at, source: SOURCE },
    "/api/runtime/requests": { items: [], invalid: [], source: SOURCE, truncated: false },
    "/api/runtime/events": { items: [], invalid: [], source: SOURCE, truncated: false },
    "/api/runtime/gpu-status": { item: gpu, source: SOURCE },
  };
}

describe("GPU 상태 카드", () => {
  it("수집 정상이면 사용률·메모리·온도·전력을 보여 준다", async () => {
    mockApi(routes({ schema_version: "runtime-gpu-status/1.0", state: "ok", fresh: true, collected_at: "2026-11-09T00:00:00Z", reason: null, gpus: [L4] }));
    renderAt("/runtime/overview", "/runtime/overview", <RuntimeOverviewPage />);
    const card = await screen.findByLabelText("GPU 상태");
    expect(await screen.findByText("NVIDIA L4")).toBeTruthy();
    expect(card.textContent).toContain("37%");
    expect(card.textContent).toContain("18120 / 23034 MiB");
    expect(card.textContent).toContain("54°C · 71.5 W");
    expect(screen.getByText("GPU 관측 · 수집 정상 · 1개")).toBeTruthy();
  });

  it("수집 중단은 마지막 값을 현재 값처럼 보이지 않게 경고한다", async () => {
    mockApi(routes({ schema_version: "runtime-gpu-status/1.0", state: "stale", fresh: false, collected_at: "2026-11-09T00:00:00Z", reason: null, gpus: [L4] }));
    renderAt("/runtime/overview", "/runtime/overview", <RuntimeOverviewPage />);
    expect(await screen.findByText("수집기가 멈춰 마지막 값이 오래됐습니다(현재 값 아님)")).toBeTruthy();
    expect(screen.getByText(/GPU 관측 · 관측 불가 · 수집 중단/)).toBeTruthy();
  });

  it.each([
    [undefined, "미연결", "off"],
    [{ state: "not_configured", gpus: [] }, "미연결", "off"],
    [{ state: "missing", gpus: [] }, "관측 불가 · 수집 결과 없음", "off"],
    [{ state: "error", reason: "TIMEOUT", gpus: [] }, "관측 불가 · 조회 시간 초과", "off"],
  ] as const)("상태 %#를 관측 불가와 구분한다", (item, label, kind) => {
    expect(gpuChipState(item as never)).toEqual([label, kind]);
  });
});

describe("현황(대시보드) 화면의 GPU 패널", () => {
  const overview = { status: null, requests: null, latest_verification: null, open_revalidation: 0 };
  it("실제 현황 화면에 GPU 값이 보인다", async () => {
    const { DashboardPage } = await import("../src/pages/DashboardPage");
    mockApi({ ...routes({ schema_version: "runtime-gpu-status/1.0", state: "ok", fresh: true, collected_at: "2026-11-09T00:00:00Z", reason: null, gpus: [L4] }), "/api/overview": overview });
    renderAt("/runtime/overview", "/runtime/overview", <DashboardPage />);
    const panel = await screen.findByLabelText("GPU 상태");
    expect(await screen.findByText("NVIDIA L4")).toBeTruthy();
    expect(panel.textContent).toContain("수집 정상 · 1개");
    expect(panel.textContent).toContain("18120 / 23034 MiB");
  });

  it("미연결이면 0이 아니라 연결 안 됨으로 보인다", async () => {
    const { DashboardPage } = await import("../src/pages/DashboardPage");
    mockApi({ ...routes({ schema_version: "runtime-gpu-status/1.0", state: "not_configured", fresh: false, collected_at: null, reason: null, gpus: [] }), "/api/overview": overview });
    renderAt("/runtime/overview", "/runtime/overview", <DashboardPage />);
    expect(await screen.findByText(/GPU 수집기가 연결되지 않았습니다/)).toBeTruthy();
  });
});
