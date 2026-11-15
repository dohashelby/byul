"""Tests for D-08 LAB record retention (report-only by default)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from serving_runtime.observation.retention import HOLD_SCHEMA, RecordRetention, load_holds
from serving_runtime.observation import store as store_module
from serving_runtime.observation.store import JsonlStore


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

    def test_deletion_range_tracks_each_files_actual_removed_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            gateway = JsonlStore(data / "gateway_records.jsonl")
            receipts = JsonlStore(data / "engine_receipts.jsonl")
            gateway.append({"request_id": "joined", "recorded_at": iso(12)})
            gateway.append({"request_id": "joined", "recorded_at": iso(10)})
            receipts.append({"request_id": "joined", "received_at": iso(9)})
            result = RecordRetention(data, enforce=True).run(now=NOW)
            expected = {"from": iso(12), "to": iso(10)}
            self.assertEqual(result["files"]["gateway_records.jsonl"]["removable_range"], expected)
            self.assertEqual(
                result["files"]["engine_receipts.jsonl"]["removable_range"], {"from": iso(9), "to": iso(9)}
            )
            self.assertEqual(gateway.read_all()[0], [])
            restarted = RecordRetention(data).run(now=NOW)
            self.assertEqual(restarted["last_deletion"]["files"]["gateway_records.jsonl"]["removable_range"], expected)

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
        total = sum(
            (self.data / name).stat().st_size
            for name in ("gateway_records.jsonl", "engine_receipts.jsonl", "observations.jsonl")
        )
        summary = RecordRetention(self.data, max_age_days=365, max_bytes=total - 50, enforce=True).run(now=NOW)
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
            self.assertEqual([item["request_id"] for item in gateway_records], ["joined"])
            self.assertEqual([item["request_id"] for item in receipt_records], ["joined"])
            self.assertEqual(summary["files"]["engine_receipts.jsonl"]["removed_lines"], 1)

    def test_route_rejections_share_request_hold_and_retention_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            routes = JsonlStore(data / "gateway_route_rejections.jsonl")
            gateway = JsonlStore(data / "gateway_records.jsonl")
            routes.append({"request_id": "held", "recorded_at": iso(20)})
            routes.append({"request_id": "joined", "recorded_at": iso(20)})
            routes.append({"request_id": "expired", "recorded_at": iso(20)})
            routes.append({"request_id": "recent", "recorded_at": iso(1)})
            gateway.append({"request_id": "joined", "recorded_at": iso(1)})
            hold = data / "hold.json"
            hold.write_text(json.dumps({"schema": HOLD_SCHEMA, "request_ids": ["held"]}), encoding="utf-8")

            preview = RecordRetention(data, hold_path=hold).run(now=NOW)
            self.assertEqual(preview["files"]["gateway_route_rejections.jsonl"]["removable_by_age"], 1)
            self.assertEqual(len(routes.read_all()[0]), 4)
            applied = RecordRetention(data, enforce=True, hold_path=hold).run(now=NOW)
            self.assertEqual([row["request_id"] for row in routes.read_all()[0]], ["held", "joined", "recent"])
            self.assertEqual(applied["files"]["gateway_route_rejections.jsonl"]["removed_lines"], 1)
            self.assertEqual([row["request_id"] for row in gateway.read_all()[0]], ["joined"])

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

            total = sum((data / name).stat().st_size for name in ("gateway_records.jsonl", "engine_receipts.jsonl"))
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
            receipts.append({"request_id": "held-undated", "received_at": iso(20)})
            receipts.append({"request_id": "remove", "received_at": iso(20)})
            hold = data / "hold.json"
            hold.write_text(
                json.dumps({"schema": HOLD_SCHEMA, "run_ids": ["evidence"]}),
                encoding="utf-8",
            )

            summary = RecordRetention(data, enforce=True, hold_path=hold).run(now=NOW)

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
            self.assertEqual(summary["files"]["engine_receipts.jsonl"]["held_units"], 1)

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
        runs, requests, state = load_holds(self.hold({"schema": HOLD_SCHEMA, "run_ids": ["a"], "request_ids": ["b"]}))
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
                "serving_runtime.observation.store.os.open",
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
            self.assertEqual(logged["files"]["gateway_records.jsonl"]["removed_lines"], 1)
            self.assertEqual(
                logged["files"]["gateway_records.jsonl"]["durability"],
                "uncertain",
            )
            self.assertEqual(
                summary["last_deletion"]["files"]["gateway_records.jsonl"]["durability"],
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


import pytest


def test_a5_inflight_request_is_not_a_size_candidate(tmp_path):
    store = JsonlStore(tmp_path / "gateway_records.jsonl")
    started = {"phase": "started", "request_id": "active", "recorded_at": iso(0)}
    store.append(started)
    result = RecordRetention(tmp_path, max_bytes=1, enforce=True).run(now=NOW)
    assert store.read_all()[0] == [started]
    assert result["deleted"] is False


def test_a5_append_before_delete_preserves_the_whole_cross_file_request(tmp_path, monkeypatch):
    gateway = JsonlStore(tmp_path / "gateway_records.jsonl")
    receipts = JsonlStore(tmp_path / "engine_receipts.jsonl")
    started = {"phase": "started", "request_id": "r", "recorded_at": iso(10)}
    final = {"phase": "final", "request_id": "r", "recorded_at": iso(10)}
    gateway.append(started)
    gateway.append(final)
    receipts.append({"request_id": "r", "received_at": iso(10)})
    original = JsonlStore.remove_lines
    injected = []

    def append_first(store, lines, **kwargs):
        if not injected:
            injected.append(True)
            gateway.append({**final, "late": True})
        return original(store, lines, **kwargs)

    monkeypatch.setattr(JsonlStore, "remove_lines", append_first)
    result = RecordRetention(tmp_path, enforce=True).run(now=NOW)
    assert gateway.read_all()[0] == [started, final, {**final, "late": True}]
    assert len(receipts.read_all()[0]) == 1 and not result["deleted"]


def test_a5_final_appended_to_started_before_age_deletion_keeps_both(tmp_path, monkeypatch):
    store = JsonlStore(tmp_path / "gateway_records.jsonl")
    started = {"phase": "started", "request_id": "active", "recorded_at": iso(10 / 1440)}
    final = {"phase": "final", "request_id": "active", "recorded_at": iso(0)}
    store.append(started)
    JsonlStore(tmp_path / "observations.jsonl").append({"observed_at": iso(10)})
    original = JsonlStore.remove_lines

    def append_final(target, lines, **kwargs):
        store.append(final)
        return original(target, lines, **kwargs)

    monkeypatch.setattr(JsonlStore, "remove_lines", append_final)
    RecordRetention(tmp_path, enforce=True).run(now=NOW)
    rows = store.read_all()[0]
    assert rows == [started, final]


@pytest.mark.parametrize("failure", ["hold", "rewrite"])
def test_a6_failed_or_blocked_deletion_reports_actual_bytes(tmp_path, monkeypatch, failure):
    path = tmp_path / "gateway_records.jsonl"
    raw = json.dumps({"request_id": "r", "recorded_at": iso(0)}, separators=(",", ":")).encode()
    assert len(raw) <= 70
    path.write_bytes(raw.ljust(70, b" ") + b"\n")
    path.chmod(0o600)
    hold = None
    if failure == "hold":
        hold = tmp_path / "hold.json"
        hold.write_text("{}")
    else:
        def fail(*args, **kwargs):
            raise OSError("A6 injected rewrite failure")
        monkeypatch.setattr(JsonlStore, "remove_lines", fail)
    result = RecordRetention(tmp_path, max_bytes=1, enforce=True, hold_path=hold).run(now=NOW)
    assert path.stat().st_size == 71 and result["deleted"] is False
    assert result["over_size_limit_after"] is True


@pytest.mark.parametrize("enforce", [False, True])
def test_a6_report_projects_and_enforce_measures_success(tmp_path, enforce):
    path = tmp_path / "gateway_records.jsonl"
    store = JsonlStore(path)
    store.append({"request_id": "r", "recorded_at": iso(0)})
    before = path.read_bytes()
    result = RecordRetention(tmp_path, max_bytes=1, enforce=enforce).run(now=NOW)
    assert result["over_size_limit_after"] is False
    assert result["deleted"] is enforce
    assert path.read_bytes() == (b"" if enforce else before)


def test_d1_append_finishes_while_initial_scan_is_paused(tmp_path, monkeypatch):
    retention = RecordRetention(tmp_path, enforce=True)
    entered, release, writer_ready, appended = (threading.Event() for _ in range(4))
    original = retention._scan_file
    errors = []

    def pause(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("scan release was not signalled")
        return original(*args, **kwargs)

    def work():
        try:
            retention.run(now=NOW)
        except Exception as error:
            errors.append(error)

    def write():
        writer_ready.set()
        JsonlStore(tmp_path / "gateway_records.jsonl").append(
            {"phase": "started", "request_id": "during-scan", "recorded_at": iso(0)}
        )
        appended.set()

    monkeypatch.setattr(retention, "_scan_file", pause)
    worker = threading.Thread(target=work)
    writer = threading.Thread(target=write)
    worker.start()
    try:
        assert entered.wait(2)
        writer.start()
        assert writer_ready.wait(2)
        finished_during_scan = appended.wait(0.75)
    finally:
        release.set()
        worker.join(5)
        if writer.ident is not None:
            writer.join(5)
    assert not errors and not worker.is_alive() and not writer.is_alive()
    assert finished_during_scan


def test_d1_late_request_append_is_checked_without_full_rescan(tmp_path, monkeypatch):
    store = JsonlStore(tmp_path / "gateway_records.jsonl")
    first = {"request_id": "r", "phase": "final", "recorded_at": iso(10)}
    late = {**first, "recorded_at": iso(0)}
    store.append(first)
    retention = RecordRetention(tmp_path, enforce=True)
    original_scan, original_remove = retention._scan_file, JsonlStore.remove_lines
    scans = []

    def scan(managed, *args, **kwargs):
        scans.append(managed.name)
        return original_scan(managed, *args, **kwargs)

    def append_before_remove(target, lines, **kwargs):
        store.append(late)
        return original_remove(target, lines, **kwargs)

    monkeypatch.setattr(retention, "_scan_file", scan)
    monkeypatch.setattr(JsonlStore, "remove_lines", append_before_remove)
    result = retention.run(now=NOW)
    assert store.read_all()[0] == [first, late] and not result["deleted"]
    assert scans == [
        "gateway_records.jsonl",
        "gateway_route_rejections.jsonl",
        "engine_receipts.jsonl",
        "observations.jsonl",
    ]


@pytest.mark.parametrize("hold_kind", ["request", "run", "invalid"])
def test_d1_hold_changed_just_before_delete_still_blocks_request(tmp_path, monkeypatch, hold_kind):
    store = JsonlStore(tmp_path / "gateway_records.jsonl")
    row = {"request_id": "r", "phase": "final", "recorded_at": iso(10), "run_id": "run"}
    store.append(row)
    hold = tmp_path / "hold.json"
    hold.write_text(json.dumps({"schema": HOLD_SCHEMA}))
    original = JsonlStore.remove_lines

    def change_hold(target, lines, **kwargs):
        value = (
            {"schema": HOLD_SCHEMA, "request_ids": ["r"]}
            if hold_kind == "request"
            else ({"schema": HOLD_SCHEMA, "run_ids": ["run"]} if hold_kind == "run" else {})
        )
        hold.write_text(json.dumps(value))
        return original(target, lines, **kwargs)

    monkeypatch.setattr(JsonlStore, "remove_lines", change_hold)
    result = RecordRetention(tmp_path, enforce=True, hold_path=hold).run(now=NOW)
    assert store.read_all()[0] == [row] and not result["deleted"]


def test_d1_new_request_row_in_previously_empty_journal_protects_receipt(tmp_path, monkeypatch):
    receipts = JsonlStore(tmp_path / "engine_receipts.jsonl")
    receipt = {"request_id": "late-gateway", "received_at": iso(10)}
    receipts.append(receipt)
    retention = RecordRetention(tmp_path, enforce=True)
    original = retention._scan_file
    gateway = JsonlStore(tmp_path / "gateway_records.jsonl")
    started = {"request_id": "late-gateway", "phase": "started", "recorded_at": iso(0)}

    def append_after_scan(managed, *args, **kwargs):
        result = original(managed, *args, **kwargs)
        if managed.name == "observations.jsonl":
            gateway.append(started)
        return result

    monkeypatch.setattr(retention, "_scan_file", append_after_scan)
    result = retention.run(now=NOW)
    assert gateway.read_all()[0] == [started] and receipts.read_all()[0] == [receipt]
    assert not result["deleted"]


@pytest.mark.parametrize("reason", ["age", "size"])
def test_d2_sixty_day_started_only_request_expires_normally(tmp_path, reason):
    store = JsonlStore(tmp_path / "gateway_records.jsonl")
    store.append({"request_id": "orphan", "phase": "started", "recorded_at": iso(60)})
    result = RecordRetention(
        tmp_path, enforce=True, max_age_days=7 if reason == "age" else 365,
        max_bytes=5 * 1024**3 if reason == "age" else 1,
    ).run(now=NOW)
    assert store.read_all()[0] == [] and result["deleted"] is True
    assert result["files"]["gateway_records.jsonl"]["removable_by_" + reason] == 1
    assert result["over_size_limit_after"] is False


@pytest.mark.parametrize(
    "seconds,protected",
    [(3599, True), (3600, True), (3601, False)],
)
def test_d2_inflight_protection_has_an_inclusive_one_hour_boundary(tmp_path, seconds, protected):
    store = JsonlStore(tmp_path / "gateway_records.jsonl")
    row = {"request_id": "boundary", "phase": "started", "recorded_at": (NOW - timedelta(seconds=seconds)).isoformat()}
    store.append(row)
    result = RecordRetention(tmp_path, max_age_days=365, max_bytes=1, enforce=True).run(now=NOW)
    assert store.read_all()[0] == ([row] if protected else [])
    assert result["deleted"] is not protected


@pytest.mark.parametrize("completed", [False, True])
def test_d2_ten_minute_inflight_is_protected_but_complete_request_is_not(tmp_path, completed):
    store = JsonlStore(tmp_path / "gateway_records.jsonl")
    started = {"request_id": "recent", "phase": "started", "recorded_at": (NOW - timedelta(minutes=10)).isoformat()}
    store.append(started)
    if completed:
        store.append({**started, "phase": "final"})
    result = RecordRetention(tmp_path, max_bytes=1, enforce=True).run(now=NOW)
    assert store.read_all()[0] == ([] if completed else [started])
    assert result["deleted"] is completed


def test_d2_latest_started_timestamp_controls_the_grace_period(tmp_path):
    store = JsonlStore(tmp_path / "gateway_records.jsonl")
    old = {"request_id": "retry", "phase": "started", "recorded_at": iso(60)}
    recent = {**old, "recorded_at": (NOW - timedelta(minutes=10)).isoformat()}
    store.append(recent)
    store.append(old)
    result = RecordRetention(tmp_path, max_bytes=1, enforce=True).run(now=NOW)
    assert store.read_all()[0] == [recent, old] and not result["deleted"]


def test_d1_append_finishes_while_file_removal_plan_is_paused(tmp_path, monkeypatch):
    from serving_runtime.observation import retention as module

    store = JsonlStore(tmp_path / "gateway_records.jsonl")
    store.append({"request_id": "expired", "phase": "final", "recorded_at": iso(10)})
    entered, release, ready, appended = (threading.Event() for _ in range(4))
    original_sorted = sorted
    errors = []

    def pause_plan(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("plan release was not signalled")
        return original_sorted(*args, **kwargs)

    def retain():
        try:
            RecordRetention(tmp_path, enforce=True).run(now=NOW)
        except Exception as error:
            errors.append(error)

    def append():
        ready.set()
        store.append({"request_id": "during-plan", "phase": "started", "recorded_at": iso(0)})
        appended.set()

    monkeypatch.setattr(module, "sorted", pause_plan, raising=False)
    worker, writer = threading.Thread(target=retain), threading.Thread(target=append)
    worker.start()
    try:
        assert entered.wait(2)
        writer.start()
        assert ready.wait(2)
        finished_during_plan = appended.wait(0.75)
    finally:
        release.set()
        worker.join(5)
        if writer.ident is not None:
            writer.join(5)
    assert not errors and not worker.is_alive() and not writer.is_alive()
    assert finished_during_plan
    assert store.read_all()[0][0]["request_id"] == "during-plan"
