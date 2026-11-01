"""Explicit LIF04 policy; no inferred memory/deadline/debug allowance."""
from datetime import datetime
import re
from guard.runtime.kubernetes_stream_buffer import normalize_read_profiles

PROFILE='kubernetes-stream-buffer-observation-v1'
POLICY_TYPES={'buffer_environment_sha256':'str','buffer_target_sha256':'str','buffer_prepared_sha256':'str',
 'buffer_validity_start':'str','buffer_validity_end':'str','buffer_max_write_bytes':'int','buffer_max_process_rss_bytes':'int',
 'buffer_max_send_wait_ms':'int','buffer_release_deadline_ms':'int','buffer_read_chunk_bytes':'int','buffer_read_interval_ms':'int','buffer_read_max_reads':'int',
 'buffer_close_chunk_bytes':'int','buffer_close_interval_ms':'int','buffer_close_max_reads':'int','buffer_debug_mode':'str'}
OBSERVATIONS={'buffer_http_trials':'list','buffer_native_samples':'list','buffer_cases':'list','buffer_missing':'list','buffer_conflict_candidates':'list',
 'buffer_producer_authenticated':'bool','buffer_inventory_complete':'bool','buffer_release_verified':'bool','buffer_scope_complete':'bool'}


def stamp(value):
    if type(value) is not str or len(value)>40:raise ValueError
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:raise ValueError
    return result


def normalize_policy(v):
    try:
        if type(v) is not dict or set(v)!=set(POLICY_TYPES) or v['buffer_debug_mode']!='unverified':raise ValueError
        if any(type(v[k]) is not str or re.fullmatch('[0-9a-f]{64}',v[k]) is None for k in POLICY_TYPES if k.endswith('_sha256')):raise ValueError
        start,end=stamp(v['buffer_validity_start']),stamp(v['buffer_validity_end'])
        if not 0<(end-start).total_seconds()<=600:raise ValueError
        for key in ('buffer_max_write_bytes','buffer_max_process_rss_bytes','buffer_max_send_wait_ms','buffer_release_deadline_ms'):
            if type(v[key]) is not int or not 0<=v[key]<2**63:raise ValueError
        profiles=normalize_read_profiles({'normal':None,
            'slow':{'profile':'slow-reader-v1','chunk_bytes':v['buffer_read_chunk_bytes'],'interval_ms':v['buffer_read_interval_ms'],'close_after_reads':v['buffer_read_max_reads']},
            'slow-close':{'profile':'slow-reader-v1','chunk_bytes':v['buffer_close_chunk_bytes'],'interval_ms':v['buffer_close_interval_ms'],'close_after_reads':v['buffer_close_max_reads']}})
        return start,end,profiles
    except (KeyError,TypeError,ValueError):raise ValueError('STREAM_BUFFER_POLICY_INVALID') from None
