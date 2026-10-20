"""Load and validate the small, local MVP policy document."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Policy:
    schema_version: str
    policy_id: str
    policy_version: str
    target_id: str
    allowed_models: tuple[str, ...]
    output_default_tokens: int
    output_max_tokens: int
    max_body_bytes: int


@dataclass(frozen=True)
class PolicyState:
    ok: bool
    policy: Policy | None
    path: str
    sha256: str | None
    loaded_at: str
    error_code: str | None
    error_message: str | None


class DuplicateKeyError(ValueError):
    """Raised when a JSON object contains the same member more than once."""


_FIELDS = {
    "schema_version",
    "policy_id",
    "policy_version",
    "target_id",
    "allowed_models",
    "output_default_tokens",
    "output_max_tokens",
    "max_body_bytes",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _error(
    path: str, loaded_at: str, code: str, message: str, sha256: str | None = None
) -> PolicyState:
    return PolicyState(False, None, path, sha256, loaded_at, code, message)


def load_policy(path: str | Path) -> PolicyState:
    """Return a validated policy state without leaking file or parse exceptions."""

    path_text = str(path)
    loaded_at = _utc_now()
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return _error(path_text, loaded_at, "POLICY_NOT_FOUND", "policy file not found")
    except (OSError, ValueError) as error:
        return _error(path_text, loaded_at, "POLICY_READ_ERROR", f"could not read policy: {error}")

    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
        document = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError:
        return _error(path_text, loaded_at, "MALFORMED_JSON", "policy is not UTF-8", digest)
    except DuplicateKeyError as error:
        return _error(path_text, loaded_at, "DUPLICATE_KEY", str(error), digest)
    except (json.JSONDecodeError, ValueError, RecursionError) as error:
        return _error(path_text, loaded_at, "MALFORMED_JSON", f"invalid policy JSON: {error}", digest)

    if not isinstance(document, dict):
        return _error(path_text, loaded_at, "INVALID_POLICY", "policy must be a JSON object", digest)

    missing = sorted(_FIELDS - document.keys())
    if missing:
        return _error(
            path_text,
            loaded_at,
            "MISSING_FIELD",
            f"missing policy field(s): {', '.join(missing)}",
            digest,
        )
    unknown = sorted(document.keys() - _FIELDS)
    if unknown:
        return _error(
            path_text,
            loaded_at,
            "UNKNOWN_FIELD",
            f"unknown policy field(s): {', '.join(unknown)}",
            digest,
        )
    if document["schema_version"] != "runtime-mvp/1.0":
        return _error(
            path_text, loaded_at, "INVALID_POLICY", "unsupported schema_version", digest
        )
    for name in ("policy_id", "policy_version", "target_id"):
        if not _is_nonempty_string(document[name]):
            return _error(
                path_text,
                loaded_at,
                "INVALID_POLICY",
                f"{name} must be a non-empty string",
                digest,
            )
    models = document["allowed_models"]
    if (
        not isinstance(models, list)
        or not models
        or any(not _is_nonempty_string(model) for model in models)
    ):
        return _error(
            path_text,
            loaded_at,
            "INVALID_POLICY",
            "allowed_models must be a non-empty list of non-empty strings",
            digest,
        )
    for name in ("output_default_tokens", "output_max_tokens", "max_body_bytes"):
        if not _is_positive_integer(document[name]):
            return _error(
                path_text,
                loaded_at,
                "INVALID_POLICY",
                f"{name} must be a positive integer",
                digest,
            )
    if document["output_default_tokens"] > document["output_max_tokens"]:
        return _error(
            path_text,
            loaded_at,
            "INVALID_POLICY",
            "output_default_tokens must not exceed output_max_tokens",
            digest,
        )

    policy = Policy(
        schema_version=document["schema_version"],
        policy_id=document["policy_id"],
        policy_version=document["policy_version"],
        target_id=document["target_id"],
        allowed_models=tuple(models),
        output_default_tokens=document["output_default_tokens"],
        output_max_tokens=document["output_max_tokens"],
        max_body_bytes=document["max_body_bytes"],
    )
    return PolicyState(True, policy, path_text, digest, loaded_at, None, None)
