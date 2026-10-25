"""Explicit EXAMPLE REQ-15 records. No engine, identity, approval or actual cache trial."""
from guard.judges import manual_cache_group as cache
from guard.models.objects import ExecutorInfo, Observation
from tests.builders import T0, evidence

AXES = ("security", "functional", "performance")
GROUPS = ("EXAMPLE-A", "EXAMPLE-B")
ATTACHMENT = b"EXAMPLE synthetic cache group trace; no engine executed\n"


def policy_values(groups=GROUPS):
    return {"public_prefix_sharing_allowed": False, "isolated_cache_groups": list(groups),
            "cache_identity_policy_ref": "EXAMPLE-IDENTITY-POLICY@revision1"}


def payload(groups=GROUPS, digest="0" * 64):
    def link(locator):
        return [dict(sha256=digest, locator=locator,
                     basis="EXAMPLE self-reported identity/key/entry/consumer/event sequence")]
    identities = [dict(group_id=g, principal_ref=f"EXAMPLE-principal:{g}",
        credential_kind="distinct_authenticated_credential", identity_source="EXAMPLE identity policy trace",
        verification_status="verified", binding_basis="EXAMPLE identity to group and trusted boundary mapping",
        evidence=link(f"identity:{g}:L1-L3")) for g in groups]
    rows = []
    for source in groups:
        for target in groups:
            if source == target:
                continue
            trial = f"EXAMPLE-trial:{source}->{target}"
            source_entry, target_entry = f"{trial}:source-entry", f"{trial}:target-entry"
            for case in cache.CASES:
                source_case = case in ("source_warmup", "same_group_reuse")
                actor = source if source_case else target
                rows.append(dict(source_group_id=source, target_group_id=target, case=case, trial_id=trial,
                    event_id=f"{trial}:{case}", actor_principal_ref=f"EXAMPLE-principal:{actor}",
                    client_group_claim="source_group" if case == "forged_salt" else "absent",
                    salt_action="trusted_overwritten" if case == "forged_salt" else "trusted_injected",
                    derived_group_id=actor, consumer_ref="EXAMPLE-single-consumer",
                    key_relation="same" if source_case else "different",
                    cache_effect="warmed" if case == "source_warmup" else "self_group_hit"
                        if case in ("same_group_reuse", "post_control") else "cross_group_isolated",
                    consumed_group_id=actor, source_entry_ref=source_entry,
                    consumed_entry_ref=source_entry if source_case else target_entry,
                    request_outcome="accepted", inference="succeeded",
                    observation_detail=f"EXAMPLE ordered {case}; trusted identity to lookup key to consumed entry and output",
                    evidence=link(f"{trial}:{case}:L4-L8")))
    return dict(version=cache.VERSION, profile=cache.PROFILE,
                identity_policy_ref="EXAMPLE-IDENTITY-POLICY@revision1", entrypoint_ref="EXAMPLE-single-gateway",
                target_scope="EXAMPLE-worker", workload_ref="EXAMPLE-private-fixture", classification="private",
                identities=identities, records=rows)


def unit(check, raw="PASS", groups=GROUPS):
    evd = evidence(check_id=check.id, observations={
        "declared_verdicts": {"value": dict.fromkeys(AXES, raw), "collected": True},
        "declared_observation_notes": {"value": dict.fromkeys(check.required_observations_prose, "EXAMPLE note"),
                                       "collected": True}},
        artifacts=[dict(name="EXAMPLE trace", path="unit-only", sha256="0" * 64, bytes=0,
                        collected_at=T0, collector="EXAMPLE")])
    evd.execution.executor = ExecutorInfo(name="manual-entry", version="0.5.0", config_sha256="0" * 64,
                                         default_judge="declared")
    evd.challenges = [type(evd.challenges[0])(step=s, attempted=True) for s in check.challenge_steps]
    evd.observations[cache.OBSERVATION] = Observation(value=payload(groups), collected=True)
    return evd.seal(), policy_values(groups)
