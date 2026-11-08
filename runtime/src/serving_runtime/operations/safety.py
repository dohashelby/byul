"""Fail-closed enforce checklist shared with the agent's platform projection.

Inputs come from local, trusted observation/evidence files, never the request.
No change is made to event classification or existing verification judgments.
"""
from __future__ import annotations
from datetime import datetime, timezone
import re

CHECKS = ("policy", "evidence", "capability", "monitor_period", "collection", "allowed_mode")
SHA = re.compile(r"^[0-9a-f]{64}$")


def recent(stamp, seconds, now=None):
    try:
        value = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        age = ((now or datetime.now(timezone.utc)) - value).total_seconds()
        return value.tzinfo is not None and 0 <= age <= seconds
    except (ValueError, TypeError, AttributeError):
        return False


def evaluate(snapshot: dict, name: str, intent, review: dict, monitor_seconds: int, *, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    checks = dict.fromkeys(CHECKS, False)
    rows = [p for p in snapshot.get("policies", []) if p.get("name") == name and p.get("namespace") == intent.namespace]
    evidence = []
    expected = None
    method = None
    capability_basis = None
    capability_reason = "capability_missing"
    monitor_windows = []
    if rows:
        first = rows[0]
        method = first.get("method")
        expected = {"policy_id": first.get("id"), "mode": first.get("mode"), "spec_sha256": first.get("spec_sha256")}
        checks["policy"] = all(p.get("state") == "enabled" and p.get("mode") in {"monitor", "enforce"}
                               and p.get("method") in {"override_lsm", "sigkill_fd_install", "override_syscall"}
                               and p.get("method") == method and p.get("spec_sha256") == first.get("spec_sha256")
                               and p.get("id") == first.get("id") for p in rows)
        # All observed nodes must pass. Missing capabilities or proof fail closed.
        proof_ok, capability_ok, period_ok = [], [], []
        capability_bases, capability_reasons = [], []
        for policy in rows:
            caps = [c for c in snapshot.get("capabilities", []) if c.get("node") == policy.get("node")]
            cap = caps[0] if len(caps) == 1 else {}
            proofs = [p for p in snapshot.get("verifications", [])
                      if p.get("data_origin") == "real" and p.get("variant", "combined") == "combined"
                      and p.get("method") == method and p.get("policy_spec_sha256") == policy.get("spec_sha256")
                      and isinstance(policy.get("spec_sha256"), str) and SHA.fullmatch(policy["spec_sha256"])
                      and p.get("kernel") == cap.get("kernel") and bool(cap.get("kernel"))
                      and p.get("node") == policy.get("node")
                      and p.get("verification", {}).get("status") == "passed"
                      and bool(SHA.fullmatch(p.get("source_report_sha256", "")))]
            proof_ok.append(bool(proofs))
            # evidence와 같은 실측 근거를 재사용하되 정적 불가 판정을 우선한다.
            basis, reason = None, None
            if method in cap.get("unavailable", []):
                reason = "static_unavailable"
            elif not cap.get("kernel"):
                reason = "capability_missing"
            elif method in cap.get("available", []):
                basis = "static"
            elif method in cap.get("unknown", []):
                if proofs:
                    basis = "live_trial"
                else:
                    reason = "evidence_required"
            else:
                reason = "capability_missing"
            capability_ok.append(basis is not None)
            capability_bases.append(basis)
            capability_reasons.append(reason)
            evidence.extend(p["source_report_sha256"] for p in proofs)
            # This bounded record is prepared from the complete monitor window,
            # and is explicitly tied to the current rule/spec/kernel. A changed
            # control, open gap, stale count, or incomplete human review rejects it.
            periods = [r for r in review.get("windows", []) if r.get("policy") == name
                       and r.get("namespace") == intent.namespace and r.get("policy_id") == policy.get("id")
                       and r.get("mode") == "monitor" and policy.get("mode") == "monitor"
                       and r.get("node") == policy.get("node") and r.get("spec_sha256") == policy.get("spec_sha256")
                       and r.get("method") == method and r.get("kernel") == cap.get("kernel")]
            valid_period = False
            for r in periods:
                try:
                    start = datetime.fromisoformat(r["since"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(r["until"].replace("Z", "+00:00"))
                    count, reviewed = r["event_count"], r.get("reviewed_event_count", 0)
                    false_positives = r.get("false_positive_count")
                    if (start.tzinfo is not None and end.tzinfo is not None and start <= end <= now
                            and type(count) is int and count >= 0 and type(reviewed) is int and 0 <= reviewed <= count
                            and type(false_positives) is int and 0 <= false_positives <= reviewed
                            and type(r.get("continuous")) is bool):
                        monitor_windows.append({"node": policy["node"], "since": r["since"], "until": r["until"],
                            "continuous": r["continuous"], "event_count": count, "reviewed_event_count": reviewed,
                            "false_positive_count": false_positives})
                    valid_period = (start.tzinfo is not None and end.tzinfo is not None and start <= end <= now
                                    and (end - start).total_seconds() >= max(0, monitor_seconds)
                                    and recent(r["until"], 30, now) and r.get("continuous") is True
                                    and type(count) is int and count >= 0 and type(false_positives) is int and false_positives == 0
                                    and (count == 0 or (type(reviewed) is int and reviewed == count
                                         and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", r.get("reviewer_token_id", ""))))
                                    and bool(SHA.fullmatch(r.get("events_sha256", ""))))
                except (ValueError, TypeError, KeyError):
                    valid_period = False
                if valid_period:
                    evidence.append(r["events_sha256"])
                    if SHA.fullmatch(review.get("file_sha256", "")):
                        evidence.append(review["file_sha256"])
                    break
            period_ok.append(bool(valid_period))
        checks["evidence"] = all(proof_ok)
        checks["capability"] = all(capability_ok)
        if checks["capability"]:
            capability_basis = "live_trial" if "live_trial" in capability_bases else "static"
            capability_reason = None
        else:
            capability_reason = ("static_unavailable" if "static_unavailable" in capability_reasons
                                 else next(r for r in capability_reasons if r is not None))
        checks["monitor_period"] = all(period_ok)
    # Current render supports enforce for this rule only. Unknown names cannot
    # inherit another rule's allowance.
    checks["allowed_mode"] = (name == "runtime-tg-secret-01" and "enforce" in intent.allowed_modes("TG-SECRET-01"))
    monitoring = snapshot.get("monitoring") or {}
    checks["collection"] = (snapshot.get("fresh") is True and snapshot.get("state") == "ok"
                            and recent(snapshot.get("generated_at"), 20, now)
                            and monitoring.get("state") == "healthy" and not monitoring.get("open_gap")
                            and all(monitoring.get(k) is True for k in
                                    ("policies_verified", "writer_verified", "identities_verified", "events_verified")))
    return {"name": name, "method": method, "expected": expected, "checks": checks,
            "monitor_windows": monitor_windows[:128],
            "capability_basis": capability_basis, "capability_reason": capability_reason,
            "evidence_sha256": sorted(set(evidence))[:64]}
