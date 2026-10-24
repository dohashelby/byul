"""Backend HTTP client for status, collection, token counts, and evidence."""

from __future__ import annotations

from http.client import HTTPException
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .backend import BackendConfig, parse_prometheus_metrics
from .mock_engine import _utc_now


class EngineClient:
    """Perform non-completion HTTP calls to the configured backend."""

    def __init__(self, backend: BackendConfig, timeout_s: float) -> None:
        self.backend = backend
        self.timeout_s = timeout_s

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        request_id: str | None = None,
        content_type: str | None = None,
        metrics: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        if metrics:
            connection, request_path = self.backend.metrics_connection(self.timeout_s)
        else:
            connection, request_path = self.backend.connection(path, self.timeout_s)
        headers = self.backend.request_headers(
            request_id=request_id, content_type=content_type, metrics=metrics
        )
        try:
            connection.request(method, request_path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def count_input_tokens(
        self,
        request_id: str,
        messages: list[Any],
        backend_model_id: str,
    ) -> int:
        """Ask the backend tokenizer for the prompt length; never estimate it."""

        payload = json.dumps(
            {
                "model": backend_model_id,
                "messages": messages,
                "add_generation_prompt": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        status, _, body = self.request(
            "POST",
            "/tokenize",
            body=payload,
            request_id=f"{request_id}-tokenize",
            content_type="application/json",
        )
        if status != 200:
            raise OSError(f"tokenize returned HTTP status {status}")
        result = json.loads(body.decode("utf-8"))
        count = result.get("count") if isinstance(result, dict) else None
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("tokenize response has no valid count")
        return count

    def fetch_json(self, path: str) -> dict[str, Any]:
        status, _, body = self.request("GET", path)
        if status != 200:
            raise OSError(f"backend returned HTTP status {status}")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("backend response must be a JSON object")
        return payload

    def collect_health(self) -> tuple[dict[str, Any], str | None]:
        if self.backend.kind == "mock":
            payload = self.fetch_json(self.backend.health_path)
            if payload.get("status") != "ok":
                raise ValueError("engine health response is not ok")
            return payload, payload.get("data_origin")
        status, _, _ = self.request("GET", self.backend.health_path)
        if status != 200:
            raise OSError(f"backend returned HTTP status {status}")
        return (
            {
                "reachable": True,
                "http_status": status,
                "inference_verified": False,
                "meaning": "health reachability does not prove successful inference",
            },
            "external_backend",
        )

    def collect_info(self) -> tuple[dict[str, Any], str | None]:
        version = self.fetch_json(self.backend.version_path)
        models = self.fetch_json(self.backend.models_path)
        entries = models.get("data")
        model_id = None
        max_model_len = None
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            model_id = entries[0].get("id")
            max_model_len = entries[0].get("max_model_len")
        if self.backend.kind == "mock":
            value = {
                "engine.kind": version.get("engine.kind"),
                "engine.version": version.get("engine.version"),
                "model.id": model_id,
                "option.max_model_len": max_model_len,
            }
            value = {key: item for key, item in value.items() if item is not None}
            origins = {version.get("data_origin"), models.get("data_origin")}
            origins.discard(None)
            data_origin = (
                origins.pop()
                if len(origins) == 1
                else ("mixed" if origins else None)
            )
            return value, data_origin

        available_ids = (
            sorted(
                {
                    item.get("id")
                    for item in entries
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                }
            )
            if isinstance(entries, list)
            else []
        )
        mapped_ids = list(dict.fromkeys(self.backend.model_map.values()))
        mapped_availability = {
            policy_model: {
                "id": backend_model,
                "available": backend_model in available_ids,
            }
            for policy_model, backend_model in self.backend.model_map.items()
        }
        value = {
            "engine.kind": "vllm",
            "engine.version": version.get("version", version.get("engine.version")),
            "model.ids": available_ids,
            "model.map": mapped_availability,
        }
        if len(mapped_ids) == 1 and mapped_ids[0] in available_ids:
            value["model.id"] = mapped_ids[0]
        value = {key: item for key, item in value.items() if item is not None}
        return value, "external_backend"

    def collect_metrics(self) -> tuple[dict[str, Any], str | None]:
        if self.backend.metrics_credential_status == "missing":
            raise OSError("metrics credential is configured but unavailable")
        if self.backend.metrics_credential_status == "invalid":
            raise OSError("metrics credential is invalid")
        status, _, body = self.request("GET", "", metrics=True)
        if status != 200:
            raise OSError(f"metrics endpoint returned HTTP status {status}")
        return (
            parse_prometheus_metrics(body, self.backend.metrics_map),
            "external_backend",
        )

    def status(self) -> dict[str, Any]:
        checked_at = _utc_now()
        url = self.backend.target.url(self.backend.health_path)
        try:
            status, _, body = self.request("GET", self.backend.health_path)
            if self.backend.kind == "mock":
                payload = json.loads(body.decode("utf-8"))
                healthy = (
                    status == 200
                    and isinstance(payload, dict)
                    and payload.get("status") == "ok"
                )
                receipts_store = (
                    payload.get("receipts_store")
                    if isinstance(payload, dict)
                    else None
                )
            else:
                payload = None
                healthy = status == 200
                receipts_store = {
                    "state": "not_available",
                    "ok": None,
                    "last_error": None,
                }
            return {
                "url": url,
                "healthy": healthy,
                "reachable": healthy,
                "inference_verified": False,
                "health": payload,
                "checked_at": checked_at,
                "error": None,
                "receipts_store": receipts_store,
                "credential": self.backend.credential_status,
            }
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            return {
                "url": url,
                "healthy": False,
                "reachable": False,
                "inference_verified": False,
                "health": None,
                "checked_at": checked_at,
                "error": f"{type(error).__name__}: backend health check failed",
                "receipts_store": {
                    "state": (
                        "unknown"
                        if self.backend.kind == "mock"
                        else "not_available"
                    ),
                    "ok": None,
                    "last_error": None,
                },
                "credential": self.backend.credential_status,
            }

    def receipt_lookup(self, request_id: str) -> dict[str, Any]:
        queried_at = _utc_now()
        if self.backend.kind != "mock":
            return {
                "status": "not_available",
                "queried_at": queried_at,
                "error": None,
                "evidence_kind": "not_connected",
            }
        query = urlencode({"request_id": request_id})
        try:
            with urlopen(
                self.backend.target.url(f"/_mock/receipts?{query}"),
                timeout=self.timeout_s,
            ) as response:
                if response.status != 200:
                    return {
                        "status": "unknown",
                        "queried_at": queried_at,
                        "error": (
                            "mock engine returned HTTP status "
                            f"{response.status}"
                        ),
                    }
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(
                payload.get("found"), bool
            ):
                return {
                    "status": "unknown",
                    "queried_at": queried_at,
                    "error": "mock engine returned an invalid receipt response",
                }
            receipts = payload.get("receipts")
            receipts = receipts if isinstance(receipts, list) else []
            final_receipt = next(
                (
                    item
                    for item in reversed(receipts)
                    if isinstance(item, dict)
                    and isinstance(item.get("stream_end"), str)
                ),
                {},
            )
            return {
                "status": "confirmed" if payload["found"] else "not_found",
                "queried_at": (
                    payload["queried_at"]
                    if isinstance(payload.get("queried_at"), str)
                    else queried_at
                ),
                "error": None,
                "stream_end": final_receipt.get("stream_end"),
                "evidence_kind": final_receipt.get("evidence_kind"),
            }
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            HTTPException,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            return {
                "status": "unknown",
                "queried_at": queried_at,
                "error": str(error),
            }

    def backend_receipt(self, request_id: str) -> str:
        if self.backend.kind != "mock":
            return "not_available"
        return str(self.receipt_lookup(request_id)["status"])

    def receipt_not_queried(self) -> str:
        return "not_queried" if self.backend.kind == "mock" else "not_available"

    def mock_cancel_evidence(self, request_id: str, *, wait: bool) -> str:
        if self.backend.kind != "mock":
            return "not_observed"
        stop_at = time.monotonic() + (0.3 if wait else 0)
        while True:
            evidence = self.receipt_lookup(request_id)
            if (
                evidence.get("stream_end") == "client_disconnected"
                and evidence.get("evidence_kind") == "mock"
            ):
                return "mock_engine_recorded"
            if time.monotonic() >= stop_at:
                return "not_observed"
            time.sleep(0.01)
