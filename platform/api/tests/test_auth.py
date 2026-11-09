from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from byul_platform import accounts_cli
from byul_platform.accounts import AccountStore
from byul_platform.config import Settings
from byul_platform.main import create_app

STRONG = "correct-horse-42"


def make(tmp_path: Path, **extra) -> TestClient:
    settings = Settings(platform_data_dir=tmp_path / "data", web_dist=tmp_path / "no-dist", login_enabled=True, **extra)
    return TestClient(create_app(settings))


def accounts_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "accounts" / "accounts.json"


def login(client: TestClient, username="admin", password="admin") -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def audit(tmp_path: Path) -> list[dict]:
    path = accounts_path(tmp_path).with_name("auth-audit.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_bootstrap_admin_and_private_files(tmp_path):
    with make(tmp_path) as client:
        path = accounts_path(tmp_path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "admin" not in json.dumps([a["password_hash"] for a in json.loads(path.read_text())["accounts"]])
        assert client.get("/api/auth/me").json() == {"login_enabled": True, "user": None, "csrf_token": None}
        body = login(client)
        assert body["user"] == {"id": "admin", "role": "admin", "default_password": True, "must_change": True,
                                "password_change_required": False}
        assert body["csrf_token"]
        assert stat.S_IMODE(path.with_name("auth-audit.jsonl").stat().st_mode) == 0o600


def test_all_apis_need_login_and_cookie_is_strict(tmp_path):
    with make(tmp_path) as client:
        for path in ("/api/meta", "/api/runtime/status", "/api/evidence", "/api/runtime/operations"):
            response = client.get(path)
            assert response.status_code == 401, path
            assert response.json()["detail"]["code"] == "AUTH_REQUIRED"
        response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
        assert client.get("/api/meta").status_code == 200
        client.post("/api/auth/logout", headers={"X-CSRF-Token": response.json()["csrf_token"]})
        assert client.get("/api/meta").status_code == 401


def test_web_pages_are_not_blocked(tmp_path):
    with make(tmp_path) as client:
        assert client.get("/runtime/overview").status_code == 503  # web not built, but not 401


def test_wrong_password_and_unknown_user_look_the_same_and_lock(tmp_path):
    with make(tmp_path) as client:
        a = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        b = client.post("/api/auth/login", json={"username": "ghost", "password": "nope"})
        assert a.status_code == b.status_code == 401
        assert a.json() == b.json()
        for _ in range(3):
            client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        locked = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert locked.status_code == 429
        assert locked.json()["detail"]["code"] == "LOGIN_LOCKED"
        events = audit(tmp_path)
        assert {"INVALID_CREDENTIALS", "LOCKED"} <= {e["code"] for e in events}
        assert "nope" not in json.dumps(events)


def test_csrf_required_for_changes(tmp_path):
    with make(tmp_path) as client:
        csrf = login(client)["csrf_token"]
        body = {"current_password": "admin", "new_password": STRONG}
        assert client.post("/api/auth/password", json=body).json()["detail"]["code"] == "CSRF_FAILED"
        assert client.post("/api/auth/password", json=body, headers={"X-CSRF-Token": "x"}).status_code == 403
        changed = client.post("/api/auth/password", json=body, headers={"X-CSRF-Token": csrf})
        assert changed.status_code == 200
        assert changed.json()["user"]["default_password"] is False
        assert client.get("/api/meta").status_code == 200  # fresh session cookie issued


def test_password_rules_and_old_sessions_end(tmp_path):
    with make(tmp_path) as first, make(tmp_path) as second:
        # Two apps share one account file (like two workers); sessions are per process.
        csrf = login(first)["csrf_token"]
        login(second)
        for weak, code in (("short", "PASSWORD_TOO_SHORT"), ("admin", "PASSWORD_TOO_SHORT"), ("aaaaaaaaaaaa", "PASSWORD_TOO_WEAK")):
            response = first.post("/api/auth/password", json={"current_password": "admin", "new_password": weak},
                                  headers={"X-CSRF-Token": csrf})
            assert response.json()["detail"]["code"] == code
        wrong = first.post("/api/auth/password", json={"current_password": "bad", "new_password": STRONG},
                           headers={"X-CSRF-Token": csrf})
        assert wrong.json()["detail"]["code"] == "INVALID_CURRENT_PASSWORD"
        first.post("/api/auth/password", json={"current_password": "admin", "new_password": STRONG},
                   headers={"X-CSRF-Token": csrf})
        assert second.get("/api/meta").status_code == 401  # epoch changed
        assert second.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 401
        login(second, password=STRONG)


def test_forced_change_switch(tmp_path):
    with make(tmp_path, require_password_change=True) as client:
        body = login(client)
        assert body["user"]["password_change_required"] is True
        response = client.get("/api/meta")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"
        assert client.get("/api/auth/me").status_code == 200
        client.post("/api/auth/password", json={"current_password": "admin", "new_password": STRONG},
                    headers={"X-CSRF-Token": body["csrf_token"]})
        assert client.get("/api/meta").status_code == 200


def test_session_idle_expiry(tmp_path):
    with make(tmp_path, session_idle_seconds=0) as client:
        login(client)
        assert client.get("/api/meta").status_code == 401


def test_cli_accounts_and_disable(tmp_path, capsys):
    path = accounts_path(tmp_path)
    with make(tmp_path) as client:
        pw = tmp_path / "op.pw"
        assert accounts_cli.main(["--accounts-file", str(path), "create", "--id", "op1", "--role", "runtime_operator",
                                  "--password-file", str(pw)]) == 0
        secret = pw.read_text().strip()
        assert stat.S_IMODE(pw.stat().st_mode) == 0o600
        assert secret not in capsys.readouterr().out
        assert accounts_cli.main(["--accounts-file", str(path), "create", "--id", "op2", "--role", "viewer",
                                  "--password-file", str(pw)]) == 1  # never overwrite
        body = login(client, "op1", secret)
        assert body["user"]["role"] == "runtime_operator" and body["user"]["must_change"] is True
        assert accounts_cli.main(["--accounts-file", str(path), "disable", "--id", "op1"]) == 0
        assert client.get("/api/meta").status_code == 401
        assert client.post("/api/auth/login", json={"username": "op1", "password": secret}).status_code == 401
        assert accounts_cli.main(["--accounts-file", str(path), "disable", "--id", "admin"]) == 1  # last admin
        capsys.readouterr()
        accounts_cli.main(["--accounts-file", str(path), "list"])
        listed = json.loads(capsys.readouterr().out)
        assert {a["id"] for a in listed} == {"admin", "op1"}
        assert "password_hash" not in json.dumps(listed)
        assert any(e["event"] == "account_create" for e in audit(tmp_path))


def test_accounts_file_with_loose_permissions_is_refused(tmp_path):
    path = tmp_path / "accounts.json"
    store = AccountStore(path)
    store.ensure_bootstrap()
    path.chmod(0o644)
    with pytest.raises(ValueError):
        store.get("admin")


def test_operations_use_session_role_and_agents_keep_tokens(tmp_path):
    token = "agent-token-value-123456"
    agent = {"id": "agent-1", "role": "agent", "server": "lab-1", "sha256": hashlib.sha256(token.encode()).hexdigest()}
    with make(tmp_path, operation_tokens=(agent,)) as client:
        path = accounts_path(tmp_path)
        pw = tmp_path / "v.pw"
        accounts_cli.main(["--accounts-file", str(path), "create", "--id", "viewer1", "--role", "viewer", "--password-file", str(pw)])
        # Agent token works without a login session.
        response = client.get("/api/runtime/operations", params={"server": "lab-1"}, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        # A viewer session can list but not request.
        csrf = login(client, "viewer1", pw.read_text().strip())["csrf_token"]
        listed = client.get("/api/runtime/operations")
        assert listed.status_code == 200 and listed.json()["actor"]["id"] == "viewer1"
        request = client.post("/api/runtime/operations", headers={"X-CSRF-Token": csrf}, json={
            "server": "lab-1", "kind": "bundle.switch", "expected": {"bundle_id": "b1"}, "bundle_id": "b2"})
        assert request.status_code == 403
        # Admin may view operations but not act as operator/approver.
        client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
        csrf = login(client)["csrf_token"]
        assert client.get("/api/runtime/operations").json()["actor"]["role"] == "admin"
        assert client.post("/api/runtime/operations", headers={"X-CSRF-Token": csrf}, json={
            "server": "lab-1", "kind": "bundle.switch", "expected": {"bundle_id": "b1"}, "bundle_id": "b2"}).status_code == 403


def test_login_disabled_keeps_old_behaviour(tmp_path):
    with TestClient(create_app(Settings(web_dist=tmp_path / "no-dist"))) as client:
        assert client.get("/api/auth/me").json()["login_enabled"] is False
        assert client.get("/api/meta").status_code == 200
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 404
