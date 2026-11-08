"""Authorization, immutable audit, lease semantics and the internal agent protocol."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

import pytest

from byul_platform.operations import OperationStore, load_tokens

BASE = "/api/runtime/operations"


def auth(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def ops(store_client, monkeypatch):
    tokens = [{"id": name, "role": role, "sha256": hashlib.sha256(name.encode()).hexdigest(), **extra}
              for name, role, extra in [("operator-01", "runtime_operator", {}), ("approver-01", "approver", {}),
                  ("viewer-01", "viewer", {}), ("agent-01", "agent", {"server": "server-01"}),
                  ("agent-02", "agent", {"server": "server-02"})]]
    state = store_client.app.state.byul
    monkeypatch.setattr(state, "settings", replace(state.settings, operation_tokens=tuple(tokens), bundle_runtime_python=Path(sys.executable)))
    return store_client


def request(client, **extra):
    return client.post(BASE, headers=auth("operator-01"), json={"kind": "bundle.switch", "server": "server-01",
                "expected": {"bundle_id": "old"}, "bundle_id": "previous", **extra})


def test_tokens_validation_and_no_secret_repr(ops):
    assert "sha256" not in repr(ops.app.state.byul.settings)
    with pytest.raises(ValueError):
        load_tokens('[{"id":"duplicate","role":"viewer","sha256":"bad"}]')


@pytest.mark.parametrize("reason", [None, "BUNDLE_ROLES_MISSING", "BUNDLE_INVALID", "CURRENT_LINK_INVALID", "STATUS_UNREACHABLE", "STATUS_MISMATCH"])
def test_result_optional_safe_reason_code(ops, reason):
    item = request(ops).json()
    path = f"{BASE}/{item['id']}"
    claimed = ops.post(path + "/claim", json={}, headers=auth("agent-01")).json()
    body = {"lease_id": claimed["lease_id"], "state": "failed", "code": "VALIDATION_FAILED",
            "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": datetime.now(timezone.utc).isoformat()}
    assert ops.post(path + "/result", headers=auth("agent-01"), json={**body, "reason_code": "/secret/path"}).status_code == 422
    if reason is not None:
        body["reason_code"] = reason
    result = ops.post(path + "/result", headers=auth("agent-01"), json=body)
    assert result.status_code == 200, result.text
    assert result.json()["result"]["reason_code"] == reason


def test_listing_identity_agent_inventory_and_scope(ops):
    payload = {"server": "server-01", "current": {"bundle_id": "old", "approval": "verified"},
               "bundles": [{"bundle_id": "previous", "sha256": "a" * 64}]}
    assert ops.post(BASE + "/agent-state", headers=auth("agent-01"), json=payload).status_code == 200
    listing = ops.get(BASE, headers=auth("approver-01")).json()
    assert listing["actor"] == {"id": "approver-01", "role": "approver"}
    assert listing["agents"][0]["bundles"] == payload["bundles"]
    assert listing["agents"][1]["current"] is None
    assert "sha256" not in listing["actor"]
    scoped = ops.get(BASE + "?server=server-01", headers=auth("agent-01")).json()
    assert [a["server"] for a in scoped["agents"]] == ["server-01"]


@pytest.mark.parametrize("seconds,self_approval", [(86400, False), (60, True)])
def test_listing_exposes_platform_settings_for_lab_warning(ops, seconds, self_approval):
    state = ops.app.state.byul
    state.settings = replace(state.settings, operation_monitor_seconds=seconds,
                             operation_lab_self_approval=self_approval)
    response = ops.get(BASE, headers=auth("viewer-01"))
    assert response.status_code == 200
    assert response.json()["monitor_seconds"] == seconds
    assert response.json()["lab_self_approval"] is self_approval


def test_auth_denial_and_body_role_forgery(ops):
    payload = {"kind": "bundle.switch", "server": "server-01", "expected": {"bundle_id": "old"}, "bundle_id": "new", "requested_by_role": "runtime_operator"}
    assert ops.post(BASE, json=payload).status_code == 401
    assert ops.post(BASE, json=payload, headers=auth("viewer-01")).status_code == 403
    assert ops.post(BASE, json=payload, headers=auth("approver-01")).status_code == 403
    assert ops.post(BASE, json=payload, headers=auth("agent-01")).status_code == 403
    item = request(ops, requested_by_role="approver").json()
    assert item["requested_role"] == "runtime_operator"
    assert "requested_by_role" not in item["payload"]
    # Old query behavior remains available with no operation credentials.
    assert ops.get("/api/runtime/status").status_code == 200
    assert ops.get(BASE).status_code == 401


def test_agent_cannot_access_another_server_or_user_mutations(ops):
    item = request(ops).json()
    assert ops.get(BASE + "?server=server-01", headers=auth("agent-02")).status_code == 403
    assert ops.get(BASE, headers=auth("agent-01")).status_code == 422
    for suffix in ("claim", "approve", "result"):
        body = {} if suffix != "result" else {"lease_id": "a" * 32, "state": "failed", "code": "STATE_CHANGED", "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": datetime.now(timezone.utc).isoformat()}
        assert ops.post(f"{BASE}/{item['id']}/{suffix}", json=body, headers=auth("agent-02")).status_code == 403
    assert ops.get(f"{BASE}/{item['id']}/bundle", headers=auth("agent-02")).status_code == 403
    assert request(ops, server="unregistered").status_code == 422


def test_lease_expiry_unknown_no_reclaim_and_append_only(ops):
    state = ops.app.state.byul
    state.settings = replace(state.settings, operation_lease_seconds=-1)
    item = request(ops).json()
    path = f"{BASE}/{item['id']}"
    claim = ops.post(path + "/claim", json={}, headers=auth("agent-01"))
    assert claim.status_code == 200
    listing = ops.get(BASE, headers=auth("viewer-01")).json()
    assert listing["items"][0]["state"] == "unknown"
    assert listing["items"][0]["error_code"] == "LEASE_EXPIRED"
    assert ops.post(path + "/claim", json={}, headers=auth("agent-01")).status_code == 409
    assert "lease_id" not in listing["items"][0]
    events = [json.loads(p.read_text()) for p in sorted((state.policies.dir.parent / "runtime_operations").glob("event-*.json"))]
    assert [e["operation"]["state"] for e in events if "operation" in e] == ["approved", "claimed", "unknown"]


@pytest.mark.parametrize("outcome,code", [("failed", "STATE_CHANGED"), ("rolled_back", "ROLLED_BACK"), ("unknown", "STATUS_UNKNOWN"), ("succeeded", "OK")])
def test_result_states_and_claim_owner(ops, outcome, code):
    item = request(ops).json()
    path = f"{BASE}/{item['id']}"
    claim = ops.post(path + "/claim", json={}, headers=auth("agent-01")).json()
    assert ops.post(path + "/claim", json={}, headers=auth("agent-01")).status_code == 409
    body = {"lease_id": claim["lease_id"], "state": "running", "code": "OK", "started_at": datetime.now(timezone.utc).isoformat()}
    assert ops.post(path + "/result", json=body, headers=auth("agent-01")).status_code == 200
    body.update(state=outcome, code=code, finished_at=datetime.now(timezone.utc).isoformat())
    assert ops.post(path + "/result", json={**body, "lease_id": "0" * 32}, headers=auth("agent-01")).status_code == 409
    completed = ops.post(path + "/result", json=body, headers=auth("agent-01"))
    assert completed.status_code == 200, completed.text
    assert completed.json()["state"] == outcome
    assert ops.post(path + "/result", json=body, headers=auth("agent-01")).status_code == 409


@pytest.mark.parametrize("outcome,code,reason", [
    ("checked", "DRY_RUN", None),
    ("failed", "VALIDATION_FAILED", "BUNDLE_ROLES_MISSING"),
])
def test_dry_run_terminal_result_is_stored_and_cannot_run_again(ops, outcome, code, reason):
    item = request(ops).json()
    path = f"{BASE}/{item['id']}"
    claim = ops.post(path + "/claim", json={}, headers=auth("agent-01")).json()
    body = {"lease_id": claim["lease_id"], "state": outcome, "code": code, "reason_code": reason,
            "dry_run": True, "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "steps": [{"stage": "validation", "state": "passed" if outcome == "checked" else "failed"}]}
    response = ops.post(path + "/result", json=body, headers=auth("agent-01"))
    assert response.status_code == 200, response.text
    stored = ops.get(BASE, headers=auth("viewer-01")).json()["items"][0]
    assert stored["state"] == outcome
    assert stored["result"]["code"] == code
    assert stored["result"]["reason_code"] == reason
    assert stored["result"]["dry_run"] is True
    assert ops.post(path + "/claim", json={}, headers=auth("agent-01")).status_code == 409
    assert ops.post(path + "/approve", json={}, headers=auth("approver-01")).status_code == 409
    assert ops.post(path + "/result", json={**body, "state": "running", "code": "OK", "dry_run": False}, headers=auth("agent-01")).status_code == 409
    assert request(ops).status_code == 201  # Execution requires a new request.


@pytest.mark.parametrize("outcome,code,dry_run", [
    ("checked", "DRY_RUN", False), ("checked", "OK", True),
    ("checked", "VALIDATION_FAILED", True), ("failed", "DRY_RUN", True),
    ("failed", "DRY_RUN", False), ("succeeded", "OK", True),
    ("running", "OK", True), ("failed", "OK", True),
])
def test_dry_run_result_rejects_inconsistent_state_and_code(ops, outcome, code, dry_run):
    item = request(ops).json()
    path = f"{BASE}/{item['id']}"
    claim = ops.post(path + "/claim", json={}, headers=auth("agent-01")).json()
    body = {"lease_id": claim["lease_id"], "state": outcome, "code": code, "dry_run": dry_run,
            "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": datetime.now(timezone.utc).isoformat()}
    response = ops.post(path + "/result", json=body, headers=auth("agent-01"))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "RESULT_INVALID"
    assert ops.get(BASE, headers=auth("viewer-01")).json()["items"][0]["state"] == "claimed"


def ready(ops, checks=True):
    expected = {"bundle_id": "old", "approval": "verified", "policy_id": "7", "mode": "monitor", "spec_sha256": "a" * 64}
    row = {"name": "runtime-tg-secret-01", "method": "override_lsm", "expected": expected,
           "checks": dict.fromkeys(("policy", "evidence", "capability", "monitor_period", "collection", "allowed_mode"), checks)}
    assert ops.post(BASE + "/agent-state", headers=auth("agent-01"), json={"server": "server-01", "policies": [row]}).status_code == 200
    return expected


def enforce(ops, expected):
    return request(ops, kind="tetragon.mode", policy_name="runtime-tg-secret-01", mode="enforce", expected=expected, confirmation="ENGINE_RESTART")


def test_enforce_without_evidence_denied_and_monitor_immediate(ops):
    expected = {"bundle_id": "old", "policy_id": "7", "mode": "monitor", "spec_sha256": "a" * 64}
    assert enforce(ops, expected).status_code == 409
    expected = ready(ops, checks=False)
    assert enforce(ops, expected).status_code == 409
    monitor = request(ops, kind="tetragon.mode", policy_name="runtime-tg-secret-01", mode="monitor", expected=expected)
    assert monitor.status_code == 201
    assert monitor.json()["state"] == "approved"
    assert monitor.json()["approval_required"] is False


@pytest.mark.parametrize("classification,proof_change,capability,basis,reason", [
    ("available", None, True, "static", None),
    ("available", "missing", True, "static", None),
    ("unknown", None, True, "live_trial", None),
    ("unknown", "missing", False, None, "evidence_required"),
    ("unavailable", None, False, None, "static_unavailable"),
    ("unknown", "kernel", False, None, "evidence_required"),
    ("unknown", "policy_spec_sha256", False, None, "evidence_required"),
])
def test_runtime_capability_projection_controls_enforce(ops, classification, proof_change, capability, basis, reason):
    from types import SimpleNamespace
    from serving_runtime.operations.safety import evaluate
    at = datetime.now(timezone.utc)
    policy = {"name": "runtime-tg-secret-01", "namespace": "serving", "node": "node-01", "id": "7",
              "mode": "monitor", "state": "enabled", "method": "sigkill_fd_install", "spec_sha256": "a" * 64}
    proof = {"data_origin": "real", "variant": "combined", "node": "node-01", "kernel": "test-kernel",
             "method": policy["method"], "policy_spec_sha256": "a" * 64,
             "verification": {"status": "passed"}, "source_report_sha256": "c" * 64}
    if proof_change and proof_change != "missing":
        proof[proof_change] = "different"
    snapshot = {"fresh": True, "state": "ok", "generated_at": at.isoformat(), "policies": [policy],
                "capabilities": [{"node": "node-01", "kernel": "test-kernel", classification: [policy["method"]]}],
                "verifications": [] if proof_change == "missing" else [proof],
                "monitoring": {"state": "healthy", **dict.fromkeys(
                    ("policies_verified", "writer_verified", "identities_verified", "events_verified"), True)}}
    window = {"policy": policy["name"], "node": "node-01", "method": policy["method"], "kernel": "test-kernel",
              "namespace": "serving", "policy_id": policy["id"], "mode": "monitor",
              "spec_sha256": "a" * 64, "since": (at - timedelta(days=1)).isoformat(), "until": at.isoformat(),
              "continuous": True, "event_count": 0, "false_positive_count": 0, "events_sha256": "b" * 64}
    row = evaluate(snapshot, policy["name"], SimpleNamespace(namespace="serving", allowed_modes=lambda _: ("monitor", "enforce")),
                   {"windows": [window]}, 86400, now=at)
    row["expected"].update(bundle_id="old", approval="verified")
    assert row["checks"]["capability"] is capability
    assert row["capability_basis"] == basis
    assert row["capability_reason"] == reason
    response = ops.post(BASE + "/agent-state", headers=auth("agent-01"), json={"server": "server-01", "policies": [row]})
    assert response.status_code == 200
    projected = ops.get(BASE, headers=auth("viewer-01")).json()["agents"][0]["policies"][0]
    assert projected["capability_basis"] == basis
    assert projected["capability_reason"] == reason
    assert enforce(ops, row["expected"]).status_code == (201 if all(row["checks"].values()) else 409)


def test_monitor_window_agent_projection_and_validation(ops):
    expected = ready(ops)
    row = ops.get(BASE, headers=auth("viewer-01")).json()["agents"][0]["policies"][0]
    at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    window = {"node": "node-01", "since": at, "until": at, "continuous": True,
              "event_count": 2, "reviewed_event_count": 1, "false_positive_count": 0}
    row.update(monitor_windows=[window], checks={**row["checks"], "monitor_period": False})
    payload = {"server": "server-01", "policies": [row]}
    assert ops.post(BASE + "/agent-state", headers=auth("agent-01"), json=payload).status_code == 200
    result = ops.get(BASE, headers=auth("viewer-01")).json()["agents"][0]["policies"][0]
    assert result["monitor_windows"] == [window]
    assert enforce(ops, expected).status_code == 409
    window["event_count"] = -1
    assert ops.post(BASE + "/agent-state", headers=auth("agent-01"), json=payload).status_code == 422


def test_runtime_readiness_mixed_monitor_only_snapshot_validates_platform_models():
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    from serving_runtime.operations.agent import Agent, Config
    from byul_platform.routes.operations import Heartbeat, PolicyReadiness

    agent = Agent(Mock(), Config("server-01", "/unused", "aws-lab", "serving-lab"))
    intent = SimpleNamespace(namespace="serving-lab", allowed_modes=lambda _: ("monitor", "enforce"))
    current = {"bundle_id": "old", "approval": "verified"}
    secret = {"name": "runtime-tg-secret-01", "namespace": "serving-lab", "node": "node-01",
              "id": "7", "state": "enabled", "mode": "monitor", "method": "sigkill_fd_install",
              "spec_sha256": "a" * 64}
    snapshot = {"policies": [secret] + [
        {**secret, "name": f"runtime-tg-{rule}-01", "id": str(index),
         "mode": "monitor_only", "method": "monitor"}
        for index, rule in enumerate(("cache", "asset"), start=8)]}
    with patch.object(agent, "snapshot", return_value=snapshot):
        rows = agent.readiness(current, intent)

    assert [row["name"] for row in rows] == [secret["name"]]
    assert PolicyReadiness.model_validate(rows[0]).expected.mode == "monitor"
    heartbeat = Heartbeat.model_validate({"server": "server-01", "current": current, "policies": rows})
    assert [row.name for row in heartbeat.policies] == [secret["name"]]


def test_two_party_approval_and_lab_self_approval(ops):
    expected = ready(ops)
    item = enforce(ops, expected).json()
    path = f"{BASE}/{item['id']}"
    assert item["state"] == "requested"
    assert ops.post(path + "/claim", json={}, headers=auth("agent-01")).status_code == 409
    assert ops.post(path + "/approve", json={}, headers=auth("operator-01")).status_code == 403
    assert ops.post(path + "/approve", json={}, headers=auth("approver-01")).json()["state"] == "approved"
    state = ops.app.state.byul
    state.settings = replace(state.settings, operation_lab_self_approval=True)
    second = enforce(ops, expected).json()
    approved = ops.post(f"{BASE}/{second['id']}/approve", json={}, headers=auth("operator-01")).json()
    assert approved["approvals"][0]["self_approval"] is True
    assert approved["lab_self_approval"] is True


def test_heartbeat_last_poll_and_closed_result(ops):
    ready(ops)
    data = ops.get(BASE + "?server=server-01", headers=auth("agent-01")).json()
    assert data["agents"][0]["last_poll_at"]
    response = ops.post(BASE + "/agent-state", headers=auth("agent-01"), json={"server": "server-02"})
    assert response.status_code == 403
    item = request(ops).json()
    assert ops.post(f"{BASE}/{item['id']}/result", headers=auth("agent-01"), json={"stdout": "excluded"}).status_code == 422


def test_requested_expiry(ops):
    expected = ready(ops)
    item = enforce(ops, expected).json()
    store = OperationStore(ops.app.state.byul.policies.dir.parent)
    with store.transaction() as (records, _):
        expired = records[item["id"]]
        expired["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        store.append({"operation": expired})
    assert ops.post(f"{BASE}/{item['id']}/approve", headers=auth("approver-01"), json={}).status_code == 409
    assert ops.get(BASE, headers=auth("viewer-01")).json()["items"][0]["state"] == "expired"


@pytest.mark.parametrize("http_gateway", [False, True], ids=["gateway-core", "gateway-http"])
def test_platform_agent_apply_temporary_gateway_verified(ops, tmp_path, monkeypatch, http_gateway):
    """Real export/verify/apply and Gateway approval; only Kubernetes rollout is local.

    The core variant runs without network privileges. The HTTP variant starts
    a real temporary Gateway and is skipped only if local sockets are denied.
    """
    from contextlib import nullcontext
    import io
    import socket
    import tarfile
    from urllib.parse import urlsplit

    from test_bundle_export import _inputs, _save, RUNTIME
    from serving_runtime import bundle_tools
    from serving_runtime.enforcement.intent import load_intent
    from serving_runtime.mvp.app import GatewayApp
    from serving_runtime.mvp.config import MVPConfig
    from serving_runtime.mvp.policy import load_policy
    from serving_runtime.mvp.policy_bundle import verify_policy_bundle
    from serving_runtime.operations.agent import Agent, Config

    if http_gateway:
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
        except OSError as exc:
            pytest.skip(f"local socket permission: {type(exc).__name__}")
    previous = __import__("os").environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", str(RUNTIME / "src") + (":" + previous if previous else ""))
    env, policy = _inputs()
    _save(ops, env, policy)
    response = ops.post(f"/api/policies/{policy['policy_id']}/revisions/1/bundle", json={
        "environment_id": env["environment_id"], "environment_revision": 1,
        "requested_by_role": "runtime_operator", "bundle_id": "old"})
    assert response.status_code == 200, response.text
    root = tmp_path / "bundles"
    root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        for item in archive.getmembers():
            path = root / item.name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(archive.extractfile(item).read())
    (root / "current").symlink_to("old")
    gateway = None
    counter = 0
    status_url = "http://127.0.0.1:18080"
    def restart():
        nonlocal gateway, counter, status_url
        if gateway:
            gateway.stop()
        bundle = verify_policy_bundle(root / "current")
        options, info = bundle.mvp_options(())
        counter += 1
        gateway = GatewayApp(MVPConfig(**options, gateway_port=0, data_dir=str(tmp_path / f"gateway-{counter}"), policy_bundle=info), load_policy(options["policy_path"]), ("127.0.0.1", 1))
        if http_gateway:
            gateway.start()
            status_url = f"http://127.0.0.1:{gateway.address[1]}"
        else:
            # Exercise the real Gateway policy and bundle projections without binding.
            status_url = "http://127.0.0.1:18080"
    restart()
    original_status = bundle_tools._status
    def status(url, token):
        if http_gateway:
            return original_status(status_url, token)
        return {"policy_bundle": gateway._policy_bundle, "policy": gateway.policy_status()}
    monkeypatch.setattr(bundle_tools, "_status", status)
    monkeypatch.setattr(bundle_tools, "_port_forward", lambda *args: nullcontext(status_url))
    monkeypatch.setattr(bundle_tools.os, "chown", lambda *args: None)
    def rollout(command):
        if "restart" in command:
            restart()
    monkeypatch.setattr(bundle_tools, "_run", rollout)

    class Adapter:
        def request(self, path="", body=None, *, binary=False):
            response = (ops.get(BASE + path, headers=auth("agent-01")) if body is None
                        else ops.post(BASE + path, json=body, headers=auth("agent-01")))
            assert response.status_code == 200, response.text
            return response.content if binary else response.json()

    try:
        item = request(ops, kind="bundle.apply", bundle_id="new", policy_id=policy["policy_id"], policy_revision=1,
                       environment_id=env["environment_id"], environment_revision=1).json()
        assert item["state"] == "requested", item
        assert ops.get(f"{BASE}/{item['id']}/bundle", headers=auth("viewer-01")).status_code == 403
        assert ops.post(f"{BASE}/{item['id']}/approve", headers=auth("approver-01"), json={}).status_code == 200
        target = json.loads((root / "old/target.json").read_text())
        agent = Agent(Adapter(), Config("server-01", str(root), target["kubernetes"]["context"], target["kubernetes"]["namespace"], status_url=status_url, execute=True))
        diagnostics = []
        def instrument(function):
            def call(*args, **kwargs):
                try:
                    return function(*args, **kwargs)
                except Exception as error:
                    diagnostics.append((function.__name__, type(error).__name__, str(error)))
                    raise
            return call
        for method in ("current", "unpack", "validate"):
            monkeypatch.setattr(agent, method, instrument(getattr(agent, method)))
        monkeypatch.setattr(bundle_tools, "apply_bundle", instrument(bundle_tools.apply_bundle))
        result = agent.once()
        assert result["state"] == "succeeded", (result, diagnostics)
        assert result["after"]["bundle_id"] == "new"
        assert __import__("os").readlink(root / "current") == "new"
        checked = status(status_url, None)
        assert checked["policy"]["gateway_active"] is True
        assert checked["policy"]["approval"]["state"] == "verified"
        assert checked["policy_bundle"]["bundle_id"] == "new"
        stored = ops.get(BASE, headers=auth("viewer-01")).json()["items"][0]
        assert stored["state"] == "succeeded"
        assert stored["result"]["evidence_sha256"]
        # Polling again cannot re-execute the terminal operation.
        assert agent.once() is None
        assert counter == 2
    finally:
        if gateway:
            gateway.stop()
