"""RB3 (PACK-BASELINE@37): producer sources, OBS-01 test actors and PLT-10/11 issuer events leave the policy."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml

from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry, run_binding
from guard.executors.base import EvidenceDraft
from guard.executors.stored_response_contract import REVIEWED_SOURCES
from guard.models import DataOrigin, ExecStatus, load_pack, utcnow
from guard.models.checkdef import PolicyParam
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import environment, policy

ROOT = Path(__file__).resolve().parents[1]
PACK = load_pack(ROOT / 'packs/PACK-BASELINE')
SESSION = '0123456789abcdef0123456789abcdef'


@pytest.fixture(autouse=True)
def fixed_prepared():
    """Stage-1 prepared hashes need real sensor assets; they are covered in test_run_binding.py."""
    from tests.run_binding_support import fixed_file_hashes
    keys = ('supply_normal_prepared_sha256', 'supply_challenge_prepared_sha256', 'revocation_prepared_sha256',
            'session_prepared_sha256')
    with fixed_file_hashes({k: '1' * 64 for k in keys}):
        yield


def env():
    return environment(model={'id': 'm', 'revision': 'r'})


def at36(check_id):
    from tests.run_binding_support import before_stage4
    old = before_stage4(PACK.checks[check_id])
    old.policy_params = list(old.policy_params) + [PolicyParam(key=k, type='str', required=True)
                                                   for k in run_binding.KEYS_BY_CHECK_3[check_id]]
    return old


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
    return str(path)


def supply_cfg(tmp_path):
    from tests.test_run_binding import target_file
    return {'normal': {'target': str(target_file(tmp_path))}, 'challenge': {'target': str(target_file(tmp_path))}}


def test_pack_37_moves_only_stage_3_keys_and_pack_36_definitions_replay():
    assert PACK.manifest.revision == 38
    for cid, keys in run_binding.KEYS_BY_CHECK_3.items():
        check = PACK.checks[cid]
        assert 3 in run_binding.stages_for(check) and not {p.key for p in check.policy_params} & set(keys)
        assert 3 not in run_binding.stages_for(at36(cid))
        assert run_binding.config_keys(at36(cid)) == run_binding.CONFIG_KEYS.get(cid, frozenset()) or \
            not run_binding.config_keys(at36(cid))
    # Decision-bound values stay in policy (RB3-Q).
    for cid, key in (('PLT-03', 'supply_allowed_objects'), ('PLT-05', 'supply_allowed_identities'),
                     ('REQ-07', 'allowed_template_sha256'), ('PLT-12', 'copy_treatments'),
                     ('LIF-12', 'retry_limits'), ('REQ-05', 'selected_model_grants'),
                     ('PLT-10', 'revocation_deadline_basis')):
        assert key in {p.key for p in PACK.checks[cid].policy_params}


def test_producer_source_is_the_sha256_of_the_named_file_bytes(tmp_path):
    raw = b'#!/usr/bin/env python3\n# EXAMPLE producer\r\nprint(1)\n'
    producer = write(tmp_path, 'producer.py', raw)
    document, values = run_binding.compute(PACK.checks['SUP-05'], supply_cfg(tmp_path), env(), side='baseline',
                                           attempt_id='A', issued_at=utcnow(), run_cfg={'record_producer': producer})
    digest = hashlib.sha256(raw).hexdigest()  # bytes, never canonical JSON or a normalised text
    assert values['supply_record_source_sha256'] == digest == document['files']['record_producer_sha256']
    assert document['schema'] == run_binding.SCHEMA_2 and document['values'] == {'supply_record_source_sha256': digest}
    link = tmp_path / 'link.py'
    os.symlink(producer, link)
    for bad, code in (({'record_producer': str(link)}, 'FILE_UNAVAILABLE'),
                      ({'record_producer': str(tmp_path / 'absent.py')}, 'FILE_UNAVAILABLE'),
                      ({'record_producer': str(tmp_path)}, 'FILE_UNAVAILABLE'),
                      (None, 'CONFIG_INVALID'), ({}, 'CONFIG_INVALID'),
                      ({'record_producer': producer, 'record_session': SESSION}, 'CONFIG_INVALID')):
        with pytest.raises(run_binding.RunBindingUnavailable, match=code):
            run_binding.compute(PACK.checks['SUP-05'], supply_cfg(tmp_path), env(), side='single', attempt_id=None,
                                issued_at=utcnow(), run_cfg=bad)


def test_req04_has_two_producers_and_req06_uses_the_reviewed_adapter_sources(tmp_path):
    from tests.test_run_binding import target_file
    target = {'target': str(target_file(tmp_path))}
    gateway, engine = write(tmp_path, 'g.py', b'gateway'), write(tmp_path, 'e.py', b'engine')
    _, values = run_binding.compute(PACK.checks['REQ-04'], target, env(), side='single', attempt_id=None, issued_at=utcnow(),
                                    run_cfg={'record_session': SESSION, 'gateway_producer': gateway, 'engine_producer': engine}, policy_row={})
    assert values['forwarded_gateway_source_sha256'] == hashlib.sha256(b'gateway').hexdigest()
    assert values['forwarded_engine_source_sha256'] == hashlib.sha256(b'engine').hexdigest()
    document, values = run_binding.compute(PACK.checks['REQ-06'], target, env(), side='single', attempt_id=None,
                                           issued_at=utcnow(), run_cfg={'record_session': SESSION}, policy_row={})
    assert {k: values[f'stored_{k}_source_sha256'] for k in REVIEWED_SOURCES} == REVIEWED_SOURCES
    assert 'record_producer_sha256' not in document['files']


def test_obs01_test_actors_move_as_they_are(tmp_path):
    from tests.test_run_binding import target_file
    target = {'target': str(target_file(tmp_path))}
    producer = write(tmp_path, 'gw.py', b'gateway')
    good = {'record_session': SESSION, 'record_producer': producer, 'test_actors': {'allowed': 'a' * 64, 'denied': 'b' * 64}}
    _, values = run_binding.compute(PACK.checks['OBS-01'], target, env(), side='single', attempt_id=None,
                                    issued_at=utcnow(), run_cfg=good, policy_row={})
    assert (values['observation_allowed_actor_sha256'], values['observation_denied_actor_sha256']) == ('a' * 64, 'b' * 64)
    for actors in ({'allowed': 'a' * 64, 'denied': 'a' * 64}, {'allowed': 'A' * 64, 'denied': 'b' * 64}, {'allowed': 'a' * 64},
                   ['a' * 64, 'b' * 64]):
        with pytest.raises(run_binding.RunBindingUnavailable, match='CONFIG_INVALID'):
            run_binding.compute(PACK.checks['OBS-01'], target, env(), side='single', attempt_id=None,
                                issued_at=utcnow(), run_cfg={**good, 'test_actors': actors})


def issuer_event(tmp_path, check_id, start):
    stamp = lambda s: (start + timedelta(seconds=s)).strftime('%Y-%m-%dT%H:%M:%SZ')
    event = {'issuer_id': 'EXAMPLE-issuer', 'event_id': 'EXAMPLE-event-1', 'event_at': stamp(-10), 'deny_after': stamp(50)}
    if check_id == 'PLT-11':
        event['open_before'] = stamp(-20)
    return event, write(tmp_path, check_id + '-event.json', event)


@pytest.mark.parametrize('check_id', ['PLT-10', 'PLT-11'])
def test_issuer_event_comes_from_its_file_and_validity_is_the_run_window(tmp_path, check_id):
    from tests.test_run_binding import target_file
    issued = utcnow()
    event, path = issuer_event(tmp_path, check_id, issued)
    prefix = run_binding._EVENT_PREFIX[check_id]
    until = issued + timedelta(seconds=300)
    document, values = run_binding.compute(PACK.checks[check_id], {'target': str(target_file(tmp_path))}, env(),
                                           side='single', attempt_id=None, issued_at=issued, valid_until=until,
                                           run_cfg={'issuer_event': path})
    assert {k: values[f'{prefix}_{k}'] for k in event} == event
    start, end, basis = run_binding.window(PACK.checks[check_id].executor, issued, None, until)
    assert values[f'{prefix}_validity_start'] == document['validity']['start'] == run_binding._stamp(start)
    assert values[f'{prefix}_validity_end'] == document['validity']['end'] == run_binding._stamp(end)
    assert document['validity']['basis'] == basis == 'run_window_and_policy'
    assert document['files']['issuer_event_sha256'] == run_binding._binding_file(path, run_binding._EVENT_FIELDS[check_id])[0]
    for bad in ({**event, 'extra': 'x'}, {k: v for k, v in event.items() if k != 'event_id'}, {**event, 'issuer_id': 7}):
        with pytest.raises(run_binding.RunBindingUnavailable, match='CONFIG_INVALID'):
            run_binding.compute(PACK.checks[check_id], {'target': str(target_file(tmp_path))}, env(), side='single',
                                attempt_id=None, issued_at=issued, run_cfg={'issuer_event': write(tmp_path, 'bad.json', bad)})
    with pytest.raises(run_binding.RunBindingUnavailable, match='OUTSIDE_POLICY_VALIDITY'):
        run_binding.compute(PACK.checks[check_id], {'target': str(target_file(tmp_path))}, env(), side='single',
                            attempt_id=None, issued_at=issued, valid_from=issued + timedelta(seconds=5),
                            run_cfg={'issuer_event': path})


def test_sources_never_mix(tmp_path):
    producer = write(tmp_path, 'p.py', b'p')
    with pytest.raises(run_binding.RunBindingUnavailable, match='BELONGS_TO_EXECUTION'):
        run_binding.effective_values(PACK.checks['SUP-05'], supply_cfg(tmp_path), env(),
                                     {'supply_record_source_sha256': 'a' * 64}, side='single', attempt_id=None,
                                     issued_at=utcnow(), run_cfg={'record_producer': producer})
    with pytest.raises(run_binding.RunBindingUnavailable, match='BELONGS_TO_POLICY'):
        run_binding.effective_values(at36('SUP-05'), supply_cfg(tmp_path), env(),
                                     {'supply_record_source_sha256': 'a' * 64}, side='single', attempt_id=None,
                                     issued_at=utcnow(), run_cfg={'record_producer': producer})
    # @36 definition: the policy value is used and nothing is computed for stage 3.
    document, values = run_binding.effective_values(at36('SUP-05'), supply_cfg(tmp_path), env(),
                                                    {'supply_record_source_sha256': 'a' * 64}, side='single',
                                                    attempt_id=None, issued_at=utcnow())
    assert values['supply_record_source_sha256'] == 'a' * 64 and document['schema'] == run_binding.SCHEMA


def test_policy_migration_stage_3_and_run_config_candidates(tmp_path):
    from tools.migrate_policy_run_binding import main
    values = {'SUP-05': {'supply_record_source_sha256': 'a' * 64},
              'OBS-01': {'observation_expected_source_sha256': 'b' * 64, 'observation_allowed_actor_sha256': 'c' * 64,
                         'observation_denied_actor_sha256': 'd' * 64, 'observation_event_policy_sha256': 'e' * 64},
              'PLT-10': {'revocation_issuer_id': 'I', 'revocation_event_id': 'E', 'revocation_event_at': 'T1',
                         'revocation_deny_after': 'T2', 'revocation_validity_start': 'S', 'revocation_validity_end': 'U',
                         'revocation_deadline_basis': 'absolute-unconditional'},
              'REQ-06': {'stored_api_router_source_sha256': 'f' * 64, 'stored_retention': 'retain-owned-lab-objects'}}
    assert run_binding.migrate_policy_values(values, stage=3) == {
        'SUP-05': {}, 'OBS-01': {'observation_event_policy_sha256': 'e' * 64},
        'PLT-10': {'revocation_deadline_basis': 'absolute-unconditional'}, 'REQ-06': {'stored_retention': 'retain-owned-lab-objects'}}
    candidates = run_binding.run_config_candidates_3(values)
    assert candidates['OBS-01']['test_actors'] == {'allowed': 'c' * 64, 'denied': 'd' * 64}
    assert candidates['SUP-05'] == {'needs': ['record_producer: producer file whose bytes SHA-256 is ' + 'a' * 64]}
    assert candidates['PLT-10']['issuer_event_content'] == {'issuer_id': 'I', 'event_id': 'E', 'event_at': 'T1', 'deny_after': 'T2'}
    assert 'REQ-06' not in candidates
    source = tmp_path / 'policy.yaml'
    source.write_text(yaml.safe_dump({'data_origin': 'DECLARED', 'status': 'CONFIRMED',
                                      'pack': {'id': 'PACK-BASELINE', 'revision': 36}, 'values': values}))
    with pytest.raises(SystemExit):
        main([str(source), '--out', str(tmp_path / 'p.yaml'), '--stage', '3'])
    main([str(source), '--out', str(tmp_path / 'p.yaml'), '--stage', '3', '--run-config-out', str(tmp_path / 'rb.yaml')])
    out = yaml.safe_load((tmp_path / 'p.yaml').read_text())
    assert out['status'] == 'DRAFT' and out['pack']['revision'] == 38 and out['values']['SUP-05'] == {}
    assert yaml.safe_load((tmp_path / 'rb.yaml').read_text())['run_binding']['OBS-01']['test_actors']['denied'] == 'd' * 64


class Recording:
    name, version = 'credential_revocation', '0.0-EXAMPLE'

    def __init__(self, seen):
        self.seen = seen

    def run(self, check, env, values, adapter, workdir):
        self.seen.append(dict(values))
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False}, challenges=[], observations={}, config={'profile': 'EXAMPLE'})


def test_guard_run_gives_the_issuer_event_and_run_window_to_plt10(tmp_path):
    from tests.test_run_binding import target_file
    store = Store(tmp_path / 'store')
    selected = env()
    ref = put_pack(store, PACK.path)
    values = {'revocation_deadline_basis': 'absolute-unconditional', 'revocation_propagation_limit_ms': 1000,
              'revocation_max_clock_skew_ms': 10, 'revocation_subject_label': 'retired', 'revocation_max_new_access_samples': 3}
    pol = policy(ref, scope={'include': ['PLT-10']}, values={'PLT-10': values})
    store.put_many([selected, pol])
    plan = select(PACK, [selected], pol)
    store.put(plan)
    event, path = issuer_event(tmp_path, 'PLT-10', utcnow())
    seen = []
    registry.EXECUTORS['credential_revocation:sim'] = lambda cfg: Recording(seen)
    try:
        evd, = run_plan(store, PACK, plan, RunConfig(executors={'credential_revocation': 'sim'}, adapter='vllm', raw={
            'credential_revocation': {'target': str(target_file(tmp_path)), 'prepared': 'p.json'},
            'run_binding': {'PLT-10': {'issuer_event': path}}}))
    finally:
        registry.EXECUTORS.pop('credential_revocation:sim')
    assert evd.execution.status == ExecStatus.COMPLETED
    assert seen[0]['revocation_event_id'] == event['event_id'] and seen[0]['revocation_deadline_basis'] == 'absolute-unconditional'
    assert seen[0]['revocation_validity_start'] == evd.execution.run_binding['validity']['start']
    assert run_binding.verify(evd.execution.run_binding, evd) == [] and evd.hash_ok()
