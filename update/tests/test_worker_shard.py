"""SUP-06 worker-shard-load 공통 입력(개발 담당, B 의존 반환 5).

행은 k3d vLLM 0.31.0 CPU 이미지의 임시 생산자 Pod에서 엔진 콜백 센서(worker_shards 켬)로 offline LLM(mp 실행기)을 띄운 실제 출력이다
(2026-11-08, arm64, EXAMPLE). 사례: 단일 파일(opt-125m bin), 같은 가중치를 safetensors shard 2개+index로 나눈 사본, 그 사본에서 shard 하나 삭제.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.adapters import vllm_worker_shard_callback as callback
from guard.collectors import engine_callbacks
from guard.collectors import gpu_common_inputs as contract
from guard.collectors import worker_shard_producer as producer
from guard.executors import common_consumption
from guard.executors import supply_boot_contract

FIXTURE = Path(__file__).parent / 'fixtures/worker_shard_k3d_20261108.json'
POD = {'pod_uid': '11111111-2222-3333-4444-555555555555', 'container_id': 'containerd://' + 'b' * 64, 'restart_count': 0}
SCOPE = {'environment_ref': {'kind': 'ENV', 'id': 'ENV-EXAMPLE', 'revision': 1, 'sha256': 'a' * 64},
         'target_sha256': 'c' * 64, 'prepared_sha256': 'd' * 64, 'session': 'e' * 32}


def case(name):
    return json.loads(FIXTURE.read_text())['cases'][name]


def build(rows, **kw):
    return producer.record(rows, pod=POD, **SCOPE, **kw)


def test_engines_reached_ready_only_when_shards_were_present():
    assert case('single-file')['engine_ready'] and case('sharded')['engine_ready']
    assert not case('missing-shard')['engine_ready']


def test_sharded_model_assigns_both_required_shards_then_loads_and_reports_ready():
    record, result = build(case('sharded')['rows'])
    assert result['gaps'] == ['GPU_INPUT_PRODUCER_UNSUPPORTED'] and result['violation_candidates'] == []
    required = {r['shard_sha256'] for r in record['events'] if r['event'] == 'shard_required'}
    assigned = {r['shard_sha256'] for r in record['events'] if r['event'] == 'shard_assigned'}
    assert len(required) == 2 and required == assigned
    assert all(r['bytes'] > 0 for r in record['events'] if r['event'] == 'shard_assigned')
    assert producer.shard_rows(record) == [{'rank': 0, 'required_missing': False, 'ready_accepted': True,
                                            'load_completed': True, 'load_failed': False}]
    assert supply_boot_contract._worker_shard(producer.shard_rows(record), {}) == ([], [])


def test_single_file_model_uses_the_assigned_file_as_required():
    record, result = build(case('single-file')['rows'])
    required, = [row['shard_sha256'] for row in record['events'] if row['event'] == 'shard_required']
    assigned, = [row['shard_sha256'] for row in record['events'] if row['event'] == 'shard_assigned']
    assert required == assigned
    assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:REQUIRED_FROM_ASSIGNED' in result['gaps']
    assert producer.shard_rows(record)[0]['ready_accepted'] is True


def test_missing_shard_fails_at_assignment_and_never_reports_ready():
    record, result = build(case('missing-shard')['rows'])
    assert [r['event'] for r in record['events']] == ['load_failed']
    assert record['completeness']['source_ended'] is False and result['violation_candidates'] == []
    row, = producer.shard_rows(record)
    assert row['ready_accepted'] is False and row['load_failed'] is True


def test_registered_producer_makes_the_sharded_record_complete(monkeypatch):
    source = producer.producer()
    monkeypatch.setitem(contract.SUPPORTED_PRODUCERS, (source['name'], source['version']), source['source_sha256'])
    _, result = build(case('sharded')['rows'])
    assert result['complete'] is True and result['producer_authenticated'] is False


def test_relative_index_symlink_preserves_observed_required_and_assigned_shards(tmp_path):
    shard = tmp_path / 'fixture.safetensors'
    content = b'EXAMPLE shard metadata fixture'
    shard.write_bytes(content)
    index = tmp_path / 'fixture.index.json'
    index.write_text(json.dumps({'weight_map': {'fixture.weight': shard.name}}), encoding='utf-8')
    link = tmp_path / 'selected.index.json'
    link.symlink_to(index.name)

    observer = callback.WorkerShardObserver(SCOPE['session'], context_provider=lambda: {})
    observed = observer._prepared((str(tmp_path), [str(shard)], True, link.name))
    assert observed['index_present'] is True and not observer.gaps
    assert observed['required'] == observed['files']
    rows = case('sharded')['rows']
    prepared = next(r for r in rows if r.get('point') == 'DefaultModelLoader._prepare_weights' and r['phase'] == 'RETURN')
    prepared['fields'].update(observed)

    record, result = build(rows)
    required, = [row for row in record['events'] if row['event'] == 'shard_required']
    assigned, = [row for row in record['events'] if row['event'] == 'shard_assigned']
    assert required['shard_sha256'] == assigned['shard_sha256'] == observed['files'][0]
    assert assigned['bytes'] == len(content)
    assert record['completeness'] == {'source_ended': True, 'dropped': 0, 'diagnostic_codes': []}
    assert result['violation_candidates'] == []


@pytest.mark.parametrize('index_state,index_present,diagnostic', [
    ('unknown', None, 'WORKER_SHARD_INDEX_UNOBSERVED'),
    ('unrecorded', None, 'WORKER_SHARD_INDEX_UNOBSERVED'),
    ('malformed', None, 'WORKER_SHARD_INDEX_UNOBSERVED'),
    ('conflicting-requirement', False, 'WORKER_SHARD_INDEX_UNOBSERVED'),
    ('unreadable', True, 'WORKER_SHARD_INDEX_UNREADABLE'),
    ('empty-requirements', True, 'WORKER_SHARD_INDEX_UNREADABLE'),
    ('malformed-digest', True, 'WORKER_SHARD_INDEX_UNREADABLE'),
    ('directory-index', True, 'WORKER_SHARD_INDEX_UNREADABLE'),
    ('linked-directory-index', True, 'WORKER_SHARD_INDEX_UNREADABLE'),
])
def test_unverified_shard_index_keeps_assignments_without_required_claims(
        tmp_path, monkeypatch, index_state, index_present, diagnostic):
    shard = tmp_path / 'fixture.safetensors'
    content = b'EXAMPLE shard metadata fixture'
    shard.write_bytes(content)
    observer = callback.WorkerShardObserver(SCOPE['session'], context_provider=lambda: {})
    native_result = (str(tmp_path), [str(shard)], True)
    index_opens = []
    if index_present is True:
        index = tmp_path / 'fixture.index.json'
        if index_state in ('directory-index', 'linked-directory-index'):
            index.mkdir()
        else:
            index.write_text('{}', encoding='utf-8')
        if index_state == 'linked-directory-index':
            directory = index
            link = tmp_path / 'linked.index.json'
            link.symlink_to(directory.name)
            index = link
        native_result += (index.name,)
    elif index_present is False:
        native_result += (None,)
    with monkeypatch.context() as inspect_index:
        if index_state == 'linked-directory-index':
            open_file = callback.os.open

            def detect_index_open(path, *args, **kwargs):
                if path in (index, directory, str(index), str(directory)):
                    index_opens.append(path)
                    raise AssertionError('A link to a directory must be rejected before opening it')
                return open_file(path, *args, **kwargs)

            inspect_index.setattr(callback.os, 'open', detect_index_open)
        observed = observer._prepared(native_result)
    assert not index_opens
    assert observed['index_present'] is index_present
    assert observed['required'] is None
    if index_state == 'unrecorded':
        del observed['index_present']
        observed['required'] = ['f' * 64]
    elif index_state == 'malformed':
        observed.update(index_present=1, required=['f' * 64])
    elif index_state == 'conflicting-requirement':
        observed['required'] = ['f' * 64]
    elif index_state == 'empty-requirements':
        observed['required'] = []
    elif index_state == 'malformed-digest':
        observed['required'] = ['f' * 64, 'not-a-sha256']

    rows = case('sharded')['rows']
    prepared = next(r for r in rows if r.get('point') == 'DefaultModelLoader._prepare_weights' and r['phase'] == 'RETURN')
    prepared['fields'] = {key: prepared['fields'][key] for key in ('pid', 'start_ticks', 'tp_rank')}
    prepared['fields'].update(observed)
    source = producer.producer()
    monkeypatch.setitem(contract.SUPPORTED_PRODUCERS, (source['name'], source['version']), source['source_sha256'])

    record, result = build(rows)
    assigned, = [row for row in record['events'] if row['event'] == 'shard_assigned']
    assert assigned['shard_sha256'] == observed['files'][0] and assigned['bytes'] == len(content)
    assert all(row['event'] != 'shard_required' for row in record['events'])
    assert record['completeness'] == {'source_ended': False, 'dropped': 0, 'diagnostic_codes': [diagnostic]}
    assert result['supported'] and not result['complete'] and not result['producer_authenticated']
    projection = common_consumption.worker_shards(record, SCOPE, {})
    assert f'GPU_INPUT_PRODUCER_DIAGNOSTIC:{diagnostic}' in projection['missing']
    assert projection['load_facts'][0]['load_completed'] is True
    assert not projection['complete'] and not projection['violations']


def test_ready_with_a_missing_required_shard_is_a_violation_candidate_and_existing_consumer_fails_it():
    rows = deepcopy(case('sharded')['rows'])
    prepared = next(r for r in rows if r.get('point') == 'DefaultModelLoader._prepare_weights' and r['phase'] == 'RETURN')
    prepared['fields']['files'] = prepared['fields']['files'][:1]
    prepared['fields']['file_bytes'] = prepared['fields']['file_bytes'][:1]
    record, result = build(rows)
    assert {'binding': 0, 'reason': 'missing_required_shard_reported_ready'} in result['violation_candidates']
    violations, _ = supply_boot_contract._worker_shard(producer.shard_rows(record), {})
    assert violations == [{'reason': 'missing_shard_reported_ready', 'rank': 0}]


def test_ready_after_a_failed_load_is_a_violation_candidate():
    rows = deepcopy(case('sharded')['rows'])
    for r in rows:
        if r.get('point') == 'DefaultModelLoader.track_weights_loading' and r['phase'] == 'RETURN':
            r['phase'] = 'RAISE'
    _, result = build(rows)
    assert {'binding': 0, 'reason': 'failed_load_reported_ready'} in result['violation_candidates']


def test_unqualified_entry_or_missing_identity_is_named():
    rows = deepcopy(case('sharded')['rows'])
    rows[0]['entry_qualified_before'] = False
    record, _ = build(rows)
    assert 'WORKER_SHARD_ENTRY_UNQUALIFIED' in record['completeness']['diagnostic_codes']
    assert record['completeness']['source_ended'] is False
    stripped = deepcopy(case('sharded')['rows'])
    for r in stripped:
        r['fields'] = {k: v for k, v in r.get('fields', {}).items() if k not in ('pid', 'start_ticks')}
    with pytest.raises(ValueError, match='WORKER_SHARD_NO_WORKER_IDENTITY'):
        build(stripped)


@pytest.mark.parametrize('gap', ['WORKER_RANK_UNOBSERVED', 'WORKER_RANK_CONFLICT'])
def test_unattributed_rank_rows_do_not_reach_the_supply_consumer(gap):
    rows = case('single-file')['rows']
    if gap == 'WORKER_RANK_UNOBSERVED':
        for row in rows:
            fields = row.get('fields') or {}
            fields.pop('rank', None)
            fields.pop('tp_rank', None)
    else:
        row = next(row for row in rows if (row.get('fields') or {}).get('tp_rank') == 0)
        row['fields']['tp_rank'] = 1
    with pytest.raises(ValueError, match='WORKER_SHARD_NO_WORKER_IDENTITY'):
        build(rows)

    observed = case('sharded')['rows']
    record, result = build(observed + rows)
    assert record['completeness'] == {'source_ended': False, 'dropped': 0, 'diagnostic_codes': [gap]}
    assert result['generations'] == 1
    projection = common_consumption.worker_shards(record, SCOPE, {})
    assert projection['rows'] == [{'rank': 0, 'required_missing': False, 'ready_accepted': True}]
    assert projection['load_facts'][0]['load_completed'] is True
    assert f'GPU_INPUT_PRODUCER_DIAGNOSTIC:{gap}' in projection['missing']
    assert not projection['complete'] and not projection['violations']


def test_points_avoid_the_traced_decorators_and_assets_carry_the_flag(tmp_path):
    assert callback.TARGETS[callback.MP] == ('WorkerProc.monitor_death_pipe',)
    assert 'DefaultModelLoader.load_weights' not in callback.TARGETS[callback.LOADER]
    manifest = engine_callbacks.prepare_assets(tmp_path / 'a', worker_shards=True)
    assert manifest['worker_shards'] is True
    assert 'guard_engine_callbacks/vllm_worker_shard_callback.py' in manifest['assets']
    assert json.loads((tmp_path / 'a/settings.json').read_text())['worker_shards'] is True
    assert engine_callbacks.prepare_assets(tmp_path / 'b')['worker_shards'] is False
