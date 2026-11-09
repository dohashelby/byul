// 로그인 상태. 세션은 HttpOnly 쿠키라 화면은 사용자 정보와 CSRF 값만 안다.
import { useSyncExternalStore } from "react";

export type AuthRole = "admin" | "approver" | "runtime_operator" | "viewer";
export type AuthUser = { id: string; role: AuthRole; default_password: boolean; must_change: boolean; password_change_required: boolean };
export type AuthState =
  | { status: "loading" }
  | { status: "disabled" }
  | { status: "anonymous"; message?: string }
  | { status: "signed_in"; user: AuthUser };
type Me = { login_enabled: boolean; user?: AuthUser | null; csrf_token?: string | null };

let state: AuthState = { status: "loading" };
let csrf = "";
const listeners = new Set<() => void>();

function set(next: AuthState) {
  state = next;
  listeners.forEach((listener) => listener());
}

function apply(me: Me) {
  csrf = me.csrf_token ?? "";
  if (!me.login_enabled) set({ status: "disabled" });
  else if (me.user) set({ status: "signed_in", user: me.user });
  else set({ status: "anonymous" });
}

export function csrfHeaders(): Record<string, string> {
  return csrf ? { "X-CSRF-Token": csrf } : {};
}

/** API가 로그인 필요(401 AUTH_REQUIRED)를 돌려주면 로그인 화면으로 돌아간다. */
export function sessionExpired() {
  if (state.status === "signed_in") {
    csrf = "";
    set({ status: "anonymous", message: "로그인이 만료됐습니다. 다시 로그인하세요." });
  }
}

export async function loadMe(): Promise<void> {
  try {
    const res = await fetch("/api/auth/me", { headers: { Accept: "application/json" } });
    if (res.status === 404) return set({ status: "disabled" }); // 로그인 기능이 없는 이전 API
    apply((await res.json()) as Me);
  } catch {
    set({ status: "anonymous", message: "플랫폼 API에 연결하지 못했습니다" });
  }
}

async function send(path: string, body: unknown): Promise<{ ok: boolean; status: number; data: any }> {
  const res = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(body),
  });
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  return { ok: res.ok, status: res.status, data };
}

export async function login(username: string, password: string): Promise<string | null> {
  try {
    const r = await send("/api/auth/login", { username, password });
    if (!r.ok) return r.data?.detail?.message ?? "로그인하지 못했습니다";
    apply(r.data as Me);
    return null;
  } catch {
    return "플랫폼 API에 연결하지 못했습니다";
  }
}

export async function logout(): Promise<void> {
  try {
    await send("/api/auth/logout", {});
  } finally {
    csrf = "";
    set({ status: "anonymous" });
  }
}

export async function changePassword(current: string, next: string): Promise<string | null> {
  try {
    const r = await send("/api/auth/password", { current_password: current, new_password: next });
    if (!r.ok) return r.data?.detail?.message ?? "비밀번호를 바꾸지 못했습니다";
    apply(r.data as Me);
    return null;
  } catch {
    return "플랫폼 API에 연결하지 못했습니다";
  }
}

export function useAuth(): AuthState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    () => state,
  );
}

export const ROLE_NAMES: Record<AuthRole, string> = {
  admin: "관리자",
  approver: "승인자",
  runtime_operator: "운영자",
  viewer: "조회자",
};

/** 시험용: 상태를 직접 넣는다. */
export function __setAuthForTest(next: AuthState, token = "") {
  csrf = token;
  set(next);
}
