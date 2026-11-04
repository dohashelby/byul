"""A GPU 1차 입력 조립. 렌더링한 Pod에 상태를 붙인 EXAMPLE로만 확인한다. kubectl·엔진 없음."""
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors import engine_callbacks
from guard.collectors.file import load_environment
from guard.executors import engine_sensor
from guard.models import canonical_json, sha256_hex
from tests.builders import baseline_pack
from tests.test_req16_lab_contexts import CohortTransport
from tools import gpu_a_aws_lab as lab
from tools import gpu_a_req17_cohort as cohort
from tools import gpu_a_run_inputs as inputs

HANDOFF = Path(__file__).parent / 'fixtures/aws-handoff-20261026'
UPDATE = Path(__file__).resolve().parents[1]
IMAGE = 'docker.io/vllm/vllm-openai@sha256:' + 'a' * 64
NS_UID = '11111111-2222-3333-4444-555555555555'


def deployed(tmp_path, generation):
    engine_yaml, probe_yaml, record = lab.render(HANDOFF, generation=generation, profile='dev', run_id='a-run-1',
        engine_image=IMAGE, model_revision='7ae557604adf67be50417f59c2c2f167def9a775', host_root='/srv/byul-a')
    deployment, _ = yaml.safe_load_all(engine_yaml)
    pods = {'engine': {'metadata': {**deployment['spec']['template']['metadata'], 'name': 'serving-engine-7d9f-x2k',
            'namespace': 'serving-lab'}, 'spec': deployment['spec']['template']['spec']}, 'probe': yaml.safe_load(probe_yaml)}
    for index, (role, pod) in enumerate(pods.items(), 1):
        container = pod['spec']['containers'][0]
        pod['metadata']['uid'] = f'00000000-0000-0000-0000-00000000000{index}'
        pod['spec']['nodeName'] = 'byul-gpu-01'
        pod['status'] = {'phase': 'Running', 'podIP': f'10.244.0.{10 + index}', 'conditions': [{'type': 'Ready', 'status': 'True'}],
            'containerStatuses': [{'name': container['name'], 'containerID': 'containerd://' + str(index) * 64,
                'imageID': IMAGE, 'restartCount': 0, 'ready': True, 'state': {'running': {'startedAt': '2026-11-04T01:00:00Z'}}}]}
    files = {'render': record, 'namespace': {'kind': 'Namespace', 'metadata': {'name': 'serving-lab', 'uid': NS_UID}},
        'engine-pod': pods['engine'], 'probe-pod': pods['probe']}
    for name, value in files.items():
        (tmp_path / f'{name}.json').write_text(json.dumps(value))
    return pods, record


def build(tmp_path, generation):
    pods, record = deployed(tmp_path, generation)
    inputs.main(['env', '--render', str(tmp_path / 'render.json'), '--gpu-model', 'NVIDIA L4', '--driver', 'EXAMPLE-driver',
        '--out', str(tmp_path / 'env.json')])
    inputs.main(['target', '--env', str(tmp_path / 'env.json'), '--render', str(tmp_path / 'render.json'),
        '--namespace', str(tmp_path / 'namespace.json'), '--engine-pod', str(tmp_path / 'engine-pod.json'),
        '--probe-pod', str(tmp_path / 'probe-pod.json'), '--context', 'byul-aws-lab', '--out', str(tmp_path / 'target.json')])
    engine_callbacks.prepare_assets(tmp_path / 'prepared')
    return pods, record


def check(tmp_path, check_id, preset, name):
    inputs.main(['check', '--check', check_id, '--env', str(tmp_path / 'env.json'), '--target', str(tmp_path / 'target.json'),
        '--prepared', str(tmp_path / 'prepared'), '--cases', preset, '--policy-draft', str(UPDATE / 'docs/gpu-a/policy-a-draft.yaml'),
        '--out', str(tmp_path / name)])
    return tmp_path / name


def test_env_and_target_bind_the_rendered_generation(tmp_path):
    pods, record = build(tmp_path, 'v1-callback')
    env = load_environment(tmp_path / 'env.json')
    target = json.loads((tmp_path / 'target.json').read_text())
    assert env.engine.image_digest == IMAGE and env.model.revision == record['model_revision']
    assert 'generation=v1-callback' in env.runtime.platform and 'runner=v1' in env.runtime.platform and env.facts['prefix_cache'].value is True
    assert target['environment_ref'] == env.ref().model_dump(mode='json') and target['lab'] is False
    assert target['engine']['trusted_url'] == 'http://10.244.0.11:8000' and target['kubernetes']['run_id'] == 'a-run-1'
    assert target['kubernetes']['probe']['pod'] == 'engine-test-probe-a-run-1' and target['model_mapping']['served_model_name'] == 'test-model'
    assert load_environment(tmp_path / 'env.json').ref() == env.ref()


@pytest.mark.parametrize('damage', ['run-label', 'generation-label', 'probe-role', 'engine-image'])
def test_target_refuses_pods_from_another_run_or_role(tmp_path, damage):
    pods, record = deployed(tmp_path, 'v1-callback')
    inputs.main(['env', '--render', str(tmp_path / 'render.json'), '--gpu-model', 'NVIDIA L4', '--driver', 'x', '--out', str(tmp_path / 'env.json')])
    env = load_environment(tmp_path / 'env.json')
    namespace = json.loads((tmp_path / 'namespace.json').read_text())
    if damage == 'run-label': pods['probe']['metadata']['labels']['guard.run'] = 'other-run'
    elif damage == 'generation-label': pods['engine']['metadata']['labels']['byul.a/generation'] = 'v2-default'
    elif damage == 'probe-role': pods['probe']['metadata']['labels']['app'] = 'serving-gateway'
    else: pods['engine']['spec']['containers'][0]['image'] = 'docker.io/vllm/vllm-openai@sha256:' + 'b' * 64
    with pytest.raises(ValueError, match='RUN_INPUTS_'):
        inputs.target(env, record, namespace, pods['engine'], pods['probe'], context='byul-aws-lab')


@pytest.mark.parametrize('check_id,preset,generation', [('REQ-14', 'req14-normal', 'v1-callback'),
    ('REQ-16', 'req16-salt', 'v1-callback'), ('REQ-16', 'req16-lab', 'v1-req16-lab'), ('REQ-18', 'req18-slot', 'v1-callback'),
    ('REQ-17', 'cohort', 'v1-callback')])
def test_check_inputs_pass_the_executor_binding_and_selection(tmp_path, check_id, preset, generation):
    build(tmp_path, generation)
    if preset == 'cohort':
        (tmp_path / 'cohort.json').write_text(json.dumps(cohort.build(5, quartets=24)))
        preset = str(tmp_path / 'cohort.json')
    out = check(tmp_path, check_id, preset, 'run-' + check_id.lower())
    env = load_environment(tmp_path / 'env.json')
    policy = yaml.safe_load((out / 'policy.yaml').read_text())
    manifest = json.loads((out / 'manifest.json').read_text())
    values = policy['values'][check_id]
    assert policy['status'] == 'DRAFT' and policy['confirmed_by'] is None and policy['scope']['include'] == [check_id]
    assert policy['data_origin'] == 'DECLARED' and values['sensor_manifest_sha256'] == sha256_hex(canonical_json(manifest))
    assert ('lab_contexts' in manifest) is (preset == 'req16-lab')
    if check_id == 'REQ-16':
        assert values['required_cache_contexts'] == ['cache_salt', 'adapter_content', 'media_identity', 'model_generation']
    if check_id == 'REQ-17':
        assert values['cache_timing_min_samples'] == 24 and len(manifest['cases']) == 124
    config = yaml.safe_load('\n'.join(line.replace(' = ', ': ', 1) for line in (out / 'guard.toml').read_text().splitlines()
        if ' = ' in line and not line.startswith('store')))
    executor = engine_sensor.EngineSensorExecutor({'target': config['target'].strip("'"), 'prepared': config['prepared'].strip("'"),
        'manifest': config['manifest'].strip("'"), 'max_duration_seconds': 300})
    sent = sum(c['role'] != 'switch' for c in manifest['cases'])
    executor.transport = CohortTransport(engine_callbacks.load_secret(tmp_path / 'prepared'), [0] * sent)
    check_def = baseline_pack().checks[check_id].model_copy(deep=True)
    check_def.executor = 'engine_sensor'; check_def.collection_profile = engine_sensor.PROFILES[check_id]
    draft = executor.run(check_def, env, values, None, tmp_path / 'work')
    assert draft.abort_reason != 'ENGINE_SENSOR_NATIVE_BINDING_MISMATCH' and len(executor.transport.order) >= sent
    result = CliRunner().invoke(app, ['select', '--pack', str(UPDATE / 'packs/PACK-BASELINE'), '--env', str(tmp_path / 'env.json'),
        '--policy', str(out / 'policy.yaml'), '--config', str(out / 'guard.toml')])
    assert result.exit_code == 0, result.output
    assert 'selected 1/' in result.output


def test_lab_cases_need_the_lab_generation_target(tmp_path):
    build(tmp_path, 'v1-callback')
    with pytest.raises(ValueError, match='RUN_INPUTS_LAB_CASES_NEED_LAB_TARGET'):
        check(tmp_path, 'REQ-16', 'req16-lab', 'run-lab')
