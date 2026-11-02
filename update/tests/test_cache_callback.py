"""Actual pinned method slices with CPU collaborators; never a deployed engine."""
from copy import deepcopy
import json
import gc
import weakref
from pathlib import Path
from types import ModuleType, SimpleNamespace, FunctionType, CodeType

import pytest

from guard.adapters import vllm_cache_callback as callback
from guard.adapters import vllm_distributed_callback as distributed
from guard.executors import cache_observations as timing
from guard.models import sha256_hex

PRIVATE = 'private-user-salt-request-prefix-contents-9346'
FIXTURES = Path(__file__).parent / 'fixtures/cache-callback-native'


def source_module(name, tmp_path, *, targets=None, sources=None):
    targets = targets or callback.TARGETS; sources = sources or callback.base.SOURCE_SHA
    raw = (FIXTURES / (name.rsplit('.', 1)[-1] + '.py.txt')).read_bytes()
    assert sha256_hex(raw) == sources[name]
    path = tmp_path / (name.rsplit('.', 1)[-1] + '.py'); path.write_bytes(raw)
    module = ModuleType(name); module.__file__ = str(path)
    codes = {}
    def visit(code):
        codes[code.co_qualname] = code
        for child in code.co_consts:
            if type(child) is CodeType: visit(child)
    visit(compile(raw, str(path), 'exec', dont_inherit=True, optimize=0))
    for point in targets[name]:
        owner, method = point.split('.')
        cls = vars(module).setdefault(owner, type(owner, (), {'__module__': name}))
        setattr(cls, method, FunctionType(codes[point], vars(module)))
    return module


@pytest.fixture
def world(tmp_path):
    manager_module = source_module('vllm.v1.core.kv_cache_manager', tmp_path)
    pool_module = source_module('vllm.v1.core.block_pool', tmp_path)
    block_type = type('KVCacheBlock', (), {})
    pool_module.KVCacheBlock = block_type
    pool_module.make_block_hash_with_group_id = lambda key, group: key + group.to_bytes(4, 'big')
    block = block_type(); block.block_id = 42
    mapping = pool_module.BlockHashToBlockMap(); mapping._cache = {PRIVATE.encode() + b'\x00\x00\x00\x00': block}
    pool = pool_module.BlockPool(); pool.cached_block_hash_to_block = mapping
    manager = manager_module.KVCacheManager(); manager.block_pool = pool
    manager.prefix_cache_lookup_enabled = lambda request: True
    manager.enable_kv_cache_events = False
    manager.create_kv_cache_blocks = lambda blocks: SimpleNamespace(blocks=tuple(blocks))
    def find(keys, maximum):
        blocks = pool.get_cached_block(keys[0], [0])
        return ([blocks or []], 4 if blocks else 0, 0)
    manager.coordinator = SimpleNamespace(find_longest_cache_hit=find)
    request = SimpleNamespace(request_id=PRIVATE, cache_salt=PRIVATE, block_hashes=[PRIVATE.encode()],
        lora_request=SimpleNamespace(lora_name=PRIVATE), num_tokens=5)
    observer = callback.CacheCallbackObserver('a' * 32, context_provider=lambda: {'pid': 1, 'fixture': True})
    for module in (manager_module, pool_module):
        for point in callback.TARGETS[module.__name__]:
            wrapped = observer.wrap(module, point)
            owner, method = point.split('.'); setattr(vars(module)[owner], method, wrapped)
    return SimpleNamespace(manager=manager, pool=pool, mapping=mapping, block=block, request=request,
        manager_module=manager_module, pool_module=pool_module, observer=observer)


def test_actual_source_methods_key_entry_request_selection_exact_parent_scope(world):
    result = world.manager.get_computed_blocks(world.request)
    assert result[0].blocks[0][0] is world.block and result[1:] == (4, 0)
    report = world.observer.report(); events = report['events']
    returned = {e['point']: e for e in events if e['phase'] == 'RETURN'}
    manager = returned['KVCacheManager.get_computed_blocks']
    pool = returned['BlockPool.get_cached_block']; entry = returned['BlockHashToBlockMap.get_one_block']
    assert pool['parent_call_sha256'] == manager['call_sha256']
    assert entry['parent_call_sha256'] == pool['call_sha256']
    assert manager['fields']['request_sha256'] == pool['fields']['request_sha256'] == entry['fields']['request_sha256']
    assert manager['fields']['returned_group_blocks'][0][0] == pool['fields']['returned_blocks'][0] == entry['fields']['returned_block']
    assert entry['fields']['lookup_key_with_group_sha256']
    assert pool['fields']['cache_lookup_hit'] is True
    assert all(e['runtime_context_injected'] and not e['transitive_dependencies_verified'] for e in events)
    assert all(not e['authenticated_identity_verified'] and not e['actual_tensor_consumption_verified'] for e in events)
    assert PRIVATE not in json.dumps(report)
    values = {'isolated_cache_groups': ['first', 'second', 'all-original-groups'], 'public_prefix_sharing_allowed': True,
        'required_cache_contexts': ['model_generation', 'unsupported-context'], 'cache_identity_policy_ref': 'policy/original'}
    original = deepcopy(values); projected = callback.project(report, values)
    assert projected['policy_values'] == original == values
    assert len(projected['source_bound_returns']) == 3
    assert not projected['native_profile_supported'] and not projected['scope_complete']


def test_real_cache_miss_and_unscoped_lookup_never_fabricate_request_identity(world):
    result = world.pool.get_cached_block(b'absent', [0])
    assert result is None
    returns = [r for r in world.observer.report()['events'] if r['phase'] == 'RETURN']
    assert all(r['fields']['request_sha256'] is None for r in returns)
    assert all(r['fields']['cache_lookup_hit'] is False for r in returns)


def test_session_process_fingerprints_do_not_allow_cross_session_join(world):
    other = callback.CacheCallbackObserver('b' * 32, context_provider=lambda: {'pid': 1, 'fixture': True})
    assert other.fingerprint(PRIVATE) != world.observer.fingerprint(PRIVATE)
    assert other.report()['session_sha256'] != world.observer.report()['session_sha256']


@pytest.mark.parametrize('failure', ['native-exception', 'sink', 'method-replaced', 'file-replaced'])
def test_independent_prior_facts_survive_native_sink_source_and_entry_failure(world, failure):
    world.manager.get_computed_blocks(world.request)
    before = callback.project(world.observer.report(), {})['source_bound_returns']
    exception = RuntimeError(PRIVATE)
    if failure == 'native-exception':
        def fail(*args): raise exception
        world.manager.coordinator.find_longest_cache_hit = fail
        with pytest.raises(RuntimeError) as caught: world.manager.get_computed_blocks(world.request)
        assert caught.value is exception
        assert world.observer.report()['events'][-1]['phase'] == 'RAISE'
    elif failure == 'sink':
        def fail(*args): raise exception
        world.observer.sink = fail
        assert world.manager.get_computed_blocks(world.request)[1] == 4
        assert 'CACHE_CALLBACK_SINK_UNAVAILABLE' in world.observer.report()['missing']
    elif failure == 'method-replaced':
        wrapped = world.manager_module.KVCacheManager.get_computed_blocks
        world.manager_module.KVCacheManager.get_computed_blocks = lambda *a: None
        assert wrapped(world.manager, world.request)[1] == 4
        assert 'CACHE_CALLBACK_ENTRY_REPLACED' in world.observer.report()['missing']
    else:
        Path(world.manager_module.__file__).write_bytes(b'# replacement')
        assert world.manager.get_computed_blocks(world.request)[1] == 4
        assert 'CACHE_CALLBACK_ENTRY_UNAVAILABLE' in world.observer.report()['missing']
    after = callback.project(world.observer.report(), {})['source_bound_returns']
    assert after[:len(before)] == before
    assert PRIVATE not in json.dumps(world.observer.report())


def test_after_call_code_change_cannot_qualify_terminal_but_keeps_entry_fact(world):
    original_find = world.manager.coordinator.find_longest_cache_hit
    def changed(*args):
        result = original_find(*args)
        Path(world.manager_module.__file__).write_bytes(b'# changed-after-native-call')
        return result
    world.manager.coordinator.find_longest_cache_hit = changed
    assert world.manager.get_computed_blocks(world.request)[1] == 4
    rows = [r for r in world.observer.report()['events'] if r['point'] == 'KVCacheManager.get_computed_blocks']
    assert rows[0]['entry_qualified_before'] is True
    assert rows[-1]['entry_qualified_after'] is False and rows[-1]['fields'] == {}
    assert not any(r['point'] == 'KVCacheManager.get_computed_blocks' for r in callback.project(world.observer.report(), {})['source_bound_returns'])
    assert any(r['point'] == 'BlockPool.get_cached_block' for r in callback.project(world.observer.report(), {})['source_bound_returns'])


def test_no_raw_verified_claim_can_qualify_changed_callable_or_foreign_global(tmp_path):
    module = source_module('vllm.v1.core.block_pool', tmp_path)
    observer = callback.CacheCallbackObserver('a' * 32, context_provider=lambda: {})
    module.BlockPool.get_cached_block = lambda *a: {'verified': True}
    with pytest.raises(ValueError, match='ENTRY_UNSUPPORTED'): observer.wrap(module, 'BlockPool.get_cached_block')
    assert observer.report()['native_profile_supported'] is False and observer.report()['events'] == []


def test_bounded_record_limit_never_suppresses_original_call(world, monkeypatch):
    monkeypatch.setattr(callback, 'MAX_EVENTS', 2)
    assert world.manager.get_computed_blocks(world.request)[1] == 4
    assert len(world.observer.report()['events']) == 2
    assert 'CACHE_CALLBACK_EVENT_LIMIT' in world.observer.report()['missing']


def test_fork_inherited_observer_does_not_supply_parent_process_evidence(world, monkeypatch):
    monkeypatch.setattr(callback.os, 'getpid', lambda: world.observer.pid + 1)
    assert world.manager.get_computed_blocks(world.request)[1] == 4
    assert not callback.project(world.observer.report(), {})['source_bound_returns']
    assert 'CACHE_CALLBACK_PROCESS_CHANGED' in world.observer.report()['missing']


def test_object_identity_does_not_retain_output_or_reuse_token(world):
    value = type('Output', (), {})(); reference = weakref.ref(value)
    first = world.observer.token(value)
    del value; gc.collect()
    assert reference() is None
    second = world.observer.token(type('Output', (), {})())
    assert first != second


@pytest.mark.parametrize('mode', ['normal', 'wrong-reply-rank', 'async-thread-boundary'])
def test_actual_worker_rpc_rank_request_and_output_object_facts_are_process_local(tmp_path, mode):
    module = source_module(distributed.MP, tmp_path, targets=distributed.TARGETS, sources=distributed.rank.SOURCE_ADDITIONS)
    module.AsyncModelRunnerOutput = type('AsyncModelRunnerOutput', (), {})
    module.WorkerProc.ResponseStatus = SimpleNamespace(SUCCESS='SUCCESS', FAILURE='FAILURE')
    observer = distributed.DistributedCallbackObserver('c' * 32, context_provider=lambda: {'fixture': True})
    for point in distributed.TARGETS[module.__name__]:
        owner, method = point.split('.'); setattr(vars(module)[owner], method, observer.wrap(module, point))
    output = type('Output', (), {})(); output.req_ids = [PRIVATE]; output.sampled_token_ids = [[42]]
    scheduler = type('Scheduler', (), {})(); scheduler.num_scheduled_tokens = {PRIVATE: 4}
    queue = []
    worker = module.WorkerProc(); worker.rank = 1; worker.local_rank = 0
    worker.worker = SimpleNamespace(execute_model=lambda scheduler: output,
        model_config=SimpleNamespace(model=PRIVATE, revision=PRIVATE))
    worker.use_async_scheduling = mode == 'async-thread-boundary'
    worker.async_output_queue = SimpleNamespace(put=queue.append)
    worker.worker_response_mq = SimpleNamespace(enqueue=queue.append)
    assert worker._execute_worker_rpc(('execute_model', (scheduler,), {}, 0 if mode == 'wrong-reply-rank' else 1)) is None
    report = observer.report(); projected = distributed.project(report, {'preserve': ['entire', 'policy']})
    events = projected['source_bound_events']
    rpc = next(r for r in events if r['point'] == 'WorkerProc._execute_worker_rpc' and r['phase'] == 'CALL')
    assert rpc['fields']['rank'] == 1 and rpc['fields']['native_request_sha256']
    assert rpc['fields']['configured_model_sha256']
    if mode == 'wrong-reply-rank':
        assert not queue and not projected['same_process_call_edges']
    else:
        handled = next(r for r in events if r['point'] == 'WorkerProc.handle_output' and r['phase'] == 'CALL')
        assert handled['fields']['native_request_sha256'] == rpc['fields']['native_request_sha256']
        assert handled['fields']['output_request_sha256'] == rpc['fields']['native_request_sha256']
        assert handled['fields']['output_object_token']
        assert projected['same_process_call_edges']
        if mode == 'async-thread-boundary':
            # Actual async queue handoff has no inherited synchronous parent.
            worker.enqueue_output(queue.pop())
            terminal = observer.report()['events'][-2]
            assert terminal['parent_call_sha256'] is None and terminal['fields']['native_request_sha256'] is None
        else:
            assert queue == [('SUCCESS', output)]
    assert PRIVATE not in json.dumps(report)
    assert not projected['native_profile_supported'] and not projected['scope_complete']
    assert 'per_rank_intermediate_tensor_buffer_transport_unimplemented' in projected['missing']


def packet(cached=3):
    body = json.dumps({'model': 'example', 'choices': [{'text': PRIVATE}],
        'usage': {'prompt_tokens_details': {'cached_tokens': cached}, 'cache_read_input_tokens': 2}})
    return {'outcome': 200, 'body': body, 'body_bytes': len(body.encode()), 'body_sha256': sha256_hex(body.encode()),
        'capture_complete': True, 'eof': True, 'utf8_valid': True, 'body_truncated': False}


def plan():
    return [{'case_id': PRIVATE + str(i), 'case': case} for i, case in enumerate(
        ['normal-before', 'present', 'absent', 'present', 'absent', 'normal-after'])]


@pytest.mark.parametrize('gap', [None, 'hash', 'truncated', 'bool', 'bad-json'])
def test_actual_response_signal_fields_require_complete_packet_and_integer_counts(gap):
    value = packet(cached=True if gap == 'bool' else 3)
    if gap == 'hash': value['body_sha256'] = '0' * 64
    if gap == 'truncated': value['body_truncated'] = True
    if gap == 'bad-json':
        value['body'] = 'bad'; value['body_bytes'] = 3; value['body_sha256'] = sha256_hex(b'bad')
    signal = timing.response_signal(value)
    assert signal['cached_tokens'] == (3 if gap is None else None)
    if gap == 'bool': assert signal['cache_read_input_tokens'] == 2
    if gap in {'hash', 'truncated', 'bad-json'}: assert not signal['response_complete']
    assert PRIVATE not in json.dumps(signal)


def test_unknown_outcome_text_is_never_persisted():
    value = packet(); value['outcome'] = PRIVATE
    assert PRIVATE not in json.dumps(timing.response_signal(value))


@pytest.mark.parametrize('failure', [None, 'request', 'post-control'])
def test_repeated_real_callbacks_keep_order_counts_original_policy_and_prior_samples(failure):
    calls = []; tick = iter(range(1000))
    def probe(case_id, case):
        calls.append(case)
        if failure == 'request' and len(calls) == 4 or failure == 'post-control' and case == 'normal-after':
            raise RuntimeError(PRIVATE)
        return packet()
    values = {'minimum_repetitions': 100, 'original_threshold': None, 'unsupported_scope': ['retain', 'all']}
    original = deepcopy(values)
    result = timing.collect_timing_experiment(probe, plan(), values, clock=lambda: next(tick))
    assert calls == [p['case'] for p in plan()]
    assert len(result['trials']) == 6 and result['trials'][1]['cached_tokens'] == 3
    assert result['policy_values'] == values == original
    assert result['normal_controls'] is (failure != 'post-control')
    assert len(result['latency_distributions_ns']['present']) == (1 if failure == 'request' else 2)
    assert PRIVATE not in json.dumps(result)
    assert not result['scope_complete'] and not result['native_profile_supported']


@pytest.mark.parametrize('change', ['duplicate', 'no-control', 'no-absent', 'too-many'])
def test_bad_timing_plan_rejected_without_requests(change):
    rows = plan()
    if change == 'duplicate': rows[1]['case_id'] = rows[0]['case_id']
    if change == 'no-control': rows[0]['case'] = 'present'
    if change == 'no-absent': rows = [r for r in rows if r['case'] != 'absent']
    if change == 'too-many': rows *= 100
    with pytest.raises(ValueError, match='PLAN_INVALID'):
        timing.collect_timing_experiment(lambda *a: pytest.fail('unexpected request'), rows, {})


@pytest.mark.parametrize('unsafe', [False, True])
def test_existing_target_bound_http_collector_performs_real_transport_protocol_and_rejects_unsafe_body(unsafe):
    from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
    from tests.test_kubernetes_request_binding import target, env
    from tests.test_lifecycle_transition import HTTPWorld
    selected_env = env(); selected_target = target(selected_env); world = HTTPWorld(selected_target)
    transport = KubernetesHTTP(selected_target, selected_env, duration=60, runner=world.runner, profile=REQUEST_BINDING_PROFILE)
    requests = [{'method': 'POST', 'url': selected_target['engine']['trusted_url'] + '/v1/chat/completions',
        'body': {'model': transport.api_model, 'messages': [{'role': 'user', 'content': PRIVATE}],
            'max_tokens': 1, 'temperature': 0, 'stream': False}} for _ in plan()]
    if unsafe:
        requests[1]['body']['max_tokens'] = 100
        with pytest.raises(ValueError, match='REQUEST_INVALID'):
            timing.collect_kubernetes_timing(transport, plan(), requests, {})
        assert world.execs == []
    else:
        result = timing.collect_kubernetes_timing(transport, plan(), requests, {'original_policy': None})
        assert len(world.execs) == len(plan()) == len(result['request_id_sha256'])
        assert result['normal_controls'] and result['data_origin'] == 'EXAMPLE'
        assert not result['transport']['collection_gap']
        assert PRIVATE not in json.dumps(result)
        assert result['policy_values'] == {'original_policy': None}


@pytest.mark.parametrize('observer_kind', ['cache', 'distributed'])
@pytest.mark.parametrize('clock_failure', [1, 2, 3])
@pytest.mark.parametrize('native_raises', [False, True])
def test_observation_clock_never_changes_native_call_return_exception_or_next_parent(
        world, tmp_path, observer_kind, clock_failure, native_raises):
    calls = []; expected_exception = RuntimeError(PRIVATE); raises = [native_raises]
    if observer_kind == 'cache':
        observer = world.observer; expected_result = world.block
        key = PRIVATE.encode() + b'\x00\x00\x00\x00'
        class CountingMap(dict):
            def get(self, key, default=None):
                calls.append(key)
                if raises[0]: raise expected_exception
                return super().get(key, default)
        world.mapping._cache = CountingMap(world.mapping._cache)
        invoke = lambda: world.mapping.get_one_block(key)
        projector = callback.project
    else:
        module = source_module(distributed.MP, tmp_path, targets=distributed.TARGETS, sources=distributed.rank.SOURCE_ADDITIONS)
        module.envs = SimpleNamespace(VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=10)
        observer = distributed.DistributedCallbackObserver('d' * 32, context_provider=lambda: {'fixture': True})
        module.MultiprocExecutor.execute_model = observer.wrap(module, 'MultiprocExecutor.execute_model')
        owner = module.MultiprocExecutor(); owner.output_rank = 0
        owner.kv_output_aggregator = None; owner.ec_output_aggregator = None
        expected_result = type('Output', (), {})()
        def collective(*a, **k):
            calls.append(a)
            if raises[0]: raise expected_exception
            return expected_result
        owner.collective_rpc = collective
        scheduler = SimpleNamespace(num_scheduled_tokens={PRIVATE: 1})
        invoke = lambda: owner.execute_model(scheduler, False)
        projector = distributed.project
    ticks = []
    def clock():
        ticks.append(len(ticks) + 1)
        if len(ticks) == clock_failure: raise OSError(PRIVATE)
        return len(ticks)
    observer.clock = clock
    if native_raises:
        with pytest.raises(RuntimeError) as caught: invoke()
        assert caught.value is expected_exception
    else:
        assert invoke() is expected_result
    assert len(calls) == 1 and len(ticks) == 3
    first_report = observer.report(); entry, terminal = first_report['events']
    assert terminal['phase'] == ('RAISE' if native_raises else 'RETURN')
    assert terminal['entry_qualified_before'] and terminal['entry_qualified_after']
    assert 'CACHE_CALLBACK_CLOCK_UNAVAILABLE' in first_report['missing']
    failed_field = {1: 'operation_started_ns', 2: 'native_started_ns', 3: 'native_finished_ns'}[clock_failure]
    assert terminal[failed_field] is None
    facts = projector(first_report, {'original_policy': ['retain-all']})
    rows = facts.get('source_bound_returns', facts.get('source_bound_events'))
    if not native_raises or observer_kind == 'distributed':
        assert any(r['phase'] == terminal['phase'] for r in rows)
    else:
        assert rows == []
    raises[0] = False; observer.clock = lambda: 100
    assert invoke() is expected_result and len(calls) == 2
    next_entry = observer.report()['events'][-2]
    assert next_entry['parent_call_sha256'] is None
    assert callback._ACTIVE.get() is None
    assert PRIVATE not in json.dumps(observer.report())


@pytest.mark.parametrize('clock_failure', [1, 6, 12])
def test_timing_clock_gaps_preserve_received_response_facts_and_all_later_probes(clock_failure):
    ticks = []; calls = []
    def clock():
        ticks.append(len(ticks) + 1)
        if len(ticks) == clock_failure: raise OSError(PRIVATE)
        return len(ticks)
    def probe(case_id, case):
        calls.append((case_id, case)); return packet()
    values = {'required_cache_contexts': ['all', 'original', 'values'], 'criterion': None}
    result = timing.collect_timing_experiment(probe, plan(), values, clock=clock)
    assert len(calls) == len(result['trials']) == 6
    assert result['normal_controls'] is True
    assert all(r['callback_completed'] and r['cached_tokens'] == 3 and r['response_complete'] for r in result['trials'])
    assert result['trials'][(clock_failure - 1) // 2]['elapsed_ns'] is None
    assert 'CACHE_TIMING_CLOCK_UNAVAILABLE' in result['missing']
    assert result['policy_values'] == values
    assert PRIVATE not in json.dumps(result)


def test_caller_label_and_unsalted_label_digest_are_absent_across_experiments():
    labels = ['alice', 'bob', 'carol', 'dave', 'erin', 'frank']
    selected_plan = [{**entry, 'case_id': label} for entry, label in zip(plan(), labels)]
    results = [timing.collect_timing_experiment(lambda *a: packet(), selected_plan, {'preserve': None}) for _ in range(2)]
    for result in results:
        serialized = json.dumps(result)
        assert all(label not in serialized and sha256_hex(label.encode()) not in serialized for label in labels)
        assert all(set(row) >= {'index', 'case'} and 'case_id_sha256' not in row for row in result['trials'])
        assert result['policy_values'] == {'preserve': None}
