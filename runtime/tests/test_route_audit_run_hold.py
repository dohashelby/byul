"""Actual route writer -> existing retention, including scope/hold boundaries."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from serving_runtime.mvp.retention import HOLD_SCHEMA, RecordRetention
from serving_runtime.mvp.route_rejections import RouteRejections
from serving_runtime.mvp.store import JsonlStore


class RouteAuditRunHoldTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.hold = self.root / "hold.json"
        # Same environment, same held run with and without a session, another
        # run, and an unscoped row must keep their original attribution.
        self.scopes = {
            "a" * 32: {"environment": "lab", "run_id": "held-run", "observation_session_id": "session-1"},
            "b" * 32: {"environment": "lab", "run_id": "held-run"},
            "c" * 32: {"environment": "lab", "run_id": "other-run"},
            "d" * 32: {"environment": "lab"},
        }
        for ident, scope in self.scopes.items():
            RouteRejections(self.root, scope=scope).record(ident, "GET", "/unsupported", 404, "NOT_FOUND")
        self.store = JsonlStore(self.root / "gateway_route_rejections.jsonl")
        self.set_hold(["held-run"])

    def set_hold(self, runs):
        self.hold.write_text(json.dumps({"schema": HOLD_SCHEMA, "run_ids": runs, "request_ids": []}))

    def records(self):
        rows, corrupt = self.store.read_all()
        self.assertEqual(corrupt, [])
        return rows

    def assert_held_scopes_preserved(self):
        rows = self.records()
        self.assertEqual({row["request_id"] for row in rows}, {"a" * 32, "b" * 32})
        for row in rows:
            self.assertEqual(row["scope"], self.scopes[row["request_id"]])
            self.assertEqual(row["environment"], "lab")
            self.assertEqual(row["run_id"], "held-run")
            self.assertEqual(row["actor"], {"state": "unattributed", "principal_id": None})

    def test_writer_preserves_original_scope_and_exposes_existing_retention_keys(self):
        for row in self.records():
            expected = self.scopes[row["request_id"]]
            self.assertEqual(row["scope"], expected)
            self.assertEqual(row["run_id"], expected.get("run_id"))
            self.assertEqual(row["environment"], expected["environment"])
        no_session = next(row for row in self.records() if row["request_id"] == "b" * 32)
        self.assertNotIn("observation_session_id", no_session["scope"])

    def test_age_hold_preserves_matching_run_then_release_allows_deletion(self):
        future = datetime.now(timezone.utc) + timedelta(days=8)
        result = RecordRetention(self.root, enforce=True, hold_path=self.hold).run(now=future)
        file = result["files"]["gateway_route_rejections.jsonl"]
        self.assertEqual(file["held_units"], 2)
        self.assertEqual(file["removed_lines"], 2)
        self.assert_held_scopes_preserved()
        self.set_hold([])
        released = RecordRetention(self.root, enforce=True, hold_path=self.hold).run(now=future)
        self.assertEqual(released["files"]["gateway_route_rejections.jsonl"]["removed_lines"], 2)
        self.assertEqual(self.records(), [])

    def test_size_hold_overrides_limit_and_other_runs_are_removed(self):
        current = datetime.now(timezone.utc) + timedelta(minutes=1)
        result = RecordRetention(self.root, max_bytes=1, enforce=True, hold_path=self.hold).run(now=current)
        file = result["files"]["gateway_route_rejections.jsonl"]
        self.assertEqual(file["removable_by_age"], 0)
        self.assertEqual(file["held_units"], 2)
        self.assertEqual(file["removed_lines"], 2)
        self.assertTrue(result["over_size_limit_after"])
        self.assert_held_scopes_preserved()
        self.set_hold([])
        released = RecordRetention(self.root, max_bytes=1, enforce=True, hold_path=self.hold).run(now=current)
        self.assertEqual(released["files"]["gateway_route_rejections.jsonl"]["removed_lines"], 2)
        self.assertEqual(self.records(), [])

    def test_report_only_with_hold_never_rewrites_journal(self):
        before = self.store.path.read_bytes()
        result = RecordRetention(self.root, hold_path=self.hold).run(now=datetime.now(timezone.utc) + timedelta(days=8))
        self.assertEqual(result["mode"], "report_only")
        self.assertEqual(result["files"]["gateway_route_rejections.jsonl"]["held_units"], 2)
        self.assertEqual(self.store.path.read_bytes(), before)
