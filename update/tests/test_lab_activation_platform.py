"""The controller can run on Windows while signals target Linux containers."""
import os
import subprocess
import sys


def test_registry_and_cli_import_without_host_posix_signals():
    program = '''import signal
for name in ("SIGKILL", "SIGSTOP", "SIGCONT"):
    if hasattr(signal, name): delattr(signal, name)
from guard.executors.registry import resolve_executor
from guard.executors import lab_activation as lab
from guard.cli import app
assert resolve_executor("engine_sensor", "kubernetes") is not None
assert lab.FAULTS["death"][0] == 9
assert lab.FAULTS["delay"][0] == 19
assert lab.POSIX_SIGCONT == 18
'''
    done = subprocess.run([sys.executable, '-c', program], env=dict(os.environ), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
