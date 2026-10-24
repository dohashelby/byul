"""Tests for D-08 LAB record retention (report-only by default)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from serving_runtime.mvp.retention import HOLD_SCHEMA, RecordRetention, load_holds
from serving_runtime.mvp import store as store_module
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

    def test_cutoff_uses_latest_time_across_gateway_and_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            gateway = JsonlStore(data / "gateway_records.jsonl")
            receipts = JsonlStore(data / "engine_receipts.jsonl")
            gateway.append({"request_id": "joined", "recorded_at": iso(10)})
            receipts.append({"request_id": "joined", "received_at": iso(1)})
            receipts.append({"request_id": "receipt-only", "received_at": iso(10)})

            summary = RecordRetention(data, enforce=True).run(now=NOW)

            gateway_records, _ = gateway.read_all()
            receipt_records, _ = receipts.read_all()
            self.assertEqual(
                [item["request_id"] for item in gateway_records], ["joined"]
            )
            self.assertEqual(
                [item["request_id"] for item in receipt_records], ["joined"]
            )
            self.assertEqual(
                summary["files"]["engine_receipts.jsonl"]["removed_lines"], 1
            )

    def test_size_limit_selects_cross_file_request_as_one_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            gateway = JsonlStore(data / "gateway_records.jsonl")
            receipts = JsonlStore(data / "engine_receipts.jsonl")
            gateway.append({"request_id": "joined", "recorded_at": iso(20)})
            receipts.append({"request_id": "joined", "received_at": iso(1)})
            gateway.append({"request_id": "old", "recorded_at": iso(10)})
            receipts.append({"request_id": "old", "received_at": iso(10)})
            gateway.append({"request_id": "new", "recorded_at": iso(0)})

            total = sum(
                (data / name).stat().st_size
                for name in ("gateway_records.jsonl", "engine_receipts.jsonl")
            )
            old_size = sum(
                len(line) + 1
                for name in ("gateway_records.jsonl", "engine_receipts.jsonl")
                for line in (data / name).read_bytes().splitlines()
                if json.loads(line)["request_id"] == "old"
            )
            RecordRetention(
                data,
                max_age_days=365,
                max_bytes=total - old_size,
                enforce=True,
            ).run(now=NOW)

            gateway_ids = [item["request_id"] for item in gateway.read_all()[0]]
            receipt_ids = [item["request_id"] for item in receipts.read_all()[0]]
            self.assertEqual(gateway_ids, ["joined", "new"])
            self.assertEqual(receipt_ids, ["joined"])

    def test_undated_gateway_run_hold_propagates_to_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            gateway = JsonlStore(data / "gateway_records.jsonl")
            receipts = JsonlStore(data / "engine_receipts.jsonl")
            gateway.append({"request_id": "held-undated", "run_id": "evidence"})
            receipts.append(
                {"request_id": "held-undated", "received_at": iso(20)}
            )
            receipts.append({"request_id": "remove", "received_at": iso(20)})
            hold = data / "hold.json"
            hold.write_text(
                json.dumps({"schema": HOLD_SCHEMA, "run_ids": ["evidence"]}),
                encoding="utf-8",
            )

            summary = RecordRetention(
                data, enforce=True, hold_path=hold
            ).run(now=NOW)

            self.assertEqual(
                [item["request_id"] for item in gateway.read_all()[0]],
                ["held-undated"],
            )
            self.assertEqual(
                [item["request_id"] for item in receipts.read_all()[0]],
                ["held-undated"],
            )
            self.assertEqual(
                summary["files"]["gateway_records.jsonl"]["unclassified_lines"],
                1,
            )
            self.assertEqual(
                summary["files"]["engine_receipts.jsonl"]["held_units"], 1
            )

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

    def test_directory_fsync_failure_reports_committed_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            gateway = JsonlStore(data / "gateway_records.jsonl")
            gateway.append({"request_id": "old", "recorded_at": iso(10)})
            original_open = store_module.os.open

            def fail_directory_open(path, flags, *args, **kwargs):
                if Path(path) == data and flags == store_module.os.O_RDONLY:
                    raise OSError("injected directory open failure")
                return original_open(path, flags, *args, **kwargs)

            with patch(
                "serving_runtime.mvp.store.os.open",
                side_effect=fail_directory_open,
            ):
                summary = RecordRetention(data, enforce=True).run(now=NOW)

            entry = summary["files"]["gateway_records.jsonl"]
            self.assertTrue(summary["deleted"])
            self.assertEqual(entry["removed_lines"], 1)
            self.assertEqual(entry["durability"], "uncertain")
            self.assertIn("gateway_records.jsonl", summary["errors"])
            self.assertEqual(gateway.read_all()[0], [])
            logged = JsonlStore(data / "retention_log.jsonl").read_all()[0][0]
            self.assertEqual(
                logged["files"]["gateway_records.jsonl"]["removed_lines"], 1
            )
            self.assertEqual(
                logged["files"]["gateway_records.jsonl"]["durability"],
                "uncertain",
            )
            self.assertEqual(
                summary["last_deletion"]["files"]["gateway_records.jsonl"][
                    "durability"
                ],
                "uncertain",
            )

    def test_retention_log_append_failure_stays_in_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            gateway = JsonlStore(data / "gateway_records.jsonl")
            gateway.append({"request_id": "old", "recorded_at": iso(10)})
            retention = RecordRetention(data, enforce=True)

            with patch.object(
                retention.log,
                "append",
                side_effect=OSError("injected log failure"),
            ):
                summary = retention.run(now=NOW)

            self.assertTrue(summary["deleted"])
            self.assertEqual(summary["retention_log"]["state"], "error")
            self.assertEqual(summary["retention_log"]["error"], "OSError")
            self.assertIn("retention_log.jsonl", summary["errors"])
            self.assertIsNotNone(summary["last_deletion"])
            self.assertFalse((data / "retention_log.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
