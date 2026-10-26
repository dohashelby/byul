"""Saved replay contracts with EXAMPLE artifacts, never serving engines."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.log_evidence import analyze_saved_logs, main
from guard.models import DataOrigin, PolicyStatus, ReviewStatus
from guard.store import Store
from guard.validation_evidence import inventory_tree
from tests.builders import PACK_DIR, baseline_pack
from tests.prepared_fixtures import manual_setup_seed


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


@pytest.mark.parametrize("changes", [{}, {"duplicate": True}, {"no_logs": True}, {"wrong_owner": True}])
def test_native_integrity_persisted_capture_replays_original_pass_fail_or_unknown(tmp_path, changes):
    from guard.core.select import select
    from guard.models import Evidence
    from guard.store.packs import put_pack
    from tests.builders import environment, policy
    from tests.test_log_integrity import IMAGE, VALUES, run
    draft, _ = run(tmp_path, changes)
    store = Store(tmp_path / "native-store")
    pack = baseline_pack()
    put_pack(store, PACK_DIR)
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE}, model={"id": "fixture-model"})
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
        execution={"status": "COMPLETED", "executor": {"name": "log_integrity", "version": "0.2.1", "config_sha256": "0" * 64}},
        normal_control=draft.normal_control, challenges=draft.challenges, observations=draft.observations,
        artifacts=artifacts).seal()
    store.put(evd)
    judge_plan(store, pack, plan, env.created_at)
    report = build_report(store, pack, plan, env.created_at)
    store.put(report)
    result = analyze_saved_logs(store.root)
    assert result["accepted"], result
    assert result["checks"][0]["verdicts"]["security"] == ("FAIL" if changes.get("duplicate") else "UNKNOWN" if changes else "PASS")


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
    assert replay["checks"][0]["status"] == "REPLAY_MATCH"
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
