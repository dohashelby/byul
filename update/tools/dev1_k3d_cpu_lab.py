"""개발 담당 k3d 로컬 CPU lab의 배포·실행 입력을 만든다. 클러스터·엔진을 직접 바꾸지 않는다.

render: 소유 네임스페이스·CPU vLLM 엔진 Pod·시험 Pod 매니페스트와 렌더 기록.
env:    렌더 기록과 실제 모델 파일 inventory로 봉인한 ENV(CPU, GPU 0).
target: kubectl get으로 받은 네임스페이스·두 Pod JSON으로 요청 바인딩 target(기존 GPU 입력 도구의 target 재사용).
http-check: REQ-13·OBS-09의 EXAMPLE 정책·실행 설정·명령.

결과는 "k3d 로컬 CPU" 환경이며 AWS K3s·GPU 결과와 합치지 않는다(DECISIONS 2026-11-04).
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import yaml

from guard.adapters.vllm import CHAT_TEMPLATE_CONTAINER_PATH, CHAT_TEMPLATE_PROFILE, CHAT_TEMPLATE_SHA256, VllmAdapter
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
        # CPU 진단의 eager 선언을 GPU 프로필에서도 실제 기동 인자로 지킨다. graph 대조는 별도 세대다.
        '--enforce-eager',
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


USAGE_SINK_PORT = 18090
# OBS-04·05 소유 수신지. 받은 보고는 본문을 남기지 않고 JSON 키 이름·크기만 `guard-usage-sink-receipt/1`로 적는다.
# 첫 행은 빈 기록도 읽을 수 있게 하는 시작 표식이다(usage_report.START_SCHEMA).
USAGE_SINK = r'''
import json,sys,time
from http.server import BaseHTTPRequestHandler,HTTPServer
PATH='/guard-usage/receipts.jsonl'
def write(row):
    with open(PATH,'a') as f: f.write(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n')
write({'schema':'guard-usage-sink-start/1'})
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n=min(int(self.headers.get('Content-Length') or 0),1048576); raw=self.rfile.read(n)
        try: value=json.loads(raw); fields=sorted(k for k in value if type(k) is str)[:64] if type(value) is dict else []
        except ValueError: fields=[]
        write({'schema':'guard-usage-sink-receipt/1','received_monotonic_ns':time.monotonic_ns(),
               'sink':self.headers.get('Host','')[:255],'fields':fields,'bytes':len(raw)})
        self.send_response(204); self.end_headers()
    def log_message(self,*a): pass
HTTPServer(('0.0.0.0',int(sys.argv[1])),H).serve_forever()
'''
def use_gpu_profile():
    """2단계 GPU lab 이름. CPU 진단 기록과 섞이지 않게 이름공간·세대 표식·API 별칭을 바꾼다."""
    global NAMESPACE, GENERATION, SERVED, SCHEMA
    NAMESPACE, GENERATION, SERVED, SCHEMA = 'byul-dev1-gpu-lab', 'gpu-l4', 'byul-dev1-lab', 'byul-dev1-gpu-render/1'
    ARGS[ARGS.index('--served-model-name') + 1] = SERVED


LIFECYCLE_FILES = ('guard_lifecycle_sensor.py', 'sitecustomize.py', 'settings.json', 'manifest.json')


def render(*, run_id, engine_image, lifecycle_dir=None, api_key_secret=None, usage=None, gpu=None, restart_always=False,
           engine_callbacks=None, dev_endpoints=False, profiler=False):
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
                       'containers': [{'name': 'engine', 'image': engine_image, 'imagePullPolicy': 'IfNotPresent',
                                       'command': ['vllm', 'serve'], 'args': ARGS,
                                       'env': [{'name': k, 'value': v} for k, v in ({**LOG_ENV} if gpu else {**LOG_ENV, 'VLLM_CPU_KVCACHE_SPACE': '1'}).items()],
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
    if engine_callbacks is not None:
        # 엔진 콜백 센서(`guard prepare-engine-callbacks`). 노드에 마운트된 모델 폴더 아래 자산을 읽기 전용으로 쓴다(AWS lab의 hostPath 방식과 같다).
        if lifecycle_dir is not None or not re.fullmatch(r'guard-callbacks-[a-z0-9-]{1,40}', engine_callbacks):
            raise ValueError('K3D_RENDER_INPUT_INVALID')
        container = engine['spec']['containers'][0]
        container['env'].append({'name': 'PYTHONPATH', 'value': '/guard-lifecycle/engine-callbacks'})
        container['volumeMounts'] += [{'name': 'guard-callbacks', 'mountPath': '/guard-lifecycle/engine-callbacks', 'readOnly': True},
                                      {'name': 'callback-data', 'mountPath': '/guard-lifecycle/data'}]
        engine['spec']['volumes'] += [{'name': 'guard-callbacks', 'hostPath': {'path': '/srv/models/' + engine_callbacks, 'type': 'Directory'}},
                                      {'name': 'callback-data', 'emptyDir': {'sizeLimit': '128Mi'}}]
    if profiler:
        # profiler-control(P1) lab: torch profiler를 켜고 출력은 엔진 Pod의 빈 폴더(/guard-profiles)에 둔다.
        container = engine['spec']['containers'][0]
        container['args'] = [*container['args'], '--profiler-config', json.dumps({'profiler': 'torch', 'torch_profiler_dir': '/guard-profiles',
            # 기본값(with_stack·gzip·device time 표)으로는 CPU lab에서 중지 때 메모리 초과였다(run 13, 센서 없이도 같음).
            'torch_profiler_with_stack': False, 'torch_profiler_use_gzip': False, 'torch_profiler_dump_cuda_time_total': False})]
        container['volumeMounts'].append({'name': 'profiles', 'mountPath': '/guard-profiles'})
        engine['spec']['volumes'].append({'name': 'profiles', 'emptyDir': {'sizeLimit': '512Mi'}})
        # 4 GiB 한도에서는 중지 때 trace 내보내기가 OOMKilled됐다(run 13 첫 시도).
        container['resources']['limits']['memory'] = '6Gi'
    if dev_endpoints:
        # lab 전용: vLLM 개발 경로(/collective_rpc·/reset_prefix_cache·/pause·/resume·/sleep). 세대 전환을 직접 일으킬 때만 켠다.
        engine['spec']['containers'][0]['env'].append({'name': 'VLLM_SERVER_DEV_MODE', 'value': '1'})
    if restart_always:
        # REQ-21 lab 재기동: 같은 Pod에서 엔진 컨테이너만 다시 뜨게 한다(engine_restart 동작의 전제).
        engine['spec']['restartPolicy'] = 'Always'
    if gpu is not None:
        # 2단계 GPU lab(Linux amd64 K3s, NVIDIA device plugin). 같은 모델·인자에 GPU 1장과 메모리 비율만 더한다.
        if (type(gpu) is not dict or set(gpu) - {'model', 'driver', 'runtime_class'} or type(gpu.get('model')) is not str
                or not gpu['model'] or gpu.get('runtime_class') is not None and not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', gpu['runtime_class'])):
            raise ValueError('K3D_RENDER_INPUT_INVALID')
        container = engine['spec']['containers'][0]
        container['args'] = [*container['args'], '--gpu-memory-utilization', '0.5']
        container['resources'] = {'requests': {'cpu': '2', 'memory': '8Gi', 'nvidia.com/gpu': '1'},
                                  'limits': {'cpu': '4', 'memory': '16Gi', 'nvidia.com/gpu': '1'}}
        if gpu.get('runtime_class'):
            engine['spec']['runtimeClassName'] = gpu['runtime_class']
    if usage is not None:
        # OBS-04(forbidden)·OBS-05(allowed): 보고 목적지를 같은 run의 소유 수신 Service로 바꾼다. 금지면 두 끄기 설정도 넣는다.
        if usage not in {'forbidden', 'allowed'}:
            raise ValueError('K3D_RENDER_INPUT_INVALID')
        settings = {'VLLM_USAGE_STATS_SERVER': f'http://usage-sink-{run_id}.{NAMESPACE}.svc:{USAGE_SINK_PORT}/report'}
        if usage == 'forbidden':
            settings.update(VLLM_NO_USAGE_STATS='1', DO_NOT_TRACK='1')
        engine['spec']['containers'][0]['env'] += [{'name': k, 'value': v} for k, v in settings.items()]
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
        container['args'] = [*container['args'], '--middleware', 'guard_lifecycle_sensor.Middleware']
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
    sink = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': f'usage-sink-{run_id}'[:63], 'namespace': NAMESPACE,
            'labels': {'app': 'usage-sink', **labels}},
            'spec': {'enableServiceLinks': False, 'automountServiceAccountToken': False, 'restartPolicy': 'Never',
                     'securityContext': {'runAsUser': 2000, 'runAsGroup': 2000, 'runAsNonRoot': True},
                     'containers': [{'name': 'sink', 'image': engine_image, 'imagePullPolicy': 'IfNotPresent',
                                     'command': ['python3', '-u', '-c', USAGE_SINK], 'args': [str(USAGE_SINK_PORT)],
                                     'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                                     'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}},
                                     'volumeMounts': [{'name': 'usage', 'mountPath': '/guard-usage'}]}],
                     'volumes': [{'name': 'usage', 'emptyDir': {'sizeLimit': '16Mi'}}]}}
    sink_service = {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': f'usage-sink-{run_id}'[:63], 'namespace': NAMESPACE, 'labels': labels},
                    'spec': {'selector': {'app': 'usage-sink', **labels}, 'ports': [{'port': USAGE_SINK_PORT, 'targetPort': USAGE_SINK_PORT}]}}
    docs = {'namespace.yaml': namespace, 'engine.yaml': engine, 'probe.yaml': probe, 'receiver.yaml': receiver,
            'usage-sink.yaml': sink, 'usage-sink-service.yaml': sink_service,
            **({'lifecycle.yaml': lifecycle} if lifecycle else {})}
    raw = {name: yaml.safe_dump(value, sort_keys=False).encode() for name, value in docs.items()}
    record = {'schema': SCHEMA, 'generation': GENERATION, 'profile': 'gpu' if gpu else 'cpu', 'gpu': gpu, 'run_id': run_id, 'engine_image': engine_image,
              'model_id': MODEL_ID, 'model_revision': MODEL_REVISION, 'served_model_name': SERVED,
              'args': engine['spec']['containers'][0]['args'], 'lifecycle_session': lifecycle and json.loads(lifecycle['data']['settings.json'])['session'],
              'enforce_eager': '--enforce-eager' in engine['spec']['containers'][0]['args'], 'lab_namespace_owner_label_required': True,
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
# observer: 인증은 되지만 추론 역할이 없는 주체. OBS-01 Gateway 경로의 거부 시도(누가 거부됐는지 기록에 남는다)에 쓴다.
PRINCIPALS = {'general': ['inference'], 'admin': ['inference', 'admin'], 'A': ['inference'], 'B': ['inference'], 'observer': ['observer']}


def _sha_json(value):
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    return raw, hashlib.sha256(raw).hexdigest()


def gateway_lab(*, run_id, gateway_image, tokens, chat_temperature=False):
    """4Pod 구성의 Gateway·바깥 probe·엔진 Service·엔진 격리 정책과 runtime 설정(비밀 원문 없음).

    tokens: 주체 이름 → 원문 토큰. 설정에는 SHA-256만 들어간다. 원문은 호출자가 저장소 밖에 0600으로 둔다.
    """
    if not _IMAGE.fullmatch(gateway_image or '') or set(tokens) != set(PRINCIPALS):
        raise ValueError('K3D_GATEWAY_INPUT_INVALID')
    labels = {'guard.run': run_id, 'byul.a/generation': GENERATION}
    target_id = f'{NAMESPACE}-{run_id}'
    # extra_fields는 runtime 정책 스키마 1.2부터 받는다(runtime mvp/policy.py _V12_FIELDS).
    policy_schema = 'runtime-mvp/1.2' if chat_temperature else 'runtime-mvp/1.1'
    policy_raw, policy_sha = _sha_json({'schema_version': policy_schema,
        'policy_id': 'dev1-k3d-gateway', 'policy_version': '1',
        'target_id': target_id, 'allowed_models': [SERVED], 'output_default_tokens': 8, 'output_max_tokens': 16,
        'max_body_bytes': 65536, 'max_context_tokens': 512, 'valid_from': '2026-01-01T00:00:00+00:00',
        'valid_until': '2027-12-31T23:59:59+00:00', 'approval_ref': 'DEV1-K3D-EXAMPLE',
        # REQ-13 Gateway 대상: 원 실행기의 고정 요청은 temperature 0을 보낸다. EXAMPLE 정책으로 chat의 0만 허용한다.
        **({'extra_fields': {'temperature': {'mode': 'validate', 'type': 'number', 'min': 0, 'max': 0}}} if chat_temperature else {})})
    approvals_raw, approvals_sha = _sha_json({'schema_version': 'runtime-policy-approvals/1.0', 'entries': [{'policy_id': 'dev1-k3d-gateway',
        'policy_version': '1', 'sha256': policy_sha, 'approval_ref': 'DEV1-K3D-EXAMPLE', 'approved_at': '2026-11-04T00:00:00+00:00',
        'valid_until': '2027-12-31T23:59:59+00:00'}]})
    target_raw, target_sha = _sha_json({'schema_version': 'runtime-target/1.0', 'target_id': target_id, 'target_ref': 'DEV1-K3D-' + run_id,
        'engine': {'kind': 'vllm', 'version': '0.31.0'}, 'model': {'id': SERVED}, 'baseline_ref': None})
    bundle_raw, _ = _sha_json({'schema_version': 'byul-policy-bundle/0.1', 'bundle_id': 'bundle-dev1-k3d-' + run_id,
        'created_at': '2026-11-04T00:00:00+00:00', 'policy_ref': {'policy_id': 'dev1-k3d-gateway', 'policy_version': '1', 'sha256': policy_sha},
        'target_ref': target_id, 'approval': {'approved_by': 'dev1-lab-owner-EXAMPLE', 'approved_at': '2026-11-04T00:00:00+00:00',
        'approval_ref': 'DEV1-K3D-EXAMPLE'}, 'validity': {'valid_from': '2026-01-01T00:00:00+00:00', 'valid_until': '2027-12-31T23:59:59+00:00'},
        'files': [{'role': 'gateway_policy', 'path': 'runtime-policy.json', 'schema_version': policy_schema, 'sha256': policy_sha, 'bytes': len(policy_raw)},
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
    gpu = record.get('gpu')
    head = 'GPU, Linux amd64, Kubernetes GPU lab; ' if gpu else 'CPU, arm64, k3d local Docker Desktop macOS; '
    platform = (head + '; '.join(f'{k}={v}' for k, v in (
        ('generation', record['generation']), ('run', record['run_id']), ('render_engine_sha256', record['outputs_sha256']['engine.yaml']))))
    # GPU 이름·드라이버는 운영자가 넘긴 값이라 DECLARED다. 측정값으로 올리지 않는다.
    return Environment(id=new_id('ENV'), data_origin=DataOrigin.DECLARED if gpu else DataOrigin.EXAMPLE,
        engine={'name': 'vllm', 'version': '0.31.0', 'image_digest': record['engine_image']},
        adapter={'name': 'vllm', 'version': VllmAdapter().version},
        runtime={'nodes': 1, 'tp_groups': 1, 'gpu_count': 1 if gpu else 0, 'gpu_model': gpu and gpu['model'],
                 'driver': gpu and gpu.get('driver'), 'platform': platform},
        model={'id': record['model_id'], 'revision': record['model_revision'], 'inventory_sha256': inventory_sha256}).seal()


def target(env, record, namespace, engine_pod, probe_pod, *, context):
    value = inputs.target(env, record, namespace, engine_pod, probe_pod, context=context, api_model=record['served_model_name'])
    container, = (c for c in engine_pod['spec']['containers'] if c['name'] == 'engine')
    value['engine']['command'] = ['vllm', 'serve']
    value['observed_version'] = env.engine.version
    # 이 lab은 AWS 인계의 /models snapshot 대신 /srv/models 아래 opt-125m 하위 폴더를 마운트한다.
    value['model_mapping'].update(host_snapshot='/srv/models/opt-125m', container_mount='/models/opt-125m')
    # OBS-09: 실제 Pod 인자와 검토된 로그 계약. 검토되지 않은 이미지면 계약은 None이고 실행기가 공백으로 남긴다.
    args = list(container['args'])
    value['comparison_config'] = {'launch': {'argv': args,
        'profile': 'gpu-bounded-v1' if record.get('profile') == 'gpu' else 'cpu-bounded-v1',
        'max_model_len': int(args[args.index('--max-model-len') + 1]),
        'chat_template': {'profile': CHAT_TEMPLATE_PROFILE, 'version': '1', 'sha256': CHAT_TEMPLATE_SHA256,
            'container_path': CHAT_TEMPLATE_CONTAINER_PATH, 'content_format': 'string', 'request_overrides': False},
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


def usage_inputs(base, env, sink_pod, *, check, root, minutes=20):
    """OBS-04(금지)·OBS-05(허용): 소유 수신 Pod target·EXAMPLE 정책·실행 설정. 엔진은 같은 run의 usage 설정으로 새로 기동한 것이다."""
    from datetime import datetime, timedelta, timezone
    from guard.runtime.kubernetes_binding import validate_container_binding
    if check not in {'OBS-04', 'OBS-05'}:
        raise ValueError('K3D_USAGE_CHECK_INVALID')
    meta, spec, status = sink_pod['metadata'], sink_pod['spec'], sink_pod['status']
    state, = (c for c in status['containerStatuses'] if c['name'] == 'sink')
    image, = (c['image'] for c in spec['containers'] if c['name'] == 'sink')
    binding = {'namespace': meta['namespace'], 'namespace_uid': base['kubernetes']['engine']['namespace_uid'],
               'pod': meta['name'], 'pod_uid': meta['uid'], 'container': 'sink', 'container_id': state['containerID'],
               'image': image, 'image_id': state['imageID'], 'restart_count': state['restartCount'], 'node': spec['nodeName'],
               'pod_ip': status['podIP'], 'started_at': state['state']['running']['startedAt']}
    validate_container_binding(binding)
    sink_target = {'schema': 'guard-kubernetes-lifecycle-target/1', 'run_id': base['kubernetes']['run_id'],
                   'environment_ref': base['environment_ref'], 'lab': True,
                   'kubernetes': {'context': base['kubernetes']['context'], 'engine': binding}}
    values = {'usage_allowed': check == 'OBS-05', 'usage_report_paths': ['/root/.config/vllm/usage_stats.json'],
              'usage_settings_fresh_process': True}
    if check == 'OBS-05':
        # EXAMPLE 허용 범위: 소유 수신지 하나만. 허용 필드는 기업이 정한다. 비워 두면 어떤 필드든 범위 밖으로 남는다.
        values.update(allowed_usage_fields=[], allowed_usage_sinks=[f"usage-sink-{base['kubernetes']['run_id']}.{NAMESPACE}.svc:{USAGE_SINK_PORT}"])
    root = Path(root).absolute()
    pack = yaml.safe_load((Path(__file__).parents[1] / 'docs/gpu-a/policy-a-draft.yaml').read_text(encoding='utf-8'))['pack']
    policy = {'data_origin': 'EXAMPLE', 'status': 'DRAFT', 'pack': pack, 'values': {check: values},
              'scope': {'include': [check], 'exclude': []}, 'exceptions': [], 'confirmed_by': None, 'confirmed_at': None}
    config = '\n'.join([f"store = '{root / 'store'}'", '', '[adapter]', "name = 'vllm'", '', '[executors]', "boundary_egress = 'kubernetes'", '',
                        '[boundary_egress]', f"target = '{root / 'target.json'}'", f"sink_target = '{root / 'sink-target.json'}'",
                        "kubectl_binary = 'kubectl'", 'max_duration_seconds = 120', 'usage_wait_seconds = 5', ''])
    return sink_target, policy, config


def obs01_inputs(route_target, env, gateway_config, *, session, root, minutes=10):
    """OBS-01 Gateway 경로: 파생 DRAFT 팩(PACK32의 OBS-01 수집 프로필만 교체)·EXAMPLE 정책·실행 설정."""
    from datetime import datetime, timedelta, timezone
    from guard.executors.kubernetes_gateway_observation import principal_sha256
    from tools import build_dev1_gateway_obs01_pack as pack_tool
    if not re.fullmatch(r'[0-9a-f]{32}', session):
        raise ValueError('K3D_OBS01_SESSION_INVALID')
    data = yaml.safe_load(gateway_config)['data']
    policy_sha = hashlib.sha256(data['runtime-policy.json'].encode()).hexdigest()
    gateway_target = json.loads(data['target.json'])['target_id']
    root = Path(root).absolute()
    result = pack_tool.derive(root / 'pack')
    now = datetime.now(timezone.utc)
    values = {'observation_environment_sha256': env.ref().sha256, 'observation_target_sha256': sha256_hex(canonical_json(route_target)),
              'observation_validity_start': now.isoformat(), 'observation_validity_end': (now + timedelta(minutes=minutes)).isoformat(),
              'observation_max_delay_ms': 5000, 'observation_required_events': ['ACCESS_ALLOWED', 'ACCESS_DENIED'],
              'observation_required_fields': ['actor_sha256', 'target_sha256', 'run_sha256', 'policy_sha256', 'request_sha256', 'result_sha256'],
              'observation_session': session,
              'observation_expected_source_sha256': hashlib.sha256(route_target['kubernetes_routes']['gateway']['image'].encode()).hexdigest(),
              # EXAMPLE: Gateway에 실제로 적재된 정책 bytes의 SHA. 기업 승인 정책이 아니다.
              'observation_event_policy_sha256': policy_sha,
              'observation_allowed_actor_sha256': principal_sha256('dev1-general'),
              'observation_denied_actor_sha256': principal_sha256('dev1-observer'),
              'observation_gateway_target_id': gateway_target}
    policy = {'data_origin': 'EXAMPLE', 'status': 'DRAFT', 'pack': {'id': result['pack_ref']['id'], 'revision': result['pack_ref']['revision']},
              'values': {'OBS-01': values}, 'scope': {'include': ['OBS-01'], 'exclude': []}, 'exceptions': [],
              'confirmed_by': None, 'confirmed_at': None}
    config = '\n'.join([f"store = '{root / 'store'}'", '', '[adapter]', "name = 'vllm'", '', '[executors]', "required_event_record = 'kubernetes'", '',
                        '[required_event_record]', f"target = '{root / 'target.json'}'", "kubectl_binary = 'kubectl'", 'max_duration_seconds = 120',
                        "roles = {allowed = 'DEV1_TOKEN_GENERAL', denied = 'DEV1_TOKEN_OBSERVER'}", ''])
    return policy, config, result


def http_check(check_id, root, route_target=None):
    """route_target: REQ-13 Gateway 대상. 4-Pod 경로 target과 허용 주체 토큰 환경변수 이름을 실행 설정에 더한다."""
    executor, settings = EXECUTORS[check_id]
    root = Path(root).absolute()
    values = deepcopy(VALUES[check_id])
    if route_target is not None:
        if check_id != 'REQ-13':
            raise ValueError('K3D_ROUTE_TARGET_UNSUPPORTED')
        values['required_targets'] = ['engine', 'gateway']
        settings = {**settings, 'route_target': str(Path(route_target).absolute()), 'allowed_key_env': 'DEV1_TOKEN_GENERAL'}
    # 팩 Ref는 기존 GPU 실측 정책 초안과 같은 PACK-BASELINE@32를 쓴다.
    pack = yaml.safe_load((Path(__file__).parents[1] / 'docs/gpu-a/policy-a-draft.yaml').read_text(encoding='utf-8'))['pack']
    policy = {'data_origin': 'EXAMPLE', 'status': 'DRAFT', 'pack': pack, 'values': {check_id: values},
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
    parser.add_argument('--profile', choices=['cpu', 'gpu'], default='cpu',
                        help='gpu: 2단계 GPU lab. 이름공간·세대·별칭을 바꾼다. 같은 run의 모든 하위 명령에 같은 값을 준다.')
    sub = parser.add_subparsers(dest='kind', required=True)
    r = sub.add_parser('render'); r.add_argument('--run-id', required=True); r.add_argument('--engine-image', required=True)
    r.add_argument('--restart-always', action='store_true'); r.add_argument('--engine-callbacks'); r.add_argument('--dev-endpoints', action='store_true'); r.add_argument('--profiler', action='store_true'); r.add_argument('--gpu-model'); r.add_argument('--gpu-driver'); r.add_argument('--runtime-class')
    r.add_argument('--lifecycle', type=Path); r.add_argument('--api-key-secret'); r.add_argument('--usage', choices=['forbidden', 'allowed'])
    r.add_argument('--out', type=Path, required=True)
    e = sub.add_parser('env'); e.add_argument('--render', type=Path, required=True); e.add_argument('--models', type=Path, required=True)
    e.add_argument('--out', type=Path, required=True)
    t = sub.add_parser('target'); t.add_argument('--env', type=Path, required=True); t.add_argument('--render', type=Path, required=True)
    for name in ('namespace', 'engine-pod', 'probe-pod'):
        t.add_argument('--' + name, type=Path, required=True)
    t.add_argument('--context', default='k3d-byul-cpu'); t.add_argument('--out', type=Path, required=True)
    w = sub.add_parser('gateway'); w.add_argument('--run-id', required=True); w.add_argument('--gateway-image', required=True)
    w.add_argument('--out', type=Path, required=True); w.add_argument('--chat-temperature', action='store_true')
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
    us = sub.add_parser('usage'); us.add_argument('--check', required=True, choices=['OBS-04', 'OBS-05'])
    us.add_argument('--target', type=Path, required=True); us.add_argument('--env', type=Path, required=True)
    us.add_argument('--sink-pod', type=Path, required=True); us.add_argument('--out', type=Path, required=True)
    ob = sub.add_parser('obs01'); ob.add_argument('--route-target', type=Path, required=True); ob.add_argument('--env', type=Path, required=True)
    ob.add_argument('--gateway-config', type=Path, required=True); ob.add_argument('--session', required=True); ob.add_argument('--out', type=Path, required=True)
    h = sub.add_parser('http-check'); h.add_argument('--check', required=True, choices=sorted(EXECUTORS))
    h.add_argument('--target', type=Path, required=True); h.add_argument('--out', type=Path, required=True)
    h.add_argument('--route-target', type=Path)
    args = parser.parse_args(argv)
    if args.profile == 'gpu':
        use_gpu_profile()
    load = lambda path: json.loads(Path(path).read_text(encoding='utf-8'))
    if args.kind == 'render':
        raw, record = render(run_id=args.run_id, engine_image=args.engine_image, lifecycle_dir=args.lifecycle, api_key_secret=args.api_key_secret, usage=args.usage, restart_always=args.restart_always, engine_callbacks=args.engine_callbacks, dev_endpoints=args.dev_endpoints, profiler=args.profiler,
            gpu=({k: v for k, v in (('model', args.gpu_model), ('driver', args.gpu_driver), ('runtime_class', args.runtime_class)) if v}
                 if args.profile == 'gpu' else None))
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
        for name, data in gateway_lab(run_id=args.run_id, gateway_image=args.gateway_image, tokens=tokens, chat_temperature=args.chat_temperature).items():
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
    elif args.kind == 'usage':
        sink_target, policy, config = usage_inputs(load(args.target), load_environment(args.env), load(args.sink_pod), check=args.check, root=args.out)
        inputs._write(args.out / 'target.json', load(args.target)); inputs._write(args.out / 'sink-target.json', sink_target)
        inputs._write(args.out / 'policy.yaml', yaml.safe_dump(policy, sort_keys=False, allow_unicode=True))
        inputs._write(args.out / 'guard.toml', config)
        print(json.dumps({'check': args.check, 'out': str(args.out)}))
    elif args.kind == 'obs01':
        policy, config, result = obs01_inputs(load(args.route_target), load_environment(args.env), args.gateway_config.read_text(encoding='utf-8'),
                                              session=args.session, root=args.out)
        inputs._write(args.out / 'target.json', load(args.route_target))
        inputs._write(args.out / 'policy.yaml', yaml.safe_dump(policy, sort_keys=False, allow_unicode=True))
        inputs._write(args.out / 'guard.toml', config)
        print(json.dumps({'check': 'OBS-01', 'out': str(args.out), 'pack': result['pack_ref']}))
    else:
        policy, config = http_check(args.check, args.out, route_target=args.route_target)
        inputs._write(args.out / 'target.json', load(args.target))
        inputs._write(args.out / 'policy.yaml', yaml.safe_dump(policy, sort_keys=False, allow_unicode=True))
        inputs._write(args.out / 'guard.toml', config)
        print(json.dumps({'check': args.check, 'executor': EXECUTORS[args.check][0], 'out': str(args.out)}))


if __name__ == '__main__':
    main()
