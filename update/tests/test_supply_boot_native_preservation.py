"""Actual native17 file-read producer, EXAMPLE Kubernetes I/O, real factory.

This checks loss of facts, not producer authority or a real GPU/engine load.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.adapters import vllm_file_read_sensor as sensor
from guard.collectors.model_read_scope import deploy_scope
from guard.collectors.native_model_file_reads import analyze
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import kubernetes_supply_boot as boot
from guard.executors import supply_boot_contract as contract
from guard.models import Policy, canonical_json, new_id, sha256_hex, utcnow
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle, verify_complete
from guard.store.packs import get_pack, put_pack
from tests.builders import environment
from tests.test_a1_file_read_sensor import MODEL_BYTES, make_world, prepared_for, scope_value
from tests.test_a1_weight_iteration_sensor import packet
from tests.test_supply_boot_record_access import PACK, base_values, good_packet


def emitted_read(root, patch, session, mismatch):
    root.mkdir()
    path = root / 'public-example.safetensors'
    path.write_bytes(MODEL_BYTES)
    scope = scope_value(path)
    if mismatch:
        scope['files'][0]['reference_sha256'] = '0' * 64
    scope_path = root / 'scope.json'
    scope_path.write_bytes(deploy_scope(scope, canonical_json(scope), session))
    world = make_world(root, patch, session=session, path=path, scope_path=scope_path)
    world.run_loader()
    data = packet(world)
    data['recordings'][0]['capture']['records'][0]['payload']['profile'] = sensor.PROFILE
    data['recorded_files_covered'] = False  # No live Kubernetes census in this CPU contract.
    prepared = prepared_for(world)
    candidate, = analyze(data, prepared)['candidates']
    assert candidate['full_fd_read_return_observed']
    assert candidate['declared_reference_mismatch_observed'] is mismatch
    return data, prepared


def product(tmp_path, patch, *, mismatch=True, normal_record=False, report_error=None, native_missing=False):
    # Each role's real callback/file-read producer is separate; no time-based join.
    data, prepared = {}, {}
    for role, session in [('normal', '1' * 32), ('challenge', '2' * 32)]:
        with patch.context() as local:
            data[role], prepared[role] = emitted_read(tmp_path / role, local, session,
                                                      mismatch and role == 'challenge')
        (tmp_path / (role + '-native-producer.json')).write_bytes(canonical_json(data[role]))
    assert prepared['normal']['session'] != prepared['challenge']['session']
    env = environment(model={'id': 'test'})
    values = base_values(env, 'SUP-05')
    cfg, targets = {}, {}
    for role in ('normal', 'challenge'):
        target = {'lab': True, 'EXAMPLE_role': role}
        target_file = tmp_path / (role + '-target.json')
        prepared_file = tmp_path / (role + '-prepared.json')
        target_file.write_bytes(canonical_json(target))
        prepared_file.write_bytes(canonical_json(prepared[role]))
        cfg[role] = {'target': str(target_file), 'prepared': str(prepared_file)}
        targets[role] = sha256_hex(canonical_json(target))
        values['supply_' + role + '_target_sha256'] = targets[role]
        values['supply_' + role + '_prepared_sha256'] = sha256_hex(canonical_json(prepared[role]))

    reports = []

    class Reader:
        """Only the EXAMPLE Kubernetes transport boundary is replaced."""
        def __init__(self, target, env, prepared, **kwargs):
            self.role = target['EXAMPLE_role']
            self.binding = {'engine': {'container': 'engine', 'image': 'image'}}
            self.image_check = None
        def snapshot(self):
            return {}
        def read_file(self, *args, **kwargs):
            if self.role != 'normal' or not normal_record:
                raise FileNotFoundError('EXAMPLE absent boot record')
            raw = canonical_json(good_packet(env.ref().model_dump(mode='json'), targets['normal'], 'SUP-05'))
            return raw, {'capture_sha256': sha256_hex(raw), 'owner_binding_verified': True}
        def seal_session(self):
            return {'sealed': True}
        def owner_identity(self):
            return None
        def report(self):
            reports.append(self.role)
            if self.role == report_error:
                raise OSError('EXAMPLE_PRIVATE_SUPPLY_REPORT')
            return {'parent_cleanup_unverified': False}

    def captured(reader, *args, **kwargs):
        if native_missing:
            raise OSError('EXAMPLE native unavailable')
        return deepcopy(data[reader.role])

    patch.setattr(boot, 'SupplyBootReader', Reader)
    patch.setattr(boot, 'capture_session', captured)
    patch.setattr('guard.collectors.lifecycle.load_prepared',
                  lambda path: json.loads(Path(path).read_text(encoding='utf-8')))
    store = Store(tmp_path / 'store')
    put_pack(store, PACK.path)
    policy = Policy(id=new_id('POL'), data_origin='EXAMPLE', status='DRAFT', pack=PACK.ref(),
                    scope={'include': ['SUP-05']}, values={'SUP-05': values}).seal()
    store.put_many([env, policy])
    plan = select(PACK, [env], policy)
    store.put(plan)
    evd, = run_plan(store, PACK, plan, RunConfig(executors={'supply_boot': 'kubernetes'},
                           adapter='vllm', raw={'supply_boot': cfg}))
    asof = utcnow()
    result, = judge_plan(store, PACK, plan, asof)
    report = build_report(store, PACK, plan, asof)
    store.put(report)
    export_bundle(store.root, tmp_path / 'bundle.zip')
    restore_bundle(tmp_path / 'bundle.zip', tmp_path / 'restored')
    restored = Store(tmp_path / 'restored')
    verify_complete(restored)
    again, = judge_plan(restored, get_pack(restored, PACK.ref()), restored.resolve(plan.ref()), asof)
    assert again.ref() == result.ref()
    return evd, result, reports


def test_missing_boot_records_keep_actual_native_byte_conflict(tmp_path, monkeypatch):
    evd, result, reports = product(tmp_path, monkeypatch)
    assert result.verdicts.security.value == 'FAIL'
    assert evd.observations['supply_boot_violations'].collected
    assert evd.observations['supply_boot_native_consumption'].collected
    assert not evd.observations['supply_boot_complete'].value
    assert evd.execution.status.value == 'ABORTED' and reports == ['normal', 'challenge']


@pytest.mark.parametrize('role', ['normal', 'challenge'])
def test_report_error_keeps_native_conflict_and_other_role(tmp_path, monkeypatch, role):
    evd, result, reports = product(tmp_path, monkeypatch, normal_record=True, report_error=role)
    assert result.verdicts.security.value == 'FAIL'
    assert reports == ['normal', 'challenge']
    assert 'SUPPLY_BOOT_READER_REPORT_UNAVAILABLE' in evd.execution.abort_reason
    assert evd.execution.cleanup_errors and evd.execution.status.value == 'ABORTED'
    assert 'EXAMPLE_PRIVATE_SUPPLY_REPORT' not in canonical_json(evd.to_dict()).decode()


@pytest.mark.parametrize('native_missing', [False, True])
def test_missing_records_or_native_do_not_create_pass(tmp_path, monkeypatch, native_missing):
    evd, result, _ = product(tmp_path, monkeypatch, mismatch=False, native_missing=native_missing)
    assert result.verdicts.security.value == 'UNKNOWN'
    assert not evd.observations['supply_boot_complete'].value
    if native_missing:
        assert not evd.observations['supply_boot_violations'].collected
