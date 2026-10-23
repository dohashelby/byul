import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner
from guard.cli import app
from guard.executors import registry
from guard.executors.fake import FakeExecutor
from guard.models.objects import Comparability
from guard import application as orchestration
from guard.runtime.docker import DockerError
from guard.store import Store
from tests.test_cli import workdir

runner = CliRunner()


def inputs():
    policy = Path("examples/policy.yaml")
    pol = yaml.safe_load(policy.read_text(encoding="utf-8"))
    pol["scope"] = {"include": ["REQ-01"], "exclude": []}
    policy.write_text(yaml.safe_dump(pol), encoding="utf-8")
    for side, version, letter in [("baseline", "1", "a"), ("candidate", "2", "b")]:
        raw = yaml.safe_load(Path("examples/env.yaml").read_text(encoding="utf-8"))
        raw["engine"].update(name="vllm", version=version, image_digest=f"example/engine@sha256:{letter * 64}")
        Path(f"{side}.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")


def invoke():
    return runner.invoke(app, ["update", "--pack", "packs/PACK-BASELINE", "--baseline", "baseline.yaml",
                              "--candidate", "candidate.yaml", "--policy", "examples/policy.yaml", "--output", "updates"])


def report():
    return json.loads(next(Path("updates").glob("*/report.json")).read_text(encoding="utf-8"))


class ComparableFake(FakeExecutor):
    def run(self, *args, **kwargs):
        draft = super().run(*args, **kwargs)
        return draft.model_copy(update={"comparability": Comparability(
            model_inventory_sha256="m", fixture_sha256="f", config_sha256="c", gpu="test", driver="test")})


def test_update_cli_runs_both_sides_and_reports_regression(workdir, monkeypatch):
    inputs()
    monkeypatch.setitem(registry.EXECUTORS, "fake", lambda c: ComparableFake(c.get("fake", {}).get("scenario", "pass")))
    with Path("guard.toml").open("a", encoding="utf-8") as f:
        f.write('\n[sides.baseline.fake]\nscenario = "pass"\n[sides.candidate.fake]\nscenario = "direct_200"\n')
    result = invoke()
    assert result.exit_code == 0, result.output + repr(result.exception)
    data = report()
    row = next(r for r in data["by_check"] if r["check_id"] == "REQ-01")
    assert row["baseline"]["security"] == "PASS"
    assert row["candidate"]["security"] == "FAIL"
    assert row["comparison"] == "REGRESSED"
    assert data["official"] is False
    assert Store(Path(".guard")).verify() == []
    assert invoke().exit_code == 0
    assert len(list(Path("updates").glob("*/report.json"))) == 2


def test_external_real_update_rejects_shared_target(workdir):
    inputs()
    Path("guard.toml").write_text(
        '[adapter]\nname="vllm"\n[executors]\ndirect_route="real"\n'
        '[sides.baseline.direct_route]\ntarget="shared.json"\n'
        '[sides.candidate.direct_route]\ntarget="shared.json"\n', encoding="utf-8")
    result = invoke()
    assert result.exit_code != 0
    assert "different target" in str(result.exception)
    assert not Path("updates").exists()


def test_generic_run_cannot_reuse_one_real_target(tmp_path):
    from guard.core.select import select
    from guard.core.run import run_plan
    from tests.builders import baseline_pack, environment, policy
    pack = baseline_pack()
    store = Store(tmp_path / "store")
    envs = [environment(id="ENV-A"), environment(id="ENV-B")]
    pol = policy(pack.ref(), scope={"include":["REQ-01"]})
    for obj in [*envs, pol]: store.put(obj)
    plan = select(pack, envs, pol, mode="update"); store.put(plan)
    cfg = orchestration.run_config({"adapter":{"name":"vllm"}, "executors":{"direct_route":"real"},
        "sides":{"baseline":{"direct_route":{"target":"same.json"}},
                 "candidate":{"direct_route":{"target":"same.json"}}}})
    with pytest.raises(ValueError, match="different target"):
        run_plan(store, pack, plan, cfg)


def test_external_distinct_files_cannot_relabel_one_image(workdir):
    inputs()
    Path("guard.toml").write_text(
        '[adapter]\nname="vllm"\n[executors]\ndirect_route="real"\n'
        '[sides.baseline.direct_route]\ntarget="a.json"\n'
        '[sides.candidate.direct_route]\ntarget="b.json"\n', encoding="utf-8")
    target = {"lab":False, "observed_version":"1", "engine":{"image_digest":"example/engine@sha256:" + "a"*64}}
    for name in ("a.json", "b.json"):
        Path(name).write_text(json.dumps(target), encoding="utf-8")
    result = invoke()
    assert result.exit_code != 0
    assert "candidate: target identity differs" in str(result.exception)
    assert not Path("updates").exists()


@pytest.mark.parametrize("root", [{}, {"direct_route":"fake"}])
def test_side_only_real_configuration_cannot_bypass_target_separation(root):
    from guard.core.run import validate_update_run_config
    cfg = orchestration.run_config({"executors":root, "sides":{
        s:{"executors":{"direct_route":"real"}, "direct_route":{"target":"same.json"}}
        for s in ("baseline","candidate")}})
    with pytest.raises(ValueError, match="different target"):
        validate_update_run_config(cfg)


@pytest.mark.parametrize("fail_baseline, cleanup_failure", [(False, False), (True, False), (False, True)])
def test_managed_update_uses_distinct_targets_and_cleans_up(workdir, monkeypatch, fail_baseline, cleanup_failure):
    inputs()
    Path("guard.toml").write_text(
        '[adapter]\nname="vllm"\n[executors]\ndirect_route="real"\n[update]\nmode="managed"\n', encoding="utf-8")
    exited, calls = [], []
    class Docker:
        def __init__(self, ident): self.ident = ident
        def __enter__(self): return self
        def __exit__(self, *args):
            exited.append(self.ident)
            if cleanup_failure:
                raise DockerError("cleanup incomplete")

    def up(docker, topology, folder, allowed_key, **kwargs):
        calls.append(kwargs["vllm"]["image"])
        if fail_baseline and folder.name == "baseline":
            raise DockerError("private engine error")
        folder.mkdir(parents=True)
        (folder / "target.json").write_text("{}", encoding="utf-8")
        image = kwargs["vllm"]["image"]
        return {"observed_version": "1" if folder.name == "baseline" else "2", "images": {image: image}}

    seen = []
    def executor(c):
        seen.append(c["direct_route"]["target"])
        return ComparableFake("direct_200" if cleanup_failure and "baseline" in seen[-1] else "pass")
    monkeypatch.setattr(orchestration, "Docker", Docker)
    monkeypatch.setattr(orchestration.sandbox, "up", up)
    monkeypatch.setattr(orchestration, "_managed_config", lambda e,c: {"image": e.engine.image_digest})
    monkeypatch.setitem(registry.EXECUTORS, "direct_route:real", executor)
    result = invoke()
    assert result.exit_code == (1 if fail_baseline or cleanup_failure else 0), result.output + repr(result.exception)
    assert len(exited) == 2 and len(set(exited)) == 2
    assert len(set(calls)) == 2
    row = next(r for r in report()["by_check"] if r["check_id"] == "REQ-01")
    if fail_baseline:
        assert row["baseline"]["security"] == "UNKNOWN"
        assert row["comparison"] != "FIXED"
        assert len(seen) == 1 and "candidate" in seen[0]
    else:
        assert row["baseline"]["security"] == ("FAIL" if cleanup_failure else "PASS")
        assert len(set(seen)) == 2
        assert "baseline" in seen[0] and "candidate" in seen[1]
    if cleanup_failure:
        assert any("cleanup" in note for note in report()["limitations"])
    assert "private engine error" not in str(report())


def test_managed_preflight_checks_real_model_inventory(tmp_path):
    from guard.adapters.model_inventory import inventory
    from tests.builders import environment
    snapshot = tmp_path / "hub/models--test/snapshots/rev"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text('{"vocab_size":100}', encoding="utf-8")
    env = environment(engine={"name":"vllm","version":"test","image_digest":"test/image@sha256:"+"a"*64},
                      runtime={"gpu_count":0},
                      model={"id":"test","revision":"rev","inventory_sha256":"wrong"})
    cfg = {"vllm":{"models_dir":str(tmp_path)}}
    with pytest.raises(ValueError, match="inventory differs"):
        orchestration._managed_config(env, cfg)
    env = env.model_copy(update={"model":env.model.model_copy(update={"inventory_sha256":inventory(snapshot)["inventory_sha256"]})}).seal()
    result = orchestration._managed_config(env, cfg)
    assert result["image"] == env.engine.image_digest and result["gpu"] is False
