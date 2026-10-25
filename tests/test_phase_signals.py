"""SP3 engine-log phase signal extraction and ExecPolicy connection."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from serving_runtime.cli import main
from serving_runtime.enforcement.execpolicy import ExecPolicy, parse_timeline
from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.enforcement.phase_signals import (
    ENGINE_PATTERN_SETS,
    extract_phase_timeline,
    parse_timezone,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/enforcement/vllm_cpu_029_startup_excerpt.log"
INTENT = ROOT / "examples/enforcement/intent-v1.1.example.json"


class PhaseSignalExtractionTests(unittest.TestCase):
    def test_observed_cpu_excerpt_uses_only_certain_boundaries(self) -> None:
        timeline = extract_phase_timeline(
            FIXTURE, engine="vllm", year=2026, timezone_offset="+00:00"
        )
        self.assertEqual(
            timeline["entries"],
            [
                {
                    "phase": "loading",
                    "from": "2026-10-23T05:47:12+00:00",
                    "until": "2026-10-23T05:47:24+00:00",
                    "basis": "engine_signal",
                },
                {
                    "phase": "compiling",
                    "from": "2026-10-23T05:47:24+00:00",
                    "until": "2026-10-23T05:47:58+00:00",
                    "basis": "engine_signal",
                },
            ],
        )
        extraction = timeline["extraction"]
        self.assertEqual(extraction["engine"], "vllm")
        self.assertEqual(extraction["runs_observed"], 2)
        self.assertEqual(extraction["lines_read"], 12)
        self.assertEqual(extraction["last_position"]["line"], 12)
        self.assertGreater(extraction["last_position"]["byte_offset"], 0)
        self.assertIn(
            {"line": 8, "signal": "inferring_start", "reason": "timestamp_missing"},
            extraction["omitted_boundaries"],
        )
        self.assertIn(
            {"line": 12, "signal": "compiling", "reason": "compile_markers_not_observed"},
            extraction["omitted_boundaries"],
        )
        self.assertNotIn("inferring", [item["phase"] for item in timeline["entries"]])
        self.assertEqual(len(parse_timeline(timeline)), 2)

    def test_two_timestamped_restarts_close_each_inferring_interval(self) -> None:
        log = b"\n".join(
            (
                b"INFO 10-23 01:00:00 Loading model from scratch...",
                b"INFO 10-23 01:00:05 Model loading took 1 GiB memory and 5 seconds",
                b"INFO 10-23 01:00:06 Application startup complete.",
                b"INFO 10-23 02:00:00 Loading model from scratch...",
                b"INFO 10-23 02:00:05 Model loading took 1 GiB memory and 5 seconds",
                b"INFO 10-23 02:00:06 Application startup complete.",
            )
        ) + b"\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "restart.log"
            path.write_bytes(log)
            timeline = extract_phase_timeline(
                path, engine="vllm", year=2026, timezone_offset="+09:00"
            )
        inferring = [item for item in timeline["entries"] if item["phase"] == "inferring"]
        self.assertEqual(len(inferring), 2)
        self.assertEqual(timeline["extraction"]["runs_observed"], 2)
        self.assertEqual(inferring[0]["until"], "2026-10-23T02:00:00+09:00")
        self.assertIsNone(inferring[1]["until"])
        self.assertTrue(all(item["basis"] == "engine_signal" for item in inferring))

    def test_sglang_and_invalid_time_inputs_fail_without_guessing(self) -> None:
        self.assertEqual(ENGINE_PATTERN_SETS["sglang"].patterns, ())
        with self.assertRaisesRegex(ValueError, "no source-backed patterns"):
            extract_phase_timeline(
                FIXTURE, engine="sglang", year=2026, timezone_offset="+00:00"
            )
        for value in ("UTC", "local", "+24:00", "+00:60"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_timezone(value)

    def test_cli_output_is_consumed_by_exec_policy_as_engine_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timeline.json"
            code = main(
                [
                    "enforce", "phase-signals", "--engine", "vllm",
                    "--log", str(FIXTURE), "--year", "2026", "--tz", "+00:00",
                    "--out", str(output),
                ]
            )
            self.assertEqual(code, 0)
            document = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], "runtime-phase-timeline/1.0")

            intent_document = json.loads(INTENT.read_text(encoding="utf-8"))
            # The engine kind affects the cache generation path, not phase parsing.
            intent_document["assets"]["cache_generation"]["engine"] = "sglang"
            intent_document["protected_paths"]["cache_dir"] = (
                "/var/cache/serving/sglang/image-example/revision-example/default"
            )
            policy = ExecPolicy(parse_intent(intent_document), timeline_path=output)
            result = policy.classify_exec(
                binary="/usr/bin/python3",
                parent=None,
                image=None,
                observed_at="2026-10-23T05:47:30+00:00",
            )
            self.assertEqual(result["phase"], ["compiling"])
            self.assertEqual(result["phase_basis"], ["engine_signal"])
            self.assertEqual(result["exec_verdict"], "allowed_in_phase")


if __name__ == "__main__":
    unittest.main()
