import { useSyncExternalStore } from "react";
import { useAuth } from "./auth";
import { SESSION_CREDENTIAL } from "./operations";

const KEY = "byul.runtime.operation-token";
let memory = "";
let loaded = false;
const listeners = new Set<() => void>();
function snapshot() {
  if (!loaded) {
    loaded = true;
    try { memory = sessionStorage.getItem(KEY) ?? ""; } catch { /* Memory remains available. */ }
  }
  return memory;
}
export function setOperationToken(value: string) {
  loaded = true;
  memory = value;
  try {
    if (value) sessionStorage.setItem(KEY, value);
    else sessionStorage.removeItem(KEY);
  } catch { /* Storage may be blocked; keep the token only in memory. */ }
  listeners.forEach(listener => listener());
}
const ignore = () => {};
/** 로그인했으면 세션(쿠키·CSRF)으로 부르고, 로그인이 꺼진 배포에서만 붙여넣은 토큰을 쓴다. */
export function useOperationToken(): [string, (value: string) => void] {
  const auth = useAuth();
  const token = useSyncExternalStore(listener => {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  }, snapshot);
  if (auth.status === "signed_in") return [SESSION_CREDENTIAL, ignore];
  return [token, setOperationToken];
}
