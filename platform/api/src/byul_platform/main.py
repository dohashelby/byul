"""FastAPI 앱. API 라우터와 빌드된 화면(정적 파일)을 함께 서빙한다."""

from __future__ import annotations

import ipaddress
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import HTTPException
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse

from .config import Settings
from .sources.runtime import RuntimeNotFound, RuntimeUnauthorized, RuntimeUnavailable
from .state import build_state
from .routes import documents, evidence, overview, runtime, verification

log = logging.getLogger("byul_platform")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state = build_state(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        state.close()

    app = FastAPI(title="byul platform API", version="0.1.0", lifespan=lifespan)
    app.state.byul = state
    for module in (overview, runtime, verification, documents, evidence):
        app.include_router(module.router)

    @app.exception_handler(RuntimeUnavailable)
    async def _unavailable(_req: Request, _exc: RuntimeUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": {"code": "RUNTIME_UNREACHABLE", "message": "runtime에 연결하지 못했습니다"}})

    @app.exception_handler(RuntimeUnauthorized)
    async def _unauthorized(_req: Request, _exc: RuntimeUnauthorized) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": {"code": "RUNTIME_UNAUTHORIZED", "message": "runtime이 조회 토큰을 거부했습니다"}})

    @app.exception_handler(RuntimeNotFound)
    async def _not_found(_req: Request, _exc: RuntimeNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": {"code": "NOT_FOUND", "message": "기록이 없습니다"}})

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        contract_components = state.contracts.openapi_components()
        contract_components["EvidenceItem"] = contract_components["EvidenceIndex"]["properties"]["items"]["items"]
        components.update(contract_components)
        schema = _resolve_contract_markers(schema)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    _mount_web(app, settings.web_dist)
    return app


def _resolve_contract_markers(node: Any) -> Any:
    if isinstance(node, dict):
        if "x-contract" in node:
            ref = {"$ref": f"#/components/schemas/{node['x-contract']}"}
            extra = {k: v for k, v in node.items() if k in ("description",)}
            return {**ref, **extra}
        return {k: _resolve_contract_markers(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_contract_markers(v) for v in node]
    return node


def _mount_web(app: FastAPI, dist: Path) -> None:
    """빌드된 화면을 서빙한다. /api 밖의 경로는 index.html로 보내 화면 라우터가 처리한다."""
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def web(path: str) -> Any:
        if path.startswith("api/") or path == "api":
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "없는 API입니다"})
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse(
            status_code=503,
            content=jsonable_encoder({"detail": {"code": "WEB_NOT_BUILT", "message": f"화면이 빌드되지 않았습니다: {dist} (web에서 npm run build)"}}),
        )


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    if not _is_loopback(settings.host):
        raise SystemExit(f"로그인이 없어 외부 주소로 열지 않습니다: {settings.host} (127.0.0.1만 허용)")
    logging.basicConfig(level=logging.INFO)
    app = create_app(settings)
    state = app.state.byul
    for info in state.source_infos():
        log.info("source %s: %s %s", info["name"], info["mode"], info["detail"] or "")
    if state.example_mode:
        log.info("예시 모드: 일부 출처가 contracts/examples를 읽습니다")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    run()
