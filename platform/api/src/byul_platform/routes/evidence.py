"""증거 목록(evidence-index)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..models import EvidenceList
from ..state import AppState, get_state

router = APIRouter(prefix="/api", tags=["evidence"])


def _entries(state: AppState) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    counter = 0
    for index_file in state.evidence.indexes():
        doc = index_file.document
        # Validate the header once and each row once (never build N-item error
        # messages before validating the same N rows a second time).
        items = doc.get("items") if isinstance(doc, dict) and isinstance(doc.get("items"), list) else None
        head_ok = isinstance(doc, dict) and items is not None and state.contracts.is_valid(
            "evidence-index", {**doc, "items": []}
        )
        if not head_ok:
            errors = state.contracts.errors("evidence-index", doc)
            invalid.append({"index": counter, "errors": [f"{index_file.path} {e.path}: {e.message}" for e in errors], "raw": doc})
            counter += 1
            continue
        for item in items:
            item_errors = state.contracts.errors("evidence-index", {**doc, "items": [item]})
            path_error = None if item_errors else state.evidence.path_error(index_file.path, item["path"])
            if item_errors:
                invalid.append({"index": counter, "errors": [f"{e.path}: {e.message}" for e in item_errors], "raw": item})
            elif path_error:
                invalid.append({"index": counter, "errors": [path_error], "raw": item})
            else:
                entries.append({"producer": doc["producer"], "index_created_at": doc["created_at"], "index_path": index_file.path, "item": item})
            counter += 1
    return entries, invalid


def evidence_for_refs(state: AppState, refs: set[str]) -> list[dict[str, Any]]:
    entries, _ = _entries(state)
    return [e["item"] for e in entries if refs & set(e["item"].get("refs") or [])]


@router.get("/evidence", response_model=EvidenceList)
def evidence(state: AppState = Depends(get_state)) -> dict[str, Any]:
    entries, invalid = _entries(state)
    return {"items": entries, "invalid": invalid, "source": state.source("evidence")}
