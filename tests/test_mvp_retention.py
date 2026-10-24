"""Tests for D-08 LAB record retention (report-only by default)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest

from serving_runtime.mvp.retention import HOLD_SCHEMA, RecordRetention, load_holds
from serving_runtime.mvp.store import JsonlStore


NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


class RetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        gateway = JsonlStore(self.data / "gateway_records.jsonl")
        # old request: both phases 10 days ago
        gateway.append({"phase": "started", "request_id": "old", "recorded_at": iso(10), "run_id": "r1"})
        gateway.append({"phase": "final", "request_id": "old", "recorded_at": iso(10), "run_id": "r1"})
        # straddling request: started old, final recent -> kept together
        gateway.append({"phase": "started", "request_id": "edge", "recorded_at": iso(8), "run_id": "r1"})
        gateway.append({"phase": "final", "request_id": "edge", "recorded_at": iso(1), "run_id": "r1"})
        # held run, old
        gateway.append({"phase": "final", "request_id": "kept", "recorded_at": iso(20), "run_id": "evidence"})
        # recent
        gateway.append({"phase": "final", "request_id": "new", "recorded_at": iso(0.5), "run_id": "r2"})
        with (self.data / "gateway_records.jsonl").open("ab") as output:
            output.write(b"not json\n")
        receipts = JsonlStore(self.data / "engine_receipts.jsonl")
        receipts.append({"request_id": "old", "received_at": iso(10)})
        receipts.append({"request_id": "kept", "received_at": iso(20)})
        observations = JsonlStore(self.data / "observations.jsonl")
        observations.append({"source": "hubble", "observed_at": iso(9), "run_id": "lab"})
        observations.append({"source": "hubble", "observed_at": iso(2), "run_id": "lab"})

    def hold(self, document: object) -> Path:
        path = self.data / "hold.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def request_ids(self) -> list[str]:
        records, _ = JsonlStore(self.data / "gateway_records.jsonl").read_all()
        return [record["request_id"] for record in records]

    def test_report_only_by_default_changes_nothing(self) -> None:
        before = (self.data / "gateway_records.jsonl").read_bytes()
        summary = RecordRetention(self.data).run(now=NOW)
        self.assertEqual(summary["mode"], "report_only")
        self.assertFalse(summary["deleted"])
        gateway = summary["files"]["gateway_records.jsonl"]
        # "old" and the unheld "kept" are removable by age; "edge" is recent as a group.
        self.assertEqual(gateway["removable_by_age"], 2)
        self.assertEqual(gateway["unclassified_lines"], 1)
        self.assertEqual((self.data / "gateway_records.jsonl").read_bytes(), before)
        self.assertFalse((self.data / "retention_log.jsonl").exists())

    def test_enforce_removes_old_groups_and_keeps_held_and_unclassified(self) -> None:
        hold = self.hold({"schema": HOLD_SCHEMA, "run_ids": ["evidence"]})
        summary = RecordRetention(self.data, enforce=True, hold_path=hold).run(now=NOW)
        self.assertTrue(summary["deleted"])
        self.assertEqual(self.request_ids(), ["edge", "edge", "kept", "new"])
        self.assertIn(b"not json", (self.data / "gateway_records.jsonl").read_bytes())
        receipts, _ = JsonlStore(self.data / "engine_receipts.jsonl").read_all()
        # The held run also holds its request's engine receipt.
        self.assertEqual([item["request_id"] for item in receipts], ["kept"])
        observations, _ = JsonlStore(self.data / "observations.jsonl").read_all()
        self.assertEqual(len(observations), 1)
        log, _ = JsonlStore(self.data / "retention_log.jsonl").read_all()
        self.assertEqual(len(log), 1)
        removed_range = log[0]["files"]["gateway_records.jsonl"]["removable_range"]
        self.assertEqual(removed_range["from"], iso(10))
        self.assertIsNotNone(summary["last_deletion"])

    def test_size_limit_removes_oldest_first(self) -> None:
        total = sum((self.data / name).stat().st_size for name in (
            "gateway_records.jsonl", "engine_receipts.jsonl", "observations.jsonl"))
        summary = RecordRetention(
            self.data, max_age_days=365, max_bytes=total - 50, enforce=True
        ).run(now=NOW)
        self.assertTrue(summary["over_size_limit"])
        removed = sum(entry["removable_by_size"] for entry in summary["files"].values())
        self.assertGreaterEqual(removed, 1)
        # The oldest unit (20 days ago, not held here) goes first.
        self.assertNotIn("kept", self.request_ids())
        self.assertIn("new", self.request_ids())

    def test_broken_hold_file_blocks_deletion(self) -> None:
        before = (self.data / "gateway_records.jsonl").read_bytes()
        hold = self.hold({"schema": "wrong"})
        summary = RecordRetention(self.data, enforce=True, hold_path=hold).run(now=NOW)
        self.assertEqual(summary["holds"]["state"], "error")
        self.assertFalse(summary["deleted"])
        self.assertIn("blocked_reason", summary)
        self.assertEqual((self.data / "gateway_records.jsonl").read_bytes(), before)

    def test_hold_file_validation(self) -> None:
        self.assertEqual(load_holds(None)[2]["state"], "not_configured")
        self.assertEqual(load_holds(self.data / "missing.json")[2]["state"], "error")
        for document in (
            [],
            {"schema": HOLD_SCHEMA, "run_ids": "x"},
            {"schema": HOLD_SCHEMA, "run_ids": [""]},
            {"schema": HOLD_SCHEMA, "extra": 1},
        ):
            with self.subTest(document=document):
                self.assertEqual(load_holds(self.hold(document))[2]["state"], "error")
        runs, requests, state = load_holds(
            self.hold({"schema": HOLD_SCHEMA, "run_ids": ["a"], "request_ids": ["b"]})
        )
        self.assertEqual((runs, requests, state["state"]), ({"a"}, {"b"}, "ok"))

    def test_invalid_limits_are_rejected(self) -> None:
        for kwargs in ({"max_age_days": 0}, {"max_bytes": 0}, {"max_bytes": True}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    RecordRetention(self.data, **kwargs)

    def test_appends_during_rewrite_are_not_lost(self) -> None:
        store = JsonlStore(self.data / "gateway_records.jsonl")
        retention = RecordRetention(self.data, enforce=True)
        done = threading.Event()

        def writer() -> None:
            for index in range(200):
                store.append({"phase": "final", "request_id": f"w{index}", "recorded_at": iso(0)})
            done.set()

        thread = threading.Thread(target=writer)
        thread.start()
        retention.run(now=NOW)
        thread.join(timeout=10)
        self.assertTrue(done.is_set())
        ids = self.request_ids()
        self.assertEqual(sum(1 for item in ids if item.startswith("w")), 200)


if __name__ == "__main__":
    unittest.main()
