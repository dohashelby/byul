"""WP5: constructor shape, ordered effects, failures and partial state files."""

from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import UUID

import pytest

from serving_runtime.gateway import app as module
from serving_runtime.config.config import MVPConfig
from serving_runtime.gateway.backend import BackendConfig
from serving_runtime.policy.policy import load_policy
from serving_runtime.policy.approval import ApprovalResult


AT = "2026-01-02T03:04:05+00:00"


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class TestVerifier:
    __test__ = False

    def verify(self, *args, **kwargs):
        return ApprovalResult("verified", AT, "test", "TEST_APPROVED", "approved", {})


def configurations():
    cases = {
        "mock-minimal": {},
        "declarations": {"declarations": True},
        "v10": {"intent": "1.0"},
        "v11": {"intent": "1.1"},
        "external-loaded": {"backend": "vllm", "load_backend": True},
        "external-supplied": {"backend": "sglang"},
        "external-timeouts-metrics": {"backend": "vllm", "timeouts": True, "metrics": True},
        "supplied-verifier": {"verifier": True},
        "file-verifier": {"approvals": True},
        "supplied-wins-file": {"verifier": True, "approvals": True},
        "history-not-stored": {"history_none": True},
        "no-policy": {"no_policy": True},
        "feeds": {"feeds": True},
        "native-cache": {"native": True, "cache": True, "target": True},
        "explicit-context": {"context": "test-context"},
        "tetragon-file-context": {"tetragon": True, "context": "test-context"},
        "tetragon-default-file": {"default_tetragon": True, "context": "test-context"},
        "tetragon-no-context": {"tetragon": True},
        "default-tetragon-no-context": {"default_tetragon": True},
        "target-context": {"target": True},
        "target-explicit-match": {"target": True, "context": "test-context"},
        "target-context-mismatch": {"target": True, "context": "other-context"},
        "snapshot-no-target": {"snapshot": True},
        "snapshot-target-no-kube": {"snapshot": True, "target": True, "no_kube": True},
        "snapshot-invalid-target": {"snapshot": True, "target": True, "bad_target": True},
        "status-no-target": {"target_status": True},
        "status-target-no-kube": {"target_status": True, "target": True, "no_kube": True},
        "status-target": {"target_status": True, "target": True},
        "snapshot-target": {"snapshot": True, "target": True},
        "cilium-no-context": {"cilium": True},
        "cilium-no-namespace": {"cilium": True, "context": "test-context"},
        "cilium-target": {"cilium": True, "target": True},
        "all-collectors": {
            "target": True,
            "target_status": True,
            "cilium": True,
            "native": True,
            "cache": True,
            "feeds": True,
            "tetragon": True,
            "intent": "1.1",
            "verifier": True,
            "asset": True,
        },
        "host-invalid": {"host": "0.0.0.0"},
        "mock-config-invalid": {"mock_config": True},
        "mock-address-missing": {"no_address": True},
        "backend-mismatch": {"backend": "vllm", "mismatch": True},
        "exceptions-without-intent": {"exceptions": True},
        "timeline-without-intent": {"timeline": True},
        "exceptions-and-timeline": {"exceptions": True, "timeline": True},
        "intent-missing": {"missing_intent": True},
        "intent-malformed": {"bad_intent": True},
        "bad-feed": {"bad_feed": True},
    }
    errors = [
        ("host", {"host": "0.0.0.0"}),
        ("mock-config", {"mock_config": True}),
        ("mock-address", {"no_address": True}),
        ("intent", {"missing_intent": True}),
        ("exception", {"exceptions": True}),
        ("snapshot", {"snapshot": True}),
        ("tetragon", {"tetragon": True}),
        ("cilium", {"cilium": True}),
    ]
    for i, (first, a) in enumerate(errors):
        for second, b in errors[i + 1 :]:
            cases["priority:" + first + "+" + second] = {**a, **b}
    for variant in ("matching", "bad-state", "no-policy", "wrong-id", "wrong-version", "wrong-sha", "not-applied"):
        cases["bundle:" + variant] = {"intent": "1.1", "bundle": variant}
    return dict(sorted(cases.items()))


def normalize(value, root):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (str, Path)):
        return str(value).replace(str(root), "<CASE_ROOT>")
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, (list, tuple)):
        return [normalize(item, root) for item in value]
    if isinstance(value, dict):
        return {str(normalize(key, root)): normalize(item, root) for key, item in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    if callable(value) and hasattr(value, "__name__"):
        return {"callable": value.__name__}
    return {"type": type(value).__name__}


def prepare(root, options):
    root.mkdir(parents=True, exist_ok=True)
    data = root / "state"
    policy_file = root / "policy.json"
    policy_file.write_text(
        json.dumps(
            {
                "schema_version": "runtime-mvp/1.0",
                "policy_id": "test-policy",
                "policy_version": "1",
                "target_id": "test-target",
                "allowed_models": ["contract-model"],
                "output_default_tokens": 16,
                "output_max_tokens": 128,
                "max_body_bytes": 4096,
            }
        )
    )
    policy = replace(load_policy(policy_file), path="/srv/test/policy.json", loaded_at=AT)
    if options.get("no_policy") or options.get("bundle") == "no-policy":
        policy = replace(policy, ok=False, policy=None)
    if options.get("bundle") == "bad-state":
        policy = replace(policy, ok=False)
    backend = replace(BackendConfig.mock(("127.0.0.1", 18088)), kind=options.get("backend", "mock"))
    if options.get("mismatch"):
        backend = replace(backend, kind="sglang")
    if options.get("timeouts"):
        backend = replace(
            backend,
            timeouts={
                "relay_timeout_s": 1.25,
                "status_timeout_s": 2.5,
                "stream_idle_timeout_s": 3.75,
                "request_deadline_s": 10.0,
            },
        )
    if options.get("metrics"):
        backend = replace(backend, metrics_target=backend.target, metrics_path="/metrics")
    config = {
        "data_dir": str(data),
        "policy_path": str(policy_file),
        "environment": "test",
        "run_id": "test-run",
        "backend": options.get("backend", "mock"),
        "gateway_port": 0,
        "kube_context": options.get("context"),
    }
    if config["backend"] != "mock":
        path = root / "backend.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "runtime-backend/1.0",
                    "kind": config["backend"],
                    "base_url": "http://127.0.0.1:18088",
                    "model_map": {"contract-model": "contract-model"},
                }
            )
        )
        config["backend_config_path"] = str(path)
    if options.get("intent"):
        intent = {
            "schema": "runtime-enforcement-intent/" + options["intent"],
            "namespace": "test-ns",
            "gateway": {"labels": {"app": "test-gateway"}},
            "engine": {"labels": {"app": "test-engine"}},
            "metrics_collectors": [],
            "protected_paths": {"model_dir": "/srv/test/model", "cache_dir": "/srv/test/cache", "secret_paths": []},
            "allowed_engine_binaries": ["/opt/test/bin/engine"],
        }
        path = root / "intent.json"
        path.write_text(json.dumps(intent))
        config["observation_intent_path"] = str(path)
    if options.get("missing_intent") or options.get("bad_intent"):
        path = root / "bad-intent.json"
        if options.get("bad_intent"):
            path.write_text("{")
        config["observation_intent_path"] = str(path)
    for option, field in [
        ("exceptions", "observation_exceptions_path"),
        ("timeline", "observation_timeline_path"),
        ("approvals", "policy_approvals_path"),
        ("native", "native_receipt_feed_path"),
        ("cache", "cache_observation_path"),
        ("asset", "asset_manifest_path"),
        ("tetragon", "tetragon_state_path"),
        ("snapshot", "target_pod_snapshot"),
    ]:
        if options.get(option):
            config[field] = str(root / (option + ".json"))
    if options.get("default_tetragon"):
        data.mkdir()
        (data / "tetragon-state.json").write_text("{}")
    if options.get("feeds") or options.get("bad_feed"):
        kinds = ["hubble", "tetragon", "management_listener"] if options.get("feeds") else ["invalid"]
        config["observation_feeds"] = tuple((kind, str(root / (kind + ".jsonl"))) for kind in kinds)
        config["observation_heartbeats"] = (("tetragon", str(root / "heartbeat.json")),)
    if options.get("target"):
        target = {
            "schema_version": "runtime-target/1.0",
            "target_id": "test-target",
            "target_ref": "test-ref",
            "engine": {"kind": "vllm", "version": "test-version"},
            "model": {"id": "contract-model"},
        }
        if not options.get("no_kube"):
            target["kubernetes"] = {
                "context": "test-context",
                "namespace": "test-ns",
                "selector": {"app": "test-engine"},
                "container": "test-engine",
            }
        path = root / "target.json"
        path.write_text("{}" if options.get("bad_target") else json.dumps(target))
        config["target_path"] = str(path)
    config["cilium_policy_status"] = options.get("cilium", False)
    config["target_pod_status"] = options.get("target_status", False)
    if options.get("bundle"):
        variant = options["bundle"]
        config["policy_bundle"] = {
            "bundle_id": "test-bundle",
            "policy_ref": {
                "policy_id": "other" if variant == "wrong-id" else "test-policy",
                "policy_version": "other" if variant == "wrong-version" else "1",
            },
            "files": [
                {"role": "gateway_policy", "sha256": "0" * 64 if variant == "wrong-sha" else policy.sha256},
                {"role": "enforcement_intent", "applied": variant != "not-applied"},
            ],
        }
    if config.get("observation_intent_path") and not config.get("observation_feeds"):
        config["observation_feeds"] = (("tetragon", str(root / "tetragon.jsonl")),)
    if options.get("native"):
        config.update(native_receipt_reader_gid=1000, native_receipt_producer_sha256="a" * 64)
    if options.get("cache"):
        config.update(
            cache_observation_reader_gid=1000,
            cache_observation_producer_sha256="b" * 64,
            cache_observation_binding_sha256="c" * 64,
        )
    if options.get("declarations"):
        config.update(baseline_path=str(root / "baseline.json"), manifest_path=str(root / "manifest.json"))
    if options.get("bad_feed"):
        config["observation_feeds"] = ()
        config["observation_heartbeats"] = ()
    result = MVPConfig(**config)
    if options.get("bad_feed"):
        object.__setattr__(result, "observation_feeds", (("invalid", str(root / "invalid.jsonl")),))
    if options.get("host"):
        object.__setattr__(result, "host", options["host"])
    kwargs = {"engine_address": None if options.get("no_address") else ("127.0.0.1", 18088), "cache_salt_environ": {}}
    if options.get("mock_config") or (config["backend"] != "mock" and not options.get("load_backend")):
        kwargs["backend_config"] = backend
    if options.get("verifier"):
        kwargs["approval_verifier"] = TestVerifier()
    return result, policy, kwargs


def observe(root, options):
    config, policy, kwargs = prepare(root, options)
    events = []
    app = module.GatewayApp.__new__(module.GatewayApp)

    def wrapped(name, original, fixed_none=False):
        def call(*args, **kwargs):
            events.append([name, normalize(args, root), normalize(kwargs, root)])
            return None if fixed_none else original(*args, **kwargs)

        return call

    with ExitStack() as stack:
        # Freeze imported clock/UUID names; constructors still execute their real storage logic.
        for loaded in list(sys.modules.values()):
            if loaded is None or not getattr(loaded, "__name__", "").startswith("serving_runtime."):
                continue
            if hasattr(loaded, "_utc_now"):
                stack.enter_context(patch.object(loaded, "_utc_now", lambda: AT))
            if hasattr(loaded, "uuid4"):
                stack.enter_context(patch.object(loaded, "uuid4", lambda: UUID(int=1)))
            if hasattr(loaded, "datetime"):
                stack.enter_context(patch.object(loaded, "datetime", FrozenDatetime))
        for name in [
            "load_backend_config",
            "load_intent",
            "render_monitor_manifests",
            "ExecPolicy",
            "EngineClient",
            "Relay",
            "load_revocations",
            "HashListApprovalVerifier",
            "load_auth",
            "load_declaration",
            "PolicyHistory",
            "RevalidationRequests",
            "load_target",
            "TargetRegistry",
            "RequestRecords",
            "RouteRejections",
            "JsonlStore",
            "ObservationQuery",
            "EngineReceipts",
            "QueryService",
            "ConcurrencyLimiter",
            "RequestFlow",
            "RecordRetention",
            "Collector",
            "ObservationFeed",
            "validate_kube_context",
            "CiliumPolicyCollector",
            "KubernetesTargetCollector",
            "TargetPodSnapshotCollector",
            "CollectorManager",
        ]:
            original = getattr(module, name)
            stack.enter_context(patch.object(module, name, wrapped(name, original)))
        # Patch the defining modules of lazy imports; they remain lazy in the product.
        from serving_runtime.devtools import lab_report
        from serving_runtime.observation import native_receipts, engine_health_events, cache_observation

        for owner, name in [
            (lab_report, "LabReportSource"),
            (native_receipts, "NativeReceiptFeed"),
            (engine_health_events, "EngineHealthEvents"),
            (cache_observation, "CacheObservationReader"),
        ]:
            stack.enter_context(patch.object(owner, name, wrapped(name, getattr(owner, name))))
        from serving_runtime.policy.policy_state import PolicyHistory
        from serving_runtime.policy.revalidation import RevalidationRequests

        for owner, name in [
            (PolicyHistory, "append_startup"),
            (RevalidationRequests, "observe_policy_history"),
            (RevalidationRequests, "record_evaluation_error"),
        ]:
            original = getattr(owner, name)
            stack.enter_context(
                patch.object(
                    owner,
                    name,
                    wrapped(name, original, options.get("history_none", False) and name == "append_startup"),
                )
            )
        try:
            module.GatewayApp.__init__(app, config, policy, **kwargs)
            error = None
        except Exception as failure:
            error = [
                type(failure).__name__,
                normalize(str(failure), root),
                None
                if failure.__cause__ is None
                else [type(failure.__cause__).__name__, normalize(str(failure.__cause__), root)],
            ]
    attributes = {name: normalize(value, root) for name, value in vars(app).items()}
    collectors = (
        [normalize(vars(item), root) for item in app._collectors.collectors] if hasattr(app, "_collectors") else []
    )
    feeds = [
        {
            name: normalize(getattr(feed, name), root)
            for name in [
                "kind",
                "path",
                "kube_context",
                "health_receipt_mode",
                "allowed_engine_binaries",
                "heartbeat_path",
            ]
        }
        for feed in getattr(app, "_observation_feeds", [])
    ]
    files = {}
    data = root / "state"
    if data.exists():
        for path in sorted(data.rglob("*")):
            if path.is_file():
                text = path.read_text()
                files[str(path.relative_to(data))] = normalize(text, root)
    return {
        "error": error,
        "attributes": attributes,
        "collectors": collectors,
        "feeds": feeds,
        "calls": events,
        "files": files,
    }


CASES = configurations()
GOLDEN = json.loads((Path(__file__).parent / "data/wp5-gateway-init-golden.json").read_text())


@pytest.mark.parametrize("case", CASES)
def test_wp5_gateway_init_contract(case, tmp_path):
    assert observe(tmp_path / "case", CASES[case]) == GOLDEN[case]
