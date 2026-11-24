import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Layout } from "../src/app/Layout";
import { useMeta } from "../src/api/hooks";
import type { RuntimeStatus } from "../src/api/types";
import { RuntimeSettingsPage } from "../src/pages/runtime/RuntimeSettingsPage";
import { mockApi, renderAt, SOURCE } from "./helpers";

const STATUS = Object.values(import.meta.glob("../../../contracts/examples/runtime-status.json", {
  eager: true, import: "default",
}))[0] as RuntimeStatus;

function Ready() { return useMeta().data ? <span>설정 확인 완료</span> : null; }

function renderState(connection: string, example = false) {
  const status = structuredClone(STATUS);
  status.collectors = [];
  mockApi({
    "/api/meta": { example_mode: example, runtime_connection: connection, contracts_version: "0.1",
      sources: [{ name: "runtime", mode: example ? "example" : "live" }, { name: "store", mode: example ? "example" : "file" }] },
    "/api/runtime/status": { item: status, source: SOURCE, invalid: null },
  });
  renderAt("/runtime/overview", "/runtime/overview", <><Layout /><Ready /></>);
}

describe("기본 화면의 정보 우선순위", () => {
  it("정상 연결에서는 출처·버전·연결 배지를 숨기고 시스템 정보로 안내한다", async () => {
    renderState("connected");
    await screen.findByText("설정 확인 완료");
    expect(screen.queryByText("실제 연결")).toBeNull();
    expect(screen.queryByText("연결 정상")).toBeNull();
    expect(screen.queryByText(/contracts 0.1/)).toBeNull();
    expect(screen.queryByText("자료 출처")).toBeNull();
    expect(screen.queryByText("예시 화면")).toBeNull();
    expect(screen.getByRole("link", { name: "시스템 정보" }).getAttribute("href")).toBe("/runtime/settings#system-info");
  });

  it("예시 안내는 한 번만 표시하고 검증에서 제외됨을 명확히 알린다", async () => {
    renderState("example", true);
    await screen.findByText("설정 확인 완료");
    expect(screen.getAllByText("예시 화면")).toHaveLength(1);
    const banner = screen.getByText("예시 화면").closest('[role="status"]')! as HTMLElement;
    expect(banner.textContent).toContain("예시 자료는 검증 결과에 포함되지 않습니다.");
    expect(banner.textContent).not.toMatch(/runtime|store|contracts/);
    expect(within(banner).getByRole("link", { name: "자료 출처 보기" }).getAttribute("href")).toBe("/runtime/settings#system-info");
  });

  it.each([
    ["disconnected", /런타임 연결 단절/],
    ["unauthorized", /런타임 인증에 실패했습니다/],
  ])("%s 상태는 조치 안내와 함께 표시한다", async (connection, message) => {
    renderState(connection as string);
    expect(await screen.findByText(message as RegExp)).toBeTruthy();
    expect(screen.getByRole("link", { name: "연결 설정 보기" })).toBeTruthy();
  });

  it("런타임 상태를 읽지 못해도 시스템 정보의 버전과 출처를 확인할 수 있다", async () => {
    mockApi({
      "/api/meta": { example_mode: true, runtime_connection: "unauthorized", contracts_version: "0.1",
        sources: [{ name: "runtime", mode: "live" }, { name: "evidence", mode: "example" }] },
    });
    renderAt("/runtime/settings", "/runtime/settings", <RuntimeSettingsPage />);
    expect(await screen.findByText("contracts 0.1")).toBeTruthy();
    expect(screen.getByText("인증 실패")).toBeTruthy();
    expect(screen.getByText("증거")).toBeTruthy();
    expect(screen.getByText("예시 자료")).toBeTruthy();
    expect(screen.getByText("서버 연결")).toBeTruthy();
  });
});
