"""새 관리 프로브 지원은 검토한 이미지·버전에만 열고 기존 안전 관문을 보존한다."""

import pytest

from guard.adapters.base import Route
from guard.adapters.fake import FakeAdapter
from guard.adapters.vllm import (
    MUTATING_ADMIN_ROUTES,
    PINNED_SAFE_PROBES,
    SAFE_PROBES,
    SAFE_PROBE_REVIEWED_IMAGES,
    VllmAdapter,
)


BASELINE = "vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b"
CANDIDATE = "vllm/vllm-openai-cpu@sha256:827ef38187f9b52f2bb838695ef815ec5b65f4bf3e4c061b002b2b0a19cb0701"
SCALE = Route("POST", "/scale_elastic_ep", kind="admin", mutating=True)
SCALING_STATUS = Route("POST", "/is_scaling_elastic_ep", kind="admin")


@pytest.mark.parametrize("version,image", [("0.31.0", BASELINE), ("0.32.0", CANDIDATE)])
def test_reviewed_scale_probe_keeps_empty_body_and_validation_response(version, image):
    probe = VllmAdapter().probe_request(SCALE, None, version, image)

    assert probe is not None
    assert (probe.method, probe.path, probe.body, probe.headers, probe.basis) == (
        "POST", "/scale_elastic_ep", {}, {"Content-Type": "application/json"}, "safe_probe",
    )
    assert probe.expected_handler_reached == {
        "status": 400, "body_contains": "new_data_parallel_size is required",
    }
    assert image in probe.source
    assert f"v{version}" in probe.source
    assert "2d6805874a868eb63747903f5e10331855b7c277d46a4cef3dca7f20a2e631db" in probe.source
    assert "ffb5e08c2b6cf6d0402c6ba31d0d05e53eb7e9e8c13b29700e84a4bfada420ba" in probe.source
    assert "L27-49" in probe.source and "L350-356" in probe.source
    assert "L62" in probe.source and "L64" in probe.source


@pytest.mark.parametrize("version,image", [("0.31.0", BASELINE), ("0.32.0", CANDIDATE)])
def test_reviewed_status_probe_has_read_only_getter_provenance(version, image):
    probe = VllmAdapter().probe_request(SCALING_STATUS, None, version, image)

    assert probe is not None and probe.body == {} and probe.basis == "safe_probe"
    assert probe.headers == {"Content-Type": "application/json"}
    assert probe.expected_handler_reached == {"status": 200, "body_contains": "is_scaling_elastic_ep"}
    assert image in probe.source
    assert "L83-85" in probe.source and "L13-14" in probe.source
    assert "99ff0d6d3767acae73e5daa7d70e1be42e7e373975122e79a41fa761e27cc26c" in probe.source


@pytest.mark.parametrize("version,image", [
    ("0.32.0", None),
    ("0.32.0", ""),
    ("0.32.0", BASELINE),
    ("0.32.0", "vllm/vllm-openai-cpu:latest"),
    ("0.32.0", CANDIDATE.split("@", 1)[1]),
    ("0.32.0", CANDIDATE.replace("vllm/vllm-openai-cpu", "unreviewed/image")),
    ("0.32.0", CANDIDATE[:-1] + "0"),
    ("0.32.1", CANDIDATE),
    ("0.33.0", CANDIDATE),
    ("v0.32.0", CANDIDATE),
    (None, CANDIDATE),
])
def test_unreviewed_version_or_digest_does_not_enable_mutating_probe(version, image):
    assert VllmAdapter().probe_request(SCALE, None, version, image) is None


@pytest.mark.parametrize("image", [None, "unreviewed/image:tag", CANDIDATE])
def test_legacy_031_version_only_lookup_remains_compatible(image):
    adapter = VllmAdapter()
    assert adapter.probe_request(SCALE, None, "0.31.0", image) is SAFE_PROBES[("0.31.0", "POST", SCALE.path)]
    assert adapter.probe_request(SCALE, None, "0.31.0") is SAFE_PROBES[("0.31.0", "POST", SCALE.path)]
    assert adapter.probe_request(SCALE, None) is None


@pytest.mark.parametrize("path", sorted(MUTATING_ADMIN_ROUTES - {SCALE.path}))
def test_pinned_image_does_not_enable_other_mutating_routes(path):
    route = Route("POST", path, kind="admin", mutating=True)
    assert VllmAdapter().probe_request(route, None, "0.32.0", CANDIDATE) is None


def test_pinned_probe_table_has_only_the_two_reviewed_pairs_and_routes():
    assert SAFE_PROBE_REVIEWED_IMAGES == {"0.31.0": BASELINE, "0.32.0": CANDIDATE}
    assert set(PINNED_SAFE_PROBES) == {
        (version, image, "POST", path)
        for version, image in [("0.31.0", BASELINE), ("0.32.0", CANDIDATE)]
        for path in [SCALE.path, SCALING_STATUS.path]
    }
    assert VllmAdapter().probe_request(
        Route("PUT", SCALE.path, kind="admin", mutating=True), None, "0.32.0", CANDIDATE,
    ) is None


def test_fake_adapter_accepts_the_optional_digest_without_enabling_requests():
    adapter = FakeAdapter()
    assert adapter.probe_request(SCALE, None) is None
    assert adapter.probe_request(SCALE, None, "0.32.0", engine_image_digest=CANDIDATE) is None
