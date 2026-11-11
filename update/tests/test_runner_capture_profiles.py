"""Provider support boundaries and data-only request-capture extensions."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from guard.adapters import engine_profiles, engine_provider
from guard.adapters.vllm_runner_capture import profile
from guard.executors import req_runner_capture as capture
from guard.models import canonical_json, sha256_hex


def env(name="vllm", version="0.31.0", image=None):
    return SimpleNamespace(engine=SimpleNamespace(
        name=name, version=version, image_digest=image or profile("0.31.0").image))


@pytest.mark.parametrize("name,version,image", [
    ("vllm", "0.32.0", None), ("sglang", "0.5.22", None),
    ("vllm", "0.31.0", "EXAMPLE/wrong-image"),
])
def test_unsupported_capture_rejected_before_tool_or_native_io(monkeypatch, name, version, image):
    def unexpected():
        pytest.fail("unsupported capture must not load the tool or perform I/O")
    monkeypatch.setattr(capture, "_preparer", unexpected)
    with pytest.raises(ValueError, match="^B_REQ_CAPTURE_SCOPE_INVALID$"):
        capture.Capture(SimpleNamespace(env=env(name, version, image)), {})


def test_registry_support_alone_does_not_supply_a_capture_implementation(monkeypatch):
    monkeypatch.setattr(engine_profiles, "PROFILES", (*engine_profiles.PROFILES,
        engine_profiles.EngineProfile("example-capture/1", "example", ("1",), frozenset({"runner_capture"}))))
    monkeypatch.setitem(engine_provider.PROVIDERS, "example", engine_provider.EngineProvider("example", object))
    with pytest.raises(ValueError, match="^B_REQ_CAPTURE_SCOPE_INVALID$"):
        capture._profile(env("example", "1"))


def test_new_provider_data_drives_the_same_executor_without_release_branches(monkeypatch):
    selected = replace(profile("0.31.0"), image="EXAMPLE/new-engine-image",
        source_commit="EXAMPLE/new-source", source_root_key="example_source_root",
        response_rules={"REQ-09": ("/example/generate", "example-", "/child")},
        response_id_pattern=r"example-[0-9]{4}")
    adapter = SimpleNamespace(runner_capture_profile=lambda version: selected if version == "2" else None)
    monkeypatch.setitem(engine_provider.PROVIDERS, "example", engine_provider.EngineProvider("example", lambda: adapter))
    monkeypatch.setattr(engine_profiles, "PROFILES", (*engine_profiles.PROFILES,
        engine_profiles.EngineProfile("example-capture/2", "example", ("2",), frozenset({"runner_capture"}))))
    selected_env = env("example", "2", selected.image)
    assert capture._profile(selected_env) is selected
    request = {"method": "POST", "url": "http://example/example/generate",
               "body": {"prompt": "EXAMPLE", "max_tokens": 1}}
    selected.validate_request(request, "REQ-09")
    with pytest.raises(ValueError, match="OUTSIDE_SELECTED_SCOPE"):
        selected.validate_request({**request, "url": "http://example/v1/completions"}, "REQ-09")
    import threading
    runner = object.__new__(capture.Capture)
    runner.ctx = SimpleNamespace(check=SimpleNamespace(id="REQ-09"))
    runner.profile = selected
    runner.fingerprinter = selected.fingerprinter(b"x" * 32)
    runner.lock, runner.calls = threading.Lock(), []
    response = {"outcome": 200, "body": canonical_json({"id": "example-1234"}).decode()}
    dispatches = []
    def dispatch():
        dispatches.append(request)
        return response
    assert runner.call("before", request, dispatch) is response
    assert dispatches == [request]
    assert runner.calls[0]["response_id_sha256"] == sha256_hex(b"example-1234")
    assert runner.calls[0]["request_sha256"] == runner.fingerprinter.fingerprint("example-1234/child")
    assert selected.request_id("REQ-09", "example-12345") is None
    assert selected.request_id("REQ-09", "cmpl-1234567890abcdef") is None


def test_capture_executor_contains_no_engine_names_or_release_pins():
    from pathlib import Path
    import re
    source = Path(capture.__file__).read_text(encoding="utf-8")
    assert not re.search(r"vllm|sglang|tensorrt|chatcmpl|cmpl-|0\.31\.0|db9527a", source, re.I)
