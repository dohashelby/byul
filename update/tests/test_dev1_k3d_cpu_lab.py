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
