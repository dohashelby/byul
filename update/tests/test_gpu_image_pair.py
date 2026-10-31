"""Two reviewed GPU identities through the existing EXAMPLE update harness."""
from guard.adapters.vllm import REQUEST_BINDING_GPU_IMAGE, REQUEST_BINDING_GPU_032_IMAGE
from guard.store import Store
import pytest

from tests import test_kubernetes_transition as transition
from tests import test_kubernetes_deferred_binding as deferred


def gpu_pair(monkeypatch):
    original = transition.Pair

    def pair(**kwargs):
        value = original(**kwargs)
        for index, (version, image) in enumerate((("0.31.0", REQUEST_BINDING_GPU_IMAGE),
                                                  ("0.32.0", REQUEST_BINDING_GPU_032_IMAGE))):
            environment = value.envs[index].model_copy(deep=True)
            environment.engine.version = version
            environment.engine.image_digest = image
            value.envs[index] = environment.seal()
            target = value.worlds[index].target
            target.update(observed_version=version, environment_ref=value.envs[index].ref().model_dump(mode="json"))
            target["engine"]["image_digest"] = image
            target["kubernetes"]["engine"].update(image=image, image_id=image)
            target["comparison_config"]["launch"]["profile"] = "gpu-bounded-v1"
        return value

    monkeypatch.setattr(transition, "Pair", pair)
    monkeypatch.setattr(deferred, "Pair", pair)


@pytest.mark.parametrize("stop", [True, False])
def test_two_gpu_versions_use_same_update_cli_and_keep_baseline_on_failure(tmp_path, monkeypatch, stop):
    gpu_pair(monkeypatch)
    transition.test_update_cli_sides_evidence_report_and_failed_candidate_preserve_baseline(tmp_path, monkeypatch, stop)
    store = Store(tmp_path / "store")
    rows = list(store.find_latest("EVD", lambda e: True))
    base = next(row for row in rows if row.side.value == "baseline")
    assert base.observations["source_contract_verified"].value is True
    if stop:
        candidate = next(row for row in rows if row.side.value == "candidate")
        assert candidate.observations["source_contract_verified"].value is True
        assert candidate.observations["input_projection_verified"].value is True
    assert all(row.data_origin.value == "EXAMPLE" for row in rows)


@pytest.mark.parametrize("mode", ["normal", "missing", "swapped"])
def test_two_gpu_versions_defer_candidate_identity_or_preserve_baseline(tmp_path, monkeypatch, mode):
    gpu_pair(monkeypatch)
    deferred.test_existing_update_cli_delayed_identity_or_abort_retains_baseline(tmp_path, monkeypatch, mode)
