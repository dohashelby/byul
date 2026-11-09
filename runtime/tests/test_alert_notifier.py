from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from tools import alert_notifier as notifier

RID1, RID2, RID3 = "1" * 32, "2" * 32, "3" * 32
SECRETS = ("/opt/serving/assets", "serving-engine-def", "198.51.100.10", "/bin/sh", RID1, RID2, RID3, "prompt")


def collectors(**states):
    base = {"engine_health": "ok", "metrics": "disconnected"}
    base.update(states)
    return {"collectors": [{"source": k, "state": v, "last_error": "free text /opt/serving/assets"} for k, v in base.items()]}


def request(rid, at, reason="POLICY_MODEL_NOT_ALLOWED"):
    return {"request_id": rid, "recorded_at": at, "result": "DENIED", "reason_code": reason, "model": "prompt"}


def exec_obs(at, verdict="outside_allowlist", mode="monitor"):
    return {"observed_at": at, "source": "tetragon", "kind": "process_exec", "binary": "/bin/sh",
            "pod": {"pod": "serving-engine-def"}, "rule_id": "TG-EXEC-01", "exec_verdict": verdict,
            "action_mode": mode, "data_origin": "live"}


def file_obs(at, mode="enforce"):
    return {"observed_at": at, "source": "tetragon", "kind": "file_access", "path": "/opt/serving/assets/model",
            "rule_id": "TG-ASSET-01", "action_mode": mode, "data_origin": "live"}


def drop_obs(at):
    return {"observed_at": at, "source": "hubble", "kind": "network_flow", "verdict": "dropped",
            "dst": {"ip": "198.51.100.10"}, "reason": "POLICY_DENIED", "data_origin": "synthetic"}


class FakeGateway:
    def __init__(self):
        self.data = {
            "/api/status": {"status": "ok"},
            "/api/collectors": collectors(),
            "/api/summary": {"record_store": {"ok": True}},
            "/api/requests": {"requests": []},
            "/api/observations?tetragon": {"counts": {"total": 0}, "observations": []},
            "/api/observations?hubble": {"counts": {"total": 0}, "observations": []},
            "/api/tetragon-status": {"state": "ok"},
            "/api/gpu-status": {"state": "not_configured"},
        }
        self.down = False
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if self.down:
            raise notifier.GatewayUnavailable("UNREACHABLE")
        key = path + ("?" + params["source"] if path == "/api/observations" else "")
        return copy.deepcopy(self.data[key])


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.state = notifier.empty_state()

    def run_once(self):
        alerts = notifier.collect(self.gateway, self.state)
        json.loads(json.dumps(self.state))  # state stays JSON
        return alerts

    def test_first_run_does_not_replay_history_but_reports_current_outage(self):
        self.gateway.data["/api/requests"] = {"requests": [request(RID1, "2026-11-09T01:00:00Z")]}
        self.gateway.data["/api/collectors"] = collectors(engine_health="stale")
        alerts = self.run_once()
        self.assertEqual([a["kind"] for a in alerts], ["health_down"])
        self.assertEqual(alerts[0]["details"]["target"], "collector:engine_health")
        self.assertIn(("/api/requests", {"result": "DENIED", "limit": "200"}), self.gateway.calls)

    def test_new_denied_requests_are_counted_once_including_same_timestamp(self):
        self.gateway.data["/api/requests"] = {"requests": [request(RID1, "2026-11-09T01:00:00Z")]}
        self.run_once()
        self.gateway.data["/api/requests"] = {"requests": [
            request(RID3, "2026-11-09T01:00:05Z", "MAX_TOKENS_EXCEEDED"),
            request(RID2, "2026-11-09T01:00:00Z"), request(RID1, "2026-11-09T01:00:00Z")]}
        alerts = self.run_once()
        self.assertEqual(len(alerts), 1)
        self.assertEqual((alerts[0]["kind"], alerts[0]["count"]), ("gateway_denied", 2))
        self.assertEqual(alerts[0]["details"]["reason_codes"], {"MAX_TOKENS_EXCEEDED": 1, "POLICY_MODEL_NOT_ALLOWED": 1})
        self.assertEqual(self.run_once(), [])

    def test_tetragon_and_hubble_detections_and_blocks(self):
        self.run_once()
        self.gateway.data["/api/observations?tetragon"] = {"counts": {"total": 4}, "observations": [
            exec_obs("2026-11-09T01:00:03Z"), exec_obs("2026-11-09T01:00:02Z", "allowed_in_phase"),
            exec_obs("2026-11-09T01:00:01Z", "phase_unknown"), file_obs("2026-11-09T01:00:04Z")]}
        self.gateway.data["/api/observations?hubble"] = {"counts": {"total": 1}, "observations": [drop_obs("2026-11-09T01:00:05Z")]}
        alerts = {a["kind"]: a for a in self.run_once()}
        self.assertEqual(set(alerts), {"tetragon_exec_detected", "tetragon_file_blocked", "hubble_dropped"})
        self.assertEqual(alerts["tetragon_exec_detected"]["count"], 1)
        self.assertEqual(alerts["tetragon_exec_detected"]["severity"], "warning")
        self.assertEqual(alerts["tetragon_file_blocked"]["severity"], "critical")
        self.assertEqual(alerts["tetragon_file_blocked"]["details"]["rule_ids"], {"TG-ASSET-01": 1})
        text = json.dumps(notifier.payload(list(alerts.values()), "lab", "generic"), ensure_ascii=False)
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertIn("합성 자료 포함", text)
        self.assertEqual(self.run_once(), [])

    def test_health_alerts_are_edge_triggered_with_recovery(self):
        self.run_once()
        self.gateway.data["/api/collectors"] = collectors(engine_health="stale", metrics="disconnected")
        self.gateway.data["/api/gpu-status"] = {"state": "error"}
        down = self.run_once()
        self.assertEqual(sorted(a["details"]["target"] for a in down), ["collector:engine_health", "gpu_status"])
        self.assertEqual(self.run_once(), [])
        self.gateway.data["/api/collectors"] = collectors(engine_health="error")
        self.assertEqual([a["details"]["state"] for a in self.run_once()], ["error"])  # state changed while bad
        self.gateway.data["/api/collectors"] = collectors()
        self.gateway.data["/api/gpu-status"] = {"state": "ok"}
        self.gateway.data["/api/summary"] = {"record_store": {"ok": False}}
        result = {(a["kind"], a["details"]["target"]) for a in self.run_once()}
        self.assertEqual(result, {("health_recovered", "collector:engine_health"), ("health_recovered", "gpu_status"),
                                  ("health_down", "record_store")})
        self.gateway.data["/api/status"]["status"] = "degraded"
        self.assertEqual(self.run_once()[0]["details"]["state"], "degraded")

    def test_gateway_unreachable_needs_two_failures_and_alerts_once(self):
        self.run_once()
        self.gateway.down = True
        self.assertEqual(self.run_once(), [])
        self.assertEqual([a["kind"] for a in self.run_once()], ["gateway_unreachable"])
        self.assertEqual(self.run_once(), [])
        self.gateway.down = False
        self.assertEqual([a["kind"] for a in self.run_once()], ["gateway_reachable"])

    def test_cursor_survives_outage(self):
        self.run_once()
        self.gateway.down = True
        self.run_once()
        self.gateway.down = False
        self.gateway.data["/api/requests"] = {"requests": [request(RID1, "2026-11-09T02:00:00Z")]}
        self.assertIn("gateway_denied", [a["kind"] for a in self.run_once()])

    def test_late_observations_are_counted_once(self):
        self.gateway.data["/api/observations?tetragon"]["observations"] = [exec_obs("2026-11-09T02:00:10Z")]
        self.run_once()
        self.gateway.data["/api/observations?tetragon"]["observations"].append(file_obs("2026-11-09T02:00:05Z"))
        self.assertEqual([(a["kind"], a["count"]) for a in self.run_once()], [("tetragon_file_blocked", 1)])
        self.assertEqual(self.run_once(), [])

    def test_fractional_and_offset_timestamps_do_not_hide_new_requests(self):
        self.gateway.data["/api/requests"] = {"requests": [request(RID1, "2026-11-09T01:00:00Z")]}
        self.run_once()
        self.gateway.data["/api/requests"]["requests"].extend([
            request(RID2, "2026-11-09T01:00:00.100Z"), request(RID3, "2026-11-09T10:00:01+09:00")])
        alert = self.run_once()[0]
        self.assertEqual(alert["count"], 2)
        self.assertEqual(alert["last_at"], "2026-11-09T01:00:01.000000Z")
        self.assertEqual(self.run_once(), [])

    def test_existing_cursor_upgrade_does_not_replay_same_timestamp(self):
        cursor = {"at": "2026-11-09T01:00:00Z", "keys": [RID1]}
        found, cursor = notifier.denied_alerts({"requests": [request(RID1, cursor["at"])]}, cursor, 200)
        self.assertEqual(found, [])
        found, _ = notifier.denied_alerts({"requests": [request(RID1, cursor["at"]), request(RID2, cursor["at"])]}, cursor, 200)
        self.assertEqual(found[0]["count"], 1)

    def test_identifiers_disguised_as_codes_never_leave_host(self):
        self.run_once()
        marker = "private-token-like-identifier"
        self.gateway.data["/api/requests"] = {"requests": [request(RID1, "2026-11-09T01:00:01Z", marker)]}
        event = file_obs("2026-11-09T01:00:02Z")
        event.update(rule_id=marker, data_origin=marker)
        self.gateway.data["/api/observations?tetragon"]["observations"] = [event]
        event = drop_obs("2026-11-09T01:00:03Z")
        event["reason"] = marker
        self.gateway.data["/api/observations?hubble"]["observations"] = [event]
        self.gateway.data["/api/status"]["status"] = marker
        self.state["health"]["gateway"] = marker  # older state files may contain an unsafe code
        self.gateway.data["/api/collectors"]["collectors"].append({"source": marker, "state": "error"})
        body = json.dumps(notifier.payload(self.run_once(), "lab", "generic"))
        self.assertNotIn(marker, body)
        self.assertIn("OTHER", body)


class DeliveryTests(unittest.TestCase):
    def test_failed_send_keeps_pending_and_overflow_is_reported(self):
        state = notifier.empty_state()
        notifier.queue(state, [notifier._alert("x", "info", "t")] * (notifier.MAX_PENDING + 5))
        self.assertEqual((len(state["pending"]), state["dropped"]), (notifier.MAX_PENDING, 5))
        self.assertEqual(notifier.flush(state, lambda part: False), 0)
        self.assertEqual(state["pending"][0]["kind"], "alerts_dropped")
        self.assertEqual(state["pending"][0]["count"], 5)
        sent = []
        self.assertEqual(notifier.flush(state, lambda part: sent.append(part) or True), notifier.MAX_PENDING + 1)
        self.assertEqual(state["pending"], [])

    def test_payload_formats(self):
        alerts = [notifier._alert("gateway_denied", "warning", "Gateway 요청 차단", count=2,
                                  details={"reason_codes": {"X": 2}}, first_at="a", last_at="b")]
        self.assertIn("Gateway 요청 차단 2건", notifier.payload(alerts, "lab", "slack")["text"])
        discord = notifier.payload(alerts, "lab", "discord")
        self.assertEqual(discord["allowed_mentions"], {"parse": []})
        self.assertEqual(notifier.payload(alerts, "lab", "generic")["schema_version"], "runtime-alert/1.0")

    def test_free_text_codes_are_replaced(self):
        self.assertEqual(notifier._code("REQUEST_ALLOWED"), "REQUEST_ALLOWED")
        self.assertEqual(notifier._code("path /opt/x"), "OTHER")
        self.assertEqual(notifier._code(None), "OTHER")

    def test_deliver_uses_opener_and_status(self):
        class Response:
            def __init__(self, status):
                self.status = status
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        class Opener:
            def __init__(self, status):
                self.status, self.requests = status, []
            def open(self, request, timeout):
                self.requests.append(request)
                return Response(self.status)

        ok = Opener(204)
        self.assertTrue(notifier.deliver("https://hooks.example.test/x", {"text": "a"}, opener=ok))
        self.assertEqual(json.loads(ok.requests[0].data), {"text": "a"})
        self.assertFalse(notifier.deliver("https://hooks.example.test/x", {"text": "a"}, opener=Opener(500)))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_env_file_must_be_private(self):
        path = self.root / "alerts.env"
        path.write_text("ALERT_WEBHOOK_URL=https://hooks.example.test/a\nOTHER=1\nRUNTIME_VIEWER_TOKEN='t'\n")
        os.chmod(path, 0o644)
        with self.assertRaises(SystemExit):
            notifier.read_env_file(path)
        os.chmod(path, 0o600)
        self.assertEqual(notifier.read_env_file(path),
                         {"ALERT_WEBHOOK_URL": "https://hooks.example.test/a", "RUNTIME_VIEWER_TOKEN": "t"})

    def test_webhook_must_be_https_except_loopback(self):
        self.assertTrue(notifier.check_webhook_url("https://hooks.example.test/a"))
        self.assertTrue(notifier.check_webhook_url("http://127.0.0.1:9999/a"))
        for bad in ("http://hooks.example.test/a", "https://user:pw@hooks.example.test/a", "file:///etc/passwd"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                notifier.check_webhook_url(bad)

    def test_malformed_webhook_is_rejected_without_echoing_url(self):
        for bad in ("https:///missing-host", "https://hooks.example.test:bad/x", "https://[broken/x",
                    "https://hooks.example.test/x#fragment", "https://hooks.example.test/a\nb"):
            with self.subTest(case="malformed"), self.assertRaises(SystemExit) as failure:
                notifier.check_webhook_url(bad)
            self.assertNotIn(bad, str(failure.exception))

    def test_pending_alerts_are_saved_before_delivery_can_fail(self):
        path = self.root / "state.json"
        alert = notifier._alert("gateway_denied", "warning", "Gateway 요청 차단", count=1)
        with patch.object(notifier, "collect", return_value=[alert]), \
                patch.object(notifier, "flush", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                notifier.main(["--gateway-url", "https://gateway.test", "--state", str(path)])
        self.assertEqual(notifier.load_state(path)["pending"], [alert])

    def test_state_file_is_private_and_bad_state_restarts(self):
        path = self.root / "state" / "alert-state.json"
        state = notifier.empty_state()
        state["initialized"] = True
        notifier.save_state(path, state)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertTrue(notifier.load_state(path)["initialized"])
        path.write_text("{")
        self.assertFalse(notifier.load_state(path)["initialized"])

    def test_send_requires_webhook(self):
        with self.assertRaises(SystemExit):
            notifier.main(["--gateway-url", "http://127.0.0.1:1", "--send", "--state", str(self.root / "s.json")])
        with self.assertRaises(SystemExit):
            notifier.main(["--gateway-url", "http://127.0.0.1:1", "--label", "bad label"])


if __name__ == "__main__":
    unittest.main()
