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
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import tomllib

import yaml


CHECKS = ("REQ-01", "REQ-03", "PLT-01", "PLT-04", "REQ-09")
EXECUTORS = ("direct_route", "role_route", "container_config", "credential_exposure", "input_validation")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        ("isolated-insufficient-observation", "UNKNOWN", "isolated", "role_enforced", "hardened", 0.000001, 127, False),
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
        cases.append({"name": name, "config": cfg, "policy": policy, "expected": dict.fromkeys(CHECKS, expected),
                      "limitations": [
                          "This is a generated EXAMPLE/DRAFT diagnostic policy, not an enterprise approval or a release verdict.",
                          "max_input_tokens=8 deliberately tests a policy boundary below the actual 128-token engine fixture; acceptance is a test-policy mismatch, not proof of memory corruption.",
                          "GUARD_TEST_CREDENTIAL is a fixed harmless canary; no real credential is injected or collected.",
                          "A positive 1e-6-second timeout requests genuinely insufficient observation; actual outcomes are preserved and may differ from UNKNOWN.",
                          "No verdict, EVD, RES, environment identity or engine response is overwritten to match the expectation.",
                      ]})
    return cases


def compare_report(report: dict, expected: dict) -> dict:
    actual = {}
    problems = []
    for row in report.get("by_check", []):
        if row.get("selected"):
            cid = row.get("check_id")
            if cid in actual:
                problems.append(f"duplicate selected report row: {cid}")
            actual[cid] = {k: (row.get("single") or {}).get(k) for k in ("security", "functional", "performance")}
    if set(actual) != set(expected):
        problems.append("selected report checks differ from the exact five-check case")
    for cid, verdict in expected.items():
        if actual.get(cid, {}).get("security") != verdict:
            problems.append(f"{cid}: expected security {verdict}, observed {actual.get(cid, {}).get('security')}")
    if report.get("official") is not False or report.get("policy_status") != "DRAFT":
        problems.append("diagnostic report must remain nonofficial with DRAFT policy")
    return {"expected_security": expected, "actual": actual, "mismatches": problems, "matched": not problems}


def _objects(store: Path) -> list[dict]:
    db = store / "store.sqlite"
    if not db.is_file():
        raise ValueError("product store was not created")
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
        return [json.loads(row[0]) for row in con.execute("SELECT body FROM objects ORDER BY kind,id,revision")]


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
    parser.add_argument("--config", type=Path, required=True, help="actual prepared bounded CPU product TOML")
    parser.add_argument("--env", type=Path, required=True, help="actual CPU/model/image ENV; never generated by this harness")
    parser.add_argument("--output", type=Path, required=True, help="new directory only; three engines maximum, sequential")
    parser.add_argument("--pack", type=Path, default=Path("packs/PACK-BASELINE"))
    parser.add_argument("--command-timeout", type=int, default=3600, help="seconds per CLI command, maximum 7200")
    args = parser.parse_args(argv)
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
    _new_json(output / "manifest.json", {"kind": "guard-cpu-case-matrix/1", "isolated_sandbox": True,
               "source_sha256": before, "pack_ref": pack.ref().model_dump(mode="json"),
               "maximum_engine_starts": 3, "execution_order": [c["name"] for c in cases],
               "environment_identity": "unchanged user --env; declared values still need independent host verification",
               "cases": [{k: c[k] for k in ("name", "expected", "limitations")} for c in cases]})
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
            if len(reports) != 1 or not reports[0].with_suffix(".html").is_file():
                raise ValueError("one JSON and HTML product report pair is required")
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            comparison = compare_report(report, case["expected"])
            _new_json(folder / "expected-actual.json", comparison)
            original_objects = _objects(store)
            proof = _proof(original_objects)
            _new_json(folder / "original-objects.json", original_objects)
            _new_json(folder / "proof.json", proof)
            outcome.update(comparison=comparison, proof_problems=proof["problems"])
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
               and all(c["completed"] and c.get("comparison", {}).get("matched") for c in outcomes))
    _new_json(output / "summary.json", {"success": success, "cases": outcomes,
               "source_inputs_unchanged": before == after, "source_sha256_after": after,
               "limitations": ["Expected verdicts are hypotheses; mismatches remain unedited and cause nonzero exit.",
                               "This is single-image diagnostic validation, not two-image update or GPU verification.",
                               "EXAMPLE/DRAFT test policies keep every report nonofficial."]})
    print(json.dumps({"success": success, "summary": str(output / "summary.json")}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
