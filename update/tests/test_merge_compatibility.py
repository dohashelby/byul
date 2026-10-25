"""Read fixtures serialized by both parent branches, without needing Git history in CI."""
import json
from pathlib import Path
from guard.models import object_from_dict


def test_both_parent_serializations_keep_hashes_and_all_fields():
    fixture = json.loads((Path(__file__).parent / "fixtures/merge_compatibility.json").read_text(encoding="utf-8"))
    assert set(fixture) == {"main-d9e9729", "update-42a609d"}
    for objects in fixture.values():
        assert {o["kind"] for o in objects} >= {"ENV","POL","PLAN","EVD","RES","RPT"}
        for raw in objects:
            obj = object_from_dict(raw)
            assert obj.hash_ok(), (raw["kind"], raw["id"])
            assert obj.to_dict() == raw
