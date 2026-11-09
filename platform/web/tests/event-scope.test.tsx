import { describe, expect, it } from "vitest";
import type { PhaseTimelineResponse, RuntimeEvent } from "../src/api/types";
import { groupAlerts } from "../src/lib/alerts";
import { matchesTimeline, sameEventContext } from "../src/lib/eventScope";

const raw = Object.values(import.meta.glob("../../../contracts/examples/runtime-events.jsonl", { eager: true, query: "?raw", import: "default" }))[0] as string;
const sample = raw.split("\n").filter(Boolean).map((x) => JSON.parse(x)).find((e) => e.source === "tetragon") as RuntimeEvent;
function event(id: string): RuntimeEvent {
  return { ...sample, event_id: id, data_origin: "MEASURED", observation: { ...sample.observation!, scope_ref: "a".repeat(64) } };
}

describe("사건 실행 범위", () => {
  it("같은 요청 ID도 다른 run의 기록과 관련 요청으로 연결하지 않는다", () => {
    const a = event("a");
    const other = { ...a, scope: { ...a.scope, run_id: "another-run" } };
    expect(sameEventContext(a, other)).toBe(false);
    expect(sameEventContext(a, event("b"))).toBe(true);
  });
  it("다른 환경·run·target·세대·정책과 미귀속 사건을 합치지 않는다", () => {
    const a = event("a"), b = event("b");
    expect(groupAlerts([a, b])).toHaveLength(1);
    for (const different of [
      { ...b, scope: { ...b.scope, environment: "other" } },
      { ...b, scope: { ...b.scope, run_id: "other" } },
      { ...b, target_ref: "other" },
      { ...b, target_instance_id: "other" },
      { ...b, observation: { ...b.observation!, scope_ref: "b".repeat(64) } },
      { ...b, policy_ref: { policy_id: "other", policy_version: "1", sha256: "b".repeat(64) } },
      { ...b, observation: { ...b.observation!, scope_ref: null } },
    ]) expect(groupAlerts([a, different])).toHaveLength(2);
  });
  it("실측 시간표는 같은 세대 실측 사건만 연결하고 합성 사건은 예시 시간표에 둔다", () => {
    const timeline = { data_origin: "real", scope_ref: "a".repeat(64) } as PhaseTimelineResponse;
    const a = event("a");
    expect(matchesTimeline(a, timeline)).toBe(true);
    expect(matchesTimeline(a, { ...timeline, scope_ref: null })).toBe(false);
    expect(matchesTimeline({ ...a, observation: { ...a.observation!, scope_ref: "b".repeat(64) } }, timeline)).toBe(false);
    const example = { ...a, data_origin: "EXAMPLE" as const };
    expect(matchesTimeline(example, timeline)).toBe(false);
    expect(matchesTimeline(example, { ...timeline, data_origin: "synthetic" })).toBe(true);
  });
});
