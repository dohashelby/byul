"""Shared test isolation.

Managed GPU launches take per-daemon resource leases and keep durable release
fences under the per-user temp directory. Unit tests that simulate cleanup
failures must not leave fences there for later tests or real runs, so each
test gets its own lease root. Lease behaviour itself is unchanged.
"""
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _isolated_resource_lease_root(tmp_path_factory, monkeypatch):
    from guard.runtime import resource_lease
    root = tmp_path_factory.mktemp("resource-leases")
    monkeypatch.setattr(resource_lease, "tempfile", SimpleNamespace(gettempdir=lambda: str(root)))


@pytest.fixture
def example_registered_callback_producers(monkeypatch):
    """EXAMPLE 소비자의 등록 후 분기만 시험한다. 제품 GPU 등록·실측이 아니다.

    새 의존성의 실제 생산자는 별도 시험에서 미등록임을 확인한다. 이 fixture를
    명시한 시험에서만 메모리 내 표를 바꾸며 종료 시 원래 표로 복원한다.
    """
    from guard.collectors import gpu_common_inputs, gpu_input_producers
    from guard.collectors.runner_consumption_v2_producer import producer
    factories = (gpu_input_producers.attempt_producer, gpu_input_producers.transition_producer,
        gpu_input_producers.health_producer, gpu_input_producers.profiler_producer,
        gpu_input_producers.consumption_producer, gpu_input_producers.supply_producer, producer)
    for factory in factories:
        row = factory()
        monkeypatch.setitem(gpu_common_inputs.SUPPORTED_PRODUCERS,
            (row['name'], row['version']), row['source_sha256'])
