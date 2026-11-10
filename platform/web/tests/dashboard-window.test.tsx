import { screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { DashboardPage } from "../src/pages/DashboardPage";
import { mockApi, renderAt, SOURCE } from "./helpers";

function routes(partial: boolean) {
  return {
    "/api/overview": { status: null, requests: { counts_lower_bound: partial, history_truncated: partial }, latest_verification: null, open_revalidation: 0 },
    "/api/runtime/requests": { items: [], invalid: [], source: SOURCE, truncated: partial },
    "/api/runtime/events": { items: [], invalid: [], source: SOURCE, truncated: partial,
      source_windows: partial ? { hubble: { returned: 2, total: null, total_lower_bound: 5, limit: 1000, truncated: true } } : {} },
    "/api/runtime/gpu-status": { item: null, source: SOURCE },
  };
}

it("실제 현황 화면에서 기간·환경·대상과 부분 조회·하한을 함께 표시한다", async () => {
  mockApi(routes(true));
  renderAt("/runtime/overview", "/runtime/overview", <DashboardPage />);
  const scope = await screen.findByLabelText("대시보드 집계 범위");
  expect(scope.textContent).toContain("선택한 기간·환경·대상");
  expect(scope.textContent).toContain("확인된 최소 건수");
  expect((await screen.findByLabelText("사건 조회 범위")).textContent).toContain("최소 5건 · 최근 조회 범위");
  expect(screen.getByRole("region", { name: "GPU 상태" })).toBeTruthy();
});

it("구판 응답에 새 window 필드가 없으면 하한을 만들어 표시하지 않는다", async () => {
  const data = routes(false);
  data["/api/overview"].requests = {} as typeof data["/api/overview"]["requests"];
  mockApi(data);
  renderAt("/runtime/overview", "/runtime/overview", <DashboardPage />);
  expect((await screen.findByLabelText("대시보드 집계 범위")).textContent).not.toContain("최소 건수");
  expect(screen.queryByLabelText("사건 조회 범위")).toBeNull();
});
