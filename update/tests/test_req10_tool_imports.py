"""Developer tools must reject another checkout before producing evidence."""
from pathlib import Path
import shutil
import subprocess
import sys
import os

import pytest


@pytest.mark.parametrize('tool,output_flag', [
    ('req10_tools_cpu.py', '--output'),
    ('req10_tools_cpu_pipeline.py', '--output-root'),
])
def test_tools_reject_foreign_product_checkout_before_output(tmp_path, tool, output_flag):
    update = Path(__file__).resolve().parents[1]
    foreign = tmp_path / 'foreign'
    shutil.copytree(update / 'src/guard', foreign / 'guard', ignore=shutil.ignore_patterns('__pycache__'))
    output = tmp_path / 'new-evidence'
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(foreign), str(update)]))
    result = subprocess.run([sys.executable, str(update / 'tools' / tool),
        '--source-root', str(tmp_path / 'unavailable-source'), output_flag, str(output)],
        env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert 'own product import mismatch' in result.stderr
    assert not output.exists()
