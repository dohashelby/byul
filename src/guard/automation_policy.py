"""Pure policy-list shape checks shared by execution and stored-evidence judging.

No engine, network, clock or filesystem dependencies belong in this module.
"""


def string_list(values: dict, key: str, *, empty: bool = False) -> list[str]:
    value = values.get(key)
    if not isinstance(value, list) or (not empty and not value) or any(type(v) is not str or not v.strip() for v in value):
        raise ValueError(f"{key} must be an explicit {'possibly empty ' if empty else 'nonempty '}list[str]")
    if len(set(value)) != len(value):
        raise ValueError(f"{key} must not contain duplicates")
    return value
