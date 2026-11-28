"""The shared executor retains normal worker facts in the current CheckDef."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.run import _normalize
from guard.executors import initialization_normal_worker, node_image_proof
from guard.executors.kubernetes_initialization import KubernetesInitializationExecutor
from guard.models import DataOrigin, canonical_json, load_pack
from tests import test_a1_ready_endpoint_product as endpoint, test_a1_worker_generation_product as generation
from tests import test_node_image_proof as node_fixtures
from tests.test_kubernetes_lifecycle import group, target as native_target
from tests.test_lifecycle_capture import case, external
from tests.test_lifecycle_sensor import writer_factory


@pytest.mark.parametrize(('check_id', 'proof'), [
    ('LIF-07', 'exact-api-image'), ('LIF-09', 'unconfigured'),
    ('LIF-09', 'different-node'), ('LIF-09', 'local-node'),
])
def test_current_checkdef_keeps_the_bound_normal_worker_step(
        tmp_path, monkeypatch, group, native_target, writer_factory, check_id, proof):
    producer = generation if check_id == 'LIF-07' else endpoint
    image_world = None
    role = native_target['kubernetes']['engine']
    release = initialization_normal_worker.release_profile(group.env.engine.version)
    if proof in {'exact-api-image', 'unconfigured'}:
        role['image'] = release['images'][0]
        if proof == 'exact-api-image':
            role['image_id'] = role['image']
    if proof in {'different-node', 'local-node'}:
        image_world = node_fixtures.World()
        for key in ('image', 'image_id', 'container_id', 'node'):
            role[key] = image_world.engine[key]
        # Use verified synthetic OCI bytes with the reviewed producer commit gate.
        monkeypatch.setattr(initialization_normal_worker, 'release_profile',
                            lambda version: {**release, 'images': [role['image']]})
        monkeypatch.setattr(node_image_proof, 'kernel_identity', lambda: node_fixtures.KERNEL)
        monkeypatch.setattr(node_image_proof, 'verify_grpcurl_binary', lambda: None)
    group.env = group.env.model_copy(update={
        'engine': group.env.engine.model_copy(update={'image_digest': role['image']}),
    }).seal()
    world, values, _, _ = producer.inputs(tmp_path, group, native_target, 'normal', monkeypatch, 'normal')
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs' / 'PACK-BASELINE')
    check = pack.checks[check_id]
    if check_id == 'LIF-07':
        values['required_readiness_items'] = ['http_listener']
    target = tmp_path / 'canonical-target.json'
    target.write_bytes(canonical_json(world.target))
    settings = {'target': str(target), 'prepared': str(group.manifest_path)}
    engine = world.target['kubernetes']['engine']
    reads = []

    def fixture_reply(body):
        return {'returncode': 0, 'stdout_bytes': canonical_json(body), 'stderr_bytes': b'',
                'capture': {'complete': True, 'bounded_at_source': True, 'timed_out': False,
                            'truncated': False, 'pipes_closed': True, 'read_errors': []}}

    def runner(argv, **kwargs):
        if argv[0] == 'kubectl' and argv[-5:-3] == ['get', 'node']:
            node = deepcopy(node_fixtures.NODE)
            if proof == 'different-node':
                node['status']['nodeInfo']['bootID'] = '0' * 36
            reads.append('node')
            return fixture_reply(node)
        if argv[:2] != ['sudo', '-n']:
            return world.runner(argv, **kwargs)
        reads.append('local')
        if argv[2] == 'python3':
            return fixture_reply(image_world.process(int(argv[-1])))
        result = image_world.run(argv, **kwargs)
        if argv[5:7] == ['containers', 'info']:
            value = json.loads(result['stdout_bytes'])
            labels = {'pod.uid': 'pod_uid', 'pod.name': 'pod', 'pod.namespace': 'namespace',
                      'container.name': 'container'}
            value['Labels'] = {'io.kubernetes.' + label: engine[key] for label, key in labels.items()}
            result['stdout_bytes'] = canonical_json(value)
        return result

    if image_world is not None:
        settings['node_image_verification'] = node_image_proof.PROFILE
    executor = KubernetesInitializationExecutor(settings, runner=runner)
    if proof == 'unconfigured':
        with pytest.raises(ValueError, match='INITIALIZATION_NORMAL_NODE_IMAGE_PROOF_CONFIGURATION_REQUIRED'):
            executor.prepared_inputs(check, group.env, values)
        assert not world.sent and not reads
        return
    _, prepared, _ = executor.prepared_inputs(check, group.env, values)
    assert not world.sent and not reads and not prepared['engine_started']

    draft = executor.run(check, group.env, values, VllmAdapter(), tmp_path / 'evidence')
    observations = _normalize(check, draft)['observations']
    facts = [row for row in observations['activation_steps']['value']
             if row['step'] == 'native-normal-worker-load-return-and-ready-send']
    if proof == 'different-node':
        assert not facts and 'node' in reads and 'local' not in reads
        assert {'INITIALIZATION_NORMAL_NODE_IMAGE_PROOF_UNVERIFIED',
                'INITIALIZATION_NORMAL_WORKER_SOURCE_UNAVAILABLE'} <= set(observations['activation_missing']['value'])
        return
    expected_ranks = {0, 1} if proof == 'local-node' else {0}
    assert {fact['rank'] for fact in facts} == expected_ranks
    assert len(facts) == len(expected_ranks)
    for fact in facts:
        assert fact['session'] == prepared['session'] and fact['recording_profile'] == prepared['profile']
        assert fact['image_identity_basis'] == (
            'node-verified-image-digest' if proof == 'local-node' else 'api-reported-exact-image-digest')
    assert ('local' in reads) is (proof == 'local-node')
    assert observations['activation_scope_complete']['value'] is False
    assert observations['activation_violations']['value'] == []
    assert draft.data_origin is DataOrigin.EXAMPLE
