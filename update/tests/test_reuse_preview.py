"""사전 이월 입력은 정적 설정으로만 계산하고 원격 수집을 호출하지 않는다."""

import json

from guard.executors import registry
from guard.executors.reuse import preview_inputs
from guard.models import canonical_json, sha256_hex
from tests.builders import baseline_pack, environment


def _cfg(tmp_path, executor, **settings):
    target = {"engine": {"trusted_url": "http://engine:8000", "container": "existing-engine"},
              "origins": {"trusted": {"network": "trusted"}}, "comparison_config": {"profile": "test"}}
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target), encoding="utf-8")
    return {"executors": {executor: "real"}, "adapter": {"name": "fake"},
            executor: {"target": str(path), **settings}}


def test_container_preview_uses_existing_fixed_fixture_without_docker(tmp_path, monkeypatch):
    from guard.executors.container_config import PROC_CMD, SENTINEL_CMD, ContainerConfigExecutor

    def forbidden(*args, **kwargs):
        raise AssertionError("preview must not run or collect")

    monkeypatch.setattr(ContainerConfigExecutor, "run", forbidden)
    monkeypatch.setattr(ContainerConfigExecutor, "collect", forbidden)
    cfg = _cfg(tmp_path, "container_config", sentinel_paths=["/one", "/one", "/two"])
    check = baseline_pack().checks["PLT-01"]
    preview = preview_inputs(check, environment(), {}, cfg)
    assert preview is not None
    assert preview.fixture_sha256 == sha256_hex(canonical_json({
        "sentinel_paths": ["/one", "/two"], "proc_cmd": PROC_CMD, "sentinel_cmd": SENTINEL_CMD}))
    assert preview.executor.name == "container_config"
    assert preview.config_sha256 == preview.executor.config_sha256


def test_dynamic_registration_fixture_cannot_be_inferred(tmp_path):
    check = baseline_pack().checks["REQ-01"]
    cfg = _cfg(tmp_path, "direct_route", roles={"allowed": "TEST_KEY"})
    cfg["_runtime_environ"] = {"TEST_KEY": "not-a-real-key"}
    assert preview_inputs(check, environment(), {"allowed_callers": ["allowed"]}, cfg) is None


def test_missing_config_and_example_target_are_not_reusable(tmp_path):
    check = baseline_pack().checks["PLT-01"]
    assert preview_inputs(check, environment(), {}, {}) is None
    cfg = _cfg(tmp_path, "container_config")
    path = cfg["container_config"]["target"]
    value = json.loads(open(path, encoding="utf-8").read())
    value["lab"] = True
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(value, stream)
    assert preview_inputs(check, environment(), {}, cfg) is None


def test_executor_plugin_can_supply_pure_inputs_without_core_name_branch(monkeypatch):
    class StaticExecutor:
        name = "test-static"
        version = "1"

        def preview_reuse_inputs(self, check, env, values, adapter):
            return {"case": "fixed"}, {"setting": 1}, "c" * 64

    monkeypatch.setitem(registry.EXECUTORS, "test-static", lambda cfg: StaticExecutor())
    check = baseline_pack().checks["PLT-01"]
    cfg = {"executors": {check.executor: "test-static"}, "adapter": {"name": "fake"}}
    preview = preview_inputs(check, environment(), {}, cfg)
    assert preview is not None
    assert preview.executor.name == "test-static"
    assert preview.fixture_sha256 == sha256_hex(canonical_json({"case": "fixed"}))


def test_fixed_routes_can_be_prepared_without_observed_registrations(tmp_path, monkeypatch):
    from guard.executors.direct_route import DirectRouteExecutor

    monkeypatch.setattr(DirectRouteExecutor, "collect", lambda *args: (_ for _ in ()).throw(
        AssertionError("must not discover registrations")))
    cfg = _cfg(tmp_path, "direct_route", comparison_routes=["POST /v1/completions"],
               roles={"allowed": "TEST_KEY"})
    cfg["adapter"] = {"name": "vllm"}
    cfg["_runtime_environ"] = {"TEST_KEY": "not-a-real-key"}
    preview = preview_inputs(baseline_pack().checks["REQ-01"], environment(), {"allowed_callers": ["allowed"]}, cfg)
    assert preview is not None
    assert preview.executor.version == DirectRouteExecutor.fixed_fixture_version
