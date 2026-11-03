"""공개 session seal의 정확 선택·전후 세대·tail 확인 경계. 원격은 EXAMPLE이다."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader, KubernetesCaptureError, supervised_source
from guard.executors.session_seal import SESSION_SEALER
from tests.test_kubernetes_lifecycle import group, target, World, PRIVATE
from tests.test_lifecycle_capture import case, external
from tests.test_lifecycle_sensor import writer_factory


@pytest.mark.parametrize("damage", [None, "unclosed", "empty", "wrong-session", "bad-hash", "extra",
    "owner-after", "transport", "cleanup", "malformed"])
def test_seal_only_exact_prepared_session_with_terminal_response(group, target, damage):
    world = World(group, target)
    seals = []
    def runner(argv, **kwargs):
        if "exec" not in argv: return world.runner(argv, **kwargs)
        command = argv[argv.index("--") + 1:]
        assert command[:5] == ["python3", "-I", "-S", "-B", "-c"]
        tree = ast.parse(command[5])
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id == "compile")
        assert ast.literal_eval(call.args[0]) == SESSION_SEALER
        assert command[5] == supervised_source(SESSION_SEALER)
        assert command[7:] == [group.prepared["session"], "262144"] and 0 < float(command[6]) <= 5
        assert kwargs["max_stdout_bytes"] == 4096 and kwargs["max_stderr_bytes"] == 4096
        seals.append(command[7])
        value = {"schema": "guard-session-seal/1", "session": group.prepared["session"], "sealed": True,
                 "recordings_closed": 2, "terminal_inventory_sha256": "a" * 64}
        pipe = {"complete": True, "bounded_at_source": True, "timed_out": False, "truncated": False}
        if damage == "unclosed": value["sealed"] = False
        elif damage == "empty": value["recordings_closed"] = 0
        elif damage == "wrong-session": value["session"] = "b" * 32
        elif damage == "bad-hash": value["terminal_inventory_sha256"] = "invalid"
        elif damage == "extra": value["secret"] = PRIVATE
        elif damage == "owner-after": world.bad_owner = True
        elif damage == "transport": raise TimeoutError(PRIVATE)
        elif damage == "cleanup": pipe.update(pipes_closed=False, read_errors=["child_reap"])
        raw = b'{"sealed":true,"sealed":false}' if damage == "malformed" else json.dumps(value).encode()
        return {"returncode": 0, "stdout_bytes": raw, "stderr_bytes": PRIVATE.encode(), "capture": pipe}
    reader = KubernetesLifecycleReader(target, group.env, group.prepared, runner=runner)
    assert reader.seal_session() == {"sealed": damage is None}
    report = reader.report()
    assert seals == [group.prepared["session"]] and not report["engine_started"]
    assert report["producer_authenticated"] is report["node_runtime_image_verified"] is False
    assert report["collection_gap"] is (damage is not None)
    assert PRIVATE not in json.dumps(report) and "sealed" not in str(report.get("verdict", ""))
    if damage == "cleanup": assert report["parent_cleanup_unverified"] is True
    if damage is None:
        assert reader.seal_session() == {"sealed": True}
        assert len(seals) == 2  # Own fixed idempotent operation only; remote helper checks the marker.


def test_seal_program_does_not_accept_another_session_or_path(group, target):
    reader = KubernetesLifecycleReader(target, group.env, group.prepared,
        runner=lambda *a, **k: pytest.fail("unexpected remote call"))
    for other in ("b" * 32, "/guard-lifecycle/data/" + group.prepared["session"], "../SEALED"):
        with pytest.raises(KubernetesCaptureError):
            reader.exec_bounded("engine", ["python3", "-c", SESSION_SEALER, other, "262144"], timeout=5, max_bytes=4096)
    session = group.prepared["session"]
    for argv in ([session], [session, "2097152"], [session, "262144", "x"]):
        with pytest.raises(KubernetesCaptureError):
            reader.exec_bounded("engine", ["python3", "-c", SESSION_SEALER, *argv], timeout=5, max_bytes=4096)
    assert not reader.rows


def test_existing_native_lab_gate_is_not_released_by_seal_support(group, target):
    target["lab"] = False
    with pytest.raises(ValueError):
        KubernetesLifecycleReader(target, group.env, group.prepared,
            runner=lambda *a, **k: pytest.fail("unexpected remote call"))


def _sealer(root, session, limit, sizes):
    """Run the fixed sealer against an owned temporary session folder."""
    folder = root / session; folder.mkdir(mode=0o700)
    end = json.dumps({"seq": 2, "event": "END", "monotonic_ns": 1, "previous_sha256": "a" * 64,
                      "payload": {"reason": "BOUND_REACHED"}}).encode() + b"\n"
    for index, size in enumerate(sizes, 1):
        fd = os.open(folder / f"pid-{index}-{index}.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, b"#" * (size - len(end) - 1) + b"\n" + end); os.close(fd)
    source = SESSION_SEALER.replace("path='/guard-lifecycle/data/'+session", "path=" + repr(str(root) + "/") + "+session")
    assert source != SESSION_SEALER
    done = subprocess.run([sys.executable, "-I", "-c", source, session, str(limit)], capture_output=True, text=True, timeout=30)
    return done.returncode, (json.loads(done.stdout) if done.returncode == 0 else None)


@pytest.mark.parametrize("limit,sizes,closed", [
    (262144, [262144], 1), (262144, [300000], None),
    (2097152, [2081509, 4096], 2), (2097152, [2097153], None)])
def test_sealer_uses_the_recording_profile_bound_and_keeps_bound_reached_terminal(tmp_path, limit, sizes, closed):
    """#196 GB12: a 2 MiB profile writer's BOUND_REACHED tail seals; one byte more does not."""
    root = Path(tempfile.mkdtemp(dir=tmp_path.resolve()))
    code, value = _sealer(root, "c" * 32, limit, sizes)
    assert code == 0 and value["sealed"] is (closed is not None)
    assert value["recordings_closed"] == (closed or 0)


def test_sealer_refuses_an_unapproved_bound(tmp_path):
    root = Path(tempfile.mkdtemp(dir=tmp_path.resolve()))
    code, value = _sealer(root, "d" * 32, 4194304, [4096])
    assert code != 0 and value is None
