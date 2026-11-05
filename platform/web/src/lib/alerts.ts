import type { RuntimeEvent } from "../api/types";
import { alertSeverity, EXEC_VERDICT, PHASE, SEVERITY_ORDER, type AlertSeverity } from "./labels";
import { isTetragonOverride } from "./tetragon";

export type AlertGroup = { key: string; severity: AlertSeverity; title: string; first: string | null; last: string | null; items: RuntimeEvent[] };

export function groupAlerts(events: RuntimeEvent[]): AlertGroup[] {
  const groups = new Map<string, AlertGroup>();
  for (const event of events) {
    if (!event.observation) continue;
    const o = event.observation;
    const verdict = o.exec_verdict ?? o.verdict ?? event.outcome;
    const subject = o.kind === "file_access"
      ? o.rule_id ?? event.rule_ref ?? o.path ?? event.source
      : o.binary ?? o.rule_id ?? event.rule_ref ?? event.source;
    const phase = o.phases?.join("+") || event.phase || "unknown";
    const blocked = isTetragonOverride(event);
    const key = JSON.stringify([verdict, subject, phase, blocked]);
    const severity = alertSeverity(o.exec_verdict ?? o.verdict, o.rule_id ?? event.rule_ref, event.source);
    const kind = event.source === "hubble" ? "통신" : o.kind === "file_access" ? /secret/i.test(o.rule_id ?? event.rule_ref ?? "") ? "보호 파일 읽기" : "파일 접근" : "프로그램 실행";
    const explanation = o.exec_verdict ? EXEC_VERDICT[o.exec_verdict] ?? "판정 미분류" : o.verdict?.toLowerCase() === "dropped" ? "차단됨(정보)" : "관측";
    const record = event.native.record as Record<string, unknown>;
    const context = record.suspect_context === "compile_toolchain_parent" ? "컴파일 도구 하위 실행" : explanation;
    const action = o.binary?.endsWith("/sh") ? "셸 실행" : kind;
    const title = `${action} — ${blocked ? "차단됨 · Override" : context} · ${PHASE[phase] ?? phase}`;
    const at = event.occurred_at ?? event.recorded_at;
    const group = groups.get(key);
    if (group) {
      group.items.push(event);
      if (at && (!group.first || at < group.first)) group.first = at;
      if (at && (!group.last || at > group.last)) group.last = at;
    } else groups.set(key, { key, severity, title, first: at, last: at, items: [event] });
  }
  return [...groups.values()].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || (b.last ?? "").localeCompare(a.last ?? ""));
}
