"""HTTP tests for the M0 gateway status skeleton."""

from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.cli import main
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_POLICY = ROOT / "examples/mvp/policy.json"


class GatewayAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        try:
            self.engine = MockEngine(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)
        state = replace(load_policy(EXAMPLE_POLICY), path='<policy&"path">')
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(EXAMPLE_POLICY),
            data_dir=self.temporary.name,
        )
        self.gateway = GatewayApp(config, state, self.engine.address).start()
        self.addCleanup(self.gateway.stop)
        host, port = self.gateway.address
        self.assertEqual(host, "127.0.0.1")
        self.base_url = f"http://{host}:{port}"

    def test_status_json_contains_policy_and_engine_check(self) -> None:
        with closing(urlopen(self.base_url + "/api/status", timeout=2)) as response:
            status = json.loads(response.read().decode("utf-8"))
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["mode"], "mock_engine")
        self.assertTrue(status["policy"]["valid"])
        self.assertEqual(status["policy"]["policy_version"], "1")
        self.assertEqual(len(status["policy"]["sha256"]), 64)
        self.assertIn("loaded_at", status["policy"])
        self.assertTrue(status["mock_engine"]["healthy"])
        self.assertIn("checked_at", status["mock_engine"])
        self.assertTrue(status["record_store"]["ok"])
        self.assertEqual(status["record_store"]["data_dir"], self.temporary.name)
        self.assertEqual(len(status["observation_session_id"]), 32)

    def test_dashboard_page_and_assets_are_served(self) -> None:
        with closing(urlopen(self.base_url + "/", timeout=2)) as response:
            page = response.read().decode("utf-8")
            self.assertTrue(response.headers["Content-Type"].startswith("text/html"))
        self.assertIn("모의 엔진", page)
        self.assertIn("/static/app.js", page)
        for asset, content_type in (("/static/app.css", "text/css"), ("/static/app.js", "text/javascript")):
            with closing(urlopen(self.base_url + asset, timeout=2)) as response:
                self.assertTrue(response.headers["Content-Type"].startswith(content_type))
                self.assertGreater(len(response.read()), 0)

    def test_invalid_chat_post_has_m2_error_and_request_id(self) -> None:
        request = Request(
            self.base_url + "/v1/chat/completions",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(len(caught.exception.headers["X-Request-ID"]), 32)
        body = json.loads(caught.exception.read().decode("utf-8"))
        self.assertEqual(body["error"]["code"], "INVALID_MODEL")
        self.assertEqual(body["error"]["request_id"], caught.exception.headers["X-Request-ID"])

class MVPCommandTests(unittest.TestCase):
    def test_non_loopback_binding_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GatewayApp(
                MVPConfig(host="0.0.0.0", gateway_port=0),
                load_policy(EXAMPLE_POLICY),
                ("127.0.0.1", 1),
            )

    def test_cli_forwards_mvp_options(self) -> None:
        with patch("serving_runtime.mvp.app.run") as run:
            result = main([
                "mvp",
                "--policy", "custom-policy.json",
                "--gateway-port", "18080",
                "--engine-port", "18088",
                "--data-dir", "/tmp/runtime-state",
                "--relay-timeout", "0.25",
            ])
        self.assertEqual(result, 0)
        config = run.call_args.args[0]
        self.assertEqual(config.policy_path, "custom-policy.json")
        self.assertEqual(config.gateway_port, 18080)
        self.assertEqual(config.engine_port, 18088)
        self.assertEqual(config.data_dir, "/tmp/runtime-state")
        self.assertEqual(config.relay_timeout_s, 0.25)


if __name__ == "__main__":
    unittest.main()
