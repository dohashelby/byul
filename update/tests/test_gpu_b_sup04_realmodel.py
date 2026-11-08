"""Dedicated SUP04 preparation binding; simulated metadata is not measured."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from guard.executors import registry
from guard.executors.running_image_approval import _snapshot, _target
from tests.builders import baseline_pack, environment
from tests.test_running_image_approval import FakeDocker, PIN, CONTAINER, RUN, _inspect
from tools.gpu_b_sup04_realmodel import actual_image_binding, bind_image_target


def docker_metadata(**kwargs):
    docker = FakeDocker(**kwargs)
    docker.run_id = RUN
    return docker


def inputs():
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": PIN})
    raw = {"lab": True, "run_id": RUN, "engine": {"container": CONTAINER, "image_digest": PIN}}
    snapshot = _snapshot(_inspect(), CONTAINER, PIN, RUN)
    return env, raw, snapshot


def test_actual_snapshots_bind_missing_ref_without_mutating_raw_then_existing_factory_accepts():
    env, raw, _ = inputs()
    original = deepcopy(raw)
    docker = docker_metadata()
    bound, proof = actual_image_binding(docker, raw, env)
    assert raw == original and "environment_ref" not in raw
    assert bound["environment_ref"] == env.ref().model_dump(mode="json")
    assert proof["before"] == proof["after"] and proof["repo_digests"] == [PIN]
    assert [call[0] for call in docker.calls] == ["inspect", "image_info", "inspect"]
    assert _target(SimpleNamespace(env=env, target=raw), None) is None
    assert _target(SimpleNamespace(env=env, target=bound), None) == (CONTAINER, PIN, RUN)
    factory = registry.build_executor("container_config", "real", {})
    check = baseline_pack().checks["SUP-04"]
    for values in ({"approved_image_digests": [PIN]}, {"approved_image_digests": []}):
        factory.validate_policy(check, values)


@pytest.mark.parametrize("failure", ["missing_before", "missing_digest", "malformed_digest", "generation", "name", "owner", "image_id", "container_id", "started_at", "config_image", "env_image", "foreign_env"])
def test_missing_unmatched_or_replaced_binding_is_rejected(failure):
    env, raw, before = inputs()
    after, digests = deepcopy(before), [PIN]
    if failure == "missing_before": before = None
    elif failure == "missing_digest": digests = []
    elif failure == "malformed_digest": digests = ["sha256:" + "a" * 64]
    elif failure == "generation": after["container_id"] = "f" * 64
    elif failure == "name": before["name"] = after["name"] = "/other"
    elif failure == "owner": before["owned_run_id"] = after["owned_run_id"] = "other"
    elif failure == "image_id": before["image_id"] = after["image_id"] = PIN
    elif failure == "container_id": before["container_id"] = after["container_id"] = "invalid"
    elif failure == "started_at": before["started_at"] = after["started_at"] = "invalid"
    elif failure == "config_image": before["configured_image"] = after["configured_image"] = "image:latest"
    elif failure == "env_image": raw["engine"]["image_digest"] = "other@sha256:" + "c" * 64
    elif failure == "foreign_env": raw["environment_ref"] = {"kind": "ENV", "id": "other"}
    original = deepcopy(raw)
    with pytest.raises(ValueError, match="SUP04_"):
        bind_image_target(raw, env, before, digests, after)
    assert raw == original


def test_query_failure_and_live_replacement_do_not_produce_bound_input():
    env, raw, _ = inputs()
    with pytest.raises(OSError): actual_image_binding(docker_metadata(image_error=True), raw, env)
    after = _inspect(); after["State"]["StartedAt"] = "2026-10-27T00:00:00Z"
    with pytest.raises(ValueError): actual_image_binding(docker_metadata(after=after), raw, env)
    assert "environment_ref" not in raw
