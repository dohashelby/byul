"""로컬 Docker 실험실: 두 위상을 guard select/run/judge/report CLI 전체 경로로 실행.

    uv run pytest -m docker -s

exposed → REQ-01 FAIL, isolated → PASS. 끝나면 guard.run 라벨의 컨테이너·네트워크가 남지 않아야 한다.
키는 실행 중 임의 생성해 이 프로세스 환경에만 둔다(파일·로그에 남기지 않음).
"""

import json
import secrets
import subprocess

import pytest
import yaml
from typer.testing import CliRunner

from guard.cli import app
from guard.runtime.docker import Docker
from lab import lab
from tests.builders import ROOT, baseline_pack

pytestmark = pytest.mark.docker
runner = CliRunner()


def _docker_ok() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _labelled(kind: str) -> list[str]:
    cmd = ["docker", "ps", "-aq"] if kind == "containers" else ["docker", "network", "ls", "-q"]
    out = subprocess.run([*cmd, "--filter", "label=guard.run"], capture_output=True, timeout=30, text=True).stdout
    return out.split()


def _ok(args, env):
    res = runner.invoke(app, args, env=env)
    assert res.exit_code == 0, res.output + repr(res.exception)
    return res.output


@pytest.mark.parametrize("topology, expected", [("exposed", "FAIL"), ("isolated", "PASS")])
def test_lab_topology_end_to_end(tmp_path, topology, expected):
    if not _docker_ok():
        pytest.fail("Docker daemon is not available; this test must not pass without running")
    allowed, denied = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    run_id = f"lab{secrets.token_hex(4)}"
    run_dir = tmp_path / "run"
    # Keep the lab's declared values and scope; bind this temporary fixture to
    # the exact pack used by the test without rewriting its historical input.
    policy_data = yaml.safe_load((ROOT / "lab/policy.yaml").read_text(encoding="utf-8"))
    policy_data["pack"] = baseline_pack().ref().model_dump(mode="json")
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(yaml.safe_dump(policy_data, allow_unicode=True), encoding="utf-8")
    env = {"GUARD_LAB_ALLOWED_KEY": allowed, "GUARD_LAB_DENIED_KEY": denied}
    with Docker(run_id) as docker:  # 실패해도 __exit__에서 cleanup
        lab.up(docker, topology, run_dir, allowed)
        cfg = run_dir / "guard.toml"
        cfg.write_text(
            f'store = "{(run_dir / ".guard").as_posix()}"\n[adapter]\nname = "vllm"\n[executors]\ndirect_route = "real"\n'
            f'[direct_route]\ntarget = "{(run_dir / "target.json").as_posix()}"\nallowed_key_env = "GUARD_LAB_ALLOWED_KEY"\n'
            f'denied_key_env = "GUARD_LAB_DENIED_KEY"\nrequest_timeout = 3.0\n', encoding="utf-8")
        c = ["--config", str(cfg)]
        out = _ok(["select", "--pack", str(ROOT / "packs/PACK-BASELINE"), "--env", str(run_dir / "env.yaml"),
                   "--policy", str(policy_path), *c], env)
        plan = out.strip().splitlines()[-1]
        pack = ["--pack", str(ROOT / "packs/PACK-BASELINE")]
        print(_ok(["run", "--plan", plan, *pack, *c], env))
        print(_ok(["judge", "--plan", plan, *pack, *c], env))
        _ok(["report", "--plan", plan, *pack, *c, "--json", str(run_dir / "report.json")], env)
        gw_logs = docker.logs(docker.name("gw"), timeout=30)
        engine_logs = docker.logs(docker.name("engine"), timeout=30)

    data = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    (row,) = [r for r in data["by_check"] if r["check_id"] == "REQ-01"]
    summary = None
    for p in (run_dir / ".guard/artifacts").iterdir():
        last = p.read_text(encoding="utf-8").strip().splitlines()[-1]
        if '"summary": true' in last:
            summary = json.loads(last)
    print(f"\n[{topology}] REQ-01 security={row['single']['security']} level={row['single']['verification_level']}")
    print(f"[{topology}] direct_outcomes={summary['direct_outcomes']}")
    print(f"[{topology}] direct_allowed_outcomes={summary['direct_allowed_outcomes']}")
    print(f"[{topology}] gateway_outcomes={summary['gateway_outcomes']} trusted={summary['trusted_origin_reachable']}")
    (run_dir / "observed.json").write_text(json.dumps({"topology": topology, **summary}, indent=2), encoding="utf-8")

    assert row["single"]["security"] == expected
    assert row["single"]["verification_level"] == "EXAMPLE"  # 실험실 증거는 EXAMPLE
    assert data["official"] is False

    # 키가 어떤 파일에도 남지 않았다 (저장소·증거·보고서·target·env·로그)
    for p in run_dir.rglob("*"):
        if p.is_file():
            text = p.read_bytes().decode("utf-8", "replace")
            assert allowed not in text and denied not in text, p
    assert allowed not in gw_logs + engine_logs and denied not in gw_logs + engine_logs

    # 정리 확인: guard.run 라벨 컨테이너·네트워크 0개
    assert _labelled("containers") == [] and _labelled("networks") == []
