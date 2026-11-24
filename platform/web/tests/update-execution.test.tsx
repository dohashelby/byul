import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { UpdateExecutionPage } from "../src/pages/UpdateExecutionPage";
import type { UpdateRun } from "../src/api/updateExecution";
import type { EnvironmentDoc, PolicyDoc } from "../src/api/types";
import { AREAS, areaFor, titleFor } from "../src/app/nav";
import { renderAt } from "./helpers";

const ID = "RUN-" + "a".repeat(32);
const REPORT_REF = { kind: "RPT" as const, id: "RPT-fixture", revision: 2, sha256: "c".repeat(64) };
const REQUEST = { mode: "validate" as const, policy: { id: "policy-one", revision: 1 }, environment: { id: "environment-one", revision: 1 } };
const READY: UpdateRun = { id: ID, request: REQUEST, status: "ready", created_at: "2026-11-15T09:00:00Z",
  requested_by: "admin", input_sha256: "a".repeat(64), source_refs: {}, preparation: { ready: true,
    checks: [{ check: "inputs", ok: true, detail: "fixture-only preparation" }],
    check_support: [{ side: "single", check_id: "REQ-01", status: "UNKNOWN", reasons: ["fixture support unknown"] }],
    guidance: { summary: "준비 검사 결과", issues: [] } }, log_tail: "준비 점검 기록", log_truncated: false, report_ids: [] };
const SOURCE = { name: "store", mode: "file", detail: null };
const ENVIRONMENT = Object.values(import.meta.glob("../../../contracts/examples/environment.json", { eager: true, import: "default" }))[0] as EnvironmentDoc;
const POLICY = Object.values(import.meta.glob("../../../contracts/examples/policy.json", { eager: true, import: "default" }))[0] as PolicyDoc;

function api({ initial, prepared = READY, unavailable = false, readonly = false, executionError = false, empty = false }:
  { initial?: UpdateRun; prepared?: UpdateRun; unavailable?: boolean; readonly?: boolean; executionError?: boolean; empty?: boolean } = {}) {
  let run = initial;
  const calls: { path: string; method: string; body: any }[] = [];
  const environment = structuredClone(ENVIRONMENT);
  environment.environment_id = "environment-one";
  const policy = structuredClone(POLICY);
  policy.policy_id = "policy-one";
  policy.environment_ref = { id: "environment-one", revision: 1, sha256: "a".repeat(64) };
  const envMeta = (revision: number) => ({ doc_id: "environment-one", revision, sha256: (revision === 1 ? "a" : "b").repeat(64),
    status: "DRAFT", saved_at: "2026-11-15T09:00:00Z", label: null, target_ref: environment.target.target_ref });
  const policyMeta = { ...envMeta(1), doc_id: "policy-one", status: "CONFIRMED", label: "3" };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).split("?")[0];
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
    calls.push({ path, method, body });
    let result: unknown;
    let status = 200;
    if (path === "/api/update/execution-settings") result = { available: !unavailable, can_execute: !readonly,
      unavailable_reason: unavailable ? "서버의 검증 실행 환경이 준비되지 않았습니다" : null, temporary: false };
    else if (path === "/api/policies" || path === "/api/policies/policy-one/revisions") result = { items: empty ? [] : [policyMeta], source: SOURCE };
    else if (path === "/api/environments") result = { items: empty ? [] : [envMeta(2)], source: SOURCE };
    else if (path === "/api/environments/environment-one/revisions") result = { items: [envMeta(1), envMeta(2)], source: SOURCE };
    else if (path === "/api/policies/policy-one/revisions/1") result = { meta: policyMeta, document: policy };
    else if (path.startsWith("/api/environments/environment-one/revisions/")) {
      const revision = Number(path.split("/").at(-1));
      result = { meta: envMeta(revision), document: { ...environment, revision } };
    } else if (path === "/api/update/runs") result = { items: run ? [run] : [] };
    else if (path === "/api/update/runs/prepare") { run = { ...prepared, request: body }; result = run; status = 202; }
    else if (path.endsWith("/execute")) {
      if (executionError) { result = { detail: { code: "INPUT_CHANGED", message: "입력이 바뀌었습니다. 다시 준비 점검하세요" } }; status = 409; }
      else { run = { ...run!, status: "completed", report_ids: [REPORT_REF.id], report_refs: [REPORT_REF], log_tail: "<script>alert('fixture')</script>", exit_code: 0 }; result = run; status = 202; }
    } else if (path === `/api/update/runs/${ID}` && run) result = run;
    else { result = { detail: { code: "NOT_FOUND", message: "없음" } }; status = 404; }
    return new Response(JSON.stringify(result), { status });
  }));
  return calls;
}

async function readyForm() {
  const prepare = await screen.findByRole("button", { name: "준비 점검" });
  await waitFor(() => expect((prepare as HTMLButtonElement).disabled).toBe(false));
  return prepare;
}

describe("Update 등록 판본 검증 실행", () => {
  it("검증 그룹에 실행 메뉴와 전용 주소를 등록한다", () => {
    expect(AREAS.find((area) => area.key === "update")?.groups[0].items).toContainEqual({ path: "/update/execute", label: "검증 실행" });
    expect(areaFor("/update/execute", null)).toBe("update");
    expect(titleFor("/update/execute")).toBe("검증 실행");
  });

  it("등록 목록과 정책의 정확한 환경 판본을 선택하고 경로·Pack·TOML 입력은 표시하지 않는다", async () => {
    const calls = api();
    renderAt("/update/execute", "/update/execute", <UpdateExecutionPage />);
    await readyForm();
    expect((screen.getByLabelText("검증 정책") as HTMLSelectElement).value).toBe("policy-one");
    expect((screen.getByLabelText("검증 환경") as HTMLSelectElement).value).toBe("environment-one");
    expect((screen.getByLabelText("검증 환경 판본") as HTMLSelectElement).value).toBe("1");
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryByLabelText(/Pack|TOML|파일/)).toBeNull();
    expect(calls.every((call) => call.method === "GET")).toBe(true);
  });

  it("명시적인 두 번의 동작으로 준비 점검 후 실행하고 보고서를 연결한다", async () => {
    const calls = api();
    renderAt("/update/execute", "/update/execute", <UpdateExecutionPage />);
    fireEvent.click(await readyForm());
    const start = await screen.findByRole("button", { name: "검증 실행" });
    await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false));
    expect(calls.filter((call) => call.method === "POST")).toEqual([{ path: "/api/update/runs/prepare", method: "POST", body: REQUEST }]);
    fireEvent.click(start);
    const report = await screen.findByRole("link", { name: "생성된 보고서 보기" });
    expect(report.getAttribute("href")).toBe(`/update/results?report=RPT-fixture&revision=2&sha256=${REPORT_REF.sha256}`);
    expect(calls.filter((call) => call.method === "POST").map((call) => call.path)).toEqual(["/api/update/runs/prepare", `/api/update/runs/${ID}/execute`]);
    expect(screen.getByText(/보안 판정과 미확인 항목은 보고서에서/)).toBeTruthy();
    expect(screen.queryByText("PASS")).toBeNull();
    expect(screen.getByText("<script>alert('fixture')</script>")).toBeTruthy();
    expect(document.querySelector("script")).toBeNull();
  });

  it("두 판본 비교에서는 등록한 환경의 서로 다른 판본과 공통 정책을 보낸다", async () => {
    const calls = api();
    renderAt("/update/execute", "/update/execute", <UpdateExecutionPage />);
    await readyForm();
    fireEvent.click(screen.getByRole("button", { name: /두 판본 비교/ }));
    fireEvent.change(screen.getByLabelText("후보 환경"), { target: { value: "environment-one" } });
    fireEvent.click(await readyForm());
    await waitFor(() => expect(calls.some((call) => call.method === "POST")).toBe(true));
    expect(calls.find((call) => call.method === "POST")!.body).toEqual({ mode: "update", policy: REQUEST.policy,
      baseline: REQUEST.environment, candidate: { id: "environment-one", revision: 2 } });
  });

  it("정책과 환경 판본이 다르면 원인을 표시하고 준비 점검을 허용하지 않는다", async () => {
    api();
    renderAt("/update/execute", "/update/execute", <UpdateExecutionPage />);
    await readyForm();
    fireEvent.change(screen.getByLabelText("검증 환경 판본"), { target: { value: "2" } });
    expect(await screen.findByText(/이 정책의 기준 환경은 environment-one · 판본 1/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "준비 점검" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("준비 실패 원인과 조치를 보여주고 실행을 허용하지 않는다", async () => {
    const calls = api({ prepared: { ...READY, status: "blocked", preparation: { ready: false, checks: [{ check: "docker", ok: false, detail: "daemon unavailable" }],
      guidance: { summary: "준비 보완 필요", issues: [{ code: "DOCKER_UNAVAILABLE", cause: "Docker 연결 없음", action: "Docker 접속을 확인하세요", blocking: true }] } } } });
    renderAt("/update/execute", "/update/execute", <UpdateExecutionPage />);
    fireEvent.click(await readyForm());
    expect(await screen.findByText("Docker 연결 없음")).toBeTruthy();
    expect(screen.getByText("Docker 접속을 확인하세요")).toBeTruthy();
    expect((screen.getByRole("button", { name: "검증 실행" }) as HTMLButtonElement).disabled).toBe(true);
    expect(calls.filter((call) => call.path.endsWith("/execute"))).toHaveLength(0);
  });

  it("새로고침으로 복원한 준비 기록을 자동 실행하지 않고 선택 변경 시 재점검을 요구한다", async () => {
    const calls = api({ initial: READY });
    renderAt(`/update/execute?run=${ID}`, "/update/execute", <UpdateExecutionPage />);
    const start = await screen.findByRole("button", { name: "검증 실행" });
    await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText("검증 환경 판본"), { target: { value: "2" } });
    expect((screen.getByRole("button", { name: "검증 실행" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/입력이 바뀌었습니다. 준비 점검을 다시/)).toBeTruthy();
    expect(calls.every((call) => call.method === "GET")).toBe(true);
  });

  it.each([{ unavailable: true }, { readonly: true }, { empty: true }])("환경·권한·등록 자료가 없으면 실행을 비활성화한다: %s", async (options) => {
    const calls = api(options);
    renderAt("/update/execute", "/update/execute", <UpdateExecutionPage />);
    const button = await screen.findByRole("button", { name: "준비 점검" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(calls.every((call) => call.method === "GET")).toBe(true);
    if (options.empty) expect(await screen.findByRole("link", { name: "환경 등록" })).toBeTruthy();
  });

  it("이전 파일 입력 기록을 조회하지만 자동으로 다시 실행하지 않는다", async () => {
    const calls = api({ initial: { ...READY, request: { mode: "validate", pack_path: "/old/pack", config_path: "/old/config.toml",
      policy_path: "/old/policy.json", environment_path: "/old/environment.json" } } });
    renderAt(`/update/execute?run=${ID}`, "/update/execute", <UpdateExecutionPage />);
    expect(await screen.findByText(/이전 파일 입력 기록입니다/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "검증 실행" }) as HTMLButtonElement).disabled).toBe(true);
    expect(calls.every((call) => call.method === "GET")).toBe(true);
  });

  it("실행 API 오류를 표시하고 완료·결과로 바꾸지 않는다", async () => {
    api({ initial: READY, executionError: true });
    renderAt(`/update/execute?run=${ID}`, "/update/execute", <UpdateExecutionPage />);
    await screen.findByRole("button", { name: "검증 실행" });
    await waitFor(() => expect((screen.getByRole("button", { name: "검증 실행" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "검증 실행" }));
    expect(await screen.findByText("입력이 바뀌었습니다. 다시 준비 점검하세요")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "실행 상태" })).queryByText("실행 완료")).toBeNull();
  });
});
