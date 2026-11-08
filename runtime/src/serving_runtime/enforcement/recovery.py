"""Persist observed outages and require policy, writer and event evidence to recover."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path

from .attribution import read_identities, timestamp

SCHEMA = "runtime-tetragon-recovery/1.0"


def reassess_operations(record):
    facts=record.get("facts") or {}
    required=["main_process_identified","child_process_identified","distinct_pods_identified",
              "initial_monitoring_verified","cleanup_ok","original_policies_preserved"]
    if record.get("restart_requested"):
        required += ["agent_change_detected","gap_detected","recovery_verified"]
    monitoring=record.get("monitoring") or {}
    ready=(record.get("status")=="passed" and all(facts.get(k) is True for k in required)
           and monitoring.get("state")=="healthy" and all(monitoring.get(k) is True
               for k in ("policies_verified","writer_verified","identities_verified","events_verified")))
    return {**record,"status":"passed" if ready else "failed"}


def recent(document, now, *, maximum=20):
    at = timestamp(document.get("updated_at", document.get("generated_at")))
    return at is not None and -5 <= (now-at).total_seconds() <= maximum


def read_object(path, *, limit=2*1024*1024):
    target = Path(path)
    if target.stat().st_size > limit:
        raise ValueError("recovery evidence exceeds size limit")
    value = json.loads(target.read_text())
    if not isinstance(value, dict):
        raise ValueError("recovery evidence must be an object")
    return value


def tail_observations(path):
    health = Path(path).parent / "tetragon-health-receipts.json"
    if health.exists():
        document = read_object(health)
        if document.get("schema_version") == "runtime-tetragon-health-receipts/1.0":
            for event in document.get("nodes", {}).values():
                if isinstance(event, dict):
                    yield event
    if not Path(path).exists() and health.exists():
        return
    with Path(path).open("rb") as source:
        source.seek(0, 2)
        size = source.tell()
        source.seek(max(0, size-2*1024*1024))
        data = source.read()
    lines = data.splitlines()
    if size > len(data):
        lines = lines[1:]
    for line in lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                yield value
        except (ValueError, UnicodeError):
            continue


class RecoveryTracker:
    def __init__(self, context, state_path, *, baseline_path, identities_path, heartbeat_path, observations_path):
        self.context, self.path = context, Path(state_path)
        self.baseline_path, self.identities_path = baseline_path, identities_path
        self.heartbeat_path, self.observations_path = heartbeat_path, observations_path
        self.document = None
        try:
            value = read_object(self.path)
            if value.get("schema_version") == SCHEMA and value.get("context") == context:
                self.document = value
        except (OSError, ValueError, TypeError):
            pass

    def update(self, snapshot, *, now=None):
        now = now or datetime.now(timezone.utc)
        at = now.isoformat()
        previous = self.document or {}
        result = {"schema_version": SCHEMA, "context": self.context, "generated_at": at,
                  "state": "recovering", "reasons": [], "policies_verified": False,
                  "writer_verified": False, "identities_verified": False, "events_verified": False,
                  "verified_at": previous.get("verified_at"), "open_gap": previous.get("open_gap"),
                  "history": list(previous.get("history", []))[-20:], "epochs": previous.get("epochs", {}),
                  "verification_since": previous.get("verification_since", at),
                  "controls_verified_since": previous.get("controls_verified_since"),
                  "event_verified_at": previous.get("event_verified_at", {})}
        reasons = result["reasons"]
        previous_at=timestamp(previous.get("generated_at"))
        if previous_at is not None and (now-previous_at).total_seconds()>20:
            result.update(verification_since=at,controls_verified_since=None,event_verified_at={})
            if result["open_gap"] is None:
                result["open_gap"]={"opened_at":at,"last_verified_at":previous.get("verified_at"),
                                    "reason":"recovery_observer_stale"}
            reasons.append("recovery_observer_stale")
        try:
            baseline = read_object(self.baseline_path)
            if (baseline.get("schema_version") != "runtime-tetragon-baseline/1.0"
                or baseline.get("context") != self.context or not baseline.get("policies")):
                raise ValueError("expected policies are not configured")
        except (OSError, ValueError, TypeError):
            result.update(state="not_configured", reasons=["expected_policies_unavailable"],
                          controls_verified_since=None, event_verified_at={}, verification_since=at)
            return self._save(result)
        baseline_hash=hashlib.sha256(json.dumps(baseline,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        result["baseline_sha256"]=baseline_hash
        if baseline_hash != previous.get("baseline_sha256"):
            result.update(controls_verified_since=None, event_verified_at={}, verification_since=at)
        agents = {a.get("node"): [a.get("pod_uid"), a.get("restart_count")]
                  for a in snapshot.get("agents", []) if a.get("pod_uid") is not None}
        epoch_changed = bool(agents) and agents != previous.get("epochs")
        if epoch_changed:
            reasons.append("agent_epoch_changed" if previous.get("epochs") else "agent_epoch_first_observed")
            result.update(epochs=agents, verification_since=at, controls_verified_since=None, event_verified_at={})
        if snapshot.get("state") != "ok" or not agents:
            reasons.append("agent_or_policy_query_unavailable")
        elif snapshot.get("generated_at") is not None and not recent(snapshot, now):
            reasons.append("agent_or_policy_query_unavailable")
        actual = {(p.get("node"), p.get("namespace"), p.get("name")): p for p in snapshot.get("policies", [])}
        checks = []
        for policy in baseline["policies"]:
            observed = actual.get((policy.get("node"), policy.get("namespace"), policy.get("name")), {})
            checks.append(observed.get("state") == "enabled" and observed.get("mode") == policy.get("mode")
                          and observed.get("spec_sha256") == policy.get("spec_sha256")
                          and bool(policy.get("spec_sha256")))
        result["policies_verified"] = (bool(checks) and all(checks)
            and {p.get("node") for p in baseline["policies"]} == set(agents))
        if not result["policies_verified"]:
            reasons.append("expected_policy_state_mismatch")
        try:
            heartbeat = read_object(self.heartbeat_path)
            processes = heartbeat.get("processes")
            result["writer_verified"] = (heartbeat.get("schema") == "runtime-observation-feed-heartbeat/1.0"
                and heartbeat.get("kind") == "tetragon" and heartbeat.get("state") == "running"
                and heartbeat.get("kube_context") == self.context
                and recent(heartbeat, now) and isinstance(processes, list) and bool(processes)
                and all(p.get("running") is True for p in processes)
                and not heartbeat.get("open_gap"))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        if not result["writer_verified"]:
            reasons.append("event_writer_unavailable")
        identities = read_identities(self.identities_path, context=self.context, now=now)
        identity_epochs = {a.get("node"): [a.get("pod_uid"), a.get("restart_count")]
                           for a in identities.get("agents", [])}
        result["identities_verified"] = (identities.get("state") == "ok" and identity_epochs == agents
                                          and bool(agents) and bool(identities.get("records")))
        if not result["identities_verified"]:
            reasons.append("process_identity_source_unavailable")
        faults = [reason for reason in reasons if reason not in {"agent_epoch_changed","agent_epoch_first_observed","recovery_observer_stale"}]
        new_fault = bool(faults) and previous.get("state") == "healthy"
        if new_fault:
            result.update(verification_since=at, event_verified_at={})
        if faults:
            result.update(controls_verified_since=None, event_verified_at={})
        elif result["controls_verified_since"] is None:
            result["controls_verified_since"] = at
        since = timestamp(result["controls_verified_since"])
        try:
            for event in tail_observations(self.observations_path):
                identity = event.get("identity") or {}
                observed = timestamp(event.get("observed_at"))
                node = identity.get("node")
                if (event.get("source") == "tetragon" and event.get("data_origin") == "real"
                    and identity.get("state") == "confirmed" and node in agents
                    and identity.get("context") == self.context and not faults
                    and observed is not None and since is not None and since <= observed <= now
                    and recent({"generated_at": event.get("observed_at")}, now, maximum=30)
                    and identity.get("sampled_at") is not None):
                    result["event_verified_at"][node] = event["observed_at"]
        except (OSError, ValueError, TypeError, AttributeError):
            faults.append("normalized_observations_unavailable")
            reasons.append("normalized_observations_unavailable")
        result["event_verified_at"] = {node: seen for node,seen in result["event_verified_at"].items()
            if timestamp(seen) is not None and 0 <= (now-timestamp(seen)).total_seconds() <= 30}
        result["events_verified"] = bool(agents) and all(node in result["event_verified_at"] for node in agents)
        if not result["events_verified"]:
            reasons.append("waiting_for_attributed_event")
            if previous.get("state")=="healthy":
                result.update(controls_verified_since=at,event_verified_at={})
        ready = not faults and result["events_verified"]
        if not ready and result["open_gap"] is None:
            result["open_gap"] = {"opened_at": at, "last_verified_at": previous.get("verified_at"),
                                  "reason": reasons[0] if reasons else "recovery_pending"}
        if ready:
            result.update(state="healthy", verified_at=at, reasons=[])
            if result["open_gap"]:
                result["history"].append({**result["open_gap"], "closed_at": at,
                                           "recovery": "policies_writer_identity_and_new_event_verified",
                                           "backfill": "unverified"})
                result["history"] = result["history"][-20:]
                result["open_gap"] = None
        return self._save(result)

    def _save(self, result):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name+".next")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
        temporary.chmod(0o600)
        temporary.replace(self.path)
        self.document = result
        return result
