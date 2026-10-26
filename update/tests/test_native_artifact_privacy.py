"""EXAMPLE native projections/faults; no engine, daemon, AWS or network."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from guard.core.judge import judge_evidence
from guard.executors.log_integrity_contract import PLANNED_CASES, analyze_integrity_capture
from guard.executors.native_projection import decision, project_native, replay_native, PUBLIC_IDS
from guard.log_evidence import analyze_saved_logs
from guard.models import ExecStatus, Verdict, canonical_json
from guard.report.log_integrity import render_log_integrity
from guard.validation_evidence import inventory_tree
from tests import test_log_integrity as source
from tests.test_log_integrity_parser import native
from tests.test_html_artifact_persistence import scenario
from tests.builders import evidence, policy, baseline_pack, T0


CANARY = "EXAMPLE_NATIVE_PRIVATE_PAYLOAD_08D78F"
TOKEN = 8725148612537489


def fixture(*, change=None):
    trials, lines = [], []
    for case in PLANNED_CASES:
        rid = "EXAMPLE_PRIVATE_ID_" + case + ({"multiline": "\nSECOND", "cr": "\rCR", "crlf": "\r\nCRLF"}.get(case, ""))
        trials.append({"case": case, "request_id": rid, "request_id_trust": "untrusted_correlation",
            "request_fixture_sha256": hashlib.sha256(case.encode()).hexdigest(), "http_outcome": 200,
            "response_id": "cmpl-" + rid, "normal_generation": "pass", "response_id_matches_body": True})
        lines.append(native("Received request cmpl-" + rid + "-0: params: " + CANARY + ", lora_request: None."))
    capture = {"text": "".join(lines), "stderr_text": "", "returncode": 0, "truncated": False,
        "timed_out": False, "source_complete": True, "stderr_present": False,
        "window": {"since": "2026-10-23T03:00:00Z", "until": "2026-10-23T03:01:00Z"}}
    identity = {"container_id": "EXAMPLE-native", "started_at": "EXAMPLE-start", "image": source.IMAGE,
        "image_id": source.IMAGE_ID, "image_repo_digest_verified": True,
        "logging_profile": "request-info-v1", "logging_controls_verified": True}
    before, after = deepcopy(identity), deepcopy(identity)
    if change:
        change(capture, trials, before, after)
    original = analyze_integrity_capture(capture, trials, before, after)
    sample = project_native(capture, trials, before, after, analysis=original)
    replay = analyze_integrity_capture(sample["capture"], sample["trials"], sample["source_identity_before"],
        sample["source_identity_after"], initial_gaps=sample["replay_initial_gaps"])
    collection = replay["collection"]
    collection.update(capture_window=capture["window"], redaction="guard-native-predicate-projection/1")
    html = render_log_integrity(collection)
    sample.update(policy_values=source.VALUES, native_contract_sha256="0"*64, source_display_sha256="1"*64,
        collection_sha256=hashlib.sha256(json.dumps(collection, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        display_sha256=hashlib.sha256(html.encode()).hexdigest(), display_verified=True)
    return capture, trials, original, sample


@pytest.mark.parametrize("change", [
    None,
    lambda c,t,b,a: c.update(text=c["text"]+c["text"]),
    lambda c,t,b,a: c.update(text=c["text"].replace("INFO", "ERROR", 1)),
    lambda c,t,b,a: c.update(text=c["text"].replace("INFO", "WARNING")),
    lambda c,t,b,a: c.update(text=c["text"].replace("request_logger.py:65", "other.py:99", 1)),
    lambda c,t,b,a: c.update(text=c["text"].replace("pid=42", "pid=456", 1)),
    lambda c,t,b,a: c.update(text=c["text"].replace("\r", "")),
    lambda c,t,b,a: c.update(text=c["text"][:-1]),
    lambda c,t,b,a: c.update(text=c["text"]+"\ufffd\n"),
    lambda c,t,b,a: c.update(text=c["text"]+"x"*131072),
    lambda c,t,b,a: c.update(text=""),
    lambda c,t,b,a: c.update(text=c["text"]+"(unsupported) "+CANARY+"\n"),
    lambda c,t,b,a: c.update(text=c["text"]+CANARY+"\n"),
    lambda c,t,b,a: c.update(text=c["text"].replace("2026-10-23T03:00:00", "2026-10-23T04:00:00")),
    lambda c,t,b,a: c.update(text=c["text"]+native("unfinished")[:-1]+"\r\n"),
    lambda c,t,b,a: c.update(text=c["text"]+native("unfinished")[:-1]+"\r\nunknown\n"),
    lambda c,t,b,a: c.update(stderr_text=CANARY+"\n", stderr_present=True),
    lambda c,t,b,a: c.update(stderr_text=c["text"], stderr_present=True),
    lambda c,t,b,a: c.update(returncode=1),
    lambda c,t,b,a: c.update(timed_out=True),
    lambda c,t,b,a: c.update(source_complete=False),
    lambda c,t,b,a: a.update(started_at="EXAMPLE-restart"),
    lambda c,t,b,a: (t[-1].update(response_id=CANARY, response_id_matches_body=False, normal_generation="no_response")),
    lambda c,t,b,a: t.pop(),
])
def test_native_predicate_replay_preserves_original_decisions_and_source_gaps(change):
    capture, trials, original, sample = fixture(change=change)
    assert decision(replay_native(sample)) == decision(original)
    text = json.dumps(sample, ensure_ascii=False)
    assert CANARY not in text and "EXAMPLE_PRIVATE_ID_" not in text
    assert sample["source_summary"]["stdout"]["decoded_sha256"] == hashlib.sha256(capture["text"].encode()).hexdigest()
    assert sample["capture"]["persisted_decoded_sha256"] == hashlib.sha256(sample["capture"]["text"].encode()).hexdigest()
    assert sample["source_summary"]["stdout"]["decoded_sha256"] != sample["capture"]["persisted_decoded_sha256"] or not capture["text"]
    assert all(t["request_id"] == PUBLIC_IDS[t["case"]] for t in sample["trials"])


@pytest.mark.parametrize("change", [
    lambda s: s.update(raw_body=CANARY),
    lambda s: s["capture"].update(error=CANARY),
    lambda s: s["capture"].update(text=s["capture"]["text"]+CANARY+"\n"),
    lambda s: s["trials"][0].update(response_id=CANARY),
    lambda s: s["trials"][0].update(request_id=CANARY),
    lambda s: s["trials"][0].update(response_id_sha256="a"*64),
    lambda s: s["trials"][0].update(request_id_sha256=CANARY),
    lambda s: s["trials"][0].update(http_outcome=True),
    lambda s: s["trials"][0].update(normal_generation=CANARY),
    lambda s: s["trials"][0].update(usage={"total_tokens":TOKEN}),
    lambda s: s["source_identity_before"].update(container_id=CANARY),
    lambda s: s["source_summary"]["stdout"].update(raw=CANARY),
    lambda s: s["source_summary"]["stdout"]["events"][0].update(message=CANARY),
    lambda s: s["source_summary"]["stdout"].update(parser_gaps=[CANARY]),
    lambda s: s.update(replay_initial_gaps=["native event is outside or has an invalid capture timestamp"]),
    lambda s: s["analysis"].update(normal_logs=False),
])
def test_native_projection_rejects_raw_values_or_inconsistent_predicates(change):
    _, _, _, sample = fixture()
    change(sample)
    with pytest.raises((ValueError, TypeError, KeyError)):
        replay_native(sample)


@pytest.mark.parametrize("raw", [CANARY, str(TOKEN), "GNI_OTHER " + CANARY, '{"secret":"'+CANARY+'"}'])
def test_rehashed_transcript_cannot_smuggle_raw_payloads_through_valid_hashes(raw):
    _, _, _, sample = fixture()
    capture = sample["capture"]
    capture["text"] = capture["text"].replace("GNI_PARAMS", raw, 1)
    capture["persisted_decoded_sha256"] = hashlib.sha256(capture["text"].encode()).hexdigest()
    capture["persisted_decoded_bytes"] = len(capture["text"].encode())
    with pytest.raises(ValueError):
        replay_native(sample)


def test_collector_uses_original_bodies_in_memory_and_never_writes_them(tmp_path, monkeypatch):
    class PrivateWorld(source.World):
        def probe(self, *args):
            result = super().probe(*args)
            value = json.loads(result[0]["body"])
            value["choices"][0]["text"] = CANARY
            value["usage"] = {"prompt_tokens":TOKEN, "completion_tokens":1, "total_tokens":TOKEN+1}
            result[0]["body"] = json.dumps(value)
            result[0]["body_bytes"] = len(result[0]["body"].encode())
            result[0]["detail"] = CANARY
            return result
        def logs_window(self, *args, **kwargs):
            result = super().logs_window(*args, **kwargs)
            result.stdout = result.stdout.replace("SamplingParams(max_tokens=1)", CANARY+str(TOKEN))
            result.stdout += CANARY+"\n"
            return result
    monkeypatch.setattr(source, "World", PrivateWorld)
    draft, world = source.run(tmp_path)
    assert draft.status == ExecStatus.COMPLETED and source.judge(draft).verdicts["security"] == Verdict.PASS
    files = "".join(a.path.read_text(encoding="utf-8") for a in draft.artifacts)
    assert CANARY not in files and str(TOKEN) not in files
    assert len(world.requests) == 7 and "\n" in world.requests[1]["body"]["request_id"]
    assert "<script>" in world.requests[5]["body"]["request_id"]
    sample = json.loads(next(a.path for a in draft.artifacts if a.name.endswith("samples.json")).read_bytes())["capture"]
    for request, trial in zip(world.requests, sample["trials"]):
        assert request["body"]["request_id"] not in files
        assert trial["request_fixture_sha256"] == hashlib.sha256(canonical_json(request["body"])).hexdigest()


@pytest.mark.parametrize("where", ["probe", "capture"])
def test_dynamic_exception_class_names_and_bodies_are_omitted(tmp_path, monkeypatch, where):
    error = type(CANARY, (Exception,), {})
    class ErrorWorld(source.World):
        def probe(self, *args):
            if where == "probe": raise error(CANARY)
            return super().probe(*args)
        def logs_window(self, *args, **kwargs):
            if where == "capture": raise error(CANARY)
            return super().logs_window(*args, **kwargs)
    monkeypatch.setattr(source, "World", ErrorWorld)
    draft, _ = source.run(tmp_path)
    assert source.judge(draft).verdicts["security"] == Verdict.UNKNOWN
    assert CANARY not in "".join(a.path.read_text(encoding="utf-8") for a in draft.artifacts)


@pytest.mark.parametrize("where", ["projection", "render", "format"])
@pytest.mark.parametrize("violation", [False, True])
def test_projection_preparation_failure_preserves_actual_observations_and_fail(tmp_path, monkeypatch, where, violation):
    import guard.executors.log_integrity as module
    (tmp_path / "baseline").mkdir()
    baseline, _ = source.run(tmp_path / "baseline", {"forged_metadata":violation})
    def broken(*a, **k): raise RuntimeError(CANARY)
    with monkeypatch.context() as patch:
        if where == "projection": patch.setattr(module, "project_native", broken)
        elif where == "render": patch.setattr(module, "render_log_integrity", broken)
        else:
            original = module.json.dumps
            def dumps(value, *a, **k):
                if isinstance(value, dict) and value.get("redaction") == "guard-native-predicate-projection/1":
                    raise RuntimeError(CANARY)
                return original(value, *a, **k)
            patch.setattr(module.json, "dumps", dumps)
        (tmp_path / "fault").mkdir()
        draft, _ = source.run(tmp_path / "fault", {"forged_metadata":violation})
    assert draft.status == ExecStatus.ABORTED
    for key in baseline.observations:
        if where == "render" and key == "log_display_verified":
            assert draft.observations[key].value is False
        else:
            assert draft.observations[key] == baseline.observations[key]
    evd = evidence(check_id="OBS-09", execution={"status": draft.status, "abort_reason": draft.abort_reason},
        normal=draft.normal_control.model_dump(), observations={key:value.model_dump() for key,value in draft.observations.items()})
    evd = evd.model_copy(update={"challenges": draft.challenges}).seal()
    pol = policy(baseline_pack().ref(), values={"OBS-09":source.VALUES})
    result = judge_evidence(source.check(), evd, pol, T0)
    assert result.verdicts.security == (Verdict.FAIL if violation else Verdict.UNKNOWN)
    assert result.verdicts.functional == Verdict.UNKNOWN and result.verification_level is None
    assert any("ABORTED" in reason for reason in result.reasons)
    assert {a.name for a in draft.artifacts} == {"probe_log.jsonl"}
    assert CANARY not in str(draft.model_dump())


@pytest.mark.parametrize("filename", ["log_integrity_samples.json", "log_integrity_events.json"])
@pytest.mark.parametrize("violation", [False, True])
def test_one_native_json_write_failure_keeps_other_successful_exact_refs(scenario, monkeypatch, filename, violation):
    baseline = scenario(violation=violation)
    original = Path.write_bytes
    def write(path, data):
        if path.name == filename: raise OSError(CANARY)
        return original(path, data)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_bytes", write)
        case = scenario(violation=violation)
    assert case.evd.execution.status == ExecStatus.ABORTED
    assert case.evd.observations == baseline.evd.observations
    assert {a.name for a in case.evd.artifacts} == {"probe_log.jsonl", "log_integrity_display.html",
        "log_integrity_samples.json", "log_integrity_events.json"} - {filename}
    assert case.res.verdicts.security == (Verdict.FAIL if violation else Verdict.UNKNOWN)
    assert case.store.verify() == [] and CANARY not in canonical_json(case.evd.to_dict()).decode()


@pytest.mark.parametrize("projection", [False, True], ids=["legacy-native", "predicate-native"])
def test_legacy_and_projected_native_store_replay_stays_read_only(tmp_path, projection):
    spec = importlib.util.spec_from_file_location("native_wheel_fixture", Path(__file__).resolve().parents[1]/"tools/wheel_log_fixture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store, report = module.build_native(tmp_path/"store", source.ROOT/"packs/PACK-BASELINE", projection=projection)
    before = inventory_tree(store)
    result = analyze_saved_logs(store, report)
    assert result["accepted"] and result["official"] is False, result
    row = result["checks"][0]
    assert row["status"] == ("PROJECTION_REPLAY_MATCH" if projection else "REPLAY_MATCH")
    if projection:
        assert row["raw_source_replayed"] is False and row["original_native_parser_replayed"] is False
        assert row["replay_basis"] == "stored_native_predicate_transcript"
    assert inventory_tree(store) == before
