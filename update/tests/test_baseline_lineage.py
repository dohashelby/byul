"""Joint baseline: pinned framework, prior runtime contracts and exact historical Refs."""
import hashlib
import json
from pathlib import Path

import pytest

from guard.models import load_pack
from guard.store import Store
from guard.store.packs import get_pack, put_pack, PackNotStoredError
from tests.pack_lineage_support import historical_pack

ROOT = Path(__file__).resolve().parents[1]
NEW_DEFINITIONS = {"PLT-07", "PLT-08", "SUP-13"}
CURRENT_REF = {
    "kind": "PACK", "id": "PACK-BASELINE", "revision": 31,
    "sha256": "54f4f2357533a8b201ad4397d4b182b08b89ee4821ffe67763e3fbecd8f369ac",
}


def test_latest_criteria_and_exact_pack_ref_are_selected():
    raw = (ROOT / "reference/framework/data/checklist.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "745e18640bc0c405baee1883dee53d1f35b644f0bc6116705a880b91893c590f"
    source = json.loads(raw)
    rows = [r for area in ("supply","request_state","platform","lifecycle","observability")
            for r in source[area + "_assessment_draft"]]
    assert len(rows) == len({r["id"] for r in rows}) == 77
    assert sum(len(r["test_branch_conditions"]) for r in rows) == 116
    assert sum(len(r.get("required_subresults", [])) for r in rows) == 9
    pack = load_pack(ROOT / "reference/pack-seeds/PACK-BASELINE-31")
    assert pack.ref().model_dump(mode="json") == CURRENT_REF
    assert not pack.reviewed and not any(c.retired for c in pack.checks.values())
    for row in rows:
        _, area, number = row["id"].split("-")
        check = pack.checks[f"{area}-{int(number):02d}"]
        assert check.framework_id == row["id"] and check.question == row["question"]


def test_current_definitions_keep_both_lineages_without_revision_reuse(tmp_path):
    main = historical_pack("main29", tmp_path / "main29")
    prior = historical_pack("prior30", tmp_path / "prior30")
    current = load_pack(ROOT / "reference/pack-seeds/PACK-BASELINE-31")
    changed = set()
    for cid, check in current.checks.items():
        new = check.model_dump(mode="json")
        before = prior.checks[cid].model_dump(mode="json")
        for key in ("executor","judge","policy_params","required_observations","functional_probes",
                    "route_scope","functional_scope","collection_profile"):
            assert new.get(key) == before.get(key), (cid,key)
        if (current.path / "checks" / (cid + ".yaml")).read_bytes() != (
                main.path / "checks" / (cid + ".yaml")).read_bytes():
            changed.add(cid)
            assert check.revision == max(main.checks[cid].revision, prior.checks[cid].revision) + 1
        else:
            assert check.revision == main.checks[cid].revision
    assert changed == NEW_DEFINITIONS
    assert {c.id for c in current.checks.values() if c.executor} == {
        "SUP-03","SUP-04","SUP-13","REQ-01","REQ-03","REQ-07","REQ-09","REQ-10",
        "REQ-11","REQ-13","PLT-01","PLT-02","PLT-04","PLT-07","PLT-08","OBS-03","OBS-09",
    }
    # Latest framework explicitly retains the unimplemented schema/tool scope.
    assert current.checks["REQ-10"].limitations == main.checks["REQ-10"].limitations
    for cid in NEW_DEFINITIONS:
        assert not current.checks[cid].judge.params["pass_when_all"]


@pytest.mark.parametrize("labels", [("main29","prior29"), ("prior29","main29")])
def test_distinct_historical_refs_with_same_id_revision_coexist(tmp_path, labels):
    packs = [historical_pack(label, tmp_path / label) for label in labels]
    store = Store(tmp_path / "EXAMPLE-store")
    current = load_pack(ROOT / "reference/pack-seeds/PACK-BASELINE-31")
    current_ref = put_pack(store,current.path)
    refs = [put_pack(store,pack.path) for pack in packs]
    assert refs[0].id == refs[1].id and refs[0].revision == refs[1].revision == 29
    assert refs[0].sha256 != refs[1].sha256
    original = {p.name:p.read_bytes() for p in (store.root / "artifacts").iterdir()}
    for pack, ref in zip(packs,refs):
        assert get_pack(store,ref).ref() == pack.ref()
        assert put_pack(store,pack.path) == ref
        with pytest.raises(PackNotStoredError):
            get_pack(store,ref.model_copy(update={"revision":28}))
    assert get_pack(store,current_ref).ref().model_dump(mode="json") == CURRENT_REF
    assert all((store.root / "artifacts" / name).read_bytes() == raw for name,raw in original.items())
    assert store.verify() == []
