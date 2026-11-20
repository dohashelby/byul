"""Check compile-time fingerprint parity with the existing approved tables."""
from pathlib import Path

import pytest

from tools import mvp_transition_dependency_abi as abi


FIXTURES = Path(__file__).with_name("fixtures")
_CANDIDATE = {
    abi.generation.MP: "vllm-mp-031/multiproc_executor.py",
    abi.generation.WRAPPER: "worker-generation-native/worker_base.py.txt",
    abi.startup.WORKER: "profiler-native/gpu_worker.py.txt",
    abi.startup.TRACING: "worker-startup-native/vllm/tracing/__init__.py",
    abi.startup.OTEL: "worker-startup-native/vllm/tracing/otel.py",
    abi.startup.UTILS: "worker-startup-native/vllm/tracing/utils.py",
    abi.endpoint.UTILS: "ready-endpoint-native/system_utils.py.txt",
}


def test_candidate_source_bytes_reproduce_approved_point_tables(tmp_path):
    for module, source in _CANDIDATE.items():
        path = tmp_path / abi.FILES[module]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((FIXTURES / source).read_bytes())

    result = abi._measure("candidate", tmp_path, abi._runtime())
    assert result["matches_approved_point_codes"] == dict.fromkeys(("/20", "/21", "/22"), True)
    assert result["source_sha256"] == abi.SOURCE_PINS["candidate"]
    assert result["tracing_dependencies"].keys() == {"instrument", *abi.startup.BACKEND_NAMES}


@pytest.mark.parametrize("module,fixture", [
    (abi.generation.MP, "vllm-mp-030/multiproc_executor.py"),
    (abi.startup.WORKER, "profiler-native-030/gpu_worker.py.txt"),
])
def test_baseline_installed_byte_fixtures_measure_distinct_point_codes(module, fixture):
    source = FIXTURES / fixture
    runtime = abi._runtime()
    if module == abi.generation.MP:
        points = abi.generation.TARGETS[module].keys() | abi.endpoint.POINTS
        approved = abi.generation.POINT_CODES[runtime] | abi.endpoint.POINT_CODES[runtime]
    else:
        points = abi.startup.SPAN_NAMES.keys()
        approved = abi.STARTUP_POINTS[runtime]["worker"]
    measured = abi._point_codes(source, module, abi.SOURCE_PINS["baseline"][module], points)
    assert measured.keys() == points
    assert all(measured[point] != approved[point] for point in points)
    with pytest.raises(ValueError, match="^TRANSITION_DEPENDENCY_SOURCE_MISMATCH: "):
        abi._point_codes(source, module, abi.SOURCE_PINS["candidate"][module], points)
