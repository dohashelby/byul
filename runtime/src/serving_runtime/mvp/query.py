"""Read-only request and evidence queries over the MVP JSONL journals."""

from collections.abc import Callable
import json
import os
from typing import Any

from .mock_engine import _utc_now
from .store import JsonlStore


_FINAL_RESULTS = ("ALLOWED", "DENIED", "INVALID", "ENGINE_REJECTED", "ERROR")
_FILTER_RESULTS = frozenset((*_FINAL_RESULTS, "INCOMPLETE"))


class QueryError(ValueError):
    """An invalid read-only query parameter."""


class RequestNotFoundError(LookupError):
    """No Gateway record exists for a request ID."""


class QueryWindowIncompleteError(LookupError):
    """A bounded search cannot establish that the requested record is absent."""


class QueryService:
    """Build request-level views without treating individual log lines as requests."""

    def __init__(
        self,
        gateway_store: JsonlStore,
        engine_store: JsonlStore,
        record_store_status: Callable[[], dict[str, Any]],
        current_session_id: str | None = None,
        engine_log_lookup: Callable[[str, str | None], dict[str, Any]] | None = None,
        native_receipt_lookup: Callable | None = None,
        native_receipt_restore: Callable | None = None,
    ) -> None:
        self._engine_log_lookup = engine_log_lookup
        self._gateway_store = gateway_store
        self._engine_store = engine_store
        self._record_store_status = record_store_status
        self._current_session_id = current_session_id
        self._native_receipt_lookup = native_receipt_lookup
        self._native_receipt_restore = native_receipt_restore

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
            "environment": source.get("environment"),
            "run_id": source.get("run_id"),
            "model": request.get("model"),
            "max_tokens": request.get("max_tokens"),
            "policy_profile": (
                source.get("policy", {}).get("profile")
                if isinstance(source.get("policy"), dict)
                else None
            ),
            "extra_fields": (
                request.get("extra_fields")
                if isinstance(request.get("extra_fields"), list)
                else []
            ),
            "cache_isolation": (
                request.get("cache_isolation")
                if isinstance(request.get("cache_isolation"), dict)
                else {"mode": None, "applied": False}
            ),
            "result": result,
            "reason_code": decision.get("reason_code"),
            "http_status": decision.get("http_status") if final is not None else None,
            "source": decision.get("source"),
            "engine_http_status": decision.get("engine_http_status"),
            "stream": source.get("stream"),
            "http_start_status": source.get("http_start_status"),
            "final_outcome": source.get("final_outcome"),
            "response_started": source.get("response_started"),
            "backend_cancel": source.get("backend_cancel"),
            "backend_kind": source.get("backend_kind", "mock"),
            "delivery": {
                "attempted": delivery.get("attempted") if final is not None else None,
                "backend_receipt": delivery.get("backend_receipt"),
                "response": delivery.get("response"),
                "error_code": delivery.get("error_code"),
            },
        }

    def _read_gateway(
        self,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        grouped, corrupt, _ = self._read_gateway_window()
        return grouped, corrupt

    @staticmethod
    def _normalize(records):
        normalized: list[dict[str, Any]] = []
        for record in records:
            item = dict(record)
            policy = item.get("policy")
            if isinstance(policy, dict):
                policy = dict(policy)
                policy.setdefault("schema_version", None)
                item["policy"] = policy
            normalized.append(item)
        return normalized

    def _read_gateway_window(self):
        records, corrupt, truncated = self._gateway_store.read_recent()
        return self._group(self._normalize(records)), corrupt, truncated

    @staticmethod
    def _window(truncated, total):
        return {'truncated': bool(truncated), 'counts_lower_bound': bool(truncated),
                'total_lower_bound': total, 'max_bytes': 4 * 1024 * 1024, 'max_records': 4096}

    @staticmethod
    def _matching_records(store, request_id):
        return store.read_matching_request(request_id)

    def summary(self) -> dict[str, Any]:
        grouped, gateway_corrupt, gateway_truncated = self._read_gateway_window()
        _, engine_corrupt, engine_truncated = self._engine_store.read_recent()
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
            "window": self._window(gateway_truncated or engine_truncated or bool(gateway_corrupt or engine_corrupt), sum(counts.values()) + incomplete),
        }

    def requests(
        self, *, result: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        return self.request_page(result=result, limit=limit)['requests']

    def request_page(self, *, result=None, limit=200, include_records=False):
        if result is not None and result not in _FILTER_RESULTS:
            raise QueryError("invalid result filter")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise QueryError("limit must be an integer from 1 through 1000")
        if not isinstance(include_records, bool):
            raise QueryError("include_records must be a boolean")
        if include_records and limit > 200:
            raise QueryError("include_records limit must not exceed 200")
        grouped, corrupt, truncated = self._read_gateway_window()
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
        window = self._window(truncated or bool(corrupt), len(views))
        window['truncated'] = window['truncated'] or len(views) > limit
        selected = views[:limit]
        payload = {'requests': selected, 'window': window, 'generated_at': _utc_now()}
        if include_records:
            # Same authorized, bounded journal snapshot as the list. Never fall
            # back to full per-request scans or expand the current window.
            payload['gateway_records'] = {view['request_id']: grouped[view['request_id']]
                                          for view in selected}
        return payload

    def detail(
        self,
        request_id: str,
        live_lookup: Callable[[str], dict[str, Any]],
        current_session_id: str,
    ) -> dict[str, Any]:
        gateway_records, gateway_corrupt, gateway_truncated = self._matching_records(self._gateway_store, request_id)
        gateway_records = self._normalize(gateway_records)
        if not gateway_records:
            if gateway_truncated or gateway_corrupt:
                raise QueryWindowIncompleteError(request_id)
            raise RequestNotFoundError(request_id)
        engine_records, engine_corrupt, engine_truncated = self._matching_records(self._engine_store, request_id)
        final_state = self._request_view(request_id, gateway_records)
        backend_kind = (
            final_state.get("backend_kind", "mock")
            if final_state is not None
            else gateway_records[-1].get("backend_kind", "mock")
        )
        matching_receipts = (
            [
                record
                for record in engine_records
                if record.get("request_id") == request_id and record.get("kind") != "source_qualified_native_receipt"
            ]
            if backend_kind == "mock"
            else []
        )
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
        if backend_kind != "mock":
            live = {
                "status": "not_available",
                "queried_at": None,
                "error": None,
                "evidence_kind": "not_connected",
            }
            evidence_state = "gateway_only"
            response = (
                final_state.get("delivery", {}).get("response")
                if final_state is not None
                else None
            )
            response_id = (
                final_state.get("delivery", {}).get("backend_receipt")
                if final_state is not None
                else None
            )
            started = next(
                (r.get("recorded_at") for r in gateway_records if isinstance(r.get("recorded_at"), str)),
                None,
            )
            log = (
                self._engine_log_lookup(request_id, started)
                if self._engine_log_lookup is not None and attempted is True
                else None
            )
            if log is not None and log.get("status") != "not_configured":
                live = {
                    "status": f"engine_log_{log['status']}",
                    "queried_at": log.get("collected_at"),
                    "error": None,
                    "evidence_kind": "engine_log",
                }
            if final_state is not None and final_state.get('result') == 'INCOMPLETE':
                evidence_summary = '최종 기록이 없어 Gateway 전달 여부 미확인'
            elif attempted is False:
                evidence_summary = (
                    "Gateway 미전달 확인, backend 미수신은 미확인(증거원 미연결)"
                )
            elif attempted is True and response == "completed":
                evidence_summary = (
                    "Gateway 전달·응답 수신, backend 측 수신 기록 미연결"
                )
            else:
                evidence_summary = (
                    "Gateway 전달 시도, backend 미수신 여부는 미확인(증거원 미연결)"
                )
            if log is not None and log.get("status") == "observed":
                # Engine-side evidence: the forwarded ID appeared in the engine's own log.
                matching_receipts = [{
                    "request_id": request_id,
                    "received_at": log.get("observed_at"),
                    "evidence_kind": "engine_log",
                }]
                evidence_state = "received"
                evidence_summary = "엔진 로그에서 이 요청 ID 확인"
            elif log is not None and log.get("status") == "not_observed":
                # Logs may omit requests (logging options), so this is never "not_received".
                evidence_summary = (
                    "엔진 로그 수집 구간 안에서 이 요청 ID 미발견(로그 설정상 미수신 단정 불가)"
                )
            elif response_id == "engine_response_id_matched":
                evidence_summary = "엔진 응답 ID가 Gateway 요청 ID와 일치(엔진 로그 근거는 미연결·미확인)"
            if self._native_receipt_restore is not None and engine_records:
                self._native_receipt_restore(engine_records)
            native = self._native_receipt_lookup(gateway_records,request_id,current_session_id) if self._native_receipt_lookup is not None else None
            if native is None and any(row.get('kind') == 'source_qualified_native_receipt' for row in engine_records):
                evidence_state = 'unknown'
                evidence_summary = '보존된 native 근거의 현재 판본·요청 문맥 검증 미확인'
            if native is not None:
                matching_receipts = [native,*native.pop('historical_related_receipts',[])]
                projection = native['projection'];facts=projection.get('native_projection')or{}
                physical = (facts.get('anchor')or{}).get('physical_process_binding_verified')is True
                measured = projection['source_data_origin'] == 'MEASURED' and (physical or backend_kind=='sglang' and facts.get('native_termination_verified')is True)
                evidence_state = 'received' if measured else 'unknown'
                historical = native.get('capture_state')=='closed' or native.get('historical_receipt') is True
                checkpoint = projection.get('closure_basis') == 'live_checkpoint' and projection.get('checkpoint_coverage_verified') is True
                evidence_summary = ('과거 native 요청 근거, 현재 센서 감시 상태는 별도' if historical else 'native 요청 checkpoint·범위별 종결 근거' if checkpoint else '미봉인 native 요청 관측, 종결·향후 감시는 미확인')+' ('+projection['source_data_origin']+')'
                live = {'status':'not_available' if historical else 'confirmed' if measured else 'unknown','queried_at':None,'error':None,'evidence_kind':'source_qualified_native_history' if historical else 'source_qualified_native_projection'}
        else:
            live = live_lookup(request_id)
            live_status = live.get("status")
            if matching_receipts or live_status == "confirmed":
                evidence_state = "received"
                evidence_summary = "엔진 수신 확인"
            elif live_status == "not_found" and same_session and not engine_corrupt and not engine_truncated and final_state is not None and final_state.get('result') != 'INCOMPLETE':
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
        if gateway_truncated or engine_truncated:
            evidence_summary += ' · 최근 기록 범위의 근거이며 이전 기록 일부는 조회 범위 밖'
        return {
            "request_id": request_id,
            "backend_kind": backend_kind,
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
            "window": {'truncated': gateway_truncated or engine_truncated,
                       'gateway_search_complete': not gateway_truncated and not gateway_corrupt,
                       'engine_search_complete': not engine_truncated and not engine_corrupt,
                       'max_search_bytes': 64 * 1024 * 1024},
        }
