"""Test support for guard-run-binding/1·/2 (PACK-BASELINE@35, @36).

Pipeline tests that replace the collector with an EXAMPLE double have no real
target/prepared files.  They keep the exact hashes their doubles expect by
fixing only the file-hash step; ENV hash and validity window are still computed
by the product code.  Direct binding behaviour is tested in test_run_binding.py.
Stage-2 values (@36) are given as the `[run_binding.<CHECK>]` table, the way
an operator gives them (`run_table`); test_run_binding_stage2.py covers them.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from guard.executors import run_binding


def split(check, values):
    """(policy values without the run-binding keys, the removed file-hash values)."""
    keys = set(run_binding.keys_for(check))
    policy = {k: v for k, v in (values or {}).items() if k not in keys}
    stage_2 = set(run_binding.KEYS_BY_CHECK_2.get(check.id, ())) | set(run_binding.DROPPED_BY_CHECK_2.get(check.id, ()))
    policy = {k: v for k, v in policy.items() if not (k in stage_2 and 2 in run_binding.stages_for(check))}
    files = {k: v for k, v in (values or {}).items() if k in keys and k not in stage_2 and not k.endswith(
        ('environment_sha256', 'validity_start', 'validity_end'))}
    return policy, files


def run_table(check, values):
    """`{'run_binding': {CHECK: ...}}` config for a stage-2 definition from @35-style values (sessions only)."""
    if 2 not in run_binding.stages_for(check):
        return {}
    row = run_binding.run_config_candidates({check.id: values or {}}).get(check.id, {})
    entry = {k: v for k, v in row.items() if k in run_binding.CONFIG_KEYS.get(check.id, ())}
    return {'run_binding': {check.id: entry}} if entry else {}


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


@contextmanager
def fixed_transition(values):
    """Fix the LIF-05/06/16 stage-2 values for doubles with a fake clock and generation loader.

    The `[run_binding.<CHECK>]` table must still have the right shape; only the
    computation from the real clock and files is replaced.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(run_binding, '_transition', lambda executor_cfg, run_cfg, issued_at: dict(values))
        yield


def transition_table(check_id, values):
    """A well-formed `[run_binding.<CHECK>]` entry naming the same phase generations."""
    phases = run_binding._PHASES
    return {'run_binding': {check_id: {
        'phase_generation': {p: values[f'transition_{p}_generation'] for p in phases},
        'sample_offsets_ms': {p: i * 1000 for i, p in enumerate(phases)}}}}


def run_table_toml(pack, check_id, values):
    """TOML for `run_table` (appended to a test's guard.toml); empty for a pack that still declares the values."""
    import json
    table = run_table(pack.checks[check_id], values).get('run_binding', {}).get(check_id)
    if not table:
        return ''
    return f'\n[run_binding.{check_id}]\n' + ''.join(f'{k}={json.dumps(v)}\n' for k, v in table.items())
