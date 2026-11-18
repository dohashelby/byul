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
NORMAL_STREAM_OPTION = "normal_stream_only"
NORMAL_SAMPLING_OPTION = "normal_sampling_only"
NORMAL_TOKEN_INPUT_OPTION = "normal_token_input_only"
NORMAL_SERVING_READINESS_OPTION = "normal_serving_readiness_only"
NORMAL_SERVING_READINESS_SCOPE_ERROR = "NORMAL_SERVING_READINESS_EXECUTION_SCOPE_INVALID"
_NORMAL_ONLY_CASES = {
    "LIF-04": ("stream_buffer", NORMAL_STREAM_OPTION, "NORMAL_STREAM_EXECUTION_SCOPE_INVALID"),
    "OBS-02": ("observation_state", NORMAL_SERVING_READINESS_OPTION, NORMAL_SERVING_READINESS_SCOPE_ERROR),
    "REQ-09": ("input_validation", NORMAL_TOKEN_INPUT_OPTION, "NORMAL_TOKEN_INPUT_EXECUTION_SCOPE_INVALID"),
    "REQ-10": ("sampling_validation", NORMAL_SAMPLING_OPTION, "NORMAL_SAMPLING_EXECUTION_SCOPE_INVALID"),
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


def _serving_readiness_markers(value, path=()):
    if type(value) is dict:
        for key, child in value.items():
            location = (*path, key)
            if key == NORMAL_SERVING_READINESS_OPTION:
                yield location, child
            else:
                yield from _serving_readiness_markers(child, location)
    elif type(value) is list:
        for index, child in enumerate(value):
            yield from _serving_readiness_markers(child, (*path, index))


def normal_serving_readiness_requested(raw):
    """Detect a serving-readiness opt-in before validating its placement or value."""
    return next(_serving_readiness_markers(raw), None) is not None


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
        for path, value in _serving_readiness_markers(raw):
            if path[:2] in {("sides", "baseline"), ("sides", "candidate")}:
                path = path[2:]
            if value is not True or path not in {
                ("observation_state", NORMAL_SERVING_READINESS_OPTION),
                (CHECK_CONFIG, "OBS-02", NORMAL_SERVING_READINESS_OPTION),
            }:
                raise ValueError(NORMAL_SERVING_READINESS_SCOPE_ERROR)
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
        for check_id, (executor, option, scope_error) in _NORMAL_ONLY_CASES.items():
            overlay = overlays.get(check_id) if type(overlays) is dict else None
            if not any(type(settings) is dict and option in settings for settings in (raw.get(executor), overlay)):
                continue
            settings = for_check(raw, executor, check_id).get(executor, {})
            if (settings[option] is not True or allowed_checks is None
                    or {cid for cid in allowed_checks if pack.checks[cid].executor == executor} != {check_id}
                    or raw.get("executors", {}).get(executor) != "kubernetes"
                    or raw.get("update", {}).get("mode", "external") != "external"
                    or raw.get("lab_activation")
                    or (check_id in {"OBS-02", "REQ-09", "REQ-10"} and raw.get("record_attestation"))
                    or (check_id == "OBS-02" and "run_binding" in raw
                        and (type(bindings) is not dict or check_id in bindings))):
                raise ValueError(scope_error)
            scoped[check_id] = (settings, None)
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
