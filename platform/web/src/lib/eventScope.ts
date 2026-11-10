import type { PhaseTimelineResponse, RuntimeEvent } from "../api/types";

export function alertScope(event: RuntimeEvent): string {
  const generation = event.observation?.scope_ref;
  // An unbound event remains individually inspectable.
  return generation ? JSON.stringify([event.source, event.scope.environment, event.scope.run_id,
    event.target_ref, event.target_instance_id, event.policy_ref?.sha256, generation]) : event.event_id;
}

export function matchesTimeline(event: RuntimeEvent, timeline: PhaseTimelineResponse): boolean {
  if (timeline.data_origin === "synthetic") return event.data_origin === "EXAMPLE";
  return event.data_origin === "MEASURED" && !!timeline.scope_ref && event.observation?.scope_ref === timeline.scope_ref;
}

export function sameEventContext(left: RuntimeEvent, right: RuntimeEvent): boolean {
  return left.scope.environment === right.scope.environment && left.scope.run_id === right.scope.run_id &&
    left.target_ref === right.target_ref && left.target_instance_id === right.target_instance_id;
}
