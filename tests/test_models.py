"""2단계: 객체 7종 + CheckDef 모델, 해시."""

import json

import pytest
import yaml
from pydantic import ValidationError

from guard.models import (
    CheckDef,
    DataOrigin,
    Environment,
    Kind,
    Plan,
    Report,
    Result,
    canonical_json,
    new_ulid,
    object_from_dict,
    object_sha256,
    sha256_hex,
)
from guard.models.objects import Change
from tests.builders import T0, baseline_pack, dummy_ref, environment, evidence, policy


def _all_examples():
    env = environment({"gateway": {"value": True, "origin": "DECLARED", "source": "env.yaml#gateway"}})
    pol = policy()
    chg = Change(
        id="CHG-TEST",
        created_at=T0,
        data_origin="DECLARED",
        baseline={"version": "1.0.0", "source_commit": "a" * 40},
        candidate={"version": "1.1.0", "source_commit": "b" * 40},
        extractor={"name": "manual", "version": "0"},
        facts=[{"id": "F1", "category": "API_ROUTE", "target": "/v1/x", "certainty": "PARTIAL"}],
    ).seal()
    plan = Plan(
        id="PLAN-TEST",
        created_at=T0,
        data_origin="EXAMPLE",
        mode="audit",
        inputs={"pack": dummy_ref("PACK"), "environments": [env.ref()], "policy": pol.ref()},
        items=[
            {
                "check_id": "REQ-01",
                "check_revision": 1,
                "applicability": "APPLICABLE",
                "conditions": [{"fact": "gateway", "expected": True, "actual": True, "origin": "DECLARED", "result": "TRUE"}],
                "reason": "all conditions TRUE",
                "selected": True,
                "priority": "reference_baseline_first",
            }
        ],
        inventory_complete={"요청·대상 접근 권한": False},
    ).seal()
    evd = evidence()
    res = Result(
        id="RES-TEST",
        created_at=T0,
        data_origin="EXAMPLE",
        evidence=evd.ref(),
        check_id="REQ-01",
        side="single",
        judge={"name": "rule_table", "version": "0.1.0", "params_sha256": "0" * 64},
        verdicts={"security": "PASS", "functional": "PASS", "performance": "UNKNOWN"},
        verification_level="EXAMPLE",
    ).seal()
    rpt = Report(
        id="RPT-TEST",
        created_at=T0,
        data_origin="EXAMPLE",
        mode="audit",
        official=False,
        inputs_manifest=[plan.ref(), res.ref()],
        pack_review_status="DRAFT",
        policy_status="DRAFT",
        by_check=[],
        by_purpose={},
        by_area={},
    ).seal()
    return [env, pol, chg, plan, evd, res, rpt]


@pytest.mark.parametrize("obj", _all_examples(), ids=lambda o: str(o.kind))
def test_yaml_round_trip(obj):
    text = yaml.safe_dump(obj.to_dict(), allow_unicode=True, sort_keys=False)
    back = object_from_dict(yaml.safe_load(text))
    assert back == obj
    assert back.hash_ok()


def test_all_seven_kinds_covered():
    assert {str(o.kind) for o in _all_examples()} == {k.value for k in Kind}


def test_canonical_json_rule():
    assert canonical_json({"b": 1, "a": "한"}) == '{"a":"한","b":1}'.encode("utf-8")


def test_hash_excludes_own_field_and_is_fixed():
    d = {"kind": "ENV", "id": "ENV-X", "content_sha256": "ignored", "z": [1, 2]}
    expected = sha256_hex(b'{"id":"ENV-X","kind":"ENV","z":[1,2]}')
    assert object_sha256(d) == expected
    # 고정값: 규칙이 바뀌면 이 테스트가 깨져야 한다
    assert expected == "2deaf3ce8681aadf3dc0651575a6d323da52e97061e9a939c71b8e87a6518dbc"


def test_environment_hash_is_pinned():
    env = environment()
    assert env.content_sha256 == env.compute_hash()
    assert env.content_sha256 == PINNED_ENV_SHA


PINNED_ENV_SHA = "86d2d68940abed216d0f6c5162667bd785a462d3789f0d57a0f1fc8b27e01fae"


def test_tampering_breaks_hash():
    env = environment()
    tampered = env.model_copy(update={"data_origin": DataOrigin.MEASURED})
    assert not tampered.hash_ok()


def test_unregistered_fact_rejected():
    with pytest.raises(ValidationError):
        environment({"made_up_fact": {"value": True}})


def test_conflicting_fact_must_be_null():
    with pytest.raises(ValidationError):
        environment({"gateway": {"value": True, "conflict": [True, False]}})


def test_evidence_has_no_verdict_field():
    data = evidence().to_dict()
    data["verdicts"] = {"security": "PASS"}
    with pytest.raises(ValidationError):
        object_from_dict(data)


def test_uncollected_observation_has_no_value():
    with pytest.raises(ValidationError):
        evidence(observations={"direct_route_status": {"value": 401, "collected": False}})


def test_confirmed_policy_needs_confirmer():
    with pytest.raises(ValidationError):
        policy(status="CONFIRMED")


def test_unreviewed_change_cannot_be_confirmed():
    with pytest.raises(ValidationError):
        Change(
            id="CHG-X",
            data_origin="DECLARED",
            baseline={"version": "1", "source_commit": "a"},
            candidate={"version": "2", "source_commit": "b"},
            extractor={"name": "manual", "version": "0"},
            facts=[{"id": "F1", "category": "OTHER", "target": "t", "certainty": "CONFIRMED"}],
        )


def test_ulid_format_and_order():
    a, b = new_ulid(1000), new_ulid(2000)
    assert len(a) == 26 and a < b


def test_all_pack_checks_load_as_checkdef():
    pack = baseline_pack()
    assert len(pack.checks) == 77
    assert all(isinstance(c, CheckDef) for c in pack.checks.values())
    req = pack.checks["REQ-01"]
    assert [o.key for o in req.required_observations] == ["direct_route_status", "gateway_route_status"]


def test_checkdef_yaml_round_trip():
    pack = baseline_pack()
    raw = yaml.safe_load((pack.path / "checks/REQ-01.yaml").read_text(encoding="utf-8"))
    check = CheckDef.model_validate(raw)
    text = yaml.safe_dump(check.model_dump(mode="json"), allow_unicode=True, sort_keys=False)
    assert CheckDef.model_validate(yaml.safe_load(text)) == check
    dumped = check.model_dump(mode="json")
    assert all(dumped[k] == v for k, v in raw.items() if k != "applies_when")
    assert dumped["applies_when"]["all"] == raw["applies_when"]["all"]
