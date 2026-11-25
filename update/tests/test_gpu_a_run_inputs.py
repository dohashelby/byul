"""A GPU 1차 입력 조립. 렌더링한 Pod에 상태를 붙인 EXAMPLE로만 확인한다. kubectl·엔진 없음."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from guard.adapters.vllm_lifecycle_catalog import approved_file_read_preparation
from guard.cli import app
from guard.collectors import engine_callbacks
from guard.collectors.file import load_environment
from guard.executors import engine_sensor, run_binding
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from guard.runtime.comparison import execution_conditions
from tests.builders import baseline_pack
from tests.test_lifecycle_capture import external  # noqa: F401
from tests.test_req16_lab_contexts import CohortTransport
from tools import gpu_a_aws_lab as lab
from tools import gpu_a_req17_cohort as cohort
from tools import gpu_a_run_inputs as inputs
from tools.mvp_versions import versions

HANDOFF = Path(__file__).parent / 'fixtures/aws-handoff-20261026'
UPDATE = Path(__file__).resolve().parents[1]
IMAGE = 'docker.io/vllm/vllm-openai@sha256:' + 'a' * 64
NS_UID = '11111111-2222-3333-4444-555555555555'


def deployed(tmp_path, generation, *, run_id='a-run-1', **render_options):
    image = render_options.pop('engine_image', IMAGE)
    engine_yaml, probe_yaml, record = lab.render(HANDOFF, generation=generation, profile='dev', run_id=run_id,
        engine_image=image, model_revision='7ae557604adf67be50417f59c2c2f167def9a775', host_root='/srv/byul-a',
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
                'imageID': image, 'restartCount': 0, 'ready': True, 'state': {'running': {'startedAt': '2026-11-04T01:00:00Z'}}}]}
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


def test_external_envs_keep_reviewed_v1_with_comparable_runtime(tmp_path, capsys):
    environments, renders = [], []
    for side, release in versions().items():
        folder = tmp_path / side
        folder.mkdir()
        _, render = deployed(folder, 'v1-callback', run_id=f'a-{side}', engine_version=release['version'],
                             engine_image=release['image'])
        source = folder / 'render.json'
        raw = source.read_bytes()
        output = folder / 'env.json'
        argv = ['env', '--render', str(source), '--external-platform', 'Linux x86_64',
                '--gpu-model', 'NVIDIA L4', '--driver', 'EXAMPLE-driver', '--model-inventory', 'b' * 64,
                '--observed-engine-version', release['version'], '--out', str(output)]
        inputs.main(argv)
        env = load_environment(output)
        assert json.loads(capsys.readouterr().out) == {
            'env': str(output), 'sha256': env.ref().sha256, 'render': {'path': str(source), 'sha256': sha256_hex(raw)}}
        assert env.runtime.platform == ('Linux x86_64, external Kubernetes; generation=v1-callback; profile=dev; '
                                        'engine_requested=V1; runner=v1; async_requested=None; '
                                        'engine_version_observed=True; eager=False')
        assert env.data_origin.value == 'DECLARED'
        assert env.runtime.gpu_model == 'NVIDIA L4' and env.runtime.driver == 'EXAMPLE-driver'
        assert env.model.id == render['model_id'] and env.model.revision == render['model_revision']
        assert env.model.inventory_sha256 == 'b' * 64 and env.facts['prefix_cache'].value is True
        saved = output.read_bytes()
        with pytest.raises(FileExistsError):
            inputs.main(argv)
        assert output.read_bytes() == saved and source.read_bytes() == raw
        environments.append(env)
        renders.append(render)
    assert renders[0]['run_id'] != renders[1]['run_id']
    assert renders[0]['outputs_sha256']['engine.yaml'] != renders[1]['outputs_sha256']['engine.yaml']
    assert environments[0].runtime == environments[1].runtime
    assert [approved_file_read_preparation(env) for env in environments] == [
        {'profile': 'vllm-native-lifecycle-metadata/35', 'model_root': '/models'},
        {'profile': 'vllm-native-lifecycle-metadata/36', 'model_root': '/models'},
    ]


@pytest.mark.parametrize('side', ('baseline', 'candidate'))
@pytest.mark.parametrize('readiness', ('ready', 'absent', 'not-ready', 'container-not-ready', 'duplicate',
                                      'before-start', 'restarted'))
def test_owned_normal_tp2_retains_only_the_qualified_ready_window(tmp_path, side, readiness):
    release = versions()[side]
    pods, render = deployed(tmp_path, 'v2-template', engine_version=release['version'], engine_image=release['image'],
                            release_launch=True, normal_tp2=True)
    namespace = json.loads((tmp_path / 'namespace.json').read_bytes())
    pod = pods['engine']
    pod['kind'] = 'Pod'
    pod_status = pod['status']
    ready, = pod_status['conditions']
    engine_status, = pod_status['containerStatuses']
    started_at = '2026-11-04T01:00:00.123456788Z'
    ready_at = '2026-11-04T01:00:00.123456789Z'
    engine_status['state']['running']['startedAt'] = started_at
    ready['lastTransitionTime'] = ready_at
    if readiness == 'absent':
        del ready['lastTransitionTime']
    elif readiness == 'not-ready':
        ready['status'] = 'False'
    elif readiness == 'container-not-ready':
        engine_status['ready'] = False
    elif readiness == 'duplicate':
        pod_status['conditions'].append(dict(ready))
    elif readiness == 'before-start':
        ready['lastTransitionTime'] = '2026-11-04T01:00:00.123456787Z'
    elif readiness == 'restarted':
        engine_status['restartCount'] = 1

    observed = release['version']
    env = inputs.environment(render, inventory='b' * 64, gpu_model='NVIDIA L4', driver='EXAMPLE-driver',
                             observed_engine_version=observed, external_platform='Linux x86_64',
                             engine_pod=pod, namespace=namespace, observed_gpu_count=2)
    target = inputs.target(env, render, namespace, pod, pods['probe'], context='byul-aws-lab',
                           observed_engine_version=observed)
    if readiness == 'ready':
        assert target['startup_ready_window'] == {'container_started_at': started_at, 'ready_at': ready_at}
    else:
        assert 'startup_ready_window' not in target
    assert 'startup_log_prefix' not in target


@pytest.mark.parametrize('side', ['baseline', 'candidate'])
@pytest.mark.parametrize('fault', [None, 'ready-changed', 'input-replaced', 'wrong-pin', 'existing-output'])
def test_owned_tp2_startup_prefix_command_uses_exact_saved_and_current_inputs(tmp_path, monkeypatch, capsys, side, fault):
    from guard.executors.kubernetes_normal_startup_load import normal_startup_load_transport
    from guard.runtime import kubernetes_http

    release = versions()[side]
    pods, render = deployed(tmp_path, 'v2-template', engine_version=release['version'], engine_image=release['image'],
                            release_launch=True, normal_tp2=True)
    namespace = json.loads((tmp_path / 'namespace.json').read_bytes())
    for pod in pods.values():
        pod['kind'] = 'Pod'
    started_at = '2026-11-04T01:00:00Z'
    ready_at = '2026-11-04T01:00:35.123456789Z'
    pods['engine']['status']['conditions'][0]['lastTransitionTime'] = ready_at
    observed = release['version']
    env = inputs.environment(render, inventory='b' * 64, gpu_model='NVIDIA L4', driver='EXAMPLE-driver',
                             observed_engine_version=observed, external_platform='Linux x86_64',
                             engine_pod=pods['engine'], namespace=namespace, observed_gpu_count=2)
    start_target = inputs.target(env, render, namespace, pods['engine'], pods['probe'], context='fixture-context',
                                 observed_engine_version=observed)
    env_path, target_path, output = (tmp_path / name for name in ('env.json', 'original-target.json', 'prefixed.json'))
    env_raw, target_raw = canonical_json(env.to_dict()), canonical_json(start_target)
    env_path.write_bytes(env_raw)
    target_path.write_bytes(target_raw)
    if fault == 'existing-output':
        output.write_bytes(b'original output')
    raw_log = b'2026-11-04T01:00:03.123456789Z EXAMPLE early boot\n'
    log_reads, readbacks = [], []

    def run(argv, **kwargs):
        assert argv[:2] == ['kubectl', '--context=fixture-context']
        assert kwargs['timeout'] > 0 and 'input_bytes' not in kwargs
        command = argv[3:]
        if command == ['get', 'namespaces', 'serving-lab', '-o', 'json']:
            raw = canonical_json({'kind': 'List', 'items': [namespace]})
        elif 'get' in command:
            role, = (role for role, pod in pods.items() if pod['metadata']['name'] in command)
            pod = deepcopy(pods[role])
            if fault == 'ready-changed' and role == 'engine' and log_reads:
                pod['status']['conditions'][0]['lastTransitionTime'] = '2026-11-04T01:00:35.123456790Z'
            raw = canonical_json(pod)
            readbacks.append(role)
        else:
            engine_name = pods['engine']['metadata']['name']
            assert command == ['--namespace=serving-lab', 'logs', engine_name, '--container=engine', '--timestamps',
                               '--since-time=' + started_at, '--limit-bytes=65536']
            log_reads.append(True)
            raw = raw_log
            if fault == 'input-replaced':
                target_path.write_bytes(target_raw + b'\n')
        return {'returncode': 0, 'stdout_bytes': raw, 'capture': {
            'complete': True, 'bounded_at_source': True, 'truncated': False, 'timed_out': False,
        }}

    monkeypatch.setattr(kubernetes_http, 'capture_pipes', run)
    target_digest = '0' * 64 if fault == 'wrong-pin' else sha256_hex(target_raw)
    command = ['startup-prefix', '--env', str(env_path), '--env-sha256', sha256_hex(env_raw),
               '--target', str(target_path), '--target-sha256', target_digest, '--out', str(output)]
    errors = {'ready-changed': 'LIF01_BOOT_PREFIX_UNVERIFIED',
              'input-replaced': 'RUN_INPUTS_STARTUP_PREFIX_SOURCE_CHANGED',
              'wrong-pin': 'RUN_INPUTS_STARTUP_PREFIX_SOURCE_CHANGED',
              'existing-output': 'RUN_INPUTS_STARTUP_PREFIX_NEW_TARGET_REQUIRED'}
    if fault:
        with pytest.raises(ValueError, match=f'^{errors[fault]}$'):
            inputs.main(command)
        if fault == 'existing-output':
            assert output.read_bytes() == b'original output'
        else:
            assert not output.exists()
        if fault in {'wrong-pin', 'existing-output'}:
            assert not readbacks and not log_reads
        else:
            assert log_reads == [True]
        return
    inputs.main(command)
    raw = output.read_bytes()
    saved = json.loads(raw)
    receipt = json.loads(capsys.readouterr().out)
    assert receipt == {'target': str(output), 'sha256': sha256_hex(canonical_json(saved)),
                       'target_file_sha256': sha256_hex(raw)}
    assert receipt['target_file_sha256'] != receipt['sha256']
    assert log_reads == [True]
    assert saved.pop('startup_log_prefix') == {
        **start_target['startup_ready_window'], 'byte_count': len(raw_log), 'sha256': sha256_hex(raw_log),
    }
    assert saved == start_target and target_path.read_bytes() == target_raw and env_path.read_bytes() == env_raw
    # A log selection says nothing about the observed rank producer contract.
    with pytest.raises(ValueError, match='^LIF01_BOOT_TARGET_UNVERIFIED$'):
        normal_startup_load_transport(json.loads(output.read_bytes()), env, duration=1)


@pytest.mark.parametrize('side', ('baseline', 'candidate'))
@pytest.mark.parametrize('generation', ('v2-default', 'v2-template', lab.NORMAL_TP2_V1_EAGER_GENERATION))
def test_owned_normal_tp2_render_binds_observed_layout_and_version(tmp_path, side, generation):
    release = versions()[side]
    pods, render = deployed(tmp_path, generation, engine_version=release['version'], engine_image=release['image'],
                            release_launch=True, normal_tp2=True)
    pod = pods['engine']
    pod['kind'] = 'Pod'
    engine = pod['spec']['containers'][0]
    assert engine['args'][-len(lab.NORMAL_TP2_ARGS):] == list(lab.NORMAL_TP2_ARGS)
    assert all(engine['args'].count(key) == 1 for key in lab.NORMAL_TP2_ARGS[::2])
    assert [engine['resources'][key][lab.GPU_RESOURCE] for key in ('requests', 'limits')] == ['2', '2']
    engine_path = tmp_path / 'engine-pod.json'
    engine_path.write_bytes(canonical_json(pod))
    namespace_path = tmp_path / 'namespace.json'
    namespace = json.loads(namespace_path.read_bytes())
    assert render['normal_tp2'] == lab.NORMAL_TP2
    kwargs = {'inventory': 'b' * 64, 'gpu_model': 'NVIDIA L4', 'driver': 'EXAMPLE-driver',
              'observed_engine_version': release['version'], 'external_platform': 'Linux x86_64',
              'namespace': namespace, 'engine_pod': pod}
    with pytest.raises(ValueError, match='^RUN_INPUTS_NORMAL_TP2_OBSERVATION_REQUIRED$'):
        inputs.environment(render, **kwargs)
    env_path = tmp_path / 'env.json'
    inputs.main(['env', '--render', str(tmp_path / 'render.json'), '--external-platform', 'Linux x86_64',
                 '--gpu-model', 'NVIDIA L4', '--driver', 'EXAMPLE-driver', '--model-inventory', 'b' * 64,
                 '--observed-engine-version', release['version'], '--observed-gpu-count', '2',
                 '--engine-pod', str(engine_path), '--namespace', str(namespace_path), '--out', str(env_path)])
    env = load_environment(env_path)
    assert {key: getattr(env.runtime, key) for key in lab.NORMAL_TP2} == lab.NORMAL_TP2
    assert env.data_origin.value == 'DECLARED'

    target_path = tmp_path / 'target.json'
    inputs.main(['target', '--env', str(env_path), '--render', str(tmp_path / 'render.json'),
                 '--engine-pod', str(engine_path), '--namespace', str(namespace_path),
                 '--probe-pod', str(tmp_path / 'probe-pod.json'), '--context', 'byul-aws-lab',
                 '--observed-engine-version', release['version'], '--out', str(target_path)])
    saved = json.loads(target_path.read_bytes())
    assert saved['observed_version'] == release['version']
    assert saved['environment_ref'] == env.ref().model_dump(mode='json')
    assert saved['lab'] is False
    launch = {'argv': engine['args'], 'max_model_len': render['max_model_len']}
    if generation in lab.NORMAL_TP2_TEMPLATE_GENERATIONS:
        assert engine['args'].count('--chat-template') == 1
        launch['chat_template'] = {'profile': inputs.CHAT_TEMPLATE_PROFILE, 'version': '1',
                                   'sha256': lab.CHAT_TEMPLATE_SHA256, 'container_path': lab.CHAT_TEMPLATE_PATH,
                                   'content_format': 'string', 'request_overrides': False}
        mount, = (item for item in engine['volumeMounts'] if item['name'] == 'guard-chat-template')
        mount['readOnly'] = False
        with pytest.raises(ValueError, match='^RUN_INPUTS_NORMAL_TP2_POD_MISMATCH$'):
            inputs.target(env, render, namespace, pod, pods['probe'], context='byul-aws-lab',
                          observed_engine_version=release['version'])
        mount['readOnly'] = True
    else:
        assert 'chat_template' not in render
    assert saved['comparison_config']['launch'] == launch
    with pytest.raises(ValueError, match='^RUN_INPUTS_NORMAL_TP2_POD_MISMATCH$'):
        inputs.target(env, {**render, 'max_model_len': 1024}, namespace, pod, pods['probe'],
                      context='byul-aws-lab', observed_engine_version=release['version'])
    with pytest.raises(ValueError, match='^RUN_INPUTS_NORMAL_TP2_MODEL_MISMATCH$'):
        inputs.target(env, render, namespace, pod, pods['probe'], context='byul-aws-lab',
                      api_model='different-model', observed_engine_version=release['version'])
    with pytest.raises(ValueError, match='^RUN_INPUTS_INSTALLED_ENGINE_VERSION_MISMATCH$'):
        inputs.target(env, render, namespace, pod, pods['probe'], context='byul-aws-lab',
                      observed_engine_version='other-version')
    engine['resources']['limits'][lab.GPU_RESOURCE] = '1'
    with pytest.raises(ValueError, match='^RUN_INPUTS_NORMAL_TP2_POD_MISMATCH$'):
        inputs.environment(render, observed_gpu_count=2, **kwargs)
    engine['resources']['limits'][lab.GPU_RESOURCE] = '2'
    if generation != lab.NORMAL_TP2_V1_EAGER_GENERATION:
        assert render['launch']['runner'] == 'engine-default'
        return
    assert render['launch']['runner'] == 'v1'
    assert 'runner=v1' in env.runtime.platform.split('; ')
    assert approved_file_read_preparation(env)['model_root'] == '/models'
    assert engine['args'].count(lab.EAGER_WEIGHT_READ_ARG) == 1
    assert '--enforce-eager' not in engine['args']
    fixed_env = {'name': lab.V2_MODEL_RUNNER_ENV, 'value': '0'}
    assert engine['env'].count(fixed_env) == 1
    with pytest.raises(ValueError, match='^AWS_NORMAL_TP2_V1_EAGER_LAUNCH_UNAVAILABLE$'):
        deployed(tmp_path, generation, engine_version=release['version'], engine_image=release['image'], release_launch=True)
    wrong_runtime = env.runtime.model_copy(update={'platform': env.runtime.platform.replace('runner=v1', 'runner=engine-default')})
    wrong_env = env.model_copy(update={'runtime': wrong_runtime}).seal()
    with pytest.raises(ValueError, match='^RUN_INPUTS_NORMAL_TP2_POD_MISMATCH$'):
        inputs.target(wrong_env, render, namespace, pod, pods['probe'], context='byul-aws-lab',
                      observed_engine_version=release['version'])

    def validate_changed_pod(changed_pod, changed_render=render):
        with pytest.raises(ValueError, match='^RUN_INPUTS_NORMAL_TP2_POD_MISMATCH$'):
            inputs.environment(changed_render, **{**kwargs, 'engine_pod': changed_pod}, observed_gpu_count=2)
        with pytest.raises(ValueError, match='^RUN_INPUTS_NORMAL_TP2_POD_MISMATCH$'):
            inputs.target(env, changed_render, namespace, changed_pod, pods['probe'],
                          context='byul-aws-lab', observed_engine_version=release['version'])

    for selectors in ([], [{**fixed_env, 'value': '1'}], [fixed_env, fixed_env]):
        changed_pod = deepcopy(pod)
        changed_engine = changed_pod['spec']['containers'][0]
        changed_engine['env'] = [item for item in changed_engine['env'] if item['name'] != lab.V2_MODEL_RUNNER_ENV]
        changed_engine['env'].extend(selectors)
        validate_changed_pod(changed_pod)
    changed_pod = deepcopy(pod)
    changed_argv = changed_pod['spec']['containers'][0]['args']
    changed_argv.remove(lab.EAGER_WEIGHT_READ_ARG)
    validate_changed_pod(changed_pod, {**render, 'launch': {**render['launch'], 'argv': changed_argv}})



@pytest.mark.parametrize('damage', ('legacy', 'mode-missing', 'mode-v2', 'mode-env', 'image'))
def test_external_env_requires_reviewed_v1_render(tmp_path, damage):
    release = versions()['baseline']
    _, render = deployed(tmp_path, 'v1-callback', engine_version=release['version'], engine_image=release['image'])
    if damage == 'legacy':
        render['schema'] = 'byul-gpu-a-aws-render/1'
    elif damage == 'mode-missing':
        del render['launch']['engine']
    elif damage == 'mode-v2':
        render['launch']['engine'] = 'V2'
    elif damage == 'mode-env':
        render['launch']['env'] = {'EXAMPLE_MODE': 'unreviewed'}
    else:
        render['engine_image'] = IMAGE
    with pytest.raises(ValueError, match='^RUN_INPUTS_EXTERNAL_ENV_REVIEWED_V1_REQUIRED$'):
        inputs.environment(render, gpu_model='NVIDIA L4', driver='EXAMPLE-driver', external_platform='Linux x86_64')


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
    # @35: the policy carries no run-binding key; guard run computes it from the files below.
    assert policy['data_origin'] == 'DECLARED' and not set(values) & set(run_binding.KEYS_BY_CHECK[check_id])
    assert ('lab_contexts' in manifest) is (preset in ('req16-lab', 'req16-full'))
    if check_id == 'REQ-16':
        assert values['required_cache_contexts'] == ['cache_salt', 'adapter_content', 'media_identity', 'model_generation']
    if check_id == 'REQ-17':
        assert values['cache_timing_min_samples'] == 24 and len(manifest['cases']) == 124
    config = yaml.safe_load('\n'.join(line.replace(' = ', ': ', 1) for line in (out / 'guard.toml').read_text().splitlines()
        if ' = ' in line and not line.startswith('store')))
    sensor_cfg = {'target': config['target'].strip("'"), 'prepared': config['prepared'].strip("'"),
        'manifest': config['manifest'].strip("'"), 'max_duration_seconds': 300}
    binding, values = run_binding.effective_values(load_pack(UPDATE / 'packs/PACK-BASELINE').checks[check_id], sensor_cfg, env, values,
        side='single', attempt_id=None, issued_at=utcnow())
    assert binding['files']['sensor_manifest_sha256'] == sha256_hex(canonical_json(manifest))
    executor = engine_sensor.EngineSensorExecutor(sensor_cfg)
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
    bound = run_binding.file_hash('REQ-16', 'sensor_manifest_sha256', {'manifest': str(out / 'manifest.json')})
    assert bound == sha256_hex(canonical_json(manifest))
    assert bound != run_binding.file_hash('REQ-16', 'sensor_manifest_sha256', {'manifest': str(original / 'manifest.json')})
    assert 'sensor_manifest_sha256' not in policy['values']['REQ-16'] | original_policy['values']['REQ-16']
    assert all('text_after' not in c.get('media', {}) for c in json.loads((original / 'manifest.json').read_text())['cases'])
    sensor_cfg = {'target': str(out / 'target.json'), 'prepared': str(tmp_path / 'prepared/manifest.json'),
                  'manifest': str(out / 'manifest.json')}
    executor = engine_sensor.EngineSensorExecutor(sensor_cfg)
    executor.transport = CohortTransport(engine_callbacks.load_secret(tmp_path / 'prepared'), [0] * 20)
    check_def = baseline_pack().checks['REQ-16'].model_copy(deep=True)
    check_def.executor = 'engine_sensor'; check_def.collection_profile = engine_sensor.PROFILES['REQ-16']
    env = load_environment(tmp_path / 'env.json')
    _, values = run_binding.effective_values(load_pack(UPDATE / 'packs/PACK-BASELINE').checks['REQ-16'], sensor_cfg, env, policy['values']['REQ-16'],
        side='single', attempt_id=None, issued_at=utcnow())
    draft = executor.run(check_def, env, values, None, tmp_path / 'work-after')
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


@pytest.mark.parametrize('side', ['baseline', 'candidate'])
@pytest.mark.parametrize('case', ['exact', 'ready', 'restarted', 'foreign-marker', 'changed-argv',
                                  'shadow-mount', 'wrong-generation', 'wrong-version'])
def test_sup05_first_import_stages_only_the_held_ordinary_container(external, monkeypatch, side, case):
    import socket
    import subprocess

    from guard.executors.kubernetes_sup05_normal_preparation import prepare
    from guard.runtime.kubernetes_binding import validate_capture_target
    from tests.test_lifecycle_capture import REPOSITORY
    from tools import gpu_a_sup05_first_import as hold
    from tools import gpu_a_sup05_first_import_runtime as waiter

    def forbid(*_args, **_kwargs):
        pytest.fail('Offline SUP-05 preparation must not dispatch work')

    monkeypatch.setattr(socket, 'socket', forbid)
    monkeypatch.setattr(subprocess, 'Popen', forbid)
    monkeypatch.setattr(waiter.os, 'execvpe', forbid)

    version = versions()[side]
    render_options = {'engine_version': version['version'], 'engine_image': version['image'],
                      'release_launch': True, 'normal_tp2': True}
    pods, render = deployed(external, lab.NORMAL_TP2_V1_EAGER_GENERATION, sup05_first_import=True, **render_options)
    namespace = json.loads((external / 'namespace.json').read_bytes())
    namespace['metadata']['labels'] = {'byul.lab/owned': 'true'}
    pod = pods['engine']
    pod['kind'] = 'Pod'
    pod['status']['conditions'][0]['status'] = 'False'
    status = pod['status']['containerStatuses'][0]
    status['ready'] = False
    container = pod['spec']['containers'][0]
    legacy_folder = external / 'legacy'
    legacy_folder.mkdir()
    legacy_pods, legacy = deployed(legacy_folder, lab.NORMAL_TP2_V1_EAGER_GENERATION, **render_options)
    legacy_container = legacy_pods['engine']['spec']['containers'][0]
    assert render['launch'] == legacy['launch']
    assert container['args'] == legacy_container['args']
    assert container['command'][:5] == ['python3', '-I', '-S', '-B', '-c']
    assert container['securityContext'] == legacy_container['securityContext']
    assert pod['spec']['securityContext'] == legacy_pods['engine']['spec']['securityContext']
    assert container['volumeMounts'] == legacy_container['volumeMounts'] + hold.mounts()
    assert 'sup05_first_import' not in legacy
    expected = hold.expected_launch(version['version'], render['run_id'], render['launch'])
    assert expected['abi'] == 'cpython-3.12.3'
    if side == 'candidate':
        assert expected['wrapper'] == (hold.NONROOT_WRAPPER, hold.NONROOT_WRAPPER_SHA256)
    else:
        assert expected['command'] == ['vllm', 'serve'] and expected['wrapper'] is None

    rows = [{'path': 'model.safetensors', 'sha256': 'a' * 64, 'bytes': 73}]
    inventory = sha256_hex(canonical_json(rows))

    engine = inputs._binding(pod, namespace, 'engine', 'serving-engine')
    waiting = {'schema': waiter.WAITING_SCHEMA, 'owner': {key: engine[key] for key in hold.IDENTITY},
               'run_id': render['run_id'], 'pid': 1, 'nonce': 'a' * 32, 'launch_sha256': expected['launch_sha256'],
               'observed_engine_version': version['version']}
    env_options = {'inventory': inventory, 'gpu_model': 'NVIDIA L4', 'driver': 'EXAMPLE-driver',
                   'external_platform': 'Linux x86_64', 'engine_pod': pod, 'namespace': namespace,
                   'observed_gpu_count': 2, 'data_origin': 'EXAMPLE'}
    if case == 'wrong-version':
        waiting['observed_engine_version'] = '0.0.0'
        with pytest.raises(ValueError, match='^SUP05_FIRST_IMPORT_OBSERVED_VERSION_REQUIRED$'):
            inputs.environment(render, sup05_waiting_raw=canonical_json(waiting), **env_options)
        return
    env = inputs.environment(render, sup05_waiting_raw=canonical_json(waiting), **env_options)
    assert env.engine.version == version['version']
    request_target = inputs.target(env, render, namespace, pod, pods['probe'], context='fixture-context',
                                   observed_engine_version=version['version'])
    assert request_target['lab'] is False and request_target['engine']['command'] == container['command']
    if case == 'ready':
        pod['status']['conditions'][0]['status'] = 'True'
    elif case == 'restarted':
        status['restartCount'] = 1
    elif case == 'foreign-marker':
        waiting['owner']['pod_uid'] = '11111111-2222-3333-4444-555555555555'
    elif case == 'changed-argv':
        container['args'] = [*container['args'], '--disable-log-requests']
    elif case == 'shadow-mount':
        container['volumeMounts'] += [{'name': 'fixture-extra', 'mountPath': '/guard-lifecycle/extra', 'readOnly': True}]
    snapshot = {'pod': pod, 'namespace': namespace}
    inputs_by_role = {'env': env.to_dict(), 'render': render, 'waiting': waiting, 'owner-snapshot': snapshot}
    refs = {}
    for role, obj in inputs_by_role.items():
        path = external / (role + '.json')
        path.write_bytes(canonical_json(obj))
        refs[role] = path, sha256_hex(path.read_bytes())
    def options(selected):
        return [flag for role, (path, digest) in selected.items()
                for flag in ('--' + role, str(path), '--' + role + '-sha256', digest)]

    target = external / 'lifecycle-target.json'
    command = ['sup05-first-import-target', '--context', 'fixture-context', '--out', str(target), *options(refs)]
    if case in {'ready', 'restarted', 'foreign-marker', 'changed-argv', 'shadow-mount'}:
        with pytest.raises(ValueError, match='^SUP05_FIRST_IMPORT_OWNER_UNVERIFIED$'):
            inputs.main(command)
        assert not target.exists()
        return

    inputs.main(command)
    native_target = json.loads(target.read_bytes())
    assert validate_capture_target(native_target, env)['engine'] == engine
    manifest = external / 'reference-manifest.json'
    manifest.write_bytes(canonical_json({'schema': 'guard.model-manifest/1', 'scope': 'all',
        'model_id': env.model.id, 'files': rows, 'inventory_sha256': inventory}))
    prepared = external / 'prepared'
    receipt = prepare(prepared, REPOSITORY, environment=refs['env'][0], target=target,
        owner_snapshot=refs['owner-snapshot'][0], owner_snapshot_sha256=refs['owner-snapshot'][1],
        model_manifest=manifest, model_manifest_sha256=sha256_hex(manifest.read_bytes()), nonsecret_model_weights=True)
    preparation = prepared / 'preparation.json'
    refs['preparation'] = (preparation, sha256_hex(preparation.read_bytes()))
    if case == 'wrong-generation':
        status['containerID'] = 'containerd://' + '9' * 64
        owner_path, _ = refs['owner-snapshot']
        owner_path.write_bytes(canonical_json(snapshot))
        refs['owner-snapshot'] = owner_path, sha256_hex(owner_path.read_bytes())
    output = external / 'release'
    command = ['sup05-first-import-release', '--context', 'fixture-context', '--out', str(output), *options(refs)]
    if case == 'wrong-generation':
        with pytest.raises(ValueError, match='^SUP05_NORMAL_PUBLICATION_INVALID$'):
            inputs.main(command)
        assert not output.exists()
        return

    inputs.main(command)
    staged = output / 'lifecycle'
    release = (staged / waiter.RELEASE).read_bytes()
    assets = {name: (staged / name).read_bytes() for name in ['manifest.json', *expected['assets']]}
    for name, raw in assets.items():
        assert (prepared / 'lifecycle' / name).read_bytes() == raw
    bootstrap_path = Path(waiter.IMPORT_DIRECTORY) / waiter.BOOTSTRAP_NAME
    assert list((staged / waiter.IMPORT_DIRECTORY).iterdir()) == [staged / bootstrap_path]
    assert (staged / bootstrap_path).read_bytes() == assets[waiter.BOOTSTRAP_NAME]
    held = refs['waiting'][0].read_bytes()
    assert waiter.verify_release(expected, held, release, assets) == receipt['session']
    with pytest.raises(ValueError):
        waiter.verify_release(expected, canonical_json({**waiting, 'nonce': 'b' * 32}), release, assets)

    # A consistently rehashed but changed observer cannot change the pinned generated code.
    source = 'sitecustomize.py'
    mutant = assets[source] + b'# different fixture bytes\n'
    changed_manifest = json.loads(assets['manifest.json'])
    changed_manifest['assets'][source] = {'bytes': len(mutant), 'sha256': sha256_hex(mutant)}
    mutated = {**assets, source: mutant, 'manifest.json': canonical_json(changed_manifest)}
    changed_release = canonical_json({**json.loads(release), 'manifest_sha256': sha256_hex(mutated['manifest.json'])})
    with pytest.raises(ValueError):
        waiter.verify_release(expected, held, changed_release, mutated)
    proof = json.loads((output / 'preparation.json').read_bytes())
    assert not any(proof[key] for key in ('pre_import_verified', 'asset_publication_verified', 'engine_started'))
    assert proof['files_sha256'][waiter.RELEASE] == sha256_hex(release)
    assert proof['files_sha256'][bootstrap_path.as_posix()] == sha256_hex(assets[waiter.BOOTSTRAP_NAME])


def test_sup05_first_import_checks_installed_metadata_and_import_directory(tmp_path, monkeypatch):
    import os

    from tools import gpu_a_sup05_first_import_runtime as waiter

    root = tmp_path / 'assets'
    root.mkdir(mode=0o700)
    data = root / 'data'
    data.mkdir(mode=0o700)
    package = tmp_path / 'site'
    package.mkdir()
    metadata_path = package / 'vllm-0.31.0.dist-info'
    metadata_path.mkdir()
    meta = metadata_path / 'METADATA'
    approved = b'Metadata-Version: 2.1\nName: vllm\nVersion: 0.31.0\n\n'
    meta.write_bytes(approved)
    expected = {'install_root': str(package), 'version': '0.31.0', 'assets': ['sitecustomize.py']}
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    assert waiter._installed_version(expected, flags) == '0.31.0'
    meta.write_bytes(approved.replace(b'0.31.0', b'0.30.0'))
    with pytest.raises(ValueError):
        waiter._installed_version(expected, flags)
    meta.write_bytes(approved)
    (package / 'vllm-0.30.0.dist-info').mkdir()
    with pytest.raises(ValueError):
        waiter._installed_version(expected, flags)

    monkeypatch.setattr(waiter, 'ROOT', str(root))
    monkeypatch.setattr(waiter, 'DATA', str(data))
    monkeypatch.setattr(waiter, 'TRUSTED_ASSET_UID', os.geteuid())
    imports = root / waiter.IMPORT_DIRECTORY
    imports.mkdir(mode=0o755)
    monkeypatch.setattr(waiter, 'IMPORT_ROOT', str(imports))
    launch_env = waiter._engine_env({'PYTHONPATH': str(root), 'EXAMPLE': 'value'})
    assert launch_env == {'PYTHONPATH': str(imports), 'GUARD_LIFECYCLE_CONFIG': str(root / 'settings.json'),
                          'EXAMPLE': 'value'}
    assert data.parent == imports.parent and not data.is_relative_to(imports)
    (data / 'observer-metadata.json').write_bytes(b'{}')

    for name in (waiter.BOOTSTRAP_NAME, 'manifest.json', waiter.RELEASE):
        path = root / name
        path.write_bytes(b'EXAMPLE')
        path.chmod(0o644)
    imported = imports / waiter.BOOTSTRAP_NAME
    imported.write_bytes((root / waiter.BOOTSTRAP_NAME).read_bytes())
    imported.chmod(0o644)
    owner, directory, import_directory = os.open(root, flags), os.open(data, flags), os.open(imports, flags)

    def inspect_files():
        return waiter._inventory(owner, directory, import_directory, expected)

    try:
        inventory = inspect_files()
        assert inspect_files() == inventory
        assert waiter._release_assets(owner, import_directory, expected) == {
            'manifest.json': b'EXAMPLE', waiter.BOOTSTRAP_NAME: b'EXAMPLE'}
        for folder in (root, imports):
            extra = folder / 'unexpected.py'
            extra.write_bytes(b'EXAMPLE')
            with pytest.raises(ValueError):
                inspect_files()
            extra.unlink()
        for item, restore_mode in ((root / waiter.BOOTSTRAP_NAME, 0o644), (imported, 0o644), (imports, 0o755)):
            item.chmod(restore_mode | 0o022)
            with pytest.raises(ValueError):
                inspect_files()
            item.chmod(restore_mode)
        imported.write_bytes(b'Different approved-copy fixture bytes')
        assert inspect_files() != inventory
        with pytest.raises(ValueError):
            waiter._release_assets(owner, import_directory, expected)
    finally:
        os.close(import_directory)
        os.close(directory)
        os.close(owner)
