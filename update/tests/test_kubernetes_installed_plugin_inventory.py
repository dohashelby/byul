"""SUP-10 exercises the exact isolated reader on EXAMPLE metadata in Linux CI."""

import json
from pathlib import Path
import sys
import sysconfig

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.run import _normalize
from guard.executors.kubernetes_installed_plugin_inventory import KubernetesInstalledPluginInventoryExecutor
from guard.executors.plugin_loading import GROUPS
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import capture_pipes
from tests.builders import evidence
from tests.test_gpu_a_run_inputs import UPDATE
from tests.test_kubernetes_sampling import World, normal_sampling_inputs


def _installed_fixture(prefix, version, fault):
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True)
    interpreter = bin_dir / "python3"
    interpreter.symlink_to(Path(sys.executable).resolve())
    (prefix / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    lib = Path(sysconfig.get_path("purelib", vars={"base": str(prefix), "platbase": str(prefix)}))
    if fault == "missing-root":
        return interpreter
    lib.mkdir(parents=True)

    def dist(name, revision, entries=None):
        metadata = lib / f"{name}-{revision}.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {revision}\n\n")
        if entries is not None:
            (metadata / "entry_points.txt").write_text(entries)
        return metadata

    if fault != "missing-engine":
        dist("vllm", "0.0.0" if fault == "wrong-version" else version)
    if fault == "duplicate-version":
        dist("vllm", "0.0.0")
    if fault != "empty-groups":
        entries = f"[{GROUPS[0]}]\nprivate-entry = private_fixture:activate\n[console_scripts]\nprivate-cli = private_fixture:cli\n"
        first = dist("private-distribution-one", "1.0", entries)
        dist("private-distribution-two", "2.0", entries)
        if fault == "symlink":
            points = first / "entry_points.txt"
            original = points.with_name("alternate.txt")
            points.rename(original)
            points.symlink_to(original.name)
        elif fault == "ambiguous":
            (first / "entry_points.txt").write_text(f"[{GROUPS[0]}]\nentry = one:fn\nentry = two:fn\n")
    (lib / "private_fixture.py").write_text("raise AssertionError('the metadata reader must not import entry points')\n")
    (lib / "fixture.pth").write_text("import private_fixture\n")
    return interpreter


@pytest.mark.parametrize("version,fault", [
    ("0.30.0", None), ("0.31.0", "empty-groups"), ("0.30.0", "wrong-version"),
    ("0.31.0", "duplicate-version"), ("0.30.0", "symlink"), ("0.31.0", "ambiguous"),
    ("0.31.0", "owner-changed"), ("0.31.0", "missing-root"), ("0.30.0", "missing-engine"),
])
def test_sup10_exact_isolated_reader_reports_only_bound_installation_metadata(tmp_path, version, fault):
    interpreter = _installed_fixture(tmp_path / "private-venv", version, fault)
    env, target, _, path = normal_sampling_inputs(tmp_path, version)
    path.write_bytes(canonical_json(target))
    dispatches = []
    child_stderr = []

    class InstalledWorld(World):
        def runner(self, argv, **kwargs):
            if "exec" not in argv:
                return super().runner(argv, **kwargs)
            assert "-i" not in argv and "--container=engine" in argv and "input_bytes" not in kwargs
            command = argv[argv.index("--") + 1:]
            assert command[:5] == ["python3", "-I", "-S", "-B", "-c"]
            assert command[-2:] == [sha256_hex(canonical_json("vllm")), sha256_hex(canonical_json(["vllm", version]))]
            dispatches.append(command)
            self.after_exec = True
            capture = capture_pipes([str(interpreter), *command[1:]], **kwargs)
            child_stderr.append(capture["stderr_bytes"])
            return capture

    def owned_api(role, document, after):
        if role == "namespaces":
            for item in document["items"]:
                item["kind"] = "Namespace"
        else:
            document["kind"] = "Pod"
            if fault == "owner-changed" and role == "engine" and after:
                document["metadata"]["uid"] = "replacement"

    world = InstalledWorld(target)
    world.mutate = owned_api
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["SUP-10"]
    executor = KubernetesInstalledPluginInventoryExecutor({"installed_plugin_inventory_only": True, "target": str(path)},
        runner=world.runner, expected_target_sha256=sha256_hex(path.read_bytes()))
    draft = executor.run(check, env, {}, VllmAdapter(), tmp_path / "work")

    collected = fault in {None, "empty-groups"}
    assert len(dispatches) == 1 and not world.execs
    assert child_stderr == [b""]
    assert draft.status == (ExecStatus.COMPLETED if collected else ExecStatus.ABORTED)
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert draft.normal_control.attempted is False
    assert [step.attempted for step in draft.challenges] == [collected, False, False]
    raw = draft.artifacts[0].path.read_text()
    assert all(text not in raw for text in ("private-entry", "private_fixture", "private-venv", "private-distribution", *GROUPS))
    assert not list((tmp_path / "private-venv").rglob("*.pyc"))
    doc = json.loads(raw)
    if collected:
        sample = doc["observed_session"]["inventory"]
        assert sample["engine_distribution_sha256"] == sha256_hex(canonical_json(["vllm", version]))
        assert [row["group_sha256"] for row in sample["groups"]] == [sha256_hex(canonical_json(group)) for group in GROUPS]
        assert [row["entry_count"] for row in sample["groups"]] == ([2, 0, 0, 0] if fault is None else [0, 0, 0, 0])
        entries = []
        if fault is None:
            for name, revision in (("private-distribution-one", "1.0"), ("private-distribution-two", "2.0")):
                provider = sha256_hex(canonical_json([name, revision]))
                entries.append(sha256_hex(canonical_json([provider, GROUPS[0], "private-entry", "private_fixture:activate"])))
        assert [row["entries_sha256"] for row in sample["groups"]] == [sha256_hex(canonical_json(sorted(entries))),
            *([sha256_hex(canonical_json([]))] * 3)]
    else:
        assert doc["observed_session"] is None and "installed_metadata_inventory" in doc["missing"]
    observations = _normalize(check, draft)["observations"]
    assert observations["supply_boot_sessions"]["collected"] is collected
    assert observations["supply_boot_complete"]["value"] is False
    assert not observations["supply_boot_normal_verified"]["collected"] and not observations["supply_boot_violations"]["collected"]
    evd = evidence(check_id=check.id, observations=observations, normal=draft.normal_control.model_dump())
    assert RuleTableJudge().judge(check, evd, check.judge.params, {}).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("fault", ["unbound", "disabled", "foreign-check"])
def test_sup10_validates_exact_scope_before_transport(tmp_path, fault):
    env, target, _, path = normal_sampling_inputs(tmp_path, "0.30.0")
    path.write_bytes(canonical_json(target))
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["SUP-09" if fault == "foreign-check" else "SUP-10"]

    def no_remote_calls(*args, **kwargs):
        raise AssertionError("SUP-10 must validate its scope and target before any API or exec call")

    executor = KubernetesInstalledPluginInventoryExecutor({"installed_plugin_inventory_only": fault != "disabled",
        "target": str(path)}, runner=no_remote_calls,
        expected_target_sha256=None if fault == "unbound" else sha256_hex(path.read_bytes()))
    with pytest.raises(ValueError, match="SUP10_INVENTORY_(?:SCOPE_INVALID|TARGET_UNVERIFIED)"):
        executor.run(check, env, {}, VllmAdapter(), tmp_path / "work")
