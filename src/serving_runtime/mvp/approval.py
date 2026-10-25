"""Replaceable policy approval verification frame.

The LAB implementation compares the exact loaded policy bytes with a static
approval list.  It deliberately does not create keys, signatures, or approval
records; a future verifier owned by the validation-tool side can implement the
same protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Protocol

from .policy import (
    DuplicateKeyError,
    _object_without_duplicates,
    _reject_json_constant,
    parse_policy_time,
)


SCHEMA_VERSION = "runtime-policy-approvals/1.0"
VERIFIER = "lab_sha256_list"
_REQUIRED_ENTRY_FIELDS = {
    "policy_id",
    "policy_version",
    "sha256",
    "approval_ref",
    "approved_at",
}
_OPTIONAL_ENTRY_FIELDS = {"valid_until"}


def _utc_now_datetime() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now() -> str:
    return _utc_now_datetime().isoformat()


@dataclass(frozen=True)
class ApprovalResult:
    """One verifier outcome with non-secret, structured evidence."""

    state: Literal["verified", "unverified", "rejected", "error"]
    checked_at: str
    verifier: str
    reason_code: str
    reason_message: str
    evidence: dict[str, Any]

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "checked_at": self.checked_at,
            "verifier": self.verifier,
            "reason_code": self.reason_code,
            "reason_message": self.reason_message,
            "evidence": dict(self.evidence),
        }


class ApprovalVerifier(Protocol):
    """Interface for an existing approval/signature/bundle verifier adapter."""

    def verify(
        self,
        policy_bytes: bytes,
        policy_id: str,
        policy_version: str,
        approval_ref: str | None,
        *,
        now: datetime | None = None,
    ) -> ApprovalResult: ...


def _result(
    state: Literal["verified", "unverified", "rejected", "error"],
    code: str,
    message: str,
    *,
    checked_at: str,
    evidence: dict[str, Any] | None = None,
) -> ApprovalResult:
    return ApprovalResult(
        state,
        checked_at,
        VERIFIER,
        code,
        message,
        evidence or {},
    )


def _parse_entries(raw: bytes) -> tuple[dict[str, str], ...]:
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError as error:
        raise ValueError("approval list is not UTF-8") from error
    except (DuplicateKeyError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise ValueError("approval list is not valid JSON") from error
    if not isinstance(document, dict) or set(document) != {"schema_version", "entries"}:
        raise ValueError("approval list must contain only schema_version and entries")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported approval list schema_version")
    entries = document["entries"]
    if not isinstance(entries, list):
        raise ValueError("approval list entries must be a list")

    normalized: list[dict[str, str]] = []
    selectors: set[tuple[str, str, str]] = set()
    allowed = _REQUIRED_ENTRY_FIELDS | _OPTIONAL_ENTRY_FIELDS
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"entries[{index}] must be an object")
        if set(entry) - allowed or _REQUIRED_ENTRY_FIELDS - set(entry):
            raise ValueError(f"entries[{index}] has invalid fields")
        if any(
            not isinstance(entry[name], str) or not entry[name].strip()
            for name in _REQUIRED_ENTRY_FIELDS
        ):
            raise ValueError(f"entries[{index}] required fields must be non-empty strings")
        digest = entry["sha256"]
        if len(digest) != 64 or any(character not in "0123456789abcdefABCDEF" for character in digest):
            raise ValueError(f"entries[{index}].sha256 must be a SHA-256 hex digest")
        try:
            approved_at = parse_policy_time(entry["approved_at"])
        except ValueError as error:
            raise ValueError(f"entries[{index}].approved_at must include a timezone") from error
        valid_until = entry.get("valid_until")
        if "valid_until" in entry:
            if not isinstance(valid_until, str) or not valid_until.strip():
                raise ValueError(f"entries[{index}].valid_until must be a non-empty string")
            try:
                expiry = parse_policy_time(valid_until)
            except ValueError as error:
                raise ValueError(f"entries[{index}].valid_until must include a timezone") from error
            if expiry <= approved_at:
                raise ValueError(f"entries[{index}].valid_until must be after approved_at")
        selector = (
            entry["policy_id"],
            entry["policy_version"],
            entry["approval_ref"],
        )
        if selector in selectors:
            raise ValueError(f"entries[{index}] duplicates an approval selector")
        selectors.add(selector)
        item = {name: entry[name] for name in _REQUIRED_ENTRY_FIELDS}
        if "valid_until" in entry:
            assert isinstance(valid_until, str)
            item["valid_until"] = valid_until
        normalized.append(item)
    return tuple(normalized)


class HashListApprovalVerifier:
    """LAB verifier that reloads and strictly validates one approval list."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def verify(
        self,
        policy_bytes: bytes,
        policy_id: str,
        policy_version: str,
        approval_ref: str | None,
        *,
        now: datetime | None = None,
    ) -> ApprovalResult:
        checked = (now or _utc_now_datetime()).astimezone(timezone.utc)
        checked_at = checked.isoformat()
        try:
            raw = Path(self.path).read_bytes()
        except FileNotFoundError:
            return _result(
                "error",
                "APPROVAL_LIST_NOT_FOUND",
                "policy approval list was not found",
                checked_at=checked_at,
            )
        except (OSError, ValueError):
            return _result(
                "error",
                "APPROVAL_LIST_READ_ERROR",
                "policy approval list could not be read",
                checked_at=checked_at,
            )
        try:
            entries = _parse_entries(raw)
        except ValueError as error:
            return _result(
                "error",
                "INVALID_APPROVAL_LIST",
                str(error),
                checked_at=checked_at,
            )

        base_evidence = {
            "schema_version": SCHEMA_VERSION,
            "policy_id": policy_id,
            "policy_version": policy_version,
            "approval_ref": approval_ref,
        }
        if not isinstance(approval_ref, str) or not approval_ref.strip():
            return _result(
                "unverified",
                "POLICY_APPROVAL_REF_MISSING",
                "policy has no approval reference",
                checked_at=checked_at,
                evidence=base_evidence,
            )
        same_policy = [
            entry
            for entry in entries
            if entry["policy_id"] == policy_id
            and entry["policy_version"] == policy_version
        ]
        if not same_policy:
            return _result(
                "unverified",
                "POLICY_APPROVAL_NOT_LISTED",
                "policy approval entry was not found",
                checked_at=checked_at,
                evidence=base_evidence,
            )
        matched = next(
            (entry for entry in same_policy if entry["approval_ref"] == approval_ref),
            None,
        )
        if matched is None:
            return _result(
                "rejected",
                "POLICY_APPROVAL_REF_MISMATCH",
                "policy approval reference does not match",
                checked_at=checked_at,
                evidence=base_evidence,
            )

        evidence = {
            **base_evidence,
            "approved_at": matched["approved_at"],
            "valid_until": matched.get("valid_until"),
            "policy_sha256_match": False,
        }
        digest = hashlib.sha256(policy_bytes).hexdigest()
        if digest.lower() != matched["sha256"].lower():
            return _result(
                "rejected",
                "POLICY_APPROVAL_HASH_MISMATCH",
                "policy bytes do not match the approved digest",
                checked_at=checked_at,
                evidence=evidence,
            )
        evidence["policy_sha256_match"] = True
        if checked < parse_policy_time(matched["approved_at"]):
            return _result(
                "rejected",
                "POLICY_APPROVAL_NOT_YET_VALID",
                "policy approval is not yet valid",
                checked_at=checked_at,
                evidence=evidence,
            )
        valid_until = matched.get("valid_until")
        if valid_until is not None and checked >= parse_policy_time(valid_until):
            return _result(
                "rejected",
                "POLICY_APPROVAL_EXPIRED",
                "policy approval has expired",
                checked_at=checked_at,
                evidence=evidence,
            )
        return _result(
            "verified",
            "POLICY_APPROVAL_VERIFIED",
            "policy approval entry matches the loaded policy bytes",
            checked_at=checked_at,
            evidence=evidence,
        )


def unavailable_policy_result() -> ApprovalResult:
    """Return a verifier-shaped result when no valid policy bytes exist."""

    return _result(
        "error",
        "POLICY_BYTES_UNAVAILABLE",
        "loaded policy bytes are unavailable for approval verification",
        checked_at=_utc_now(),
    )
