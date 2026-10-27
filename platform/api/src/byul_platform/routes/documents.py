"""환경·정책 입력. 저장은 항상 새 판본이고, 이번 단계는 DRAFT로만 저장한다."""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..contracts import canonical_sha256
from ..diff import json_diff
from ..models import (
    EnvironmentRevision,
    EnvironmentSaved,
    PolicyRevision,
    PolicySaved,
    RevisionDiff,
    RevisionList,
    SaveRejected,
)
from ..models import EnvironmentDoc, PolicyDoc
from ..store import DocumentStore, InvalidDocId, RevisionExists, StoredRevision, now_utc
from ..state import AppState, get_state

router = APIRouter(prefix="/api", tags=["documents"])


def _reject(code: str, errors: list[dict[str, str]], status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "errors": errors})


def _revision_out(r: StoredRevision) -> dict[str, Any]:
    return {"meta": r.meta(), "document": r.document}


def _not_found() -> HTTPException:
    return HTTPException(404, detail={"code": "NOT_FOUND", "message": "판본이 없습니다"})


def _revisions(store: DocumentStore, doc_id: str) -> list[StoredRevision]:
    try:
        return store.revisions(doc_id)
    except InvalidDocId:
        raise _not_found() from None


def _get(store: DocumentStore, doc_id: str, revision: int) -> StoredRevision:
    try:
        found = store.get(doc_id, revision)
    except InvalidDocId:
        found = None
    if found is None:
        raise _not_found()
    return found


def fact_warnings(document: dict[str, Any]) -> list[dict[str, str]]:
    """facts 값을 false로 두면서 비사용 근거가 비어 있으면 경고한다(계약 원칙: false는 근거가 있어야 인정)."""
    out = []
    facts = document.get("facts") if isinstance(document.get("facts"), dict) else {}
    for name, fact in facts.items():
        if isinstance(fact, dict) and fact.get("value") is False and not (fact.get("absence_evidence") or "").strip():
            out.append({"path": f"/facts/{name}/absence_evidence", "message": "'사용 안 함'으로 두려면 비사용 근거가 필요합니다(없으면 update가 인정하지 않음)"})
    return out + claim_warnings(document)


def environment_snapshot_sha256(document: dict[str, Any]) -> str:
    """비사용 근거가 묶이는 환경 입력의 해시. revision·created_at·framework_profile.absence_claims를 뺀다."""
    snapshot = {k: v for k, v in document.items() if k not in ("revision", "created_at")}
    profile = snapshot.get("framework_profile")
    if isinstance(profile, dict):
        snapshot["framework_profile"] = {k: v for k, v in profile.items() if k != "absence_claims"}
    return canonical_sha256(snapshot)


def claim_warnings(document: dict[str, Any]) -> list[dict[str, str]]:
    """검토 완료된 비사용 근거가 지금 입력과 맞지 않으면 경고한다. 근거의 진위는 판단하지 않는다."""
    profile = document.get("framework_profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("absence_claims"), dict):
        return []
    facts = document.get("facts") if isinstance(document.get("facts"), dict) else {}
    snapshot = environment_snapshot_sha256(document)
    out = []
    for name, claim in profile["absence_claims"].items():
        if not isinstance(claim, dict) or claim.get("status") != "reviewed":
            continue
        base = f"/framework_profile/absence_claims/{name}"
        fact = facts.get(name)
        if not isinstance(fact, dict) or fact.get("value") is not False:
            out.append({"path": base, "message": "사실 값이 '사용 안 함'이 아닌데 비사용 근거가 있습니다"})
        target = claim.get("target") if isinstance(claim.get("target"), dict) else {}
        if target.get("environment_snapshot_sha256") != snapshot:
            out.append({"path": f"{base}/target/environment_snapshot_sha256", "message": "근거를 기록한 뒤 환경 입력이 바뀌었습니다. 다시 검토하고 대상을 고정하세요"})
        if profile.get("profile_status") != "observed":
            out.append({"path": f"{base}/status", "message": "실제 확인 환경이 아니면 이 근거로 비적용 후보가 되지 않습니다"})
    return out


def _schema_errors(state: AppState, schema: str, doc: Any) -> list[dict[str, str]]:
    return [e.as_dict() for e in state.contracts.errors(schema, doc)]


def _diff(store: DocumentStore, doc_id: str, from_rev: int, to_rev: int) -> dict[str, Any]:
    a, b = _get(store, doc_id, from_rev), _get(store, doc_id, to_rev)
    return {"doc_id": doc_id, "from_revision": from_rev, "to_revision": to_rev, "entries": json_diff(a.document, b.document)}


# --- 환경 --------------------------------------------------------------------


@router.get("/environments", response_model=RevisionList, tags=["environments"])
def list_environments(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {"items": [r.meta() for r in state.environments.list_latest()], "source": state.source("store")}


@router.get("/environments/{doc_id}/revisions", response_model=RevisionList, tags=["environments"])
def environment_revisions(doc_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {"items": [r.meta() for r in _revisions(state.environments, doc_id)], "source": state.source("store")}


@router.get("/environments/{doc_id}/revisions/{revision}", response_model=EnvironmentRevision, tags=["environments"])
def environment_revision(doc_id: str, revision: int, state: AppState = Depends(get_state)) -> dict[str, Any]:
    return _revision_out(_get(state.environments, doc_id, revision))


@router.get("/environments/{doc_id}/diff", response_model=RevisionDiff, tags=["environments"])
def environment_diff(doc_id: str, from_revision: int, to_revision: int, state: AppState = Depends(get_state)) -> dict[str, Any]:
    return _diff(state.environments, doc_id, from_revision, to_revision)


@router.post(
    "/environments",
    status_code=201,
    response_model=EnvironmentSaved,
    responses={422: {"model": SaveRejected}},
    tags=["environments"],
)
def save_environment(document: EnvironmentDoc = Body(...), state: AppState = Depends(get_state)) -> Any:
    doc = copy.deepcopy(document)
    doc_id = doc.get("environment_id")
    if not isinstance(doc_id, str):
        return _reject("SCHEMA_INVALID", [{"path": "/environment_id", "message": "환경 ID가 필요합니다"}])
    try:
        doc["revision"] = state.environments.next_revision(doc_id)
    except InvalidDocId:
        return _reject("SCHEMA_INVALID", [{"path": "/environment_id", "message": "ID 형식이 맞지 않습니다"}])
    doc["created_at"] = now_utc()
    errors = _schema_errors(state, "environment", doc)
    if errors:
        return _reject("SCHEMA_INVALID", errors)
    try:
        saved = state.environments.append(doc_id, doc["revision"], doc, status="DRAFT", label=None)
    except RevisionExists:
        return _reject("BAD_REQUEST", [{"path": "/revision", "message": "같은 판본이 이미 있습니다. 다시 저장하세요"}], 409)
    return JSONResponse(status_code=201, content={"saved": _revision_out(saved), "warnings": fact_warnings(doc)})


# --- 정책 --------------------------------------------------------------------


@router.get("/policies", response_model=RevisionList, tags=["policies"])
def list_policies(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {"items": [r.meta() for r in state.policies.list_latest()], "source": state.source("store")}


@router.get("/policies/{doc_id}/revisions", response_model=RevisionList, tags=["policies"])
def policy_revisions(doc_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {"items": [r.meta() for r in _revisions(state.policies, doc_id)], "source": state.source("store")}


@router.get("/policies/{doc_id}/revisions/{revision}", response_model=PolicyRevision, tags=["policies"])
def policy_revision(doc_id: str, revision: int, state: AppState = Depends(get_state)) -> dict[str, Any]:
    return _revision_out(_get(state.policies, doc_id, revision))


@router.get("/policies/{doc_id}/diff", response_model=RevisionDiff, tags=["policies"])
def policy_diff(doc_id: str, from_revision: int, to_revision: int, state: AppState = Depends(get_state)) -> dict[str, Any]:
    return _diff(state.policies, doc_id, from_revision, to_revision)


@router.post(
    "/policies",
    status_code=201,
    response_model=PolicySaved,
    responses={422: {"model": SaveRejected}},
    tags=["policies"],
)
def save_policy(document: PolicyDoc = Body(...), state: AppState = Depends(get_state)) -> Any:
    doc = copy.deepcopy(document)
    if doc.get("status", "DRAFT") != "DRAFT":
        return _reject("STATUS_NOT_ALLOWED", [{"path": "/status", "message": "이번 단계에서는 DRAFT로만 저장합니다(확정은 다음 단계)"}])
    doc["status"] = "DRAFT"
    doc["created_at"] = now_utc()
    doc_id = doc.get("policy_id")
    if not isinstance(doc_id, str):
        return _reject("SCHEMA_INVALID", [{"path": "/policy_id", "message": "정책 ID가 필요합니다"}])

    # 기준 환경 판본이 저장소에 있으면 해시를 저장소 값으로 채우고, 다르면 거부한다.
    env_ref = doc.get("environment_ref")
    if isinstance(env_ref, dict) and isinstance(env_ref.get("id"), str) and isinstance(env_ref.get("revision"), int):
        try:
            stored_env = state.environments.get(env_ref["id"], env_ref["revision"])
        except InvalidDocId:
            stored_env = None
        if stored_env is not None:
            if env_ref.get("sha256") and env_ref["sha256"] != stored_env.sha256:
                return _reject("SCHEMA_INVALID", [{"path": "/environment_ref/sha256", "message": "저장된 환경 판본의 해시와 다릅니다"}])
            env_ref["sha256"] = stored_env.sha256

    errors = _schema_errors(state, "policy", doc)
    if errors:
        return _reject("SCHEMA_INVALID", errors)
    try:
        revision = state.policies.next_revision(doc_id)
        saved = state.policies.append(doc_id, revision, doc, status="DRAFT", label=str(doc.get("policy_version")))
    except InvalidDocId:
        return _reject("SCHEMA_INVALID", [{"path": "/policy_id", "message": "ID 형식이 맞지 않습니다"}])
    except RevisionExists:
        return _reject("BAD_REQUEST", [{"path": "/", "message": "같은 판본이 이미 있습니다. 다시 저장하세요"}], 409)
    return JSONResponse(status_code=201, content={"saved": _revision_out(saved), "warnings": []})
