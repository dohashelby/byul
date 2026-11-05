"""개발 담당 k3d CPU lab 입력 도구: 렌더 형식과 실측 실패에서 얻은 조건을 고정한다. 클러스터는 부르지 않는다."""
import yaml

import pytest

from guard.adapters.vllm import CHAT_TEMPLATE_CONTAINER_PATH
from tools import dev1_k3d_cpu_lab as lab

IMAGE = 'k3d-byul-reg:5111/vllm/vllm-openai-cpu@sha256:' + '0' * 64


def test_render_binds_run_labels_and_the_conditions_found_in_real_runs():
    raw, record = lab.render(run_id='dev1-k3d-9', engine_image=IMAGE)
    engine = yaml.safe_load(raw['engine.yaml']); probe = yaml.safe_load(raw['probe.yaml'])
    for pod, app in ((engine, 'serving-engine'), (probe, 'engine-test-probe')):
        assert pod['metadata']['labels'] == {'app': app, 'guard.run': 'dev1-k3d-9', 'byul.a/generation': 'cpu-k3d'}
    container, = engine['spec']['containers']
    mounts = {m['mountPath'] for m in container['volumeMounts']}
    assert '/dev/shm' in mounts and CHAT_TEMPLATE_CONTAINER_PATH in mounts  # run 1·2 실패 원인
    assert container['args'].count('--enable-log-requests') == 1 and container['args'][-2:] == ['--chat-template', CHAT_TEMPLATE_CONTAINER_PATH]
    assert {e['name']: e['value'] for e in container['env']}['VLLM_LOGGING_LEVEL'] == 'INFO'
    assert yaml.safe_load(raw['namespace.yaml'])['metadata']['labels'] == {'byul.lab/owned': 'true'}
    assert record['engine_image'] == IMAGE and record['lab_namespace_owner_label_required'] is True


@pytest.mark.parametrize('kwargs', [{'run_id': 'Bad_ID', 'engine_image': IMAGE}, {'run_id': 'ok', 'engine_image': 'vllm:latest'}])
def test_render_refuses_unpinned_image_or_bad_run_id(kwargs):
    with pytest.raises(ValueError, match='K3D_RENDER_INPUT_INVALID'):
        lab.render(**kwargs)


def test_inventory_ignores_the_download_cache_and_env_is_cpu(tmp_path):
    (tmp_path / '.cache').mkdir(); (tmp_path / '.cache' / 'x').write_text('noise')
    (tmp_path / 'config.json').write_text('{}')
    digest, files = lab.inventory(tmp_path)
    assert [f['path'] for f in files] == ['config.json']
    _, record = lab.render(run_id='dev1-k3d-9', engine_image=IMAGE)
    env = lab.environment(record, digest)
    assert env.runtime.gpu_count == 0 and env.model.inventory_sha256 == digest and env.hash_ok()


def test_gateway_lab_keeps_raw_tokens_out_and_isolates_the_engine_port():
    import yaml
    from tools import dev1_k3d_cpu_lab as lab
    tokens = {name: f'EXAMPLE-raw-token-{name}-0123456789' for name in lab.PRINCIPALS}
    image = 'k3d-byul-reg:5111/serving-gateway@sha256:' + 'a' * 64
    out = lab.gateway_lab(run_id='dev1-k3d-9', gateway_image=image, tokens=tokens)
    blob = b''.join(out.values()).decode()
    assert all(raw not in blob for raw in tokens.values())
    pod = yaml.safe_load(out['gateway.yaml'])
    names = [c['name'] for c in pod['spec']['containers']]
    assert len(names) == 2 and pod['spec']['containers'][0]['image'] == image
    policy = yaml.safe_load(out['engine-ingress.yaml'])
    rule, = policy['spec']['ingress']
    assert [p['port'] for p in rule['ports']] == [8000] and len(rule['from']) == 2
    for bad in ({'gateway_image': 'serving-gateway:latest', 'tokens': tokens},
                {'gateway_image': image, 'tokens': {'general': 'x'}}):
        with pytest.raises(ValueError, match='K3D_GATEWAY_INPUT_INVALID'):
            lab.gateway_lab(run_id='dev1-k3d-9', **bad)


def test_record_access_grants_logs_of_the_engine_pod_only_and_leaves_the_copy_inventory_to_the_enterprise(tmp_path):
    import yaml
    from guard.models import canonical_json, sha256_hex
    from tools import dev1_k3d_cpu_lab as lab
    from tests.builders import environment
    env = environment()
    engine = {'namespace': 'byul-cpu-lab', 'pod': 'serving-engine-x'}
    base = {'environment_ref': env.ref().model_dump(mode='json'),
            'kubernetes': {'run_id': 'dev1-k3d-9', 'context': 'k3d-byul-cpu', 'engine': engine}}
    selected, rbac, subjects, policy, config = lab.record_access(base, env, session='a' * 32, root=tmp_path)
    assert selected['schema'] == 'guard-kubernetes-lifecycle-target/1' and set(selected['kubernetes']) == {'context', 'engine'}
    role, binding = yaml.safe_load_all(rbac)
    assert all(rule['resourceNames'] == ['serving-engine-x'] and rule['verbs'] == ['get'] for rule in role['rules'])
    assert [s['name'] for s in binding['subjects']] == [lab.READER_USER]
    values = policy['values']['OBS-06']
    assert values['record_subject_plan_sha256'] == sha256_hex(canonical_json(subjects))
    assert values['record_target_sha256'] == sha256_hex(canonical_json(selected)) and policy['confirmed_by'] is None
    assert {s['username'] for s in subjects['subjects']} == {lab.READER_USER, lab.NO_READER_USER}
    with pytest.raises(ValueError, match='K3D_RECORD_SESSION_INVALID'):
        lab.record_access(base, env, session='x', root=tmp_path)


def test_lifecycle_render_follows_the_runbook_launch_and_keeps_assets_read_only(tmp_path):
    import json
    import yaml
    from tools import dev1_k3d_cpu_lab as lab
    session = 'c' * 32
    for name in lab.LIFECYCLE_FILES:
        (tmp_path / name).write_text('x', encoding='utf-8')
    (tmp_path / 'settings.json').write_text(json.dumps({'session': session}), encoding='utf-8')
    (tmp_path / 'manifest.json').write_text(json.dumps({'session': session}), encoding='utf-8')
    image = 'k3d-byul-reg:5111/vllm/vllm-openai-cpu@sha256:' + 'a' * 64
    raw, record = lab.render(run_id='dev1-k3d-9', engine_image=image, lifecycle_dir=tmp_path)
    pod = yaml.safe_load(raw['engine.yaml']); engine = pod['spec']['containers'][0]
    assert engine['args'][-2:] == ['--middleware', 'guard_lifecycle_sensor.Middleware'] and record['lifecycle_session'] == session
    env = {e['name']: e['value'] for e in engine['env']}
    assert env['PYTHONPATH'] == '/guard-lifecycle' and env['GUARD_LIFECYCLE_CONFIG'] == '/guard-lifecycle/settings.json'
    mounts = {m['mountPath']: m for m in engine['volumeMounts']}
    assert mounts['/guard-lifecycle']['readOnly'] is True and '/guard-lifecycle/data' in mounts
    init, = pod['spec']['initContainers']
    assert init['command'][-1] == f'mkdir -m 0700 /guard-lifecycle/data/{session}'
    assert set(yaml.safe_load(raw['lifecycle.yaml'])['data']) == set(lab.LIFECYCLE_FILES)
    plain, _ = lab.render(run_id='dev1-k3d-9', engine_image=image)
    assert 'lifecycle.yaml' not in plain and '--middleware' not in yaml.safe_load(plain['engine.yaml'])['spec']['containers'][0]['args']
    (tmp_path / 'manifest.json').write_text(json.dumps({'session': 'd' * 32}), encoding='utf-8')
    with pytest.raises(ValueError, match='K3D_LIFECYCLE_ASSETS_INVALID'):
        lab.render(run_id='dev1-k3d-9', engine_image=image, lifecycle_dir=tmp_path)


def test_api_key_is_rendered_only_as_a_secret_reference():
    import yaml
    from tools import dev1_k3d_cpu_lab as lab
    image = 'k3d-byul-reg:5111/vllm/vllm-openai-cpu@sha256:' + 'a' * 64
    raw, _ = lab.render(run_id='dev1-k3d-9', engine_image=image, api_key_secret='engine-api-key-x')
    env = yaml.safe_load(raw['engine.yaml'])['spec']['containers'][0]['env']
    key, = (e for e in env if e['name'] == 'VLLM_API_KEY')
    assert key == {'name': 'VLLM_API_KEY', 'valueFrom': {'secretKeyRef': {'name': 'engine-api-key-x', 'key': 'api-key'}}}
    with pytest.raises(ValueError, match='K3D_RENDER_INPUT_INVALID'):
        lab.render(run_id='dev1-k3d-9', engine_image=image, api_key_secret='Bad Name')


def test_gateway_chat_temperature_uses_policy_schema_1_2_and_allows_only_zero():
    import json
    import yaml
    from tools import dev1_k3d_cpu_lab as lab
    tokens = {name: f'EXAMPLE-raw-token-{name}-0123456789' for name in lab.PRINCIPALS}
    image = 'k3d-byul-reg:5111/serving-gateway@sha256:' + 'a' * 64
    config = yaml.safe_load(lab.gateway_lab(run_id='dev1-k3d-9', gateway_image=image, tokens=tokens, chat_temperature=True)['gateway-config.yaml'])
    policy, bundle = json.loads(config['data']['runtime-policy.json']), json.loads(config['data']['bundle.json'])
    assert policy['schema_version'] == 'runtime-mvp/1.2'
    assert policy['extra_fields'] == {'temperature': {'mode': 'validate', 'type': 'number', 'min': 0, 'max': 0}}
    entry, = (f for f in bundle['files'] if f['path'] == 'runtime-policy.json')
    assert entry['schema_version'] == 'runtime-mvp/1.2'
    plain = yaml.safe_load(lab.gateway_lab(run_id='dev1-k3d-9', gateway_image=image, tokens=tokens)['gateway-config.yaml'])
    assert json.loads(plain['data']['runtime-policy.json'])['schema_version'] == 'runtime-mvp/1.1'


def test_gpu_profile_adds_one_gpu_and_keeps_names_apart_from_the_cpu_diagnosis(monkeypatch):
    import yaml
    from tools import dev1_k3d_cpu_lab as lab
    for name in ('NAMESPACE', 'GENERATION', 'SERVED', 'SCHEMA'):
        monkeypatch.setattr(lab, name, getattr(lab, name))
    monkeypatch.setattr(lab, 'ARGS', list(lab.ARGS))
    lab.use_gpu_profile()
    image = 'registry.example/vllm/vllm-openai@sha256:' + 'a' * 64
    raw, record = lab.render(run_id='dev1-gpu-1', engine_image=image, gpu={'model': 'NVIDIA L4', 'runtime_class': 'nvidia'})
    pod = yaml.safe_load(raw['engine.yaml']); engine = pod['spec']['containers'][0]
    assert pod['metadata']['namespace'] == 'byul-dev1-gpu-lab' and pod['spec']['runtimeClassName'] == 'nvidia'
    assert engine['command'] == ['vllm', 'serve']
    assert engine['resources']['limits']['nvidia.com/gpu'] == '1' and engine['args'][-2:] == ['--gpu-memory-utilization', '0.5']
    assert 'VLLM_CPU_KVCACHE_SPACE' not in {e['name'] for e in engine['env']}
    assert record['profile'] == 'gpu' and record['served_model_name'] == 'byul-dev1-lab'
    assert engine['args'][engine['args'].index('--served-model-name') + 1] == 'byul-dev1-lab'
    record = {**record, 'model_id': 'facebook/opt-125m', 'model_revision': 'r', 'outputs_sha256': record['outputs_sha256']}
    env = lab.environment(record, 'b' * 64)
    assert env.data_origin.value == 'DECLARED' and env.runtime.gpu_count == 1 and env.runtime.gpu_model == 'NVIDIA L4'
    with pytest.raises(ValueError, match='K3D_RENDER_INPUT_INVALID'):
        lab.render(run_id='dev1-gpu-1', engine_image=image, gpu={'model': ''})


def test_gpu_lifecycle_keeps_the_memory_budget_and_records_the_actual_eager_option(tmp_path):
    import json
    session = 'c' * 32
    for name in lab.LIFECYCLE_FILES:
        (tmp_path / name).write_text('x', encoding='utf-8')
    for name in ('settings.json', 'manifest.json'):
        (tmp_path / name).write_text(json.dumps({'session': session}), encoding='utf-8')
    raw, record = lab.render(run_id='dev1-gpu-2', engine_image=IMAGE, lifecycle_dir=tmp_path, gpu={'model': 'NVIDIA L4'})
    args = yaml.safe_load(raw['engine.yaml'])['spec']['containers'][0]['args']
    assert args[args.index('--gpu-memory-utilization') + 1] == '0.5'
    assert '--enforce-eager' in args and record['enforce_eager'] is True
    assert args[-2:] == ['--middleware', 'guard_lifecycle_sensor.Middleware']
    assert record['args'] == args


def test_graph_declaration_cannot_claim_eager_when_the_launch_option_is_absent(monkeypatch):
    monkeypatch.setattr(lab, 'ARGS', [arg for arg in lab.ARGS if arg != '--enforce-eager'])
    raw, record = lab.render(run_id='dev1-gpu-3', engine_image=IMAGE, gpu={'model': 'NVIDIA L4'})
    assert '--enforce-eager' not in yaml.safe_load(raw['engine.yaml'])['spec']['containers'][0]['args']
    assert record['enforce_eager'] is False
def test_gpu_target_declares_the_actual_opt_subdirectory_and_managed_template(tmp_path):
    import json
    from tests.test_gpu_a_run_inputs import deployed
    from guard.adapters.vllm import VllmAdapter
    pods, _ = deployed(tmp_path, 'v1-callback')
    image = pods['engine']['spec']['containers'][0]['image']
    raw, record = lab.render(run_id='a-run-1', engine_image=image, gpu={'model': 'EXAMPLE-L4'})
    rendered = yaml.safe_load(raw['engine.yaml'])
    pods['engine']['spec']['containers'][0]['args'] = rendered['spec']['containers'][0]['args']
    for pod in pods.values():
        pod['metadata']['labels']['byul.a/generation'] = record['generation']
    env = lab.environment(record, 'b' * 64)
    value = lab.target(env, record, json.loads((tmp_path / 'namespace.json').read_text()),
                       pods['engine'], pods['probe'], context='EXAMPLE-context')
    launch = value['comparison_config']['launch']
    assert launch['profile'] == 'gpu-bounded-v1' and launch['max_model_len'] == 512
    assert VllmAdapter().supports_template_profile(launch['chat_template'])
    assert launch['log_profile'] is None  # EXAMPLE digest는 새로 지원하지 않는다.
    assert value['model_mapping']['host_snapshot'] == '/srv/models/opt-125m'
    assert value['model_mapping']['container_mount'] == '/models/opt-125m'
    assert value['environment_ref'] == env.ref().model_dump(mode='json') and env.hash_ok()
