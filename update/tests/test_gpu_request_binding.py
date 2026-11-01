"""GPU image contract through the same injected Kubernetes/product interfaces."""
import pytest

from guard.adapters.vllm import VllmAdapter, CHAT_REVIEWED_IMAGE, REQUEST_BINDING_GPU_IMAGE, REQUEST_BINDING_GPU_032_IMAGE
from tests import test_kubernetes_request_binding as kube
from tests.test_request_binding import judge


def gpu_contract(monkeypatch, version="0.31.0"):
    original_env, original_target = kube.env, kube.target
    image = {"0.31.0": REQUEST_BINDING_GPU_IMAGE, "0.32.0": REQUEST_BINDING_GPU_032_IMAGE}[version]

    def environment():
        value = original_env().model_copy(deep=True)
        value.engine.image_digest = image
        value.engine.version = version
        return value.seal()

    def target(environment_=None):
        value = original_target(environment_ or environment())
        value["observed_version"] = version
        value["engine"]["image_digest"] = image
        value["kubernetes"]["engine"].update(image=image, image_id=image)
        value["comparison_config"]["launch"]["profile"] = "gpu-bounded-v1"
        return value

    monkeypatch.setattr(kube, "env", environment)
    monkeypatch.setattr(kube, "target", target)


@pytest.mark.parametrize("version", ["0.31.0", "0.32.0"])
def test_gpu_runs_same_json_sse_cases_without_claiming_gpu_execution(tmp_path, monkeypatch, version):
    gpu_contract(monkeypatch, version)
    draft, world = kube.run(tmp_path)
    assert draft.data_origin.value == "EXAMPLE"
    assert draft.observations["source_contract_verified"].value is True
    assert draft.observations["input_projection_verified"].value is True
    assert draft.observations["output_binding_verified"].value is False
    assert sum(map(len, world.packets)) == 20 and world.template_reads == 2
    result = judge(draft)
    assert result.verdicts["functional"].value == "PASS"
    assert result.verdicts["security"].value == "UNKNOWN"


@pytest.mark.parametrize("late", ["exception", "restart", "cleanup", "duplicate", "truncated"])
@pytest.mark.parametrize("version", ["0.31.0", "0.32.0"])
def test_gpu_retains_independent_fail_after_later_gap(tmp_path, monkeypatch, late, version):
    gpu_contract(monkeypatch, version)
    kube.test_independently_bound_fail_survives_later_collection_or_cleanup_gap(tmp_path, late)


@pytest.mark.parametrize("mode,expected", [("normal", "UNKNOWN"), ("foreign", "FAIL"),
                                          ("attachment", "FAIL"), ("wrong-owner", "UNKNOWN")])
@pytest.mark.parametrize("version", ["0.31.0", "0.32.0"])
def test_gpu_cli_refs_survive_export_restore_rejudge_and_attachment_error(tmp_path, monkeypatch, mode, expected, version):
    gpu_contract(monkeypatch, version)
    kube.test_existing_cli_evd_res_rpt_refs_export_restore_and_rejudge(tmp_path, monkeypatch, mode, expected)


@pytest.mark.parametrize("damage", ["cpu_profile", "unknown_profile", "writable", "wrong_template"])
@pytest.mark.parametrize("version", ["0.31.0", "0.32.0"])
def test_gpu_rejects_unreviewed_template_or_launch_before_requests(tmp_path, monkeypatch, damage, version):
    gpu_contract(monkeypatch, version)
    value = kube.target()
    if damage in {"cpu_profile", "unknown_profile"}:
        value["comparison_config"]["launch"]["profile"] = "cpu-bounded-v1" if damage == "cpu_profile" else "gpu-unbounded"
    elif damage == "wrong_template":
        value["comparison_config"]["launch"]["chat_template"]["sha256"] = "0" * 64
    world = kube.World(value)
    if damage == "writable":
        original = world.runner

        def writable(argv, **kwargs):
            import json
            result = original(argv, **kwargs)
            if "get" in argv and "pod" in argv:
                row = json.loads(result["stdout_bytes"])
                if row["metadata"]["name"] == value["kubernetes"]["engine"]["pod"]:
                    row["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = False
                    result["stdout_bytes"] = json.dumps(row).encode()
            return result

        world.runner = writable
    draft, world = kube.run(tmp_path, world)
    assert not world.packets
    assert draft.observations["profile_supported"].value is False
    assert judge(draft).verdicts["security"].value == "UNKNOWN"


@pytest.mark.parametrize("image,version", [
    (REQUEST_BINDING_GPU_IMAGE.replace("a4a4", "a4a5"), "0.31.0"),
    ("vllm/vllm-openai:v0.31.0", "0.31.0"),
    ("vllm/vllm-openai@sha256:c1c9f6fd5c109ba7f0546a59f5b2f15fb87f64c77782e90a27b648b42a8e67c3", "0.31.0"),
    ("vllm/vllm-openai@sha256:c76d0e2225a4b1cb1e2109ace39639f55e714abd1a7a427acc8b0bbd7f6a83b3", "0.31.0"),
    (REQUEST_BINDING_GPU_IMAGE, "0.32.0"),
    (REQUEST_BINDING_GPU_032_IMAGE, "0.31.0"),
    ("vllm/vllm-openai-cpu@sha256:827ef38187f9b52f2bb838695ef815ec5b65f4bf3e4c061b002b2b0a19cb0701", "0.32.0"),
    ("vllm/vllm-openai@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34", "0.32.0"),
    ("vllm/vllm-openai@sha256:bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d", "0.32.0"),
])
def test_gpu_contract_does_not_infer_tag_index_config_or_other_version(image, version):
    assert VllmAdapter().request_binding_contract(image, version) is None


def test_cpu_contract_and_other_gpu_gates_are_unchanged():
    adapter = VllmAdapter()
    cpu = adapter.request_binding_contract(CHAT_REVIEWED_IMAGE, "0.31.0")
    gpu = adapter.request_binding_contract(REQUEST_BINDING_GPU_IMAGE, "0.31.0")
    assert "launch_profile" not in cpu
    assert gpu["sources"] == cpu["sources"]
    assert gpu["launch_profile"] == "gpu-bounded-v1"
    assert adapter.native_log_contract(REQUEST_BINDING_GPU_IMAGE, "0.31.0") is None
    assert adapter.native_log_contract(REQUEST_BINDING_GPU_032_IMAGE, "0.32.0") is None
