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
RECEIVER_PORT = 18080
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
    # 실행기가 지원하는 유일한 조합(request_binding.SUPPORTED_POLICY). 기업 결정값이 아니다.
    'REQ-11': {'result_binding': 'originating_connection', 'client_request_id_trust': 'untrusted_correlation'},
    # EXAMPLE: 엔진 직통의 허용 주체는 runtime Gateway의 일반 주체 하나(REQ-01 기능 대조의 역할 이름은 allowed). 기업 목록이 아니다.
    'REQ-01': {'allowed_callers': ['allowed']},
    # EXAMPLE: 일반 주체에게 추론 경로만 허용한다. 관리·진단 경로는 관리자 역할만.
    'REQ-03': {'general_allowed_kinds': ['inference']},
}
EXECUTORS = {'REQ-13': ('response_content', {'endpoint': '/v1/chat/completions', 'request_timeout': 30}),
             'OBS-09': ('log_integrity', {'request_timeout': 30, 'max_duration_seconds': 300}),
             'REQ-11': ('request_binding', {'endpoint': '/v1/chat/completions', 'tokenize_endpoint': '/tokenize',
                                            'request_timeout': 30, 'max_duration_seconds': 300}),
             'REQ-01': ('direct_route', {'request_timeout': 10, 'max_duration_seconds': 300,
                                         'roles': {'allowed': 'DEV1_TOKEN_GENERAL'}}),
             'REQ-03': ('role_route', {'request_timeout': 10, 'max_duration_seconds': 300, 'admin_role': 'admin',
                                       'roles': {'general': 'DEV1_TOKEN_GENERAL', 'admin': 'DEV1_TOKEN_ADMIN'}})}


LIFECYCLE_FILES = ('guard_lifecycle_sensor.py', 'sitecustomize.py', 'settings.json', 'manifest.json')


def render(*, run_id, engine_image, lifecycle_dir=None, api_key_secret=None):
    """lifecycle_dir: `guard prepare-lifecycle-sensor` 출력. 주면 RUNBOOK의 계측 기동 설정을 더한다.

    고정 자산은 읽기 전용 /guard-lifecycle(ConfigMap), 기록은 /guard-lifecycle/data/<session>(엔진 UID 소유 0700)이다.
    준비물 하나가 세션 하나이므로 세션을 봉인하는 점검마다 엔진을 새로 띄운다.
    """
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
    if api_key_secret is not None:
        # PLT-10: 엔진 API 키는 Secret 참조로만 넣는다. 인자·리터럴 환경변수에 키를 두지 않는다(대상 읽기도 valueFrom을 읽지 않는다).
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', api_key_secret):
            raise ValueError('K3D_RENDER_INPUT_INVALID')
        engine['spec']['containers'][0]['env'].append(
            {'name': 'VLLM_API_KEY', 'valueFrom': {'secretKeyRef': {'name': api_key_secret, 'key': 'api-key'}}})
    lifecycle = None
    if lifecycle_dir is not None:
        assets = {name: (Path(lifecycle_dir) / name).read_text(encoding='utf-8') for name in LIFECYCLE_FILES}
        session = json.loads(assets['settings.json'])['session']
        if not re.fullmatch(r'[0-9a-f]{32}', session) or json.loads(assets['manifest.json'])['session'] != session:
            raise ValueError('K3D_LIFECYCLE_ASSETS_INVALID')
        lifecycle = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': f'guard-lifecycle-{run_id}'[:63], 'namespace': NAMESPACE,
                     'labels': labels}, 'data': assets}
        spec, container = engine['spec'], engine['spec']['containers'][0]
        container['args'] = [*ARGS, '--middleware', 'guard_lifecycle_sensor.Middleware']
        container['env'] += [{'name': 'PYTHONPATH', 'value': '/guard-lifecycle'},
                             {'name': 'GUARD_LIFECYCLE_CONFIG', 'value': '/guard-lifecycle/settings.json'}]
        container['volumeMounts'] += [{'name': 'lifecycle-assets', 'mountPath': '/guard-lifecycle', 'readOnly': True},
                                      {'name': 'lifecycle-data', 'mountPath': '/guard-lifecycle/data'}]
        spec['volumes'] += [{'name': 'lifecycle-assets', 'configMap': {'name': lifecycle['metadata']['name']}},
                            {'name': 'lifecycle-data', 'emptyDir': {'sizeLimit': '64Mi'}}]
        # 엔진은 이미지 기본 사용자(root)로 돈다. 같은 UID의 0700 세션 폴더를 엔진보다 먼저 만든다.
        spec['initContainers'] = [{'name': 'lifecycle-session', 'image': engine_image, 'imagePullPolicy': 'IfNotPresent',
                                   'command': ['sh', '-c', f'mkdir -m 0700 /guard-lifecycle/data/{session}'],
                                   'volumeMounts': [{'name': 'lifecycle-data', 'mountPath': '/guard-lifecycle/data'}]}]
    probe = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': f'engine-test-probe-{run_id}'[:63], 'namespace': NAMESPACE,
             'labels': {'app': 'engine-test-probe', **labels}},
             'spec': {'enableServiceLinks': False, 'automountServiceAccountToken': False, 'restartPolicy': 'Never',
                      'securityContext': {'runAsUser': 2000, 'runAsGroup': 0, 'runAsNonRoot': True},
                      'containers': [{'name': 'probe', 'image': engine_image, 'imagePullPolicy': 'IfNotPresent',
                                      'command': ['sleep', 'infinity'],
                                      'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                                      'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'cpu': '1', 'memory': '1Gi'}}}]}}
    # PLT-07: 원본 수신기를 Pod 명령으로 그대로 실행한다(A1_KUBERNETES_EGRESS_COMMON). 뜬 뒤 120초만 받으므로 측정 직전에 적용한다.
    receiver_source = (Path(__file__).parents[1] / 'src/guard/executors/egress_receiver.py').read_text(encoding='utf-8')
    receiver = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': f'egress-receiver-{run_id}'[:63], 'namespace': NAMESPACE,
                'labels': {'app': 'egress-receiver', **labels}},
                'spec': {'enableServiceLinks': False, 'automountServiceAccountToken': False, 'restartPolicy': 'Never',
                         'securityContext': {'runAsUser': 2000, 'runAsGroup': 2000, 'runAsNonRoot': True},
                         'containers': [{'name': 'receiver', 'image': engine_image, 'imagePullPolicy': 'IfNotPresent',
                                         'command': ['python3', '-u', '-c', receiver_source], 'args': [str(RECEIVER_PORT)],
                                         'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                                         'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}},
                                         'volumeMounts': [{'name': 'observations', 'mountPath': '/guard-observations'}]}],
                         'volumes': [{'name': 'observations', 'emptyDir': {'sizeLimit': '16Mi'}}]}}
    docs = {'namespace.yaml': namespace, 'engine.yaml': engine, 'probe.yaml': probe, 'receiver.yaml': receiver,
            **({'lifecycle.yaml': lifecycle} if lifecycle else {})}
    raw = {name: yaml.safe_dump(value, sort_keys=False).encode() for name, value in docs.items()}
    record = {'schema': SCHEMA, 'generation': GENERATION, 'profile': 'cpu', 'run_id': run_id, 'engine_image': engine_image,
              'model_id': MODEL_ID, 'model_revision': MODEL_REVISION, 'served_model_name': SERVED,
              'args': engine['spec']['containers'][0]['args'], 'lifecycle_session': lifecycle and json.loads(lifecycle['data']['settings.json'])['session'],
              'enforce_eager': True, 'lab_namespace_owner_label_required': True,
              'outputs_sha256': {'engine.yaml': hashlib.sha256(raw['engine.yaml']).hexdigest(),
                                 **{n: hashlib.sha256(b).hexdigest() for n, b in raw.items() if n != 'engine.yaml'}}}
    return raw, record


GATEWAY_PORT = 8080
GATEWAY_LOOPBACK_PORT = 8081
# runtime Gateway는 설계상 127.0.0.1에만 바인딩한다(runtime deploy/gpu-lab README의 미결 노출 방식).
# lab에서는 같은 Pod의 중계 컨테이너가 0.0.0.0:8080을 127.0.0.1:8081로 넘긴다. runtime 코드는 바꾸지 않는다.
RELAY = r"""
import socket,threading
def pipe(a,b):
    try:
        while True:
            d=a.recv(65536)
            if not d: break
            b.sendall(d)
    except OSError: pass
    finally:
        for s in (a,b):
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            s.close()
def serve(c):
    try: u=socket.create_connection(("127.0.0.1",%d),timeout=10)
    except OSError: c.close(); return
    u.settimeout(None)
    threading.Thread(target=pipe,args=(c,u),daemon=True).start(); pipe(u,c)
l=socket.socket(); l.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); l.bind(("0.0.0.0",%d)); l.listen(64)
while True:
    c,_=l.accept(); threading.Thread(target=serve,args=(c,),daemon=True).start()
"""
# runtime Gateway(BYUL runtime 제품)를 lab Gateway로 쓴다. 역할은 REQ-03(general/admin)과 REQ-06(A/B) 시험용이다.
PRINCIPALS = {'general': ['inference'], 'admin': ['inference', 'admin'], 'A': ['inference'], 'B': ['inference']}


def _sha_json(value):
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    return raw, hashlib.sha256(raw).hexdigest()


def gateway_lab(*, run_id, gateway_image, tokens):
    """4Pod 구성의 Gateway·바깥 probe·엔진 Service·엔진 격리 정책과 runtime 설정(비밀 원문 없음).

    tokens: 주체 이름 → 원문 토큰. 설정에는 SHA-256만 들어간다. 원문은 호출자가 저장소 밖에 0600으로 둔다.
    """
    if not _IMAGE.fullmatch(gateway_image or '') or set(tokens) != set(PRINCIPALS):
        raise ValueError('K3D_GATEWAY_INPUT_INVALID')
    labels = {'guard.run': run_id, 'byul.a/generation': GENERATION}
    target_id = f'{NAMESPACE}-{run_id}'
    policy_raw, policy_sha = _sha_json({'schema_version': 'runtime-mvp/1.1', 'policy_id': 'dev1-k3d-gateway', 'policy_version': '1',
        'target_id': target_id, 'allowed_models': [SERVED], 'output_default_tokens': 8, 'output_max_tokens': 16,
        'max_body_bytes': 65536, 'max_context_tokens': 512, 'valid_from': '2026-01-01T00:00:00+00:00',
        'valid_until': '2027-12-31T23:59:59+00:00', 'approval_ref': 'DEV1-K3D-EXAMPLE'})
    approvals_raw, approvals_sha = _sha_json({'schema_version': 'runtime-policy-approvals/1.0', 'entries': [{'policy_id': 'dev1-k3d-gateway',
        'policy_version': '1', 'sha256': policy_sha, 'approval_ref': 'DEV1-K3D-EXAMPLE', 'approved_at': '2026-11-04T00:00:00+00:00',
        'valid_until': '2027-12-31T23:59:59+00:00'}]})
    target_raw, target_sha = _sha_json({'schema_version': 'runtime-target/1.0', 'target_id': target_id, 'target_ref': 'DEV1-K3D-' + run_id,
        'engine': {'kind': 'vllm', 'version': '0.31.0'}, 'model': {'id': SERVED}, 'baseline_ref': None})
    bundle_raw, _ = _sha_json({'schema_version': 'byul-policy-bundle/0.1', 'bundle_id': 'bundle-dev1-k3d-' + run_id,
        'created_at': '2026-11-04T00:00:00+00:00', 'policy_ref': {'policy_id': 'dev1-k3d-gateway', 'policy_version': '1', 'sha256': policy_sha},
        'target_ref': target_id, 'approval': {'approved_by': 'dev1-lab-owner-EXAMPLE', 'approved_at': '2026-11-04T00:00:00+00:00',
        'approval_ref': 'DEV1-K3D-EXAMPLE'}, 'validity': {'valid_from': '2026-01-01T00:00:00+00:00', 'valid_until': '2027-12-31T23:59:59+00:00'},
        'files': [{'role': 'gateway_policy', 'path': 'runtime-policy.json', 'schema_version': 'runtime-mvp/1.1', 'sha256': policy_sha, 'bytes': len(policy_raw)},
                  {'role': 'policy_approvals', 'path': 'policy-approvals.json', 'schema_version': 'runtime-policy-approvals/1.0', 'sha256': approvals_sha, 'bytes': len(approvals_raw)},
                  {'role': 'target', 'path': 'target.json', 'schema_version': 'runtime-target/1.0', 'sha256': target_sha, 'bytes': len(target_raw)}]})
    engine_service = f'serving-engine-{run_id}'[:63]
    backend = {'schema_version': 'runtime-backend/1.0', 'kind': 'vllm', 'base_url': f'http://{engine_service}.{NAMESPACE}.svc.cluster.local:8000',
               'model_map': {SERVED: SERVED}, 'health_path': '/health', 'models_path': '/v1/models', 'version_path': '/version',
               'metrics_url': f'http://{engine_service}.{NAMESPACE}.svc.cluster.local:8000/metrics',
               'timeouts': {'relay_timeout_s': 60, 'status_timeout_s': 5, 'stream_idle_timeout_s': 30, 'request_deadline_s': 120}}
    auth = {'schema_version': 'runtime-auth/1.0', 'auth_ref': 'DEV1-K3D-AUTH-EXAMPLE',
            'principals': [{'principal_id': 'dev1-' + name.lower(), 'roles': roles, 'allowed_models': [SERVED],
                            'token_sha256': hashlib.sha256(tokens[name].encode('ascii')).hexdigest()} for name, roles in PRINCIPALS.items()],
            'route_roles': {'POST /v1/chat/completions': ['inference'], 'POST /v1/completions': ['inference'], 'GET /api/*': ['admin']}}
    config = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': f'gateway-config-{run_id}'[:63], 'namespace': NAMESPACE, 'labels': labels},
              'data': {'backend.json': json.dumps(backend, indent=2) + '\n', 'auth.json': json.dumps(auth, indent=2) + '\n',
                       'bundle.json': bundle_raw.decode(), 'runtime-policy.json': policy_raw.decode(),
                       'policy-approvals.json': approvals_raw.decode(), 'target.json': target_raw.decode()}}
    files = {'backend.json': 'backend.json', 'auth.json': 'auth.json'}
    bundle_items = ['bundle.json', 'runtime-policy.json', 'policy-approvals.json', 'target.json']
    gateway = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': f'serving-gateway-{run_id}'[:63], 'namespace': NAMESPACE,
               'labels': {'app': 'serving-gateway', **labels}},
               'spec': {'enableServiceLinks': False, 'automountServiceAccountToken': False, 'restartPolicy': 'Never',
                        'securityContext': {'runAsUser': 10000, 'runAsGroup': 10000, 'runAsNonRoot': True, 'seccompProfile': {'type': 'RuntimeDefault'}},
                        'containers': [{'name': 'gateway', 'image': gateway_image, 'imagePullPolicy': 'IfNotPresent',
                            'args': ['mvp', '--backend', 'vllm', '--backend-config', '/etc/serving/backend/backend.json',
                                     '--policy-bundle', '/etc/serving/bundle', '--auth-config', '/etc/serving/auth/auth.json',
                                     '--gateway-port', str(GATEWAY_LOOPBACK_PORT), '--data-dir', '/srv/serving/runtime/data',
                                     '--environment', 'dev1-k3d', '--run-id', run_id],
                            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                            'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}},
                            'volumeMounts': [{'name': 'config', 'mountPath': '/etc/serving/backend/backend.json', 'subPath': 'backend.json', 'readOnly': True},
                                             {'name': 'config', 'mountPath': '/etc/serving/auth/auth.json', 'subPath': 'auth.json', 'readOnly': True},
                                             *({'name': 'config', 'mountPath': f'/etc/serving/bundle/{n}', 'subPath': n, 'readOnly': True} for n in bundle_items),
                                             {'name': 'data', 'mountPath': '/srv/serving/runtime/data'}, {'name': 'tmp', 'mountPath': '/tmp'}]},
                                       {'name': 'relay', 'image': gateway_image, 'imagePullPolicy': 'IfNotPresent',
                                        'command': ['python3', '-u', '-c', RELAY % (GATEWAY_LOOPBACK_PORT, GATEWAY_PORT)],
                                        'ports': [{'containerPort': GATEWAY_PORT}],
                                        'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                                        'resources': {'requests': {'cpu': '20m', 'memory': '32Mi'}, 'limits': {'cpu': '500m', 'memory': '128Mi'}}}],
                        'volumes': [{'name': 'config', 'configMap': {'name': config['metadata']['name']}},
                                    {'name': 'data', 'emptyDir': {'sizeLimit': '256Mi'}}, {'name': 'tmp', 'emptyDir': {'sizeLimit': '64Mi'}}]}}
    external = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': f'external-test-probe-{run_id}'[:63], 'namespace': NAMESPACE,
                'labels': {'app': 'external-test-probe', **labels}},
                'spec': {'enableServiceLinks': False, 'automountServiceAccountToken': False, 'restartPolicy': 'Never',
                         'securityContext': {'runAsUser': 2000, 'runAsGroup': 0, 'runAsNonRoot': True},
                         'containers': [{'name': 'probe', 'image': gateway_image, 'imagePullPolicy': 'IfNotPresent', 'command': ['sleep', 'infinity'],
                                         'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                                         'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}}}]}}
    service = {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': engine_service, 'namespace': NAMESPACE, 'labels': labels},
               'spec': {'selector': {'app': 'serving-engine', 'guard.run': run_id}, 'ports': [{'port': 8000, 'targetPort': 8000}]}}
    # REQ-01: 엔진은 Gateway와 안쪽 probe에서만 받는다. 바깥 probe의 직통 접근은 막는다.
    isolate = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': {'name': f'engine-ingress-{run_id}'[:63], 'namespace': NAMESPACE},
               'spec': {'podSelector': {'matchLabels': {'app': 'serving-engine', 'guard.run': run_id}}, 'policyTypes': ['Ingress'],
                        'ingress': [{'from': [{'podSelector': {'matchLabels': {'app': name, 'guard.run': run_id}}} for name in ('serving-gateway', 'engine-test-probe')],
                                     'ports': [{'port': 8000}]}]}}
    docs = {'gateway-config.yaml': config, 'gateway.yaml': gateway, 'external-probe.yaml': external, 'engine-service.yaml': service,
            'engine-ingress.yaml': isolate}
    return {name: yaml.safe_dump(value, sort_keys=False).encode() for name, value in docs.items()}


def route_target(base, gateway_pod, external_pod, namespace):
    """REQ-01·03·06 4Pod target: 기존 target에 Gateway·바깥 probe 바인딩과 직통 대상만 더한다."""
    from guard.runtime.kubernetes_routes import EXTERNAL
    value = json.loads(json.dumps(base))
    value['kubernetes_routes'] = {'gateway': inputs._binding(gateway_pod, namespace, 'gateway', 'serving-gateway'),
                                  'external_probe': inputs._binding(external_pod, namespace, 'probe', 'external-test-probe')}
    value['gateway'] = {'url': f"http://{value['kubernetes_routes']['gateway']['pod_ip']}:{GATEWAY_PORT}"}
    value['origins'] = {'trusted': {'network': 'kubernetes-probe'}, 'external': {'network': EXTERNAL}}
    value['direct_targets'] = [{'origin': 'external', 'url': value['engine']['trusted_url']}]
    return value


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


def egress(source_target, receiver_pod, *, receiver_pid, subject_uid, subject_netns, root, minutes=20):
    """PLT-07 수신기 target·EXAMPLE 정책·DRAFT 팩·실행 설정. 대상 사실(PID·UID·netns)은 측정 직전 실제 Pod에서 읽어 넘긴다."""
    from datetime import datetime, timedelta, timezone
    from guard.runtime.kubernetes_binding import validate_container_binding
    from tools import build_kubernetes_boundary_egress_pack as pack_tool
    meta, spec, status = receiver_pod['metadata'], receiver_pod['spec'], receiver_pod['status']
    state, = (c for c in status['containerStatuses'] if c['name'] == 'receiver')
    image, = (c['image'] for c in spec['containers'] if c['name'] == 'receiver')
    binding = {'namespace': meta['namespace'], 'namespace_uid': source_target['kubernetes']['engine']['namespace_uid'],
               'pod': meta['name'], 'pod_uid': meta['uid'], 'container': 'receiver', 'container_id': state['containerID'],
               'image': image, 'image_id': state['imageID'], 'restart_count': state['restartCount'], 'node': spec['nodeName'],
               'pod_ip': status['podIP'], 'started_at': state['state']['running']['startedAt']}
    validate_container_binding(binding)
    receiver_target = {'schema': 'guard-kubernetes-egress-receiver-target/1', 'environment_ref': source_target['environment_ref'],
                       'source_target_sha256': sha256_hex(canonical_json(source_target)), 'run_id': source_target['kubernetes']['run_id'],
                       'lab': True, 'context': source_target['kubernetes']['context'], 'receiver': binding, 'receiver_pid': receiver_pid}
    now = datetime.now(timezone.utc)
    values = {'subject_scope': 'all_processes_in_container_uid_netns',
              'subject_container_id': source_target['kubernetes']['engine']['container_id'], 'subject_uid': subject_uid,
              'subject_net_namespace_inode': subject_netns, 'forbidden_receiver_container_id': binding['container_id'],
              'forbidden_receiver_ip': binding['pod_ip'], 'forbidden_receiver_port': RECEIVER_PORT, 'forbidden_protocol': 'tcp',
              'forbidden_operation': 'tcp_connect', 'validity_start': now.isoformat(), 'validity_end': (now + timedelta(minutes=minutes)).isoformat(),
              'source_target_sha256': receiver_target['source_target_sha256'], 'receiver_target_sha256': sha256_hex(canonical_json(receiver_target))}
    root = Path(root).absolute()
    pack_tool.derive(Path(__file__).parents[1] / 'reference/pack-seeds/PACK-BASELINE-31', root / 'pack')
    pack_ref = yaml.safe_load((root / 'pack' / 'pack.yaml').read_text(encoding='utf-8'))
    policy = {'data_origin': 'EXAMPLE', 'status': 'DRAFT', 'pack': {'id': pack_ref['pack_id'], 'revision': pack_ref['revision']},
              'values': {'PLT-07': values}, 'scope': {'include': ['PLT-07'], 'exclude': []}, 'exceptions': [],
              'confirmed_by': None, 'confirmed_at': None}
    config = '\n'.join([f"store = '{root / 'store'}'", '', '[adapter]', "name = 'vllm'", '', '[executors]', "boundary_egress = 'kubernetes'", '',
                        '[boundary_egress]', f"target = '{root / 'target.json'}'", f"receiver_target = '{root / 'receiver-target.json'}'",
                        f'actor_spec = {{uid={subject_uid},gid={subject_uid},groups=[{subject_uid}]}}', 'connect_timeout = 1',
                        'total_timeout = 10', 'max_duration_seconds = 60', ''])
    return receiver_target, policy, config


def _prepared(path):
    from guard.collectors.lifecycle import load_prepared
    return load_prepared(path)


def route_record(base, env, records_text, *, session, gateway_pod, root, prepared=None, minutes=10):
    """REQ-02: runtime Gateway의 실제 요청 기록을 엔진 소유 위치용 route 기록으로 옮기고 정책·설정을 만든다.

    native_route 부분 수집은 엔진 안 lifecycle 센서 준비물(prepared manifest)이 있어야 한다. 주지 않으면
    준비물이 없다는 값을 두고, 실행기가 그 부분을 SUBCOLLECTION_UNAVAILABLE로 남긴다.
    """
    from datetime import datetime, timedelta, timezone
    from tools import dev1_runtime_records as bridge
    if not re.fullmatch(r'[0-9a-f]{32}', session):
        raise ValueError('K3D_ROUTE_SESSION_INVALID')
    engine = base['kubernetes']['engine']
    rows = bridge.final_records(records_text.splitlines(), run_id=base['kubernetes']['run_id'])
    packet = bridge.route_packet(rows, run_id=base['kubernetes']['run_id'], environment_ref=env.ref().model_dump(mode='json'),
                                 image=engine['image'], ingress_id=gateway_pod, upstream_id=engine['pod'])
    data = json.dumps(packet, ensure_ascii=False, sort_keys=True).encode()
    now = datetime.now(timezone.utc)
    start, end = now.isoformat(), (now + timedelta(minutes=minutes)).isoformat()
    target_sha = sha256_hex(canonical_json(base))
    values = {'route_environment_sha256': env.ref().sha256, 'route_target_sha256': target_sha, 'route_session': session,
              'route_file_sha256': hashlib.sha256(data).hexdigest(), 'route_validity_start': start, 'route_validity_end': end,
              'route_native_environment_sha256': env.ref().sha256, 'route_native_target_sha256': target_sha,
              # 준비물이 없으면 그 사실을 값으로 남긴다. 부분 수집은 이 값과 맞는 준비물을 찾지 못해 실패한다.
              'route_native_prepared_sha256': (sha256_hex(canonical_json(_prepared(prepared))) if prepared
                                               else hashlib.sha256(b'k3d-cpu-lab-native-route-prepared-absent').hexdigest()),
              'route_native_validity_start': start, 'route_native_validity_end': end}
    root = Path(root).absolute()
    pack = yaml.safe_load((Path(__file__).parents[1] / 'docs/gpu-a/policy-a-draft.yaml').read_text(encoding='utf-8'))['pack']
    policy = {'data_origin': 'EXAMPLE', 'status': 'DRAFT', 'pack': pack, 'values': {'REQ-02': values},
              'scope': {'include': ['REQ-02'], 'exclude': []}, 'exceptions': [], 'confirmed_by': None, 'confirmed_at': None}
    config = '\n'.join([f"store = '{root / 'store'}'", '', '[adapter]', "name = 'vllm'", '', '[executors]', "route_record = 'kubernetes'", '',
                        '[route_record]', f"target = '{root / 'target.json'}'", "kubectl_binary = 'kubectl'", 'max_duration_seconds = 60',
                        f"native_route = {{target = '{root / 'target.json'}', prepared = '{Path(prepared).absolute() if prepared else root / 'native-route-prepared-absent.json'}', max_duration_seconds = 120}}", ''])
    return data, policy, config, len(packet['events'])


READER_USER, READER_GROUP = 'dev1-log-reader', 'dev1-log-readers'
NO_READER_USER, NO_READER_GROUP = 'dev1-no-reader', 'dev1-no-readers'


def lifecycle_target(base):
    """엔진 Pod만 고르는 수집 target(`guard-kubernetes-lifecycle-target/1`). 같은 run·Pod 바인딩을 그대로 쓴다."""
    return {'schema': 'guard-kubernetes-lifecycle-target/1', 'run_id': base['kubernetes']['run_id'],
            'environment_ref': base['environment_ref'], 'lab': True,
            'kubernetes': {'context': base['kubernetes']['context'], 'engine': base['kubernetes']['engine']}}


def record_access(base, env, *, session, root, minutes=20):
    """OBS-06: 엔진 Pod 로그를 읽을 수 있는 신원 하나와 없는 신원 하나(RBAC)와 주체 계획·정책·설정.

    두 신원은 kubectl 가장(--as)으로 쓴다. 사본 목록(로그 sink·백업·회전 파일 등)은 기업 기록 체계의 사실이라
    lab에서 만들지 않는다. 실행기는 그 부분을 RECORD_ACCESS_INVENTORY_UNAVAILABLE로 남긴다.
    """
    from datetime import datetime, timedelta, timezone
    if not re.fullmatch(r'[0-9a-f]{32}', session):
        raise ValueError('K3D_RECORD_SESSION_INVALID')
    engine = base['kubernetes']['engine']
    base = lifecycle_target(base)
    rbac = [{'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role',
             'metadata': {'name': 'dev1-engine-log-reader', 'namespace': engine['namespace']},
             'rules': [{'apiGroups': [''], 'resources': ['pods'], 'resourceNames': [engine['pod']], 'verbs': ['get']},
                       {'apiGroups': [''], 'resources': ['pods/log'], 'resourceNames': [engine['pod']], 'verbs': ['get']}]},
            {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding',
             'metadata': {'name': 'dev1-engine-log-reader', 'namespace': engine['namespace']},
             'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'dev1-engine-log-reader'},
             'subjects': [{'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'User', 'name': READER_USER}]}]
    rbac_text = yaml.safe_dump_all(rbac, sort_keys=False)
    # 조회 권한 정책의 출처는 위 RBAC 매니페스트다. 기업 승인 정책이 아니므로 EXAMPLE 식별자를 쓴다.
    access_ref = {'kind': 'POL', 'id': 'POL-DEV1-K3D-RECORD-ACCESS-EXAMPLE', 'revision': 1,
                  'sha256': hashlib.sha256(rbac_text.encode()).hexdigest()}
    target_sha = sha256_hex(canonical_json(base))
    subjects = {'schema': 'guard-record-access-subjects/1', 'environment_ref': env.ref().model_dump(mode='json'),
                'target_sha256': target_sha, 'policy_ref': access_ref,
                'subjects': [{'role': 'authorized_reader', 'username': READER_USER, 'groups': [READER_GROUP, 'system:authenticated']},
                             {'role': 'unauthorized_reader', 'username': NO_READER_USER, 'groups': [NO_READER_GROUP, 'system:authenticated']}]}
    now = datetime.now(timezone.utc)
    values = {'record_environment_sha256': env.ref().sha256, 'record_target_sha256': target_sha,
              'record_subject_plan_sha256': sha256_hex(canonical_json(subjects)), 'record_inventory_session': session,
              'record_inventory_source_sha256': hashlib.sha256(b'k3d-cpu-lab-record-copy-inventory-absent').hexdigest(),
              'record_validity_start': now.isoformat(), 'record_validity_end': (now + timedelta(minutes=minutes)).isoformat()}
    root = Path(root).absolute()
    pack = yaml.safe_load((Path(__file__).parents[1] / 'docs/gpu-a/policy-a-draft.yaml').read_text(encoding='utf-8'))['pack']
    policy = {'data_origin': 'EXAMPLE', 'status': 'DRAFT', 'pack': pack, 'values': {'OBS-06': values},
              'scope': {'include': ['OBS-06'], 'exclude': []}, 'exceptions': [], 'confirmed_by': None, 'confirmed_at': None}
    config = '\n'.join([f"store = '{root / 'store'}'", '', '[adapter]', "name = 'vllm'", '', '[executors]', "record_access = 'kubernetes'", '',
                        '[record_access]', f"target = '{root / 'target.json'}'", f"subjects = '{root / 'subjects.json'}'",
                        "kubectl_binary = 'kubectl'", 'max_duration_seconds = 120', ''])
    return base, rbac_text, subjects, policy, config


def revocation(base, env, prepared, *, event_at, root, minutes=10):
    """PLT-10: 엔진 API 키 교체 뒤 퇴역 키의 새 접근과 대조 키의 정상 접근. EXAMPLE 정책.

    lab의 교체는 엔진을 대조 키 하나로 새로 기동한 것이다(event_at = 그 엔진의 기동 시각, 선언).
    발급자 사건을 검증할 수단이 없으므로 기한 기준은 absolute-unconditional이다. 키 원문은 환경변수 이름으로만 넘긴다.
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    values = {'revocation_environment_sha256': env.ref().sha256, 'revocation_target_sha256': sha256_hex(canonical_json(base)),
              'revocation_prepared_sha256': sha256_hex(canonical_json(_prepared(prepared))),
              'revocation_validity_start': now.isoformat(), 'revocation_validity_end': (now + timedelta(minutes=minutes)).isoformat(),
              'revocation_issuer_id': 'dev1-k3d-engine-restart', 'revocation_event_id': 'plt10-static-key-rotation',
              'revocation_event_at': event_at, 'revocation_deny_after': now.isoformat(),
              'revocation_deadline_basis': 'absolute-unconditional', 'revocation_propagation_limit_ms': 0,
              'revocation_max_clock_skew_ms': 1000, 'revocation_subject_label': 'retired', 'revocation_max_new_access_samples': 3}
    root = Path(root).absolute()
    pack = yaml.safe_load((Path(__file__).parents[1] / 'docs/gpu-a/policy-a-draft.yaml').read_text(encoding='utf-8'))['pack']
    policy = {'data_origin': 'EXAMPLE', 'status': 'DRAFT', 'pack': pack, 'values': {'PLT-10': values},
              'scope': {'include': ['PLT-10'], 'exclude': []}, 'exceptions': [], 'confirmed_by': None, 'confirmed_at': None}
    config = '\n'.join([f"store = '{root / 'store'}'", '', '[adapter]', "name = 'vllm'", '', '[executors]', "credential_revocation = 'kubernetes'", '',
                        '[credential_revocation]', f"target = '{root / 'target.json'}'", f"prepared = '{Path(prepared).absolute()}'",
                        "kubectl_binary = 'kubectl'", 'max_duration_seconds = 120',
                        "retired_key_env = 'DEV1_PLT10_RETIRED'", "control_key_env = 'DEV1_PLT10_CONTROL'", ''])
    return policy, config


def http_check(check_id, root):
    executor, settings = EXECUTORS[check_id]
    root = Path(root).absolute()
    # 팩 Ref는 기존 GPU 실측 정책 초안과 같은 PACK-BASELINE@32를 쓴다.
    pack = yaml.safe_load((Path(__file__).parents[1] / 'docs/gpu-a/policy-a-draft.yaml').read_text(encoding='utf-8'))['pack']
    policy = {'data_origin': 'EXAMPLE', 'status': 'DRAFT', 'pack': pack, 'values': {check_id: VALUES[check_id]},
              'scope': {'include': [check_id], 'exclude': []}, 'exceptions': [], 'confirmed_by': None, 'confirmed_at': None}
    lines = [f"store = '{root / 'store'}'", '', '[adapter]', "name = 'vllm'", '', '[executors]', f"{executor} = 'kubernetes'",
             '', f'[{executor}]', f"target = '{root / 'target.json'}'", "kubectl_binary = 'kubectl'"]
    def toml(value):
        if type(value) is str:
            return f"'{value}'"
        if type(value) is dict:
            return '{' + ', '.join(f'{k} = {toml(v)}' for k, v in value.items()) + '}'
        return str(value)
    lines += [f'{k} = {toml(v)}' for k, v in settings.items()]
    return policy, '\n'.join([*lines, ''])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='kind', required=True)
    r = sub.add_parser('render'); r.add_argument('--run-id', required=True); r.add_argument('--engine-image', required=True)
    r.add_argument('--lifecycle', type=Path); r.add_argument('--api-key-secret')
    r.add_argument('--out', type=Path, required=True)
    e = sub.add_parser('env'); e.add_argument('--render', type=Path, required=True); e.add_argument('--models', type=Path, required=True)
    e.add_argument('--out', type=Path, required=True)
    t = sub.add_parser('target'); t.add_argument('--env', type=Path, required=True); t.add_argument('--render', type=Path, required=True)
    for name in ('namespace', 'engine-pod', 'probe-pod'):
        t.add_argument('--' + name, type=Path, required=True)
    t.add_argument('--context', default='k3d-byul-cpu'); t.add_argument('--out', type=Path, required=True)
    w = sub.add_parser('gateway'); w.add_argument('--run-id', required=True); w.add_argument('--gateway-image', required=True)
    w.add_argument('--out', type=Path, required=True)
    rt = sub.add_parser('route-target'); rt.add_argument('--target', type=Path, required=True)
    for name in ('gateway-pod', 'external-pod', 'namespace'):
        rt.add_argument('--' + name, type=Path, required=True)
    rt.add_argument('--out', type=Path, required=True)
    g = sub.add_parser('egress'); g.add_argument('--target', type=Path, required=True); g.add_argument('--receiver-pod', type=Path, required=True)
    g.add_argument('--receiver-pid', type=int, required=True); g.add_argument('--subject-uid', type=int, required=True)
    g.add_argument('--subject-netns', type=int, required=True); g.add_argument('--out', type=Path, required=True)
    rr = sub.add_parser('route-record'); rr.add_argument('--target', type=Path, required=True); rr.add_argument('--env', type=Path, required=True)
    rr.add_argument('--records', type=Path, required=True); rr.add_argument('--session', required=True)
    rr.add_argument('--gateway-pod', required=True); rr.add_argument('--out', type=Path, required=True)
    rr.add_argument('--prepared', type=Path)
    ra = sub.add_parser('record-access'); ra.add_argument('--target', type=Path, required=True); ra.add_argument('--env', type=Path, required=True)
    ra.add_argument('--session', required=True); ra.add_argument('--out', type=Path, required=True)
    rv = sub.add_parser('revocation'); rv.add_argument('--target', type=Path, required=True); rv.add_argument('--env', type=Path, required=True)
    rv.add_argument('--prepared', type=Path, required=True); rv.add_argument('--event-at', required=True); rv.add_argument('--out', type=Path, required=True)
    h = sub.add_parser('http-check'); h.add_argument('--check', required=True, choices=sorted(EXECUTORS))
    h.add_argument('--target', type=Path, required=True); h.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    load = lambda path: json.loads(Path(path).read_text(encoding='utf-8'))
    if args.kind == 'render':
        raw, record = render(run_id=args.run_id, engine_image=args.engine_image, lifecycle_dir=args.lifecycle, api_key_secret=args.api_key_secret)
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
    elif args.kind == 'gateway':
        import os, secrets
        tokens = {name: secrets.token_urlsafe(32) for name in PRINCIPALS}
        args.out.mkdir(parents=True, exist_ok=True)
        secret_file = args.out / 'tokens.env'
        fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(''.join(f'DEV1_TOKEN_{name.upper()}={value}\n' for name, value in tokens.items()))
        for name, data in gateway_lab(run_id=args.run_id, gateway_image=args.gateway_image, tokens=tokens).items():
            inputs._write(args.out / name, data.decode())
        print(json.dumps({'gateway': str(args.out), 'tokens_file': str(secret_file), 'principals': sorted(PRINCIPALS)}))
    elif args.kind == 'route-target':
        value = route_target(load(args.target), load(args.gateway_pod), load(args.external_pod), load(args.namespace))
        inputs._write(args.out, value)
        print(json.dumps({'target': str(args.out), 'sha256': sha256_hex(canonical_json(value))}))
    elif args.kind == 'egress':
        receiver_target, policy, config = egress(load(args.target), load(args.receiver_pod), receiver_pid=args.receiver_pid,
            subject_uid=args.subject_uid, subject_netns=args.subject_netns, root=args.out)
        inputs._write(args.out / 'target.json', load(args.target)); inputs._write(args.out / 'receiver-target.json', receiver_target)
        inputs._write(args.out / 'policy.yaml', yaml.safe_dump(policy, sort_keys=False, allow_unicode=True))
        inputs._write(args.out / 'guard.toml', config)
        print(json.dumps({'check': 'PLT-07', 'out': str(args.out), 'pack': policy['pack']}))
    elif args.kind == 'route-record':
        data, policy, config, count = route_record(load(args.target), load_environment(args.env), args.records.read_text(encoding='utf-8'),
            session=args.session, gateway_pod=args.gateway_pod, root=args.out, prepared=args.prepared)
        inputs._write(args.out / 'target.json', load(args.target)); args.out.mkdir(parents=True, exist_ok=True)
        (args.out / 'route-observations.json').write_bytes(data)
        inputs._write(args.out / 'policy.yaml', yaml.safe_dump(policy, sort_keys=False, allow_unicode=True))
        inputs._write(args.out / 'guard.toml', config)
        print(json.dumps({'check': 'REQ-02', 'events': count, 'file_sha256': hashlib.sha256(data).hexdigest(),
                          'engine_path': f'/guard-route/data/{args.session}/route-observations.json'}))
    elif args.kind == 'record-access':
        selected, rbac, subjects, policy, config = record_access(load(args.target), load_environment(args.env), session=args.session, root=args.out)
        inputs._write(args.out / 'target.json', selected); inputs._write(args.out / 'rbac.yaml', rbac)
        inputs._write(args.out / 'subjects.json', subjects)
        inputs._write(args.out / 'policy.yaml', yaml.safe_dump(policy, sort_keys=False, allow_unicode=True))
        inputs._write(args.out / 'guard.toml', config)
        print(json.dumps({'check': 'OBS-06', 'out': str(args.out)}))
    elif args.kind == 'revocation':
        policy, config = revocation(load(args.target), load_environment(args.env), args.prepared, event_at=args.event_at, root=args.out)
        inputs._write(args.out / 'target.json', load(args.target))
        inputs._write(args.out / 'policy.yaml', yaml.safe_dump(policy, sort_keys=False, allow_unicode=True))
        inputs._write(args.out / 'guard.toml', config)
        print(json.dumps({'check': 'PLT-10', 'out': str(args.out)}))
    else:
        policy, config = http_check(args.check, args.out)
        inputs._write(args.out / 'target.json', load(args.target))
        inputs._write(args.out / 'policy.yaml', yaml.safe_dump(policy, sort_keys=False, allow_unicode=True))
        inputs._write(args.out / 'guard.toml', config)
        print(json.dumps({'check': args.check, 'executor': EXECUTORS[args.check][0], 'out': str(args.out)}))


if __name__ == '__main__':
    main()
