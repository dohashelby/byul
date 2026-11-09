import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { __setAuthForTest, useAuth } from "../src/api/auth";
import { getJson, postJson } from "../src/api/client";
import { setOperationToken } from "../src/api/operationSession";
import { AuthGate } from "../src/app/App";
import { Layout } from "../src/app/Layout";
import { RuntimeOperationsPage } from "../src/pages/RuntimeOperations";
import "./helpers";

const ADMIN = { id: "admin", role: "admin", default_password: true, must_change: true, password_change_required: false } as const;

type Call = { method: string; url: string; headers: Record<string, string>; body?: string };
function stubFetch(handler: (call: Call) => { status?: number; body: unknown }) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { method: init?.method ?? "GET", url: String(input), headers: (init?.headers ?? {}) as Record<string, string>,
      body: init?.body as string | undefined };
    calls.push(call);
    const { status = 200, body } = handler(call);
    return new Response(JSON.stringify(body), { status });
  }));
  return calls;
}

function Probe() {
  const auth = useAuth();
  return <p>상태:{auth.status}</p>;
}

beforeEach(() => {
  __setAuthForTest({ status: "loading" });
  setOperationToken("");
});

it("로그인이 켜져 있고 세션이 없으면 로그인 화면만 보이고, 로그인하면 화면을 띄운다", async () => {
  const calls = stubFetch(({ url }) => url === "/api/auth/me" ? { body: { login_enabled: true, user: null } }
    : { body: { login_enabled: true, user: ADMIN, csrf_token: "csrf-1" } });
  render(<AuthGate><p>보호된 화면</p></AuthGate>);
  await screen.findByRole("form", { name: "로그인" });
  expect(screen.queryByText("보호된 화면")).toBeNull();
  expect(screen.queryByText(/회원가입/)).toBeNull();
  fireEvent.change(screen.getByLabelText("아이디"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "admin" } });
  fireEvent.click(screen.getByRole("button", { name: "로그인" }));
  await screen.findByText("보호된 화면");
  const login = calls.find(c => c.url === "/api/auth/login")!;
  expect(login.method).toBe("POST");
  expect(JSON.parse(login.body!)).toEqual({ username: "admin", password: "admin" });
});

it("로그인 실패 문구를 그대로 보여 준다", async () => {
  stubFetch(({ url }) => url === "/api/auth/me" ? { body: { login_enabled: true, user: null } }
    : { status: 401, body: { detail: { code: "INVALID_CREDENTIALS", message: "아이디 또는 비밀번호가 맞지 않습니다" } } });
  render(<AuthGate><p>보호된 화면</p></AuthGate>);
  fireEvent.change(await screen.findByLabelText("아이디"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "로그인" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "아이디 또는 비밀번호가 맞지 않습니다");
});

it("로그인이 꺼진 배포는 예전처럼 바로 화면을 띄운다", async () => {
  stubFetch(() => ({ body: { login_enabled: false } }));
  render(<AuthGate><p>보호된 화면</p></AuthGate>);
  await screen.findByText("보호된 화면");
});

it("변경 강제 설정이 켜진 계정은 비밀번호 변경 화면만 본다", async () => {
  stubFetch(() => ({ body: { login_enabled: true, user: { ...ADMIN, password_change_required: true }, csrf_token: "c" } }));
  render(<AuthGate><p>보호된 화면</p></AuthGate>);
  await screen.findByRole("form", { name: "비밀번호 변경" });
  expect(screen.queryByText("보호된 화면")).toBeNull();
});

it("API가 로그인 만료를 알리면 로그인 화면으로 돌아가고, 변경 요청에는 CSRF 값을 붙인다", async () => {
  __setAuthForTest({ status: "signed_in", user: ADMIN }, "csrf-xyz");
  const calls = stubFetch(({ url }) => url === "/api/write" ? { body: { ok: true } }
    : { status: 401, body: { detail: { code: "AUTH_REQUIRED", message: "로그인이 필요합니다" } } });
  render(<Probe />);
  await postJson("/api/write", {});
  expect(calls[0].headers["X-CSRF-Token"]).toBe("csrf-xyz");
  await expect(getJson("/api/meta")).rejects.toThrow();
  await screen.findByText("상태:anonymous");
});

it("상단에 계정·역할·로그아웃과 기본 비밀번호 경고를 보여 준다", async () => {
  __setAuthForTest({ status: "signed_in", user: ADMIN }, "c");
  const calls = stubFetch(() => ({ status: 404, body: { detail: { code: "NOT_FOUND" } } }));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/evidence"]}>
        <Routes><Route element={<Layout />}><Route path="/evidence" element={<p>본문</p>} /></Route></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("admin · 관리자");
  expect(screen.getByText(/기본 비밀번호\(admin\) 사용 중/)).toBeTruthy();
  expect(screen.getByRole("link", { name: "비밀번호 변경" }).getAttribute("href")).toBe("/account/password");
  fireEvent.click(screen.getByRole("button", { name: "로그아웃" }));
  await waitFor(() => expect(calls.some(c => c.url === "/api/auth/logout" && c.headers["X-CSRF-Token"] === "c")).toBe(true));
});

it("로그인하면 운영 화면은 토큰 입력 없이 세션으로 조회한다", async () => {
  __setAuthForTest({ status: "signed_in", user: { ...ADMIN, id: "approver1", role: "approver", default_password: false } }, "csrf-2");
  const calls = stubFetch(() => ({ body: { actor: { id: "approver1", role: "approver", via: "session" }, items: [], agents: [],
    lab_self_approval: false, monitor_seconds: 86400 } }));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/runtime/operations"]}>
        <Routes><Route path="/runtime/operations" element={<RuntimeOperationsPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findAllByText(/로그인 계정으로 요청 · 현재 역할: 승인자/);
  expect(screen.queryByLabelText("운영 토큰")).toBeNull();
  const op = calls.find(c => c.url.startsWith("/api/runtime/operations"))!;
  expect(op.headers.Authorization).toBeUndefined();
  expect(op.headers["X-CSRF-Token"]).toBe("csrf-2");
});
