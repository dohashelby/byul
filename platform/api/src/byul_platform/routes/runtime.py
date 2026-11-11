"""runtime 조회 결과(요청 내역·사건·상태). 전부 읽기 전용."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from fastapi.responses import Response

from ..models import (
    LabReportResponse,
    PhaseTimelineResponse,
    RequestDetail,
    RequestList,
    RequestSummary,
    RuntimeEventList,
    RuntimeStatusResponse,
)
from ..sources.runtime import RuntimeNotFound, RuntimeWindowIncomplete, request_row
from ..tetragon_models import TetragonSnapshot, TetragonStatusResponse
from ..gpu_models import GpuSnapshot, GpuStatusResponse
from pydantic import ValidationError
from ..state import AppState, get_state, invalid_one, partition
from .evidence import evidence_for_refs
from ..coverage_models import RuntimeCheckCoverage
from ..sources.checklist_links import coverage

from .route_audit import router as route_audit_router

router = APIRouter(prefix="/api/runtime", tags=["runtime"])
router.include_router(route_audit_router)


@router.get("/checks", response_model=RuntimeCheckCoverage)
def checks(state: AppState = Depends(get_state)) -> dict[str, Any]:
    batch = state.runtime.events()
    valid, _invalid = partition(state.contracts, "runtime-event", batch.candidates)
    return {**coverage(valid), "truncated": batch.truncated, "source_windows": batch.source_windows,
            "source": state.source("runtime")}


def _sorted_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda e: (e.get("recorded_at") or "", e.get("event_id") or ""), reverse=True)


@router.get("/events", response_model=RuntimeEventList)
def events(state: AppState = Depends(get_state)) -> dict[str, Any]:
    batch = state.runtime.events()
    valid, invalid = partition(state.contracts, "runtime-event", batch.candidates)
    return {"items": _sorted_events(valid), "invalid": invalid, "source": state.source("runtime"), "truncated": batch.truncated, "source_windows": batch.source_windows}


@router.get("/requests", response_model=RequestList)
def requests(state: AppState = Depends(get_state)) -> dict[str, Any]:
    batch = state.runtime.events()
    gateway = [c for c in batch.candidates if not isinstance(c, dict) or c.get("source") in ("gateway", None)]
    valid, invalid = partition(state.contracts, "runtime-event", gateway)
    rows = [request_row(e) for e in _sorted_events(valid) if e["source"] == "gateway"]
    return {"items": rows, "invalid": invalid, "source": state.source("runtime"), "truncated": batch.source_windows.get("gateway", {}).get("truncated", batch.truncated)}


@router.get("/requests/{request_id}", response_model=RequestDetail)
def request_detail(request_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    try:
        result = state.runtime.request_detail(request_id)
    except RuntimeWindowIncomplete:
        raise HTTPException(503,detail={"code":"REQUEST_WINDOW_INCOMPLETE","message":"제한된 검색 범위에서 요청을 확인하지 못했습니다.","source_available":True,"request_id":request_id}) from None
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
    except RuntimeWindowIncomplete:
        raise HTTPException(503,detail={"code":"REQUEST_WINDOW_INCOMPLETE","message":"제한된 검색 범위에서 요청 근거를 확인하지 못했습니다.","source_available":True,"request_id":request_id}) from None
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
        "counts_lower_bound":s.counts_lower_bound,"history_truncated":s.history_truncated,
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


@router.get("/phase-timeline", response_model=PhaseTimelineResponse)
def phase_timeline(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {**state.runtime.phase_timeline(), "source": state.source("runtime")}


@router.get("/tetragon-status", response_model=TetragonStatusResponse)
def tetragon_status(state: AppState = Depends(get_state)) -> dict[str, Any]:
    try:
        item = TetragonSnapshot.model_validate(state.runtime.tetragon_status())
    except ValidationError:
        item = TetragonSnapshot(schema_version="runtime-tetragon-state/1.0", state="error", fresh=False,
                                error="Tetragon 상태 자료의 형식을 확인할 수 없습니다")
    return {"item": item, "source": state.source("runtime")}


@router.get("/gpu-status", response_model=GpuStatusResponse)
def gpu_status(state: AppState = Depends(get_state)) -> dict[str, Any]:
    reader = getattr(state.runtime, "gpu_status", None)
    try:
        item = GpuSnapshot.model_validate(reader() if reader else {"state": "not_configured"})
    except ValidationError:
        item = GpuSnapshot(state="error", reason="INVALID_SNAPSHOT")
    return {"item": item, "source": state.source("runtime")}
