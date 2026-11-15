"""The real launcher returns immediately and guards a failed directory change."""

import ast
from pathlib import Path
import shlex
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def launch(directory, worker):
    tree = ast.parse((ROOT / "labtools/wp7/execute_from_mac.py").read_text())
    node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "launch" for target in node.targets)
    )
    return eval(
        compile(ast.Expression(node.value), "actual_launch_expression", "eval"),
        {"shlex": shlex, "remote_dir": str(directory), "worker": worker},
    )


def test_real_launcher_does_not_hold_ssh_output_pipe_until_worker_finishes(tmp_path):
    start = time.monotonic()
    result = subprocess.run(["/bin/sh", "-c", launch(tmp_path, "sleep 2")], capture_output=True, text=True, timeout=4)
    assert result.returncode == 0
    assert time.monotonic() - start < 0.75


def test_failed_remote_directory_change_never_starts_worker(tmp_path):
    marker = tmp_path / "unexpected"
    result = subprocess.run(
        ["/bin/sh", "-c", launch(tmp_path / "absent", "touch " + shlex.quote(str(marker)))],
        capture_output=True,
        text=True,
        timeout=4,
    )
    assert result.returncode != 0
    assert not marker.exists()
