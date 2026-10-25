"""대시보드 요약과 연결 상태."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..models import Meta, Overview
from ..sources.runtime import RuntimeUnauthorized, RuntimeUnavailable
from ..state import AppState, get_state, partition
from . import runtime as runtime_routes
from .verification import latest_result

router = APIRouter(prefix="/api", tags=["overview"])


def _error(error: Exception) -> dict[str, str]:
    if isinstance(error, RuntimeUnauthorized):
        return {"code": "RUNTIME_UNAUTHORIZED", "message": "runtime이 조회 토큰을 거부했습니다"}
    return {"code": "RUNTIME_UNREACHABLE", "message": "runtime에 연결하지 못했습니다"}


@router.get("/meta", response_model=Meta)
def meta(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {
        "contracts_version": state.contracts.version,
        "example_mode": state.example_mode,
        "sources": state.source_infos(),
        "runtime_connection": state.runtime.ping(),
    }


@router.get("/overview", response_model=Overview)
def overview(state: AppState = Depends(get_state)) -> dict[str, Any]:
    out: dict[str, Any] = {"requests": None, "status": None, "open_revalidation": None}
    try:
        out["requests"] = runtime_routes.summary(state)
    except (RuntimeUnavailable, RuntimeUnauthorized) as error:
        out["requests_error"] = _error(error)
    try:
        out["status"] = runtime_routes.status(state)
    except (RuntimeUnavailable, RuntimeUnauthorized) as error:
        out["status_error"] = _error(error)
    try:
        valid, _ = partition(state.contracts, "revalidation-request", state.runtime.revalidation())
        out["open_revalidation"] = sum(1 for r in valid if r["status"] == "open")
    except (RuntimeUnavailable, RuntimeUnauthorized) as error:
        out["revalidation_error"] = _error(error)
    out["latest_verification"] = latest_result(state)
    return out
