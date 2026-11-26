"""Inventory the frozen main without creating ENV, POL, EVD, RES or RPT objects.

Run on Linux CI. Historical targets are deliberately not current input sources.
The canonical policy decisions are inspected as decisions, never promoted into
an approved policy for the new version pair or a fabricated live environment.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess
import sys

from guard.configuration import KUBERNETES_BOUND_CHECK_OPTIONS
from guard.core.select import check_policy_value
from guard.models import load_pack
from tools.mvp_live_readiness import audit, template

SOURCE_MAIN = "22e6e83d4fe26074c3c6c6e5e68742863f5239fe"
ROOT = Path(__file__).resolve().parents[2]
UPDATE = ROOT / "update"


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    subprocess.run(["git", "diff", "--exit-code", SOURCE_MAIN, "--", "update", "contracts", "reference"],
                   cwd=ROOT, check=True)
    pack = load_pack(UPDATE / "packs/PACK-BASELINE")
    active = {check.id: check for check in pack.active_checks()}
    index = args.output / "current-input-sources.json"
    write(index, template(pack))
    readiness = audit(index, pack=pack)
    write(args.output / "product-live-readiness.json", readiness)

    base_path = UPDATE / "docs/dev-1/mvp-enterprise-policy-20261116.json"
    amendment_path = UPDATE / "docs/dev-1/mvp-enterprise-policy-20261118.json"
    base, amendment = read(base_path), read(amendment_path)
    if (base["status"] != "CONFIRMED" or amendment["status"] != "CONFIRMED"
            or sha256(base_path.read_bytes()).hexdigest() != amendment["base_document"]["file_sha256"]
            or amendment["pack_ref"] != pack.ref().model_dump(mode="json")):
        raise ValueError("CANONICAL_POLICY_DECISION_CHAIN_MISMATCH")
    decisions = {}
    for item in [*base["decisions"], *amendment["decisions"]]:
        decisions.setdefault(item["check_id"], {})[item["key"]] = item["value"]
    runtime = {}
    for item in base["runtime_materialization_required"]:
        runtime.setdefault(item["check_id"], {})[item["key"]] = item["rule"]

    rows = []
    for raw in readiness["rows"]:
        cid, side = raw["check_id"], raw["side"]
        check = active[cid]
        values, runtime_rules = decisions.get(cid, {}), runtime.get(cid, {})
        required = [param.key for param in check.policy_params if param.required]
        missing = [key for key in required if key not in values and key not in runtime_rules]
        invalid = []
        for param in check.policy_params:
            if param.key in values and param.key not in runtime_rules:
                try:
                    check_policy_value(cid, param.key, param.type, values[param.key])
                except (TypeError, ValueError) as error:
                    invalid.append({"key": param.key, "error": str(error)})
        codes = [*raw["blocking_codes"], "CURRENT_PAIRED_POLICY_NOT_BOUND"]
        if side == "candidate":
            codes.append("CANONICAL_POLICY_SCOPE_DOES_NOT_COVER_CANDIDATE")
        if missing:
            codes.append("REQUIRED_POLICY_DECISION_MISSING")
        if runtime_rules:
            codes.append("RUNTIME_POLICY_VALUES_NOT_MATERIALIZED")
        if invalid:
            codes.append("POLICY_DECISION_TYPE_INVALID")
        rows.append({
            **raw,
            "collection_profile": getattr(check, "collection_profile", None),
            "question": check.question,
            "remaining_setup": getattr(check, "remaining_setup", None),
            "reviewed_bounded_configuration_option": KUBERNETES_BOUND_CHECK_OPTIONS.get(cid),
            "required_policy_decisions_absent": missing,
            "policy_decision_type_errors": invalid,
            "runtime_policy_rules_requiring_current_values": runtime_rules,
            "missing_current_input_files": ["sealed environment", "run-bound policy", "side/check configuration"],
            "current_target_and_prepared_assets_verified": False,
            "static_implementation_available": bool(raw["available_implementations"]),
            "execution_status": "NOT_RUN",
            "execution_status_stage": "PRE_START_NO_LIVE_RUN",
            "blocking_codes": list(dict.fromkeys(codes)),
        })

    expected = {(cid, side) for cid in active for side in ("baseline", "candidate")}
    if (len(active) != 69 or len(rows) != 138
            or {(row["check_id"], row["side"]) for row in rows} != expected
            or any(row["engine_executed"] or row["actual_results"] is not None for row in rows)):
        raise ValueError("ALL_ACTIVE_PAIRED_SCOPE_INTEGRITY_FAILED")
    summary = {
        "schema": "byul-all69-prestart-inventory/1",
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "source_main": SOURCE_MAIN,
        "audit_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python": sys.version,
        "platform": platform.platform(),
        "pack_ref": pack.ref().model_dump(mode="json"),
        "reviewed_source_file_sha256": sha256((UPDATE / "release/reviewed-source.json").read_bytes()).hexdigest(),
        "active_checks": len(active), "preflight_sides": len(rows),
        "current_live_inputs_supplied": 0,
        "engine_executed": False, "rpt_created": False,
        "not_run_stage": "PRE_START_NO_LIVE_RUN",
        "not_run": {side: sum(row["side"] == side for row in rows) for side in ("baseline", "candidate")},
        "common_not_run": len(active),
        "static_implementation_unavailable": {
            side: [row["check_id"] for row in rows
                   if row["side"] == side and not row["static_implementation_available"]]
            for side in ("baseline", "candidate")},
        "blocking_counts": dict(Counter(code for row in rows for code in row["blocking_codes"])),
        "canonical_policy": {
            "base": {"id": base["id"], "sha256": sha256(base_path.read_bytes()).hexdigest()},
            "amendment": {"id": amendment["id"], "sha256": sha256(amendment_path.read_bytes()).hexdigest()},
            "scope": amendment["scope"],
            "new_pair_policy_generated": False,
        },
        "limits": ["Registry availability is not a checked collection profile or live qualification.",
                   "No current ENV, target, policy, prepared assets or live prepare-update was supplied.",
                   "NOT_RUN is a pre-start inventory state; no new GPU RPT exists.",
                   "Historical measured results and pending queues are excluded."],
        "rows": rows,
    }
    write(args.output / "all69-prestart-inventory.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
