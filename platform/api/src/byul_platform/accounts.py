"""Login accounts in one private file (0600). No database, no sign-up.

Passwords are stored as scrypt hashes (standard library). The first start creates
``admin`` / ``admin`` flagged as a default password; changing it is not forced unless
BYUL_REQUIRE_PASSWORD_CHANGE=1. Every change bumps ``session_epoch`` so existing
sessions of that account end on their next request.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Iterator

SCHEMA = "byul-platform-accounts/1.0"
ROLES = ("admin", "approver", "runtime_operator", "viewer")
USERNAME = re.compile(r"^[a-z][a-z0-9_.-]{1,31}$")
MIN_PASSWORD = 10
MAX_PASSWORD = 256
_N, _R, _P = 2**14, 8, 1


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def check_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode()[:MAX_PASSWORD * 4], salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Verifying against this keeps unknown-user and wrong-password timing alike.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def password_problem(username: str, password: str) -> str | None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return "PASSWORD_TOO_SHORT"
    if len(password) > MAX_PASSWORD:
        return "PASSWORD_TOO_LONG"
    if password.lower() in {username.lower(), "admin", "password"} or len(set(password)) < 4:
        return "PASSWORD_TOO_WEAK"
    return None


class AccountError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AccountStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._cache: tuple[tuple[int, int], dict[str, dict[str, Any]]] | None = None

    # ------------------------------------------------------------ file
    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path.with_name(self.path.name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _read(self) -> dict[str, dict[str, Any]]:
        info = self.path.stat()
        if info.st_mode & 0o077:
            raise AccountError("ACCOUNTS_FILE_PERMISSIONS")  # must be 0600 (or stricter)
        key = (info.st_mtime_ns, info.st_size)
        if self._cache and self._cache[0] == key:
            return self._cache[1]
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != SCHEMA or not isinstance(value.get("accounts"), list):
            raise AccountError("ACCOUNTS_FILE_INVALID")
        accounts = {}
        for item in value["accounts"]:
            if (not isinstance(item, dict) or not USERNAME.fullmatch(str(item.get("id")))
                    or item.get("role") not in ROLES or not isinstance(item.get("password_hash"), str)
                    or item["id"] in accounts):
                raise AccountError("ACCOUNTS_FILE_INVALID")
            accounts[item["id"]] = item
        self._cache = (key, accounts)
        return accounts

    def _write(self, accounts: dict[str, dict[str, Any]]) -> None:
        temporary = self.path.with_name(self.path.name + ".next")
        temporary.unlink(missing_ok=True)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump({"schema_version": SCHEMA, "accounts": sorted(accounts.values(), key=lambda a: a["id"])},
                      output, ensure_ascii=False, indent=1)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.path)
        self._cache = None

    # ------------------------------------------------------------ public
    def ensure_bootstrap(self) -> bool:
        """Create admin/admin when no account file exists. Returns True if created."""
        with self._locked():
            if self.path.exists():
                return False
            now = utcnow()
            self._write({"admin": {"id": "admin", "role": "admin", "password_hash": hash_password("admin"),
                                   "enabled": True, "default_password": True, "must_change": True,
                                   "session_epoch": 0, "created_at": now, "updated_at": now}})
            return True

    def get(self, username: str) -> dict[str, Any] | None:
        return self._read().get(username)

    def verify(self, username: str, password: str) -> dict[str, Any] | None:
        account = self._read().get(username) if isinstance(username, str) else None
        ok = check_password(password if isinstance(password, str) else "", account["password_hash"] if account else _DUMMY_HASH)
        if not account or not ok or not account.get("enabled", True):
            return None
        return account

    def list(self) -> list[dict[str, Any]]:
        return [{k: a.get(k) for k in ("id", "role", "enabled", "default_password", "must_change", "created_at", "updated_at")}
                for a in sorted(self._read().values(), key=lambda a: a["id"])]

    def _change(self, username: str, **fields) -> dict[str, Any]:
        with self._locked():
            accounts = dict(self._read())
            if username not in accounts:
                raise AccountError("ACCOUNT_NOT_FOUND")
            account = {**accounts[username], **fields, "updated_at": utcnow(),
                       "session_epoch": int(accounts[username].get("session_epoch", 0)) + 1}
            if account["role"] != "admin" or not account.get("enabled", True):
                others = [a for a in accounts.values() if a["id"] != username
                          and a["role"] == "admin" and a.get("enabled", True)]
                if accounts[username]["role"] == "admin" and accounts[username].get("enabled", True) and not others:
                    raise AccountError("LAST_ADMIN")
            accounts[username] = account
            self._write(accounts)
            return account

    def create(self, username: str, role: str) -> str:
        """Create an account with a random temporary password and return it (caller writes it to a 0600 file)."""
        if not USERNAME.fullmatch(username or ""):
            raise AccountError("INVALID_USERNAME")
        if role not in ROLES:
            raise AccountError("INVALID_ROLE")
        temporary = secrets.token_urlsafe(15)
        with self._locked():
            accounts = dict(self._read())
            if username in accounts:
                raise AccountError("ACCOUNT_EXISTS")
            now = utcnow()
            accounts[username] = {"id": username, "role": role, "password_hash": hash_password(temporary),
                                  "enabled": True, "default_password": False, "must_change": True,
                                  "session_epoch": 0, "created_at": now, "updated_at": now}
            self._write(accounts)
        return temporary

    def set_password(self, username: str, password: str) -> dict[str, Any]:
        problem = password_problem(username, password)
        if problem:
            raise AccountError(problem)
        return self._change(username, password_hash=hash_password(password), default_password=False, must_change=False)

    def reset_password(self, username: str) -> str:
        temporary = secrets.token_urlsafe(15)
        self._change(username, password_hash=hash_password(temporary), default_password=False, must_change=True)
        return temporary

    def set_enabled(self, username: str, enabled: bool) -> dict[str, Any]:
        return self._change(username, enabled=bool(enabled))

    def set_role(self, username: str, role: str) -> dict[str, Any]:
        if role not in ROLES:
            raise AccountError("INVALID_ROLE")
        return self._change(username, role=role)


def write_secret_file(path: Path, value: str) -> None:
    """Write a temporary password to a new 0600 file; never overwrite."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(value + "\n")
        output.flush()
        os.fsync(output.fileno())
