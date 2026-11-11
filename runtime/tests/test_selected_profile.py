import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.selected_profile import validate_selected_profile

ROOT = Path(__file__).resolve().parents[1]


class SelectedProfileTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "policy.json"
        self.base = json.loads((ROOT / "examples/mvp/policy-v1.2-default-sampling.json").read_text())

    def check(self, value):
        self.path.write_text(json.dumps(value))
        state = load_policy(self.path)
        return state, validate_selected_profile(state)

    def test_current_default_and_bounded_sampling_supported(self):
        for extras in ({}, self.base["extra_fields"]):
            state, report = self.check({**self.base, "extra_fields": extras})
            self.assertTrue(state.ok)
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["policy_sha256"], state.sha256)
            self.assertEqual(report["response_privacy"]["allowed_return_options"], [])

    def test_bounded_extra_rules_cannot_reopen_selected_unsupported_inputs(self):
        forbidden = ("prompt_token_ids", "kv_transfer_params", "code_revision", "processor", "mm_processor_kwargs",
                     "response_format", "structured_outputs", "tools", "tool_choice", "native", "extra_body",
                     "return_token_ids", "return_prompt_text", "prompt_logprobs", "private_debug", "some_future_option")
        for name in forbidden:
            state, report = self.check({**self.base, "extra_fields": {name: {"mode": "validate", "type": "string", "max_length": 128}}})
            self.assertTrue(state.ok, name)
            self.assertFalse(report["supported"], name)
            self.assertIn(name, report["violations"][0]["fields"])

    def test_generic_policy_and_zeroday_remain_loadable_but_outside_selected_profile(self):
        document = {**self.base, "profile": "zeroday-lab", "extra_fields": {"tools": {"mode": "passthrough"}}}
        state, report = self.check(document)
        self.assertTrue(state.ok)
        self.assertFalse(report["supported"])
        self.assertEqual(report["violations"][0]["code"], "SELECTED_PROFILE_MISMATCH")
        state, report = self.check({**self.base, "extra_fields": {"temperature": {"mode": "validate", "type": "string", "max_length": 12}}})
        self.assertTrue(state.ok)
        self.assertEqual(report["violations"][0]["code"], "UNSUPPORTED_SELECTED_SAMPLING_RULE")

    def test_cli_exit_and_report_are_preflight_consumable(self):
        spec = importlib.util.spec_from_file_location("check_selected_profile", ROOT / "tools/check_selected_profile.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.check({**self.base, "extra_fields": {}})
        output = Path(self.temp.name) / "report.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(tool.main(["--policy", str(self.path), "--report-out", str(output)]), 0)
        self.assertTrue(json.loads(output.read_text())["supported"])
        self.check({**self.base, "extra_fields": {"prompt_token_ids": {"mode": "validate", "type": "boolean"}}})
        with redirect_stdout(io.StringIO()):
            self.assertEqual(tool.main(["--policy", str(self.path)]), 1)
        self.path.write_text("broken")
        self.assertFalse(validate_selected_profile(load_policy(self.path))["supported"])
