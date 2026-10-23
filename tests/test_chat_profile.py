"""고정 chat 프로파일의 입력·무결성·실행 조건 계약. 엔진은 실행하지 않는다."""
import hashlib
import json
from pathlib import Path

import pytest

from guard import application
from guard.adapters import vllm
from guard.adapters.base import LaunchSpec
from guard.runtime import sandbox
from tests.test_windows_cpu_launch import launch, setup_inputs


def test_chat_profile_is_explicit_and_adds_only_a_readonly_fixed_asset():
    plain = vllm.VllmAdapter().launch_plan(launch())
    chat = vllm.VllmAdapter().launch_plan(launch(chat_template_profile="guard-text-chat-v1"))
    assert plain.chat_template is None and len(plain.mounts) == 1
    assert "--chat-template" not in plain.argv
    assert chat.argv[:-4] == plain.argv
    assert chat.argv[-4:] == ["--chat-template", "/guard-assets/chat/guard-text-chat-v1.jinja",
                              "--chat-template-content-format", "string"]
    assert "--trust-request-chat-template" not in chat.argv
    assert chat.env == plain.env and chat.limits == plain.limits
    assert chat.mounts[0] == plain.mounts[0]
    asset = chat.mounts[1]
    assert asset.read_only and asset.target == "/guard-assets/chat/guard-text-chat-v1.jinja"
    assert "/models/" not in asset.target
    assert asset.source.name == "guard-text-chat-v1.jinja"
    assert hashlib.sha256(asset.source.read_bytes()).hexdigest() == chat.chat_template["sha256"]
    assert chat.chat_template == {
        "profile": "guard-text-chat-v1", "version": "1",
        "sha256": "e5b96cc7595d53305284a40f579747da846f01b566ad571ad2cb67526bdadc2e",
        "container_path": asset.target, "content_format": "string", "request_overrides": False}


@pytest.mark.parametrize("profile", [None, True, {}, [], "", "custom", "C:/private/custom.jinja", "{{ messages }}"])
def test_chat_profile_cannot_be_an_arbitrary_template_or_path(profile):
    with pytest.raises(ValueError, match="chat_template_profile"):
        vllm.VllmAdapter().launch_plan(launch(chat_template_profile=profile))


@pytest.mark.parametrize("options", [{"chat_template_profile": "guard-text-chat-v1"},
                                     {"chat_template": "{{ messages }}"},
                                     {"trust_request_chat_template": True}])
def test_legacy_launch_never_inherits_or_accepts_new_chat_controls(options):
    spec = LaunchSpec("model", 8000, image="cpu", options={
        "revision": "rev", "models_dir": Path("/snapshot"), "device": "cpu", **options})
    with pytest.raises(ValueError):
        vllm.VllmAdapter().launch_plan(spec)


@pytest.mark.parametrize("content", [None, b"different template", b"\r\n"])
def test_missing_or_modified_asset_fails_before_any_container_creation(tmp_path, monkeypatch, content):
    asset = tmp_path / "guard-text-chat-v1.jinja"
    if content is not None:
        asset.write_bytes(content)
    monkeypatch.setattr(vllm, "CHAT_TEMPLATE_ASSET", asset)
    with pytest.raises(ValueError, match="asset"):
        vllm.VllmAdapter().launch_plan(launch(chat_template_profile="guard-text-chat-v1"))


def test_managed_chat_preflight_checks_asset_without_changing_model_inventory(tmp_path, monkeypatch):
    _, env, _, cfg = setup_inputs(tmp_path)
    snapshot = Path(cfg["vllm"]["models_dir"])
    before = {file.name: file.read_bytes() for file in snapshot.iterdir()}
    cfg["vllm"]["chat_template_profile"] = "guard-text-chat-v1"
    prepared = application._managed_config(env, cfg)
    plan = sandbox._vllm_plan(prepared)
    assert plan.chat_template["profile"] == "guard-text-chat-v1"
    assert plan.mounts[1].source.parent != snapshot
    assert {file.name: file.read_bytes() for file in snapshot.iterdir()} == before
    monkeypatch.setattr(vllm, "CHAT_TEMPLATE_ASSET", tmp_path / "missing.jinja")
    with pytest.raises(ValueError, match="asset"):
        application._managed_config(env, cfg)


def test_comparison_conditions_include_actual_template_and_argv_without_host_paths(tmp_path, monkeypatch):
    captured = []
    class Recording:
        run_id = "ephemeral-local-run"
        def network_create(self, name, **kwargs): return "ephemeral-" + name
        def run_detached(self, image, suffix, **kwargs):
            if suffix == "engine": captured.append(kwargs)
            return "ephemeral-" + suffix
        def ip(self, *args, **kwargs): return "172.20.0.2"
        def published_port(self, *args, **kwargs): return 32001
        def image_digest(self, image, **kwargs): return image
    monkeypatch.setattr(sandbox, "pull_images", lambda docker: {sandbox.NGINX_IMAGE: "nginx@sha256:fixed",
                                                               sandbox.PYTHON_IMAGE: "python@sha256:fixed"})
    monkeypatch.setattr(sandbox, "probe", lambda *args, **kwargs: {"outcome": 401})
    cfg = {"model": "test", "revision": "rev", "image": "cpu@sha256:fixed", "models_dir": "C:/private/model",
           "gpu": False, "launch_profile": "cpu-bounded", "model_layout": "snapshot",
           "chat_template_profile": "guard-text-chat-v1"}
    first = sandbox.up(Recording(), "isolated", tmp_path / "one", "credential-not-persisted", engine="vllm", vllm=cfg,
                       wait_ready=False, engine_profile="hardened")
    template = first["comparison_config"]["launch"]["chat_template"]
    assert template["sha256"] == hashlib.sha256(captured[0]["mounts"][1].source.read_bytes()).hexdigest()
    assert captured[0]["mounts"][1].read_only
    assert first["comparison_config"]["launch"]["argv"] == captured[0]["command"]
    assert captured[0]["command"][-3] == template["container_path"]
    moved_asset = tmp_path / "installed-package" / "template.jinja"
    moved_asset.parent.mkdir()
    moved_asset.write_bytes(vllm.CHAT_TEMPLATE_ASSET.read_bytes())
    monkeypatch.setattr(vllm, "CHAT_TEMPLATE_ASSET", moved_asset)
    cfg["models_dir"] = "D:/another-private/model"
    second = sandbox.up(Recording(), "isolated", tmp_path / "two", "credential-not-persisted", engine="vllm", vllm=cfg,
                        wait_ready=False, engine_profile="hardened")
    assert first["comparison_config"] == second["comparison_config"]
    encoded = json.dumps(first["comparison_config"])
    assert not any(text in encoded for text in ("C:/private", "D:/another-private", "installed-package", "ephemeral-local-run"))
    assert "credential-not-persisted" not in (tmp_path / "one" / "target.json").read_text(encoding="utf-8")


def rejection(message, param=None):
    return {"outcome": 400, "body": json.dumps({"error": {
        "message": message, "param": param, "type": "BadRequestError", "code": 400}})}


OVERRIDE_REJECTION = ("Chat template is passed with request, but --trust-request-chat-template is not set. "
                      "Refused request with untrusted chat template.")
INPUT_REJECTION = ("This model's maximum context length is 128 tokens. However, you requested 1 output tokens "
                   "and your prompt contains at least 128 input tokens, for a total of at least 129 tokens. "
                   "Please reduce the length of the input prompt or the number of requested output tokens."
                   " (parameter=input_tokens, value=128)")


def test_rejection_evidence_identifies_inspected_pre_generation_branches_only():
    adapter = vllm.VllmAdapter()
    override = adapter.template_rejection_evidence(rejection(OVERRIDE_REJECTION), kind="request_override")
    assert override["phase"] == "pre_generation" and override["engine_version"] == "0.31.0"
    assert override["image_digest"] == "vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b"
    assert override["source_sha256"] == "1ada26191425fd9d56c3525949f999826dad9f2c82df94ad586a1cf6f67b089e"
    length = adapter.template_rejection_evidence(rejection(INPUT_REJECTION, "input_tokens"), kind="input_limit",
                                               prompt_tokens=128, output_tokens=1, max_model_len=128)
    assert length["observed_input_tokens"] == 128 and length["requested_output_tokens"] == 1
    assert length["image_digest"] == override["image_digest"]
    assert length["source_sha256"] == "ad38852674ebc5bbe69aa9df91602847d518382cc2ceb0a25aac1d07be66b504"
    assert length["formatter_source"] == "vllm/exceptions.py:49-56"
    assert length["formatter_source_sha256"] == "22fe819c7ef10438f740c251d81af204871a04c3aa3eb1fd0e68604dc4c2146f"
    assert length["serialization_source_sha256"] == "354a0581fbc29e2c3acbebe5e84ff3dbb132ba0af14ceba5363b6cf2fec6d54e"
    assert length["sanitizer_source_sha256"] == "723214aa4c63418f1d5eb7c1ff2116eca474778576ac7198daa1f39217c17a4f"
    assert adapter.template_rejection_evidence(rejection(OVERRIDE_REJECTION), kind="other") is None


@pytest.mark.parametrize("change", ["http", "code", "type", "param", "message", "truncated", "body"])
def test_unrelated_or_incomplete_errors_never_prove_template_enforcement(change):
    result = rejection(OVERRIDE_REJECTION)
    error = json.loads(result["body"])["error"]
    if change == "http": result["outcome"] = 422
    elif change == "truncated": result["body_truncated"] = True
    elif change == "body": result["body"] = "{}"
    else:
        error[{"code": "code", "type": "type", "param": "param", "message": "message"}[change]] = {
            "code": "400", "type": "UnauthorizedError", "param": "messages", "message": "invalid input"}[change]
        result["body"] = json.dumps({"error": error})
    assert vllm.VllmAdapter().template_rejection_evidence(result, kind="request_override") is None


@pytest.mark.parametrize("numbers", [(127, 1, 128), (128, 0, 128), (128, True, 128), (128, 129, 128),
                                     ("128", 1, 128), (None, 1, 128), (128, 1, 129)])
def test_length_rejection_requires_matching_actual_token_counts(numbers):
    assert vllm.VllmAdapter().template_rejection_evidence(
        rejection(INPUT_REJECTION, "input_tokens"), kind="input_limit", prompt_tokens=numbers[0],
        output_tokens=numbers[1], max_model_len=numbers[2]) is None


def test_length_rejection_does_not_accept_api_or_character_validation_instead():
    adapter = vllm.VllmAdapter()
    for result in (rejection(INPUT_REJECTION, "input_text"), rejection("invalid model", "model"),
                   rejection(INPUT_REJECTION.replace("129 tokens", "130 tokens"), "input_tokens")):
        assert adapter.template_rejection_evidence(result, kind="input_limit", prompt_tokens=128,
                                                  output_tokens=1, max_model_len=128) is None


@pytest.mark.parametrize("message", [
    INPUT_REJECTION.removesuffix(" (parameter=input_tokens, value=128)"),
    INPUT_REJECTION.replace("parameter=input_tokens", "parameter=input_text"),
    INPUT_REJECTION.replace("value=128)", "value=127)"),
    INPUT_REJECTION.replace("value=128)", "value=129)"),
    INPUT_REJECTION.replace("value=128)", "value=128.0)"),
    INPUT_REJECTION.replace("value=128)", "value=True)"),
    INPUT_REJECTION.replace("parameter=input_tokens, value=128", "value=128, parameter=input_tokens"),
    INPUT_REJECTION + " extra", "extra " + INPUT_REJECTION,
])
def test_length_rejection_requires_the_exact_source_formatter_suffix(message):
    assert vllm.VllmAdapter().template_rejection_evidence(
        rejection(message, "input_tokens"), kind="input_limit", prompt_tokens=128,
        output_tokens=1, max_model_len=128) is None


def test_only_the_exact_shipped_template_profile_is_supported():
    adapter = vllm.VllmAdapter()
    _, identity = vllm.fixed_chat_template("guard-text-chat-v1")
    assert adapter.supports_template_profile(identity)
    for key, value in (("profile", "custom"), ("version", 1), ("sha256", "a" * 64),
                       ("container_path", "/models/template.jinja"), ("content_format", "openai"),
                       ("request_overrides", 0), ("request_overrides", True), ("extra", "value")):
        changed = {**identity, key: value}
        assert not adapter.supports_template_profile(changed), key
    assert not adapter.supports_template_profile({})
    assert not adapter.supports_template_profile(None)
