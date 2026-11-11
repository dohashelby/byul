"""Opt-in deployment gate over current approvals and sealed UPDATE evidence.

The guard file is a protected operator-owned input. It supplies the current
required CHK mapping and approved bundle/target, never a bundle-owned assertion.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

from .approval import HashListApprovalVerifier
from .policy import parse_policy_time, load_policy
from .policy_state import evaluate_policy, load_revocations
from .policy_bundle import VerifiedPolicyBundle, _strict_json


def _fields(value, fields, where):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{where}: invalid fields")
    return value


def _read(path):
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValueError("guard input paths must be absolute")
    return _strict_json(Path(path).read_bytes(), path)


def verify_deployment_guard(bundle: VerifiedPolicyBundle, guard_path: str,
                            *, now: datetime | None = None) -> dict:
    # Reuse UPDATE object validation and canonical Ref/hash semantics only in
    # this opt-in adapter; the dependency-free LAB runtime stays available.
    try:
        from guard.models.objects import Result, Evidence, Environment, Plan
        from guard.models.base import Ref
    except ImportError as error:
        raise ValueError("deployment guard requires the existing BYUL UPDATE package") from error
    checked = now or datetime.now(timezone.utc)
    if checked.tzinfo is None or checked.utcoffset() is None:
        raise ValueError("guard now must include timezone")
    guard = _fields(_read(guard_path), {"schema_version", "approvals_path", "pack_ref", "required_checks", "max_evidence_age_s", "records"}, "guard")
    if guard["schema_version"] != "runtime-deployment-guard/1.0":
        raise ValueError("unsupported deployment guard schema")
    age = guard["max_evidence_age_s"]
    if type(age) is not int or age <= 0:
        raise ValueError("max_evidence_age_s must be a positive integer")
    requirements = guard["required_checks"]
    if not isinstance(requirements, dict) or not requirements or any(
            not isinstance(k, str) or not k.startswith("CHK-") or not isinstance(v, str) or not v for k, v in requirements.items()):
        raise ValueError("required_checks must map non-empty CHK IDs to pinned pack check IDs")
    if len(set(requirements.values())) != len(requirements):
        raise ValueError("required pack check IDs must be unique")
    pack_ref = Ref.model_validate(guard["pack_ref"])
    if pack_ref.kind != "PACK":
        raise ValueError("pack_ref must reference PACK")
    by_role = {item.role: item for item in bundle.files}
    if not {"target", "gateway_policy"} <= by_role.keys():
        raise ValueError("guarded deployment requires target and gateway policy")
    target_file, policy_file = by_role["target"], by_role["gateway_policy"]
    target = _strict_json(target_file.content, "target")
    policy = _strict_json(policy_file.content, "gateway_policy")
    if target.get("target_id") != bundle.target_ref:
        raise ValueError("bundle target does not match target registration")
    # New verifier each time: source failures cannot use a previous approval.
    approvals_path = guard["approvals_path"]
    if not isinstance(approvals_path, str) or not Path(approvals_path).is_absolute():
        raise ValueError("approvals_path must be absolute")
    approval = HashListApprovalVerifier(approvals_path).verify(
        policy_file.content, policy["policy_id"], policy["policy_version"], policy.get("approval_ref"), now=checked)
    if approval.state != "verified":
        raise ValueError(f"current external policy approval failed: {approval.reason_code}")
    with bundle.materialized_files() as paths:
        policy_state = load_policy(paths["gateway_policy"])
    policy_status = evaluate_policy(policy_state, load_revocations(None), approval=approval, now=checked)
    if not policy_status["gateway_active"]:
        raise ValueError(f"current policy unavailable: {policy_status['reason_code']}")
    records = guard["records"]
    if not isinstance(records, list):
        raise ValueError("guard records must be a list")
    matches = [r for r in records if isinstance(r, dict) and r.get("bundle_sha256") == bundle.sha256]
    if len(matches) != 1:
        raise ValueError("approved bundle evidence missing or ambiguous")
    record = _fields(matches[0], {"bundle_sha256", "target_sha256", "model_inventory_sha256", "valid_from", "valid_until", "revoked", "checks", "artifact_root"}, "record")
    if record["revoked"] is not False:
        raise ValueError("bundle approval revoked")
    if any(not isinstance(record[k], str) for k in ("valid_from", "valid_until")):
        raise ValueError("bundle evidence validity must contain timezone-aware strings")
    inventory_sha = record["model_inventory_sha256"]
    if (not isinstance(inventory_sha, str) or len(inventory_sha) != 64
            or any(character not in "0123456789abcdef" for character in inventory_sha)):
        raise ValueError("model inventory must be a lowercase SHA-256")
    start, expiry = (parse_policy_time(record[k]) for k in ("valid_from", "valid_until"))
    if not start <= checked < expiry:
        raise ValueError("bundle evidence outside current validity window")
    if record["target_sha256"] != target_file.sha256:
        raise ValueError("approved target hash mismatch")
    checks = record["checks"]
    if not isinstance(checks, dict) or set(checks) != set(requirements):
        raise ValueError("required CHK evidence missing or unexpected")
    if not isinstance(record["artifact_root"], str):
        raise ValueError("artifact_root must be an absolute path string")
    artifact_root = Path(record["artifact_root"])
    if not artifact_root.is_absolute():
        raise ValueError("artifact_root must be absolute")
    artifact_root = artifact_root.resolve(strict=True)
    checked_refs = {}

    def load_object(path, cls):
        value = cls.model_validate(_read(path))
        if not value.hash_ok():
            raise ValueError("UPDATE evidence object hash mismatch")
        return value

    for framework_id, internal_id in requirements.items():
        source = _fields(checks[framework_id], {"result_path", "result_ref", "evidence_path", "environment_path", "plan_path"}, framework_id)
        result = load_object(source["result_path"], Result)
        evidence = load_object(source["evidence_path"], Evidence)
        environment = load_object(source["environment_path"], Environment)
        plan = load_object(source["plan_path"], Plan)
        if (result.ref() != Ref.model_validate(source["result_ref"]) or result.evidence != evidence.ref()
                or evidence.environment != environment.ref() or evidence.plan != plan.ref()
                or evidence.environment not in plan.inputs.environments or plan.inputs.pack != pack_ref):
            raise ValueError(f"{framework_id}: exact UPDATE Ref/pack mismatch")
        if result.check_id != internal_id or evidence.check_id != internal_id or result.side != evidence.side:
            raise ValueError(f"{framework_id}: check/side mismatch")
        items = [item for item in plan.items if item.check_id == internal_id]
        if (len(items) != 1 or not items[0].selected or items[0].gaps
                or str(items[0].applicability) != "APPLICABLE"
                or not isinstance(items[0].executor, str) or not items[0].executor.strip()
                or evidence.execution.executor is None
                or evidence.execution.executor.name != items[0].executor):
            raise ValueError(f"{framework_id}: selected executable plan item missing")
        if (str(result.verdicts.security) != "PASS" or result.missing_observations or result.exception is not None
                or str(result.verification_level) != "MEASURED" or str(result.data_origin) != "MEASURED"
                or str(evidence.data_origin) != "MEASURED" or str(evidence.execution.status) != "COMPLETED"
                or evidence.execution.operator is not None or evidence.execution.cleanup_errors
                or not evidence.normal_control.attempted or evidence.normal_control.succeeded is not True):
            raise ValueError(f"{framework_id}: required measured security PASS unavailable")
        started, finished = evidence.execution.started_at, evidence.execution.finished_at
        if (started is None or finished is None or not started <= finished <= result.created_at <= checked
                or (checked - finished).total_seconds() > age):
            raise ValueError(f"{framework_id}: evidence stale or invalid execution interval")
        engine, model = target.get("engine", {}), target.get("model", {})
        if (environment.engine.name != engine.get("kind") or environment.engine.version != engine.get("version")
                or environment.engine.image_digest != engine.get("image_digest")
                or environment.model.id != model.get("id") or environment.model.revision != model.get("revision")
                or not record["model_inventory_sha256"]
                or environment.model.inventory_sha256 != record["model_inventory_sha256"]):
            raise ValueError(f"{framework_id}: tested engine/model/version mismatch")
        for artifact in evidence.artifacts:
            relative = Path(artifact.path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("artifact path must remain under approved artifact_root")
            path = (artifact_root / relative).resolve(strict=True)
            if not path.is_relative_to(artifact_root):
                raise ValueError("artifact escapes approved artifact_root")
            raw = path.read_bytes()
            if len(raw) != artifact.bytes or hashlib.sha256(raw).hexdigest() != artifact.sha256:
                raise ValueError("required evidence artifact missing or changed")
        checked_refs[framework_id] = result.ref().model_dump(mode="json")
    return {"state": "verified", "checked_at": checked.isoformat(), "bundle_sha256": bundle.sha256,
            "target_sha256": target_file.sha256, "pack_ref": pack_ref.model_dump(mode="json"),
            "results": checked_refs, "approval": approval.status()}
