"""EXAMPLE regression for the served-alias mismatch found on the owned GPU."""
from copy import deepcopy
import json

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.executors.kubernetes_sampling import KubernetesSamplingExecutor
from guard.executors.sampling_validation import SamplingValidationExecutor
from guard.models import Verdict, canonical_json
from tests.builders import environment
from tests.test_kubernetes_sampling import IMAGE, World as KubernetesWorld, inputs
from tests.test_sampling_validation import World, cfg, check, judge, VALUES


@pytest.mark.parametrize("backend", ["real", "kubernetes"])
@pytest.mark.parametrize("reported", ["served-alias", "repository/model", "unrelated", None])
def test_sampling_controls_use_the_validated_served_alias_without_rewriting_env(tmp_path, backend, reported):
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE},
                      model={"id": "repository/model", "revision": "a" * 40, "inventory_sha256": "b" * 64})
    mapping = {"schema": "guard.model-request-mapping/1", "environment_ref": env.ref().model_dump(mode="json"),
               "repository_id": env.model.id, "revision": env.model.revision, "served_model_name": "served-alias",
               "host_snapshot": "/srv/models/" + env.model.revision, "container_mount": "/models",
               "inventory_sha256": env.model.inventory_sha256}
    before = canonical_json(env.to_dict())

    def response_alias(value):
        body = json.loads(value["body"])
        if reported is None:
            body.pop("model")
        else:
            body["model"] = reported
        value["body"] = json.dumps(body)

    if backend == "kubernetes":
        target = inputs()
        target["model_mapping"] = mapping
        world = KubernetesWorld(target)

        def runner(argv, **kwargs):
            result = world.runner(argv, **kwargs)
            if "exec" in argv:
                values = json.loads(result["stdout_bytes"])
                for value in values:
                    response_alias(value)
                result["stdout_bytes"] = json.dumps(values).encode()
            return result

        executor_type, injected = KubernetesSamplingExecutor, {"runner": runner}
        calls = world.execs
    else:
        target = {"lab": True, "origins": {"trusted": {"network": "trusted-net"}},
                  "engine": {"trusted_url": "http://engine:8000"}, "model_mapping": mapping}
        world = World()

        def probe(network, requests, timeout):
            values = world.probe(network, requests, timeout)
            for value in values:
                response_alias(value)
            return values

        executor_type, injected = SamplingValidationExecutor, {"container_probe": probe}
        calls = world.calls
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target), encoding="utf-8")
    draft = executor_type({**cfg(), "target": str(path)}, **injected).run(
        check(), env, deepcopy(VALUES), VllmAdapter(), tmp_path / "work")
    result = judge(draft)
    matched = reported == "served-alias"
    assert draft.normal_control.succeeded is matched
    assert result.verdicts["functional"] == (Verdict.PASS if matched else Verdict.UNKNOWN)
    assert result.verdicts["security"] == Verdict.UNKNOWN
    assert len(calls) == (16 if matched else 1)
    assert all(request["body"]["model"] == "served-alias" for request in calls if "body" in request)
    assert canonical_json(env.to_dict()) == before
    assert draft.data_origin == "EXAMPLE"
