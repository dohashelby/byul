"""One requested-configuration merge for preparation, selection and execution."""
from copy import deepcopy


def merge_config(base, override):
    out = deepcopy(base)
    for key, value in override.items():
        out[key] = merge_config(out.get(key, {}), value) if isinstance(value, dict) else deepcopy(value)
    return out


CHECK_CONFIG = "check_config"
UPDATE_CHECK_OUTSIDE_SCOPE = "UPDATE_CHECK_OUTSIDE_EXECUTION_SCOPE"
GATEWAY_RECORD_SNAPSHOT_OPTION = "gateway_record_snapshot_only"
CILIUM_EGRESS_STATUS_OPTION = "cilium_egress_status_only"
CILIUM_EGRESS_STATUS_SCOPE_ERROR = "CILIUM_EGRESS_STATUS_EXECUTION_SCOPE_INVALID"
INSTALLED_PLUGIN_INVENTORY_OPTION = "installed_plugin_inventory_only"
INSTALLED_PLUGIN_INVENTORY_SCOPE_ERROR = "INSTALLED_PLUGIN_INVENTORY_EXECUTION_SCOPE_INVALID"
NORMAL_STREAM_OPTION = "normal_stream_only"
NORMAL_SAMPLING_OPTION = "normal_sampling_only"
NORMAL_TOKEN_INPUT_OPTION = "normal_token_input_only"
NORMAL_REQUEST_LATENCY_OPTION = "normal_request_latency_only"
NORMAL_REQUEST_LATENCY_SCOPE_ERROR = "NORMAL_REQUEST_LATENCY_EXECUTION_SCOPE_INVALID"
NORMAL_SERVING_READINESS_OPTION = "normal_serving_readiness_only"
NORMAL_SERVING_READINESS_SCOPE_ERROR = "NORMAL_SERVING_READINESS_EXECUTION_SCOPE_INVALID"
NORMAL_STARTUP_LOAD_OPTION = "normal_startup_load_only"
NORMAL_STARTUP_LOAD_SCOPE_ERROR = "NORMAL_STARTUP_LOAD_EXECUTION_SCOPE_INVALID"
_NORMAL_ONLY_CASES = {
    "LIF-01": ("supply_boot", NORMAL_STARTUP_LOAD_OPTION, NORMAL_STARTUP_LOAD_SCOPE_ERROR),
    "LIF-03": ("stream_buffer", NORMAL_REQUEST_LATENCY_OPTION, NORMAL_REQUEST_LATENCY_SCOPE_ERROR),
    "LIF-04": ("stream_buffer", NORMAL_STREAM_OPTION, "NORMAL_STREAM_EXECUTION_SCOPE_INVALID"),
    "OBS-02": ("observation_state", NORMAL_SERVING_READINESS_OPTION, NORMAL_SERVING_READINESS_SCOPE_ERROR),
    "PLT-07": ("boundary_egress", CILIUM_EGRESS_STATUS_OPTION, CILIUM_EGRESS_STATUS_SCOPE_ERROR),
    "REQ-09": ("input_validation", NORMAL_TOKEN_INPUT_OPTION, "NORMAL_TOKEN_INPUT_EXECUTION_SCOPE_INVALID"),
    "REQ-10": ("sampling_validation", NORMAL_SAMPLING_OPTION, "NORMAL_SAMPLING_EXECUTION_SCOPE_INVALID"),
    "SUP-10": ("supply_boot", INSTALLED_PLUGIN_INVENTORY_OPTION, INSTALLED_PLUGIN_INVENTORY_SCOPE_ERROR),
}
RECORD_SNAPSHOT_OPTIONS = {
    "OBS-01": "record_snapshot_only",
    "PLT-12": "copy_record_snapshot_only",
}
KUBERNETES_BOUND_CHECK_OPTIONS = {
    **RECORD_SNAPSHOT_OPTIONS,
    **{check_id: option for check_id, (_, option, _) in _NORMAL_ONLY_CASES.items()},
    "OBS-04": "local_observation_only",
    "PLT-02": "protected_path_metadata_only",
    "PLT-06": "worker_trust_metadata_only",
    "PLT-08": "passive_from_plt01",
}


def update_allowed_checks(raw, pack):
    """Read the common execution allowlist without changing PLAN selection."""
    sides = raw.get("sides", {})
    if type(sides) is not dict or any(type(side) is not dict for side in sides.values()):
        raise ValueError("UPDATE_SIDES_CONFIGURATION_INVALID")
    for side in sides.values():
        side_options = side.get("update", {})
        if type(side_options) is not dict:
            raise ValueError("UPDATE_CONFIGURATION_INVALID")
        if "allowed_checks" in side_options:
            raise ValueError("UPDATE_ALLOWED_CHECKS_MUST_BE_COMMON")
    options = raw.get("update", {})
    if type(options) is not dict:
        raise ValueError("UPDATE_CONFIGURATION_INVALID")
    if "allowed_checks" not in options:
        return None
    check_ids = options["allowed_checks"]
    if (type(check_ids) is not list
            or any(type(cid) is not str or cid not in pack.checks or pack.checks[cid].retired for cid in check_ids)
            or len(set(check_ids)) != len(check_ids)):
        raise ValueError("UPDATE_ALLOWED_CHECKS_INVALID")
    return frozenset(check_ids)


def for_check(raw, executor, check_id):
    """Run config as one check sees it: `[check_config.<CHECK>]` merged over its executor section.

    Several checks share one executor (LIF-05/06/16, OBS-01/PLT-12, LIF-07/09);
    a per-check entry lets one `guard update` give each its own target,
    prepared or generations (RB2-S, 2026-11-14 user decision).  The merge is the
    same as for sides (tables merge, other values replace) and touches only
    that check's executor section.  The table stays in the requested config, so
    it is part of the PLAN execution recipe hash.
    """
    table = (raw or {}).get(CHECK_CONFIG)
    if table is None:
        return raw
    if type(table) is not dict or any(type(k) is not str or type(v) is not dict for k, v in table.items()):
        raise ValueError("CHECK_CONFIG_INVALID")
    entry = table.get(check_id)
    if not entry:
        return raw
    section = raw.get(executor, {})
    if type(section) is not dict:
        raise ValueError("CHECK_CONFIG_INVALID")
    out = dict(raw)
    out[executor] = merge_config(section, entry)
    return out


def normal_token_input_requested(raw):
    """Detect the option before validating its value, check, or paired scope."""
    overlays = raw.get(CHECK_CONFIG)
    settings = (raw.get("input_validation"), *(overlays.values() if type(overlays) is dict else ()))
    return any(type(section) is dict and NORMAL_TOKEN_INPUT_OPTION in section for section in settings)


def _option_occurrences(value, option, path=()):
    if type(value) is dict:
        for key, child in value.items():
            location = (*path, key)
            if key == option:
                yield location, child
            else:
                yield from _option_occurrences(child, option, location)
    elif type(value) is list:
        for index, child in enumerate(value):
            yield from _option_occurrences(child, option, (*path, index))


def _validate_normal_option_placement(raw, option, paths, error):
    for path, value in _option_occurrences(raw, option):
        if path[:2] in {("sides", "baseline"), ("sides", "candidate")}:
            path = path[2:]
        if value is not True or path not in paths:
            raise ValueError(error)


def normal_request_latency_requested(raw):
    return next(_option_occurrences(raw, NORMAL_REQUEST_LATENCY_OPTION), None) is not None


def normal_startup_load_requested(raw):
    return next(_option_occurrences(raw, NORMAL_STARTUP_LOAD_OPTION), None) is not None


def installed_plugin_inventory_requested(raw):
    return next(_option_occurrences(raw, INSTALLED_PLUGIN_INVENTORY_OPTION), None) is not None


def cilium_egress_status_requested(raw):
    return next(_option_occurrences(raw, CILIUM_EGRESS_STATUS_OPTION), None) is not None


def normal_serving_readiness_requested(raw):
    """Detect a serving-readiness opt-in before validating its placement or value."""
    return next(_option_occurrences(raw, NORMAL_SERVING_READINESS_OPTION), None) is not None


def bounded_metadata_scopes(configurations, pack, allowed_checks):
    """Bind the explicitly configured metadata cases without enabling sibling collectors."""
    name = "required_event_record"
    error = "OBSERVATION_RECORD_SNAPSHOT_SCOPE_INVALID"
    scopes = []
    gateway_modes = []
    for raw in configurations:
        scoped = {}
        overlays = raw.get(CHECK_CONFIG)
        bindings = raw.get("run_binding")
        _validate_normal_option_placement(raw, NORMAL_SERVING_READINESS_OPTION, {
            ("observation_state", NORMAL_SERVING_READINESS_OPTION),
            (CHECK_CONFIG, "OBS-02", NORMAL_SERVING_READINESS_OPTION),
        }, NORMAL_SERVING_READINESS_SCOPE_ERROR)
        _validate_normal_option_placement(raw, NORMAL_REQUEST_LATENCY_OPTION, {
            (CHECK_CONFIG, "LIF-03", NORMAL_REQUEST_LATENCY_OPTION),
        }, NORMAL_REQUEST_LATENCY_SCOPE_ERROR)
        _validate_normal_option_placement(raw, NORMAL_STARTUP_LOAD_OPTION, {
            (CHECK_CONFIG, "LIF-01", NORMAL_STARTUP_LOAD_OPTION),
        }, NORMAL_STARTUP_LOAD_SCOPE_ERROR)
        _validate_normal_option_placement(raw, INSTALLED_PLUGIN_INVENTORY_OPTION, {
            (CHECK_CONFIG, "SUP-10", INSTALLED_PLUGIN_INVENTORY_OPTION),
        }, INSTALLED_PLUGIN_INVENTORY_SCOPE_ERROR)
        _validate_normal_option_placement(raw, CILIUM_EGRESS_STATUS_OPTION, {
            (CHECK_CONFIG, "PLT-07", CILIUM_EGRESS_STATUS_OPTION),
        }, CILIUM_EGRESS_STATUS_SCOPE_ERROR)
        if type(overlays) is dict and any(
                check_id != "REQ-09" and type(section) is dict and NORMAL_TOKEN_INPUT_OPTION in section
                for check_id, section in overlays.items()):
            raise ValueError("NORMAL_TOKEN_INPUT_EXECUTION_SCOPE_INVALID")
        for check_id, option in RECORD_SNAPSHOT_OPTIONS.items():
            overlay = overlays.get(check_id) if type(overlays) is dict else None
            sections = (raw.get(name), overlay)
            gateway = check_id == "OBS-01" and any(
                type(settings) is dict and GATEWAY_RECORD_SNAPSHOT_OPTION in settings for settings in sections)
            if not gateway and not any(type(settings) is dict and option in settings for settings in sections):
                continue
            settings = for_check(raw, name, check_id).get(name, {})
            if (settings.get(option) is not True or gateway and
                    (settings.get(GATEWAY_RECORD_SNAPSHOT_OPTION) is not True
                     or raw.get("update", {}).get("kubernetes_candidate_binding", "prebound") != "prebound")):
                raise ValueError(error)
            scoped[check_id] = (settings, bindings.get(check_id) if type(bindings) is dict else None)
        if scoped:
            if (allowed_checks is None or set(scoped) != {cid for cid in allowed_checks if pack.checks[cid].executor == name}
                    or raw.get("update", {}).get("mode", "external") != "external"
                    or raw.get("executors", {}).get(name) != "kubernetes"
                    or raw.get("record_attestation") or raw.get("lab_activation")):
                raise ValueError(error)
            sessions = [binding.get("record_session") for _, binding in scoped.values() if type(binding) is dict]
            if len(sessions) == 2 and sessions[0] == sessions[1]:
                raise ValueError("OBSERVATION_RECORD_SNAPSHOT_SESSIONS_MUST_DIFFER")
        stream_cases = {}
        for check_id, (executor, option, scope_error) in _NORMAL_ONLY_CASES.items():
            overlay = overlays.get(check_id) if type(overlays) is dict else None
            if not any(type(settings) is dict and option in settings for settings in (raw.get(executor), overlay)):
                continue
            settings = for_check(raw, executor, check_id).get(executor, {})
            if (settings[option] is not True or allowed_checks is None
                    or (executor not in {"stream_buffer", "boundary_egress", "supply_boot"}
                        and {cid for cid in allowed_checks if pack.checks[cid].executor == executor} != {check_id})
                    or raw.get("executors", {}).get(executor) != "kubernetes"
                    or raw.get("update", {}).get("mode", "external") != "external"
                    or raw.get("lab_activation")
                    or (check_id in {"LIF-01", "LIF-03", "OBS-02", "PLT-07", "REQ-09", "REQ-10", "SUP-10"}
                        and raw.get("record_attestation"))
                    or (check_id in {"LIF-01", "LIF-03", "OBS-02", "PLT-07", "SUP-10"} and "run_binding" in raw
                        and (type(bindings) is not dict or check_id in bindings))
                    or (check_id in {"LIF-01", "PLT-07", "SUP-10"}
                        and set(settings) - {option, "target", "kubectl_binary", "max_duration_seconds"})):
                raise ValueError(scope_error)
            scoped[check_id] = (settings, None)
            if executor == "stream_buffer":
                stream_cases[check_id] = settings
        if stream_cases:
            stream_error = (NORMAL_REQUEST_LATENCY_SCOPE_ERROR if "LIF-03" in stream_cases
                            else _NORMAL_ONLY_CASES["LIF-04"][2])
            if set(stream_cases) != {cid for cid in allowed_checks if pack.checks[cid].executor == "stream_buffer"}:
                raise ValueError(stream_error)
            if "LIF-03" in stream_cases:
                _validate_normal_option_placement(raw, NORMAL_STREAM_OPTION, {
                    (CHECK_CONFIG, "LIF-04", NORMAL_STREAM_OPTION),
                }, stream_error)
                base = raw.get("stream_buffer", {})
                target = base.get("target")
                if (type(target) is not str or not target
                        or any(settings.get("target") != target or {"prepared", "common_inputs"} & settings.keys()
                               for settings in stream_cases.values())
                        or ("run_binding" in raw and (type(bindings) is not dict or set(stream_cases) & bindings.keys()))):
                    raise ValueError(stream_error)
        supply_cases = {cid: scoped[cid][0] for cid in ("LIF-01", "SUP-10") if cid in scoped}
        if supply_cases:
            supply_error = (NORMAL_STARTUP_LOAD_SCOPE_ERROR if "LIF-01" in supply_cases
                            else INSTALLED_PLUGIN_INVENTORY_SCOPE_ERROR)
            if set(supply_cases) != {cid for cid in allowed_checks if pack.checks[cid].executor == "supply_boot"}:
                raise ValueError(supply_error)
            if "LIF-01" in supply_cases:
                target = raw.get("supply_boot", {}).get("target")
                if (type(target) is not str or not target
                        or any(settings.get("target") != target for settings in supply_cases.values())):
                    raise ValueError(supply_error)
        if "PLT-07" in scoped:
            executor = "boundary_egress"
            egress_checks = {cid for cid in allowed_checks if pack.checks[cid].executor == executor}
            target = raw.get(executor, {}).get("target")
            if (egress_checks not in ({"PLT-07"}, {"PLT-07", "OBS-04"})
                    or type(target) is not str or not target or scoped["PLT-07"][0].get("target") != target):
                raise ValueError(CILIUM_EGRESS_STATUS_SCOPE_ERROR)
            local_option = KUBERNETES_BOUND_CHECK_OPTIONS["OBS-04"]
            _validate_normal_option_placement(raw, local_option, {
                (CHECK_CONFIG, "OBS-04", local_option),
            }, CILIUM_EGRESS_STATUS_SCOPE_ERROR)
            if "OBS-04" in egress_checks:
                local = for_check(raw, executor, "OBS-04").get(executor, {})
                if (local.get(local_option) is not True or local.get("target") != target
                        or set(local) - {local_option, "target", "kubectl_binary", "max_duration_seconds",
                                         "usage_wait_seconds"}
                        or (type(bindings) is dict and "OBS-04" in bindings)):
                    raise ValueError(CILIUM_EGRESS_STATUS_SCOPE_ERROR)
                scoped["OBS-04"] = (local, None)
        scopes.append(scoped)
        gateway_modes.append(scoped.get("OBS-01", ({}, None))[0].get(GATEWAY_RECORD_SNAPSHOT_OPTION) is True)
    for check_id, (_, _, scope_error) in _NORMAL_ONLY_CASES.items():
        sides = [check_id in scoped for scoped in scopes]
        if any(sides) and (len(sides) != 2 or not all(sides)):
            raise ValueError(scope_error)
    if any(scopes) and (len(scopes) != 2 or set(scopes[0]) != set(scopes[1])):
        raise ValueError(error)
    if any(gateway_modes) and not all(gateway_modes):
        raise ValueError(error)
    return scopes
