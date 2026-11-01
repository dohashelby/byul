"""OBS-06: 두 신원의 Pod 로그 조회와 사본별 반출·저장 보호 기록을 각각 수집한다.

조회 수집은 기존 capture_record_access를 그대로 쓴다. 클라이언트 오류는 정책 거부의 근거가 아니다.
사본 기록은 소유 위치의 닫힌 형식이며 생산자는 미인증이다. 키 값·로그 본문은 받지 않는다.
두 신원의 조회만 수집되면 부분 관측으로 남긴다.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from .base import DraftArtifact, EvidenceDraft
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .kubernetes_record_access import capture_record_access
from .owned_capture import _FILE_READER, MAX_BYTES
from .rank_failure_contract import stamp

PROFILE = "kubernetes-record-access-scope-v1"
VERSION = "0.1.0"
INVENTORY_SCHEMA = "guard-record-copy-inventory/1"
POLICY_TYPES = {"record_environment_sha256": "str", "record_target_sha256": "str", "record_subject_plan_sha256": "str",
                "record_inventory_session": "str", "record_inventory_source_sha256": "str",
                "record_validity_start": "str", "record_validity_end": "str"}
OBSERVATIONS = {"record_access_rows": "list", "record_unauthorized_read_verified": "bool", "record_normal_read_verified": "bool",
                "record_access_gaps": "list", "record_copy_inventory": "list", "record_copy_violations": "list",
                "record_copy_missing": "list", "record_access_partial": "bool", "record_denial_verified": "bool",
                "record_scope_complete": "bool", "record_inventory_authenticated": "bool"}
_ROW = {"location_sha256", "kind", "grade", "encryption_required", "encryption_applied", "key_reference_sha256",
        "unauthorized_export", "unauthorized_decrypt"}
_KINDS = {"original", "rotated", "collector", "sink", "backup", "diagnostic"}
_SHA = re.compile(r"[0-9a-f]{64}")


def inventory_path(session):
    return "/guard-record-access/data/" + session + "/copy-inventory.json"


def normalize_policy(values):
    try:
        if type(values) is not dict or set(values) != set(POLICY_TYPES):
            raise ValueError
        for key in ("record_environment_sha256", "record_target_sha256", "record_subject_plan_sha256", "record_inventory_source_sha256"):
            if type(values[key]) is not str or _SHA.fullmatch(values[key]) is None:
                raise ValueError
        if type(values["record_inventory_session"]) is not str or re.fullmatch("[0-9a-f]{32}", values["record_inventory_session"]) is None:
            raise ValueError
        start, end = stamp(values["record_validity_start"]), stamp(values["record_validity_end"])
        if not 0 < (end - start).total_seconds() <= 3600:
            raise ValueError
        return start, end
    except (KeyError, TypeError, ValueError):
        raise ValueError("RECORD_ACCESS_POLICY_INVALID") from None


def validate_inventory(packet, *, environment_ref, target_sha256, source_sha256):
    if (type(packet) is not dict or set(packet) != {"schema", "environment_ref", "target_sha256", "source_sha256", "copies"}
            or packet["schema"] != INVENTORY_SCHEMA or packet["environment_ref"] != environment_ref
            or packet["target_sha256"] != target_sha256 or packet["source_sha256"] != source_sha256
            or type(packet["copies"]) is not list or not 1 <= len(packet["copies"]) <= 64):
        raise ValueError("RECORD_ACCESS_INVENTORY_INVALID")
    for row in packet["copies"]:
        if (type(row) is not dict or set(row) != _ROW or row["kind"] not in _KINDS or type(row["grade"]) is not str
                or type(row["location_sha256"]) is not str or _SHA.fullmatch(row["location_sha256"]) is None
                or row["key_reference_sha256"] is not None and (type(row["key_reference_sha256"]) is not str
                    or _SHA.fullmatch(row["key_reference_sha256"]) is None)
                or any(row[k] is not None and type(row[k]) is not bool
                       for k in ("encryption_required", "encryption_applied", "unauthorized_export", "unauthorized_decrypt"))):
            raise ValueError("RECORD_ACCESS_INVENTORY_INVALID")
    return packet


def evaluate_inventory(packet):
    """null은 미확인이다. 저장 보호 요구가 미정인 사본은 위반도 충족도 아니다."""
    violations, missing = [], []
    for row in packet["copies"]:
        where = row["location_sha256"]
        if row["unauthorized_export"] is True:
            violations.append({"reason": "unauthorized_export_succeeded", "location_sha256": where})
        if row["unauthorized_decrypt"] is True:
            violations.append({"reason": "unauthorized_decrypt_succeeded", "location_sha256": where})
        if row["encryption_required"] is True and row["encryption_applied"] is False:
            violations.append({"reason": "required_encryption_absent", "location_sha256": where})
        for key in ("unauthorized_export", "encryption_required"):
            if row[key] is None:
                missing.append(f"{key}_undecided:{where}")
        if row["encryption_required"] is True and (row["encryption_applied"] is None or row["key_reference_sha256"] is None
                                                   or row["unauthorized_decrypt"] is None):
            missing.append("encryption_evidence_incomplete:" + where)
    kinds = {row["kind"] for row in packet["copies"]}
    missing.extend("copy_location_unobserved:" + kind for kind in sorted({"original", "collector", "backup"} - kinds))
    return violations, sorted(set(missing))


class InventoryReader(KubernetesLifecycleReader):
    """사본 기록 한 개만 읽는다. native 세션 읽기는 허용하지 않는다."""

    def __init__(self, target, env, session, *, binary, duration, runner=None):
        from .kubernetes_lifecycle_capture import validate_target
        self._initialize(validate_target(target, env), target, {"session": session}, binary=binary, duration=duration, runner=runner)

    def _program(self, container, argv, max_bytes):
        if (container != self.binding["engine"]["container"] or max_bytes != 2 * MAX_BYTES
                or argv != ["python3", "-c", _FILE_READER, inventory_path(self.session), str(MAX_BYTES)]):
            raise ValueError("RECORD_ACCESS_INVENTORY_PROGRAM_INVALID")
        return "file"


class KubernetesRecordAccessExecutor:
    name = "record_access"
    version = VERSION

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.cfg, self.runner, self.expected_target = cfg, runner, expected_target_sha256

    def keys(self):
        return {}

    def validate_policy(self, check, values):
        if check.id != "OBS-06" or getattr(check, "collection_profile", None) != PROFILE:
            raise ValueError("RECORD_ACCESS_PROFILE_UNSUPPORTED")
        normalize_policy(values)

    def _inventory(self, env, target, values, digest):
        reader = InventoryReader(target, env, values["record_inventory_session"], binary=self.cfg.get("kubectl_binary", "kubectl"),
                                 duration=self.cfg.get("max_duration_seconds", 60), runner=self.runner)
        engine = reader.binding["engine"]
        raw, provenance = reader.read_file(reader, target, container=engine["container"], image=engine["image"],
                                           path=inventory_path(values["record_inventory_session"]))
        if provenance.get("owner_binding_verified") is not True:
            raise ValueError("RECORD_ACCESS_INVENTORY_OWNER_CHANGED")
        return validate_inventory(strict_json(raw, limit=MAX_BYTES), environment_ref=env.ref().model_dump(mode="json"),
                                  target_sha256=digest, source_sha256=values["record_inventory_source_sha256"])

    def run(self, check, env, policy_values, adapter, workdir):
        started = utcnow()
        self.validate_policy(check, policy_values)
        begin, end = normalize_policy(policy_values)
        try:
            if set(self.cfg) - {"target", "subjects", "kubectl_binary", "max_duration_seconds"}:
                raise ValueError
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            with Path(self.cfg["subjects"]).open("rb") as stream:
                subjects = strict_json(stream.read(262145), limit=262144)
            digest = sha256_hex(canonical_json(target))
            if (digest != policy_values["record_target_sha256"] or env.ref().sha256 != policy_values["record_environment_sha256"]
                    or sha256_hex(canonical_json(subjects)) != policy_values["record_subject_plan_sha256"]
                    or self.expected_target is not None and self.expected_target != digest or not begin <= started <= end):
                raise ValueError
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("RECORD_ACCESS_CONFIGURATION_INVALID") from None
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        diagnostics, capture, packet, evaluated = [], None, None, None
        try:
            capture = capture_record_access(env, target, subjects, expected_policy_ref=subjects["policy_ref"], workdir=workdir,
                binary=self.cfg.get("kubectl_binary", "kubectl"), duration=self.cfg.get("max_duration_seconds", 60),
                _runner=self.runner)
        except Exception:
            diagnostics.append("RECORD_ACCESS_LOG_READ_UNAVAILABLE")
        try:
            packet = self._inventory(env, target, policy_values, digest)
            evaluated = evaluate_inventory(packet)
        except Exception:
            diagnostics.append("RECORD_ACCESS_INVENTORY_UNAVAILABLE")
        return self._draft(check, env, workdir, started, capture, packet, evaluated, diagnostics)

    def _draft(self, check, env, workdir, started, capture, packet, evaluated, diagnostics):
        unauthorized = capture.get("unauthorized_read_verified") if capture else None
        rows = [{key: row[key] for key in ("case_id", "role", "attempted", "identity_verified", "binding_verified",
                                           "read_verified", "returncode", "byte_count", "gaps")} for row in capture["rows"]] if capture else None
        violations, missing = evaluated if evaluated is not None else (None, None)
        complete = bool(evaluated is not None and capture and not capture["gaps"] and not missing)
        collected = capture is not None
        control = capture["normal_control"] if capture else {"attempted": False, "succeeded": None}
        observations = {
            "record_access_rows": {"value": rows, "collected": collected},
            "record_unauthorized_read_verified": {"value": unauthorized is True, "collected": collected and bool(rows[1]["attempted"])},
            "record_normal_read_verified": {"value": control["succeeded"] is True, "collected": collected},
            "record_access_gaps": {"value": capture["gaps"] if capture else None, "collected": collected},
            "record_copy_inventory": {"value": packet["copies"] if packet else None, "collected": packet is not None},
            "record_copy_violations": {"value": violations, "collected": evaluated is not None},
            "record_copy_missing": {"value": missing, "collected": evaluated is not None},
            "record_access_partial": {"value": evaluated is None or bool(missing), "collected": True},
            "record_denial_verified": {"value": False, "collected": True},
            "record_scope_complete": {"value": complete, "collected": True},
            "record_inventory_authenticated": {"value": False, "collected": True}}
        for item in observations.values():
            if not item["collected"]:
                item["value"] = None
        artifact = workdir / "record_access_summary.json"
        artifact.write_text(json.dumps({"profile": PROFILE, "rows": rows, "diagnostic_codes": diagnostics, "copies": packet and packet["copies"]},
                                       ensure_ascii=False, sort_keys=True), encoding="utf-8")
        gap = bool(diagnostics or (capture and capture["gaps"]))
        cleanup = [] if capture is None or capture["cleanup"]["complete"] else ["RECORD_ACCESS_CLEANUP_UNVERIFIED"]
        return EvidenceDraft(
            status=ExecStatus.ABORTED if gap else ExecStatus.COMPLETED,
            data_origin=DataOrigin.EXAMPLE if self.runner is not None or env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
            normal_control={"attempted": control["attempted"], "succeeded": control["succeeded"],
                            "detail": "허용 신원의 전후 조회. 정상 기록 전체가 아니라 선택 Pod 로그 범위다."},
            challenges=[{"step": step, "attempted": collected and index == 1 and bool(rows[1]["attempted"])}
                        for index, step in enumerate(check.challenge_steps)],
            observations=observations, artifacts=[DraftArtifact(name="record_access_summary.json", path=artifact, collector=f"{self.name}/{self.version}")],
            comparability={"fixture_sha256": sha256_hex(canonical_json({"profile": PROFILE})), "model_inventory_sha256": env.model.inventory_sha256,
                           "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={"profile": PROFILE}, started_at=started, finished_at=utcnow(),
            abort_reason="; ".join(sorted(set(diagnostics))) or None, cleanup_errors=cleanup)
