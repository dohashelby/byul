"""담당 A의 AWS GPU lab 배포 덧씌우기. 인계 묶음의 엔진 템플릿을 세대별로 렌더링한다.

인계 묶음(aws-handoff-20261026)은 읽기 전용이다. 템플릿 SHA가 고정값과 다르면 거부하고, 채우는 규칙은
인계 03 스크립트와 같다. 그 위에 A 실행에 필요한 것만 덧씌운다. kubectl은 실행하지 않고 YAML과 렌더
기록만 쓴다. 절차는 update/docs/gpu-a 의 AWS 절차서를 따른다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import yaml

SCHEMA = 'byul-gpu-a-aws-render/1'
HANDOFF_ZIP_SHA256 = '717fa446e62588f39caaee0e159a354c344b5d64da082e9a52bfaa6f697c5445'
TEMPLATE_SHA256 = '09cd45a4e10fae83b2eed07c8c4d4490311da4cf98646b681b0c91405c1f6eda'
TEST_ACCESS_SHA256 = '043bd977d53eed7ed457de6d544e181af3fb13da6d65e81478ec4f3cb23e78cc'
NAMESPACE = 'serving-lab'
# 인계 profile.sh와 같은 값. dev와 reference 결과를 서로 비교쌍으로 쓰지 않는다.
PROFILES = {'dev': ('2', '4', '8Gi', '16Gi'), 'reference': ('4', '8', '12Gi', '24Gi')}
CALLBACKS, DATA = '/guard-lifecycle/engine-callbacks', '/guard-lifecycle/data'
GENERATIONS = {
    # 콜백 센서는 V1 고정 소스 지점을 감싼다. REQ-14·16·17·18.
    'v1-callback': {'sensor': True, 'args': ['--enable-prefix-caching'], 'env': {'VLLM_USE_V2_MODEL_RUNNER': '0'}},
    # REQ-14 동기 분기. async 기본 세대와 결과를 섞지 않는다.
    'v1-callback-sync': {'sensor': True, 'args': ['--enable-prefix-caching', '--no-async-scheduling'],
        'env': {'VLLM_USE_V2_MODEL_RUNNER': '0'}},
    # REQ-08·09·10, SUP-03·08. 기본 V2 runner, 센서 없음.
    'v2-default': {'sensor': False, 'args': [], 'env': {}},
    # REQ-16 lab 문맥. 소유 lab 네임스페이스에서만 띄운다.
    'v1-req16-lab': {'sensor': True, 'lab': True,
        'args': ['--enable-prefix-caching', '--enable-lora', '--max-lora-rank', '8', '--max-loras', '1'],
        'env': {'VLLM_USE_V2_MODEL_RUNNER': '0', 'VLLM_ALLOW_RUNTIME_LORA_UPDATING': 'True', 'VLLM_SERVER_DEV_MODE': '1'}},
}
_RUN_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,62}')
_IMAGE = re.compile(r'[a-z0-9./_-]+@sha256:[a-f0-9]{64}')
_HOST_PATH = re.compile(r'(/[A-Za-z0-9][A-Za-z0-9._-]{0,63}){2,10}')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _fill(template, values):
    """인계 03 스크립트의 RENDER 단계와 같은 치환. 남은 자리표시자가 있으면 거부한다."""
    text = template
    for key, value in values.items():
        text = text.replace('__' + key + '__', value)
    if '__' in text:
        raise ValueError('AWS_RENDER_PLACEHOLDER_LEFT')
    return text


def render(handoff_dir, *, generation, profile, run_id, engine_image, model_revision, host_root, enforce_eager=False):
    if (generation not in GENERATIONS or profile not in PROFILES or not _RUN_ID.fullmatch(run_id or '')
            or not _IMAGE.fullmatch(engine_image or '') or not re.fullmatch(r'[0-9a-f]{40}', model_revision or '')
            or not _HOST_PATH.fullmatch(host_root or '')):
        raise ValueError('AWS_RENDER_INPUT_INVALID')
    raw = (Path(handoff_dir) / 'engine.yaml.template').read_bytes()
    if _sha(raw) != TEMPLATE_SHA256:
        raise ValueError('AWS_HANDOFF_TEMPLATE_CHANGED')
    if _sha((Path(handoff_dir) / 'engine-test-access.yaml').read_bytes()) != TEST_ACCESS_SHA256:
        raise ValueError('AWS_HANDOFF_TEST_ACCESS_CHANGED')
    cpu_request, cpu_limit, memory_request, memory_limit = PROFILES[profile]
    cache = f'/srv/serving/cache/vllm/{engine_image.rsplit("@sha256:", 1)[1]}/{model_revision}/{profile}-profile-01'
    text = _fill(raw.decode(), {'ENGINE_IMAGE': engine_image, 'MODEL_REVISION': model_revision, 'CACHE_PATH': cache,
        'LAB_PROFILE': profile, 'ENGINE_CPU_REQUEST': cpu_request, 'ENGINE_CPU_LIMIT': cpu_limit,
        'ENGINE_MEMORY_REQUEST': memory_request, 'ENGINE_MEMORY_LIMIT': memory_limit})
    deployment, service = yaml.safe_load_all(text)
    if deployment['kind'] != 'Deployment' or service['kind'] != 'Service' or deployment['metadata']['namespace'] != NAMESPACE:
        raise ValueError('AWS_HANDOFF_TEMPLATE_SHAPE_UNEXPECTED')
    spec = GENERATIONS[generation]
    pod = deployment['spec']['template']
    labels = {'guard.run': run_id, 'byul.a/generation': generation}
    pod['metadata']['labels'].update(labels)
    pod['spec']['enableServiceLinks'] = False  # Service 링크 환경 변수가 MP worker 초기화를 깨뜨렸다(첫 실증).
    engine, = (c for c in pod['spec']['containers'] if c['name'] == 'engine')
    engine['args'] = [*engine['args'], *spec['args'], *(['--enforce-eager'] if enforce_eager else [])]
    engine['env'] = [*engine['env'], *({'name': k, 'value': v} for k, v in spec['env'].items())]
    run_root = f'{host_root}/{run_id}'
    if spec['sensor']:
        engine['env'].append({'name': 'PYTHONPATH', 'value': CALLBACKS})
        engine['volumeMounts'] += [{'name': 'guard-callbacks', 'mountPath': CALLBACKS, 'readOnly': True},
            {'name': 'guard-data', 'mountPath': DATA}]
        pod['spec']['volumes'] += [{'name': 'guard-callbacks', 'hostPath': {'path': run_root + '/engine-callbacks', 'type': 'Directory'}},
            {'name': 'guard-data', 'hostPath': {'path': run_root + '/lifecycle-data', 'type': 'Directory'}}]
    if spec.get('lab'):
        engine['volumeMounts'] += [{'name': 'guard-lora', 'mountPath': '/guard-lora'},
            {'name': 'guard-weights', 'mountPath': '/guard-weights'}]
        pod['spec']['volumes'] += [{'name': 'guard-lora', 'emptyDir': {'sizeLimit': '1Gi'}},
            {'name': 'guard-weights', 'emptyDir': {'sizeLimit': '4Gi'}}]
    probe = {'apiVersion': 'v1', 'kind': 'Pod',
        'metadata': {'name': f'engine-test-probe-{run_id}'[:63], 'namespace': NAMESPACE,
            'labels': {'app': 'engine-test-probe', **labels}},
        'spec': {'serviceAccountName': 'probe', 'automountServiceAccountToken': False, 'enableServiceLinks': False,
            'restartPolicy': 'Never',
            'securityContext': {'runAsUser': 2000, 'runAsGroup': 0, 'runAsNonRoot': True, 'seccompProfile': {'type': 'RuntimeDefault'}},
            # 이미 받은 엔진 이미지를 GPU 없이 쓴다. 시험 프로그램에 Python만 있으면 된다.
            'containers': [{'name': 'probe', 'image': engine_image, 'command': ['sleep', 'infinity'],
                'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'cpu': '1', 'memory': '1Gi'}}}]}}
    engine_yaml = yaml.safe_dump_all([deployment, service], sort_keys=False).encode()
    probe_yaml = yaml.safe_dump(probe, sort_keys=False).encode()
    record = {'schema': SCHEMA, 'handoff_zip_sha256': HANDOFF_ZIP_SHA256, 'template_sha256': TEMPLATE_SHA256,
        'generation': generation, 'profile': profile, 'run_id': run_id, 'engine_image': engine_image,
        'model_revision': model_revision, 'cache_path': cache, 'enforce_eager': enforce_eager,
        'lab_namespace_owner_label_required': bool(spec.get('lab')),
        'host_directories': ({'engine_callbacks': run_root + '/engine-callbacks', 'lifecycle_data': run_root + '/lifecycle-data'}
            if spec['sensor'] else {}),
        'outputs_sha256': {'engine.yaml': _sha(engine_yaml), 'probe.yaml': _sha(probe_yaml)}}
    return engine_yaml, probe_yaml, record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--handoff-dir', required=True)
    parser.add_argument('--generation', required=True, choices=sorted(GENERATIONS))
    parser.add_argument('--profile', default='dev', choices=sorted(PROFILES))
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--engine-image', required=True, help='인계 images.lock의 첫 줄')
    parser.add_argument('--model-revision', default='7ae557604adf67be50417f59c2c2f167def9a775')
    parser.add_argument('--host-root', default='/srv/byul-a')
    parser.add_argument('--enforce-eager', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    engine_yaml, probe_yaml, record = render(args.handoff_dir, generation=args.generation, profile=args.profile,
        run_id=args.run_id, engine_image=args.engine_image, model_revision=args.model_revision,
        host_root=args.host_root, enforce_eager=args.enforce_eager)
    args.out.mkdir(parents=True)
    (args.out / 'engine.yaml').write_bytes(engine_yaml); (args.out / 'probe.yaml').write_bytes(probe_yaml)
    (args.out / 'render.json').write_text(json.dumps(record, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'out': str(args.out), **record['outputs_sha256']}))


if __name__ == '__main__':
    main()
