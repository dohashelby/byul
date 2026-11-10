import pytest
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
