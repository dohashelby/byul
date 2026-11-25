import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from guard import application
from guard.cli import app
from guard.models import load_pack
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


def test_kubernetes_only_readiness_keeps_unconfigured_input_without_docker(monkeypatch):
    def no_docker(*args):
        pytest.fail("Kubernetes-only preparation must not inspect a Docker daemon")

    monkeypatch.setattr(application, "Docker", no_docker)
    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    envs = [environment(engine={"name": "vllm", "version": version,
                                "image_digest": "example/engine@sha256:" + digit * 64})
            for version, digit in (("0.30.0", "a"), ("0.31.0", "b"))]
    pol = policy(pack.ref(), scope={"include": ["SUP-03"]},
                 values={"SUP-03": {"allowed_template_sha256": ["a" * 64]}})
    cfg = {
        "update": {"mode": "external", "kubernetes_candidate_binding": "after_baseline"},
        "sides": {side: {"executors": {"container_config": "kubernetes"}}
                  for side in ("baseline", "candidate")},
    }
    result = application.update_readiness(pack, envs, pol, cfg)
    checks = {row["check"]: row for row in result["checks"]}
    assert set(checks) == {"inputs", "inputs:baseline/SUP-03", "inputs:candidate/SUP-03", "kubernetes-static-preparation"}
    assert checks["inputs"]["ok"] is False
    for side in ("baseline", "candidate"):
        row = checks[f"inputs:{side}/SUP-03"]
        assert row["ok"] is False and row["detail"] == "real executor is required"
    assert result["ready"] is False
    assert {(row["side"], row["status"]) for row in result["check_support"]} == {
        ("baseline", "UNSUPPORTED"), ("candidate", "UNSUPPORTED")}


def test_invalid_external_side_override_stops_before_docker_inspection(monkeypatch):
    calls = []

    class Local:
        def __init__(self, *args):
            pass

        def daemon_info(self, **kwargs):
            calls.append("docker")
            return {"os": "linux", "architecture": "amd64"}

    monkeypatch.setattr(application, "Docker", Local)
    pack = baseline_pack()
    cfg = {
        "executors": {"request_binding": "kubernetes"},
        "sides": {"candidate": {"executors": {"request_binding": "real"}}},
    }
    result = application.update_readiness(pack, [environment(), environment()], policy(pack.ref()), cfg)
    checks = {row["check"]: row for row in result["checks"]}
    assert calls == []
    assert set(checks) == {"inputs"}
    assert checks["inputs"]["detail"] == "request_binding: both update sides must use real executors"
    assert checks["inputs"]["ok"] is False and result["ready"] is False


@pytest.mark.parametrize("image_arch, expected", [("amd64", True), ("arm64", False)])
def test_readiness_checks_local_images_without_starting_containers(tmp_path, monkeypatch, image_arch, expected):
    from guard.adapters.model_inventory import inventory
    snapshot = tmp_path / "hub/models--test/snapshots/rev"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    pack = baseline_pack()
    envs = [environment(id="ENV-"+side,
        engine={"name":"vllm", "version":"test", "image_digest":"image@sha256:"+letter*64},
        # 2026-11-12 (M-A9): managed vLLM launches GPU images only.
        runtime={"gpu_count":1, "gpu_model":"EXAMPLE-GPU", "driver":"EXAMPLE-DRIVER", "tp_size":1},
        model={"id":"test", "revision":"rev", "inventory_sha256":inventory(snapshot)["inventory_sha256"]})
        for side, letter in [("a","a"),("b","b")]]
    pol = policy(pack.ref(), scope={"include":["REQ-09"]},
                 values={"REQ-09":{"max_input_tokens":8, "rejection_codes":["400"],
                    "allowed_endpoints":[pack.checks["REQ-09"].functional_probes[0].id],
                    "allowed_input_formats":["token_ids"]}})
    cfg = {"adapter":{"name":"vllm"}, "executors":{"input_validation":"real"},
           "update":{"mode":"managed"}, "vllm":{"models_dir":str(tmp_path), "gpu_devices":["0"],
               "resources":{"cpus":4, "memory_bytes":17179869184, "pids_limit":512}},
           "input_validation":{"vocab_size":100, "vocab_source":"synthetic fixture"}}
    class Local:
        def __init__(self, *a): pass
        def daemon_info(self, **kw): return {"os":"linux", "architecture":"x86_64"}
        def daemon_resources(self, **kw): return {"memory_bytes":64 << 30, "cpus":16}
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
