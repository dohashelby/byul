"""Allowlisted dashboard snapshot; private evidence stays in the host state file."""
from __future__ import annotations


def fields(names, **children):
    return {**dict.fromkeys(names.split()), **children}


MODE = fields("id mode state")
STEP = fields("at context namespace policy requested_mode target_mode ok rollback_ok",
              before=MODE, after=MODE, rollback=MODE)
EVENT = fields("time policy path access_mask pid ns_pid function action return_value identity_source",
               kernel_task=fields("pid start_ns pidns"), pod_uid="alias")
AGENT = fields("restart_count last_exit_at last_exit_code", node="alias", pod_uid="alias")
GAP = fields("opened_at last_verified_at closed_at")
MONITORING = fields("state generated_at policies_verified writer_verified identities_verified events_verified "
                    "verified_at verification_since controls_verified_since",
                    open_gap=GAP, history=[GAP])
READ = fields("read errno pid")
TRIAL = fields("id context kernel namespace policy probe_path method variant data_origin started_at finished_at "
               "main_pid policy_id policy_spec_sha256 manifest_sha256 raw_event_sha256 source_report_sha256",
    node="alias", pod_uid="alias", facts="facts", events=[EVENT], monitor_events=[EVENT], steps=[STEP],
    task_identity=fields("namespace node_pid ns_pid pidns start_ticks clock_ticks", pod_uid="alias", container_id="alias"),
    identity_probe_data=[fields("index type source label resolve")],
    policy_actions=[fields("action argError")], read_result=READ, monitor_read_result=READ,
    restore_read_result=READ, agent_before=AGENT, agent_after=AGENT,
    termination=fields("exitCode signal finishedAt"), load=fields("state id"))
FACTS = set(("enforce_active event_bound collector_complete sigkill_event override_event override_configured "
    "kernel_read_denied read_denied read_succeeded process_terminated monitor_read_ok restore_read_ok "
    "same_policy_id cleanup_ok main_process service_unavailable container_restarted service_recovered "
    "unsupported agent_changed main_process_identified child_process_identified distinct_pods_identified "
    "initial_monitoring_verified original_policies_preserved agent_change_detected gap_detected recovery_verified "
    "threaded_child_identified").split())
REASONS = set(("recovery_observer_stale expected_policies_unavailable agent_epoch_changed agent_epoch_first_observed "
    "agent_or_policy_query_unavailable expected_policy_state_mismatch event_writer_unavailable "
    "process_identity_source_unavailable normalized_observations_unavailable waiting_for_attributed_event "
    "recovery_pending recovery_evidence_unavailable recovery_check_not_configured").split())


def public_snapshot(snapshot):
    """Keep evidence needed for reassessment, replacing opaque infrastructure identities.

    No arbitrary dictionaries, raw commands, exception messages, host names,
    agent UIDs or container IDs cross this boundary. Aliases are local to a sample.
    """
    aliases = {}

    def project(value, shape):
        if value is None:
            return None
        if shape == "alias":
            if not isinstance(value, str) or not value:
                return None
            return aliases.setdefault(value, f"identity-{len(aliases) + 1}")
        if shape == "facts":
            if not isinstance(value, dict):
                return {}
            return {key: val for key, val in value.items() if key in FACTS and (val is None or type(val) is bool)}
        if isinstance(shape, list):
            if not isinstance(value, list):
                return []
            return [project(item, shape[0]) for item in value if isinstance(item, dict)]
        if isinstance(shape, dict):
            if not isinstance(value, dict):
                return None
            result = {key: project(value[key], child) for key, child in shape.items() if key in value}
            # Errors and transition reasons may contain raw command output.
            for key in ("error", "load_error", "transport_error"):
                if value.get(key):
                    result[key] = "host_details_withheld"
            if "reason" in value:
                reason = value["reason"]
                result["reason"] = reason if isinstance(reason, str) and reason in REASONS | {"OOMKilled"} else "host_details_withheld"
            if "reasons" in value:
                reasons = value["reasons"] if isinstance(value["reasons"], list) else []
                result["reasons"] = [r if isinstance(r, str) and r in REASONS else "host_details_withheld" for r in reasons]
            return result
        return value if isinstance(value, (str, int, float, bool)) else None

    shape = fields("schema_version generated_at context state",
        policies=[fields("name namespace id state mode method contains_sigkill spec_sha256", node="alias")],
        capabilities=[fields("checked_at kernel apparmor recommended tetragon_override_return tetragon_lsm tetragon_fmodret", node="alias")],
        agents=[AGENT], transitions=[STEP], verifications=[TRIAL], monitoring=MONITORING,
        operation_checks=[fields("id context namespace data_origin started_at finished_at restart_requested status",
                                facts="facts", monitoring=MONITORING)])
    result = project(snapshot, shape)
    # Capability lists are fixed method names, never arbitrary diagnostics.
    methods = {"monitor", "lsm_file_open", "override_syscall", "override_lsm", "override_only", "sigkill_fd_install"}
    for raw, safe in zip(snapshot.get("capabilities", []), result.get("capabilities", [])):
        for key in ("available", "unavailable", "unknown"):
            safe[key] = [m for m in raw.get(key, []) if m in methods]
    for raw, safe in zip(snapshot.get("agents", []), result.get("agents", [])):
        # The collector produces this fixed summary, never the termination message.
        if raw.get("last_exit_summary") == "정책 조회 경로 내부 오류 (TotalMemlock/ListTracingPolicies)":
            safe["last_exit_summary"] = raw["last_exit_summary"]
    return result
