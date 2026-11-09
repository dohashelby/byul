"""Actual pinned library reads -> SUP-03 parser support; native scope remains open."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.collectors import auxiliary_supply_producer as producer
from guard.adapters import vllm_auxiliary_read as observer

FIXTURE = Path(__file__).parent / 'fixtures/auxiliary_supply_k3d_20261108.json'


def captured():
    return json.loads(FIXTURE.read_text())


def test_actual_read_values_bind_to_three_successful_loader_results():
    value = captured()
    result = producer.project(value)
    assert value['data_origin'] == 'EXAMPLE'
    assert result['parser_scope_complete'] is True and result['errors'] == []
    assert {r['role'] for r in result['parser_reads']} == set(producer.ROLES)
    assert all(r['file_bytes'] > 0 and r['parser_input_bytes'] > 0 for r in result['parser_reads'])
    assert all(r['file_sha256'] == r['parser_input_sha256'] for r in result['parser_reads'])
    assert result['native_worker_consumption_verified'] is False and not result['producer_authenticated']
    assert 'AUXILIARY_NATIVE_TOKENIZER_READS_UNOBSERVED' in result['missing']


def test_missing_policy_and_explicit_empty_lab_list_have_distinct_meanings():
    value = captured()
    assert producer.project(value)['policy_supplied'] is False
    assert producer.project(value)['violation_candidates'] == []
    result = producer.project(value, allowed_sha256=[])
    assert result['policy_supplied'] is True and len(result['violation_candidates']) == len(result['parser_reads'])
    allowed = [r['file_sha256'] for r in result['parser_reads']]
    assert producer.project(value, allowed_sha256=allowed)['violation_candidates'] == []


def test_excluding_the_actual_config_bytes_preserves_an_independent_read_candidate_after_collection_error():
    value = captured()
    allowed = [r['file_sha256'] for r in value['events'] if r.get('role') != 'config' and r['phase'] == 'READ_RETURN']
    value['source_ended'] = False
    value['diagnostic_codes'] = ['AUXILIARY_OBSERVATION_UNAVAILABLE']
    result = producer.project(value, allowed_sha256=allowed)
    assert not result['parser_scope_complete']
    assert [v['role'] for v in result['violation_candidates']] == ['config']
    assert 'AUXILIARY_SOURCE_INCOMPLETE' in result['errors']


@pytest.mark.parametrize('damage', ['session', 'pid', 'source', 'extra', 'unstable', 'unqualified', 'missing-call', 'different-role', 'duplicate-result'])
def test_changed_source_scope_read_or_result_is_not_complete(damage):
    value = captured()
    row = value['events'][0]
    if damage == 'session':
        row['session'] = 'f' * 32
    elif damage == 'pid':
        row['pid'] += 1
    elif damage == 'source':
        row['source_sha256'] = 'f' * 64
    elif damage == 'extra':
        row['payload'] = 'not accepted'
    elif damage == 'unstable':
        row['stable'] = False
    elif damage == 'unqualified':
        row['qualified'] = False
    elif damage == 'missing-call':
        value['results'][0]['read_calls'] = ['f' * 32]
    elif damage == 'different-role':
        value['results'][0]['read_calls'] = value['results'][1]['read_calls']
    else:
        value['results'].append(deepcopy(value['results'][0]))
    if damage == 'duplicate-result':
        with pytest.raises(ValueError, match='AUXILIARY_ENVELOPE_INVALID'):
            producer.project(value)
    else:
        result = producer.project(value)
        assert not result['parser_scope_complete'] and result['errors']


def test_failed_read_has_no_successful_consumption_but_does_not_erase_other_reads():
    value = captured()
    row = value['events'][0]
    common = {k: v for k, v in row.items() if k not in {'stable', 'qualified', 'complete', 'file_bytes', 'file_sha256', 'parser_input_bytes', 'parser_input_sha256'}}
    value['events'][0] = dict(common, phase='READ_RAISE')
    result = producer.project(value, allowed_sha256=[])
    assert not result['parser_scope_complete']
    assert 'AUXILIARY_LOADER_READ_FAILED' in result['errors']
    assert all(r['call'] != row['call'] for r in result['parser_reads'])
    assert result['violation_candidates']


def test_producer_drift_and_open_envelopes_are_rejected():
    for damage in ('producer', 'payload', 'grade'):
        value = captured()
        if damage == 'producer':
            value['producer']['source_sha256'] = 'f' * 64
        elif damage == 'payload':
            value['payload'] = 'not accepted'
        else:
            value['data_origin'] = 'MEASURED'
        with pytest.raises(ValueError, match='AUXILIARY_ENVELOPE_INVALID'):
            producer.project(value)


@pytest.mark.parametrize('value', ['', 'relative/model', '/model/../escape'])
def test_capture_paths_are_explicit_local_paths(value):
    with pytest.raises(ValueError, match='AUXILIARY_PATH_INVALID'):
        producer.capture_args(value, '/template.jinja')


def test_text_parser_input_and_file_bytes_keep_distinct_hashes(tmp_path):
    path = tmp_path / 'text.json'
    path.write_bytes(b'{"x": 1}\r\n')
    source = observer.AuxiliaryReadObserver([str(tmp_path)], session='a' * 32)
    source._qualify = lambda *args: True
    with path.open() as stream:
        proxy = observer._ReadProxy(source, None, None, str(path), stream, {})
        assert proxy.read() == '{"x": 1}\n'
    row, = source.events
    assert row['complete'] and row['file_sha256'] != row['parser_input_sha256']


def test_observation_failure_preserves_the_original_read(tmp_path, monkeypatch):
    path = tmp_path / 'text.json'
    path.write_text('original')
    source = observer.AuxiliaryReadObserver([str(tmp_path)], session='a' * 32)
    with path.open() as stream:
        proxy = observer._ReadProxy(source, None, None, str(path), stream, {})
        monkeypatch.setattr(observer.os, 'fstat', lambda *args: (_ for _ in ()).throw(OSError('observation only')))
        assert proxy.read() == 'original'
    assert 'AUXILIARY_OBSERVATION_UNAVAILABLE' in source.gaps


def test_malformed_late_row_preserves_earlier_exclusion_candidates():
    value = captured()
    damaged = deepcopy(value['events'][0]); damaged['point'] = ['invalid']
    value['events'].append(damaged)
    result = producer.project(value, allowed_sha256=[])
    assert 'AUXILIARY_ROW_INVALID' in result['errors'] and result['violation_candidates']
