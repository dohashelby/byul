import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from guard import application
from guard.cli import app
from guard.runtime.docker import Docker, CmdResult
from tests.builders import baseline_pack, environment, policy


def test_readiness_reports_invalid_inputs_and_unavailable_daemon(monkeypatch):
    class Offline:
        def __init__(self, *a): pass
        def daemon_info(self, **kw): raise RuntimeError("secret daemon output")
    monkeypatch.setattr(application, "Docker", Offline)
    pack = baseline_pack()
    result = application.update_readiness(pack, [environment(), environment()], policy(pack.ref()), {})
    assert result["ready"] is False
    assert {row["check"] for row in result["checks"]} >= {"inputs", "docker", "image-pair"}
    assert "secret daemon output" not in json.dumps(result)


@pytest.mark.parametrize("image_arch, expected", [("amd64", True), ("arm64", False)])
def test_readiness_checks_local_images_without_starting_containers(tmp_path, monkeypatch, image_arch, expected):
    from guard.adapters.model_inventory import inventory
    snapshot = tmp_path / "hub/models--test/snapshots/rev"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    pack = baseline_pack()
    envs = [environment(id="ENV-"+side,
        engine={"name":"vllm", "version":"test", "image_digest":"image@sha256:"+letter*64},
        runtime={"gpu_count":0, "cpu":{"architecture":"x86_64", "model":"test", "system":"Linux", "kernel":"test", "threads":2}},
        model={"id":"test", "revision":"rev", "inventory_sha256":inventory(snapshot)["inventory_sha256"]})
        for side, letter in [("a","a"),("b","b")]]
    pol = policy(pack.ref(), scope={"include":["REQ-09"]},
                 values={"REQ-09":{"max_input_tokens":8, "rejection_codes":["400"]}})
    cfg = {"adapter":{"name":"vllm"}, "executors":{"input_validation":"real"},
           "update":{"mode":"managed"}, "vllm":{"models_dir":str(tmp_path)},
           "input_validation":{"vocab_size":100, "vocab_source":"synthetic fixture"}}
    class Local:
        def __init__(self, *a): pass
        def daemon_info(self, **kw): return {"os":"linux", "architecture":"x86_64"}
        def image_info(self, image, **kw):
            return {"os":"linux", "architecture":image_arch, "digests":[image if "@" in image else image+"@sha256:"+"c"*64]}
    monkeypatch.setattr(application, "Docker", Local)
    result = application.update_readiness(pack, envs, pol, cfg)
    assert result["ready"] is expected, result
    assert not (tmp_path / "target.json").exists()


def test_preparation_cli_missing_inputs_creates_no_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["prepare-update", "--pack", "absent", "--baseline", "a",
                                    "--candidate", "b", "--policy", "p"])
    assert result.exit_code == 1
    assert json.loads(result.output)["ready"] is False
    assert not Path(".guard").exists()


def test_daemon_and_image_info_do_not_disclose_image_environment():
    calls = []
    def runner(argv, **kw):
        calls.append(argv)
        return CmdResult(0, json.dumps({"OSType":"linux", "Os":"linux", "Architecture":"amd64",
                                       "RepoDigests":["image@sha256:abc"], "Config":{"Env":["SECRET=value"]}}), "")
    docker = Docker("prepare", runner=runner)
    assert docker.daemon_info(timeout=1) == {"os":"linux", "architecture":"amd64"}
    assert "SECRET" not in str(docker.image_info("image", timeout=1))
    assert not any("run" in c or "pull" in c for c in calls)


def test_accelerated_pack_parser_has_safe_loader_semantics():
    from guard.models.checkdef import SAFE_YAML_LOADER
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.load("!!python/object/apply:os.system ['should-not-run']", Loader=SAFE_YAML_LOADER)
    for path in Path("packs/PACK-BASELINE/checks").glob("*.yaml"):
        raw = path.read_text(encoding="utf-8")
        assert yaml.load(raw, Loader=SAFE_YAML_LOADER) == yaml.safe_load(raw)
