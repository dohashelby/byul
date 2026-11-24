import { fireEvent, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { RequestDrawer } from "../src/pages/runtime/RequestDrawer";
import { HubbleEvents } from "../src/pages/runtime/HubbleEvents";
import type { RuntimeEvent, RuntimeRequestDetail } from "../src/api/types";
import { SOURCE, mockApi, renderAt } from "./helpers";

const detailExample = Object.values(import.meta.glob("../../../contracts/examples/runtime-request-detail.json", { eager: true, import: "default" }))[0] as RuntimeRequestDetail;
const raw = Object.values(import.meta.glob("../../../contracts/examples/runtime-events.jsonl", { eager: true, query: "?raw", import: "default" }))[0] as string;
const hubble = raw.split("\n").filter(Boolean).map((line) => JSON.parse(line) as RuntimeEvent).find((event) => event.source === "hubble")!;

function showRequest(detail: RuntimeRequestDetail) {
  mockApi({ [`/api/runtime/requests/${detail.request_id}`]: { detail, detail_invalid: null, event: null, evidence: [], source: SOURCE } });
  return renderAt("/requests", "/requests", <RequestDrawer id={detail.request_id} onClose={() => {}} />);
}

it("코드 선택 거부의 코드·대상 필드·HTTP·미전달 상태를 접지 않고 보여 준다", async () => {
  const detail = structuredClone(detailExample);
  Object.assign(detail.decision, { reason_code: "CODE_SELECTION_FIELD", message: "mm_processor_kwargs.code_revision", http_status: 400 });
  Object.assign(detail.delivery, { attempted: false, response: "not_sent" });
  detail.outcome = "denied";
  detail.policy.sha256 = "a".repeat(64);
  const view = showRequest(detail);
  const summary = await screen.findByLabelText("요청 판정 요약");
  for (const text of ["CODE_SELECTION_FIELD", "mm_processor_kwargs.code_revision", "400", "전달 안 함"]) {
    const value = within(summary).getByText(text);
    expect(value.closest("details")).toBeNull();
  }
  expect(screen.getAllByText(detail.request_id)).toHaveLength(1);
  expect(view.container.textContent).toContain("예시");
  const policy = screen.getByText("요청·정책 정보").closest("details")!;
  expect(policy.open).toBe(false);
  fireEvent.click(within(policy).getByText("요청·정책 정보"));
  expect(policy.open).toBe(true);
  expect(policy.textContent).toContain(detail.policy.sha256);
  expect(screen.getByRole("link", { name: "증거 파일 내려받기" }).getAttribute("href")).toBe(`/api/runtime/requests/${detail.request_id}/evidence`);
});

it("응답 시작 뒤 실패한 요청의 시작 HTTP와 최종 결과를 함께 보여 준다", async () => {
  const detail = structuredClone(detailExample);
  detail.http_start_status = 200;
  detail.final_outcome = "client_disconnected";
  Object.assign(detail.decision, { http_status: 502 });
  Object.assign(detail.delivery, { attempted: true, response: "started", error_code: "CLIENT_DISCONNECTED" });
  showRequest(detail);
  const summary = await screen.findByLabelText("요청 판정 요약");
  for (const text of ["응답 시작", "200", "502", "클라이언트 연결 끊김"]) expect(within(summary).getByText(text)).toBeTruthy();
  expect(within(summary).queryByText("응답 완료")).toBeNull();
});

it("네트워크 검색은 Pod·포트를 함께 대조하고 같은 이벤트의 원본을 연다", async () => {
  const event = structuredClone(hubble);
  event.event_id = "synthetic-network-1";
  Object.assign(event.observation!, { src_pod: "attack-client", dst_pod: "serving-engine", dst_port: 8080, verdict: "DROPPED", reason: "POLICY_DENIED", dst_ip: null });
  const other = structuredClone(event);
  other.event_id = "synthetic-network-2";
  other.observation!.src_pod = "other-client";
  Object.assign(event.observation!, { dst_pod: null, dst_ip: "192.0.2.10" });
  mockApi({ "/api/runtime/events": { items: [other, event], invalid: [], source: SOURCE } });
  renderAt("/hubble", "/hubble", <HubbleEvents />);
  await screen.findAllByText("POLICY_DENIED");
  fireEvent.change(screen.getByRole("textbox", { name: "네트워크 검색" }), { target: { value: "attack-client 8080" } });
  const table = screen.getByRole("table");
  expect(table.querySelectorAll("tbody tr")).toHaveLength(1);
  expect(table.textContent).toContain("DROPPED");
  expect(table.textContent).toContain("흐름 드롭 관측");
  expect(table.textContent).toContain("192.0.2.10");
  expect(table.textContent).toContain("POLICY_DENIED");
  expect(table.textContent).not.toContain("other-client");
  fireEvent.click(screen.getByRole("button", { name: "기록 보기" }));
  expect(screen.getByText(event.event_id)).toBeTruthy();
  expect(screen.getByRole("tabpanel").textContent).toContain("192.0.2.10");
  fireEvent.click(screen.getByRole("tab", { name: "원본 기록" }));
  expect(screen.getByRole("tabpanel").textContent).toContain(event.event_id);
  expect(screen.getByRole("tabpanel").textContent).toContain("POLICY_DENIED");
});

it("네트워크 API 실패를 기록 없음으로 표시하지 않는다", async () => {
  mockApi({});
  renderAt("/hubble", "/hubble", <HubbleEvents />);
  expect(await screen.findByText(/NOT_FOUND/)).toBeTruthy();
  expect(screen.queryByText(/네트워크 기록이 없습니다/)).toBeNull();
});

it("네트워크 목록은 선택한 기간과 대상 환경만 보여 준다", async () => {
  const event = structuredClone(hubble);
  event.target_ref = "demo-engine";
  event.event_id = "selected-event";
  event.occurred_at = "2026-11-16T01:00:00Z";
  const old = structuredClone(event);
  old.event_id = "old-event";
  old.occurred_at = "2026-11-15T01:00:00Z";
  const other = structuredClone(event);
  other.event_id = "other-target";
  other.target_ref = "other-engine";
  mockApi({
    "/api/runtime/events": { items: [old, other, event], invalid: [], source: SOURCE },
    "/api/environments": { items: [{ doc_id: "demo", target_ref: "demo-engine" }], invalid: [], source: SOURCE },
  });
  renderAt("/hubble?env=demo&period=custom&from=2026-11-16T00:00:00Z&to=2026-11-17T00:00:00Z", "/hubble", <HubbleEvents />);
  await screen.findByText("POLICY_DENIED");
  expect(screen.getByRole("table").querySelectorAll("tbody tr")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "기록 보기" }));
  expect(screen.getByText("selected-event")).toBeTruthy();
  expect(screen.queryByText("old-event")).toBeNull();
  expect(screen.queryByText("other-target")).toBeNull();
});
