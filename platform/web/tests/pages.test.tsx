import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SOURCE, mockApi, renderAt } from "./helpers";
import { EventsPage } from "../src/pages/EventsPage";
import { RuntimeObservePage } from "../src/pages/runtime/RuntimeObservePage";
import { RequestsPage, filterRequests } from "../src/pages/RequestsPage";
import { BlockedPage, blockedRows } from "../src/pages/BlockedPage";
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
  observation_session_id: null,
  data_origin: "EXAMPLE",
  origin_detail: "actual_test",
  ...over,
});

const EMPTY_LIST = { items: [], source: { name: "store", mode: "example", detail: null } };

describe("사건 로그", () => {
  it("네트워크 이벤트 표를 25행씩 표시하고 나머지를 다음 페이지에 둔다", async () => {
    const hubble = EVENTS.find((e) => e.source === "hubble")!;
    const items = Array.from({ length: 27 }, (_, index) => ({ ...hubble, event_id: `network-${index}` }));
    mockApi({ "/api/runtime/events": { items, invalid: [], source: SOURCE, truncated: false } });
    renderAt("/runtime/observe/hubble?page=2", "/runtime/observe/:sub", <RuntimeObservePage />);
    const nav = await screen.findByRole("navigation", { name: "페이지 이동" });
    expect(nav.textContent).toContain("26–27 / 27");
    expect(screen.getByRole("table").querySelectorAll("tbody tr")).toHaveLength(2);
  });

  it("Gateway Pod의 의심 실행을 Tetragon 탐지 행으로 표시한다", async () => {
    const sample = EVENTS.find((e) => e.source === "tetragon")!;
    const suspect: RuntimeEvent = {
      ...sample, event_id: "sha:" + "d".repeat(64), outcome: "detected",
      native: { ...sample.native, record: { ...sample.native.record, parent_check: "matched", parent_attribution: "available", suspect_context: "compile_toolchain_parent" } as unknown as RuntimeEvent["native"]["record"] },
      observation: { ...sample.observation!, kind: "process_exec", binary: "/bin/sh",
        pod_namespace: "serving-lab", pod_name: "serving-gateway-1",
        exec_verdict: "suspect_binary", phases: ["unknown"], allowed_binary: null },
    };
    mockApi({ "/api/runtime/events": { items: [suspect], invalid: [], source: SOURCE, truncated: false } });
    renderAt("/runtime/observe/tetragon", "/runtime/observe/:sub", <RuntimeObservePage />);
    const row = (await screen.findByText("/bin/sh")).closest("tr")!;
    expect(row.textContent).toContain("의심 실행");
    expect(row.textContent).toContain("부모 프로세스 일치");
    expect(row.textContent).toContain("컴파일 도구 하위 실행");
    expect(row.textContent).toContain("단계 미확인");
    expect(within(row).getByText("부모 프로세스 일치 · 부모 프로세스 정보 확인 · 컴파일 도구 하위 실행")).toBeTruthy();
    expect(row.className).toContain("rowFlagged");
    expect(row.querySelector("td[title='serving-lab/serving-gateway-1']")).toBeTruthy();
  });

  it("보호 파일 Override의 판정 열에 차단 표지와 원본 행위를 표시한다", async () => {
    const sample = EVENTS.find((e) => e.source === "tetragon")!;
    const blocked: RuntimeEvent = {
      ...sample, event_id: "override-file", outcome: "enforce_attempted",
      native: { ...sample.native, record: { ...sample.native.record, action_raw: "KPROBE_ACTION_OVERRIDE" } as unknown as RuntimeEvent["native"]["record"] },
      observation: { ...sample.observation!, kind: "file_access", path: "/tmp/fake-secret", rule_id: "TG-SECRET-01", action_mode: "enforce", exec_verdict: null },
    };
    mockApi({ "/api/runtime/events": { items: [blocked], invalid: [], source: SOURCE, truncated: false } });
    renderAt("/runtime/observe/tetragon", "/runtime/observe/:sub", <RuntimeObservePage />);
    const tableRow = (await screen.findByText("/tmp/fake-secret")).closest("tr")!;
    expect(within(tableRow).getByText("차단")).toBeTruthy();
    expect(tableRow.textContent).toContain("차단 (Override)");
    expect(tableRow.textContent).toContain("TG-SECRET-01");
  });

  it("합성 보호 파일 Override를 예시 자료로 표시하면서 차단 표지를 유지한다", async () => {
    const sample = EVENTS.find((e) => e.source === "tetragon")!;
    const blocked: RuntimeEvent = {
      ...sample, event_id: "synthetic-secret-override", outcome: "enforce_attempted",
      data_origin: "EXAMPLE", origin_detail: "synthetic",
      native: { ...sample.native, record: { ...sample.native.record, action_raw: "KPROBE_ACTION_OVERRIDE" } as unknown as RuntimeEvent["native"]["record"] },
      observation: { ...sample.observation!, kind: "file_access", path: "/run/byul-probe/secret.txt", access: "read", rule_id: "TG-SECRET-01", action_mode: "enforce", exec_verdict: null },
    };
    mockApi({
      "/api/runtime/events": { items: [blocked], invalid: [], source: SOURCE, truncated: false },
      "/api/environments": EMPTY_LIST,
      "/api/update/results": { items: [], invalid: [], source: SOURCE },
    });
    const view = renderAt("/runtime/observe/tetragon", "/runtime/observe/:sub", <RuntimeObservePage />);
    const tableRow = (await screen.findByText("/run/byul-probe/secret.txt")).closest("tr")!;
    expect(within(tableRow).getByText("합성")).toBeTruthy();
    expect(within(tableRow).getByText("차단")).toBeTruthy();
    expect(tableRow.textContent).toContain("차단 (Override)");
    expect(tableRow.textContent).toContain("TG-SECRET-01 · 차단 시도 기록(enforce)");
    view.unmount();
    renderAt("/runtime/events", "/runtime/events", <EventsPage />);
    const eventRow = within(await screen.findByRole("table")).getByText("프로세스·파일 이벤트 (Tetragon)").closest("tr")!;
    expect(within(eventRow).getByText("예시").title).toBe("EXAMPLE (synthetic)");
  });

  it("Hubble 사건(DROPPED 포함)은 거부 확인으로 표시되지 않는다", async () => {
    mockApi({
      "/api/runtime/events": { items: EVENTS, invalid: [], source: SOURCE, truncated: false },
      "/api/environments": EMPTY_LIST,
      "/api/update/results": { items: [], invalid: [], source: SOURCE },
    });
    renderAt("/runtime/events", "/runtime/events", <EventsPage />);
    const table = await screen.findByRole("table");
    const hubbleRow = within(table).getByText("네트워크 이벤트 (Hubble)").closest("tr")!;
    expect(hubbleRow.textContent).toContain("관측");
    expect(hubbleRow.textContent).not.toContain("거부 확인");
    const tetragonRow = within(table).getByText("프로세스·파일 이벤트 (Tetragon)").closest("tr")!;
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
    expect(await screen.findByText("런타임 연결 단절")).toBeTruthy();
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
    expect(cellOf("사유")).toBe("JSON 외 미디어 유형 (엔진 미전달)");
    expect(cellOf("엔진전달")).toBe("미전달");
    expect(cellOf("결과")).toBe("입력 오류");
    expect(cellOf("환경·실행")).toBe("환경 미기록실행 미기록");
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

  it("필터: 검색·결과·스트리밍", () => {
    const rows = [row({ request_id: "b".repeat(32), model: "test-model", outcome: "allowed", stream: true }), row({})];
    expect(filterRequests(rows, { q: "TEST" })).toHaveLength(1);
    expect(filterRequests(rows, { outcome: "invalid" })).toHaveLength(1);
    expect(filterRequests(rows, { stream: "true" })[0].model).toBe("test-model");
    expect(filterRequests(rows, {})).toHaveLength(2);
  });
});

describe("차단 내역", () => {
  const hubble = EVENTS.find((e) => e.source === "hubble")!;
  const tetragon = EVENTS.find((e) => e.source === "tetragon")!;
  const dropped: RuntimeEvent = { ...hubble, event_id: "drop-1", observation: { ...hubble.observation!, verdict: "DROPPED" } };
  const override: RuntimeEvent = {
    ...tetragon, event_id: "file-override", outcome: "enforce_attempted", origin_detail: "synthetic",
    native: { ...tetragon.native, record: { ...tetragon.native.record, action_raw: "KPROBE_ACTION_OVERRIDE" } as unknown as RuntimeEvent["native"]["record"] },
    observation: { ...tetragon.observation!, kind: "file_access", path: "/tmp/secret", action_mode: "enforce", rule_id: "TG-1" },
  };

  it("미전달 요청·DROPPED·파일 Override만 담고 탐지는 제외한다", () => {
    const rows = blockedRows(
      [row({}), row({ request_id: "b".repeat(32), outcome: "denied", delivery_attempted: true }), row({ request_id: "c".repeat(32), outcome: "allowed", delivery_attempted: false })],
      [dropped, { ...hubble, event_id: "allow-1", observation: { ...hubble.observation!, verdict: "FORWARDED" } }, override, { ...override, event_id: "detected-1", outcome: "detected", native: tetragon.native }],
    );
    expect(rows.requests).toHaveLength(1);
    expect(rows.network).toHaveLength(1);
    expect(rows.files).toHaveLength(1);
  });

  it("세 구역의 건수·요청 상세 링크·합성 표지를 표시한다", async () => {
    mockApi({
      "/api/runtime/requests": { items: [row({})], invalid: [], source: SOURCE, truncated: false },
      "/api/runtime/events": { items: [dropped, override], invalid: [], source: SOURCE, truncated: false },
      "/api/environments": EMPTY_LIST,
    });
    renderAt("/runtime/blocked?section=requests", "/runtime/blocked", <BlockedPage />);
    const requestSection = await screen.findByRole("region", { name: "엔진에 전달되지 않은 요청" });
    expect(requestSection.textContent).toContain("1건");
    expect(within(requestSection).getByRole("link", { name: "a".repeat(32) }).getAttribute("href")).toContain(`sel=${"a".repeat(32)}`);
    const networkTab = screen.getByRole("tab", { name: "네트워크 차단 1건" });
    const filesTab = screen.getByRole("tab", { name: "파일 차단 시도 1건" });
    expect(screen.queryByRole("navigation", { name: "페이지 이동" })).toBeNull();
    expect(screen.queryByRole("region", { name: "네트워크 차단" })).toBeNull();
    fireEvent.click(networkTab);
    expect(networkTab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("region", { name: "네트워크 차단" }).textContent).toContain("1건");
    expect(screen.queryByRole("region", { name: "엔진에 전달되지 않은 요청" })).toBeNull();
    fireEvent.click(filesTab);
    expect(filesTab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("region", { name: "파일 차단 시도" }).textContent).toContain("예시 자료");
  });

  it("빈 구역마다 이유를 표시한다", async () => {
    mockApi({
      "/api/runtime/requests": { items: [], invalid: [], source: SOURCE, truncated: false },
      "/api/runtime/events": { items: [], invalid: [], source: SOURCE, truncated: false },
      "/api/environments": EMPTY_LIST,
    });
    renderAt("/runtime/blocked", "/runtime/blocked", <BlockedPage />);
    expect(await screen.findByText(/엔진 미전달 거부·입력 오류·처리 오류 요청이 없습니다/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "네트워크 차단 0건" }));
    expect(screen.getByText(/Hubble DROPPED 기록이 없습니다/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "파일 차단 시도 0건" }));
    expect(screen.getByText(/Tetragon Override 파일 차단 시도 기록이 없습니다/)).toBeTruthy();
  });

  it("구역 쿼리·해시로 탭을 선택하고 탭마다 25행씩 페이지를 전환한다", async () => {
    const requests = Array.from({ length: 27 }, (_, index) => row({ request_id: String(index).padStart(32, "0") }));
    mockApi({
      "/api/runtime/requests": { items: requests, invalid: [], source: SOURCE, truncated: false },
      "/api/runtime/events": { items: [dropped, override], invalid: [], source: SOURCE, truncated: false },
    });
    const view = renderAt("/runtime/blocked?section=requests&page=2", "/runtime/blocked", <BlockedPage />);
    const section = await screen.findByRole("region", { name: "엔진에 전달되지 않은 요청" });
    expect(within(section).getAllByRole("row")).toHaveLength(3);
    expect(within(section).getByText("26–27 / 27")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "네트워크 차단 1건" }));
    expect(screen.getByRole("region", { name: "네트워크 차단" })).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "페이지 이동" })).toBeNull();
    view.unmount();
    renderAt("/runtime/blocked#blocked-files", "/runtime/blocked", <BlockedPage />);
    expect((await screen.findByRole("tab", { name: "파일 차단 시도 1건" })).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("region", { name: "파일 차단 시도" })).toBeTruthy();
  });
});
