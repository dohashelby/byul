"""Build-independent installed-wheel contract check (Python 3.12 + uv).

The supervisor installs only uv.lock runtime dependencies and the supplied
wheel into a new environment. The copied worker runs outside the checkout with
Python -I; product execution is offline with EXAMPLE fixtures only.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from email.parser import BytesParser
import hashlib
import http.client
import importlib.util
from importlib.metadata import metadata
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch
from uuid import uuid4
import urllib.request
import zipfile


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def inventory(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): digest(path.read_bytes())
            for path in sorted(root.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}


def prohibited(*args, **kwargs):
    raise AssertionError("installed-wheel verification forbids subprocess, Docker and network execution")


def installed_worker(root: Path, wheel: Path) -> dict:
    assert sys.version_info[:2] == (3, 12), "installed-wheel verification requires Python 3.12"
    assert sys.flags.isolated == 1, "worker must use Python -I"
    sys.dont_write_bytecode = True
    os.chdir(root / "work")
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        os.environ.pop(key, None)
    import guard
    installed = Path(guard.__file__).resolve().parent
    assert installed.is_relative_to(Path(sys.prefix).resolve()) and "site-packages" in installed.parts
    expected = json.loads((root / "source-manifest.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(wheel) as archive:
        shipped = {name: archive.read(name) for name in archive.namelist() if name.startswith("guard/")}
        metadata_files = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        assert len(metadata_files) == 1, "wheel must contain exactly one distribution metadata file"
        wheel_metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
    requires_python = wheel_metadata.get("Requires-Python", "")
    assert {part.strip() for part in requires_python.split(",")} == {">=3.12", "<3.13"}, "wheel Python support range differs"
    installed_metadata = metadata("guard")
    assert installed_metadata["Requires-Python"] == requires_python, "wheel/installed Python support range differs"
    assert installed_metadata["Name"] == wheel_metadata["Name"] == "guard", "wheel/installed distribution name differs"
    assert expected == {name: digest(body) for name, body in shipped.items()}, "source/wheel files differ"
    assert all((installed.parent / name).read_bytes() == body for name, body in shipped.items())
    assert not any(".assessment" in Path(entry).parts or Path(entry).name in {"src", "tools", "tests"}
                   for entry in sys.path if entry), "source checkout leaked into isolated sys.path"
    from guard.cli import app
    from guard.models import load_pack
    from guard.validation_evidence import inventory_tree, read_objects, verify_store_files, verify_saved_bundle
    from typer.testing import CliRunner
    runner = CliRunner()
    commands = []

    def cli(argv, expected_code=0):
        result = runner.invoke(app, argv)
        assert result.exit_code == expected_code, result.output + repr(result.exception)
        commands.append({"command": argv[:2], "exit_code": result.exit_code})
        return result.output

    with ExitStack() as stack:
        for target in ("subprocess.run", "subprocess.Popen", "os.system", "os.popen",
                       "socket.socket", "socket.create_connection", "urllib.request.urlopen",
                       "http.client.HTTPConnection.connect", "http.client.HTTPSConnection.connect"):
            stack.enter_context(patch(target, prohibited))
        for argv in (["--help"], ["select", "--help"], ["run", "--help"], ["update", "--help"],
                     ["prepare-update", "--help"], ["judge", "--help"], ["report", "--help"],
                     ["policy", "template", "--help"], ["evidence", "export", "--help"],
                     ["evidence", "restore", "--help"]):
            cli(argv)
        pack_dir = root / "fixture-pack"
        pack = load_pack(pack_dir)
        inputs = root / "inputs"
        inputs.mkdir()
        env = inputs / "env.yaml"
        env.write_text("data_origin: EXAMPLE\nengine: {name: engine-x, version: 'EXAMPLE'}\nfacts: {gateway: true}\n", encoding="utf-8")
        policy = inputs / "policy.yaml"
        policy.write_text("data_origin: EXAMPLE\nstatus: DRAFT\npack: {id: " + pack.ref().id +
                          ", revision: " + str(pack.ref().revision) + "}\nscope: {include: [REQ-01]}\n"
                          "values: {REQ-01: {allowed_callers: [allowed]}}\n", encoding="utf-8")
        config = inputs / "fake.toml"
        config.write_text('[adapter]\nname="fake"\n[executors]\ndirect_route="fake"\n[fake]\nscenario="pass"\n', encoding="utf-8")
        original_inputs = inventory_tree(inputs)
        preparation = json.loads(cli(["prepare-update", "--pack", str(pack_dir),
            "--baseline", str(inputs / "EXAMPLE-missing-baseline.yaml"),
            "--candidate", str(inputs / "EXAMPLE-missing-candidate.yaml"),
            "--policy", str(policy), "--config", str(config)], expected_code=1))
        assert not preparation["ready"] and not preparation["guidance"]["engine_started"]
        assert {row["side"] for row in preparation["guidance"]["issues"] if row["code"] == "INPUT_MISSING"} == {"baseline", "candidate"}
        assert inventory_tree(inputs) == original_inputs
        store = root / "audit-store"
        common = ["--config", str(config), "--store", str(store)]
        plan_id = cli(["select", "--pack", str(pack_dir), "--env", str(env), "--policy", str(policy), *common]).strip().splitlines()[-1]
        cli(["run", "--plan", plan_id, "--pack", str(pack_dir), *common])
        absent = root / "absent-current-pack"
        frozen = "2026-10-24T00:00:00Z"
        cli(["judge", "--plan", plan_id, "--pack", str(absent), "--as-of", frozen, *common])
        report = root / "report.json"
        cli(["report", "--plan", plan_id, "--pack", str(absent), "--as-of", frozen,
             "--json", str(report), *common])
        data = json.loads(report.read_text(encoding="utf-8"))
        row = next(row for row in data["by_check"] if row["check_id"] == "REQ-01")
        assert row["single"]["security"] == "PASS" and not data["official"] and data["approval"] is None
        assert all(obj["data_origin"] == "EXAMPLE" for obj in read_objects(store))
        cli(["verify", *common])
        original = inventory_tree(store)
        bundle = root / "evidence.zip"
        cli(["evidence", "export", "--store", str(store), "--output", str(bundle)])
        restored = root / "restored"
        cli(["evidence", "restore", "--bundle", str(bundle), "--destination", str(restored)])
        verify_saved_bundle(bundle, restored)
        assert read_objects(restored) == read_objects(store)
        verify_store_files(restored, read_objects(restored))
        replay = ["--config", str(config), "--store", str(restored)]
        cli(["judge", "--plan", plan_id, "--pack", str(absent), "--as-of", frozen, *replay])
        replay_report = root / "replay-report.json"
        cli(["report", "--plan", plan_id, "--pack", str(absent), "--as-of", frozen,
             "--json", str(replay_report), *replay])
        new = json.loads(replay_report.read_text(encoding="utf-8"))
        assert new["by_check"] == data["by_check"] and not new["official"]
        assert inventory_tree(store) == original
        # Content-addressed corruption must be refused, never silently repaired.
        damaged = root / "damaged"
        cli(["evidence", "restore", "--bundle", str(bundle), "--destination", str(damaged)])
        next((damaged / "artifacts").iterdir()).write_bytes(b"EXAMPLE deliberate tampering")
        damaged_before = inventory_tree(damaged)
        cli(["verify", "--store", str(damaged), "--config", str(config)], expected_code=1)
        assert inventory_tree(damaged) == damaged_before
        template = root / "policy-template.yaml"
        cli(["policy", "template", "--pack", str(pack_dir), "--out", str(template)])
        assert "REQ-15" in template.read_text(encoding="utf-8")
        req15 = verify_req15(pack)
        analysis = verify_analysis_modules(root, store, report)
        for name, module in list(sys.modules.items()):
            if name == "guard" or name.startswith("guard."):
                if getattr(module, "__file__", None):
                    assert Path(module.__file__).resolve().is_relative_to(installed), name
        assert not any(name == "tests" or name.startswith(("tests.", "tools.")) for name in sys.modules)
    summary = {"schema": "guard-installed-verification/1", "success": True,
               "python": sys.version, "isolated": True, "source_wheel_installed_files_equal": len(shipped),
               "python312_verified": True, "requires_python": requires_python,
               "wheel_installed_python_metadata_equal": True,
               "wheel_sha256": digest(wheel.read_bytes()), "commands": commands,
               "example_fake_audit": True, "saved_pack_replay": True, "tampering_rejected": True,
               "source_unchanged": True, "req15": req15, "analysis": analysis,
               "real_engine_executed": False, "product_network_and_subprocess_blocked": True}
    write_json(root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def verify_req15(pack) -> dict:
    """Installed policy definition and conservative manual PASS/FAIL ceiling."""
    from guard.judges import manual_cache_group as group
    from guard.models import Evidence, Ref
    from guard.models.objects import object_from_dict
    from guard.judges.declared import DeclaredJudge
    check = pack.checks["REQ-15"]
    assert group.contract_state(check) == "complete"
    assert {param.key: param.type for param in check.policy_params if param.key in group.POLICIES} == group.POLICIES
    zero = Ref(kind="PLAN", id="PLAN-EXAMPLE-INSTALLED", revision=1, sha256="0" * 64)
    env_ref = Ref(kind="ENV", id="ENV-EXAMPLE-INSTALLED", revision=1, sha256="0" * 64)
    values = {"public_prefix_sharing_allowed": False, "isolated_cache_groups": ["EXAMPLE-A", "EXAMPLE-B"],
              "cache_identity_policy_ref": "EXAMPLE unapproved identity policy"}
    artifact = b"EXAMPLE manual declaration only; no actual cache trial"
    raw = Evidence(id="EVD-EXAMPLE-INSTALLED", data_origin="DECLARED", plan=zero, environment=env_ref,
        check_id="REQ-15", side="single", execution={"status": "COMPLETED"},
        normal_control={"attempted": True, "succeeded": True, "detail": "EXAMPLE manual declaration"},
        challenges=[{"step": step, "attempted": True} for step in check.challenge_steps],
        artifacts=[{"name": "EXAMPLE.txt", "path": "artifacts/" + digest(artifact), "sha256": digest(artifact),
                    "bytes": len(artifact), "collected_at": datetime(2026, 10, 24, tzinfo=timezone.utc), "collector": "EXAMPLE"}],
        observations={"declared_verdicts": {"collected": True, "value": dict.fromkeys(("security", "functional", "performance"), "PASS")},
            "declared_observation_notes": {"collected": True, "value": {
                item: "EXAMPLE self-reported notes" for item in check.required_observations_prose}}}).seal()
    plugin = DeclaredJudge()
    out = plugin.judge(check, raw, {}, values)
    assert out.verdicts["security"] == out.verdicts["functional"] == "UNKNOWN", out
    complete = raw.to_dict()
    complete["observations"][group.OBSERVATION] = {"collected": True, "value": req15_example(values, digest(artifact))}
    complete_evidence = object_from_dict(complete).seal()
    positive = plugin.judge(check, complete_evidence, {}, values)
    assert positive.verdicts["security"] == positive.verdicts["functional"] == "PASS", positive
    assert positive.verdicts["performance"] == "UNKNOWN"
    # A structurally complete payload cannot borrow another entry's attachment.
    foreign = complete_evidence.to_dict()
    foreign["artifacts"][0]["sha256"] = "f" * 64
    foreign_out = plugin.judge(check, object_from_dict(foreign).seal(), {}, values)
    assert foreign_out.verdicts["security"] == foreign_out.verdicts["functional"] == "UNKNOWN"
    changed = raw.to_dict()
    changed["observations"]["declared_verdicts"]["value"] = dict.fromkeys(("security", "functional", "performance"), "FAIL")
    failed = plugin.judge(check, object_from_dict(changed).seal(), {}, values)
    assert all(verdict == "FAIL" for verdict in failed.verdicts.values())
    partial = check.model_copy(deep=True)
    partial.policy_params = partial.policy_params[:-1]
    assert group.contract_state(partial) == "partial"
    assert group.evaluate(partial, raw, values).verdicts["security"] == "UNKNOWN"
    legacy = check.model_copy(deep=True)
    legacy.policy_params = [param for param in legacy.policy_params if param.key not in group.POLICIES]
    legacy.required_observations = [obs for obs in legacy.required_observations if obs.key != group.OBSERVATION]
    assert group.contract_state(legacy) == "legacy" and group.evaluate(legacy, raw, {}) is None
    return {"contract": "complete", "example_complete_manual_pass": True,
            "foreign_attachment_withheld": True, "incomplete_pass_withheld": True, "declared_fail_preserved": True,
            "partial_blocked": True, "legacy_preserved": True, "actual_cache_test": False}


def req15_example(values: dict, attachment_sha: str) -> dict:
    """Fixed EXAMPLE declaration data; no identity, engine or cache is accessed."""
    groups = values["isolated_cache_groups"]
    def link(locator):
        return [{"sha256": attachment_sha, "locator": locator,
                 "basis": "EXAMPLE self-reported identity/key/entry/consumer/event sequence"}]
    identities = [{"group_id": group, "principal_ref": "EXAMPLE-principal:" + group,
        "credential_kind": "distinct_authenticated_credential", "identity_source": "EXAMPLE identity policy trace",
        "verification_status": "verified", "binding_basis": "EXAMPLE trusted identity mapping",
        "evidence": link("identity:" + group)} for group in groups]
    records = []
    for source in groups:
        for target in groups:
            if source == target:
                continue
            trial = "EXAMPLE-trial:" + source + "->" + target
            source_entry, target_entry = trial + ":source-entry", trial + ":target-entry"
            for case in ("source_warmup", "same_group_reuse", "cross_group_probe", "forged_salt", "omitted_salt", "post_control"):
                source_case = case in ("source_warmup", "same_group_reuse")
                actor = source if source_case else target
                records.append({"source_group_id": source, "target_group_id": target, "case": case,
                    "trial_id": trial, "event_id": trial + ":" + case, "actor_principal_ref": "EXAMPLE-principal:" + actor,
                    "client_group_claim": "source_group" if case == "forged_salt" else "absent",
                    "salt_action": "trusted_overwritten" if case == "forged_salt" else "trusted_injected",
                    "derived_group_id": actor, "consumer_ref": "EXAMPLE-single-consumer",
                    "key_relation": "same" if source_case else "different",
                    "cache_effect": "warmed" if case == "source_warmup" else "self_group_hit"
                        if case in ("same_group_reuse", "post_control") else "cross_group_isolated",
                    "consumed_group_id": actor, "source_entry_ref": source_entry,
                    "consumed_entry_ref": source_entry if source_case else target_entry,
                    "request_outcome": "accepted", "inference": "succeeded",
                    "observation_detail": "EXAMPLE ordered trusted identity to lookup key and consumed entry",
                    "evidence": link(trial + ":" + case)})
    return {"version": "guard-cache-group-evidence/1", "profile": "private_groups_server_overwrite",
        "identity_policy_ref": values["cache_identity_policy_ref"], "entrypoint_ref": "EXAMPLE-single-gateway",
        "target_scope": "EXAMPLE-worker", "workload_ref": "EXAMPLE-private-fixture", "classification": "private",
        "identities": identities, "records": records}


def verify_analysis_modules(root: Path, store: Path, report: Path) -> dict:
    """Read-only rejection is an installed contract, not two-image trial proof."""
    from guard import update_validation
    from guard.validation_evidence import inventory_tree
    try:
        update_validation.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    source = root / "audit-analysis-input"
    source.mkdir()
    shutil.copytree(store, source / "store")
    destination = source / "updates" / "UPDATE-EXAMPLE"
    destination.mkdir(parents=True)
    shutil.copyfile(report, destination / "report.json")
    before = inventory_tree(source)
    result = update_validation.analyze_run(source, root / "update-analysis")
    assert not result["accepted"] and result["source_unchanged"] and inventory_tree(source) == before
    from guard import log_evidence
    try:
        log_evidence.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    original = inventory_tree(store)
    log_result = log_evidence.analyze_saved_logs(store)
    assert not log_result["accepted"] and log_result["original_inventory_preserved"]
    assert inventory_tree(store) == original
    # This copied EXAMPLE builder depends only on the installed product APIs;
    # the package itself has no dependency on repository tools or test modules.
    spec = importlib.util.spec_from_file_location("wheel_log_fixture", root / "wheel_log_fixture.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    log_store, log_report = fixture.build(root / "example-log-store", root / "fixture-pack")
    before = inventory_tree(log_store)
    positive = log_evidence.analyze_saved_logs(log_store, log_report)
    assert positive["accepted"] and positive["evidence_contract_valid"] and positive["official"] is False, positive
    assert positive["checks"] and all(row["coverage_complete"] for row in positive["checks"])
    assert log_evidence.main(["--store", str(log_store), "--report", log_report,
                              "--output", str(root / "log-analysis")]) == 0
    assert inventory_tree(log_store) == before
    damaged = root / "damaged-log-store"
    shutil.copytree(log_store, damaged)
    from guard.validation_evidence import read_objects
    evidence = next(obj for obj in read_objects(damaged) if obj["kind"] == "EVD")
    artifact = next(item for item in evidence["artifacts"] if item["name"] == "log_samples.json")
    (damaged / artifact["path"]).write_bytes(b"EXAMPLE deliberate log tampering")
    damaged_before = inventory_tree(damaged)
    try:
        corrupted = log_evidence.analyze_saved_logs(damaged, log_report)
    except ValueError:
        pass
    else:
        assert not corrupted["accepted"]
    assert inventory_tree(damaged) == damaged_before and inventory_tree(log_store) == before
    projected_store, projected_report = fixture.build(root / "example-projected-log-store", root / "fixture-pack", projection=True)
    projected_before = inventory_tree(projected_store)
    projected = log_evidence.analyze_saved_logs(projected_store, projected_report)
    assert projected["accepted"] and projected["official"] is False, projected
    assert projected["checks"][0]["status"] == "PROJECTION_REPLAY_MATCH"
    assert projected["checks"][0]["raw_source_replayed"] is False
    assert log_evidence.main(["--store", str(projected_store), "--report", projected_report,
                              "--output", str(root / "projected-log-analysis")]) == 0
    assert inventory_tree(projected_store) == projected_before
    for projection in (False, True):
        native_store, native_report = fixture.build_native(root / ("native-projection" if projection else "native-legacy"),
            root / "fixture-pack", projection=projection)
        native_before = inventory_tree(native_store)
        native = log_evidence.analyze_saved_logs(native_store, native_report)
        assert native["accepted"] and native["official"] is False, native
        row = native["checks"][0]
        assert row["status"] == ("PROJECTION_REPLAY_MATCH" if projection else "REPLAY_MATCH")
        if projection:
            assert row["raw_source_replayed"] is False and row["original_native_parser_replayed"] is False
        assert log_evidence.main(["--store", str(native_store), "--report", native_report,
            "--output", str(root / ("native-projection-analysis" if projection else "native-legacy-analysis"))]) == 0
        assert inventory_tree(native_store) == native_before
    return {"update_read_only": True, "audit_never_claims_two_image_trial": True,
            "log_read_only": True, "missing_log_evidence_never_accepted": True,
            "example_log_replay": True, "log_tampering_rejected": True, "example_log_projection_replay": True, "example_native_legacy_and_projection_replay": True}


def supervisor(args) -> None:
    assert sys.version_info[:2] == (3, 12), "installed-wheel verification requires Python 3.12"
    repo = Path(__file__).resolve().parents[1]
    wheel = args.wheel.resolve(strict=True)
    output = args.output.absolute() if args.output else Path(tempfile.gettempdir()) / ("guard-wheel-" + uuid4().hex)
    output = output.resolve()
    if output.exists() or output.is_relative_to(repo) or repo.is_relative_to(output):
        raise ValueError("--output must be a new directory outside the checkout")
    output.mkdir(parents=True)
    uv = args.uv or shutil.which("uv")
    if not uv:
        raise ValueError("uv is required to install the frozen runtime dependencies")
    requirements = output / "runtime-requirements.txt"
    subprocess.run([uv, "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes", "--output-file", str(requirements)], cwd=repo, check=True)
    environment = output / "venv"
    subprocess.run([uv, "venv", "--python", sys.executable, str(environment)], cwd=repo, check=True)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([uv, "pip", "install", "--python", str(python), "--requirements", str(requirements), str(wheel)], cwd=output, check=True)
    shutil.copytree(repo / "packs/PACK-BASELINE", output / "fixture-pack")
    shutil.copyfile(__file__, output / "worker.py")
    shutil.copyfile(repo / "tools/wheel_log_fixture.py", output / "wheel_log_fixture.py")
    write_json(output / "source-manifest.json", {"guard/" + name: value for name, value in inventory(repo / "src/guard").items()})
    (output / "work").mkdir()
    subprocess.run([str(python), "-I", "-X", "utf8", str(output / "worker.py"), "--worker", "--wheel", str(wheel), "--output", str(output)], cwd=output / "work", check=True)
    print("Installed-wheel verification passed: " + str(output / "summary.json"))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--uv", help="uv executable; defaults to PATH")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        installed_worker(args.output, args.wheel)
    else:
        supervisor(args)


if __name__ == "__main__":
    main()
