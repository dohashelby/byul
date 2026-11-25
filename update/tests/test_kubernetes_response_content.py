"""REQ-13 Kubernetes 전송: 원 응답 내용 실행기가 만든 요청을 실제 전송 검증기에 넣고 판정까지 잇는다.

kubectl은 부르지 않는다. 엔진 응답은 대역이며 전송 검증기·사례·관측·판정은 원 코드다.
"""
import json
from pathlib import Path
import socket
import subprocess

import pytest

from guard import application
from guard.collectors.file import load_environment
from guard.core.execution_recipe import bind_recipe
from guard.core.select import select
from guard.executors import kubernetes_response_content as module
from guard.executors.registry import EXECUTORS
from guard.models import ExecStatus, Side, canonical_json, load_pack, sha256_hex
from guard.runtime.kubernetes_http import RESPONSE_CONTENT_PROFILE, KubernetesBindingError, KubernetesHTTP
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import baseline_pack, environment, policy
from tests.run_binding_support import run_locked_update_trial
from tests.test_gpu_a_run_inputs import build
from tests.test_kubernetes_routes import inputs as owned_routes
from tools import mvp_live_readiness
from tools.j02_run_config import write_config

VALUES = {"allowed_response_fields": ["id", "model", "choices[].text", "choices[].message.role", "choices[].message.content",
          "choices[].finish_reason", "usage.prompt_tokens", "usage.completion_tokens", "usage.total_tokens"],
          "allowed_error_fields": ["error.message"], "forbidden_return_options": ["return_prompt_text", "return_token_ids"],
          "required_targets": ["engine"]}


def _no_kubectl(*args, **kwargs):
    raise AssertionError('no kubectl in contract tests')


class Engine:
    """엔진 응답 대역. leak이면 금지 옵션 필드를 돌려준다."""
    def __init__(self, transport, *, leak=False):
        self.transport = transport; self.leak = leak; self.seen = []

    def __call__(self, network, requests, timeout):
        assert network == 'kubernetes-probe'
        request, = requests
        self.transport._response_content_request(request)  # 원 실행기가 만든 그대로 검증한다
        self.seen.append(request['id'])
        body = request['body']; chat = 'messages' in body
        choice = {'message': {'role': 'assistant', 'content': 'ok'}} if chat else {'text': 'ok'}
        choice['finish_reason'] = 'length'
        value = {'id': 'x', 'model': self.transport.api_model, 'choices': [choice],
                 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
        status = 200
        if isinstance(body['max_tokens'], str):
            status, value = 400, {'error': {'message': 'invalid max_tokens'}}
        elif any(k in body for k in ('return_prompt_text', 'return_token_ids')):
            if self.leak:
                value['prompt_token_ids'] = [1, 2]
            else:
                status, value = 400, {'error': {'message': 'option disabled'}}
        raw = json.dumps(value)
        return [{'id': request['id'], 'outcome': status, 'body': raw, 'body_bytes': len(raw), 'capture_complete': True}]

    def report(self):
        return {'cleanup_errors': []}

    @property
    def injected(self):
        return False

    def __getattr__(self, name):
        return getattr(self.transport, name)


def run(tmp_path, monkeypatch, *, leak=False, endpoint='/v1/chat/completions'):
    build(tmp_path, 'v2-default')
    env = load_environment(tmp_path / 'env.json')
    engines = []

    def factory(target, env_, **kwargs):
        assert kwargs['profile'] == RESPONSE_CONTENT_PROFILE
        engines.append(Engine(KubernetesHTTP(target, env_, duration=5, runner=_no_kubectl, profile=RESPONSE_CONTENT_PROFILE), leak=leak))
        return engines[-1]
    monkeypatch.setattr(module, 'KubernetesHTTP', factory)
    executor = module.KubernetesResponseContentExecutor({'target': str(tmp_path / 'target.json'), 'endpoint': endpoint}, runner=_no_kubectl)
    check = baseline_pack().checks['REQ-13']
    draft = executor.run(check, env, VALUES, object(), tmp_path / 'work')
    return draft, engines[0]


def test_real_executor_requests_pass_the_closed_transport_and_stay_clean(tmp_path, monkeypatch):
    draft, engine = run(tmp_path, monkeypatch)
    assert engine.seen == ['normal', 'private_system', 'option:return_prompt_text', 'option:return_token_ids', 'safe_error', 'after']
    assert draft.observations['response_violations'].value == []
    assert draft.observations['response_coverage_complete'].value is True
    assert draft.normal_control.succeeded is True


def test_forbidden_option_field_returned_through_kubernetes_is_a_violation(tmp_path, monkeypatch):
    draft, _ = run(tmp_path, monkeypatch, leak=True)
    assert any('forbidden option return_token_ids' in v for v in draft.observations['response_violations'].value)


def test_completions_endpoint_records_the_private_context_gap(tmp_path, monkeypatch):
    draft, engine = run(tmp_path, monkeypatch, endpoint='/v1/completions')
    assert 'private_system' not in engine.seen
    assert draft.observations['response_coverage_complete'].value is False


def test_transport_refuses_other_shapes(tmp_path):
    build(tmp_path, 'v2-default')
    t = KubernetesHTTP(json.loads((tmp_path / 'target.json').read_text()), load_environment(tmp_path / 'env.json'),
        duration=5, runner=_no_kubectl, profile=RESPONSE_CONTENT_PROFILE)
    mark = 'G0123456789abU'
    good = {'id': 'normal', 'method': 'POST', 'url': 'http://10.244.0.11:8000/v1/completions', 'headers': {},
            'body': {'model': t.api_model, 'prompt': mark, 'max_tokens': 1, 'temperature': 0}}
    t._response_content_request(good)
    for change in ({'id': 'shell'}, {'headers': {'Authorization': 'Bearer x'}}, {'body': {**good['body'], 'max_tokens': 2}},
                   {'body': {**good['body'], 'prompt': 'free text'}}, {'body': {**good['body'], 'trust_remote_code': True}},
                   {'id': 'option:echo', 'body': {**good['body'], 'echo': 'yes'}}, {'id': 'option:shell', 'body': {**good['body'], 'shell': True}},
                   {'id': 'private_system'}):
        with pytest.raises(KubernetesBindingError):
            t._response_content_request({**good, **change})


def test_executor_is_registered_and_rejects_unknown_settings(tmp_path):
    assert 'response_content:kubernetes' in EXECUTORS
    build(tmp_path, 'v2-default')
    executor = module.KubernetesResponseContentExecutor({'target': str(tmp_path / 'target.json'), 'shell': 'x'}, runner=_no_kubectl)
    with pytest.raises(ValueError, match='KUBERNETES_RESPONSE_CONTENT_CONFIGURATION_INVALID'):
        executor.run(baseline_pack().checks['REQ-13'], load_environment(tmp_path / 'env.json'), VALUES, object(), tmp_path / 'w')


def test_gateway_leg_goes_to_the_route_transport_and_the_engine_leg_drops_only_the_caller_key():
    from guard.executors.kubernetes_response_content import _Legs
    from guard.runtime.kubernetes_routes import EXTERNAL
    calls = []
    def engine(network, requests, timeout):
        calls.append(('engine', network, requests)); return [{'id': r['id'], 'outcome': 200} for r in requests]
    def gateway(network, requests, timeout):
        calls.append(('gateway', network, requests)); return [{'id': r['id'], 'outcome': 200} for r in requests]
    legs = _Legs(engine, gateway)
    request = {'id': 'normal', 'method': 'POST', 'url': 'u', 'headers': {'Authorization': 'Bearer caller'}, 'body': {'model': 'm'}}
    legs('kubernetes-probe', [request], 5); legs(EXTERNAL, [request], 5)
    (_, _, sent_engine), (_, _, sent_gateway) = calls[0], calls[1]
    assert sent_engine[0]['headers'] == {} and sent_engine[0]['body'] == request['body']
    assert sent_gateway[0]['headers'] == {'Authorization': 'Bearer caller'}


def test_route_target_and_key_env_must_be_given_together(tmp_path):
    check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['REQ-13']
    for cfg in ({'target': 'x', 'route_target': 'y'}, {'target': 'x', 'allowed_key_env': 'K'}):
        with pytest.raises(ValueError, match='KUBERNETES_RESPONSE_CONTENT_CONFIGURATION_INVALID'):
            module.KubernetesResponseContentExecutor(cfg).validate_configuration(check)


def _paired_gateway_inputs(tmp_path, *, required=True):
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    values = {**VALUES, 'required_targets': ['gateway'] if required else ['engine']}
    pol = policy(pack.ref(), scope={'include': ['REQ-13']}, values={'REQ-13': values})
    cfg = {'adapter': {'name': 'vllm'}, 'executors': {'response_content': 'kubernetes'},
           'update': {'mode': 'external', 'allowed_checks': ['REQ-13']}, 'sides': {}}
    envs = []
    for side, release in mvp_live_readiness.versions().items():
        folder = tmp_path / side
        folder.mkdir()
        env = environment(id=f'ENV-REQ13-{side.upper()}',
            engine={'name': release['engine'], 'version': release['version'], 'image_digest': release['image']},
            runtime={'gpu_count': 1, 'gpu_model': 'EXAMPLE L4', 'driver': 'EXAMPLE driver'},
            model={'id': 'EXAMPLE-model', 'revision': 'EXAMPLE-current', 'inventory_sha256': 'c' * 64})
        envs.append(env)
        (folder / 'env.json').write_bytes(canonical_json(env.to_dict()))
        _, routes = owned_routes('REQ-13')
        routes.pop('gateway_known_routes')
        routes.update(environment_ref=env.ref().model_dump(mode='json'), observed_version=release['version'],
                      model_mapping={'repository_id': env.model.id, 'revision': env.model.revision, 'api_model': 'fixture'})
        routes['engine']['image_digest'] = release['image']
        routes['kubernetes']['engine'].update(image=release['image'], image_id=release['image'])
        target = {key: value for key, value in routes.items() if key not in {'kubernetes_routes', 'gateway', 'direct_targets'}}
        target['origins'] = {'trusted': routes['origins']['trusted']}
        target_path, route_path = folder / 'target.json', folder / 'routes.json'
        target_path.write_bytes(canonical_json(target))
        route_path.write_bytes(canonical_json(routes))
        cfg['sides'][side] = {'response_content': {'target': str(target_path), 'route_target': str(route_path),
            'allowed_key_env': 'BYUL_EXAMPLE_REQ13_UNSET', 'endpoint': '/v1/chat/completions'}}
    return pack, envs, pol, cfg


@pytest.mark.parametrize(('candidate_input', 'gap'), [
    ('exact', None),
    ('missing', module.GATEWAY_TARGET_REQUIRED),
    ('wrong-owner', module.CONFIGURATION_INVALID),
    ('aliased-gateway', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('engine-only', None),
    ('staged-existing', None),
])
def test_paired_gateway_inputs_are_checked_offline(tmp_path, monkeypatch, candidate_input, gap):
    monkeypatch.setattr(subprocess, 'Popen', _no_kubectl)
    monkeypatch.setattr(socket, 'socket', _no_kubectl)
    monkeypatch.delenv('BYUL_EXAMPLE_REQ13_UNSET', raising=False)
    pack, envs, pol, cfg = _paired_gateway_inputs(tmp_path, required=candidate_input != 'engine-only')
    candidate = cfg['sides']['candidate']['response_content']
    if candidate_input in {'missing', 'engine-only', 'staged-existing'}:
        sides = ('candidate',) if candidate_input == 'missing' else cfg['sides']
        for side in sides:
            settings = cfg['sides'][side]['response_content']
            del settings['route_target'], settings['allowed_key_env']
    if candidate_input in {'wrong-owner', 'aliased-gateway'}:
        path = Path(candidate['route_target'])
        routes = json.loads(path.read_bytes())
        if candidate_input == 'wrong-owner':
            routes['kubernetes']['probe']['pod_uid'] = '00000000-0000-0000-0000-000000000101'
        else:
            routes['kubernetes_routes']['gateway']['pod_uid'] = routes['kubernetes']['engine']['pod_uid']
        path.write_bytes(canonical_json(routes))
    if candidate_input == 'staged-existing':
        cfg['update']['kubernetes_candidate_binding'] = 'after_baseline'
    if candidate_input == 'missing':
        # Even this incomplete collector profile must still flag its missing required leg.
        candidate.pop('endpoint')

    ready = application.update_readiness(pack, envs, pol, cfg)
    inputs = next(row for row in ready['checks'] if row['check'] == 'inputs')
    assert inputs['ok'] is (gap is None), ready
    if gap is not None:
        assert inputs['detail'] == gap

    policy_path, config_path = tmp_path / 'policy.json', tmp_path / 'guard.toml'
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    write_config(cfg, config_path)
    index = mvp_live_readiness.template(pack)
    index['inputs'] = [{'check_id': 'REQ-13', 'side': side, **{
        role: {'path': str(path), 'sha256': sha256_hex(path.read_bytes())}
        for role, path in {'environment': tmp_path / side / 'env.json', 'policy': policy_path,
                           'configuration': config_path}.items()
    }} for side in cfg['sides']]
    index_path = tmp_path / 'index.json'
    index_path.write_bytes(canonical_json(index))
    audited = mvp_live_readiness.audit(index_path, pack=pack)
    assert audited['active_checks'] == 69 and audited['sides'] == 138
    assert audited['file_binding_valid_sides'] == (2 if gap is None else 1)
    for row in audited['rows']:
        if row['check_id'] != 'REQ-13':
            continue
        assert row['live_target_verified'] is row['ready_for_execution'] is row['engine_executed'] is False
        if row['side'] == 'candidate' and gap is not None:
            assert row['blocking_codes'] == [gap]


def test_prebound_gateway_audit_keeps_the_independent_route_file_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', _no_kubectl)
    monkeypatch.setattr(socket, 'socket', _no_kubectl)
    monkeypatch.setattr(module.KubernetesResponseContentExecutor, 'keys', _no_kubectl)
    pack, _, pol, cfg = _paired_gateway_inputs(tmp_path)
    policy_path, config_path = tmp_path / 'policy.json', tmp_path / 'guard.toml'
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    write_config(cfg, config_path)

    def reference(path):
        return {'path': str(path), 'sha256': sha256_hex(path.read_bytes())}

    index = mvp_live_readiness.template(pack)
    index['inputs'] = [{'check_id': 'REQ-13', 'side': side,
                        'environment': reference(tmp_path / side / 'env.json'),
                        'policy': reference(policy_path), 'configuration': reference(config_path)}
                       for side in cfg['sides']]
    candidate = next(row for row in index['inputs'] if row['side'] == 'candidate')
    route_path = Path(cfg['sides']['candidate']['response_content']['route_target'])
    pointer = reference(route_path)
    candidate['route_target'] = pointer
    index_path = tmp_path / 'index.json'

    def audit():
        index_path.write_bytes(canonical_json(index))
        result = mvp_live_readiness.audit(index_path, pack=pack)
        return {row['side']: row for row in result['rows'] if row['check_id'] == 'REQ-13'}

    rows = audit()
    assert all(row['file_binding_valid'] and not row['live_target_verified'] for row in rows.values())
    assert rows['candidate']['files']['route_target'] == pointer
    assert 'route_target' not in rows['baseline']['files']

    original = route_path.read_bytes()
    route_path.write_bytes(original + b'\n')
    rows = audit()
    assert rows['baseline']['file_binding_valid']
    assert not rows['candidate']['file_binding_valid']
    assert rows['candidate']['blocking_codes'] == ['MVP_INPUT_HASH_MISMATCH']

    route_path.write_bytes(original)
    alternative = tmp_path / 'same-route-bytes.json'
    alternative.write_bytes(original)
    candidate['route_target'] = reference(alternative)
    rows = audit()
    assert rows['baseline']['file_binding_valid']
    assert rows['candidate']['blocking_codes'] == ['MVP_TARGET_REFERENCE_PATH_MISMATCH']

    cfg['update']['kubernetes_candidate_binding'] = 'after_baseline'
    write_config(cfg, config_path)
    for row in index['inputs']:
        row['configuration'] = reference(config_path)
    candidate['route_target'] = pointer
    assert audit()['candidate']['blocking_codes'] == ['MVP_ROUTE_TARGET_REFERENCE_SCOPE_INVALID']


def test_prepared_gateway_checks_each_supplied_base_target_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', _no_kubectl)
    monkeypatch.setattr(socket, 'socket', _no_kubectl)
    monkeypatch.setattr(KubernetesHTTP, '__call__', _no_kubectl)
    monkeypatch.setattr(module.KubernetesRoutesHTTP, '__call__', _no_kubectl)
    monkeypatch.setattr(module.KubernetesResponseContentExecutor, 'keys', _no_kubectl)
    _, envs, _, cfg = _paired_gateway_inputs(tmp_path)
    settings = cfg['sides']['baseline']['response_content']
    expected = sha256_hex(canonical_json(json.loads(Path(settings['target']).read_bytes())))
    other = '0' * 64

    class NoCallerKeys(dict):
        get = staticmethod(_no_kubectl)

    def prepare(constructor_pin, caller_pin):
        executor = module.KubernetesResponseContentExecutor(settings, runner=_no_kubectl, environ=NoCallerKeys(),
            expected_target_sha256=constructor_pin)
        executor.prepared_gateway(envs[0], expected_target_sha256=caller_pin)

    for constructor_pin, caller_pin in ((None, None), (expected, None), (None, expected), (expected, expected)):
        prepare(constructor_pin, caller_pin)

    for constructor_pin, caller_pin in ((other, None), (None, other), (other, expected), (expected, other)):
        with pytest.raises(ValueError, match=f'^{module.ROUTE_TARGET_CHANGED}$'):
            prepare(constructor_pin, caller_pin)


@pytest.mark.parametrize('late', [False, True])
def test_prebound_gateway_bytes_cannot_change_after_selection(tmp_path, monkeypatch, late):
    from guard.core import run as core_run

    def forbid_dispatch(*args, **kwargs):
        pytest.fail('An uncommitted Gateway binding must stop before transport construction or dispatch')

    pack, envs, pol, cfg = _paired_gateway_inputs(tmp_path)
    store = Store(tmp_path / 'store')
    put_pack(store, pack.path)
    store.put_many([*envs, pol])
    plan = bind_recipe(select(pack, envs, pol, mode='update'), cfg, envs)
    store.put(plan)
    route_path = Path(cfg['sides']['candidate']['response_content']['route_target'])
    monkeypatch.setattr(subprocess, 'Popen', forbid_dispatch)
    monkeypatch.setattr(socket, 'socket', forbid_dispatch)
    monkeypatch.setattr(module, 'KubernetesRoutesHTTP', forbid_dispatch)
    monkeypatch.setattr(module.KubernetesResponseContentExecutor, '_finish', forbid_dispatch)
    if late:
        run_one = core_run._run_one

        def replace_after_recipe(*args, **kwargs):
            route_path.write_bytes(route_path.read_bytes() + b'\n')
            return run_one(*args, **kwargs)

        monkeypatch.setattr(core_run, '_run_one', replace_after_recipe)
        evd, = run_locked_update_trial(store, pack, plan, application.run_config(cfg), only_side=Side.candidate)
        assert evd.execution.status == ExecStatus.ABORTED and evd.execution.abort_reason == 'EXECUTOR_FAILURE'
    else:
        routes = json.loads(route_path.read_bytes())
        routes['kubernetes_routes']['gateway']['pod_uid'] = '00000000-0000-0000-0000-000000000102'
        route_path.write_bytes(canonical_json(routes))
        with pytest.raises(ValueError, match='^EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED$'):
            run_locked_update_trial(store, pack, plan, application.run_config(cfg), only_side=Side.candidate)
        assert not store.find_latest('EVD', lambda row: row.plan == plan.ref())


def test_gateway_pin_is_checked_before_parsing_substituted_bytes(tmp_path):
    _, envs, _, cfg = _paired_gateway_inputs(tmp_path)
    settings = cfg['sides']['candidate']['response_content']
    path = Path(settings['route_target'])
    executor = module.KubernetesResponseContentExecutor(settings,
        runner=_no_kubectl, expected_route_target_sha256=sha256_hex(path.read_bytes()))
    path.write_bytes(b'{')
    with pytest.raises(ValueError, match=f'^{module.ROUTE_TARGET_CHANGED}$'):
        executor.prepared_gateway(envs[1])
