"""Per-principal vLLM prefix-cache isolation without secret disclosure."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import os
from typing import Any, Mapping

from .auth import AuthState


CACHE_SALT_KEY_ENV = "SERVING_RUNTIME_CACHE_SALT_KEY"
MINIMUM_KEY_BYTES = 32
_DOMAIN = b"serving-runtime/cache-salt/v1\x00"


@dataclass(frozen=True)
class CacheSaltKey:
    """Startup snapshot of the HMAC key; the value is never serialized."""

    state: str  # configured | missing | too_short | invalid
    _value: bytes | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_environ(
        cls, environ: Mapping[str, str] | None = None
    ) -> "CacheSaltKey":
        source = os.environ if environ is None else environ
        candidate = source.get(CACHE_SALT_KEY_ENV)
        if candidate is None or candidate == "":
            return cls("missing")
        try:
            value = candidate.encode("utf-8")
        except UnicodeEncodeError:
            return cls("invalid")
        if len(value) < MINIMUM_KEY_BYTES:
            return cls("too_short")
        return cls("configured", value)

    def derive(self, principal_id: str) -> str:
        """Return a vLLM-compatible deterministic salt for one principal."""

        if self._value is None:
            raise RuntimeError("cache isolation key is unavailable")
        return hmac.new(
            self._value,
            _DOMAIN + principal_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


def isolation_status(
    mode: str | None,
    auth_state: AuthState,
    backend_kind: str,
    key: CacheSaltKey,
) -> dict[str, Any]:
    """Return only non-secret readiness information for status APIs."""

    if mode is None:
        return {"mode": None, "ready": False, "unavailable_reason_code": "POLICY_UNAVAILABLE"}
    if mode == "off":
        return {"mode": "off", "ready": True, "unavailable_reason_code": None}
    if auth_state.state == "not_configured":
        reason = "CACHE_ISOLATION_AUTH_NOT_CONFIGURED"
    elif auth_state.state != "ok":
        reason = "CACHE_ISOLATION_AUTH_UNAVAILABLE"
    elif key.state == "missing":
        reason = "CACHE_ISOLATION_KEY_MISSING"
    elif key.state == "too_short":
        reason = "CACHE_ISOLATION_KEY_TOO_SHORT"
    elif key.state == "invalid":
        reason = "CACHE_ISOLATION_KEY_INVALID"
    elif backend_kind != "vllm":
        reason = "CACHE_ISOLATION_BACKEND_UNSUPPORTED"
    else:
        reason = None
    return {
        "mode": mode,
        "ready": reason is None,
        "unavailable_reason_code": reason,
    }


def unavailable_message(reason_code: str | None) -> str:
    """Map readiness codes to bounded messages that contain no key material."""

    return {
        "CACHE_ISOLATION_AUTH_NOT_CONFIGURED": (
            "cache isolation requires configured authentication"
        ),
        "CACHE_ISOLATION_AUTH_UNAVAILABLE": (
            "cache isolation authentication is unavailable"
        ),
        "CACHE_ISOLATION_KEY_MISSING": "cache isolation key is unavailable",
        "CACHE_ISOLATION_KEY_TOO_SHORT": (
            "cache isolation key does not meet the minimum requirements"
        ),
        "CACHE_ISOLATION_KEY_INVALID": "cache isolation key is invalid",
        "CACHE_ISOLATION_BACKEND_UNSUPPORTED": (
            "cache isolation requires a supported vllm backend"
        ),
    }.get(reason_code, "cache isolation is unavailable")
