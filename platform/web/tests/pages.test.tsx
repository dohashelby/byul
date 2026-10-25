import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SOURCE, mockApi, renderAt } from "./helpers";
import { EventsPage } from "../src/pages/EventsPage";
import { RequestsPage, filterRequests } from "../src/pages/RequestsPage";
import type { RequestRow, RuntimeEvent } from "../src/api/types";

const raw = Object.values(
  import.meta.glob("../../../contracts/examples/runtime-events.jsonl", { eager: true, query: "?raw", import: "default" }),
)[0] as string;
const EVENTS = raw
  .split("\n")
  .filter((l) => l.trim())
  .map((l) => JSON.parse(l)) as RuntimeEvent[];

const row = (over: Partial<RequestRow>): RequestRow => ({
  event_id: "req:" + "a".repeat(32),
  request_id: "a".repeat(32),
  recorded_at: "2026-10-25T01:00:00+00:00",
  outcome: "invalid",
  decision_result: "INVALID",
  model: null,
  max_tokens: null,
  reason_code: "UNSUPPORTED_MEDIA_TYPE",
  rule_id: null,
  http_status: 415,
  engine_http_status: null,
  stream: false,
  delivery_attempted: false,
  backend_kind: "mock",
  environment: null,
  run_id: null,
  target_ref: null,
  data_origin: "EXAMPLE",
  origin_detail: "actual_test",
  ...over,
});

const EMPTY_LIST = { items: [], source: { name: "store", mode: "example", detail: null } };

describe("사건 로그", () => {
  it("Hubble 사건(DROPPED 포함)은 거부 확인으로 표시되지 않는다", async () => {
    mockApi({
      "/api/runtime/events": { items: EVENTS, invalid: [], source: SOURCE, truncated: false },
      "/api/environments": EMPTY_LIST,
      "/api/update/results": { items: [], invalid: [], source: SOURCE },
    });
    renderAt("/runtime/events", "/runtime/events", <EventsPage />);
    const table = await screen.findByRole("table");
    const hubbleRow = within(table).getByText("Hubble").closest("tr")!;
    expect(hubbleRow.textContent).toContain("관측");
    expect(hubbleRow.textContent).not.toContain("거부 확인");
    const tetragonRow = within(table).getByText("Tetragon").closest("tr")!;
    expect(tetragonRow.textContent).toContain("탐지");
    expect(tetragonRow.textContent).not.toMatch(/거부 확인|차단/);
  });

  it("상세 패널이 URL의 sel로 열리고 원본을 텍스트로 보여준다", async () => {
    mockApi({
      "/api/runtime/events": { items: EVENTS, invalid: [], source: SOURCE, truncated: false },
      "/api/environments": EMPTY_LIST,
      "/api/update/results": { items: [], invalid: [], source: SOURCE },
    });
    const hubble = EVENTS.find((e) => e.source === "hubble")!;
    renderAt(`/runtime/events?sel=${hubble.event_id}`, "/runtime/events", <EventsPage />);
    const panel = await screen.findByRole("complementary", { name: "상세" });
    expect(within(panel).getByText("synthetic")).toBeTruthy();
    expect(within(panel).queryByText("거부 확인")).toBeNull();
  });

  it("형식 오류 항목을 버리지 않고 따로 보여준다", async () => {
    mockApi({
      "/api/runtime/events": {
        items: [],
        invalid: [{ index: 3, errors: ["/outcome: 'blocked' is not one of [...]"], raw: { outcome: "blocked" } }],
        source: SOURCE,
        truncated: false,
      },
      "/api/environments": EMPTY_LIST,
    });
    renderAt("/runtime/events", "/runtime/events", <EventsPage />);
    expect(await screen.findByText("형식 오류 1건")).toBeTruthy();
  });

  it("runtime 연결이 끊기면 연결 끊김 화면을 보여준다", async () => {
    mockApi({ "/api/environments": EMPTY_LIST });
    const { vi } = await import("vitest");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).startsWith("/api/runtime")
          ? new Response(JSON.stringify({ detail: { code: "RUNTIME_UNREACHABLE", message: "x" } }), { status: 503 })
          : new Response(JSON.stringify(EMPTY_LIST), { status: 200 }),
      ),
    );
    renderAt("/runtime/events", "/runtime/events", <EventsPage />);
    expect(await screen.findByText("runtime 연결 끊김")).toBeTruthy();
  });
});

describe("요청 내역", () => {
  it("null 값은 –로 보이고 0이나 빈칸이 되지 않는다", async () => {
    mockApi({
      "/api/runtime/requests": { items: [row({})], invalid: [], source: SOURCE, truncated: false },
      "/api/environments": EMPTY_LIST,
    });
    renderAt("/runtime/requests", "/runtime/requests", <RequestsPage />);
    const table = await screen.findByRole("table");
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent?.replace(/[▲▼ ]/g, ""));
    const cells = within(table).getAllByRole("row")[1].querySelectorAll("td");
    const cellOf = (name: string) => cells[headers.indexOf(name)].textContent;
    expect(cellOf("max_tokens")).toBe("–");
    expect(cellOf("모델")).toBe("–");
    expect(cellOf("엔진HTTP")).toBe("–");
    expect(cellOf("클라이언트HTTP")).toBe("415");
    // 지연시간·토큰 수·비용 열은 없다
    expect(headers.join(" ")).not.toMatch(/지연|토큰 수|비용|latency|cost/);
  });

  it("화면은 GET만 보낸다", async () => {
    const calls = mockApi({
      "/api/runtime/requests": { items: [row({})], invalid: [], source: SOURCE, truncated: false },
      "/api/environments": EMPTY_LIST,
    });
    renderAt("/runtime/requests", "/runtime/requests", <RequestsPage />);
    await screen.findByRole("table");
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(new Set(calls.map((c) => c.method))).toEqual(new Set(["GET"]));
    expect(calls.every((c) => c.url.startsWith("/api/"))).toBe(true);
  });

  it("필터: 검색·결과·스트림", () => {
    const rows = [row({ request_id: "b".repeat(32), model: "test-model", outcome: "allowed", stream: true }), row({})];
    expect(filterRequests(rows, { q: "TEST" })).toHaveLength(1);
    expect(filterRequests(rows, { outcome: "invalid" })).toHaveLength(1);
    expect(filterRequests(rows, { stream: "true" })[0].model).toBe("test-model");
    expect(filterRequests(rows, {})).toHaveLength(2);
  });
});
