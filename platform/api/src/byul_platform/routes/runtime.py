"""runtime 조회 결과(요청 내역·사건·상태). 전부 읽기 전용."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..models import (
    RequestDetail,
    RequestList,
    RequestSummary,
    RuntimeComparisons,
    RuntimeEventList,
    RuntimeStatusResponse,
)
from ..sources.runtime import RuntimeNotFound, request_row
from ..state import AppState, get_state, invalid_one, partition
from .evidence import evidence_for_refs

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


def _sorted_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda e: (e.get("recorded_at") or "", e.get("event_id") or ""), reverse=True)


@router.get("/events", response_model=RuntimeEventList)
def events(state: AppState = Depends(get_state)) -> dict[str, Any]:
    batch = state.runtime.events()
    valid, invalid = partition(state.contracts, "runtime-event", batch.candidates)
    return {"items": _sorted_events(valid), "invalid": invalid, "source": state.source("runtime"), "truncated": batch.truncated}


@router.get("/requests", response_model=RequestList)
def requests(state: AppState = Depends(get_state)) -> dict[str, Any]:
    batch = state.runtime.events()
    gateway = [c for c in batch.candidates if not isinstance(c, dict) or c.get("source") in ("gateway", None)]
    valid, invalid = partition(state.contracts, "runtime-event", gateway)
    rows = [request_row(e) for e in _sorted_events(valid) if e["source"] == "gateway"]
    return {"items": rows, "invalid": invalid, "source": state.source("runtime"), "truncated": batch.truncated}


@router.get("/requests/{request_id}", response_model=RequestDetail)
def request_detail(request_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    try:
        result = state.runtime.request_detail(request_id)
    except RuntimeNotFound:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "요청 기록이 없습니다"}) from None
    event, event_invalid = result.event, None
    if event is not None:
        event_invalid = invalid_one(state.contracts, "runtime-event", event)
        if event_invalid:
            event = None
    evidence = evidence_for_refs(state, {f"req:{request_id}"})
    return {
        "row": request_row(event) if event else None,
        "event": event,
        "event_invalid": event_invalid,
        "evidence_state": result.evidence_state,
        "evidence_summary": result.evidence_summary,
        "native_detail": result.native_detail,
        "evidence": evidence,
        "source": state.source("runtime"),
    }


@router.get("/summary", response_model=RequestSummary)
def summary(state: AppState = Depends(get_state)) -> dict[str, Any]:
    s = state.runtime.summary()
    return {
        "counts": s.counts,
        "total": s.total,
        "corrupt_lines": s.corrupt_lines,
        "generated_at": s.generated_at,
        "source": state.source("runtime"),
    }


@router.get("/status", response_model=RuntimeStatusResponse)
def status(state: AppState = Depends(get_state)) -> dict[str, Any]:
    doc = state.runtime.status()
    bad = invalid_one(state.contracts, "runtime-status", doc)
    return {"item": None if bad else doc, "invalid": bad, "source": state.source("runtime")}


@router.get("/comparisons", response_model=RuntimeComparisons)
def runtime_comparisons(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {**state.runtime.comparisons(), "source": state.source("runtime")}
