"""요청 메타데이터 후보의 비밀 원문/사용자 코드 실행과 잘못된 귀속을 거부한다."""
from hashlib import sha256
import hmac
import sys
import types

import pytest

from guard.adapters import vllm_rank_request_identity as reader


@pytest.fixture
def model(monkeypatch):
    module = types.ModuleType('rank_request_fixture')
    exec('''
class Batch:
    __slots__ = ('groups',)
class Group:
    __slots__ = ('request_id',)
class Delta:
    __slots__ = ('request_id',)
class Scheduled:
    pass
class Assigned:
    __slots__ = ('request_id', 'external_req_id')
''', vars(module))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


def spec(name):
    return {'module': 'rank_request_fixture', 'name': name}


def layout(kind):
    return {'kind': kind, 'type': spec('Batch' if kind == 'struct-list' else 'Scheduled'),
            'field': 'groups' if kind == 'struct-list' else 'num_scheduled_tokens',
            'item_types': [spec('Group'), spec('Delta')] if kind == 'struct-list' else []}


def test_slot_batch_and_scheduler_agree_without_exposing_ids(model):
    group, delta = model.Group(), model.Delta()
    group.request_id, delta.request_id = 'request-a', 'request-b'
    batch = model.Batch(); batch.groups = [group, delta]
    scheduler = model.Scheduled(); scheduler.num_scheduled_tokens = {'request-a': 1, 'request-b': 2}
    key = b'K' * 32
    first = reader.requests(batch, layout=layout('struct-list'), key=key)
    second = reader.requests(scheduler, layout=layout('positive-dict-keys'), key=key)
    expected = [hmac.new(key, b'guard-rank-request-id/1\0' + v, sha256).hexdigest()
                for v in (b'request-a', b'request-b')]
    assert first == second and first['request_hmac_sha256'] == expected
    assert 'request-a' not in repr(first) and 'request-b' not in repr(first)
    assert all(first[name] is False for name in ('source_authenticated', 'rpc_active_verified', 'producer_registered'))
    assert batch.groups == [group, delta] and group.request_id == 'request-a'
    assert reader.requests(batch, layout=layout('struct-list'), key=b'Q' * 32) != first


def test_assignment_follows_internal_id_change_and_preserves_external_id(model):
    obj = model.Assigned(); obj.external_req_id = 'external'; obj.request_id = 'internal'
    first = reader.assignment(obj, expected_type=spec('Assigned'), key=b'K' * 32)
    obj.request_id = 'new-internal'
    second = reader.assignment(obj, expected_type=spec('Assigned'), key=b'K' * 32)
    assert first['external_hmac_sha256'] == second['external_hmac_sha256']
    assert first['internal_hmac_sha256'] != second['internal_hmac_sha256']
    assert 'external' not in repr(first.values()) and 'internal' not in repr(first.values())
    assert first['rpc_active_verified'] is False


@pytest.mark.parametrize('values', [{}, {'a': 0}, {'a': -1}, {'a': True}, {'a': '1'},
                                  {'': 1}, {'a' * 513: 1}, {str(i): 1 for i in range(9)}])
def test_invalid_scheduled_work_is_not_a_request_binding(model, values):
    obj = model.Scheduled(); obj.num_scheduled_tokens = values
    with pytest.raises(ValueError):
        reader.requests(obj, layout=layout('positive-dict-keys'), key=b'K' * 32)


def test_property_custom_descriptor_and_getter_are_never_invoked(model):
    calls = []
    class Descriptor:
        def __get__(self, obj, cls):
            calls.append('descriptor')
            raise AssertionError
    for descriptor in [property(lambda obj: calls.append('property')), Descriptor()]:
        model.Scheduled.num_scheduled_tokens = descriptor
        obj = model.Scheduled(); obj.__dict__['num_scheduled_tokens'] = {'stale': 1}
        with pytest.raises(ValueError):
            reader.requests(obj, layout=layout('positive-dict-keys'), key=b'K' * 32)
    del model.Scheduled.num_scheduled_tokens
    def getter(self, name):
        calls.append('getter')
        raise AssertionError
    model.Scheduled.__getattribute__ = getter
    with pytest.raises(ValueError):
        reader.member(model.Scheduled(), 'num_scheduled_tokens', spec('Scheduled'))
    assert calls == []


def test_wrong_class_module_and_replaced_slot_are_rejected(model, monkeypatch):
    item = model.Group(); item.request_id = 'a'
    batch = model.Batch(); batch.groups = [item]
    original = model.Group
    monkeypatch.setattr(model, 'Group', model.Delta)
    with pytest.raises(ValueError, match='RANK_REQUEST_TYPE_UNAVAILABLE'):
        reader.requests(batch, layout=layout('struct-list'), key=b'K' * 32)
    monkeypatch.setattr(model, 'Group', original)
    # 형식 이름만 같은 클래스·다른 클래스의 슬롯을 정확한 소유자로 간주하지 않는다.
    exec('class Other:\n __slots__=("request_id",)\n', vars(model))
    model.Group.request_id = model.Other.request_id
    with pytest.raises(ValueError, match='RANK_REQUEST_FIELD_UNAVAILABLE'):
        reader.member(item, 'request_id', spec('Group'))


def test_duplicates_oversized_lists_wrong_items_and_keys_are_rejected(model):
    item = model.Group(); item.request_id = 'a'
    obj = model.Batch()
    for values in [[], [item, item], [item] * 9, [object()], (item,)]:
        obj.groups = values
        with pytest.raises(ValueError):
            reader.requests(obj, layout=layout('struct-list'), key=b'K' * 32)
    obj.groups = [item]
    for key in [None, b'', b'K' * 31, 'K' * 32]:
        with pytest.raises(ValueError):
            reader.requests(obj, layout=layout('struct-list'), key=key)


def test_uninitialized_slot_surrogate_and_user_key_do_not_run_code(model):
    obj = model.Assigned()
    with pytest.raises(ValueError):
        reader.assignment(obj, expected_type=spec('Assigned'), key=b'K' * 32)
    obj.external_req_id, obj.request_id = 'a', '\ud800'
    with pytest.raises(ValueError):
        reader.assignment(obj, expected_type=spec('Assigned'), key=b'K' * 32)
    calls = []
    class Key:
        def __hash__(self):
            return 42
        def __eq__(self, other):
            calls.append('eq')
            raise AssertionError
    scheduled = model.Scheduled(); scheduled.num_scheduled_tokens = {Key(): 1}
    with pytest.raises(ValueError):
        reader.requests(scheduled, layout=layout('positive-dict-keys'), key=b'K' * 32)
    assert calls == []


@pytest.mark.parametrize('kind', ['struct-list', 'positive-dict-keys'])
def test_request_change_during_fingerprinting_is_rejected(model, monkeypatch, kind):
    item = model.Group(); item.request_id = 'a'
    obj = model.Batch() if kind == 'struct-list' else model.Scheduled()
    if kind == 'struct-list':
        obj.groups = [item]
    else:
        obj.num_scheduled_tokens = {'a': 1}
    original = reader._fingerprints
    def change(values, key):
        result = original(values, key)
        if kind == 'struct-list':
            item.request_id = 'b'
        else:
            obj.num_scheduled_tokens['a'] = 2
        return result
    monkeypatch.setattr(reader, '_fingerprints', change)
    with pytest.raises(ValueError, match='RANK_REQUEST_IDS_CHANGED'):
        reader.requests(obj, layout=layout(kind), key=b'K' * 32)


def test_assignment_change_does_not_call_custom_equality(model, monkeypatch):
    calls = []
    class Other:
        def __eq__(self, other):
            calls.append('eq')
            raise AssertionError
    obj = model.Assigned(); obj.external_req_id = 'a'; obj.request_id = 'b'
    original = reader._fingerprints
    def change(values, key):
        result = original(values, key)
        obj.request_id = Other()
        return result
    monkeypatch.setattr(reader, '_fingerprints', change)
    with pytest.raises(ValueError, match='RANK_REQUEST_IDS_UNAVAILABLE'):
        reader.assignment(obj, expected_type=spec('Assigned'), key=b'K' * 32)
    assert calls == []
