"""B lifecycle/platform consumers for committed A /1 records, not an executor.

No producer registration, remote collection, inferred causality or new verdict.
The original consumer remains responsible for its normal/challenge observations.
Candidates and lineage are facts with explicit gaps, never automatic violations.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import stat

from guard.collectors import gpu_common_inputs as contract
from guard.collectors import ipc_ownership_producer, group_resource_producer
from guard.models import DataOrigin, ExecStatus, ORIGIN_STRENGTH, Observation, canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from .base import DraftArtifact


KINDS = frozenset({"request-attempt", "generation-transition", "health-worker-causal",
                   "profiler-control", "process-device-provider", "ipc-ownership", "failed-group-resources"})
CHECK_IDS = frozenset({"LIF-03", "LIF-05", "LIF-06", "LIF-07", "LIF-09", "LIF-10", "LIF-11",
                       "LIF-12", "LIF-14", "REQ-12", "OBS-02", "OBS-03", "PLT-01", "PLT-02", "PLT-04"})
MAX_BYTES = 8 * 1024**2


def _read(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("B_COMMON_INPUT_SYMLINK")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_BYTES:
        raise ValueError("B_COMMON_INPUT_FILE_BOUND")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("B_COMMON_INPUT_FILE_CHANGED")
    return strict_json(raw, limit=MAX_BYTES), sha256_hex(raw)


def consume_record(check_id, record, *, environment_ref, target_sha256, prepared_sha256, session, owner=None):
    """Exact A envelope -> B domain facts. Never use the record as its own expected scope."""
    kind = record.get("kind") if type(record) is dict else None
    if check_id not in CHECK_IDS or kind not in KINDS:
        raise ValueError("B_COMMON_INPUT_KIND_UNSUPPORTED")
    if check_id not in contract.KINDS[kind]["checks"]:
        raise ValueError("B_COMMON_INPUT_CHECK_UNDECLARED:" + check_id)
    validation = contract.validate(record, kind=kind, environment_ref=environment_ref,
        target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)
    gaps = list(validation["gaps"])
    gaps.append("B_COMMON_INPUT_WORKER_IDENTITY_NOT_INDEPENDENTLY_VERIFIED")
    if owner is None:
        gaps.append("B_COMMON_INPUT_CURRENT_OWNER_UNVERIFIED")
    else:
        expected = {"pod_uid": owner["pod_uid"], "container_id": owner["container_id"].removeprefix("containerd://")[:128],
                    "restart_count": owner["restart_count"]}
        matches = [all(binding[key] == value for key, value in expected.items()) for binding in record["bindings"]]
        if not all(matches):
            if kind == "failed-group-resources" and any(matches):
                gaps.append("B_COMMON_INPUT_RECOVERY_NEXT_OWNER_UNVERIFIED")
            else:
                raise ValueError("B_COMMON_INPUT_CURRENT_OWNER_MISMATCH")
    events = record["events"]
    facts = {"event_counts": {name: sum(e["event"] == name for e in events)
                             for name in sorted({e["event"] for e in events})}}
    if kind == "ipc-ownership":
        facts.update(ipc_ownership_producer.summarize(record))
        gaps.append("B_IPC_OWNERSHIP_NOT_ACTOR_AUTHORIZATION_OR_MESSAGE_CONSUMPTION")
    elif kind == "failed-group-resources":
        facts.update(group_resource_producer.lineage(record))
        gaps.append("B_GROUP_LINEAGE_NOT_GLOBAL_GPU_RELEASE_OR_RECOVERY_POLICY_VERIFICATION")
    elif kind == "health-worker-causal":
        health = {(e["generation"], e["causal_sha256"]) for e in events if e["event"] == "api_health"}
        worker = {(e["generation"], e["causal_sha256"]) for e in events if e["event"] == "worker_state"}
        facts["shared_health_worker_causal_keys"] = len(health & worker)
        if not health & worker:
            gaps.append("B_HEALTH_WORKER_CAUSAL_LINK_UNAVAILABLE")
    elif kind == "profiler-control":
        facts["cross_process_causality_verified"] = False
        gaps.append("B_PROFILER_CONTROL_NOT_BACKEND_TRACE_OR_RPC_CAUSALITY")
    elif kind == "generation-transition":
        facts["actual_weights_cache_consumption_verified"] = False
        gaps.append("B_GENERATION_LABELS_NOT_ACTUAL_WEIGHTS_CACHE_CONSUMPTION")
    elif kind == "request-attempt":
        facts["automatic_retry_and_final_output_binding_verified"] = False
        gaps.append("B_ATTEMPT_EVENTS_NOT_FULL_BUDGET_DISCONNECT_OR_RETRY_SCOPE")
    elif kind == "process-device-provider":
        facts["namespace_host_pid_pairs"] = sorted({(e["namespace_pid"], e["nvml_host_pid"])
            for e in events if e["event"] == "gpu_process" and e["namespace_pid"] is not None and e["nvml_host_pid"] is not None})
        gaps.append("B_PROCESS_DEVICE_NOT_COMPLETE_WORKER_ROSTER_OR_ENTERPRISE_CREDENTIAL_SCOPE")
    return {"kind": kind, "record_sha256": sha256_hex(canonical_json(record)),
            "validation": validation, "facts": facts, "gaps": sorted(set(gaps)),
            "producer_authenticated": False, "whole_question_complete": False,
            "violation_candidates": validation["violation_candidates"]}


def _observe(draft, key, value):
    draft.observations[key] = Observation(value=value, collected=True)


def _list(draft, key):
    row = draft.observations.get(key)
    return list(row.value) if row is not None and row.collected and type(row.value) is list else []


def attach_inputs(draft, check, env, target, descriptor, workdir, *, prepared=None, owner=None, generation=None):
    """Add domain facts to existing judge inputs and an exact, bounded evidence artifact."""
    if descriptor is None:
        return draft
    rows, gaps = [], []
    try:
        if (check.id not in CHECK_IDS or type(descriptor) is not dict
                or set(descriptor) - {"prepared", "session", "records", "generation", "data_origin"}
                or not {"prepared", "session", "records"} <= set(descriptor)
                or type(descriptor["records"]) is not dict or not 1 <= len(descriptor["records"]) <= len(KINDS)
                or type(descriptor["session"]) is not str
                or descriptor.get("data_origin", "EXAMPLE") not in {"EXAMPLE", "DECLARED"}
                or descriptor.get("generation") != generation):
            raise ValueError("B_COMMON_INPUT_CONFIGURATION_INVALID")
        declared, _ = _read(descriptor["prepared"])
        if type(declared) is not dict:
            raise ValueError("B_COMMON_INPUT_PREPARED_INVALID")
        if prepared is not None and canonical_json(declared) != canonical_json(prepared):
            raise ValueError("B_COMMON_INPUT_PREPARED_MISMATCH")
        if prepared is None:
            gaps.append("B_COMMON_INPUT_PREPARED_RUNTIME_UNVERIFIED")
        if "session" in declared and declared["session"] != descriptor["session"]:
            raise ValueError("B_COMMON_INPUT_PREPARED_SESSION_MISMATCH")
        scope = {"environment_ref": env.ref().model_dump(mode="json"),
                 "target_sha256": sha256_hex(canonical_json(target)),
                 "prepared_sha256": sha256_hex(canonical_json(declared)), "session": descriptor["session"], "owner": owner}
        for kind, path in descriptor["records"].items():
            try:
                if kind not in KINDS:
                    raise ValueError("B_COMMON_INPUT_KIND_UNSUPPORTED")
                record, file_sha = _read(path)
                if type(record) is not dict or record.get("kind") != kind:
                    raise ValueError("B_COMMON_INPUT_KIND_MISMATCH")
                result = consume_record(check.id, record, **scope)
                result["file_sha256"] = file_sha
                # The validated contract contains hashes/metadata, never payload bodies.
                result["record"] = record
                rows.append(result)
                gaps.extend(result["gaps"])
            except (KeyError, ValueError, TypeError, OSError) as error:
                code = str(error) if isinstance(error, ValueError) else type(error).__name__
                gaps.append("B_COMMON_INPUT_REJECTED:" + str(kind) + ":" + code)
    except (KeyError, ValueError, TypeError, OSError) as error:
        code = str(error) if isinstance(error, ValueError) else type(error).__name__
        gaps.append("B_COMMON_INPUT_UNAVAILABLE:" + code)
    report = {"schema": "guard-b-lifecycle-common-inputs/1", "check_id": check.id, "records": rows,
              "gaps": sorted(set(gaps)), "whole_question_complete": False, "producer_authenticated": False,
              "native_observations": {key: value.model_dump(mode="json") for key, value in draft.observations.items()
                                      if not key.startswith("activation_")}}
    if any(spec.key == "activation_steps" for spec in check.required_observations):
        _observe(draft, "activation_steps", _list(draft, "activation_steps") + [{"step": "committed-A-common-inputs", **report}])
        _observe(draft, "activation_missing", _list(draft, "activation_missing") + report["gaps"])
        # Candidates are not confirmed violations. Preserve every existing independent FAIL.
        _observe(draft, "activation_violations", _list(draft, "activation_violations"))
        _observe(draft, "activation_scope_complete", False)
    elif check.id == "OBS-03":
        _observe(draft, "log_coverage_gaps", _list(draft, "log_coverage_gaps") + report["gaps"])
        _observe(draft, "log_coverage_complete", False)
    elif check.id in {"PLT-01", "PLT-04"}:
        _observe(draft, "process_scan_complete", False)
    elif check.id == "PLT-02":
        _observe(draft, "ipc_scope_complete", False)
    try:
        path = Path(workdir) / "b-common-inputs.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(canonical_json(report))
        draft.artifacts.append(DraftArtifact(name=path.name, path=path, collector="b-common-input-consumption/1"))
    except OSError:
        draft.cleanup_errors.append("B_COMMON_INPUT_ARTIFACT_UNAVAILABLE")
    draft.config = {**draft.config, "b_common_inputs": {"record_sha256": [r["record_sha256"] for r in rows],
                    "gaps": report["gaps"], "whole_question_complete": False}}
    origin = descriptor.get("data_origin", "EXAMPLE") if type(descriptor) is dict else "EXAMPLE"
    ceiling = DataOrigin.DECLARED if origin == "DECLARED" else DataOrigin.EXAMPLE
    draft.data_origin = min((draft.data_origin, env.data_origin, ceiling), key=lambda v: ORIGIN_STRENGTH[v])
    draft.status = ExecStatus.ABORTED
    draft.abort_reason = draft.abort_reason or "B_COMMON_INPUT_QUESTION_SCOPE_INCOMPLETE"
    return draft


class LifecycleCommonInputConsumer:
    """Optional B record input on the existing classes; no plugin/factory/run replacement."""
    def __init__(self, cfg, *args, **kwargs):
        self.b_common_inputs = deepcopy(cfg.get("common_inputs")) if type(cfg) is dict else None
        clean = {k: v for k, v in cfg.items() if k != "common_inputs"} if type(cfg) is dict else cfg
        super().__init__(clean, *args, **kwargs)

    def _finish(self, ctx):
        draft = super()._finish(ctx)
        if self.b_common_inputs is None:
            return draft
        prepared = next((getattr(ctx, name) for name in ("_termination_prepared", "_init_prepared", "_recovery_prepared", "_buffer_prepared")
                         if hasattr(ctx, name)), None)
        binding = getattr(ctx.cprobe, "binding", {})
        return attach_inputs(draft, ctx.check, ctx.env, ctx.target, self.b_common_inputs, ctx.workdir,
                             prepared=prepared, owner=binding.get("engine"))

    def common_input_config(self):
        """Carry optional inputs through the existing closed dispatcher, never into native cfg."""
        return {**self.cfg, **({"common_inputs": deepcopy(self.b_common_inputs)} if self.b_common_inputs is not None else {})}


def merge_activation_inputs(facts, passive):
    """The existing activation merge must not erase consumed records or an independent FAIL."""
    if passive is None or "b_common_inputs" not in passive.config:
        return facts
    facts = deepcopy(facts)
    facts["activation_steps"] += _list(passive, "activation_steps")
    facts["activation_missing"] += _list(passive, "activation_missing")
    facts["activation_violations"] += _list(passive, "activation_violations")
    facts["activation_scope_complete"] = False
    return facts
