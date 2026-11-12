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
from guard.runtime.comparison import execution_conditions
from tests.builders import baseline_pack
from tests.test_req16_lab_contexts import CohortTransport
from tools import gpu_a_aws_lab as lab
from tools import gpu_a_req17_cohort as cohort
from tools import gpu_a_run_inputs as inputs

HANDOFF = Path(__file__).parent / 'fixtures/aws-handoff-20261026'
UPDATE = Path(__file__).resolve().parents[1]
IMAGE = 'docker.io/vllm/vllm-openai@sha256:' + 'a' * 64
NS_UID = '11111111-2222-3333-4444-555555555555'


def deployed(tmp_path, generation, **render_options):
    engine_yaml, probe_yaml, record = lab.render(HANDOFF, generation=generation, profile='dev', run_id='a-run-1',
        engine_image=IMAGE, model_revision='7ae557604adf67be50417f59c2c2f167def9a775', host_root='/srv/byul-a',
        **render_options)
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


def check(tmp_path, check_id, preset, name, *, media_text_after=None):
    argv = ['check', '--check', check_id, '--env', str(tmp_path / 'env.json'), '--target', str(tmp_path / 'target.json'),
        '--prepared', str(tmp_path / 'prepared'), '--cases', preset, '--policy-draft', str(UPDATE / 'docs/gpu-a/policy-a-draft.yaml'),
        '--out', str(tmp_path / name)]
    if media_text_after is not None:
        argv += ['--media-text-after', media_text_after]
    inputs.main(argv)
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


def test_code_selection_env_requires_installed_version_and_target_checks_launch(tmp_path):
    pods, render = deployed(tmp_path, 'v2-code-selection-trust', engine_version='0.30.0',
                            model_id='microsoft/Phi-3-vision-128k-instruct', code_revision='b' * 40,
                            model_host_snapshot='/srv/serving/models/phi3v-test')
    args = {'gpu_model': 'NVIDIA L4', 'driver': 'test'}
    with pytest.raises(ValueError, match='RUN_INPUTS_INSTALLED_ENGINE_VERSION_MISMATCH'):
        inputs.environment(render, **args)
    with pytest.raises(ValueError, match='RUN_INPUTS_INSTALLED_ENGINE_VERSION_MISMATCH'):
        inputs.environment(render, observed_engine_version='0.7.0', **args)
    env = inputs.environment(render, observed_engine_version='0.30.0', **args)
    assert env.engine.version == '0.30.0' and env.model.id == render['model_id']
    assert env.facts['prefix_cache'].value is None
    namespace = json.loads((tmp_path / 'namespace.json').read_text())
    target = inputs.target(env, render, namespace, pods['engine'], pods['probe'], context='byul-aws-lab')
    assert target['code_marker_root'] == render['code_marker_root']
    assert target['comparison_config']['code_revision'] == 'b' * 40
    assert target['comparison_config']['max_model_len'] == render['max_model_len']
    assert target['comparison_config']['trust_remote_code'] is True
    pods['engine']['spec']['containers'][0]['args'].append('--trust-request-mm-kwargs')
    with pytest.raises(ValueError, match='RUN_INPUTS_RENDERED_LAUNCH_MISMATCH'):
        inputs.target(env, render, namespace, pods['engine'], pods['probe'], context='byul-aws-lab')
    pods['engine']['spec']['containers'][0]['args'].pop()
    pods['engine']['spec']['containers'][0]['command'] = ['sleep', 'infinity']
    with pytest.raises(ValueError, match='RUN_INPUTS_RENDERED_LAUNCH_MISMATCH'):
        inputs.target(env, render, namespace, pods['engine'], pods['probe'], context='byul-aws-lab')


def test_code_selection_comparison_normalizes_version_launch_and_run_paths(tmp_path):
    conditions=[]
    for version in ('0.7.0','0.30.0'):
        folder=tmp_path/version;folder.mkdir()
        pods, render=deployed(folder,'v2-code-selection-trust',engine_version=version,
                              model_id='local-byul/sup08-phi3v-derived',code_revision='b'*40)
        env=inputs.environment(render,gpu_model='NVIDIA L4',driver='test',observed_engine_version=version)
        namespace=json.loads((folder/'namespace.json').read_text())
        target=inputs.target(env,render,namespace,pods['engine'],pods['probe'],context='byul-aws-lab')
        settings={'target':str(folder/'target.json'),'code_marker_root':f'/cache/hf/markers/{version}',
                  'code_module_root':'/cache/hf/modules/transformers_modules'}
        conditions.append(execution_conditions(target,settings,[]))
    assert sha256_hex(canonical_json(conditions[0])) == sha256_hex(canonical_json(conditions[1]))


def test_legacy_render_can_replay_custom_model_id(tmp_path):
    _, render = deployed(tmp_path, 'v2-default')
    render['schema'] = 'byul-gpu-a-aws-render/1'
    for key in ('engine_version', 'model_id', 'model_host_snapshot', 'code_revision', 'code_marker_root', 'launch'):
        render.pop(key)
    env = inputs.environment(render, model_id='Qwen/Qwen2-VL-2B-Instruct', gpu_model='NVIDIA L4', driver='test')
    assert env.engine.version == '0.31.0' and env.model.id == 'Qwen/Qwen2-VL-2B-Instruct'


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
    ('REQ-16', 'req16-salt', 'v1-callback'), ('REQ-16', 'req16-lab', 'v1-req16-lab'),
    ('REQ-16', 'req16-full', 'v1-req16-lab'), ('REQ-18', 'req18-slot', 'v1-callback'),
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
    assert ('lab_contexts' in manifest) is (preset in ('req16-lab', 'req16-full'))
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


def test_trailing_media_text_is_bound_to_the_manifest_and_sent_after_the_image(tmp_path):
    build(tmp_path, 'v1-req16-lab')
    suffix = ' '.join(f'after{i}' for i in range(64))
    original = check(tmp_path, 'REQ-16', 'req16-full', 'run-original')
    out = check(tmp_path, 'REQ-16', 'req16-full', 'run-after', media_text_after=suffix)
    manifest = json.loads((out / 'manifest.json').read_text())
    policy = yaml.safe_load((out / 'policy.yaml').read_text())
    original_policy = yaml.safe_load((original / 'policy.yaml').read_text())
    assert len(manifest['cases']) == 23
    media_cases = [c for c in manifest['cases'] if 'media' in c]
    assert len(media_cases) == 8 and all(c['media']['text_after'] == suffix for c in media_cases)
    assert policy['values']['REQ-16']['sensor_manifest_sha256'] == sha256_hex(canonical_json(manifest))
    assert policy['values']['REQ-16']['sensor_manifest_sha256'] != original_policy['values']['REQ-16']['sensor_manifest_sha256']
    assert all('text_after' not in c.get('media', {}) for c in json.loads((original / 'manifest.json').read_text())['cases'])
    executor = engine_sensor.EngineSensorExecutor({'target': str(out / 'target.json'),
        'prepared': str(tmp_path / 'prepared/manifest.json'), 'manifest': str(out / 'manifest.json')})
    executor.transport = CohortTransport(engine_callbacks.load_secret(tmp_path / 'prepared'), [0] * 20)
    check_def = baseline_pack().checks['REQ-16'].model_copy(deep=True)
    check_def.executor = 'engine_sensor'; check_def.collection_profile = engine_sensor.PROFILES['REQ-16']
    draft = executor.run(check_def, load_environment(tmp_path / 'env.json'), policy['values']['REQ-16'], None, tmp_path / 'work-after')
    parts = [b['messages'][0]['content'] for b in executor.transport.bodies if isinstance(b['messages'][0]['content'], list)]
    assert len(parts) == 8 and all(p[2] == {'type': 'text', 'text': suffix} for p in parts)
    assert draft.data_origin.value == 'EXAMPLE' and draft.observations['engine_sensor_full_scope'].value is False
    assert suffix not in draft.artifacts[0].path.read_text()


@pytest.mark.parametrize('check_id,preset', [('REQ-16', 'req16-salt'), ('REQ-17', 'req16-full'), ('REQ-16', 'custom.json')])
def test_trailing_media_option_is_not_silently_ignored(tmp_path, check_id, preset):
    with pytest.raises(SystemExit) as error:
        check(tmp_path, check_id, preset, 'unused', media_text_after='after')
    assert error.value.code == 2 and not (tmp_path / 'unused').exists()


def test_only_engine_relaunching_lab_checks_get_the_long_duration(tmp_path):
    """LIF-13(소비자 기동)·REQ-21(TP2 재기동)만 900초. 다른 점검은 300초 그대로다."""
    assert engine_sensor.LONG_LAB_CHECKS == {'LIF-13', 'REQ-21'}
    build(tmp_path, 'v1-callback')
    out = check(tmp_path, 'REQ-14', 'req14-normal', 'run-req14-duration')
    assert 'max_duration_seconds = 300' in (out / 'guard.toml').read_text()
    import inspect
    assert "('LIF-13', 'REQ-21')" in inspect.getsource(inputs.check_inputs)
