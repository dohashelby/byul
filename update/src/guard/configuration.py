"""One requested-configuration merge for preparation, selection and execution."""
from copy import deepcopy


def merge_config(base, override):
    out = deepcopy(base)
    for key, value in override.items():
        out[key] = merge_config(out.get(key, {}), value) if isinstance(value, dict) else deepcopy(value)
    return out


CHECK_CONFIG = "check_config"
UPDATE_CHECK_OUTSIDE_SCOPE = "UPDATE_CHECK_OUTSIDE_EXECUTION_SCOPE"
RECORD_SNAPSHOT_OPTIONS = {
    "OBS-01": "record_snapshot_only",
    "PLT-12": "copy_record_snapshot_only",
}
KUBERNETES_BOUND_CHECK_OPTIONS = {
    **RECORD_SNAPSHOT_OPTIONS,
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


def record_snapshot_scopes(configurations, pack, allowed_checks):
    """Keep the two snapshot children isolated within the shared record executor."""
    name = "required_event_record"
    error = "OBSERVATION_RECORD_SNAPSHOT_SCOPE_INVALID"
    scopes = []
    for raw in configurations:
        scoped = {}
        overlays = raw.get(CHECK_CONFIG)
        bindings = raw.get("run_binding")
        for check_id, option in RECORD_SNAPSHOT_OPTIONS.items():
            overlay = overlays.get(check_id) if type(overlays) is dict else None
            if not any(type(settings) is dict and option in settings for settings in (raw.get(name), overlay)):
                continue
            settings = for_check(raw, name, check_id).get(name, {})
            if settings.get(option) is not True:
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
        scopes.append(scoped)
    if any(scopes) and (len(scopes) != 2 or set(scopes[0]) != set(scopes[1])):
        raise ValueError(error)
    return scopes
