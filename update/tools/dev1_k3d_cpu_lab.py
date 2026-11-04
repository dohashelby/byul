"""개발 담당 k3d 로컬 CPU lab의 배포·실행 입력을 만든다. 클러스터·엔진을 직접 바꾸지 않는다.

render: 소유 네임스페이스·CPU vLLM 엔진 Pod·시험 Pod 매니페스트와 렌더 기록.
env:    렌더 기록과 실제 모델 파일 inventory로 봉인한 ENV(CPU, GPU 0).
target: kubectl get으로 받은 네임스페이스·두 Pod JSON으로 요청 바인딩 target(기존 GPU 입력 도구의 target 재사용).
http-check: REQ-13·OBS-09의 EXAMPLE 정책·실행 설정·명령.

결과는 "k3d 로컬 CPU" 환경이며 AWS K3s·GPU 결과와 합치지 않는다(DECISIONS 2026-11-04).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import yaml

from guard.adapters.vllm import CHAT_TEMPLATE_CONTAINER_PATH, VllmAdapter
from guard.collectors.file import load_environment
from guard.models import DataOrigin, Environment, canonical_json, new_id, sha256_hex
from tools import gpu_a_run_inputs as inputs

SCHEMA = 'byul-dev1-k3d-cpu-render/1'
GENERATION = 'cpu-k3d'
NAMESPACE = 'byul-cpu-lab'
MODEL_ID = 'facebook/opt-125m'
MODEL_REVISION = '27dcfa74d334bc871f3234de431e71c6eeba5dd6'
SERVED = 'byul-cpu-lab'
# 11-02 CPU 실측과 같은 작은 모델 설정. OBS-09가 요구하는 요청 로그 설정을 함께 둔다.
ARGS = ['/models/opt-125m', '--served-model-name', SERVED, '--max-model-len', '512', '--max-num-seqs', '2',
        '--dtype', 'float32', '--block-size', '32', '--enable-log-requests', '--max-log-len', '512',
        # opt-125m에는 chat template이 없다. SUP-03에서 검토한 guard 고정 템플릿을 그대로 붙인다(run 2에서 chat 400 확인).
        '--chat-template', CHAT_TEMPLATE_CONTAINER_PATH]
LOG_ENV = {'VLLM_CONFIGURE_LOGGING': '1', 'VLLM_LOGGING_LEVEL': 'INFO', 'VLLM_LOGGING_COLOR': '0',
           'VLLM_LOGGING_PREFIX': '', 'VLLM_LOGGING_STREAM': 'ext://sys.stdout'}
_IMAGE = re.compile(r'[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}')
_RUN = re.compile(r'[a-z0-9][a-z0-9-]{0,40}')
VALUES = {
    'REQ-13': {'allowed_response_fields': ['id', 'object', 'created', 'model', 'choices[].index', 'choices[].text',
                                           'choices[].message.role', 'choices[].message.content', 'choices[].finish_reason',
                                           'usage.prompt_tokens', 'usage.completion_tokens', 'usage.total_tokens'],
               'allowed_error_fields': ['error.message', 'error.type', 'error.param', 'error.code'],
               'forbidden_return_options': ['return_prompt_text', 'return_token_ids', 'prompt_logprobs'],
               'required_targets': ['engine']},
    'OBS-09': {'collector_profile': 'guard-vllm-native-info-v1', 'required_trusted_fields': ['process', 'level', 'source'],
               'client_request_id_trust': 'untrusted_correlation'},
}
EXECUTORS = {'REQ-13': ('response_content', {'endpoint': '/v1/chat/completions', 'request_timeout': 30}),
             'OBS-09': ('log_integrity', {'request_timeout': 30, 'max_duration_seconds': 300})}


def render(*, run_id, engine_image):
    if not _RUN.fullmatch(run_id or '') or not _IMAGE.fullmatch(engine_image or ''):
        raise ValueError('K3D_RENDER_INPUT_INVALID')
    labels = {'guard.run': run_id, 'byul.a/generation': GENERATION}
    namespace = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE, 'labels': {'byul.lab/owned': 'true'}}}
    engine = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': f'serving-engine-{run_id}'[:63], 'namespace': NAMESPACE,
              'labels': {'app': 'serving-engine', **labels}},
              'spec': {'enableServiceLinks': False, 'automountServiceAccountToken': False, 'restartPolicy': 'Never',
                       'containers': [{'name': 'engine', 'image': engine_image, 'imagePullPolicy': 'IfNotPresent', 'args': ARGS,
                                       'env': [{'name': k, 'value': v} for k, v in {**LOG_ENV, 'VLLM_CPU_KVCACHE_SPACE': '1'}.items()],
                                       'ports': [{'containerPort': 8000}],
                                       'readinessProbe': {'httpGet': {'path': '/health', 'port': 8000}, 'periodSeconds': 5},
                                       'resources': {'requests': {'cpu': '2', 'memory': '2Gi'}, 'limits': {'cpu': '4', 'memory': '4Gi'}},
                                       'volumeMounts': [{'name': 'models', 'mountPath': '/models', 'readOnly': True},
                                                        {'name': 'shm', 'mountPath': '/dev/shm'},
                                                        {'name': 'chat-template', 'mountPath': CHAT_TEMPLATE_CONTAINER_PATH, 'readOnly': True}]}],
                       # 기본 64MiB /dev/shm으로는 CPU 엔진의 공유 메모리 할당(160MiB)이 실패했다(run 1).
                       'volumes': [{'name': 'models', 'hostPath': {'path': '/srv/models', 'type': 'Directory'}},
                                   {'name': 'shm', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '1Gi'}},
                                   {'name': 'chat-template', 'hostPath': {'path': '/srv/models/guard-chat/guard-text-chat-v1.jinja', 'type': 'File'}}]}}
    probe = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': f'engine-test-probe-{run_id}'[:63], 'namespace': NAMESPACE,
             'labels': {'app': 'engine-test-probe', **labels}},
             'spec': {'enableServiceLinks': False, 'automountServiceAccountToken': False, 'restartPolicy': 'Never',
                      'securityContext': {'runAsUser': 2000, 'runAsGroup': 0, 'runAsNonRoot': True},
                      'containers': [{'name': 'probe', 'image': engine_image, 'imagePullPolicy': 'IfNotPresent',
                                      'command': ['sleep', 'infinity'],
                                      'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                                      'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'cpu': '1', 'memory': '1Gi'}}}]}}
    docs = {'namespace.yaml': namespace, 'engine.yaml': engine, 'probe.yaml': probe}
    raw = {name: yaml.safe_dump(value, sort_keys=False).encode() for name, value in docs.items()}
    record = {'schema': SCHEMA, 'generation': GENERATION, 'profile': 'cpu', 'run_id': run_id, 'engine_image': engine_image,
              'model_id': MODEL_ID, 'model_revision': MODEL_REVISION, 'served_model_name': SERVED, 'args': ARGS,
              'enforce_eager': True, 'lab_namespace_owner_label_required': True,
              'outputs_sha256': {'engine.yaml': hashlib.sha256(raw['engine.yaml']).hexdigest(),
                                 **{n: hashlib.sha256(b).hexdigest() for n, b in raw.items() if n != 'engine.yaml'}}}
    return raw, record


def inventory(root):
    """모델 폴더의 실제 파일 이름·크기·SHA256 목록 해시. 내려받기 도구의 .cache는 뺀다."""
    files = []
    for path in sorted(Path(root).rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and not relative.startswith('.cache/'):
            files.append({'path': relative, 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    if not files:
        raise ValueError('K3D_MODEL_INVENTORY_EMPTY')
    return sha256_hex(canonical_json(files)), files


def environment(record, inventory_sha256):
    platform = ('CPU, arm64, k3d local Docker Desktop macOS; ' + '; '.join(f'{k}={v}' for k, v in (
        ('generation', record['generation']), ('run', record['run_id']), ('render_engine_sha256', record['outputs_sha256']['engine.yaml']))))
    return Environment(id=new_id('ENV'), data_origin=DataOrigin.EXAMPLE,
        engine={'name': 'vllm', 'version': '0.31.0', 'image_digest': record['engine_image']},
        adapter={'name': 'vllm', 'version': VllmAdapter().version},
        runtime={'nodes': 1, 'tp_groups': 1, 'gpu_count': 0, 'gpu_model': None, 'driver': None, 'platform': platform},
        model={'id': record['model_id'], 'revision': record['model_revision'], 'inventory_sha256': inventory_sha256}).seal()


def target(env, record, namespace, engine_pod, probe_pod, *, context):
    value = inputs.target(env, record, namespace, engine_pod, probe_pod, context=context, api_model=record['served_model_name'])
    container, = (c for c in engine_pod['spec']['containers'] if c['name'] == 'engine')
    value['engine']['command'] = ['vllm', 'serve']
    value['observed_version'] = env.engine.version
    # OBS-09: 실제 Pod 인자와 검토된 로그 계약. 검토되지 않은 이미지면 계약은 None이고 실행기가 공백으로 남긴다.
    value['comparison_config'] = {'launch': {'argv': list(container['args']),
        'log_profile': VllmAdapter().native_log_contract(env.engine.image_digest, env.engine.version)}}
    return value


def http_check(check_id, root):
    executor, settings = EXECUTORS[check_id]
    root = Path(root).absolute()
    # 팩 Ref는 기존 GPU 실측 정책 초안과 같은 PACK-BASELINE@32를 쓴다.
    pack = yaml.safe_load((Path(__file__).parents[1] / 'docs/gpu-a/policy-a-draft.yaml').read_text(encoding='utf-8'))['pack']
    policy = {'data_origin': 'EXAMPLE', 'status': 'DRAFT', 'pack': pack, 'values': {check_id: VALUES[check_id]},
              'scope': {'include': [check_id], 'exclude': []}, 'exceptions': [], 'confirmed_by': None, 'confirmed_at': None}
    lines = [f"store = '{root / 'store'}'", '', '[adapter]', "name = 'vllm'", '', '[executors]', f"{executor} = 'kubernetes'",
             '', f'[{executor}]', f"target = '{root / 'target.json'}'", "kubectl_binary = 'kubectl'"]
    lines += [f"{k} = '{v}'" if type(v) is str else f'{k} = {v}' for k, v in settings.items()]
    return policy, '\n'.join([*lines, ''])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='kind', required=True)
    r = sub.add_parser('render'); r.add_argument('--run-id', required=True); r.add_argument('--engine-image', required=True)
    r.add_argument('--out', type=Path, required=True)
    e = sub.add_parser('env'); e.add_argument('--render', type=Path, required=True); e.add_argument('--models', type=Path, required=True)
    e.add_argument('--out', type=Path, required=True)
    t = sub.add_parser('target'); t.add_argument('--env', type=Path, required=True); t.add_argument('--render', type=Path, required=True)
    for name in ('namespace', 'engine-pod', 'probe-pod'):
        t.add_argument('--' + name, type=Path, required=True)
    t.add_argument('--context', default='k3d-byul-cpu'); t.add_argument('--out', type=Path, required=True)
    h = sub.add_parser('http-check'); h.add_argument('--check', required=True, choices=sorted(EXECUTORS))
    h.add_argument('--target', type=Path, required=True); h.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    load = lambda path: json.loads(Path(path).read_text(encoding='utf-8'))
    if args.kind == 'render':
        raw, record = render(run_id=args.run_id, engine_image=args.engine_image)
        for name, data in raw.items():
            inputs._write(args.out / name, data.decode())
        inputs._write(args.out / 'render.json', record)
        print(json.dumps({'render': str(args.out / 'render.json'), 'outputs_sha256': record['outputs_sha256']}))
    elif args.kind == 'env':
        digest, files = inventory(args.models)
        env = environment(load(args.render), digest)
        inputs._write(args.out, env.model_dump(mode='json'))
        inputs._write(args.out.with_name('model-inventory.json'), {'inventory_sha256': digest, 'files': files})
        if load_environment(args.out).ref() != env.ref():
            raise ValueError('K3D_ENV_ROUNDTRIP_MISMATCH')
        print(json.dumps({'env': str(args.out), 'sha256': env.ref().sha256, 'inventory_sha256': digest}))
    elif args.kind == 'target':
        value = target(load_environment(args.env), load(args.render), load(args.namespace), load(args.engine_pod),
                       load(args.probe_pod), context=args.context)
        inputs._write(args.out, value)
        print(json.dumps({'target': str(args.out), 'sha256': sha256_hex(canonical_json(value))}))
    else:
        policy, config = http_check(args.check, args.out)
        inputs._write(args.out / 'target.json', load(args.target))
        inputs._write(args.out / 'policy.yaml', yaml.safe_dump(policy, sort_keys=False, allow_unicode=True))
        inputs._write(args.out / 'guard.toml', config)
        print(json.dumps({'check': args.check, 'executor': EXECUTORS[args.check][0], 'out': str(args.out)}))


if __name__ == '__main__':
    main()
