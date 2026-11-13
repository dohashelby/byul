"""TOML defaults use the existing MVPConfig and explicit CLI always wins."""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import fields
import io
import json
from pathlib import Path
import tempfile
import tomllib
from typing import get_args
import unittest
from unittest.mock import patch

from serving_runtime.cli import main
from serving_runtime.config.config import MVPConfig
from serving_runtime.config.node_config import load_node_defaults

DATA = Path(__file__).resolve().parent / "data"


class NodeConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "node.toml"

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        return str(self.path)

    def invoke(self, arguments):
        out, err = io.StringIO(), io.StringIO()
        with patch("serving_runtime.gateway.app.run") as run, redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(arguments)
            except SystemExit as error:
                code = error.code
        return code, run, out.getvalue(), err.getvalue()

    def capture(self, arguments):
        code, run, out, err = self.invoke(arguments)
        self.assertEqual((code, out, err), (0, "", ""))
        run.assert_called_once()
        return run.call_args.args[0]

    def test_all_55_configurable_fields_equal_independent_cli_fixture(self):
        path = DATA / "node_all_fields.toml"
        values = tomllib.loads(path.read_text())
        allowed = {field.name for field in fields(MVPConfig)} - {"host", "policy_bundle"}
        self.assertEqual(set(values), allowed)
        expected = dict(values)
        for name in ("observation_feeds", "observation_heartbeats"):
            expected[name] = tuple(expected[name].items())
        for field in fields(MVPConfig):
            if field.name in expected and field.type is float:
                expected[field.name] = float(expected[field.name])
        config = self.capture(["mvp", "--config", str(path)])
        cli = self.capture(json.loads((DATA / "node_all_fields.argv.json").read_text()))
        direct = MVPConfig(**expected)
        for field in fields(MVPConfig):
            with self.subTest(field=field.name):
                self.assertEqual(getattr(config, field.name), getattr(direct, field.name))
                self.assertEqual(getattr(config, field.name), getattr(cli, field.name))
                if field.type is float:
                    self.assertIs(type(getattr(config, field.name)), float)

    def test_cli_then_toml_then_code_default_and_equals_spelling(self):
        path = self.write("gateway_port = 19080\nrelay_timeout_s = 7\ncilium_timeout_s = 12.5\n")
        config = self.capture(["mvp", f"--config={path}", "--gateway-port", "19081"])
        self.assertEqual(config.gateway_port, 19081)
        self.assertEqual(config.relay_timeout_s, 7.0)
        self.assertIs(type(config.relay_timeout_s), float)
        self.assertEqual(config.engine_port, 8088)
        self.assertEqual(config.cilium_timeout_s, 12.5)
        plain = self.capture(["mvp"])
        self.assertEqual((plain.gateway_port, plain.relay_timeout_s, plain.cilium_timeout_s), (8080, 5.0, 10.0))

    def test_explicit_cli_feeds_replace_each_toml_list_including_abbreviation(self):
        path = self.write(
            '[observation_feeds]\nhubble = "file-hubble.jsonl"\n[observation_heartbeats]\nhubble = "file-health.json"\n'
        )
        config = self.capture(
            [
                "mvp",
                "--config",
                path,
                "--observation-f",
                "tetragon=cli-tetragon.jsonl",
                "--observation-h",
                "tetragon=cli-health.json",
            ]
        )
        self.assertEqual(config.observation_feeds, (("tetragon", "cli-tetragon.jsonl"),))
        self.assertEqual(config.observation_heartbeats, (("tetragon", "cli-health.json"),))

    def test_unknown_forbidden_and_wrong_types_are_usage_errors(self):
        cases = [
            ("unknown = 1", "node config: unknown key: unknown"),
            ('token = "never-print-this-value"', "node config: unknown key: token"),
            ('host = "127.0.0.1"', "node config: host is fixed to 127.0.0.1"),
            ("policy_bundle = {}", "node config: policy_bundle is a verified result and cannot be configured"),
            ("max_concurrent_requests = true", "node config: max_concurrent_requests must be int"),
            ("gateway_port = 1.5", "node config: gateway_port must be int"),
            ("relay_timeout_s = true", "node config: relay_timeout_s must be float"),
            ("data_dir = false", "node config: data_dir must be str"),
            ("retention_enforce = 1", "node config: retention_enforce must be bool"),
            ('backend = "other"', "node config: backend must be one of mock, sglang, vllm"),
            ("observation_feeds = []", "node config: observation_feeds must be a table of kind = path"),
            ('[observation_feeds]\nother = "x"', "node config: observation_feeds has unknown kind: other"),
            ('[observation_feeds]\nhubble = " "', "node config: observation_feeds.hubble must be a non-empty path"),
            (
                "[observation_heartbeats]\nhubble = 1",
                "node config: observation_heartbeats.hubble must be a non-empty path",
            ),
        ]
        for text, message in cases:
            with self.subTest(text=text):
                code, run, out, err = self.invoke(["mvp", "--config", self.write(text)])
                self.assertEqual((code, out), (2, ""))
                self.assertEqual(err.splitlines()[-1], "serving-runtime: error: " + message)
                self.assertNotIn("never-print-this-value", err)
                run.assert_not_called()

    def test_policy_bundle_conflicts_from_toml_keep_exact_existing_error(self):
        path = self.write('policy_path = "policy.json"\n')
        code, run, out, err = self.invoke(["mvp", "--config", path, "--policy-bundle", "reviewed"])
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(
            err.splitlines()[-1],
            "serving-runtime: error: --policy-bundle은 다음 명시 옵션과 함께 쓸 수 없습니다: --policy",
        )
        run.assert_not_called()

    def test_missing_and_unreadable_config_keep_mvp_oserror_path(self):
        code, run, out, err = self.invoke(["mvp", "--config", str(self.root / "missing.toml")])
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("MVP 실행 실패: "))
        run.assert_not_called()
        self.write("gateway_port = 19080\n")
        original = Path.open
        target = self.path

        def open_file(path, *args, **kwargs):
            if path == target:
                raise PermissionError("configuration is unreadable")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", open_file):
            code, run, out, err = self.invoke(["mvp", "--config", str(self.path)])
        self.assertEqual((code, out, err), (1, "", "MVP 실행 실패: configuration is unreadable\n"))
        run.assert_not_called()

    def test_invalid_toml_is_an_error_and_other_commands_reject_config(self):
        code, run, out, err = self.invoke(["mvp", "--config", self.write("gateway_port = [")])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("node config: invalid TOML:", err)
        run.assert_not_called()
        code, run, out, err = self.invoke(["file-info", "unread", "--config", str(self.path)])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("unrecognized arguments: --config", err)
        run.assert_not_called()

    def test_empty_config_keeps_all_code_defaults(self):
        config = self.capture(["mvp", "--config", self.write("")])
        plain = self.capture(["mvp"])
        self.assertEqual(config, plain)

    def test_config_requires_full_spelling_but_existing_cli_abbreviations_work(self):
        path = self.write("gateway_port = 19080\n")
        for option in ("--conf", "--conf=" + path):
            args = ["mvp", option] + ([path] if "=" not in option else [])
            code, run, out, err = self.invoke(args)
            self.assertEqual((code, out), (2, ""))
            detail = option if "=" in option else option + " " + path
            self.assertEqual(err.splitlines()[-1], "serving-runtime: error: unrecognized arguments: " + detail)
            run.assert_not_called()
        config = self.capture(["mvp", "--config", path, "--gateway-p", "19081"])
        self.assertEqual(config.gateway_port, 19081)
