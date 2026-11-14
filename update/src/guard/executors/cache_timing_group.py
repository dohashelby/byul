"""REQ-17 두 묶음의 입력·원 표본 결합. 기존 통계 계산과 묶음별 상한을 유지한다."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import os
import math
from pathlib import Path
import re
import stat

from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from . import engine_callback_projection as projection

SCHEMA = 'byul-engine-callback-cohort-group/1'
CAPTURE_SCHEMA = 'guard-cache-timing-part/1'
PARTS = 2
CASES_PER_PART = 504
SAMPLES_PER_ROLE = 100
MAX_PART_BYTES = 1024 * 1024
ROLES = ('control-present', 'control-absent', 'present', 'absent')
_SHA = re.compile('[0-9a-f]{64}')
_BASE = {'schema', 'session', 'environment_ref', 'target_sha256'}


def read_regular(path, *, limit=MAX_PART_BYTES, expected_sha256=None):
    """FIFO·심볼릭 링크·읽는 동안 바뀐 입력을 소비하지 않는다."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('TIMING_GROUP_FILE_INVALID')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError('TIMING_GROUP_FILE_INVALID')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(raw) > limit or len(raw) != before.st_size
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
               (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or expected_sha256 is not None and sha256_hex(raw) != expected_sha256):
        raise ValueError('TIMING_GROUP_FILE_CHANGED')
    return raw


def validate_case_parts(parts):
    if type(parts) is not list or len(parts) != PARTS:
        raise ValueError('TIMING_GROUP_PARTS_INVALID')
    seen = set()
    for cases in parts:
        projection.validate_cases('REQ-17', cases)
        if (len(cases) != CASES_PER_PART or
                Counter(c['role'] for c in cases) != Counter(dict.fromkeys(ROLES, SAMPLES_PER_ROLE), prime=104)):
            raise ValueError('TIMING_GROUP_LAYOUT_INVALID')
        # 파일 안에서는 prime과 대조가 같은 입력을 써야 한다. 다른 묶음의 점수 사례 중복은 거부한다.
        current = {canonical_json({k: v for k, v in c.items() if k != 'case_id'})
                   for c in cases if c['role'] in ROLES}
        if len(current) != len(ROLES) * SAMPLES_PER_ROLE or seen & current:
            raise ValueError('TIMING_GROUP_DUPLICATE_COHORT')
        seen.update(current)


def validate_parts(group, parts):
    if (type(group) is not dict or set(group) != _BASE | {'parts'} or group['schema'] != SCHEMA
            or type(group['parts']) is not list or len(group['parts']) != PARTS
            or type(parts) is not list or len(parts) != PARTS):
        raise ValueError('TIMING_GROUP_MANIFEST_INVALID')
    for index, (entry, part) in enumerate(zip(group['parts'], parts, strict=True), 1):
        if (type(entry) is not dict or set(entry) != {'index', 'file', 'file_sha256', 'manifest_sha256'}
                or type(entry['index']) is not int or entry['index'] != index
                or entry['file'] != f'part-{index}.json'
                or any(type(entry[k]) is not str or not _SHA.fullmatch(entry[k])
                       for k in ('file_sha256', 'manifest_sha256'))
                or type(part) is not dict or set(part) != _BASE | {'cases'}
                or part['schema'] != projection.SCHEMA
                or any(part[k] != group[k] for k in _BASE - {'schema'})
                or sha256_hex(canonical_json(part)) != entry['manifest_sha256']
                or len(canonical_json(part)) > MAX_PART_BYTES):
            raise ValueError('TIMING_GROUP_PART_BINDING_INVALID')
    validate_case_parts([p['cases'] for p in parts])


def load_parts(group, root):
    if type(group) is not dict or type(group.get('parts')) is not list or len(group['parts']) != PARTS:
        raise ValueError('TIMING_GROUP_MANIFEST_INVALID')
    parts = []
    for index, entry in enumerate(group['parts'], 1):
        if (type(entry) is not dict or entry.get('file') != f'part-{index}.json'
                or type(entry.get('file_sha256')) is not str or not _SHA.fullmatch(entry['file_sha256'])):
            raise ValueError('TIMING_GROUP_PART_BINDING_INVALID')
        raw = read_regular(Path(root) / entry['file'], expected_sha256=entry['file_sha256'])
        parts.append(strict_json(raw, limit=MAX_PART_BYTES))
    validate_parts(group, parts)
    return parts


@dataclass(frozen=True)
class Capture:
    """수집 직후 확정한 bytes. 내용 해시는 생산자 인증을 뜻하지 않는다."""
    raw: bytes
    sha256: str

    def value(self):
        if sha256_hex(self.raw) != self.sha256:
            raise ValueError('TIMING_GROUP_CAPTURE_CHANGED')
        return strict_json(self.raw, limit=MAX_PART_BYTES)


def seal_capture(value):
    # JSON 표준 밖 NaN/Infinity로 기록하기 전에 원 지연값을 검사한다.
    for sample in value.get('samples', []):
        duration = sample.get('elapsed_ms')
        if duration is not None and (type(duration) not in (int, float) or not 0 <= duration <= 120000
                                     or not math.isfinite(duration)):
            raise ValueError('TIMING_GROUP_SAMPLE_INVALID')
    raw = canonical_json(value)
    if len(raw) > MAX_PART_BYTES:
        raise ValueError('TIMING_GROUP_CAPTURE_TOO_LARGE')
    strict_json(raw, limit=MAX_PART_BYTES)
    return Capture(raw, sha256_hex(raw))


def aggregate(group, parts, captures, values, scope):
    """원 지연값으로 기존 계산을 다시 한다. 유효한 개별 위반은 다른 공백으로 지우지 않는다."""
    validate_parts(group, parts)
    if (type(scope) is not dict or set(scope) != {'environment_ref', 'target_sha256', 'prepared_sha256',
            'group_sha256', 'policy_values_sha256', 'runtime_binding'}
            or scope['environment_ref'] != group['environment_ref'] or scope['target_sha256'] != group['target_sha256']
            or scope['group_sha256'] != sha256_hex(canonical_json(group))
            or scope['policy_values_sha256'] != sha256_hex(canonical_json(values))
            or type(scope['prepared_sha256']) is not str or not _SHA.fullmatch(scope['prepared_sha256'])
            or type(scope['runtime_binding']) is not dict or not scope['runtime_binding']):
        raise ValueError('TIMING_GROUP_SCOPE_INVALID')
    combined, rows, elapsed = [], {}, {}
    missing, positive = [], False
    origins, actors, windows, digests, part_samples = [], [], [], set(), []
    normal = []
    if len(captures) != PARTS:
        missing.append('timing_group_parts_incomplete')
    for index, capture in enumerate(captures[:PARTS], 1):
        try:
            value = capture.value()
            if (set(value) != {'schema', 'index', 'part_sha256', 'scope', 'runtime_binding', 'actors',
                              'started_at', 'finished_at', 'data_origin', 'capture_complete',
                              'normal_control_succeeded', 'samples'}
                    or value['schema'] != CAPTURE_SCHEMA or type(value['index']) is not int or value['index'] != index
                    or value['part_sha256'] != group['parts'][index - 1]['manifest_sha256']
                    or value['scope'] != scope or value['runtime_binding'] != scope['runtime_binding']
                    or value['data_origin'] not in ('MEASURED', 'EXAMPLE', 'DECLARED')
                    or type(value['capture_complete']) is not bool or type(value['normal_control_succeeded']) is not bool
                    or type(value['actors']) is not list or not value['actors']
                    or value['actors'] != sorted(set(value['actors']))
                    or any(type(a) is not str or not _SHA.fullmatch(a) for a in value['actors'])
                    or type(value['samples']) is not list or len(value['samples']) != CASES_PER_PART):
                raise ValueError
            start, finish = (datetime.fromisoformat(value[k]) for k in ('started_at', 'finished_at'))
            if start.tzinfo is None or finish.tzinfo is None or start > finish:
                raise ValueError
            local_cases, local_rows, local_elapsed, local_digests = [], {}, {}, set()
            for case, sample in zip(parts[index - 1]['cases'], value['samples'], strict=True):
                if (type(sample) is not dict or set(sample) != {'case_id', 'role', 'request_sha256', 'elapsed_ms', 'clock', 'native_tokens'}
                        or sample['case_id'] != case['case_id'] or sample['role'] != case['role']
                        or type(sample['request_sha256']) is not str or not _SHA.fullmatch(sample['request_sha256'])
                        or sample['request_sha256'] in local_digests
                        or type(sample['native_tokens']) is not list
                        or any(type(t) is not int or t < 0 for t in sample['native_tokens'])):
                    raise ValueError
                local_digests.add(sample['request_sha256'])
                duration = sample['elapsed_ms']
                if duration is not None:
                    from .engine_sensor import _callback_elapsed_ms
                    clock = sample['clock']
                    numeric = ('dispatch_ms', 'request_sent_ms', 'headers_ms', 'end_ms', 'elapsed_ms')
                    if (type(duration) not in (int, float) or not 0 <= duration <= 120000
                            or type(clock) is not dict
                            or not {'outcome', 'capture_complete'} <= set(clock)
                            or set(clock) - {'outcome', 'capture_complete', 'timing_basis', *numeric}
                            or any(k in clock and (type(clock[k]) not in (int, float) or not 0 <= clock[k] <= 120000)
                                   for k in numeric)
                            or _callback_elapsed_ms(clock) != duration):
                        raise ValueError
                    local_elapsed[case['case_id']] = duration
                elif sample['clock'] is not None:
                    raise ValueError
                if sample['native_tokens']:
                    local_rows[case['case_id']] = [{'tokens': t} for t in sample['native_tokens']]
                local_cases.append(case)
            if local_digests & digests:
                raise ValueError
            digests.update(local_digests)
            result = projection.project_cache_timing(local_cases, local_rows, local_elapsed, values)
            positive |= result['violation'] is True
            part_samples.append(result['samples'])
            origins.append(value['data_origin']); actors.append(value['actors']); windows.append((start, finish))
            normal.append(value['normal_control_succeeded'])
            if not value['capture_complete'] or not value['normal_control_succeeded']:
                missing.append('timing_group_collection_incomplete')
            for case in local_cases:
                key = f'part{index}-{case["case_id"]}'
                combined.append({**case, 'case_id': key})
                if case['case_id'] in local_rows:
                    rows[key] = local_rows[case['case_id']]
                if case['case_id'] in local_elapsed:
                    elapsed[key] = local_elapsed[case['case_id']]
        except (ValueError, TypeError, KeyError, AttributeError):
            missing.append('timing_group_capture_invalid')
    if len(origins) != PARTS or len(set(origins)) != 1:
        missing.append('timing_group_origin_incomplete')
    if len(actors) == PARTS and actors[0] != actors[1]:
        missing.append('timing_group_actor_generation_changed')
    if len(windows) == PARTS and windows[0][1] > windows[1][0]:
        missing.append('timing_group_windows_overlap')
    result = projection.project_cache_timing(combined, rows, elapsed, values)
    if missing:
        unsafe = set(missing) - {'timing_group_collection_incomplete'}
        positive |= not unsafe and result['violation'] is True
        result = {**result, 'violation': True if positive else None, 'full_scope': False,
                  'missing': sorted(set(result.get('missing', []) + missing))}
    elif positive:
        result.update(violation=True, full_scope=False)
    result.update(normal_control_succeeded=all(normal) if len(normal) == PARTS else None,
                  data_origin='EXAMPLE' if 'EXAMPLE' in origins else (
                      origins[0] if len(origins) == PARTS and len(set(origins)) == 1 else 'DECLARED'),
                  part_samples=part_samples, producer_authenticated=False)
    return result
