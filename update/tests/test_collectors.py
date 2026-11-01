"""파일 수집기: env.yaml / policy.yaml."""

import pytest

from guard.collectors.file import load_environment, load_policy
from guard.models import load_pack
from tests.builders import ROOT, baseline_pack


def test_example_env_loads_with_shorthand_and_origin():
    env = load_environment(ROOT / "examples/env.yaml")
    assert env.data_origin == "EXAMPLE"
    g = env.facts["gateway"]
    assert (g.value, g.origin, g.source) == (True, "EXAMPLE", "env.yaml#facts.gateway")
    assert env.facts["dynamic_import"].value is None and env.facts["dynamic_import"].origin is None
    assert env.hash_ok()


def test_declared_file_shorthand_is_declared_never_measured(tmp_path):
    p = tmp_path / "env.yaml"
    p.write_text("data_origin: DECLARED\nengine: {name: e}\nfacts: {gateway: true}\n", encoding="utf-8")
    assert load_environment(p).facts["gateway"].origin == "DECLARED"


def test_policy_must_match_pack_revision(tmp_path):
    pack = load_pack(ROOT / "packs/PACK-BASELINE")  # 예시 정책은 현행 팩에 묶여 있다
    pol = load_policy(ROOT / "examples/policy.yaml", pack)
    assert pol.pack == pack.ref() and pol.status == "DRAFT"
    p = tmp_path / "policy.yaml"
    p.write_text("pack: {id: PACK-BASELINE, revision: 99}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(p, pack)
