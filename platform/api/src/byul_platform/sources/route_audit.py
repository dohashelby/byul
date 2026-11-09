"""One bounded GET using the existing runtime client and viewer credential."""
from pydantic import ValidationError
from ..config import Settings
from ..route_audit_models import RouteAuditDocument
from .runtime import RuntimeClient, RuntimeNotFound


def read_route_audit(settings: Settings, *, limit: int = 100) -> tuple[str, dict | None]:
    if not settings.runtime_base_url:
        return "not_configured", None
    client = RuntimeClient(settings.runtime_base_url, settings.runtime_viewer_token)
    try:
        try:
            document = client.get("/api/route-rejections", {"limit": limit})
        except RuntimeNotFound:
            return "unsupported", None
        try:
            item = RouteAuditDocument.model_validate(document)
            if item.window.returned != len(item.items) or item.window.returned > item.window.limit or item.window.scanned_bytes > item.window.max_bytes:
                return "invalid", None
        except ValidationError:
            return "invalid", None
        return "ok", item.model_dump(mode="json")
    finally:
        client.close()
