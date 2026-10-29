"""공개 session seal의 정확 선택·전후 세대·tail 확인 경계. 원격은 EXAMPLE이다."""
import ast
import json

import pytest

from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader, KubernetesCaptureError, supervised_source
from guard.executors.session_seal import SESSION_SEALER
from tests.test_kubernetes_lifecycle import group, target, World, PRIVATE


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
        assert command[7:] == [group.prepared["session"]] and 0 < float(command[6]) <= 5
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
            reader.exec_bounded("engine", ["python3", "-c", SESSION_SEALER, other], timeout=5, max_bytes=4096)
    assert not reader.rows


def test_existing_native_lab_gate_is_not_released_by_seal_support(group, target):
    target["lab"] = False
    with pytest.raises(ValueError):
        KubernetesLifecycleReader(target, group.env, group.prepared,
            runner=lambda *a, **k: pytest.fail("unexpected remote call"))
