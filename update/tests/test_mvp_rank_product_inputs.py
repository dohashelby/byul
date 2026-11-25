"""EXAMPLE 파일의 정확 결합만 검증한다. 실제 엔진/환경/정책 확정으로 올리지 않는다."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import tomllib

import pytest

from guard.models import canonical_json, load_pack, sha256_hex
from tests.builders import environment, policy
from tools import mvp_rank_product_inputs as tool
from tools.mvp_versions import versions


def inputs(tmp_path, side='candidate', wire='guard-gpu-common-input/1'):
    row = versions()[side]
    env = environment(engine={'name': row['engine'], 'version': row['version'], 'image_digest': row['image']},
        runtime={'gpu_count': 2, 'tp_size': 2})
    target = {'environment_ref': env.ref().model_dump(mode='json'), 'lab': True}
    prepared = {'session': 'd' * 32, 'image': row['image'], 'example': True}
    record = {'schema': wire, 'kind': 'rank-fault',
        'producer': {'name': 'EXAMPLE-unregistered', 'version': '1', 'source_sha256': 'e' * 64},
        'environment_ref': env.ref().model_dump(mode='json'), 'target_sha256': sha256_hex(canonical_json(target)),
        'prepared_sha256': sha256_hex(canonical_json(prepared)), 'session': prepared['session'],
        'bindings': [{'pod_uid': 'EXAMPLE-pod', 'container_id': 'EXAMPLE-container', 'restart_count': 0,
            'worker_pid': 101, 'start_ticks': 5, 'rank': 0}],
        'events': [{'seq': 0, 'event': 'fault_applied', 'binding': 0, 'at_ms': 1, 'ack': True,
            'fault_kind': 'delay', 'request_sha256': None}],
        'completeness': {'source_ended': False, 'dropped': 0, 'diagnostic_codes': ['EXAMPLE_PARTIAL']}}
    if wire == 'guard-gpu-common-input/4':
        record['events'][0].update(source_seq=0, repeat=None)
    pack = load_pack(tool.ROOT / 'packs/PACK-BASELINE')
    values = {'rank_executor_backend': 'mp', 'rank_expected_ranks': ['0', '1'], 'rank_output_rank': 0}
    for kind in ('exception', 'death', 'delay', 'communication_stall'):
        for phase in ('detection', 'notification'): values['rank_' + kind + '_' + phase + '_ms'] = 20
    pol = policy(pack.ref(), scope={'include': ['LIF-08']}, values={'LIF-08': values})
    args = {}
    for name, value in [('environment', env.to_dict()), ('target', target), ('prepared', prepared),
            ('common_input', record), ('policy', pol.to_dict())]:
        args[name] = tmp_path / (name + '.json'); args[name].write_bytes(canonical_json(value))
    return args


@pytest.mark.parametrize('side', ['baseline', 'candidate'])
@pytest.mark.parametrize('wire', ['guard-gpu-common-input/1', 'guard-gpu-common-input/4'])
def test_exact_input_bytes_scope_and_partial_state_survive_copy_without_network(tmp_path, monkeypatch, side, wire):
    args = inputs(tmp_path, side, wire)
    def forbidden(*a, **kw): raise AssertionError('파일 조립 중 엔진/네트워크 실행 금지')
    monkeypatch.setattr(socket, 'socket', forbidden); monkeypatch.setattr(subprocess, 'Popen', forbidden)
    result = tool.prepare(**args, output=tmp_path / 'result', side=side)
    assert result['file_binding_valid'] and result['data_origin'] == 'EXAMPLE'
    assert result['policy_status'] == 'DRAFT' and result['policy_file_changed'] is False
    assert not result['record_validation']['supported'] and not result['record_validation']['complete']
    assert not result['record_validation']['producer_authenticated']
    assert all(result[k] is False for k in ('engine_executed', 'live_target_verified', 'input_preflight_passed', 'ready_for_execution'))
    assert result['actual_results'] is None
    assert result['implementation_available'] is result['run_binding_preflight_valid']
    for role, name in [('environment', 'environment.json'), ('target', 'target.json'), ('prepared', 'prepared.json'),
            ('common_input', 'common-input.json'), ('policy', 'policy.yaml')]:
        assert (tmp_path / 'result' / name).read_bytes() == args[role].read_bytes()
        assert result['files'][name] == sha256_hex(args[role].read_bytes())
    config = tomllib.loads((tmp_path / 'result/guard.toml').read_text())
    assert config['executors']['rank_failure'] == 'common-input'
    assert config['rank_failure']['common_inputs']['records']['rank-fault'] == str(tmp_path / 'result/common-input.json')
    assert config['rank_failure']['common_inputs']['data_origin'] == 'EXAMPLE'


@pytest.mark.parametrize('damage', ['wrong-side', 'target', 'session', 'prepared', 'common-scope', 'policy-scope',
    'policy-missing', 'binding-in-policy', 'array', 'duplicate', 'symlink', 'missing'])
def test_invalid_or_missing_inputs_are_not_materialized(tmp_path, damage):
    args = inputs(tmp_path)
    side = 'baseline' if damage == 'wrong-side' else 'candidate'
    if damage == 'missing': args['environment'].unlink()
    elif damage == 'symlink':
        path = args['target']; dest = tmp_path / 'link'; dest.symlink_to(path); args['target'] = dest
    elif damage == 'duplicate': args['target'].write_text('{"lab":true,"lab":false}')
    else:
        role = 'target' if damage == 'target' else 'policy' if damage.startswith('policy') or damage == 'binding-in-policy' else 'common_input'
        obj = json.loads(args[role].read_bytes())
        if damage == 'target': obj['environment_ref']['sha256'] = '0' * 64
        elif damage == 'session': obj['session'] = '0' * 32
        elif damage == 'prepared': obj['prepared_sha256'] = '0' * 64
        elif damage == 'common-scope': obj['target_sha256'] = '0' * 64
        elif damage == 'policy-scope': obj['scope']['include'] = ['LIF-08', 'LIF-07']
        elif damage == 'policy-missing': del obj['values']['LIF-08']['rank_delay_detection_ms']
        elif damage == 'binding-in-policy': obj['values']['LIF-08']['rank_target_sha256'] = '0' * 64
        elif damage == 'array': obj = []
        if role == 'policy':
            from guard.models import Policy
            obj = Policy.model_validate(obj).seal().to_dict()
        args[role].write_bytes(canonical_json(obj))
    with pytest.raises((ValueError, OSError)):
        tool.prepare(**args, output=tmp_path / 'result', side=side)
    assert not (tmp_path / 'result').exists()


@pytest.mark.parametrize('damage,error', [
    (None, None), ('side', 'MA7_RANK_PAIRED_INPUT_INVALID'),
    ('owner', 'MA7_RANK_PAIRED_SIDE_SCOPE_MISMATCH'), ('foreign-owner', 'MA7_RANK_PAIRED_OWNER_SCOPE_MISMATCH'),
    ('rank', 'MA7_RANK_PAIRED_RANK_SCOPE_MISMATCH'), ('session', 'MA7_RANK_PAIRED_SIDE_SCOPE_MISMATCH'),
    ('bytes', 'MA7_RANK_INPUT_FILE_CHANGED'), ('short-write', 'MA7_RANK_PAIRED_SHORT_WRITE'),
])
def test_paired_saved_rank_inputs_reach_only_the_file_product(tmp_path, monkeypatch, damage, error):
    from typer.testing import CliRunner
    from guard.cli import app
    from guard.models import Environment
    from guard.store import Store

    def forbidden(*a, **kw):
        pytest.fail('Paired saved record processing cannot start an engine or open a connection')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    snapshots = {}
    for side in ('baseline', 'candidate'):
        folder = tmp_path / side
        folder.mkdir()
        args = inputs(folder, side=side)
        env = Environment.model_validate_json(args['environment'].read_bytes()).model_copy(update={'id': 'ENV-EXAMPLE-' + side}).seal()
        target, prepared, record = (json.loads(args[key].read_bytes()) for key in ('target', 'prepared', 'common_input'))
        suffix = 'a' if side == 'baseline' else 'b'
        owner_suffix = 'a' if damage == 'owner' else suffix
        owner = {'pod_uid': 'EXAMPLE-pod-' + owner_suffix, 'container_id': owner_suffix * 64, 'restart_count': 0}
        target.update(environment_ref=env.ref().model_dump(mode='json'), kubernetes={'engine': owner})
        prepared['session'] = ('a' if damage == 'session' else suffix) * 32
        record.update(environment_ref=target['environment_ref'], target_sha256=sha256_hex(canonical_json(target)),
                      prepared_sha256=sha256_hex(canonical_json(prepared)), session=prepared['session'], events=[])
        record['bindings'][0].update(owner)
        if side == 'candidate' and damage == 'rank':
            record['bindings'][0]['rank'] = 2
        if side == 'candidate' and damage == 'foreign-owner':
            record['bindings'][0]['container_id'] = 'c' * 64
        for key, value in (('environment', env.to_dict()), ('target', target), ('prepared', prepared), ('common_input', record)):
            args[key].write_bytes(canonical_json(value))
        output = folder / 'saved'
        tool.prepare(**args, output=output, side=side)
        manifest = output / 'manifest.json'
        snapshots[side] = {'path': str(manifest), 'sha256': sha256_hex(manifest.read_bytes())}

    if damage == 'side':
        snapshots['candidate'] = snapshots['baseline']
    elif damage == 'bytes':
        record_path = tmp_path / 'candidate/saved/common-input.json'
        record_path.write_bytes(record_path.read_bytes() + b'\n')
    selection = tmp_path / 'pair.json'
    selection.write_bytes(canonical_json({'schema': tool.PAIR_SELECTION_SCHEMA, 'sides': snapshots}))
    destination = tmp_path / 'pair'
    if damage == 'short-write':
        from contextlib import contextmanager
        from types import SimpleNamespace

        open_file = Path.open

        @contextmanager
        def open_output(path, mode='r', *args, **kwargs):
            with open_file(path, mode, *args, **kwargs) as stream:
                if path == destination / 'manifest.json' and mode == 'xb':
                    yield SimpleNamespace(write=lambda raw: stream.write(raw[:-1]))
                else:
                    yield stream

        monkeypatch.setattr(Path, 'open', open_output)
    if error:
        with pytest.raises(OSError if damage == 'short-write' else ValueError, match=error):
            tool.prepare_pair(selection, sha256_hex(selection.read_bytes()), destination)
        assert not destination.exists()
        assert all(sha256_hex(Path(ref['path']).read_bytes()) == ref['sha256'] for ref in snapshots.values())
        return

    result = tool.prepare_pair(selection, sha256_hex(selection.read_bytes()), destination)
    config = tomllib.loads((destination / 'guard.toml').read_text())
    assert config['executors'] == {'rank_failure': 'common-input'}
    assert config['update'] == {'mode': 'external', 'allowed_checks': ['LIF-08']}
    for side, manifest in snapshots.items():
        assert result['sides'][side]['source_manifest_sha256'] == manifest['sha256']
        for name in ('environment.json', 'prepared.json', 'target.json', 'common-input.json'):
            raw = (Path(manifest['path']).parent / name).read_bytes()
            assert (destination / f'{side}-{name}').read_bytes() == raw
            assert result['files'][f'{side}-{name}'] == sha256_hex(raw)

    pack = load_pack(tool.ROOT / 'packs/PACK-BASELINE')
    completed = CliRunner().invoke(app, ['update', '--pack', str(pack.path),
        '--baseline', result['sides']['baseline']['environment'], '--candidate', result['sides']['candidate']['environment'],
        '--policy', str(destination / 'policy.yaml'), '--store', str(tmp_path / 'store'),
        '--config', str(destination / 'guard.toml'), '--output', str(tmp_path / 'product')])
    assert completed.exit_code == 1, (completed.output, completed.exception)
    store = Store.read_only(tmp_path / 'store')
    evidence = list(store.iter_kind('EVD'))
    assert {item.side.value for item in evidence} == {'baseline', 'candidate'}
    for item in evidence:
        assert item.check_id == 'LIF-08' and item.execution.status.value == 'ABORTED' and item.data_origin.value == 'EXAMPLE'
        assert item.execution.abort_reason == 'RANK_RECORD_INPUT_QUESTION_SCOPE_INCOMPLETE'
        assert not item.normal_control.attempted and all(not step.attempted for step in item.challenges)
        assert 'RANK_RECORD_INPUT_FILE_ONLY' in item.observations['activation_missing'].value
        assert item.observations['activation_scope_complete'].value is False
        artifact, = item.artifacts
        assert artifact.name == 'b-common-inputs.json'
        raw = (tmp_path / 'store' / artifact.path).read_bytes()
        assert len(raw) == artifact.bytes and sha256_hex(raw) == artifact.sha256
        report = json.loads(raw)
        assert report['native_observations']['rank_record_input'] == {
            'value': {'input_mode': 'common-records', 'engine_executed': False}, 'collected': True}
        assert item.observations['activation_steps'].value[-1] == {'step': 'committed-A-common-inputs', **report}
        receipt, = report['records']
        name = f'{item.side.value}-common-input.json'
        assert receipt['file_sha256'] == result['files'][name]
        assert receipt['record'] == json.loads((destination / name).read_bytes())
    assert all(item.verdicts.security.value == 'UNKNOWN' for item in store.iter_kind('RES'))


@pytest.mark.parametrize(('filename', 'phase'), [
    ('prepared.json', 'before-run'),
    ('common-input.json', 'before-run'),
    ('prepared.json', 'at-binding'),
    ('target.json', 'at-consumption'),
    ('prepared.json', 'at-consumption'),
    ('common-input.json', 'at-consumption'),
])
def test_sealed_file_only_rank_plan_never_consumes_same_path_replacement(tmp_path, monkeypatch, filename, phase):
    from guard import application
    from guard.collectors.file import load_environment, load_policy
    from guard.core.execution_recipe import bind_recipe
    from guard.core.run import run_plan
    from guard.core.select import select
    from guard.executors import registry, run_binding
    from guard.models import DataOrigin, ExecStatus
    from guard.store import Store
    from guard.store.packs import put_pack

    def forbidden(*args, **kwargs):
        pytest.fail('File-only rank consumption must not make a runtime request')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    output = tmp_path / 'result'
    tool.prepare(**inputs(tmp_path), output=output, side='candidate')
    pack = load_pack(tool.ROOT / 'packs/PACK-BASELINE')
    env = load_environment(output / 'environment.json')
    pol = load_policy(output / 'policy.yaml', pack)
    cfg = tomllib.loads((output / 'guard.toml').read_text())
    store = Store(tmp_path / 'store')
    put_pack(store, pack.path)
    store.put_many([env, pol])
    plan = bind_recipe(select(pack, [env], pol), cfg, [env])
    store.put(plan)
    source = output / filename
    original = source.read_bytes()
    expected_targets = []

    def rewrite_bytes():
        # The JSON meaning is identical. Only the exact saved bytes change.
        source.write_bytes(original + b'\n')

    if phase == 'before-run':
        rewrite_bytes()
        with pytest.raises(ValueError, match='^EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED$'):
            run_plan(store, pack, plan, application.run_config(cfg))
        assert not list(store.iter_kind('EVD'))
        return
    if phase == 'at-binding':
        bind = run_binding.effective_values

        def swap_before_binding(*args, **kwargs):
            assert kwargs['expected_rank_prepared_file']['sha256'] == sha256_hex(original)
            rewrite_bytes()
            return bind(*args, **kwargs)

        monkeypatch.setattr(run_binding, 'effective_values', swap_before_binding)
    else:
        build = registry.build_executor

        def swap_before_consumption(executor, impl, raw, **kwargs):
            assert executor == 'rank_failure' and impl == 'common-input'
            if filename == 'target.json':
                expected_targets.append(raw['_rank_common_files'].get('target'))
            else:
                key = 'prepared' if filename == 'prepared.json' else 'record'
                assert raw['_rank_common_files'][key]['sha256'] == sha256_hex(original)
            rewrite_bytes()
            return build(executor, impl, raw, **kwargs)

        monkeypatch.setattr(registry, 'build_executor', swap_before_consumption)

    evd, = run_plan(store, pack, plan, application.run_config(cfg))
    assert evd.data_origin == DataOrigin.EXAMPLE and not evd.normal_control.attempted
    assert store.resolve(evd.ref()).to_dict() == evd.to_dict()
    if phase == 'at-binding':
        assert evd.execution.status == ExecStatus.NOT_RUN
        assert evd.execution.abort_reason == 'RUN_BINDING_RANK_PREPARED_CHANGED'
        assert not evd.artifacts
    elif filename in {'target.json', 'prepared.json'}:
        assert evd.execution.status == ExecStatus.ABORTED
        assert not evd.observations['activation_steps'].collected and not evd.artifacts
        if filename == 'target.json':
            assert expected_targets == [{'path': str(source), 'sha256': sha256_hex(original)}]
    else:
        assert evd.execution.status == ExecStatus.ABORTED
        report = evd.observations['activation_steps'].value[-1]
        assert report['records'] == [] and not report['producer_authenticated']
        assert 'B_COMMON_INPUT_REJECTED:rank-fault:B_COMMON_INPUT_FILE_CHANGED' in report['gaps']
