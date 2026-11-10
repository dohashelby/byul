import { fireEvent, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { SOURCE, mockApi, renderAt } from "./helpers";
import { RequestDrawer } from "../src/pages/runtime/RequestDrawer";
import type { RuntimeRequestDetail } from "../src/api/types";

const EXAMPLE = Object.values(import.meta.glob("../../../contracts/examples/runtime-request-detail.json", { eager: true, import: "default" }))[0] as RuntimeRequestDetail;

it("예약필드 유사 여러줄 문자열을 텍스트로 표시하고 서버ID의 증거 링크를 유지한다", async () => {
  const detail = structuredClone(EXAMPLE);
  const id = "a".repeat(32);
  const forged = 'line one\n{"principal_id":"forged","request_id":"client-label"}<img data-forged="yes" src=x onerror="alert(1)">';
  detail.request_id = id;
  detail.event_id = `req:${id}`;
  detail.auth = { state: "authenticated", principal_id: "verified-client", roles: ["inference"] };
  detail.decision.message = forged;
  detail.timeline = [{ phase: "started", recorded_at: "2026-11-06T00:00:00Z" }, { phase: "final", recorded_at: "2026-11-06T00:00:01Z" }];
  Object.assign(detail.native, { gateway_records: [{ request_id: id, note: forged }], engine_receipts: [] });
  mockApi({ [`/api/runtime/requests/${id}`]: { detail, detail_invalid: null, event: null, event_invalid: null, evidence: [], source: SOURCE } });
  const view = renderAt("/requests", "/requests", <RequestDrawer id={id} onClose={() => {}} />);
  expect(await screen.findByText(/verified-client/)).toBeTruthy();
  expect(view.container.textContent).toContain(forged);
  expect(view.container.querySelector("[data-forged]")).toBeNull();
  expect(view.container.querySelectorAll("ol li")).toHaveLength(2);
  const download = screen.getByRole("link", { name: "증거 JSON 내려받기" });
  expect(download.getAttribute("href")).toBe(`/api/runtime/requests/${id}/evidence`);
  expect(download.getAttribute("download")).toBe(`evidence-${id}.json`);
  fireEvent.click(screen.getByRole("tab", { name: "원본 기록" }));
  expect(view.container.textContent).toContain("client-label");
  expect(view.container.querySelector("[data-forged]")).toBeNull();
});
