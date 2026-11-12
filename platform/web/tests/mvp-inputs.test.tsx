import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { EnvironmentDoc, InputCatalog, PolicyDoc } from "../src/api/types";
import { EnvironmentForm, PolicyForm, decisionComplete, newEnvironment, newPolicy } from "../src/pages/forms";
import { mockApi, renderAt } from "./helpers";

// 합성 메타데이터다. 실제 엔진 기능이나 GPU 실측을 의미하지 않는다.
const catalog: InputCatalog = {
  pack: { id: "PACK-BASELINE", revision: 34 }, available: true, reason: null,
  versions: [
    { engine: "vllm", version: "old", profiles: ["test-old"], features: [], hidden_fields: ["placement.runner"] },
    { engine: "vllm", version: "new", profiles: ["test-new"], features: [], hidden_fields: [] },
  ], bindings: [{ requirement: "1.4", field: "require_non_root", check_id: "PLT-01", key: "require_non_root", type: "bool", source: "values" }],
  parameters: {}, fixed_values: {}, limitations: [],
};

function env(): EnvironmentDoc {
  return { ...newEnvironment(), target: { ...newEnvironment().target, engine: { name: "vllm", version: "old", image_digest: null, source_commit: null } },
    framework_profile: { catalog_version: "byul-framework-input/0.1", framework_checklist_sha256: null, role: "current", profile_status: "observed", absence_claims: {}, conditions: {
      placement: { origin: "DECLARED", source: "보존할 자료", values: { runner: "v1" } },
    } } } as EnvironmentDoc;
}

describe("MVP input preparation", () => {
  it("uses backend version rules, preserves hidden values and shows common fields for unknown versions", async () => {
    mockApi({ "/api/inputs/catalog": catalog });
    let current = env();
    function Harness() {
      const [doc, setDoc] = useState(current);
      current = doc;
      return <EnvironmentForm doc={doc} setDoc={setDoc} issues={[]} warnings={[]} isNew={false} />;
    }
    renderAt("/", "/", <Harness />);
    await waitFor(() => expect(screen.queryByRole("radiogroup", { name: "실행 runner" })).toBeNull());
    expect((current as any).framework_profile.conditions.placement.values.runner).toBe("v1");
    fireEvent.change(screen.getByLabelText("버전"), { target: { value: "new" } });
    expect(await screen.findByRole("radiogroup", { name: "실행 runner" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("버전"), { target: { value: "unregistered" } });
    expect(screen.getByRole("radiogroup", { name: "실행 runner" })).toBeTruthy();
    expect(screen.getByText(/판본별 지원 정보 미확인/)).toBeTruthy();
    expect((current as any).framework_profile.conditions.placement.source).toBe("보존할 자료");
    expect((current as any).framework_profile.profile_status).toBe("observed");
    expect(screen.queryByRole("radiogroup", { name: "환경 역할" })).toBeNull();
    expect(screen.queryByRole("radiogroup", { name: "입력값 상태" })).toBeNull();
  });

  it("separates the three response features without interpreting the old combined answer", async () => {
    mockApi({ "/api/inputs/catalog": catalog });
    const doc = env() as any;
    doc.framework_profile.conditions.features = { origin: null, source: null, values: { echo_logprobs: true } };
    renderAt("/", "/", <EnvironmentForm doc={doc} setDoc={() => {}} issues={[]} warnings={[]} isNew={false} />);
    for (const label of ["입력 원문 반환 — 엔진 활성", "출력 logprobs — 엔진 활성", "입력 logprobs — Gateway 노출"]) {
      expect(screen.getByRole("radiogroup", { name: label }).querySelector('[aria-checked="true"]')).toBeNull();
    }
    expect(doc.framework_profile.conditions.features.values.echo_logprobs).toBe(true);
  });

  it("gets the Pack reference from the server and never exposes a Pack selector", async () => {
    mockApi({ "/api/inputs/catalog": { ...catalog, available: false, reason: "Pack 34 준비 미완료" }, "/api/environments": { items: [] } });
    let current = newPolicy();
    function Harness() {
      const [doc, setDoc] = useState(current);
      current = doc;
      return <PolicyForm doc={doc} setDoc={setDoc} issues={[]} warnings={[]} isNew />;
    }
    renderAt("/", "/", <Harness />);
    await waitFor(() => expect(current.verification.pack).toEqual(catalog.pack));
    const pack = screen.getByLabelText("사용 Pack (백엔드 지정)") as HTMLInputElement;
    expect(pack.readOnly).toBe(true);
    expect(pack.value).toBe("PACK-BASELINE@34");
    expect(screen.getByText("Pack 34 준비 미완료")).toBeTruthy();
    expect(screen.queryByLabelText("팩 판본")).toBeNull();
    expect(screen.queryByLabelText("팩 ID")).toBeNull();
  });

  it("does not count pending decisions or an empty keep as complete", () => {
    const blank = { decision: "needs_confirmation", values: {}, numbers: {}, basis: null, note: null };
    expect(decisionComplete(blank)).toBe(false);
    expect(decisionComplete({ ...blank, decision: "keep" })).toBe(false);
    expect(decisionComplete({ ...blank, decision: "change" })).toBe(true);
    expect(decisionComplete({ ...blank, decision: "keep", values: { allowed_devices: [] } })).toBe(true);
  });

  it("lets the actual input card express the request token limit consumed by REQ-09", () => {
    mockApi({ "/api/inputs/catalog": catalog, "/api/environments": { items: [] } });
    let current = newPolicy();
    function Harness() {
      const [doc, setDoc] = useState(current);
      current = doc;
      return <PolicyForm doc={doc} setDoc={setDoc} issues={[]} warnings={[]} isNew />;
    }
    renderAt("/", "/", <Harness />);
    fireEvent.click(screen.getByText("최종 입력 검증", { exact: true }));
    const card = screen.getByText("최종 입력 검증", { exact: true }).closest("details")!;
    fireEvent.click(within(card).getByRole("radio", { name: "새로 정함" }));
    fireEvent.click(within(screen.getByRole("radiogroup", { name: "입력 길이 한도 상태" })).getByRole("radio", { name: "확정" }));
    fireEvent.change(screen.getByLabelText("입력 길이 한도 값"), { target: { value: "4096" } });
    const number = (current as any).framework_values.requirements["3.1"].numbers.max_input_tokens;
    expect(number).toMatchObject({ value: 4096, unit: "token", scope: "요청별", status: "confirmed" });
  });

  it("displays the actual approved values when keep is selected", async () => {
    const entry = { decision: "change", values: { require_non_root: false }, numbers: {}, basis: "승인 근거", note: null };
    const approved = { ...newPolicy(), policy_id: "p", status: "CONFIRMED", verification: { ...newPolicy().verification, pack: catalog.pack }, framework_values: {
      catalog_version: "byul-framework-input/0.1", framework_checklist_sha256: null, requirements: { "1.4": entry }, approval_terms: { decision: null, scope: null, conditions: null },
    } } as PolicyDoc;
    const draft = { ...approved, status: "DRAFT", framework_values: { ...(approved as any).framework_values, requirements: { "1.4": { ...entry, decision: "keep", values: {} } } } } as PolicyDoc;
    mockApi({ "/api/inputs/catalog": catalog, "/api/environments": { items: [] },
      "/api/policies/p/revisions": { items: [{ revision: 1, status: "CONFIRMED" }] },
      "/api/policies/p/revisions/1": { document: approved },
    });
    renderAt("/", "/", <PolicyForm doc={draft} setDoc={() => {}} issues={[]} warnings={[]} isNew={false} />);
    const field = await screen.findByRole("radiogroup", { name: "비root 실행 요구" });
    await waitFor(() => expect(field.querySelector('[aria-checked="true"]')?.textContent).toBe("아니오"));
  });
});
