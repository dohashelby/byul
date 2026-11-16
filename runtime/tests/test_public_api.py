"""Public exports retain object identity without importing optional sidebranches."""

import importlib
from pathlib import Path
import subprocess
import sys
import unittest

EXPORTS = {
    "Agent": "operations.agent",
    "Config": "operations.agent",
    "evaluate": "operations.safety",
    "GatewayApp": "gateway.app",
    "MVPConfig": "config.config",
    "load_policy": "policy.policy",
    "verify_policy_bundle": "policy.policy_bundle",
    "load_intent": "enforcement.intent",
    "ObservationQuery": "enforcement.events",
    "RouteRejections": "gateway.route_rejections",
    "identity_scope_ref": "observation.identity_scope",
    "read_phase_timeline": "observation.phase_timeline",
    "NativeReceiptFeed": "observation.native_receipts",
    "canonical": "observation.native_receipts",
    "read_host_feed": "observation.native_receipts",
    "read_protected": "observation.native_receipts",
    "CollectionFailure": "observation.collectors",
    "Collector": "observation.collectors",
    "project_termination": "bridge_update.update_engine_link",
    "QueryService": "observation.query",
    "JsonlStore": "observation.store",
    "RecordRetention": "observation.retention",
    "HOLD_SCHEMA": "observation.retention",
}


class PublicApiTests(unittest.TestCase):
    def test_all_exports_are_the_original_objects(self):
        api = importlib.import_module("serving_runtime.public_api")
        self.assertEqual(set(api.__all__), set(EXPORTS))
        for name, module in EXPORTS.items():
            with self.subTest(name=name):
                self.assertIs(getattr(api, name), getattr(importlib.import_module("serving_runtime." + module), name))
        for name in ("FakeBackend", "_parse", "bundle_tools"):
            self.assertFalse(hasattr(api, name))

    def test_fresh_import_does_not_load_guard_or_devtools(self):
        code = (
            "import sys;import serving_runtime.public_api;"
            "loaded=[n for n in sys.modules if n=='guard' or n.startswith('guard.') "
            "or n=='serving_runtime.devtools' or n.startswith('serving_runtime.devtools.')];"
            "assert loaded==[],loaded"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
