"""정확 source hook이 넘긴 요청 ID 메타데이터만 읽는 미등록 후보.

요청 본문·토큰·출력을 읽지 않는다. msgspec 구조체의 일반 dict 부재를
property 호출로 우회하지 않고 표준 member descriptor만 직접 대조한다.
이 모듈의 반환은 source/생산자 인증이나 활성 RPC·장애 적용 증명이 아니다.
"""
from hashlib import sha256
import hmac
import inspect
from itertools import islice
import sys
import types

from .vllm_rank_ipc_hooks import MISSING, _plain


def _class(value, expected):
    if (type(expected) is not dict or set(expected) != {'module', 'name'}
            or any(type(expected[k]) is not str for k in expected)):
        raise ValueError('RANK_REQUEST_TYPE_UNAVAILABLE')
    cls = type(value)
    namespace = type.__dict__['__dict__'].__get__(cls)
    module = sys.modules.get(expected['module'])
    if (namespace.get('__module__') != expected['module']
            or type.__dict__['__qualname__'].__get__(cls) != expected['name']
            or type(module) is not types.ModuleType
            or vars(module).get(expected['name']) is not cls
            or inspect.getattr_static(cls, '__getattribute__') is not object.__getattribute__):
        raise ValueError('RANK_REQUEST_TYPE_UNAVAILABLE')
    return cls, namespace


def member(value, name, expected):
    """정확 클래스의 실제 dict 또는 표준 슬롯만 받는다. 사용자 getter는 거부한다."""
    if type(name) is not str or not name or name.startswith('__'):
        raise ValueError('RANK_REQUEST_FIELD_UNAVAILABLE')
    cls, namespace = _class(value, expected)
    descriptor = namespace.get(name, MISSING)
    if (type(descriptor) is types.MemberDescriptorType
            and descriptor.__objclass__ is cls and descriptor.__name__ == name):
        try:
            return descriptor.__get__(value, cls)
        except AttributeError:
            raise ValueError('RANK_REQUEST_FIELD_UNAVAILABLE') from None
    result = _plain(value, name)
    if result is MISSING:
        raise ValueError('RANK_REQUEST_FIELD_UNAVAILABLE')
    return result


def _identifiers(values):
    if not 1 <= len(values) <= 8:
        raise ValueError('RANK_REQUEST_IDS_UNAVAILABLE')
    if any(type(v) is not str or not 1 <= len(v) <= 512 for v in values):
        raise ValueError('RANK_REQUEST_IDS_UNAVAILABLE')
    if len(set(values)) != len(values):
        raise ValueError('RANK_REQUEST_IDS_UNAVAILABLE')


def _fingerprints(values, key):
    if type(key) is not bytes or len(key) != 32:
        raise ValueError('RANK_REQUEST_KEY_INVALID')
    _identifiers(values)
    try:
        return [hmac.new(key, b'guard-rank-request-id/1\0' + v.encode('utf-8'), sha256).hexdigest()
                for v in values]
    except UnicodeError:
        raise ValueError('RANK_REQUEST_IDS_UNAVAILABLE') from None


def _dictionary(value):
    if type(value) is not dict or not 1 <= len(value) <= 8:
        raise ValueError('RANK_REQUEST_IDS_UNAVAILABLE')
    try:
        items = tuple(islice(value.items(), 9))
    except RuntimeError:
        raise ValueError('RANK_REQUEST_IDS_CHANGED') from None
    _identifiers(tuple(k for k, _ in items))
    if any(type(v) is not int or v <= 0 for _, v in items):
        raise ValueError('RANK_REQUEST_IDS_UNAVAILABLE')
    return items


def requests(value, *, layout, key):
    """adapter 데이터로 고른 구조의 ID만 HMAC한다. 원 ID/다른 필드는 반환하지 않는다."""
    if (type(layout) is not dict or set(layout) != {'kind', 'type', 'field', 'item_types'}
            or type(layout['field']) is not str or type(layout['item_types']) is not list):
        raise ValueError('RANK_REQUEST_LAYOUT_INVALID')
    selected = member(value, layout['field'], layout['type'])
    if layout['kind'] == 'positive-dict-keys':
        if type(selected) is not dict or layout['item_types']:
            raise ValueError('RANK_REQUEST_IDS_UNAVAILABLE')
        before = _dictionary(selected)
        values = tuple(k for k, _ in before)
        digests = _fingerprints(values, key)
        if member(value, layout['field'], layout['type']) is not selected or _dictionary(selected) != before:
            raise ValueError('RANK_REQUEST_IDS_CHANGED')
    elif layout['kind'] == 'struct-list':
        if type(selected) is not list or not 1 <= len(selected) <= 8 or not layout['item_types']:
            raise ValueError('RANK_REQUEST_IDS_UNAVAILABLE')
        items = tuple(selected)
        values = []
        choices = []
        for item in items:
            matches = []
            for expected in layout['item_types']:
                try:
                    _class(item, expected)
                    matches.append(expected)
                except ValueError:
                    pass
            if len(matches) != 1:
                raise ValueError('RANK_REQUEST_TYPE_UNAVAILABLE')
            choices.append(matches[0])
            values.append(member(item, 'request_id', matches[0]))
        digests = _fingerprints(values, key)
        current = tuple(islice(selected, 9))
        after = [member(item, 'request_id', expected) for item, expected in zip(items, choices)]
        if (member(value, layout['field'], layout['type']) is not selected
                or tuple(map(id, current)) != tuple(map(id, items))
                or _fingerprints(after, key) != digests):
            raise ValueError('RANK_REQUEST_IDS_CHANGED')
    else:
        raise ValueError('RANK_REQUEST_LAYOUT_INVALID')
    return {'schema': 'guard-rank-request-identity-candidate/1', 'request_hmac_sha256': digests,
            'source_authenticated': False, 'rpc_active_verified': False, 'producer_registered': False}


def assignment(value, *, expected_type, key):
    """정확 재배정 함수의 호출자가 넘긴 현재 external/internal ID 쌍만 읽는다."""
    external = member(value, 'external_req_id', expected_type)
    internal = member(value, 'request_id', expected_type)
    values = _fingerprints([external, internal], key)
    if _fingerprints([member(value, 'external_req_id', expected_type),
                      member(value, 'request_id', expected_type)], key) != values:
        raise ValueError('RANK_REQUEST_IDS_CHANGED')
    return {'schema': 'guard-rank-request-assignment-candidate/1',
            'external_hmac_sha256': values[0], 'internal_hmac_sha256': values[1],
            'source_authenticated': False, 'rpc_active_verified': False, 'producer_registered': False}
