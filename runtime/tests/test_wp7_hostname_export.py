"""Encoded cloud hostnames must not disclose the original host address."""

import importlib.util
from pathlib import Path


def exporter(monkeypatch):
    root = Path(__file__).resolve().parents[1] / "project-context/refactor-2026-11/wp7"
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location("wp7_hostname_export", root / "export_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_encoded_hostname_is_detected_redacted_and_stable(monkeypatch):
    module = exporter(monkeypatch)
    original = "ip-192-0-2-7"
    assert "host_address" in module.leaks(original)
    source = {"node": original, "log": original + ".compute.internal", "state": "passed", "count": 31}
    masked = module.Sanitizer().value(source)
    assert original not in str(masked)
    assert not module.leaks(str(masked))
    assert module.protected(source) == module.protected(masked)
    assert module.Sanitizer().value(masked) == masked


def test_invalid_hostname_is_not_treated_as_an_address(monkeypatch):
    module = exporter(monkeypatch)
    original = "ip-999-0-2-7"
    assert module.Sanitizer().text(original) == original
    assert not module.leaks(original)
