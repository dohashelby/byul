"""태그의 실제 함수 본문과 EXAMPLE 협력 객체만 실행한다. 엔진/GPU 실행은 아니다."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time
import types
from types import SimpleNamespace

import pytest

from guard.adapters import vllm_lifecycle_catalog as catalog
from guard.adapters import vllm_lifecycle_sensor as legacy
from guard.adapters.vllm_lifecycle_links import fresh_assignment
from guard.adapters.vllm_observation_state import project as project_observation_state
from guard.adapters.vllm_request_attempts import project_attempts
from guard.adapters.vllm_request_termination import project_native as project_termination
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture
from guard.executors import kubernetes_request_termination as termination_executor
from guard.executors import kubernetes_request_attempts as attempts_executor
from guard.executors import kubernetes_observation_state as observation_executor
from guard.models import utcnow
from tests.test_configured_lifecycle import native_class
from tests.test_lifecycle_capture import external  # noqa: F401
from tests.test_lifecycle_sensor import CONTEXT, FakeOS, SilentThread

ROOT = Path(__file__).resolve().parents[1]
PROFILE = 'vllm-native-lifecycle-metadata/28'
SECRET = 'EXAMPLE-private-request-id'


@pytest.fixture
def world(external):  # noqa: F811
    return make_world(external)


def make_world(external):
    folder = external / 'assets'
    prepared = lifecycle.prepare_assets(folder, ROOT.parent, profile=PROFILE)
    assert lifecycle.load_prepared(folder / 'manifest.json') == prepared
    module = types.ModuleType('_fresh_request_binding_example')
    module.__file__ = str(folder / 'guard_lifecycle_sensor.py')
    exec(compile((folder / 'guard_lifecycle_sensor.py').read_bytes(), module.__file__, 'exec', dont_inherit=True), module.__dict__)
    module._dependency = lambda: Path(legacy.__file__).read_bytes()
    original_open = os.open
    module.os = SimpleNamespace(**(vars(os) | {'open': lambda p, f, *a, **kw:
        original_open(str(folder) if p == '/guard-lifecycle' else p, f, *a, **kw)}))
    base = module._private_base()
    fake = FakeOS(); base.os = fake; base._context = lambda: dict(CONTEXT); base._open_directory = lambda p: 10
    base.threading = SimpleNamespace(RLock=threading.RLock, Thread=SilentThread, Event=threading.Event)
    settings = json.loads((folder / 'settings.json').read_bytes())
    observer = base.Observer(settings, prepared['assets']['settings.json']['sha256']); base._OBSERVER = observer
    for name, digest in base.SOURCE_SHA.items():
        observer.modules[name] = {'source_sha256': digest, 'code_sha256': 'c' * 64}
        observer.emit('MODULE', {'module': name, 'verified': True, **observer.modules[name]})
    return SimpleNamespace(root=external, folder=folder, prepared=prepared, module=module, base=base,
                           fake=fake, observer=observer)


def program(world):
    """실제 handler→generate→add_request→process_inputs 본문을 무변경 AST로 실행한다."""
    class Params:
        def clone(self): return self
        def update_from_generation_config(self, *a): pass
    params = Params()
    adapter = SimpleNamespace(prompt='EXAMPLE-private-prompt', prompt_token_ids=[17],
        multi_modal_hashes=None, multi_modal_data=None, multi_modal_placeholders=None)
    processor_class = native_class('vllm.v1.engine.processor', 'Processor', ['process_inputs'], time=time,
        SamplingParams=Params, is_encoder_decoder_inputs=lambda v: False,
        SingletonInputsAdapter=lambda v: adapter, EngineCoreRequest=SimpleNamespace, MultiModalKwargs=type('MM', (), {}))
    processor = processor_class.__new__(processor_class)
    processor.lora_config = None; processor.use_hash = False; processor.generation_config_fields = {}
    processor.input_preprocessor = SimpleNamespace(preprocess=lambda *a, **kw: {}, get_eos_token_id=lambda v: 1)
    processor.input_processor = lambda value: value; processor._validate_model_inputs = lambda value: None
    engine_class = native_class('vllm.v1.engine.async_llm', 'AsyncLLM', ['add_request', 'generate'], asyncio=asyncio,
        RequestOutputKind=SimpleNamespace(DELTA='delta'))
    engine = engine_class.__new__(engine_class); engine.processor = processor; engine.output_handler = object()
    engine.log_requests = False; engine.errored = False
    queues = {}
    engine.output_processor = SimpleNamespace(is_request_active=lambda rid: False,
        add_request=lambda req, q: queues.__setitem__(req.request_id, q))
    async def add_core(req):
        queues[req.request_id].put_nowait(SimpleNamespace(finished=True, prompt='EXAMPLE-private-output'))
    engine.engine_core = SimpleNamespace(add_request_async=add_core)
    async def tokenizer(*a): return object()
    engine.get_tokenizer = tokenizer
    async def merge(*generators):
        for index, generator in enumerate(generators):
            async for value in generator: yield index, value
    handler_class = native_class('vllm.entrypoints.openai.serving_completion', 'OpenAIServingCompletion',
        ['create_completion'], time=time, RequestResponseMetadata=SimpleNamespace, BeamSearchParams=type('Beam', (), {}),
        merge_async_iterators=merge, cast=lambda kind, value: value, asyncio=asyncio, List=list, RequestOutput=SimpleNamespace)
    serving_class = native_class('vllm.entrypoints.openai.serving_engine', 'OpenAIServing', ['_base_request_id', '_log_inputs'],
        random_uuid=lambda: 'EXAMPLE-generated-request')
    handler = handler_class.__new__(handler_class); handler.engine_client = engine; handler.max_model_len = 64
    async def model(*a): return None
    async def prompts(*a, **kw): return ['EXAMPLE-private-prompt'], [{'prompt_token_ids': [17]}]
    handler._check_model = model; handler._get_trace_headers = model; handler._preprocess_completion = prompts
    handler._maybe_get_adapters = lambda req: (None, None); handler.request_logger = None
    handler.model_config = SimpleNamespace(get_diff_sampling_param=lambda: {}, logits_processor_pattern=None)
    handler.models = SimpleNamespace(model_name=lambda v: 'EXAMPLE-model')
    result = object(); handler.request_output_to_completion_response = lambda *a: result
    spec = catalog.row(PROFILE)['request_binding']
    for cls, name, point in [(handler_class, 'create_completion', spec['handler_point']),
                            (engine_class, 'add_request', spec['caller_point']),
                            (processor_class, 'process_inputs', spec['input_point'])]:
        source = next(name for name, row in catalog.row(PROFILE)['sources'].items()
                      if point in {c + '.' + method for c, methods in row['classes'].items() for method in methods})
        setattr(cls, name, world.observer.wrap(source, point, vars(cls)[name]))
    handler_class._base_request_id = staticmethod(world.observer.wrap('vllm.entrypoints.openai.serving_engine',
        spec['selection_point'], vars(serving_class)['_base_request_id'].__func__))
    handler_class._log_inputs = world.observer.wrap('vllm.entrypoints.openai.serving_engine',
        spec['native_selection_point'], vars(serving_class)['_log_inputs'])
    request = SimpleNamespace(suffix=None, prompt='EXAMPLE-private-prompt', truncate_prompt_tokens=None,
        add_special_tokens=True, use_beam_search=False, priority=0, stream=False, best_of=None, n=1,
        to_sampling_params=lambda *a: params)
    return SimpleNamespace(handler=handler, engine=engine, processor=processor, params=params, request=request, result=result)


def run_request(world, app, *, header=True, method='POST', receive=None):
    async def forbidden(*a): raise AssertionError('관측기가 추가 receive/send를 호출했다')
    scope = {'type': 'http', 'path': '/v1/completions', 'root_path': '', 'method': method,
             'headers': [(b'x-request-id', SECRET.encode())] if header else []}
    return asyncio.run(world.base.Middleware(app)(scope, receive or forbidden, forbidden))


def recording(world):
    world.observer._writer().close('SEALED'); raw = bytes(world.fake.data)
    assert all(value not in raw for value in (SECRET.encode(), b'EXAMPLE-private-prompt', b'EXAMPLE-private-output'))
    key = json.loads((world.folder / 'request-binding-key.json').read_bytes())['key_hex']
    assert key.encode() not in raw
    return capture.parse_recording(raw, world.prepared), raw


@pytest.mark.parametrize('header', [True, False])
def test_actual_tag_handler_and_native_input_have_one_fresh_hmac_link(world, header):
    state = program(world)
    async def app(scope, receive, send):
        raw = SimpleNamespace(scope=scope, headers={'X-Request-Id': SECRET} if header else {}, state=SimpleNamespace())
        return await state.handler.create_completion(state.request, raw)
    assert run_request(world, app, header=header) is state.result
    (rows, summary), raw = recording(world)
    journal = summary['fresh_request_binding']
    assert journal['selection_callbacks'] == journal['native_calls'] == journal['closed_native_calls'] == 1
    assert journal['closed_asgi_invocations'] == 1 and journal['missing'] == []
    assert not journal['question_scope_complete'] and not journal['producer_authenticated'] and not journal['worker_connection_verified']
    samples = [r['payload']['metadata']['request_binding'] for r in rows if 'request_binding' in r['payload'].get('metadata', {})]
    assert journal['native_selections'] == 1
    native = [r for r in samples if r['native_call_sha256'] is not None]
    assert len(native) == 2 and native[0] == native[1]
    if header:
        key = bytes.fromhex(json.loads((world.folder / 'request-binding-key.json').read_bytes())['key_hex'])
        expected = hmac.new(key, ('guard-native-request-binding/1\0native-request\0cmpl-' + SECRET + '-0').encode(), hashlib.sha256).hexdigest()
        assert native[0]['native_request_sha256'] == expected
        assert hashlib.sha256(SECRET.encode()).hexdigest().encode() not in raw


def test_actual_fresh_binding_feeds_limited_observation_and_attempt_projections(world):
    state = program(world)
    async def app(scope, receive, send):
        raw = SimpleNamespace(scope=scope, headers={'X-Request-Id': SECRET}, state=SimpleNamespace())
        return await state.handler.create_completion(state.request, raw)
    assert run_request(world, app) is state.result
    (rows, summary), _ = recording(world)
    request = lifecycle.request_header_fingerprint(world.folder / 'manifest.json', world.prepared, SECRET)
    selected = fresh_assignment(rows, request)
    assert len(selected) == 1 and selected[0][1]['asgi_request_sha256'] == request
    assert fresh_assignment(rows, hashlib.sha256(SECRET.encode()).hexdigest()) == []
    packet = {'recorded_files_covered': True, 'recordings': [{'capture': {
        'records': rows, 'summary': summary, 'process_context_verified': True,
        'image_digest_verified': False}, 'session_owner_match': True, 'listed_file_identity_match': True}]}
    trial = {'case_id': 'obs02-normal-before', 'attempted': True, 'method': 'POST',
             'request_id_sha256': request}
    observation = project_observation_state(packet, [trial], ['request_selection'],
                                            binding_hashes={trial['case_id']: request})
    assert len(observation['bindings']) == 1
    assert observation['bindings'][0]['native_request_sha256'] == selected[0][1]['native_request_sha256']
    selection = next(row for row in observation['component_status'] if row['component'] == 'request_selection')
    assert selection['bound_events'] == 1
    assert not observation['whole_scope_complete'] and not observation['producer_authenticated']
    attempt = project_attempts(packet, [{'case_id': 'req12-disconnect', 'attempted': True,
        'external_request_sha256': request, 'auxiliary_session_sha256': 'a' * 64}],
        binding_hashes={'req12-disconnect': request})
    assert len(attempt['bindings']) == 1 and not attempt['native_attempt_binding_observed']
    assert 'REQUEST_ATTEMPT_NATIVE_ATTEMPT_UNOBSERVED' in attempt['diagnostic_codes']
    assert not attempt['native_output_collector_binding_observed'] and not attempt['whole_scope_complete']
    assert hashlib.sha256(SECRET.encode()).hexdigest() not in json.dumps(
        {'trials': [trial], 'observation': observation, 'attempt': attempt})


@pytest.mark.parametrize('disconnect', [True, False])
def test_actual_fresh_binding_requires_genuine_asgi_disconnect_for_termination_anchor(world, disconnect):
    state = program(world)
    async def receive():
        return {'type': 'http.disconnect'}
    async def app(scope, receive_callback, send):
        raw = SimpleNamespace(scope=scope, headers={'X-Request-Id': SECRET}, state=SimpleNamespace())
        result = await state.handler.create_completion(state.request, raw)
        if disconnect:
            assert await receive_callback() == {'type': 'http.disconnect'}
        return result
    assert run_request(world, app, receive=receive if disconnect else None) is state.result
    (rows, summary), _ = recording(world)
    request = lifecycle.request_header_fingerprint(world.folder / 'manifest.json', world.prepared, SECRET)
    packet = {'recorded_files_covered': True, 'recordings': [{'capture': {
        'records': rows, 'summary': summary, 'process_context_verified': True,
        'image_digest_verified': False}, 'session_owner_match': True, 'listed_file_identity_match': True}]}
    projected = project_termination(packet, request, 500,
                                    ['asgi_transport'], request_hmac=request)
    assert projected['binding_observed'] is disconnect
    if disconnect:
        assert projected['anchor']['native_request_sha256'] == fresh_assignment(rows, request)[0][1]['native_request_sha256']
        assert projected['anchor']['asgi_request_sha256'] == request
        assert {event['scope'] for event in projected['events']} == {'asgi_transport'}
        assert projected['scope_status'][0]['termination_verified'] is False
    else:
        assert projected['anchor'] is None and projected['events'] == []
    assert not projected['worker_gpu_release_verified'] and not projected['whole_scope_complete']
    assert all(not row['termination_verified'] for row in projected['scope_status'])
    assert hashlib.sha256(SECRET.encode()).hexdigest() not in json.dumps(projected)


@pytest.mark.parametrize('module,executor,rows_field,identity_field,gap', [
    (termination_executor, termination_executor.KubernetesRequestTerminationExecutor,
     'attempts', 'request_id_sha256', 'TERMINATION_REQUEST_BINDING_KEY_UNAVAILABLE'),
    (attempts_executor, attempts_executor.KubernetesRequestAttemptsExecutor,
     'trials', 'external_request_sha256', 'REQUEST_ATTEMPT_BINDING_KEY_UNAVAILABLE'),
    (observation_executor, observation_executor.KubernetesObservationStateExecutor,
     'trials', 'request_id_sha256', 'OBSERVATION_STATE_REQUEST_BINDING_KEY_UNAVAILABLE'),
])
def test_invalid_private_key_leaves_trial_unidentified_and_never_dispatches_http(
        world, monkeypatch, module, executor, rows_field, identity_field, gap):
    key = world.folder / 'request-binding-key.json'
    key.chmod(0o644)
    with pytest.raises(ValueError, match='REQUEST_BINDING_KEY_UNAVAILABLE'):
        lifecycle.request_header_fingerprint(world.folder / 'manifest.json', world.prepared, SECRET)
    calls = []
    engine = {'container': 'engine', 'image': 'example@sha256:' + 'a' * 64}
    class Reader:
        def __init__(self, *args, **kwargs):
            self.binding = {'engine': engine}
            self.owner_identity = self.read_file = self.image_check = None
        def snapshot(self): pass
        def seal_session(self): return {'sealed': True}
        def report(self): return {}
    class Transport:
        binding = {'run_id': 'EXAMPLE-run', 'context': 'EXAMPLE-context', 'engine': engine}
        def __call__(self, *args):
            calls.append(args)
            raise AssertionError('HTTP dispatch after invalid key')
        def report(self): return {'requests': [], 'collection_gap': False, 'cleanup_errors': []}
    monkeypatch.setattr(module, 'KubernetesLifecycleReader', Reader)
    monkeypatch.setattr(module, 'capture_session', lambda *a, **kw: (_ for _ in ()).throw(OSError('EXAMPLE capture unavailable')))
    now = utcnow(); start = (now - timedelta(minutes=1)).isoformat(); end = (now + timedelta(minutes=1)).isoformat()
    if module is termination_executor:
        values = {'termination_environment_sha256': 'a' * 64, 'termination_target_sha256': 'b' * 64,
                  'termination_validity_start': start, 'termination_validity_end': end,
                  'termination_max_delay_ms': 1, 'termination_required_scopes': ['asgi_transport']}
    elif module is attempts_executor:
        values = {'attempt_environment_sha256': 'a' * 64, 'attempt_target_sha256': 'b' * 64,
                  'attempt_validity_start': start, 'attempt_validity_end': end}
    else:
        values = {'observation_state_environment_sha256': 'a' * 64,
                  'observation_state_target_sha256': 'b' * 64,
                  'observation_state_validity_start': start, 'observation_state_validity_end': end,
                  'observation_state_required_components': ['request_selection']}
    from tests.test_kubernetes_request_binding import env as sample_env
    context = SimpleNamespace(policy_values=values, target={'lab': True}, cprobe=Transport(),
        env=sample_env(), api_model='EXAMPLE-model', engine_url='http://engine:8000', timeout=10,
        _termination_prepared=world.prepared)
    _, _, _, summary = executor({'prepared': str(world.folder / 'manifest.json')}).collect(context)
    assert not calls and gap in summary['diagnostic_codes']
    assert summary[rows_field] and all(row[identity_field] is None and row['attempted'] is False
                                       for row in summary[rows_field])
    assert context._termination_gap is True
    legacy_context = SimpleNamespace(**(vars(context) | {'_termination_prepared':
        {**world.prepared, 'profile': legacy.PROFILE}}))
    _, _, _, old_summary = executor({'prepared': str(world.folder / 'manifest.json')}).collect(legacy_context)
    assert calls
    legacy_header = calls[0][1][0]['headers']['X-Request-Id']
    assert old_summary[rows_field][0][identity_field] == hashlib.sha256(legacy_header.encode()).hexdigest()


def test_independent_sessions_do_not_publish_the_same_request_fingerprint(external):  # noqa: F811
    fingerprints = []
    for label in ('first', 'second'):
        folder = external / label; folder.mkdir()
        world = make_world(folder); state = program(world)
        async def app(scope, receive, send):
            return await state.handler.create_completion(state.request,
                SimpleNamespace(scope=scope, headers={'X-Request-Id': SECRET}, state=SimpleNamespace()))
        assert run_request(world, app) is state.result
        (rows, summary), _ = recording(world)
        assert summary['fresh_request_binding']['closed_native_calls'] == 1
        fingerprints.append(next(row['payload']['metadata']['request_binding']['native_request_sha256']
            for row in rows if row['event'] == 'NATIVE' and row['payload']['point'] == 'Processor.process_inputs'))
    assert fingerprints[0] != fingerprints[1]


@pytest.mark.parametrize('damage', ['source', 'scope', 'caller', 'owner', 'different-id'])
def test_matching_context_or_string_does_not_substitute_native_source_frame_owner_and_scope(world, damage):
    state = program(world)
    if damage == 'source': world.observer.modules.pop('vllm.v1.engine.processor')
    if damage == 'caller':
        async def generate(prompt, params, request_id, **kwargs):
            state.processor.process_inputs(request_id if damage == 'caller' else 'cmpl-other-0', prompt, params)
            yield SimpleNamespace(finished=True, prompt='EXAMPLE-private-output')
        state.engine.generate = generate
    if damage == 'different-id':
        generate = state.engine.generate
        state.engine.generate = lambda prompt, params, request_id, **kw: generate(
            prompt, params, request_id.rsplit('-', 1)[0] + '-999', **kw)
    if damage == 'owner':
        state.engine.processor = SimpleNamespace(process_inputs=state.processor.process_inputs)
    async def app(scope, receive, send):
        raw = SimpleNamespace(scope={**scope} if damage == 'scope' else scope,
            headers={'X-Request-Id': SECRET}, state=SimpleNamespace())
        return await state.handler.create_completion(state.request, raw)
    assert run_request(world, app) is state.result
    (rows, summary), _ = recording(world)
    assert summary['fresh_request_binding']['native_calls'] == 0
    assert 'REQUEST_BINDING_NATIVE_CALL_UNOBSERVED' in summary['fresh_request_binding']['missing']


def test_secret_key_is_session_bound_private_and_old_profile_bytes_stay_unchanged(world):
    key = world.folder / 'request-binding-key.json'
    assert key.stat().st_mode & 0o777 == 0o600
    assert len(key.read_bytes()) <= 256
    config = json.loads((world.folder / 'settings.json').read_bytes()); config['session'] = '0' * 32
    with pytest.raises(ValueError, match='REQUEST_BINDING_KEY_SESSION_MISMATCH'):
        world.base.Observer(config, 'e' * 64)
    assert catalog.render_source('vllm-native-lifecycle-metadata/27').startswith(Path(catalog.template.__file__).read_bytes())
    assert b'guard_fresh_request_binding_call' not in catalog.render_source('vllm-native-lifecycle-metadata/27')


def test_background_task_cannot_reuse_a_closed_asgi_invocation(world):
    state = program(world)
    async def scenario():
        go = asyncio.Event(); task = None
        async def late():
            await go.wait()
            # 같은 선택 ID라도 ASGI 종료 뒤 상속된 context는 거부한다.
            return await state.engine.add_request('cmpl-' + SECRET + '-0', {}, state.params)
        async def app(scope, receive, send):
            nonlocal task
            raw = SimpleNamespace(scope=scope, headers={'X-Request-Id': SECRET}, state=SimpleNamespace())
            result = await state.handler.create_completion(state.request, raw)
            task = asyncio.create_task(late())
            return result
        async def forbidden(*args): raise AssertionError('추가 ASGI IO 금지')
        scope = {'type': 'http', 'path': '/v1/completions', 'method': 'POST', 'root_path': '',
                 'headers': [(b'x-request-id', SECRET.encode())]}
        assert await world.base.Middleware(app)(scope, forbidden, forbidden) is state.result
        go.set()
        assert isinstance(await task, asyncio.Queue)
    asyncio.run(scenario())
    (rows, summary), _ = recording(world)
    assert summary['fresh_request_binding']['native_calls'] == summary['fresh_request_binding']['closed_native_calls'] == 1
    assert sum(r['event'] == 'ASGI' and r['payload']['phase'] == 'APP_RETURN' for r in rows) == 1


def test_streaming_native_work_stays_fresh_until_the_actual_asgi_app_returns(world):
    state = program(world); state.request.stream = True
    sent = []
    async def stream(request, generator, *args, **kwargs):
        async for _, value in generator:
            assert value.finished
            yield 'EXAMPLE-private-output'
    state.handler.completion_stream_generator = stream
    async def app(scope, receive, send):
        output = await state.handler.create_completion(state.request,
            SimpleNamespace(scope=scope, headers={'X-Request-Id': SECRET}, state=SimpleNamespace()))
        async for value in output:
            await send({'type': 'http.response.body', 'body': value.encode(), 'more_body': False})
    async def scenario():
        async def forbidden(): raise AssertionError('추가 ASGI receive 금지')
        async def send(message): sent.append(message)
        await world.base.Middleware(app)({'type': 'http', 'path': '/v1/completions', 'method': 'POST',
            'root_path': '', 'headers': [(b'x-request-id', SECRET.encode())]}, forbidden, send)
    asyncio.run(scenario())
    (rows, summary), _ = recording(world)
    assert len(sent) == 1 and sent[0]['body'] == b'EXAMPLE-private-output'
    assert summary['fresh_request_binding']['closed_native_calls'] == 1
    assert summary['fresh_request_binding']['missing'] == []


def test_observation_preserves_the_identical_original_exception(world):
    state = program(world); error = RuntimeError(SECRET)
    def broken(*args): raise error
    state.processor._validate_model_inputs = broken
    async def app(scope, receive, send):
        return await state.handler.create_completion(state.request,
            SimpleNamespace(scope=scope, headers={'X-Request-Id': SECRET}, state=SimpleNamespace()))
    with pytest.raises(RuntimeError) as caught:
        run_request(world, app)
    assert caught.value is error
    (rows, summary), _ = recording(world)
    samples = [row['payload'] for row in rows if row['event'] == 'NATIVE'
               and row['payload']['point'] == 'Processor.process_inputs']
    assert [row['phase'] for row in samples] == ['CALL', 'RAISE']
    assert summary['fresh_request_binding']['closed_native_calls'] == 1
    assert any(row['event'] == 'ASGI' and row['payload']['phase'] == 'APP_RAISE' for row in rows)


def test_observer_never_runs_a_native_processor_property(world):
    state = program(world); accesses = []
    engine_type = type(state.engine)
    del state.engine.processor
    def processor(owner): accesses.append('native'); return state.processor
    engine_type.processor = property(processor)
    async def app(scope, receive, send):
        return await state.handler.create_completion(state.request,
            SimpleNamespace(scope=scope, headers={'X-Request-Id': SECRET}, state=SimpleNamespace()))
    assert run_request(world, app) is state.result
    assert accesses == ['native']
    (rows, summary), _ = recording(world)
    assert summary['fresh_request_binding']['native_calls'] == 0


@pytest.mark.parametrize('damage', ['mode', 'symlink', 'bytes', 'duplicate'])
def test_private_key_permissions_link_bytes_and_duplicate_fields_are_rejected(world, damage):
    key = world.folder / 'request-binding-key.json'
    if damage == 'mode': key.chmod(0o644)
    elif damage == 'symlink':
        target = world.root / 'key-copy.json'; key.rename(target); key.symlink_to(target)
    elif damage == 'bytes': key.write_bytes(key.read_bytes() + b' ')
    else:
        key.write_text('{"schema":"guard-lifecycle-request-key/1","session":"' + world.prepared['session']
            + '","key_hex":"' + '0' * 64 + '","key_hex":"' + '1' * 64 + '"}')
    with pytest.raises((ValueError, OSError)):
        lifecycle.load_prepared(world.folder / 'manifest.json')
    with pytest.raises((ValueError, OSError)):
        world.module._private_base()


def reencode(rows):
    previous = '0' * 64; output = []
    for index, row in enumerate(rows):
        row = {**row, 'seq': index, 'previous_sha256': previous}
        raw = capture.canonical_row(row); output.append(raw); previous = hashlib.sha256(raw).hexdigest()
    return b''.join(output)


@pytest.mark.parametrize('damage', ['late', 'authentication', 'worker', 'extra-field'])
def test_recording_cannot_replay_fresh_claims_after_asgi_end_or_expand_the_claim(world, damage):
    state = program(world)
    async def app(scope, receive, send):
        return await state.handler.create_completion(state.request,
            SimpleNamespace(scope=scope, headers={'X-Request-Id': SECRET}, state=SimpleNamespace()))
    assert run_request(world, app) is state.result
    (rows, _), _ = recording(world)
    rows = deepcopy(rows)
    target = next(r for r in rows if r['event'] == 'NATIVE' and r['payload']['point'] == 'Processor.process_inputs'
                  and r['payload']['phase'] == 'CALL')
    if damage == 'late':
        target = deepcopy(target); target['monotonic_ns'] = rows[-1]['monotonic_ns']
        rows.insert(-1, target)
    else:
        metadata = target['payload']['metadata']['request_binding']
        if damage == 'authentication': metadata['producer_authenticated'] = True
        elif damage == 'worker': metadata['worker_connection_verified'] = True
        else: metadata['key_hex'] = '0' * 64
    with pytest.raises(ValueError, match='REQUEST_BINDING|LIFECYCLE_RECORDING_ROW_INVALID'):
        capture.parse_recording(reencode(rows), world.prepared)
