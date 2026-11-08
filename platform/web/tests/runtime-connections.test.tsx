import { screen } from "@testing-library/react";
import { expect, it } from "vitest";
import type { RuntimeStatus } from "../src/api/types";
import { RuntimePolicyPage } from "../src/pages/runtime/RuntimePolicyPage";
import { RuntimeEnforcePage } from "../src/pages/runtime/RuntimeEnforcePage";
import { SOURCE, mockApi, renderAt } from "./helpers";

const STATUS = Object.values(
  import.meta.glob("../../../contracts/examples/runtime-status.json", { eager: true, import: "default" }),
)[0] as RuntimeStatus;

it.each([
  ["not_connected", "미연결"],
  ["error", "상태 읽기 오류"],
  ["unknown", "현재 상태 미확인"],
  ["enforce", "집행 모드 (차단 실측과 별도)"],
  ["monitor", "감시 모드"],
  ["monitor_only", "감시 전용 모드"],
  ["mixed", "혼합 모드 (정책별 확인)"],
  ["future_mode", "현재 상태 미확인"],
])("외부 Tetragon 상태 %s를 한국어로 표시한다", async (value, text) => {
  const status = structuredClone(STATUS);
  status.policy.external_enforcement = value;
  mockApi({ "/api/runtime/status": { item: status, source: SOURCE } });
  renderAt("/runtime/policy", "/runtime/policy", <RuntimePolicyPage />);
  expect(await screen.findByText(`외부 집행 (Tetragon): ${text}`)).toBeTruthy();
  expect(screen.queryByText(/차단 중/)).toBeNull();
});

it("연결한 Lab 폴더가 비어 있으면 파일 없음으로 표시한다", async () => {
  mockApi({ "/api/runtime/lab-report": { item: { state: "missing", report: null,
    configured_path: "/srv/serving/observation/lab-reports" }, source: SOURCE } });
  renderAt("/runtime/enforce/lab", "/runtime/enforce/:sub", <RuntimeEnforcePage />);
  expect(await screen.findByText("lab 보고서 파일을 찾지 못했습니다.")).toBeTruthy();
  expect(screen.queryByText(/lab 보고서가 설정되지 않았습니다/)).toBeNull();
});

it("과거 Lab 결과의 회차·환경·시작/완료 시각과 현재 상태와의 구분을 표시한다", async () => {
  mockApi({ "/api/runtime/lab-report": { item: { state: "ok", report: {
    run_id: "old-lab-run", context: "lab-context", namespace: "test-target",
    started_at: "2020-01-01T00:00:00Z", finished_at: "2020-01-01T00:01:00Z",
    overall: "partial", cases: [],
  } }, source: SOURCE } });
  renderAt("/runtime/enforce/lab", "/runtime/enforce/:sub", <RuntimeEnforcePage />);
  expect(await screen.findByText("lab-context · test-target")).toBeTruthy();
  expect(screen.getByText("old-lab-run")).toBeTruthy();
  expect(screen.getByText("시작")).toBeTruthy();
  expect(screen.getByText("완료")).toBeTruthy();
  expect(screen.getAllByText(/2020/).length).toBe(2);
  expect(screen.getByText(/현재 정책의 차단 상태를 보장하지 않습니다/)).toBeTruthy();
});
