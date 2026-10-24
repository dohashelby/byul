"""Pure credential policy contracts shared by collection and saved judging.

Only path syntax and declared values are checked here. This module performs
no filesystem, network, process or clock operations.
"""
from pathlib import PurePosixPath
import re

MAX_PATHS = 32
MAX_PATH_BYTES = 128


def candidate_paths(value):
    """Return explicit bounded paths; absence is distinct from an empty list."""
    if value is None:
        return None
    if (type(value) is not list or len(value) > MAX_PATHS
            or any(type(path) is not str for path in value) or len(set(value)) != len(value)):
        raise ValueError("credential_candidate_paths must be a unique list of at most 32 paths")
    for candidate in value:
        path = PurePosixPath(candidate)
        if (not path.is_absolute() or len(candidate.encode("utf-8")) > MAX_PATH_BYTES
                or any(ord(character) < 32 for character in candidate) or "\\" in candidate or "//" in candidate
                or any(part in {".", ".."} for part in candidate.split("/"))
                or candidate != str(path)):
            raise ValueError("credential candidate entries must be bounded canonical absolute paths")
    return list(value)


def validate_credential_policy(check, values):
    """Use the same policy meaning for execution and declared PASS ceilings."""
    separated = any(obs.key == "forbidden_mount_violations" for obs in check.required_observations)
    if separated:
        for key in ("forbidden_env_names", "forbidden_mount_paths"):
            value = values.get(key)
            if type(value) is not list or any(type(item) is not str for item in value):
                raise ValueError("credential policy entries must be explicit lists of strings")
    for name in values.get("forbidden_env_names", []):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("forbidden environment entries must be variable names, never values")
    for value in values.get("forbidden_mount_paths", []):
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("credential paths must be absolute container paths")
        if separated and (str(path) != value or value.startswith("//")
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError("mount bans must use canonical absolute container paths")
    if "credential_candidate_paths" in values:
        if values["credential_candidate_paths"] is None:
            raise ValueError("credential_candidate_paths must be an explicit list when supplied")
        candidate_paths(values["credential_candidate_paths"])


def manual_pass_ceiling(check, values, evidence):
    """Cap only security for unsupported new policy semantics; no I/O."""
    if not any(obs.key == "forbidden_mount_violations" for obs in check.required_observations):
        return {}
    try:
        validate_credential_policy(check, values)
    except Exception:
        return {"security": "UNKNOWN"}
    return {}
