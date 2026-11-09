import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LandingPage } from "../src/pages/LandingPage";
import { useMeta } from "../src/api/hooks";
import { mockApi, renderAt } from "./helpers";

function MetaReady() {
  return useMeta().data ? <span>출처 조회 완료</span> : null;
}

describe("첫 화면 자료 출처", () => {
  it("실측 증거가 연결되면 여전히 예시인 출처만 알린다", async () => {
    mockApi({ "/api/meta": { example_mode: true, sources: [
      { name: "runtime", mode: "example" }, { name: "update", mode: "example" },
      { name: "evidence", mode: "file" }, { name: "store", mode: "example" },
    ], runtime_connection: "example", contracts_version: "0.1" } });
    renderAt("/", "/", <LandingPage />);
    const banner = await screen.findByText("예시 자료 · runtime, update, store · 해당 자료는 공식 판정 근거 제외");
    expect(banner.textContent).not.toContain("evidence");
    expect(screen.queryByText(/일부 수집원은 합성/)).toBeNull();
  });

  it("모든 출처가 실제 연결되면 예시 배너가 없다", async () => {
    mockApi({ "/api/meta": { example_mode: false, sources: [
      { name: "runtime", mode: "live" }, { name: "update", mode: "file" },
      { name: "evidence", mode: "file" }, { name: "store", mode: "file" },
    ], runtime_connection: "connected", contracts_version: "0.1" } });
    renderAt("/", "/", <><LandingPage /><MetaReady /></>);
    await screen.findByText("출처 조회 완료");
    expect(screen.queryByText(/공식 판정 근거 제외/)).toBeNull();
  });
});
