from copy import deepcopy
import json

import pytest

from guard.runtime.kubernetes_callback_gateway import KubernetesCallbackGatewayHTTP
from guard.runtime.kubernetes_http import KubernetesBindingError
from tests.test_kubernetes_routes import inputs, World


def configured(*, late=False):
    env, full = inputs()
    base = {k: v for k, v in deepcopy(full).items() if k not in {'kubernetes_routes', 'gateway', 'direct_targets'}}
    base['origins'] = {'trusted': {'network': 'kubernetes-probe'}}
    world = World(full)
    def runner(argv, **kwargs):
        if 'exec' in argv and 'input_bytes' in kwargs:
            requests = json.loads(kwargs['input_bytes'])['requests']
            world.packets.append(requests)
            if late:
                world.changed = True
            return world.result([{'id': r['id'], 'outcome': 200, 'capture_complete': True, 'body': '{}'} for r in requests])
        return world.runner(argv, **kwargs)
    return env, base, full, world, runner


def request(url):
    return {'id': 'cb-1', 'method': 'POST', 'mode': 'json', 'url': url + '/v1/chat/completions',
            'headers': {'X-Request-Id': 'byul-cb-' + 'a' * 32, 'Authorization': 'Bearer EXAMPLE'},
            'body': {'model': 'fixture-model', 'messages': [{'role': 'user', 'content': 'hello'}],
                     'max_tokens': 1, 'stream': False}}


def test_dispatches_to_gateway_and_keeps_callback_source_on_engine():
    env, base, full, world, runner = configured()
    transport = KubernetesCallbackGatewayHTTP(base, full, env, duration=30, runner=runner)
    result, = transport('kubernetes-probe', [request(full['gateway']['url'])], 10)
    assert result['outcome'] == 200 and world.packets[0][0]['url'].startswith(full['gateway']['url'])
    assert transport.binding['engine'] == base['kubernetes']['engine']
    assert sum('gateway-pod' in call for call in world.calls) >= 2
    assert transport.report()['principal_authenticity_verified'] is False


def test_rejects_engine_dispatch_and_late_gateway_replacement():
    env, base, full, world, runner = configured(late=True)
    transport = KubernetesCallbackGatewayHTTP(base, full, env, duration=30, runner=runner)
    with pytest.raises(KubernetesBindingError):
        transport('kubernetes-probe', [request(full['gateway']['url'])], 10)
    assert world.packets and transport.report()['collection_gap'] is True
    env, base, full, world, runner = configured()
    transport = KubernetesCallbackGatewayHTTP(base, full, env, duration=30, runner=runner)
    with pytest.raises(KubernetesBindingError):
        transport('kubernetes-probe', [request(base['engine']['trusted_url'])], 10)
    assert world.packets == []


@pytest.mark.parametrize('field', ['pod_uid', 'container_id', 'image_id'])
def test_rejects_unjoined_engine_generation_before_io(field):
    env, base, full, world, runner = configured()
    full['kubernetes']['engine'][field] = {'pod_uid': '10000000-0000-0000-0000-999999999999',
        'container_id': 'containerd://' + 'f' * 64, 'image_id': 'sha256:' + 'f' * 64}[field]
    with pytest.raises(KubernetesBindingError):
        KubernetesCallbackGatewayHTTP(base, full, env, duration=30, runner=runner)
    assert world.calls == []


@pytest.mark.parametrize('supplied', [False, True])
def test_principal_cases_require_a_manifest_bound_gateway_file(tmp_path, supplied):
    from guard.collectors import engine_callbacks
    from guard.executors.engine_sensor import EngineSensorExecutor, PROFILES
    from guard.executors.engine_callback_projection import SCHEMA
    from guard.models import canonical_json, sha256_hex
    from tests.builders import baseline_pack
    env, base, full, world, runner = configured()
    prepared = engine_callbacks.prepare_assets(tmp_path / 'prepared')
    manifest = {'schema': SCHEMA, 'session': prepared['session'],
        'target_sha256': sha256_hex(canonical_json(base)), 'environment_ref': env.ref().model_dump(mode='json'),
        'cases': [{'case_id': 'prime', 'role': 'prime', 'principal': 'A', 'group': 'A', 'prompt': 'hello'}]}
    for name, value in [('target.json', base), ('manifest.json', manifest), ('gateway.json', full)]:
        (tmp_path / name).write_bytes(canonical_json(value))
    cfg = {'target': str(tmp_path / 'target.json'), 'prepared': str(tmp_path / 'prepared/manifest.json'),
           'manifest': str(tmp_path / 'manifest.json')}
    if supplied:
        cfg['gateway_target'] = str(tmp_path / 'gateway.json')  # Missing its manifest pin.
    values = {'sensor_environment_sha256': env.ref().sha256,
        'sensor_target_sha256': sha256_hex(canonical_json(base)),
        'sensor_prepared_sha256': sha256_hex(canonical_json(prepared)),
        'sensor_manifest_sha256': sha256_hex(canonical_json(manifest))}
    check = baseline_pack().checks['REQ-15'].model_copy(deep=True)
    check.executor = 'engine_sensor'; check.collection_profile = PROFILES['REQ-15']
    with pytest.raises(ValueError, match='ENGINE_SENSOR_GATEWAY_TARGET_BINDING_REQUIRED'):
        EngineSensorExecutor(cfg).run(check, env, values, None, tmp_path / 'work')
