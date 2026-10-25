"""The update CLI forwards the same judgement time used for offline replay."""
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from guard import cli
from tests.test_cli import workdir
from tests.test_update_cli import inputs


@pytest.mark.parametrize("value, expected", [
    ("2026-10-24T10:00:00Z", datetime(2026, 10, 24, 10, tzinfo=timezone.utc)),
    ("2026-10-24T19:00:00+09:00", datetime.fromisoformat("2026-10-24T19:00:00+09:00")),
    ("2026-10-24T10:00:00", datetime(2026, 10, 24, 10, tzinfo=timezone.utc)),
    (None, datetime(2026, 10, 24, 12, tzinfo=timezone.utc)),
])
def test_update_forwards_explicit_or_current_as_of(workdir, monkeypatch, value, expected):
    inputs()
    called = []
    original = cli.execute_update

    def execute(*args):
        called.append(args[-1])
        return original(*args)

    monkeypatch.setattr(cli, "execute_update", execute)
    monkeypatch.setattr(cli, "utcnow", lambda: datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
    args = ["update", "--pack", "packs/PACK-BASELINE", "--baseline", "baseline.yaml",
            "--candidate", "candidate.yaml", "--policy", "examples/policy.yaml", "--output", "updates"]
    if value is not None:
        args += ["--as-of", value]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output + repr(result.exception)
    assert called == [expected]


def test_invalid_update_as_of_cannot_start_execution(workdir, monkeypatch):
    inputs()
    monkeypatch.setattr(cli, "execute_update", lambda *args: pytest.fail("execution before time validation"))
    result = CliRunner().invoke(cli.app, ["update", "--pack", "packs/PACK-BASELINE", "--baseline", "baseline.yaml",
        "--candidate", "candidate.yaml", "--policy", "examples/policy.yaml", "--as-of", "invalid"])
    assert result.exit_code != 0
    assert not (workdir / ".guard").exists()
