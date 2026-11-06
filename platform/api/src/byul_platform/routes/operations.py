"""Authenticated internal operation protocol; existing read/export APIs are untouched."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ..operations import ID, SHA, OperationStore, authenticate, deny, utcnow
from ..state import AppState, get_state
from .bundle_export import BundleExportRequest, export_bundle

router = APIRouter(prefix="/api/runtime/operations", tags=["runtime operations (internal)"])
Identifier = Annotated[str, Field(pattern=ID)]
Digest = Annotated[str, Field(pattern=SHA)]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expected(Closed):
    bundle_id: Identifier
    approval: Literal["verified"] = "verified"
    policy_id: Identifier | None = None
    mode: Literal["monitor", "enforce"] | None = None
    spec_sha256: Digest | None = None


class OperationRequest(Closed):
    server: Identifier
    kind: Literal["bundle.apply", "bundle.switch", "tetragon.mode"]
    expected: Expected
    policy_id: Identifier | None = None
    policy_revision: int | None = Field(default=None, ge=1)
    environment_id: Identifier | None = None
    environment_revision: int | None = Field(default=None, ge=1)
    bundle_id: Identifier | None = None
    server_values_override: dict | None = None
    policy_name: Identifier | None = None
    mode: Literal["monitor", "enforce"] | None = None
    confirmation: Literal["ENGINE_RESTART"] | None = None
    # Accepted for compatibility but never used as an authorization input.
    requested_by_role: str | None = Field(default=None, max_length=64)


class Claim(Closed):
    pass


class StateSummary(Closed):
    bundle_id: Identifier
    approval: Literal["verified", "unknown"]
    policy_id: Identifier | None = None
    mode: Literal["monitor", "enforce", "monitor_only", "unknown"] | None = None
    spec_sha256: Digest | None = None


class Step(Closed):
    stage: Literal["validation", "link", "restart", "status", "set-mode", "rollback"]
    state: Literal["passed", "failed", "unknown", "skipped"]


ReasonCode = Literal["BUNDLE_ROLES_MISSING", "BUNDLE_INVALID", "CURRENT_LINK_INVALID",
                     "STATUS_UNREACHABLE", "STATUS_MISMATCH"]


class Result(Closed):
    lease_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    state: Literal["running", "checked", "succeeded", "failed", "rolled_back", "unknown"]
    code: Literal["OK", "DRY_RUN", "STATE_CHANGED", "VALIDATION_FAILED", "HASH_MISMATCH",
                  "APPROVAL_REQUIRED", "TARGET_MISMATCH", "SAFETY_REJECTED", "EXECUTION_FAILED",
                  "ROLLED_BACK", "STATUS_UNKNOWN", "LEASE_EXPIRED"]
    reason_code: ReasonCode | None = None
    started_at: datetime
    finished_at: datetime | None = None
    before: StateSummary | None = None
    after: StateSummary | None = None
    steps: list[Step] = Field(default_factory=list, max_length=12)
    evidence_sha256: list[Digest] = Field(default_factory=list, max_length=64)
    dry_run: bool = False


class PolicyReadiness(Closed):
    name: Identifier
    method: Identifier | None = None
    expected: Expected
    checks: dict[Literal["policy", "evidence", "capability", "monitor_period", "collection", "allowed_mode"], bool]
    capability_basis: Literal["static", "live_trial"] | None = None
    capability_reason: Literal["static_unavailable", "evidence_required", "capability_missing"] | None = None
    evidence_sha256: list[Digest] = Field(default_factory=list, max_length=64)


class AvailableBundle(Closed):
    bundle_id: Identifier
    sha256: Digest


class Heartbeat(Closed):
    server: Identifier
    policies: list[PolicyReadiness] = Field(default_factory=list, max_length=128)
    current: StateSummary | None = None
    bundles: list[AvailableBundle] = Field(default_factory=list, max_length=256)


def actor(state: AppState = Depends(get_state), authorization: str | None = Header(default=None)):
    return authenticate(state.settings, authorization)


def _role(who, roles):
    if who["role"] not in roles:
        deny("FORBIDDEN", 403)


def _server(who, server):
    if who["role"] == "agent" and who.get("server") != server:
        deny("FORBIDDEN", 403)


def _store(state):
    return OperationStore(state.policies.dir.parent)


def _record(store, op, who, action):
    store.append({"action": action, "actor": who["id"], "role": who["role"], "operation": op})


def _expire(store, records):
    now = utcnow()
    for op in records.values():
        if op["state"] in {"claimed", "running"} and op["lease_until"] < now:
            op.update(state="unknown", error_code="LEASE_EXPIRED")
            _record(store, op, {"id": "platform", "role": "system"}, "lease_expired")
        elif op["state"] in {"requested", "approved"} and op["expires_at"] < now:
            op.update(state="expired", error_code="REQUEST_EXPIRED")
            _record(store, op, {"id": "platform", "role": "system"}, "expired")


def _find(records, op_id, who):
    op = records.get(op_id)
    if not op:
        deny("NOT_FOUND", 404)
    _server(who, op["server"])
    return op


def _public(op):
    return {k: v for k, v in op.items() if k != "lease_id"}


def _check_ready(heartbeats, body):
    beat = heartbeats.get(body.server)
    if not beat or (datetime.now(timezone.utc) - datetime.fromisoformat(beat["at"])).total_seconds() > 30:
        deny("SAFETY_REJECTED")
    ready = next((p for p in beat["policies"] if p["name"] == body.policy_name), None)
    required = {"policy", "evidence", "capability", "monitor_period", "collection", "allowed_mode"}
    if not ready or set(ready["checks"]) != required or not all(ready["checks"].values()):
        deny("SAFETY_REJECTED")
    if ready["expected"] != body.expected.model_dump(mode="json"):
        deny("STATE_CHANGED")
    if ready["method"] in {"sigkill_fd_install", "override_lsm", "override_syscall"} and body.confirmation != "ENGINE_RESTART":
        deny("CONFIRMATION_REQUIRED")


@router.post("", status_code=201)
def request_operation(body: OperationRequest, state: AppState = Depends(get_state), who=Depends(actor)):
    _role(who, {"runtime_operator"})
    if body.kind == "bundle.apply" and not all((body.policy_id, body.policy_revision, body.environment_id, body.environment_revision)):
        deny("BUNDLE_INPUT_REQUIRED", 422)
    if body.kind == "bundle.switch" and not body.bundle_id:
        deny("BUNDLE_INPUT_REQUIRED", 422)
    if body.kind == "tetragon.mode" and (not body.policy_name or not body.mode or not body.expected.policy_id or not body.expected.mode or not body.expected.spec_sha256):
        deny("MODE_INPUT_REQUIRED", 422)
    servers = {t.get("server") for t in state.settings.operation_tokens if t["role"] == "agent"}
    if body.server not in servers:
        deny("TARGET_MISMATCH", 422)
    store = _store(state)
    op_id = uuid4().hex
    with store.transaction() as (records, heartbeats):
        if body.kind == "tetragon.mode" and body.mode == "enforce":
            _check_ready(heartbeats, body)
        payload = ({"bundle_id": body.bundle_id} if body.kind == "bundle.switch" else
                   {"policy_name": body.policy_name, "mode": body.mode, "confirmation": body.confirmation})
        if body.kind == "bundle.apply":
            response = export_bundle(body.policy_id, body.policy_revision, BundleExportRequest(
                environment_id=body.environment_id, environment_revision=body.environment_revision,
                requested_by_role="runtime_operator", server_values_override=body.server_values_override,
                bundle_id=body.bundle_id), state)
            if response.status_code != 200:
                return response
            metadata = json.loads(base64.urlsafe_b64decode(response.headers["X-Byul-Bundle-Metadata"]))
            archive = store.root / f"{op_id}.tar.gz"
            with archive.open("xb") as out:
                out.write(response.body)
            archive.chmod(0o600)
            payload = {"bundle_id": metadata["bundle_id"], "bundle_sha256": metadata["bundle_sha256"],
                       "archive_sha256": hashlib.sha256(response.body).hexdigest(),
                       "policy_id": body.policy_id, "policy_revision": body.policy_revision}
        needs = body.kind == "bundle.apply" or (body.kind == "tetragon.mode" and body.mode == "enforce")
        op = {"id": op_id, "server": body.server, "kind": body.kind, "payload": payload,
              "expected": body.expected.model_dump(mode="json"), "state": "requested" if needs else "approved",
              "approval_required": needs, "requested_by": who["id"], "requested_role": who["role"],
              "approvals": [], "lab_self_approval": state.settings.operation_lab_self_approval,
              "created_at": utcnow(), "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()}
        _record(store, op, who, "requested")
        return _public(op)


@router.get("")
def list_operations(server: str | None = None, state: AppState = Depends(get_state), who=Depends(actor)):
    _role(who, {"viewer", "runtime_operator", "approver", "agent"})
    if who["role"] == "agent":
        if not server:
            deny("SERVER_REQUIRED", 422)
        _server(who, server)
    store = _store(state)
    with store.transaction() as (records, heartbeats):
        _expire(store, records)
        if who["role"] == "agent":
            beat = heartbeats.get(server, {"server": server, "policies": [], "current": None})
            beat["last_poll_at"] = utcnow()
            store.append({"action": "poll", "actor": who["id"], "heartbeat": beat})
            heartbeats[server] = beat
        servers = {t["server"] for t in state.settings.operation_tokens if t["role"] == "agent"}
        agents = [heartbeats.get(s, {"server": s, "current": None, "policies": [], "bundles": []})
                  for s in sorted(servers) if server is None or s == server]
        return {"items": [_public(o) for o in records.values() if server is None or o["server"] == server],
                "actor": who, "agents": agents, "lab_self_approval": state.settings.operation_lab_self_approval,
                "monitor_seconds": state.settings.operation_monitor_seconds}


@router.post("/agent-state")
def heartbeat(body: Heartbeat, state: AppState = Depends(get_state), who=Depends(actor)):
    _role(who, {"agent"})
    _server(who, body.server)
    store = _store(state)
    with store.transaction() as (_, beats):
        beat = {**body.model_dump(mode="json"), "at": utcnow(),
                "last_poll_at": beats.get(body.server, {}).get("last_poll_at")}
        store.append({"action": "agent_state", "actor": who["id"], "heartbeat": beat})
    return {"ok": True}


@router.post("/{op_id}/approve")
def approve(op_id: str, state: AppState = Depends(get_state), who=Depends(actor)):
    _role(who, {"approver", "runtime_operator"} if state.settings.operation_lab_self_approval else {"approver"})
    store = _store(state)
    with store.transaction() as (records, _):
        _expire(store, records)
        op = _find(records, op_id, who)
        if op["state"] != "requested" or not op["approval_required"]:
            deny("STATE_CONFLICT")
        self_approval = op["requested_by"] == who["id"]
        if self_approval and not state.settings.operation_lab_self_approval:
            deny("SELF_APPROVAL_FORBIDDEN", 403)
        if who["role"] != "approver" and not self_approval:
            deny("FORBIDDEN", 403)
        op["approvals"].append({"token_id": who["id"], "role": who["role"], "at": utcnow(), "self_approval": self_approval})
        op["state"] = "approved"
        _record(store, op, who, "approved")
        return _public(op)


@router.post("/{op_id}/claim")
def claim(op_id: str, body: Claim, state: AppState = Depends(get_state), who=Depends(actor)):
    _role(who, {"agent"})
    store = _store(state)
    with store.transaction() as (records, _):
        _expire(store, records)
        op = _find(records, op_id, who)
        if op["state"] != "approved":
            deny("STATE_CONFLICT")
        op.update(state="claimed", claimed_by=who["id"], lease_id=uuid4().hex,
                  claimed_at=utcnow(),
                  lease_until=(datetime.now(timezone.utc) + timedelta(seconds=state.settings.operation_lease_seconds)).isoformat())
        _record(store, op, who, "claimed")
        return op


@router.get("/{op_id}/bundle")
def download(op_id: str, state: AppState = Depends(get_state), who=Depends(actor)):
    _role(who, {"agent"})
    store = _store(state)
    with store.transaction() as (records, _):
        _expire(store, records)
        op = _find(records, op_id, who)
        if op["kind"] != "bundle.apply" or op["state"] != "claimed" or op["claimed_by"] != who["id"]:
            deny("STATE_CONFLICT")
        return Response((store.root / f"{op['id']}.tar.gz").read_bytes(), media_type="application/gzip")


@router.post("/{op_id}/result")
def result(op_id: str, body: Result, state: AppState = Depends(get_state), who=Depends(actor)):
    _role(who, {"agent"})
    store = _store(state)
    with store.transaction() as (records, _):
        _expire(store, records)
        op = _find(records, op_id, who)
        if op["state"] not in {"claimed", "running"} or op["claimed_by"] != who["id"] or op["lease_id"] != body.lease_id:
            deny("STATE_CONFLICT")
        if body.started_at.tzinfo is None or (body.finished_at is not None and body.finished_at.tzinfo is None):
            deny("UTC_REQUIRED", 422)
        if body.state != "running" and (body.finished_at is None or body.finished_at < body.started_at):
            deny("RESULT_TIME_REQUIRED", 422)
        if ((body.state in {"running", "succeeded"} and (body.code != "OK" or body.dry_run))
                or (body.state == "checked" and (body.code != "DRY_RUN" or not body.dry_run))
                or (body.code == "DRY_RUN" and body.state != "checked")
                or (body.state == "rolled_back" and body.code != "ROLLED_BACK")
                or (body.state == "unknown" and body.code not in {"STATUS_UNKNOWN", "LEASE_EXPIRED"})
                or (body.dry_run and (body.state not in {"checked", "failed"} or body.code == "OK"))):
            deny("RESULT_INVALID", 422)
        op.update(state=body.state, result=body.model_dump(mode="json", exclude={"lease_id"}))
        if body.state == "running":
            op["running_at"] = utcnow()
        _record(store, op, who, body.state)
        return _public(op)
