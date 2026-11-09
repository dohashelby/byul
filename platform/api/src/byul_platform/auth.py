"""Login sessions, lockout, CSRF and audit for people. Agents keep their Bearer tokens.

Sessions live in process memory (a restart logs everyone out). The cookie is
HttpOnly + SameSite=Strict (+ Secure on HTTPS); every state-changing request also
needs the X-CSRF-Token header returned by login / me.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette._utils import get_route_path

from .accounts import AccountError, AccountStore, utcnow

COOKIE = "byul_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_PATHS = {"/api/auth/login", "/api/auth/me"}
CHANGE_ALLOWED = {"/api/auth/me", "/api/auth/password", "/api/auth/logout"}
FAILURES_BEFORE_LOCK = 5
FAILURE_WINDOW_S = 600
LOCK_S = 300


@dataclass
class Session:
    username: str
    epoch: int
    csrf: str
    created: float
    last_seen: float


@dataclass
class AuthService:
    accounts: AccountStore
    audit_path: Path
    idle_s: int = 7200
    max_s: int = 43200
    require_change: bool = False
    cookie_secure: bool = False
    _sessions: dict[str, Session] = field(default_factory=dict)
    _failures: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    _locked_until: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ------------------------------------------------------------ audit
    def audit(self, event: str, *, username: str | None = None, ok: bool = True, code: str | None = None) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as output:
            output.write(json.dumps({"at": utcnow(), "event": event, "username": username, "ok": ok, "code": code},
                                    ensure_ascii=False) + "\n")

    # ------------------------------------------------------------ lockout
    def _locked(self, keys: list[str], now: float) -> bool:
        return any(self._locked_until.get(k, 0) > now for k in keys)

    def _fail(self, keys: list[str], now: float) -> None:
        for key in keys:
            window = self._failures[key]
            window.append(now)
            while window and window[0] < now - FAILURE_WINDOW_S:
                window.popleft()
            if len(window) >= FAILURES_BEFORE_LOCK:
                self._locked_until[key] = now + LOCK_S
                window.clear()

    # ------------------------------------------------------------ sessions
    def login(self, username: str, password: str, client: str) -> tuple[str, Session] | str:
        now = time.monotonic()
        name = username.strip().lower() if isinstance(username, str) else ""
        keys = ["user:" + name[:64], "client:" + client]
        with self._lock:
            if self._locked(keys, now):
                self.audit("login", username=name[:64] or None, ok=False, code="LOCKED")
                return "LOCKED"
        account = self.accounts.verify(name, password)
        with self._lock:
            if account is None:
                self._fail(keys, now)
                self.audit("login", username=name[:64] or None, ok=False, code="INVALID_CREDENTIALS")
                return "INVALID_CREDENTIALS"
            for key in keys:
                self._failures.pop(key, None)
            sid = secrets.token_urlsafe(32)
            session = Session(name, int(account.get("session_epoch", 0)), secrets.token_urlsafe(24), now, now)
            self._sessions[sid] = session
            self._sessions = {k: s for k, s in self._sessions.items() if not self._expired(s, now)}
        self.audit("login", username=name)
        return sid, session

    def _expired(self, session: Session, now: float) -> bool:
        return now - session.last_seen > self.idle_s or now - session.created > self.max_s

    def resolve(self, sid: str | None) -> tuple[Session, dict[str, Any]] | None:
        if not sid:
            return None
        now = time.monotonic()
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            if self._expired(session, now):
                self._sessions.pop(sid, None)
                return None
        try:
            account = self.accounts.get(session.username)
        except (OSError, ValueError):
            return None
        if (account is None or not account.get("enabled", True)
                or int(account.get("session_epoch", 0)) != session.epoch):
            with self._lock:
                self._sessions.pop(sid, None)
            return None
        session.last_seen = now
        return session, account

    def logout(self, sid: str | None) -> None:
        with self._lock:
            session = self._sessions.pop(sid or "", None)
        if session:
            self.audit("logout", username=session.username)

    def must_change(self, account: dict[str, Any]) -> bool:
        return self.require_change and bool(account.get("must_change"))

    def public_user(self, account: dict[str, Any]) -> dict[str, Any]:
        return {"id": account["id"], "role": account["role"],
                "default_password": bool(account.get("default_password")),
                "must_change": bool(account.get("must_change")),
                "password_change_required": self.must_change(account)}


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def deny(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"code": code, "message": message}})


def gate(service: AuthService, request: Request) -> JSONResponse | None:
    """Decide whether an /api request may proceed. Sets request.state.account for sessions."""
    path = get_route_path(request.scope)
    if path in PUBLIC_PATHS:
        return None
    authorization = request.headers.get("authorization", "")
    if (path == "/api/runtime/operations" or path.startswith("/api/runtime/operations/")) and authorization.startswith("Bearer "):
        return None  # agent / script tokens are checked by the operations routes
    resolved = service.resolve(request.cookies.get(COOKIE))
    if resolved is None:
        return deny(401, "AUTH_REQUIRED", "로그인이 필요합니다")
    session, account = resolved
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not secrets.compare_digest(header, session.csrf):
            return deny(403, "CSRF_FAILED", "요청 확인값이 맞지 않습니다. 새로고침 후 다시 시도하세요")
    if service.must_change(account) and path not in CHANGE_ALLOWED:
        return deny(403, "PASSWORD_CHANGE_REQUIRED", "비밀번호를 먼저 바꿔야 합니다")
    request.state.account = account
    return None


# ------------------------------------------------------------ routes

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class AuthUser(BaseModel):
    id: str
    role: str
    default_password: bool
    must_change: bool
    password_change_required: bool


class AuthMe(BaseModel):
    login_enabled: bool
    user: AuthUser | None = None
    csrf_token: str | None = None


def _service(request: Request) -> AuthService | None:
    return getattr(request.app.state, "auth", None)


def _set_cookie(response: JSONResponse, request: Request, service: AuthService, sid: str) -> None:
    response.set_cookie(COOKIE, sid, httponly=True, samesite="strict", path="/",
                        secure=service.cookie_secure or request.url.scheme == "https")


@router.get("/me", response_model=AuthMe)
def me(request: Request):
    service = _service(request)
    if service is None:
        return AuthMe(login_enabled=False)
    resolved = service.resolve(request.cookies.get(COOKIE))
    if resolved is None:
        return AuthMe(login_enabled=True)
    session, account = resolved
    return AuthMe(login_enabled=True, user=AuthUser(**service.public_user(account)), csrf_token=session.csrf)


@router.post("/login", response_model=AuthMe)
def login(body: LoginBody, request: Request):
    service = _service(request)
    if service is None:
        return deny(404, "LOGIN_DISABLED", "로그인이 꺼져 있습니다")
    result = service.login(body.username, body.password, client_key(request))
    if result == "LOCKED":
        return deny(429, "LOGIN_LOCKED", "로그인 실패가 많아 잠시 막혔습니다. 5분 뒤 다시 시도하세요")
    if isinstance(result, str):
        return deny(401, "INVALID_CREDENTIALS", "아이디 또는 비밀번호가 맞지 않습니다")
    sid, session = result
    account = service.accounts.get(session.username) or {}
    response = JSONResponse(AuthMe(login_enabled=True, user=AuthUser(**service.public_user(account)),
                                   csrf_token=session.csrf).model_dump())
    _set_cookie(response, request, service, sid)
    return response


@router.post("/logout")
def logout(request: Request):
    service = _service(request)
    response = JSONResponse({"ok": True})
    if service is not None:
        service.logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return response


@router.post("/password", response_model=AuthMe)
def change_password(body: PasswordBody, request: Request):
    service = _service(request)
    account = getattr(request.state, "account", None)
    if service is None or account is None:
        return deny(401, "AUTH_REQUIRED", "로그인이 필요합니다")
    if service.accounts.verify(account["id"], body.current_password) is None:
        service.audit("password_change", username=account["id"], ok=False, code="INVALID_CREDENTIALS")
        return deny(400, "INVALID_CURRENT_PASSWORD", "현재 비밀번호가 맞지 않습니다")
    try:
        service.accounts.set_password(account["id"], body.new_password)
    except AccountError as error:
        service.audit("password_change", username=account["id"], ok=False, code=error.code)
        return deny(400, error.code, "새 비밀번호 규칙: 10자 이상, 아이디·admin과 다르게, 서로 다른 문자 4개 이상")
    service.audit("password_change", username=account["id"])
    service.logout(request.cookies.get(COOKIE))  # epoch changed; issue a fresh session
    result = service.login(account["id"], body.new_password, client_key(request))
    if isinstance(result, str):
        return deny(401, "AUTH_REQUIRED", "다시 로그인하세요")
    sid, session = result
    fresh = service.accounts.get(account["id"]) or account
    response = JSONResponse(AuthMe(login_enabled=True, user=AuthUser(**service.public_user(fresh)),
                                   csrf_token=session.csrf).model_dump())
    _set_cookie(response, request, service, sid)
    return response
