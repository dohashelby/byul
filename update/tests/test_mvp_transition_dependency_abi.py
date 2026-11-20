"""Check compile-time fingerprint parity with the existing approved tables."""
from pathlib import Path
import json

import pytest

from tools import mvp_transition_dependency_abi as abi


FIXTURES = Path(__file__).with_name("fixtures")
_BASELINE = {module: f"vllm-0.30.0/{path}.txt" for module, path in abi.FILES.items()}
_BASELINE[abi.generation.MP] = "vllm-mp-030/multiproc_executor.py"
_BASELINE[abi.startup.WORKER] = "profiler-native-030/gpu_worker.py.txt"
_CANDIDATE = {
    abi.generation.MP: "vllm-mp-031/multiproc_executor.py",
    abi.generation.WRAPPER: "worker-generation-native/worker_base.py.txt",
    abi.startup.WORKER: "profiler-native/gpu_worker.py.txt",
    abi.startup.TRACING: "worker-startup-native/vllm/tracing/__init__.py",
    abi.startup.OTEL: "worker-startup-native/vllm/tracing/otel.py",
    abi.startup.UTILS: "worker-startup-native/vllm/tracing/utils.py",
    abi.endpoint.UTILS: "ready-endpoint-native/system_utils.py.txt",
}
_WEIGHT_FIXTURES = {
    "baseline": {
        abi.weight_iteration.DEFAULT: "vllm-0.30.0/vllm/model_executor/model_loader/default_loader.py.txt",
        abi.weight_iteration.WEIGHTS: "vllm-0.30.0/vllm/model_executor/model_loader/weight_utils.py.txt",
    },
    "candidate": {
        abi.weight_iteration.DEFAULT: "weight-iteration-native/default_loader.py.txt",
        abi.weight_iteration.WEIGHTS: "weight-iteration-native/weight_utils.py.txt",
        abi.weight_iteration.MODELS: "weight-iteration-native/models-utils.py.txt",
    },
}


def test_paired_installed_sources_reproduce_transition_point_tables(tmp_path):
    provenance = json.loads((FIXTURES / "vllm-0.30.0/PROVENANCE.json").read_bytes())["files"]
    for side, fixtures in (("baseline", _BASELINE), ("candidate", _CANDIDATE)):
        assert fixtures.keys() == abi.FILES.keys() == abi.SOURCE_PINS[side].keys()
        for module, source in fixtures.items():
            path = tmp_path / side / abi.FILES[module]
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = (FIXTURES / source).read_bytes()
            if side == "baseline" and source.startswith("vllm-0.30.0/"):
                row = provenance[source.split("/", 1)[1]]
                assert row["source"] == abi.FILES[module]
                assert row["bytes"] == len(raw)
                assert row["sha256"] == abi.SOURCE_PINS[side][module]
                assert row["image"].endswith("@" + abi.IMAGE_DIGESTS[side])
            path.write_bytes(raw)

    for side, fixtures in _WEIGHT_FIXTURES.items():
        for module, source in fixtures.items():
            path = tmp_path / side / (module.replace(".", "/") + ".py")
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = (FIXTURES / source).read_bytes()
            if side == "baseline":
                row = provenance[source.split("/", 1)[1]]
                assert row["bytes"] == len(raw)
                assert row["image"].endswith("@" + abi.IMAGE_DIGESTS[side])
                assert row["sha256"] == abi._WEIGHT_SOURCE_PINS[side][module]
            path.write_bytes(raw)
    # Staging known bytes from a different release at the unverified baseline
    # slot cannot make it a reviewed installed dependency.
    models_path = abi.weight_iteration.MODELS.replace(".", "/") + ".py"
    unverified_path = tmp_path / "baseline" / models_path
    unverified_path.parent.mkdir(parents=True, exist_ok=True)
    unverified_path.write_bytes((tmp_path / "candidate" / models_path).read_bytes())

    result = abi.measure(tmp_path / "baseline", tmp_path / "candidate", include_weight_iteration=True)
    assert result["schema"] == abi.SCHEMA
    assert result["runtime"] == abi._runtime()
    assert result["engine_executed"] is False
    assert result["producer_registered"] is False
    baseline, candidate = (result["sides"][side] for side in ("baseline", "candidate"))
    for side, row in result["sides"].items():
        assert row["source_sha256"] == abi.SOURCE_PINS[side]
        assert row["image"].endswith("@" + abi.IMAGE_DIGESTS[side])
        assert row["tracing_dependencies"].keys() == {"instrument", *abi.startup.BACKEND_NAMES}
    profiles = ("/20", "/21", "/22")
    assert candidate["matches_approved_point_codes"] == dict.fromkeys(profiles, True)
    assert baseline["matches_approved_point_codes"] == dict.fromkeys(profiles, False)
    for profile in profiles:
        assert baseline["point_codes"][profile].keys() == candidate["point_codes"][profile].keys()

    approved = abi.weight_iteration.RUNTIME_POINT_CODES[result["runtime"]]
    assert candidate["weight_iteration"] == {
        "complete": True, "source_sha256": abi.weight_iteration.SOURCE_ADDITIONS,
        "point_codes": approved, "unverified_modules": {}, "matches_approved_point_codes": True,
    }
    baseline_weights = baseline["weight_iteration"]
    missing_points = ["AutoWeightsLoader._load_module", "AutoWeightsLoader._load_param"]
    assert baseline_weights["unverified_modules"] == {abi.weight_iteration.MODELS: missing_points}
    assert baseline_weights["point_codes"].keys() == approved.keys() - set(missing_points)
    assert all(digest != approved[point] for point, digest in baseline_weights["point_codes"].items())
    assert baseline_weights["source_sha256"].keys() == set(_WEIGHT_FIXTURES["baseline"])
    assert baseline_weights["complete"] is False
    assert baseline_weights["matches_approved_point_codes"] is None

    module = abi.generation.MP
    target = tmp_path / "baseline" / abi.FILES[module]
    target.write_bytes((tmp_path / "candidate" / abi.FILES[module]).read_bytes())
    with pytest.raises(ValueError, match=r"^TRANSITION_DEPENDENCY_SOURCE_MISMATCH: vllm\.v1\.executor\.multiproc_executor$"):
        abi.measure(tmp_path / "baseline", tmp_path / "candidate")


def test_pinned_weight_source_rejects_foreign_bytes(tmp_path):
    iteration = abi.weight_iteration
    fixture = _WEIGHT_FIXTURES["candidate"][iteration.DEFAULT]
    filename = "vllm/model_executor/model_loader/default_loader.py"
    path = tmp_path / filename
    path.parent.mkdir(parents=True)
    path.write_bytes((FIXTURES / fixture).read_bytes())
    with pytest.raises(ValueError, match=r"^TRANSITION_DEPENDENCY_SOURCE_MISMATCH: vllm\.model_executor\.model_loader\.default_loader$"):
        abi._measure_weight_iteration("baseline", tmp_path, abi._runtime())
