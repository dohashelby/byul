"""Tests for metric-map checks: declared TYPE/HELP, label keys, counter resets."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from serving_runtime.cli import main
from serving_runtime.mvp.backend import (
    BackendConfig,
    parse_prometheus_metrics,
    unmapped_metric_families,
)
from serving_runtime.mvp.engine_client import EngineClient


ROOT = Path(__file__).resolve().parents[1]
SGLANG_EXAMPLE = ROOT / "examples/mvp/backend-sglang.example.json"
DOC = "https://example.invalid/metrics"


def mapping(metric: str, metric_type: str) -> dict[str, object]:
    return {"metric": metric, "type": metric_type, "unit": "x", "source_doc": DOC, "verified": False}


METRICS_MAP = {
    "running": mapping("engine:running", "gauge"),
    "generated": mapping("engine:generated_tokens_total", "counter"),
    "ttft": mapping("engine:ttft_seconds", "histogram"),
    "wrong": mapping("engine:queue", "gauge"),
    "missing": mapping("engine:absent", "gauge"),
}

PAYLOAD = (
    "# HELP engine:running Running requests.\n"
    "# TYPE engine:running gauge\n"
    'engine:running{model_name="m",engine="0"} 2\n'
    "# HELP engine:generated_tokens Generated tokens.\n"
    "# TYPE engine:generated_tokens counter\n"
    'engine:generated_tokens_total{model_name="m"} 10\n'
    "# TYPE engine:ttft_seconds histogram\n"
    'engine:ttft_seconds_bucket{le="0.1"} 1\n'
    "engine:ttft_seconds_sum 0.05\n"
    "engine:ttft_seconds_count 1\n"
    "# TYPE engine:queue counter\n"
    "engine:queue 3\n"
    "# TYPE engine:unmapped gauge\n"
    "engine:unmapped 1\n"
).encode("utf-8")


class MetricMapParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = parse_prometheus_metrics(PAYLOAD, METRICS_MAP)

    def test_declared_type_is_checked(self) -> None:
        self.assertEqual(self.parsed["running"]["type_check"], "match")
        self.assertEqual(self.parsed["running"]["state"], "ok")
        self.assertEqual(self.parsed["ttft"]["type_check"], "match")
        self.assertEqual(self.parsed["wrong"]["declared_type"], "counter")
        self.assertEqual(self.parsed["wrong"]["type_check"], "mismatch")
        self.assertEqual(self.parsed["wrong"]["state"], "type_mismatch")
        self.assertEqual(self.parsed["missing"]["state"], "unsupported_or_absent")
        self.assertEqual(self.parsed["missing"]["type_check"], "not_declared")

    def test_counter_family_declared_without_total_suffix(self) -> None:
        generated = self.parsed["generated"]
        self.assertEqual(generated["declared_type"], "counter")
        self.assertEqual(generated["state"], "ok")
        self.assertEqual(generated["help"], "Generated tokens.")

    def test_counter_declared_with_total_suffix(self) -> None:
        payload = (
            "# HELP engine:generated_tokens_total Generated.\n"
            "# TYPE engine:generated_tokens_total counter\n"
            "engine:generated_tokens_total 4\n"
        ).encode()
        parsed = parse_prometheus_metrics(payload, {"generated": METRICS_MAP["generated"]})
        self.assertEqual(parsed["generated"]["type_check"], "match")

    def test_undeclared_samples_stay_ok(self) -> None:
        parsed = parse_prometheus_metrics(b"engine:running 1\n", {"running": METRICS_MAP["running"]})
        self.assertEqual(parsed["running"]["state"], "ok")
        self.assertEqual(parsed["running"]["type_check"], "not_declared")

    def test_label_keys_and_help_text(self) -> None:
        self.assertEqual(self.parsed["running"]["label_keys"], ["engine", "model_name"])
        self.assertEqual(self.parsed["running"]["help"], "Running requests.")
        noisy = ("# HELP engine:running " + "a\x1bb" + "z" * 300 + "\nengine:running 1\n").encode()
        help_text = parse_prometheus_metrics(noisy, {"running": METRICS_MAP["running"]})["running"]["help"]
        self.assertNotIn("\x1b", help_text)
        self.assertLessEqual(len(help_text), 200)

    def test_unmapped_families(self) -> None:
        self.assertEqual(unmapped_metric_families(PAYLOAD, METRICS_MAP), ["engine:unmapped"])


class CounterResetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = EngineClient(BackendConfig.mock(("127.0.0.1", 1)), 1.0)

    def poll(self, value: float) -> dict[str, object]:
        payload = f'engine:generated_tokens_total{{model_name="m"}} {value}\n'.encode()
        parsed = parse_prometheus_metrics(payload, {"generated": METRICS_MAP["generated"]})
        self.client._mark_counter_resets(parsed)
        return parsed["generated"]["counter_reset"]

    def test_decrease_is_flagged_and_remembered(self) -> None:
        self.assertFalse(self.poll(10)["detected_this_poll"])
        self.assertFalse(self.poll(15)["detected_this_poll"])
        reset = self.poll(2)
        self.assertTrue(reset["detected_this_poll"])
        self.assertEqual(reset["last_detected"]["samples"][0]["previous"], 15.0)
        self.assertEqual(reset["last_detected"]["samples"][0]["current"], 2.0)
        later = self.poll(3)
        self.assertFalse(later["detected_this_poll"])
        self.assertIsNotNone(later["last_detected"])

    def test_gauges_are_not_checked(self) -> None:
        parsed = parse_prometheus_metrics(b"engine:running 5\n", {"running": METRICS_MAP["running"]})
        self.client._mark_counter_resets(parsed)
        self.assertNotIn("counter_reset", parsed["running"])


class MetricsCheckCliTests(unittest.TestCase):
    def run_cli(self, payload: bytes, *extra: str) -> tuple[int, str]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "metrics.txt"
        path.write_bytes(payload)
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(
                ["metrics-check", "--backend-config", str(SGLANG_EXAMPLE), "--input", str(path), *extra]
            )
        return code, output.getvalue()

    def test_all_ok_returns_zero_and_json_report(self) -> None:
        payload = (
            "# TYPE sglang:num_running_reqs gauge\nsglang:num_running_reqs 1\n"
            "# TYPE sglang:num_queue_reqs gauge\nsglang:num_queue_reqs 0\n"
            "# TYPE sglang:token_usage gauge\nsglang:token_usage 0.1\n"
            "# TYPE sglang:generation_tokens_total counter\nsglang:generation_tokens_total 5\n"
            "# TYPE sglang:other gauge\nsglang:other 1\n"
        ).encode()
        code, output = self.run_cli(payload, "--json")
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertEqual(report["backend_kind"], "sglang")
        self.assertEqual(report["unmapped_families"], ["sglang:other"])
        self.assertEqual(report["fields"]["requests.running"]["sample_count"], 1)

    def test_missing_or_mismatched_returns_three(self) -> None:
        code, output = self.run_cli(b"# TYPE sglang:num_running_reqs counter\nsglang:num_running_reqs 1\n")
        self.assertEqual(code, 3)
        self.assertIn("type_mismatch", output)
        self.assertIn("unsupported_or_absent", output)


if __name__ == "__main__":
    unittest.main()
