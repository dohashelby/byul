"""OBS-11 ceilings over declared facts. No engine, files, clock or Store access."""
from __future__ import annotations

from collections import defaultdict
import json

from guard.manual_audit import CASES, OBSERVATION, PREFIX, enabled, normalize, policy_state, timestamp


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _identity(value):
    return _text(value) and value == value.strip()


class _Facts:
    def __init__(self, row, budgets, conflicts):
        self.row, self.o, self.b, self.conflicts = row, row["observations"], budgets, conflicts
        self.claims = {r["claim"] for r in row["evidence"] if r.get("sha256") and _text(r.get("locator")) and _text(r.get("basis"))}

    def has(self, *claims):
        return set(claims) <= self.claims

    def base(self):
        o = self.o
        return (not self.conflicts.intersection({"identity", "occurrence"})
                and all(_identity(o.get(f)) for f in ("trial_id", "event_id", "target_scope", "path_id",
                                                       "occurrence_source", "collection_source", "clock_source"))
                and all(_text(o.get(f)) for f in ("profile_detail", "independence_detail"))
                and o.get("profile_applicability") == "confirmed" and o.get("occurrence") == "confirmed"
                and o["occurrence_source"] != o["collection_source"]
                and o.get("clock_basis") == "bounded_common_time"
                and self.has("profile", "occurrence", "independence", "clock"))

    def interval(self, start, end):
        errors = self.o.get("time_errors_ms") or {}
        a, b = timestamp(self.o.get(start)), timestamp(self.o.get(end))
        ea, eb, limit = errors.get(start), errors.get(end), self.b.get("max_clock_uncertainty_ms")
        if a is None or b is None or ea is None or eb is None or limit is None or ea + eb > limit:
            return None
        return b - a - ea - eb, b - a + ea + eb

    def within(self, field, budget):
        interval = self.interval("occurred_at", field)
        return interval is not None and budget is not None and 0 <= interval[0] and interval[1] <= budget

    def window(self, alert=False):
        o = self.o
        key = "alert_window" if alert else "collection_window"
        start, end = ("alert_window_start_at", "alert_window_end_at") if alert else ("window_start_at", "window_end_at")
        source, detail = ("alert_source", "alert_window_detail") if alert else ("window_source", "window_detail")
        c = self.b.get("collection_deadline_ms")
        extra = self.b.get("alert_deadline_ms") if alert else 0
        lo, hi = self.interval("occurred_at", start), self.interval("occurred_at", end)
        claims = ("alert", "alert_window") if alert else ("collection", "window")
        return (c is not None and extra is not None and o.get(key) == "complete"
                and _identity(o.get(source)) and o[source] != o.get("collection_source") and _text(o.get(detail))
                and self.has(*claims) and lo is not None and hi is not None and lo[1] <= 0 and hi[0] > c + extra)

    def missing(self):
        return self.base() and self.o.get("record") == "absent" and self.o.get("received_at") is None and self.window()

    def dependent(self):
        dep = self.o.get("dependent") or {}
        linked = ("dependent" not in self.conflicts and self.has("dependent") and _identity(dep.get("test_id"))
                  and dep.get("depends_on_record") is True and dep.get("execution_status") in ("COMPLETED", "ABORTED", "NOT_RUN"))
        handled = linked and (dep.get("security") == "UNKNOWN" or (
            dep.get("security") == "FAIL" and dep.get("independent_failure") is True and self.has("dependent_failure")))
        false_pass = linked and dep.get("execution_status") != "NOT_RUN" and dep.get("security") == "PASS"
        return handled, false_pass

    def positive(self):
        o, c = self.o, self.b.get("collection_deadline_ms")
        if not self.base() or c is None or o.get("attempted") is not True or not _text(o.get("case_detail")):
            return False
        if self.row["case"] == "normal":
            return o.get("record") == "received" and self.has("collection") and self.within("received_at", c)
        if self.row["case"] == "clock_uncertain":
            delta = self.interval("occurred_at", "received_at")
            return (o.get("record") == "received" and self.has("collection") and delta is not None
                    and 0 <= delta[0] <= c < delta[1] and self.dependent()[0])
        d, a = self.b.get("detection_deadline_ms"), self.b.get("alert_deadline_ms")
        detection, alert = self.interval("occurred_at", "detected_at"), self.interval("occurred_at", "alert_received_at")
        method = {"collector_interrupt": "interrupted", "write_denied": "write_denied"}.get(o.get("fault_method"))
        return (self.missing() and "collector" not in self.conflicts and method is not None and o.get("collector") == method
                and self.has("collector", "detection", "alert", "recovery") and o.get("missing_handling") == "identified"
                and d is not None and a is not None and detection is not None and alert is not None
                and detection[0] >= c and alert[0] >= c
                and self.within("detected_at", c + d) and self.within("alert_received_at", c + a)
                and o.get("alert") == "received" and _identity(o.get("alert_receiver")) and _identity(o.get("alert_source"))
                and self.dependent()[0] and o.get("recovery") == "restored")

    def problems(self):
        """Safe diagnostics: names/codes only, never raw source details."""
        o, c = self.o, self.b.get("collection_deadline_ms")
        codes = [f"NO_VALID_POLICY:{key}" for key in ("collection_deadline_ms", "max_clock_uncertainty_ms",
                  "detection_deadline_ms", "alert_deadline_ms") if key not in self.b]
        if not self.base(): codes.append("PROFILE_IDENTITY_OCCURRENCE_OR_CLOCK_UNCONFIRMED")
        if o.get("attempted") is not True: codes.append("CASE_NOT_ATTEMPTED")
        if o.get("record") == "received":
            interval = self.interval("occurred_at", "received_at")
            if interval is None or c is None: codes.append("TIME_BASIS_UNCONFIRMED")
            elif interval[0] < 0: codes.append("TIME_ORDER_UNCONFIRMED")
            elif interval[0] > c: codes.append("COLLECTION_LATE")
            elif interval[1] > c: codes.append("TIME_UNCERTAIN")
        elif o.get("record") == "absent" and not self.window():
            codes.append("COLLECTION_WINDOW_UNCONFIRMED")
        if o.get("alert") == "absent" and not self.window(alert=True): codes.append("ALERT_WINDOW_UNCONFIRMED")
        if self.row["case"] != "normal" and not self.dependent()[0]: codes.append("DEPENDENT_HANDLING_UNCONFIRMED")
        if self.row["case"] == "collection_fault":
            if o.get("recovery") != "restored": codes.append("RECOVERY_UNCONFIRMED")
            if o.get("alert") == "received" and not _identity(o.get("alert_receiver")): codes.append("ALERT_RECEIVER_UNCONFIRMED")
        return codes


def _conflicts(rows, budgets):
    """Only shared facts conflict; different record receipt times may be valid."""
    groups, identities = defaultdict(list), defaultdict(list)
    conflicts = defaultdict(set)
    for row in rows:
        o = row["observations"]
        facts = _Facts(row, budgets.get(row["record_id"], {}), set())
        if facts.base():
            identity = tuple(o[f] for f in ("trial_id", "event_id", "target_scope", "path_id"))
            groups[(*identity, row["case"])].append(row)
            identities[(row["record_id"], *identity)].append(row)
    for group in identities.values():
        if len({r["case"] for r in group}) > 1:
            for r in group: conflicts[(r["record_id"], r["case"])].add("identity")
    for group in groups.values():
        times = []
        for r in group:
            o = r["observations"]; t = timestamp(o.get("occurred_at")); e = (o.get("time_errors_ms") or {}).get("occurred_at")
            limit = budgets.get(r["record_id"], {}).get("max_clock_uncertainty_ms")
            if t is not None and e is not None and limit is not None and e <= limit: times.append((t - e, t + e))
        if times and max(lo for lo, _ in times) > min(hi for _, hi in times):
            for r in group: conflicts[(r["record_id"], r["case"])].add("occurrence")
        states = {r["observations"].get("collector") for r in group} - {None, "unknown"}
        if len(states) > 1:
            for r in group: conflicts[(r["record_id"], r["case"])].add("collector")
        dependent = defaultdict(list)
        for r in group:
            dep = r["observations"].get("dependent") or {}
            if (_Facts(r, budgets.get(r["record_id"], {}), set()).has("dependent") and _identity(dep.get("test_id"))
                    and dep.get("execution_status") in ("COMPLETED", "ABORTED", "NOT_RUN")):
                dependent[dep["test_id"]].append(r)
        for same_test in dependent.values():
            if len({(r["observations"].get("dependent") or {}).get("security") for r in same_test} - {None}) > 1:
                for r in same_test: conflicts[(r["record_id"], r["case"])].add("dependent")
    return conflicts


def evaluate(check, evidence, values):
    if not enabled(check):
        return None
    ceilings = dict.fromkeys(("security", "functional", "performance"), "UNKNOWN")
    reasons, missing = [], []

    def note(code, row=None, *, gap=False):
        identity = "" if row is None else f" record={json.dumps(row['record_id'], ensure_ascii=False)} case={row['case']}"
        hashes = sorted({r["sha256"][:12] for r in row["evidence"] if r.get("sha256")}) if row else []
        reasons.append(f"{PREFIX}{identity} {code}" + (f" evidence={','.join(hashes)}" if hashes else ""))
        if gap: missing.append(f"{OBSERVATION}{identity}: {code}")

    items, complete, budgets = policy_state(values)
    if not complete: note("POLICY_SET_OR_BUDGET_UNRESOLVED", gap=True)
    obs = evidence.observations.get(OBSERVATION)
    if obs is None or not obs.collected:
        note("AUDIT_EVIDENCE_MISSING", gap=True)
        return ceilings, reasons, missing
    try:
        data = normalize(check, values, obs.value, {a.sha256: a.sha256 for a in evidence.artifacts}, stored=True)
        if data is None: raise ValueError("empty payload")
    except (ValueError, TypeError):
        note("AUDIT_EVIDENCE_INVALID", gap=True)
        return ceilings, reasons, missing
    rows = {(r["record_id"], r["case"]): r for r in data["records"]}
    conflicts = _conflicts(data["records"], budgets)
    failed = False
    for item in items:
        for case in CASES:
            row = rows.get((item, case))
            if row is None:
                note("CASE_MISSING", {"record_id": item, "case": case, "evidence": []}, gap=True)
                complete = False
                continue
            f = _Facts(row, budgets[item], conflicts[(item, case)])
            if f.conflicts: note("EVENT_CONFLICT", row, gap=True)
            if f.missing():
                note("REQUIRED_RECORD_MISSING", row)
                if (f.o.get("missing_handling") == "treated_normal" and f.has("detection")) or f.dependent()[1]:
                    failed = True
                    note("MISSING_TREATED_NORMAL", row)
                if (f.o.get("alert") == "absent" and f.o.get("alert_received_at") is None
                        and _identity(f.o.get("alert_receiver")) and f.window(alert=True)):
                    failed = True
                    note("REQUIRED_ALERT_ABSENT", row)
            if not f.positive() or _text(row.get("limitation")) or f.conflicts:
                complete = False
                note("CASE_OR_BASIS_UNCONFIRMED", row, gap=True)
                for code in f.problems(): note(code, row, gap=True)
            else:
                note({"normal": "NORMAL_LINKED", "collection_fault": "OUTAGE_HANDLED",
                      "clock_uncertain": "UNCERTAINTY_HANDLED"}[case], row)
    if complete: ceilings["security"] = "PASS"
    if failed: ceilings["security"] = "FAIL"
    note("FUNCTIONAL_PERFORMANCE_UNVERIFIED")
    note("DECLARED_LINKAGE_ONLY_NO_AUTHENTICATION_OR_RES_PROPAGATION")
    return ceilings, reasons, sorted(set(missing))
