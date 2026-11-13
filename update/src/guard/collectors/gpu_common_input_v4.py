"""승인된 공통 입력 /4: 유계 반복 요약과 명시적인 자원 단위.

요약은 동일한 연속 값만 묶고 양 끝 원 행을 남긴다. 판정 시 요약을
원 행 수만큼 펼치지 않는다. 생산자 등록과 실제 관측 자격은 별도다.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from . import gpu_common_inputs as common

SCHEMA = 'guard-gpu-common-input/4'
REPEATED = {
    'request-attempt': frozenset({'stage', 'gpu_fence', 'kv_owned'}),
    'generation-transition': frozenset({'consumed'}),
    'health-worker-causal': frozenset({'worker_state'}),
}
_UNITS = frozenset({'block', 'token_slot'})
_METHODS = frozenset({'alloc_refused', 'preempted', 'rejected'})


def source_digest(paths):
    """변환·계약·호출자가 넘긴 관측/감싸기 기반의 정확 bytes를 결합한다."""
    # 등록표가 있는 공통 검증 파일을 포함하면 등록 자체가 SHA를 다시 바꾸므로 제외한다.
    files = {Path(p) for p in paths} | {Path(__file__)}
    digest = sha256()
    for path in sorted(files, key=lambda p: (p.parent.name, p.name)):
        digest.update(path.parent.name.encode() + b'/' + path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def _values(row):
    return {k: v for k, v in row.items() if k not in {'seq', 'source_seq', 'at_ms', 'repeat'}}


def _maxima(row):
    return {k: v for k, v in _values(row).items() if type(v) is int and k != 'binding'}


def compact(kind, events):
    """동일한 연속 행 네 개 이상을 첫 행·중간 요약·끝 행으로 바꾼다."""
    out, start = [], 0
    while start < len(events):
        first = events[start]
        end = start + 1
        if first['event'] in REPEATED.get(kind, ()):
            while (end < len(events) and _values(events[end]) == _values(first)
                   and events[end]['at_ms'] >= events[end - 1]['at_ms']):
                end += 1
        run = events[start:end]
        if len(run) >= 4:
            middle = dict(run[-2], repeat={
                'first_at_ms': run[1]['at_ms'], 'last_at_ms': run[-2]['at_ms'],
                'first_seq': run[1]['source_seq'], 'last_seq': run[-2]['source_seq'],
                'count': len(run) - 2, 'max_values': _maxima(first)})
            out.extend((first, middle, run[-1]))
        else:
            out.extend(run)
        start = end
    return [dict(row, seq=i) for i, row in enumerate(out)]


def encode(record, *, wire_schema, kv_unit=None, kv_unit_tokens=None):
    """기존 변환의 원 행을 /4로 직렬화한다. /1 재생 경로도 유지한다."""
    if wire_schema == common.SCHEMA:
        return record
    if wire_schema != SCHEMA:
        raise common.GpuInputError('GPU_INPUT_ENVELOPE_INVALID')
    out = deepcopy(record)
    out['schema'] = SCHEMA
    for row in out['events']:
        row['repeat'] = None
        row['source_seq'] = row['seq']
        if out['kind'] == 'request-attempt':
            value = row.pop('kv_blocks')
            row.update(kv_units=value if kv_unit is not None else None,
                       kv_unit=kv_unit, kv_unit_tokens=kv_unit_tokens,
                       enforcement_method=row.get('enforcement_method'))
            if value is not None and kv_unit is None:
                out['completeness']['diagnostic_codes'].append('KV_UNIT_UNOBSERVED')
    out['events'] = compact(out['kind'], out['events'])
    return out


def _fail():
    raise common.GpuInputError('GPU_INPUT_V4_EVENT_INVALID')


def _check_repeat(events, index, kind):
    row = events[index]
    repeat = row['repeat']
    if repeat is None:
        return
    if (row.get('event') not in REPEATED.get(kind, ()) or type(repeat) is not dict
            or set(repeat) != {'first_at_ms', 'last_at_ms', 'first_seq', 'last_seq', 'count', 'max_values'}
            or not all(common._check(repeat[k], 'int') for k in ('first_at_ms', 'last_at_ms', 'first_seq', 'last_seq', 'count'))
            or repeat['count'] < 2 or repeat['first_at_ms'] > repeat['last_at_ms']
            or repeat['count'] != repeat['last_seq'] - repeat['first_seq'] + 1
            or row.get('source_seq') != repeat['last_seq']
            or row.get('at_ms') != repeat['last_at_ms']
            or type(repeat['max_values']) is not dict or repeat['max_values'] != _maxima(row)
            or not all(common._check(v, 'int') for v in repeat['max_values'].values())
            or index == 0 or index + 1 == len(events)):
        _fail()
    before, after = events[index - 1], events[index + 1]
    if (type(before) is not dict or type(after) is not dict
            or before.get('repeat') is not None or after.get('repeat') is not None
            or _values(before) != _values(row) or _values(after) != _values(row)
            or before.get('source_seq') != repeat['first_seq'] - 1 or after.get('source_seq') != repeat['last_seq'] + 1
            or not common._check(before.get('at_ms'), 'ms') or not common._check(after.get('at_ms'), 'ms')
            or not before['at_ms'] <= repeat['first_at_ms'] <= repeat['last_at_ms'] <= after['at_ms']):
        _fail()


def validate(record, *, kind, **scope):
    """닫힌 /4 형식과 요약 경계를 검사하고 기존 판정 재료를 반환한다."""
    if type(record) is not dict or record.get('schema') != SCHEMA:
        raise common.GpuInputError('GPU_INPUT_ENVELOPE_INVALID')
    events = record.get('events')
    if type(events) is not list or len(events) > common.MAX_EVENTS:
        raise common.GpuInputError('GPU_INPUT_EVENTS_INVALID')
    legacy = deepcopy(record)
    legacy['schema'] = common.SCHEMA
    normalized = []
    methods_missing = False
    source_seq = 0
    for i, row in enumerate(events):
        if (type(row) is not dict or 'repeat' not in row or type(row.get('seq')) is not int
                or not common._check(row.get('source_seq'), 'int')):
            _fail()
        _check_repeat(events, i, kind)
        first_seq = row['repeat']['first_seq'] if row['repeat'] is not None else row['source_seq']
        if first_seq != source_seq:
            _fail()
        source_seq = row['source_seq'] + 1
        item = dict(row)
        item.pop('repeat')
        item.pop('source_seq')
        if kind == 'request-attempt':
            if not {'kv_units', 'kv_unit', 'kv_unit_tokens', 'enforcement_method'} <= set(item) or 'kv_blocks' in item:
                _fail()
            value, unit, tokens, method = (item.pop(k) for k in
                ('kv_units', 'kv_unit', 'kv_unit_tokens', 'enforcement_method'))
            if (not common._check(value, 'opt_int') or (unit is not None and (type(unit) is not str or unit not in _UNITS))
                    or (tokens is not None and (not common._check(tokens, 'int') or tokens == 0))
                    or (unit is None and (value is not None or tokens is not None))
                    or (method is not None and (type(method) is not str or method not in _METHODS))
                    or (row.get('event') != 'limit_enforced' and method is not None)):
                _fail()
            methods_missing |= row.get('event') == 'limit_enforced' and method is None
            item['kv_blocks'] = value
        normalized.append(item)
    legacy['events'] = normalized
    result = common._validate_v1(legacy, kind=kind, sampler_logit_bias=True,
                                 registered_producers=common.SUPPORTED_V4_PRODUCERS, **scope)
    result['source_events'] = sum(row['repeat']['count'] if row['repeat'] is not None else 1 for row in events)
    if methods_missing:
        result['gaps'].append('GPU_INPUT_LIMIT_ENFORCEMENT_METHOD_UNOBSERVED')
        result['complete'] = False
    return result


def event_count(row):
    """검증된 행의 원 관측 개수. 요약을 펼치지 않는다."""
    return row['repeat']['count'] if row.get('repeat') is not None else 1
