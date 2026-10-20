"""Read-only request and evidence queries over the MVP JSONL journals."""

from collections.abc import Callable
from typing import Any

from .mock_engine import _utc_now
from .store import JsonlStore


_FINAL_RESULTS = ("ALLOWED", "DENIED", "INVALID", "ERROR")
_FILTER_RESULTS = frozenset((*_FINAL_RESULTS, "INCOMPLETE"))


class QueryError(ValueError):
    """An invalid read-only query parameter."""


class RequestNotFoundError(LookupError):
    """No Gateway record exists for a request ID."""


class QueryService:
    """Build request-level views without treating individual log lines as requests."""

    def __init__(
        self,
        gateway_store: JsonlStore,
        engine_store: JsonlStore,
        record_store_status: Callable[[], dict[str, Any]],
        current_session_id: str | None = None,
    ) -> None:
        self._gateway_store = gateway_store
        self._engine_store = engine_store
        self._record_store_status = record_store_status
        self._current_session_id = current_session_id

    @staticmethod
    def _group(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            request_id = record.get("request_id")
            if isinstance(request_id, str) and request_id:
                grouped.setdefault(request_id, []).append(record)
        return grouped

    @staticmethod
    def _final(records: list[dict[str, Any]]) -> dict[str, Any] | None:
        finals = [record for record in records if record.get("phase") == "final"]
        return finals[-1] if finals else None

    @staticmethod
    def _is_incomplete(records: list[dict[str, Any]]) -> bool:
        return (
            any(record.get("phase") == "started" for record in records)
            and QueryService._final(records) is None
        )

    @staticmethod
    def _request_view(
        request_id: str, records: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        final = QueryService._final(records)
        if final is not None:
            result = final.get("decision", {}).get("result")
            source = final
        elif QueryService._is_incomplete(records):
            result = "INCOMPLETE"
            source = records[-1]
        else:
            return None
        if result not in _FILTER_RESULTS:
            return None
        decision = source.get("decision")
        request = source.get("request")
        delivery = source.get("delivery")
        if not isinstance(decision, dict):
            decision = {}
        if not isinstance(request, dict):
            request = {}
        if not isinstance(delivery, dict):
            delivery = {}
        return {
            "request_id": request_id,
            "recorded_at": source.get("recorded_at"),
            "observation_session_id": source.get("observation_session_id"),
            "model": request.get("model"),
            "max_tokens": request.get("max_tokens"),
            "result": result,
            "reason_code": decision.get("reason_code"),
            "http_status": decision.get("http_status"),
            "delivery": {
                "attempted": delivery.get("attempted"),
                "backend_receipt": delivery.get("backend_receipt"),
                "response": delivery.get("response"),
                "error_code": delivery.get("error_code"),
            },
        }

    def _read_gateway(
        self,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        records, corrupt = self._gateway_store.read_all()
        return self._group(records), corrupt

    def summary(self) -> dict[str, Any]:
        grouped, gateway_corrupt = self._read_gateway()
        _, engine_corrupt = self._engine_store.read_all()
        counts = {result: 0 for result in _FINAL_RESULTS}
        incomplete = 0
        for request_id, records in grouped.items():
            view = self._request_view(request_id, records)
            if view is None:
                continue
            result = view["result"]
            if result == "INCOMPLETE":
                incomplete += 1
            else:
                counts[result] += 1
        return {
            "counts": counts,
            "incomplete": incomplete,
            "total": sum(counts.values()) + incomplete,
            "corrupt_lines": {
                "gateway": len(gateway_corrupt),
                "engine": len(engine_corrupt),
            },
            "corrupt_details": {
                "gateway": gateway_corrupt[:20],
                "engine": engine_corrupt[:20],
            },
            "record_store": self._record_store_status(),
            "generated_at": _utc_now(),
        }

    def requests(
        self, *, result: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if result is not None and result not in _FILTER_RESULTS:
            raise QueryError("invalid result filter")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise QueryError("limit must be an integer from 1 through 1000")
        grouped, _ = self._read_gateway()
        views = [
            view
            for request_id, records in grouped.items()
            if (view := self._request_view(request_id, records)) is not None
            and (result is None or view["result"] == result)
        ]
        for view in views:
            view["current_session"] = (
                self._current_session_id is not None
                and view["observation_session_id"] == self._current_session_id
            )
        views.sort(
            key=lambda view: (
                view["recorded_at"] if isinstance(view["recorded_at"], str) else "",
                view["request_id"],
            ),
            reverse=True,
        )
        return views[:limit]

    def detail(
        self,
        request_id: str,
        live_lookup: Callable[[str], dict[str, Any]],
        current_session_id: str,
    ) -> dict[str, Any]:
        grouped, gateway_corrupt = self._read_gateway()
        gateway_records = grouped.get(request_id)
        if not gateway_records:
            raise RequestNotFoundError(request_id)
        engine_records, engine_corrupt = self._engine_store.read_all()
        matching_receipts = [
            record for record in engine_records if record.get("request_id") == request_id
        ]
        final_state = self._request_view(request_id, gateway_records)
        live = live_lookup(request_id)
        live_status = live.get("status")
        attempted = (
            final_state.get("delivery", {}).get("attempted")
            if final_state is not None
            else None
        )
        # The mock engine's live journal only covers its current process, so a
        # live "not_found" is evidence only for requests from this session.
        same_session = any(
            record.get("observation_session_id") == current_session_id
            for record in gateway_records
        )
        if matching_receipts or live_status == "confirmed":
            evidence_state = "received"
            evidence_summary = "엔진 수신 확인"
        elif live_status == "not_found" and same_session and not engine_corrupt:
            evidence_state = "not_received"
            evidence_summary = (
                "Gateway 미전달, 엔진 미수신 확인"
                if attempted is False
                else "Gateway 전달 시도 후 엔진 미수신 확인"
            )
        else:
            evidence_state = "unknown"
            if live_status == "unknown":
                evidence_summary = "엔진 조회 실패로 수신 확인 불가"
            elif not same_session:
                evidence_summary = "이전 관측 세션 요청이라 실시간 조회로 미수신을 확인할 수 없음"
            else:
                evidence_summary = "엔진 수신 기록 손상으로 수신 확인 불가"
        return {
            "request_id": request_id,
            "gateway_records": gateway_records,
            "final_state": final_state,
            "engine_receipts": matching_receipts,
            "engine_live": live,
            "corrupt_lines": {
                "gateway": len(gateway_corrupt),
                "engine": len(engine_corrupt),
            },
            "same_observation_session": same_session,
            "evidence_state": evidence_state,
            "evidence_summary": evidence_summary,
            "generated_at": _utc_now(),
        }
