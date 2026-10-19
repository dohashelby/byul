"""실제 vLLM(5b): 두 위상을 guard select/run/judge/report CLI 전체 경로로 실행하고, INV-15 실제 기동 실패를 확인한다.

    uv run pytest -m engine -s

guard.toml [vllm] 설정(이미지 digest, 모델 디렉토리, revision)을 쓴다. 결과는 out/5b/<이름>/에 남긴다(키 없음).
기대: exposed → FAIL, isolated → PASS. 다르게 나오면 이 테스트는 실패하고, 그 결과를 그대로 보고한다(기대값을 고치지 않는다).
"""

import json
import secrets
import shutil
import subprocess
import time
import tomllib
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.runtime.docker import Docker
from guard.store import Store
from lab import lab
from tests.builders import ROOT

pytestmark = pytest.mark.engine
runner = CliRunner()
OUT = ROOT / "out/5b"
PACK = ROOT / "packs/PACK-BASELINE"


def _vllm_cfg() -> dict:
    return tomllib.loads((ROOT / "guard.toml").read_text(encoding="utf-8"))["vllm"]


def _labelled() -> tuple[list[str], list[str]]:
    c = subprocess.run(["docker", "ps", "-aq", "--filter", "label=guard.run"], capture_output=True, text=True, timeout=30)
    n = subprocess.run(["docker", "network", "ls", "-q", "--filter", "label=guard.run"], capture_output=True, text=True, timeout=30)
    return c.stdout.split(), n.stdout.split()


def _wildcard_listeners() -> list[str]:
    out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=30).stdout
    return sorted({" ".join(l.split()[i] for i in (0, 8)) for l in out.splitlines()[1:]
                   if " *:" in l or "[::]" in l})


def _ok(args, env):
    res = runner.invoke(app, args, env=env)
    assert res.exit_code == 0, res.output + repr(res.exception)
    return res.output


def _pipeline(run_dir: Path, env_vars: dict, cfg_toml: Path) -> dict:
    c = ["--config", str(cfg_toml)]
    out = _ok(["select", "--pack", str(PACK), "--env", str(run_dir / "env.yaml"), "--policy", str(ROOT / "lab/policy.yaml"), *c],
              env_vars)
    plan = out.strip().splitlines()[-1]
    pk = ["--pack", str(PACK)]
    t0 = time.monotonic()
    print(_ok(["run", "--plan", plan, *pk, *c], env_vars))
    run_s = time.monotonic() - t0
    print(_ok(["judge", "--plan", plan, *pk, *c], env_vars))
    _ok(["report", "--plan", plan, *pk, *c, "--json", str(run_dir / "report.json"), "--html", str(run_dir / "report.html")],
        env_vars)
    return {"plan": plan, "run_seconds": round(run_s, 1)}


def _write_cfg(run_dir: Path) -> Path:
    cfg = run_dir / "guard.toml"
    cfg.write_text(
        f'store = "{run_dir / ".guard"}"\n[adapter]\nname = "vllm"\n[executors]\ndirect_route = "real"\n'
        f'[direct_route]\ntarget = "{run_dir / "target.json"}"\nallowed_key_env = "GUARD_LAB_ALLOWED_KEY"\n'
        f'denied_key_env = "GUARD_LAB_DENIED_KEY"\nrequest_timeout = 3.0\nallow_mutating_routes = false\n', encoding="utf-8")
    return cfg


def _fresh(name: str) -> Path:
    d = OUT / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


@pytest.mark.parametrize("topology, expected", [("exposed", "FAIL"), ("isolated", "PASS")])
def test_vllm_topology_end_to_end(topology, expected):
    vcfg = _vllm_cfg()
    run_dir = _fresh(topology)
    before = _wildcard_listeners()
    allowed, denied = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    env_vars = {"GUARD_LAB_ALLOWED_KEY": allowed, "GUARD_LAB_DENIED_KEY": denied}
    t0 = time.monotonic()
    with Docker(f"v{secrets.token_hex(4)}") as docker:
        target = lab.up(docker, topology, run_dir, allowed, engine="vllm", vllm=vcfg)
        during = _wildcard_listeners()
        cfg = _write_cfg(run_dir)
        timing = _pipeline(run_dir, env_vars, cfg)
        engine_logs = docker.logs(docker.name("engine"), timeout=60)
        gw_logs = docker.logs(docker.name("gw"), timeout=60)
    total_s = round(time.monotonic() - t0, 1)

    data = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    (row,) = [r for r in data["by_check"] if r["check_id"] == "REQ-01"]
    art_dir = run_dir / ".guard/artifacts"
    lines = [json.loads(l) for p in art_dir.iterdir() for l in p.read_text(encoding="utf-8").splitlines()]
    summary = next(l for l in lines if l.get("summary"))
    dist = Counter((l["via"], l["origin"], l["key"], str(l["outcome"])) for l in lines if not l.get("summary"))
    observed = {
        "topology": topology, "verdict": row["single"], "expected_security": expected,
        "ready_seconds": target["engine"]["ready_seconds"], "run_seconds": timing["run_seconds"], "total_seconds": total_s,
        "wildcard_listeners_before": before, "wildcard_listeners_during": during,
        "distribution": {" | ".join(k): v for k, v in sorted(dist.items())},
        **{k: summary[k] for k in ("routes_registered", "skipped_routes", "direct_outcomes", "direct_allowed_outcomes",
                                   "gateway_outcomes", "gateway_denied_outcomes", "trusted_origin_reachable")},
        "limitations": data["limitations"],
    }
    (run_dir / "observed.json").write_text(json.dumps(observed, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "engine_log_tail.txt").write_text(engine_logs[-30000:], encoding="utf-8")
    print(json.dumps(observed, ensure_ascii=False, indent=1))

    # 안전 확인: 키 미기록, 새 와일드카드 리스너 없음, 자원 정리
    for p in run_dir.rglob("*"):
        if p.is_file():
            t = p.read_bytes().decode("utf-8", "replace")
            assert allowed not in t and denied not in t, p
    assert allowed not in engine_logs + gw_logs and denied not in engine_logs + gw_logs
    assert set(during) <= set(before), f"new wildcard listeners: {set(during) - set(before)}"
    assert _labelled() == ([], [])

    # 실제 엔진 실행 증거(EVD)는 MEASURED. RES 검증 수준은 근거 중 가장 약한 것 = 적용 조건(gateway 사실, DECLARED) → DECLARED (INV-12)
    (evd,) = [e for e in Store(run_dir / ".guard").iter_kind("EVD") if e.check_id == "REQ-01"]
    assert evd.data_origin.value == "MEASURED" and evd.execution.status.value == "COMPLETED"
    # 비교 조건은 마운트한 snapshot 전체 inventory, 참고 필드는 비교에 쓰지 않음
    assert evd.comparability.model_inventory_sha256 == vcfg["inventory_sha256"]
    (env_obj,) = list(Store(run_dir / ".guard").iter_kind("ENV"))
    assert env_obj.model.inventory_sha256 == vcfg["inventory_sha256"]
    assert env_obj.model.files_read_by_engine.inventory_sha256 == vcfg["files_read_by_engine_sha256"]
    assert row["single"]["verification_level"] == "DECLARED"
    # 보고서는 공식 아님(정책·팩 DRAFT)
    assert data["official"] is False
    assert row["single"]["security"] == expected


def test_inv15_real_engine_start_failure_is_aborted_unknown():
    """존재하지 않는 모델(빈 모델 디렉토리)로 실제 vLLM 기동 → 실행기가 기동 실패를 감지 → ABORTED → UNKNOWN, 컨테이너 정리."""
    vcfg = dict(_vllm_cfg())
    run_dir = _fresh("inv15")
    empty = run_dir / "empty_models"
    empty.mkdir()
    vcfg["models_dir"] = str(empty)
    vcfg["ready_timeout"] = 300
    allowed = secrets.token_urlsafe(24)
    env_vars = {"GUARD_LAB_ALLOWED_KEY": allowed, "GUARD_LAB_DENIED_KEY": secrets.token_urlsafe(24)}
    with Docker(f"f{secrets.token_hex(4)}") as docker:
        lab.up(docker, "isolated", run_dir, allowed, engine="vllm", vllm=vcfg, wait_ready=False)
        _pipeline(run_dir, env_vars, _write_cfg(run_dir))
        (run_dir / "engine_log_tail.txt").write_text(docker.logs(docker.name("engine"), timeout=60)[-10000:], encoding="utf-8")
    data = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    (row,) = [r for r in data["by_check"] if r["check_id"] == "REQ-01"]
    (evd,) = [e for e in Store(run_dir / ".guard").iter_kind("EVD") if e.check_id == "REQ-01"]
    (run_dir / "observed.json").write_text(json.dumps({"status": evd.execution.status.value,
                                                       "abort_reason": evd.execution.abort_reason,
                                                       "verdict": row["single"]}, ensure_ascii=False, indent=2))
    print(evd.execution.status.value, evd.execution.abort_reason)
    assert evd.execution.status.value == "ABORTED"
    assert row["single"]["security"] == "UNKNOWN"
    assert row["single"]["verification_level"] is None  # 실행 미완료 → 근거 없음 (INV-12)
    assert "REQ-01" not in data["manual_required"]
    assert _labelled() == ([], [])
