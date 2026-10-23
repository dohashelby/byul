from copy import deepcopy
import pytest
from guard.core.compare import comparable, comparison_differences
from guard.models.objects import Comparability, Environment
from guard.runtime.comparison import execution_conditions
from tests.builders import environment

CPU = dict(architecture="x86_64", model="test cpu", system="Linux", kernel="test", threads=2)
FIELDS = dict(model_inventory_sha256="m", fixture_sha256="f", config_sha256="c")


def test_explicit_cpu_profile_and_unknown_hardware_are_distinct():
    c = Comparability(**FIELDS, cpu=CPU)
    assert comparable(c, c)
    unknown = Comparability(**FIELDS)
    assert not comparable(unknown, unknown)
    assert comparison_differences(c, unknown) == ["cpu"]
    other = Comparability(**FIELDS, cpu={**CPU, "threads": 4})
    assert not comparable(c, other)


def test_cpu_profile_preserves_legacy_serialization():
    env = environment()
    raw = env.to_dict()
    assert "cpu" not in raw["runtime"]
    assert Environment.model_validate(raw).hash_ok()
    assert Comparability(**FIELDS).model_dump() == {**FIELDS, "gpu": None, "driver": None}
    with pytest.raises(ValueError):
        Comparability(**FIELDS, cpu=CPU, gpu="L4")


def test_cpu_never_claims_performance_verification():
    from tests.builders import evidence
    from tests.judging import judge, REQ01
    check = REQ01.model_copy(deep=True)
    check.judge.params.setdefault("axes", {})["performance"] = {
        "pass_when_all":[{"obs":"routes_registered","op":"nonempty"}]}
    evd = evidence(comparability={"cpu":CPU})
    result = judge(evd, check=check)
    assert result.verdicts.performance.value == "UNKNOWN"


def test_semantic_hash_ignores_resource_names_but_preserves_conditions():
    t = {"run_id": "one", "topology": "isolated", "engine": {"container": "a", "trusted_url": "http://1.2.3.4:8000"},
         "origins": {"trusted": {"network": "n1"}, "external": {"network": "n2"}},
         "comparison_config": {"device": "cpu", "options": {"kv": 1}}}
    other = deepcopy(t)
    other["run_id"] = "two"
    other["engine"] = {"container": "b", "trusted_url": "http://5.6.7.8:8000"}
    other["origins"] = {"trusted": {"network": "n3"}, "external": {"network": "n4"}}
    f = lambda target: execution_conditions(target, {"request_timeout": 3}, ["allowed"])
    assert f(t) == f(other)
    other["comparison_config"]["options"]["kv"] = 2
    assert f(t) != f(other)
