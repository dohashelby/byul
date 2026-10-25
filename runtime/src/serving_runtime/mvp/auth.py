"""Replaceable Gateway authentication frame (static bearer-token principals).

This is a LAB frame for plan v1.4 F2, not a selected identity provider. The
real authentication integration (D-02) and accounts/roles (D-03) are still
open. Tokens are stored only as SHA-256 digests; neither tokens nor digests are
written to records, status, or logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Any

from .policy import DuplicateKeyError, _object_without_duplicates, _reject_json_constant


SCHEMA_VERSION = "runtime-auth/1.0"
CHAT_ROUTE = "POST /v1/chat/completions"
# All read-only query APIs (status, requests, evidence, observations, ...).
READ_API_ROUTE = "GET /api/*"
PROTECTED_ROUTES = frozenset({CHAT_ROUTE, READ_API_ROUTE})
# The chat route must always be covered; read APIs are protected only when the
# config lists them, so existing configs keep their behaviour.
REQUIRED_ROUTES = frozenset({CHAT_ROUTE})
DEFAULT_ROUTE_ROLES = {CHAT_ROUTE: ("inference",)}
METHOD = "bearer_sha256"

_TOP_LEVEL = {"schema_version", "auth_ref", "principals", "route_roles"}
_PRINCIPAL_FIELDS = {"principal_id", "roles", "token_sha256", "allowed_models", "disabled"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_ROLE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
# Printable ASCII without spaces; length bounds keep header handling simple.
_TOKEN = re.compile(r"[\x21-\x7e]{16,512}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_digest(token: str) -> str:
    """Return the digest stored in the auth file for a bearer token."""

    if _TOKEN.fullmatch(token) is None:
        raise ValueError("token must be 16-512 printable ASCII characters without spaces")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class Principal:
    principal_id: str
    roles: tuple[str, ...]
    token_sha256: str
    allowed_models: tuple[str, ...] | None = None
    disabled: bool = False


@dataclass(frozen=True)
class AuthResult:
    """Outcome of authenticating one request. Never carries the credential."""

    state: str  # not_configured | authenticated | missing | invalid | unavailable
    principal: Principal | None = None

    def record(self) -> dict[str, Any]:
        principal = self.principal
        return {
            "state": self.state,
            "method": METHOD if self.state != "not_configured" else None,
            "principal_id": principal.principal_id if principal else None,
            "roles": list(principal.roles) if principal else [],
        }


@dataclass(frozen=True)
class AuthState:
    state: str  # not_configured | ok | error
    path: str | None
    sha256: str | None
    loaded_at: str
    auth_ref: str | None = None
    principals: tuple[Principal, ...] = ()
    route_roles: tuple[tuple[str, tuple[str, ...]], ...] = ()
    error_code: str | None = None
    error_message: str | None = None

    @property
    def configured(self) -> bool:
        return self.state != "not_configured"

    def roles_for(self, route: str) -> tuple[str, ...]:
        return dict(self.route_roles).get(route, ())

    @property
    def read_api_protected(self) -> bool:
        return self.state == "ok" and READ_API_ROUTE in dict(self.route_roles)

    def authorize_read(self, authorization: str | None) -> tuple[int, str, str] | None:
        """Return (status, code, message) when a read API request must be refused."""

        if self.state == "not_configured":
            return None
        if self.state != "ok":
            # The config is unreadable, so whether reads were protected is unknown.
            return (503, "AUTH_UNAVAILABLE", "authentication configuration is unavailable")
        if not self.read_api_protected:
            return None
        result = self.authenticate(authorization)
        if result.state == "missing":
            return (401, "AUTH_REQUIRED", "authentication is required")
        if result.state != "authenticated" or result.principal is None:
            return (401, "AUTH_INVALID", "authentication failed")
        if not set(self.roles_for(READ_API_ROUTE)) & set(result.principal.roles):
            return (403, "ROLE_NOT_ALLOWED", "principal role is not allowed for this route")
        return None

    def status(self) -> dict[str, Any]:
        active = [principal for principal in self.principals if not principal.disabled]
        return {
            "state": self.state,
            "method": METHOD if self.configured else None,
            "path": self.path,
            "loaded_at": self.loaded_at,
            "auth_ref": self.auth_ref,
            "principal_count": len(active),
            "disabled_principal_count": len(self.principals) - len(active),
            "route_roles": {route: list(roles) for route, roles in self.route_roles},
            "protected_routes": sorted(route for route, _ in self.route_roles),
            # 화면은 byul platform이 맡는다. Gateway는 정적 화면을 서빙하지 않는다.
            "unprotected": [] if self.read_api_protected else ["GET /api/*"],
            "error_code": self.error_code,
            "error_message": self.error_message,
        }

    def authenticate(self, authorization: str | None) -> AuthResult:
        if self.state == "not_configured":
            return AuthResult("not_configured")
        if self.state != "ok":
            return AuthResult("unavailable")
        if authorization is None or not authorization.strip():
            return AuthResult("missing")
        scheme, _, token = authorization.strip().partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or _TOKEN.fullmatch(token) is None:
            return AuthResult("invalid")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        matched: Principal | None = None
        # Compare against every principal so timing does not reveal position.
        for principal in self.principals:
            if hmac.compare_digest(principal.token_sha256, digest) and matched is None:
                matched = principal
        if matched is None or matched.disabled:
            return AuthResult("invalid")
        return AuthResult("authenticated", matched)


def not_configured() -> AuthState:
    return AuthState("not_configured", None, None, _utc_now())


def _error(path: str, loaded_at: str, code: str, message: str, digest: str | None = None) -> AuthState:
    return AuthState("error", path, digest, loaded_at, error_code=code, error_message=message)


def _string_list(value: object, pattern: re.Pattern[str], where: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or pattern.fullmatch(item) is None for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{where} must be a non-empty list of unique valid names")
    return tuple(value)


def _parse(document: object) -> tuple[str | None, tuple[Principal, ...], tuple[tuple[str, tuple[str, ...]], ...]]:
    if not isinstance(document, dict):
        raise ValueError("auth config must be a JSON object")
    unknown = sorted(document.keys() - _TOP_LEVEL)
    if unknown:
        raise ValueError(f"unknown auth field(s): {', '.join(unknown)}")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    auth_ref = document.get("auth_ref")
    if auth_ref is not None and (not isinstance(auth_ref, str) or not auth_ref.strip()):
        raise ValueError("auth_ref must be a non-empty string")

    principals_value = document.get("principals")
    if not isinstance(principals_value, list) or not principals_value:
        raise ValueError("principals must be a non-empty list")
    principals: list[Principal] = []
    for index, item in enumerate(principals_value):
        where = f"principals[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be an object")
        unknown = sorted(item.keys() - _PRINCIPAL_FIELDS)
        if unknown:
            raise ValueError(f"{where} has unknown field(s): {', '.join(unknown)}")
        principal_id = item.get("principal_id")
        if not isinstance(principal_id, str) or _ID.fullmatch(principal_id) is None:
            raise ValueError(f"{where}.principal_id is invalid")
        roles = _string_list(item.get("roles"), _ROLE, f"{where}.roles")
        digest = item.get("token_sha256")
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise ValueError(f"{where}.token_sha256 must be 64 lowercase hex characters")
        allowed_models: tuple[str, ...] | None = None
        if "allowed_models" in item:
            models = item["allowed_models"]
            if (
                not isinstance(models, list)
                or not models
                or any(not isinstance(model, str) or not model.strip() for model in models)
            ):
                raise ValueError(f"{where}.allowed_models must be a non-empty list of strings")
            allowed_models = tuple(models)
        disabled = item.get("disabled", False)
        if not isinstance(disabled, bool):
            raise ValueError(f"{where}.disabled must be a boolean")
        principals.append(Principal(principal_id, roles, digest, allowed_models, disabled))
    ids = [principal.principal_id for principal in principals]
    if len(ids) != len(set(ids)):
        raise ValueError("principal_id values must be unique")
    digests = [principal.token_sha256 for principal in principals]
    if len(digests) != len(set(digests)):
        raise ValueError("token_sha256 values must be unique")

    route_value = document.get("route_roles")
    if route_value is None:
        route_roles = dict(DEFAULT_ROUTE_ROLES)
    else:
        if not isinstance(route_value, dict) or not route_value:
            raise ValueError("route_roles must be a non-empty object")
        route_roles = {}
        for route, roles in route_value.items():
            if route not in PROTECTED_ROUTES:
                raise ValueError(f"route_roles key {route!r} is not a protectable route")
            route_roles[route] = _string_list(roles, _ROLE, f"route_roles.{route}")
    # The inference route must be covered so it is never left open silently.
    missing = sorted(REQUIRED_ROUTES - route_roles.keys())
    if missing:
        raise ValueError(f"route_roles must cover: {', '.join(missing)}")
    return auth_ref, tuple(principals), tuple(sorted(route_roles.items()))


def load_auth(path: str | Path | None) -> AuthState:
    """Load the auth config. Any problem becomes an error state (fail closed)."""

    if path is None:
        return not_configured()
    path_text = str(path)
    loaded_at = _utc_now()
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return _error(path_text, loaded_at, "AUTH_CONFIG_NOT_FOUND", "auth config not found")
    except OSError as error:
        return _error(path_text, loaded_at, "AUTH_CONFIG_READ_ERROR", f"could not read auth config: {type(error).__name__}")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except DuplicateKeyError as error:
        return _error(path_text, loaded_at, "DUPLICATE_KEY", str(error), digest)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return _error(path_text, loaded_at, "MALFORMED_JSON", "auth config is not valid JSON", digest)
    try:
        auth_ref, principals, route_roles = _parse(document)
    except ValueError as error:
        return _error(path_text, loaded_at, "INVALID_AUTH_CONFIG", str(error), digest)
    return AuthState(
        "ok",
        path_text,
        digest,
        loaded_at,
        auth_ref=auth_ref,
        principals=principals,
        route_roles=route_roles,
    )
