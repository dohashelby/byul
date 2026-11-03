"""REQ-16 lab 문맥(어댑터 내용·가중치 세대·미디어) 계약. EXAMPLE only, no engine or GPU."""
from copy import deepcopy
import base64
import json

import pytest

from guard.collectors import engine_callbacks
from guard.executors import engine_callback_projection as projection, engine_sensor
from guard.models import DataOrigin, Environment, canonical_json, sha256_hex
from guard.runtime.kubernetes_http import (ENGINE_CALLBACK_PROFILE, LORA_VARIANT_COPY, KubernetesBindingError, KubernetesHTTP,
    _callback_content)
from tests.builders import baseline_pack
from tests.test_kubernetes_request_binding import World as BindingWorld, env as binding_env, target as binding_target

LAB = {'lora': {'name': 'byul-lora', 'active_path': '/guard-lora/active',
        'variants': {'A': '/guard-lora/variants/a', 'B': '/guard-lora/variants/b'}},
    'weights': {'variants': {'gen2': '/guard-weights/gen2'}}}
SECRET_TEXT = 'adapter context prompt'
CASES = [{'case_id': 'load-a', 'role': 'switch', 'action': 'lora_reload', 'variant': 'A'},
    {'case_id': 'prime', 'role': 'prime', 'context': 'adapter_content', 'prefix': 'a', 'prompt': SECRET_TEXT, 'adapter': 'byul-lora'},
    {'case_id': 'control', 'role': 'control', 'context': 'adapter_content', 'prefix': 'a', 'prompt': SECRET_TEXT, 'adapter': 'byul-lora'},
    {'case_id': 'swap-b', 'role': 'switch', 'action': 'lora_reload', 'variant': 'B'},
    {'case_id': 'probe', 'role': 'probe', 'context': 'adapter_content', 'prefix': 'a', 'prompt': SECRET_TEXT, 'adapter': 'byul-lora'},
    {'case_id': 'post', 'role': 'post', 'prefix': 'a', 'prompt': SECRET_TEXT, 'adapter': 'byul-lora'}]


def test_cases_accept_lab_fields_only_for_req16():
    projection.validate_cases('REQ-16', CASES)
    projection.validate_cases('REQ-16', [{'case_id': 'm', 'role': 'prime', 'prompt': 'x', 'media': {'seed': 3, 'uuid': 'u-1'}}])
    for check_id, cases in (('REQ-15', [CASES[0]]), ('REQ-17', [{'case_id': 'p', 'role': 'prime', 'prompt': 'x', 'adapter': 'a'}]),
            ('REQ-16', [{'case_id': 's', 'role': 'switch', 'action': 'shell', 'variant': 'A'}]),
            ('REQ-16', [{'case_id': 's', 'role': 'switch', 'action': 'lora_reload', 'variant': 'A', 'prompt': 'x'}]),
            ('REQ-16', [{'case_id': 'm', 'role': 'prime', 'prompt': 'x', 'media': {'seed': -1}}]),
            ('REQ-16', [{'case_id': 'm', 'role': 'prime', 'prompt': 'x', 'media': {'seed': 1, 'url': 'http://x'}}])):
        with pytest.raises(ValueError, match='ENGINE_CALLBACK_CASES_INVALID'):
            projection.validate_cases(check_id, cases)


@pytest.mark.parametrize('change', ['outside-root', 'dot-dot', 'active-is-variant', 'unknown-variant', 'other-adapter', 'extra-key'])
def test_lab_declaration_is_closed(change):
    spec, cases = deepcopy(LAB), deepcopy(CASES)
    if change == 'outside-root': spec['lora']['variants']['B'] = '/models/qwen'
    elif change == 'dot-dot': spec['weights']['variants']['gen2'] = '/guard-weights/../models'
    elif change == 'active-is-variant': spec['lora']['active_path'] = spec['lora']['variants']['A']
    elif change == 'unknown-variant': cases[3]['variant'] = 'C'
    elif change == 'other-adapter': cases[1]['adapter'] = 'other-lora'
    else: spec['command'] = 'rm'
    projection.validate_lab_contexts(LAB, CASES)
    with pytest.raises(ValueError, match='ENGINE_CALLBACK_LAB_CONTEXTS_INVALID'):
        projection.validate_lab_contexts(spec, cases)


def test_media_content_is_a_reproducible_png_part():
    text = projection.message_content('hello', None)
    parts = projection.message_content('hello', {'seed': 9, 'uuid': 'same-id'})
    assert text == 'hello' and _callback_content(text) and _callback_content(parts)
    raw = base64.b64decode(parts[1]['image_url']['url'].split(',', 1)[1])
    assert raw.startswith(b'\x89PNG\r\n\x1a\n') and raw == projection.media_png(9) != projection.media_png(10)
    assert parts[1]['uuid'] == 'same-id' and projection.message_content('hello', {'seed': 9})[1].keys() == {'type', 'image_url'}
    bad = deepcopy(parts); bad[1]['image_url']['url'] = 'https://example.invalid/x.png'
    assert not _callback_content(bad) and not _callback_content([parts[0]])


class LabWorld(BindingWorld):
    """Owned-namespace label, the fixed copy program and the reload POST; nothing else."""
    def __init__(self, *, owned=True):
        value = binding_target(); value['lab'] = True
        super().__init__(value)
        self.owned = owned; self.copies = []; self.posts = []

    def runner(self, argv, **kwargs):
        if argv[3:6] == ['get', 'namespace', 'engine']:
            item = {'kind': 'Namespace', 'metadata': {'name': 'engine', 'uid': self.target['kubernetes']['engine']['namespace_uid'],
                'labels': {'byul.lab/owned': 'true'} if self.owned else {}}}
            return {'returncode': 0, 'stdout_bytes': json.dumps(item).encode(), 'stderr_bytes': b'', 'capture': {'complete': True}}
        if 'exec' in argv and LORA_VARIANT_COPY in argv:
            self.copies.append(argv[-2:])
            value = {'schema': 'guard-lab-lora-copy/1', 'files_sha256': {'adapter_config.json': 'a' * 64, 'adapter_model.safetensors': 'b' * 64}}
            return {'returncode': 0, 'stdout_bytes': json.dumps(value).encode(), 'stderr_bytes': b'', 'capture': {'complete': True}}
        if 'exec' in argv and 'input_bytes' in kwargs:
            request, = json.loads(kwargs['input_bytes'])['requests']; self.posts.append(request)
            row = {'id': request['id'], 'mode': 'json', 'method': 'POST', 'outcome': 200, 'body': '{}', 'body_bytes': 2,
                'body_sha256': sha256_hex(b'{}'), 'capture_complete': True}
            return {'returncode': 0, 'stdout_bytes': json.dumps([row]).encode(), 'stderr_bytes': b'', 'capture': {'complete': True}}
        return super().runner(argv, **kwargs)


def transport(world):
    return KubernetesHTTP(world.target, binding_env(), duration=30, runner=world.runner, profile=ENGINE_CALLBACK_PROFILE)


def chat(model, content='nonce text'):
    return [{'id': 'cb-1', 'mode': 'json', 'method': 'POST', 'url': 'http://127.0.0.1:8000/v1/chat/completions',
        'headers': {'X-Request-Id': 'byul-cb-' + 'a' * 32},
        'body': {'model': model, 'messages': [{'role': 'user', 'content': content}], 'max_tokens': 1, 'temperature': 0, 'stream': False}}]


def test_transport_switches_only_declared_variants_in_an_owned_lab():
    world = LabWorld(); http = transport(world)
    with pytest.raises(KubernetesBindingError):
        http.engine_callback_switch('lora_reload', 'B')
    http.declare_callback_lab(LAB)
    facts = http.engine_callback_switch('lora_reload', 'B')
    assert world.copies == [['/guard-lora/variants/b', '/guard-lora/active']]
    assert world.posts[-1]['url'].endswith('/v1/load_lora_adapter')
    assert world.posts[-1]['body'] == {'lora_name': 'byul-lora', 'lora_path': '/guard-lora/active', 'load_inplace': True}
    assert facts['variant_files_sha256']['adapter_model.safetensors'] == 'b' * 64 and facts['engine_outcome'] == 200
    http.engine_callback_switch('weights_reload', 'gen2')
    assert world.posts[-1]['url'].endswith('/collective_rpc')
    assert world.posts[-1]['body'] == {'method': 'reload_weights', 'kwargs': {'weights_path': '/guard-weights/gen2'}}
    assert http('kubernetes-probe', chat('byul-lora'), 5)[0]['outcome'] == 200
    assert http('kubernetes-probe', chat('fixture-model', projection.message_content('x', {'seed': 1})), 5)[0]['outcome'] == 200
    for action, variant in (('lora_reload', 'C'), ('weights_reload', 'A'), ('shell', 'A')):
        with pytest.raises(KubernetesBindingError):
            http.engine_callback_switch(action, variant)


@pytest.mark.parametrize('damage', ['unowned', 'not-lab', 'adapter-before-declare', 'other-reload-body'])
def test_transport_refuses_lab_changes_outside_the_declaration(damage):
    world = LabWorld(owned=damage != 'unowned')
    if damage == 'not-lab': world.target['lab'] = False
    http = transport(world)
    if damage in ('unowned', 'not-lab'):
        with pytest.raises(KubernetesBindingError):
            http.declare_callback_lab(LAB)
        assert not world.copies and not world.posts
    elif damage == 'adapter-before-declare':
        with pytest.raises(KubernetesBindingError):
            http('kubernetes-probe', chat('byul-lora'), 5)
    else:
        http.declare_callback_lab(LAB)
        request = {'id': 'cbx-9', 'mode': 'json', 'method': 'POST', 'headers': {}, 'url': 'http://127.0.0.1:8000/v1/load_lora_adapter',
            'body': {'lora_name': 'byul-lora', 'lora_path': '/models/qwen', 'load_inplace': True}}
        with pytest.raises(KubernetesBindingError):
            http('kubernetes-probe', [request], 5)
        assert not world.posts


class CohortTransport:
    """Records the order of switches and requests; each request returns one computed row."""
    api_model = 'EXAMPLE-model'; process_observation_origin = 'EXAMPLE'
    from types import SimpleNamespace as _NS
    endpoint = _NS(scheme='http', netloc='engine:8000')

    def __init__(self, secret, tokens, *, fail_switch=None, owned=True):
        self.secret = secret; self.tokens = tokens; self.fail_switch = fail_switch; self.owned = owned
        self.order = []; self.events = []; self.bodies = []; self.declared = None

    def declare_callback_lab(self, spec):
        if not self.owned: raise KubernetesBindingError()
        self.declared = spec

    def engine_callback_switch(self, action, variant):
        self.order.append(('switch', variant))
        if variant == self.fail_switch: raise KubernetesBindingError()
        return {'variant_files_sha256': {'adapter_model.safetensors': 'c' * 64}, 'engine_outcome': 200, 'engine_response_sha256': 'd' * 64}

    def __call__(self, network, requests, timeout):
        request, = requests; tag = request['headers']['X-Request-Id']
        if request['id'] != 'cb-9999':
            self.order.append(('request', request['id'])); self.bodies.append(request['body'])
        tokens = 0 if request['id'] == 'cb-9999' else self.tokens.pop(0)
        self.events.append({'point': projection.COMPUTED, 'phase': 'RETURN', 'entry_qualified_before': True,
            'entry_qualified_after': True, 'process_sha256': 'p', 'fields': {'request_sha256': projection.tag_digest(self.secret, tag),
                'num_computed_tokens': tokens, 'request_block_hashes_sha256': ['h'], 'request_cache_salt_sha256': None}})
        return [{'outcome': 200, 'capture_complete': True, 'elapsed_ms': 5}]

    def engine_callback_events(self, session):
        return {'events': self.events, 'truncated': False}


def run_cohort(tmp_path, tokens, *, lab=True, **options):
    env = Environment(id='ENV-REQ16-EXAMPLE', data_origin=DataOrigin.EXAMPLE, engine={'name': 'vllm', 'version': '0.31.0'}).seal()
    prepared = engine_callbacks.prepare_assets(tmp_path / 'assets')
    target = {'kubernetes': {}, 'lab': lab}
    manifest = {'schema': projection.SCHEMA, 'session': prepared['session'], 'target_sha256': sha256_hex(canonical_json(target)),
        'environment_ref': env.ref().model_dump(mode='json'), 'cases': deepcopy(CASES), 'lab_contexts': deepcopy(LAB)}
    (tmp_path / 'target.json').write_bytes(canonical_json(target)); (tmp_path / 'manifest.json').write_bytes(canonical_json(manifest))
    executor = engine_sensor.EngineSensorExecutor({'target': str(tmp_path / 'target.json'),
        'prepared': str(tmp_path / 'assets/manifest.json'), 'manifest': str(tmp_path / 'manifest.json')}, implementation='kubernetes')
    executor.transport = CohortTransport(engine_callbacks.load_secret(tmp_path / 'assets'), list(tokens), **options)
    values = {'sensor_environment_sha256': env.ref().sha256, 'sensor_target_sha256': sha256_hex(canonical_json(target)),
        'sensor_prepared_sha256': sha256_hex(canonical_json(prepared)), 'sensor_manifest_sha256': sha256_hex(canonical_json(manifest)),
        'required_cache_contexts': ['adapter_content']}
    check = baseline_pack().checks['REQ-16'].model_copy(deep=True)
    check.executor = 'engine_sensor'; check.collection_profile = engine_sensor.PROFILES['REQ-16']
    draft = executor.run(check, env, values, None, tmp_path / 'work')
    facts = json.loads(draft.artifacts[0].path.read_text(encoding='utf-8'))['safe_facts'] if draft.artifacts else None
    return draft, facts, executor.transport


@pytest.mark.parametrize('probe_tokens,violation', [(16, True), (0, False)])
def test_adapter_content_swap_reaches_the_existing_context_rule(tmp_path, probe_tokens, violation):
    draft, facts, sent = run_cohort(tmp_path, [0, 16, probe_tokens, 16])
    assert sent.order == [('switch', 'A'), ('request', 'cb-1'), ('request', 'cb-2'), ('switch', 'B'),
        ('request', 'cb-4'), ('request', 'cb-5')]
    assert all(body['model'] == 'byul-lora' for body in sent.bodies) and sent.declared == LAB
    assert draft.observations['engine_sensor_violation'].value is violation
    assert [s['variant'] for s in facts['lab_switches']] == ['A', 'B'] and all(s['completed'] for s in facts['lab_switches'])
    assert SECRET_TEXT not in json.dumps(facts)


def test_failed_switch_stops_the_context_and_stays_unknown(tmp_path):
    draft, facts, sent = run_cohort(tmp_path, [0, 16], fail_switch='B')
    assert sent.order == [('switch', 'A'), ('request', 'cb-1'), ('request', 'cb-2'), ('switch', 'B')]
    assert 'ENGINE_SENSOR_LAB_SWITCH_UNAVAILABLE' in draft.cleanup_errors and draft.status.value == 'ABORTED'
    assert draft.observations['engine_sensor_violation'].value is None
    assert facts['lab_switches'][-1] == {'case_id': 'swap-b', 'action': 'lora_reload', 'variant': 'B', 'completed': False}


def test_unowned_lab_sends_no_case(tmp_path):
    draft, facts, sent = run_cohort(tmp_path, [], owned=False)
    assert sent.order == [] and 'ENGINE_SENSOR_LAB_OWNERSHIP_UNVERIFIED' in draft.cleanup_errors
    assert draft.observations['engine_sensor_violation'].value is None and facts['lab_switches'] == []


def test_state_changing_cases_need_a_lab_target(tmp_path):
    with pytest.raises(ValueError, match='ENGINE_SENSOR_LAB_CONTEXT_REQUIRES_LAB'):
        run_cohort(tmp_path, [], lab=False)


def _copy(source, active):
    import subprocess, sys
    done = subprocess.run([sys.executable, '-I', '-c', LORA_VARIANT_COPY, str(source), str(active)],
        capture_output=True, text=True, timeout=30)
    return done.returncode, (json.loads(done.stdout) if done.returncode == 0 else None)


def test_copy_program_replaces_active_files_at_the_same_path(tmp_path):
    for name, marker in (('a', b'A'), ('b', b'B')):
        folder = tmp_path / name; folder.mkdir()
        (folder / 'adapter_config.json').write_bytes(b'{"r":8}'); (folder / 'adapter_model.safetensors').write_bytes(marker * 64)
    active = tmp_path / 'active'
    code, first = _copy(tmp_path / 'a', active)
    inode = (active / 'adapter_model.safetensors').stat().st_ino
    code_b, second = _copy(tmp_path / 'b', active)
    assert code == code_b == 0 and (active / 'adapter_model.safetensors').read_bytes() == b'B' * 64
    assert first['files_sha256']['adapter_model.safetensors'] == sha256_hex(b'A' * 64)
    assert second['files_sha256']['adapter_model.safetensors'] == sha256_hex(b'B' * 64)
    assert (active / 'adapter_model.safetensors').stat().st_ino != inode and sorted(p.name for p in active.iterdir()) == [
        'adapter_config.json', 'adapter_model.safetensors']


def test_copy_program_refuses_missing_or_linked_variants(tmp_path):
    (tmp_path / 'c').mkdir(); (tmp_path / 'c' / 'adapter_config.json').write_bytes(b'{}')
    assert _copy(tmp_path / 'c', tmp_path / 'active')[0] != 0
    (tmp_path / 'c' / 'adapter_model.safetensors').symlink_to(tmp_path / 'c' / 'adapter_config.json')
    assert _copy(tmp_path / 'c', tmp_path / 'active2')[0] != 0


def _model(root, norm=0.5):
    import struct
    from tools import gpu_a_lab_variants as variants
    root.mkdir()
    (root / 'config.json').write_text(json.dumps({'hidden_size': 8, 'num_hidden_layers': 2, 'num_attention_heads': 2,
        'num_key_value_heads': 1, '_name_or_path': 'EXAMPLE-model'}))
    (root / 'tokenizer.json').write_text('{}')
    bf16 = lambda value: struct.pack('<f', value)[2:]
    variants.write_safetensors(root / 'model.safetensors', {'model.embed.weight': ('F32', (2, 2), struct.pack('<4f', 1, 2, 3, 4)),
        'model.norm.weight': ('BF16', (8,), bf16(norm) * 8)})
    return variants


def _tensors(path):
    import struct
    raw = path.read_bytes(); size = struct.unpack('<Q', raw[:8])[0]; header = json.loads(raw[8:8 + size])
    return {k: (v['dtype'], v['shape'], raw[8 + size + v['data_offsets'][0]:8 + size + v['data_offsets'][1]])
        for k, v in header.items() if k != '__metadata__'}


def test_lora_variants_follow_the_model_config_and_differ_only_in_content(tmp_path):
    variants = _model(tmp_path / 'model')
    result = variants.lora(tmp_path / 'model', tmp_path / 'lora', 7)
    again = variants.lora(tmp_path / 'model', tmp_path / 'again', 7)
    a, b = (_tensors(tmp_path / 'lora' / n / 'adapter_model.safetensors') for n in ('a', 'b'))
    key = 'base_model.model.model.layers.1.self_attn.v_proj.lora_B.weight'
    assert a.keys() == b.keys() and len(a) == 2 * 2 * 2 and a[key][:2] == ('F16', [4, 8]) and a[key][2] != b[key][2]
    assert result['variants'] == again['variants'] and result['data_origin'] == 'EXAMPLE'
    config = json.loads((tmp_path / 'lora/a/adapter_config.json').read_text())
    assert config['r'] == 8 and config['target_modules'] == ['q_proj', 'v_proj'] and config['base_model_name_or_path'] == 'EXAMPLE-model'


def test_weight_generation_changes_only_the_named_tensor(tmp_path):
    variants = _model(tmp_path / 'model')
    result = variants.weights(tmp_path / 'model', tmp_path / 'gen2')
    before, after = _tensors(tmp_path / 'model/model.safetensors'), _tensors(tmp_path / 'gen2/model.safetensors')
    assert after['model.embed.weight'] == before['model.embed.weight'] and after['model.norm.weight'] != before['model.norm.weight']
    assert after['model.norm.weight'][2] == variants.ONE['BF16'] * 8 and result['changed']['tensor'] == 'model.norm.weight'
    assert (tmp_path / 'gen2/tokenizer.json').read_text() == '{}'
    one = _model(tmp_path / 'ones', norm=1.0)
    with pytest.raises(ValueError, match='LAB_VARIANT_TENSOR_ALREADY_ONE'):
        one.weights(tmp_path / 'ones', tmp_path / 'gen3')
    with pytest.raises(ValueError, match='LAB_VARIANT_TENSOR_MISSING'):
        variants.weights(tmp_path / 'model', tmp_path / 'gen4', tensor='model.missing')
