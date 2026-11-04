from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.aws_metrics_names import REMOTE, compare_names, main


class MetricNamesTests(unittest.TestCase):
    def test_remote_code_emits_names_only_and_candidate_comparison(self):
        self.assertNotIn("print(raw)", REMOTE)
        config = {"metrics_map": {"requests.running": {"metric": "vllm:num_requests_running", "verified": False}}}
        self.assertEqual(compare_names(["vllm:num_requests_running", "other_metric"], config),
                         {"requests.running": {"candidate": "vllm:num_requests_running", "found": True}})
        with self.assertRaises(ValueError):
            compare_names(["bad{label=value}"], config)
        config["metrics_map"]["requests.running"]["verified"] = True
        with self.assertRaises(ValueError):
            compare_names(["vllm:num_requests_running"], config)

    def test_output_contains_no_sample_or_label_values(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "names.json"
            fake = SimpleNamespace(returncode=0, stdout=b'["vllm:num_requests_running", "other_metric"]', stderr=b"sensitive=secret")
            with patch("tools.aws_metrics_names.subprocess.run", return_value=fake):
                with redirect_stdout(io.StringIO()):
                    code = main(["--engine", "vllm", "--context", "byul-aws-lab", "--output", str(output)])
            self.assertEqual(code, 0)
            document = json.loads(output.read_text())
            self.assertEqual(document["candidate_comparison"]["requests.running"]["found"], True)
            self.assertNotIn("secret", output.read_text())
            self.assertNotIn("sample", output.read_text())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
