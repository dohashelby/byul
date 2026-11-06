import { screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { TetragonPolicyPage } from "../src/pages/runtime/TetragonPolicyPage";
import { mockApi, renderAt, SOURCE } from "./helpers";

function state() {
  return { schema_version: "runtime-tetragon-state/1.0", state: "ok", fresh: true, context: "kind-runtime-lab",
    generated_at: "2026-11-06T00:00:00Z", error: null,
    policies: [{ node: "node", name: "trial-policy", namespace: "trial", id: "17", mode: "enforce", state: "enabled", method: "sigkill_fd_install", load_error: null }],
    capabilities: [], transitions: [], verifications: [] as object[] };
}

it("실제 조회 모드와 로드 상태를 표시하며 제어 요청을 보내지 않는다", async () => {
  const calls = mockApi({ "/api/runtime/tetragon-status": { item: state(), source: SOURCE } });
  renderAt("/runtime/enforce/tetragon", "/runtime/enforce/tetragon", <TetragonPolicyPage />);
  expect(await screen.findByText("현재 정책 모드")).toBeTruthy();
  expect(screen.getByText("trial-policy")).toBeTruthy();
  expect(screen.getByText("차단")).toBeTruthy();
  expect(screen.getByText("로드됨")).toBeTruthy();
  expect(calls.every((c) => c.method === "GET")).toBe(true);
});

it("수집 지연이면 마지막 모드를 현재 상태로 확정하지 않는다", async () => {
  mockApi({ "/api/runtime/tetragon-status": { item: { ...state(), state: "stale", fresh: false }, source: SOURCE } });
  renderAt("/runtime/enforce/tetragon", "/runtime/enforce/tetragon", <TetragonPolicyPage />);
  expect(await screen.findByText("마지막 관측 정책 모드")).toBeTruthy();
  expect(screen.getByText("차단 (현재 미확인)")).toBeTruthy();
  expect(screen.queryByText("현재 정책 모드")).toBeNull();
});

it("종료 확인과 파일 읽기 거부 미확인을 다른 칸에 표시한다", async () => {
  const item = state();
  item.verifications = [{ id: "proof", context: "kind-runtime-lab", namespace: "trial", policy: "trial-policy", method: "sigkill_fd_install", data_origin: "real",
    started_at: "2026-11-06T00:00:00Z", finished_at: "2026-11-06T00:00:03Z", pod_uid: "uid", policy_id: "17", facts: {}, events: [],
    task_identity: { pod_uid: "uid", namespace: "trial", container_id: "fake-id", node_pid: 123, ns_pid: 1, pidns: 456, start_ticks: 1000, clock_ticks: 100 },
    verification: { expected_effect: "process_termination", file_read_denial: "unknown", process_termination: "confirmed", service_recovery: "confirmed", monitor_restored: true, status: "passed" } }];
  mockApi({ "/api/runtime/tetragon-status": { item, source: SOURCE } });
  renderAt("/runtime/enforce/tetragon", "/runtime/enforce/tetragon", <TetragonPolicyPage />);
  const restored = await screen.findByText("감시 복귀 확인");
  const row = restored.closest("tr")!;
  const cells = within(row).getAllByRole("cell");
  expect(cells[2].textContent).toBe("미확인");
  expect(cells[3].textContent).toBe("확인");
  expect(screen.getByText(/커널 task의 PID namespace·시작 시각/)).toBeTruthy();
  expect(screen.queryByText("파일 읽기 차단 확인")).toBeNull();
});
