"""Run bootstrap/document TAG probes with the real update adapter."""

import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_bootstrap_tag_probe_imports_real_update():
    path = ROOT / "runtime/project-context/refactor-2026-11/wp7/bootstrap_server.py"
    tree = ast.parse(path.read_text())
    probes = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "from guard.adapters.vllm_engine_callback_sensor import TAG" in node.value
    ]
    assert len(probes) == 1
    check = (
        "from pathlib import Path; import guard.adapters.vllm_engine_callback_sensor as adapter; assert Path(adapter.__file__).resolve().is_relative_to(Path("
        + repr(str(ROOT / "update"))
        + ").resolve()); "
    )
    result = subprocess.run([sys.executable, "-c", check + probes[0]], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_document_tag_probes_import_real_update():
    doc = (ROOT / "runtime/deploy/install/첫_설치_준비.md").read_text()
    probes = [
        line.split(" -c '", 1)[1].rsplit("'", 1)[0]
        for line in doc.splitlines()
        if " -c 'from guard.adapters.vllm_engine_callback_sensor import TAG" in line
    ]
    assert len(probes) == 2
    for probe in probes:
        result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
