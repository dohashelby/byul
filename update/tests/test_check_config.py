"""[check_config.<CHECK>]: per-check executor settings for checks sharing one executor (RB2-S)."""
from __future__ import annotations

from pathlib import Path

import pytest

from guard.configuration import for_check
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.base import EvidenceDraft
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import environment, policy
from tests.run_binding_support import fixed_transition, transition_table

ROOT = Path(__file__).resolve().parents[1]
PACK = load_pack(ROOT / 'packs/PACK-BASELINE')
TRANSITION = {'transition_inputs_sha256': 'a' * 64,
              **{f'transition_{p}_at': f'2026-11-14T12:00:0{i}.000Z' for i, p in enumerate(('before', 'during', 'after', 'recovered'))},
              **{f'transition_{p}_generation': 'g' for p in ('before', 'during', 'after', 'recovered')}}


def test_entry_merges_only_into_that_checks_executor_section():
    raw = {'lifecycle_transition': {'generations': ['base'], 'kubectl_binary': 'kubectl'},
           'initialization': {'target': 'shared'},
           'check_config': {'LIF-06': {'generations': ['six'], 'nested': {'a': 1}}}}
    six = for_check(raw, 'lifecycle_transition', 'LIF-06')
    assert six['lifecycle_transition'] == {'generations': ['six'], 'kubectl_binary': 'kubectl', 'nested': {'a': 1}}
    assert six['initialization'] == {'target': 'shared'}
    assert for_check(raw, 'lifecycle_transition', 'LIF-05') is raw  # no entry: the shared section
    assert raw['lifecycle_transition']['generations'] == ['base']  # never mutated
    assert for_check({}, 'x', 'LIF-05') == {}
    for bad in ({'check_config': ['LIF-06']}, {'check_config': {'LIF-06': 'x'}},
                {'check_config': {'LIF-06': {'k': 1}}, 'lifecycle_transition': 'x'}):
        with pytest.raises(ValueError, match='CHECK_CONFIG_INVALID'):
            for_check(bad, 'lifecycle_transition', 'LIF-06')


class Recording:
    name, version = 'lifecycle_transition', '0.0-EXAMPLE'

    def __init__(self, cfg, seen):
        self.cfg, self.seen = cfg, seen

    def run(self, check, env, values, adapter, workdir):
        self.seen[check.id] = self.cfg
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False}, challenges=[], observations={},
                             config={'generations': self.cfg.get('generations')})


def run_three(tmp_path, raw):
    store = Store(tmp_path / 'store')
    env = environment(model={'id': 'm', 'revision': 'r'})
    ref = put_pack(store, PACK.path)
    checks = ['LIF-05', 'LIF-06', 'LIF-16']
    pol = policy(ref, scope={'include': checks}, values={c: {} for c in checks})
    store.put_many([env, pol])
    plan = select(PACK, [env], pol)
    store.put(plan)
    seen = {}
    registry.EXECUTORS['lifecycle_transition:sim'] = lambda cfg: Recording(cfg.get('lifecycle_transition', {}), seen)
    try:
        with fixed_transition(TRANSITION):
            evds = run_plan(store, PACK, plan, RunConfig(executors={'lifecycle_transition': 'sim'}, adapter='vllm', raw=raw))
    finally:
        registry.EXECUTORS.pop('lifecycle_transition:sim')
    return {e.check_id: e for e in evds}, seen


def test_one_run_gives_each_check_its_own_executor_settings(tmp_path):
    tables = {c: transition_table(c, TRANSITION)['run_binding'][c] for c in ('LIF-05', 'LIF-06', 'LIF-16')}
    raw = {'lifecycle_transition': {'generations': ['shared'], 'max_duration_seconds': 300},
           'check_config': {'LIF-05': {'generations': ['five']}, 'LIF-16': {'generations': ['sixteen']}},
           'run_binding': tables}
    evds, seen = run_three(tmp_path, raw)
    assert {c: s['generations'] for c, s in seen.items()} == {'LIF-05': ['five'], 'LIF-06': ['shared'], 'LIF-16': ['sixteen']}
    assert all(s['max_duration_seconds'] == 300 for s in seen.values())
    assert {c: e.execution.status for c, e in evds.items()} == {c: ExecStatus.COMPLETED for c in seen}
    # Each check's own executor config is what its evidence hashes.
    assert evds['LIF-05'].execution.executor.config_sha256 == sha256_hex(canonical_json({'generations': ['five']}))


def test_invalid_check_config_is_not_run(tmp_path):
    evds, seen = run_three(tmp_path, {'lifecycle_transition': {}, 'check_config': ['LIF-05']})
    assert seen == {} and {e.execution.abort_reason for e in evds.values()} == {'CHECK_CONFIG_INVALID'}
    assert {e.execution.status for e in evds.values()} == {ExecStatus.NOT_RUN}
