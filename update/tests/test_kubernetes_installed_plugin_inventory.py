"""SUP-10 exercises the exact isolated reader on EXAMPLE metadata in Linux CI."""

import json
from pathlib import Path
import os
import sys
import sysconfig

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.run import _normalize
from guard.executors import kubernetes_installed_plugin_inventory as inventory
from guard.executors.kubernetes_installed_plugin_inventory import KubernetesInstalledPluginInventoryExecutor
from guard.executors.plugin_loading import GROUPS
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import capture_pipes
from tests.builders import evidence
from tests.test_gpu_a_run_inputs import UPDATE
from tests.test_kubernetes_sampling import World, normal_sampling_inputs


def _venv_path(prefix, key):
    return Path(sysconfig.get_path(key, scheme="venv", vars={"base": str(prefix), "platbase": str(prefix)}))


def _installed_fixture(prefix, version, fault):
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True)
    interpreter = bin_dir / "python3"
    interpreter.symlink_to(Path(sys.executable).resolve())
    (prefix / "pyvenv.cfg").write_text(
        f"home = {Path(sys.executable).resolve().parent}\ninclude-system-site-packages = false\n")
    if fault == "missing-root":
        return interpreter
    pure, plat = (_venv_path(prefix, key) for key in ("purelib", "platlib"))
    for path in {pure, plat}:
        path.mkdir(parents=True)
    lib = pure

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
        elif fault in {"at-list-limit", "many-entries"}:
            count = 15 if fault == "at-list-limit" else 16
            entries = [f"private-member-{n} = private_fixture:activate" for n in range(count)]
            (first / "entry_points.txt").write_text(f"[{GROUPS[0]}]\n" + "\n".join(entries) + "\n")
    (lib / "private_fixture.py").write_text("raise AssertionError('the metadata reader must not import entry points')\n")
    (lib / "fixture.pth").write_text("import private_fixture\n")
    return interpreter


@pytest.mark.parametrize("version,fault", [
    ("0.30.0", None), ("0.31.0", "empty-groups"), ("0.30.0", "wrong-version"),
    ("0.31.0", "duplicate-version"), ("0.30.0", "symlink"), ("0.31.0", "ambiguous"),
    ("0.31.0", "owner-changed"), ("0.31.0", "missing-root"), ("0.30.0", "missing-engine"),
    ("0.31.0", "at-list-limit"), ("0.31.0", "many-entries"), ("0.30.0", "inconsistent-entry"),
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
            if fault == "inconsistent-entry":
                inventory_row = json.loads(capture["stdout_bytes"])
                inventory_row["groups"][0]["entry_fingerprints"][0] = "0" * 64
                capture["stdout_bytes"] = canonical_json(inventory_row)
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

    collected = fault in {None, "empty-groups", "at-list-limit", "many-entries"}
    assert len(dispatches) == 1 and not world.execs
    assert child_stderr == [b""]
    assert draft.status == (ExecStatus.COMPLETED if collected else ExecStatus.ABORTED)
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert draft.normal_control.attempted is False
    assert [step.attempted for step in draft.challenges] == [collected, False, False]
    raw = draft.artifacts[0].path.read_text()
    assert all(text not in raw for text in ("private-entry", "private-member", "private_fixture", "private-venv", "private-distribution", *GROUPS))
    assert not list((tmp_path / "private-venv").rglob("*.pyc"))
    doc = json.loads(raw)
    if collected:
        sample = doc["observed_session"]["inventory"]
        assert sample["engine_distribution_sha256"] == sha256_hex(canonical_json(["vllm", version]))
        assert [row["group_sha256"] for row in sample["groups"]] == [sha256_hex(canonical_json(group)) for group in GROUPS]
        entries = []
        if fault != "empty-groups":
            for name, revision in (("private-distribution-one", "1.0"), ("private-distribution-two", "2.0")):
                provider = sha256_hex(canonical_json([name, revision]))
                names = ["private-entry"]
                if fault in {"at-list-limit", "many-entries"} and revision == "1.0":
                    count = 15 if fault == "at-list-limit" else 16
                    names = [f"private-member-{n}" for n in range(count)]
                entries.extend(sha256_hex(canonical_json([provider, GROUPS[0], item, "private_fixture:activate"])) for item in names)
        assert [row["entry_count"] for row in sample["groups"]] == [len(entries), 0, 0, 0]
        assert [row["entries_sha256"] for row in sample["groups"]] == [sha256_hex(canonical_json(sorted(entries))),
            *([sha256_hex(canonical_json([]))] * 3)]
        assert [row["entry_fingerprints"] for row in sample["groups"]] == [
            None if fault == "many-entries" else sorted(entries), [], [], []]
    else:
        assert doc["observed_session"] is None and "installed_metadata_inventory" in doc["missing"]
    observations = _normalize(check, draft)["observations"]
    assert observations["supply_boot_sessions"]["collected"] is collected
    assert observations["supply_boot_complete"]["value"] is False
    assert not observations["supply_boot_normal_verified"]["collected"] and not observations["supply_boot_violations"]["collected"]
    evd = evidence(check_id=check.id, observations=observations, normal=draft.normal_control.model_dump())
    assert RuleTableJudge().judge(check, evd, check.judge.params, {}).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("members", [None, [], ["a" * 64, "c" * 64], ["b" * 64, "a" * 64], [1, 2]])
def test_sup10_rejects_incomplete_or_inconsistent_entry_identity_list(members):
    group_digest = sha256_hex(canonical_json(["a" * 64, "b" * 64]))
    assert not inventory._valid_entry_fingerprints({
        "entry_count": 2, "entries_sha256": group_digest, "entry_fingerprints": members})


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
@pytest.mark.parametrize("settings,changed_pin,accepted", [
    ({}, False, True),
    ({"max_duration_seconds": 12.5, "kubectl_binary": "example-kubectl"}, False, True),
    ({"max_duration_seconds": 0}, False, False),
    ({"kubectl_binary": "  "}, False, False),
    ({}, True, False),
])
def test_sup10_prepares_the_saved_target_without_collecting(
        tmp_path, monkeypatch, version, settings, changed_pin, accepted):
    from tools.mvp_versions import versions

    env, target, _, path = normal_sampling_inputs(tmp_path, version)
    image = next(item["image"] for item in versions().values() if item["version"] == version)
    env = env.model_copy(update={"engine": env.engine.model_copy(update={"image_digest": image})}).seal()
    target["environment_ref"] = env.ref().model_dump(mode="json")
    target["engine"]["image_digest"] = image
    target["kubernetes"]["engine"].update(image=image, image_id=image)
    path.write_bytes(canonical_json(target))
    digest = sha256_hex(path.read_bytes())
    expected = sha256_hex(b"different-owned-target") if changed_pin else digest
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["SUP-10"]

    def no_collection(*args, **kwargs):
        pytest.fail("SUP-10 preparation must not collect inventory")

    monkeypatch.setattr(inventory, "_inventory", no_collection)
    executor = KubernetesInstalledPluginInventoryExecutor({"installed_plugin_inventory_only": True,
        "target": str(path), **settings}, runner=no_collection, expected_target_sha256=digest if changed_pin else None)
    if accepted:
        executor.prepared_installed_plugin_inventory(check, env, {}, expected_target_sha256=expected)
    else:
        with pytest.raises(ValueError, match="^SUP10_INVENTORY_TARGET_UNVERIFIED$"):
            executor.prepared_installed_plugin_inventory(check, env, {}, expected_target_sha256=expected)


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


def test_sup10_rejects_metadata_changed_after_individual_read(tmp_path, monkeypatch, capsys):
    version = "0.31.0"
    prefix = tmp_path / "private-venv"
    _installed_fixture(prefix, version, None)
    root = _venv_path(prefix, "purelib")
    distribution = root / f"vllm-{version}.dist-info"
    source = distribution / "METADATA"
    selected = distribution.stat()

    scope = {}
    with monkeypatch.context() as patch:
        patch.setattr(sys, "argv", ["installed-metadata-standalone-reader"])
        exec(compile(inventory._READER, "installed metadata reader", "exec"), scope)
    assert json.loads(capsys.readouterr().out)["unavailable"] is True
    original_read = scope["bounded_text"]
    changed = []

    def read_then_change(fd, name, ceiling, **kwargs):
        result = original_read(fd, name, ceiling, **kwargs)
        if name == "entry_points.txt" and os.fstat(fd).st_ino == selected.st_ino:
            source.write_text("Metadata-Version: 2.1\nName: vllm\nVersion: 0.31.100\n\n")
            current = distribution.stat()
            assert (current.st_mtime_ns, current.st_ctime_ns) == (selected.st_mtime_ns, selected.st_ctime_ns)
            changed.append(name)
        return result

    scope["bounded_text"] = read_then_change
    with pytest.raises(ValueError):
        scope["scan"]([root], sha256_hex(canonical_json("vllm")), sha256_hex(canonical_json(["vllm", version])))
    assert changed == ["entry_points.txt"]
