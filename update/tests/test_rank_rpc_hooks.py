"""정확 상위 RPC 프레임·동일 worker/요청 연결과 원 호출 보존을 검증한다."""
from copy import deepcopy
from hashlib import sha256
import json
import sys
import types

import pytest

from guard.adapters import vllm_rank_ipc_hooks as ipc
from guard.adapters import vllm_rank_ipc_sources as sources
from guard.adapters import vllm_rank_rpc_hooks as rpc

RAW = b'''calls = []
class Scheduled:
    pass
class Request:
    pass
class Inner:
    def execute(self, scheduler_output):
        calls.append(('inner', scheduler_output))
        if self.error is not None:
            raise self.error
        return self.result
class Outer:
    def execute(self, scheduler_output):
        calls.append(('outer', scheduler_output))
        if self.replace_ids:
            scheduler_output.num_scheduled_tokens = {'different-request': 1}
        return self.worker.execute(scheduler_output)
class External:
    @staticmethod
    def header(value):
        return value
class Assignment:
    @staticmethod
    def assign(request):
        request.external_req_id = request.request_id
        request.request_id += '-internal'
'''


@pytest.fixture
def setup(tmp_path, monkeypatch):
    name = 'rank_rpc_fixture'
    path = tmp_path / 'fixture.py'; path.write_bytes(RAW)
    module = types.ModuleType(name); module.__file__ = str(path)
    exec(compile(RAW, str(path), 'exec'), vars(module)); monkeypatch.setitem(sys.modules, name, module)
    points = ['Inner.execute', 'Outer.execute', 'External.header', 'Assignment.assign']
    codes = sources._codes(compile(RAW, 'fixture.py', 'exec'))
    common = {'phase': 'around-original', 'role': 'EXAMPLE', 'source_modules': [name]}
    request = {'root': 'scheduler_output', 'layout': 'scheduled'}
    hooks = {'Inner.execute': {**common, 'kind': 'rpc', 'requests': request,
                 'parent': {'point': 'Outer.execute', 'worker_path': ['worker']}},
             'Outer.execute': {**common, 'kind': 'rpc', 'requests': request},
             'External.header': {**common, 'kind': 'external-id'},
             'Assignment.assign': {**common, 'kind': 'assignment', 'assignment': {'root': 'request'}}}
    source = {'path': 'fixture.py', 'bytes': len(RAW), 'sha256': sha256(RAW).hexdigest(),
              'functions': points, 'hooks': hooks,
              'body_code_sha256': {p: sources.code_digest(codes[p]) for p in points}}
    profile = {'id': 'EXAMPLE', 'image': 'fixture-image', 'sources': {},
        'runtime': sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3])),
        'rpc_sources': {name: source}, 'request_layouts': {'scheduled': {
            'kind': 'positive-dict-keys', 'type': {'module': name, 'name': 'Scheduled'},
            'field': 'num_scheduled_tokens', 'item_types': []}},
        'assignment_type': {'module': name, 'name': 'Request'}}
    profiles = tmp_path / 'profiles.json'; profiles.write_text(json.dumps({
        'schema': 'guard-rank-ipc-source-candidates/1', 'profiles': [profile]}))
    monkeypatch.setattr(sources, 'PROFILES', profiles)
    rows = []; identity = ipc.IPCObserver(image='fixture-image', key=b'K' * 32, emit=rows.append)
    observer = rpc.RPCObserver(image='fixture-image', key=b'K' * 32, emit=rows.append, identity_observer=identity)
    observer.attach(module)
    inner, outer, scheduled = module.Inner(), module.Outer(), module.Scheduled()
    inner.error, inner.result, outer.worker = None, object(), inner
    outer.replace_ids = False
    scheduled.num_scheduled_tokens = {'private-request': 1}
    return module, observer, rows, inner, outer, scheduled, path


def test_nested_actual_rpc_frame_worker_and_request_are_bound(setup):
    module, observer, rows, inner, outer, scheduled, _ = setup
    assert outer.execute(scheduled) is inner.result
    assert module.calls == [('outer', scheduled), ('inner', scheduled)]
    assert [(r['point'], r['event']) for r in rows] == [
        ('Outer.execute', 'CALL'), ('Inner.execute', 'CALL'),
        ('Inner.execute', 'RETURN'), ('Outer.execute', 'RETURN')]
    first, entered, returned, last = rows
    assert first['call_hmac_sha256'] == last['call_hmac_sha256']
    assert entered['call_hmac_sha256'] == returned['call_hmac_sha256'] != first['call_hmac_sha256']
    assert entered['rpc_parent_call_hmac_sha256'] == returned['rpc_parent_call_hmac_sha256'] == first['call_hmac_sha256']
    assert entered['rpc_parent_frame_verified'] is returned['rpc_parent_frame_verified'] is True
    assert entered['rpc_parent_worker_verified'] is True and entered['gaps'] == returned['gaps'] == []
    assert len({tuple(r['request_hmac_sha256']) for r in rows}) == 1
    assert first['object_hmac_sha256'] != entered['object_hmac_sha256']
    assert 'private-request' not in json.dumps(rows)
    assert all(r[k] is False for r in rows for k in ('source_authenticated', 'engine_group_verified', 'producer_registered'))


def test_same_request_without_parent_rpc_cannot_make_active_binding(setup):
    _, _, rows, inner, _, scheduled, _ = setup
    assert inner.execute(scheduled) is inner.result
    assert len(rows) == 2
    assert all(r['rpc_parent_frame_verified'] is r['rpc_parent_worker_verified'] is False for r in rows)
    assert all(r['gaps'] == ['RANK_RPC_ACTIVE_PARENT_UNVERIFIED'] for r in rows)


def test_context_token_alone_cannot_replace_a_live_original_frame(setup):
    module, observer, rows, inner, outer, scheduled, _ = setup
    original = observer.functions[module.__name__]['Outer.execute']
    ids = observer._requests(original, (outer, scheduled), {}, observer.sources[module.__name__]['hooks']['Outer.execute'])
    parent = {'point': 'Outer.execute', 'original': original, 'owner': outer,
              'context': ipc.base._context(), 'ids': ids, 'call': 'a' * 64,
              'source_modules': [module.__name__]}
    token = observer.current.set(parent)
    try:
        assert inner.execute(scheduled) is inner.result
    finally:
        observer.current.reset(token)
    assert all(r['rpc_parent_frame_verified'] is False and r['gaps'] for r in rows)


def test_wrong_worker_path_is_rejected(setup):
    module, observer, rows, inner, outer, scheduled, _ = setup
    spec = observer.sources[module.__name__]['hooks']['Inner.execute']
    spec['parent']['worker_path'] = ['other_worker']
    assert outer.execute(scheduled) is inner.result
    assert rows[1]['rpc_parent_worker_verified'] is False and rows[1]['gaps']


def test_different_request_inside_real_rpc_frame_is_not_bound(setup):
    _, _, rows, inner, outer, scheduled, _ = setup
    outer.replace_ids = True
    assert outer.execute(scheduled) is inner.result
    assert rows[0]['request_hmac_sha256'] != rows[1]['request_hmac_sha256']
    assert rows[1]['rpc_parent_frame_verified'] is False and rows[1]['gaps']


def test_original_exception_identity_and_call_count_are_preserved(setup):
    module, observer, rows, inner, outer, scheduled, _ = setup
    error = RuntimeError('EXAMPLE original'); inner.error = error
    with pytest.raises(RuntimeError) as caught:
        outer.execute(scheduled)
    assert caught.value is error
    assert module.calls == [('outer', scheduled), ('inner', scheduled)]
    assert [r['event'] for r in rows] == ['CALL', 'CALL', 'RAISE', 'RAISE']
    assert observer.current.get() is None


@pytest.mark.parametrize('sink_error', [OSError, KeyboardInterrupt, SystemExit])
def test_sink_failure_never_changes_original_result_or_exception(setup, sink_error):
    module, observer, rows, inner, outer, scheduled, _ = setup
    def reject(_):
        raise sink_error('EXAMPLE sink unavailable')
    observer.emit = reject
    assert outer.execute(scheduled) is inner.result
    assert len(module.calls) == 2 and observer.current.get() is None
    error = RuntimeError('EXAMPLE original'); inner.error = error
    with pytest.raises(RuntimeError) as caught:
        outer.execute(scheduled)
    assert caught.value is error and len(module.calls) == 4


@pytest.mark.parametrize('change', ['file', 'code', 'hook'])
def test_source_or_installed_hook_drift_prevents_a_parent_proof(setup, change):
    module, observer, rows, inner, outer, scheduled, path = setup
    if change == 'file':
        path.write_bytes(RAW + b'\n')
    elif change == 'code':
        original = observer.functions[module.__name__]['Outer.execute']
        def altered(self, scheduler_output):
            return self.worker.execute(scheduler_output)
        original.__code__ = altered.__code__
    else:
        module.External.header = staticmethod(lambda value: value)
    assert outer.execute(scheduled) is inner.result
    assert rows and all(r['gaps'] for r in rows)
    assert not any(r.get('rpc_parent_frame_verified') for r in rows)


def test_external_and_internal_ids_are_only_hmac_and_original_changes_remain(setup):
    module, _, rows, _, _, _, _ = setup
    assert module.External.header('external-nonce') == 'external-nonce'
    request = module.Request(); request.request_id = 'external-nonce'; request.external_req_id = None
    assert module.Assignment.assign(request) is None
    assert request.request_id == 'external-nonce-internal' and request.external_req_id == 'external-nonce'
    header, assigned = rows
    assert header['external_request_hmac_sha256'] == assigned['assignment']['external_hmac_sha256']
    assert assigned['assignment']['internal_hmac_sha256'] != header['external_request_hmac_sha256']
    assert 'external-nonce' not in json.dumps(rows)


def test_invalid_scope_is_rejected_without_user_hashing(setup):
    module, _, _, _, _, _, _ = setup
    class Invalid:
        def __hash__(self):
            raise AssertionError('사용자 hash 실행 금지')
    with pytest.raises(ValueError, match='RANK_IPC_SOURCE_UNAVAILABLE'):
        sources.qualify(module, image='fixture-image', scope=Invalid())


def test_rpc_scope_does_not_change_default_ipc_scope(setup):
    module, observer, _, _, _, _, _ = setup
    # 기본 IPC scope에는 이 모듈 행이 없으며 RPC 데이터로 자동 대체하지 않는다.
    with pytest.raises(ValueError, match='RANK_IPC_SOURCE_UNAVAILABLE'):
        sources.qualify(module, image='fixture-image')
    assert len(observer.functions[module.__name__]) == 4


def test_missing_scheduled_ids_keep_gap_and_original_call_with_fixed_diagnostic(setup):
    module, _, rows, inner, outer, scheduled, _ = setup
    scheduled.num_scheduled_tokens = {}
    assert outer.execute(scheduled) is inner.result
    assert module.calls == [('outer', scheduled), ('inner', scheduled)]
    assert len(rows) == 2
    assert all(r['gaps'] == ['RANK_RPC_CAPTURE_UNAVAILABLE'] for r in rows)
    assert all(r['diagnostic_code'] == 'RANK_REQUEST_IDS_UNAVAILABLE' for r in rows)
    assert all(r['diagnostic_stage'] == 'before-original' for r in rows)


def test_gap_diagnostic_never_formats_user_exception_or_copies_private_message(setup):
    _, observer, rows, _, _, _, _ = setup
    class PrivateError(ValueError):
        def __str__(self):
            raise AssertionError('사용자 예외 문자열 변환 금지')
    for error in (PrivateError('비밀'), ValueError('비밀'), ValueError('RANK_BAD\n비밀')):
        observer._gap('EXAMPLE', error, 'before-original')
    assert len(rows) == 3
    assert all(r['diagnostic_code'] == 'RANK_RPC_OBSERVATION_ERROR' for r in rows)
    assert '비밀' not in json.dumps(rows, ensure_ascii=False)


def test_explicit_zero_work_is_observed_without_request_binding_or_control(setup):
    module, observer, rows, inner, outer, scheduled, _ = setup
    for hook in observer.sources[module.__name__]['hooks'].values():
        if hook['kind'] == 'rpc':
            hook['no_scheduled_work'] = {'total_field': 'total_num_scheduled_tokens'}
    class Forbidden:
        def maybe_hold(self, _):
            raise AssertionError('빈 일정에 장애 적용 금지')
    observer.control = Forbidden()
    observer.sources[module.__name__]['hooks']['Inner.execute']['fault_eligible'] = True
    scheduled.num_scheduled_tokens, scheduled.total_num_scheduled_tokens = {}, 0
    assert outer.execute(scheduled) is inner.result
    assert module.calls == [('outer', scheduled), ('inner', scheduled)]
    assert [r['event'] for r in rows] == ['IDLE_CALL', 'IDLE_CALL', 'IDLE_RETURN', 'IDLE_RETURN']
    assert all(r['gaps'] == [] and r['request_hmac_sha256'] == [] for r in rows)
    assert all(r['rpc_parent_frame_verified'] is r['rpc_parent_worker_verified'] is False for r in rows)
    assert all(r['idle_basis'] == {'empty_scheduled_requests': True, 'total_scheduled_tokens': 0,
                                 'request_bound': False, 'fault_eligible': False} for r in rows)
    assert observer.current.get() is None


@pytest.mark.parametrize('total', [None, True, 1, -1, '0'])
def test_empty_schedule_with_invalid_total_keeps_gap_and_original(setup, total):
    module, observer, rows, inner, outer, scheduled, _ = setup
    for hook in observer.sources[module.__name__]['hooks'].values():
        if hook['kind'] == 'rpc':
            hook['no_scheduled_work'] = {'total_field': 'total_num_scheduled_tokens'}
    scheduled.num_scheduled_tokens, scheduled.total_num_scheduled_tokens = {}, total
    assert outer.execute(scheduled) is inner.result
    assert module.calls == [('outer', scheduled), ('inner', scheduled)]
    assert len(rows) == 2 and all(r['gaps'] for r in rows)
