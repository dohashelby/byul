"""담당 A GPU 1차의 실행 입력을 조립한다. AWS lab의 실제 Pod 값으로 env·target·점검별 입력을 맞춘다.

env:    렌더 기록(gpu_a_aws_lab)과 서버 사실로 봉인된 ENV 객체를 만든다.
target: kubectl get으로 받은 네임스페이스·엔진·시험 Pod JSON으로 요청 바인딩 target을 만들고 전송 검사로 확인한다.
check:  콜백 점검(REQ-14·16·17·18) 하나의 사례 manifest, 해시를 채운 정책, 실행 설정, 실행 명령을 만든다.

kubectl·엔진을 실행하지 않는다. 출력 해시가 실행기의 바인딩 검사와 같은 방식으로 맞는지 쓰기 전에 확인한다.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

import yaml

from guard.adapters.vllm import VllmAdapter
from guard.collectors import engine_callbacks
from guard.collectors.file import load_environment
from guard.executors import engine_callback_projection as projection
from guard.models import DataOrigin, Environment, canonical_json, new_id, sha256_hex
from guard.runtime.kubernetes_binding import validate_container_binding
from guard.runtime.kubernetes_http import ENGINE_CALLBACK_PROFILE, KubernetesHTTP

SCHEMA = 'byul-gpu-a-run-inputs/1'
TARGET_SCHEMA = 'guard-kubernetes-request-binding-target/1'
MODEL_ID = 'Qwen/Qwen2.5-0.5B-Instruct'
EXAMPLE_POLICY_CHECKS = frozenset({'REQ-08', 'REQ-09', 'REQ-10', 'SUP-03', 'SUP-08'})  # 2026-11-03 사용자 결정
LAB = {'lora': {'name': 'byul-lora', 'active_path': '/guard-lora/active',
        'variants': {'A': '/guard-lora/variants/a', 'B': '/guard-lora/variants/b'}},
    'weights': {'variants': {'gen2': '/guard-weights/gen2'}}}
TEXT = ' '.join(f'word{i}' for i in range(60))


def _context(context, prefix, prompt, roles, **extra):
    """prime·control은 원래 문맥, probe는 바꾼 문맥(extra 'probe'), post는 probe 뒤 같은 문맥."""
    probe = extra.pop('probe', {})
    return [{'case_id': f'{prefix}-{role}', 'role': role, 'context': context, 'prefix': prefix, 'prompt': prompt,
        **(probe if role == 'probe' else extra)} for role in roles]


def cases(preset):
    if preset == 'req14-normal':
        return [{'case_id': f'n{i}', 'role': 'normal', 'prompt': f'normal {i} {TEXT}'} for i in range(4)]
    if preset == 'req18-slot':
        return [{'case_id': 's1', 'role': 'standalone', 'prompt': 'tell me', 'max_tokens': 4},
            {'case_id': 's2', 'role': 'standalone', 'prompt': 'tell me', 'max_tokens': 4},
            {'case_id': 'r', 'role': 'restricted', 'prompt': 'tell me', 'max_tokens': 4, 'allowed_token_ids': [5]},
            {'case_id': 'b', 'role': 'reused', 'prompt': 'tell me', 'max_tokens': 4}]
    salt = _context('cache_salt', 'c', 'salt ' + TEXT, ('prime', 'control', 'probe', 'post'),
        cache_salt='salt-one', probe={'cache_salt': 'salt-two'})
    if preset == 'req16-salt':
        return salt
    if preset == 'req16-lab':
        adapter = _context('adapter_content', 'a', 'adapter ' + TEXT, ('prime', 'control'), adapter=LAB['lora']['name'])
        swapped = _context('adapter_content', 'a', 'adapter ' + TEXT, ('probe', 'post'), adapter=LAB['lora']['name'],
            probe={'adapter': LAB['lora']['name']})
        before = _context('model_generation', 'g', 'generation ' + TEXT, ('prime', 'control'))
        after = _context('model_generation', 'g', 'generation ' + TEXT, ('probe', 'post'))
        # 가중치 교체는 이후 모든 사례의 기준 모델을 바꾸므로 맨 뒤에 둔다.
        return [*salt, {'case_id': 'load-a', 'role': 'switch', 'action': 'lora_reload', 'variant': 'A'}, *adapter,
            {'case_id': 'swap-b', 'role': 'switch', 'action': 'lora_reload', 'variant': 'B'}, *swapped, *before,
            {'case_id': 'gen2', 'role': 'switch', 'action': 'weights_reload', 'variant': 'gen2'}, *after]
    raise ValueError('RUN_INPUTS_CASE_PRESET_UNKNOWN')


def environment(render, *, model_id=MODEL_ID, inventory=None, gpu_model, driver, data_origin='DECLARED'):
    generation = render['generation']
    # facts는 등록된 키만 받는다. 세대 구분은 서술 필드에 남기고 결과를 세대끼리 섞지 않는다.
    platform = ('GPU, amd64, AWS g6 kubeadm serving-lab; ' + '; '.join(f'{k}={v}' for k, v in (
        ('generation', generation), ('run', render['run_id']), ('profile', render['profile']),
        ('runner', 'v2' if generation == 'v2-default' else 'v1'), ('async_requested', generation != 'v1-callback-sync'),
        ('eager', render['enforce_eager']), ('render_engine_sha256', render['outputs_sha256']['engine.yaml']))))
    origin = DataOrigin(data_origin)
    env = Environment(id=new_id('ENV'), data_origin=origin,
        engine={'name': 'vllm', 'version': '0.31.0', 'image_digest': render['engine_image']},
        adapter={'name': 'vllm', 'version': VllmAdapter().version},
        runtime={'nodes': 1, 'tp_groups': 1, 'gpu_count': 1, 'gpu_model': gpu_model, 'driver': driver, 'platform': platform},
        model={'id': model_id, 'revision': render['model_revision'], 'inventory_sha256': inventory},
        facts={'prefix_cache': {'value': generation != 'v2-default', 'origin': origin, 'source': 'render.json#generation'}}).seal()
    return env


def _binding(pod, namespace, container, role_label=None):
    meta, spec, status = pod['metadata'], pod['spec'], pod['status']
    state, = (s for s in status['containerStatuses'] if s['name'] == container)
    image, = (c['image'] for c in spec['containers'] if c['name'] == container)
    if role_label is not None and meta['labels'].get('app') != role_label:
        raise ValueError('RUN_INPUTS_POD_ROLE_LABEL_MISMATCH')
    item = {'namespace': meta['namespace'], 'namespace_uid': namespace['metadata']['uid'], 'pod': meta['name'],
        'pod_uid': meta['uid'], 'container': container, 'container_id': state['containerID'], 'image': image,
        'image_id': state['imageID'], 'restart_count': state['restartCount'], 'node': spec['nodeName'],
        'pod_ip': status['podIP'], 'started_at': state['state']['running']['startedAt']}
    validate_container_binding(item)
    return item


def target(env, render, namespace, engine_pod, probe_pod, *, context, api_model='test-model'):
    run_id = render['run_id']
    for pod in (engine_pod, probe_pod):
        labels = pod['metadata']['labels']
        if labels.get('guard.run') != run_id or labels.get('byul.a/generation') != render['generation']:
            raise ValueError('RUN_INPUTS_POD_GENERATION_MISMATCH')
    engine = _binding(engine_pod, namespace, 'engine', 'serving-engine')
    probe = _binding(probe_pod, namespace, 'probe', 'engine-test-probe')
    if engine['image'] != render['engine_image'] or engine['image'] != env.engine.image_digest:
        raise ValueError('RUN_INPUTS_ENGINE_IMAGE_MISMATCH')
    value = {'schema': TARGET_SCHEMA, 'lab': bool(render['lab_namespace_owner_label_required']),
        'topology': 'kubernetes-owned-direct', 'engine_kind': 'vllm', 'origins': {'trusted': {'network': 'kubernetes-probe'}},
        'engine': {'trusted_url': f"http://{engine['pod_ip']}:8000", 'image_digest': env.engine.image_digest},
        'kubernetes': {'context': context, 'run_id': run_id, 'engine': engine, 'probe': probe},
        'environment_ref': env.ref().model_dump(mode='json'),
        'model_mapping': {'repository_id': env.model.id, 'revision': env.model.revision, 'api_model': api_model}}
    # 전송 계층 생성자는 kubectl을 부르지 않고 target 형식과 신원 결합만 검사한다.
    KubernetesHTTP(value, env, duration=1, runner=_no_kubectl, profile=ENGINE_CALLBACK_PROFILE)
    return value


def _no_kubectl(*args, **kwargs):
    raise AssertionError('RUN_INPUTS_NO_KUBECTL')


def check_inputs(check_id, env, target_value, prepared_dir, case_list, draft, *, root):
    if check_id not in projection.ROLES or check_id == 'REQ-15':
        raise ValueError('RUN_INPUTS_CHECK_UNSUPPORTED')
    prepared = engine_callbacks.load_prepared(prepared_dir)
    projection.validate_cases(check_id, case_list)
    lab = LAB if any(c['role'] == 'switch' or 'adapter' in c for c in case_list) else None
    if lab is not None:
        if target_value['lab'] is not True:
            raise ValueError('RUN_INPUTS_LAB_CASES_NEED_LAB_TARGET')
        projection.validate_lab_contexts(lab, case_list)
    target_sha = sha256_hex(canonical_json(target_value))
    manifest = {'schema': projection.SCHEMA, 'session': prepared['session'], 'target_sha256': target_sha,
        'environment_ref': env.ref().model_dump(mode='json'), 'cases': deepcopy(case_list), **({'lab_contexts': deepcopy(lab)} if lab else {})}
    values = {key: value for key, value in (draft['values'].get(check_id) or {}).items() if not key.startswith('sensor_')}
    values.update(sensor_environment_sha256=env.ref().sha256, sensor_target_sha256=target_sha,
        sensor_prepared_sha256=sha256_hex(canonical_json(prepared)), sensor_manifest_sha256=sha256_hex(canonical_json(manifest)))
    policy = {'data_origin': 'EXAMPLE' if check_id in EXAMPLE_POLICY_CHECKS else 'DECLARED', 'status': 'DRAFT',
        'pack': deepcopy(draft['pack']), 'values': {check_id: values}, 'scope': {'include': [check_id], 'exclude': []},
        'exceptions': [], 'confirmed_by': None, 'confirmed_at': None}
    root = Path(root).absolute()
    config = '\n'.join([f"store = '{root / 'store'}'", '', '[adapter]', "name = 'vllm'", '', '[executors]', "engine_sensor = 'kubernetes'",
        '', '[engine_sensor]', f"target = '{root / 'target.json'}'", f"prepared = '{Path(prepared_dir).absolute() / 'manifest.json'}'",
        f"manifest = '{root / 'manifest.json'}'", 'max_duration_seconds = 300', ''])
    return manifest, policy, config


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if type(data) is str else json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='kind', required=True)
    e = sub.add_parser('env'); e.add_argument('--render', type=Path, required=True); e.add_argument('--gpu-model', required=True)
    e.add_argument('--driver', required=True); e.add_argument('--model-inventory'); e.add_argument('--out', type=Path, required=True)
    t = sub.add_parser('target'); t.add_argument('--env', type=Path, required=True); t.add_argument('--render', type=Path, required=True)
    for name in ('namespace', 'engine-pod', 'probe-pod'):
        t.add_argument('--' + name, type=Path, required=True)
    t.add_argument('--context', default='kubernetes-admin@kubernetes'); t.add_argument('--api-model', default='test-model')
    t.add_argument('--out', type=Path, required=True)
    c = sub.add_parser('check'); c.add_argument('--check', required=True); c.add_argument('--env', type=Path, required=True)
    c.add_argument('--target', type=Path, required=True); c.add_argument('--prepared', type=Path, required=True)
    c.add_argument('--cases', required=True, help='req14-normal, req16-salt, req16-lab, req18-slot 또는 REQ-17 사례 생성기 출력 파일')
    c.add_argument('--policy-draft', type=Path, default=Path('docs/gpu-a/policy-a-draft.yaml'))
    c.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    load = lambda path: json.loads(Path(path).read_text(encoding='utf-8'))
    if args.kind == 'env':
        env = environment(load(args.render), gpu_model=args.gpu_model, driver=args.driver, inventory=args.model_inventory)
        _write(args.out, env.model_dump(mode='json'))
        if load_environment(args.out).ref() != env.ref():
            raise ValueError('RUN_INPUTS_ENV_ROUNDTRIP_MISMATCH')
        print(json.dumps({'env': str(args.out), 'sha256': env.ref().sha256}))
    elif args.kind == 'target':
        value = target(load_environment(args.env), load(args.render), load(args.namespace), load(args.engine_pod),
            load(args.probe_pod), context=args.context, api_model=args.api_model)
        _write(args.out, value)
        print(json.dumps({'target': str(args.out), 'sha256': sha256_hex(canonical_json(value))}))
    else:
        case_list = load(args.cases)['cases'] if Path(args.cases).is_file() else cases(args.cases)
        manifest, policy, config = check_inputs(args.check, load_environment(args.env), load(args.target), args.prepared, case_list,
            yaml.safe_load(args.policy_draft.read_text(encoding='utf-8')), root=args.out)
        _write(args.out / 'target.json', load(args.target)); _write(args.out / 'manifest.json', manifest)
        _write(args.out / 'policy.yaml', yaml.safe_dump(policy, sort_keys=False, allow_unicode=True)); _write(args.out / 'guard.toml', config)
        commands = [f'uv run guard select --pack packs/PACK-BASELINE --env {args.env.absolute()} --policy {args.out.absolute() / "policy.yaml"} --config {args.out.absolute() / "guard.toml"}',
            f'uv run guard run --plan <PLAN> --config {args.out.absolute() / "guard.toml"}',
            f'uv run guard judge --plan <PLAN> --config {args.out.absolute() / "guard.toml"}',
            f'uv run guard report --plan <PLAN> --config {args.out.absolute() / "guard.toml"} --json {args.out.absolute() / "report.json"}']
        _write(args.out / 'commands.txt', '\n'.join(commands) + '\n')
        print(json.dumps({'schema': SCHEMA, 'check': args.check, 'cases': len(case_list), 'lab': 'lab_contexts' in manifest,
            'manifest_sha256': policy['values'][args.check]['sensor_manifest_sha256']}))


if __name__ == '__main__':
    main()
