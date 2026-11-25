"""Read an owned normal startup and verify the selected workers' closed journals."""

from pathlib import Path
import re

from guard.collectors import normal_tp2_callbacks, worker_shard_producer
from guard.configuration import NORMAL_WORKER_SHARDS_OPTION
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.engine_http import ENGINE_CALLBACK_PROFILE, KubernetesBindingError, KubernetesHTTP
from guard.update_validation import _plain_path
from .base import DraftArtifact, EvidenceDraft
from .cache_timing_group import read_regular
from .rank_failure_contract import stamp
from .supply_boot_artifacts import _publish_complete_bytes
from .worker_shard_archive import (
    CAPTURE_SCHEMA, captured_process_epochs, expected_worker_ranks, mounted_source_expectation,
    verify_saved_pages,
)

PROFILE = "kubernetes-owned-normal-worker-shards/1"
SUMMARY = "sup06-normal-worker-shards.json"
BINDING_KEYS = {
    "supply_environment_sha256", "supply_normal_target_sha256", "supply_normal_worker_target_file_sha256",
    "supply_normal_worker_prepared_file_sha256", "supply_validity_start", "supply_validity_end",
}
TARGET_FILE = "supply_normal_worker_target_file_sha256"
PREPARED_FILE = "supply_normal_worker_prepared_file_sha256"
_INVALID_INPUT = "SUP06_NORMAL_WORKER_BINDING_INVALID"
_INVALID_RANK = "SUP06_NORMAL_WORKER_RANK_UNVERIFIED"
_SHA = re.compile(r"[0-9a-f]{64}")


def read_inputs(executor_cfg, reads=None):
    """Return stable raw inputs with optional hashes sealed by the run binding."""
    pins = reads or {}
    target_path = _plain_path(Path(executor_cfg["target"]))
    manifest_path = _plain_path(Path(executor_cfg["prepared"]))
    if manifest_path.name != "manifest.json":
        raise ValueError(_INVALID_INPUT)
    target_raw = read_regular(target_path, limit=65536, expected_sha256=pins.get(TARGET_FILE))
    plan, files, prepared, _ = normal_tp2_callbacks.read_preparation(manifest_path.parent, "worker-shards")
    if pins.get(PREPARED_FILE, plan["manifest_sha256"]) != plan["manifest_sha256"]:
        raise ValueError(_INVALID_INPUT)
    target = strict_json(target_raw, limit=65536)
    actual = target[normal_tp2_callbacks.KEY]
    normal_tp2_callbacks.validate_plan(actual, attached=True)
    if any(actual.get(key) != value for key, value in plan.items()):
        raise ValueError(_INVALID_INPUT)
    return target, files, prepared, {TARGET_FILE: sha256_hex(target_raw), PREPARED_FILE: plan["manifest_sha256"]}


def validity(values):
    if (type(values) is not dict or set(values) != BINDING_KEYS
            or any(type(values[key]) is not str or not _SHA.fullmatch(values[key])
                   for key in BINDING_KEYS if key.endswith("_sha256"))):
        raise ValueError(_INVALID_INPUT)
    begin, end = stamp(values["supply_validity_start"]), stamp(values["supply_validity_end"])
    if not 0 < (end - begin).total_seconds() <= 3600:
        raise ValueError(_INVALID_INPUT)
    return begin, end


def prepared_inputs(cfg, env, values, *, runner=None, expected_target=None):
    """Validate bound inputs without dispatching to the engine."""
    validity(values)
    try:
        target, files, prepared, pins = read_inputs(cfg, values)
        canonical_target = sha256_hex(canonical_json(target))
        plan = target[normal_tp2_callbacks.KEY]
        fields = (env.runtime.platform or "").split("; ")
        ranks = expected_worker_ranks(env)
        if (not 1 < len(ranks) <= 8 or env.runtime.gpu_count != len(ranks)
                or not env.model.inventory_sha256 or not _SHA.fullmatch(env.model.inventory_sha256)
                or target.get("lab") is not False or target.get("synthetic", False) is not False
                or fields.count(normal_tp2_callbacks.environment_field(plan)) != 1
                or values["supply_environment_sha256"] != env.ref().sha256
                or values["supply_normal_target_sha256"] != canonical_target
                or expected_target is not None and expected_target != canonical_target):
            raise ValueError
        transport = KubernetesHTTP(target, env, duration=cfg.get("max_duration_seconds", 120),
                                   binary=cfg.get("kubectl_binary", "kubectl"), runner=runner,
                                   profile=ENGINE_CALLBACK_PROFILE)
        if transport.binding["engine"]["restart_count"] != 0:
            raise ValueError
    except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
        raise ValueError(_INVALID_INPUT) from None
    options = {
        "normal_callback_assets": {
            "target_file_sha256": pins[TARGET_FILE], "plan_sha256": normal_tp2_callbacks.plan_sha256(plan),
            "files": {name: {"bytes": len(raw), "sha256": sha256_hex(raw)} for name, raw in files.items()},
        },
        "normal_pod_check": lambda pod: normal_tp2_callbacks.verify_mounted_pod(target, env, pod),
    }
    mount = mounted_source_expectation(target, prepared, transport.binding,
                                       {"target": pins[TARGET_FILE], "prepared": pins[PREPARED_FILE]})
    scope = {"environment_ref": env.ref().model_dump(mode="json"), "target_sha256": transport.target_sha256,
             "prepared_sha256": sha256_hex(canonical_json(prepared)), "session": prepared["session"]}
    return transport, scope, options, mount, ranks, pins


def _capture(transport, scope, options, archive, epochs=None):
    value = transport.engine_attempt_events(scope["session"], archive=archive,
                                            normal_worker_terminal_epochs=epochs, **options)
    pod = transport.binding["engine"]
    record, validation = worker_shard_producer.record_from_processes(value["processes"], pod=pod, **scope)
    return {
        "schema": CAPTURE_SCHEMA, "session": scope["session"],
        "processes": [{"pid": process["pid"], "start_ticks": process["start_ticks"], "rows": len(process["events"])}
                      for process in value["processes"]],
        "records": {"worker-shard-load": {"record": record, "validation": validation}},
        "raw_page_refs": value["page_refs"], "truncated": value["truncated"],
        "mounted_source_attestation": value["mounted_source_attestation"],
    }


def _replay(capture, archive, env, pod, scope, mount, ranks, *, finalized=False):
    def document(path, digest, limit):
        return path, strict_json(read_regular(path, limit=limit, expected_sha256=digest), limit=limit)

    record = capture["records"]["worker-shard-load"]["record"]
    return verify_saved_pages(capture, archive, document, record=record, epochs=captured_process_epochs(capture),
                              pod=pod, scope=scope, version=env.engine.version, expected_ranks=ranks,
                              mounted_source=mount, require_finalized=finalized)


def _rank_epochs(native, ranks):
    rows = native["ranks"]
    if (tuple(row["rank"] for row in rows) != tuple(ranks)
            or any(not {"prepare_return", "load_return", "ready_candidate_call"} <= row.keys() for row in rows)):
        raise ValueError(_INVALID_RANK)
    for row in rows:
        assignment = row["prepare_return"].get("indexed_shard_assignment", {})
        if (assignment.get("required_shards_assigned") is not True
                or assignment.get("required_shard_count", 0) <= 0
                or assignment.get("assigned_shard_count") != assignment["required_shard_count"]):
            raise ValueError(_INVALID_RANK)
    epochs = {row["worker_pid"]: row["start_ticks"] for row in rows}
    if len(epochs) != len(ranks) or any(type(ticks) is not int or ticks <= 0 for ticks in epochs.values()):
        raise ValueError(_INVALID_RANK)
    return epochs


def run_normal_worker_shards(check, env, values, cfg, workdir, started, *, collector, runner=None, expected_target=None):
    begin, end = validity(values)
    if not begin <= started <= end:
        raise ValueError("SUP06_NORMAL_WORKER_RUN_WINDOW_EXPIRED")
    transport, scope, options, mount, ranks, pins = prepared_inputs(cfg, env, values, runner=runner,
                                                                   expected_target=expected_target)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    pod = transport.binding["engine"]
    prefix_dir, final_dir = workdir / "sup06-prefix", workdir / "sup06-final"
    prefix = capture = native = None
    terminal = False
    diagnostics = []
    try:
        prefix = _capture(transport, scope, options, prefix_dir)
        before = _replay(prefix, prefix_dir, env, pod, scope, mount, ranks)
        epochs = _rank_epochs(before, ranks)
        capture = _capture(transport, scope, options, final_dir, epochs)
        native = _replay(capture, final_dir, env, pod, scope, mount, ranks, finalized=True)
        if (_rank_epochs(native, ranks) != epochs
                or native["mounted_source_attestation"]["observation"] != before["mounted_source_attestation"]["observation"]
                or capture["records"]["worker-shard-load"]["record"] != prefix["records"]["worker-shard-load"]["record"]
                or capture["records"]["worker-shard-load"]["record"]["completeness"]["diagnostic_codes"]):
            raise ValueError(_INVALID_RANK)
        terminal = True
    except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError) as exc:
        code = str(exc)
        diagnostics.append(code if re.fullmatch(r"[A-Z0-9_]{1,100}", code) else "SUP06_NORMAL_WORKER_CAPTURE_UNAVAILABLE")
    pages, artifacts = {}, []
    for label, wrapper, directory in (("prefix", prefix, prefix_dir), ("final", capture, final_dir)):
        references = []
        for item in wrapper["raw_page_refs"] if wrapper is not None else ():
            name = "sup06-" + label + "-" + item["path"]
            path = directory / item["path"]
            references.append({"name": name, **item})
            artifacts.append(DraftArtifact(name=name, path=path, collector=collector))
        pages[label] = references
    summary = {"schema": "guard-sup06-normal-worker-shards/1", "mode": PROFILE,
               "startup_source_finalized": terminal, "capture": capture, "native_ranks": native,
               "prefix": prefix, "page_artifacts": pages["final"], "prefix_page_artifacts": pages["prefix"],
               "expected_ranks": list(ranks), "target_sha256": scope["target_sha256"],
               "prepared_sha256": scope["prepared_sha256"], "environment_ref": scope["environment_ref"],
               "target_file_sha256": pins[TARGET_FILE], "prepared_file_sha256": pins[PREPARED_FILE],
               "diagnostic_codes": diagnostics}
    path = workdir / SUMMARY
    _publish_complete_bytes(path, canonical_json(summary), staging_prefix=".sup06-normal-")
    artifacts.append(DraftArtifact(name=SUMMARY, path=path, collector=collector))
    observed = {
        "supply_boot_sessions": [{"role": "normal", "collected": terminal, "diagnostic_codes": diagnostics}],
        "supply_boot_missing": sorted(set(diagnostics + ["SUPPLY_BOOT_CHANGE_NOT_EXERCISED"])),
        "supply_boot_complete": False, "supply_boot_normal_verified": terminal, "supply_boot_change_applied": False,
        "supply_boot_record_authenticated": False,
        "supply_boot_native_consumption": [{"role": "normal", "startup_source_finalized": True,
                                            "worker_ranks": list(ranks)}] if terminal else None,
        "supply_boot_change": None, "supply_boot_violations": None,
    }
    observations = {key: {"value": value, "collected": value is not None} for key, value in observed.items()}
    origin = DataOrigin.EXAMPLE if runner is not None or env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED
    return EvidenceDraft(
        status=ExecStatus.COMPLETED if terminal else ExecStatus.ABORTED, data_origin=origin, observations=observations,
        normal_control={"attempted": True, "succeeded": True if terminal else None, "detail": PROFILE},
        challenges=[{"step": step, "attempted": False} for step in check.challenge_steps], artifacts=artifacts,
        started_at=started, finished_at=utcnow(), abort_reason="; ".join(diagnostics) or None,
        comparability={"fixture_sha256": sha256_hex(canonical_json({"profile": PROFILE, "ranks": list(ranks)})),
                       "model_inventory_sha256": env.model.inventory_sha256, "gpu": env.runtime.gpu_model,
                       "driver": env.runtime.driver, "cpu": env.runtime.cpu},
        config={"profile": PROFILE, NORMAL_WORKER_SHARDS_OPTION: True, "startup_source_finalized": terminal,
                "target_sha256": scope["target_sha256"], "prepared_sha256": scope["prepared_sha256"], **pins},
    )
