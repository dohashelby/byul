"""Internal operation authentication and append-only, process-safe event store."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re

from fastapi import HTTPException

ID = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
SHA = r"^[0-9a-f]{64}$"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def deny(code: str, status: int = 409):
    raise HTTPException(status, detail={"code": code})


def authenticate(settings, authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        deny("AUTH_REQUIRED", 401)
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    matches = []
    for item in settings.operation_tokens:
        if hmac.compare_digest(digest, item["sha256"]):
            matches.append(item)
    # Ambiguous credentials must never acquire privileges through list order.
    if len(matches) != 1:
        deny("AUTH_REQUIRED", 401)
    return {k: v for k, v in matches[0].items() if k != "sha256"}


def load_tokens(raw: str) -> tuple[dict, ...]:
    values = json.loads(raw)
    if not isinstance(values, list):
        raise ValueError("operation token configuration must be a list")
    ids, hashes = set(), set()
    for item in values:
        if (not isinstance(item, dict) or set(item) - {"id", "role", "server", "sha256"}
                or not re.fullmatch(ID, item.get("id", ""))
                or not re.fullmatch(SHA, item.get("sha256", ""))
                or item.get("role") not in {"viewer", "runtime_operator", "approver", "agent"}
                or (item["role"] == "agent" and not re.fullmatch(ID, item.get("server", "")))
                or item["id"] in ids or item["sha256"] in hashes):
            raise ValueError("invalid operation token configuration")
        ids.add(item["id"])
        hashes.add(item["sha256"])
    return tuple(values)


class OperationStore:
    """Every mutation appends a complete immutable event under an OS lock.

    No mutable index is authoritative. fsync precedes the response. One local
    filesystem is required; multi-host/NFS deployment needs a database backend.
    """
    def __init__(self, root: Path):
        self.root = root / "runtime_operations"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextmanager
    def transaction(self):
        fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records, heartbeats = {}, {}
            for path in sorted(self.root.glob("event-*.json")):
                event = json.loads(path.read_text())
                if "operation" in event:
                    records[event["operation"]["id"]] = event["operation"]
                if "heartbeat" in event:
                    heartbeats[event["heartbeat"]["server"]] = event["heartbeat"]
            yield records, heartbeats

    def append(self, event: dict) -> None:
        from uuid import uuid4
        # Monotonic sequence is replay order even if the wall clock moves back.
        sequence = max((int(p.name.split("-")[1]) for p in self.root.glob("event-*.json")), default=0) + 1
        name = f"event-{sequence:020d}-{uuid4().hex}.json"
        fd = os.open(self.root / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump({"at": utcnow(), **event}, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
