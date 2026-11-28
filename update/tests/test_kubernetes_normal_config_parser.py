"""The owned normal parser scope never substitutes for the eager-weight scope."""

from copy import deepcopy
import json
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from guard.adapters.engine_provider import get_provider
from guard.configuration import (NORMAL_FILE_READS_SCOPE_ERROR, SUP05_CONFIG_PARSER_OPTION,
    bounded_metadata_scopes, for_side, requested_input_paths, staged_observer_executors)
from guard.executors import kubernetes_normal_config_parser as parser
from guard.executors import run_binding
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from tests.builders import environment


APPROVAL_SHA256 = "9" * 64

WINDOW = {"container_started_at": "2026-10-26T00:00:00Z", "ready_at": "2026-10-26T00:00:04Z"}
SESSION = "TRACE-" + "1" * 26
CHECK = "SUP-05"
PACK = Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE"
IMAGES = {
    "0.30.0": "5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40",
    "0.31.0": "a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b",
}
PARSER_PINS = {
    "0.30.0": "aaaf215c9166f85656b7491fca3c1abd48d395d084973e7ca8c3e73188b6b2e8",
    "0.31.0": "bfa2d8ed9fc0971694e7873351a06dc9d5948775984f120148ad8a8e38a5acbc",
}


def test_approved_reader_window_is_accepted_by_production_supervisor():
    code = parser.supervised_source("print('read-started')")
    for seconds, expected in ((parser.READER_SECONDS, 0), (7, 124)):
        observed = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(seconds)],
                                  capture_output=True, timeout=3, check=False)
        assert observed.returncode == expected
        assert observed.stdout == (b"read-started\n" if expected == 0 else b"")


def parser_env(version):
    return environment(
        engine={"name": "vllm", "version": version,
                "image_digest": f"docker.io/vllm/vllm-openai@sha256:{IMAGES[version]}"},
        model={"id": parser.MODEL, "revision": parser.MODEL_REVISION, "inventory_sha256": parser.INVENTORY},
    )


def packet(profile):
    sources = profile["source_pins"]
    points = profile["points"]
    identity = dict(device=1, inode=2, mode=stat.S_IFREG | 0o444, uid=0, gid=0, size=1196, mtime_ns=22, ctime_ns=23)
    process = dict(pid=1, start_ticks=22, boot_sha256="f" * 64, pid_namespace=3, mount_namespace=4, time_namespace=5)
    first = parser._ns(WINDOW["container_started_at"]) + 100
    rows = []

    def event(kind, **fields):
        seq = len(rows)
        rows.append({"kind": kind, "seq": seq, "session": SESSION, "pid": 1,
                    "receipt_utc_ns": first + seq, "monotonic_ns": 200 + seq, **fields})

    event("interpreter", actor="stock-vllm-cli", sources=sources, trial_approval_sha256=APPROVAL_SHA256,
          feature_settings={"normal_phase": "normal-parser"}, **{k: v for k, v in process.items() if k != "pid"})
    for index, (role, (name, path, _)) in enumerate(points.items()):
        event("enter", role=role, point=name, source_sha256=sources[path], parent_call=index or None,
              artifact_path_sha256=sha256_hex(parser.CONFIG_PATH.encode()), artifact_at_entry={"state": "file", **identity})
    for call, (role, (_, _, key)) in reversed(list(enumerate(points.items(), 1))):
        event("return", role=role, call=call, result={key: True, "return_kind": "dict",
              "same_artifact_path": True, "artifact_at_return": {"state": "file", **identity}})
    body = {"process": process, "session": SESSION, "profile_phase": "normal-parser",
            "profile_approval_sha256": APPROVAL_SHA256, "config_sha256": parser.CONFIG_SHA,
            "config_identity": identity, "sources": sources, "journal_uid": 2000,
            "monitor": {"owned_startup_trace.py": parser.MONITOR, "byul_owned_trace.pth": parser.BOOTSTRAP}}
    return body, rows


def project(body, rows, profile):
    raw = b"\n".join(map(canonical_json, rows)) + b"\n"
    return parser._journal({**body, "journal": raw.decode(), "journal_hash": sha256_hex(raw)}, profile, WINDOW,
                           approval_sha256=APPROVAL_SHA256)


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_exact_native_parser_return_joins_only_current_approved_file(version):
    profile = parser._parser_profile(parser_env(version))
    body, rows = packet(profile)
    fact, = project(body, rows, profile)
    assert fact["parser_call_return_seq"] == [3, 4]
    assert fact["hf_call_return_seq"] == [2, 5] and fact["engine_call_return_seq"] == [1, 6]
    assert fact["config_sha256"] == parser.CONFIG_SHA and fact["eager_weight_reads_verified"] is False
    for changed in ({"config_sha256": "f" * 64}, {"session": "TRACE-" + "2" * 26},
                    {"monitor": {"owned_startup_trace.py": "0" * 64}}):
        with pytest.raises(ValueError):
            project({**body, **changed}, rows, profile)
    for index, change in ((3, {"parent_call": 1}), (4, {"kind": "unwind"}),
                          (5, {"receipt_utc_ns": parser._ns(WINDOW["ready_at"]) + 1})):
        bad = deepcopy(rows)
        bad[index].update(change)
        assert project(body, bad, profile) == []


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_parser_contract_binds_exact_provider_release_and_installed_sources(version):
    env = parser_env(version)
    provider = get_provider("vllm")
    profile = provider.evidence_contract("config_parser_source", env)
    root = "/usr/local/lib/python3.12/dist-packages/"
    assert profile["source_pins"] == {
        root + "vllm/transformers_utils/config.py": PARSER_PINS[version],
        root + "transformers/configuration_utils.py":
            "02826ac9e987487b645135d8f1d747f60a7e567d1b8f72327de8514d02b6f15d",
    }
    assert profile["actor"] == "stock-vllm-cli"
    assert sha256_hex(parser._snapshot_program(profile).encode()) == (
        "6fc43c6d64b5f183b20a24119ecf76a9a53dbe639dce4eda365835712f728bac"
    )
    other = "0.31.0" if version == "0.30.0" else "0.30.0"
    for fields in ({"version": other}, {"source_commit": "0" * 40}, {"name": "another-provider"}):
        candidate = env.model_copy(update={"engine": env.engine.model_copy(update=fields)})
        with pytest.raises(ValueError, match="SUP05_CONFIG_PARSER_SOURCE_UNQUALIFIED"):
            provider.evidence_contract("config_parser_source", candidate)
    model = env.model_copy(update={"model": env.model.model_copy(update={"inventory_sha256": "0" * 64})})
    with pytest.raises(ValueError, match="SUP05_CONFIG_PARSER_SOURCE_UNQUALIFIED"):
        parser._parser_profile(model)


def test_unconfigured_provider_does_not_open_parser_transport(monkeypatch):
    monkeypatch.setattr(parser, "get_provider", lambda _: get_provider("sglang"))
    monkeypatch.setattr(parser, "normal_startup_load_transport", lambda *args, **kwargs: pytest.fail("transport opened"))
    with pytest.raises(ValueError, match="SUP05_CONFIG_PARSER_SOURCE_UNQUALIFIED"):
        parser.parser_transport({}, parser_env("0.31.0"), duration=30)


@pytest.mark.parametrize("fault", [None, "other-check", "prebound", "legacy-mode", "prepared", "one-side"])
def test_parser_added_scope_requires_exact_paired_staged_sup05(tmp_path, fault):
    pack = load_pack(PACK)
    cfg = {"adapter": {"name": "vllm"}, "executors": {"supply_boot": "kubernetes"},
           "update": {"mode": "external", "allowed_checks": [CHECK], "kubernetes_candidate_binding": "after_baseline"},
           "check_config": {CHECK: {SUP05_CONFIG_PARSER_OPTION: True}},
           "sides": {side: {"supply_boot": {"target": str(tmp_path / side)}} for side in ("baseline", "candidate")}}
    if fault == "other-check":
        cfg["update"]["allowed_checks"].append("SUP-07")
    elif fault == "prebound":
        cfg["update"]["kubernetes_candidate_binding"] = "prebound"
    elif fault == "legacy-mode":
        cfg["check_config"][CHECK]["normal_file_reads_only"] = True
    elif fault == "prepared":
        cfg["sides"]["candidate"]["supply_boot"]["prepared"] = "unrelated"
    elif fault == "one-side":
        entry = cfg.pop("check_config")
        cfg["sides"]["candidate"]["check_config"] = entry
    effective = [for_side(cfg, side) for side in ("baseline", "candidate")]
    allowed = set(cfg["update"]["allowed_checks"])
    if fault:
        with pytest.raises(ValueError, match=NORMAL_FILE_READS_SCOPE_ERROR):
            bounded_metadata_scopes(effective, pack, allowed)
        return
    assert all(staged_observer_executors(item) == {"supply_boot"} for item in effective)
    assert len(bounded_metadata_scopes(effective, pack, allowed)) == 2
    assert [(p.name, deferred) for _, p, deferred in requested_input_paths(cfg)] == [
        ("baseline", False), ("candidate", True)]
    target = tmp_path / "baseline"
    target.write_text(json.dumps({"fixture": "bound"}))
    options = {SUP05_CONFIG_PARSER_OPTION: True, "target": str(target)}
    binding, values = run_binding.effective_values(pack.checks[CHECK], options, environment(), {}, side="baseline",
        attempt_id="attempt", issued_at=utcnow(), expected_target_sha256=sha256_hex(canonical_json({"fixture": "bound"})))
    assert list(binding["files"]) == ["supply_target_sha256"] and not values

def test_parser_requires_plan_approval_before_reading_target():
    pack = load_pack(PACK)
    options = {SUP05_CONFIG_PARSER_OPTION: True, "target": "/missing"}
    executor = parser.KubernetesNormalConfigParserExecutor(options)
    with pytest.raises(ValueError, match="SUP05_CONFIG_PARSER_APPROVAL_UNVERIFIED"):
        executor.run(pack.checks[CHECK], parser_env("0.31.0"), {}, None, None)
