"""WP5: a pre-extraction, field-by-field contract for the strict intent parser."""

import copy
from dataclasses import asdict
import itertools
import json
from pathlib import Path

import pytest

from serving_runtime.enforcement.intent import parse_intent


def base_document(v11=False):
    document = {
        "schema": "runtime-enforcement-intent/1.1" if v11 else "runtime-enforcement-intent/1.0",
        "namespace": "test-ns",
        "gateway": {"labels": {"app": "test-gateway"}},
        "engine": {"labels": {"app": "test-engine"}, "port": 8000},
        "metrics_collectors": [{"labels": {"app": "test-metrics"}}],
        "dns": {"namespace": "test-dns", "labels": {"app": "test-dns"}, "port": 53},
        "engine_egress_allow": [{"fqdn": "engine.example", "port": 443, "protocol": "TCP"}],
        "protected_paths": {
            "model_dir": "/srv/test/model",
            "cache_dir": "/srv/test/cache",
            "secret_paths": ["/srv/test/secrets/token.json"],
        },
        "allowed_engine_binaries": ["/opt/test/bin/engine"],
        "tetragon_mode": "monitor",
        "probe_path": "/srv/test/probe",
    }
    if v11:
        document["engine"]["container_name"] = "test-engine"
        document["engine"]["management_port"] = 8001
        document["protected_paths"]["enforce_exact_files"] = ["/srv/test/secrets/token.json"]
        document.update(
            acquire={
                "labels": {"app": "test-acquire"},
                "egress_allow": [
                    {"fqdn": "download.example", "port": 443, "protocol": "TCP"},
                    {"endpoint": {"app": "test-receiver"}, "port": 9000, "protocol": "UDP"},
                ],
            },
            test_receivers=[
                {
                    "name": "test-receiver",
                    "labels": {"app": "test-receiver"},
                    "port": 9000,
                    "protocol": "UDP",
                    "allowed_from": ["acquire", "observer"],
                }
            ],
            observer_kube_api=True,
            observer_kube_api_port=7443,
            assets={
                "tokenizer_dir": "/srv/test/tokenizer",
                "manifest_path": "/srv/test/manifests/approved.json",
                "cache_root": "/srv/test/host-cache",
                "cache_mount_path": "/srv/test/cache",
                "cache_generation": {
                    "engine": "engine",
                    "image_id": "image",
                    "model_revision": "revision",
                    "compile_profile": "profile",
                },
                "engine_uid": 1001,
                "acquire_uid": 1002,
                "probe_uid": 1003,
            },
            phase_binaries={
                "loading": ["/opt/test/bin/loader"],
                "compiling": ["/opt/test/bin/compiler"],
                "inferring": ["/opt/test/bin/engine"],
            },
            suspect_binaries=["/opt/test/bin/suspect"],
            parent_constraints={"/opt/test/bin/engine": ["/opt/test/bin/parent"]},
            suspect_parent_contexts={
                "/opt/test/bin/suspect": {"parents": ["/opt/test/bin/parent"], "phases": ["inferring"]}
            },
            tetragon_rule_modes={"TG-SECRET-01": "enforce", "TG-ASSET-01": "monitor"},
            tetragon_allowed_modes={"TG-SECRET-01": ["monitor", "enforce"], "TG-ASSET-01": ["monitor"]},
            tetragon_enforce_method="override_syscall",
            tetragon_openat_symbol="__x64_sys_openat",
            tetragon_kernel_identity=True,
            code_load_watch_paths=["/srv/test/code"],
        )
    return document


def field_paths(value, prefix=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield prefix + (key,)
            yield from field_paths(child, prefix + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield prefix + (index,)
            yield from field_paths(child, prefix + (index,))


def changed(document, path, value=None, delete=False):
    result = copy.deepcopy(document)
    owner = result
    for key in path[:-1]:
        owner = owner[key]
    if delete:
        del owner[path[-1]]
    else:
        owner[path[-1]] = copy.deepcopy(value)
    return result


def corpus():
    cases = {}
    for version in (False, True):
        base = base_document(version)
        tag = "v11" if version else "v10"
        cases[tag + ":valid"] = base
        for path in field_paths(base):
            original = base
            for key in path:
                original = original[key]
            variations = {
                "null": None,
                "bool": True,
                "integer": 0,
                "empty-string": "",
                "unknown": "?",
                "empty-list": [],
                "empty-object": {},
            }
            if isinstance(original, list) and original:
                variations["duplicate"] = original + original[:1]
            if isinstance(original, dict):
                variations["unknown-field"] = {**original, "unexpected": 1}
            key = tag + ":" + ".".join(map(str, path))
            cases[key + ":deleted"] = changed(base, path, delete=True)
            for mutation, value in variations.items():
                if value != original or type(value) is not type(original):
                    cases[key + ":" + mutation] = changed(base, path, value)
    minimal = base_document(False)
    minimal["schema"] = "runtime-enforcement-intent/1.1"
    cases["v11:minimal"] = minimal
    for key in set(base_document(True)) - set(base_document(False)):
        cases["v10:forbidden:" + key] = {**base_document(False), key: base_document(True)[key]}
    invalid = [
        ("schema",),
        ("namespace",),
        ("gateway",),
        ("engine",),
        ("metrics_collectors",),
        ("dns",),
        ("engine_egress_allow",),
        ("protected_paths",),
        ("allowed_engine_binaries",),
        ("tetragon_mode",),
        ("probe_path",),
    ]
    for first, second in itertools.combinations(invalid, 2):
        cases["priority:" + first[0] + "+" + second[0]] = changed(
            changed(base_document(False), first, "?"), second, "?"
        )
    extras = {
        "probe-required": [(("tetragon_mode",), "enforce_probe"), (("probe_path",), None)],
        "probe-valid": [(("tetragon_mode",), "enforce_probe")],
        "exact-not-secret": [(("protected_paths", "enforce_exact_files"), ["/srv/test/other"])],
        "exact-duplicate": [(("protected_paths", "enforce_exact_files"), ["/srv/test/secrets/token.json"] * 2)],
        "rule-other-enforce": [(("tetragon_rule_modes", "TG-ASSET-01"), "enforce")],
        "allowed-other-enforce": [(("tetragon_allowed_modes", "TG-ASSET-01"), ["monitor", "enforce"])],
        "symbol-wrong-method": [(("tetragon_enforce_method",), "override_lsm")],
        "mode-conflict": [(("tetragon_rule_modes", "TG-SECRET-01"), "monitor")],
        "mode-no-method": [(("tetragon_enforce_method",), None)],
        "method-unsupported": [(("tetragon_enforce_method",), "unsupported")],
        "secret-empty": [(("protected_paths", "enforce_exact_files"), [])],
        "secret-prefix": [
            (("protected_paths", "secret_paths"), ["/srv/test/a", "/srv/test/a/b"]),
            (("protected_paths", "enforce_exact_files"), ["/srv/test/a", "/srv/test/a/b"]),
        ],
        "secret-slash": [
            (("protected_paths", "secret_paths"), ["/srv/test/a/"]),
            (("protected_paths", "enforce_exact_files"), ["/srv/test/a/"]),
        ],
        "acquire-same": [(("acquire", "labels"), {"app": "test-engine"})],
        "acquire-shared": [(("engine_egress_allow",), [{"fqdn": "DOWNLOAD.EXAMPLE.", "port": 443, "protocol": "TCP"}])],
        "receiver-bad-name": [(("test_receivers", 0, "name"), "Bad_Name")],
        "receiver-no-acquire": [(("test_receivers", 0, "allowed_from"), ["acquire"])],
        "kube-port-disabled": [(("observer_kube_api",), False)],
        "uid-duplicate": [(("assets", "probe_uid"), 1001)],
        "cache-mismatch": [(("protected_paths", "cache_dir"), "/srv/test/other")],
        "mount-overlap": [
            (("assets", "cache_mount_path"), "/srv/test/model"),
            (("protected_paths", "cache_dir"), "/srv/test/model"),
        ],
        "suspect-overlap": [(("suspect_binaries",), ["/opt/test/bin/engine"])],
        "unknown-parent": [(("parent_constraints",), {"/opt/test/bin/unknown": ["/opt/test/bin/parent"]})],
        "unknown-suspect-context": [(("suspect_parent_contexts",), {"/opt/test/bin/unknown": {}})],
        "roles-overlap": [(("metrics_collectors", 0, "labels"), {"app": "test-gateway"})],
        "namespace-label": [(("namespace",), "Bad_Name")],
        "selector-label": [(("gateway", "labels"), {"bad key": "bad value"})],
        "code-root": [(("code_load_watch_paths",), ["/"])],
        "code-long": [(("code_load_watch_paths",), ["/" + "x" * 255])],
        "code-count": [(("code_load_watch_paths",), ["/test/" + str(i) for i in range(9)])],
        "code-control": [(("code_load_watch_paths",), ["/test/\n"])],
    }
    for name, edits in extras.items():
        document = base_document(True)
        if name == "receiver-no-acquire":
            del document["acquire"]
        for path, value in edits:
            document = changed(document, path, value)
        cases["interaction:" + name] = document
    for method in ("lsm_file_open", "override_lsm", "sigkill_fd_install"):
        document = base_document(True)
        document["tetragon_enforce_method"] = method
        del document["tetragon_openat_symbol"]
        cases["method:" + method] = document
    for mode in ("monitor", "enforce"):
        document = copy.deepcopy(minimal)
        document["tetragon_rule_modes"] = {"TG-SECRET-01": mode}
        cases["legacy:" + mode] = document
    for overlap in ("none", "model", "manifest"):
        document = base_document(True)
        del document["assets"]["cache_mount_path"]
        root = {"none": "/srv/test/host-cache", "model": "/srv/test/model", "manifest": "/srv/test/manifests"}[overlap]
        document["assets"]["cache_root"] = root
        document["protected_paths"]["cache_dir"] = root + "/engine/image/revision/profile"
        cases["unmounted:" + overlap] = document
    document = base_document(False)
    document["protected_paths"]["enforce_exact_files"] = []
    cases["v10:exact-files"] = document
    document = copy.deepcopy(minimal)
    document["tetragon_openat_symbol"] = "__x64_sys_openat"
    cases["method-without-enforce"] = document
    for value in (None, [], "?", 1):
        cases["root:" + repr(value)] = value
    return dict(sorted(cases.items()))


def outcome(document):
    try:
        result = {"value": asdict(parse_intent(document))}
    except Exception as error:
        result = {
            "error": type(error).__name__,
            "message": str(error),
            "cause": None if error.__cause__ is None else [type(error.__cause__).__name__, str(error.__cause__)],
        }
    return json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


CASES = corpus()
GOLDEN = json.loads((Path(__file__).parent / "data/wp5-intent-golden.json").read_text())


@pytest.mark.parametrize("case", CASES)
def test_wp5_intent_contract(case):
    assert outcome(CASES[case]) == GOLDEN["outcomes"][GOLDEN["cases"][case]]
