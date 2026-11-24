"""Explicit platform-local guard preparation and execution endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request

from ..sources.update_execution import ExecutionError
from ..state import AppState, get_state
from ..update_execution_models import UpdateExecutionSettings, UpdateRun, UpdateRunList, UpdateRunRequest

router = APIRouter(prefix="/api/update", tags=["update execution"])


def _can_execute(request: Request, state: AppState) -> bool:
    account = getattr(request.state, "account", None)
    return not state.settings.login_enabled or bool(account and account["role"] == "admin")


def _actor(request: Request, state: AppState) -> str:
    if not _can_execute(request, state):
        raise HTTPException(403, detail={"code": "ROLE_FORBIDDEN", "message": "검증 실행은 관리자만 할 수 있습니다"})
    account = getattr(request.state, "account", None)
    return account["id"] if account else "local-development"


def _call(callback, *args):
    try:
        return callback(*args)
    except ExecutionError as error:
        raise HTTPException(error.status, detail={"code": error.code, "message": error.message}) from None


@router.get("/execution-settings", response_model=UpdateExecutionSettings)
def execution_settings(request: Request, state: AppState = Depends(get_state)):
    service = state.update_execution
    reason = service.unavailable_reason()
    return UpdateExecutionSettings(available=reason is None, can_execute=_can_execute(request, state),
        unavailable_reason=reason, temporary=state.store_mode == "example")


@router.get("/runs", response_model=UpdateRunList)
def runs(state: AppState = Depends(get_state)):
    return UpdateRunList(items=state.update_execution.list())


@router.get("/runs/{run_id}", response_model=UpdateRun)
def run(run_id: str, state: AppState = Depends(get_state)):
    return _call(state.update_execution.get, run_id)


@router.post("/runs/prepare", status_code=202, response_model=UpdateRun)
def prepare(body: UpdateRunRequest, request: Request, state: AppState = Depends(get_state)):
    actor = _actor(request, state)
    return _call(state.update_execution.prepare, body, actor)


@router.post("/runs/{run_id}/execute", status_code=202, response_model=UpdateRun)
def execute(run_id: str, request: Request, state: AppState = Depends(get_state)):
    actor = _actor(request, state)
    return _call(state.update_execution.execute, run_id, actor)
