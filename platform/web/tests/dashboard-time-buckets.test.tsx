import { fireEvent, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DashboardPage } from "../src/pages/DashboardPage";
import { mockApi, renderAt, SOURCE } from "./helpers";

vi.mock("../src/components/StackedBars", async (importOriginal) => {
  const original = await importOriginal<typeof import("../src/components/StackedBars")>();
  const Plot = (props: { size: number; buckets: unknown[] }) => <output data-testid="time-buckets">{JSON.stringify(props)}</output>;
  return { ...original, TimeLine: Plot, TimeBars: Plot };
});

it("한 시간 안의 요청을 다른 구간에 표시하고 두 탭에서 같은 간격을 유지한다", async () => {
  const now = Date.now();
  vi.spyOn(Date, "now").mockReturnValue(now);
  mockApi({
    "/api/overview": { status: null, requests: null, latest_verification: null, open_revalidation: 0 },
    "/api/runtime/requests": { source: SOURCE, invalid: [], truncated: false, items: [
      { recorded_at: new Date(now - 45 * 60_000).toISOString(), outcome: "allowed", target_ref: null },
      { recorded_at: new Date(now - 5 * 60_000).toISOString(), outcome: "denied", reason_code: "MODEL_NOT_ALLOWED", target_ref: null },
    ] },
    "/api/runtime/events": { items: [], invalid: [], source: SOURCE, truncated: false },
  });
  renderAt("/runtime/overview?period=1h", "/runtime/overview", <DashboardPage />);
  const chart = (await screen.findByRole("heading", { name: "요청 추이" })).closest("article")!;
  const read = () => JSON.parse(within(chart).getByTestId("time-buckets").textContent!) as {
    size: number; buckets: { start: number; allowed: number; denied: number }[];
  };
  const total = read();
  expect(total.size).toBeLessThan(3_600_000);
  expect(total.buckets.filter((bucket) => bucket.allowed + bucket.denied > 0)).toHaveLength(2);
  expect(total.buckets.reduce((sum, bucket) => sum + bucket.allowed + bucket.denied, 0)).toBe(2);
  fireEvent.click(within(chart).getByRole("tab", { name: "결과별" }));
  const results = read();
  expect(results.size).toBe(total.size);
  expect(results.buckets).toEqual(total.buckets);
});

it("수집된 이벤트 전체를 탐지 건수로 표시하지 않는다", async () => {
  const at = new Date().toISOString();
  mockApi({
    "/api/overview": { status: null, requests: null, latest_verification: null, open_revalidation: 0 },
    "/api/runtime/requests": { items: [], source: SOURCE, invalid: [], truncated: false },
    "/api/runtime/events": { source: SOURCE, invalid: [], truncated: false, items:
      Array.from({ length: 12 }, (_, index) => ({ source: "tetragon", target_ref: null,
        recorded_at: at, outcome: index === 0 ? "detected" : "observed",
        observation: { kind: "process_exec" } })) },
  });
  renderAt("/runtime/overview", "/runtime/overview", <DashboardPage />);
  expect(await screen.findByText("탐지 1건")).toBeTruthy();
  const card = screen.getByRole("button", { name: "프로세스 이벤트 보기" });
  expect(card.querySelector('.bigNumber > span')?.textContent).toBe("12");
  expect(card.textContent).not.toContain("탐지 12건");
});
