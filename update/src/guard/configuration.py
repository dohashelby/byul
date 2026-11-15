"""One requested-configuration merge for preparation, selection and execution."""
from copy import deepcopy


def merge_config(base, override):
    out = deepcopy(base)
    for key, value in override.items():
        out[key] = merge_config(out.get(key, {}), value) if isinstance(value, dict) else deepcopy(value)
    return out


CHECK_CONFIG = "check_config"


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
