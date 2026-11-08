import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { isValidElement } from "react";
import { describe, expect, it } from "vitest";
import { AlertsPage } from "../src/pages/runtime/AlertsPage";
import { PhaseTimelinePage } from "../src/pages/runtime/PhaseTimelinePage";
import { groupAlerts } from "../src/lib/alerts";
import { isTetragonOverride } from "../src/lib/tetragon";
import { alertSeverity, GATEWAY_REASON, PHASE, EXEC_VERDICT } from "../src/lib/labels";
import type { RuntimeEvent } from "../src/api/types";
import { SOURCE, mockApi, renderAt } from "./helpers";

const raw = Object.values(import.meta.glob("../../../contracts/examples/runtime-events.jsonl", { eager: true, query: "?raw", import: "default" }))[0] as string;
const events = raw.split("\n").filter(Boolean).map((line) => JSON.parse(line)) as RuntimeEvent[];
const sample = events.find((e) => e.source === "tetragon")!;

function secret(id: string, override: boolean): RuntimeEvent {
  return {
    ...sample, event_id: id, source: "tetragon", outcome: override ? "enforce_attempted" : "detected",
    native: { ...sample.native, record: { ...sample.native.record, action_raw: override ? "KPROBE_ACTION_OVERRIDE" : "KPROBE_ACTION_POST" } as unknown as RuntimeEvent["native"]["record"] },
    observation: { ...sample.observation!, kind: "file_access", rule_id: "TG-SECRET-01", path: "/tmp/fake-secret", exec_verdict: null, action_mode: override ? "enforce" : "monitor", phases: [] },
  };
}

function flagged(id: string, verdict: string): RuntimeEvent {
  return { ...sample, event_id: id, occurred_at: "2026-11-04T10:00:00Z", observation: { ...sample.observation!, kind: "process_exec", binary: "/bin/sh", exec_verdict: verdict, phases: ["compiling"] } };
}

describe("알림 심각도와 묶음", () => {
  it("Gateway 사유 59개를 전문 용어로 표시하고 미전달 사유를 명시한다", () => {
    expect(Object.keys(GATEWAY_REASON)).toHaveLength(59);
    expect(GATEWAY_REASON.TARGET_UNVERIFIED).toBe("대상 미확인 (엔진 미전달)");
    expect(GATEWAY_REASON.REQUEST_HEADER_TIMEOUT).toBe("요청 헤더 수신 시간 초과 (엔진 미전달)");
    expect(GATEWAY_REASON.UNSUPPORTED_MEDIA_TYPE).toBe("JSON 외 미디어 유형 (엔진 미전달)");
    expect(Object.values(GATEWAY_REASON).join(" ")).not.toMatch(/안 됨|보내지 않음|머리말|달라짐/);
    expect(PHASE.loading).toBe("모델 적재");
    expect(PHASE.compiling).toBe("컴파일");
    expect(EXEC_VERDICT.exec_from_cache).toBe("캐시 경로 실행");
  });
  it("표에 없는 판정은 미분류이며 보호 파일은 높음이다", () => {
    expect(alertSeverity("unlisted", null, "tetragon")).toBe("미분류");
    expect(alertSeverity("allowed_in_phase", "TG-SECRET-01", "tetragon")).toBe("높음");
    expect(alertSeverity("DROPPED", null, "hubble")).toBe("정보");
  });
  it("같은 판정·실행 파일·단계를 묶고 심각도 순서로 정렬한다", () => {
    const groups = groupAlerts([flagged("a", "outside_allowlist"), flagged("b", "suspect_binary"), flagged("c", "suspect_binary")]);
    expect(groups[0].severity).toBe("높음");
    expect(groups[0].items).toHaveLength(2);
    expect(groups[1].severity).toBe("중간");
  });
  it("보호 파일 Override만 차단으로 보고 같은 규칙의 탐지와 별도 묶음으로 표시한다", () => {
    const detected = secret("detected", false);
    const blocked = secret("blocked", true);
    expect(isTetragonOverride(blocked)).toBe(true);
    expect(isTetragonOverride({ ...blocked, native: detected.native })).toBe(false);
    const groups = groupAlerts([detected, blocked]);
    expect(groups).toHaveLength(2);
    expect(groups.map((group) => group.severity)).toEqual(["높음", "높음"]);
    expect(groups.find((group) => group.items[0].event_id === "blocked")?.title).toContain("차단 시도");
    expect(groups.find((group) => group.items[0].event_id === "detected")?.title).not.toContain("차단됨");
  });
  it("차단 알림 행에 별도 표지를 표시한다", async () => {
    mockApi({ "/api/runtime/events": { items: [secret("blocked", true)], invalid: [], source: SOURCE, truncated: false } });
    renderAt("/runtime/alerts", "/runtime/alerts", <AlertsPage />);
    expect(await screen.findByText(/차단 시도 · Override/)).toBeTruthy();
    expect(screen.getAllByText("차단 시도")).toHaveLength(2);
  });
  it("알림 페이지에 건수와 자세히 연결을 보여준다", async () => {
    mockApi({ "/api/runtime/events": { items: [flagged("a", "suspect_binary"), flagged("b", "suspect_binary")], invalid: [], source: SOURCE, truncated: false } });
    renderAt("/runtime/alerts", "/runtime/alerts", <AlertsPage />);
    expect(await screen.findByText(/동일 판정·실행 파일/)).toBeTruthy();
    expect(screen.getByText(/셸 실행 — 의심 실행/)).toBeTruthy();
  });
  it("탐지 알림 묶음 목록을 URL 페이지에서 25개씩 보여준다", async () => {
    const items = Array.from({ length: 27 }, (_, index) => {
      const event = flagged(`group-${index}`, "suspect_binary");
      return { ...event, observation: { ...event.observation!, binary: `/bin/tool-${index}` } };
    });
    mockApi({ "/api/runtime/events": { items, invalid: [], source: SOURCE, truncated: false } });
    renderAt("/runtime/alerts?page=2", "/runtime/alerts", <AlertsPage />);
    const nav = await screen.findByRole("navigation", { name: "페이지 이동" });
    expect(nav.textContent).toContain("26–27 / 27");
    expect(screen.getAllByText(/프로세스·파일 탐지/)).toHaveLength(2);
  });
});

describe("엔진 단계", () => {
  it("합성 시간표와 고정 크기 라벨, 같은 시각의 실행 점을 표시한다", async () => {
    mockApi({
      "/api/runtime/phase-timeline": { entries: [{ phase: "loading", from: "2026-11-04T09:00:00Z", until: "2026-11-04T09:10:00Z", basis: "engine_signal" }], collector: { state: "example", written_at: "2026-11-04T09:10:00Z", fresh: true }, extraction: { engine: "sglang", support_level: "example", limitations: [] }, data_origin: "synthetic", source: SOURCE },
      "/api/runtime/events": { items: [{ ...flagged("point", "suspect_binary"), recorded_at: "2026-11-04T09:05:00Z", occurred_at: "2026-11-04T09:05:00Z" }], invalid: [], source: SOURCE, truncated: false },
    });
    renderAt("/runtime/observe/phases", "/runtime/observe/phases", <PhaseTimelinePage />);
    expect(await screen.findByText(/엔진 실행 단계별 프로세스 판정 기준/)).toBeTruthy();
    expect(screen.getByText(/단계 근거 미검증 \(엔진 로그\)/)).toBeTruthy();
    const chart = screen.getByRole("img", { name: /엔진 실행 단계 1개와 실행 기록 1건/ });
    expect(chart.querySelectorAll("circle")).toHaveLength(1);
    expect(chart.querySelectorAll("text")).toHaveLength(0);
    expect(screen.queryByText(/engine_signal/)).toBeNull();
  });
});

describe("통합 개요", () => {
  it("요청 통제·네트워크 차단 카드가 차단 내역의 해당 구역으로 이동한다", async () => {
    const { DashboardPage } = await import("../src/pages/DashboardPage");
    mockApi({
      "/api/overview": { status: null, requests: null, latest_verification: null, open_revalidation: 0 },
      "/api/runtime/requests": { items: [], invalid: [], source: SOURCE, truncated: false },
      "/api/runtime/events": { items: [], invalid: [], source: SOURCE, truncated: false },
    });
    function Destination() { const location = useLocation(); return <span>{location.pathname}{location.search}</span>; }
    for (const [button, section] of [["차단 내역의 엔진 미전달 요청 보기", "requests"], ["차단 내역의 네트워크 차단 보기", "network"]]) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/runtime/overview"]}><Routes>
        <Route path="/runtime/overview" element={<DashboardPage />} />
        <Route path="/runtime/blocked" element={<Destination />} />
      </Routes></MemoryRouter></QueryClientProvider>);
      fireEvent.click(await screen.findByRole("button", { name: button }));
      expect(await screen.findByText(`/runtime/blocked?section=${section}`)).toBeTruthy();
      cleanup();
    }
  });
  it("두 주소가 같은 화면을 가리키고 Gateway 사건을 관측 건수에 다시 더하지 않는다", async () => {
    const { routes } = await import("../src/app/App");
    const children = routes[1].children!;
    const dashboard = children.find((r) => r.path === "dashboard")?.element;
    const overview = children.find((r) => r.path === "runtime/overview")?.element;
    expect(isValidElement(dashboard) && isValidElement(overview) && dashboard.type === overview.type).toBe(true);
    const { DashboardPage } = await import("../src/pages/DashboardPage");
    const at = new Date().toISOString();
    const gateway = events.find((event) => event.source === "gateway")!;
    const hubble = events.find((event) => event.source === "hubble")!;
    const reasonCodes = ["MODEL_NOT_ALLOWED", "AUTH_REQUIRED", "POLICY_EXPIRED", "INVALID_REQUEST", "TARGET_MISMATCH"];
    const requests = [
      ...reasonCodes.flatMap((reasonCode, index) => Array.from({ length: 5 - index }, () => ({ recorded_at: at, outcome: "denied", target_ref: null, reason_code: reasonCode }))),
      ...Array.from({ length: 41 }, () => ({ recorded_at: at, outcome: "allowed", target_ref: null, reason_code: null })),
    ];
    const gatewayEvents = Array.from({ length: 56 }, (_, index) => ({ ...gateway, event_id: `gateway-${index}`, recorded_at: at, occurred_at: at }));
    const observations = [
      ...Array.from({ length: 3 }, (_, index) => ({ ...flagged(`tetragon-${index}`, "suspect_binary"), recorded_at: at, occurred_at: at })),
      ...Array.from({ length: 2 }, (_, index) => ({ ...hubble, event_id: `hubble-${index}`, recorded_at: at, occurred_at: at })),
    ];
    mockApi({
      "/api/overview": { status: null, requests: null, latest_verification: null, open_revalidation: 0 },
      "/api/runtime/requests": { items: requests, invalid: [], source: SOURCE, truncated: false },
      "/api/runtime/events": { items: [...gatewayEvents, ...observations], invalid: [], source: SOURCE, truncated: false },
    });
    renderAt("/dashboard", "/dashboard", <DashboardPage />);
    const chart = (await screen.findByRole("heading", { name: "시간별 요청" })).closest("article")!;
    const eventChart = screen.getByRole("heading", { name: "시간별 관측 이벤트" }).closest("article")!;
    expect(screen.queryByRole("heading", { name: "시간대별 추이" })).toBeNull();
    expect(chart.querySelector(".bigNumber")?.textContent).toBe("56최근 요청");
    expect(eventChart.textContent).toContain("관측 이벤트 5건");
    expect(eventChart.textContent).toContain("기타(관측·허용 등)");
    const totalTab = within(chart).getByRole("tab", { name: "전체" });
    const resultsTab = within(chart).getByRole("tab", { name: "결과별" });
    expect(totalTab.getAttribute("aria-selected")).toBe("true");
    expect(chart.textContent).not.toContain("판정 미기록");
    fireEvent.click(resultsTab);
    expect(resultsTab.getAttribute("aria-selected")).toBe("true");
    expect(chart.textContent).toContain("판정 미기록");
    expect(eventChart.textContent).toContain("관측 이벤트 5건");
    fireEvent.click(totalTab);
    expect(totalTab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("heading", { name: "통제·관측 현황" })).toBeTruthy();
    const chartRow = chart.parentElement!.parentElement!;
    expect(chartRow.children).toHaveLength(2);
    expect(chartRow.className).toContain("dashboardChartRow");
    const left = chartRow.children[0];
    expect(Array.from(left.children, (card) => card.querySelector("h3")?.textContent)).toEqual(["시간별 요청", "시간별 관측 이벤트"]);
    expect(eventChart.querySelector(".bigNumber")).toBeNull();
    const side = chartRow.children[1];
    expect(Array.from(side.children, (card) => card.querySelector("h3")?.textContent)).toEqual(["구성 비율", "거부·오류 사유"]);
    expect(screen.getAllByRole("heading", { name: "구성 비율" })).toHaveLength(1);
    const donut = side.children[0].querySelector(".row")!;
    expect(donut.className).not.toContain("donutCompact");
    expect((donut as HTMLElement).style.height).toBe("240px");
    expect(donut.children).toHaveLength(2);
    expect(donut.children[0].className).toContain("chartBox");
    expect(donut.children[1].className).toContain("stack");
    const legend = Array.from(donut.children[1].children);
    expect(legend).toHaveLength(6);
    expect(legend[0].textContent).toBe("허용41");
    expect(legend[5].textContent).toBe("판정 미기록0");
    expect(legend.every((item) => item.className.includes("nowrap") && (item.lastElementChild as HTMLElement).style.marginLeft === "8px")).toBe(true);
    expect(chartRow.nextElementSibling?.textContent).not.toContain("구성 비율");
    expect(screen.queryByText("117")).toBeNull();
    expect(screen.queryByRole("heading", { name: /검토 대상 탐지 알림/ })).toBeNull();
    const reasons = screen.getByText("거부·오류 사유").closest("article")!;
    const ranked = Array.from(reasons.querySelectorAll("ol li"));
    expect(ranked).toHaveLength(5);
    expect(ranked.map((item) => item.querySelector("button > span:first-child")?.textContent)).toEqual(["1", "2", "3", "4", "5"]);
    expect(ranked[0].querySelector("button > span:first-child")?.className).toContain("reasonRankFirst");
    expect(ranked.slice(1).every((item) => !item.querySelector("button > span:first-child")?.className.includes("reasonRankFirst"))).toBe(true);
    expect(ranked[0].querySelector("small")?.textContent).toBe("엔진 미전달");
    expect(ranked[0].querySelector("button > span:nth-child(2)")?.textContent).toBe("정책 외 모델엔진 미전달");
    expect(ranked.map((item) => item.querySelector("button > span:last-child strong")?.textContent)).toEqual(["5", "4", "3", "2", "1"]);
    expect(reasons.querySelector(".bigNumber")).toBeNull();
  });
  it("거부·오류 사유를 누르면 요청 내역의 사유 필터로 이동한다", async () => {
    const { DashboardPage } = await import("../src/pages/DashboardPage");
    mockApi({
      "/api/overview": { status: null, requests: null, latest_verification: null, open_revalidation: 0 },
      "/api/runtime/requests": { items: [{ recorded_at: new Date().toISOString(), outcome: "denied", target_ref: null, reason_code: "MODEL_NOT_ALLOWED" }], invalid: [], source: SOURCE, truncated: false },
      "/api/runtime/events": { items: [], invalid: [], source: SOURCE, truncated: false },
    });
    function Destination() { const location = useLocation(); return <span>{location.pathname}{location.search}</span>; }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/runtime/overview"]}><Routes>
      <Route path="/runtime/overview" element={<DashboardPage />} />
      <Route path="/runtime/requests" element={<Destination />} />
    </Routes></MemoryRouter></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: /정책 외 모델.*엔진 미전달.*1건/ }));
    expect(await screen.findByText("/runtime/requests?reason=MODEL_NOT_ALLOWED")).toBeTruthy();
  });
});
