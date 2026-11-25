"""Replay an ordinary writer's closed bytes without closing the product's scope."""

from base64 import b64encode
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.adapters import observer_runtime, vllm_engine_callback_sensor as sensor
from guard.collectors import engine_callbacks, worker_shard_producer
from guard.executors.worker_shard_archive import (
    CAPTURE_SCHEMA, captured_process_epochs, mounted_source_expectation, verify_saved_pages,
)
from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.callback_pages import assemble
from tests.test_callback_paging_runtime import reader
from tests.test_worker_shard import POD, SCOPE, case


@pytest.fixture
def captured_writer(tmp_path, monkeypatch):
    prepared = engine_callbacks.prepare_assets(tmp_path / 'assets', worker_shards=True)
    session = prepared['session']
    session_sha256 = sha256_hex(session.encode())
    data = tmp_path / 'original-data'
    installed = []
    monkeypatch.setattr(observer_runtime.JournalSink, 'SEGMENT_BYTES', 4096)
    monkeypatch.setattr(sensor, 'sys', SimpleNamespace(meta_path=installed))
    monkeypatch.setattr(sensor, 'atexit', SimpleNamespace(register=lambda _callback: None))
    assert sensor.install(root=tmp_path / 'assets', data_root=data)
    callbacks = installed[0].callbacks
    context = callbacks.shard.context
    pid, ticks = context['pid'], context['start_ticks']
    rows = deepcopy(case('sharded')['rows'])
    try:
        for row in rows:
            if 'session_sha256' in row:
                row['session_sha256'] = session_sha256
                row['fields'].update(pid=pid, start_ticks=ticks)
            row['process_sha256'] = callbacks.shard.process_sha256
            callbacks.sink(row)
    finally:
        callbacks.sink.close()
    root = data / session
    page = reader()(root, capture_id='1' * 32, session=session)
    assert page['done'] is True
    marker = root / f'callbacks-{pid}.terminal.json'
    raw = marker.read_bytes()
    assert len(page['journal_terminals']) == 1
    return page, raw, {**SCOPE, 'session': session}


def _replay(tmp_path, page, scope, *, version='0.31.0', mounted_source=None, mounted_receipt=None,
            expected_ranks=range(2), require_finalized=False):
    archive = tmp_path / 'pages'
    captured = assemble([page], archive=archive)
    processes = captured['processes']
    record, _ = worker_shard_producer.record_from_processes(processes, pod=POD, **scope)
    wrapper = {'schema': CAPTURE_SCHEMA, 'session': scope['session'], 'raw_page_refs': captured['page_refs'],
               'processes': [{'pid': item['pid'], 'start_ticks': item['start_ticks'], 'rows': len(item['events'])}
                             for item in processes]}
    if mounted_receipt is not None:
        wrapper.update(truncated=mounted_receipt['collection_truncated'], mounted_source_attestation=mounted_receipt)

    def document(path, digest, limit):
        raw = Path(path).read_bytes()
        assert sha256_hex(raw) == digest
        return path, strict_json(raw, limit=limit)

    before = canonical_json(record)
    result = verify_saved_pages(wrapper, archive, document, record=record, epochs=captured_process_epochs(wrapper),
                                pod=POD, scope=scope, version=version, expected_ranks=expected_ranks,
                                mounted_source=mounted_source, require_finalized=require_finalized)
    assert canonical_json(record) == before and record['completeness']['source_ended'] is False
    return captured['page_refs'][0]['sha256'], result


def test_retains_exact_rank_terminal_marker_but_keeps_source_open(tmp_path, captured_writer):
    page, raw, scope = captured_writer
    reference, result = _replay(tmp_path, page, scope)
    rank, = result['ranks']
    assert result['expected_ranks'] == [0, 1] and rank['rank'] == 0
    assert {'load_call', 'load_return', 'ready_candidate_call'} <= rank.keys()
    assert set(page['gaps']) == {'CALLBACK_CHECKPOINT_UNAVAILABLE'}
    assert result['page_diagnostic_codes'] == ['CALLBACK_CHECKPOINT_UNAVAILABLE']
    segments = json.loads(raw)['segments']
    assert len(segments) >= 2
    assert rank['journal_terminal'] == {
        'marker_sha256': sha256_hex(raw), 'page_sha256': reference,
        'written_rows': len(page['rows']), 'segment_count': len(segments),
    }


@pytest.mark.parametrize('damage', ['missing-marker', 'marker-digest', 'row-digest', 'partial-prefix',
                                    'other-session', 'other-process', 'noncanonical-marker',
                                    'invalid-unicode-marker', 'wrong-release'])
def test_unverified_terminal_does_not_extend_the_saved_prefix(tmp_path, captured_writer, damage):
    page, raw, scope = captured_writer
    if damage == 'missing-marker':
        del page['journal_terminals']
    elif damage == 'marker-digest':
        page['journal_terminals'][0]['raw_sha256'] = '0' * 64
    elif damage == 'row-digest':
        page['rows'][-1]['raw_sha256'] = '0' * 64
    elif damage == 'partial-prefix':
        row = page['rows'].pop()
        page['cursor'][row['file']] = row['offset']
        page['done'] = False
    elif damage in {'other-session', 'other-process', 'noncanonical-marker', 'invalid-unicode-marker'}:
        marker = json.loads(raw)
        if damage == 'noncanonical-marker':
            changed = b' ' + raw
        elif damage == 'invalid-unicode-marker':
            marker['schema'] = chr(0xD800)
            changed = (json.dumps(marker, sort_keys=True, separators=(',', ':')) + '\n').encode()
        else:
            key = 'session_sha256' if damage == 'other-session' else 'process_sha256'
            marker[key] = sha256_hex(damage.encode())
            changed = canonical_json(marker) + b'\n'
        page['journal_terminals'][0] = {'raw_sha256': sha256_hex(changed),
                                        'raw_b64': b64encode(changed).decode('ascii')}
    elif damage == 'wrong-release':
        with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_RELEASE_SOURCE_MISMATCH$'):
            _replay(tmp_path, page, scope, version='0.30.0')
        return
    _, result = _replay(tmp_path, page, scope)
    rank, = result['ranks']
    assert 'load_return' in rank and 'journal_terminal' not in rank


def _mount(tmp_path, page, scope, *, origin='EXAMPLE', truncated=True):
    from guard.runtime.engine_http import NORMAL_CALLBACK_ATTESTATION_SCHEMA, NORMAL_CALLBACK_SOURCE_SCHEMA

    prepared = engine_callbacks.load_prepared(tmp_path / 'assets')
    manifest_sha256 = sha256_hex((tmp_path / 'assets' / 'manifest.json').read_bytes())
    plan = {'manifest_sha256': manifest_sha256, 'session_sha256': sha256_hex(scope['session'].encode()),
            'assets_sha256': sha256_hex(canonical_json(prepared['assets']))}
    target, binding = {'normal_tp2_callbacks': plan}, {'engine': POD}
    pins = {'target': sha256_hex(canonical_json(target)), 'prepared': manifest_sha256}
    scope = {**scope, 'target_sha256': sha256_hex(canonical_json(target))}
    mounted_source = mounted_source_expectation(target, prepared, binding, pins)
    observation = {'schema': NORMAL_CALLBACK_SOURCE_SCHEMA, 'uid': 2000, 'gid': 0,
                   'file_count': len(prepared['assets']) + 2, 'files_sha256': 'a' * 64,
                   'filesystem_sha256': 'b' * 64}
    receipt = {
        'schema': NORMAL_CALLBACK_ATTESTATION_SCHEMA, 'data_origin': origin,
        'target_sha256': scope['target_sha256'], 'target_file_sha256': pins['target'],
        'environment_ref': scope['environment_ref'], 'plan_sha256': sha256_hex(canonical_json(plan)),
        'source_sha256': mounted_source['source_sha256'], 'owned_generation_sha256': sha256_hex(canonical_json(binding)),
        'observed_pages': 1, 'observation': observation.copy(), 'collection_truncated': truncated,
    }
    page['normal_callback_sources'] = observation
    return scope, mounted_source, receipt, observation


@pytest.mark.parametrize('damage', (None, 'missing', 'target-pin', 'generation', 'reader', 'count', 'raw-page',
                                    'truncation'))
def test_saved_worker_capture_requires_matching_mounted_source_receipt(tmp_path, captured_writer, damage):
    page, _, scope = captured_writer
    scope, mounted_source, receipt, observation = _mount(tmp_path, page, scope)
    if damage == 'missing':
        receipt = None
    elif damage == 'target-pin':
        receipt['target_file_sha256'] = 'd' * 64
    elif damage == 'generation':
        receipt['owned_generation_sha256'] = 'd' * 64
    elif damage == 'reader':
        receipt['source_sha256'] = 'd' * 64
    elif damage == 'count':
        receipt['observed_pages'] = 0
    elif damage == 'raw-page':
        observation['filesystem_sha256'] = 'd' * 64
    elif damage == 'truncation':
        receipt['collection_truncated'] = False

    if damage is not None:
        with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_MOUNT_RECEIPT_MISMATCH$'):
            _replay(tmp_path, page, scope, mounted_source=mounted_source, mounted_receipt=receipt)
        return
    _, witnesses = _replay(tmp_path, page, scope, mounted_source=mounted_source, mounted_receipt=receipt)
    assert witnesses['mounted_source_attestation'] == {**receipt, 'archived_pages': 1}
    assert witnesses['page_diagnostic_codes'] == ['CALLBACK_CHECKPOINT_UNAVAILABLE']


@pytest.mark.parametrize('defect', [None, 'real-gap', 'missing-terminal', 'missing-rank', 'example'])
def test_required_finalization_needs_ungapped_owned_archive_and_all_rank_terminals(tmp_path, captured_writer, defect):
    page, _, scope = captured_writer
    if defect != 'real-gap':
        # Model the future gap-free collector. The retained terminal was written by the actual test JournalSink.
        page['gaps'] = []
    if defect == 'missing-terminal':
        del page['journal_terminals']
    origin = 'EXAMPLE' if defect == 'example' else 'MEASURED'
    scope, mounted_source, receipt, _ = _mount(tmp_path, page, scope, origin=origin, truncated=defect == 'real-gap')
    ranks = range(2 if defect == 'missing-rank' else 1)
    options = dict(mounted_source=mounted_source, mounted_receipt=receipt, expected_ranks=ranks,
                   require_finalized=True)
    if defect is not None:
        with pytest.raises(ValueError, match='^MVP_SUP06_WORKER_SHARD_FINALIZATION_UNVERIFIED$'):
            _replay(tmp_path, page, scope, **options)
        return
    _, result = _replay(tmp_path, page, scope, **options)
    assert result['expected_ranks'] == [0]
    assert result['ranks'][0]['journal_terminal']['written_rows'] == len(page['rows'])
    assert 'page_diagnostic_codes' not in result
