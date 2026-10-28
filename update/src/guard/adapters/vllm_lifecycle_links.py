"""Declared opaque request links from the reviewed lifecycle profile only."""
from __future__ import annotations
import re


def _fingerprint(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def fingerprints(row):
    payload = row["payload"]
    if row["event"] == "ASGI":
        return [payload["request_sha256"]] if payload["request_sha256"] else []
    if row["event"] == "NATIVE":
        values = [item["request_sha256"] for item in payload["requests"]]
        external = payload["metadata"].get("external_request_sha256")
        if _fingerprint(external):
            values.append(external)
        asgi = asgi_assignment(row)
        if asgi is not None:
            values.append(asgi)
        return list(dict.fromkeys(values))
    return []


def assignment(row):
    """CALL IDs may still be external; only the native RETURN supplies this pair."""
    payload = row["payload"]
    if (row["event"] != "NATIVE" or payload["point"] != "InputProcessor.assign_request_id"
            or payload["phase"] != "RETURN" or len(payload["requests"]) != 1):
        return None
    external = payload["metadata"].get("external_request_sha256")
    if not _fingerprint(external):
        return None
    return external, payload["requests"][0]["request_sha256"]


def asgi_assignment(row):
    """An observed header-context association, never a unique HTTP identity."""
    if assignment(row) is None:
        return None
    value = row["payload"]["metadata"].get("asgi_request_sha256")
    return value if _fingerprint(value) else None
