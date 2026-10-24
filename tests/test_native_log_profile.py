"""Fixed native logger launch/evidence contracts; no engine or Docker daemon."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard import application
from guard.adapters import vllm
from guard.adapters.base import LaunchSpec
from guard.runtime import sandbox
from tests.test_windows_cpu_launch import launch, setup_inputs


PROFILE_ENV = {"VLLM_CONFIGURE_LOGGING": "1", "VLLM_LOGGING_LEVEL": "INFO",
               "VLLM_LOGGING_COLOR": "0", "VLLM_LOGGING_PREFIX": "",
               "VLLM_LOGGING_STREAM": "ext://sys.stdout"}
IMAGE = "vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b"
SOURCE_PINS = {
    "vllm/logger.py": "4e26afd9feab3448f2af529867b00e8bed6a22690defd81a54d2df6b5afdd010",
    "vllm/logging_utils/formatter.py": "cc2153d8edfd59ed401f0c3f3ae985a7dd678799daad6829a2d33ac6ccb20ea5",
    "vllm/entrypoints/serve/utils/request_logger.py": "e7839742189c6f336f7d9fa3355cd78ccb1b1d63a8a24361b8b13a7f7cd0e83d",
}


def test_fixed_native_logging_is_opt_in_and_preserves_default_launch_contract():
    adapter = vllm.VllmAdapter()
    plain = adapter.launch_plan(launch())
    logged = adapter.launch_plan(launch(logging_profile="request-info-v1"))
    assert plain.log_profile is None
    assert "--enable-log-requests" not in plain.argv and "--max-log-len" not in plain.argv
    assert not set(PROFILE_ENV) & set(plain.env)
    assert logged.argv[:-3] == plain.argv
    assert logged.argv[-3:] == ["--enable-log-requests", "--max-log-len", "512"]
    assert logged.env == {**plain.env, **PROFILE_ENV}
    assert logged.mounts == plain.mounts  # No arbitrary dictConfig/Python file mount.
    assert logged.entrypoint == plain.entrypoint and logged.limits == plain.limits
    assert logged.gpus == plain.gpus and logged.chat_template is None
    assert logged.log_profile["profile"] == "request-info-v1"
    assert logged.log_profile["version"] == "1"
    assert logged.log_profile["level"] == "INFO" and logged.log_profile["max_log_len"] == 512
    assert logged.log_profile["color"] is False and logged.log_profile["custom_config_allowed"] is False
    assert logged.log_profile["external_request_id_trust"] == "untrusted_correlation"


@pytest.mark.parametrize("profile", [None, True, 1, {}, [], "", " ", "debug", "REQUEST-INFO-V1",
                                     "request-info-v1 ", "C:/private/logger.py", "/tmp/logging.json",
                                     {"version": 1, "handlers": {"custom": {"class": "arbitrary.Class"}}}])
def test_native_logging_cannot_accept_custom_code_paths_or_implicit_profiles(profile):
    with pytest.raises(ValueError, match="logging_profile"):
        vllm.VllmAdapter().launch_plan(launch(logging_profile=profile))


@pytest.mark.parametrize("profile", ["request-info-v1", None, "", "/tmp/logging.json"])
def test_legacy_launch_rejects_native_logging_options_even_if_the_value_is_empty(profile):
    spec = LaunchSpec("model", 8000, image="cpu", options={"revision": "rev", "models_dir": Path("/snapshot"),
                                                            "device": "cpu", "logging_profile": profile})
    with pytest.raises(ValueError, match="cpu-bounded"):
        vllm.VllmAdapter().launch_plan(spec)


def test_native_log_source_contract_is_available_only_for_the_exact_reviewed_image_and_version():
    adapter = vllm.VllmAdapter()
    contract = adapter.native_log_contract(IMAGE, "0.31.0")
    assert contract["profile"] == "request-info-v1"
    assert contract["environment"] == PROFILE_ENV
    assert {source["path"]: source["sha256"] for source in contract["sources"]} == SOURCE_PINS
    assert contract["logger_sha256"] == SOURCE_PINS["vllm/logger.py"]
    assert contract["formatter_sha256"] == SOURCE_PINS["vllm/logging_utils/formatter.py"]
    assert all(type(source["lines"]) is str and source["lines"] for source in contract["sources"])
    for image in (None, "", "vllm/vllm-openai-cpu:latest", IMAGE.removesuffix("b"),
                  IMAGE.replace("cpu@", "gpu@"), "sha256:" + IMAGE.split("sha256:")[1]):
        assert adapter.native_log_contract(image, "0.31.0") is None
    for version in (None, "", "0.31", "0.31.0+local", "0.31.1", 0.31):
        assert adapter.native_log_contract(IMAGE, version) is None


def test_native_log_contract_is_fresh_and_does_not_inherit_host_custom_logging(monkeypatch):
    monkeypatch.setenv("VLLM_LOGGING_CONFIG_PATH", "C:/private/unreviewed.json")
    monkeypatch.setenv("VLLM_LOGGING_LEVEL", "DEBUG")
    adapter = vllm.VllmAdapter()
    contract = adapter.native_log_contract(IMAGE, "0.31.0")
    contract["environment"]["VLLM_LOGGING_LEVEL"] = "DEBUG"
    contract["sources"][0]["sha256"] = "f" * 64
    contract["custom_config_allowed"] = True
    fresh = adapter.native_log_contract(IMAGE, "0.31.0")
    assert fresh["environment"] == PROFILE_ENV and fresh["custom_config_allowed"] is False
    assert fresh["sources"][0]["sha256"] == SOURCE_PINS["vllm/logger.py"]
    plan = adapter.launch_plan(launch(logging_profile="request-info-v1"))
    assert plan.env["VLLM_LOGGING_LEVEL"] == "INFO"
    assert "VLLM_LOGGING_CONFIG_PATH" not in plan.env
    assert "C:/private" not in json.dumps(plan.log_profile)


def test_native_log_profile_composes_with_fixed_chat_without_replacing_assets():
    adapter = vllm.VllmAdapter()
    chat = adapter.launch_plan(launch(chat_template_profile="guard-text-chat-v1"))
    both = adapter.launch_plan(launch(chat_template_profile="guard-text-chat-v1", logging_profile="request-info-v1"))
    assert both.argv[:-3] == chat.argv
    assert both.mounts == chat.mounts and both.chat_template == chat.chat_template
    assert both.env == {**chat.env, **PROFILE_ENV}
    assert "--trust-request-chat-template" not in both.argv and "--trust-remote-code" not in both.argv


def test_managed_preflight_passes_fixed_logging_profile_without_changing_snapshot(tmp_path):
    _, env, _, cfg = setup_inputs(tmp_path)
    snapshot = Path(cfg["vllm"]["models_dir"])
    before = {path.name: path.read_bytes() for path in snapshot.iterdir()}
    cfg["vllm"]["logging_profile"] = "request-info-v1"
    prepared = application._managed_config(env, cfg)
    plan = sandbox._vllm_plan(prepared)
    assert plan.log_profile["profile"] == "request-info-v1"
    assert plan.argv[-3:] == ["--enable-log-requests", "--max-log-len", "512"]
    assert {path.name: path.read_bytes() for path in snapshot.iterdir()} == before
    bad = deepcopy(cfg)
    bad["vllm"]["logging_profile"] = "/tmp/custom-logging.json"
    with pytest.raises(ValueError, match="logging_profile"):
        application._managed_config(env, bad)


def test_comparison_conditions_capture_actual_fixed_log_flags_and_env_without_host_paths(tmp_path, monkeypatch):
    starts = []

    class Recording:
        run_id = "local-ephemeral-owner"
        def network_create(self, name, **kwargs): return "local-" + name
        def run_detached(self, image, suffix, **kwargs):
            if suffix == "engine":
                starts.append(kwargs)
            return "local-" + suffix
        def ip(self, *args, **kwargs): return "172.20.0.2"
        def published_port(self, *args, **kwargs): return 32001
        def image_digest(self, image, **kwargs): return image

    monkeypatch.setattr(sandbox, "pull_images", lambda docker: {
        sandbox.NGINX_IMAGE: "nginx@sha256:fixed", sandbox.PYTHON_IMAGE: "python@sha256:fixed"})
    monkeypatch.setattr(sandbox, "probe", lambda *args, **kwargs: {"outcome": 401})
    config = {"model": "test", "revision": "rev", "image": IMAGE, "models_dir": "C:/private/model",
              "gpu": False, "launch_profile": "cpu-bounded", "model_layout": "snapshot",
              "logging_profile": "request-info-v1"}
    first = sandbox.up(Recording(), "isolated", tmp_path / "one", "private-gateway-key", engine="vllm",
                       vllm=config, wait_ready=False, engine_profile="hardened")
    actual = first["comparison_config"]["launch"]
    assert actual["argv"] == starts[0]["command"]
    assert actual["environment"] == starts[0]["public_env"]
    assert actual["log_profile"] == vllm.fixed_native_log_profile("request-info-v1")
    assert all(actual["environment"][key] == value for key, value in PROFILE_ENV.items())
    assert actual["argv"][-3:] == ["--enable-log-requests", "--max-log-len", "512"]
    assert len(starts[0]["mounts"]) == 1 and starts[0]["mounts"][0].read_only
    config["models_dir"] = "D:/another-private/model"
    second = sandbox.up(Recording(), "isolated", tmp_path / "two", "different-private-key", engine="vllm",
                        vllm=config, wait_ready=False, engine_profile="hardened")
    assert first["comparison_config"] == second["comparison_config"]
    encoded = json.dumps(first["comparison_config"])
    assert not any(value in encoded for value in ("C:/private", "D:/another-private", "local-ephemeral-owner",
                                                  "private-gateway-key", "different-private-key", "tmp_path"))
    assert "private-gateway-key" not in (tmp_path / "one" / "target.json").read_text(encoding="utf-8")
