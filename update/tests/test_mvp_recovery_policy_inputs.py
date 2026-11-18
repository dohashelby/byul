from datetime import datetime, timedelta, timezone
import json

import pytest

from guard.core.select import select
from guard.executors.resource_recovery_contract import stamp
from guard.models import DataOrigin, Gap, canonical_json, sha256_hex
from tests.builders import baseline_pack, environment, policy
from tests.test_a1_resource_recovery import packet, selection
from tools import mvp_recovery_policy_inputs as inputs
from tools.mvp_runtime_policy import AMENDMENT, BASE_DECISIONS

T0 = datetime(2026, 11, 18, 18, tzinfo=timezone.utc)


def pair():
    pack = baseline_pack()
    decisions = json.loads(BASE_DECISIONS.read_bytes())['decisions']
    computed = {inputs.START, inputs.END, inputs.ABSENT_AFTER}
    values = {row['key']: row['value'] for row in decisions if row['check_id'] == 'LIF-11' and row['key'] not in computed}
    base = policy(pack.ref(), status='CONFIRMED', confirmed_by='tests', confirmed_at='2026-11-17T00:00:00Z',
        data_origin=DataOrigin.DECLARED, source_document_sha256=sha256_hex(AMENDMENT.read_bytes()), values={'LIF-11': values})
    envs, targets, sources = {}, {}, {}
    for offset, (side, img) in enumerate(inputs.versions().items()):
        env = environment(id=f'ENV-{side}', origin=DataOrigin.DECLARED, runtime={'tp_size': 2, 'gpu_count': 2},
            engine={'name': img['engine'], 'version': img['version'], 'image_digest': img['image']})
        start = T0 + timedelta(seconds=offset)
        finish = start + timedelta(milliseconds=100)
        selected = selection()
        receipt = packet(selected)
        receipt.update(started_wall_ns=inputs._unix_ns(start), finished_wall_ns=inputs._unix_ns(finish))
        ref = env.ref().model_dump(mode='json')
        target = {'environment_ref': ref}
        census = {'profile': inputs.PROFILE, 'probe_source_sha256': inputs.PROBE_SOURCE_SHA256,
            'selection_sha256': receipt['selection_sha256'], 'started_at': start.isoformat(),
            'finished_at': finish.isoformat(), 'collected': True, 'owner_binding_verified': True,
            'clock_window_verified': True, 'packet': receipt, 'diagnostic_codes': []}
        envs[side], targets[side] = env, target
        sources[side] = {'environment_ref': ref, 'target_sha256': sha256_hex(canonical_json(target)),
                         'selection': selected, 'census': census}
    source = {'schema': inputs.SCHEMA, 'base_policy_ref': base.ref().model_dump(mode='json'), 'sides': sources}
    return pack, base, envs, targets, source


def test_window_removes_only_the_policy_value_gap():
    pack, base, envs, targets, source = pair()
    revised = inputs.materialize(base, pack, envs, targets, source, issued_at=T0 + timedelta(seconds=2))
    ordered = [envs[side] for side in ('baseline', 'candidate')]
    assert Gap.NO_POLICY_VALUE in select(pack, ordered, base, mode='update').item('LIF-11').gaps
    assert Gap.NO_POLICY_VALUE not in select(pack, ordered, revised, mode='update').item('LIF-11').gaps
    values = revised.values['LIF-11']
    assert stamp(values[inputs.START]) == T0
    assert stamp(values[inputs.ABSENT_AFTER]) == T0 + timedelta(seconds=30)
    assert stamp(values[inputs.END]) == T0 + timedelta(minutes=10)
    assert values['recovery_require_absent_kinds'] == base.values['LIF-11']['recovery_require_absent_kinds']
    assert revised.refs[-1] == base.ref() and revised.hash_ok()


@pytest.mark.parametrize('damage,diagnostic', [
    ('stale', 'MVP_RECOVERY_PREFLIGHT_OUTSIDE_WINDOW'),
    ('clock', 'MVP_RECOVERY_PREFLIGHT_CENSUS_INCOMPLETE'),
    ('owner', 'MVP_RECOVERY_PREFLIGHT_CENSUS_INCOMPLETE'),
    ('target', 'MVP_RECOVERY_PREFLIGHT_SCOPE_MISMATCH'),
    ('tp1', 'MVP_RECOVERY_PREFLIGHT_TP2_REQUIRED'),
])
def test_lif11_preflight_refuses_unbound_or_late_census(damage, diagnostic):
    pack, base, envs, targets, source = pair()
    now = T0 + timedelta(seconds=2)
    candidate = source['sides']['candidate']
    if damage == 'stale':
        now = T0 + timedelta(seconds=31)
    elif damage == 'clock':
        candidate['census']['packet']['started_wall_ns'] -= 2 * 10**9
    elif damage == 'owner':
        candidate['census']['owner_binding_verified'] = False
    elif damage == 'target':
        candidate['target_sha256'] = '0' * 64
    else:
        env = envs['candidate']
        envs['candidate'] = env.model_copy(update={'runtime': env.runtime.model_copy(update={'tp_size': 1})}).seal()
    with pytest.raises(ValueError, match=diagnostic):
        inputs.materialize(base, pack, envs, targets, source, issued_at=now)
