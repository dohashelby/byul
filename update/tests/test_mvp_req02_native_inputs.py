"""Use real prepared lifecycle assets and Guard's static product entry point."""

import json
import subprocess
import tomllib

import pytest

from guard.application import update_readiness
from guard.collectors.lifecycle import prepare_assets
from guard.models import DataOrigin, canonical_json, load_pack, sha256_hex
from guard.runtime.kubernetes_http import KubernetesBindingError
from tests.builders import environment, policy
from tests.test_kubernetes_request_binding import target
from tools import mvp_req02_native_inputs as tool
from tools.mvp_versions import versions


PROFILES = {"baseline": "vllm-native-lifecycle-metadata/25", "candidate": "vllm-native-lifecycle-metadata/8"}


def write(path, value):
    path.write_bytes(canonical_json(value))
    return path


@pytest.fixture
def paired_inputs(tmp_path):
    pack = load_pack(tool.ROOT / "packs/PACK-BASELINE")
    pol = policy(pack.ref(), scope={"include": ["REQ-02"]}, values={"REQ-02": {}})
    pol_path = write(tmp_path / "policy.json", pol.to_dict())
    spec, envs = {}, []
    for ordinal, (side, release) in enumerate(versions().items(), 1):
        directory = tmp_path / side
        directory.mkdir()
        prepare_assets(directory / "sensor", tool.ROOT.parent, profile=PROFILES[side])
        env = environment(id=f"ENV-{side}", origin=DataOrigin.DECLARED, engine={
            "name": release["engine"], "version": release["version"], "image_digest": release["image"],
        }, model={"id": "fixture-model", "revision": "fixture-revision"})
        envs.append(env)
        owned = target(env)
        owned.update(lab=True, observed_version=release["version"])
        owned["engine"]["image_digest"] = release["image"]
        owned["kubernetes"]["run_id"] = f"fixture-{side}"
        owned["kubernetes"]["engine"].update(image=release["image"], image_id=release["image"],
                                            pod_uid=f"00000000-0000-0000-0000-00000000000{ordinal}")
        write(directory / "env.json", env.to_dict())
        write(directory / "target.json", owned)
        spec[side] = {
            "environment": f"{side}/env.json", "target": f"{side}/target.json",
            "prepared": f"{side}/sensor/manifest.json", "record_session": f"{ordinal:032x}",
        }
    return pack, pol, pol_path, envs, spec


def test_preparation_uses_both_full_native_bindings(tmp_path, paired_inputs, monkeypatch):
    pack, pol, pol_path, envs, spec = paired_inputs

    def forbidden(*args, **kwargs):
        raise AssertionError("static assembly and preparation cannot dispatch")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    output = tmp_path / "inputs"
    result = tool.prepare(write(tmp_path / "spec.json", spec), pol_path, output)
    configuration = tomllib.loads((output / "guard.toml").read_text())
    assert configuration["update"]["allowed_checks"] == ["REQ-02"]
    assert result["official"] is False and result["live_target_verified"] is False
    for side, profile in PROFILES.items():
        side_config = configuration["sides"][side]
        assert side_config["route_record"]["target"] == side_config["route_record"]["native_route"]["target"]
        assert result["sides"][side]["prepared"]["profile"] == profile
        assert result["sides"][side]["binding_files"]["route_native_prepared_sha256"]
    readiness = update_readiness(pack, envs, pol, configuration)
    assert readiness["execution_scope"]["static_preparation_ready"] is True, readiness


@pytest.mark.parametrize("defect,error", [
    ("plain-target", KubernetesBindingError),
    ("reused-session", ValueError),
    ("wrong-native-release", ValueError),
    ("wrong-native-settings", ValueError),
    ("missing-native", FileNotFoundError),
])
def test_incomplete_pair_does_not_publish_config(tmp_path, paired_inputs, defect, error):
    _, _, pol_path, _, spec = paired_inputs
    if defect == "plain-target":
        path = tmp_path / spec["candidate"]["target"]
        write(path, {**json.loads(path.read_bytes()), "lab": False})
    elif defect == "reused-session":
        spec["candidate"]["record_session"] = spec["baseline"]["record_session"]
    elif defect == "wrong-native-release":
        spec["candidate"]["prepared"] = spec["baseline"]["prepared"]
    elif defect == "wrong-native-settings":
        manifest_path = tmp_path / spec["candidate"]["prepared"]
        settings_path = manifest_path.with_name("settings.json")
        settings = json.loads(settings_path.read_bytes())
        settings["output_dir"] = "/guard-lifecycle/data/wrong"
        actual = write(settings_path, settings).read_bytes()
        manifest = json.loads(manifest_path.read_bytes())
        manifest["assets"]["settings.json"] = {"bytes": len(actual), "sha256": sha256_hex(actual)}
        write(manifest_path, manifest)
    else:
        (tmp_path / spec["candidate"]["prepared"]).unlink()
    output = tmp_path / "inputs"
    with pytest.raises(error):
        tool.prepare(write(tmp_path / "spec.json", spec), pol_path, output)
    assert not output.exists()
