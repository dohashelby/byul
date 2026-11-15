"""Read-only analysis of saved CPU case matrices (historical records).

CPU image execution was removed by the 2026-11-12 GPU-only decision. This tool
keeps only --analyze-existing so past CPU evidence remains verifiable; it never
starts an engine, runs a subprocess, or modifies the saved run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import zipfile


from guard.validation_evidence import (
    file_sha256 as _sha,
    inventory_tree as _inventory_tree,
    read_objects as _objects,
    verify_saved_bundle as _verify_saved_bundle,
    verify_store_files as _verify_store_files,
)

CHECKS = ("REQ-01", "REQ-03", "PLT-01", "PLT-04", "REQ-09")
EXECUTORS = ("direct_route", "role_route", "container_config", "credential_exposure", "input_validation")


def _new_json(path: Path, data) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def compare_report(report: dict, expected: dict | None) -> dict:
    actual = {}
    problems = []
    for row in report.get("by_check", []):
        if row.get("selected"):
            cid = row.get("check_id")
            if cid in actual:
                problems.append(f"duplicate selected report row: {cid}")
            actual[cid] = {k: (row.get("single") or {}).get(k) for k in ("security", "functional", "performance")}
    if set(actual) != set(CHECKS):
        problems.append("selected report checks differ from the exact five-check case")
    for cid, verdict in (expected or {}).items():
        if actual.get(cid, {}).get("security") != verdict:
            problems.append(f"{cid}: expected security {verdict}, observed {actual.get(cid, {}).get('security')}")
    if report.get("official") is not False or report.get("policy_status") != "DRAFT":
        problems.append("diagnostic report must remain nonofficial with DRAFT policy")
    return {"expected_security": expected, "actual": actual, "mismatches": problems, "matched": not problems}


def observation_contract(report: dict, objects: list[dict], pack, *, short_timeout: bool = False) -> dict:
    """Pure independent audit of saved objects; never runs or amends a judgment.

    A pinned rule_table result is checked against its original observations,
    policy and INV-11 gate. Both confirmed FAIL and withheld PASS must survive.
    The fixed pack comes from the source store, never today's working tree.
    """
    from guard.core.judge import pass_gate
    from guard.judges.rule_table import RuleTableJudge
    from guard.models import canonical_json, sha256_hex
    from guard.models.objects import object_from_dict

    problems, checks = [], {}
    try:
        parsed = [object_from_dict(o) for o in objects]
        if any(not o.hash_ok() for o in parsed):
            raise ValueError("stored object hash mismatch")
        index = {(str(o.kind), o.id, o.revision, o.content_sha256): o for o in parsed}
        if len(index) != len(parsed):
            raise ValueError("duplicate stored object identity")

        def resolve(ref):
            key = (ref.kind, ref.id, ref.revision, ref.sha256)
            if ref.kind == "PACK":
                if ref != pack.ref():
                    raise ValueError("object pack reference differs from the fixed snapshot")
                return pack
            if key not in index:
                raise ValueError(f"broken saved reference: {ref.kind} {ref.id}")
            return index[key]

        for obj in parsed:
            for ref in obj.refs:
                resolve(ref)
        saved_report = object_from_dict(report)
        if not saved_report.hash_ok() or resolve(saved_report.ref()).to_dict() != report:
            raise ValueError("report JSON differs from its sealed stored RPT")
        scope = compare_report(report, None)
        problems.extend(scope["mismatches"])
        plans = [o for o in parsed if str(o.kind) == "PLAN"]
        if len(plans) != 1:
            raise ValueError("one original PLAN is required")
        plan = plans[0]
        if plan.mode.value != "audit" or len(plan.inputs.environments) != 1:
            raise ValueError("case contract requires an audit PLAN with exactly one original ENV")
        original_env = plan.inputs.environments[0]
        resolve(original_env)
        if saved_report.mode != plan.mode:
            raise ValueError("report mode differs from the original audit PLAN")
        if resolve(plan.inputs.pack) is not pack:
            raise ValueError("PLAN pack mismatch")
        policy = resolve(plan.inputs.policy)
        if policy.status.value != "DRAFT" or policy.exceptions:
            raise ValueError("case contract requires the original DRAFT policy without time-dependent exceptions")
        selected = {item.check_id for item in plan.items if item.selected}
        if selected != set(CHECKS):
            raise ValueError("PLAN selection differs from the exact five checks")
        evds = [o for o in parsed if str(o.kind) == "EVD"]
        results = [o for o in parsed if str(o.kind) == "RES"]
        if len(evds) != 5 or len(results) != 5 or {e.check_id for e in evds} != set(CHECKS):
            raise ValueError("exactly five original EVD and RES are required")
        rows = {row["check_id"]: row for row in report["by_check"] if row.get("selected")}
        plugin = RuleTableJudge()
        for evd in evds:
            cid = evd.check_id
            check = pack.checks[cid]
            if evd.plan != plan.ref() or evd.side.value != "single":
                raise ValueError(f"{cid}: EVD PLAN/side mismatch")
            if evd.environment != original_env:
                raise ValueError(f"{cid}: EVD environment differs from the PLAN's original single ENV")
            item = plan.item(cid)
            if item.check_revision != check.revision or item.executor != check.executor:
                raise ValueError(f"{cid}: PLAN definition revision/executor differs from the fixed CheckDef")
            env = resolve(evd.environment)
            if env.runtime.gpu_count != 0 or env.runtime.cpu is None or evd.comparability.cpu != env.runtime.cpu:
                raise ValueError(f"{cid}: explicit matching CPU identity is required")
            executor = evd.execution.executor
            if (evd.execution.status.value != "COMPLETED" or evd.data_origin.value != "MEASURED"
                    or evd.execution.operator is not None or evd.execution.cleanup_errors
                    or executor is None or executor.name != check.executor or executor.default_judge):
                raise ValueError(f"{cid}: completed automated original measurement is required")
            if check.judge is None or check.judge.plugin != "rule_table":
                raise ValueError(f"{cid}: fixed rule_table definition is required")
            matching = [res for res in results if res.evidence == evd.ref() and res.check_id == cid and res.side == evd.side]
            if len(matching) != 1:
                raise ValueError(f"{cid}: exact original RES/EVD relation is required")
            res = matching[0]
            if (res.judge.name != plugin.name or res.judge.version != plugin.version
                    or res.judge.params_sha256 != sha256_hex(canonical_json(check.judge.params))):
                raise ValueError(f"{cid}: stored judge version/parameters differ from the pinned rule table")
            if policy.ref() not in res.refs:
                raise ValueError(f"{cid}: RES does not reference the original POL")
            raw = plugin.judge(check, evd, check.judge.params, policy.values.get(cid, {}))
            blocks = pass_gate(check, evd)
            recorded_steps = [challenge.step for challenge in evd.challenges]
            if len(recorded_steps) != len(set(recorded_steps)) or set(recorded_steps) - set(check.challenge_steps):
                problems.append(f"{cid}: duplicate or unrecognized original challenge records")
            expected = {axis: str(raw.verdicts.get(axis, "UNKNOWN")) for axis in ("security", "functional", "performance")}
            expected = {axis: "UNKNOWN" if verdict == "PASS" and blocks else verdict for axis, verdict in expected.items()}
            expected["performance"] = "UNKNOWN"
            actual = res.verdicts.model_dump(mode="json")
            side = rows.get(cid, {}).get("single") or {}
            if actual != expected:
                problems.append(f"{cid}: saved RES differs from the fixed rules and PASS gate")
            if side.get("result") != res.ref().model_dump(mode="json") or any(side.get(a) != actual[a] for a in actual):
                problems.append(f"{cid}: report differs from the original RES/ref")
            if actual["security"] == "PASS" and blocks:
                problems.append(f"{cid}: PASS without complete normal control/challenges/required observations")
            if short_timeout and actual["security"] not in {"PASS", "UNKNOWN"}:
                problems.append(f"{cid}: unexpected confirmed security FAIL; retain and investigate it")
            confirmed = [axis for axis, verdict in raw.verdicts.items() if str(verdict) == "FAIL" and actual[axis] != "FAIL"]
            if confirmed:
                problems.append(f"{cid}: confirmed FAIL erased on {confirmed}")
            checks[cid] = {"evidence": evd.ref().model_dump(mode="json"), "result": res.ref().model_dump(mode="json"),
                           "raw_rule_verdicts": {axis: str(v) for axis, v in raw.verdicts.items()},
                           "pass_gate_blocks": blocks, "expected_from_stored_evidence": expected, "actual": actual,
                           "required_observations": [obs.key for obs in check.required_observations],
                           "challenge_steps": list(check.challenge_steps)}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        problems.append(f"saved observation contract invalid: {exc}")
    return {"schema": "guard-cpu-observation-contract/1", "success": not problems,
            "short_timeout": short_timeout, "pack_ref": pack.ref().model_dump(mode="json"),
            "checks": checks, "problems": problems,
            "limitations": ["This audits saved evidence; it does not execute an engine or authenticate the operator/host.",
                            "A verdict name alone is insufficient: original object refs, fixed rules and the PASS gate are checked."]}


def analyze_existing(source: Path, output: Path) -> dict:
    """Read-only compatibility analysis for matrix/1 and matrix/2 output."""
    source, output = source.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("analysis output must be a new directory outside the source matrix")
    before = _inventory_tree(source)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    if manifest.get("kind") not in {"guard-cpu-case-matrix/1", "guard-cpu-case-matrix/2"}:
        raise ValueError("unsupported source matrix schema")
    order = manifest.get("execution_order", [])
    if (len(order) != 3 or order[:2] != ["exposed-violations", "isolated-controls"]
            or order[2] not in {"isolated-insufficient-observation", "short-timeout-observation"}):
        raise ValueError("source must be the exact three-case CPU matrix")
    outcomes = []
    for index, name in enumerate(order):
        result = {"name": name, "success": False}
        try:
            folder = source / name
            objects = _objects(folder / "store")
            pack = _verify_store_files(folder / "store", objects)
            if pack.ref().model_dump(mode="json") != manifest.get("pack_ref"):
                raise ValueError("matrix manifest differs from the source store pack snapshot")
            reports = list((folder / "validation").glob("VALIDATE-*/report.json"))
            if len(reports) != 1:
                raise ValueError("one original JSON report is required")
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            contract = observation_contract(report, objects, pack, short_timeout=index == 2)
            expected = dict.fromkeys(CHECKS, ("FAIL", "PASS")[index]) if index < 2 else None
            comparison = compare_report(report, expected)
            restored = folder / "restored-store"
            restored_objects = _objects(restored)
            _verify_store_files(restored, restored_objects)
            _verify_saved_bundle(folder / "evidence.zip", restored)
            original_json = json.loads((folder / "original-objects.json").read_text(encoding="utf-8"))
            if objects != restored_objects or objects != original_json:
                raise ValueError("source/saved-object JSON/restored objects differ")
            old = [c for c in summary.get("cases", []) if c.get("name") == name]
            if len(old) != 1 or old[0].get("completed") is not True:
                raise ValueError("original matrix did not complete and preserve this case")
            result.update(contract=contract, fixed_expectation=comparison,
                          original_hypothesis=old[0].get("comparison"), refs=_proof(objects)["refs"],
                          success=contract["success"] and comparison["matched"])
        except (OSError, ValueError, LookupError, TypeError, sqlite3.Error, zipfile.BadZipFile) as exc:
            result["error"] = str(exc)
        outcomes.append(result)
    after = _inventory_tree(source)
    unchanged = before == after
    result = {"schema": "guard-cpu-matrix-analysis/1", "source_matrix_schema": manifest["kind"],
              "original_summary_sha256": _sha(source / "summary.json"),
              "original_success": summary.get("success"),
              "original_hypothesis_status": {"success": summary.get("hypothesis_success", summary.get("success")),
                                             "comparisons": [c.get("hypothesis_comparison", c.get("comparison")) for c in summary.get("cases", [])]},
              "observation_contract_success": unchanged and len(outcomes) == 3 and all(c["success"] for c in outcomes),
              "source_bytes_unchanged": unchanged, "source_inventory_before": before,
              "source_inventory_after": after, "cases": outcomes,
              "limitations": ["Independent read-only analysis, not a replacement for the original success or 14/15 hypothesis result.",
                              "No engine, source store, prior report, EVD, RES, fixed pack or archived summary is changed."]}
    output.mkdir(parents=True, exist_ok=False)
    _new_json(output / "analysis.json", result)
    return result


def _proof(objects: list[dict]) -> dict:
    evds = [o for o in objects if o.get("kind") == "EVD"]
    results = [o for o in objects if o.get("kind") == "RES"]
    problems = []
    if len(evds) != 5 or {o.get("check_id") for o in evds} != set(CHECKS):
        problems.append("need exactly five actual EVD objects")
    if len(results) != 5 or {o.get("check_id") for o in results} != set(CHECKS):
        problems.append("need exactly five RES objects")
    for evd in evds:
        execution = evd.get("execution") or {}
        if (evd.get("side") != "single" or execution.get("status") != "COMPLETED"
                or evd.get("data_origin") != "MEASURED" or execution.get("operator") is not None):
            problems.append(f"{evd.get('check_id')}: actual completed automated measurement is missing")
        if execution.get("cleanup_errors"):
            problems.append(f"{evd.get('check_id')}: cleanup incomplete")
    refs = [{"kind": o["kind"], "id": o["id"], "revision": o["revision"], "sha256": o["content_sha256"]}
            for o in objects]
    return {"refs": refs, "evidence": [{"check_id": e["check_id"], "data_origin": e["data_origin"],
             "execution": e["execution"], "side": e["side"]} for e in evds], "problems": problems}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="removed: CPU execution is no longer supported")
    parser.add_argument("--env", type=Path, help="removed: CPU execution is no longer supported")
    parser.add_argument("--analyze-existing", type=Path, help="read-only matrix/1 or matrix/2 analysis; no engine or subprocess")
    parser.add_argument("--output", type=Path, required=True, help="new directory only; three engines maximum, sequential")
    parser.add_argument("--pack", type=Path, help="accepted for compatibility; analysis uses the saved pack only")
    args = parser.parse_args(argv)
    if args.analyze_existing is not None:
        if args.config is not None or args.env is not None:
            parser.error("--analyze-existing cannot be combined with --config or --env")
        try:
            analysis = analyze_existing(args.analyze_existing.expanduser(), args.output.expanduser())
        except (OSError, ValueError, LookupError, sqlite3.Error, zipfile.BadZipFile) as exc:
            parser.error(str(exc))
        print(json.dumps({"observation_contract_success": analysis["observation_contract_success"],
                          "original_success": analysis["original_success"],
                          "analysis": str(args.output / "analysis.json")}, ensure_ascii=False))
        return 0 if analysis["observation_contract_success"] else 1
    # 2026-11-12 GPU-only decision: CPU image execution was removed. Saved
    # CPU case matrices remain readable through --analyze-existing.
    parser.error("CPU case execution was removed (GPU-only, 2026-11-12); only --analyze-existing remains")

if __name__ == "__main__":
    raise SystemExit(main())
