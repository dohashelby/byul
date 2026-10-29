"""Only our safe result boundary; real source tests run in the isolated CPU tool."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import b2_message_cpu as runner
from tools.b2_message_cpu import PREFIX, safe_summary


def line(**changes):
    return PREFIX + json.dumps({"tests": 3, "passed": 3, "failed": 0, "error": 0, "skipped": 0,
                                "seconds": 1.0, **changes})


def test_exact_success_keeps_only_counters_and_timing():
    report = safe_summary("private fixture/exception\n" + line(), 0)
    assert report["status"] == "PASSED" and report["passed"] == 3
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize("output,rc", [(line(passed=2, failed=1), 1), (line(error=1), 1),
    (line(passed=2, skipped=1), 0), (line(), 2)])
def test_failure_skip_and_later_process_error_never_become_success(output, rc):
    result = safe_summary(output, rc)
    assert result["status"] == "NOT_PASSED"
    assert result["passed"] is not None  # Earlier observed counters are kept.


@pytest.mark.parametrize("output", ["", "private diagnostic", PREFIX + "{", line(passed=True), line(seconds=float("inf"))])
def test_missing_or_malformed_summary_is_unknown_not_zero(output):
    result = safe_summary(output, 0)
    assert result["status"] == "SUMMARY_UNAVAILABLE"
    assert result["passed"] is result["failed"] is None
    assert "private" not in json.dumps(result)



def test_child_import_prefers_own_tool_over_target_tools_package(tmp_path):
    target = tmp_path / "target"
    (target / "tools").mkdir(parents=True)
    (target / "tools/__init__.py").write_text("raise RuntimeError('target tools package selected')\n")
    (target / "tools/b2_message_cpu.py").write_text("raise RuntimeError('target tool selected')\n")
    (target / "vllm").mkdir()
    (target / "vllm/__init__.py").write_text("ORIGIN = 'synthetic-target'\n")
    own_tool = Path(runner.__file__).resolve()
    repo = own_tool.parents[1]
    script = (
        "from pathlib import Path\nimport tools.b2_message_cpu as message_tool\nimport vllm\n"
        + f"assert Path(message_tool.__file__).samefile(Path({str(own_tool)!r}))\n"
        + f"assert Path(vllm.__file__).samefile(Path({str(target / 'vllm/__init__.py')!r}))\n"
        + "assert vllm.ORIGIN == 'synthetic-target'\nprint('OWN_TOOL_AND_TARGET_SELECTED')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path,
                            env=runner.child_environment(target, repo), capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip() == "OWN_TOOL_AND_TARGET_SELECTED"


def test_own_decoder_helper_from_another_checkout_is_rejected(tmp_path, monkeypatch):
    foreign = tmp_path / "b2_decoder_cpu.py"
    foreign.write_text("def verify_source(root): return {}\n")
    namespace = {}
    exec(compile(foreign.read_text(), str(foreign), "exec"), namespace)
    monkeypatch.setattr(runner, "verify_source", namespace["verify_source"])
    with pytest.raises(ValueError, match="outside the own checkout"):
        runner.verify_own_helper()
