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
