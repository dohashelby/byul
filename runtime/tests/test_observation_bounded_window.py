import pytest
import json
import os
from serving_runtime.enforcement.events import ObservationQuery


def test_normal_observation_poll_uses_only_bounded_tail_and_marks_partial_counts(tmp_path,monkeypatch):
    query=ObservationQuery(tmp_path/'observations.jsonl');query.store.check_writable()
    with query.store._lock,query.store._open(__import__('os').O_WRONLY|__import__('os').O_APPEND) as stream:
        stream.write((b'{"source":"hubble","kind":"network_flow","observed_at":"2026-11-08T10:00:00Z","data_origin":"real","padding":"'+b'x'*1000+b'"}\n')*5000)
    query.store.append(dict(source='engine_health',kind='engine_recovered',observed_at='2026-11-08T10:00:01Z',data_origin='real'))
    original=query.store.path.stat().st_size
    monkeypatch.setattr(query.store,'read_all',lambda:pytest.fail('full journal read on normal poll'))
    result=query.query({'source':'engine_health'})
    assert result['window']['truncated'] and result['window']['counts_lower_bound']
    assert result['counts']['total']==1 and result['observations'][0]['kind']=='engine_recovered'
    assert query.store.path.stat().st_size==original


def test_grouped_rules_reserve_ten_each_in_one_protected_tail_read(tmp_path, monkeypatch):
    query = ObservationQuery(tmp_path / 'observations.jsonl')
    query.store.check_writable()
    rows = [dict(source='tetragon', rule_id='TG-SECRET-01', observed_at='0000')]
    rows += [dict(source='tetragon', rule_id='TG-CODE-01', observed_at=f'{n:04d}') for n in range(1, 61)]
    rows += [dict(source='tetragon', rule_id='TG-EXEC-01', observed_at=f'{n:04d}') for n in range(61, 3000)]
    with query.store._open(os.O_WRONLY | os.O_APPEND) as stream:
        stream.write(''.join(json.dumps(r) + '\n' for r in rows).encode())
    read = query.store.read_recent
    calls = []
    def recent():
        calls.append(True)
        return read()
    monkeypatch.setattr(query.store, 'read_recent', recent)
    filters = {'source': 'tetragon', 'rule_ids': 'TG-CODE-01,TG-SECRET-01'}
    result = query.query(filters)
    assert len(calls) == 1 and 'rule_ids' in filters
    assert result['counts']['total'] == 61
    assert len(result['observations']) == 11
    assert result['observations'][-1]['rule_id'] == 'TG-SECRET-01'
    assert [r['observed_at'] for r in result['observations'][:-1]] == [f'{n:04d}' for n in range(60, 50, -1)]
    assert result['window']['max_bytes'] == 4 * 1024 * 1024
    assert result['window']['max_records'] == 4096


@pytest.mark.parametrize('filters', [
    {'source': 'hubble', 'rule_ids': 'TG-CODE-01'},
    {'source': 'tetragon', 'rule_ids': ''},
    {'source': 'tetragon', 'rule_ids': 'TG-CODE-01,TG-CODE-01'},
    {'source': 'tetragon', 'rule_ids': 'TG-CODE-01', 'rule_id': 'TG-CODE-01'},
    {'source': 'tetragon', 'rule_ids': 'TG-CODE-01,invalid'},
    {'source': 'tetragon', 'rule_ids': ','.join(f'TG-TEST-{n:02d}' for n in range(6))},
])
def test_invalid_rule_groups_fail_before_read(tmp_path, monkeypatch, filters):
    query = ObservationQuery(tmp_path / 'observations.jsonl')
    monkeypatch.setattr(query.store, 'read_recent', lambda: pytest.fail('invalid filter read journal'))
    with pytest.raises(ValueError, match='rule_ids'):
        query.query(filters)
