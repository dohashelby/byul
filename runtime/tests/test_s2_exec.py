"""Execution denial is explicit, scoped, bounded, and never confused with proof."""

import copy
import json
from pathlib import Path
import pytest

from serving_runtime.enforcement.intent import IntentError, parse_intent
from serving_runtime.enforcement.tetragon import render_manifests, render_monitor_manifests
from tools.tetragon_status import method_from_spec

ROOT = Path(__file__).resolve().parents[1]


def source():
    doc = json.loads((ROOT / "examples/enforcement/intent-v1.1.example.json").read_text())
    doc.update(
        suspect_binaries=["/bin/sh", "/usr/bin/bash"],
        exec_deny_binaries=["/bin/sh", "/usr/bin/bash"],
        exec_deny_exempt_callers=["/opt/test/nvcc"],
        tetragon_allowed_modes={"TG-EXEC-01": ["monitor", "enforce"]},
        tetragon_enforce_methods={"TG-EXEC-01": "lsm_bprm_check"},
    )
    return doc


def test_explicit_policy_and_selector_order():
    doc = source()
    intent = parse_intent(doc)
    p = render_manifests(intent)["tetragon-tg-exec-01.json"]
    assert p["spec"]["podSelector"]["matchLabels"] == intent.engine.labels
    assert p["spec"]["options"] == [{"name": "policy-mode", "value": "monitor"}]
    hook = p["spec"]["lsmhooks"][0]
    assert hook["hook"] == "bprm_check_security"
    assert hook["args"] == [{"index": 0, "type": "linux_binprm"}]
    assert "data" not in hook
    assert hook["selectors"][0] == {
        "matchBinaries": [{"operator": "In", "values": ["/opt/test/nvcc"]}],
        "matchActions": [{"action": "NoPost"}],
    }
    assert hook["selectors"][-1] == {
        "matchArgs": [{"index": 0, "operator": "Equal", "values": doc["exec_deny_binaries"]}],
        "matchActions": [{"action": "Post"}, {"action": "Override", "argError": -13}],
    }
    assert method_from_spec(p) == "lsm_bprm_check"
    assert render_monitor_manifests(intent)["tetragon-tg-exec-01.json"] == p


@pytest.mark.parametrize(
    "field,values",
    [
        ("exec_deny_binaries", []),
        ("exec_deny_binaries", ["/bin/sh"] * 2),
        ("exec_deny_binaries", ["/usr/bin"]),
        ("exec_deny_binaries", ["/usr/bin/"]),
        ("exec_deny_binaries", ["/"]),
        ("exec_deny_binaries", ["sh"]),
        ("exec_deny_binaries", ["/bin/../sh"]),
        ("exec_deny_binaries", ["/bin//sh"]),
        ("exec_deny_binaries", ["/bin/wget"]),
        ("exec_deny_binaries", ["/" + "a" * 255]),
        ("exec_deny_exempt_callers", ["/bin/sh"] * 2),
        ("exec_deny_exempt_callers", [f"/opt/tool{i}" for i in range(33)]),
    ],
)
def test_invalid_inputs_fail_closed(field, values):
    doc = source()
    doc[field] = values
    with pytest.raises(IntentError):
        parse_intent(doc)


def test_selector_capacity_and_empty_exemptions():
    doc = source()
    doc["exec_deny_exempt_callers"] = [f"/opt/tool{i}" for i in range(32)]
    selectors = render_manifests(parse_intent(doc))["tetragon-tg-exec-01.json"]["spec"]["lsmhooks"][0]["selectors"]
    assert len(selectors) == 5
    assert all(len(s["matchBinaries"][0]["values"]) == 8 for s in selectors[:-1])
    doc["exec_deny_exempt_callers"] = []
    selectors = render_manifests(parse_intent(doc))["tetragon-tg-exec-01.json"]["spec"]["lsmhooks"][0]["selectors"]
    assert len(selectors) == 1 and "matchArgs" in selectors[0]


def test_monitor_render_remains_adapter_only():
    doc = source()
    doc.pop("tetragon_enforce_methods")
    doc.pop("tetragon_allowed_modes")
    plain = copy.deepcopy(doc)
    for key in ("exec_deny_binaries", "exec_deny_exempt_callers"):
        plain.pop(key)
    assert render_manifests(parse_intent(doc)) == render_manifests(parse_intent(plain))
    doc["tetragon_allowed_modes"] = {"TG-EXEC-01": ["monitor"]}
    assert render_manifests(parse_intent(doc)) == render_manifests(parse_intent(plain))


def test_method_conflicts_and_unknown_rules_remain_rejected():
    doc = source()
    doc["tetragon_enforce_methods"]["TG-ASSET-01"] = "lsm_bprm_check"
    with pytest.raises(IntentError):
        parse_intent(doc)
    doc = source()
    doc["tetragon_enforce_methods"]["TG-SECRET-01"] = "lsm_file_open"
    doc["tetragon_enforce_method"] = "override_syscall"
    with pytest.raises(IntentError):
        parse_intent(doc)
