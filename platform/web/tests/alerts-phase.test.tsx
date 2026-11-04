import { screen } from "@testing-library/react";
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
    expect(groups.find((group) => group.items[0].event_id === "blocked")?.title).toContain("차단됨");
    expect(groups.find((group) => group.items[0].event_id === "detected")?.title).not.toContain("차단됨");
  });
  it("차단 알림 행에 별도 표지를 표시한다", async () => {
    mockApi({ "/api/runtime/events": { items: [secret("blocked", true)], invalid: [], source: SOURCE, truncated: false } });
    renderAt("/runtime/alerts", "/runtime/alerts", <AlertsPage />);
    expect(await screen.findByText(/차단됨 · Override/)).toBeTruthy();
    expect(screen.getAllByText("차단")).toHaveLength(2);
  });
  it("알림 페이지에 건수와 자세히 연결을 보여준다", async () => {
    mockApi({ "/api/runtime/events": { items: [flagged("a", "suspect_binary"), flagged("b", "suspect_binary")], invalid: [], source: SOURCE, truncated: false } });
    renderAt("/runtime/alerts", "/runtime/alerts", <AlertsPage />);
    expect(await screen.findByText(/동일 판정·실행 파일/)).toBeTruthy();
    expect(screen.getByText(/셸 실행 — 의심 실행/)).toBeTruthy();
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
    const requests = Array.from({ length: 56 }, (_, index) => ({ recorded_at: at, outcome: index === 0 ? "denied" : "allowed", target_ref: null, reason_code: index === 0 ? "MODEL_NOT_ALLOWED" : null }));
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
    const chart = (await screen.findByText("시간별 요청·관측 이벤트")).closest("article")!;
    expect(Array.from(chart.querySelectorAll("strong")).map((node) => node.textContent)).toEqual(["56", "5"]);
    expect(chart.textContent).toContain("56요청");
    expect(chart.textContent).toContain("5관측 이벤트");
    expect(screen.queryByText("117")).toBeNull();
    expect(screen.getByText("검토 대상 탐지 알림 1묶음")).toBeTruthy();
    expect(screen.getByText("거부·오류 사유").parentElement?.textContent).toContain("1");
  });
});
