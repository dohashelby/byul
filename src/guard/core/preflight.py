"""Validate the small rule-table contract; no general-purpose policy language."""
from guard.judges.rule_table import OPS


def validate_pack(pack):
    for check in pack.active_checks():
        if check.judge is None or check.judge.plugin != "rule_table":
            continue
        obs = {o.key for o in check.required_observations}
        policy = {p.key for p in check.policy_params}
        tables = [check.judge.params, *check.judge.params.get("axes", {}).values()]
        def rule(item):
            if item.get("obs") not in obs:
                raise ValueError(f"{check.id}: undeclared observation {item.get('obs')}")
            if item.get("op") not in OPS:
                raise ValueError(f"{check.id}: unsupported operator {item.get('op')}")
            if "value_obs" in item and item["value_obs"] not in obs:
                raise ValueError(f"{check.id}: undeclared value_obs")
            for key in ("value_policy", "when_policy"):
                if key in item and item[key] not in policy:
                    raise ValueError(f"{check.id}: undeclared {key} {item[key]}")
            if "value_policy" in item and "value" in item:
                raise ValueError(f"{check.id}: conflicting value and value_policy")
            if item.get("when"):
                rule(item["when"])
        for table in tables:
            for item in [*table.get("pass_when_all", []), *table.get("fail_when_any", [])]:
                rule(item)
