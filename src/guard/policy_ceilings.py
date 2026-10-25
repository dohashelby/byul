"""Pure policy ceiling callbacks; independent of executor/runtime imports."""
from typing import Callable

from .credential_policy import manual_pass_ceiling as credential_manual_pass_ceiling

MANUAL_PASS_CEILINGS: dict[str, Callable] = {
    "credential_exposure": credential_manual_pass_ceiling,
}


def manual_pass_ceiling(check, values, evidence) -> dict[str, str]:
    callback = MANUAL_PASS_CEILINGS.get(check.executor)
    return callback(check, values, evidence) if callback is not None else {}
