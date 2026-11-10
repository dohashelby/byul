import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from serving_runtime.mvp.http_handler import Handler
from serving_runtime.mvp.management_listener import AccessRecorder, ManagementListenerConfig
from serving_runtime.mvp.route_rejections import RouteRejections, QUERY_MAX_BYTES


class RouteAuditObservabilityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_overflow_write_failure_and_tail_are_separate_from_inference(self):
        audit = RouteRejections(self.root, limit=2, scope={"run_id": "run1", "target_id": "target1"})
        with patch.object(audit.store, "append", side_effect=OSError("private failure detail")):
            audit.record("a" * 32, "POST", "/unsupported?token=secret", 404, "NOT_FOUND")
        audit.record("b" * 32, "GET", "/missing", 404, "NOT_FOUND")
        audit.record("c" * 32, "POST", "/missing", 404, "NOT_FOUND")
        document = audit.query(limit=1)
        self.assertEqual(document["status"]["attempted_count"], 3)
        self.assertEqual(document["status"]["write_failure_count"], 1)
        self.assertEqual(document["status"]["recorded_count"], 1)
        self.assertEqual(document["status"]["overflow_count"], 1)
        self.assertEqual(document["items"][0]["scope"]["run_id"], "run1")
        self.assertEqual(document["items"][0]["actor"]["state"], "unattributed")
        self.assertEqual(len(document["items"][0]["event_id"]), 32)
        self.assertIsNone(document["window"]["total"])
        self.assertFalse((self.root / "gateway_records.jsonl").exists())
        self.assertNotIn("private failure detail", json.dumps(document))
        self.assertNotIn("secret", json.dumps(document))

    def test_tail_is_bounded_and_reports_corrupt_and_read_failure(self):
        audit = RouteRejections(self.root)
        for i in range(3):
            audit.record(str(i) * 32, "GET", f"/missing-{i}", 404, "NOT_FOUND")
        with audit.store.path.open("ab") as stream:
            stream.write(b"broken\n")
        document = audit.query(limit=2)
        self.assertEqual([x["path"] for x in document["items"]], ["/missing-2", "/missing-1"])
        self.assertTrue(document["window"]["truncated"])
        self.assertEqual(document["window"]["corrupt_lines"], 1)
        with audit.store.path.open("ab") as stream:
            stream.write(b"x" * (QUERY_MAX_BYTES + 10))
        document = audit.query()
        self.assertLessEqual(document["window"]["scanned_bytes"], QUERY_MAX_BYTES)
        self.assertTrue(document["window"]["truncated"])
        with patch.object(Path, "open", side_effect=PermissionError("private")):
            self.assertEqual(audit.query()["window"]["read_error_class"], "PermissionError")
        for value in (0, 201, True):
            with self.assertRaises(ValueError):
                audit.query(limit=value)

    def test_retention_deletion_is_reflected_without_cached_rows(self):
        audit = RouteRejections(self.root)
        audit.record("a" * 32, "GET", "/missing", 404, "NOT_FOUND")
        self.assertEqual(len(audit.query()["items"]), 1)
        audit.store.remove_lines({1})
        self.assertEqual(audit.query()["items"], [])
        self.assertEqual(audit.status()["recorded_count"], 1)  # process history, not current retained rows

    def test_route_read_api_uses_existing_read_authorization(self):
        handler = object.__new__(Handler)
        handler.command, handler.path = "GET", "/api/route-rejections?limit=1"
        handler.request_header = Mock(return_value=None)
        query = Mock(return_value={"items": []})
        handler.dependencies = SimpleNamespace(authorize_read=lambda _: (401, "AUTH_REQUIRED", "required"), route_rejections=query)
        handler._send_read_refusal, handler._send_json = Mock(), Mock()
        handler.do_GET()
        query.assert_not_called()
        handler.dependencies.authorize_read = lambda _: None
        handler.do_GET()
        query.assert_called_once_with(limit=1)
        handler.path = "/api/route-rejections?limit=1&limit=2"
        handler.do_GET()
        self.assertEqual(handler._send_json.call_args.args[0], 400)

    def test_management_access_identity_and_scope_use_server_configuration(self):
        config = ManagementListenerConfig.build(engine_url="http://127.0.0.1:8000", target_ref="target1", run_id="run1", environment="lab", policy_ref="p1")
        stream = io.StringIO()
        recorder = AccessRecorder(stream=stream, scope={"target_ref": config.target_ref, "run_id": config.run_id})
        recorder.record(allowed=False, path="http://user:password@adapter.invalid/manage?token=secret", method="POST", status_code=405)
        recorder.record(allowed=True, path="/health", method="GET", status_code=200)
        rows = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertNotEqual(rows[0]["event_id"], rows[1]["event_id"])
        self.assertTrue(rows[0]["recorded_at"])
        self.assertEqual(rows[0]["method"], "POST")
        self.assertEqual(rows[0]["scope"]["target_ref"], "target1")
        self.assertEqual(rows[0]["actor"], {"state": "unattributed", "principal_id": None})
        self.assertNotIn("password", stream.getvalue())
        self.assertNotIn("secret", stream.getvalue())
        with self.assertRaises(ValueError):
            ManagementListenerConfig.build(engine_url="http://127.0.0.1:8000", run_id="unsafe\nref")

    def test_management_failed_sink_is_visible_and_does_not_change_access_result(self):
        sink = Mock()
        sink.write.side_effect = OSError("sensitive sink path")
        recorder = AccessRecorder(stream=sink)
        recorder.record(allowed=False, path="/manage", method="DELETE", status_code=405)
        self.assertEqual(recorder.status()["denied_count"], 1)
        self.assertEqual(recorder.status()["write_failure_count"], 1)
        self.assertEqual(recorder.status()["last_write_error_class"], "OSError")
        self.assertNotIn("sensitive", json.dumps(recorder.status()))

    def test_long_absolute_uri_userinfo_is_removed_before_path_truncation(self):
        sink = io.StringIO()
        recorder = AccessRecorder(stream=sink)
        recorder.record(allowed=False, method="GET", status_code=404,
                        path="http://user:" + "SYNTHETIC_CREDENTIAL_" * 1000 + "@adapter.invalid/health")
        record = json.loads(sink.getvalue())
        self.assertEqual(record["path"], "http://adapter.invalid/health")
        self.assertNotIn("SYNTHETIC_CREDENTIAL", sink.getvalue())
