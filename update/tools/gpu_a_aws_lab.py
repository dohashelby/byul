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

SCHEMA = 'byul-gpu-a-aws-render/2'
HANDOFF_ZIP_SHA256 = '717fa446e62588f39caaee0e159a354c344b5d64da082e9a52bfaa6f697c5445'
TEMPLATE_SHA256 = '09cd45a4e10fae83b2eed07c8c4d4490311da4cf98646b681b0c91405c1f6eda'
TEST_ACCESS_SHA256 = '043bd977d53eed7ed457de6d544e181af3fb13da6d65e81478ec4f3cb23e78cc'
NAMESPACE = 'serving-lab'
# 인계 profile.sh와 같은 값. dev와 reference 결과를 서로 비교쌍으로 쓰지 않는다.
PROFILES = {'dev': ('2', '4', '8Gi', '16Gi'), 'reference': ('4', '8', '12Gi', '24Gi')}
CALLBACKS, DATA = '/guard-lifecycle/engine-callbacks', '/guard-lifecycle/data'
MP = ('--distributed-executor-backend', 'mp')
# guard 어댑터의 고정 SUP-03 템플릿(adapters/assets/chat). 호스트 사본은 실행 전에 같은 바이트로 둔다.
CHAT_TEMPLATE_PATH = '/guard-assets/chat/guard-text-chat-v1.jinja'
CHAT_TEMPLATE_SHA256 = 'e5b96cc7595d53305284a40f579747da846f01b566ad571ad2cb67526bdadc2e'
PROFILES_DIR = '/guard-profiles'
# k3d run 13과 같은 설정: with_stack·gzip·device time 표를 끈다(중지 때 trace 내보내기 메모리).
PROFILER_CONFIG = json.dumps({'profiler': 'torch', 'torch_profiler_dir': PROFILES_DIR, 'torch_profiler_with_stack': False,
    'torch_profiler_use_gzip': False, 'torch_profiler_dump_cuda_time_total': False}, sort_keys=True)
GENERATIONS = {
    # 콜백 센서는 V1 고정 소스 지점을 감싼다. REQ-14·16·17·18. TP1의 기본 executor는 워커를 따로 띄우지 않아
    # REQ-14가 감싸는 MultiprocExecutor·WorkerProc 지점이 불리지 않는다. 첫 실증과 같은 MP backend로 띄운다.
    'v1-callback': {'sensor': True, 'args': ['--enable-prefix-caching', *MP], 'env': {'VLLM_USE_V2_MODEL_RUNNER': '0'}},
    # REQ-14 동기 분기. async 기본 세대와 결과를 섞지 않는다.
    'v1-callback-sync': {'sensor': True, 'args': ['--enable-prefix-caching', *MP, '--no-async-scheduling'],
        'env': {'VLLM_USE_V2_MODEL_RUNNER': '0'}},
    # REQ-08·09·10, SUP-08. 기본 V2 runner, 센서 없음.
    'v2-default': {'sensor': False, 'args': [], 'env': {}},
    # SUP-08 코드 선택 PoC. 동적 처리기 코드(auto_map)를 실행하는 구성: --trust-remote-code 켬.
    # 성립 조건을 갖춘 취약 판본(예: 0.30)에서 FAIL을 보이는 세대다. 센서 없음.
    'v2-code-selection-trust': {'sensor': False, 'args': ['--trust-remote-code'], 'env': {}},
    # trust 비활성 대조. 동적 처리기가 필수라 기동 실패할 수도 있으므로 PASS를 가정하지 않는다.
    'v2-code-selection-notrust': {'sensor': False, 'args': [], 'env': {}},
    # SUP-03. guard 고정 템플릿 파일을 읽기 전용으로 마운트하고 --chat-template으로 고른다.
    'v2-template': {'sensor': False, 'template': True, 'args': ['--chat-template', CHAT_TEMPLATE_PATH], 'env': {}},
    # 2단계 P1·P2 생산자 GPU 확인. 콜백 센서의 선택 관측(prepare-engine-callbacks 플래그)을 한 엔진에서 함께 켠다.
    # 세대 전환을 직접 일으키는 개발 경로와 torch profiler를 켠다. 시험 예외로만 닿는 lab 엔진이다.
    'v1-producers': {'sensor': True, 'profiles': True,
        # MP backend를 쓰지 않는다: REQ-14 분산 관측(MultiprocExecutor·WorkerProc)이 프로세스마다 16 MiB 기록 한도를
        # 먼저 채워 생산자 행이 잘렸다(a-p1-20261105-1). TP1 기본 executor에서는 그 지점이 불리지 않는다.
        'args': ['--enable-prefix-caching', '--profiler-config', PROFILER_CONFIG],
        'env': {'VLLM_USE_V2_MODEL_RUNNER': '0', 'VLLM_SERVER_DEV_MODE': '1'}},
    # 같은 생산자 확인의 KV 압박 분기: KV 블록을 줄여 동시 요청 두 개가 선점(retry)·한도 집행 경로를 지나게 한다.
    # max_model_len 512·블록 40개(16토큰 기준 640토큰): 400토큰 요청 하나는 담고 두 개는 함께 못 담는다.
    # 처음 시도(2048·160블록·1,300토큰, a-p1-20261105-4)는 계약 사건 상한 4,096과 센서 파일 16 MiB를 넘었다.
    'v1-producers-kv': {'sensor': True, 'profiles': True,
        'args': ['--enable-prefix-caching', '--profiler-config', PROFILER_CONFIG, '--max-model-len', '512',
                 '--num-gpu-blocks-override', '40'],
        'env': {'VLLM_USE_V2_MODEL_RUNNER': '0', 'VLLM_SERVER_DEV_MODE': '1'}},
    # P2 runner-consumption의 V2 runner 경로 확인. 센서는 같은 준비 자산(--runner-consumption)으로 API 행과 V2 runner 행을 함께 남긴다.
    # V2 runner를 명시한다(0.31.0 기본값과 같다). lab kernel 대조는 같은 sample 지점을 감싸므로 이 세대에서 켜지 않는다.
    'v2-producers': {'sensor': True, 'args': ['--enable-prefix-caching'], 'env': {'VLLM_USE_V2_MODEL_RUNNER': '1'}},
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


def render(handoff_dir, *, generation, profile, run_id, engine_image, model_revision, host_root, enforce_eager=False,
           engine_version=None, model_id=None, model_host_snapshot=None,
           code_revision=None, extra_engine_args=(), max_model_len=2048):
    if (generation not in GENERATIONS or profile not in PROFILES or not _RUN_ID.fullmatch(run_id or '')
            or not _IMAGE.fullmatch(engine_image or '') or not re.fullmatch(r'[0-9a-f]{40}', model_revision or '')
            or not _HOST_PATH.fullmatch(host_root or '')):
        raise ValueError('AWS_RENDER_INPUT_INVALID')
    code_selection = generation in {'v2-code-selection-trust', 'v2-code-selection-notrust'}
    if code_selection and (engine_version is None or model_id is None or code_revision is None):
        raise ValueError('AWS_CODE_SELECTION_IDENTITY_REQUIRED')
    engine_version = engine_version or '0.31.0'
    model_id = model_id or 'Qwen/Qwen2.5-0.5B-Instruct'
    if (not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.+-]*)', engine_version or '')
            or not isinstance(model_id, str) or not _HOST_PATH.fullmatch('/' + model_id)
            or (model_host_snapshot is not None and not _HOST_PATH.fullmatch(model_host_snapshot))
            or (code_revision is not None and not re.fullmatch(r'[0-9a-f]{40}', code_revision))
            or type(max_model_len) is not int or not 256 <= max_model_len <= 131072
            or not isinstance(extra_engine_args, (list, tuple)) or len(extra_engine_args) > 32
            or any(not isinstance(arg, str) or not arg or '\x00' in arg or len(arg) > 2048 for arg in extra_engine_args)
            or any(arg in {'--model', '--revision', '--code-revision', '--trust-remote-code', '--served-model-name',
                           '--max-model-len'}
                   or arg.startswith(('--model=', '--revision=', '--code-revision=', '--trust-remote-code=',
                                      '--served-model-name=', '--max-model-len=')) for arg in extra_engine_args)):
        raise ValueError('AWS_RENDER_INPUT_INVALID')
    raw = (Path(handoff_dir) / 'engine.yaml.template').read_bytes()
    if _sha(raw) != TEMPLATE_SHA256:
        raise ValueError('AWS_HANDOFF_TEMPLATE_CHANGED')
    if _sha((Path(handoff_dir) / 'engine-test-access.yaml').read_bytes()) != TEST_ACCESS_SHA256:
        raise ValueError('AWS_HANDOFF_TEST_ACCESS_CHANGED')
    cpu_request, cpu_limit, memory_request, memory_limit = PROFILES[profile]
    cache_suffix = f'{profile}-{run_id}' if code_selection else f'{profile}-profile-01'
    cache = f'/srv/serving/cache/vllm/{engine_image.rsplit("@sha256:", 1)[1]}/{model_revision}/{cache_suffix}'
    marker_root = f'/cache/hf/byul-code-markers/{run_id}' if code_selection else None
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
    # 0.7.0 이미지에는 인계 템플릿의 nonroot wrapper가 없고 API 서버가 --model을 받는다.
    # 0.30.0과 0.31.0 이미지는 wrapper가 vllm serve의 위치 인자를 받는다.
    if code_selection and engine_version == '0.7.0':
        engine['command'] = ['python3', '-m', 'vllm.entrypoints.openai.api_server']
        engine['args'] = ['--model', *engine['args']]
    engine['args'][engine['args'].index('--max-model-len') + 1] = str(max_model_len)
    engine['args'] = [*engine['args'], *spec['args'], *(['--enforce-eager'] if enforce_eager else [])]
    engine['args'] += [*(['--code-revision', code_revision] if code_revision else []), *extra_engine_args]
    if model_host_snapshot is not None:
        model_volume, = (v for v in pod['spec']['volumes'] if v['name'] == 'model')
        model_volume['hostPath']['path'] = model_host_snapshot
    engine['env'] = [*engine['env'], *({'name': k, 'value': v} for k, v in spec['env'].items())]
    if marker_root is not None:
        engine['env'].append({'name': 'BYUL_CODE_MARKER_ROOT', 'value': marker_root})
    run_root = f'{host_root}/{run_id}'
    if spec['sensor']:
        engine['env'].append({'name': 'PYTHONPATH', 'value': CALLBACKS})
        engine['volumeMounts'] += [{'name': 'guard-callbacks', 'mountPath': CALLBACKS, 'readOnly': True},
            {'name': 'guard-data', 'mountPath': DATA}]
        pod['spec']['volumes'] += [{'name': 'guard-callbacks', 'hostPath': {'path': run_root + '/engine-callbacks', 'type': 'Directory'}},
            {'name': 'guard-data', 'hostPath': {'path': run_root + '/lifecycle-data', 'type': 'Directory'}}]
    if spec.get('template'):
        engine['volumeMounts'].append({'name': 'guard-chat-template', 'mountPath': CHAT_TEMPLATE_PATH, 'readOnly': True})
        pod['spec']['volumes'].append({'name': 'guard-chat-template',
            'hostPath': {'path': run_root + '/guard-text-chat-v1.jinja', 'type': 'File'}})
    if spec.get('profiles'):
        engine['volumeMounts'].append({'name': 'guard-profiles', 'mountPath': PROFILES_DIR})
        pod['spec']['volumes'].append({'name': 'guard-profiles', 'emptyDir': {'sizeLimit': '1Gi'}})
    if spec.get('lab'):
        engine['volumeMounts'] +=[{'name': 'guard-lora', 'mountPath': '/guard-lora'},
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
        'max_model_len': max_model_len,
        'engine_version': engine_version, 'model_id': model_id,
        'model_host_snapshot': model_host_snapshot or f'/srv/serving/models/{model_revision}',
        'code_revision': code_revision, 'code_marker_root': marker_root,
        'launch': {'command': list(engine['command']), 'argv': list(engine['args']),
            'runner': {'0': 'v1', '1': 'v2'}.get(spec['env'].get('VLLM_USE_V2_MODEL_RUNNER'), 'engine-default'),
            'async_requested': False if '--no-async-scheduling' in engine['args'] else None,
            'prefix_cache': True if '--enable-prefix-caching' in engine['args'] else None},
        'lab_namespace_owner_label_required': bool(spec.get('lab')),
        'host_directories': ({'engine_callbacks': run_root + '/engine-callbacks', 'lifecycle_data': run_root + '/lifecycle-data'}
            if spec['sensor'] else {}),
        **({'chat_template': {'host_file': run_root + '/guard-text-chat-v1.jinja', 'container_path': CHAT_TEMPLATE_PATH,
            'sha256': CHAT_TEMPLATE_SHA256}} if spec.get('template') else {}),
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
    parser.add_argument('--engine-version', help='코드 선택 세대에서는 필수이며 실제 설치본과 대조한다')
    parser.add_argument('--model-id', help='코드 선택 세대에서는 필수인 모델 저장소 ID')
    parser.add_argument('--model-host-snapshot')
    parser.add_argument('--max-model-len', type=int, default=2048)
    parser.add_argument('--code-revision')
    parser.add_argument('--extra-engine-args-json', default='[]', help='추가 기동 인자의 JSON 배열')
    parser.add_argument('--host-root', default='/srv/byul-a')
    parser.add_argument('--enforce-eager', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    engine_yaml, probe_yaml, record = render(args.handoff_dir, generation=args.generation, profile=args.profile,
        run_id=args.run_id, engine_image=args.engine_image, model_revision=args.model_revision,
        host_root=args.host_root, enforce_eager=args.enforce_eager, engine_version=args.engine_version,
        model_id=args.model_id, model_host_snapshot=args.model_host_snapshot, code_revision=args.code_revision,
        extra_engine_args=json.loads(args.extra_engine_args_json), max_model_len=args.max_model_len)
    args.out.mkdir(parents=True)
    (args.out / 'engine.yaml').write_bytes(engine_yaml); (args.out / 'probe.yaml').write_bytes(probe_yaml)
    (args.out / 'render.json').write_text(json.dumps(record, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'out': str(args.out), **record['outputs_sha256']}))


if __name__ == '__main__':
    main()
