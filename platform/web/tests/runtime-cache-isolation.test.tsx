import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RuntimeRequestDetail, RuntimeStatus } from "../src/api/types";
import { RequestDrawer } from "../src/pages/runtime/RequestDrawer";
import { RuntimeOverviewPage } from "../src/pages/runtime/RuntimeOverviewPage";
import { SOURCE, mockApi, renderAt } from "./helpers";

const STATUS = Object.values(
  import.meta.glob("../../../contracts/examples/runtime-status.json", { eager: true, import: "default" }),
)[0] as RuntimeStatus;
const DETAIL = Object.values(
  import.meta.glob("../../../contracts/examples/runtime-request-detail.json", { eager: true, import: "default" }),
)[0] as RuntimeRequestDetail;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function overviewRoutes(status: RuntimeStatus) {
  return {
    "/api/runtime/status": { item: status, invalid: null, source: SOURCE },
    "/api/runtime/summary": {
      counts: { allowed: 0, denied: 0, invalid: 0, engine_rejected: 0, error: 0, unknown: 0 },
      total: 0,
      corrupt_lines: 0,
      generated_at: status.generated_at,
      source: SOURCE,
    },
    "/api/runtime/requests": { items: [], invalid: [], source: SOURCE, truncated: false },
    "/api/runtime/events": { items: [], invalid: [], source: SOURCE, truncated: false },
  };
}

describe("사용자별 캐시 분리 표시", () => {
  it.each([
    [{ mode: "off", ready: true, unavailable_reason_code: null }, "꺼짐(사용자 간 prefix cache 공유)", false],
    [{ mode: "per_principal", ready: true, unavailable_reason_code: null }, "켜짐", false],
    [{ mode: "per_principal", ready: false, unavailable_reason_code: "CACHE_ISOLATION_KEY_MISSING" }, "준비 불가(CACHE_ISOLATION_KEY_MISSING)", true],
    [{ mode: null, ready: false, unavailable_reason_code: null }, "모름", false],
  ] as const)("현황 정책 상태 %#를 표시한다", async (cacheIsolation, expected, problem) => {
    const status = clone(STATUS);
    status.policy.cache_isolation = cacheIsolation;
    mockApi(overviewRoutes(status));
    renderAt("/runtime/overview", "/runtime/overview", <RuntimeOverviewPage />);

    const label = await screen.findByText("사용자별 캐시 분리");
    const row = label.parentElement!;
    expect(row.textContent).toContain(expected);
    expect((row.querySelector("dd span") as HTMLElement).style.color).toBe(problem ? "var(--error)" : "");
  });

  it.each([
    [true, "적용"],
    [false, "미적용"],
    [null, "모름"],
  ] as const)("요청 상세 applied=%s를 %s으로 표시한다", async (applied, expected) => {
    const detail = clone(DETAIL);
    detail.request.cache_isolation = { mode: applied === true ? "per_principal" : applied === false ? "off" : null, applied };
    mockApi({
      [`/api/runtime/requests/${detail.request_id}`]: {
        row: null,
        event: null,
        event_invalid: null,
        detail,
        detail_invalid: null,
        evidence: [],
        source: SOURCE,
      },
    });
    renderAt("/", "*", <RequestDrawer id={detail.request_id} onClose={() => undefined} />);

    const label = await screen.findByText("캐시 분리");
    expect(label.parentElement?.textContent).toContain(expected);
  });
});
