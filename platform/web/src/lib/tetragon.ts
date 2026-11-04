import type { RuntimeEvent } from "../api/types";

/** Tetragon의 원본 Override 기록으로만 파일 읽기 차단 시도를 표시한다. */
export function isTetragonOverride(event: RuntimeEvent): boolean {
  if (event.source !== "tetragon" || event.observation?.kind !== "file_access") return false;
  const record = event.native.record as Record<string, unknown>;
  return event.observation.action_mode === "enforce"
    && (record.action_raw === "KPROBE_ACTION_OVERRIDE" || record.action === "KPROBE_ACTION_OVERRIDE");
}
