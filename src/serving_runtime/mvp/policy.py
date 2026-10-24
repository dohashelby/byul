"""Load and validate the small, local MVP policy document."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExtraFieldRule:
    """One test-only request field opened by a runtime-mvp/1.2 zeroday-lab policy."""

    name: str
    mode: str
    constraints: dict[str, Any] = field(default_factory=dict)


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
    valid_from: str | None = None
    valid_until: str | None = None
    approval_ref: str | None = None
    max_context_tokens: int | None = None
    profile: str = "default"
    extra_fields: tuple[ExtraFieldRule, ...] = ()
    max_n: int = 1

    def extra_field_rule(self, name: str) -> ExtraFieldRule | None:
        return next((rule for rule in self.extra_fields if rule.name == name), None)


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


_REQUIRED_FIELDS = {
    "schema_version",
    "policy_id",
    "policy_version",
    "target_id",
    "allowed_models",
    "output_default_tokens",
    "output_max_tokens",
    "max_body_bytes",
}
_V11_FIELDS = _REQUIRED_FIELDS | {
    "valid_from",
    "valid_until",
    "approval_ref",
    "max_context_tokens",
}
_V12_FIELDS = _V11_FIELDS | {"profile", "extra_fields", "max_n"}
_SCHEMA_FIELDS = {
    "runtime-mvp/1.0": _REQUIRED_FIELDS,
    "runtime-mvp/1.1": _V11_FIELDS,
    "runtime-mvp/1.2": _V12_FIELDS,
}
_PROFILES = {"default", "zeroday-lab"}
# Fields the Gateway always validates or normalizes itself; a policy may not
# reopen them as extra fields.
RESERVED_REQUEST_FIELDS = frozenset(
    {"model", "messages", "max_tokens", "stream", "n", "max_completion_tokens"}
)
EXTRA_FIELD_TYPES = frozenset(
    {"integer", "number", "string", "boolean", "array", "object"}
)
_ITEM_TYPES = frozenset({"integer", "number", "string", "boolean"})
_CONSTRAINTS_BY_TYPE = {
    "integer": {"min", "max"},
    "number": {"min", "max"},
    "string": {"max_length"},
    "boolean": set(),
    "array": {"max_items", "items", "max_bytes"},
    "object": {"max_bytes"},
}
_ITEM_CONSTRAINTS_BY_TYPE = {
    "integer": {"min", "max"},
    "number": {"min", "max"},
    "string": {"max_length"},
    "boolean": set(),
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


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and math.isfinite(value))


def _is_non_negative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_bounds(where: str, kind: str, rule: dict[str, Any]) -> None:
    for bound in ("min", "max"):
        if bound in rule:
            value = rule[bound]
            if kind == "integer":
                valid = isinstance(value, int) and not isinstance(value, bool)
            else:
                valid = _is_finite_number(value)
            if not valid:
                raise ValueError(f"{where}.{bound} must be a finite {kind}")
    if "min" in rule and "max" in rule and rule["min"] > rule["max"]:
        raise ValueError(f"{where}.min must not exceed max")
    if "max_length" in rule and not _is_non_negative_integer(rule["max_length"]):
        raise ValueError(f"{where}.max_length must be a non-negative integer")


def _parse_item_rule(where: str, value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    kind = value.get("type")
    if not isinstance(kind, str) or kind not in _ITEM_TYPES:
        raise ValueError(f"{where}.type must be one of {', '.join(sorted(_ITEM_TYPES))}")
    unknown = sorted(value.keys() - {"type"} - _ITEM_CONSTRAINTS_BY_TYPE[kind])
    if unknown:
        raise ValueError(f"{where} has unknown or unsupported key(s): {', '.join(unknown)}")
    _check_bounds(where, kind, value)
    return dict(value)


def _parse_extra_fields(value: object) -> tuple[ExtraFieldRule, ...]:
    if not isinstance(value, dict):
        raise ValueError("extra_fields must be an object")
    rules: list[ExtraFieldRule] = []
    for name in sorted(value):
        where = f"extra_fields.{name}"
        if not name or name != name.strip():
            raise ValueError("extra_fields names must be non-empty without surrounding spaces")
        if name in RESERVED_REQUEST_FIELDS:
            raise ValueError(f"{where} is reserved by the Gateway")
        rule = value[name]
        if not isinstance(rule, dict):
            raise ValueError(f"{where} must be an object")
        mode = rule.get("mode")
        if mode == "passthrough":
            unknown = sorted(rule.keys() - {"mode"})
            if unknown:
                raise ValueError(f"{where} passthrough allows only mode; unknown key(s): {', '.join(unknown)}")
            rules.append(ExtraFieldRule(name, "passthrough"))
            continue
        if mode != "validate":
            raise ValueError(f"{where}.mode must be validate or passthrough")
        kind = rule.get("type")
        if not isinstance(kind, str) or kind not in EXTRA_FIELD_TYPES:
            raise ValueError(f"{where}.type must be one of {', '.join(sorted(EXTRA_FIELD_TYPES))}")
        unknown = sorted(rule.keys() - {"mode", "type"} - _CONSTRAINTS_BY_TYPE[kind])
        if unknown:
            raise ValueError(f"{where} has unknown or unsupported key(s) for type {kind}: {', '.join(unknown)}")
        _check_bounds(where, kind, rule)
        for size in ("max_items", "max_bytes"):
            if size in rule and not _is_non_negative_integer(rule[size]):
                raise ValueError(f"{where}.{size} must be a non-negative integer")
        constraints = {key: item for key, item in rule.items() if key not in {"mode", "type"}}
        constraints["type"] = kind
        if "items" in rule:
            constraints["items"] = _parse_item_rule(f"{where}.items", rule["items"])
        rules.append(ExtraFieldRule(name, "validate", constraints))
    return tuple(rules)


def parse_policy_time(value: str) -> datetime:
    """Parse a policy timestamp, requiring an explicit UTC offset."""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone is required")
    return parsed


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

    missing = sorted(_REQUIRED_FIELDS - document.keys())
    if missing:
        return _error(
            path_text,
            loaded_at,
            "MISSING_FIELD",
            f"missing policy field(s): {', '.join(missing)}",
            digest,
        )
    schema_version = document.get("schema_version")
    if not isinstance(schema_version, str) or schema_version not in _SCHEMA_FIELDS:
        return _error(
            path_text, loaded_at, "INVALID_POLICY", "unsupported schema_version", digest
        )
    allowed_fields = _SCHEMA_FIELDS[schema_version]
    unknown = sorted(document.keys() - allowed_fields)
    if unknown:
        return _error(
            path_text,
            loaded_at,
            "UNKNOWN_FIELD",
            f"unknown policy field(s): {', '.join(unknown)}",
            digest,
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

    if schema_version in {"runtime-mvp/1.1", "runtime-mvp/1.2"}:
        for name in ("valid_from", "valid_until"):
            value = document.get(name)
            if value is None:
                continue
            if not _is_nonempty_string(value):
                return _error(
                    path_text,
                    loaded_at,
                    "INVALID_POLICY",
                    f"{name} must be an ISO8601 string with a timezone",
                    digest,
                )
            try:
                parse_policy_time(value)
            except ValueError:
                return _error(
                    path_text,
                    loaded_at,
                    "INVALID_POLICY",
                    f"{name} must be an ISO8601 string with a timezone",
                    digest,
                )
        approval_ref = document.get("approval_ref")
        if approval_ref is not None and not isinstance(approval_ref, str):
            return _error(
                path_text,
                loaded_at,
                "INVALID_POLICY",
                "approval_ref must be a string",
                digest,
            )
        max_context_tokens = document.get("max_context_tokens")
        if max_context_tokens is not None:
            if not _is_positive_integer(max_context_tokens):
                return _error(
                    path_text,
                    loaded_at,
                    "INVALID_POLICY",
                    "max_context_tokens must be a positive integer",
                    digest,
                )
            if document["output_max_tokens"] >= max_context_tokens:
                return _error(
                    path_text,
                    loaded_at,
                    "INVALID_POLICY",
                    "max_context_tokens must exceed output_max_tokens",
                    digest,
                )

    profile = "default"
    extra_fields: tuple[ExtraFieldRule, ...] = ()
    max_n = 1
    if schema_version == "runtime-mvp/1.2":
        profile = document.get("profile", "default")
        if not isinstance(profile, str) or profile not in _PROFILES:
            return _error(
                path_text,
                loaded_at,
                "INVALID_POLICY",
                "profile must be default or zeroday-lab",
                digest,
            )
        if "extra_fields" in document:
            if profile != "zeroday-lab":
                return _error(
                    path_text,
                    loaded_at,
                    "INVALID_POLICY",
                    "extra_fields require profile zeroday-lab",
                    digest,
                )
            try:
                extra_fields = _parse_extra_fields(document["extra_fields"])
            except ValueError as error:
                return _error(path_text, loaded_at, "INVALID_POLICY", str(error), digest)
        if "max_n" in document:
            max_n = document["max_n"]
            if not _is_positive_integer(max_n):
                return _error(
                    path_text,
                    loaded_at,
                    "INVALID_POLICY",
                    "max_n must be a positive integer",
                    digest,
                )
            if max_n > 1 and profile != "zeroday-lab":
                # The default profile keeps the D-09 LAB value n=1.
                return _error(
                    path_text,
                    loaded_at,
                    "INVALID_POLICY",
                    "max_n above 1 requires profile zeroday-lab",
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
        valid_from=document.get("valid_from"),
        valid_until=document.get("valid_until"),
        approval_ref=document.get("approval_ref"),
        max_context_tokens=document.get("max_context_tokens"),
        profile=profile,
        extra_fields=extra_fields,
        max_n=max_n,
    )
    return PolicyState(True, policy, path_text, digest, loaded_at, None, None)
