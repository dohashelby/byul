"""A AWS lab 덧씌우기 렌더링. 인계 템플릿 사본(fixture)으로만 확인한다. kubectl·GPU 없음."""
import json
from pathlib import Path
import shutil

import pytest
import yaml

from guard.runtime.kubernetes_binding import verify_owned_pod
from tools import gpu_a_aws_lab as lab

HANDOFF = Path(__file__).parent / 'fixtures/aws-handoff-20261026'
IMAGE = 'docker.io/vllm/vllm-openai@sha256:' + 'a' * 64
REVISION = '7ae557604adf67be50417f59c2c2f167def9a775'


def rendered(generation, **options):
    engine, probe, record = lab.render(HANDOFF, generation=generation, profile=options.pop('profile', 'dev'), run_id='a-run-1',
        engine_image=IMAGE, model_revision=REVISION, host_root='/srv/byul-a', **options)
    deployment, service = yaml.safe_load_all(engine)
    return deployment, service, yaml.safe_load(probe), record


@pytest.mark.parametrize('generation', sorted(lab.GENERATIONS))
def test_overlay_keeps_the_handoff_engine_and_adds_only_a_requirements(generation):
    deployment, service, probe, record = rendered(generation)
    original, original_service = yaml.safe_load_all(lab._fill((HANDOFF / 'engine.yaml.template').read_text(), {
        'ENGINE_IMAGE': IMAGE, 'MODEL_REVISION': REVISION, 'CACHE_PATH': record['cache_path'], 'LAB_PROFILE': 'dev',
        'ENGINE_CPU_REQUEST': '2', 'ENGINE_CPU_LIMIT': '4', 'ENGINE_MEMORY_REQUEST': '8Gi', 'ENGINE_MEMORY_LIMIT': '16Gi'}))
    assert service == original_service and deployment['spec']['strategy'] == {'type': 'Recreate'}
    pod, base = deployment['spec']['template'], original['spec']['template']
    engine, = pod['spec']['containers']; base_engine, = base['spec']['containers']
    assert engine['image'] == IMAGE and engine['resources'] == base_engine['resources']
    assert engine['securityContext'] == base_engine['securityContext'] and pod['spec']['securityContext'] == base['spec']['securityContext']
    assert engine['args'][:len(base_engine['args'])] == base_engine['args'] and engine['env'][:len(base_engine['env'])] == base_engine['env']
    assert pod['metadata']['labels'] == {**base['metadata']['labels'], 'guard.run': 'a-run-1', 'byul.a/generation': generation}
    assert pod['spec']['enableServiceLinks'] is False and 'enableServiceLinks' not in base['spec']
    added = engine['args'][len(base_engine['args']):]
    assert added == lab.GENERATIONS[generation]['args']
    env = {e['name']: e['value'] for e in engine['env']}
    mounts = {m['mountPath']: m for m in engine['volumeMounts']}
    if lab.GENERATIONS[generation]['sensor']:
        # 센서 세대는 V1 runner 고정이다. P2 V2 runner 확인 세대만 V2를 명시한다.
        assert env['PYTHONPATH'] == lab.CALLBACKS and env['VLLM_USE_V2_MODEL_RUNNER'] == ('1' if generation == 'v2-producers' else '0')
        assert mounts[lab.CALLBACKS]['readOnly'] is True and not mounts[lab.DATA].get('readOnly')
        assert record['host_directories']['lifecycle_data'] == '/srv/byul-a/a-run-1/lifecycle-data'
    else:
        assert 'PYTHONPATH' not in env and lab.CALLBACKS not in mounts and record['host_directories'] == {}
    assert ('/guard-lora' in mounts) is (generation == 'v1-req16-lab') is record['lab_namespace_owner_label_required']
    if generation == 'v1-callback-sync':
        assert '--no-async-scheduling' in engine['args']
    # REQ-14 세대는 워커 프로세스 지점이 불리도록 MP backend로 띄운다.
    mp = engine['args'][engine['args'].index('--distributed-executor-backend') + 1] if '--distributed-executor-backend' in engine['args'] else None
    assert mp == ('mp' if generation in ('v1-callback', 'v1-callback-sync') else None)
    assert (lab.PROFILES_DIR in mounts) is generation.startswith('v1-producers')
    if generation.startswith('v1-producers'):
        assert env['VLLM_SERVER_DEV_MODE'] == '1' and json.loads(engine['args'][engine['args'].index('--profiler-config') + 1])['torch_profiler_dir'] == lab.PROFILES_DIR
    if generation == 'v1-req16-lab':
        assert env['VLLM_ALLOW_RUNTIME_LORA_UPDATING'] == 'True' and env['VLLM_SERVER_DEV_MODE'] == '1'


def test_probe_matches_the_handoff_test_exception_and_engine_policies():
    deployment, _, probe, _ = rendered('v1-callback')
    access = yaml.safe_load((HANDOFF / 'engine-test-access.yaml').read_text())
    isolation = yaml.safe_load((HANDOFF / 'engine-isolation.yaml').read_text())
    sources, = access['spec']['ingress'][0]['from']
    assert sources['podSelector']['matchLabels'].items() <= probe['metadata']['labels'].items()
    engine_labels = deployment['spec']['template']['metadata']['labels']
    for policy in (access, isolation):
        assert policy['spec']['podSelector']['matchLabels'].items() <= engine_labels.items()
    container, = probe['spec']['containers']
    assert container['image'] == IMAGE and 'nvidia.com/gpu' not in json.dumps(container) and 'runtimeClassName' not in probe['spec']
    assert probe['metadata']['labels']['guard.run'] == 'a-run-1' and probe['spec']['automountServiceAccountToken'] is False


def test_rendered_pods_satisfy_the_transport_ownership_check():
    deployment, _, probe, _ = rendered('v1-req16-lab')
    pods = {'engine': {'metadata': {**deployment['spec']['template']['metadata'], 'name': 'serving-engine-x', 'namespace': 'serving-lab', 'uid': 'u1'},
        'spec': deployment['spec']['template']['spec']}, 'probe': probe}
    pods['probe']['metadata'].update(uid='u2')
    for index, (role, pod) in enumerate(pods.items(), 1):
        container = pod['spec']['containers'][0]
        pod['spec']['nodeName'] = 'byul-gpu-01'
        pod['status'] = {'phase': 'Running', 'podIP': f'10.244.0.{index}', 'conditions': [{'type': 'Ready', 'status': 'True'}],
            'containerStatuses': [{'name': container['name'], 'containerID': f'containerd://{index}', 'imageID': IMAGE,
                'restartCount': 0, 'ready': True, 'state': {'running': {'startedAt': '2026-11-03T00:00:00Z'}}}]}
        expected = {'namespace': 'serving-lab', 'namespace_uid': 'ns', 'pod': pod['metadata']['name'], 'pod_uid': pod['metadata']['uid'],
            'container': container['name'], 'container_id': f'containerd://{index}', 'image': IMAGE, 'image_id': IMAGE,
            'restart_count': 0, 'node': 'byul-gpu-01', 'pod_ip': f'10.244.0.{index}', 'started_at': '2026-11-03T00:00:00Z'}
        verify_owned_pod(pod, {'uid': 'ns'}, expected, 'a-run-1')
        with pytest.raises(ValueError):
            verify_owned_pod(pod, {'uid': 'ns'}, expected, 'another-run')


@pytest.mark.parametrize('damage', ['template', 'test-access', 'tag-image', 'run-id', 'revision', 'generation'])
def test_render_refuses_changed_handoff_or_unpinned_inputs(tmp_path, damage):
    copy = tmp_path / 'handoff'; shutil.copytree(HANDOFF, copy)
    options = dict(generation='v1-callback', profile='dev', run_id='a-run-1', engine_image=IMAGE, model_revision=REVISION,
        host_root='/srv/byul-a')
    if damage == 'template': (copy / 'engine.yaml.template').write_text((HANDOFF / 'engine.yaml.template').read_text().replace('"0.70"', '"0.90"'))
    elif damage == 'test-access': (copy / 'engine-test-access.yaml').write_text('kind: NetworkPolicy\n')
    elif damage == 'tag-image': options['engine_image'] = 'docker.io/vllm/vllm-openai:v0.31.0'
    elif damage == 'run-id': options['run_id'] = 'A_RUN'
    elif damage == 'revision': options['model_revision'] = 'main'
    else: options['generation'] = 'v1-callback-tp2'
    with pytest.raises(ValueError, match='AWS_'):
        lab.render(copy, **options)


def test_cli_writes_engine_probe_and_record(tmp_path, capsys):
    out = tmp_path / 'render'
    lab.main(['--handoff-dir', str(HANDOFF), '--generation', 'v2-default', '--run-id', 'a-run-2', '--engine-image', IMAGE,
        '--profile', 'reference', '--enforce-eager', '--out', str(out)])
    record = json.loads((out / 'render.json').read_text())
    assert record['template_sha256'] == lab.TEMPLATE_SHA256 and record['profile'] == 'reference' and record['enforce_eager'] is True
    assert record['outputs_sha256']['engine.yaml'] == lab._sha((out / 'engine.yaml').read_bytes())
    deployment, _ = yaml.safe_load_all((out / 'engine.yaml').read_text())
    engine, = deployment['spec']['template']['spec']['containers']
    assert engine['args'][-1] == '--enforce-eager' and engine['resources']['limits']['memory'] == '24Gi'
    assert json.loads(capsys.readouterr().out)['probe.yaml'] == record['outputs_sha256']['probe.yaml']
