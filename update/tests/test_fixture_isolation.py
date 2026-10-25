"""Fixture optimizations must not share mutable policy or object state."""
from tests.builders import baseline_pack
from tests.update_builders import build_pure_update_case


def test_cached_pack_seed_returns_independent_nested_models():
    first = baseline_pack()
    expected = first.ref()
    first.checks["REQ-01"].challenge_steps.clear()
    first.checks["REQ-01"].judge.params.clear()
    first.manifest.files_sha256.clear()
    second = baseline_pack()
    assert second.ref() == expected
    assert second.checks["REQ-01"].challenge_steps and second.checks["REQ-01"].judge.params
    assert second.manifest.files_sha256


def test_pure_update_graph_uses_no_store_or_pack_copy(tmp_path, monkeypatch):
    from tests import update_builders
    def forbidden(*args, **kwargs):
        raise AssertionError("pure graph must not create a Store or copy pack bytes")
    monkeypatch.setattr(update_builders, "Store", forbidden)
    monkeypatch.setattr(update_builders, "put_pack", forbidden)
    report, objects, pack = build_pure_update_case(tmp_path)
    assert not list(tmp_path.iterdir())
    assert len([obj for obj in objects if obj["kind"] == "EVD"]) == 10
    assert len([obj for obj in objects if obj["kind"] == "RES"]) == 10
    assert report["inputs_manifest"] and not report["official"]
    objects[0]["engine"]["version"] = "EXAMPLE tampered test instance"
    _, other, fresh_pack = build_pure_update_case(tmp_path)
    assert other[0]["engine"]["version"] != objects[0]["engine"]["version"]
    assert fresh_pack.ref() == pack.ref()


def _prepared_store(folder):
    from guard.core.select import select
    from guard.store import Store
    from tests.builders import environment, policy
    from tests.prepared_fixtures import put_baseline_snapshot

    pack = baseline_pack()
    store = Store(folder / "store")
    put_baseline_snapshot(store)
    env = environment()
    pol = policy(pack.ref(), scope={"include": ["REQ-01"]})
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    attachment = store.put_artifact(b"EXAMPLE immutable seed attachment\r\n")
    return store, plan, {"attachment": attachment.path, "notes": ["EXAMPLE pristine"]}


def test_prepared_seed_clones_database_artifacts_and_mutable_metadata(tmp_path):
    from guard.store import Store
    from tests.prepared_fixtures import manual_setup_seed

    calls = []
    def create(folder):
        calls.append(folder)
        return _prepared_store(folder)

    parameters = {"pack": baseline_pack().model_dump(mode="json"), "mode": "audit"}
    first, plan, metadata = manual_setup_seed("isolation-clones", parameters, tmp_path / "first", create)
    expected_plan = plan.to_dict()
    artifact = first.root / metadata["attachment"]
    expected_bytes = artifact.read_bytes()
    assert first.verify() == []
    artifact.write_bytes(b"EXAMPLE deliberately damaged first copy")
    plan.items.clear()
    metadata["notes"].append("EXAMPLE changed")
    first.put_artifact(b"EXAMPLE only in first clone")
    second, other_plan, other_metadata = manual_setup_seed("isolation-clones", parameters, tmp_path / "second", create)
    assert len(calls) == 1
    assert other_plan.to_dict() == expected_plan
    assert other_metadata["notes"] == ["EXAMPLE pristine"]
    assert (second.root / other_metadata["attachment"]).read_bytes() == expected_bytes
    assert not first.db_path.samefile(second.db_path)
    assert not artifact.samefile(second.root / other_metadata["attachment"])
    assert second.verify() == []
    assert len(list((first.root / "artifacts").iterdir())) == len(list((second.root / "artifacts").iterdir())) + 1
    assert Store(second.root).resolve(other_plan.ref()).to_dict() == expected_plan


def test_prepared_seed_key_includes_full_input_data_beyond_pack_ref(tmp_path):
    from copy import deepcopy
    from tests.prepared_fixtures import manual_setup_seed

    pack = baseline_pack()
    base = {"pack": pack.model_dump(mode="json"), "scope": ["REQ-01"],
            "policy": {"limit": 1}, "mode": "audit", "attachment": "EXAMPLE A"}
    variants = [base]
    for field, value in (("scope", ["REQ-03"]), ("policy", {"limit": 2}),
                         ("mode", "update"), ("attachment", "EXAMPLE B")):
        changed = deepcopy(base)
        changed[field] = value
        variants.append(changed)
    changed_pack = deepcopy(pack)
    changed_pack.checks["SUP-01"].required_observations_prose.append("EXAMPLE outside selected scope")
    assert changed_pack.ref() == pack.ref()
    changed = deepcopy(base)
    changed["pack"] = changed_pack.model_dump(mode="json")
    variants.append(changed)
    calls = []
    def create(folder):
        calls.append(folder)
        return _prepared_store(folder)

    for index, parameters in enumerate(variants):
        manual_setup_seed("isolation-key", parameters, tmp_path / str(index), create)
    manual_setup_seed("isolation-key", deepcopy(base), tmp_path / "again", create)
    assert len(calls) == len(variants)


def test_prepared_pack_copy_rejects_and_never_repairs_corrupt_destination(tmp_path):
    import pytest
    from guard.store import IntegrityError, Store
    from tests.prepared_fixtures import put_baseline_snapshot

    store = Store(tmp_path / "store")
    ref = put_baseline_snapshot(store)
    manifest = store.root / "artifacts" / ref.sha256
    manifest.write_bytes(b"EXAMPLE corrupt stored manifest")
    before = manifest.read_bytes()
    with pytest.raises(IntegrityError):
        put_baseline_snapshot(store)
    assert manifest.read_bytes() == before


def test_frozen_tree_clones_private_files_without_changing_source(tmp_path):
    from tests.prepared_fixtures import copy_frozen_tree

    source = tmp_path / "source"
    source.mkdir()
    (source / "evidence.txt").write_bytes(b"EXAMPLE original\r\n")
    first, second = tmp_path / "first", tmp_path / "second"
    copy_frozen_tree(source, first)
    (first / "evidence.txt").write_bytes(b"EXAMPLE changed copy")
    copy_frozen_tree(source, second)
    assert (source / "evidence.txt").read_bytes() == b"EXAMPLE original\r\n"
    assert (second / "evidence.txt").read_bytes() == b"EXAMPLE original\r\n"
    assert not (source / "evidence.txt").samefile(first / "evidence.txt")
    assert not (source / "evidence.txt").samefile(second / "evidence.txt")


def test_fixture_yaml_writer_preserves_default_bytes_and_unicode_values():
    import yaml
    from tests.prepared_fixtures import dump_fixture_yaml

    doc = {"EXAMPLE": ["보존할 수동 증거 " * 25, "EXAMPLE colon: newline\nnext"]}
    assert dump_fixture_yaml(doc, sort_keys=False) == yaml.safe_dump(doc, sort_keys=False)
    assert yaml.safe_load(dump_fixture_yaml(doc, allow_unicode=True, sort_keys=False)) == doc
