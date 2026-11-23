"""Saved replay contracts with EXAMPLE artifacts, never serving engines."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.log_evidence import _artifact, analyze_saved_logs, main
from guard.models import DataOrigin, PolicyStatus, ReviewStatus
from guard.store import Store
from guard.validation_evidence import inventory_tree
from tests.builders import PACK_DIR, baseline_pack
from tests.prepared_fixtures import manual_setup_seed
from tests.test_passive_api_hop import paired_original, passive_update


_spec = importlib.util.spec_from_file_location("wheel_log_fixture", Path(__file__).resolve().parents[1] / "tools/wheel_log_fixture.py")
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


def seed(tmp_path):
    def create(folder):
        source, report = _fixture.build(folder / "store", PACK_DIR)
        store = Store(source)
        plan, = list(store.iter_kind("PLAN"))
        return store, plan, report

    parameters = {"pack": baseline_pack().model_dump(mode="json"),
                  "fixture_source": hashlib.sha256(Path(_fixture.__file__).read_bytes()).hexdigest()}
    store, _, report = manual_setup_seed("saved-log-replay", parameters, tmp_path, create)
    return store.root, report


def revised_sample(store_path, change, *, replace_observations=False, observation_change=None):
    """Append new EVD/RES/RPT; original objects and artifacts remain untouched."""
    store = Store(store_path)
    evd, = list(store.iter_kind("EVD"))
    artifact = next(item for item in evd.artifacts if item.name == "log_samples.json")
    envelope = json.loads((store_path / artifact.path).read_bytes())
    change(envelope["capture"])
    content = json.dumps(envelope, ensure_ascii=False).encode()
    saved = store.put_artifact(content)
    updated = artifact.model_copy(update={"path": saved.path, "sha256": saved.sha256, "bytes": saved.bytes})
    fields = {"revision": 2, "artifacts": [updated if item == artifact else item for item in evd.artifacts]}
    if replace_observations:
        from guard.models import Observation
        fields["observations"] = {**evd.observations,
            **{key: Observation(value=value, collected=True) for key, value in envelope["capture"]["analysis"].items()}}
    if observation_change is not None:
        fields.setdefault("observations", deepcopy(evd.observations))
        observation_change(fields["observations"])
    newer = evd.model_copy(update=fields).seal()
    store.put(newer)
    plan = store.resolve(evd.plan)
    pack = baseline_pack()
    as_of = datetime(2026, 10, 24, tzinfo=timezone.utc)
    judge_plan(store, pack, plan, as_of)
    report = build_report(store, pack, plan, as_of)
    store.put(report)
    return f"{report.id}@{report.revision}"


def test_positive_exact_pinned_log_replay_is_read_only_and_example_not_official(tmp_path):
    source, report = seed(tmp_path)
    before = inventory_tree(source)
    result = analyze_saved_logs(source, report)
    assert result["accepted"] and result["evidence_contract_valid"]
    assert result["checks"][0]["status"] == "REPLAY_MATCH"
    assert result["checks"][0]["coverage_complete"]
    assert result["checks"][0]["data_origin"] == "EXAMPLE"
    assert result["official"] is False and result["policy_status"] == "DRAFT"
    assert result["original_inventory_preserved"] and before == inventory_tree(source)


@pytest.mark.parametrize("replacement", [b'{"sample":2}', b'{"sample":10}'], ids=["same-size", "longer"])
def test_replay_consumes_only_the_named_artifacts_pinned_bytes(tmp_path, replacement):
    store = Store(tmp_path / "store")
    raw = b'{"sample":1}'
    saved = store.put_artifact(raw)
    item = SimpleNamespace(name="log_integrity_events.json", path=saved.path, sha256=saved.sha256, bytes=saved.bytes)
    evidence = SimpleNamespace(artifacts=[item])
    assert _artifact(store.root, evidence, item.name) == raw

    (store.root / saved.path).write_bytes(replacement)
    with pytest.raises(ValueError, match="^log replay artifact bytes differ from pinned evidence$"):
        _artifact(store.root, evidence, item.name)


def test_ordinary_log_metadata_is_readable_in_a_paired_report_with_passive_hops(tmp_path, monkeypatch, passive_update,
                                                                          paired_original):
    from guard import application
    from guard.executors import registry
    from guard.executors.kubernetes_log_analysis import KubernetesLogAnalysisExecutor
    from guard.models import Side
    from tests.builders import T0
    from tests.test_kubernetes_passive_log_analysis import LogWorld, PRIVATE, REQUEST_ID, STDERR, VALUES

    store, pack, envs, policy, config, _ = passive_update
    policy = policy.model_copy(update={
        "scope": policy.scope.model_copy(update={"include": [*policy.scope.include, "OBS-03", "OBS-09"]}),
        "values": {**policy.values, "OBS-03": VALUES}}).seal()
    config["update"]["allowed_checks"].append("OBS-03")
    config["executors"]["log_analysis"] = "kubernetes"
    worlds = {}
    for side, (_, _, _, prior_world) in paired_original.items():
        target_path = config["sides"][side.value]["container_config"]["target"]
        config["sides"][side.value]["log_analysis"] = {"target": target_path}
        worlds[target_path] = LogWorld(prior_world.target, "plain")

    def ordinary_logs(cfg):
        options = cfg["log_analysis"]
        executor = KubernetesLogAnalysisExecutor(options, expected_target_sha256=cfg.get("_kubernetes_target_sha256"))
        executor._kubectl_runner = worlds[options["target"]].runner
        return executor

    monkeypatch.setitem(registry.EXECUTORS, "log_analysis:kubernetes", ordinary_logs)
    report, _ = application.execute_update(store, pack, envs, policy, config, tmp_path / "update", T0)
    original = f"{report.id}@{report.revision}"
    pinned = [store.resolve(ref) for ref in report.inputs_manifest if ref.kind == "EVD"]
    logs = {evd.side.value: evd for evd in pinned if evd.check_id == "OBS-03"}
    hops = {evd.side: evd for evd in pinned if evd.check_id == "PLT-08"}
    assert all(len(evd.refs) == 3 for evd in hops.values()) and set(hops) == {Side.baseline, Side.candidate}
    assert all(len(world.packets) == world.log_reads == 1 for world in worlds.values())

    before = inventory_tree(store.root)
    audit = analyze_saved_logs(store.root, original)
    assert audit["original_inventory_preserved"] and inventory_tree(store.root) == before
    assert audit["report"] == report.ref().model_dump(mode="json") and not audit["accepted"] and not audit["official"]
    rows = {(row["check_id"], row["side"]): row for row in audit["checks"]}
    for side in ("baseline", "candidate"):
        row = rows["OBS-03", side]
        assert row["status"] == "NORMAL_METADATA_VALIDATED" and row["data_origin"] == "EXAMPLE"
        assert not row["replayed"] and not row["raw_source_replayed"] and not row["coverage_complete"]
        assert "verdicts" not in row and row["normal_capture"]["artifact_sha256"] == logs[side].artifacts[0].sha256
        assert row["normal_capture"]["source"]["payload_retained"] is False
        assert row["normal_capture"]["collection_status"]["engine_stdout"]["stage"] == "bounded_capture_complete"
        assert rows["OBS-09", side]["replayed"] is False
    assert all(marker not in json.dumps(audit) for marker in (PRIVATE, REQUEST_ID, STDERR))

    plan = store.resolve(hops[Side.baseline].plan)

    def publish_revised(evd, **updates):
        store.put(evd.model_copy(update={"revision": evd.revision + 1, **updates}).seal())
        judge_plan(store, pack, plan, T0)
        revised = build_report(store, pack, plan, T0)
        store.put(revised)
        return f"{revised.id}@{revised.revision}"

    evd = logs["baseline"]
    original_item, = evd.artifacts
    summary = json.loads((store.root / original_item.path).read_bytes())
    summary["fixture"]["policy_sha256"] = "0" * 64
    replacement = store.put_artifact(json.dumps(summary).encode() + b"\n")
    changed = original_item.model_copy(update={"path": replacement.path, "sha256": replacement.sha256, "bytes": replacement.bytes})
    bad_summary = publish_revised(evd, artifacts=[changed])
    assert "OBS-03/baseline: saved ordinary log summary differs from its pinned fixture" in analyze_saved_logs(
        store.root, bad_summary)["problems"]

    hop = hops[Side.baseline]
    wrong_attempt = "ATTEMPT-EXAMPLE-OTHER"
    execution = hop.execution.model_copy(update={"attempt_id": wrong_attempt,
        "run_binding": {**hop.execution.run_binding, "attempt_id": wrong_attempt}})
    bad_link = publish_revised(hop, execution=execution)
    with pytest.raises(ValueError, match="PASSIVE_API_HOP_SOURCE_SCOPE_UNVERIFIED"):
        analyze_saved_logs(store.root, bad_link)
    assert analyze_saved_logs(store.root, original)["checks"] == audit["checks"]


def test_cli_new_output_and_no_input_overwrite(tmp_path, capsys):
    source, report = seed(tmp_path)
    before = inventory_tree(source)
    output = tmp_path / "audit"
    assert main(["--store", str(source), "--report", report, "--output", str(output)]) == 0
    assert json.loads((output / "summary.json").read_bytes())["accepted"]
    assert "보안 PASS" in (output / "summary.txt").read_text(encoding="utf-8")
    assert main(["--store", str(source), "--output", str(output)]) == 2
    assert main(["--store", str(source), "--output", str(source / "audit")]) == 2
    assert inventory_tree(source) == before


@pytest.mark.parametrize("operation", ["alter", "delete"])
def test_artifact_exact_bytes_tamper_or_absence_is_rejected_without_source_repair(tmp_path, operation):
    source, report = seed(tmp_path)
    evd, = list(Store(source).iter_kind("EVD"))
    path = source / next(item.path for item in evd.artifacts if item.name == "log_samples.json")
    if operation == "alter":
        path.write_bytes(path.read_bytes() + b"\r\n")
    else:
        path.unlink()
    before = inventory_tree(source)
    with pytest.raises(ValueError):
        analyze_saved_logs(source, report)
    assert inventory_tree(source) == before


@pytest.mark.parametrize("change", [
    lambda sample: sample["captures"][0].update(text='{"request_id":"EXAMPLE_TRIAL_NORMAL-extra"}'),
    lambda sample: sample["captures"][0]["source"].update(container="other-engine"),
    lambda sample: sample["captures"][0]["window"].update(until="2026-10-25T00:00:00Z"),
    lambda sample: sample["requests"][1].update(request_id="EXAMPLE-other-run"),
    lambda sample: sample["controls"].update(after="fail"),
    lambda sample: sample.update(policy_values={}),
    lambda sample: sample["analysis"].update(log_fields_seen=[]),
])
def test_resealed_mismatched_capture_never_replaces_pinned_original_result(tmp_path, change):
    source, original = seed(tmp_path)
    newer = revised_sample(source, change)
    before = inventory_tree(source)
    result = analyze_saved_logs(source, newer)
    assert not result["accepted"] and result["problems"]
    assert analyze_saved_logs(source, original)["accepted"]
    assert inventory_tree(source) == before


def test_ambiguous_reports_require_exact_selection_and_history_is_not_replaced_by_latest(tmp_path):
    source, first = seed(tmp_path)
    second = revised_sample(source, lambda sample: sample["controls"].update(after="fail"))
    with pytest.raises(ValueError, match="exact saved report"):
        analyze_saved_logs(source)
    assert analyze_saved_logs(source, first)["accepted"]
    assert not analyze_saved_logs(source, second)["accepted"]


def test_legacy_capture_is_retained_without_claiming_new_transport_guarantees(tmp_path):
    source, first = seed(tmp_path)
    newer = revised_sample(source, lambda sample: sample.pop("schema"))
    result = analyze_saved_logs(source, newer)
    assert not result["accepted"] and result["checks"][0]["status"] == "LEGACY_UNVERIFIABLE"
    assert result["evidence_contract_valid"] and result["limitations"]
    assert analyze_saved_logs(source, first)["accepted"]


def test_nonempty_journal_is_not_ignored_by_immutable_sqlite_read(tmp_path):
    source, report = seed(tmp_path)
    (source / "store.sqlite-wal").write_bytes(b"EXAMPLE pending state")
    with pytest.raises(ValueError, match="close/checkpoint"):
        analyze_saved_logs(source, report)


def test_restored_copy_replays_saved_pack_without_local_pack_lookup(tmp_path, monkeypatch):
    from guard.store.bundle import export_bundle, restore_bundle
    source, report = seed(tmp_path)
    bundle = tmp_path / "evidence.zip"
    export_bundle(source, bundle)
    restored = tmp_path / "restored"
    restore_bundle(bundle, restored)
    import guard.models
    monkeypatch.setattr(guard.models, "load_pack", lambda *args: (_ for _ in ()).throw(AssertionError("current pack forbidden")))
    assert analyze_saved_logs(restored, report)["accepted"]


@pytest.mark.parametrize("changes", [{"official": True}, {"policy_status": PolicyStatus.CONFIRMED},
    {"pack_review_status": ReviewStatus.REVIEWED}, {"data_origin": DataOrigin.MEASURED}])
def test_resealed_report_cannot_escalate_official_review_or_origin(tmp_path, changes):
    source, original = seed(tmp_path)
    store = Store(source)
    report, = list(store.iter_kind("RPT"))
    newer = report.model_copy(update={"revision": 2, **changes}).seal()
    store.put(newer)
    with pytest.raises(ValueError, match="report"):
        analyze_saved_logs(source, f"{newer.id}@2")
    assert analyze_saved_logs(source, original)["accepted"]


@pytest.mark.parametrize("changes", [{"verification_level": DataOrigin.MEASURED}, {"data_origin": DataOrigin.MEASURED}])
def test_resealed_result_cannot_escalate_verification_or_origin(tmp_path, changes):
    source, original = seed(tmp_path)
    store = Store(source)
    res, = list(store.iter_kind("RES"))
    newer = res.model_copy(update={"revision": 2, **changes}).seal()
    store.put(newer)
    plan, = list(store.iter_kind("PLAN"))
    rpt = build_report(store, baseline_pack(), plan, datetime(2026, 10, 24, tzinfo=timezone.utc))
    store.put(rpt)
    with pytest.raises(ValueError, match="result origin/verification"):
        analyze_saved_logs(source, f"{rpt.id}@1")
    assert analyze_saved_logs(source, original)["accepted"]


@pytest.mark.parametrize("representation", ["utf8-replacements", "redaction-expansion"])
def test_expanded_persisted_text_uses_artifact_budget_and_replays_incomplete_without_escalation(tmp_path, representation):
    source, original = seed(tmp_path)
    def expand(sample):
        from guard.executors.log_analysis_contract import analyze_log_captures
        capture = sample["captures"][0]
        # Raw invalid bytes can each expand to three UTF-8 bytes on decode;
        # short sensitive values can expand into longer redaction markers.
        # This is an EXAMPLE saved representation, never an engine execution.
        suffix = "\ufffd" * 400000 if representation == "utf8-replacements" else "[REDACTED]" * 110000
        capture["text"] += "\n" + suffix
        encoded = capture["text"].encode()
        assert len(encoded) > 1_000_000
        capture["persisted_decoded_sha256"] = hashlib.sha256(encoded).hexdigest()
        capture["persisted_decoded_bytes"] = len(encoded)
        capture["truncated"] = True
        capture["source_complete"] = False
        sample["analysis"] = analyze_log_captures(sample["captures"], sample["policy_values"],
            sample["markers"], sample["correlation"], initial_gaps=sample["initial_gaps"])
    newer = revised_sample(source, expand, replace_observations=True)
    result = analyze_saved_logs(source, newer)
    assert result["accepted"] and result["original_inventory_preserved"], result
    assert not result["checks"][0]["coverage_complete"]
    assert result["checks"][0]["verdicts"]["security"] == "UNKNOWN"
    assert analyze_saved_logs(source, original)["accepted"]


def test_missing_saved_input_is_a_sanitized_validation_error_and_cli_exit_two(tmp_path, capsys):
    source = tmp_path / "EXAMPLE-sensitive-path-must-not-enter-error"
    with pytest.raises(ValueError, match="saved evidence input could not be read") as error:
        analyze_saved_logs(source)
    assert str(source) not in str(error.value)
    assert main(["--store", str(source), "--output", str(tmp_path / "failed-audit")]) == 2
    assert "EXAMPLE-sensitive" not in capsys.readouterr().out


def test_source_parent_symlink_is_rejected(tmp_path):
    source, report = seed(tmp_path)
    linked = tmp_path / "link"
    try:
        linked.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("this Windows account cannot create symlinks")
    with pytest.raises(ValueError):
        analyze_saved_logs(linked, report)


def test_no_log_report_does_not_count_as_success(tmp_path, monkeypatch):
    from guard.core.run import RunConfig, run_plan
    from guard.core.select import select
    from guard.store.packs import put_pack
    from tests.builders import environment, policy
    store = Store(tmp_path / "nonlog")
    pack = baseline_pack()
    put_pack(store, PACK_DIR)
    env = environment()
    pol = policy(pack.ref(), scope={"include": ["REQ-01"]})
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    run_plan(store, pack, plan, RunConfig(executors={"direct_access": "fake"}, adapter="vllm"))
    judge_plan(store, pack, plan, env.created_at)
    report = build_report(store, pack, plan, env.created_at)
    store.put(report)
    result = analyze_saved_logs(store.root)
    assert not result["accepted"] and result["original_inventory_preserved"]


def save_native_integrity_report(tmp_path, draft, env, version):
    from guard.core.select import select
    from guard.models import Evidence
    from guard.store.packs import put_pack
    from tests.builders import policy
    from tests.test_log_integrity import VALUES

    assert draft.status.value == "COMPLETED"
    store = Store(tmp_path / "native-store")
    pack = baseline_pack()
    put_pack(store, PACK_DIR)
    pol = policy(pack.ref(), scope={"include": ["OBS-09"]}, values={"OBS-09": VALUES})
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    artifacts = []
    for item in draft.artifacts:
        saved = store.put_artifact(item.path.read_bytes())
        artifacts.append({"name": item.name, "path": saved.path, "sha256": saved.sha256, "bytes": saved.bytes,
                          "collected_at": env.created_at, "collector": "EXAMPLE-persisted-draft"})
    evd = Evidence(id="EVD-EXAMPLE-NATIVE", data_origin="EXAMPLE", refs=[plan.ref(), env.ref()],
        plan=plan.ref(), environment=env.ref(), check_id="OBS-09", side="single",
        execution={"status": "COMPLETED", "executor": {"name": "log_integrity", "version": version, "config_sha256": "0" * 64}},
        normal_control=draft.normal_control, challenges=draft.challenges, observations=draft.observations,
        artifacts=artifacts).seal()
    store.put(evd)
    judge_plan(store, pack, plan, env.created_at)
    report = build_report(store, pack, plan, env.created_at)
    store.put(report)
    return store.root


@pytest.mark.parametrize("changes,version", [
    ({}, "0.3.0"), ({"duplicate": True}, "0.3.1"), ({"no_logs": True}, "0.3.0"), ({"wrong_owner": True}, "0.3.0"),
])
def test_native_integrity_persisted_capture_replays_original_pass_fail_or_unknown(tmp_path, changes, version):
    from tests.builders import environment
    from tests.test_log_integrity import IMAGE, run

    draft, _ = run(tmp_path, changes)
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE}, model={"id": "fixture-model"})
    source = save_native_integrity_report(tmp_path, draft, env, version)
    result = analyze_saved_logs(source)
    assert result["accepted"], result
    assert result["checks"][0]["status"] == "PROJECTION_REPLAY_MATCH"
    assert result["checks"][0]["raw_source_replayed"] is False
    assert result["checks"][0]["original_native_parser_replayed"] is False
    assert result["checks"][0]["verdicts"]["security"] == ("FAIL" if changes.get("duplicate") else "UNKNOWN" if changes else "PASS")


@pytest.mark.parametrize("release,scope", [
    ("0.30.0", "matched"), ("0.30.0", "different-image"), ("0.30.0", "different-version"),
    ("0.30.0", "different-contract"), ("0.30.0", "different-source"),
    ("0.31.0", "matched"), ("0.31.0", "matched-canonical-source"),
    ("0.31.0", "different-contract"), ("0.31.0", "different-source"),
])
def test_saved_obs09_replay_uses_the_pinned_environment(tmp_path, release, scope):
    from guard.adapters.engine_provider import NATIVE_LOG_REPLAY_BASELINE_IMAGE, get_provider
    from guard.adapters.vllm import REQUEST_BINDING_GPU_IMAGE, VllmAdapter
    from guard.executors.native_projection import PROFILE, envelope, render_projection, replay_native
    from guard.log_evidence import _obs09
    from guard.models import canonical_json, sha256_hex
    from tests.builders import environment
    from tests.test_log_integrity import VALUES
    from tests.test_native_artifact_privacy import fixture

    baseline = release == "0.30.0"
    image = "docker.io/" + (NATIVE_LOG_REPLAY_BASELINE_IMAGE if baseline else REQUEST_BINDING_GPU_IMAGE)
    source_image = image if baseline or scope == "matched-canonical-source" else REQUEST_BINDING_GPU_IMAGE
    other_image = "vllm/vllm-openai@sha256:" + "f" * 64
    foreign_source = not baseline and scope == "different-source"
    if foreign_source:
        source_image = "foreign/vllm-openai@" + REQUEST_BINDING_GPU_IMAGE.rsplit("@", 1)[1]
    adapter = VllmAdapter()
    profile = get_provider("vllm").evidence_contract("native_log_replay_profile", image, release)
    request_logger_line = 63 if baseline else 65
    assert profile["request_logger_line"] == request_logger_line
    assert adapter.native_log_contract(image, release) is None

    def record_identity(capture, trials, before, after):
        for identity in [before, after]:
            identity.update(image=source_image, image_id=source_image)

    _, _, _, sample = fixture(change=record_identity, cases=("before", "after"), request_logger_line=request_logger_line)
    sample["native_contract_sha256"] = sha256_hex(canonical_json(profile["contract"]))
    if not baseline:
        assert sample["native_contract_sha256"] == "97bea97ae6e7984b8d3ce53bcefe9e7665471c228b9dc36eeb7e4a3a3373daf2"
    collection = replay_native(sample, request_logger_line=request_logger_line)["collection"]
    collection.update(capture_window=sample["capture"]["window"], redaction=PROFILE)
    document = render_projection(collection)
    sample["display_sha256"] = sha256_hex(document.encode())
    artifacts, artifact_refs = [], []
    fixture_store = Store(tmp_path / "fixture-store")
    for name, content in (("log_integrity_events.json", json.dumps(envelope(collection))),
                          ("log_integrity_display.html", document)):
        saved = fixture_store.put_artifact(content.encode("utf-8"))
        artifacts.append(SimpleNamespace(name=name, path=fixture_store.root / saved.path))
        artifact_refs.append(SimpleNamespace(name=name, path=saved.path, sha256=saved.sha256, bytes=saved.bytes))

    fixture_evd = SimpleNamespace(artifacts=artifact_refs, execution=SimpleNamespace(executor=SimpleNamespace(version="0.3.1")))
    original = environment(engine={"name": "vllm", "version": release, "image_digest": image},
                           model={"id": "EXAMPLE-projection"})
    replay_env = original
    if foreign_source:
        # The saved foreign transcript is internally consistent, including its physical image digest.
        replay_env = environment(engine={"name": "vllm", "version": release, "image_digest": source_image})
    obs, complete = _obs09(fixture_store.root, fixture_evd, sample, VALUES, replay_env)
    assert not complete and obs["normal_controls"] and obs["log_trials_reached"] == ["before", "after"]

    if scope in {"different-image", "different-version"}:
        original = environment(engine={"name": "vllm", "version": "0.31.0" if scope == "different-version" else release,
                                      "image_digest": other_image if scope == "different-image" else image},
                               model={"id": "EXAMPLE-projection"})
    elif scope == "different-contract":
        sample["native_contract_sha256"] = "b" * 64
    elif scope == "different-source" and baseline:
        sample["source_identity_after"]["image"] = other_image

    samples = tmp_path / "log_integrity_samples.json"
    samples.write_text(json.dumps(envelope(sample)), encoding="utf-8")
    artifacts.append(SimpleNamespace(name=samples.name, path=samples))
    draft = SimpleNamespace(status=SimpleNamespace(value="COMPLETED"), artifacts=artifacts, challenges=[],
                           normal_control={"attempted": True, "succeeded": True},
                           observations={key: {"value": value, "collected": True} for key, value in obs.items()})
    store = save_native_integrity_report(tmp_path, draft, original, "0.3.1")
    result = analyze_saved_logs(store)
    assert result["accepted"] is (scope in {"matched", "matched-canonical-source"}), result
    assert result["original_inventory_preserved"] and result["official"] is False
    if result["accepted"]:
        row, = result["checks"]
        assert row["status"] == "PROJECTION_REPLAY_MATCH" and row["verdicts"]["security"] == "UNKNOWN"
        assert row["coverage_complete"] is False and row["raw_source_replayed"] is False


@pytest.mark.parametrize("version,accepted", [("0.3.1+k8s.1", True), ("0.3.1+k8s.2", True), ("0.3.1+k8s.99", False)])
def test_native_kubernetes_transcript_replays_partial_cri_evidence(tmp_path, monkeypatch, version, accepted):
    from tests.test_kubernetes_log_integrity import run

    draft, _, env = run(tmp_path, monkeypatch, attach=None)
    assert draft.observations["log_trials_reached"].value == ["before", "cr", "reserved", "html", "after"]
    sample_artifact = next(item for item in draft.artifacts if item.name == "log_integrity_samples.json")
    assert sample_artifact.collector == "log_integrity/0.3.1+k8s.2"
    source = save_native_integrity_report(tmp_path, draft, env, version)
    before = inventory_tree(source)
    result = analyze_saved_logs(source)
    detail, = result["checks"]

    assert result["accepted"] is accepted, result
    assert detail["replayed"] is accepted
    assert detail["status"] == ("PROJECTION_REPLAY_MATCH" if accepted else "REPLAY_MISMATCH")
    assert detail["coverage_complete"] is False
    if accepted:
        assert detail["verdicts"]["security"] == "UNKNOWN"
        assert detail["raw_source_replayed"] is False
        assert detail["original_native_parser_replayed"] is False
    assert result["original_inventory_preserved"] and inventory_tree(source) == before


@pytest.mark.parametrize("kind,leak,expected", [(None, False, "PASS"), ("request", False, "UNKNOWN"),
                                             ("nested", True, "FAIL"), ("nan", False, "UNKNOWN")])
def test_new_strict_collection_replays_saved_pass_unknown_or_independent_fail(tmp_path, monkeypatch, kind, leak, expected):
    from guard.executors.log_analysis_contract import STRICT_JSON_PROFILE
    from guard.store.packs import put_pack
    from tests.test_log_analysis import AmbiguousSource, Source, run_log

    source = AmbiguousSource(kind, leak=leak) if kind else Source()
    store, evd, res = run_log(tmp_path, monkeypatch, source)
    assert res.verdicts.security.value == expected
    pack = baseline_pack()
    assert set(evd.observations) == {item.key for item in pack.checks["OBS-03"].required_observations}
    assert "log_violations" not in evd.observations
    put_pack(store, PACK_DIR)
    plan = store.resolve(evd.plan)
    report = build_report(store, pack, plan, store.resolve(evd.environment).created_at)
    store.put(report)
    before = inventory_tree(store.root)
    replay = analyze_saved_logs(store.root)
    assert replay["accepted"] and replay["original_inventory_preserved"], replay
    assert replay["checks"][0]["status"] == "PROJECTION_REPLAY_MATCH"
    assert replay["checks"][0]["replay_basis"] == "stored_predicate_projection"
    assert replay["checks"][0]["raw_source_replayed"] is False
    assert replay["checks"][0]["analysis_profile"] == STRICT_JSON_PROFILE
    assert replay["checks"][0]["strict_json_profile_applied"] is True
    assert replay["checks"][0]["coverage_complete"] is (kind is None)
    assert replay["checks"][0]["verdicts"]["security"] == expected
    assert inventory_tree(store.root) == before
    if kind:
        item = next(artifact for artifact in evd.artifacts if artifact.name == "log_samples.json")
        sample = json.loads((store.root / item.path).read_bytes())["capture"]
        assert sample["captures"][0]["rejected_json_lines"]["text"] == 1
        assert "GUARD_LOG_JSON_UNVERIFIABLE" in sample["captures"][0]["text"]
        assert '"request_id":"EXAMPLE-unrelated"' not in sample["captures"][0]["text"]


def test_legacy_duplicate_key_replay_keeps_original_profile_without_new_strict_claim(tmp_path):
    from guard.executors.log_analysis_contract import LEGACY_JSON_PROFILE

    source, original = seed(tmp_path)
    def append_legacy_row(sample):
        capture = sample["captures"][0]
        capture["text"] += ('{"request_id":"EXAMPLE_TRIAL_NORMAL","request_id":"EXAMPLE-unrelated",'
                            '"prompt":"EXAMPLE untrusted row"}\n')
        encoded = capture["text"].encode()
        capture["persisted_decoded_sha256"] = hashlib.sha256(encoded).hexdigest()
        capture["persisted_decoded_bytes"] = len(encoded)
    newer = revised_sample(source, append_legacy_row)
    before = inventory_tree(source)
    replay = analyze_saved_logs(source, newer)
    assert replay["accepted"], replay
    assert replay["checks"][0]["analysis_profile"] == LEGACY_JSON_PROFILE
    assert replay["checks"][0]["strict_json_profile_applied"] is False
    assert replay["checks"][0]["verdicts"]["security"] == "PASS"
    assert any("historical JSON parser" in item for item in replay["limitations"])
    assert analyze_saved_logs(source, original)["accepted"]
    assert inventory_tree(source) == before


@pytest.mark.parametrize("change", [
    lambda sample: sample.update(schema="guard-log-analysis-capture/3", analysis_profile="guard-log-json/2"),
    lambda sample: sample.update(analysis_profile="guard-log-json/future"),
    lambda sample: sample.update(schema="guard-log-analysis-capture/3", analysis_profile="guard-log-json/future"),
])
def test_unknown_or_mismatched_analysis_profile_is_not_substituted_for_saved_executor(tmp_path, change):
    source, original = seed(tmp_path)
    newer = revised_sample(source, change)
    before = inventory_tree(source)
    replay = analyze_saved_logs(source, newer)
    assert not replay["accepted"]
    assert replay["checks"][0]["status"] == "REPLAY_MISMATCH"
    assert analyze_saved_logs(source, original)["accepted"]
    assert inventory_tree(source) == before


@pytest.mark.parametrize("missing", ["absent", "not-collected"])
def test_required_stored_observation_is_never_inferred_from_capture(tmp_path, missing):
    source, original = seed(tmp_path)
    def omit(observations):
        if missing == "absent":
            observations.pop("log_fields_seen")
        else:
            observations["log_fields_seen"] = observations["log_fields_seen"].model_copy(update={"value": None, "collected": False})
    newer = revised_sample(source, lambda sample: None, observation_change=omit)
    before = inventory_tree(source)
    replay = analyze_saved_logs(source, newer)
    assert not replay["accepted"] and replay["checks"][0]["status"] == "REPLAY_MISMATCH"
    assert any("log_fields_seen" in problem for problem in replay["problems"])
    assert analyze_saved_logs(source, original)["accepted"]
    assert inventory_tree(source) == before


def test_required_reconstructed_observation_cannot_be_silently_omitted(tmp_path, monkeypatch):
    import guard.log_evidence as replay_module

    source, original = seed(tmp_path)
    newer = revised_sample(source, lambda sample: sample["analysis"].pop("log_fields_seen"))
    original_analyzer = replay_module.analyze_log_captures
    def incomplete_reconstruction(*args, **kwargs):
        reconstructed = original_analyzer(*args, **kwargs)
        reconstructed.pop("log_fields_seen")
        return reconstructed
    before = inventory_tree(source)
    with monkeypatch.context() as context:
        context.setattr(replay_module, "analyze_log_captures", incomplete_reconstruction)
        replay = analyze_saved_logs(source, newer)
    assert not replay["accepted"] and replay["checks"][0]["status"] == "REPLAY_MISMATCH"
    assert any("cannot be reconstructed: log_fields_seen" in problem for problem in replay["problems"])
    assert analyze_saved_logs(source, original)["accepted"]
    assert inventory_tree(source) == before


def test_auxiliary_evidence_diagnostic_does_not_change_fixed_verdict_projection(tmp_path):
    from guard.models import Observation

    source, original = seed(tmp_path)
    def add_diagnostic(observations):
        observations["log_violations"] = Observation(value=["EXAMPLE auxiliary diagnostic only"], collected=True)
    newer = revised_sample(source, lambda sample: None, observation_change=add_diagnostic)
    before = inventory_tree(source)
    replay = analyze_saved_logs(source, newer)
    assert replay["accepted"] and replay["checks"][0]["verdicts"]["security"] == "PASS", replay
    assert analyze_saved_logs(source, original)["accepted"]
    assert inventory_tree(source) == before


def test_auxiliary_artifact_diagnostic_is_still_compared_against_full_reconstruction(tmp_path):
    source, original = seed(tmp_path)
    newer = revised_sample(source, lambda sample: sample["analysis"].update(log_violations=["EXAMPLE invented diagnostic"]))
    before = inventory_tree(source)
    replay = analyze_saved_logs(source, newer)
    assert not replay["accepted"] and replay["checks"][0]["status"] == "REPLAY_MISMATCH"
    assert any("recorded log analysis differs" in problem for problem in replay["problems"])
    assert analyze_saved_logs(source, original)["accepted"]
    assert inventory_tree(source) == before
