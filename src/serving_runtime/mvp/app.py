"""Gateway composition and single-process MVP launcher."""

from __future__ import annotations

from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4

from serving_runtime.enforcement.events import ObservationQuery

from .assets import check as check_assets, load_manifest as load_asset_manifest
from .backend import BackendConfig, load_backend_config
from .collectors import (
    Collector,
    CollectorManager,
    MODEL_ID_LIMIT_NOTE,
    compare_baseline,
    load_declaration,
)
from .config import MVPConfig
from .engine_client import EngineClient
from .http_handler import HandlerDependencies, create_handler
from .mock_engine import MockEngine, _utc_now
from .policy import PolicyState, load_policy
from .policy_state import PolicyHistory, evaluate_policy, load_revocations
from .query import QueryService
from .relay import Relay
from .request_flow import ConcurrencyLimiter, RequestFlow
from .request_records import RequestRecords
from .store import JsonlStore


class GatewayApp:
    """A startable local gateway used by both tests and the CLI."""

    def __init__(
        self,
        config: MVPConfig,
        policy_state: PolicyState,
        engine_address: tuple[str, int] | None = None,
        backend_config: BackendConfig | None = None,
    ) -> None:
        if config.host != "127.0.0.1":
            raise ValueError("the MVP gateway must bind to 127.0.0.1")
        self.config = config
        self.policy_state = policy_state
        if config.backend == "mock":
            if backend_config is not None:
                raise ValueError("backend_config must not be supplied in mock mode")
            if engine_address is None:
                raise ValueError("engine_address is required in mock mode")
            self.backend = BackendConfig.mock(engine_address)
        else:
            self.backend = backend_config or load_backend_config(
                str(config.backend_config_path)
            )
            if self.backend.kind != "vllm":
                raise ValueError("vllm mode requires a vllm backend config")

        self.engine_address = (self.backend.target.host, self.backend.target.port)
        self.relay_timeout_s = self.backend.timeouts.get(
            "relay_timeout_s", config.relay_timeout_s
        )
        self.stream_idle_timeout_s = self.backend.timeouts.get(
            "stream_idle_timeout_s", config.stream_idle_timeout_s
        )
        self.request_deadline_s = self.backend.timeouts.get(
            "request_deadline_s", config.request_deadline_s
        )
        self._engine_client = EngineClient(self.backend, self.relay_timeout_s)
        self._relay = Relay(
            self.backend,
            self.relay_timeout_s,
            self.stream_idle_timeout_s,
            self.request_deadline_s,
        )

        # A restart in the same Python process is a distinct observation session.
        self.observation_session_id = uuid4().hex
        self.revocation_state = load_revocations(config.revocations_path)
        self._baseline = load_declaration(config.baseline_path, kind="baseline")
        self._manifest = load_declaration(config.manifest_path, kind="manifest")
        self._policy_history = PolicyHistory(
            Path(config.data_dir) / "policy_history.jsonl"
        )
        self._policy_history.append_startup(
            policy_state, self.observation_session_id
        )

        self._records = RequestRecords(
            config.data_dir,
            self.observation_session_id,
            lambda: self.policy_state,
            self.backend.kind,
        )
        self._engine_store = JsonlStore(
            Path(config.data_dir) / "engine_receipts.jsonl"
        )
        self._observations = ObservationQuery(
            Path(config.data_dir) / "observations.jsonl"
        )
        self._queries = QueryService(
            self._records.store,
            self._engine_store,
            self._records.status,
            self.observation_session_id,
        )
        self._concurrency = ConcurrencyLimiter(config.max_concurrent_requests)
        self._request_flow = RequestFlow(
            self.backend,
            lambda: self.policy_state,
            lambda: self.revocation_state,
            self._records,
            self._engine_client,
            self._relay,
            self._concurrency,
        )

        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._collectors = CollectorManager(
            [
                Collector(
                    "engine_health",
                    self._engine_client.collect_health,
                    config.collector_interval_s,
                    config.collector_stale_after_s,
                ),
                Collector(
                    "engine_info",
                    self._engine_client.collect_info,
                    config.collector_interval_s,
                    config.collector_stale_after_s,
                ),
                Collector(
                    "metrics",
                    (
                        self._engine_client.collect_metrics
                        if self.backend.metrics_target is not None
                        else None
                    ),
                    config.collector_interval_s,
                    config.collector_stale_after_s,
                ),
                Collector(
                    "asset_integrity",
                    (
                        self._collect_asset_integrity
                        if config.asset_manifest_path
                        else None
                    ),
                    config.asset_interval_s,
                    config.asset_interval_s * 3,
                    dedicated_thread=True,
                ),
            ]
        )

    def _collect_asset_integrity(self) -> tuple[dict[str, Any], str]:
        # Keep the module-level name so serving_runtime.mvp.app.check_assets remains
        # the patch point used by the asset collector tests.
        started = time.monotonic()
        manifest = load_asset_manifest(self.config.asset_manifest_path)
        result = check_assets(manifest)
        result["duration_s"] = time.monotonic() - started
        return result, "local_file"

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            raise RuntimeError("gateway is not started")
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def policy_status(self) -> dict[str, Any]:
        policy = self.policy_state.policy
        validity = evaluate_policy(self.policy_state, self.revocation_state)
        history = self._policy_history.latest(50)
        history_storage = self._policy_history.status()
        current = {
            "path": self.policy_state.path,
            "schema_version": policy.schema_version if policy else None,
            "policy_id": policy.policy_id if policy else None,
            "policy_version": policy.policy_version if policy else None,
            "target_id": policy.target_id if policy else None,
            "sha256": self.policy_state.sha256,
            "loaded_at": self.policy_state.loaded_at,
            "valid_from": policy.valid_from if policy else None,
            "valid_until": policy.valid_until if policy else None,
            "approval_ref": policy.approval_ref if policy else None,
            "error_code": self.policy_state.error_code,
            "error_message": self.policy_state.error_message,
        }
        return {
            **validity,
            "current": current,
            "policy": current,
            "revocations": {
                "state": self.revocation_state.state,
                "path": self.revocation_state.path,
                "loaded_at": self.revocation_state.loaded_at,
                "sha256": self.revocation_state.sha256,
                "error_code": self.revocation_state.error_code,
                "error_message": self.revocation_state.error_message,
            },
            "history_storage": history_storage,
            "history": history,
            "implemented_validity_items": [
                "policy schema and fields",
                "valid_from",
                "valid_until",
                "revocation snapshot",
            ],
            "unimplemented_validity_items": [
                "signature verification",
                "approval service integration",
                "hot reload",
                "external enforcement confirmation",
            ],
        }

    def baseline_status(self) -> dict[str, Any]:
        return compare_baseline(self._baseline, self._manifest, self._collectors)

    def collectors_status(self) -> dict[str, Any]:
        return {
            "collectors": self._collectors.snapshots(),
            "generated_at": _utc_now(),
        }

    def status(self) -> dict[str, Any]:
        policy = self.policy_state.policy
        engine = self._engine_client.status()
        record_store = self._records.status()
        policy_runtime = self.policy_status()
        baseline = self.baseline_status()
        collectors = self.collectors_status()
        return {
            "status": (
                "ok"
                if policy_runtime["gateway_active"]
                and engine["healthy"]
                and record_store["ok"]
                and policy_runtime["history_storage"]["ok"]
                else "degraded"
            ),
            "mode": (
                "mock_engine"
                if self.backend.kind == "mock"
                else "external_vllm_backend"
            ),
            "backend_kind": self.backend.kind,
            "observation_session_id": self.observation_session_id,
            "gateway": {"host": self.address[0], "port": self.address[1]},
            "policy": {
                "path": self.policy_state.path,
                "valid": self.policy_state.ok,
                "schema_version": policy.schema_version if policy else None,
                "policy_id": policy.policy_id if policy else None,
                "policy_version": policy.policy_version if policy else None,
                "target_id": policy.target_id if policy else None,
                "sha256": self.policy_state.sha256,
                "loaded_at": self.policy_state.loaded_at,
                "error_code": self.policy_state.error_code,
                "error_message": self.policy_state.error_message,
                "loaded": policy_runtime["loaded"],
                "gateway_active": policy_runtime["gateway_active"],
                "external_enforcement": policy_runtime["external_enforcement"],
                "reason_code": policy_runtime["reason_code"],
            },
            "policy_state": {
                "loaded": policy_runtime["loaded"],
                "gateway_active": policy_runtime["gateway_active"],
                "external_enforcement": policy_runtime["external_enforcement"],
                "reason_code": policy_runtime["reason_code"],
            },
            "policy_history_store": policy_runtime["history_storage"],
            "record_store": record_store,
            "concurrency": self._concurrency.status(),
            "backend": {
                **engine,
                **self.backend.safe_status(),
                "timeouts": {
                    "relay_timeout_s": self.relay_timeout_s,
                    "stream_idle_timeout_s": self.stream_idle_timeout_s,
                    "request_deadline_s": self.request_deadline_s,
                },
            },
            **({"mock_engine": engine} if self.backend.kind == "mock" else {}),
            "baseline": {
                "configured": baseline["baseline"]["state"] != "not_configured",
                "states": {
                    name: sum(
                        1 for row in baseline["rows"] if row["state"] == name
                    )
                    for name in (
                        "match",
                        "drift",
                        "missing",
                        "stale",
                        "error",
                        "not_collected",
                    )
                },
                "model_id_limit": MODEL_ID_LIMIT_NOTE,
            },
            "collectors": {
                item["source"]: item["state"]
                for item in collectors["collectors"]
            },
        }

    def start(self) -> "GatewayApp":
        if self._server is not None:
            raise RuntimeError("gateway is already started")
        handler = create_handler(
            HandlerDependencies(
                chat=self._request_flow.chat,
                status=self.status,
                policy_status=self.policy_status,
                baseline_status=self.baseline_status,
                collectors_status=self.collectors_status,
                queries=self._queries,
                observations=self._observations,
                receipt_lookup=self._engine_client.receipt_lookup,
                observation_session_id=self.observation_session_id,
            )
        )
        self._server = ThreadingHTTPServer(
            (self.config.host, self.config.gateway_port), handler
        )
        self._collectors.start()
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mvp-gateway",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        if server is None:
            self._collectors.stop()
            return
        self._collectors.stop()
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        self._server = None
        self._thread = None

    def __enter__(self) -> "GatewayApp":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def run(config: MVPConfig) -> None:
    """Run the gateway and, only in mock mode, its bundled mock engine."""

    if config.host != "127.0.0.1":
        raise ValueError("the MVP servers must bind to 127.0.0.1")
    policy_state = load_policy(config.policy_path)
    data_dir = Path(config.data_dir)
    engine: MockEngine | None = None
    gateway: GatewayApp | None = None
    try:
        if config.backend == "mock":
            engine = MockEngine(
                port=config.engine_port,
                receipts_path=data_dir / "engine_receipts.jsonl",
            ).start()
            gateway = GatewayApp(config, policy_state, engine.address).start()
        else:
            gateway = GatewayApp(config, policy_state).start()
        gateway_url = f"http://{gateway.address[0]}:{gateway.address[1]}"
        print(f"Gateway: {gateway_url}", flush=True)
        if engine is not None:
            engine_url = f"http://{engine.address[0]}:{engine.address[1]}"
            print(f"Mock engine: {engine_url}", flush=True)
        else:
            print(f"External vLLM backend: {gateway.backend.base_url}", flush=True)
        print(f"Data directory: {data_dir}", flush=True)
        print(f"Browser: {gateway_url}/", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            print("Stopping MVP servers.", flush=True)
    finally:
        if gateway is not None:
            gateway.stop()
        if engine is not None:
            engine.stop()
