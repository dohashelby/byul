"""Observed immutable live prefixes; no sealed session or durable-write claim."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json

SCHEMA = 'guard-native-checkpoint-ack/1'


def canonical(row):
    return json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode() + b'\n'


def marker(row, session):
    payload = row.get('payload') or {}
    return (row.get('event') == 'CHECKPOINT' and type(payload) is dict
            and set(payload) == {'schema', 'session', 'prefix_seq', 'prefix_sha256'}
            and payload['schema'] == SCHEMA and payload['session'] == session
            and type(payload['prefix_seq']) is int and payload['prefix_seq'] == row.get('seq')
            and type(payload['prefix_sha256']) is str and payload['prefix_sha256'] == row.get('previous_sha256'))


def prefix(records, summary, session):
    if (summary.get('recording_end') is not None or summary.get('incomplete_final_row') is not False
            or summary.get('discarded_tail_bytes') != 0):
        return None
    # A visible observer failure is retained even if a marker predates it.
    if any(r['event'] == 'SG_GAP' or r['payload'].get('phase') == 'OBSERVER_GAP' for r in records):
        return None
    checkpoints = [index for index, row in enumerate(records) if marker(row, session)]
    if not checkpoints:
        return None
    rows = records[:checkpoints[-1] + 1]
    raw = b''.join(canonical(r) for r in rows)
    return rows, raw, {
        'schema': 'guard-live-lifecycle-checkpoint-prefix/1', 'session': session,
        'seq': rows[-1]['seq'], 'monotonic_ns': rows[-1]['monotonic_ns'],
        'prefix_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
        'observed_prefix_only': True, 'durability_verified': False,
    }


def census(metadata):
    value = deepcopy(metadata)
    # An append changes these three file fields. File identity/owner/mode and
    # the complete process inventory remain exact; new files still change it.
    for row in value.get('files', []):
        for key in ('bytes', 'mtime_ns', 'ctime_ns'):
            row.pop(key, None)
    return value


def covered(packet):
    before, after = packet.get('before') or {}, packet.get('after') or {}
    if (packet.get('capture_mode') not in {'live_checkpoint','live_checkpoint_rolling'}
            or packet.get('live_census_stable') is not True
            or packet.get('recorded_files_covered') is not True
            or packet.get('python_processes_without_recording')
            or not before.get('owner') or before.get('owner') != after.get('owner')
            or before.get('owner_stable') is not True or after.get('owner_stable') is not True
            or census(before.get('metadata', {})) != census(after.get('metadata', {}))):
        return False
    from .lifecycle_session import parse_inventory
    rolling=packet.get('capture_mode')=='live_checkpoint_rolling'
    if rolling:
        from .lifecycle_rolling import parse_inventory
    for side in (before, after):
        meta = side.get('metadata') or {}
        try:parse_inventory(json.dumps(meta).encode(), packet.get('session'))
        except (ValueError,TypeError,KeyError):return False
        if (meta.get('sealed') is not False or meta.get('directory_stable') is not True
                or meta.get('unknown_entries') != 0 or meta.get('files_truncated') is not False
                or meta.get('processes_truncated') is not False or meta.get('unreadable_pids') != []):
            return False
    actual = set()
    for item in packet.get('recordings', []):
        rows = (item.get('capture') or {}).get('records') or []
        if not rows:return False
        context = rows[0]['payload']['context']
        actual.add((context.get('pid'), context.get('start_ticks'), item.get('segment')))
    files = {(r['pid'], r['start_ticks'],r.get('segment')) for r in before['metadata']['files']}
    python = {(r['pid'], r['start_ticks']) for r in before['metadata']['processes'] if r['same_executable']}
    return bool(actual and actual == files and python <= {(r[0],r[1]) for r in actual})


def recording_covered(item, session, deadline, clock_context=None, packet=None):
    if packet is not None and packet.get('capture_mode')=='live_checkpoint_rolling':
        from .lifecycle_rolling import active_for
        active=active_for(item,packet)
        if active is None:return False
        if active is not item:
            if not all(item.get(k) is True for k in ('session_owner_match','listed_file_identity_match')):return False
            capture=item.get('capture') or {}
            if capture.get('process_context_verified') is not True or capture.get('image_digest_verified') is not True:return False
            return recording_covered(active,session,deadline,clock_context,packet=None)
    capture = item.get('capture') or {}; rows = capture.get('records') or []
    if not rows or not marker(rows[-1], session):
        return False
    result = prefix(rows, capture.get('summary') or {}, session)
    if result is None or result[2] != capture.get('checkpoint_prefix'):
        return False
    tail = capture.get('checkpoint_tail', [])
    if tail:
        previous = hashlib.sha256(canonical(rows[-1])).hexdigest()
        for row in tail:
            if row.get('previous_sha256') != previous:return False
            previous = hashlib.sha256(canonical(row)).hexdigest()
        if hashlib.sha256(b''.join(canonical(r) for r in rows + tail)).hexdigest() != capture.get('checkpoint_tail_sha256'):
            return False
    receipt = capture['checkpoint_prefix']; context = rows[0]['payload']['context']
    if (type(deadline) is not int or rows[-1]['monotonic_ns'] < deadline
            or capture.get('process_context_verified') is not True
            or capture.get('image_digest_verified') is not True
            or item.get('session_owner_match') is not True or item.get('listed_file_identity_match') is not True):
        return False
    if clock_context is not None:
        keys = ('boot_sha256', 'pid_namespace', 'time_namespace', 'time_offsets_sha256')
        if any(context.get(k) != clock_context.get(k) for k in keys):
            return False
    return receipt.get('observed_prefix_only') is True and receipt.get('durability_verified') is False
