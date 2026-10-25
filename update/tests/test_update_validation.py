"""Harness contracts use synthetic CLI replies, never a live Docker daemon."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest
import yaml

from guard import update_validation as harness
from guard.models import DataOrigin
from tests.builders import PACK_DIR, baseline_pack, environment, policy


def declared_inputs():
    pack = baseline_pack()
    runtime = {"gpu_count": 0, "cpu": {"architecture": "amd64", "model": "EXAMPLE-CPU",
        "system": "Linux", "kernel": "EXAMPLE-kernel", "threads": 2}}
    model = {"id": "EXAMPLE-model", "revision": "EXAMPLE-revision", "inventory_sha256": "a" * 64}
    envs = [environment(id=f"ENV-{side}", origin=DataOrigin.DECLARED,
        engine={"name": "vllm", "version": f"0.{i}.0", "image_digest": f"example/fixture@sha256:{str(i) * 64}"},
        runtime=runtime, model=model,
        facts={"gateway": {"value": True, "origin": "DECLARED", "source": "synthetic test"}})
        for side, i in (("A", 1), ("B", 2))]
    pol = policy(pack.ref(), scope={"include": list(harness.CHECKS)})
    cfg = {"update": {"mode": "managed", "engine_profile": "hardened"},
           "adapter": {"name": "vllm"}, "executors": {name: "real" for name in
               ("direct_route", "role_route", "container_config", "credential_exposure", "input_validation")},
           "vllm": {"launch_profile": "cpu-bounded", "model_layout": "snapshot", "cpu_threads": 2}}
    return pack, envs, pol, cfg


def readiness(status="SUPPORTED", *, ready=True):
    return {"ready": ready, "checks": [], "check_support": [
        {"side": side, "check_id": check, "status": status} for side in ("baseline", "candidate") for check in harness.CHECKS]}


def input_files(tmp_path):
    folder = tmp_path / "inputs"
    folder.mkdir()
    pack, envs, pol, _ = declared_inputs()
    result = {"pack": PACK_DIR}
    for name, env in zip(("baseline", "candidate"), envs):
        path = folder / f"{name}.yaml"
        raw = {key: env.to_dict()[key] for key in ("data_origin", "engine", "runtime", "model", "facts")}
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        result[name] = path
    result["policy"] = folder / "policy.yaml"
    result["policy"].write_text(yaml.safe_dump({"pack": {"id": pack.ref().id, "revision": pack.ref().revision},
        "data_origin": "EXAMPLE", "status": "DRAFT", "scope": pol.scope.model_dump(), "values": pol.values}), encoding="utf-8")
    result["config"] = folder / "config.toml"
    result["config"].write_text("[update]\nmode='managed'\nengine_profile='hardened'\n[vllm]\nlaunch_profile='cpu-bounded'\n", encoding="utf-8")
    return result


def argv(inputs, output, *extra):
    return [part for name, path in inputs.items() for part in (f"--{name}", str(path))] + ["--output", str(output), *extra]


def test_declared_inputs_accept_distinct_images_and_classify_same_version():
    pack, envs, pol, cfg = declared_inputs()
    assert harness.validate_inputs(pack, envs, pol, cfg)["comparison_kind"] == "version-upgrade"
    envs[1] = envs[1].model_copy(update={"engine": envs[1].engine.model_copy(update={"version": envs[0].engine.version})}).seal()
    assert harness.validate_inputs(pack, envs, pol, cfg)["comparison_kind"] == "image-comparison"


def test_same_digest_aliases_do_not_establish_two_images():
    pack, envs, pol, cfg = declared_inputs()
    envs[1] = envs[1].model_copy(update={"engine": envs[1].engine.model_copy(
        update={"image_digest": "alias/other@" + envs[0].engine.image_digest.split("@")[1]})}).seal()
    with pytest.raises(ValueError, match="distinct image digests"):
        harness.validate_inputs(pack, envs, pol, cfg)


@pytest.mark.parametrize("kind", ["example", "missing-cpu", "wrong-model", "runtime", "profile", "scope", "external", "legacy"])
def test_declared_input_contract_rejects_incompatible_pairs(kind):
    pack, envs, pol, cfg = declared_inputs()
    expected = {"example": "EXAMPLE", "missing-cpu": "CPU profile", "wrong-model": "model identity",
                "runtime": "CPU/runtime", "profile": "execution conditions", "scope": "exactly", "external": "managed",
                "legacy": "cpu-bounded"}[kind]
    if kind == "example":
        envs[0] = envs[0].model_copy(update={"data_origin": DataOrigin.EXAMPLE}).seal()
    elif kind == "missing-cpu":
        envs[0] = envs[0].model_copy(update={"runtime": envs[0].runtime.model_copy(update={"cpu": None})}).seal()
    elif kind == "wrong-model":
        envs[1] = envs[1].model_copy(update={"model": envs[1].model.model_copy(update={"inventory_sha256": "b" * 64})}).seal()
    elif kind == "runtime":
        envs[1] = envs[1].model_copy(update={"runtime": envs[1].runtime.model_copy(update={"platform": "different"})}).seal()
    elif kind == "profile":
        cfg["sides"] = {"candidate": {"update": {"engine_profile": "overprivileged"}}}
    elif kind == "scope":
        pol = policy(pack.ref(), scope={"include": ["REQ-01"]})
    elif kind == "external":
        cfg["update"]["mode"] = "external"
    else:
        cfg["vllm"]["launch_profile"] = "legacy"
    with pytest.raises(ValueError, match=expected):
        harness.validate_inputs(pack, envs, pol, cfg)


def test_condition_normalization_does_not_mutate_input():
    _, _, _, cfg = declared_inputs()
    cfg["sides"] = {"candidate": {"vllm": {"image": "other", "expected_version": "other"}}}
    before = deepcopy(cfg)
    assert harness._conditions(cfg, "baseline") == harness._conditions(cfg, "candidate")
    assert cfg == before


@pytest.mark.parametrize("status", ["UNKNOWN", "UNSUPPORTED", None])
def test_host_ready_does_not_override_per_check_support(status):
    data = readiness()
    data["check_support"][3]["status"] = status
    assert not harness._support_ready(data)


def test_missing_side_support_and_host_failure_are_not_ready():
    data = readiness()
    data["check_support"].pop()
    assert not harness._support_ready(data)
    assert not harness._support_ready(readiness(ready=False))


def test_prepare_creates_only_new_summary_not_store_or_subprocess(tmp_path, monkeypatch):
    inputs = input_files(tmp_path)
    before = {str(path): path.read_bytes() for name, path in inputs.items() if name != "pack"}
    monkeypatch.setattr(harness, "update_readiness", lambda *a: readiness())
    monkeypatch.setattr(harness, "_command", lambda *a: pytest.fail("preparation must not invoke subprocess"))
    output = tmp_path / "prepared"
    assert harness.main(argv(inputs, output)) == 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "preparation" and summary["ready"] and not summary["accepted"]
    assert summary["source_inputs_unchanged"]
    assert not (output / "store").exists()
    assert before == {str(path): path.read_bytes() for name, path in inputs.items() if name != "pack"}


def test_prepare_insufficient_support_prevents_optin_execution(tmp_path, monkeypatch):
    inputs = input_files(tmp_path)
    monkeypatch.setattr(harness, "update_readiness", lambda *a: readiness("UNSUPPORTED"))
    monkeypatch.setattr(harness, "_command", lambda *a: pytest.fail("unsupported scope must not start update"))
    assert harness.main(argv(inputs, tmp_path / "not-ready", "--execute")) == 2


def test_output_and_source_overlap_existing_files_are_preserved(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    keep = source / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="new directory"):
        harness.reserve_output(source, protected=[])
    with pytest.raises(ValueError, match="outside source"):
        harness.reserve_output(source / "inside", protected=[source])
    assert keep.read_text() == "keep"


def test_output_linked_parent_is_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("symlink permission unavailable")
    with pytest.raises(ValueError, match="symlinks"):
        harness.reserve_output(link / "out", protected=[])


def test_help_and_analysis_reject_execution_inputs_without_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "_command", lambda *a: pytest.fail("no command expected"))
    monkeypatch.setattr(harness, "update_readiness", lambda *a: pytest.fail("no readiness expected"))
    with pytest.raises(SystemExit) as stop:
        harness.main(["--help"])
    assert stop.value.code == 0
    assert harness.main(["--analyze-existing", str(tmp_path), "--pack", str(PACK_DIR), "--output", str(tmp_path / "out")]) == 1


def fake_execution(tmp_path, monkeypatch, *, update_code=0, timeout=False, replay_accepted=True):
    output = tmp_path / "run"
    output.mkdir()
    inputs = {key: tmp_path / key for key in ("pack", "baseline", "candidate", "policy", "config")}
    calls = []
    report = {"inputs_manifest": [{"kind": "PLAN", "id": "PLAN-EXAMPLE", "revision": 1}]}
    def command(args, label, seconds):
        calls.append((label, args))
        if label == "update" and not timeout:
            folder = output / "updates" / "UPDATE-EXAMPLE"
            folder.mkdir(parents=True)
            (folder / "report.json").write_text(json.dumps(report), encoding="utf-8")
            (output / "store").mkdir()
            (output / "store" / "marker").write_text("EXAMPLE", encoding="utf-8")
        if label == "export":
            (output / "evidence.zip").write_bytes(b"EXAMPLE")
        if label in {"restore", "restore-replay"}:
            (output / ("restored" if label == "restore" else "replay-store")).mkdir()
        if label == "report-restored":
            (output / "replay-report.json").write_text(json.dumps(report), encoding="utf-8")
        return {"command": label, "returncode": update_code if label == "update" else 0,
                "timed_out": label == "update" and timeout}, json.dumps(readiness()) if label == "prepare-update" else ""
    monkeypatch.setattr(harness, "_command", command)
    monkeypatch.setattr(harness, "_assess_store", lambda store, path, **kw: {
        "accepted": replay_accepted if store.name == "replay-store" else True})
    monkeypatch.setattr(harness, "_target_proof", lambda *a: {"synthetic": True})
    monkeypatch.setattr(harness, "_report_pair", lambda *a: report)
    monkeypatch.setattr(harness, "_preserved_graph", lambda *a: {"synthetic": True})
    monkeypatch.setattr(harness, "read_objects", lambda *a: [])
    monkeypatch.setattr(harness, "verify_store_files", lambda *a: SimpleNamespace(path=output / "restored-pack"))
    monkeypatch.setattr(harness, "verify_saved_bundle", lambda *a: None)
    return output, inputs, calls


def test_execute_delegates_product_sequence_and_preserves_fresh_restore(tmp_path, monkeypatch):
    output, inputs, calls = fake_execution(tmp_path, monkeypatch)
    summary = harness.execute_run(inputs, output, readiness(), {"comparison_kind": "image-comparison"}, timeout=30)
    assert summary["accepted"] and summary["original_store_unchanged"]
    assert [label for label, _ in calls] == ["prepare-update", "update", "verify-original", "export", "restore",
                                               "restore-replay", "judge-restored", "report-restored"]
    updated = dict(calls)["update"]
    judged = dict(calls)["judge-restored"]
    assert updated[updated.index("--as-of") + 1] == judged[judged.index("--as-of") + 1]
    assert str(output / "replay-store") in judged and str(output / "restored") not in judged
    assert "guard.cli" in updated


@pytest.mark.parametrize("update_code,replay_accepted", [(1, True), (0, False)])
def test_cli_success_or_preserved_fail_cannot_bypass_acceptance(tmp_path, monkeypatch, update_code, replay_accepted):
    output, inputs, calls = fake_execution(tmp_path, monkeypatch, update_code=update_code, replay_accepted=replay_accepted)
    summary = harness.execute_run(inputs, output, readiness(), {}, timeout=30)
    assert not summary["accepted"]
    assert "export" in [label for label, _ in calls]
    assert (output / "updates" / "UPDATE-EXAMPLE" / "report.json").is_file()


def test_update_timeout_stops_further_commands_without_cleanup(tmp_path, monkeypatch):
    output, inputs, calls = fake_execution(tmp_path, monkeypatch, timeout=True)
    summary = harness.execute_run(inputs, output, readiness(), {}, timeout=1)
    assert not summary["accepted"]
    assert [label for label, _ in calls] == ["prepare-update", "update"]
    assert "inspect owned resources" in summary["problems"][0]


def test_subprocess_error_output_is_not_persisted_in_command_metadata(monkeypatch):
    secret = "EXAMPLE-SECRET-DO-NOT-LOG"
    monkeypatch.setattr(harness.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        stdout=b"public", stderr=secret.encode(), returncode=1))
    record, text = harness._command(["EXAMPLE"], "example", 1)
    assert text == "public" and record["returncode"] == 1
    assert secret not in json.dumps(record)
    def expired(*a, **kw):
        raise subprocess.TimeoutExpired("EXAMPLE", 1, stderr=secret.encode())
    monkeypatch.setattr(harness.subprocess, "run", expired)
    record, text = harness._command(["EXAMPLE"], "example", 1)
    assert record["timed_out"] and secret not in json.dumps(record) and not text


def existing_run(tmp_path):
    source = tmp_path / "source"
    folder = source / "updates" / "UPDATE-EXAMPLE"
    folder.mkdir(parents=True)
    (folder / "report.json").write_text("{}", encoding="utf-8")
    for name in ("store", "restored", "replay-store"):
        (source / name).mkdir()
    (source / "evidence.zip").write_bytes(b"EXAMPLE")
    (source / "replay-report.json").write_text("{}", encoding="utf-8")
    return source


def test_existing_run_analysis_is_read_only_and_does_not_launch(tmp_path, monkeypatch):
    source = existing_run(tmp_path)
    before = harness.inventory_tree(source)
    monkeypatch.setattr(harness, "_assess_store", lambda *a, **kw: {"accepted": True})
    monkeypatch.setattr(harness, "_target_proof", lambda *a: {"synthetic": True})
    monkeypatch.setattr(harness, "verify_saved_bundle", lambda *a: None)
    monkeypatch.setattr(harness, "_preserved_graph", lambda *a: {"synthetic": True})
    monkeypatch.setattr(harness, "_command", lambda *a: pytest.fail("analysis must not invoke subprocess"))
    summary = harness.analyze_run(source, tmp_path / "analysis")
    assert summary["accepted"] and summary["source_unchanged"]
    assert harness.inventory_tree(source) == before


def test_analysis_detects_original_source_changed_during_assessment(tmp_path, monkeypatch):
    source = existing_run(tmp_path)
    def assessment(*a, **kw):
        (source / "changed").write_text("synthetic concurrent write", encoding="utf-8")
        return {"accepted": True}
    monkeypatch.setattr(harness, "_assess_store", assessment)
    monkeypatch.setattr(harness, "_target_proof", lambda *a: {})
    monkeypatch.setattr(harness, "verify_saved_bundle", lambda *a: None)
    monkeypatch.setattr(harness, "_preserved_graph", lambda *a: {})
    summary = harness.analyze_run(source, tmp_path / "analysis")
    assert not summary["accepted"] and not summary["source_unchanged"]


@pytest.mark.parametrize("missing", ["evidence.zip", "replay-report.json"])
def test_analysis_requires_both_export_restore_and_saved_pack_replay(tmp_path, monkeypatch, missing):
    source = existing_run(tmp_path)
    (source / missing).unlink()
    monkeypatch.setattr(harness, "_assess_store", lambda *a, **kw: {"accepted": True})
    monkeypatch.setattr(harness, "_target_proof", lambda *a: {})
    monkeypatch.setattr(harness, "verify_saved_bundle", lambda *a: None)
    monkeypatch.setattr(harness, "_preserved_graph", lambda *a: {})
    summary = harness.analyze_run(source, tmp_path / "analysis")
    assert not summary["accepted"] and summary["problems"]


def test_target_proof_rejects_source_identity_mismatch(tmp_path, monkeypatch):
    source = tmp_path
    folder = source / "updates" / "UPDATE-EXAMPLE"
    folder.mkdir(parents=True)
    env = {"kind": "ENV", "id": "ENV-A", "revision": 1, "content_sha256": "e" * 64,
           "engine": {"name": "vllm", "version": "1.0", "image_digest": "example/image@sha256:" + "1" * 64}}
    env_ref = {"kind": "ENV", "id": env["id"], "revision": 1, "sha256": env["content_sha256"]}
    plan = {"kind": "PLAN", "id": "PLAN-A", "revision": 1, "content_sha256": "p" * 64,
            "inputs": {"environments": [env_ref, env_ref]}}
    report = {"inputs_manifest": [{"kind": "PLAN", "id": "PLAN-A", "revision": 1, "sha256": "p" * 64}]}
    report_path = folder / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(harness, "read_objects", lambda *a: [env, plan])
    target = {"lab": False, "engine_kind": "vllm", "observed_version": "DIFFERENT",
              "engine": {"image_digest": env["engine"]["image_digest"]},
              "images": {env["engine"]["image_digest"]: env["engine"]["image_digest"]}}
    (folder / "baseline").mkdir()
    (folder / "baseline" / "target.json").write_text(json.dumps(target), encoding="utf-8")
    with pytest.raises(ValueError, match="target identity differs"):
        harness._target_proof(source, report_path)


def test_report_pair_reads_json_only(tmp_path):
    # HTML 보고서는 없앴다(화면은 byul platform). JSON 보고서만 읽고, 없으면 실패한다.
    path = tmp_path / "report.json"
    with pytest.raises(FileNotFoundError):
        harness._report_pair(path)
    path.write_text('{"kind": "RPT"}', encoding="utf-8")
    assert harness._report_pair(path) == {"kind": "RPT"}


def graph_fixture(monkeypatch, source, *, restored=None, replay=None, replay_rows=None):
    original = [{"kind": "EVD", "id": "EVD-EXAMPLE", "revision": 1, "body": "original"}]
    values = {"store": original, "restored": original if restored is None else restored,
              "replay-store": original if replay is None else replay}
    monkeypatch.setattr(harness, "read_objects", lambda folder: deepcopy(values[folder.name]))
    report = {"inputs_manifest": [{"kind": "PLAN", "id": "PLAN-EXAMPLE", "revision": 1, "sha256": "p" * 64}],
              "by_check": [{"check_id": "REQ-01", "selected": True, "baseline": {"security": "FAIL"},
                            "candidate": {"security": "FAIL"}, "comparison": "STILL_FAILING"}]}
    def pair(path):
        data = deepcopy(report)
        if path.name == "replay-report.json" and replay_rows is not None:
            data["by_check"] = replay_rows
        return data
    monkeypatch.setattr(harness, "_report_pair", pair)


def test_unrelated_valid_bundle_cannot_replace_original_graph(tmp_path, monkeypatch):
    source = existing_run(tmp_path)
    graph_fixture(monkeypatch, source, restored=[{"kind": "EVD", "id": "OTHER", "revision": 1}])
    with pytest.raises(ValueError, match="do not match the original"):
        harness._preserved_graph(source)


@pytest.mark.parametrize("replay,reason", [
    ([], "removed or changed"),
    ([{"kind": "EVD", "id": "EVD-EXAMPLE", "revision": 1, "body": "changed"}], "removed or changed"),
    ([{"kind": "EVD", "id": "EVD-EXAMPLE", "revision": 1, "body": "original"},
      {"kind": "EVD", "id": "EVD-NEW", "revision": 1}], "added execution inputs")])
def test_replay_must_preserve_original_evidence_instead_of_reexecuting(tmp_path, monkeypatch, replay, reason):
    source = existing_run(tmp_path)
    graph_fixture(monkeypatch, source, replay=replay)
    with pytest.raises(ValueError, match=reason):
        harness._preserved_graph(source)


def test_replay_preserves_confirmed_fail_and_same_comparison(tmp_path, monkeypatch):
    source = existing_run(tmp_path)
    graph_fixture(monkeypatch, source)
    assert harness._preserved_graph(source)["same_verdicts_and_comparisons"]
    graph_fixture(monkeypatch, source, replay_rows=[{"check_id": "REQ-01", "selected": True,
        "baseline": {"security": "PASS"}, "candidate": {"security": "PASS"}, "comparison": "NO_CHANGE"}])
    with pytest.raises(ValueError, match="changed verdicts or comparisons"):
        harness._preserved_graph(source)


def test_full_synthetic_cli_preservation_replay_and_read_only_analysis(tmp_path, monkeypatch):
    """Real pack/store/CLI roundtrip; only engine execution is a labelled fixture."""
    from typer.testing import CliRunner
    from guard.cli import app
    from tests.update_builders import build_update_case
    output = tmp_path / "synthetic-run"
    output.mkdir()
    inputs = {name: tmp_path / name for name in ("pack", "baseline", "candidate", "policy", "config")}
    def command(args, label, seconds):
        if label == "prepare-update":
            return {"command": label, "returncode": 0, "timed_out": False}, json.dumps(readiness())
        if label == "update":
            report, objects, _ = build_update_case(output, candidate_fail=True)
            folder = output / "updates" / "UPDATE-SYNTHETIC-NO-ENGINE"
            folder.mkdir(parents=True)
            (folder / "report.json").write_text(json.dumps(report), encoding="utf-8")
            envs = [obj for obj in objects if obj["kind"] == "ENV"]
            for side, env in zip(("baseline", "candidate"), envs):
                (folder / side).mkdir()
                image = env["engine"]["image_digest"]
                target = {"synthetic_test_only": True, "lab": False, "engine_kind": "vllm",
                          "observed_version": env["engine"]["version"], "engine": {"image_digest": image}, "images": {image: image}}
                (folder / side / "target.json").write_text(json.dumps(target), encoding="utf-8")
            return {"command": label, "returncode": 0, "timed_out": False}, "SYNTHETIC fixture; no engine executed"
        result = CliRunner().invoke(app, args[3:])
        return {"command": label, "returncode": result.exit_code, "timed_out": False}, result.output
    monkeypatch.setattr(harness, "_command", command)
    summary = harness.execute_run(inputs, output, readiness(), {}, timeout=30)
    assert summary["accepted"], summary
    assert summary["preserved_graph"]["all_original_objects_preserved"]
    assert summary["acceptance"]["checks"]["REQ-01"]["comparison"] == "REGRESSED"
    before = harness.inventory_tree(output)
    monkeypatch.setattr(harness, "_command", lambda *a: pytest.fail("analysis cannot execute commands"))
    analyzed = harness.analyze_run(output, tmp_path / "synthetic-analysis")
    assert analyzed["accepted"], analyzed
    assert analyzed["source_unchanged"] and harness.inventory_tree(output) == before
