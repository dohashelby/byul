"""Pure acceptance audit of the original five-check CPU update evidence.

This does not execute an engine, amend a verdict, or authenticate an operator.
The caller must first verify the stored pack bytes, content-addressed artifacts,
and actual managed target identities. MEASURED is a recorded source claim, not
cryptographic proof that a host ran an engine.
"""
from __future__ import annotations

import re

from guard.core.compare import compare_results, comparison_differences
from guard.core.judge import pass_gate
from guard.core.select import check_policy_value
from guard.judges.rule_table import RuleTableJudge
from guard.models import canonical_json, sha256_hex, weakest_origin
from guard.models.objects import object_from_dict

CHECKS = ("REQ-01", "REQ-03", "PLT-01", "PLT-04", "REQ-09")
SIDES = ("baseline", "candidate")
AXES = ("security", "functional", "performance")
_HASH = re.compile(r"[0-9a-f]{64}")
_IMAGE = re.compile(r".+@sha256:[0-9a-f]{64}")


def _key(ref):
    return (ref.kind, ref.id, ref.revision, ref.sha256)


def _coverage_blocks(check, evd):
    """Collection completeness beyond PASS: a FAIL must not hide missing trials.

    These are the existing five collectors' bounded observation contracts, not
    security verdict rules. They never turn FAIL into PASS or alter RES.
    """
    def value(name):
        obs = evd.observations.get(name)
        return obs.value if obs and obs.collected else None

    blocks = []
    planned, functional = value("functional_probes_planned"), value("functional_probe_results")
    expected = ({"before", "after_token_id", "after_length"} if check.id == "REQ-09"
                else {probe.id for probe in check.functional_probes})
    if (not isinstance(planned, list) or not planned or not expected.issubset(set(planned))
            or not isinstance(functional, dict) or not set(planned).issubset(functional)
            or any(functional.get(key) not in {"pass", "fail"} for key in planned)):
        blocks.append("planned normal functional probes are incomplete")
    if check.id in {"REQ-01", "REQ-03"}:
        registered, tested = value("routes_registered"), value("routes_tested")
        outcomes = value("direct_outcomes" if check.id == "REQ-01" else "nonadmin_outcomes")
        if (not isinstance(registered, list) or not registered or not isinstance(tested, list)
                or not set(registered).issubset(tested) or not isinstance(outcomes, dict)
                or not set(registered).issubset(outcomes)):
            blocks.append("registered route challenges are incomplete")
    if check.id in {"PLT-01", "PLT-04"} and value("process_scan_complete") is not True:
        blocks.append("original process collection is incomplete")
    if check.id == "REQ-09":
        planned_challenges, outcomes = value("challenges_planned"), value("challenge_outcomes")
        if (not isinstance(planned_challenges, list) or set(planned_challenges) != {"token_id", "length"}
                or not isinstance(outcomes, dict) or not {"token_id", "length"}.issubset(outcomes)
                or any(not isinstance(outcomes.get(key), str) or not outcomes[key].isdigit()
                       or not 100 <= int(outcomes[key]) <= 599 for key in ("token_id", "length"))):
            blocks.append("bounded token-ID/length challenges are incomplete")
    return blocks


def assess_update(report: dict, objects: list[dict], pack, *, diagnostic: bool = False) -> dict:
    """Audit pinned rules/refs and require ten complete, concrete CPU results.

    A confirmed security FAIL is a valid trial outcome. Missing collection still
    prevents trial acceptance, even where an independently established FAIL is
    correctly retained. Profile diagnostics retain NOT_COMPARABLE and can have
    a valid evidence contract, but never count as accepted image-update trials.
    No input object or policy value is changed.
    """
    contract_problems, incomplete, comparison_problems = [], [], []
    checks, refs, identities = {}, {}, {}
    kind = None
    official = report.get("official") if isinstance(report, dict) else None
    policy_status = report.get("policy_status") if isinstance(report, dict) else None
    review_status = report.get("pack_review_status") if isinstance(report, dict) else None
    try:
        parsed = [object_from_dict(obj) for obj in objects]
        if any(not obj.hash_ok() for obj in parsed):
            raise ValueError("stored object hash mismatch")
        index = {_key(obj.ref()): obj for obj in parsed}
        if len(index) != len(parsed):
            raise ValueError("duplicate stored object identity")

        def resolve(ref):
            if ref.kind == "PACK":
                if ref != pack.ref():
                    raise ValueError("saved pack reference differs from the fixed snapshot")
                return pack
            if _key(ref) not in index:
                raise ValueError(f"broken saved reference: {ref.kind} {ref.id}")
            return index[_key(ref)]

        for obj in parsed:
            for ref in obj.refs:
                resolve(ref)
        saved = object_from_dict(report)
        if str(saved.kind) != "RPT" or not saved.hash_ok() or resolve(saved.ref()).to_dict() != report:
            raise ValueError("report JSON differs from its sealed stored RPT")
        if saved.mode.value != "update":
            raise ValueError("an update RPT is required")
        # Product-owned sandbox teardown can fail after COMPLETED EVD was
        # committed. It deliberately preserves those EVD/FAIL and records the
        # lifecycle failure on RPT instead; read-only replay must not erase it.
        for side in SIDES:
            prefixes = (f"{side}: cleanup incomplete (",
                        f"{side}: cleanup incomplete after startup/execution failure (")
            if any(note.startswith(prefixes) for note in saved.limitations):
                incomplete.append(f"{side}: product-managed cleanup incomplete (original RPT limitation)")
        manifest = saved.inputs_manifest
        if len({_key(ref) for ref in manifest}) != len(manifest):
            raise ValueError("duplicate report inputs_manifest reference")
        if {_key(ref) for ref in saved.refs} != {_key(ref) for ref in manifest}:
            raise ValueError("RPT refs differ from inputs_manifest")
        inputs = [resolve(ref) for ref in manifest]
        plan_refs = [ref for ref in manifest if ref.kind == "PLAN"]
        if len(plan_refs) != 1:
            raise ValueError("one original update PLAN is required")
        plan = resolve(plan_refs[0])
        if plan.mode.value != "update" or len(plan.inputs.environments) != 2:
            raise ValueError("PLAN must identify exactly two update environments")
        if plan.inputs.environments[0] == plan.inputs.environments[1]:
            raise ValueError("the two sides must reference distinct ENV objects")
        if resolve(plan.inputs.pack) is not pack:
            raise ValueError("PLAN pack mismatch")
        policy = resolve(plan.inputs.policy)
        if str(policy.kind) != "POL" or policy.pack != pack.ref():
            raise ValueError("PLAN policy/pack mismatch")
        environments = [resolve(ref) for ref in plan.inputs.environments]
        for env in environments:
            if str(env.kind) != "ENV":
                raise ValueError("PLAN environment reference is not ENV")
            if env.engine.name not in pack.manifest.engines:
                raise ValueError("ENV engine kind is unsupported by the fixed pack")
            if (str(env.data_origin) == "EXAMPLE" or not env.engine.version
                    or not _IMAGE.fullmatch(env.engine.image_digest or "")):
                raise ValueError("ENV requires a declared real pinned image and version")
            if (not env.model.id or not env.model.revision
                    or not _HASH.fullmatch(env.model.inventory_sha256 or "")):
                raise ValueError("ENV requires actual model id/revision/inventory")
            if env.runtime.gpu_count != 0 or env.runtime.cpu is None or env.runtime.cpu.system != "Linux":
                raise ValueError("ENV requires an explicit Linux CPU profile with gpu_count=0")
        for side, env in zip(SIDES, environments):
            identities[side] = {"environment": env.ref().model_dump(mode="json"),
                                "image_digest": env.engine.image_digest,
                                "engine_version": env.engine.version,
                                "model": env.model.model_dump(mode="json"),
                                "cpu": env.runtime.cpu.model_dump(mode="json")}
        base_env, cand_env = environments
        if base_env.engine.name != cand_env.engine.name:
            comparison_problems.append("engine kinds differ")
        base_digest = base_env.engine.image_digest.rsplit("@sha256:", 1)[1]
        cand_digest = cand_env.engine.image_digest.rsplit("@sha256:", 1)[1]
        if base_digest == cand_digest:
            comparison_problems.append("two distinct image digests are required; repository aliases do not count")
        else:
            kind = "version-upgrade" if base_env.engine.version != cand_env.engine.version else "image-comparison"
        if any(getattr(base_env.model, key) != getattr(cand_env.model, key)
               for key in ("id", "revision", "inventory_sha256")):
            comparison_problems.append("model identity/revision/inventory differ")

        selected = [item for item in plan.items if item.selected]
        if len({item.check_id for item in plan.items}) != len(plan.items):
            raise ValueError("duplicate PLAN check")
        if len(selected) != len(CHECKS) or {item.check_id for item in selected} != set(CHECKS):
            raise ValueError("PLAN selection differs from the exact five required checks")
        if len({row.check_id for row in saved.by_check}) != len(saved.by_check):
            raise ValueError("duplicate report check row")
        rows = {row.check_id: row for row in saved.by_check if row.selected}
        if set(rows) != set(CHECKS):
            raise ValueError("selected report checks differ from the exact five required checks")
        evidence = [obj for obj in inputs if getattr(obj, "kind", None) == "EVD"]
        results = [obj for obj in inputs if getattr(obj, "kind", None) == "RES"]
        expected_pairs = {(cid, side) for cid in CHECKS for side in SIDES}
        if (len(evidence) != 10 or {(obj.check_id, str(obj.side)) for obj in evidence} != expected_pairs
                or len(results) != 10 or {(obj.check_id, str(obj.side)) for obj in results} != expected_pairs):
            raise ValueError("RPT must retain exactly ten original EVD/RES side pairs")
        required_refs = [pack.ref(), *plan.inputs.environments, policy.ref(), plan.ref(),
                         *[obj.ref() for obj in evidence], *[obj.ref() for obj in results]]
        if plan.inputs.change:
            resolve(plan.inputs.change)
            required_refs.append(plan.inputs.change)
        if {_key(ref) for ref in manifest} != {_key(ref) for ref in required_refs}:
            raise ValueError("RPT input manifest differs from the original PLAN/EVD/RES graph")
        plan_refs_expected = [pack.ref(), *plan.inputs.environments, policy.ref()]
        if plan.inputs.change:
            plan_refs_expected.append(plan.inputs.change)
        if {_key(ref) for ref in plan.refs} != {_key(ref) for ref in plan_refs_expected}:
            raise ValueError("PLAN refs differ from its original inputs")
        if policy.ref() != plan.inputs.policy or resolve(policy.pack) is not pack:
            raise ValueError("POL differs from the original PLAN")
        if saved.policy_status != policy.status or str(saved.pack_review_status) != ("REVIEWED" if pack.reviewed else "DRAFT"):
            raise ValueError("report policy/pack review status mismatch")
        origins = [env.data_origin for env in environments] + [policy.data_origin, plan.data_origin]
        origins += [obj.data_origin for obj in [*evidence, *results]]
        expected_official = str(policy.status) == "CONFIRMED" and pack.reviewed and all(str(o) != "EXAMPLE" for o in origins)
        if saved.official != expected_official:
            raise ValueError("report official flag differs from original source/review states")
        if saved.data_origin != weakest_origin(origins):
            raise ValueError("report origin differs from its original inputs")
        refs = {"pack": pack.ref().model_dump(mode="json"), "plan": plan.ref().model_dump(mode="json"),
                "policy": policy.ref().model_dump(mode="json"), "report": saved.ref().model_dump(mode="json")}
        plugin = RuleTableJudge()
        evd_by_pair = {(evd.check_id, str(evd.side)): evd for evd in evidence}
        for cid in CHECKS:
            check, item, row = pack.checks[cid], plan.item(cid), rows[cid]
            if row.applicability != item.applicability or row.gaps != item.gaps:
                raise ValueError(f"{cid}: report applicability/gaps differ from the original PLAN")
            if item.gaps or str(item.applicability) != "APPLICABLE":
                incomplete.append(f"{cid}: unresolved original PLAN applicability/gaps")
            if (item.check_revision != check.revision or item.executor != check.executor
                    or check.judge is None or check.judge.plugin != plugin.name):
                raise ValueError(f"{cid}: PLAN definition/executor/rule_table differs from the fixed pack")
            for param in check.policy_params:
                values = policy.values.get(cid, {})
                if param.required and param.key not in values:
                    raise ValueError(f"{cid}: original policy value is missing: {param.key}")
                if param.key in values:
                    check_policy_value(cid, param.key, param.type, values[param.key])
            checks[cid] = {}
            for side, env in zip(SIDES, environments):
                evd = evd_by_pair[cid, side]
                if evd.plan != plan.ref() or evd.environment != env.ref():
                    raise ValueError(f"{cid}/{side}: EVD PLAN/environment mismatch")
                if {_key(ref) for ref in evd.refs} != {_key(plan.ref()), _key(env.ref())}:
                    raise ValueError(f"{cid}/{side}: EVD refs differ from original PLAN/ENV")
                executor = evd.execution.executor
                if (str(evd.data_origin) != "MEASURED" or str(evd.execution.status) != "COMPLETED"
                        or evd.execution.operator is not None or evd.execution.override_reason
                        or executor is None or executor.name != check.executor or not executor.version
                        or executor.default_judge or not _HASH.fullmatch(executor.config_sha256)):
                    raise ValueError(f"{cid}/{side}: completed automated original measurement is required")
                if (evd.execution.adapter is None or evd.execution.adapter.name != env.engine.name
                        or not evd.execution.adapter.version):
                    raise ValueError(f"{cid}/{side}: original engine adapter identity mismatch")
                if evd.execution.cleanup_errors:
                    incomplete.append(f"{cid}/{side}: cleanup incomplete: {evd.execution.cleanup_errors}")
                if evd.execution.abort_reason:
                    incomplete.append(f"{cid}/{side}: completed evidence has an abort reason")
                if not evd.artifacts or not any(artifact.name == "probe_log.jsonl" for artifact in evd.artifacts):
                    raise ValueError(f"{cid}/{side}: original automated probe log attachment is required")
                for artifact in evd.artifacts:
                    if (not _HASH.fullmatch(artifact.sha256)
                            or artifact.path != f"artifacts/{artifact.sha256}" or artifact.bytes < 1):
                        raise ValueError(f"{cid}/{side}: noncanonical original evidence artifact metadata")
                if (evd.execution.started_at is None or evd.execution.finished_at is None
                        or evd.execution.finished_at < evd.execution.started_at):
                    raise ValueError(f"{cid}/{side}: valid original execution timestamps are required")
                if (evd.comparability.cpu != env.runtime.cpu or evd.comparability.gpu is not None
                        or evd.comparability.driver is not None
                        or evd.comparability.model_inventory_sha256 != env.model.inventory_sha256
                        or not _HASH.fullmatch(evd.comparability.config_sha256 or "")
                        or not _HASH.fullmatch(evd.comparability.fixture_sha256 or "")):
                    raise ValueError(f"{cid}/{side}: explicit EVD CPU/model/fixture/config identity mismatch")
                matches = [obj for obj in results if obj.evidence == evd.ref() and obj.check_id == cid and str(obj.side) == side]
                if len(matches) != 1:
                    raise ValueError(f"{cid}/{side}: exact original RES/EVD relation is required")
                result = matches[0]
                if {_key(ref) for ref in result.refs} != {_key(evd.ref()), _key(policy.ref())}:
                    raise ValueError(f"{cid}/{side}: RES refs differ from original EVD/POL")
                if (result.judge.name != plugin.name or result.judge.version != plugin.version
                        or result.judge.params_sha256 != sha256_hex(canonical_json(check.judge.params))):
                    raise ValueError(f"{cid}/{side}: original judge version/parameters mismatch")
                raw = plugin.judge(check, evd, check.judge.params, policy.values.get(cid, {}))
                blocks = pass_gate(check, evd)
                coverage_blocks = _coverage_blocks(check, evd)
                steps = [challenge.step for challenge in evd.challenges]
                if len(steps) != len(set(steps)) or set(steps) - set(check.challenge_steps):
                    raise ValueError(f"{cid}/{side}: duplicate or unrecognized challenge records")
                expected = {axis: str(raw.verdicts.get(axis, "UNKNOWN")) for axis in AXES}
                expected = {axis: "UNKNOWN" if value == "PASS" and blocks else value for axis, value in expected.items()}
                expected["performance"] = "UNKNOWN"
                actual = result.verdicts.model_dump(mode="json")
                shown = getattr(row, side)
                checks[cid][side] = {"evidence": evd.ref().model_dump(mode="json"),
                                     "result": result.ref().model_dump(mode="json"),
                                     "actual": actual, "raw_rule_verdicts": {axis: str(value) for axis, value in raw.verdicts.items()},
                                     "pass_gate_blocks": blocks,
                                     "collection_blocks": coverage_blocks,
                                     "required_observations": [obs.key for obs in check.required_observations],
                                     "challenge_steps": list(check.challenge_steps)}
                if actual != expected:
                    contract_problems.append(f"{cid}/{side}: saved RES differs from the fixed rules and PASS gate")
                if (shown is None or shown.result != result.ref()
                        or any(str(getattr(shown, axis)) != actual[axis] for axis in AXES)
                        or shown.verification_level != result.verification_level):
                    contract_problems.append(f"{cid}/{side}: report differs from the original RES/ref")
                expected_level = weakest_origin([evd.data_origin, *[condition.origin for condition in item.conditions]])
                if result.verification_level != expected_level:
                    contract_problems.append(f"{cid}/{side}: RES verification level differs from original evidence/conditions")
                if result.data_origin != weakest_origin([evd.data_origin, policy.data_origin, plan.data_origin]):
                    contract_problems.append(f"{cid}/{side}: RES origin differs from original inputs")
                if blocks:
                    incomplete.append(f"{cid}/{side}: incomplete normal control/challenges/observations: {blocks}")
                if coverage_blocks:
                    incomplete.append(f"{cid}/{side}: incomplete collection coverage: {coverage_blocks}")
                if any(actual[axis] not in {"PASS", "FAIL"} for axis in ("security", "functional")):
                    incomplete.append(f"{cid}/{side}: concrete security and functional PASS/FAIL are required")
                if actual["performance"] != "UNKNOWN":
                    contract_problems.append(f"{cid}/{side}: CPU performance must remain UNKNOWN")
            base, cand = (evd_by_pair[cid, side] for side in SIDES)
            differences = comparison_differences(base.comparability, cand.comparability)
            for axis, field in (("security", "comparison"), ("functional", "functional_comparison")):
                expected_comparison = str(compare_results(
                    checks[cid]["baseline"]["actual"][axis], base.comparability,
                    checks[cid]["candidate"]["actual"][axis], cand.comparability))
                if str(getattr(row, field)) != expected_comparison:
                    contract_problems.append(f"{cid}: report {field} differs from the original comparison rules")
            if row.comparison_reasons != differences:
                contract_problems.append(f"{cid}: comparison reasons differ from original execution conditions")
            checks[cid]["comparison"] = str(row.comparison)
            checks[cid]["functional_comparison"] = str(row.functional_comparison)
            checks[cid]["comparison_reasons"] = differences
            if differences:
                comparison_problems.append(f"{cid}: NOT_COMPARABLE execution conditions: {differences}")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        contract_problems.append(f"saved update contract invalid: {exc}")
    problems = [*contract_problems, *incomplete, *comparison_problems]
    if diagnostic:
        problems.append("profile diagnostic only; this is not an accepted image-update trial")
    return {"schema": "guard-update-acceptance/1", "accepted": not problems, "diagnostic": diagnostic,
            "evidence_contract_valid": not contract_problems,
            "comparison_kind": kind, "version_upgrade_verified": not problems and kind == "version-upgrade",
            "checks": checks, "identities": identities, "refs": refs,
            "official": official, "policy_status": policy_status, "pack_review_status": review_status,
            "problems": problems, "limitations": [
                "This audits original saved evidence; it does not execute an engine or authenticate source claims.",
                "The caller must independently verify stored pack/artifact bytes and actual target identity.",
                "MEASURED/COMPLETED and process exit 0 alone never establish trial acceptance.",
                "CPU comparison covers security/function only; performance remains UNKNOWN.",
                "Accepted evidence can contain FAIL/STILL_FAILING; acceptance is not a release approval.",
            ]}
