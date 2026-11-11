import { screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { RouteAuditPanel } from "../src/pages/runtime/RouteAuditPanel";
import { mockApi, renderAt } from "./helpers";

it("경로 감사 손실과 파일 조회 범위를 추론 건수와 구분해 표시한다", async () => {
  const calls = mockApi({ "/api/runtime/route-audit": { state: "ok", item: {
    status: { state: "error", attempted_count: 12, recorded_count: 8, overflow_count: 3, write_failure_count: 1, last_write_error_class: "OSError", last_write_failure_at: "2026-11-06T01:00:00Z" },
    window: { returned: 1, truncated: true, corrupt_lines: 2, read_error_class: null, scanned_bytes: 1000, max_bytes: 1048576 },
    items: [{ event_id: "a".repeat(32), request_id: "b".repeat(32), recorded_at: "2026-11-06T01:00:00Z", method: "POST", path: "/generate", response_code: 404, reason: "NOT_FOUND", scope: { environment: "lab", run_id: "r1", target_id: "t1" }, actor: { state: "unattributed" } }],
  } } });
  renderAt("/audit", "/audit", <RouteAuditPanel />);
  expect(await screen.findByText("POST /generate")).toBeTruthy();
  expect(screen.getByText(/기록 한도 초과 3 · 저장 실패 1/)).toBeTruthy();
  expect(screen.getByText(/조회 범위 잘림/)).toBeTruthy();
  expect(screen.getByText(/주체 미귀속/)).toBeTruthy();
  expect(calls.every((call) => call.method === "GET")).toBe(true);
  expect(calls.some((call) => call.url.includes("requests"))).toBe(false);
});

it("실제 연결이 없으면 0건 성공으로 표시하지 않는다", async () => {
  mockApi({ "/api/runtime/route-audit": { state: "not_configured", item: null } });
  renderAt("/audit", "/audit", <RouteAuditPanel />);
  expect(await screen.findByText("실제 runtime 감사 연결이 설정되지 않았습니다.")).toBeTruthy();
  expect(screen.queryByText(/저장된 경로 거부 기록이 없습니다/)).toBeNull();
});
