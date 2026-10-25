"""runtime 조회 결과(요청 내역·사건·상태). 전부 읽기 전용."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from fastapi.responses import Response

from ..models import (
    LabReportResponse,
    RequestDetail,
    RequestList,
    RequestSummary,
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
    detail, detail_invalid = result.detail, None
    if detail is not None:
        detail_invalid = invalid_one(state.contracts, "runtime-request-detail", detail)
        if detail_invalid:
            detail = None
    return {
        "row": request_row(event) if event else None,
        "event": event,
        "event_invalid": event_invalid,
        "detail": detail,
        "detail_invalid": detail_invalid,
        "evidence": evidence_for_refs(state, {f"req:{request_id}"}),
        "source": state.source("runtime"),
    }


@router.get(
    "/requests/{request_id}/evidence",
    response_class=Response,
    responses={200: {"content": {"application/json": {}}, "description": "runtime 증거 JSON 파일(그대로 전달)"}},
)
def request_evidence(request_id: str, state: AppState = Depends(get_state)) -> Response:
    """runtime의 요청 증거 JSON을 내려받는다. 내용은 runtime이 만든 그대로다(본문·자격정보 없음)."""
    try:
        body = state.runtime.request_evidence(request_id)
    except RuntimeNotFound:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "증거가 없습니다"}) from None
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="evidence-{request_id}.json"'},
    )


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


@router.get("/lab-report", response_model=LabReportResponse)
def lab_report(state: AppState = Depends(get_state)) -> dict[str, Any]:
    doc = state.runtime.lab_report()
    bad = invalid_one(state.contracts, "runtime-lab-report", doc)
    return {"item": None if bad else doc, "invalid": bad, "source": state.source("runtime")}
