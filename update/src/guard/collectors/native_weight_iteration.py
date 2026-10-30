"""A2 전용 native16 선택/진행 후보 결합. 실제 FD/tensor 사용 판정은 별도다.

입력은 A1 capture_session이 이미 prepared/ENV/target/source/code/session 및
recording을 검증한 packet이다. 임의 JSON을 신뢰 입력으로 가져오는 API가 아니다.
공통 parser/계측/수집은 복제하지 않으며 기존 native_weight_files는 변경하지 않는다.
"""
from collections import Counter
from pathlib import Path

from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_weight_iteration_metadata as common
from guard.adapters import vllm_weight_iteration_sensor as sensor
from guard.models import canonical_json, sha256_hex

VERSION = "0.1.1"
SCHEMA = "guard-native-weight-selection-iteration/1"
SOURCE_PINS = {
    "vllm_weight_iteration_metadata.py": "f8ced901b1b1c7d918f96c9ef8ec0899696c2d161efbaefdd43dbedd340f04c0",
    "vllm_weight_iteration_sensor.py": "e7494edaa0a512201652228acc73ac13621cb40d938e6f23b6d4eaba73d05bc6",
}
MAX_SELECTIONS = 256
MAX_CANDIDATES = 256
MAX_PATHS = 32
MAX_LOCATORS = 64


def _require(value):
    if not value:
        raise ValueError("NATIVE_WEIGHT_ITERATION_INPUT_INVALID")


def _source_gate():
    root = Path(__file__).resolve().parents[1] / "adapters"
    try:
        matched = all(sha256_hex((root / name).read_bytes()) == expected for name, expected in SOURCE_PINS.items())
    except OSError:
        matched = False
    if not matched:
        raise ValueError("NATIVE_WEIGHT_ITERATION_COMMON_SOURCE_MISMATCH")


def _selection(value):
    _require(type(value) is dict and all(value[k] is None or common.fingerprint(value[k])
        for k in ("weight_call_sha256", "weight_model_sha256")))
    _require(type(value["paths"]) is list and len(value["paths"]) <= 128
        and all(common.fingerprint(path) for path in value["paths"])
        and type(value["path_count"]) is int and 0 <= value["path_count"] < 2**63
        and all(type(value[k]) is bool for k in ("paths_complete", "load_context_linked"))
        and (value["factory_returned_generator"] is None or type(value["factory_returned_generator"]) is bool))
    return value


def _analyze(packet):
    """검증된 공통 packet의 같은 recording/loader-call/model 후보만 결합한다.

    native16 proxy가 반환되면 legacy generator-type flag는 false일 수 있다.
    값을 바꾸거나 generator/factory 반환을 실제 파일 사용으로 해석하지 않는다.
    """
    _require(type(packet) is dict and type(packet.get("recordings")) is list and len(packet["recordings"]) <= 32)
    selections, missing, seen = [], [], set()
    for index, recording in enumerate(packet["recordings"]):
        _require(type(recording) is dict and "capture" in recording)
        if recording["capture"] is None:
            _require(recording.get("session_owner_match") is False and recording.get("listed_file_identity_match") is False)
            missing.append("weight_recording_capture_unavailable")
            continue
        _require(type(recording["capture"]) is dict)
        capture = recording["capture"]; rows = capture.get("records")
        _require(type(rows) is list and len(rows) <= sensor.MAX_ROWS and len(canonical_json(rows)) <= sensor.MAX_BYTES)
        if not rows:
            missing.append("weight_recording_rows_unavailable")
            continue
        _require(rows[0]["event"] == "START" and rows[0]["payload"]["profile"] == sensor.PROFILE)
        bound = (capture.get("process_context_verified") is True and recording.get("session_owner_match") is True
            and recording.get("listed_file_identity_match") is True)
        for row in rows:
            _require(type(row) is dict)
            _require(type(row.get("seq")) is int and row["seq"] >= 0 and (index, row["seq"]) not in seen)
            seen.add((index, row["seq"]))
            selected = profiles.returned_weight_selection(row)
            if selected is None:
                continue
            selected = _selection(selected)
            if len(selections) >= MAX_SELECTIONS:
                missing.append("weight_selection_output_bound")
                continue
            selections.append({**selected, "event": [index, row["seq"]], "process_binding_verified": bound})
    # This shared function validates the closed event fields, pinned runtime/code
    # and journal. The caller retains the full capture/session admission boundary.
    projection = common.project(packet)
    candidates = []
    counts = Counter((s["event"][0], s["weight_call_sha256"], s["weight_model_sha256"])
        for s in selections if s["load_context_linked"])
    for selection in selections:
        if not (selection["process_binding_verified"] and selection["load_context_linked"]):
            missing.append("weight_selection_binding_unverified")
            continue
        recording, seq = selection["event"]
        for root in projection["model_candidates"]:
            if not (root["recording"] == recording and root["process_binding_verified"] and root["source_verified"]
                and root["loader_call_model_link_observed"]
                and root["loader_call_sha256"] == selection["weight_call_sha256"]
                and root["loader_model_sha256"] == root["model_object_sha256"] == selection["weight_model_sha256"]):
                continue
            facts = [fact for fact in projection["samples"] if fact["recording"] == recording
                and fact["root_load_sha256"] == root["call_sha256"]
                and fact["model_object_sha256"] == root["model_object_sha256"]
                and fact["loader_call_sha256"] == root["loader_call_sha256"]
                and fact["loader_model_sha256"] == root["loader_model_sha256"]
                and fact["factory_present"] and fact["source_pair_verified"] and fact["root_load_active_observed"]
                and max(seq, root["factory_seq"]) < fact["end_seq"]]
            if not facts:
                continue
            if len(candidates) >= MAX_CANDIDATES:
                missing.append("weight_candidate_output_bound")
                continue
            paths = selection["paths"][:MAX_PATHS]
            candidates.append({"selection_event": selection["event"], "root_event": [recording, root["factory_seq"]],
                "root_load_sha256": root["call_sha256"], "model_object_sha256": root["model_object_sha256"],
                "loader_call_sha256": root["loader_call_sha256"], "pid": root["pid"], "start_ticks": root["start_ticks"],
                "selected_path_sha256": paths, "reported_path_count": selection["path_count"],
                "selection_paths_complete": selection["paths_complete"] and len(selection["paths"]) <= MAX_PATHS,
                "legacy_factory_returned_generator": selection["factory_returned_generator"],
                "same_loader_call_candidate": True,
                "selection_ambiguous": counts[(recording, selection["weight_call_sha256"], selection["weight_model_sha256"])] > 1,
                "step_yields_observed": len(facts),
                "parameter_callback_returns_observed": sum(fact["parameter_callback_returned_observed"] for fact in facts),
                "source_events": [[recording, fact["end_seq"]] for fact in facts[:MAX_LOCATORS]],
                "source_event_locators_complete": len(facts) <= MAX_LOCATORS,
                "root_exhausted_observed": root["root_exhausted_observed"], "recording_closed": root["recording_closed"],
                "per_file_consumption_verified": False, **{key: False for key in common.TRUTH}, "scope_complete": False})
            if not candidates[-1]["selection_paths_complete"]:
                missing.append("weight_selection_paths_incomplete")
            if not candidates[-1]["source_event_locators_complete"]:
                missing.append("weight_event_locators_incomplete")
    if not selections:
        missing.append("weight_selection_unavailable")
    if not candidates:
        missing.append("weight_selection_iteration_join_unavailable")
    return {"schema": SCHEMA, "profile": sensor.PROFILE, "version": VERSION, "candidates": candidates,
        "missing": sorted(set(missing + projection["missing"])), "security": "UNKNOWN",
        "per_file_consumption_verified": False, **{key: False for key in common.TRUTH}, "scope_complete": False}


def analyze(packet):
    _source_gate()
    try:
        return _analyze(packet)
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise ValueError("NATIVE_WEIGHT_ITERATION_INPUT_INVALID") from None
