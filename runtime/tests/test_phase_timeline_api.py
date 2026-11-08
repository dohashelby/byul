"""Timeline read projection never exposes Kubernetes identity."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from serving_runtime.mvp.phase_timeline import read_phase_timeline


class TimelineReadTests(unittest.TestCase):
    def test_missing_and_demo_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "missing.json")
            missing = read_phase_timeline(path)
            self.assertEqual(missing["collector"]["state"], "missing")
            self.assertFalse(missing["collector"]["fresh"])
            demo = read_phase_timeline(path, demo=True)
            self.assertEqual([x["phase"] for x in demo["entries"]], ["loading", "compiling", "inferring"])
            self.assertEqual(demo["data_origin"], "synthetic")

    def test_live_redacts_pod_identity_and_reports_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timeline.json"
            now = datetime.now(timezone.utc)
            doc = {
                "schema_version": "runtime-phase-timeline/1.0",
                "entries": [{"phase": "loading", "from": (now - timedelta(seconds=5)).isoformat(), "until": None, "basis": "engine_signal"}],
                "collector": {"state": "running", "pod_uid": "secret-uid", "pod_name": "secret-pod", "written_at": now.isoformat(), "last_log_at": None, "log_delivery_lag_seconds": None},
            }
            path.write_text(json.dumps(doc), encoding="utf-8")
            fresh = read_phase_timeline(str(path))
            self.assertTrue(fresh["collector"]["fresh"])
            self.assertNotIn("secret-pod", json.dumps(fresh))
            self.assertNotIn("secret-uid", json.dumps(fresh))
            doc["collector"]["written_at"] = (now - timedelta(seconds=30)).isoformat()
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertEqual(read_phase_timeline(str(path))["collector"]["state"], "stale")

    def test_demo_timeline_covers_existing_synthetic_observation(self) -> None:
        observed = "2026-05-22T12:00:00+00:00"
        timeline = read_phase_timeline(None, demo=True, observed_times=[observed])
        point = datetime.fromisoformat(observed)
        self.assertTrue(any(datetime.fromisoformat(entry["from"]) <= point < datetime.fromisoformat(entry["until"])
                            for entry in timeline["entries"]))
        self.assertEqual(timeline["data_origin"], "synthetic")
