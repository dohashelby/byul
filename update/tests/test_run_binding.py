"""guard-run-binding/1: per-run technical binding computed by guard run (PACK-BASELINE@35, RB)."""
from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import pytest
import yaml

from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry, run_binding
from guard.executors.base import EvidenceDraft
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
from guard.models.objects import Execution
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import environment, policy

ROOT = Path(__file__).resolve().parents[1]
PACK = load_pack(ROOT / 'packs/PACK-BASELINE')


def target_file(tmp_path, value=None):
    path = tmp_path / 'target.json'
    path.write_text(json.dumps(value or {'schema': 'EXAMPLE-target', 'lab': True}))
    return path


def test_pack_35_moves_exactly_the_listed_keys_and_keeps_rules():
    assert PACK.manifest.revision == 35
    for cid, keys in run_binding.KEYS_BY_CHECK.items():
        check = PACK.checks[cid]
        assert check.collection_profile == run_binding.PROFILE_BY_CHECK[cid]
        assert run_binding.keys_for(check) == keys
        assert not {p.key for p in check.policy_params} & set(keys)
    # Validity stays in policy where the policy also declares event/deadline times inside it.
    for cid in ('PLT-10', 'PLT-11', 'LIF-11'):
        declared = {p.key for p in PACK.checks[cid].policy_params}
        assert sum(k.endswith(('validity_start', 'validity_end')) for k in declared) == 2


def test_definitions_that_still_declare_the_keys_or_use_another_profile_are_untouched():
    from guard.models.checkdef import PolicyParam
    old = PACK.checks['REQ-12'].model_copy(deep=True)
    old.policy_params = [PolicyParam(key=k, type='str', required=True) for k in run_binding.KEYS_BY_CHECK['REQ-12']]
    assert run_binding.keys_for(old) == ()  # @34 definition: policy values as before
    other = PACK.checks['REQ-18'].model_copy(deep=True)
    other.collection_profile = 'engine-sensor-slot-lifetime-v1-run-binding-v2'
    assert run_binding.keys_for(other) == ()  # sensor /2 packs keep their operator binding
    assert run_binding.keys_for(PACK.checks['REQ-01']) == ()


def test_compute_uses_env_ref_file_bytes_and_a_bounded_run_window(tmp_path):
    env = environment(model={'id': 'm', 'revision': 'r'})
    path = target_file(tmp_path)
    issued = utcnow()
    document, values = run_binding.compute(PACK.checks['REQ-12'], {'target': str(path)}, env,
                                           side='single', attempt_id='ATTEMPT-X', issued_at=issued)
    assert values['attempt_environment_sha256'] == env.ref().sha256
    assert values['attempt_target_sha256'] == sha256_hex(canonical_json(json.loads(path.read_text())))
    start, end = (run_binding.window('request_termination', issued)[i] for i in (0, 1))
    assert (end - start).total_seconds() == 600 and start <= utcnow()
    assert document == {'schema': run_binding.SCHEMA, 'check_id': 'REQ-12', 'side': 'single', 'attempt_id': 'ATTEMPT-X',
                        'environment_sha256': env.ref().sha256,
                        'files': {'attempt_target_sha256': values['attempt_target_sha256']},
                        'validity': {'start': values['attempt_validity_start'], 'end': values['attempt_validity_end'],
                                     'basis': 'run_window'},
                        'issued_at': document['issued_at']}
    # The executor's own policy contract accepts the computed values.
    from guard.executors.kubernetes_request_attempts import normalize_policy
    normalize_policy({k: values[k] for k in run_binding.KEYS_BY_CHECK['REQ-12']})


def test_window_follows_the_executor_limit_and_policy_validity():
    now = utcnow()
    assert (lambda s, e, b: (e - s).total_seconds())(*run_binding.window('supply_boot', now)) == 3600
    start, end, basis = run_binding.window('request_termination', now, valid_until=now + timedelta(seconds=90))
    assert basis == 'run_window_and_policy' and 0 < (end - start).total_seconds() <= 90
    with pytest.raises(run_binding.RunBindingUnavailable, match='OUTSIDE_POLICY_VALIDITY'):
        run_binding.window('request_termination', now, valid_from=now + timedelta(minutes=5))
    with pytest.raises(run_binding.RunBindingUnavailable, match='OUTSIDE_POLICY_VALIDITY'):
        run_binding.window('request_termination', now, valid_until=now - timedelta(seconds=1))


def test_policy_must_not_carry_run_binding_keys_and_missing_files_are_unavailable(tmp_path):
    env = environment(model={'id': 'm', 'revision': 'r'})
    check = PACK.checks['REQ-12']
    with pytest.raises(run_binding.RunBindingUnavailable, match='BELONGS_TO_EXECUTION'):
        run_binding.effective_values(check, {'target': str(target_file(tmp_path))}, env,
            {'attempt_target_sha256': 'a' * 64}, side='single', attempt_id=None, issued_at=utcnow())
    with pytest.raises(run_binding.RunBindingUnavailable, match='RUN_BINDING_UNAVAILABLE'):
        run_binding.effective_values(check, {'target': str(tmp_path / 'absent.json')}, env, {},
            side='single', attempt_id=None, issued_at=utcnow())
    with pytest.raises(run_binding.RunBindingUnavailable, match='FILE_UNAVAILABLE'):
        run_binding.effective_values(check, {'target': ''}, env, {}, side='single', attempt_id=None, issued_at=utcnow())


def test_empty_run_binding_is_not_serialized_so_past_evidence_hashes_hold():
    assert 'run_binding' not in Execution(status=ExecStatus.NOT_RUN).model_dump(mode='json')
    assert Execution(status=ExecStatus.NOT_RUN, run_binding={'schema': run_binding.SCHEMA}).model_dump(mode='json')['run_binding']


class Recording:
    name, version = 'request_termination', '0.0-EXAMPLE'

    def __init__(self, seen):
        self.seen = seen

    def run(self, check, env, values, adapter, workdir):
        self.seen.append(dict(values))
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False}, challenges=[], observations={}, config={'profile': 'EXAMPLE'})


def run_req12(tmp_path, values, cfg):
    store = Store(tmp_path / 'store')
    env = environment(model={'id': 'm', 'revision': 'r'})
    ref = put_pack(store, PACK.path)
    pol = policy(ref, scope={'include': ['REQ-12']}, values={'REQ-12': values})
    store.put_many([env, pol])
    plan = select(PACK, [env], pol)
    store.put(plan)
    seen = []
    registry.EXECUTORS['request_termination:sim'] = lambda raw: Recording(seen)
    try:
        evd, = run_plan(store, PACK, plan, RunConfig(executors={'request_termination': 'sim'}, adapter='vllm', raw=cfg))
    finally:
        registry.EXECUTORS.pop('request_termination:sim')
    return env, evd, seen


def test_guard_run_binds_without_policy_values_and_records_the_binding(tmp_path):
    path = target_file(tmp_path)
    env, evd, seen = run_req12(tmp_path, {}, {'request_termination': {'target': str(path)}})
    assert evd.execution.status == ExecStatus.COMPLETED
    values, = seen
    assert values['attempt_environment_sha256'] == env.ref().sha256 == evd.environment.sha256
    assert values['attempt_target_sha256'] == sha256_hex(canonical_json(json.loads(path.read_text())))
    document = evd.execution.run_binding
    assert document['files'] == {'attempt_target_sha256': values['attempt_target_sha256']}
    assert document['validity']['start'] == values['attempt_validity_start']
    assert run_binding.verify(document, evd) == [] and evd.hash_ok()
    # The binding is not part of the executor configuration hash.
    assert evd.execution.executor.config_sha256 == sha256_hex(canonical_json({'profile': 'EXAMPLE'}))


def test_unbindable_run_is_not_run_with_a_fixed_code_and_keeps_unknown_semantics(tmp_path):
    _, evd, seen = run_req12(tmp_path, {}, {'request_termination': {'target': str(tmp_path / 'absent.json')}})
    assert seen == [] and evd.execution.status == ExecStatus.NOT_RUN
    assert evd.execution.abort_reason == 'RUN_BINDING_UNAVAILABLE'
    assert not evd.normal_control.attempted


def test_tampered_binding_document_is_reported_on_replay(tmp_path):
    _, evd, _ = run_req12(tmp_path, {}, {'request_termination': {'target': str(target_file(tmp_path))}})
    forged = {**evd.execution.run_binding, 'environment_sha256': 'f' * 64, 'check_id': 'REQ-01'}
    assert set(run_binding.verify(forged, evd)) == {'RUN_BINDING_ENVIRONMENT_MISMATCH', 'RUN_BINDING_SUBJECT_MISMATCH'}


def test_file_changed_after_binding_still_fails_the_executor_comparison(tmp_path):
    from guard.collectors import engine_callbacks
    from guard.collectors.file import load_environment
    from guard.executors import engine_sensor
    from tests.test_gpu_a_run_inputs import build, check
    build(tmp_path, 'v1-callback')
    out = check(tmp_path, 'REQ-18', 'req18-slot', 'run')
    env = load_environment(tmp_path / 'env.json')
    cfg = {'target': str(out / 'target.json'), 'prepared': str(tmp_path / 'prepared/manifest.json'),
           'manifest': str(out / 'manifest.json'), 'max_duration_seconds': 300}
    values = yaml.safe_load((out / 'policy.yaml').read_text())['values']['REQ-18']
    _, values = run_binding.effective_values(PACK.checks['REQ-18'], cfg, env, values, side='single', attempt_id=None,
                                             issued_at=utcnow())
    manifest = json.loads((out / 'manifest.json').read_text())
    manifest['cases'] = manifest['cases'][:-1]
    (out / 'manifest.json').write_text(json.dumps(manifest))
    executor = engine_sensor.EngineSensorExecutor(cfg)
    executor.transport = None
    unit = PACK.checks['REQ-18'].model_copy(deep=True)
    with pytest.raises(ValueError, match='ENGINE_SENSOR_NATIVE_BINDING_MISMATCH'):
        executor.run(unit, env, values, None, tmp_path / 'work')
    assert engine_callbacks.load_prepared(cfg['prepared'])


def test_policy_migration_drops_only_run_binding_keys():
    values = {'REQ-12': {'attempt_target_sha256': 'a' * 64}, 'REQ-01': {'allowed_callers': ['x']},
              'PLT-10': {'revocation_target_sha256': 'b' * 64, 'revocation_validity_start': 'T'}}
    assert run_binding.migrate_policy_values(values) == {'REQ-12': {}, 'REQ-01': {'allowed_callers': ['x']},
                                                         'PLT-10': {'revocation_validity_start': 'T'}}


def test_pack_migration_is_a_minimal_text_change(tmp_path):
    seed = tmp_path / 'pack34'
    import shutil
    shutil.copytree(PACK.path, seed)
    # Rebuild an @34-shaped REQ-12 from the current file, then migrate it again.
    path = seed / 'checks/REQ-12.yaml'
    text = path.read_text(encoding='utf-8')
    entries = ''.join(f'- key: {k}\n  type: str\n  required: true\n' for k in run_binding.KEYS_BY_CHECK['REQ-12'])
    text = text.replace('policy_params: []\n', 'policy_params:\n' + entries)
    path.write_text(text, encoding='utf-8')
    manifest = (seed / 'pack.yaml').read_text(encoding='utf-8')
    import re
    from hashlib import sha256
    manifest = re.sub(r'(- path: checks/REQ-12.yaml\n +sha256: )[0-9a-f]{64}',
                      lambda m: m.group(1) + sha256(path.read_bytes()).hexdigest(), manifest)
    (seed / 'pack.yaml').write_text(manifest, encoding='utf-8')
    migrated = run_binding.migrate_pack(seed, tmp_path / 'pack36')
    assert migrated.manifest.revision == 36 and not migrated.reviewed
    assert migrated.checks['REQ-12'].revision == PACK.checks['REQ-12'].revision + 1
    assert (tmp_path / 'pack36/checks/REQ-12.yaml').read_text(encoding='utf-8').replace(
        f"revision: {PACK.checks['REQ-12'].revision + 1}\n", f"revision: {PACK.checks['REQ-12'].revision}\n", 1) == \
        (PACK.path / 'checks/REQ-12.yaml').read_text(encoding='utf-8')
    with pytest.raises(ValueError, match='ALREADY_APPLIED'):
        run_binding.migrate_pack(PACK.path, tmp_path / 'again')


def test_policy_migration_tool_keeps_enterprise_values_and_never_overwrites(tmp_path):
    from tools.migrate_policy_run_binding import main, migrate
    source = {'data_origin': 'DECLARED', 'status': 'CONFIRMED', 'confirmed_by': 'EXAMPLE-owner', 'confirmed_at': 'T',
              'pack': {'id': 'PACK-BASELINE', 'revision': 34},
              'values': {'REQ-12': {'attempt_environment_sha256': 'a' * 64, 'attempt_target_sha256': 'b' * 64},
                         'LIF-11': {'recovery_target_sha256': 'c' * 64, 'recovery_validity_start': 'S',
                                    'recovery_branch': 'runtime-failure'}}}
    out, removed = migrate(source, PACK)
    assert removed == {'REQ-12': ['attempt_environment_sha256', 'attempt_target_sha256'], 'LIF-11': ['recovery_target_sha256']}
    assert out['values'] == {'REQ-12': {}, 'LIF-11': {'recovery_validity_start': 'S', 'recovery_branch': 'runtime-failure'}}
    assert out['pack'] == {'id': 'PACK-BASELINE', 'revision': 35}
    assert out['status'] == 'DRAFT' and out['confirmed_by'] is None and source['status'] == 'CONFIRMED'
    path = tmp_path / 'policy.yaml'
    path.write_text(yaml.safe_dump(source))
    main([str(path), '--out', str(tmp_path / 'new.yaml')])
    assert yaml.safe_load((tmp_path / 'new.yaml').read_text()) == out
    with pytest.raises(SystemExit):
        main([str(path), '--out', str(tmp_path / 'new.yaml')])
    with pytest.raises(ValueError, match='SEALED_POLICY'):
        migrate({'kind': 'POL'}, PACK)
