from fastapi import APIRouter, Depends, Query
from ..route_audit_models import RouteAuditResponse
from ..sources.route_audit import read_route_audit
from ..state import AppState, get_state

router = APIRouter(tags=["runtime audit"])


@router.get("/route-audit", response_model=RouteAuditResponse)
def route_audit(limit: int = Query(100, ge=1, le=200), state: AppState = Depends(get_state)) -> dict:
    status, item = read_route_audit(state.settings, limit=limit)
    return {"state": status, "item": item, "source": state.source("runtime")}
