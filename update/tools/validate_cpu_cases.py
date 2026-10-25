"""Run three explicit CPU sandbox cases sequentially through the product CLI.

This is an opt-in real-engine harness, not a test result or an enterprise policy.
It never pulls images, provisions servers, edits source inputs or deletes Docker
resources. Product validation owns the lifecycle of each newly created sandbox.
Existing unrelated containers are preserved. Run only against prepared local
Docker Desktop Linux/WSL2 or Linux CPU fixtures, never a production endpoint.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import tomllib
import zipfile

import yaml

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


def _toml_value(value) -> str:
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False)
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float and math.isfinite(value):
        return repr(value)
    if type(value) is list:
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if type(value) is dict:
        return "{ " + ", ".join(json.dumps(k) + " = " + _toml_value(v) for k, v in value.items()) + " }"
    raise ValueError("case config supports finite scalar values, lists and tables only")


def config_toml(data: dict) -> str:
    """Small deterministic writer for an already-parsed user TOML configuration."""
    lines = []

    def table(values, path):
        if path:
            lines.append("[" + ".".join(json.dumps(p) for p in path) + "]")
        for key, value in values.items():
            if type(value) is not dict:
                lines.append(f"{json.dumps(key)} = {_toml_value(value)}")
        lines.append("")
        for key, value in values.items():
            if type(value) is dict:
                table(value, [*path, key])

    table(data, [])
    return "\n".join(lines)


def validate_seed_inputs(cfg: dict, env: dict) -> None:
    """Validate declared fixture identity without inventing or authenticating it."""
    launch = cfg.get("vllm", {})
    if "chat_template_profile" in launch:
        raise ValueError("the fixed five-check CPU matrix is completions-only; use the separate chat fixture validation instead")
    if launch.get("launch_profile") != "cpu-bounded" or launch.get("model_layout") != "snapshot":
        raise ValueError("cases require the explicit cpu-bounded/snapshot launch profile")
    if type(launch.get("max_model_len")) is not int or launch["max_model_len"] != 128:
        raise ValueError("this fixed case matrix requires the actual max_model_len=128 diagnostic fixture")
    if not launch.get("models_dir"):
        raise ValueError("provide the actual offline snapshot directory")
    engine, model, runtime = (env.get(k, {}) for k in ("engine", "model", "runtime"))
    if (env.get("data_origin", "DECLARED") == "EXAMPLE" or engine.get("name") != "vllm"
            or not engine.get("version") or not re.fullmatch(r".+@sha256:[0-9a-f]{64}", engine.get("image_digest") or "")):
        raise ValueError("--env must identify the real vllm CPU version and pinned repository image; no EXAMPLE identity")
    if (not model.get("id") or not model.get("revision")
            or not re.fullmatch(r"[0-9a-f]{64}", model.get("inventory_sha256") or "")):
        raise ValueError("--env needs the actual model id/revision and snapshot inventory SHA-256")
    cpu = runtime.get("cpu") or {}
    if (type(runtime.get("gpu_count")) is not int or runtime["gpu_count"] != 0
            or cpu.get("system") != "Linux" or not cpu.get("architecture")
            or type(cpu.get("threads")) is not int or cpu["threads"] != launch.get("cpu_threads")):
        raise ValueError("--env needs a real declared Linux CPU profile matching cpu_threads and gpu_count=0")
    gateway = env.get("facts", {}).get("gateway")
    gateway = gateway.get("value") if type(gateway) is dict else gateway
    if gateway is not True:
        raise ValueError("--env must declare the sandbox Gateway fact; the harness does not invent environment facts")
    inp = cfg.get("input_validation", {})
    if type(inp.get("vocab_size")) is not int or inp["vocab_size"] < 1 or not inp.get("vocab_source"):
        raise ValueError("execution config must supply actual model vocab_size and its fixture source")
    if type(inp.get("max_probe_tokens")) is not int or inp["max_probe_tokens"] < 128:
        raise ValueError("input_validation.max_probe_tokens must allow the fixed 128-token boundary fixture")


def build_cases(base: dict, pack_id: str, pack_revision: int) -> list[dict]:
    """Generate only EXAMPLE/DRAFT policies and explicit sandbox configuration."""
    cases = []
    for name, expected, topology, gateway, engine, timeout, limit, canary in (
        ("exposed-violations", "FAIL", "exposed", "role_open", "default", 30.0, 8, True),
        ("isolated-controls", "PASS", "isolated", "role_enforced", "hardened", 30.0, 127, False),
        ("short-timeout-observation", None, "isolated", "role_enforced", "hardened", 0.000001, 127, False),
    ):
        cfg = deepcopy(base)
        cfg.pop("sides", None)
        cfg.pop("update", None)
        cfg["adapter"] = {"name": "vllm"}
        cfg["executors"] = dict.fromkeys(EXECUTORS, "real")
        cfg["validation"] = {"mode": "managed", "topology": topology, "gateway_profile": gateway,
                             "engine_profile": engine, "credential_canary": canary}
        for executor in EXECUTORS:
            cfg.setdefault(executor, {})["request_timeout"] = timeout
            cfg[executor].pop("target", None)
            cfg[executor].pop("container", None)
        direct_roles = cfg.setdefault("direct_route", {}).setdefault("roles", {"allowed": "GUARD_CPU_GENERAL"})
        cfg.setdefault("role_route", {}).setdefault("roles", {"general": "GUARD_CPU_GENERAL", "admin": "GUARD_CPU_ADMIN"})
        cfg["role_route"].setdefault("admin_role", "admin")
        cfg["container_config"]["sentinel_paths"] = ["/models", "/"]
        cfg["direct_route"]["allow_mutating_routes"] = False
        cfg["role_route"]["allow_mutating_routes"] = False
        policy = {
            "data_origin": "EXAMPLE", "status": "DRAFT", "pack": {"id": pack_id, "revision": pack_revision},
            "values": {
                "REQ-01": {"allowed_callers": list(direct_roles)},
                "REQ-03": {"general_allowed_kinds": ["inference"]},
                "PLT-01": {"allowed_capabilities": [], "allowed_mounts": ["/models:ro"], "allowed_devices": [],
                           "require_non_root": True, "allowed_exec_writable_paths": ["/guard-cache"]},
                "PLT-04": {"forbidden_env_names": ["GUARD_TEST_CREDENTIAL"], "forbidden_mount_paths": []},
                "REQ-09": {"max_input_tokens": limit, "rejection_codes": ["400", "413", "422"],
                           "allowed_endpoints": ["POST /v1/completions"], "allowed_input_formats": ["token_ids"]},
            },
            "scope": {"include": list(CHECKS), "exclude": []}, "exceptions": [],
            "confirmed_by": None, "confirmed_at": None,
        }
        cases.append({"name": name, "config": cfg, "policy": policy,
                      "expected": dict.fromkeys(CHECKS, expected) if expected is not None else None,
                      "legacy_hypothesis": dict.fromkeys(CHECKS, expected or "UNKNOWN"),
                      "observation_contract": "short-timeout" if expected is None else "fixed-security",
                      "limitations": [
                          "This is a generated EXAMPLE/DRAFT diagnostic policy, not an enterprise approval or a release verdict.",
                          "max_input_tokens=8 deliberately tests a policy boundary below the actual 128-token engine fixture; acceptance is a test-policy mismatch, not proof of memory corruption.",
                          "GUARD_TEST_CREDENTIAL is a fixed harmless canary; no real credential is injected or collected.",
                          "A positive 1e-6-second HTTP timeout does not force collection failure: PLT-01 uses a 30-second normal-control floor and independent Docker collection timeouts.",
                          "The short-timeout row requires stored-rule and PASS-gate verification; an allowed verdict name alone never satisfies its observation contract.",
                          "No verdict, EVD, RES, environment identity or engine response is overwritten to match the expectation.",
                      ]})
    return cases


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


def _command(args: list[str], folder: Path, label: str, timeout: int) -> dict:
    started = time.monotonic()
    record = {"argv": args, "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
        record.update(returncode=result.returncode, stdout=result.stdout.decode("utf-8", "replace"),
                      stderr=result.stderr.decode("utf-8", "replace"))
    except subprocess.TimeoutExpired as exc:
        record.update(returncode=None, timed_out=True,
                      stdout=(exc.stdout or b"").decode("utf-8", "replace"),
                      stderr=(exc.stderr or b"").decode("utf-8", "replace"),
                      limitation="Child timeout may prevent product teardown; stop the matrix and inspect this case's guard.run resources. No resources are deleted by the harness.")
    record["elapsed_seconds"] = time.monotonic() - started
    _new_json(folder / f"{label}.json", record)
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="actual prepared bounded CPU product TOML")
    parser.add_argument("--env", type=Path, help="actual CPU/model/image ENV; never generated by this harness")
    parser.add_argument("--analyze-existing", type=Path, help="read-only matrix/1 or matrix/2 analysis; no engine or subprocess")
    parser.add_argument("--output", type=Path, required=True, help="new directory only; three engines maximum, sequential")
    parser.add_argument("--pack", type=Path, default=Path("packs/PACK-BASELINE"))
    parser.add_argument("--command-timeout", type=int, default=3600, help="seconds per CLI command, maximum 7200")
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
    if args.config is None or args.env is None:
        parser.error("--config and --env are required for actual engine execution")
    if not 1 <= args.command_timeout <= 7200:
        parser.error("--command-timeout must be in [1,7200]")
    config, env_path, output, pack_path = (p.expanduser().resolve() for p in (args.config, args.env, args.output, args.pack))
    if output.exists():
        parser.error("--output must not exist; source inputs and previous evidence are never overwritten")
    if output.is_relative_to(pack_path):
        parser.error("--output must be outside the input pack; generated evidence must not alter pack contents")
    source_paths = [config, env_path, pack_path / "pack.yaml"]
    before = {str(p): _sha(p) for p in source_paths}
    cfg = tomllib.loads(config.read_text(encoding="utf-8"))
    env = yaml.safe_load(env_path.read_text(encoding="utf-8"))
    validate_seed_inputs(cfg, env)
    from guard.models import load_pack
    pack = load_pack(pack_path)
    # Match product path semantics: the caller prepares an absolute snapshot path.
    models = Path(cfg["vllm"]["models_dir"]).expanduser()
    if not models.is_absolute():
        raise ValueError("use an absolute models_dir; the harness does not reinterpret the source configuration")
    cases = build_cases(cfg, pack.manifest.pack_id, pack.manifest.revision)
    output.mkdir(parents=True, exist_ok=False)
    _new_json(output / "manifest.json", {"kind": "guard-cpu-case-matrix/2", "isolated_sandbox": True,
               "source_sha256": before, "pack_ref": pack.ref().model_dump(mode="json"),
               "maximum_engine_starts": 3, "execution_order": [c["name"] for c in cases],
               "environment_identity": "unchanged user --env; declared values still need independent host verification",
               "cases": [{k: c[k] for k in ("name", "expected", "legacy_hypothesis", "observation_contract", "limitations")} for c in cases],
               "compatibility": "success retains the legacy fixed-verdict hypothesis; observation_contract_success controls the v2 exit status."})
    cli = [sys.executable, "-m", "guard.cli"]
    outcomes = []
    for case in cases:
        folder = output / case["name"]
        folder.mkdir()
        generated_config, policy_path = folder / "config.toml", folder / "policy.yaml"
        store, restored = folder / "store", folder / "restored-store"
        case["config"]["store"] = str(store)
        with generated_config.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(config_toml(case["config"]))
        with policy_path.open("x", encoding="utf-8", newline="\n") as stream:
            yaml.safe_dump(case["policy"], stream, allow_unicode=True, sort_keys=False)
        common = ["--pack", str(pack_path), "--env", str(env_path), "--policy", str(policy_path), "--config", str(generated_config)]
        outcome = {"name": case["name"], "expected_security": case["expected"], "completed": False}
        _new_json(folder / "generated-inputs.json", {str(p): _sha(p) for p in (generated_config, policy_path)})
        prepare = _command([*cli, "prepare-validation", *common], folder, "01-prepare", args.command_timeout)
        if prepare["returncode"] != 0:
            outcome["error"] = "preparation failed; no further engine started"
            outcomes.append(outcome)
            break
        execute = _command([*cli, "validate", *common, "--store", str(store), "--output", str(folder / "validation")],
                           folder, "02-validate", args.command_timeout)
        try:
            reports = list((folder / "validation").glob("VALIDATE-*/report.json"))
            if len(reports) != 1:
                raise ValueError("one JSON product report is required")
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            comparison = compare_report(report, case["expected"])
            _new_json(folder / "expected-actual.json", comparison)
            hypothesis = compare_report(report, case["legacy_hypothesis"])
            _new_json(folder / "hypothesis-actual.json", hypothesis)
            original_objects = _objects(store)
            snapshot = _verify_store_files(store, original_objects)
            contract = observation_contract(report, original_objects, snapshot,
                                            short_timeout=case["observation_contract"] == "short-timeout")
            _new_json(folder / "observation-contract.json", contract)
            proof = _proof(original_objects)
            _new_json(folder / "original-objects.json", original_objects)
            _new_json(folder / "proof.json", proof)
            outcome.update(comparison=comparison, hypothesis_comparison=hypothesis,
                           observation_contract=contract, proof_problems=proof["problems"])
            if execute["returncode"] != 0 or proof["problems"]:
                outcome["error"] = "execution/measurement/cleanup incomplete; matrix stopped without deleting resources"
                outcomes.append(outcome)
                break
            commands = [
                ("03-verify", ["verify", "--store", str(store), "--config", str(generated_config)]),
                ("04-export", ["evidence", "export", "--store", str(store), "--output", str(folder / "evidence.zip")]),
                ("05-restore", ["evidence", "restore", "--bundle", str(folder / "evidence.zip"), "--destination", str(restored)]),
                ("06-verify-restored", ["verify", "--store", str(restored), "--config", str(generated_config)]),
            ]
            for label, command in commands:
                if _command([*cli, *command], folder, label, args.command_timeout)["returncode"] != 0:
                    raise ValueError(f"product evidence preservation failed at {label}")
            restored_objects = _objects(restored)
            unchanged = original_objects == restored_objects
            _new_json(folder / "restored-proof.json", {"all_object_contents_equal": unchanged,
                       "proof": _proof(restored_objects)})
            outcome["completed"] = unchanged
            if not unchanged:
                raise ValueError("restored object contents differ; never amend evidence to conceal this")
        except (OSError, ValueError, sqlite3.Error) as exc:
            outcome["error"] = str(exc)
            outcomes.append(outcome)
            break
        outcomes.append(outcome)
    after = {str(p): _sha(p) for p in source_paths}
    success = (len(outcomes) == 3 and before == after
               and all(c["completed"] and c.get("hypothesis_comparison", {}).get("matched") for c in outcomes))
    contract_success = (len(outcomes) == 3 and before == after
                        and all(c["completed"] and c.get("comparison", {}).get("matched")
                                and c.get("observation_contract", {}).get("success") for c in outcomes))
    _new_json(output / "summary.json", {"schema": "guard-cpu-case-summary/2", "success": success,
               "hypothesis_success": success, "observation_contract_success": contract_success, "cases": outcomes,
               "source_inputs_unchanged": before == after, "source_sha256_after": after,
               "limitations": ["success/hypothesis_success preserve the old all-UNKNOWN timing hypothesis; they are separate from the v2 observation contract exit status.",
                               "Fixed normal/violation mismatches or unsupported saved verdicts fail observation_contract_success. Timing PASS needs the original complete evidence and fixed-rule verification.",
                               "This is single-image diagnostic validation, not two-image update or GPU verification.",
                               "EXAMPLE/DRAFT test policies keep every report nonofficial."]})
    print(json.dumps({"success": success, "observation_contract_success": contract_success,
                      "summary": str(output / "summary.json")}, ensure_ascii=False))
    return 0 if contract_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
