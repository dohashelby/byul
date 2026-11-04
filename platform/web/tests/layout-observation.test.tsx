import { screen } from "@testing-library/react";
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

function renderLayout(status: RuntimeStatus) {
  mockApi({
    "/api/meta": { example_mode: true, sources: [{ name: "runtime", mode: "example" }], runtime_connection: "example", contracts_version: "test" },
    "/api/runtime/status": { item: status, invalid: null, source: SOURCE },
  });
  renderAt("/runtime/overview", "/runtime/overview", <><Layout /><StatusReady /></>);
}

describe("관측 연결 경고", () => {
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
