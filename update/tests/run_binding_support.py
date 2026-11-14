"""Test support for guard-run-binding/1 (PACK-BASELINE@35).

Pipeline tests that replace the collector with an EXAMPLE double have no real
target/prepared files.  They keep the exact hashes their doubles expect by
fixing only the file-hash step; ENV hash and validity window are still computed
by the product code.  Direct binding behaviour is tested in test_run_binding.py.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from guard.executors import run_binding


def split(check, values):
    """(policy values without the run-binding keys, the removed file-hash values)."""
    keys = set(run_binding.keys_for(check))
    policy = {k: v for k, v in (values or {}).items() if k not in keys}
    files = {k: v for k, v in (values or {}).items() if k in keys and not k.endswith(
        ('environment_sha256', 'validity_start', 'validity_end'))}
    return policy, files


@contextmanager
def fixed_file_hashes(hashes):
    """Fix file hashes by policy key; any other key keeps the real loader."""
    real = run_binding.file_hash
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(run_binding, 'file_hash',
                      lambda check_id, key, cfg: hashes[key] if key in hashes else real(check_id, key, cfg))
        yield


def for_pack(pack, check_id, values):
    """Values as a policy for `pack`: drop run-binding keys only where the pack no longer declares them."""
    return split(pack.checks[check_id], values)[0]
