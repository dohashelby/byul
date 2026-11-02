"""준비된 두 기동 세션을 각각 읽어 공급물이 실제로 어떻게 소비됐는지 관측한다.

엔진 생성·변조·재배포·재기동은 하지 않는다. 준비는 실험 절차가 맡고 여기서는 target·prepared만 받는다.
정상과 변경 기동은 세션이 다르므로 시간이 가까워도 합치지 않고, 한쪽 실패가 다른 쪽 사실을 지우지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from .base import DraftArtifact, EvidenceDraft
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .lifecycle_session import capture_session
from .owned_capture import _FILE_READER, MAX_BYTES
from . import supply_boot_contract as contract

VERSION = "0.1.0"
RECORD_DIRECTORY = "/guard-supply-boot/data/"
CONFIG_KEYS = {"normal", "challenge", "kubectl_binary", "max_duration_seconds"}


def record_path(session):
    return RECORD_DIRECTORY + session + "/boot-record.json"


class SupplyBootReader(KubernetesLifecycleReader):
    """기존 native 수집에 이 세션의 boot-record.json 읽기 한 개만 더한다."""

    def _program(self, container, argv, max_bytes):
        if (container == self.binding["engine"]["container"] and type(argv) is list
                and argv == ["python3", "-c", _FILE_READER, record_path(self.session), str(MAX_BYTES)] and max_bytes == 2 * MAX_BYTES):
            return "file"
        return super()._program(container, argv, max_bytes)


class KubernetesSupplyBootExecutor:
    name = "supply_boot"
    version = VERSION

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.cfg, self.runner, self.expected_target = cfg, runner, expected_target_sha256

    def validate_policy(self, check, values):
        if check.id not in contract.CHECK_PROFILES or getattr(check, "collection_profile", None) != contract.CHECK_PROFILES[check.id]:
            raise ValueError("SUPPLY_BOOT_PROFILE_UNSUPPORTED")
        contract.normalize_policy(check.id, values)

    def keys(self):
        return {}

    def _load(self, env, values, role):
        from guard.collectors.lifecycle import load_prepared
        section = self.cfg[role]
        if type(section) is not dict or set(section) != {"target", "prepared"}:
            raise ValueError
        with Path(section["target"]).open("rb") as stream:
            target = strict_json(stream.read(65537), limit=65536)
        prepared = load_prepared(section["prepared"])
        digest = sha256_hex(canonical_json(target))
        if (digest != values[f"supply_{role}_target_sha256"] or target.get("lab") is not True
                or sha256_hex(canonical_json(prepared)) != values[f"supply_{role}_prepared_sha256"]):
            raise ValueError
        return target, prepared, digest

    def _collect_role(self, check, env, values, role):
        """역할 하나의 세션. 입력·연결 오류는 진단 코드로만 남기고 앞선 역할을 지우지 않는다."""
        from guard.collectors import native_model_file_reads
        row = {"role": role, "collected": False, "sealed": False, "record_sha256": None, "owner_binding_verified": False,
               "native_files_covered": False, "diagnostic_codes": []}
        result = {"row": row, "packet": None, "native": None, "evaluated": None}
        try:
            target, prepared, digest = self._load(env, values, role)
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            row["diagnostic_codes"].append("SUPPLY_BOOT_INPUT_UNAVAILABLE")
            return result
        reader = None
        try:
            reader = SupplyBootReader(target, env, prepared, runner=self.runner, binary=self.cfg.get("kubectl_binary", "kubectl"),
                                      duration=self.cfg.get("max_duration_seconds", 120))
            reader.snapshot()
        except Exception:
            row["diagnostic_codes"].append("SUPPLY_BOOT_PREFLIGHT_UNAVAILABLE")
            return result
        engine = reader.binding["engine"]
        try:
            raw, provenance = reader.read_file(reader, target, container=engine["container"], image=engine["image"],
                                               path=record_path(prepared["session"]))
            packet = contract.validate_record(strict_json(raw, limit=MAX_BYTES), check_id=check.id, role=role,
                environment_ref=env.ref().model_dump(mode="json"), target_sha256=digest,
                source_sha256=values["supply_record_source_sha256"])
            row.update(collected=True, record_sha256=provenance["capture_sha256"],
                       owner_binding_verified=provenance["owner_binding_verified"])
            result["packet"] = packet
            result["evaluated"] = contract.evaluate_record(check.id, packet, values)
        except Exception:
            row["diagnostic_codes"].append("SUPPLY_BOOT_RECORD_UNAVAILABLE")
        if check.id in contract.NATIVE_CHECKS:
            try:
                row["sealed"] = reader.seal_session().get("sealed") is True
                captured = capture_session(reader, target, container=engine["container"], image=engine["image"], prepared=prepared,
                    owner_identity=reader.owner_identity, read_file=reader.read_file, image_check=reader.image_check)
                row["native_files_covered"] = captured["recorded_files_covered"]
                result["native"] = contract.native_consumption(native_model_file_reads.analyze(captured, prepared))
            except Exception:
                row["diagnostic_codes"].append("SUPPLY_BOOT_NATIVE_UNAVAILABLE")
        report = reader.report()
        if report.get("parent_cleanup_unverified"):
            result["cleanup"] = "SUPPLY_BOOT_PARENT_CLEANUP_UNVERIFIED"
        return result

    def run(self, check, env, policy_values, adapter, workdir):
        started = utcnow()
        self.validate_policy(check, policy_values)
        if set(self.cfg) - CONFIG_KEYS or set(contract.ROLES) - set(self.cfg):
            raise ValueError("SUPPLY_BOOT_CONFIGURATION_INVALID")
        if env.ref().sha256 != policy_values["supply_environment_sha256"]:
            raise ValueError("SUPPLY_BOOT_CONFIGURATION_INVALID")
        begin, end = contract.normalize_policy(check.id, policy_values)
        if not begin <= started <= end:
            raise ValueError("SUPPLY_BOOT_POLICY_WINDOW_EXPIRED")
        collected = {role: self._collect_role(check, env, policy_values, role) for role in contract.ROLES}
        return self._draft(check, env, policy_values, workdir, started, collected)

    def _project(self, check, collected):
        normal, challenge = collected["normal"], collected["challenge"]
        violations, missing = [], []
        native_rows = []
        for role, part in collected.items():
            if part["evaluated"] is not None:
                found, gaps = part["evaluated"]
                if role == "challenge":
                    violations.extend(found)
                missing.extend(f"{role}:{gap}" for gap in gaps)
            elif part["packet"] is None:
                missing.append(role + ":boot_record_unavailable")
            if check.id in contract.NATIVE_CHECKS:
                native = part["native"]
                if native is None:
                    missing.append(role + ":native_consumption_unavailable")
                    continue
                native_rows.append({"role": role, **native})
                missing.extend(f"{role}:{gap}" for gap in native["missing"] if role == "normal")
                if role == "challenge":
                    violations.extend({"reason": "unapproved_bytes_consumed", "path_sha256": row["path_sha256"]}
                                      for row in native["rows"] if row["mismatch"])
        change = (challenge["packet"] or {}).get("change")
        if change is not None and not change["sensor_started_before_consumption"]:
            missing.append("challenge:instrumentation_started_after_consumption")
        if check.id in contract.NATIVE_CHECKS:
            part = normal["native"]
            normal_ok = bool(part and part["rows"] and normal["row"]["native_files_covered"] and not part["unread_declared_paths"]
                             and all(row["matches_approved"] for row in part["rows"]))
        else:
            normal_ok = bool(normal["evaluated"] is not None and not normal["evaluated"][0] and not normal["evaluated"][1])
        complete = all(part["row"]["collected"] and part["row"]["owner_binding_verified"] and not part["row"]["diagnostic_codes"]
                       for part in collected.values())
        return violations, sorted(set(missing)), native_rows, normal_ok, complete, change

    def _draft(self, check, env, values, workdir, started, collected):
        violations, missing, native_rows, normal_ok, complete, change = self._project(check, collected)
        sessions = [part["row"] for part in collected.values()]
        cleanup = sorted({part["cleanup"] for part in collected.values() if part.get("cleanup")})
        observed = {"supply_boot_sessions": sessions, "supply_boot_change": change, "supply_boot_violations": violations,
                    "supply_boot_missing": missing, "supply_boot_complete": complete and not missing,
                    "supply_boot_normal_verified": normal_ok, "supply_boot_change_applied": bool(change and change["applied"]),
                    "supply_boot_record_authenticated": False, "supply_boot_native_consumption": native_rows}
        record_seen = any(part["packet"] is not None for part in collected.values())
        observations = {key: {"value": value, "collected": record_seen or key in ("supply_boot_sessions", "supply_boot_missing",
                              "supply_boot_complete", "supply_boot_record_authenticated")}
                        for key, value in observed.items()}
        observations["supply_boot_change"]["collected"] = change is not None
        for item in observations.values():
            if not item["collected"]:
                item["value"] = None
        workdir = Path(workdir)
        summary = {"profile": check.collection_profile, "sessions": sessions, "violations": violations, "missing": missing,
                   "native": native_rows, "producer_authenticated": False, "engine_started": False}
        workdir.mkdir(parents=True, exist_ok=True)
        artifact = workdir / "supply_boot_summary.json"
        artifact.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        gap = bool(any(row["diagnostic_codes"] for row in sessions) or cleanup)
        codes = sorted({code for row in sessions for code in row["diagnostic_codes"]})
        normal = {"attempted": any(row["collected"] for row in sessions[:1]), "succeeded": normal_ok if collected["normal"]["row"]["collected"] else None,
                  "detail": "승인 공급물로 준비된 기동 세션"}
        return EvidenceDraft(
            status=ExecStatus.ABORTED if gap else ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE
            if self.runner is not None or env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
            normal_control=normal, challenges=[{"step": step, "attempted": bool(change and change["applied"])} for step in check.challenge_steps],
            observations=observations, artifacts=[DraftArtifact(name="supply_boot_summary.json", path=artifact, collector=f"{self.name}/{self.version}")],
            comparability={"fixture_sha256": sha256_hex(canonical_json({"profile": check.collection_profile})),
                           "model_inventory_sha256": env.model.inventory_sha256, "gpu": env.runtime.gpu_model,
                           "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={"profile": check.collection_profile, "roles": list(contract.ROLES)}, started_at=started, finished_at=utcnow(),
            abort_reason="; ".join(codes) or None, cleanup_errors=cleanup)


__all__ = ["KubernetesSupplyBootExecutor", "SupplyBootReader", "record_path"]
