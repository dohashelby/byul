"""Project the retained stock journals and reject invalid owner context."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path

import pytest

from guard.collectors.file import load_policy
from guard.configuration import BOOT_IDENTITY_OPTIONS, bounded_metadata_scopes, for_side, requested_input_paths
from guard.executors import kubernetes_normal_boot_identity as identity
from guard.executors.kubernetes_normal_config_parser import _parser_profile
from tests.test_kubernetes_normal_config_parser import parser_env
from guard.models import canonical_json, load_pack


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_retained_journals_retain_actual_flags_and_three_model_returns(side):
    retained = os.environ.get("BYUL_RETAINED_BOOT")
    if not retained:
        pytest.skip("M10 original live recordings not supplied")
    root = Path(retained) / side / "export"
    read = lambda name: json.loads((root / name).read_bytes())
    original = read("SUP-05-mutable-process-trace.json")
    monitor = read("SUP-05-observer-source.json")["mounted_file_sha256s"]
    parser_pins = _parser_profile(parser_env(original["version"]))["source_pins"]
    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    approval_sha256 = load_policy(Path(retained) / "policy.json", pack).source_document_sha256
    assert approval_sha256 is not None
    pins = identity._pinned_sources(original["version"], parser_pins)
    subjects = []
    for row in original["processes"]:
        assert row["owner_verified"]
        first = row["rows"][0]
        actor = {key: first[key] for key in ("pid", "actor", "start_ticks", "boot_sha256", "pid_namespace",
                                             "mount_namespace", "time_namespace")}
        raw = b"\n".join(map(canonical_json, row["rows"])) + b"\n"
        subjects.append({"subject": actor, "journal": raw.decode(), "journal_sha256": sha256(raw).hexdigest(), "uid": 2000})
    packet = {"session": original["session"], "monitor": {key: monitor[key] for key in
        ("owned_startup_trace.py", "byul_owned_trace.pth")}, "sources": pins, "phase": "normal-parser",
        "approval_sha256": approval_sha256, "processes": subjects}
    window = read("target.json")["startup_log_prefix"]
    proof = {"parser_pins": parser_pins, "approval_sha256": approval_sha256}
    assert len(identity.project(packet, original["version"], window, "SUP-11", **proof)) == 2
    facts = identity.project(packet, original["version"], window, "SUP-09", **proof)
    assert [r["observed_return"] for r in facts].count("model-loader-return") == 1
    assert [r["observed_return"] for r in facts].count("model-registry-return") == 2
    for key in ("start_ticks", "pid_namespace"):
        wrong = deepcopy(packet)
        wrong["processes"][1]["subject"][key] += 1
        with pytest.raises(ValueError):
            identity.project(wrong, original["version"], window, "SUP-09", **proof)


def test_ordered_shared_boot_configuration_keeps_exact_two_normal_scopes(tmp_path):
    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    scopes = {"SUP-11", "SUP-09"}
    cfg = {"executors": {"supply_boot": "kubernetes"}, "update": {"mode": "external",
        "allowed_checks": ["SUP-11", "SUP-09"], "kubernetes_candidate_binding": "after_baseline"},
        "check_config": {cid: {option: True} for cid, option in BOOT_IDENTITY_OPTIONS.items()},
        "sides": {side: {"supply_boot": {"target": str(tmp_path / side)}} for side in ("baseline", "candidate")}}
    assert [set(visible) for visible in bounded_metadata_scopes(
        [for_side(cfg, side) for side in cfg["sides"]], pack, scopes)] == [scopes, scopes]
    assert [deferred for _, _, deferred in requested_input_paths(cfg)] == [False, True]
    wrong = deepcopy(cfg)
    wrong["check_config"]["SUP-10"] = wrong["check_config"].pop("SUP-11")
    with pytest.raises(ValueError, match="NORMAL_BOOT_IDENTITY_EXECUTION_SCOPE_INVALID"):
        bounded_metadata_scopes([for_side(wrong, side) for side in wrong["sides"]], pack, scopes)
