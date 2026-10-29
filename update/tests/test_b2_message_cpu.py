"""Only our safe result boundary; real source tests run in the isolated CPU tool."""
import json
import builtins
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



@pytest.mark.parametrize("flags,environment_optimize", [(["-O"], None), (["-OO"], None), ([], "1")])
@pytest.mark.parametrize("entry", ["run", "load_official_tests"])
def test_optimized_entry_is_rejected_before_source_or_output(tmp_path, flags, environment_optimize, entry):
    missing_source = tmp_path / "missing-source"
    output = tmp_path / "must-not-exist"
    repo = Path(runner.__file__).resolve().parents[1]
    env = runner.child_environment(missing_source, repo)
    env.pop("PYTHONOPTIMIZE", None)
    if environment_optimize is not None:
        env["PYTHONOPTIMIZE"] = environment_optimize
    invocation = (f"message_tool.run(Path({str(missing_source)!r}), Path({str(output)!r}))" if entry == "run"
                  else f"message_tool.load_official_tests(Path({str(missing_source)!r}), {{}})")
    script = (
        "from pathlib import Path\nimport tools.b2_message_cpu as message_tool\ntry:\n " + invocation + "\n"
        "except ValueError as exc:\n"
        " if str(exc) != 'normal Python required for official verification assertions': raise\n"
        " print('OPTIMIZATION_REJECTED')\n"
        "else: raise RuntimeError('optimized verification was accepted')\n"
    )
    result = subprocess.run([sys.executable, *flags, "-c", script], cwd=tmp_path,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip() == "OPTIMIZATION_REJECTED"
    assert not missing_source.exists() and not output.exists()


def test_selected_definition_assertions_survive_an_optimized_compile_default(tmp_path, monkeypatch):
    path = tmp_path / "official_tests.py"
    source = "import unavailable_engine\n"
    for name in sorted(runner.NAMES):
        if name in {"CustomClass", "RequestWithTensor"}:
            source += f"class {name}: pass\n"
        elif name.startswith("test_"):
            source += f"def {name}():\n    assert False, 'original assertion'\n"
        else:
            source += f"def {name}(): return None\n"
    path.write_text(source)
    def compile_with_optimized_default(*args, **kwargs):
        kwargs.setdefault("optimize", 2)
        return builtins.compile(*args, **kwargs)
    monkeypatch.setattr(runner, "compile", compile_with_optimized_default, raising=False)
    namespace = {}
    exec(runner.compile_official_definitions(path), namespace)
    tests = [namespace[name] for name in runner.NAMES if name.startswith("test_")]
    assert len(tests) == 3
    for function in tests:
        assert function.__code__.co_filename == str(path)
        with pytest.raises(AssertionError, match="original assertion"):
            function()
