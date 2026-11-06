import { cleanup, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useRuntimeStatus } from "../src/api/hooks";
import type { Collector, RuntimeStatus } from "../src/api/types";
import { Layout } from "../src/app/Layout";
import { observationState } from "../src/pages/runtime/parts";
import { SOURCE, mockApi, renderAt } from "./helpers";

const STATUS = Object.values(
  import.meta.glob("../../../contracts/examples/runtime-status.json", { eager: true, import: "default" }),
)[0] as RuntimeStatus;

function StatusReady() {
  return useRuntimeStatus().data?.item ? <span>상태 조회 완료</span> : null;
}

function renderLayout(status: RuntimeStatus, path = "/runtime/overview") {
  mockApi({
    "/api/meta": { example_mode: true, sources: [{ name: "runtime", mode: "example" }], runtime_connection: "example", contracts_version: "test" },
    "/api/runtime/status": { item: status, invalid: null, source: SOURCE },
  });
  renderAt(path, path, <><Layout /><StatusReady /></>);
}

describe("관측 연결 경고", () => {
  it("실제 runtime과 예시 증거가 섞이면 예시 경고의 대상을 구분한다", async () => {
    const status = structuredClone(STATUS);
    status.collectors = [];
    mockApi({
      "/api/meta": { example_mode: true, sources: [{ name: "runtime", mode: "live" }, { name: "evidence", mode: "example" }], runtime_connection: "connected", contracts_version: "test" },
      "/api/runtime/status": { item: status, invalid: null, source: { ...SOURCE, mode: "live" } },
    });
    renderAt("/runtime/enforce/tetragon", "/runtime/enforce/tetragon", <><Layout /><StatusReady /></>);
    await screen.findByText("상태 조회 완료");
    expect(screen.getByText("일부 예시 포함")).toBeTruthy();
    expect(screen.getByText("예시 자료 · evidence · 해당 자료는 공식 판정 근거 제외")).toBeTruthy();
    expect(screen.queryByText(/^예시 데이터 ·/)).toBeNull();
  });
  it("Runtime과 Update 사이드바의 세 단계 제목을 표시한다", async () => {
    const status = structuredClone(STATUS);
    renderLayout(status);
    await screen.findByText("상태 조회 완료");
    expect(screen.getByText("Runtime · 운영 통제")).toBeTruthy();
    expect(screen.getByText("요청 통제")).toBeTruthy();
    expect(screen.getByRole("link", { name: "차단 내역" })).toBeTruthy();
    cleanup();
    renderLayout(status, "/update/results");
    await screen.findByText("상태 조회 완료");
    expect(screen.getByText("Update · 격리 검증")).toBeTruthy();
    expect(screen.getByText("검증")).toBeTruthy();
    expect(screen.getByRole("link", { name: "검증 결과" })).toBeTruthy();
  });
  it("수집원이 없는 합성 자료 시연에서는 관측 불가 경고를 띄우지 않는다", async () => {
    const status = structuredClone(STATUS);
    status.collectors = [];
    expect(observationState(status.observations.origins_by_kind.network_flow, undefined)).toEqual(["합성 데이터", "synthetic"]);
    renderLayout(status);

    await screen.findByText("상태 조회 완료");
    expect(screen.getByText(/예시 데이터 ·/)).toBeTruthy();
    expect(screen.queryByText(/^관측 불가:/)).toBeNull();
  });

  it("존재하는 Tetragon 수집원이 비정상이면 해당 수집원만 경고한다", async () => {
    const status = structuredClone(STATUS);
    status.collectors = [{ ...status.collectors[0], source: "observation_feed_tetragon", state: "stale" }];
    renderLayout(status);

    expect((await screen.findByText(/^관측 불가:/)).textContent).toContain("(Tetragon)");
    expect(screen.getByText(/예시 데이터 ·/)).toBeTruthy();
  });

  it("수집기 상태 문구를 한국어로 표시한다", () => {
    const collector = (state: string, detail: unknown, last_error: string | null = null) =>
      ({ state, detail, last_error } as unknown as Collector);
    expect(observationState(undefined, collector("stale", { kind: "observation_feed", writer: { configured: false } }))[0])
      .toBe("관측 불가 · 수집기 생존 신호 미설정 · 지연");
    expect(observationState(undefined, collector("error", null, "writer heartbeat failed"))[0])
      .toBe("관측 불가 · 수집기 생존 신호 오류");
    expect(observationState(undefined, collector("ok", null))[0])
      .toBe("실시간 수집 · 수집기 생존 신호 미확인");
  });
});
