"""Test support for guard-run-binding/1·/2 (PACK-BASELINE@35, @36).

Pipeline tests that replace the collector with an EXAMPLE double have no real
target/prepared files.  They keep the exact hashes their doubles expect by
fixing only the file-hash step; ENV hash and validity window are still computed
by the product code.  Direct binding behaviour is tested in test_run_binding.py.
Stage-2 values (@36) are given as the `[run_binding.<CHECK>]` table, the way
an operator gives them (`run_table`); test_run_binding_stage2.py covers them.
Stage-3 values (@37) are given the same way: `run_table` names EXAMPLE
placeholder producer files and issuer events that carry the value the double
expects, and `example_inputs` (autouse in conftest) answers only those
placeholders; a real path still goes through the product code
(test_run_binding_stage3.py).
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from guard.executors import run_binding


def run_locked_update_trial(store, pack, plan, config, *, only_side=None):
    """Exercise the coordinator's dispatch boundary with a real PLAN lock."""
    from guard.core.execution_recipe import execution_lock
    from guard.core.run import run_plan_locked, validate_update_run_config

    assert plan.mode == 'update'
    validate_update_run_config(config)
    with execution_lock(store.root, plan.id):
        return run_plan_locked(store, pack, plan, config, only_side=only_side)


def before_stage4(check):
    """Replay the earlier definition's policy ownership, including its old decision-file hashes."""
    from guard.models.checkdef import PolicyParam
    old = check.model_copy(deep=True)
    decision = run_binding.DECISION_KEYS_4.get(check.id)
    old.policy_params = [p for p in old.policy_params if p.key != decision]
    declared = {p.key for p in old.policy_params}
    old.policy_params += [PolicyParam(key=k, type='str', required=True)
                          for k in run_binding.KEYS_BY_CHECK_4.get(check.id, ()) if k not in declared]
    return old


def split(check, values):
    """(policy values without the run-binding keys, the removed file-hash values)."""
    keys = set(run_binding.keys_for(check))
    policy = {k: v for k, v in (values or {}).items() if k not in keys}
    stage_2 = set(run_binding.KEYS_BY_CHECK_2.get(check.id, ())) | set(run_binding.DROPPED_BY_CHECK_2.get(check.id, ()))
    policy = {k: v for k, v in policy.items() if not (k in stage_2 and 2 in run_binding.stages_for(check))}
    stage_3 = set(run_binding.KEYS_BY_CHECK_3.get(check.id, ()))
    policy = {k: v for k, v in policy.items() if not (k in stage_3 and 3 in run_binding.stages_for(check))}
    files = {k: v for k, v in (values or {}).items() if k in keys and k not in stage_2 and k not in stage_3 and not k.endswith(
        ('environment_sha256', 'validity_start', 'validity_end'))}
    return policy, files


PRODUCER = 'EXAMPLE-producer:'
ISSUER_EVENT = 'EXAMPLE-issuer-event:'


def stage_3_table(check, values):
    """`[run_binding.<CHECK>]` stage-3 entries carrying the values a double expects (EXAMPLE placeholders)."""
    import json
    values, entry = values or {}, {}
    if 3 not in run_binding.stages_for(check):
        return entry
    for name, key in run_binding.PRODUCERS_3.get(check.id, {}).items():
        if key in values:
            entry[name] = PRODUCER + values[key]
    if check.id == 'OBS-01' and all(k in values for k in run_binding._ACTORS):
        entry['test_actors'] = {role: values[f'observation_{role}_actor_sha256'] for role in ('allowed', 'denied')}
    if check.id in run_binding._EVENT_FIELDS:
        prefix = run_binding._EVENT_PREFIX[check.id]
        event = {f: values[f'{prefix}_{f}'] for f in run_binding._EVENT_FIELDS[check.id] if f'{prefix}_{f}' in values}
        if event:
            entry['issuer_event'] = ISSUER_EVENT + check.id + ':' + json.dumps(event, sort_keys=True)
    return entry


@contextmanager
def example_inputs():
    """Answer only the EXAMPLE placeholders of `stage_3_table`; any other path uses the product code."""
    import json
    from guard.models import canonical_json, sha256_hex
    real_producer, real_event = run_binding.producer_sha256, run_binding._issuer_event

    def producer(path):
        if type(path) is str and path.startswith(PRODUCER):
            return path[len(PRODUCER):]
        return real_producer(path)

    def issuer_event(check_id, path):
        if type(path) is str and path.startswith(ISSUER_EVENT):
            owner, _, text = path[len(ISSUER_EVENT):].partition(':')
            event = json.loads(text)
            if owner != check_id or set(event) != set(run_binding._EVENT_FIELDS[check_id]):
                raise run_binding.RunBindingUnavailable('RUN_BINDING_CONFIG_INVALID')
            prefix = run_binding._EVENT_PREFIX[check_id]
            return sha256_hex(canonical_json(event)), {f'{prefix}_{k}': v for k, v in event.items()}
        return real_event(check_id, path)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(run_binding, 'producer_sha256', producer)
        patch.setattr(run_binding, '_issuer_event', issuer_event)
        yield


def run_table(check, values):
    """`{'run_binding': {CHECK: ...}}` config from older-pack values (stage-2 sessions, stage-3 placeholders)."""
    entry = {}
    if 2 in run_binding.stages_for(check):
        row = run_binding.run_config_candidates({check.id: values or {}}).get(check.id, {})
        entry = {k: v for k, v in row.items() if k in run_binding.CONFIG_KEYS.get(check.id, ())}
    entry.update(stage_3_table(check, values))
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
    def value(v):
        if isinstance(v, dict):
            return '{' + ', '.join(f'{json.dumps(k)} = {value(x)}' for k, x in v.items()) + '}'
        return json.dumps(v)
    return f'\n[run_binding.{check_id}]\n' + ''.join(f'{k}={value(v)}\n' for k, v in table.items())


@contextmanager
def window_opened_before(seconds=60):
    """Open the computed run window `seconds` before guard run starts, keeping the executor limit.

    Doubles that write their records while the test is being set up (before
    `guard run`) would otherwise fall outside a window that starts at the run's
    own second, depending only on whether the clock ticked in between.  Only
    the window start moves; the window length, the policy intersection and the
    executors' own window checks stay the product code's.
    """
    from datetime import timedelta
    real = run_binding.window
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(run_binding, 'window', lambda executor, issued_at, *args, **kwargs:
                      real(executor, issued_at - timedelta(seconds=seconds), *args, **kwargs))
        yield
