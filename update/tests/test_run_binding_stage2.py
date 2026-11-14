"""guard-run-binding/2: run-preparation values moved out of policy (PACK-BASELINE@36, RB2-Q1~Q7)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
import yaml

from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry, run_binding
from guard.executors.base import EvidenceDraft
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
from guard.models.checkdef import PolicyParam
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import environment, policy

ROOT = Path(__file__).resolve().parents[1]
PACK = load_pack(ROOT / 'packs/PACK-BASELINE')
SESSION = '0123456789abcdef0123456789abcdef'
EGRESS = {'subject_container_id': 'a' * 64, 'subject_uid': 1000, 'subject_net_namespace_inode': 4026531840,
          'forbidden_receiver_container_id': 'b' * 64, 'forbidden_receiver_ip': '10.0.0.7', 'forbidden_receiver_port': 9000}
API = {'api_container_id': 'c' * 64, 'api_network_id': 'd' * 64, 'api_endpoint_id': 'e' * 64,
       'api_ip': '10.0.0.8', 'api_port': 8000}


def env():
    return environment(model={'id': 'm', 'revision': 'r'})


def declaring(check, keys):
    """The same definition as an older pack that still declares `keys` in policy."""
    old = check.model_copy(deep=True)
    old.policy_params = list(old.policy_params) + [PolicyParam(key=k, type='str', required=True) for k in keys]
    return old


def write(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(json.dumps(value))
    return str(path)


def test_stages_are_decided_separately_so_pack_35_definitions_replay_unchanged():
    check = PACK.checks['OBS-01']
    assert run_binding.stages_for(check) == (1, 2)
    at35 = declaring(check, run_binding.KEYS_BY_CHECK_2['OBS-01'])
    assert run_binding.stages_for(at35) == (1,)
    assert run_binding.keys_for(at35) == run_binding.KEYS_BY_CHECK['OBS-01']  # B1 stays computed
    at34 = declaring(at35, run_binding.KEYS_BY_CHECK['OBS-01'])
    assert run_binding.keys_for(at34) == ()
    # REQ-02's dropped declared-file hash belongs to stage 2 as well.
    req02 = declaring(PACK.checks['REQ-02'], ('route_file_sha256',))
    assert run_binding.stages_for(req02) == (1,)
    # A check with only stage-2 keys.
    assert run_binding.stages_for(PACK.checks['LIF-05']) == (2,)
    assert run_binding.stages_for(declaring(PACK.checks['LIF-05'], run_binding.KEYS_BY_CHECK_2['LIF-05'])) == ()


def test_record_session_comes_from_the_check_table_and_is_recorded_as_v2(tmp_path):
    target = write(tmp_path, 'target.json', {'schema': 'EXAMPLE-target', 'lab': True})
    document, values = run_binding.compute(PACK.checks['OBS-01'], {'target': target}, env(), side='baseline',
                                           attempt_id='A', issued_at=utcnow(), run_cfg={'record_session': SESSION})
    assert values['observation_session'] == SESSION
    assert values['observation_target_sha256'] == document['files']['observation_target_sha256']
    assert document['schema'] == run_binding.SCHEMA_2 and document['values'] == {'observation_session': SESSION}
    for bad in (None, {}, {'record_session': 'X' * 32}, {'record_session': SESSION, 'scenario': 'x'}):
        with pytest.raises(run_binding.RunBindingUnavailable, match='CONFIG_INVALID'):
            run_binding.compute(PACK.checks['OBS-01'], {'target': target}, env(), side='single', attempt_id=None,
                                issued_at=utcnow(), run_cfg=bad)


def test_sources_never_mix(tmp_path):
    target = write(tmp_path, 'target.json', {'schema': 'EXAMPLE-target', 'lab': True})
    at35 = declaring(PACK.checks['OBS-01'], run_binding.KEYS_BY_CHECK_2['OBS-01'])
    with pytest.raises(run_binding.RunBindingUnavailable, match='BELONGS_TO_POLICY'):
        run_binding.effective_values(at35, {'target': target}, env(), {'observation_session': SESSION}, side='single',
                                     attempt_id=None, issued_at=utcnow(), run_cfg={'record_session': SESSION})
    for cid, values in (('OBS-01', {'observation_session': SESSION}), ('REQ-02', {'route_file_sha256': 'f' * 64})):
        with pytest.raises(run_binding.RunBindingUnavailable, match='BELONGS_TO_EXECUTION'):
            run_binding.effective_values(PACK.checks[cid], {'target': target}, env(), values, side='single',
                                         attempt_id=None, issued_at=utcnow(), run_cfg={'record_session': SESSION})
    with pytest.raises(run_binding.RunBindingUnavailable, match='CONFIG_INVALID'):
        run_binding.run_config({'run_binding': ['OBS-01']}, 'OBS-01')
    assert run_binding.run_config({}, 'OBS-01') is None


def test_scenario_hash_is_the_canonical_json_of_the_configured_file(tmp_path):
    scenario = {'scenario_order': ['normal-before', 'exception'], 'scope_limitations': ['EXAMPLE']}
    files, values = run_binding._stage_2(PACK.checks['LIF-07'], {}, {'scenario': write(tmp_path, 's.json', scenario)},
                                         utcnow())
    assert values == files == {'init_scenario_sha256': sha256_hex(canonical_json(scenario))}
    with pytest.raises(run_binding.RunBindingUnavailable, match='FILE_UNAVAILABLE'):
        run_binding._stage_2(PACK.checks['LIF-11'], {}, {'scenario': ''}, utcnow())


def _transition_inputs(tmp_path, monkeypatch):
    from guard.executors import lifecycle_transition
    from guard.collectors import lifecycle
    selected = env()
    prepared = {'schema': 'EXAMPLE-prepared', 'session': SESSION}
    monkeypatch.setattr(lifecycle_transition, 'load_environment', lambda path: selected)
    monkeypatch.setattr(lifecycle, 'load_prepared', lambda path: prepared)
    rows = [{'id': key, 'target': write(tmp_path, key + '.json', {'lab': True, 'id': key}),
             'prepared': 'P', 'environment': 'E'} for key in ('old', 'new')]
    manifest = [{'id': row['id'], 'environment_ref': selected.ref().model_dump(mode='json'),
                 'target_sha256': sha256_hex(canonical_json({'lab': True, 'id': row['id']})),
                 'prepared_sha256': sha256_hex(canonical_json(prepared))} for row in rows]
    return {'generations': rows}, manifest


def test_transition_values_follow_the_executor_contract(tmp_path, monkeypatch):
    from guard.executors.lifecycle_transition import inputs_manifest, normalize_policy
    executor_cfg, manifest = _transition_inputs(tmp_path, monkeypatch)
    assert inputs_manifest(executor_cfg) == manifest
    issued = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    run_cfg = {'phase_generation': {'before': 'old', 'during': 'old', 'after': 'new', 'recovered': 'new'},
               'sample_offsets_ms': {'before': 5000, 'during': 60000, 'after': 120000, 'recovered': 200000}}
    files, values = run_binding._stage_2(PACK.checks['LIF-16'], executor_cfg, run_cfg, issued)
    assert files == {}
    assert values['transition_inputs_sha256'] == sha256_hex(canonical_json(manifest))
    assert values['transition_before_at'] == '2026-11-14T12:00:05.000Z'
    assert values['transition_recovered_generation'] == 'new'
    schedule = normalize_policy(values)  # the executor's own order/240-second checks
    assert schedule['before'] == issued + timedelta(seconds=5)
    # The executor still rejects a non-increasing schedule; guard does not repair it.
    flat = run_binding._stage_2(PACK.checks['LIF-05'], executor_cfg,
                                {**run_cfg, 'sample_offsets_ms': {**run_cfg['sample_offsets_ms'], 'after': 60000}}, issued)[1]
    with pytest.raises(ValueError, match='TRANSITION_POLICY_INVALID'):
        normalize_policy(flat)
    for bad in ({**run_cfg, 'sample_offsets_ms': {**run_cfg['sample_offsets_ms'], 'recovered': 240001}},
                {**run_cfg, 'phase_generation': {**run_cfg['phase_generation'], 'during': 'Bad'}},
                {'phase_generation': run_cfg['phase_generation']}):
        with pytest.raises(run_binding.RunBindingUnavailable, match='CONFIG_INVALID'):
            run_binding._stage_2(PACK.checks['LIF-06'], executor_cfg, bad, issued)


def test_plt07_plt08_identities_come_from_a_declared_file_not_from_the_run(tmp_path):
    from guard.executors import api_transport, boundary_egress
    target = write(tmp_path, 't.json', {'lab': True})
    for cid, name, content, normalize, policy_values in (
            ('PLT-07', 'egress_binding', EGRESS, boundary_egress.normalize_policy,
             {'forbidden_protocol': 'tcp', 'forbidden_operation': 'tcp_connect'}),
            ('PLT-08', 'api_binding', API, api_transport.normalize_policy, {'plaintext_forbidden': True})):
        path = write(tmp_path, name + '.json', content)
        document, values = run_binding.effective_values(PACK.checks[cid], {'target': target}, env(), policy_values,
                                                        side='single', attempt_id=None, issued_at=utcnow(),
                                                        run_cfg={name: path})
        assert document['files'] == {name + '_sha256': sha256_hex(canonical_json(content))}
        assert {k: values[k] for k in content} == content
        normalize(values)  # the executor contract accepts the values unchanged
        with pytest.raises(run_binding.RunBindingUnavailable, match='CONFIG_INVALID'):
            run_binding.effective_values(PACK.checks[cid], {}, env(), policy_values, side='single', attempt_id=None,
                                         issued_at=utcnow(), run_cfg={name: write(tmp_path, 'x.json', {**content, 'extra': 1})})


def test_req02_records_the_read_file_hash_when_the_definition_no_longer_declares_it():
    from guard.executors.kubernetes_route_record import KubernetesRouteRecordExecutor, composite_contract, file_declared
    check = PACK.checks['REQ-02']
    assert not file_declared(check) and file_declared(declaring(check, ('route_file_sha256',)))
    now = utcnow()
    values = {k: ('a' * 64 if k.endswith('sha256') else SESSION if k.endswith('session') else
                  now.isoformat() if k.endswith('start') else (now + timedelta(minutes=5)).isoformat())
              for k in composite_contract()[0] if k != 'route_file_sha256'}
    executor = KubernetesRouteRecordExecutor({})
    from guard.executors import native_route_contract as native
    try:
        executor.validate_policy(check, values)
    except ValueError as exc:  # native part has its own value rules; the record part must not object
        assert 'KUBERNETES_ROUTE_POLICY_INVALID' not in str(exc)
    with pytest.raises(ValueError, match='KUBERNETES_ROUTE_POLICY_INVALID'):
        executor.validate_policy(declaring(check, ('route_file_sha256',)), values)
    with pytest.raises(ValueError, match='KUBERNETES_ROUTE_POLICY_INVALID'):
        executor.validate_policy(check, {**values, 'route_file_sha256': 'a' * 64})
    assert native.POLICY_TYPES


def test_verify_accepts_both_schemas_and_rejects_a_v2_without_values():
    class Evidence:
        environment = type('R', (), {'sha256': 'e' * 64})
        check_id, side = 'OBS-11', type('S', (), {'value': 'single'})
        execution = type('X', (), {'attempt_id': None})
    base = {'environment_sha256': 'e' * 64, 'check_id': 'OBS-11', 'side': 'single', 'attempt_id': None}
    assert run_binding.verify({**base, 'schema': run_binding.SCHEMA}, Evidence) == []
    assert run_binding.verify({**base, 'schema': run_binding.SCHEMA_2, 'values': {}}, Evidence) == []
    assert run_binding.verify({**base, 'schema': run_binding.SCHEMA_2}, Evidence) == ['RUN_BINDING_SCHEMA_INVALID']


def test_policy_migration_stage_2_and_run_config_candidates(tmp_path):
    from tools.migrate_policy_run_binding import main
    values = {'OBS-01': {'observation_session': SESSION, 'observation_max_delay_ms': 5000},
              'REQ-02': {'route_session': SESSION, 'route_file_sha256': 'f' * 64},
              'LIF-12': {'retry_session': SESSION, 'retry_scope_sha256': 'a' * 64},
              'LIF-05': {'transition_inputs_sha256': 'b' * 64,
                         **{f'transition_{p}_at': f'2026-11-14T12:0{i}:00Z' for i, p in enumerate(run_binding._PHASES)},
                         **{f'transition_{p}_generation': 'g' for p in run_binding._PHASES}},
              'PLT-08': {**API, 'api_hop_scope': 'guard_probe_to_owned_direct_api', 'plaintext_forbidden': True}}
    assert run_binding.migrate_policy_values(values, stage=2) == {
        'OBS-01': {'observation_max_delay_ms': 5000}, 'REQ-02': {}, 'LIF-12': {'retry_scope_sha256': 'a' * 64},
        'LIF-05': {}, 'PLT-08': {'plaintext_forbidden': True}}  # E keys (retry_scope) stay
    candidates = run_binding.run_config_candidates(values)
    assert candidates['OBS-01'] == {'record_session': SESSION} and candidates['REQ-02'] == {'record_session': SESSION}
    assert candidates['LIF-05']['sample_offsets_ms'] == {'before': 0, 'during': 60000, 'after': 120000, 'recovered': 180000}
    assert candidates['PLT-08']['api_binding_content'] == API
    source = tmp_path / 'policy.yaml'
    source.write_text(yaml.safe_dump({'data_origin': 'DECLARED', 'status': 'CONFIRMED', 'pack': {'id': 'PACK-BASELINE',
                                      'revision': 35}, 'values': values}))
    with pytest.raises(SystemExit):
        main([str(source), '--out', str(tmp_path / 'p.yaml'), '--stage', '2'])
    main([str(source), '--out', str(tmp_path / 'p.yaml'), '--stage', '2', '--run-config-out', str(tmp_path / 'rb.yaml')])
    out = yaml.safe_load((tmp_path / 'p.yaml').read_text())
    assert out['status'] == 'DRAFT' and out['pack']['revision'] == 36
    assert out['values']['LIF-12'] == {'retry_scope_sha256': 'a' * 64} and out['values']['REQ-02'] == {}
    written = yaml.safe_load((tmp_path / 'rb.yaml').read_text())
    assert written['status'] == 'DRAFT' and written['run_binding']['OBS-01'] == {'record_session': SESSION}


class Recording:
    name, version = 'log_integrity', '0.0-EXAMPLE'

    def __init__(self, seen):
        self.seen = seen

    def run(self, check, env, values, adapter, workdir):
        self.seen.append(dict(values))
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False}, challenges=[], observations={}, config={'profile': 'EXAMPLE'})


def run_obs11(tmp_path, raw):
    store = Store(tmp_path / 'store')
    selected = env()
    ref = put_pack(store, PACK.path)
    values = {'required_records': ['EXAMPLE'], 'collection_deadline_ms': {'EXAMPLE': 1000},
              'max_clock_uncertainty_ms': {'EXAMPLE': 10}, 'detection_deadline_ms': {'EXAMPLE': 1000},
              'alert_deadline_ms': {'EXAMPLE': 1000}}
    pol = policy(ref, scope={'include': ['OBS-11']}, values={'OBS-11': values})
    store.put_many([selected, pol])
    plan = select(PACK, [selected], pol)
    store.put(plan)
    seen = []
    registry.EXECUTORS['log_integrity:sim'] = lambda cfg: Recording(seen)
    try:
        evd, = run_plan(store, PACK, plan, RunConfig(executors={'log_integrity': 'sim'}, adapter='vllm', raw=raw))
    finally:
        registry.EXECUTORS.pop('log_integrity:sim')
    return evd, seen


def test_guard_run_reads_the_check_table_and_seals_a_v2_binding(tmp_path):
    evd, seen = run_obs11(tmp_path, {'log_integrity': {}, 'run_binding': {'OBS-11': {'record_session': SESSION}}})
    assert evd.execution.status == ExecStatus.COMPLETED and seen[0]['collection_session'] == SESSION
    assert evd.execution.run_binding['schema'] == run_binding.SCHEMA_2
    assert evd.execution.run_binding['values'] == {'collection_session': SESSION}
    assert run_binding.verify(evd.execution.run_binding, evd) == [] and evd.hash_ok()
    # The session is not part of the executor configuration hash.
    assert evd.execution.executor.config_sha256 == sha256_hex(canonical_json({'profile': 'EXAMPLE'}))


def test_missing_check_table_is_not_run_and_stays_unknown(tmp_path):
    evd, seen = run_obs11(tmp_path, {'log_integrity': {}})
    assert seen == [] and evd.execution.status == ExecStatus.NOT_RUN
    assert evd.execution.abort_reason == 'RUN_BINDING_CONFIG_INVALID'
