"""Shared validation for sealed object relationships and stored attachments."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import hashlib
from pathlib import Path
import re

from guard.models import GuardObject, Ref
from guard.models.objects import Evidence, Plan, Policy, Report, Result


class IntegrityError(Exception):
    pass


_DIGEST = re.compile(r"[0-9a-f]{64}")
Resolver = Callable[[Ref], GuardObject]


def ref_key(ref: Ref) -> tuple[str, str, int, str]:
    return ref.kind, ref.id, ref.revision, ref.sha256


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def is_content_digest(value) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def attachments(obj: GuardObject):
    yield from getattr(obj, "artifacts", None) or ()
    yield from getattr(obj, "diagnostic_artifacts", None) or ()


def verify_attachment(root: Path, artifact) -> None:
    if (not is_content_digest(artifact.sha256) or artifact.bytes < 0
            or artifact.path != f"artifacts/{artifact.sha256}"):
        raise IntegrityError("noncanonical artifact path or byte count")
    path = root / artifact.path
    if (path.is_symlink() or path.parent.is_symlink() or not path.is_file()
            or path.stat().st_size != artifact.bytes or file_sha256(path) != artifact.sha256):
        raise IntegrityError(f"artifact {artifact.path} missing or changed")


def _require_kind(ref: Ref, kind: str) -> None:
    if ref.kind != kind:
        raise IntegrityError(f"expected {kind} reference, got {ref.kind}")


def _typed_refs(obj: GuardObject) -> Iterator[tuple[Ref, str]]:
    if isinstance(obj, Policy):
        yield obj.pack, "PACK"
    elif isinstance(obj, Plan):
        yield obj.inputs.pack, "PACK"
        yield obj.inputs.policy, "POL"
        yield from ((env, "ENV") for env in obj.inputs.environments)
        if obj.inputs.change is not None:
            yield obj.inputs.change, "CHG"
        if obj.inputs.previous_plan is not None:
            yield obj.inputs.previous_plan, "PLAN"
        for item in obj.items:
            if item.carried_over is not None:
                yield item.carried_over.evidence, "EVD"
                yield item.carried_over.result, "RES"
    elif isinstance(obj, Evidence):
        yield obj.plan, "PLAN"
        yield obj.environment, "ENV"
    elif isinstance(obj, Result):
        yield obj.evidence, "EVD"
    elif isinstance(obj, Report):
        yield from ((ref, ref.kind) for ref in obj.inputs_manifest)
        for row in obj.by_check:
            if row.carried_over is not None:
                yield row.carried_over.evidence, "EVD"
                yield row.carried_over.result, "RES"
            for side in (row.single, row.baseline, row.candidate, row.previous_result):
                if side is None:
                    continue
                if side.result is not None:
                    yield side.result, "RES"
                if side.evidence_ref is not None:
                    yield side.evidence_ref, "EVD"


def object_references(obj: GuardObject) -> Iterator[Ref]:
    """Include references stored in typed fields of legacy objects."""
    refs = {ref_key(ref): ref for ref in obj.refs}
    for ref, kind in _typed_refs(obj):
        _require_kind(ref, kind)
        refs.setdefault(ref_key(ref), ref)
    yield from refs.values()


def result_policy(result: Result, plan: Plan) -> Ref:
    policies = [ref for ref in result.refs if ref.kind == "POL"]
    if len({ref_key(ref) for ref in policies}) > 1:
        raise IntegrityError("RES has more than one policy reference")
    # Prior supported objects need no rewriting to recover their PLAN policy.
    return policies[0] if policies else plan.inputs.policy


def _evidence_plan(evidence: Evidence, resolve: Resolver) -> Plan:
    plan = resolve(evidence.plan)
    if not isinstance(plan, Plan):
        raise IntegrityError("EVD reference must resolve to a PLAN")
    environments = (dict(zip(("baseline", "candidate"), plan.inputs.environments))
                    if plan.mode == "update" else dict(zip(("single",), plan.inputs.environments)))
    if environments.get(evidence.side) != evidence.environment:
        raise IntegrityError("EVD side environment does not match its PLAN")
    if not any(item.check_id == evidence.check_id and item.selected for item in plan.items):
        raise IntegrityError("EVD check was not selected by its PLAN")
    return plan


def _carryover(check_id, carried, resolve: Resolver) -> None:
    evidence = resolve(carried.evidence)
    result = resolve(carried.result)
    if (not isinstance(evidence, Evidence) or not isinstance(result, Result)
            or result.evidence != carried.evidence or evidence.check_id != check_id
            or result.check_id != check_id or result.side != evidence.side):
        raise IntegrityError("carried RES/EVD relationships do not match the check")
    if result.verdicts.security != "PASS":
        raise IntegrityError("carried RES does not record security PASS")


def validate_relationships(obj: GuardObject, resolve: Resolver) -> None:
    if isinstance(obj, Plan):
        expected_count = 2 if obj.mode == "update" else 1
        if len(obj.inputs.environments) != expected_count:
            raise IntegrityError("PLAN environment count does not match its mode")
        if len({item.check_id for item in obj.items}) != len(obj.items):
            raise IntegrityError("PLAN repeats a check ID")
        if resolve(obj.inputs.policy).pack != obj.inputs.pack:
            raise IntegrityError("PLAN and POL refer to different packs")
        for item in obj.items:
            if item.carried_over is not None:
                _carryover(item.check_id, item.carried_over, resolve)
    elif isinstance(obj, Evidence):
        _evidence_plan(obj, resolve)
    elif isinstance(obj, Result):
        evidence = resolve(obj.evidence)
        if not isinstance(evidence, Evidence) or (obj.check_id, obj.side) != (evidence.check_id, evidence.side):
            raise IntegrityError("RES check and side must match its EVD")
        plan = _evidence_plan(evidence, resolve)
        policy = result_policy(obj, plan)
        if (getattr(obj, "as_of", None) is not None and getattr(obj, "evaluation_mode", None) is None
                and policy != plan.inputs.policy):
            raise IntegrityError("RES policy does not match the EVD's PLAN")
    elif isinstance(obj, Report):
        _validate_report(obj, resolve)


def _validate_report(report: Report, resolve: Resolver) -> None:
    manifest = {ref_key(ref) for ref in report.inputs_manifest}
    plans = [resolve(ref) for ref in report.inputs_manifest if ref.kind == "PLAN"]
    previous = {ref_key(plan.inputs.previous_plan) for plan in plans if plan.inputs.previous_plan is not None}
    current = [plan for plan in plans if ref_key(plan.ref()) not in previous]
    if len(current) != 1 or current[0].mode != report.mode:
        raise IntegrityError("RPT does not identify one current PLAN of its mode")
    plan = current[0]
    plan_ref = plan.ref()
    required_inputs = (plan.inputs.pack, plan.inputs.policy, *plan.inputs.environments)
    if any(ref_key(ref) not in manifest for ref in required_inputs):
        raise IntegrityError("RPT manifest omits an input of its current PLAN")
    items = {item.check_id: item for item in plan.items}
    previous_plan = resolve(plan.inputs.previous_plan) if plan.inputs.previous_plan is not None else None
    previous_items = {item.check_id: item for item in previous_plan.items} if previous_plan is not None else {}
    if len({row.check_id for row in report.by_check}) != len(report.by_check):
        raise IntegrityError("RPT repeats a check ID")
    for row in report.by_check:
        item = items.get(row.check_id)
        if item is None or row.selected != item.selected or row.carried_over != item.carried_over:
            raise IntegrityError("RPT row does not match the current PLAN")
        if row.carried_over is not None:
            _carryover(row.check_id, row.carried_over, resolve)
        for name in ("single", "baseline", "candidate", "previous_result"):
            side = getattr(row, name)
            if side is None:
                continue
            evidence, result = _report_side(row.check_id, side, manifest, resolve)
            if evidence is None:
                continue
            if name == "previous_result":
                previous_item = previous_items.get(row.check_id)
                carried = previous_item.carried_over if previous_item is not None else None
                previous_side = "candidate" if previous_plan is not None and previous_plan.mode == "update" else "single"
                exact_carried = carried is not None and side.result == carried.result
                previous_result = (previous_plan is not None and evidence.plan == plan.inputs.previous_plan
                                   and evidence.side == previous_side and (result is None or (
                                       result.evaluation_mode is None
                                       and result_policy(result, previous_plan) == previous_plan.inputs.policy)))
                if not (exact_carried or previous_result):
                    raise IntegrityError("RPT previous result does not belong to its previous PLAN")
                continue
            if row.carried_over is not None and name == "single":
                if side.result != row.carried_over.result or evidence.ref() != row.carried_over.evidence:
                    raise IntegrityError("RPT carried result does not match the PLAN")
                continue
            if evidence.plan != plan_ref or evidence.side != name or not item.selected:
                raise IntegrityError("RPT evidence does not belong to the current PLAN and side")
            if report.execution_attempt_id is not None and evidence.execution.attempt_id != report.execution_attempt_id:
                retained_manual = (evidence.execution.operator is not None and evidence.execution.attempt_id is None
                                   and evidence.data_origin == "DECLARED")
                if not retained_manual:
                    raise IntegrityError("RPT evidence does not belong to its execution attempt")
                if report.mode == "update" and (
                    row.comparison != "NOT_COMPARABLE" or row.functional_comparison != "NOT_COMPARABLE"
                ):
                    raise IntegrityError("retained manual evidence is outside the compared attempt")
            if result is not None:
                if result_policy(result, plan) != plan.inputs.policy or result.evaluation_mode is not None:
                    raise IntegrityError("RPT result does not belong to the current policy")
                if report.as_of is not None and result.as_of is not None and report.as_of != result.as_of:
                    raise IntegrityError("RPT and RES judgment times differ")


def _report_side(check_id, side, manifest, resolve):
    result = resolve(side.result) if side.result is not None else None
    if result is not None and (ref_key(side.result) not in manifest or result.check_id != check_id):
        raise IntegrityError("RPT result is outside its check or input manifest")
    if result is not None and (
        side.security != result.verdicts.security
        or side.functional not in (None, result.verdicts.functional)
        or side.performance not in (None, result.verdicts.performance)
        or side.verification_level not in (None, result.verification_level)
    ):
        raise IntegrityError("RPT verdict or verification level differs from the stored RES")
    if side.evidence_ref is None and (side.execution_status is not None or side.data_origin is not None):
        raise IntegrityError("RPT evidence status and origin need an exact EVD reference")
    ref = side.evidence_ref if side.evidence_ref is not None else (result.evidence if result is not None else None)
    if ref is None:
        return None, result
    evidence = resolve(ref)
    if (not isinstance(evidence, Evidence) or ref_key(ref) not in manifest or evidence.check_id != check_id
            or (result is not None and (result.evidence != ref or result.side != evidence.side))):
        raise IntegrityError("RPT evidence is outside its check, result or input manifest")
    if side.evidence_ref is not None and (
        side.execution_status != evidence.execution.status or side.data_origin != evidence.data_origin
    ):
        raise IntegrityError("RPT evidence status or origin differs from the stored EVD")
    if side.evidence_ref is not None and result is None and (
        side.security != "UNKNOWN" or side.functional is not None or side.performance is not None
    ):
        raise IntegrityError("RPT reports a verdict without a RES")
    return evidence, result
