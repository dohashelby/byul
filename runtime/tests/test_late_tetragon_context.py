"""Late state files and missing feed context remain observable failures."""

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.request import urlopen

from serving_runtime.config.config import MVPConfig
from serving_runtime.devtools.mock_engine import MockEngine
from serving_runtime.gateway.app import GatewayApp
from serving_runtime.observation.collectors import Collector, CollectorManager
from serving_runtime.observation.observation_feed import HEARTBEAT_SCHEMA, ObservationFeed
from serving_runtime.policy.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]


def test_state_file_created_after_startup_is_an_error_value_in_all_status_routes(tmp_path):
    policy = ROOT / "examples/mvp/policy.json"
    engine = MockEngine(port=0, receipts_path=tmp_path / "receipts.jsonl").start()
    try:
        app = GatewayApp(
            MVPConfig(gateway_port=0, engine_port=0, data_dir=str(tmp_path)), load_policy(policy), engine.address
        ).start()
        try:
            assert app.tetragon_status()["state"] == "not_configured"
            state = tmp_path / "tetragon-state.json"
            state.write_text("not even JSON: SHOULD_NOT_BE_READ")
            result = app.tetragon_status()
            assert result["state"] == "error" and result["error"] == "kube_context_missing"
            assert result["fresh"] is False and result["policies"] == []
            assert isinstance(app.policy_status(), dict)
            for route in ("/api/status", "/api/policy", "/api/tetragon-status"):
                with urlopen(f"http://{app.address[0]}:{app.address[1]}{route}", timeout=5) as response:
                    assert response.status == 200
                    document = json.load(response)
                    assert isinstance(document, dict)
                    assert "SHOULD_NOT_BE_READ" not in json.dumps(document)
                    if route == "/api/tetragon-status":
                        assert document["error"] == "kube_context_missing"
            state.unlink()
            assert app.tetragon_status()["state"] == "not_configured"
        finally:
            app.stop()
    finally:
        engine.stop()


def test_three_feed_context_checks_become_collector_errors_without_stopping_polling(tmp_path):
    def feed(name, **kwargs):
        path = tmp_path / name
        path.write_text("")
        return ObservationFeed(
            kind="tetragon", path=path, data_dir=tmp_path, data_origin="real", environment=None, run_id=None, **kwargs
        )

    heartbeat_path = tmp_path / "heartbeat.json"
    heartbeat = feed("heartbeat-feed", heartbeat_path=heartbeat_path)
    at = datetime.now(timezone.utc).isoformat()
    heartbeat_path.write_text(
        json.dumps(
            dict(
                schema=HEARTBEAT_SCHEMA,
                kind="tetragon",
                kube_context="formal",
                feed_path=str(heartbeat.path),
                started_at=at,
                updated_at=at,
                state="running",
                processes=[],
            )
        )
    )
    identity = feed("identity-feed", identity_path=tmp_path / "identities.json")
    health = feed("health-feed")
    item = {"identity": {"node": "node"}, "observed_at": at}
    collectors = [
        Collector("heartbeat", heartbeat.collect, interval_s=0.01),
        Collector("identity", identity.collect, interval_s=0.01),
        Collector("health", lambda: health._save_health_receipt(item), interval_s=0.01),
    ]
    manager = CollectorManager(collectors)
    manager.start()
    try:
        first = [c.snapshot()["last_attempt_at"] for c in collectors]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(
            c.snapshot()["last_attempt_at"] == stamp for c, stamp in zip(collectors, first)
        ):
            time.sleep(0.01)
        assert manager._thread.is_alive()
        for collector, stamp in zip(collectors, first):
            result = collector.snapshot()
            assert result["last_attempt_at"] != stamp
            assert result["state"] == "error" and result["last_error_kind"] == "ValueError"
            assert "Kubernetes context is required" in result["last_error"]
            assert result["last_success_at"] is None
    finally:
        manager.stop()
    assert not (tmp_path / "tetragon-health-receipts.json").exists()
