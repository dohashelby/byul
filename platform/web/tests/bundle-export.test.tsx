import { fireEvent, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { BundleExport } from "../src/pages/BundleExport";
import type { RevisionMeta } from "../src/api/types";
import { renderAt } from "./helpers";

const meta = (status: "DRAFT" | "CONFIRMED", revision: number): RevisionMeta => ({
  doc_id: "test-policy", revision, status, saved_at: "2026-11-05T00:00:00Z", sha256: "a".repeat(64),
  label: "1", target_ref: "test-target",
});
const env = { environment_id: "test-env", deployment: { kubernetes_context: "test-context", namespace: "test-namespace" } };
const routes: Record<string, unknown> = {
  "/api/environments": { items: [{ ...meta("DRAFT", 1), doc_id: "test-env" }] },
  "/api/environments/test-env/revisions": { items: [{ ...meta("DRAFT", 1), doc_id: "test-env" }] },
  "/api/environments/test-env/revisions/1": { meta: { ...meta("DRAFT", 1), doc_id: "test-env" }, document: env },
  "/api/policies/test-policy/revisions/1": { meta: meta("DRAFT", 1), document: { status: "DRAFT", environment_ref: null } },
  "/api/policies/test-policy/revisions/2": { meta: meta("CONFIRMED", 2), document: { status: "CONFIRMED", environment_ref: null } },
};

it("DRAFT는 내보내기를 막고 사유를 보여준다", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(routes[url]), { status: 200 })));
  renderAt("/", "/", <BundleExport policyId="test-policy" revisions={[meta("DRAFT", 1)]} />);
  expect(await screen.findByText("DRAFT 판본은 승인 후 내보낼 수 있습니다.")).toBeTruthy();
  expect((screen.getByRole("button", { name: "정책 묶음 내보내기" }) as HTMLButtonElement).disabled).toBe(true);
});

it("CONFIRMED에서 누락 칸 경로를 안내한다", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") return new Response(JSON.stringify({ code: "BUNDLE_FIELDS_MISSING", errors: [
      { path: "/deployment/engine_container", message: "묶음 생성에 필요한 값이 없습니다" },
    ] }), { status: 422 });
    return new Response(JSON.stringify(routes[url]), { status: 200 });
  }));
  renderAt("/", "/", <BundleExport policyId="test-policy" revisions={[meta("CONFIRMED", 2)]} />);
  const button = await screen.findByRole("button", { name: "정책 묶음 내보내기" });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button);
  expect((await screen.findByRole("alert")).textContent).toContain("/deployment/engine_container");
});

it("성공하면 묶음 ID와 해시 및 적용 명령을 보여준다", async () => {
  const metadata = { bundle_id: "bundle-test", bundle_sha256: "b".repeat(64), policy_sha256: "c".repeat(64),
    files: [{ role: "target", sha256: "d".repeat(64), bytes: 100 }], server_values_source: "environment_revision", human_input_paths: [] };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") return new Response(new Blob(["archive"]), { status: 200,
      headers: { "X-Byul-Bundle-Metadata": btoa(JSON.stringify(metadata)) } });
    return new Response(JSON.stringify(routes[url]), { status: 200 });
  }));
  vi.stubGlobal("URL", { ...URL, createObjectURL: () => "blob:test", revokeObjectURL: () => {} });
  renderAt("/", "/", <BundleExport policyId="test-policy" revisions={[meta("CONFIRMED", 2)]} />);
  const button = await screen.findByRole("button", { name: "정책 묶음 내보내기" });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button);
  expect((await screen.findByRole("status")).textContent).toContain("bundle-test");
  expect(screen.getByRole("status").textContent).toContain("b".repeat(64));
  expect(screen.getByRole("status").textContent).toContain("--dry-run");
  const command = screen.getByRole("status").querySelector("pre")?.textContent ?? "";
  expect(command).toContain("--context 'test-context' --namespace 'test-namespace'");
  expect(command).not.toContain("\\n+");
});
