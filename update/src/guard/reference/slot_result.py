"""Historical slot result reader, independent of checkout-only producer tools."""
from datetime import datetime, timezone
import hashlib, json, re
from guard.models import canonical_json
SOURCE_COMMIT = 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
COLLECTOR_SHA256 = '60b46a9b877a21e6611c4ec49856345dde9b023c9c7171e6e45ef7b8a78f9baf'
CALLBACK_SHA256 = '659d615f133cdc96ef54163678753a60e6b8ee51aa0e78e0560e0d308f7f50da'
_CASES = ('standalone-b', 'condense-reuse', 'swap')

def native_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()

def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None

def _date(value):
    try:
        value = datetime.fromisoformat(value)
        return value.astimezone(timezone.utc) if value.tzinfo is not None else None
    except (TypeError, ValueError):
        return None

def _cpu_result(record, *, collector_sha256=COLLECTOR_SHA256, callback_sha256=CALLBACK_SHA256):
    """Strict source-invariant evidence; never infer a mismatch from output alone."""
    if (not _sha(collector_sha256) or not _sha(callback_sha256) or not isinstance(record, dict)
            or record.get('schema') != 'byul-req18-slot-cpu-capture/1' or record.get('source_profile') != 'target031'
            or record.get('source_commit') != SOURCE_COMMIT
            or record.get('own_producer_sha256') != collector_sha256
            or record.get('native_callback_sha256') != callback_sha256):
        return None
    started, finished = _date(record.get('capture_started_at')), _date(record.get('capture_finished_at'))
    if (type(record.get('collection_id')) is not str or re.fullmatch('req18-slot-cpu-[0-9a-f]{32}', record['collection_id']) is None
            or started is None or finished is None or finished < started):
        return None
    validation = record.get('source_validation', {})
    post = validation.get('post', {})
    if validation.get('pre') != 'verified':
        return None
    rows = record.get('results')
    if (type(rows) is not list or not 1 <= len(rows) <= 3 or any(not isinstance(r, dict) for r in rows)
            or len({r.get('case_id') for r in rows}) != len(rows)
            or not {r.get('case_id') for r in rows} <= set(_CASES)
            or 'standalone-b' not in {r.get('case_id') for r in rows}):
        return None
    by_case = {r['case_id']: r for r in rows}
    standalone = by_case['standalone-b']
    details, collected, mismatch = [], False, False
    for key in _CASES:
        if key not in by_case:
            continue
        row = by_case[key]
        safe = {k: row[k] for k in ('constraint_sha256', 'logits_sha256', 'host_mask_sha256', 'metadata_mask_sha256',
            'sampling_metadata_token', 'sampling_mask_token', 'sampled_ids_sha256', 'standalone_output_sha256', 'standalone_mask_sha256') if _sha(row.get(k))}
        safe.update({k: None for k in ('host_mask_sha256', 'metadata_mask_sha256', 'standalone_mask_sha256',
            'host_masked_count', 'metadata_masked_count', 'sampling_mask_token') if k in row and row[k] is None})
        safe.update({k: row[k] for k in ('host_mask_status', 'metadata_mask_status') if row.get(k) in {'absent', 'collected', 'unavailable'}})
        if row.get('mask_comparison_basis') == 'effective_unrestricted_branches':
            safe['mask_comparison_basis'] = row['mask_comparison_basis']
        safe.update({k: row[k] for k in ('current_b_unrestricted', 'slot_reused', 'mask_matches_standalone',
            'output_matches_standalone', 'sampler_returned', 'metadata_argument_matches_refresh',
            'restricted_request_preserved', 'metadata_mask_present', 'effective_unrestricted') if type(row.get(k)) is bool})
        safe.update({k: row[k] for k in ('host_masked_count', 'metadata_masked_count', 'sampled_count')
                     if type(row.get(k)) is int and row[k] >= 0})
        row_validation = row.get('source_validation', {})
        qualified = (row_validation.get('pre') == 'verified' and row_validation.get('post', {}).get('status') == 'verified'
            and standalone.get('collected') is True and standalone.get('sampler_returned') is True
            and standalone.get('source_validation', {}).get('post', {}).get('status') == 'verified'
            and row.get('collected') is True and row.get('current_b_unrestricted') is True
            and row.get('constraint_sha256') == native_digest(None) and row.get('sampler_returned') is True
            and row.get('metadata_argument_matches_refresh') is True and _sha(row.get('sampling_metadata_token'))
            and (_sha(row.get('sampling_mask_token')) if row.get('metadata_mask_present') is True else
                 row.get('sampling_mask_token') is None and row.get('metadata_mask_status') == 'absent')
            and _sha(row.get('logits_sha256')) and row.get('logits_sha256') == standalone.get('logits_sha256')
            and row.get('sampled_count') == 1 and _sha(row.get('sampled_ids_sha256'))
            and row.get('standalone_output_sha256') == standalone.get('sampled_ids_sha256')
            and row.get('standalone_mask_sha256') == standalone.get('host_mask_sha256'))
        safe['source_validation'] = {'pre': row_validation.get('pre') if row_validation.get('pre') == 'verified' else 'unavailable',
            'post': {'status': row_validation.get('post', {}).get('status') if row_validation.get('post', {}).get('status') in {'verified', 'mismatch', 'unavailable'} else 'unavailable'},
            'boundary': 'source_component'}
        if key != 'standalone-b':
            qualified = qualified and row.get('slot_reused') is True
        # A stale CPU host row is consequential only if the real Sampler received
        # that mismatched mask and its identical-logits output changed as well.
        failure = bool(qualified and key != 'standalone-b' and type(row.get('host_masked_count')) is int
            and row['host_masked_count'] > 0 and type(row.get('metadata_masked_count')) is int and row['metadata_masked_count'] > 0 and _sha(row.get('host_mask_sha256'))
            and row.get('metadata_mask_sha256') == row['host_mask_sha256']
            and row.get('host_mask_sha256') != standalone.get('host_mask_sha256')
            and row['sampled_ids_sha256'] != standalone.get('sampled_ids_sha256')
            and row.get('mask_matches_standalone') is False and row.get('output_matches_standalone') is False)
        mismatch |= failure
        if key != 'standalone-b':
            collected |= bool(qualified)
        details.append({'case_id': key, 'qualified': bool(qualified), 'mismatch': failure, **safe})
    return {'mode': 'cpu', 'rows': details, 'collected': collected, 'mismatch': mismatch,
            'complete': len(details) == 3 and record.get('capture_status') == 'completed' and post.get('status') == 'verified' and all(r['qualified'] for r in details),
            'collection_id': record.get('collection_id'), 'started_at': record.get('capture_started_at'),
            'finished_at': record.get('capture_finished_at'), 'installed_target': None,
            'gpu_execution_verified': False, 'full_live_coverage': False,
            'source_commit': SOURCE_COMMIT, 'collector_sha256': collector_sha256, 'callback_sha256': callback_sha256,
            'source_validation': {'pre': 'verified', 'post': {'status': post.get('status') if post.get('status') in {'verified', 'unavailable', 'mismatch'} else 'unavailable'}, 'boundary': 'source_component'}}
