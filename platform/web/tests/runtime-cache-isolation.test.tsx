import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RuntimeEvent, RuntimeRequestDetail, RuntimeStatus } from "../src/api/types";
import { RequestDrawer } from "../src/pages/runtime/RequestDrawer";
import { RuntimeOverviewPage } from "../src/pages/runtime/RuntimeOverviewPage";
import { RuntimeTargetPage } from "../src/pages/runtime/RuntimeTargetPage";
import { SOURCE, mockApi, renderAt } from "./helpers";

const STATUS = Object.values(
  import.meta.glob("../../../contracts/examples/runtime-status.json", { eager: true, import: "default" }),
)[0] as RuntimeStatus;
const DETAIL = Object.values(
  import.meta.glob("../../../contracts/examples/runtime-request-detail.json", { eager: true, import: "default" }),
)[0] as RuntimeRequestDetail;
const EVENT_RAW = Object.values(import.meta.glob("../../../contracts/examples/runtime-events.jsonl", { eager: true, query: "?raw", import: "default" }))[0] as string;
const TETRAGON = EVENT_RAW.split("\n").filter(Boolean).map((line) => JSON.parse(line) as RuntimeEvent).find((event) => event.source === "tetragon")!;

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
    [{ mode: "off", ready: true, unavailable_reason_code: null }, "비활성 (사용자 간 prefix cache 공유)", false],
    [{ mode: "per_principal", ready: true, unavailable_reason_code: null }, "활성", false],
    [{ mode: "per_principal", ready: false, unavailable_reason_code: "CACHE_ISOLATION_KEY_MISSING" }, "준비 불가(CACHE_ISOLATION_KEY_MISSING)", true],
    [{ mode: null, ready: false, unavailable_reason_code: null }, "미확인", false],
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

describe("기간별 Tetragon 통제 방식", () => {
  it.each([[true, true], [false, false]] as const)("선택 기간 안 Override=%s이면 보조 표시=%s", async (recent, expected) => {
    const at = recent ? new Date().toISOString() : "2020-01-01T00:00:00Z";
    const blocked: RuntimeEvent = {
      ...TETRAGON, event_id: "override", recorded_at: at, occurred_at: at,
      native: { ...TETRAGON.native, record: { ...TETRAGON.native.record, action_raw: "KPROBE_ACTION_OVERRIDE" } as unknown as RuntimeEvent["native"]["record"] },
      observation: { ...TETRAGON.observation!, kind: "file_access", action_mode: "enforce" },
    };
    mockApi({ ...overviewRoutes(clone(STATUS)), "/api/runtime/events": { items: [blocked], invalid: [], source: SOURCE, truncated: false } });
    renderAt("/runtime/overview?period=24h", "/runtime/overview", <RuntimeOverviewPage />);
    expect(await screen.findByText("런타임 탐지 (Tetragon) · 탐지")).toBeTruthy();
    expect(Boolean(screen.queryByText("탐지 · 일부 규칙 차단"))).toBe(expected);
  });
});

describe("대상 확인 중단 표시", () => {
  it("직전 정상 확인 뒤 수집이 끊기면 요청 처리와 마지막 확인 시각을 경고한다", async () => {
    const status = clone(STATUS);
    status.overall = "degraded";
    status.target.verification = {
      state: "unavailable", last_confirmed_at: "2026-10-25T01:04:58Z",
    };
    status.target.gateway_hold = null;
    mockApi({
      "/api/runtime/status": { item: status, invalid: null, source: SOURCE },
      "/api/update/revalidation": { items: [], source: SOURCE },
    });
    renderAt("/runtime/target", "/runtime/target", <RuntimeTargetPage />);
    expect(await screen.findByText(/대상 미확인, 요청은 처리 중/)).toBeTruthy();
    expect(screen.getByText(/마지막 확인 시각/)).toBeTruthy();
    expect(screen.queryByText(/신규 추론 요청 보류\(503\)/)).toBeNull();
  });
});
