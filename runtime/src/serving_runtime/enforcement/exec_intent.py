"""Bounded optional execution controls; no host filesystem assumptions."""

from pathlib import PurePosixPath
from serving_runtime.common.enforce_rules import ENFORCE_RULE_METHODS, IntentError

VALUES_PER_SELECTOR = 8
MAX_EXEMPT_CALLERS = 32
MAX_BINARY_BYTES = 255


def _paths(raw, limit, field):
    if not isinstance(raw, list) or len(raw) > limit:
        raise IntentError(f"{field} requires at most {limit} exact binary paths")
    values = []
    directories = {"/", "/bin", "/usr", "/usr/bin", "/usr/local/bin", "/sbin", "/usr/sbin", "/tmp", "/opt"}
    for path in raw:
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or not path.isprintable()
            or path.endswith("/")
            or path in directories
            or str(PurePosixPath(path)) != path
            or ".." in PurePosixPath(path).parts
            or len(path.encode()) > MAX_BINARY_BYTES
        ):
            raise IntentError(f"{field} requires printable absolute file paths, not directories")
        if path in values:
            raise IntentError(f"{field} contains duplicates")
        values.append(path)
    if any(a != b and b.startswith(a + "/") for a in values for b in values):
        raise IntentError(f"{field} contains a directory prefix")
    return tuple(values)


def parse_execution_control(root, modes, allowed):
    raw = root.get("tetragon_enforce_methods", {})
    if not isinstance(raw, dict) or raw.keys() - ENFORCE_RULE_METHODS.keys():
        raise IntentError("tetragon_enforce_methods requires reviewed rule IDs")
    methods = dict(raw)
    legacy = root.get("tetragon_enforce_method")
    if legacy is not None and "TG-SECRET-01" in methods and legacy != methods["TG-SECRET-01"]:
        raise IntentError("conflicting TG-SECRET-01 enforcement methods")
    for rule, method in methods.items():
        if not isinstance(method, str) or method not in ENFORCE_RULE_METHODS[rule]:
            raise IntentError("unsupported rule enforcement method")
        if "enforce" not in dict(allowed).get(rule, ()) and dict(modes).get(rule) != "enforce":
            raise IntentError("enforcement method requires explicit allowed enforce mode")
    denied = _paths(root.get("exec_deny_binaries", []), VALUES_PER_SELECTOR, "exec_deny_binaries")
    exempt = _paths(root.get("exec_deny_exempt_callers", []), MAX_EXEMPT_CALLERS, "exec_deny_exempt_callers")
    enabled = "enforce" in dict(allowed).get("TG-EXEC-01", ()) or dict(modes).get("TG-EXEC-01") == "enforce"
    if enabled and (not denied or methods.get("TG-EXEC-01") != "lsm_bprm_check"):
        raise IntentError("TG-EXEC-01 enforce requires denied binaries and explicit lsm_bprm_check")
    return methods, denied, exempt
