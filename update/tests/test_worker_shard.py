"""SUP-06 worker-shard-load 공통 입력(개발 담당, B 의존 반환 5).

행은 k3d vLLM 0.31.0 CPU 이미지의 임시 생산자 Pod에서 엔진 콜백 센서(worker_shards 켬)로 offline LLM(mp 실행기)을 띄운 실제 출력이다
(2026-11-08, arm64, EXAMPLE). 사례: 단일 파일(opt-125m bin), 같은 가중치를 safetensors shard 2개+index로 나눈 사본, 그 사본에서 shard 하나 삭제.
"""
from copy import deepcopy
from itertools import count
import json
import os
from pathlib import Path
import sys
from types import CodeType, FunctionType, ModuleType, SimpleNamespace

import pytest

from guard.adapters import vllm_worker_shard_callback as callback
from guard.adapters.vllm_engine_callback_sensor import EngineCallbacks, Sink
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


def _selected_code(fixture_name, *scope, directory='model_executor/model_loader'):
    path = FIXTURE.parent / 'vllm-0.30.0/vllm' / directory / fixture_name
    code = compile(path.read_bytes(), str(path), 'exec', dont_inherit=True, optimize=0)
    for name in scope:
        code = next(child for child in code.co_consts if type(child) is CodeType and child.co_name == name)
    return path, code


@pytest.mark.parametrize('selection', ['observed', 'absent-index', 'different-alias', 'flag-off'])
def test_vllm_030_selected_native_index_flows_through_the_worker_shard_writer(
        tmp_path, monkeypatch, selection):
    shards = [tmp_path / name for name in ('part-a.safetensors', 'part-b.safetensors')]
    shard_bytes = [b'EXAMPLE first shard', b'EXAMPLE other shard data']
    for shard, data in zip(shards, shard_bytes):
        shard.write_bytes(data)
    index = tmp_path / 'model.safetensors.index.json'
    has_index = selection != 'absent-index'
    if has_index:
        index.write_text(json.dumps({'weight_map': {'layer_a': shards[0].name, 'layer_b': shards[1].name}}),
                         encoding='utf-8')

    native_reads, guard_reads = [], []
    os_open = os.open

    def native_index_open(path):
        assert path == str(index)
        native_reads.append(path)
        return index.open(encoding='utf-8')

    def checked_os_open(path, *args, **kwargs):
        if path in (index, str(index)):
            guard_reads.append(path)
            raise AssertionError('Only the ordinary native helper may read the index')
        return os_open(path, *args, **kwargs)

    monkeypatch.setattr(os, 'open', checked_os_open)
    monkeypatch.setattr(callback, '_start_ticks', lambda: 123)
    monkeypatch.setattr(callback, '_tp_rank', lambda: 0)

    helper = ModuleType(callback.WEIGHTS)
    helper_file, filter_code = _selected_code('weight_utils.py.txt', callback.FILTER)
    helper.__dict__.update(__file__=str(helper_file), os=SimpleNamespace(path=os.path),
                           json=SimpleNamespace(load=json.load), open=native_index_open)
    native_filter = FunctionType(filter_code, vars(helper))
    setattr(helper, callback.FILTER, native_filter)
    loader_alias = FunctionType(filter_code, vars(helper)) if selection == 'different-alias' else native_filter
    monkeypatch.setitem(sys.modules, helper.__name__, helper)

    loader = ModuleType(callback.LOADER)
    class_name = 'DefaultModelLoader'
    loader_file, prepare_code = _selected_code('default_loader.py.txt', class_name, '_prepare_weights')
    _, track_code = _selected_code('default_loader.py.txt', class_name, 'track_weights_loading')
    matching_paths = [str(shard) for shard in shards]

    def matching_shards(pattern):
        assert pattern == str(tmp_path / '*.safetensors')
        return matching_paths

    loader.__dict__.update(__file__=str(loader_file), SAFE_WEIGHTS_INDEX_NAME=index.name,
                            os=SimpleNamespace(path=os.path), glob=SimpleNamespace(glob=matching_shards),
                            maybe_download_from_modelscope=lambda path, revision: None)
    setattr(loader, callback.FILTER, loader_alias)
    prepare = FunctionType(prepare_code, vars(loader))
    loader.DefaultModelLoader = type(class_name, (), {
        '__module__': loader.__name__, '_prepare_weights': prepare,
        'track_weights_loading': FunctionType(track_code, vars(loader)),
    })
    monkeypatch.setitem(sys.modules, loader.__name__, loader)

    flag_on = selection != 'flag-off'
    sink = Sink(tmp_path)
    ticks = count(1_000_000)
    callbacks = EngineCallbacks(SCOPE['session'], b'w' * 32, sink, worker_shards=flag_on,
                                context_provider=lambda: {}, clock=lambda: next(ticks))
    try:
        assert callbacks.instrument(loader) is flag_on
        instance = loader.DefaultModelLoader()
        instance.load_config = SimpleNamespace(load_format='safetensors')
        assert instance._prepare_weights(str(tmp_path), None, None, False, None) == (
            str(tmp_path), matching_paths, True)
        if selection == 'observed':
            model = SimpleNamespace(named_parameters=lambda: (), named_modules=lambda: ())
            instance.track_weights_loading(model, set())
            worker_module = ModuleType(callback.MP)
            worker_source, monitor = _selected_code('multiproc_executor.py.txt', 'WorkerProc', 'monitor_death_pipe',
                                                    directory='v1/executor')
            worker_module.__file__ = str(worker_source)
            worker_module.WorkerProc = type('WorkerProc', (), {
                '__module__': worker_module.__name__, 'monitor_death_pipe': FunctionType(monitor, vars(worker_module)),
            })
            worker = worker_module.WorkerProc()
            worker.rank = 0
            observe = callbacks.shard.wrap(worker_module, 'WorkerProc.monitor_death_pipe')
            observe(worker, None, None)  # The native method returns before starting a monitor without a pipe.
            callbacks.shard.gaps.add('WORKER_SHARD_INDEX_UNREADABLE')
            callbacks.gap('DISTRIBUTED_NON_EXECUTE_RPC_UNSUPPORTED')
            callbacks.flush_gaps()
            other = EngineCallbacks(SCOPE['session'], b'w' * 32, sink,
                                    context_provider=lambda: {'fixture_other_process': True})
            other.gap('WORKER_SHARD_FILE_SET_UNAVAILABLE')
    finally:
        if sink.fd is not None:
            os.close(sink.fd)

    assert native_reads == ([str(index)] if has_index else [])
    assert not guard_reads
    assert getattr(helper, callback.FILTER) is native_filter
    if not flag_on:
        assert getattr(loader, callback.FILTER) is loader_alias
        assert loader.DefaultModelLoader._prepare_weights is prepare
        assert not sink.path.exists()
        return

    raw = sink.path.read_text(encoding='utf-8')
    rows = [json.loads(line) for line in raw.splitlines()]
    returned, = [row for row in rows if row['schema'] == callback.SCHEMA and row['phase'] == 'RETURN'
                 and row['point'] == callback.PREPARE]
    assert returned['entry_qualified_before'] is returned['entry_qualified_after'] is True
    assert returned['runtime_context_injected'] is True
    assert returned['native_profile_installed'] is False
    fields = returned['fields']
    keyed = [callbacks.shard.fingerprint('shard:' + shard.name) for shard in shards]
    assert fields['files'] == keyed and fields['file_bytes'] == [len(data) for data in shard_bytes]
    # A normal index selecting every shard produces a fresh list with unchanged contents.
    observed = selection == 'observed'
    assert fields['index_present'] is (True if observed else None)
    assert fields['required'] == (sorted(keyed) if observed else None)
    assert all(value not in raw for value in [str(tmp_path), index.name, *(shard.name for shard in shards)])

    if observed:
        assert callbacks.cache.process_sha256 == returned['process_sha256']
        record, result = producer.record_from_processes(
            [{'pid': fields['pid'], 'start_ticks': fields['start_ticks'], 'events': rows}], pod=POD, **SCOPE)
        assert {'load_completed', 'rank_ready'} <= {event['event'] for event in record['events']}
        assert record['completeness'] == {'source_ended': False, 'dropped': 0,
                                          'diagnostic_codes': ['WORKER_SHARD_INDEX_UNREADABLE']}
    else:
        record, result = build(rows)
    assert {event['shard_sha256']: event['bytes'] for event in record['events']
            if event['event'] == 'shard_assigned'} == dict(zip(keyed, fields['file_bytes']))
    assert {event['shard_sha256'] for event in record['events'] if event['event'] == 'shard_required'} == (
        set(keyed) if observed else set())
    projection = common_consumption.worker_shards(record, SCOPE, {})
    assert 'GPU_INPUT_PRODUCER_UNSUPPORTED' in projection['missing']
    if observed:
        assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:WORKER_SHARD_INDEX_UNREADABLE' in projection['missing']
        assert not any('FILE_SET_UNAVAILABLE' in gap or 'DISTRIBUTED' in gap for gap in projection['missing'])
    index_gap = 'WORKER_SHARD_INDEX_UNOBSERVED'
    assert (index_gap in record['completeness']['diagnostic_codes']) == (not observed)
    assert (f'GPU_INPUT_PRODUCER_DIAGNOSTIC:{index_gap}' in projection['missing']) == (not observed)
    assert not result['producer_authenticated'] and not projection['complete'] and not projection['violations']
    if observed:
        handoff_root = tmp_path / 'handoff-input'
        assets = handoff_root / 'assets'
        engine_callbacks.prepare_assets(assets, worker_shards=True)
        capture, _, env, env_path, target, target_path = _collect_saved_worker_rows(
            handoff_root, monkeypatch, 'baseline', assets, rows)
        _assert_saved_capture_handoff(
            handoff_root, monkeypatch, 'baseline', env, env_path, target, target_path, assets, capture)


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


@pytest.mark.parametrize('broken_pair', [None, 'missing-call', 'entry-code', 'duplicate-terminal', 'terminal-first'])
def test_page_assignment_requires_one_matching_native_call(broken_pair):
    from guard.models import sha256_hex

    rows = case('sharded')['rows']
    for row in rows:
        if row.get('schema') == callback.SCHEMA:
            row['session_sha256'] = sha256_hex(SCOPE['session'].encode())
    call = next(row for row in rows if row.get('point') == callback.PREPARE and row['phase'] == 'CALL')
    terminal = next(row for row in rows if row.get('point') == callback.PREPARE and row['phase'] == 'RETURN')
    if broken_pair == 'missing-call':
        rows.remove(call)
    elif broken_pair == 'entry-code':
        terminal['entry_code_sha256'] = sha256_hex(b'EXAMPLE other entry')
    elif broken_pair == 'duplicate-terminal':
        rows.append(deepcopy(terminal))
    elif broken_pair == 'terminal-first':
        rows.remove(terminal)
        rows.insert(rows.index(call), terminal)
    pid, ticks = (terminal['fields'][key] for key in ('pid', 'start_ticks'))
    record, result = producer.record_from_processes([{'pid': pid, 'start_ticks': ticks, 'events': rows}], pod=POD, **SCOPE)
    events = {row['event'] for row in record['events']}
    assert {'load_completed', 'rank_ready'} <= events
    shard_facts = {'shard_required', 'shard_assigned'}
    assert events & shard_facts == (shard_facts if broken_pair is None else set())
    assert ('WORKER_SHARD_TERMINAL_UNJOINED' in record['completeness']['diagnostic_codes']) == (broken_pair is not None)
    assert not record['completeness']['source_ended']
    assert not result['supported'] and not result['complete']


def test_page_process_requires_one_native_context_and_keeps_an_independent_worker():
    from guard.models import sha256_hex

    session_sha256 = sha256_hex(SCOPE['session'].encode())

    def page(sample):
        rows = [row for row in case(sample)['rows'] if row['schema'] == callback.SCHEMA]
        for row in rows:
            row['session_sha256'] = session_sha256
        return {'pid': rows[0]['fields']['pid'], 'start_ticks': rows[0]['fields']['start_ticks'], 'events': rows}

    mixed = page('sharded')
    sibling = page('single-file')
    other_context = sibling['events'][0]['process_sha256']
    # Both legs of the saved prepare call still match, but the load and Ready
    # records for this page identify another native context.
    for row in mixed['events']:
        if row['point'] == callback.PREPARE:
            row['process_sha256'] = other_context

    record, result = producer.record_from_processes([mixed, sibling], pod=POD, **SCOPE)
    binding, = record['bindings']
    assert (binding['worker_pid'], binding['start_ticks']) == (sibling['pid'], sibling['start_ticks'])
    assert [row['bytes'] for row in record['events'] if row['event'] == 'shard_assigned'] == [
        next(row['fields']['file_bytes'][0] for row in sibling['events'] if row['point'] == callback.PREPARE
             and row['phase'] == 'RETURN')]
    assert {'shard_required', 'shard_assigned', 'load_completed', 'rank_ready'} <= {
        row['event'] for row in record['events']}
    assert record['completeness']['diagnostic_codes'] == ['REQUIRED_FROM_ASSIGNED', 'WORKER_SHARD_SCOPE_MISMATCH']
    assert not record['completeness']['source_ended']
    assert not result['supported'] and not result['complete'] and not result['producer_authenticated']
    assert other_context not in json.dumps(record)


@pytest.mark.parametrize(('side', 'wrong_scope', 'index_present'), (
    ('baseline', None, True), ('candidate', None, True), ('candidate', 'session', True),
    ('candidate', 'pid', True), ('candidate', None, False), ('candidate', None, None),
))
def test_cli_selected_worker_shards_use_bound_collector_rows(tmp_path, monkeypatch, side, wrong_scope, index_present):
    from typer.testing import CliRunner

    from guard.cli import app
    from guard.models import canonical_json, sha256_hex

    assets = tmp_path / 'assets'
    prepared_result = CliRunner().invoke(app, ['prepare-engine-callbacks', '--output', str(assets), '--worker-shards'])
    assert prepared_result.exit_code == 0, prepared_result.output
    rows = deepcopy(case('sharded')['rows'])
    if index_present is not True:
        for row in rows:
            if row.get('point') == callback.PREPARE and row.get('phase') == 'RETURN':
                row['fields'].update(index_present=index_present, required=None)
    result, prepared, env, env_path, target, target_path = _collect_saved_worker_rows(
        tmp_path, monkeypatch, side, assets, rows, wrong_scope=wrong_scope)
    captured = result['records']['worker-shard-load']
    record = captured['record']
    required = {row['shard_sha256'] for row in record['events'] if row['event'] == 'shard_required'}
    assigned = {row['shard_sha256'] for row in record['events'] if row['event'] == 'shard_assigned'}
    assert required == (assigned if index_present is not None else set())
    assert len(assigned) == (0 if wrong_scope else 2)
    assert {'load_completed', 'rank_ready'} <= {row['event'] for row in record['events']}
    assert record['completeness']['source_ended'] is False
    assert ('WORKER_SHARD_SCOPE_MISMATCH' in record['completeness']['diagnostic_codes']) is bool(wrong_scope)

    scope = {'environment_ref': env.ref().model_dump(mode='json'), 'target_sha256': sha256_hex(canonical_json(target)),
             'prepared_sha256': sha256_hex(canonical_json(prepared)), 'session': prepared['session']}
    checked = common_consumption.worker_shards(record, scope, {})
    for validation in (captured['validation'], checked):
        assert validation['supported'] is validation['complete'] is False
    _assert_saved_capture_handoff(tmp_path, monkeypatch, side, env, env_path, target, target_path, assets, result,
                                  other_release=side == 'baseline', assignments_verified=wrong_scope is None)


def _collect_saved_worker_rows(tmp_path, monkeypatch, side, assets, rows, *, wrong_scope=None):
    from guard.adapters import observer_runtime
    from guard.adapters import vllm_engine_callback_sensor as sensor
    from guard.models import canonical_json, sha256_hex
    from guard.runtime.callback_pages import assemble
    from tests.test_callback_paging_runtime import reader
    from tests.test_kubernetes_sampling import normal_sampling_inputs
    from tools import gpu_a_producers_collect as collector
    from tools.mvp_versions import versions

    prepared = engine_callbacks.load_prepared(assets)
    assert prepared['worker_shards'] is True
    session = prepared['session']
    session_hash = sha256_hex(session.encode())
    release = versions()[side]
    env, target, _, target_path = normal_sampling_inputs(tmp_path, release['version'])
    env = env.model_copy(update={
        'engine': env.engine.model_copy(update={'image_digest': release['image']}),
        'runtime': env.runtime.model_copy(update={'tp_size': 2, 'pp_size': 1, 'dp_size': 1}),
    }).seal()
    target['environment_ref'] = env.ref().model_dump(mode='json')
    target['engine']['image_digest'] = release['image']
    target['kubernetes']['engine'].update(image=release['image'], image_id=release['image'])
    env_path = tmp_path / 'env.json'
    env_path.write_bytes(canonical_json(env.to_dict()))
    target_path.write_bytes(canonical_json(target))

    # Rebind the inert sample to this EXAMPLE session and its local writer epoch.
    pid = os.getpid()
    start_ticks = int(Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19])
    rows = deepcopy(rows)
    for row in rows:
        if 'session_sha256' in row:
            row['session_sha256'] = session_hash
        fields = row.get('fields', {})
        if 'pid' in fields and 'start_ticks' in fields:
            fields.update(pid=pid, start_ticks=start_ticks)
    assignment = next(row for row in rows if row.get('point') == callback.PREPARE and row['phase'] == 'RETURN')
    if wrong_scope == 'session':
        assignment['session_sha256'] = sha256_hex(b'other fixture session')
    elif wrong_scope == 'pid':
        assignment['fields']['pid'] += 1

    pages_dir = tmp_path / 'callback-pages'
    data_root = tmp_path / 'callback-data'
    read_page = reader()
    row_lengths = [len((json.dumps(event, sort_keys=True, separators=(',', ':')) + '\n').encode()) for event in rows]
    capture_id = sha256_hex((session + ':example-checkpoint').encode())[:32]

    def retained_pages():
        snapshot, cursor = None, None
        for _ in rows:
            page = read_page(data_root / session, snapshot, cursor, capture_id)
            yield page
            if page['done']:
                return
            snapshot, cursor = page['snapshot'], page['cursor']
        raise AssertionError('The EXAMPLE journal did not fit in the expected pages')

    class CapturedTransport:
        def __init__(self, selected_target, selected_env, *, duration, profile):
            assert (selected_target, selected_env.ref()) == (target, env.ref())
            assert profile == collector.ENGINE_CALLBACK_PROFILE
            self.binding = target['kubernetes']

        def engine_attempt_events(self, selected_session, *, archive):
            assert selected_session == session and archive == pages_dir
            return assemble(retained_pages(), archive=archive)

    with monkeypatch.context() as local:
        meta_path = []
        local.setattr(sensor, 'sys', SimpleNamespace(meta_path=meta_path))
        local.setattr(sensor, 'atexit', SimpleNamespace(register=lambda _callback: None))
        local.setattr(collector, 'KubernetesHTTP', CapturedTransport)
        local.setitem(read_page.__globals__, 'MAX_PAGE', max(max(row_lengths), sum(row_lengths) // 2))
        assert sensor.install(root=assets, data_root=data_root, context_provider=lambda: {})
        callbacks = meta_path[0].callbacks
        assert isinstance(callbacks.sink, observer_runtime.JournalSink)
        assert callbacks.shard is not None and callbacks.consumption is callbacks.consumption_v2 is None
        try:
            for row in rows:
                callbacks.sink(row)
            result = collector.collect(env_path=env_path, target_path=target_path, prepared_dir=assets, pages_dir=pages_dir)
            assert (data_root / session / f'checkpoint-{pid}-{capture_id}.json').is_file()
            assert result['page_capture']['diagnostic_codes'] == []
        finally:
            callbacks.sink.close()
    return result, prepared, env, env_path, target, target_path


def _assert_saved_capture_handoff(root, monkeypatch, side, env, env_path, target, target_path, assets, capture, *,
                                 other_release=False, assignments_verified=True):
    import socket
    import subprocess
    from types import SimpleNamespace

    from guard.collectors import lifecycle
    from guard.executors import cache_timing_group
    from guard.executors.kubernetes_supply_boot import KubernetesSupplyBootExecutor
    from guard.models import canonical_json, sha256_hex
    from tools import mvp_sup06_worker_shard_inputs as handoff

    def forbidden(*args, **kwargs):
        pytest.fail('Saved worker shard materialization must not contact an owner or execute a process')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    owner = target['kubernetes']
    lifecycle_target = {'schema': 'guard-kubernetes-lifecycle-target/1', 'lab': True, 'run_id': owner['run_id'],
                        'environment_ref': env.ref().model_dump(mode='json'),
                        'kubernetes': {key: deepcopy(owner[key]) for key in ('context', 'engine')}}
    lifecycle_path, capture_path = root / 'lifecycle-target.json', root / 'producers.json'
    lifecycle_path.write_bytes(canonical_json(lifecycle_target))
    capture_raw = (json.dumps(capture, ensure_ascii=False, indent=1, sort_keys=True) + '\n').encode()
    capture_path.write_bytes(capture_raw)
    paths = {'environment': env_path, 'lifecycle_target': lifecycle_path, 'callback_target': target_path,
             'callback_prepared': assets / 'manifest.json', 'capture': capture_path}
    selection_path = root / 'selection.json'

    def write_selection(*, with_archive=True):
        files = {key: {'path': str(path), 'sha256': sha256_hex(path.read_bytes())} for key, path in paths.items()}
        selection = {'schema': handoff.SELECTION_SCHEMA, 'side': side, 'files': files}
        if with_archive:
            selection['page_archive'] = str(root / 'callback-pages')
        raw = canonical_json(selection)
        selection_path.write_bytes(raw)
        return sha256_hex(raw)

    selection_sha = write_selection()
    output = root / 'handoff'
    if other_release:
        with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_RELEASE_SOURCE_MISMATCH$'):
            handoff.prepare(selection_path, selection_sha, output)
        assert not output.exists()
        assert capture_path.read_bytes() == capture_raw
        return
    receipt = handoff.prepare(selection_path, selection_sha, output)
    assert json.loads((output / handoff.OUTPUT_NAME).read_bytes()) == receipt
    assert receipt['side'] == side and receipt['role'] == 'normal'
    assert receipt['source_file_sha256']['capture'] == sha256_hex(capture_raw)
    assert receipt['source_ended'] is receipt['owner_binding_verified'] is receipt['producer_authenticated'] is False
    assert not receipt['ready_for_execution']
    fragment = receipt['worker_shard_input']
    saved_record = Path(fragment['path']).read_bytes()
    assert saved_record == canonical_json(capture['records']['worker-shard-load']['record'])
    assert fragment['sha256'] == sha256_hex(saved_record) and fragment['data_origin'] == 'EXAMPLE'
    assert fragment['capture_archive'] == {'path': str(capture_path), 'sha256': sha256_hex(capture_raw),
                                           'page_archive': str(root / 'callback-pages')}
    prepared_path = assets / 'manifest.json'
    source_sha256 = {'target': sha256_hex(target_path.read_bytes()), 'prepared': sha256_hex(prepared_path.read_bytes())}
    assert fragment['callback_source_sha256'] == source_sha256
    assert capture_path.read_bytes() == capture_raw

    base_prepared = lifecycle.prepare_assets(root / 'lifecycle-assets', Path(__file__).resolve().parents[2])
    executor = KubernetesSupplyBootExecutor({'normal': {'worker_shard_input': fragment}}, runner=forbidden)
    bound = {'supply_normal_prepared_sha256': sha256_hex(canonical_json(base_prepared))}
    read_regular = cache_timing_group.read_regular
    reads = []

    def read(path, *, limit, expected_sha256=None):
        reads.append((Path(path), (limit, expected_sha256)))
        return read_regular(path, limit=limit, expected_sha256=expected_sha256)

    with monkeypatch.context() as tracked:
        tracked.setattr(cache_timing_group, 'read_regular', read)
        record, scope, result = executor._worker_shard_input(
            env, bound, 'normal', lifecycle_target, sha256_hex(lifecycle_path.read_bytes()))
    assert dict(reads) == {target_path: (65536, source_sha256['target']),
                           prepared_path: (262144, source_sha256['prepared']),
                           **{assets / name: (len(raw), None) for name, raw in engine_callbacks._sources().items()}}
    assert len(reads) == len(dict(reads)) and assets / 'settings.json' not in dict(reads)
    assert canonical_json(record) == saved_record and scope == fragment['scope']
    assert result['complete'] is False and result['violations'] == []
    assert result['callback_source_sha256'] == source_sha256
    witnesses = result['native_rank_witnesses']
    assert receipt['native_rank_witnesses'] == witnesses
    assert witnesses['capture_sha256'] == sha256_hex(capture_raw)
    assert witnesses['expected_ranks'] == [0, 1]
    rank, = witnesses['ranks']
    binding, = record['bindings']
    assert (rank['rank'], rank['worker_pid'], rank['start_ticks']) == (
        0, binding['worker_pid'], binding['start_ticks'])
    raw_pages = {reference['sha256']: json.loads((root / 'callback-pages' / reference['path']).read_bytes())
                 for reference in capture['raw_page_refs']}
    expected = [
        ('load_call', 'DefaultModelLoader.track_weights_loading', 'CALL'),
        ('load_return', 'DefaultModelLoader.track_weights_loading', 'RETURN'),
        ('ready_candidate_call', 'WorkerProc.monitor_death_pipe', 'CALL'),
    ]
    if assignments_verified:
        expected.extend([('prepare_call', callback.PREPARE, 'CALL'), ('prepare_return', callback.PREPARE, 'RETURN')])
    else:
        assert 'prepare_call' not in rank and 'prepare_return' not in rank
    for key, point, phase in expected:
        location = rank[key]
        retained = raw_pages[location['page_sha256']]['rows'][location['row_index']]['event']
        assert (retained['point'], retained['phase']) == (point, phase)
        for field in ('call_sha256', 'source_file_sha256', 'operation_started_ns', 'operation_finished_ns'):
            assert location[field] == retained[field]
        if key == 'prepare_return':
            assigned = [(event['shard_sha256'], event['bytes'])
                        for event in record['events'] if event['event'] == 'shard_assigned']
            assert assigned == list(zip(retained['fields']['files'], retained['fields']['file_bytes']))
            if retained['fields']['index_present'] is True:
                required = {event['shard_sha256'] for event in record['events'] if event['event'] == 'shard_required'}
                assert location['indexed_shard_assignment'] == {
                    'required_shard_count': len(required),
                    'assigned_shard_count': len({digest for digest, _ in assigned}),
                    'required_shards_assigned': True,
                }
            else:
                assert 'indexed_shard_assignment' not in location
    assert rank['load_call']['call_sha256'] == rank['load_return']['call_sha256']
    if assignments_verified:
        assert rank['prepare_call']['call_sha256'] == rank['prepare_return']['call_sha256']

    with monkeypatch.context() as inert:
        def offline_reader(*args):
            raise OSError('EXAMPLE owner unavailable')

        inert.setattr(executor, '_role_reader', offline_reader)
        collected = executor._collect_role(
            SimpleNamespace(id='SUP-06'), env, bound, 'normal',
            role_inputs={'normal': (lifecycle_target, base_prepared, sha256_hex(lifecycle_path.read_bytes()))})
    role = collected['row']
    assert role['worker_shard_input']['native_rank_witnesses'] == witnesses
    assert role['worker_shard_input']['callback_source_sha256'] == source_sha256
    assert role['worker_shard_input']['reported_source']['owner_binding_verified'] is False
    assert role['collected'] is role['sealed'] is False

    if not assignments_verified:
        return

    for path in (target_path, prepared_path):
        original = path.read_bytes()
        path.write_bytes(original + b'\n')
        with pytest.raises(ValueError, match='^B_WORKER_SHARD_CALLBACK_SCOPE_MISMATCH$'):
            executor._worker_shard_input(env, bound, 'normal', lifecycle_target, sha256_hex(lifecycle_path.read_bytes()))
        path.write_bytes(original)

    legacy = {key: item for key, item in fragment.items() if key != 'callback_source_sha256'}
    old_executor = KubernetesSupplyBootExecutor({'normal': {'worker_shard_input': legacy}}, runner=forbidden)
    old_record, old_scope, old_result = old_executor._worker_shard_input(
        env, bound, 'normal', lifecycle_target, sha256_hex(lifecycle_path.read_bytes()))
    assert (old_record, old_scope) == (record, scope)
    assert old_result == {key: item for key, item in result.items() if key != 'callback_source_sha256'}

    assert len(capture['raw_page_refs']) >= 2
    page = root / 'callback-pages' / capture['raw_page_refs'][0]['path']
    page_raw = page.read_bytes()
    assert sha256_hex(page_raw) == capture['raw_page_refs'][0]['sha256']
    page.write_bytes(page_raw + b'\n')
    with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_SOURCE_CHANGED$'):
        handoff.prepare(selection_path, selection_sha, root / 'changed-page')
    with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_SOURCE_CHANGED$'):
        executor._worker_shard_input(env, bound, 'normal', lifecycle_target, sha256_hex(lifecycle_path.read_bytes()))
    assert not (root / 'changed-page').exists()
    page.write_bytes(page_raw)

    with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_PAGES_INVALID$'):
        handoff.prepare(selection_path, write_selection(with_archive=False), root / 'missing-page-archive')
    assert not (root / 'missing-page-archive').exists()

    repeated_page = deepcopy(capture)
    repeated_page['raw_page_refs'][1]['sha256'] = repeated_page['raw_page_refs'][0]['sha256']
    capture_path.write_bytes(canonical_json(repeated_page))
    with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_PAGES_INVALID$'):
        handoff.prepare(selection_path, write_selection(), root / 'repeated-page')
    assert not (root / 'repeated-page').exists()

    other_rank = deepcopy(capture)
    other_rank['records']['worker-shard-load']['record']['bindings'][0]['rank'] += 1
    capture_path.write_bytes(canonical_json(other_rank))
    with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_PAGE_PROJECTION_MISMATCH$'):
        handoff.prepare(selection_path, write_selection(), root / 'other-rank')
    assert not (root / 'other-rank').exists()

    capture_path.write_bytes(canonical_json({**capture, 'raw_page_refs': []}))
    prior = handoff.prepare(selection_path, write_selection(with_archive=False), root / 'unarchived-handoff')
    assert prior['worker_shard_input']['sha256'] == fragment['sha256']
    assert 'capture_archive' not in prior['worker_shard_input']
    assert 'callback_source_sha256' not in prior['worker_shard_input']
    assert 'native_rank_witnesses' not in prior
    assert prior['source_ended'] is prior['owner_binding_verified'] is prior['producer_authenticated'] is False
    capture_path.write_bytes(capture_raw)

    previous_epoch = deepcopy(capture)
    previous_epoch['processes'][0]['start_ticks'] += 1
    capture_path.write_bytes(canonical_json(previous_epoch))
    with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_PREFIX_MISMATCH$'):
        handoff.prepare(selection_path, write_selection(), root / 'other-process-epoch')
    assert not (root / 'other-process-epoch').exists()
    capture_path.write_bytes(capture_raw)
    assert write_selection() == selection_sha

    capture_path.write_bytes(capture_raw + b'\n')
    with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_SOURCE_CHANGED$'):
        handoff.prepare(selection_path, selection_sha, root / 'changed')
    assert not (root / 'changed').exists()
    capture_path.write_bytes(capture_raw)

    validate = handoff.common_consumption.validate_record

    def replace_after_validation(*args, **kwargs):
        result = validate(*args, **kwargs)
        capture_path.write_bytes(capture_raw + b'\n')
        return result

    with monkeypatch.context() as edit:
        edit.setattr(handoff.common_consumption, 'validate_record', replace_after_validation)
        with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_SOURCE_CHANGED$'):
            handoff.prepare(selection_path, selection_sha, root / 'late-change')
    assert not (root / 'late-change').exists()
    capture_path.write_bytes(capture_raw)
    lifecycle_target['kubernetes']['engine']['restart_count'] += 1
    lifecycle_path.write_bytes(canonical_json(lifecycle_target))
    with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_OWNER_MISMATCH$'):
        handoff.prepare(selection_path, write_selection(), root / 'other-owner')
    assert not (root / 'other-owner').exists()
