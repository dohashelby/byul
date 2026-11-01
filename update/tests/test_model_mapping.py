"""모델 매핑은 선언이며 실제 serving/worker 소비를 증명하지 않는다."""
from copy import deepcopy
import pytest

from guard.collectors.model_mapping import SCHEMA, validate_model_mapping, request_model_name, container_model_path
from guard.models import canonical_json
from tests.builders import environment

REPO = "Qwen/Qwen2.5-0.5B-Instruct"
REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"


def declared_mapping():
    env = environment(model={"id": REPO, "revision": REVISION, "inventory_sha256": "a" * 64})
    mapping = {"schema": SCHEMA, "environment_ref": env.ref().model_dump(mode="json"),
        "repository_id": REPO, "revision": REVISION, "served_model_name": "test-model",
        "host_snapshot": "/srv/serving/models/" + REVISION, "container_mount": "/models",
        "inventory_sha256": env.model.inventory_sha256}
    return env, mapping


def test_served_alias_never_replaces_the_environment_model_identity():
    env, mapping = declared_mapping()
    before = canonical_json(env.to_dict()), deepcopy(mapping)
    assert request_model_name(env, mapping) == "test-model"
    assert validate_model_mapping(env, mapping) == mapping
    assert env.model.id == REPO and env.model.revision == REVISION
    assert (canonical_json(env.to_dict()), mapping) == before
    assert container_model_path(env, mapping, mapping["host_snapshot"] + "/config.json") == "/models/config.json"


@pytest.mark.parametrize("field,value", [("repository_id", "other/repository"), ("revision", "b" * 40),
    ("served_model_name", ""), ("served_model_name", "test\nmodel"), ("inventory_sha256", "b" * 64),
    ("host_snapshot", "/srv/../models"), ("container_mount", "/"), ("container_mount", "/models/"),
    ("environment_ref", {"kind": "ENV", "id": "ENV-other", "revision": 1, "sha256": "0" * 64})])
def test_mapping_mismatch_or_unsafe_technical_values_are_not_repaired(field, value):
    env, mapping = declared_mapping()
    mapping[field] = value
    original = deepcopy(mapping)
    with pytest.raises(ValueError, match="^MODEL_MAPPING_INVALID$"):
        request_model_name(env, mapping)
    assert mapping == original


@pytest.mark.parametrize("path", ["/elsewhere/model.bin", "/srv/serving/models-other/model.bin", "/models/../secret", "C:\\model.bin"])
def test_host_file_must_be_below_the_declared_snapshot(path):
    env, mapping = declared_mapping()
    with pytest.raises(ValueError, match="^MODEL_MAPPING_PATH_INVALID$"):
        container_model_path(env, mapping, path)


def test_missing_inventory_is_explicit_preparation_and_never_filled():
    env, mapping = declared_mapping()
    env = env.model_copy(update={"model": env.model.model_copy(update={"inventory_sha256": None})}).seal()
    mapping.update(environment_ref=env.ref().model_dump(mode="json"), inventory_sha256=None)
    assert validate_model_mapping(env, mapping)["inventory_sha256"] is None
    assert request_model_name(env, mapping) == "test-model"


def test_unsealed_or_other_generation_environment_cannot_borrow_mapping():
    env, mapping = declared_mapping()
    for other in (env.model_copy(update={"content_sha256": "0" * 64}), env.model_copy(update={"revision": 2}).seal()):
        with pytest.raises(ValueError, match="^MODEL_MAPPING_INVALID$"):
            validate_model_mapping(other, mapping)
