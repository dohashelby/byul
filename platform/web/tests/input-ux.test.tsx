import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { InputCatalog, PolicyDoc, PolicyInputPreview } from "../src/api/types";
import { InputIssueSummary } from "../src/components/InputEditor";
import { TextField } from "../src/components/Form";
import { Segmented } from "../src/components/Choice";
import { hasConfirmedAnswer, inputIssueGroup } from "../src/lib/inputUx";
import { PolicyEditorPage } from "../src/pages/DocumentsPage";
import { PolicyForm, newPolicy } from "../src/pages/forms";
import { mockApi, renderAt } from "./helpers";

const catalog: InputCatalog = { pack: { id: "PACK-BASELINE", revision: 34 }, available: false, reason: "Pack 34 준비 미완료", versions: [], bindings: [], parameters: {}, fixed_values: {}, limitations: [] };
function policy(): PolicyDoc {
  return { ...newPolicy(), policy_id: "ux-policy", target_ref: "demo-target", verification: { ...newPolicy().verification, pack: { id: "PACK-BASELINE", revision: 34 } },
    runtime: { ...newPolicy().runtime, gateway: { ...newPolicy().runtime.gateway, allowed_models: ["demo-model"] } } };
}
const meta = { doc_id: "ux-policy", revision: 1, status: "DRAFT", sha256: "a".repeat(64), label: "1", saved_at: "2026-11-12T00:00:00Z" };
function routes(doc = policy(), preview?: Partial<PolicyInputPreview>) {
  return { "/api/inputs/catalog": catalog, "/api/environments": { items: [] },
    "/api/policies/ux-policy/revisions": { items: [meta] },
    "/api/policies/ux-policy/revisions/1": { meta, document: doc },
    "/api/inputs/policy-preview": { document: doc, errors: [], warnings: [], gaps: [], connected: [], pack_available: false, engine_executed: false, ...preview },
  };
}

describe("input UX boundaries", () => {
  it("keeps unknown answers incomplete while recognizing explicit false and empty lists", () => {
    expect(hasConfirmedAnswer(null)).toBe(false);
    expect(hasConfirmedAnswer({ torch: null })).toBe(false);
    expect(hasConfirmedAnswer(["a", null])).toBe(false);
    expect(hasConfirmedAnswer(false)).toBe(true);
    expect(hasConfirmedAnswer([])).toBe(true);
    expect(hasConfirmedAnswer(0)).toBe(true);
  });

  it("opens a collapsed error location and focuses its field without changing its value", () => {
    const issue = { path: "/target/engine/version", message: "판본을 확인하세요." };
    renderAt("/", "/", <><InputIssueSummary issues={[issue]} /><details><summary>엔진 입력</summary>
      <TextField ctx={{ doc: { target: { engine: { version: "preserved" } } }, set: vi.fn(), issues: [issue], warnings: [] }} path={issue.path} label="엔진 판본" />
    </details></>);
    const field = screen.getByLabelText("엔진 판본") as HTMLInputElement;
    expect(field.closest("details")!.open).toBe(false);
    fireEvent.click(within(screen.getByRole("alert", { name: "입력 내용을 확인해 주세요" })).getByRole("button"));
    expect(field.closest("details")!.open).toBe(true);
    expect(document.activeElement).toBe(field);
    expect(field.value).toBe("preserved");
    expect(field.getAttribute("aria-invalid")).toBe("true");
  });

  it("supports arrow keys, Home and End in segmented radio controls", () => {
    function Harness() { const [value, setValue] = useState<unknown>(); return <Segmented label="확인 상태" value={value} onChange={setValue} options={[{ value: true, label: "예" }, { value: false, label: "아니오" }, { value: null, label: "모름" }]} />; }
    renderAt("/", "/", <Harness />);
    const yes = screen.getByRole("radio", { name: "예" });
    fireEvent.keyDown(yes, { key: "ArrowRight" });
    const no = screen.getByRole("radio", { name: "아니오" });
    expect(no.getAttribute("aria-checked")).toBe("true");
    expect(document.activeElement).toBe(no);
    fireEvent.keyDown(no, { key: "End" });
    expect(screen.getByRole("radio", { name: "모름" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.keyDown(screen.getByRole("radio", { name: "모름" }), { key: "Home" });
    expect(yes.getAttribute("aria-checked")).toBe("true");
  });

  it("separates customer work from generated sensor references", async () => {
    const gaps = [{ path: "/verification/values/REQ-15/sensor_target_sha256", message: "준비 값이 없습니다." },
      { path: "/environment_ref", message: "연결할 환경을 선택하세요." },
      { path: "/framework_values/requirements/5.1/values/allowed_return", message: "현재 검증에 연결되지 않음" }];
    expect(gaps.map(inputIssueGroup)).toEqual(["system", "customer", "connection"]);
    mockApi(routes(policy(), { gaps }));
    renderAt("/", "/", <PolicyForm doc={policy()} setDoc={() => {}} issues={[]} warnings={[]} isNew />);
    const system = await screen.findByText("시스템 준비·수집에서 확보할 내용");
    expect(system.closest("details")?.textContent).toContain("고객이 직접 만들어 입력할 항목이 아닙니다");
    expect(screen.getByText("입력에서 확인할 내용").closest("details")?.textContent).not.toContain("sensor_target_sha256");
  });

  it("does not count measurement-pending numbers as a completed policy card", async () => {
    const doc = { ...policy(), framework_values: { catalog_version: "byul-framework-input/0.1", framework_checklist_sha256: null,
      requirements: { "5.6": { decision: "change", values: { acceptance: "bounded" }, numbers: { max_inference_rate: { value: null, unit: "%", scope: "공격자 관측 범위별", condition: null, status: "after_measurement" } }, basis: null, note: null } },
      approval_terms: { decision: null, scope: null, conditions: null } } } as unknown as PolicyDoc;
    mockApi(routes(doc));
    renderAt("/", "/", <PolicyForm doc={doc} setDoc={() => {}} issues={[]} warnings={[]} isNew />);
    expect(screen.getByText("측정 후 결정할 수치가 있습니다")).toBeTruthy();
    expect(screen.getAllByText("입력 확정 0/35").length).toBeGreaterThan(0);
  });

  it("blocks saving an invalid JSON buffer instead of silently saving the previous value", async () => {
    const calls = mockApi(routes());
    renderAt("/policies/ux-policy", "/policies/:id", <PolicyEditorPage />);
    await screen.findByDisplayValue("ux-policy");
    fireEvent.click(screen.getByRole("link", { name: "고급(JSON)" }));
    expect(document.getElementById("pol-advanced")?.hasAttribute("open")).toBe(true);
    const json = screen.getByLabelText(/점검별 정책 값/);
    fireEvent.change(json, { target: { value: "{" } });
    const save = screen.getByRole("button", { name: "새 판본으로 저장 (DRAFT)" }) as HTMLButtonElement;
    await waitFor(() => expect(save.disabled).toBe(true));
    fireEvent.click(save);
    expect(calls.some((call) => call.method === "POST" && call.url === "/api/policies")).toBe(false);
    expect((json as HTMLTextAreaElement).value).toBe("{");
    expect(screen.getByRole("textbox", { name: "점검별 정책 값 (check_id → 값)" })).toBe(json);
    fireEvent.change(json, { target: { value: "{}" } });
    await waitFor(() => expect(save.disabled).toBe(false));
  });

  it("shows the stored revision after reopening without claiming unsaved input", async () => {
    mockApi(routes());
    renderAt("/policies/ux-policy", "/policies/:id", <PolicyEditorPage />);
    await screen.findByDisplayValue("ux-policy");
    expect(await screen.findByText("판본 1로 저장했습니다.")).toBeTruthy();
    expect(screen.queryByText("아직 저장하지 않음")).toBeNull();
    expect(screen.queryByText("미저장 변경 있음")).toBeNull();
  });

  it("shows a recoverable load failure instead of leaving the editor loading forever", async () => {
    mockApi(routes());
    const fallback = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => String(input) === "/api/policies/ux-policy/revisions/1"
      ? Promise.resolve(new Response(JSON.stringify({ code: "UNAVAILABLE", message: "연결 실패" }), { status: 503 })) : fallback(input, init)));
    renderAt("/policies/ux-policy", "/policies/:id", <PolicyEditorPage />);
    expect(await screen.findByRole("button", { name: "다시 불러오기" })).toBeTruthy();
    expect(screen.getByText(/저장된 입력을 불러오지 못했습니다/)).toBeTruthy();
  });

  it("preserves edits after a failed save and links the error to the actual control", async () => {
    mockApi(routes());
    const fallback = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => String(input) === "/api/policies" && init?.method === "POST"
      ? Promise.resolve(new Response(JSON.stringify({ code: "SCHEMA_INVALID", errors: [{ path: "/target_ref", message: "대상을 다시 확인하세요." }] }), { status: 422 })) : fallback(input, init)));
    renderAt("/policies/ux-policy", "/policies/:id", <PolicyEditorPage />);
    await screen.findByDisplayValue("ux-policy");
    const target = screen.getByLabelText(/대상 ID \(target_ref\)/) as HTMLInputElement;
    fireEvent.change(target, { target: { value: "edited-target" } });
    expect(screen.getByText("미저장 변경 있음")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "새 판본으로 저장 (DRAFT)" }));
    await screen.findByText("저장 실패 · 입력 확인 필요");
    expect(target.value).toBe("edited-target");
    fireEvent.click(within(screen.getByRole("alert", { name: "입력 내용을 확인해 주세요" })).getByRole("button"));
    expect(document.activeElement).toBe(target);
  });

  it("locks fields during save and clears the dirty state only after the server confirms", async () => {
    mockApi(routes());
    const fallback = globalThis.fetch;
    let finish: (response: Response) => void = () => {};
    let submitted = policy();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) !== "/api/policies" || init?.method !== "POST") return fallback(input, init);
      submitted = JSON.parse(String(init.body));
      return new Promise<Response>((resolve) => { finish = resolve; });
    }));
    renderAt("/policies/ux-policy", "/policies/:id", <PolicyEditorPage />);
    await screen.findByDisplayValue("ux-policy");
    fireEvent.change(screen.getByLabelText(/대상 ID \(target_ref\)/), { target: { value: "edited-target" } });
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "새 판본으로 저장 (DRAFT)" }));
    await waitFor(() => expect(screen.getByRole("group", { name: "정책 입력" }).hasAttribute("disabled")).toBe(true));
    finish(new Response(JSON.stringify({ saved: { meta: { ...meta, revision: 2 }, document: submitted }, warnings: [] }), { status: 201 }));
    await screen.findByText("판본 2로 저장했습니다.");
    expect((screen.getByLabelText(/대상 ID \(target_ref\)/) as HTMLInputElement).value).toBe("edited-target");
    expect(screen.queryByText("미저장 변경 있음")).toBeNull();
    const savedUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(savedUnload);
    expect(savedUnload.defaultPrevented).toBe(false);
  });
});
