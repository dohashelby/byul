"""update 검증 결과와 재검증 요청. 판정은 update가 낸 값을 그대로 옮긴다."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..models import RevalidationList, VerificationDetail, VerificationList
from ..sources.report_evidence import enrich_report
from ..sources.update import ReportEntry, report_view
from ..state import AppState, get_state, partition

router = APIRouter(prefix="/api/update", tags=["update"])


def _results(state: AppState) -> tuple[list[ReportEntry], list[dict[str, Any]], dict[str, Any]]:
    entries, source = state.update.snapshot()
    valid, invalid = [], []
    seen = set()
    for index, entry in enumerate(entries):
        errors = state.contracts.errors("verification-result", entry.candidate)
        if errors:
            messages = ([entry.candidate["_error"]] if isinstance(entry.candidate, dict) and "_error" in entry.candidate
                        else [f"{e.path}: {e.message}" for e in errors])
            invalid.append({"index": index, "errors": messages, "raw": entry.candidate})
        else:
            ref = entry.candidate["report_ref"]
            key = (ref["id"], ref["revision"], ref["sha256"])
            if key not in seen:
                seen.add(key)
                valid.append(entry)
    valid.sort(key=lambda e: (e.candidate["created_at"], e.candidate["report_ref"]["revision"]), reverse=True)
    return valid, invalid, source


def latest_result(state: AppState) -> dict[str, Any] | None:
    valid, _, _ = _results(state)
    return valid[0].candidate if valid else None


@router.get("/results", response_model=VerificationList)
def results(state: AppState = Depends(get_state)) -> dict[str, Any]:
    valid, invalid, source = _results(state)
    return {"items": [e.candidate for e in valid], "invalid": invalid, "source": source}


@router.get("/results/{report_id}", response_model=VerificationDetail)
def result(
    report_id: str,
    revision: Annotated[int | None, Query(ge=1)] = None,
    sha256: Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")] = None,
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    if (revision is None) != (sha256 is None):
        raise HTTPException(422, detail={"code": "REPORT_REF_INCOMPLETE", "message": "보고서 판본과 SHA-256을 함께 지정하세요"})
    valid, _, source = _results(state)
    entry = next((e for e in valid if e.candidate["report_ref"]["id"] == report_id and
                  (revision is None or (e.candidate["report_ref"]["revision"], e.candidate["report_ref"]["sha256"])
                   == (revision, sha256))), None)
    if entry is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "보고서가 없거나 형식 오류입니다"})
    report = report_view(entry.candidate, entry)
    enrich_report(report, entry.candidate, entry.object_store)
    return {
        "item": entry.candidate,
        "raw_checks": entry.raw_checks,
        "raw_path": entry.path,
        "report": report,
        "source": source,
    }


@router.get("/revalidation", response_model=RevalidationList)
def revalidation(state: AppState = Depends(get_state)) -> dict[str, Any]:
    valid, invalid = partition(state.contracts, "revalidation-request", state.runtime.revalidation())
    valid.sort(key=lambda r: r["created_at"], reverse=True)
    return {"items": valid, "invalid": invalid, "source": state.source("runtime")}
