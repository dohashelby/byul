"""EXAMPLE 원 기록을 실제 guard 제품 경로로 소비한다. 엔진/클라우드 실행은 없다."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors import rank_rpc_fault_producer as producer
from guard.executors import run_binding
from guard.executors.base import EvidenceDraft
from guard.executors.kubernetes_rank_failure import KubernetesRankFailureExecutor
from guard.executors.lifecycle_common_input_consumption import attach_inputs
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from tests.builders import environment, policy
from tests.test_rank_rpc_fault_producer import capture_example
from tests.test_rank_rpc_product_binding import example
from tools.mvp_versions import versions

ROOT = Path(__file__).resolve().parents[1]


def world(tmp_path, side='candidate', kind='delay', wire='guard-gpu-common-input/4'):
    row = versions()[side]
    env = environment(id='ENV-EXAMPLE-' + side, engine={'name': row['engine'], 'version': row['version'], 'image_digest': 'fixture-image'},
        model={'id': 'EXAMPLE-model', 'revision': 'EXAMPLE-revision'}, runtime={'tp_size': 2, 'gpu_count': 2})
    target = {'environment_ref': env.ref().model_dump(mode='json'), 'lab': True,
        'kubernetes': {'engine': {'pod_uid': 'EXAMPLE-pod', 'container_id': '4' * 64, 'restart_count': 0}}}
    parent = tmp_path / 'signed'; parent.mkdir()
    def bind(prepared, manifest):
        value = example(manifest['session'])
        value.update(environment_ref=env.ref().model_dump(mode='json'), target_sha256=sha256_hex(canonical_json(target)),
            prepared_sha256=sha256_hex((prepared / 'manifest.json').read_bytes()))
        return value
    prepared, capture, binding = capture_example(parent, kind, binding_factory=bind)
    pod = {k: binding[k] for k in ('pod_uid', 'container_id', 'restart_count')}
    record, _, _ = producer.record(prepared, capture, pod=pod,
        **{k: binding[k] for k in ('environment_ref', 'target_sha256', 'prepared_sha256', 'session')}, wire_schema=wire)
    path = tmp_path / 'common-input.json'; path.write_bytes(canonical_json(record))
    target_file = tmp_path / 'target.json'; target_file.write_bytes(canonical_json(target))
    cfg = {'input_mode': 'common-records', 'target': str(target_file), 'prepared': str(prepared / 'manifest.json'),
        'common_inputs': {'prepared': str(prepared / 'manifest.json'), 'session': binding['session'],
            'records': {'rank-fault': str(path)}, 'data_origin': 'EXAMPLE'}}
    values = {'rank_executor_backend': 'mp', 'rank_expected_ranks': ['0', '1'], 'rank_output_rank': 0}
    for fault in ('exception', 'death', 'delay', 'communication_stall'):
        for phase in ('detection', 'notification'):
            values['rank_' + fault + '_' + phase + '_ms'] = 20
    return env, target, cfg, values, record


def config_file(tmp_path, cfg):
    path = tmp_path / 'guard.toml'
    text = '[executors]\nrank_failure="common-input"\n[rank_failure]\n'
    text += ''.join(k + '=' + json.dumps(cfg[k]) + '\n' for k in ('input_mode', 'target', 'prepared'))
    text += '[rank_failure.common_inputs]\n' + ''.join(k + '=' + json.dumps(cfg['common_inputs'][k]) + '\n'
        for k in ('prepared', 'session', 'data_origin'))
    text += '[rank_failure.common_inputs.records]\nrank-fault=' + json.dumps(cfg['common_inputs']['records']['rank-fault']) + '\n'
    path.write_text(text); return path


@pytest.mark.parametrize('side', ['baseline', 'candidate'])
@pytest.mark.parametrize('wire', ['guard-gpu-common-input/1', 'guard-gpu-common-input/4'])
@pytest.mark.parametrize('kind', ['delay', 'exception', 'death'])
def test_common_record_reaches_exact_product_refs_and_restores_as_unknown(tmp_path, monkeypatch, side, wire, kind):
    env, target, cfg, values, record = world(tmp_path, side, kind, wire)
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    pol = policy(pack.ref(), scope={'include': ['LIF-08']}, values={'LIF-08': values})
    for name, value in [('environment', env.to_dict()), ('policy', pol.to_dict())]:
        (tmp_path / (name + '.json')).write_bytes(canonical_json(value))
    config = config_file(tmp_path, cfg); common = ['--store', str(tmp_path / 'store'), '--config', str(config)]
    def forbidden(*a, **kw):
        raise AssertionError('제품 파일 소비 중 네트워크/엔진 실행 금지')
    monkeypatch.setattr(socket, 'socket', forbidden); monkeypatch.setattr(subprocess, 'Popen', forbidden)
    runner = CliRunner()
    def cli(args, code=0):
        result = runner.invoke(app, [str(a) for a in args]); assert result.exit_code == code, (result.output, result.exception)
    cli(['select', '--pack', pack.path, '--env', tmp_path / 'environment.json', '--policy', tmp_path / 'policy.json', *common])
    plan, = Store.read_only(tmp_path / 'store').iter_kind('PLAN')
    assert next(r for r in plan.items if r.check_id == 'LIF-08').selected
    # 기존 CLI는 cleanup 오류가 있을 때만1을 반환한다. ABORTED/UNKNOWN은 아래 객체로 확인한다.
    cli(['run', '--plan', plan.id, '--pack', pack.path, *common])
    as_of = utcnow().isoformat()
    cli(['judge', '--plan', plan.id, '--as-of', as_of, *common])
    cli(['report', '--plan', plan.id, '--as-of', as_of, '--json', tmp_path / 'report.json', *common])
    cli(['verify', *common])
    store = Store.read_only(tmp_path / 'store'); evidence, = store.iter_kind('EVD'); result, = store.iter_kind('RES')
    assert evidence.data_origin.value == 'EXAMPLE' and evidence.execution.status.value == 'ABORTED'
    assert not evidence.normal_control.attempted and evidence.normal_control.succeeded is None
    assert all(getattr(result.verdicts, key).value == 'UNKNOWN' for key in ('security', 'functional', 'performance'))
    assert evidence.observations['activation_scope_complete'].value is False
    assert set(evidence.observations) == {o.key for o in pack.checks['LIF-08'].required_observations}
    assert not evidence.observations['activation_violations'].value
    report = evidence.observations['activation_steps'].value[-1]
    assert report['native_observations']['rank_record_input']['value']['engine_executed'] is False
    row, = report['records']
    assert row['record'] == record and row['record_sha256'] == sha256_hex(canonical_json(record))
    assert row['validation']['supported'] is row['validation']['complete'] is False
    assert 'B_COMMON_INPUT_CURRENT_OWNER_UNVERIFIED' in report['gaps']
    assert 'B_COMMON_INPUT_PREPARED_RUNTIME_UNVERIFIED' in report['gaps']
    assert not report['whole_question_complete']
    objects = [obj for key in ('ENV', 'POL', 'PLAN', 'EVD', 'RES', 'RPT') for obj in store.iter_kind(key)]
    assert len(objects) == 6
    cli(['evidence', 'export', '--store', tmp_path / 'store', '--output', tmp_path / 'bundle.zip'])
    cli(['evidence', 'restore', '--bundle', tmp_path / 'bundle.zip', '--destination', tmp_path / 'restored'])
    restored = Store.read_only(tmp_path / 'restored')
    assert all(restored.resolve(obj.ref()).to_dict() == obj.to_dict() for obj in objects)
    cli(['judge', '--plan', plan.id, '--as-of', as_of, '--store', tmp_path / 'restored', '--config', config])
    assert [r.ref() for r in Store.read_only(tmp_path / 'restored').iter_kind('RES')] == [result.ref()]


@pytest.mark.parametrize('wire', ['guard-gpu-common-input/1', 'guard-gpu-common-input/4'])
def test_update_compares_two_exact_partial_inputs_without_starting_an_engine(tmp_path, monkeypatch, wire):
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    parts = ['[executors]\nrank_failure="common-input"\n[update]\nmode="external"\n']
    environments = {}
    for side in ('baseline', 'candidate'):
        folder = tmp_path / side; folder.mkdir()
        env, _, cfg, values, _ = world(folder, side=side, wire=wire)
        environments[side] = folder / 'environment.json'; environments[side].write_bytes(canonical_json(env.to_dict()))
        raw = config_file(folder, cfg).read_text().split('[rank_failure]', 1)[1]
        parts.append('[sides.' + side + '.rank_failure]' + raw.replace('[rank_failure.', '[sides.' + side + '.rank_failure.'))
    pol = policy(pack.ref(), scope={'include': ['LIF-08']}, values={'LIF-08': values})
    (tmp_path / 'policy.json').write_bytes(canonical_json(pol.to_dict()))
    config = tmp_path / 'guard.toml'; config.write_text('\n'.join(parts))
    def forbidden(*a, **kw): raise AssertionError('파일 비교에서 네트워크/엔진 기동 금지')
    monkeypatch.setattr(socket, 'socket', forbidden); monkeypatch.setattr(subprocess, 'Popen', forbidden)
    result = CliRunner().invoke(app, ['update', '--pack', str(pack.path), '--baseline', str(environments['baseline']),
        '--candidate', str(environments['candidate']), '--policy', str(tmp_path / 'policy.json'),
        '--store', str(tmp_path / 'store'), '--config', str(config), '--output', str(tmp_path / 'output')])
    # update는 기존 계약대로 ABORTED 증거가 하나라도 있으면1을 반환한다.
    assert result.exit_code == 1, (result.output, result.exception)
    store = Store.read_only(tmp_path / 'store')
    evidence = list(store.iter_kind('EVD')); results = list(store.iter_kind('RES')); report, = store.iter_kind('RPT')
    assert len(evidence) == len(results) == 2 and not report.official
    assert {e.side.value for e in evidence} == {'baseline', 'candidate'}
    assert report.execution_attempt_id and {e.execution.attempt_id for e in evidence} == {report.execution_attempt_id}
    assert len({e.environment.id for e in evidence}) == 2
    for evd in evidence:
        assert evd.execution.status.value == 'ABORTED' and evd.data_origin.value == 'EXAMPLE'
        assert evd.execution.abort_reason == 'RANK_RECORD_INPUT_QUESTION_SCOPE_INCOMPLETE' and not evd.execution.cleanup_errors
        row, = evd.observations['activation_steps'].value[-1]['records']
        assert row['record']['environment_ref'] == store.resolve(evd.environment).ref().model_dump(mode='json')
    assert all(getattr(r.verdicts, axis).value == 'UNKNOWN' for r in results for axis in ('security', 'functional', 'performance'))


@pytest.mark.parametrize('damage', ['target', 'prepared', 'session', 'kind', 'origin', 'file', 'extra'])
def test_unbound_record_configuration_is_rejected_or_captured_as_gap_before_io(tmp_path, monkeypatch, damage):
    env, target, cfg, values, record = world(tmp_path)
    pack = load_pack(ROOT / 'packs/PACK-BASELINE'); check = pack.checks['LIF-08']
    _, effective = run_binding.effective_values(check, cfg, env, values,
        side='baseline', attempt_id='EXAMPLE', issued_at=utcnow())
    if damage == 'target': effective['rank_target_sha256'] = '0' * 64
    elif damage == 'prepared': effective['rank_prepared_sha256'] = '0' * 64
    elif damage == 'session': cfg['common_inputs']['session'] = '0' * 32
    elif damage == 'kind': cfg['common_inputs']['records'] = {'request-attempt': cfg['common_inputs']['records']['rank-fault']}
    elif damage == 'origin': cfg['common_inputs']['data_origin'] = 'MEASURED'
    elif damage == 'file': cfg['common_inputs']['records']['rank-fault'] = str(tmp_path / 'absent.json')
    else: cfg['kubectl_binary'] = 'forbidden'
    def forbidden(*a, **kw): raise AssertionError('잘못된 파일 입력에서 외부 IO 금지')
    monkeypatch.setattr(socket, 'socket', forbidden); monkeypatch.setattr(subprocess, 'Popen', forbidden)
    executor = KubernetesRankFailureExecutor(cfg, common_records=True)
    if damage in {'target', 'prepared', 'session', 'kind', 'extra'}:
        with pytest.raises(ValueError, match='RANK_RECORD_INPUT_CONFIGURATION_INVALID'):
            executor.run(check, env, effective, None, tmp_path / 'evd')
    else:
        draft = executor.run(check, env, effective, None, tmp_path / 'evd')
        assert not draft.observations['activation_scope_complete'].value and draft.data_origin.value == 'EXAMPLE'
        assert any('B_COMMON_INPUT_' in r for r in draft.observations['activation_missing'].value)


def test_existing_native_configuration_does_not_accept_record_mode(tmp_path):
    env, _, cfg, values, _ = world(tmp_path)
    pack = load_pack(ROOT / 'packs/PACK-BASELINE'); check = pack.checks['LIF-08']
    _, effective = run_binding.effective_values(check, cfg, env, values, side='baseline', attempt_id='EXAMPLE', issued_at=utcnow())
    with pytest.raises(ValueError, match='RANK_FAILURE_CONFIGURATION_INVALID'):
        KubernetesRankFailureExecutor(cfg).run(check, env, effective, object(), tmp_path / 'native')


def test_late_common_artifact_error_preserves_existing_independent_fail(tmp_path, monkeypatch):
    env, target, cfg, _, record = world(tmp_path)
    pack = load_pack(ROOT / 'packs/PACK-BASELINE'); violation = {'code': 'EXAMPLE_CONFIRMED_FAILURE'}
    draft = EvidenceDraft(status='ABORTED', data_origin='EXAMPLE', normal_control={'attempted': False, 'succeeded': None},
        observations={'activation_violations': {'value': [violation], 'collected': True}})
    original = Path.open
    def denied(path, *a, **kw):
        if path.name == 'b-common-inputs.json' and a and a[0] == 'xb': raise OSError('EXAMPLE')
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'open', denied)
    result = attach_inputs(draft, pack.checks['LIF-08'], env, target, cfg['common_inputs'], tmp_path / 'attach')
    assert result.observations['activation_violations'].value == [violation]
    assert not result.observations['activation_scope_complete'].value and result.cleanup_errors
