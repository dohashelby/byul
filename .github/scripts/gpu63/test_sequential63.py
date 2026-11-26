import json
from pathlib import Path
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

from guard.configuration import validate_requested_config
from guard.models import load_pack, utcnow
import sequential63 as run
import stock_capture as stock


class SequentialScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = load_pack(stock.UPDATE / "packs/PACK-BASELINE")

    def test_only_six_operator_deferred_checks_are_excluded(self):
        ids = set(run.scope(self.pack))
        self.assertEqual(len(ids), 63)
        self.assertEqual({check.id for check in self.pack.active_checks()} - ids,
                         {"LIF-08", "LIF-11", "REQ-14", "REQ-16", "REQ-21", "SUP-06"})

    def test_runtime_identity_rules_are_not_fabricated_as_policy_values(self):
        policy = run.policy(self.pack, utcnow() + timedelta(minutes=45))
        self.assertTrue(policy.hash_ok())
        self.assertEqual(policy.status.value, "DRAFT")
        self.assertEqual(set(policy.scope.include), set(run.scope(self.pack)))
        self.assertNotIn("selected_model_grants", policy.values["REQ-05"])
        self.assertNotIn("copy_treatments", policy.values["PLT-12"])

    def test_actual_target_configuration_names_every_requested_executor(self):
        cfg = run.configuration(self.pack, Path("/owned/current/target.json"))
        validate_requested_config(cfg)
        self.assertEqual(set(cfg["executors"]), {self.pack.checks[cid].executor for cid in run.scope(self.pack)})
        self.assertEqual(set(cfg["executors"].values()), {"kubernetes"})
        self.assertNotIn("allowed_checks", cfg.get("update", {}))

    def test_two_physical_gpus_are_not_accepted_as_the_single_gpu_job(self):
        one = "GPU-00000000-0000-0000-0000-000000000001,L4,580.0\n"
        two = one + "GPU-00000000-0000-0000-0000-000000000002,L4,580.0\n"
        context = b"owned-test-context"
        with patch.object(stock, "CONTEXT_SHA", stock.sha256_hex(context)), \
                patch.object(stock, "kube", return_value=context), patch.object(stock, "command", return_value=two.encode()):
            with self.assertRaisesRegex(ValueError, "SINGLE_L4_DEVICE_REQUIRED"):
                stock.verify_host(Path("/owned/kubeconfig"), {})


if __name__ == "__main__":
    unittest.main()
