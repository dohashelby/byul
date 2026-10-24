"""REQ-11 rev3 scope regressions; every transport and artifact is EXAMPLE.

The output-only mutation preserves independently checked prompt projections and
response metadata. Its UNKNOWN is a verifier scope limit, not an engine finding.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from guard.core.judge import judge_evidence
from guard.core.report import build_report
from guard.core.select import select
from guard.judges.declared import DeclaredJudge
from guard.judges.rule_table import RuleTableJudge
from guard.models import CheckDef, Verdict
from guard.report.html import render_html
from guard.store import Store
from tests.builders import T0, baseline_pack, environment, evidence, policy
from tests.test_binding_log_policy_contracts import sample
from tests.test_request_binding import VALUES, World, judge, run, summary


class OutputWorld(World):
    """Exchange only generated output; keep body/events/bytes/SHA consistent."""

    def __init__(self, *, swap=False, **changes):
        super().__init__(**changes)
        self.swap = swap
        self.captures = []

    def stream(self, network, requests, timeout):
        results = super().stream(network, requests, timeout)
        for request, result in zip(requests, results):
            own = "B" if request["id"].endswith("-B") else "A"
            supplied = ("B" if own == "A" else "A") if self.swap and len(requests) == 2 else own
            token = 101 if supplied == "A" else 202
            text = "EXAMPLE-OUTPUT-" + supplied
            if request["mode"] == "json":
                value = json.loads(result["body"])
                value["choices"][0]["message"]["content"] = text
                value["choices"][0]["token_ids"] = [token]
                raw = json.dumps(value)
            else:
                events = deepcopy(result["events"])
                for event in events:
                    if event["data"] == "[DONE]":
                        continue
                    value = json.loads(event["data"])
                    for choice in value.get("choices", []):
                        if "token_ids" in choice:
                            choice["token_ids"] = [token]
                            choice["delta"]["content"] = text
                    event["data"] = json.dumps(value)
                result["events"] = events
                raw = "".join("data: " + event["data"] + "\n\n" for event in events)
            result.update(body=raw, body_bytes=len(raw.encode("utf-8")),
                          body_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())
            self.captures.append({"label": request["id"], "mode": request["mode"],
                                  "own": own, "supplied": supplied, "response": deepcopy(result)})
        return results


def protected_projection(capture):
    response = capture["response"]
    frames = ([json.loads(response["body"])] if capture["mode"] == "json" else
              [json.loads(event["data"]) for event in response["events"] if event["data"] != "[DONE]"])
    return [{key: frame.get(key) for key in
             ("id", "model", "object", "prompt_token_ids", "prompt_text", "usage")} for frame in frames]


def test_output_only_exchange_is_unknown_while_valid_input_subproperties_remain_visible(tmp_path, monkeypatch):
    monkeypatch.setattr("guard.executors.request_binding.secrets.token_hex", lambda _: "0123456789ab")
    drafts, worlds = [], []
    for name, swap in (("normal", False), ("output-exchanged", True)):
        folder = tmp_path / name
        folder.mkdir()
        draft, world = run(folder, OutputWorld(swap=swap))
        drafts.append(draft)
        worlds.append(world)
        assert judge(draft).verdicts == {"security": Verdict.UNKNOWN, "functional": Verdict.PASS,
                                        "performance": Verdict.UNKNOWN}
        assert draft.observations["input_projection_verified"].value is True
        assert draft.observations["connection_binding_verified"].value is True
        assert draft.observations["output_binding_verified"].value is False
        assert draft.observations["collection_complete"].value is True
        assert draft.observations["security_violations"].value == []
        assert len(draft.observations["cases_verified"].value) == 8
        assert summary(draft)["verification_scope"]["output_binding_verified"] is False
    normal, swapped = worlds
    assert len(normal.captures) == len(swapped.captures) == 18
    assert sum(capture["own"] != capture["supplied"] for capture in swapped.captures) == 16
    assert drafts[0].normal_control == drafts[1].normal_control
    assert [protected_projection(value) for value in normal.captures] == [
        protected_projection(value) for value in swapped.captures]
    for capture in normal.captures + swapped.captures:
        response = capture["response"]
        assert response["body_bytes"] == len(response["body"].encode("utf-8"))
        assert response["body_sha256"] == hashlib.sha256(response["body"].encode("utf-8")).hexdigest()


@pytest.mark.parametrize("claim", [False, True, "true", 1, None])
def test_declared_output_claim_cannot_create_a_security_pass_or_hide_independent_declared_fail(claim):
    definition, evd = sample("REQ-11")
    evd.observations["output_binding_verified"].value = claim
    verdicts = DeclaredJudge().judge(definition, evd.seal(), {}, VALUES).verdicts
    assert verdicts == {"security": Verdict.UNKNOWN, "functional": Verdict.PASS, "performance": Verdict.UNKNOWN}
    # The v3 definition has no security PASS route, including self-reported true.
    assert RuleTableJudge().judge(definition, evd, definition.judge.params, VALUES).verdicts["security"] == Verdict.UNKNOWN
    evd.observations["declared_verdicts"].value["security"] = "FAIL"
    assert DeclaredJudge().judge(definition, evd.seal(), {}, {}).verdicts["security"] == Verdict.FAIL


def test_rev2_saved_definition_keeps_its_original_rule_contract():
    legacy_path = Path(__file__).with_name("fixtures") / "REQ-11.rev2.yaml"
    legacy_bytes = legacy_path.read_bytes()
    legacy = CheckDef.model_validate(yaml.safe_load(legacy_bytes))
    assert legacy.revision == 2
    current, evd = sample("REQ-11")
    for key in ("input_projection_verified", "output_binding_verified", "output_binding_gap"):
        del evd.observations[key]
    before = (legacy.model_dump(mode="json"), evd.to_dict())
    automatic = RuleTableJudge().judge(legacy, evd, legacy.judge.params, VALUES)
    declared = DeclaredJudge().judge(legacy, evd, {}, VALUES)
    assert automatic.verdicts == declared.verdicts == {
        "security": Verdict.PASS, "functional": Verdict.PASS, "performance": Verdict.UNKNOWN}
    assert RuleTableJudge().judge(current, evd, current.judge.params, VALUES).verdicts["security"] == Verdict.UNKNOWN
    assert (legacy.model_dump(mode="json"), evd.to_dict()) == before
    assert legacy_path.read_bytes() == legacy_bytes


@pytest.mark.parametrize("changes,complete", [
    ({}, True), ({"incomplete": "sse-generated-A"}, False),
    ({"missing_done": "sse-distinct_body-A"}, False), ({"post_done": "sse-generated-A"}, False),
])
def test_json_and_html_report_explain_input_only_scope_and_output_gap(tmp_path, changes, complete):
    draft, _ = run(tmp_path, World(**changes))
    assert draft.observations["collection_complete"].value is complete
    pack = baseline_pack()
    assert pack.checks["REQ-11"].revision == 3
    store = Store(tmp_path / "report-store")
    env = environment()
    pol = policy(pack.ref(), values={"REQ-11": VALUES},
                 scope={"include": ["REQ-11"], "exclude": []})
    store.put(env)
    store.put(pol)
    plan = select(pack, [env], pol)
    store.put(plan)
    evd = evidence(check_id="REQ-11",
        normal=draft.normal_control.model_dump(), observations={
            key: value.model_dump() for key, value in draft.observations.items()})
    evd.plan = plan.ref()
    evd.environment = env.ref()
    evd.challenges = draft.challenges
    evd = evd.seal()
    store.put(evd)
    result = judge_evidence(pack.checks["REQ-11"], evd, pol, T0, plan=plan)
    store.put(result)
    report = build_report(store, pack, plan, T0)
    data = json.loads(json.dumps(report.to_dict(), ensure_ascii=False))
    row = next(row for row in data["by_check"] if row["check_id"] == "REQ-11")
    assert row["single"]["security"] == "UNKNOWN"
    assert row["single"]["functional"] == "PASS"
    assert data["official"] is False and data["data_origin"] == "EXAMPLE"
    html = render_html(data)
    for required in ("입력 projection", "생성 출력 자체의 귀속은 미검증", "보안 PASS를 보류"):
        assert any(required in note for note in data["limitations"])
        assert required in html
