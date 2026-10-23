"""Bounded SUP-14/LIF-07 checks over declared facts, never engine execution."""
from __future__ import annotations

from collections import defaultdict
import json

from guard.manual_policy import CASES, FIELDS, OBSERVATION, REPORT_PREFIX, normalize, parameter, policy_items


def evaluate(check, evidence, values):
    """Return axis ceilings/confirmed failures and traceable, non-sensitive reasons.

    PASS here permits keeping a person's PASS; it never upgrades their UNKNOWN.
    Declared FAIL and the existing core PASS gate are applied by the caller.
    """
    key = parameter(check)
    if key is None:
        return None
    ceilings = {"security": "UNKNOWN", "functional": "UNKNOWN", "performance": "UNKNOWN"}
    reasons, missing = [], []

    def note(code, row=None, *, gap=False):
        identity = "" if row is None else f" item={json.dumps(row['item_id'], ensure_ascii=False)} case={row['case']}"
        hashes = sorted({r["sha256"][:12] for r in row["evidence"] if r.get("sha256")}) if row else []
        reasons.append(f"{REPORT_PREFIX}{identity} {code}" + (f" evidence={','.join(hashes)}" if hashes else ""))
        if gap:
            missing.append(f"{OBSERVATION}{identity}: {code}")

    items = policy_items(values, key)
    resolved = items is not None
    if items is None:
        note("POLICY_ITEM_SET_UNRESOLVED", gap=True)
        given = values.get(key)
        # An ambiguous other item must not erase a violation for a separately
        # identifiable required item. This is not a normalized policy set.
        items = [i for i in given if isinstance(i, str) and i.strip() and i == i.strip() and given.count(i) == 1] if isinstance(given, list) else []
    observation = evidence.observations.get(OBSERVATION)
    if observation is None or not observation.collected:
        note("POLICY_EVIDENCE_MISSING", gap=True)
        return ceilings, reasons, missing
    try:
        payload = normalize(check, values, observation.value, {a.sha256: a.sha256 for a in evidence.artifacts}, stored=True)
        if payload is None:
            raise ValueError("empty stored payload")
    except (TypeError, ValueError):
        note("POLICY_EVIDENCE_INVALID", gap=True)
        return ceilings, reasons, missing
    rows = {(r["item_id"], r["case"]): r for r in payload["records"]}

    # Same declared event cannot both permit and block admission. Exclude just
    # the conflicting event; a violation from a separate event must survive.
    events = defaultdict(list)
    for row in rows.values():
        o = row["observations"]
        if o.get("trial_id") and o.get("target_scope"):
            events[(o["trial_id"], o["target_scope"], row["case"])].append(row)
    conflicts, admission_conflicts = set(), set()
    event_fields = ("gate_outcome",) if check.id == "SUP-14" else ("request_outcome", "inference_outcome", "order_basis")
    for group in events.values():
        # Only contradictory admission facts can invalidate an independently
        # linked early-admission failure. Inference results and the method used
        # to establish order are not premises of that security failure.
        if check.id == "LIF-07" and len({r["observations"].get("request_outcome") for r in group
                                         if r["observations"].get("request_outcome") not in (None, "unknown")}) > 1:
            admission_conflicts.update((r["item_id"], r["case"]) for r in group)
        if any(len({r["observations"].get(f) for r in group if r["observations"].get(f) not in (None, "unknown")}) > 1
               for f in event_fields):
            conflicts.update((r["item_id"], r["case"]) for r in group)

    complete, failures, positives = resolved, set(), set()
    positive_case = "normal" if check.id == "SUP-14" else "ready"
    positive_events = set()
    ready_admissions = {}
    for item in items:
        for case in CASES[check.id]:
            row = rows.get((item, case))
            if row is None:
                note("CASE_MISSING", {"item_id": item, "case": case, "evidence": []}, gap=True)
                complete = False
                continue
            o = row["observations"]
            linked = (bool(row["evidence"])
                      and all(r.get("sha256") and (r.get("locator") or "").strip() and (r.get("basis") or "").strip()
                              for r in row["evidence"]))
            supported = (
                all(isinstance(o.get(f), str) and o[f].strip() and o[f] != "unknown" for f in FIELDS[check.id])
                and linked
            )
            if (check.id == "LIF-07" and linked and (item, case) not in admission_conflicts
                    and all((o.get(f) or "").strip() for f in ("trial_id", "target_scope", "order_detail"))
                    and o.get("order_basis") in ("same_clock", "independent_order")
                    and (o.get("order_detail") or "").strip()
                    and o.get("item_state") == "not_ready" and o.get("request_outcome") == "accepted"):
                failures.add("security")
                note("LIF_ADMITTED_BEFORE_READY", row)
            if (item, case) in conflicts:
                note("EVENT_CONFLICT", row, gap=True)
                complete = False
                continue
            if not supported:
                note("RECORD_INCOMPLETE", row, gap=True)
                complete = False
                continue
            note("DECLARED_RECORD_LINKED", row)
            if row.get("limitation") and row["limitation"].strip():
                note("RECORD_LIMITATION", row, gap=True)
                complete = False
                # A limitation prevents PASS; independently explicit failure facts remain below.
            if case == positive_case:
                positive_events.add((o["trial_id"], o["target_scope"]))
                positives.add(item)
            if check.id == "SUP-14":
                raw, gate, exception = o["check_outcome"], o["gate_outcome"], o["exception_assessment"]
                if raw != "succeeded" and gate == "advanced" and exception == "none":
                    failures.add("security")
                    note("SUP_REQUIRED_INCOMPLETE_ADVANCED", row)
                expected = "succeeded" if case == "normal" else case
                if raw != expected or gate != ("advanced" if case == "normal" else "blocked") or exception != "none":
                    note("SUP_EXPECTATION_OR_EXCEPTION_UNVERIFIED", row, gap=True)
                    complete = False
            else:
                state, admission, inference = o["item_state"], o["request_outcome"], o["inference_outcome"]
                if case == "ready" and state == "ready" and admission == "accepted":
                    ready_admissions[item] = row
                expected = (("not_ready", "blocked", "not_attempted") if case == "not_ready"
                            else ("ready", "accepted", "succeeded"))
                if (state, admission, inference) != expected:
                    note("LIF_EXPECTATION_UNVERIFIED", row, gap=True)
                    complete = False
    if positives != set(items) or len(positive_events) != 1:
        note("POSITIVE_BUNDLE_NOT_SAME_TRIAL", gap=True)
        complete = False
    if (check.id == "LIF-07" and resolved and items and set(ready_admissions) == set(items)
            and len(positive_events) == 1 and evidence.normal_control.attempted):
        for row in ready_admissions.values():
            if row["observations"]["inference_outcome"] == "failed":
                failures.add("functional")
                note("LIF_NORMAL_INFERENCE_FAILED", row)
    if complete:
        ceilings["security"] = "PASS"
        if check.id == "LIF-07":
            ceilings["functional"] = "PASS"
    for axis in failures:
        ceilings[axis] = "FAIL"
    note("PERFORMANCE_UNVERIFIED")
    if check.id == "SUP-14":
        note("FUNCTIONAL_UNVERIFIED")
    return ceilings, reasons, sorted(set(missing))
