"""REQ-08·09·SUP-08 Kubernetes 전송의 닫힌 요청 계약. EXAMPLE target만 쓰고 kubectl은 부르지 않는다."""
import json
import os
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from guard.collectors.file import load_environment, load_policy
from guard.executors.kubernetes_input_validation import KubernetesInputValidationExecutor, _WithBody, _contract
from guard.executors.kubernetes_normal_token_input import KubernetesNormalTokenInputExecutor
from guard.executors.stream_probe import _prepare
from guard.executors.registry import EXECUTORS
from guard.input_matrix_policy import planned
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE, KubernetesBindingError, KubernetesHTTP
from tests.builders import baseline_pack, evidence
from tests.test_gpu_a_run_inputs import UPDATE, build
from tests.test_kubernetes_sampling import World, normal_sampling_inputs
from tools import gpu_a_run_inputs as inputs

TOKENS = {'model': 'test-model', 'prompt': [0], 'max_tokens': 1}
CHAT = {'model': 'test-model', 'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 1, 'temperature': 0}
NORMAL_INPUT_POLICY = {'allowed_endpoints': ['POST /v1/completions', 'POST /v1/chat/completions'],
                       'allowed_input_formats': ['text', 'token_ids'],
                       'allowed_input_pairs': ['POST /v1/completions|token_ids', 'POST /v1/completions|text',
                                               'POST /v1/chat/completions|text'],
                       'max_input_tokens': 127, 'rejection_codes': ['400', '422']}


def _no_kubectl(*args, **kwargs):
    raise AssertionError('no kubectl in contract tests')


@pytest.mark.parametrize('version,fault', [('0.30.0', None), ('0.31.0', None), ('0.31.0', 'duplicate'),
                                           ('0.30.0', 'non-integer'), ('0.30.0', 'owner-changed'),
                                           ('0.31.0', 'window-mismatch')])
def test_req09_normal_reuses_only_ids_from_verified_tokenizer(tmp_path, version, fault):
    from guard.adapters.vllm import VllmAdapter
    from guard.core.run import _normalize
    from guard.judges.rule_table import RuleTableJudge

    requests = []
    private = 'EXAMPLE-private-completion-text'
    token_ids = [23, 41]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, self.headers.get('Authorization'), body))
            if self.path == '/tokenize':
                token_response = {'count': 2, 'tokens': [True, 41] if fault == 'non-integer' else token_ids,
                                  'max_model_len': 256 if fault == 'window-mismatch' else 128}
                raw = json.dumps(token_response).encode()
                if fault == 'duplicate':
                    raw = raw[:-1] + b',"count":2}'
            else:
                raw = json.dumps({'model': 'fixture-model', 'choices': [{'text': private, 'finish_reason': 'length'}],
                                  'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env, target, _, path = normal_sampling_inputs(tmp_path, version)
        env = env.model_copy(update={
            'model': env.model.model_copy(update={'inventory_sha256': 'a' * 64}),
            'runtime': env.runtime.model_copy(update={'gpu_count': 1, 'gpu_model': 'EXAMPLE GPU', 'driver': 'EXAMPLE driver'}),
        }).seal()
        target['environment_ref'] = env.ref().model_dump(mode='json')
        target['engine']['trusted_url'] = f'http://127.0.0.1:{server.server_port}'
        target['comparison_config'] = {'launch': {'max_model_len': 128}}
        path.write_bytes(canonical_json(target))
        world = World(target)
        world.actual_probe = True
        if fault == 'owner-changed':
            def change_owner(role, pod, after):
                if role == 'engine' and after:
                    pod['metadata']['uid'] = 'replacement'
            world.mutate = change_owner
        check = load_pack(UPDATE / 'packs/PACK-BASELINE').checks['REQ-09']
        executor = KubernetesNormalTokenInputExecutor({'normal_token_input_only': True, 'target': str(path)},
            runner=world.runner, expected_target_sha256=sha256_hex(path.read_bytes()))
        executor.validate_policy(check, NORMAL_INPUT_POLICY)
        draft = executor.run(check, env, NORMAL_INPUT_POLICY, VllmAdapter(), tmp_path / 'work')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    expected = [('/tokenize', None, {'model': 'fixture-model', 'prompt': 'ping', 'add_special_tokens': False,
                                     'return_token_strs': False})]
    if fault is None:
        expected.append(('/v1/completions', None, {'model': 'fixture-model', 'prompt': token_ids, 'max_tokens': 1}))
    assert requests == expected and len(world.execs) == len(expected)
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert draft.status == (ExecStatus.COMPLETED if fault is None else ExecStatus.ABORTED)
    assert draft.normal_control.succeeded is (True if fault is None else None)
    assert all(not item.attempted for item in draft.challenges)
    raw = draft.artifacts[0].path.read_text()
    assert private not in raw and '"ping"' not in raw and '[23,41]' not in raw
    summary = json.loads(raw)
    assert summary['normal_completion_shape_verified'] is (fault is None)
    assert summary['api_reported_prompt_tokens'] == (2 if fault is None else None)
    if fault is None:
        assert summary['tokenizer']['binding_sha256'] == summary['completion']['binding_sha256']
        fixture = {'tokenizer_body_sha256': sha256_hex(canonical_json(requests[0][2])),
                   'completion_body_sha256': sha256_hex(canonical_json(requests[1][2]))}
        assert draft.comparability.fixture_sha256 == sha256_hex(canonical_json(fixture))
        assert len(draft.comparability.config_sha256) == 64
        assert (draft.comparability.model_inventory_sha256, draft.comparability.gpu, draft.comparability.driver) == (
            env.model.inventory_sha256, env.runtime.gpu_model, env.runtime.driver)
    else:
        assert summary['completion'] is None
        assert draft.comparability.fixture_sha256 is None and draft.comparability.config_sha256 is None
    observations = _normalize(check, draft)['observations']
    assert not observations['input_matrix_complete']['value'] and not observations['challenge_consumption']['collected']
    assert observations['input_matrix_planned']['value'] == planned(check, NORMAL_INPUT_POLICY)
    assert observations['input_endpoints_tested']['value'] == (['POST /v1/completions'] if fault is None else None)
    sealed = evidence(check_id=check.id, observations=observations, normal=draft.normal_control.model_dump())
    assessed = RuleTableJudge().judge(check, sealed, check.judge.params, NORMAL_INPUT_POLICY)
    assert assessed.verdicts['security'] == Verdict.UNKNOWN and assessed.verdicts['functional'] == Verdict.UNKNOWN


@pytest.mark.parametrize('fault', ['unbound', 'foreign-check', 'missing-option', 'invalid-option', 'no-approved-pair'])
def test_req09_normal_rejects_unapproved_scope_before_any_io(tmp_path, fault):
    env, target, _, path = normal_sampling_inputs(tmp_path, '0.30.0')
    target['comparison_config'] = {'launch': {'max_model_len': 128}}
    path.write_bytes(canonical_json(target))
    pack = load_pack(UPDATE / 'packs/PACK-BASELINE')
    check = pack.checks['REQ-08' if fault == 'foreign-check' else 'REQ-09']
    settings = {'target': str(path), 'normal_token_input_only': fault != 'invalid-option'}
    if fault == 'missing-option':
        settings.pop('normal_token_input_only')
    policy = NORMAL_INPUT_POLICY
    if fault == 'no-approved-pair':
        policy = {**policy, 'allowed_input_pairs': ['POST /v1/completions|text', 'POST /v1/chat/completions|text'],
                  'allowed_input_formats': ['text']}
    target_sha = None if fault == 'unbound' else sha256_hex(path.read_bytes())
    executor = KubernetesNormalTokenInputExecutor(settings, runner=_no_kubectl, expected_target_sha256=target_sha)
    with pytest.raises(ValueError, match='KUBERNETES_INPUT_NORMAL_(?:POLICY_INVALID|SCOPE_INVALID|TARGET_UNVERIFIED)'):
        executor.run(check, env, policy, object(), tmp_path / 'work')


@pytest.mark.parametrize('version', ['0.30.0', '0.31.0'])
@pytest.mark.parametrize('settings,accepted', [
    ({}, True),
    ({'max_duration_seconds': 12.5, 'kubectl_binary': 'example-kubectl'}, True),
    ({'max_duration_seconds': 0}, False),
    ({'max_duration_seconds': True}, False),
    ({'max_duration_seconds': 301}, False),
    ({'kubectl_binary': ' '}, False),
])
def test_req09_prepares_effective_settings_without_building_requests(
        tmp_path, monkeypatch, version, settings, accepted):
    from tests.test_model_preparation_integration import full_inputs
    from tools.mvp_versions import versions

    env, target = full_inputs()
    image = next(item['image'] for item in versions().values() if item['version'] == version)
    env = env.model_copy(update={'engine': env.engine.model_copy(update={'version': version, 'image_digest': image})}).seal()
    env_ref = env.ref().model_dump(mode='json')
    target.update(lab=False, environment_ref=env_ref, observed_version=version)
    target['model_mapping']['environment_ref'] = env_ref
    target['engine']['image_digest'] = image
    target['kubernetes']['engine'].update(image=image, image_id=image)
    target['comparison_config']['launch']['max_model_len'] = 2048
    raw = canonical_json(target)
    path = tmp_path / 'normal-target.json'
    path.write_bytes(raw)
    check = load_pack(UPDATE / 'packs/PACK-BASELINE').checks['REQ-09']

    monkeypatch.setattr(KubernetesHTTP, 'declare_input_contract', _no_kubectl)
    monkeypatch.setattr(KubernetesHTTP, '_verify', _no_kubectl)
    executor = KubernetesNormalTokenInputExecutor({'normal_token_input_only': True,
        'target': str(path), **settings}, runner=_no_kubectl)
    pin = sha256_hex(raw)
    if accepted:
        executor.prepared_normal_token_input(check, env, NORMAL_INPUT_POLICY, expected_target_sha256=pin)
    else:
        with pytest.raises(ValueError, match='^KUBERNETES_INPUT_NORMAL_TARGET_UNVERIFIED$'):
            executor.prepared_normal_token_input(check, env, NORMAL_INPUT_POLICY, expected_target_sha256=pin)


@pytest.mark.parametrize('damage', ['missing-file', 'missing-pin', 'different-pin', 'invalid-window'])
def test_req09_preparation_requires_saved_selected_target(tmp_path, damage):
    env, target, _, path = normal_sampling_inputs(tmp_path, '0.30.0')
    target['comparison_config']['launch']['max_model_len'] = True if damage == 'invalid-window' else 2048
    raw = canonical_json(target)
    if damage != 'missing-file':
        path.write_bytes(raw)
    pin = None if damage == 'missing-pin' else sha256_hex(raw)
    constructor_pin = sha256_hex(b'different-owned-target') if damage == 'different-pin' else None
    executor = KubernetesNormalTokenInputExecutor({'normal_token_input_only': True, 'target': str(path)},
        runner=_no_kubectl, expected_target_sha256=constructor_pin)
    check = load_pack(UPDATE / 'packs/PACK-BASELINE').checks['REQ-09']
    with pytest.raises(ValueError, match='^KUBERNETES_INPUT_NORMAL_TARGET_UNVERIFIED$'):
        executor.prepared_normal_token_input(check, env, NORMAL_INPUT_POLICY, expected_target_sha256=pin)


def transport(tmp_path):
    build(tmp_path, 'v2-default')
    target = json.loads((tmp_path / 'target.json').read_text())
    return KubernetesHTTP(target, load_environment(tmp_path / 'env.json'), duration=5, runner=_no_kubectl,
        profile=INPUT_VALIDATION_PROFILE)


def request(body, rid='func|POST_/v1/completions', **extra):
    return {'id': rid, 'method': 'POST', 'url': 'http://10.244.0.11:8000/v1/completions', 'headers': {}, 'body': body, **extra}


def test_token_prompt_contract_accepts_only_bounded_token_arrays(tmp_path):
    t = transport(tmp_path)
    with pytest.raises(KubernetesBindingError):
        t._input_validation_request(request(TOKENS))  # 계약 선언 전에는 아무 요청도 받지 않는다
    t.declare_input_contract({'path': '/v1/completions', 'control': TOKENS, 'prompt_tokens': {'max_length': 128, 'max_value': 151936},
        'fields': {}})
    for prompt in ([0], [151936], [0] * 128):
        t._input_validation_request(request({**TOKENS, 'prompt': prompt}))
    for body in ({**TOKENS, 'prompt': [0] * 129}, {**TOKENS, 'prompt': [151937]}, {**TOKENS, 'prompt': [-1]},
            {**TOKENS, 'prompt': 'text'}, {**TOKENS, 'max_tokens': 2}, {**TOKENS, 'echo': True}, {'model': 'test-model', 'prompt': [0]},
            {**TOKENS, 'model': 'other'}):
        with pytest.raises(KubernetesBindingError):
            t._input_validation_request(request(body))
    for bad in (request(TOKENS, rid='sampling-x'), request(TOKENS, rid='func|POST_/v1/chat/completions'),
            request(TOKENS, rid='func|POST /v1/completions'), request(TOKENS, headers={'Authorization': 'Bearer x'}),
            request(TOKENS, method='GET'), request(TOKENS, mode='sse')):
        with pytest.raises(KubernetesBindingError):
            t._input_validation_request(bad)
    with pytest.raises(KubernetesBindingError):
        t.declare_input_contract({'path': '/v1/completions', 'control': TOKENS, 'prompt_tokens': None, 'fields': {}})


def test_field_contract_accepts_only_the_declared_fixture(tmp_path):
    t = transport(tmp_path)
    t.declare_input_contract({'path': '/v1/chat/completions', 'control': CHAT, 'prompt_tokens': None,
        'fields': {'code_revision': 'byul-unapproved-revision'}})
    chat = 'func|POST_/v1/chat/completions'
    t._input_validation_request(request(CHAT, rid=chat))
    t._input_validation_request(request({**CHAT, 'code_revision': 'byul-unapproved-revision'}, rid=chat))
    for body in ({**CHAT, 'code_revision': 'main'}, {**CHAT, 'trust_remote_code': True},
            {**CHAT, 'messages': [{'role': 'user', 'content': 'other'}]}):
        with pytest.raises(KubernetesBindingError):
            t._input_validation_request(request(body, rid=chat))


def test_nested_reused_token_field_reaches_only_its_declared_transport_fixture(tmp_path):
    check=load_pack(UPDATE/'packs/PACK-BASELINE').checks['REQ-08']
    # The matrix delegates each selected field to this unchanged child protocol.
    check=check.model_copy(update={'collection_profile':'reused-token-input-v1'})
    contract=_contract(check,{'reused_token_field':'kv_transfer_params.prompt_token_ids'},
        {'reused_token_ids':[5,7]},'test-model')
    assert contract['fields']=={'kv_transfer_params':{'prompt_token_ids':[5,7]}}
    t=transport(tmp_path);t.declare_input_contract(contract)
    rid='func|POST_/v1/chat/completions'
    body={**contract['control'],**contract['fields']}
    t._input_validation_request(request(body,rid=rid))
    for extra in ({'prompt_token_ids':[5,8]}, {'prompt_token_ids':[5,7],'other':'unreviewed'}):
        with pytest.raises(KubernetesBindingError):
            t._input_validation_request(request({**body,'kv_transfer_params':extra},rid=rid))


@pytest.mark.parametrize('contract', [
    {'path': '/v1/embeddings', 'control': TOKENS, 'prompt_tokens': None, 'fields': {}},
    {'path': '/v1/completions', 'control': {**TOKENS, 'model': 'other'}, 'prompt_tokens': None, 'fields': {}},
    {'path': '/v1/completions', 'control': TOKENS, 'prompt_tokens': None, 'fields': {'max_tokens': 2}},
    {'path': '/v1/completions', 'control': TOKENS, 'prompt_tokens': None, 'fields': {'a.b': 1}},
    {'path': '/v1/completions', 'control': CHAT, 'prompt_tokens': {'max_length': 2, 'max_value': 2}, 'fields': {}},
    {'path': '/v1/completions', 'control': TOKENS, 'prompt_tokens': {'max_length': 0, 'max_value': 2}, 'fields': {}}])
def test_contract_declaration_is_closed(tmp_path, contract):
    with pytest.raises(KubernetesBindingError):
        transport(tmp_path).declare_input_contract(contract)


def test_executor_is_registered_and_rejects_unknown_settings(tmp_path):
    assert 'input_validation:kubernetes' in EXECUTORS
    build(tmp_path, 'v2-default')
    check = baseline_pack().checks['REQ-09']
    executor = KubernetesInputValidationExecutor({'target': str(tmp_path / 'target.json'), 'vocab_size': 151936,
        'vocab_source': 'EXAMPLE', 'shell': 'x'}, runner=_no_kubectl)
    values = {'max_input_tokens': 127, 'rejection_codes': ['400'], 'allowed_endpoints': ['POST /v1/completions'],
        'allowed_input_formats': ['token_ids']}
    with pytest.raises(ValueError, match='KUBERNETES_INPUT_CONFIGURATION_INVALID'):
        executor.run(check, load_environment(tmp_path / 'env.json'), values, object(), tmp_path / 'work')


@pytest.mark.parametrize('path,body', [('/v1/completions', TOKENS), ('/v1/chat/completions', CHAT)])
def test_executor_ids_cross_both_request_contracts_and_return_unchanged(tmp_path, path, body):
    t = transport(tmp_path)
    t.declare_input_contract({'path': path, 'control': body, 'prompt_tokens': None, 'fields': {}})
    original = request(body, rid='func|POST ' + path)

    def fixed_probe(network, requests, timeout):
        assert network == 'engine'
        wire, = requests
        t._input_validation_request(wire)
        source = {key: value for key, value in wire.items() if key != 'want_body'}
        prepared, = _prepare([source], timeout)
        assert wire['want_body'] is True
        return [{'id': prepared['id'], 'outcome': 200, 'body': '{}'}]

    rows = _WithBody(fixed_probe)('engine', [original], 5)
    assert rows == [{'id': original['id'], 'outcome': 200, 'body': '{}'}]
    assert original == request(body, rid='func|POST ' + path)


@pytest.mark.parametrize('check_id', ['REQ-08', 'REQ-09', 'SUP-08'])
def test_run_inputs_write_kubernetes_input_configs(tmp_path, monkeypatch, check_id):
    build(tmp_path, 'v2-default')
    output = tmp_path / check_id
    args = ['http-check', '--check', check_id, '--env', str(tmp_path / 'env.json'), '--target', str(tmp_path / 'target.json'),
            '--policy-draft', str(UPDATE / 'docs/gpu-a/policy-a-draft.yaml'), '--out', str(output)]
    matrix = check_id in {'REQ-08', 'REQ-09'}
    if matrix:
        from guard.collectors import native_input
        from tools import verify_req10_consumption

        with pytest.raises(ValueError, match='RUN_INPUTS_NATIVE_INPUT_PREPARED_REQUIRED'):
            inputs.main(args)
        assert not output.exists()
        profile, = json.loads(native_input.profiles.PROFILES.read_bytes())['profiles']
        monkeypatch.setattr(verify_req10_consumption, 'verify', lambda repo: {'reviewed_common_commit': 'a' * 64})
        prepared = tmp_path / 'native input'
        manifest = native_input.prepare(prepared, image=profile['image'], repository=UPDATE.parent)
        args += ['--prepared', str(prepared), '--prepared-uid', str(os.geteuid())]
        target_path = tmp_path / 'target.json'
        target = json.loads(target_path.read_bytes())
        target_path.write_text(json.dumps({**target, 'lab': True}))
        with pytest.raises(ValueError, match='RUN_INPUTS_NATIVE_INPUT_TARGET_MISMATCH'):
            inputs.main(args)
        assert not output.exists()
        target['lab'] = True
        target['engine']['image_digest'] = profile['image']
        target_path.write_text(json.dumps(target))

    inputs.main(args)
    config = (output / 'guard.toml').read_text()
    assert "input_validation = 'kubernetes'" in config
    assert ('vocab_size = 151936' in config) is matrix
    assert ('reused_token_ids' in config) is (check_id == 'REQ-08')
    settings = tomllib.loads(config)
    if matrix:
        assert settings['input_validation']['native_input_capture'] == {
            'prepared_dir': str(prepared),
            'prepared_sha256': sha256_hex(native_input.runtime.canonical(manifest)),
            'prepared_uid': os.geteuid(),
        }
        pack = load_pack(UPDATE / 'packs/PACK-BASELINE')
        policy = load_policy(output / 'policy.yaml', pack)
        check = pack.checks[check_id]
        assert all(param.key in policy.values[check_id] for param in check.policy_params if param.required)
        EXECUTORS['input_validation:kubernetes'](settings).validate_policy(check, policy.values[check_id])
    else:
        assert 'native_input_capture' not in settings['input_validation']
