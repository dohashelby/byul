"""update 검증 결과와 재검증 요청. 판정은 update가 낸 값을 그대로 옮긴다."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..models import RevalidationList, VerificationDetail, VerificationList
from ..sources.report_evidence import enrich_report
from ..sources.update import ReportEntry, report_view
from ..state import AppState, get_state, partition

router = APIRouter(prefix="/api/update", tags=["update"])


def _results(state: AppState) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[ReportEntry]]:
    entries = state.update.reports()
    valid, invalid = partition(state.contracts, "verification-result", [e.candidate for e in entries])
    valid.sort(key=lambda r: (r["created_at"], r["report_ref"]["revision"]), reverse=True)
    return valid, invalid, entries


def latest_result(state: AppState) -> dict[str, Any] | None:
    valid, _, _ = _results(state)
    return valid[0] if valid else None


@router.get("/results", response_model=VerificationList)
def results(state: AppState = Depends(get_state)) -> dict[str, Any]:
    valid, invalid, _ = _results(state)
    return {"items": valid, "invalid": invalid, "source": state.source("update")}


@router.get("/results/{report_id}", response_model=VerificationDetail)
def result(report_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    valid, _, entries = _results(state)
    item = next((r for r in valid if r["report_ref"]["id"] == report_id), None)
    if item is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "보고서가 없거나 형식 오류입니다"})
    entry = next(e for e in entries if e.candidate is item)
    report = report_view(item)
    enrich_report(report, item, entry.object_store)
    return {
        "item": item,
        "raw_checks": entry.raw_checks,
        "raw_path": entry.path,
        "report": report,
        "source": state.source("update"),
    }


@router.get("/revalidation", response_model=RevalidationList)
def revalidation(state: AppState = Depends(get_state)) -> dict[str, Any]:
    valid, invalid = partition(state.contracts, "revalidation-request", state.runtime.revalidation())
    valid.sort(key=lambda r: r["created_at"], reverse=True)
    return {"items": valid, "invalid": invalid, "source": state.source("runtime")}
