"""준비된 두 기동 세션을 각각 읽어 공급물이 실제로 어떻게 소비됐는지 관측한다.

엔진 생성·변조·재배포·재기동은 하지 않는다. 준비는 실험 절차가 맡고 여기서는 target·prepared만 받는다.
정상과 변경 기동은 세션이 다르므로 시간이 가까워도 합치지 않고, 한쪽 실패가 다른 쪽 사실을 지우지 않는다.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from .base import EvidenceDraft
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader, validate_target
from .lifecycle_session import capture_session
from .owned_capture import _FILE_READER, MAX_BYTES
from . import supply_boot_contract as contract

VERSION = "0.1.0"
RECORD_DIRECTORY = "/guard-supply-boot/data/"
CONFIG_KEYS = {"normal", "challenge", "kubectl_binary", "max_duration_seconds"}
NORMAL_FILE_READS_OPTION = "normal_file_reads_only"
NORMAL_FILE_READS_PROFILE = "owned-normal-model-file-reads/1"
_MODEL_ROOT = "/models"
_REFERENCE_KEY = "supply_normal_reference_manifest_sha256"
_NORMAL_BINDING_KEYS = {"supply_environment_sha256", "supply_normal_target_sha256",
                        "supply_normal_prepared_sha256", _REFERENCE_KEY,
                        "supply_validity_start", "supply_validity_end"}


def record_path(session):
    return RECORD_DIRECTORY + session + "/boot-record.json"


def _record_location(role_input, env):
    target, prepared, _ = role_input
    try:
        owner = validate_target(target, env)["engine"]
    except ValueError:
        # Leave the ordinary per-role preflight to record the invalid target.
        return None
    return owner["namespace_uid"], owner["pod_uid"], owner["container_id"], record_path(prepared["session"])


def _read_projection(captured, prepared, digest, prepared_sha256, analysis):
    native = contract.native_consumption(analysis)
    native["source_binding"] = {"session": prepared["session"], "target_sha256": digest,
                                "prepared_sha256": prepared_sha256, "declared_scope": analysis["declared_scope"]}
    for projection, candidate in zip(native["rows"], analysis["candidates"], strict=True):
        source = captured["recordings"][candidate["recording"]]["capture"]["provenance"]
        projection["source_binding"] = {"recording_sha256": source["capture_sha256"],
            "recording_owner_binding_verified": source["owner_binding_verified"],
            "process_start_ticks": candidate["start_ticks"], "root_event": candidate["root_event"],
            "source_events": candidate["source_events"], "hash_status": candidate["hash_status"],
            "reference_sha256": candidate["reference_sha256"],
            "read_return_sha256": candidate["read_return_bytes_sha256"]}
        if "loader_iterator_observation_sha256" in candidate:
            binding = projection["source_binding"]
            binding["open_event"] = binding.pop("root_event")
            binding.update({key: candidate[key] for key in ("loader_iterator_observation_sha256",
                                                             "loader_call_sha256", "model_object_sha256")})
    return native


def _normal_validity(values):
    from guard.adapters.vllm_file_read_sensor import fingerprint
    from .rank_failure_contract import stamp

    if (type(values) is not dict or set(values) != _NORMAL_BINDING_KEYS
            or any(not fingerprint(values[key]) for key in _NORMAL_BINDING_KEYS if key.endswith("_sha256"))):
        raise ValueError("SUPPLY_BOOT_NORMAL_BINDING_INVALID")
    start, end = stamp(values["supply_validity_start"]), stamp(values["supply_validity_end"])
    if not 0 < (end - start).total_seconds() <= 3600:
        raise ValueError("SUPPLY_BOOT_NORMAL_BINDING_INVALID")
    return start, end


def _approved_reference(path, expected_sha256, env, target, prepared):
    from guard.adapters import vllm_file_read_sensor as sensor
    from guard.collectors.model_manifest import _load_manifest
    from guard.collectors.model_read_scope import validate_binding

    validate_binding(prepared, env, target)
    raw = sensor.dependency(path, expected_sha256)
    manifest = _load_manifest(strict_json(raw, limit=sensor.MAX_SCOPE_BYTES), None)
    if (manifest.get("schema") != "guard.model-manifest/1" or manifest.get("model_id") != env.model.id
            or manifest["inventory_sha256"] != env.model.inventory_sha256):
        raise ValueError("SUPPLY_BOOT_NORMAL_REFERENCE_MISMATCH")
    files = []
    for row in manifest["files"]:
        if not row["path"].endswith(".safetensors"):
            continue
        if (row["path"] != PurePosixPath(row["path"]).as_posix() or "\x00" in row["path"]
                or type(row["bytes"]) is not int or not 0 < row["bytes"] <= sensor.MAX_HASH_BYTES):
            raise ValueError("SUPPLY_BOOT_NORMAL_REFERENCE_MISMATCH")
        files.append({"normalized_path_sha256": sha256_hex(str(PurePosixPath(_MODEL_ROOT) / row["path"]).encode()),
                      "reference_sha256": row["sha256"], "bytes": row["bytes"]})
    scope = prepared["model_read_scope"]
    if (not files or scope["files"] != files or scope["manifest_bytes_sha256"] != expected_sha256
            or scope["nonsecret_model_weights"] is not True or scope["hash_model_bytes"] is not True
            or scope["max_hash_bytes"] < max(row["bytes"] for row in files)
            or scope["data_origin"] != ("EXAMPLE" if env.data_origin == DataOrigin.EXAMPLE else "DECLARED")):
        raise ValueError("SUPPLY_BOOT_NORMAL_REFERENCE_MISMATCH")


def _model_mount(snapshot):
    spec = snapshot["pod"]["spec"]
    container, = spec["containers"]
    mount, = [row for row in container["volumeMounts"] if row.get("mountPath") == _MODEL_ROOT]
    if mount.get("readOnly") is not True or any(mount.get(key) for key in ("subPath", "subPathExpr", "mountPropagation")):
        raise ValueError("SUPPLY_BOOT_NORMAL_MODEL_MOUNT_UNVERIFIED")
    root = PurePosixPath(_MODEL_ROOT)
    for other in container["volumeMounts"]:
        if other is mount:
            continue
        path = PurePosixPath(other["mountPath"])
        if (path.root != "/" or ".." in path.parts or path.is_relative_to(root) or root.is_relative_to(path)):
            raise ValueError("SUPPLY_BOOT_NORMAL_MODEL_MOUNT_UNVERIFIED")
    volume, = [row for row in spec["volumes"] if row["name"] == mount["name"]]
    return mount, volume


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

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None,
                 expected_prepared_sha256_by_role=None):
        self.cfg, self.runner, self.expected_target = cfg, runner, expected_target_sha256
        if expected_prepared_sha256_by_role is not None and (
                type(expected_prepared_sha256_by_role) is not dict
                or set(expected_prepared_sha256_by_role) != set(contract.ROLES)
                or any(type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value)
                       for value in expected_prepared_sha256_by_role.values())):
            raise ValueError('SUPPLY_BOOT_PREPARED_PINS_INVALID')
        self.expected_prepared_sha256_by_role = dict(expected_prepared_sha256_by_role or {})
        if cfg.get(NORMAL_FILE_READS_OPTION) is True:
            self.version = "0.1.0+k8s-normal-model-file-reads"

    def validate_policy(self, check, values):
        if check.id not in contract.CHECK_PROFILES or getattr(check, "collection_profile", None) != contract.CHECK_PROFILES[check.id]:
            raise ValueError("SUPPLY_BOOT_PROFILE_UNSUPPORTED")
        if self.expected_prepared_sha256_by_role and check.id not in {'SUP-15', 'SUP-16'}:
            raise ValueError('SUPPLY_BOOT_PREPARED_PINS_INVALID')
        if NORMAL_FILE_READS_OPTION in self.cfg:
            self.validate_configuration(check)
            _normal_validity(values)
            return
        contract.normalize_policy(check.id, values)
        if check.id == "SUP-05" and any(
                values[f"supply_normal_{kind}_sha256"] == values[f"supply_challenge_{kind}_sha256"]
                for kind in ("target", "prepared")):
            raise ValueError("SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED")

    def validate_configuration(self, check):
        if NORMAL_FILE_READS_OPTION not in self.cfg:
            return
        section = self.cfg.get("normal")
        if (self.cfg[NORMAL_FILE_READS_OPTION] is not True or check.id != "SUP-05" or check.executor != self.name
                or set(self.cfg) - ((CONFIG_KEYS - {"challenge"}) | {NORMAL_FILE_READS_OPTION})
                or type(section) is not dict or set(section) != {"target", "prepared", "reference_manifest"}
                or any(type(path) is not str or not path for path in section.values())):
            raise ValueError("SUPPLY_BOOT_NORMAL_CONFIGURATION_INVALID")

    def keys(self):
        return {}

    def _load(self, env, values, role):
        from guard.collectors.lifecycle import load_prepared
        section = self.cfg[role]
        if (type(section) is not dict or set(section) - {"target", "prepared", "worker_shard_input"}
                or not {"target", "prepared"} <= set(section)):
            raise ValueError
        with Path(section["target"]).open("rb") as stream:
            target = strict_json(stream.read(65537), limit=65536)
        expected_prepared = self.expected_prepared_sha256_by_role.get(role)
        prepared = (load_prepared(section['prepared'], expected_sha256=expected_prepared)
                    if expected_prepared is not None else load_prepared(section['prepared']))
        digest = sha256_hex(canonical_json(target))
        if (digest != values[f"supply_{role}_target_sha256"] or target.get("lab") is not True
                or sha256_hex(canonical_json(prepared)) != values[f"supply_{role}_prepared_sha256"]):
            raise ValueError
        return target, prepared, digest

    def _load_available(self, env, values, role):
        try:
            return self._load(env, values, role)
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            return None

    def _validate_paired_configuration(self, env, values):
        if (set(self.cfg) - CONFIG_KEYS or set(contract.ROLES) - set(self.cfg)
                or env.ref().sha256 != values['supply_environment_sha256']):
            raise ValueError('SUPPLY_BOOT_CONFIGURATION_INVALID')

    def sup15_prepared_roles(self, env, values, *, require_complete=False):
        """Check bound role files locally. Readiness requires both; runtime can keep an available role."""
        return self._prepared_roles(env, values, require_complete=require_complete, distinct_locations=True)

    def prepared_supply_roles(self, check, env, values):
        """Validate both local declarations without contacting owners or reading live boot records."""
        if check.executor != self.name or NORMAL_FILE_READS_OPTION in self.cfg:
            raise ValueError('SUPPLY_BOOT_PROFILE_UNSUPPORTED')
        self.validate_policy(check, values)
        required = {'target', 'prepared'}
        allowed = required | {'worker_shard_input'} if check.id in {'SUP-06', 'SUP-15'} else required
        for role in contract.ROLES:
            section = self.cfg.get(role)
            if type(section) is not dict or not required <= set(section) <= allowed:
                raise ValueError('SUPPLY_BOOT_INPUT_UNAVAILABLE')
        roles = self._prepared_roles(env, values, require_complete=True, distinct_locations=True)
        if check.id == 'SUP-05' and roles['normal'][1]['session'] == roles['challenge'][1]['session']:
            raise ValueError('SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED')
        if check.id == 'SUP-06':
            for role, (_, _, digest) in roles.items():
                if 'worker_shard_input' in self.cfg[role]:
                    try:
                        self._worker_shard_input(env, values, role, digest)
                    except (KeyError, TypeError, ValueError, OSError):
                        raise ValueError('B_WORKER_SHARD_INPUT_UNAVAILABLE') from None
        return roles

    def _prepared_roles(self, env, values, *, require_complete, distinct_locations):
        if require_complete:
            self._validate_paired_configuration(env, values)
        roles = {role: self._load_available(env, values, role) for role in contract.ROLES}
        if distinct_locations:
            normal, challenge = (_record_location(roles[role], env) if roles[role] is not None else None
                                 for role in contract.ROLES)
            if normal is not None and normal == challenge:
                # A record replaced between reads cannot supply two separate role sessions.
                raise ValueError('SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED')
            if require_complete and (normal is None or challenge is None):
                raise ValueError('SUPPLY_BOOT_INPUT_UNAVAILABLE')
        if require_complete:
            if any(value is None for value in roles.values()):
                raise ValueError('SUPPLY_BOOT_INPUT_UNAVAILABLE')
            for target, prepared, _ in roles.values():
                self._role_reader(target, env, prepared)
        return roles

    def _role_reader(self, target, env, prepared):
        return SupplyBootReader(target, env, prepared, runner=self.runner,
                                binary=self.cfg.get("kubectl_binary", "kubectl"),
                                duration=self.cfg.get("max_duration_seconds", 120))

    def _worker_shard_input(self, env, values, role, digest):
        from .common_consumption import load_input, worker_shards

        record, scope = load_input(self.cfg[role]['worker_shard_input'],
            environment_ref=env.ref().model_dump(mode='json'), target_sha256=digest,
            prepared_sha256=values[f'supply_{role}_prepared_sha256'])
        return record, scope, worker_shards(record, scope, values)

    def _collect_role(self, check, env, values, role, *, role_inputs=None):
        """역할 하나의 세션. 입력·연결 오류는 진단 코드로만 남기고 앞선 역할을 지우지 않는다."""
        from guard.collectors import native_model_file_reads
        row = {"role": role, "collected": False, "sealed": False, "record_sha256": None, "owner_binding_verified": False,
               "native_files_covered": False, "diagnostic_codes": []}
        result = {"row": row, "packet": None, "native": None, "evaluated": None}
        loaded = self._load_available(env, values, role) if role_inputs is None else role_inputs[role]
        if loaded is None:
            row["diagnostic_codes"].append("SUPPLY_BOOT_INPUT_UNAVAILABLE")
            return result
        target, prepared, digest = loaded
        if "worker_shard_input" in self.cfg[role]:
            try:
                if check.id != "SUP-06":
                    raise ValueError("B_WORKER_SHARD_CHECK_UNSUPPORTED")
                settings = self.cfg[role]["worker_shard_input"]
                record, scope, result["worker_shards"] = self._worker_shard_input(env, values, role, digest)
                # Preserve the validated record's own worker/event binding. The
                # local file check does not independently verify its live owner.
                row["worker_shard_input"] = {
                    **result["worker_shards"], "data_origin": "EXAMPLE",
                    "record_sha256": sha256_hex(canonical_json(record)),
                    "reported_source": {
                        "schema": record["schema"], "producer": record["producer"],
                        "file_sha256": settings["sha256"],
                        "environment_ref": scope["environment_ref"],
                        "target_sha256": scope["target_sha256"], "prepared_sha256": scope["prepared_sha256"],
                        "session_sha256": sha256_hex(scope["session"].encode()),
                        "owner_binding_verified": False,
                        "bindings": [
                            {"generation_sha256": sha256_hex(canonical_json([
                                binding["pod_uid"], binding["container_id"], binding["restart_count"]])),
                             "worker_pid": binding["worker_pid"], "start_ticks": binding["start_ticks"],
                             "rank": binding["rank"]}
                            for binding in record["bindings"]
                        ],
                        "events": record["events"],
                    },
                }
            except (KeyError, TypeError, ValueError, OSError) as exc:
                row["diagnostic_codes"].append("B_WORKER_SHARD_INPUT_UNAVAILABLE")
                row["worker_shard_input_error"] = {"type": type(exc).__name__, "message": str(exc)[:1024]}
        reader = None
        try:
            reader = self._role_reader(target, env, prepared)
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
            if check.id in {"LIF-01", "SUP-15"} and provenance["owner_binding_verified"] is not True:
                raise ValueError("SUPPLY_BOOT_RECORD_OWNER_UNVERIFIED")
            row.update(collected=True, record_sha256=provenance["capture_sha256"],
                       owner_binding_verified=provenance["owner_binding_verified"])
            result["packet"] = packet
            if check.id in {'SUP-15', 'SUP-16'}:
                result['record_bytes'] = raw
            result["evaluated"] = contract.evaluate_record(check.id, packet, values)
            if check.id == "LIF-01":
                row["reported_stage_costs"] = [stage for stage in packet["rows"]
                                               if stage["stage"] in values["supply_stage_budget_ms"]]
        except Exception:
            row["diagnostic_codes"].append("SUPPLY_BOOT_RECORD_UNAVAILABLE")
        if check.id in contract.NATIVE_CHECKS:
            try:
                row["sealed"] = reader.seal_session().get("sealed") is True
                captured = capture_session(reader, target, container=engine["container"], image=engine["image"], prepared=prepared,
                    owner_identity=reader.owner_identity, read_file=reader.read_file, image_check=reader.image_check)
                row["native_files_covered"] = captured["recorded_files_covered"]
                analysis = native_model_file_reads.analyze(captured, prepared)
                if check.id == "SUP-05":
                    result["native"] = _read_projection(captured, prepared, digest,
                                                        values[f"supply_{role}_prepared_sha256"], analysis)
                else:
                    result["native"] = contract.native_consumption(analysis)
            except Exception:
                row["diagnostic_codes"].append("SUPPLY_BOOT_NATIVE_UNAVAILABLE")
        try:
            report = reader.report()
            if report.get("parent_cleanup_unverified"):
                result["cleanup"] = "SUPPLY_BOOT_PARENT_CLEANUP_UNVERIFIED"
        except Exception:
            row["diagnostic_codes"].append("SUPPLY_BOOT_READER_REPORT_UNAVAILABLE")
            result["cleanup"] = "SUPPLY_BOOT_PARENT_CLEANUP_UNVERIFIED"
        return result

    def run(self, check, env, policy_values, adapter, workdir):
        started = utcnow()
        self.validate_policy(check, policy_values)
        if NORMAL_FILE_READS_OPTION in self.cfg:
            return self._run_normal_read(check, env, policy_values, workdir, started)
        self._validate_paired_configuration(env, policy_values)
        begin, end = contract.normalize_policy(check.id, policy_values)
        if not begin <= started <= end:
            raise ValueError("SUPPLY_BOOT_POLICY_WINDOW_EXPIRED")
        sealed_sup16 = check.id == 'SUP-16' and bool(self.expected_prepared_sha256_by_role)
        if check.id in {"SUP-05", "SUP-15"} or sealed_sup16:
            if check.id == "SUP-15":
                role_inputs = self.sup15_prepared_roles(env, policy_values)
            elif sealed_sup16:
                role_inputs = self._prepared_roles(env, policy_values, require_complete=False, distinct_locations=True)
            else:
                role_inputs = {role: self._load_available(env, policy_values, role) for role in contract.ROLES}
                normal, challenge = (role_inputs[role] for role in contract.ROLES)
                if normal is not None and challenge is not None and normal[1]["session"] == challenge[1]["session"]:
                    raise ValueError("SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED")
            collected = {role: self._collect_role(check, env, policy_values, role, role_inputs=role_inputs)
                         for role in contract.ROLES}
        else:
            collected = {role: self._collect_role(check, env, policy_values, role) for role in contract.ROLES}
        return self._draft(check, env, policy_values, workdir, started, collected)

    def normal_file_read_inputs(self, env, values):
        """Validate the local files and reader settings without contacting the workload."""
        inputs = self._read_normal_file_inputs(env, values)
        target, prepared, _, _ = inputs
        self._normal_reader(target, env, prepared)
        return inputs

    def _read_normal_file_inputs(self, env, values):
        from guard.adapters import vllm_file_read_sensor as sensor
        from guard.adapters.vllm_runner_capture import profile as reviewed_release
        from guard.collectors.lifecycle import load_prepared

        _normal_validity(values)
        if env.ref().sha256 != values["supply_environment_sha256"]:
            raise ValueError("SUPPLY_BOOT_NORMAL_BINDING_INVALID")
        section = self.cfg["normal"]
        with Path(section["target"]).open("rb") as stream:
            target = strict_json(stream.read(65537), limit=65536)
        validate_target(target, env)
        prepared = load_prepared(section["prepared"])
        target_sha256 = sha256_hex(canonical_json(target))
        prepared_sha256 = sha256_hex(canonical_json(prepared))
        if prepared["profile"] == sensor.PROFILE:
            release = reviewed_release(env.engine.version)
            profile_matches = (release is not None and prepared["public_commit"] == release.source_commit
                               and env.engine.source_commit in (None, release.source_commit))
        else:
            from guard.adapters.vllm_lifecycle_catalog import approved_file_read_matches
            profile_matches = approved_file_read_matches(env, prepared)
        if (not profile_matches or target_sha256 != values["supply_normal_target_sha256"]
                or prepared_sha256 != values["supply_normal_prepared_sha256"]
                or self.expected_target is not None and self.expected_target != target_sha256):
            raise ValueError("SUPPLY_BOOT_NORMAL_BINDING_INVALID")
        _approved_reference(section["reference_manifest"], values[_REFERENCE_KEY], env, target, prepared)
        return target, prepared, target_sha256, prepared_sha256

    def _normal_reader(self, target, env, prepared):
        return KubernetesLifecycleReader(target, env, prepared, runner=self.runner,
                                         binary=self.cfg.get("kubectl_binary", "kubectl"),
                                         duration=self.cfg.get("max_duration_seconds", 120))

    def _run_normal_read(self, check, env, values, workdir, started):
        from guard.adapters import vllm_file_read_sensor as sensor
        from .supply_boot_artifacts import persist, persist_normal_file_read_sources

        start, end = _normal_validity(values)
        if not start <= started <= end:
            raise ValueError("SUPPLY_BOOT_NORMAL_BINDING_INVALID")
        target, prepared, target_sha256, prepared_sha256 = self._read_normal_file_inputs(env, values)
        if prepared["profile"] == sensor.PROFILE:
            from guard.collectors import native_model_file_reads as model_reads
        else:
            from guard.collectors import approved_model_file_reads as model_reads
        reader = self._normal_reader(target, env, prepared)
        engine = reader.binding["engine"]
        native = None
        cleanup = []
        gap = "SUPPLY_BOOT_NORMAL_SEALED_CAPTURE_UNAVAILABLE"
        try:
            mount = _model_mount(reader.snapshot())
            captured = capture_session(reader, target, container=engine["container"], image=engine["image"], prepared=prepared,
                owner_identity=reader.owner_identity, read_file=reader.read_file, image_check=reader.image_check)
            if (captured["session"] != prepared["session"] or not captured["recorded_files_covered"]
                    or any(captured[key]["metadata"]["sealed"] is not True for key in ("before", "after"))
                    or _model_mount(reader.snapshot()) != mount):
                raise ValueError(gap)
            analysis = model_reads.analyze(captured, prepared)
            candidates = [row for row in analysis["candidates"] if (
                row["declared_reference_comparison_observed"] and row["recording_closed"]
                and row["path_fd_metadata_match_observed"] and row["close_observed"]
                and captured["recordings"][row["recording"]]["capture"]["provenance"]["owner_binding_verified"] is True
            )]
            if candidates:
                analysis["unjoined_read_count"] += len(analysis["candidates"]) - len(candidates)
                analysis["candidates"] = candidates
                declared_paths = {row["normalized_path_sha256"] for row in prepared["model_read_scope"]["files"]}
                unread_paths = declared_paths - {row["normalized_path_sha256"] for row in candidates}
                analysis["groups"] = [{"declared_paths_without_candidate_count": len(unread_paths)}]
                if unread_paths:
                    analysis["missing"] = sorted(set(analysis["missing"]) | {"declared_scope_file_read_candidates_unavailable"})
                native = _read_projection(captured, prepared, target_sha256, prepared_sha256, analysis)
                gap = None
            else:
                gap = "SUPPLY_BOOT_NORMAL_OWNED_READ_UNAVAILABLE"
        except (KeyError, TypeError, ValueError, OSError, RuntimeError, IndexError):
            pass
        try:
            report = reader.report()
            if report.get("parent_cleanup_unverified"):
                cleanup.append("SUPPLY_BOOT_PARENT_CLEANUP_UNVERIFIED")
            elif report["collection_gap"]:
                cleanup.append("SUPPLY_BOOT_NORMAL_CAPTURE_INCOMPLETE")
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            cleanup.append("SUPPLY_BOOT_READER_REPORT_UNAVAILABLE")
        session = {"role": "normal", "session": prepared["session"], "producer_profile": prepared["profile"],
                   "owner_bound_records": native is not None,
                   "target_sha256": target_sha256, "prepared_sha256": prepared_sha256,
                   "approved_reference_manifest_sha256": values[_REFERENCE_KEY], "producer_authenticated": False}
        missing = ["normal_serving_control_unobserved", "change_and_enforcement_unobserved", "producer_authentication_unavailable"]
        missing.extend(native["missing"] if native is not None else [gap])
        collector = f"{self.name}/{self.version}"
        original_artifacts, source_errors = [], []
        if native is not None:
            bindings = [row["source_binding"] for row in native["rows"]]
            recording_numbers = [binding["source_events"][0][0] for binding in bindings]
            original_artifacts, source_errors, names = persist_normal_file_read_sources(
                workdir, captured, recording_numbers, collector)
            for recording, binding in zip(recording_numbers, bindings, strict=True):
                if recording in names:
                    binding["recording_artifact"] = names[recording]
        rows = [{"role": "normal", **native}] if native is not None else []
        summary = {"profile": NORMAL_FILE_READS_PROFILE, "sessions": [session], "native": rows, "gap": gap,
                   "missing": sorted(set(missing + cleanup + source_errors))}
        summary_artifacts, errors = persist(workdir, summary, {"normal": {}, "challenge": {}}, collector)
        errors = source_errors + errors
        observed = {"supply_boot_sessions": [session], "supply_boot_missing": sorted(set(missing + cleanup + errors)),
                    "supply_boot_complete": False, "supply_boot_normal_verified": False,
                    "supply_boot_change_applied": False, "supply_boot_record_authenticated": False}
        observations = {key: {"value": value, "collected": True} for key, value in observed.items()}
        observations["supply_boot_native_consumption"] = {"value": rows or None, "collected": bool(rows)}
        observations["supply_boot_violations"] = observations["supply_boot_change"] = {"value": None, "collected": False}
        origin = DataOrigin.EXAMPLE if self.runner is not None or env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED
        settings = {"profile": NORMAL_FILE_READS_PROFILE, "model_root": _MODEL_ROOT}
        return EvidenceDraft(
            status=ExecStatus.ABORTED if gap or cleanup or errors else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": False, "succeeded": None, "detail": "Existing startup file reads only."},
            challenges=[{"step": step, "attempted": False} for step in check.challenge_steps],
            observations=observations, artifacts=original_artifacts + summary_artifacts, started_at=started, finished_at=utcnow(),
            abort_reason="; ".join(([gap] if gap else []) + cleanup + errors) or None, cleanup_errors=cleanup + errors,
            comparability={"fixture_sha256": sha256_hex(canonical_json(settings)),
                           "config_sha256": sha256_hex(canonical_json({"model_root": _MODEL_ROOT, "strategy": "eager"})),
                           "model_inventory_sha256": env.model.inventory_sha256, "gpu": env.runtime.gpu_model,
                           "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={**settings, NORMAL_FILE_READS_OPTION: True, "target_sha256": target_sha256,
                    "prepared_sha256": prepared_sha256, "reference_manifest_sha256": values[_REFERENCE_KEY]},
        )

    def _project(self, check, collected):
        normal, challenge = collected["normal"], collected["challenge"]
        violations, missing = [], []
        native_rows = []
        for role, part in collected.items():
            shards = part.get("worker_shards")
            if shards is not None:
                missing.extend(f"{role}:{gap}" for gap in shards["missing"])
                if role == "challenge":
                    violations.extend(shards["violations"])
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
        b3 = check.id in {'SUP-15', 'SUP-16'}
        sessions = [{**part['row'], **({'reported_boot_record': part['packet'],
                    'reported_record_is_native_worker_verification': False}
                    if b3 and part['packet'] is not None else {})} for part in collected.values()]
        cleanup = sorted({part["cleanup"] for part in collected.values() if part.get("cleanup")})
        observed = {"supply_boot_sessions": sessions, "supply_boot_change": change, "supply_boot_violations": violations,
                    "supply_boot_missing": missing, "supply_boot_complete": complete and not missing,
                    "supply_boot_normal_verified": normal_ok, "supply_boot_change_applied": bool(change and change["applied"]),
                    "supply_boot_record_authenticated": False, "supply_boot_native_consumption": native_rows}
        record_seen = any(part["packet"] is not None for part in collected.values())
        native_seen = any(part["native"] is not None for part in collected.values())
        observations = {key: {"value": value, "collected": record_seen or key in ("supply_boot_sessions", "supply_boot_missing",
                              "supply_boot_complete", "supply_boot_record_authenticated") or (native_seen and key in (
                              "supply_boot_violations", "supply_boot_native_consumption"))}
                        for key, value in observed.items()}
        observations["supply_boot_change"]["collected"] = change is not None
        for item in observations.values():
            if not item["collected"]:
                item["value"] = None
        workdir = Path(workdir)
        summary = {"profile": check.collection_profile, "sessions": sessions, "violations": violations, "missing": missing,
                   "native": native_rows, "producer_authenticated": False, "engine_started": False}
        from .supply_boot_artifacts import persist
        artifacts, artifact_errors = persist(workdir, summary, collected, f'{self.name}/{self.version}')
        if artifact_errors:
            cleanup = sorted(set(cleanup + artifact_errors))
            observations['supply_boot_complete'] = {'value': False, 'collected': True}
            observations['supply_boot_missing'] = {'value': sorted(set(missing + artifact_errors)), 'collected': True}
        gap = bool(any(row["diagnostic_codes"] for row in sessions) or cleanup)
        codes = sorted({code for row in sessions for code in row["diagnostic_codes"]} | set(artifact_errors))
        normal = {"attempted": any(row["collected"] for row in sessions[:1]), "succeeded": normal_ok if collected["normal"]["row"]["collected"] else None,
                  "detail": "승인 공급물로 준비된 기동 세션"}
        input_examples = any("worker_shard_input" in self.cfg.get(role, {}) for role in contract.ROLES)
        return EvidenceDraft(
            status=ExecStatus.ABORTED if gap else ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE
            if input_examples or self.runner is not None or env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
            normal_control=normal, challenges=[{"step": step, "attempted": bool(change and change["applied"])} for step in check.challenge_steps],
            observations=observations, artifacts=artifacts,
            comparability={"fixture_sha256": sha256_hex(canonical_json({"profile": check.collection_profile})),
                           "model_inventory_sha256": env.model.inventory_sha256, "gpu": env.runtime.gpu_model,
                           "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={"profile": check.collection_profile, "roles": list(contract.ROLES)}, started_at=started, finished_at=utcnow(),
            abort_reason="; ".join(codes) or None, cleanup_errors=cleanup)


__all__ = ["KubernetesSupplyBootExecutor", "SupplyBootReader", "record_path"]
