"""EXAMPLE 파일의 정확 결합만 검증한다. 실제 엔진/환경/정책 확정으로 올리지 않는다."""
from copy import deepcopy
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
    import json
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
