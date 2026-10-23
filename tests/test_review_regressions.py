"""Previously reproduced false PASS and snapshot availability regressions."""
from pathlib import Path

import pytest

from guard.executors.container_config import MOUNTS_CMD, PROC_CMD
from guard.models import Verdict
from guard.runtime.docker import CmdResult
from tests.test_container_config import FakeDocker, _run
from tests.test_pack_snapshot import _ok, workdir


class IncompleteDocker(FakeDocker):
    def __init__(self, text=None, mounts=None):
        super().__init__("hardened")
        self.text, self.mounts = text, mounts

    def exec(self, container, argv, *, timeout):
        if argv == ["sh", "-c", PROC_CMD] and self.text is not None:
            return CmdResult(0, self.text, "")
        if argv == ["sh", "-c", MOUNTS_CMD] and self.mounts is not None:
            return CmdResult(0, self.mounts, "")
        return super().exec(container, argv, timeout=timeout)


VALID = "PID 1\nUid:\t1000\t1000\t1000\t1000\nCapEff:\t0000000000000000\n"


def test_incomplete_root_worker_retains_confirmed_failure(tmp_path):
    _, _, res = _run(tmp_path, IncompleteDocker(VALID + "PID 57\nUid:\t0\t0\t0\t0\n"))
    assert res.verdicts.security == Verdict.FAIL


def test_incomplete_nonroot_worker_is_not_pass(tmp_path):
    _, _, res = _run(tmp_path, IncompleteDocker(VALID + "PID 57\nUid:\t1000\t1000\t1000\t1000\n"))
    assert res.verdicts.security == Verdict.UNKNOWN


@pytest.mark.parametrize("mounts", ["", "malformed mount output\n"])
def test_unreadable_mount_inventory_is_unknown(tmp_path, mounts):
    _, _, res = _run(tmp_path, IncompleteDocker(mounts=mounts))
    assert res.verdicts.security == Verdict.UNKNOWN


@pytest.mark.parametrize("broken", ["missing", "corrupt"])
def test_rejudge_uses_valid_snapshot_without_readable_disk_pack(workdir, broken):
    plan = _ok(["select", "--pack", "packs/PACK-BASELINE", "--env", "examples/env.yaml",
                "--policy", "examples/policy.yaml"]).strip().splitlines()[-1]
    _ok(["run", "--plan", plan])
    folder = Path("packs/PACK-BASELINE")
    if broken == "missing":
        folder.rename("packs/saved-original")
    else:
        (folder / "checks/REQ-01.yaml").write_bytes(b"corrupt")
    _ok(["judge", "--plan", plan])
    _ok(["report", "--plan", plan, "--json", "review.json"])
    assert "OK" in _ok(["verify"])
