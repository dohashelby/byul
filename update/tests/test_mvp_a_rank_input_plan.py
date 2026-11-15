"""저장된 준비물만 계획에 추가하고 기존 입력/결과와 미완료 경계를 보존한다."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess

import pytest

from guard.models import canonical_json
from tools import mvp_a_rank_input_plan as tool, rank_ipc_candidates as ipc
from tools.mvp_versions import versions


@pytest.fixture
def inputs(tmp_path):
    rows = []; reviewed = None
    for side, profile in versions().items():
        for kind in ('delay', 'exception', 'death'):
            path = tmp_path / (side + '-' + kind)
            manifest = ipc.prepare(path, image=profile['image']); reviewed = manifest['source_manifest_sha256']
            row = {'side': side, 'fault_kind': kind, 'session': manifest['session'],
                'prepared_directory': str(path), 'assets': manifest['assets'],
                'prepared_manifest_sha256': sha256((path / 'manifest.json').read_bytes()).hexdigest(),
                'environment_ref': None, 'target_sha256': None, 'product_binding_file': None, 'actual_results': None}
            row.update({k: False for k in tool.FALSE_FIELDS}); rows.append(row)
    document = {'schema': 'guard-ma3-product-binding-preparation/1', 'data_origin': 'EXAMPLE',
        'reviewed_source_manifest_sha256': reviewed, 'prepared_candidates': rows, 'actual_results': None,
        **{k: False for k in tool.FALSE_FIELDS}}
    source = tmp_path / 'rank-preparation.json'; source.write_bytes(canonical_json(document))
    return source, document


def test_six_file_verified_candidates_join_j01_without_replacing_existing_rows(inputs, tmp_path, monkeypatch):
    source, document = inputs
    def forbidden(*a, **kw):
        raise AssertionError('계획 연결은 네트워크/엔진을 실행하지 않는다')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    original = json.loads(tool.PLAN.read_bytes())
    link, plan = tool.prepare(source, tmp_path / 'result')
    assert plan['checks'] == original['checks'] and plan['input_materialization'] == original['input_materialization']
    assert len(link['candidates']) == 6 and len({r['session'] for r in link['candidates']}) == 6
    assert link['data_origin'] == 'EXAMPLE' and link['check_id'] == 'LIF-08' and link['work_item'] == 'M-A3'
    assert link['source_plan_sha256'] == sha256(tool.PLAN.read_bytes()).hexdigest()
    assert link['rank_preparation_sha256'] == sha256(source.read_bytes()).hexdigest()
    assert link['actual_results'] is None and not link['ready_for_execution'] and not link['input_preflight_passed']
    assert not link['engine_executed'] and not link['live_target_verified']
    for row in link['candidates']:
        assert row['prepared_source_bytes_verified'] and not row['prepared_runtime_qualified']
        assert row['environment_ref'] is row['target'] is row['product_binding_file'] is row['common_input_file'] is None
    for row in document['prepared_candidates']:
        settings = json.loads((Path(row['prepared_directory']) / 'settings.json').read_bytes())
        assert settings['key'] not in json.dumps(plan)
    assert plan == json.loads((tmp_path / 'result/session-input-plan.json').read_bytes())
    with pytest.raises(FileExistsError): tool.prepare(source, tmp_path / 'result')


@pytest.mark.parametrize('damage', ['manifest', 'settings', 'asset', 'symlink', 'observed',
    'environment', 'result', 'qualified', 'duplicate_session', 'image', 'bound_plan', 'complete_plan', 'linked_plan'])
def test_wrong_or_observed_candidates_never_become_pending_execution(inputs, tmp_path, damage):
    source, document = inputs; row = document['prepared_candidates'][0]; root = Path(row['prepared_directory'])
    plan_file = tool.PLAN
    if damage == 'manifest': (root / 'manifest.json').write_bytes(b'{}')
    elif damage == 'settings': (root / 'settings.json').write_bytes(b'{}')
    elif damage == 'asset': (root / 'guard/adapters/vllm_rank_rpc_control.py').write_bytes('# 다른 코드\n'.encode())
    elif damage == 'symlink':
        path = root / 'manifest.json'; raw = path.read_bytes(); path.unlink()
        other = tmp_path / 'manifest-original.json'; other.write_bytes(raw); path.symlink_to(other)
    elif damage == 'observed': (root / 'data' / row['session'] / 'control-arm.json').write_bytes(b'{}')
    elif damage == 'environment': row['environment_ref'] = {'EXAMPLE': 'bound'}
    elif damage == 'result': document['actual_results'] = {'EXAMPLE': 'existing'}
    elif damage == 'qualified': row['scope_complete'] = True
    elif damage == 'duplicate_session': document['prepared_candidates'][1]['session'] = row['session']
    elif damage == 'image':
        manifest = json.loads((root / 'manifest.json').read_bytes()); manifest['image'] = 'other-image'
        (root / 'manifest.json').write_bytes(canonical_json(manifest))
        row['prepared_manifest_sha256'] = sha256((root / 'manifest.json').read_bytes()).hexdigest()
    else:
        plan = json.loads(tool.PLAN.read_bytes())
        if damage == 'bound_plan':
            next(r for r in plan['checks'] if r['check_id'] == 'LIF-08')['sides']['baseline']['target'] = 'existing-target'
        elif damage == 'complete_plan': plan['ready_for_execution'] = True
        else: plan['rank_fault_input_preparation'] = {'EXAMPLE': 'existing'}
        plan_file = tmp_path / 'plan.json'; plan_file.write_bytes(canonical_json(plan))
    source.write_bytes(canonical_json(document)); output = tmp_path / 'rejected'
    with pytest.raises((ValueError, OSError)):
        tool.prepare(source, output, plan_file=plan_file)
    assert not output.exists()
