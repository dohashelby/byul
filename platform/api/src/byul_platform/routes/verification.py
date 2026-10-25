"""update 검증 결과와 재검증 요청. 판정은 update가 낸 값을 그대로 옮긴다."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..models import RevalidationList, VerificationDetail, VerificationList
from ..state import AppState, get_state, partition

router = APIRouter(prefix="/api/update", tags=["update"])


def _results(state: AppState) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    entries = state.update.reports()
    valid, invalid = partition(state.contracts, "verification-result", [e.candidate for e in entries])
    by_id = {
        e.candidate["report_ref"]["id"]: e
        for e in entries
        if isinstance(e.candidate, dict) and isinstance(e.candidate.get("report_ref"), dict)
    }
    valid.sort(key=lambda r: r["created_at"], reverse=True)
    return valid, invalid, by_id


def latest_result(state: AppState) -> dict[str, Any] | None:
    valid, _, _ = _results(state)
    return valid[0] if valid else None


@router.get("/results", response_model=VerificationList)
def results(state: AppState = Depends(get_state)) -> dict[str, Any]:
    valid, invalid, _ = _results(state)
    return {"items": valid, "invalid": invalid, "source": state.source("update")}


@router.get("/results/{report_id}", response_model=VerificationDetail)
def result(report_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    valid, _, by_id = _results(state)
    item = next((r for r in valid if r["report_ref"]["id"] == report_id), None)
    if item is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "보고서가 없거나 형식 오류입니다"})
    entry = by_id.get(report_id)
    return {
        "item": item,
        "raw_checks": entry.raw_checks if entry else None,
        "raw_path": entry.path if entry else None,
        "source": state.source("update"),
    }


@router.get("/revalidation", response_model=RevalidationList)
def revalidation(state: AppState = Depends(get_state)) -> dict[str, Any]:
    valid, invalid = partition(state.contracts, "revalidation-request", state.runtime.revalidation())
    valid.sort(key=lambda r: r["created_at"], reverse=True)
    return {"items": valid, "invalid": invalid, "source": state.source("runtime")}
