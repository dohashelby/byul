"""Gateway composition and single-process MVP launcher."""

from __future__ import annotations

import copy
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4

from serving_runtime.enforcement.events import ObservationQuery
from serving_runtime.enforcement.intent import load_intent

from .assets import check as check_assets, load_manifest as load_asset_manifest
from .backend import BackendConfig, load_backend_config
from .collectors import (
    Collector,
    CollectorManager,
    MODEL_ID_LIMIT_NOTE,
    compare_baseline,
    load_declaration,
)
from .cilium_status import CiliumPolicyCollector
from .kube_context import DEFAULT_KUBE_CONTEXT
from .config import MVPConfig
from .engine_client import EngineClient
from .http_handler import (
    HandlerDependencies,
    LimitedThreadingHTTPServer,
    create_handler,
)
from .lab_report import LabReportSource
from .mock_engine import MockEngine, _utc_now
from .observation_feed import ObservationFeed
from serving_runtime.enforcement.execpolicy import ExecPolicy

from .approval import (
    ApprovalResult,
    ApprovalVerifier,
    HashListApprovalVerifier,
    unavailable_policy_result,
)
from .auth import load_auth
from .target import KubernetesTargetCollector, TargetRegistry, load_target
from .policy import PolicyState, load_policy
from .policy_state import PolicyHistory, evaluate_policy, load_revocations
from .query import QueryService
from .relay import Relay
from .retention import RecordRetention
from .revalidation import RevalidationRequests
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
        approval_verifier: ApprovalVerifier | None = None,
    ) -> None:
        if config.host != "127.0.0.1":
            raise ValueError("the MVP gateway must bind to 127.0.0.1")
        self.config = config
        self.policy_state = policy_state
        self._policy_bundle = copy.deepcopy(config.policy_bundle)
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
            if self.backend.kind != config.backend:
                raise ValueError(
                    f"{config.backend} mode requires a {config.backend} backend config"
                )
        allowed_binaries: tuple[str, ...] = ()
        self._exec_policy: ExecPolicy | None = None
        if config.observation_intent_path is not None:
            intent = load_intent(config.observation_intent_path)
            allowed_binaries = intent.allowed_engine_binaries
            if intent.schema != "runtime-enforcement-intent/1.0":
                self._exec_policy = ExecPolicy(
                    intent,
                    exceptions_path=config.observation_exceptions_path,
                    timeline_path=config.observation_timeline_path,
                )
        elif config.observation_exceptions_path or config.observation_timeline_path:
            raise ValueError("observation exceptions/timeline need an observation intent")

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
        self._approval_verifier: ApprovalVerifier | None = approval_verifier
        if self._approval_verifier is None and config.policy_approvals_path is not None:
            self._approval_verifier = HashListApprovalVerifier(
                config.policy_approvals_path
            )
        self.auth_state = load_auth(config.auth_config_path)
        self._baseline = load_declaration(config.baseline_path, kind="baseline")
        self._manifest = load_declaration(config.manifest_path, kind="manifest")
        self._policy_history = PolicyHistory(
            Path(config.data_dir) / "policy_history.jsonl"
        )
        self._revalidation = RevalidationRequests(config.data_dir)
        startup_approval = self._approval_result()
        policy_history_record = self._policy_history.append_startup(
            policy_state, self.observation_session_id, startup_approval
        )
        if policy_history_record is None:
            self._revalidation.record_evaluation_error(
                "policy_history", RuntimeError("startup policy history was not stored")
            )
        else:
            self._revalidation.observe_policy_history(
                policy_history_record, **self._revalidation_context()
            )

        self._target = TargetRegistry(
            load_target(config.target_path),
            data_dir=config.data_dir,
            observation_session_id=self.observation_session_id,
        )
        self._records = RequestRecords(
            config.data_dir,
            self.observation_session_id,
            lambda: self.policy_state,
            self.backend.kind,
            config.environment,
            config.run_id,
            target_context=self._target.record_context,
        )
        self._engine_store = JsonlStore(
            Path(config.data_dir) / "engine_receipts.jsonl"
        )
        self._observations = ObservationQuery(
            Path(config.data_dir) / "observations.jsonl"
        )
        self._lab_report = LabReportSource(config.lab_report_path)
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
            lambda: self.auth_state,
            self._target.gateway_decision,
            approval_result=self._approval_result,
        )

        self._server: LimitedThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._retention = RecordRetention(
            config.data_dir,
            max_age_days=config.retention_max_age_days,
            max_bytes=config.retention_max_bytes,
            enforce=config.retention_enforce,
            hold_path=config.retention_hold_path,
        )
        collectors = [
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
                on_collected=self._evaluate_baseline_revalidation,
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
                "record_retention",
                self._retention.collect,
                config.retention_interval_s,
                config.retention_interval_s * 3,
                dedicated_thread=True,
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
                on_collected=self._evaluate_asset_revalidation,
            ),
        ]
        self._observation_feeds: list[ObservationFeed] = []
        observation_heartbeats = dict(config.observation_heartbeats)
        for kind, path in config.observation_feeds:
            feed = ObservationFeed(
                kind=kind,
                path=path,
                data_dir=config.data_dir,
                data_origin=config.observation_origin,
                environment=config.observation_environment,
                run_id=config.observation_run_id,
                allowed_engine_binaries=allowed_binaries,
                heartbeat_path=observation_heartbeats.get(kind),
                heartbeat_stale_after_s=config.collector_stale_after_s,
                exec_policy=self._exec_policy,
            )
            self._observation_feeds.append(feed)
            collectors.append(
                Collector(
                    f"observation_feed_{kind}",
                    feed.collect,
                    config.collector_interval_s,
                    config.collector_stale_after_s,
                )
            )
        registration = self._target.registration
        registered_context = (
            registration.kubernetes.context if registration.kubernetes is not None else None
        )
        # The target registration names the cluster it was approved for. An
        # explicit --kube-context must agree with it instead of silently
        # pointing the collectors at another cluster.
        if (
            config.kube_context is not None
            and registered_context is not None
            and config.kube_context != registered_context
        ):
            raise ValueError(
                f"--kube-context {config.kube_context!r}가 대상 등록의 "
                f"kubernetes.context {registered_context!r}와 다릅니다"
            )
        kube_context = config.kube_context or registered_context or DEFAULT_KUBE_CONTEXT
        self._cilium_policy_collector: CiliumPolicyCollector | None = None
        if config.cilium_policy_status:
            self._cilium_policy_collector = CiliumPolicyCollector(
                timeout_s=config.cilium_timeout_s,
                context=kube_context,
            )
            collectors.append(
                Collector(
                    "cilium_policy",
                    self._cilium_policy_collector.collect,
                    config.collector_interval_s,
                    config.collector_stale_after_s,
                )
            )
        if config.target_pod_status and registration.kubernetes is not None:
            collectors.append(
                Collector(
                    "target_pods",
                    KubernetesTargetCollector(
                        registration.kubernetes,
                        timeout_s=config.cilium_timeout_s,
                    ).collect,
                    config.collector_interval_s,
                    config.collector_stale_after_s,
                    on_collected=self._target.observe_kubernetes,
                )
            )
        self._collectors = CollectorManager(collectors)

    def _approval_result(self) -> ApprovalResult | None:
        if self._approval_verifier is None:
            return None
        policy = self.policy_state.policy
        raw = self.policy_state.raw_bytes
        if not self.policy_state.ok or policy is None or raw is None:
            return unavailable_policy_result()
        return self._approval_verifier.verify(
            raw,
            policy.policy_id,
            policy.policy_version,
            policy.approval_ref,
        )

    def _revalidation_context(self) -> dict[str, Any]:
        policy = self.policy_state.policy
        return {
            "observation_session_id": self.observation_session_id,
            "target_id": policy.target_id if policy else None,
            "policy": {
                "policy_id": policy.policy_id if policy else None,
                "policy_version": policy.policy_version if policy else None,
                "sha256": self.policy_state.sha256,
            },
        }

    def _evaluate_asset_revalidation(
        self, result: Any, data_origin: str | None
    ) -> None:
        if not isinstance(result, dict):
            self._revalidation.record_evaluation_error(
                "asset_integrity", ValueError("asset collector result is not an object")
            )
            return
        self._revalidation.observe_asset(
            result, data_origin=data_origin, **self._revalidation_context()
        )

    def _evaluate_baseline_revalidation(
        self, _result: Any, _data_origin: str | None
    ) -> None:
        try:
            comparison = compare_baseline(
                self._baseline, self._manifest, self._collectors
            )
        except Exception as error:
            self._revalidation.record_evaluation_error("baseline", error)
            return
        self._revalidation.observe_baseline(
            comparison, **self._revalidation_context()
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

    @staticmethod
    def _profile_status(policy: Any) -> dict[str, Any]:
        if policy is None:
            return {
                "profile": None,
                "test_only": False,
                "max_n": None,
                "extra_fields": [],
                "passthrough_count": None,
                "validate_count": None,
            }
        extra = [{"name": rule.name, "mode": rule.mode} for rule in policy.extra_fields]
        return {
            "profile": policy.profile,
            "test_only": policy.profile == "zeroday-lab",
            "max_n": policy.max_n,
            "extra_fields": extra,
            "passthrough_count": sum(1 for item in extra if item["mode"] == "passthrough"),
            "validate_count": sum(1 for item in extra if item["mode"] == "validate"),
        }

    def target_status(self) -> dict[str, Any]:
        collectors = self.collectors_status()
        by_source = {item["source"]: item for item in collectors.get("collectors", [])}
        pods = by_source.get("target_pods")
        policy = self.policy_state.policy
        return self._target.status(
            policy_target_id=policy.target_id if policy else None,
            backend_kind=self.backend.kind,
            engine_info=by_source.get("engine_info"),
            kubernetes=(pods.get("last_value") if pods and pods.get("state") == "ok" else (
                {"state": pods.get("state"), "error": pods.get("last_error")} if pods else None
            )),
        )

    def policy_status(self) -> dict[str, Any]:
        policy = self.policy_state.policy
        validity = evaluate_policy(
            self.policy_state,
            self.revocation_state,
            approval=self._approval_result(),
        )
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
            **self._profile_status(policy),
            "error_code": self.policy_state.error_code,
            "error_message": self.policy_state.error_message,
        }
        return {
            **validity,
            "policy_bundle": copy.deepcopy(self._policy_bundle),
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
                "LAB approval-list SHA-256 comparison",
            ],
            "unimplemented_validity_items": [
                "production signature verification",
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

    def revalidation_status(self) -> dict[str, Any]:
        return self._revalidation.summary()

    def status(self) -> dict[str, Any]:
        policy = self.policy_state.policy
        engine = self._engine_client.status()
        record_store = self._records.status()
        policy_runtime = self.policy_status()
        baseline = self.baseline_status()
        collectors = self.collectors_status()
        revalidation = self.revalidation_status()
        target = self.target_status()
        return {
            "status": (
                "ok"
                if policy_runtime["gateway_active"]
                and engine["healthy"]
                and record_store["ok"]
                and policy_runtime["history_storage"]["ok"]
                and revalidation["evaluation_status"]["ok"]
                and revalidation["storage"]["ok"]
                and self.auth_state.state != "error"
                and target["gateway_hold"] is None
                else "degraded"
            ),
            "auth": self.auth_state.status(),
            "target": {
                "registration": target["registration"]["state"],
                "target_id": target["registration"]["target_id"],
                "target_ref": target["registration"]["target_ref"],
                "error_code": target["registration"]["error_code"],
                "gateway_hold": target["gateway_hold"],
                "counts": target["counts"],
                "instance": target["instance"],
            },
            "mode": (
                "mock_engine"
                if self.backend.kind == "mock"
                else f"external_{self.backend.kind}_backend"
            ),
            "backend_kind": self.backend.kind,
            "observation_session_id": self.observation_session_id,
            "environment": self.config.environment,
            "run_id": self.config.run_id,
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
                **self._profile_status(policy),
                "error_code": self.policy_state.error_code,
                "error_message": self.policy_state.error_message,
                "loaded": policy_runtime["loaded"],
                "gateway_active": policy_runtime["gateway_active"],
                "external_enforcement": policy_runtime["external_enforcement"],
                "reason_code": policy_runtime["reason_code"],
                "approval": policy_runtime["approval"],
            },
            "policy_state": {
                "loaded": policy_runtime["loaded"],
                "gateway_active": policy_runtime["gateway_active"],
                "external_enforcement": policy_runtime["external_enforcement"],
                "reason_code": policy_runtime["reason_code"],
                "approval": policy_runtime["approval"],
            },
            "policy_bundle": copy.deepcopy(self._policy_bundle),
            "policy_history_store": policy_runtime["history_storage"],
            "record_store": record_store,
            "concurrency": self._concurrency.status(),
            "connections": {
                **(
                    self._server.connection_status()
                    if self._server is not None
                    else {
                        "active": 0,
                        "limit": self.config.max_connections,
                        "rejected": 0,
                    }
                ),
                "request_header_timeout_s": self.config.request_header_timeout_s,
                "request_body_timeout_s": self.config.request_body_timeout_s,
            },
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
            "revalidation": {
                "open_count": revalidation["open_count"],
                "current_condition_count": revalidation["current_condition_count"],
                "evaluation_state": revalidation["evaluation_status"]["state"],
                "storage_state": revalidation["storage"]["state"],
            },
        }

    def start(self) -> "GatewayApp":
        if self._server is not None:
            raise RuntimeError("gateway is already started")
        handler = create_handler(
            HandlerDependencies(
                chat=self._request_flow.chat,
                completions=self._request_flow.completions,
                status=self.status,
                policy_status=self.policy_status,
                baseline_status=self.baseline_status,
                collectors_status=self.collectors_status,
                revalidation_status=self.revalidation_status,
                queries=self._queries,
                observations=self._observations,
                receipt_lookup=self._engine_client.receipt_lookup,
                observation_session_id=self.observation_session_id,
                lab_report=self._lab_report.read,
                record_transport_error=self._request_flow.record_without_relay,
                request_header_timeout_s=self.config.request_header_timeout_s,
                request_body_timeout_s=self.config.request_body_timeout_s,
                target_status=self.target_status,
                authorize_read=lambda header: self.auth_state.authorize_read(header),
            )
        )
        self._server = LimitedThreadingHTTPServer(
            (self.config.host, self.config.gateway_port),
            handler,
            max_connections=self.config.max_connections,
            on_connection_rejected=self._request_flow.record_without_relay,
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
            backend_name = "vLLM" if gateway.backend.kind == "vllm" else "SGLang"
            print(f"External {backend_name} backend: {gateway.backend.base_url}", flush=True)
        print(f"Data directory: {data_dir}", flush=True)
        print(f"Query API: {gateway_url}/api/ (화면: byul platform, RUNTIME_BASE_URL={gateway_url})", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            print("Stopping MVP servers.", flush=True)
    finally:
        if gateway is not None:
            gateway.stop()
        if engine is not None:
            engine.stop()
