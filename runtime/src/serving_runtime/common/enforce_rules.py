"""Reviewed rule/method pairs; registration never grants an enforce transition."""

ENFORCE_RULE_METHODS = {
    "TG-SECRET-01": frozenset({"lsm_file_open", "override_lsm", "sigkill_fd_install", "override_syscall"}),
    "TG-EXEC-01": frozenset({"lsm_bprm_check"}),
}
ENFORCE_METHODS = frozenset().union(*ENFORCE_RULE_METHODS.values())
POLICY_RULES = {"runtime-" + rule.lower(): rule for rule in ENFORCE_RULE_METHODS}


def supports_enforce(rule, method):
    return method in ENFORCE_RULE_METHODS.get(rule, ())


class IntentError(ValueError):
    """The enforcement intent failed strict validation."""
