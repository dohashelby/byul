"""Run original pinned serialization/buffer-lifetime tests with real CPU modules.

No engine constructor, model, external endpoint or GPU is used. The original
tests own their local inproc ZMQ context and cleanup. Raw pytest diagnostics
stay in memory; only validated counters, timing and source metadata persist.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from tools.b2_decoder_cpu import verify_source

PREFIX = "B2_MESSAGE_SUMMARY="
NAMES = {"CustomClass", "RequestWithTensor", "_logprobs_outputs",
         "test_custom_class_serialization_disallowed_without_pickle",
         "test_payload_buffer_reuse_does_not_corrupt_in_flight_messages",
         "test_zero_copy_frames_survive_without_caller_side_references"}


def safe_summary(stdout, returncode):
    candidates = [line.split(PREFIX, 1)[1] for line in stdout.splitlines() if PREFIX in line]
    value = None
    if candidates:
        try:
            parsed = json.loads(candidates[-1])
            counts = ("tests", "passed", "failed", "error", "skipped")
            if (type(parsed) is dict and set(parsed) == {*counts, "seconds"}
                    and all(type(parsed[key]) is int and parsed[key] >= 0 for key in counts)
                    and type(parsed["seconds"]) in (int, float) and math.isfinite(parsed["seconds"])
                    and parsed["seconds"] >= 0):
                value = parsed
        except (TypeError, ValueError):
            pass
    if value is None:
        return {"status": "SUMMARY_UNAVAILABLE", "returncode": returncode, "tests": None,
                "passed": None, "failed": None, "error": None, "skipped": None, "seconds": None}
    success = (returncode == 0 and value["tests"] == value["passed"] == 3
               and value["failed"] == value["error"] == value["skipped"] == 0)
    return {"status": "PASSED" if success else "NOT_PASSED", "returncode": returncode, **value}


def child_environment(root, repo):
    """Own tools/product package first; the pinned target supplies vLLM last."""
    return {**os.environ, "PYTHONPATH": os.pathsep.join([str(repo), str(repo / "src"), str(root)]),
            "VLLM_TARGET_DEVICE": "cpu", "CUDA_VISIBLE_DEVICES": "", "VLLM_ALLOW_INSECURE_SERIALIZATION": "0"}


def verify_own_helper():
    actual = Path(inspect.getfile(verify_source)).resolve()
    expected = Path(__file__).resolve().with_name("b2_decoder_cpu.py")
    if not actual.is_file() or not expected.is_file() or not actual.samefile(expected):
        raise ValueError("imported decoder helper is outside the own checkout")


def compile_official_definitions(path):
    tree = ast.parse(path.read_bytes(), filename=str(path))
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in NAMES]
    if {node.name for node in nodes} != NAMES:
        raise ValueError("official test definition inventory mismatch")
    tree.body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes]
    ast.fix_missing_locations(tree)
    return compile(tree, str(path), "exec", optimize=0)


def load_official_tests(root, namespace):
    if sys.flags.optimize:
        raise ValueError("normal Python required for official verification assertions")
    verify_own_helper()
    verify_source(root)
    import pytest
    import torch
    from dataclasses import dataclass
    if torch.version.cuda is not None:
        raise ValueError("CPU-only torch required")
    from vllm.v1.serial_utils import MsgpackEncoder, MsgpackDecoder
    from vllm.v1.engine.core import EngineCoreProc
    from vllm.v1.engine import EngineCoreOutputs

    for obj in (MsgpackEncoder, MsgpackDecoder, EngineCoreProc, EngineCoreOutputs):
        if not Path(inspect.getfile(obj)).resolve().is_relative_to(root / "vllm"):
            raise ValueError("imported target source is outside the pinned checkout")
    path = root / "tests/v1/test_serial_utils.py"
    namespace.update(torch=torch, pytest=pytest, dataclass=dataclass,
                     MsgpackEncoder=MsgpackEncoder, MsgpackDecoder=MsgpackDecoder)
    # Keep pytest's harness identity; code objects retain upstream locations.
    exec(compile_official_definitions(path), namespace)


def run(root, output):
    if sys.flags.optimize:
        raise ValueError("normal Python required for official verification assertions")
    verify_own_helper()
    pin = verify_source(root)
    output.mkdir(parents=True, exist_ok=False)
    harness = output / "test_official_message.py"
    harness.write_text("from pathlib import Path\nimport tools.b2_message_cpu as message_tool\n"
                       + f"if not Path(message_tool.__file__).samefile(Path({str(Path(__file__).resolve())!r})):\n"
                       + " raise ValueError('imported message tool is outside the own checkout')\n"
                       + f"message_tool.load_official_tests(Path({str(root)!r}), globals())\n")
    launcher = output / "run_tests.py"
    launcher.write_text(
        "import pytest,sys,time,json\nstart=time.perf_counter()\n"
        "class Summary:\n"
        " def pytest_sessionfinish(self,session,exitstatus):\n"
        "  stats=session.config.pluginmanager.get_plugin('terminalreporter').stats\n"
        "  result={key:len(stats.get(key,[])) for key in ('passed','failed','error','skipped')}\n"
        "  result.update(tests=session.testscollected,seconds=time.perf_counter()-start)\n"
        + f"  print({PREFIX!r}+json.dumps(result))\n"
        "raise SystemExit(pytest.main(sys.argv[1:],plugins=[Summary()]))\n")
    repo = Path(__file__).resolve().parents[1]
    env = child_environment(root, repo)
    result = subprocess.run([sys.executable, str(launcher), "-q", str(harness), "--noconftest",
        "-p", "no:cacheprovider", "--override-ini=addopts=", "--basetemp", str(output / "temp")],
        capture_output=True, text=True, env=env)
    safe = safe_summary(result.stdout, result.returncode)
    summary = {"schema": "byul-b2-official-message-cpu/1", "source": pin,
        "selected_definitions": sorted(NAMES), "official_test_assertions_modified": False,
        "real_target_modules_imported": True if safe["status"] == "PASSED" else None,
        "transport": "local inproc ZMQ in the original unit tests",
        "fixture_origin": "EXAMPLE", "scope": "source-reviewed CPU unit path; no engine constructor/model/HTTP/GPU",
        "own_tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "result": safe}
    with (output / "summary.json").open("x") as stream:
        json.dump(summary, stream, indent=2); stream.write("\n")
    print(json.dumps(summary["result"]))
    if summary["result"]["status"] != "PASSED":
        raise RuntimeError("official CPU message tests did not pass; raw diagnostics were not saved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.source.resolve(), args.output.resolve())
