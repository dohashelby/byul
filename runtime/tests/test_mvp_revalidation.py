"""Automatic revalidation request detection, lifecycle, API, and export."""

from __future__ import annotations

from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.request import Request, urlopen

from serving_runtime.cli import main
from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.revalidation import (
    EXPORT_FORMAT_STATUS,
    RevalidationRequests,
    evaluate_asset_integrity,
    evaluate_config_drift,
    evaluate_policy_history,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "examples/mvp/policy.json"


def context(session: str = "a" * 32) -> dict:
    return {
        "observation_session_id": session,
        "target_id": "target-a",
        "policy": {
            "policy_id": "policy-a",
            "policy_version": "1",
            "sha256": "1" * 64,
        },
    }


def asset_result(
    *,
    intact: bool,
    checked_at: str = "2026-10-24T00:00:00+00:00",
    changed_paths: tuple[str, ...] = ("model.bin",),
) -> dict:
    return {
        "intact": intact,
        "items": (
            [{"path": path, "state": "changed"} for path in changed_paths]
            if not intact
            else []
        ),
        "counts": {
            "match": 0 if not intact else 1,
            "changed": len(changed_paths) if not intact else 0,
            "missing": 0,
            "unreadable": 0,
            "symlink": 0,
        },
        "extra_files": [],
        "extra_file_count": 0,
        "extra_symlinks": [],
        "extra_symlink_count": 0,
        "walk_errors": [],
        "walk_error_count": 0,
        "manifest_sha256": "2" * 64,
        "checked_at": checked_at,
    }


def drift_comparison(state: str = "drift", observed: str = "mock") -> dict:
    return {
        "rows": [
            {
                "field": "engine.kind",
                "expected": "vllm",
                "observed": observed,
                "source": "engine_api",
                "observed_at": "2026-10-24T00:00:00+00:00",
                "state": state,
                "data_origin": "mock",
            },
            {
                "field": "engine.kind",
                "expected": "vllm",
                "observed": "declared",
                "source": "declared_manifest",
                "observed_at": "2026-10-24T00:00:00+00:00",
                "state": "drift",
                "data_origin": "declared",
            },
        ]
    }


def changed_policy_history() -> dict:
    return {
        "policy_id": "policy-a",
        "policy_version": "1",
        "sha256": "b" * 64,
        "previous": {
            "policy_id": "policy-a",
            "policy_version": "1",
            "sha256": "a" * 64,
        },
        "same_version_content_changed": True,
    }


class PureEvaluationTests(unittest.TestCase):
    def test_each_trigger_builds_openable_condition(self) -> None:
        asset = evaluate_asset_integrity(asset_result(intact=False), "local_file")
        drift = evaluate_config_drift(drift_comparison())
        policy = evaluate_policy_history(changed_policy_history())

        self.assertEqual(asset.conditions[0].trigger, "asset_changed")
        self.assertEqual(asset.conditions[0].evidence["paths"]["changed"], ["model.bin"])
        self.assertEqual(drift.conditions[0].trigger, "config_drift")
        self.assertEqual(drift.conditions[0].data_origin, "mock")
        self.assertEqual(policy.conditions[0].trigger, "policy_content_changed")
        self.assertEqual(policy.conditions[0].evidence["previous"]["sha256"], "a" * 64)

    def test_only_explicit_match_can_clear_drift(self) -> None:
        stale = drift_comparison("stale")
        self.assertEqual(evaluate_config_drift(stale).observed_origins, {})
        matched = evaluate_config_drift(drift_comparison("match", "vllm"))
        self.assertEqual(matched.conditions, ())
        self.assertIn("engine_api:engine.kind", matched.observed_origins)


class RequestLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)
        self.requests = RevalidationRequests(self.data_dir)

    def observe_changed(self, checked_at: str = "2026-10-24T00:00:00+00:00") -> None:
        self.requests.observe_asset(
            asset_result(intact=False, checked_at=checked_at),
            data_origin="local_file",
            **context(),
        )

    def test_opened_record_schema_and_same_evidence_is_not_duplicated(self) -> None:
        self.observe_changed()
        self.observe_changed("2026-10-24T00:01:00+00:00")
        records, corrupt = self.requests.store.read_all()
        self.assertEqual(corrupt, [])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["schema_version"], "runtime-revalidation-request/1.0")
        self.assertEqual(record["event"], "opened")
        self.assertEqual(record["status"], "open")
        self.assertEqual(len(record["revalidation_id"]), 32)
        self.assertEqual(len(record["fingerprint"]), 64)
        self.assertEqual(record["data_origin"], "local_file")

    def test_clear_then_reappear_uses_same_request_id(self) -> None:
        self.observe_changed()
        request_id = self.requests.summary()["requests"][0]["revalidation_id"]
        self.requests.observe_asset(
            asset_result(intact=True), data_origin="local_file", **context()
        )
        self.requests.observe_asset(
            asset_result(intact=True), data_origin="local_file", **context()
        )
        cleared = self.requests.summary()["requests"][0]
        self.assertEqual(cleared["last_event"], "condition_cleared")
        self.assertEqual(cleared["event_count"], 2)
        self.assertFalse(cleared["current_evidence_present"])
        self.assertEqual(cleared["condition_state"], "cleared")
        self.assertEqual(cleared["status"], "open")
        self.assertEqual(self.requests.summary()["current_condition_count"], 0)

        self.observe_changed("2026-10-24T00:02:00+00:00")
        reappeared = self.requests.summary()["requests"][0]
        self.assertEqual(reappeared["revalidation_id"], request_id)
        self.assertEqual(reappeared["last_event"], "condition_reappeared")
        self.assertEqual(reappeared["event_count"], 3)
        self.assertTrue(reappeared["current_evidence_present"])
        self.assertEqual(self.requests.summary()["current_condition_count"], 1)

    def test_expanded_asset_evidence_supersedes_without_clearing(self) -> None:
        self.observe_changed()
        first_id = self.requests.summary()["requests"][0]["revalidation_id"]
        self.requests.observe_asset(
            asset_result(
                intact=False,
                changed_paths=("model.bin", "tokenizer.json"),
            ),
            data_origin="local_file",
            **context(),
        )

        records, _ = self.requests.store.read_all()
        self.assertEqual(
            [record["event"] for record in records],
            ["opened", "superseded", "opened"],
        )
        self.assertNotIn("condition_cleared", {record["event"] for record in records})
        self.assertEqual(records[1]["revalidation_id"], first_id)
        self.assertEqual(records[1]["superseded_by"], records[2]["revalidation_id"])
        summary = self.requests.summary()
        self.assertEqual(summary["open_count"], 2)
        self.assertEqual(summary["current_condition_count"], 1)
        superseded = next(
            item for item in summary["requests"] if item["revalidation_id"] == first_id
        )
        self.assertEqual(superseded["condition_state"], "superseded")
        self.assertEqual(
            superseded["condition_state_label"],
            "다른 근거로 대체됨, 해소 아님",
        )
        self.assertFalse(superseded["current_evidence_present"])

        current_id = records[2]["revalidation_id"]
        self.requests.observe_asset(
            asset_result(intact=True), data_origin="local_file", **context()
        )
        records, _ = self.requests.store.read_all()
        self.assertEqual(records[-1]["event"], "condition_cleared")
        self.assertEqual(records[-1]["revalidation_id"], current_id)
        resolved = self.requests.summary()
        self.assertEqual(resolved["current_condition_count"], 0)
        states = {
            item["revalidation_id"]: item["condition_state"]
            for item in resolved["requests"]
        }
        self.assertEqual(states[first_id], "superseded")
        self.assertEqual(states[current_id], "cleared")

    def test_changed_drift_value_supersedes_without_clearing(self) -> None:
        self.requests.observe_baseline(drift_comparison(observed="mock-a"), **context())
        first_id = self.requests.summary()["requests"][0]["revalidation_id"]
        self.requests.observe_baseline(drift_comparison(observed="mock-b"), **context())

        records, _ = self.requests.store.read_all()
        self.assertEqual(
            [record["event"] for record in records],
            ["opened", "superseded", "opened"],
        )
        self.assertEqual(records[1]["revalidation_id"], first_id)
        self.assertEqual(records[1]["superseded_by"], records[2]["revalidation_id"])
        self.assertEqual(self.requests.summary()["current_condition_count"], 1)

    def test_restart_preserves_supersession_and_reappearance_rules(self) -> None:
        self.observe_changed()
        first_id = self.requests.summary()["requests"][0]["revalidation_id"]

        restarted = RevalidationRequests(self.data_dir)
        restarted.observe_asset(
            asset_result(
                intact=False,
                changed_paths=("model.bin", "tokenizer.json"),
            ),
            data_origin="local_file",
            **context("b" * 32),
        )
        records, _ = restarted.store.read_all()
        second_id = records[2]["revalidation_id"]
        self.assertEqual(records[1]["event"], "superseded")
        self.assertEqual(records[1]["superseded_by"], second_id)

        restarted_again = RevalidationRequests(self.data_dir)
        restarted_again.observe_asset(
            asset_result(intact=False),
            data_origin="local_file",
            **context("c" * 32),
        )
        records, _ = restarted_again.store.read_all()
        self.assertEqual(
            [record["event"] for record in records],
            [
                "opened",
                "superseded",
                "opened",
                "superseded",
                "condition_reappeared",
            ],
        )
        self.assertEqual(records[3]["revalidation_id"], second_id)
        self.assertEqual(records[3]["superseded_by"], first_id)
        self.assertEqual(records[4]["revalidation_id"], first_id)
        self.assertNotIn("condition_cleared", {record["event"] for record in records})
        summary = restarted_again.summary()
        self.assertEqual(summary["open_count"], 2)
        self.assertEqual(summary["current_condition_count"], 1)

    def test_restart_reads_existing_file_for_deduplication(self) -> None:
        self.observe_changed()
        restarted = RevalidationRequests(self.data_dir)
        restarted.observe_asset(
            asset_result(intact=False, checked_at="later"),
            data_origin="local_file",
            **context("b" * 32),
        )
        records, _ = restarted.store.read_all()
        self.assertEqual(len(records), 1)

    def test_drift_and_policy_triggers_open_requests(self) -> None:
        self.requests.observe_baseline(drift_comparison(), **context())
        self.requests.observe_policy_history(changed_policy_history(), **context())
        summary = self.requests.summary()
        self.assertEqual(summary["open_count"], 2)
        self.assertEqual(
            {item["trigger"] for item in summary["requests"]},
            {"config_drift", "policy_content_changed"},
        )

    def test_evaluation_error_does_not_stop_later_request_handling(self) -> None:
        self.requests.observe_asset(
            {"intact": "unknown"}, data_origin="local_file", **context()
        )
        first = self.requests.summary()
        self.assertEqual(first["evaluation_status"]["state"], "error")
        self.assertEqual(first["open_count"], 0)

        self.observe_changed()
        recovered = self.requests.summary()
        self.assertEqual(recovered["evaluation_status"]["state"], "ok")
        self.assertEqual(recovered["open_count"], 1)
        self.assertEqual(len(recovered["evaluation_errors"]), 1)

    def test_corrupt_line_is_reported_without_hiding_valid_request(self) -> None:
        self.observe_changed()
        with self.requests.store.path.open("ab") as output:
            output.write(b"not-json\n")
        summary = self.requests.summary()
        self.assertEqual(summary["open_count"], 1)
        self.assertEqual(summary["corrupt_lines"][0]["line"], 2)


class ExportAndDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def test_export_is_exclusive_and_marks_unagreed_draft(self) -> None:
        requests = RevalidationRequests(self.base / "data")
        requests.observe_asset(
            asset_result(intact=False), data_origin="local_file", **context()
        )
        requests.observe_asset(
            asset_result(
                intact=False,
                changed_paths=("model.bin", "tokenizer.json"),
            ),
            data_origin="local_file",
            **context(),
        )
        out = self.base / "export.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = main(
                [
                    "revalidation",
                    "export",
                    "--data-dir",
                    str(self.base / "data"),
                    "--out",
                    str(out),
                ]
            )
        self.assertEqual(result, 0)
        document = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(document["open_count"], 2)
        self.assertEqual(document["current_condition_count"], 1)
        superseded = next(
            item
            for item in document["requests"]
            if item["condition_state"] == "superseded"
        )
        self.assertEqual(
            superseded["condition_state_label"],
            "다른 근거로 대체됨, 해소 아님",
        )
        self.assertIsInstance(superseded["superseded_by"], str)
        self.assertEqual(document["format_status"], EXPORT_FORMAT_STATUS)
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "revalidation",
                        "export",
                        "--data-dir",
                        str(self.base / "data"),
                        "--out",
                        str(out),
                    ]
                ),
                1,
            )

    def test_api_returns_open_requests_and_status_count(self) -> None:
        try:
            engine = MockEngine(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(engine.stop)
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(POLICY_PATH),
            data_dir=str(self.base / "gateway-data"),
        )
        gateway = GatewayApp(config, load_policy(POLICY_PATH), engine.address)
        gateway._revalidation.observe_asset(
            {"intact": "unknown"}, data_origin="local_file", **context()
        )
        gateway._revalidation.observe_asset(
            asset_result(intact=False), data_origin="local_file", **context()
        )
        gateway.start()
        self.addCleanup(gateway.stop)
        host, port = gateway.address
        with closing(urlopen(f"http://{host}:{port}/api/revalidation", timeout=2)) as response:
            payload = json.loads(response.read())
        with closing(urlopen(f"http://{host}:{port}/api/status", timeout=2)) as response:
            status = json.loads(response.read())
        chat = Request(
            f"http://{host}:{port}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 1,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with closing(urlopen(chat, timeout=2)) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(payload["open_count"], 1)
        self.assertEqual(payload["current_condition_count"], 1)
        self.assertEqual(payload["requests"][0]["event_count"], 1)
        self.assertEqual(len(payload["evaluation_errors"]), 1)
        self.assertEqual(status["revalidation"]["open_count"], 1)
        self.assertEqual(status["revalidation"]["current_condition_count"], 1)


if __name__ == "__main__":
    unittest.main()
