"""S2 policy validity, history, collector, and compatibility tests."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.collectors import (
    Collector,
    CollectorManager,
    compare_baseline,
    load_declaration,
)
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.policy_state import (
    PolicyHistory,
    evaluate_policy,
    load_revocations,
)
from serving_runtime.mvp.query import QueryService
from serving_runtime.mvp.store import JsonlStore


def policy_document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "runtime-mvp/1.1",
        "policy_id": "policy-a",
        "policy_version": "1",
        "target_id": "mock-engine-001",
        "allowed_models": ["test-model"],
        "output_default_tokens": 128,
        "output_max_tokens": 128,
        "max_body_bytes": 65536,
    }
    document.update(updates)
    return document


class PolicySchemaAndValidityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def write(self, name: str, document: object) -> Path:
        path = self.directory / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_v11_optional_fields_and_v10_strict_compatibility(self) -> None:
        v11 = self.write(
            "v11.json",
            policy_document(
                valid_from="2026-01-01T00:00:00+09:00",
                valid_until="2027-01-01T00:00:00Z",
                approval_ref="display-only",
            ),
        )
        state = load_policy(v11)
        self.assertTrue(state.ok, state.error_message)
        self.assertEqual(state.policy.approval_ref, "display-only")

        v10_document = policy_document(valid_until="2027-01-01T00:00:00Z")
        v10_document["schema_version"] = "runtime-mvp/1.0"
        v10 = load_policy(self.write("v10.json", v10_document))
        self.assertFalse(v10.ok)
        self.assertEqual(v10.error_code, "UNKNOWN_FIELD")

        no_timezone = load_policy(
            self.write("naive.json", policy_document(valid_until="2027-01-01T00:00:00"))
        )
        self.assertFalse(no_timezone.ok)
        self.assertEqual(no_timezone.error_code, "INVALID_POLICY")

    def test_period_revocation_and_unknown_have_distinct_reasons(self) -> None:
        now = datetime.now(timezone.utc)
        future = load_policy(
            self.write(
                "future.json",
                policy_document(valid_from=(now + timedelta(hours=1)).isoformat()),
            )
        )
        self.assertEqual(
            evaluate_policy(future, load_revocations(None), now=now)["reason_code"],
            "POLICY_NOT_YET_VALID",
        )
        expired = load_policy(
            self.write(
                "expired.json",
                policy_document(valid_until=(now - timedelta(seconds=1)).isoformat()),
            )
        )
        self.assertEqual(
            evaluate_policy(expired, load_revocations(None), now=now)["reason_code"],
            "POLICY_EXPIRED",
        )

        active_path = self.write("active.json", policy_document())
        active = load_policy(active_path)
        revocations = self.write(
            "revocations.json",
            {
                "schema_version": "runtime-mvp-revocations/1.0",
                "revoked": [
                    {
                        "policy_id": "policy-a",
                        "policy_version": "1",
                        "sha256": active.sha256,
                        "revoked_at": now.isoformat(),
                        "reason": "test",
                    }
                ],
            },
        )
        self.assertEqual(
            evaluate_policy(active, load_revocations(revocations))["reason_code"],
            "POLICY_REVOKED",
        )
        self.assertEqual(
            evaluate_policy(active, load_revocations(self.directory / "missing.json"))[
                "reason_code"
            ],
            "POLICY_REVOCATION_UNKNOWN",
        )


class HistoryAndCompatibilityTests(unittest.TestCase):
    def test_history_previous_change_and_old_request_schema_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory_text:
            directory = Path(directory_text)
            policy_path = directory / "policy.json"
            policy_path.write_text(json.dumps(policy_document()), encoding="utf-8")
            history = PolicyHistory(directory / "policy_history.jsonl")
            history.append_startup(load_policy(policy_path), "a" * 32)
            changed = policy_document(allowed_models=["test-model", "second-model"])
            policy_path.write_text(json.dumps(changed), encoding="utf-8")
            history.append_startup(load_policy(policy_path), "b" * 32)
            latest = history.latest()
            self.assertEqual(len(latest), 2)
            self.assertTrue(latest[0]["same_version_content_changed"])
            self.assertEqual(latest[0]["previous"]["sha256"], latest[1]["sha256"])

            gateway = JsonlStore(directory / "gateway_records.jsonl")
            gateway.append(
                {
                    "phase": "final",
                    "request_id": "c" * 32,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "policy": {"policy_id": "old", "policy_version": "1"},
                    "decision": {"result": "ERROR", "http_status": 503},
                    "request": {},
                    "delivery": {},
                }
            )
            service = QueryService(
                gateway,
                JsonlStore(directory / "engine_receipts.jsonl"),
                lambda: {"state": "ok"},
            )
            detail = service.detail(
                "c" * 32,
                lambda _: {"status": "unknown"},
                "d" * 32,
            )
            self.assertIsNone(detail["gateway_records"][0]["policy"]["schema_version"])

    def test_history_storage_failure_is_state_not_startup_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory_text:
            blocker = Path(directory_text) / "file"
            blocker.write_text("x", encoding="utf-8")
            history = PolicyHistory(blocker / "policy_history.jsonl")
            history.append_startup(load_policy(blocker / "missing.json"), "a" * 32)
            self.assertEqual(history.status()["state"], "error")


class CollectorAndBaselineTests(unittest.TestCase):
    def test_collector_retains_success_then_error_and_stale(self) -> None:
        calls = [{"engine.version": "mock-1.0", "data_origin": "mock"}]

        def fetch():
            if calls:
                return calls.pop(0)
            raise OSError("stopped")

        collector = Collector("engine_info", fetch, interval_s=0.01, stale_after_s=0.04)
        collector.collect()
        success = collector.snapshot()
        collector.collect()
        failed = collector.snapshot()
        self.assertEqual(success["state"], "ok")
        self.assertEqual(failed["state"], "error")
        self.assertEqual(failed["last_value"], success["last_value"])
        self.assertEqual(failed["last_success_at"], success["last_success_at"])
        time.sleep(0.05)
        self.assertEqual(collector.snapshot()["state"], "stale")

    def test_dedicated_collector_starts_in_background_and_waits_after_finish(self) -> None:
        starts: list[float] = []
        finishes: list[float] = []
        second_started = threading.Event()

        def fetch() -> dict[str, bool]:
            starts.append(time.monotonic())
            if len(starts) == 2:
                second_started.set()
            time.sleep(0.04)
            finishes.append(time.monotonic())
            return {"ok": True}

        collector = Collector(
            "slow",
            fetch,
            interval_s=0.05,
            stale_after_s=1,
            dedicated_thread=True,
        )
        manager = CollectorManager([collector])
        started = time.monotonic()
        manager.start()
        self.addCleanup(manager.stop)
        self.assertLess(time.monotonic() - started, 0.04)
        self.assertTrue(second_started.wait(1))
        self.assertGreaterEqual(starts[1] - finishes[0], 0.04)

    def test_baseline_rows_separate_manifest_and_engine_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory_text:
            directory = Path(directory_text)
            baseline_path = directory / "baseline.json"
            manifest_path = directory / "manifest.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "schema_version": "runtime-mvp-baseline/1.0",
                        "fields": {
                            "engine.kind": "mock",
                            "option.max_model_len": 4096,
                            "engine.version": "mock-1.0",
                            "model.id": "test-model",
                            "unsupported.field": "x",
                        },
                    }
                ),
                encoding="utf-8",
            )
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": "runtime-mvp-baseline/1.0",
                        "fields": {"engine.kind": "mock", "option.max_model_len": 2048},
                    }
                ),
                encoding="utf-8",
            )
            info = Collector(
                "engine_info",
                lambda: (
                    {"engine.version": "mock-1.0", "model.id": "test-model"},
                    "mock",
                ),
            )
            info.collect()
            manager = CollectorManager([info])
            compared = compare_baseline(
                load_declaration(baseline_path, kind="baseline"),
                load_declaration(manifest_path, kind="manifest"),
                manager,
            )
            states = {
                (row["field"], row["source"]): row["state"]
                for row in compared["rows"]
            }
            self.assertEqual(states[("engine.kind", "declared_manifest")], "match")
            self.assertEqual(states[("option.max_model_len", "declared_manifest")], "drift")
            self.assertEqual(states[("engine.version", "engine_api")], "match")
            self.assertEqual(states[("unsupported.field", "engine_api")], "not_collected")
            self.assertIn("weight", compared["model_id_limit"])


class PolicyHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        try:
            self.engine = MockEngine(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)

    def start_gateway(self, document: dict[str, object], **config_updates: object) -> None:
        policy_path = self.directory / "http-policy.json"
        policy_path.write_text(json.dumps(document), encoding="utf-8")
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(policy_path),
            data_dir=str(self.directory),
            collector_interval_s=0.05,
            collector_stale_after_s=0.15,
            **config_updates,
        )
        self.gateway = GatewayApp(config, load_policy(policy_path), self.engine.address).start()
        self.addCleanup(self.gateway.stop)
        self.base_url = f"http://{self.gateway.address[0]}:{self.gateway.address[1]}"

    def test_expired_policy_is_503_final_without_engine_call(self) -> None:
        self.start_gateway(
            policy_document(
                valid_until=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            )
        )
        request = Request(
            self.base_url + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 64,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 503)
        body = json.loads(caught.exception.read().decode())
        self.assertEqual(body["error"]["code"], "POLICY_EXPIRED")
        self.assertEqual(self.engine.receipts, [])
        with closing(urlopen(self.base_url + "/api/policy", timeout=2)) as response:
            policy = json.loads(response.read().decode())
        self.assertFalse(policy["gateway_active"])

    def test_stream_snapshot_is_not_interrupted_after_expiration(self) -> None:
        self.engine.stop()
        self.engine = MockEngine(port=0, stream_events=10, stream_interval_s=0.2).start()
        self.addCleanup(self.engine.stop)
        expires = datetime.now(timezone.utc) + timedelta(seconds=1.5)
        self.start_gateway(
            policy_document(valid_until=expires.isoformat())
        )
        request = Request(
            self.base_url + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 64,
                    "stream": True,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with closing(urlopen(request, timeout=4)) as response:
            body = response.read()
        self.assertIn(b"data: [DONE]", body)
        self.assertGreater(datetime.now(timezone.utc), expires)


if __name__ == "__main__":
    unittest.main()
