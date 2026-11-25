"""A paired ordinary REQ-11 control through the public UPDATE path. All I/O is EXAMPLE."""
from copy import deepcopy
import json
import socket
import subprocess

from guard import application
from guard.executors import kubernetes_request_binding as binding
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from tests.builders import T0, environment, policy
from tests.test_kubernetes_request_binding import target as request_target
from tests.test_kubernetes_sampling import PRIVATE, World
from tests.test_request_binding import VALUES
from tools import mvp_metadata_wiring as wiring


def test_paired_req11_normal_control_reaches_evidence_without_output_attribution(tmp_path, monkeypatch):
    pack = load_pack(wiring.readiness.ROOT / 'packs/PACK-BASELINE')
    declared = policy(pack.ref(), status='CONFIRMED', data_origin='DECLARED', confirmed_by='Fixture owner',
                      confirmed_at=T0, values={'REQ-11': deepcopy(VALUES)}, scope={'include': ['REQ-11']})
    envs, sides, worlds, originals = [], {}, {}, {}
    for ordinal, (side, release) in enumerate(wiring.versions().items(), start=1):
        env = environment(id=f'ENV-EXAMPLE-{side.upper()}', origin=DataOrigin.DECLARED,
                          engine={'name': release['engine'], 'version': release['version'],
                                  'image_digest': release['image']},
                          model={'id': 'fixture-model', 'revision': 'fixture-revision', 'inventory_sha256': 'a' * 64},
                          runtime={'gpu_count': 2, 'gpu_model': 'EXAMPLE GPU', 'driver': 'EXAMPLE driver',
                                   'tp_size': 2, 'pp_size': 1, 'dp_size': 1})
        envs.append(env)
        target = request_target(env)
        target.update(lab=False, observed_version=release['version'])
        target['engine']['image_digest'] = release['image']
        engine = target['kubernetes']['engine']
        engine.update(image=release['image'], image_id=release['image'],
                      pod_uid=f'00000000-0000-0000-0000-{ordinal:012d}',
                      container_id=f'containerd://{ordinal:064x}')
        target_path = tmp_path / f'{side}-target.json'
        original = canonical_json(target) + b'\n'
        target_path.write_bytes(original)
        originals[target_path] = original
        sides[side] = {'request_binding': {'target': str(target_path)}}
        worlds[env.id] = World(target)
    cfg = {
        'adapter': {'name': 'vllm'}, 'update': {'mode': 'external', 'allowed_checks': ['REQ-11']},
        'executors': {'request_binding': 'kubernetes'}, 'sides': sides,
        'check_config': {'REQ-11': {'normal_request_binding_only': True}},
    }

    def forbidden(*args, **kwargs):
        raise AssertionError('EXAMPLE pair must use only the injected owner and response fixtures')

    transport = binding.KubernetesHTTP

    def owned_transport(target, env, **kwargs):
        world = worlds[env.id]
        assert world.target == target

        def runner(argv, **opts):
            result = world.runner(argv, **opts)
            if 'exec' in argv:
                assert len(world.execs) == 1
                request, = world.execs
                assert request['method'] == 'POST' and request['mode'] == 'json'
                assert request['url'].endswith('/v1/chat/completions') and request['headers'] == {}
                assert request['body'] == {'model': 'fixture-model', 'max_tokens': 1,
                                           'messages': [{'role': 'user', 'content': 'guard request binding control'}]}
                response, = json.loads(result['stdout_bytes'])
                body = canonical_json({'id': 'chatcmpl-EXAMPLE', 'object': 'chat.completion',
                    'model': 'fixture-model', 'choices': [{'index': 0, 'finish_reason': 'length',
                        'message': {'role': 'assistant', 'content': PRIVATE}}],
                    'usage': {'prompt_tokens': 4, 'completion_tokens': 1, 'total_tokens': 5}})
                response.update(mode='json', content_type='application/json', body=body.decode(), body_bytes=len(body),
                                body_sha256=sha256_hex(body), eof=True, utf8_valid=True, body_truncated=False,
                                capture_complete=True)
                result['stdout_bytes'] = canonical_json([response])
            return result

        kwargs['runner'] = runner
        return transport(target, env, **kwargs)

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(binding, 'KubernetesHTTP', owned_transport)
    readiness = application.update_readiness(pack, envs, declared, cfg)
    input_check, = (row for row in readiness['checks'] if row['check'] == 'inputs')
    assert input_check['ok'], input_check
    assert readiness['execution_scope']['static_preparation_ready'], readiness['execution_scope']
    assert all(not world.execs for world in worlds.values())

    store = Store(tmp_path / 'store')
    report, _ = application.execute_update(store, pack, envs, declared, cfg, tmp_path / 'run', utcnow())
    assert report.official is False
    evidence = [row for row in store.iter_kind('EVD') if row.check_id == 'REQ-11']
    assert {row.side.value for row in evidence} == {'baseline', 'candidate'}
    assert all(len(world.execs) == 1 for world in worlds.values())
    for row in evidence:
        assert row.execution.status == ExecStatus.COMPLETED and row.data_origin == DataOrigin.EXAMPLE
        assert row.normal_control.succeeded is True and not any(step.attempted for step in row.challenges)
        assert row.observations['output_binding_verified'].value is not True
        assert row.observations['normal_controls'].value is not True
        assert row.artifacts and all(PRIVATE.encode() not in (store.root / artifact.path).read_bytes()
                                     for artifact in row.artifacts)
    results = [row for row in store.iter_kind('RES') if row.check_id == 'REQ-11']
    assert len(results) == 2
    assert all(row.verdicts.security.value == row.verdicts.functional.value == 'UNKNOWN' for row in results)
    for path, original in originals.items():
        assert path.read_bytes() == original
