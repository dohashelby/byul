import { useSyncExternalStore } from "react";

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
export function useOperationToken(): [string, (value: string) => void] {
  const token = useSyncExternalStore(listener => {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  }, snapshot);
  return [token, setOperationToken];
}
