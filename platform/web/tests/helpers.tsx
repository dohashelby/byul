import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** 플랫폼 API 응답을 경로별로 돌려주는 가짜 fetch. 받은 메서드도 기록한다. */
export function mockApi(routes: Record<string, unknown>) {
  const calls: { method: string; url: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ method: init?.method ?? "GET", url });
      const path = url.split("?")[0];
      if (path in routes) return new Response(JSON.stringify(routes[path]), { status: 200 });
      return new Response(JSON.stringify({ detail: { code: "NOT_FOUND", message: "없음" } }), { status: 404 });
    }),
  );
  return calls;
}

export function renderAt(path: string, routePath: string, element: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={routePath} element={element} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export const SOURCE = { name: "runtime", mode: "example", detail: null };
