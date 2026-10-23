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
