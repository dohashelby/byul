"""Runtime policy validity, revocation, and durable load history."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .approval import ApprovalResult
from .policy import (
    DuplicateKeyError,
    PolicyState,
    _object_without_duplicates,
    _reject_json_constant,
    parse_policy_time,
)
from .store import JsonlStore


@dataclass(frozen=True)
class RevocationState:
    state: str
    path: str | None
    loaded_at: str
    entries: tuple[dict[str, str], ...]
    sha256: str | None = None
    error_code: str | None = None
    error_message: str | None = None


def _utc_now_datetime() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now() -> str:
    return _utc_now_datetime().isoformat()


def _revocation_error(
    path: str, loaded_at: str, code: str, message: str, sha256: str | None = None
) -> RevocationState:
    return RevocationState("unknown", path, loaded_at, (), sha256, code, message)


def load_revocations(path: str | Path | None) -> RevocationState:
    """Load the optional revocation snapshot without raising file errors."""

    loaded_at = _utc_now()
    if path is None:
        return RevocationState("not_configured", None, loaded_at, ())
    path_text = str(path)
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return _revocation_error(
            path_text, loaded_at, "REVOCATIONS_NOT_FOUND", "revocations file not found"
        )
    except (OSError, ValueError) as error:
        return _revocation_error(
            path_text,
            loaded_at,
            "REVOCATIONS_READ_ERROR",
            f"could not read revocations: {error}",
        )

    digest = hashlib.sha256(raw).hexdigest()
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError:
        return _revocation_error(
            path_text, loaded_at, "MALFORMED_REVOCATIONS", "revocations are not UTF-8", digest
        )
    except (DuplicateKeyError, json.JSONDecodeError, ValueError, RecursionError) as error:
        return _revocation_error(
            path_text,
            loaded_at,
            "MALFORMED_REVOCATIONS",
            f"invalid revocations JSON: {error}",
            digest,
        )

    if not isinstance(document, dict) or set(document) != {"schema_version", "revoked"}:
        return _revocation_error(
            path_text,
            loaded_at,
            "INVALID_REVOCATIONS",
            "revocations must contain only schema_version and revoked",
            digest,
        )
    if document["schema_version"] != "runtime-mvp-revocations/1.0":
        return _revocation_error(
            path_text,
            loaded_at,
            "INVALID_REVOCATIONS",
            "unsupported revocations schema_version",
            digest,
        )
    entries = document["revoked"]
    if not isinstance(entries, list):
        return _revocation_error(
            path_text, loaded_at, "INVALID_REVOCATIONS", "revoked must be a list", digest
        )
    required = {"policy_id", "policy_version", "sha256", "revoked_at", "reason"}
    normalized: list[dict[str, str]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != required:
            return _revocation_error(
                path_text,
                loaded_at,
                "INVALID_REVOCATIONS",
                f"revoked[{index}] has invalid fields",
                digest,
            )
        if any(not isinstance(entry[name], str) or not entry[name].strip() for name in required):
            return _revocation_error(
                path_text,
                loaded_at,
                "INVALID_REVOCATIONS",
                f"revoked[{index}] fields must be non-empty strings",
                digest,
            )
        if len(entry["sha256"]) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in entry["sha256"]
        ):
            return _revocation_error(
                path_text,
                loaded_at,
                "INVALID_REVOCATIONS",
                f"revoked[{index}].sha256 must be a SHA-256 hex digest",
                digest,
            )
        try:
            parse_policy_time(entry["revoked_at"])
        except ValueError:
            return _revocation_error(
                path_text,
                loaded_at,
                "INVALID_REVOCATIONS",
                f"revoked[{index}].revoked_at must include a timezone",
                digest,
            )
        normalized.append({name: entry[name] for name in sorted(required)})
    return RevocationState("ok", path_text, loaded_at, tuple(normalized), digest)


def evaluate_policy(
    policy_state: PolicyState,
    revocations: RevocationState,
    *,
    approval: ApprovalResult | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate the immutable startup snapshot for one new request."""

    evaluated_at = (now or _utc_now_datetime()).astimezone(timezone.utc)
    policy = policy_state.policy
    loaded = policy_state.ok and policy is not None
    period_state = "not_configured"
    period_result = "unbounded"
    reason_code: str | None = None
    reason_message: str | None = None

    if not loaded:
        reason_code = "POLICY_UNAVAILABLE"
        reason_message = "policy is unavailable"
        period_state = "unknown"
        period_result = "policy_unavailable"
    elif policy.valid_from is not None and evaluated_at < parse_policy_time(policy.valid_from):
        reason_code = "POLICY_NOT_YET_VALID"
        reason_message = "policy is not yet valid"
        period_state = "not_yet_valid"
        period_result = "before valid_from"
    elif policy.valid_until is not None and evaluated_at >= parse_policy_time(policy.valid_until):
        reason_code = "POLICY_EXPIRED"
        reason_message = "policy has expired"
        period_state = "expired"
        period_result = "valid_until elapsed"
    elif policy.valid_from is not None or policy.valid_until is not None:
        period_state = "valid"
        period_result = "within configured period"

    matched: dict[str, str] | None = None
    if loaded and revocations.state == "ok":
        matched = next(
            (
                dict(entry)
                for entry in revocations.entries
                if entry["policy_id"] == policy.policy_id
                and entry["policy_version"] == policy.policy_version
                and entry["sha256"].lower() == (policy_state.sha256 or "").lower()
            ),
            None,
        )
    if revocations.state == "not_configured":
        revocation_result = "not_configured"
    elif revocations.state == "unknown":
        revocation_result = "unknown"
        if reason_code is None:
            reason_code = "POLICY_REVOCATION_UNKNOWN"
            reason_message = "policy revocation status is unavailable"
    elif matched is not None:
        revocation_result = "revoked"
        if reason_code is None:
            reason_code = "POLICY_REVOKED"
            reason_message = "policy has been revoked"
    else:
        revocation_result = "not_revoked"

    if approval is None:
        approval_status = {
            "state": "not_configured",
            "checked_at": None,
            "verifier": None,
            "reason_code": None,
            "reason_message": None,
            "evidence": {},
        }
    else:
        approval_status = approval.status()
        if approval.state not in {"verified", "last_verified"} and reason_code is None:
            reason_code = {
                "unverified": "POLICY_APPROVAL_UNVERIFIED",
                "rejected": "POLICY_APPROVAL_REJECTED",
                "error": "POLICY_APPROVAL_ERROR",
            }[approval.state]
            reason_message = approval.reason_message
    return {
        "loaded": loaded,
        "gateway_active": loaded and reason_code is None,
        "external_enforcement": "not_connected",
        "evaluated_at": evaluated_at.isoformat(),
        "reason_code": reason_code,
        "reason_message": reason_message,
        "validity": [
            {
                "item": "period",
                "state": period_state,
                "result": period_result,
                "valid_from": policy.valid_from if policy else None,
                "valid_until": policy.valid_until if policy else None,
            },
            {
                "item": "revocation",
                "state": revocations.state,
                "result": revocation_result,
                "matched": matched,
                "error_code": revocations.error_code,
                "error_message": revocations.error_message,
            },
            {
                "item": "approval",
                "state": approval_status["state"],
                "result": approval_status["reason_code"] or approval_status["state"],
                "value": policy.approval_ref if policy else None,
                "checked_at": approval_status["checked_at"],
                "verifier": approval_status["verifier"],
                "evidence": approval_status["evidence"],
            },
        ],
        "approval": approval_status,
    }


class PolicyHistory:
    """Append and query startup policy observations."""

    def __init__(self, path: str | Path) -> None:
        self.store = JsonlStore(path)
        self.state = "unknown"
        self.last_error: str | None = None
        self.last_error_at: str | None = None

    @staticmethod
    def _normalized(record: dict[str, Any]) -> dict[str, Any]:
        result = dict(record)
        for name in (
            "event",
            "at",
            "observation_session_id",
            "path",
            "policy_id",
            "policy_version",
            "sha256",
            "schema_version",
            "valid_from",
            "valid_until",
            "approval_ref",
            "approval_state",
            "approval_reason_code",
            "approval_checked_at",
            "approval_verifier",
            "error_code",
        ):
            result.setdefault(name, None)
        previous = result.get("previous")
        if not isinstance(previous, dict):
            previous = {}
        result["previous"] = {
            "policy_id": previous.get("policy_id"),
            "policy_version": previous.get("policy_version"),
            "sha256": previous.get("sha256"),
        }
        result.setdefault("same_version_content_changed", False)
        return result

    def append_startup(
        self,
        policy_state: PolicyState,
        session_id: str,
        approval: ApprovalResult | None = None,
    ) -> dict[str, Any] | None:
        try:
            existing, _ = self.store.read_all()
        except OSError as error:
            self.state = "error"
            self.last_error = str(error)
            self.last_error_at = _utc_now()
            return None
        previous_record = self._normalized(existing[-1]) if existing else None
        policy = policy_state.policy
        previous = {
            "policy_id": previous_record.get("policy_id") if previous_record else None,
            "policy_version": previous_record.get("policy_version") if previous_record else None,
            "sha256": previous_record.get("sha256") if previous_record else None,
        }
        same_version_changed = bool(
            policy
            and previous["policy_id"] == policy.policy_id
            and previous["policy_version"] == policy.policy_version
            and previous["sha256"] is not None
            and previous["sha256"] != policy_state.sha256
        )
        record = {
            "event": "loaded" if policy_state.ok else "load_failed",
            "at": _utc_now(),
            "observation_session_id": session_id,
            "path": policy_state.path,
            "policy_id": policy.policy_id if policy else None,
            "policy_version": policy.policy_version if policy else None,
            "sha256": policy_state.sha256,
            "schema_version": policy.schema_version if policy else None,
            "valid_from": policy.valid_from if policy else None,
            "valid_until": policy.valid_until if policy else None,
            "approval_ref": policy.approval_ref if policy else None,
            "approval_state": approval.state if approval else "not_configured",
            "approval_reason_code": approval.reason_code if approval else None,
            "approval_checked_at": approval.checked_at if approval else None,
            "approval_verifier": approval.verifier if approval else None,
            "error_code": policy_state.error_code,
            "previous": previous,
            "same_version_content_changed": same_version_changed,
        }
        try:
            self.store.append(record)
        except OSError as error:
            self.state = "error"
            self.last_error = str(error)
            self.last_error_at = _utc_now()
            return None
        self.state = "ok"
        self.last_error = None
        return record

    def latest(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            records, _ = self.store.read_all()
        except OSError as error:
            self.state = "error"
            self.last_error = str(error)
            self.last_error_at = _utc_now()
            return []
        return [self._normalized(record) for record in records[-limit:]][::-1]

    def status(self) -> dict[str, Any]:
        try:
            _, corrupt = self.store.read_all()
        except OSError as error:
            self.state = "error"
            self.last_error = str(error)
            self.last_error_at = _utc_now()
            corrupt = []
        return {
            "path": str(self.store.path),
            "state": self.state,
            "ok": self.state == "ok",
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "corrupt_lines": corrupt[:20],
        }
