"""Check compile-time fingerprint parity with the existing approved tables."""
from pathlib import Path
import json
import re

import pytest

from tools import mvp_transition_dependency_abi as abi


FIXTURES = Path(__file__).with_name("fixtures")
CONNECTION_FIXTURE = FIXTURES / "cpython-312-transition-connection" / "connection.py.txt"
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
        abi.weight_iteration.MODELS: "vllm-0.30.0/vllm/model_executor/models/utils.py.txt",
    },
    "candidate": {
        abi.weight_iteration.DEFAULT: "weight-iteration-native/default_loader.py.txt",
        abi.weight_iteration.WEIGHTS: "weight-iteration-native/weight_utils.py.txt",
        abi.weight_iteration.MODELS: "weight-iteration-native/models-utils.py.txt",
    },
}


def stage_source_fixtures(directory):
    """Stage the pinned vLLM files under baseline/ and candidate/ for replay."""
    provenance = json.loads((FIXTURES / "vllm-0.30.0/PROVENANCE.json").read_bytes())["files"]
    for side, fixtures in (("baseline", _BASELINE), ("candidate", _CANDIDATE)):
        assert fixtures.keys() == abi.FILES.keys() == abi.SOURCE_PINS[side].keys()
        for module, source in fixtures.items():
            path = directory / side / abi.FILES[module]
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
            path = directory / side / (module.replace(".", "/") + ".py")
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = (FIXTURES / source).read_bytes()
            if side == "baseline":
                row = provenance[source.split("/", 1)[1]]
                assert row["bytes"] == len(raw)
                assert row["image"].endswith("@" + abi.IMAGE_DIGESTS[side])
                assert row["sha256"] == abi._WEIGHT_SOURCE_PINS[side][module]
            path.write_bytes(raw)


def test_paired_installed_sources_reproduce_transition_point_tables(tmp_path):
    stage_source_fixtures(tmp_path)
    connection_sources = json.loads(CONNECTION_FIXTURE.with_name("PROVENANCE.json").read_bytes())
    result = abi.measure(tmp_path / "baseline", tmp_path / "candidate", include_weight_iteration=True,
        baseline_connection=CONNECTION_FIXTURE, candidate_connection=CONNECTION_FIXTURE)
    assert result["schema"] == abi.SCHEMA
    assert result["runtime"] == abi._runtime()
    assert result["engine_executed"] is False
    assert result["producer_registered"] is False
    baseline, candidate = (result["sides"][side] for side in ("baseline", "candidate"))
    for side, row in result["sides"].items():
        assert row["source_sha256"] == abi.SOURCE_PINS[side]
        assert row["image"].endswith("@" + abi.IMAGE_DIGESTS[side])
        assert row["tracing_dependencies"].keys() == {"instrument", *abi.startup.BACKEND_NAMES}
        sources = connection_sources[side]
        connection = row["stdlib_connection"]
        assert connection["source_scope"] == "image_installation_layer"
        assert connection["source_path"] == sources["source_path"]
        assert connection["source_sha256"] == sources["sha256"]
        assert sources["image"].endswith("@" + abi.IMAGE_DIGESTS[side])
        assert CONNECTION_FIXTURE.name == sources["fixture"]
        assert CONNECTION_FIXTURE.stat().st_size == sources["bytes"]
        expected_delegates = {"/21": abi.generation.CONNECTION_CODES[result["runtime"]],
                              "/22": abi.endpoint.DELEGATE_CODES[result["runtime"]]}
        assert connection["point_codes"].keys() == expected_delegates.keys()
        for profile, expected in expected_delegates.items():
            actual = connection["point_codes"][profile]
            assert actual.keys() == expected.keys()
            assert all(re.fullmatch("[0-9a-f]{64}", digest) for digest in actual.values())
            assert connection["matches_approved_point_codes"][profile] is (actual == expected)
    assert baseline["stdlib_connection"] == candidate["stdlib_connection"]
    assert connection_sources["baseline"]["layer"] != connection_sources["candidate"]["layer"]
    profiles = ("/20", "/21", "/22")
    assert candidate["matches_approved_point_codes"] == dict.fromkeys(profiles, True)
    assert baseline["matches_approved_point_codes"] == dict.fromkeys(profiles, False)
    for profile in profiles:
        assert baseline["point_codes"][profile].keys() == candidate["point_codes"][profile].keys()

    approved = abi.weight_iteration.RUNTIME_POINT_CODES[result["runtime"]]
    assert candidate["weight_iteration"] == {
        "complete": True, "source_sha256": abi.weight_iteration.SOURCE_ADDITIONS,
        "point_codes": approved, "matches_approved_point_codes": True,
    }
    baseline_weights = baseline["weight_iteration"]
    assert baseline_weights["point_codes"].keys() == approved.keys()
    assert all(digest != approved[point] for point, digest in baseline_weights["point_codes"].items())
    assert baseline_weights["source_sha256"].keys() == set(_WEIGHT_FIXTURES["baseline"])
    assert baseline_weights["complete"] is True
    assert baseline_weights["matches_approved_point_codes"] is False
    if result["runtime"] == "cpython-3.12.3" and abi.platform.machine() == "x86_64":
        assert baseline_weights["matches_baseline_point_codes"] is True
    else:
        assert baseline_weights["matches_baseline_point_codes"] is None

    module = abi.generation.MP
    target = tmp_path / "baseline" / abi.FILES[module]
    target.write_bytes((tmp_path / "candidate" / abi.FILES[module]).read_bytes())
    with pytest.raises(ValueError, match=r"^TRANSITION_DEPENDENCY_SOURCE_MISMATCH: vllm\.v1\.executor\.multiproc_executor$"):
        abi.measure(tmp_path / "baseline", tmp_path / "candidate")


@pytest.mark.parametrize("foreign", [abi.weight_iteration.DEFAULT, abi.weight_iteration.MODELS])
def test_pinned_weight_source_rejects_foreign_bytes(tmp_path, foreign):
    for module, baseline_path in _WEIGHT_FIXTURES["baseline"].items():
        source = _WEIGHT_FIXTURES["candidate"][module] if module == foreign else baseline_path
        path = tmp_path / (module.replace(".", "/") + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((FIXTURES / source).read_bytes())
    with pytest.raises(ValueError, match=f"^TRANSITION_DEPENDENCY_SOURCE_MISMATCH: {re.escape(foreign)}$"):
        abi._measure_weight_iteration("baseline", tmp_path, abi._runtime())


def test_connection_source_inputs_must_be_paired(tmp_path):
    with pytest.raises(ValueError, match="^TRANSITION_CONNECTION_PAIRED_INPUTS_REQUIRED$"):
        abi.measure(tmp_path / "baseline", tmp_path / "candidate",
                    baseline_connection=tmp_path / "connection.py")


def test_connection_immutable_source_pin_rejects_appended_bytes(tmp_path):
    raw = CONNECTION_FIXTURE.read_bytes()
    changed = tmp_path / "connection.py"
    changed.write_bytes(raw + b"\n# Extra fixture bytes.\n")
    with pytest.raises(ValueError, match=r"^TRANSITION_DEPENDENCY_SOURCE_MISMATCH: multiprocessing\.connection$"):
        abi._measure_connection(changed, "baseline", abi._runtime())


@pytest.mark.parametrize("override", [
    {},
    {"version": "0.31.0"},
    {"image": "vllm/vllm-openai@" + abi.IMAGE_DIGESTS["candidate"]},
    {"image": "unrelated.example/vllm-openai@" + abi.IMAGE_DIGESTS["baseline"]},
    {"sys_platform": "darwin"},
    {"machine": "aarch64"},
    {"runtime": "cpython-3.12.15"},
    {"optimize": 1},
    {"optimize": False},
])
def test_baseline_weight_profile_uses_exact_release_and_compiler_identity(override):
    identity = {"engine": "vllm", "version": "0.30.0", "image": "vllm/vllm-openai@" + abi.IMAGE_DIGESTS["baseline"],
                "sys_platform": "linux", "machine": "x86_64", "runtime": "cpython-3.12.3", "optimize": 0}
    selected = abi.baseline_weight_profile(**(identity | override))
    if override:
        assert selected is None
    else:
        assert selected["source_sha256"].keys() == set(_WEIGHT_FIXTURES["baseline"])
        assert selected["source_bytes"].keys() == selected["source_sha256"].keys()
        assert selected["point_codes"].keys() == abi.weight_iteration.POINTS
        selected["point_codes"].clear()
        assert abi.baseline_weight_profile(**identity)["point_codes"].keys() == abi.weight_iteration.POINTS
