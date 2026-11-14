"""WP7 runner protocol regression checks without external commands."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WP = Path(__file__).resolve().parents[1] / "project-context/refactor-2026-11/wp7"


def load(name, monkeypatch):
    monkeypatch.syspath_prepend(str(WP))
    spec = importlib.util.spec_from_file_location("wp7_contract_" + name, WP / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_R08_uses_supported_observation_query_and_actual_predicate(monkeypatch):
    module = load("server_checks", monkeypatch)
    calls = []
    live = SimpleNamespace(normal=lambda: calls.append("normal"))

    def kube(*argv):
        calls.append("probe")
        live.probe = argv[-1].split("p=pathlib.Path(", 1)[1].split(");", 1)[0].strip("'")

    def wait_api(path, predicate):
        assert path == "/api/observations?source=tetragon"
        good = dict(code_load_verdict="detected", phase=["inferring"], identity={"state": "confirmed"}, path=live.probe)
        assert predicate({"events": [good]})
        for key, value in [
            ("phase", ["loading"]),
            ("identity", {"state": "unknown"}),
            ("code_load_verdict", "unknown"),
            ("path", "/different.py"),
        ]:
            assert not predicate({"events": [{**good, key: value}]})
        calls.append("observations")

    live.kube, live.wait_api = kube, wait_api
    module.Live.R_08(live)
    assert calls == ["normal", "probe", "observations"]


def test_bootstrap_installs_matching_update_before_preflight_or_image_import(tmp_path, monkeypatch):
    module = load("bootstrap_server", monkeypatch)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "bootstrap_server.py"))
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    calls = []

    def run(argv, **kwargs):
        argv = list(map(str, argv))
        calls.append(argv)
        if "rev-parse" in argv:
            return 0, module.BASELINE
        if any("sysconfig" in arg for arg in argv):
            return 0, str(tmp_path / "site")
        return 0, ""

    with (
        patch.object(module, "approval"),
        patch.object(
            module,
            "private_json",
            return_value={"runtime_root": str(checkout / "runtime"), "customer_install_validation": True},
        ),
        patch.object(module, "verify_artifacts"),
        patch.object(module, "run", side_effect=run),
        patch.object(
            module.argparse.ArgumentParser,
            "parse_args",
            return_value=SimpleNamespace(config="private-config", approval_file="private-approval"),
        ),
    ):
        module.main()
    install = next(i for i, args in enumerate(calls) if args[1:5] == ["-m", "pip", "install", "-e"])
    assert calls[install][-1] == str(checkout / "update")
    callback = next(
        i
        for i, args in enumerate(calls)
        if any("from guard.adapters.vllm_engine_callback_sensor import TAG" in arg for arg in args)
    )
    image = next(i for i, args in enumerate(calls) if args[:2] == ["sudo", "ctr"])
    assert install < callback < image
