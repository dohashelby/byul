"""소유 실험 환경 전용 능동 활성화. 실제 장애·전환을 일으키고 부모가 직접 측정한다.

수동 수집기는 연결된 실행기가 그대로 수행한다. 이 모듈은 그 뒤에 점검의 챌린지를
실행해 activation_* 관측을 더한다. 네임스페이스에 소유 표식이 없으면 어떤 변경도
보내지 않는다. 측정하지 못한 분기는 missing에 남고 위반으로 바꾸지 않는다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import ipaddress
import json
from pathlib import Path
import posixpath
import re
import time
import uuid
from urllib.parse import urlsplit

import yaml

from guard.models import DataOrigin, ExecStatus, ORIGIN_STRENGTH, canonical_json, utcnow
from guard.models.objects import Observation
from guard.runtime.bounded import capture_pipes, strict_json

from .base import DraftArtifact, EvidenceDraft

OWNED_LABEL = 'byul.lab/owned'
OBSERVATIONS = {'activation_steps': 'list', 'activation_violations': 'list', 'activation_missing': 'list',
    'activation_scope_complete': 'bool'}
CONFIG_KEYS = {'namespace', 'selector', 'container', 'probe_pod', 'probe_container', 'service_url', 'model', 'kubectl_binary',
    'kubectl_context', 'deployment', 'process_pattern', 'deadline_ms', 'burst_requests', 'faulty_manifest', 'good_manifest',
    'update_manifest', 'allowed_models', 'trace_directory', 'fault_directory', 'max_duration_seconds',
    'generation_models', 'change_path', 'failure_marker', 'restore_objects', 'tampered_objects',
    'worker_pod', 'worker_pid', 'worker_start_ticks', 'worker_rank', 'worker_rank_regex',
    'probe_progress_directory', 'good_manifest_sha256', 'update_manifest_sha256'}
# 해시로 비교할 수 있는 복원 대상. volume·snapshot 내용은 이 실행기가 읽지 않는다.
RESTORE_KINDS = frozenset({'configmap', 'secret'})
REQUIRED_KEYS = {'namespace', 'selector', 'container', 'probe_pod', 'probe_container', 'service_url', 'model',
    'deployment', 'process_pattern', 'deadline_ms'}
POLL_SECONDS = 0.5
KUBECTL_CALL_TIMEOUT = 60
REQUEST_CALL_TIMEOUT = 120
FAULT_FILE = 'lab-fault-exception'
# Signals are sent by a Python helper inside the Linux engine container.
# They must not depend on the controller host's signal module (e.g. Windows).
POSIX_SIGKILL, POSIX_SIGSTOP, POSIX_SIGCONT = 9, 19, 18

CLIENT = r'''
import concurrent.futures as f,hashlib,http.client,json,os,re,sys,time
from urllib.parse import urlsplit
spec=json.load(sys.stdin);base=urlsplit(spec["url"])
def one(item):
    began=time.monotonic();row=dict(outcome="error",elapsed_ms=None,sha256=None,model=None,closed_early=False,complete=False,
        first_chunk_elapsed_ms=None)
    connection=http.client.HTTPConnection(base.hostname,base.port or 80,timeout=item.get("timeout",30))
    try:
        body=item.get("body");raw=None if body is None else json.dumps(body).encode()
        headers=dict(item.get("headers",{}))
        if raw is not None:headers["Content-Type"]="application/json"
        connection.request(item["method"],item["path"],raw,headers)
        response=connection.getresponse();row["outcome"]=response.status
        digest=hashlib.sha256();got=0;close_after=item.get("close_after_bytes");delay=item.get("read_delay_ms",0)/1000
        text=b""
        while True:
            part=response.read(256)
            if not part:row["complete"]=True;break
            if row["first_chunk_elapsed_ms"] is None:
                row["first_chunk_elapsed_ms"]=int((time.monotonic()-began)*1000)
                if spec.get("progress_path"):
                    try:
                        fd=os.open(spec["progress_path"],os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
                        with os.fdopen(fd,"wb") as stream:stream.write(b"first-body")
                    except OSError:pass
            digest.update(part);got+=len(part)
            if len(text)<65536:text+=part
            if delay:time.sleep(delay)
            if close_after is not None and got>=close_after:row["closed_early"]=True;break
        row["sha256"]=digest.hexdigest();row["bytes"]=got
        try:
            row["models"]=sorted({m.decode("utf-8","replace") for m in re.findall(rb'"model":\s*"([^"]{1,200})"',text)})
            document=json.loads(text);row["model"]=document.get("model")
            row["answer"]=hashlib.sha256(document["choices"][0]["message"]["content"].encode()).hexdigest()
        except Exception:pass
        if item.get("metrics"):row["metrics"]=text.decode("utf-8","replace")
    except (OSError,http.client.HTTPException) as error:
        if row["outcome"]=="error" or isinstance(error,TimeoutError):
            row["outcome"]="timeout" if isinstance(error,TimeoutError) else row["outcome"]
    finally:
        connection.close()
    row["elapsed_ms"]=int((time.monotonic()-began)*1000);return row
with f.ThreadPoolExecutor(max_workers=max(1,len(spec["requests"]))) as pool:
    print(json.dumps(list(pool.map(one,spec["requests"]))))
'''

CENSUS = r'''
import json,os,re,sys
pattern=re.compile(sys.argv[1]);found=[];errors=[]
def problem(code,pid=None):
    row={'code':code}
    if pid is not None:row['pid']=pid
    errors.append(row)
def enumerate_pids():
    try:return {name for name in os.listdir('/proc') if name.isdigit() and int(name)!=os.getpid()}
    except OSError:
        problem('PROC_ENUMERATION_UNAVAILABLE');return None
def process_stat(name):
    with open(f'/proc/{name}/stat') as stream:raw=stream.read()
    fields=raw[raw.rfind(')')+2:].split()
    return fields[0],int(fields[19]),int(fields[21])
before=enumerate_pids()
for name in sorted(before or (),key=int):
    try:
        state,start,rss=process_stat(name)
        with open(f'/proc/{name}/cmdline','rb') as stream:raw=stream.read(4097)
        with open(f'/proc/{name}/comm') as stream:comm=stream.read().strip()
        after_state,after_start,after_rss=process_stat(name)
        if start!=after_start:
            problem('PID_IDENTITY_CHANGED_DURING_READ',int(name));continue
        if len(raw)>4096:problem('PID_CMDLINE_TRUNCATED',int(name))
        command=raw[:4096].replace(b'\0',b' ').decode('utf-8','replace')
    except FileNotFoundError:
        problem('PID_DISAPPEARED_DURING_READ',int(name));continue
    except OSError:
        problem('PID_READ_UNAVAILABLE',int(name));continue
    except (ValueError,IndexError,UnicodeError):
        problem('PID_METADATA_INVALID',int(name));continue
    if pattern.search(command) or pattern.search(comm):
        found.append(dict(pid=int(name),state=after_state,rss_pages=after_rss,start_ticks=after_start,
            command=command,comm=comm))
after=enumerate_pids()
if before is not None and after is not None and before!=after:problem('PROC_SCOPE_CHANGED_DURING_READ')
try:shm=sorted(os.listdir('/dev/shm'))
except OSError:shm=None
print(json.dumps(dict(processes=found,process_scope_complete=not errors,
    process_scope_errors=errors,process_count_before=None if before is None else len(before),
    process_count_after=None if after is None else len(after),
    shm_entries=None if shm is None else len(shm),shm_names=shm,page_bytes=os.sysconf('SC_PAGE_SIZE'))))
'''


SIGNAL = """import os,sys
pid,number,start=map(int,sys.argv[1:4])
with open(f'/proc/{pid}/stat') as stream: stat=stream.read()
if int(stat[stat.rfind(')')+2:].split()[19])!=start: raise RuntimeError('WORKER_GENERATION_CHANGED')
os.kill(pid,number)
print('{}')
"""
FILE_COUNT = "import json,os,sys\nprint(json.dumps(dict(count=sum(len(f) for _,_,f in os.walk(sys.argv[1])))))\n"
TOUCH = "import pathlib,sys\npathlib.Path(sys.argv[1]).write_text('1')\nprint('{}')\n"
PROGRESS = """import json,os,stat,sys
path=sys.argv[1];action=sys.argv[2]
try:
    value=os.lstat(path)
    valid=stat.S_ISDIR(value.st_mode) if action=='directory' else stat.S_ISREG(value.st_mode)
except OSError: valid=False
if action=='remove' and valid: os.unlink(path)
print(json.dumps({'valid':valid}))
"""


class CurrentEngineMarkerChanged(ValueError):
    """A valid current incarnation changed while its logs were being read."""


class ActivationRefused(ValueError):
    pass


class Lab:
    """kubectl 호출과 측정 기록. 소유 표식을 확인한 뒤에만 변경 명령을 보낸다."""

    def __init__(self, cfg, *, runner=None, clock=None, wait=None):
        if type(cfg) is not dict or set(cfg) - CONFIG_KEYS or not REQUIRED_KEYS <= set(cfg):
            raise ActivationRefused('LAB_ACTIVATION_CONFIG_INVALID')
        if type(cfg['deadline_ms']) is not int or not 0 < cfg['deadline_ms'] <= 600000:
            raise ActivationRefused('LAB_ACTIVATION_DEADLINE_INVALID')
        self.cfg = cfg; self.injected = runner is not None
        self.runner = runner or capture_pipes; self.clock = clock or time.monotonic; self.wait = wait or time.sleep
        self.binary = cfg.get('kubectl_binary', 'kubectl')
        self.duration = cfg.get('max_duration_seconds', 600)
        self.deadline = self.clock() + self.duration
        self.steps = []; self.violations = []; self.missing = []; self.mutated = False; self.normal = []
        self.cleanup_errors = []; self.fault_restore_failed = False
        self.restore_model = cfg['model']
        # Diagnostic records never participate in activation predicates.
        self.diagnostics = []; self.diagnostic_write_errors = []; self.diagnostic_namespace = None
        self.diagnostic_poll = None; self.diagnostic_deadline = None
        self.diagnostic_idle_overrun = False
        self.observation_deadline = None

    def diagnostic(self, event, **facts):
        self.diagnostics.append({'poll': self.diagnostic_poll, 'event': event,
            'recorded_monotonic': self.clock(), **facts})

    def diagnostic_stream_metadata(self, raw):
        return {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}

    def write_diagnostics(self, workdir):
        artifacts = []
        if self.diagnostics:
            try:
                path = workdir / 'lab-diagnostics.json'
                path.write_bytes(canonical_json({'diagnostic_only': True, 'metadata_only': True,
                    'records': self.diagnostics}))
                artifacts.append(DraftArtifact(name=path.name, path=path, collector='lab_activation/0.1.0'))
            except (OSError, ValueError) as error:
                self.diagnostic_write_errors.append({'reason': _diagnostic_error_kind(error)})
        return artifacts

    def _kubectl(self, *args, stdin=None, cap=2 * 1024 ** 2, timeout=KUBECTL_CALL_TIMEOUT):
        remaining = min(timeout, self.deadline - self.clock())
        if self.observation_deadline is not None:
            remaining = min(remaining, self.observation_deadline - self.clock())
        if self.diagnostic_deadline is not None:
            remaining = min(remaining, self.diagnostic_deadline - self.clock())
        timeout_ns = int(remaining * 1_000_000_000)
        if timeout_ns <= 0:
            raise TimeoutError('LAB_ACTIVATION_BUDGET_EXHAUSTED')
        context = [f"--context={self.cfg['kubectl_context']}"] if 'kubectl_context' in self.cfg else []
        options = {'input_bytes': stdin} if stdin is not None else {}
        try:
            result = self.runner([self.binary, *context, f"--namespace={self.cfg['namespace']}", f'--request-timeout={timeout_ns}ns', *args],
                timeout=remaining, env_extra={}, max_stdout_bytes=cap, max_stderr_bytes=4096, **options)
        except (OSError, TimeoutError, ValueError) as error:
            if self.diagnostic_poll is not None and (args[0] == 'logs' or CLIENT in args):
                self.diagnostic('marker-command-error' if args[0] == 'logs' else 'api-command-error', error=_diagnostic_error_kind(error),
                    container='current' if f"--container={self.cfg['container']}" in args else 'diagnostic',
                    previous='--previous' in args)
            raise
        if self.diagnostic_poll is not None and (args[0] == 'logs' or CLIENT in args):
            self.diagnostic('marker-command' if args[0] == 'logs' else 'api-command', returncode=_diagnostic_int(result['returncode'], signed=True),
                capture=_diagnostic_capture_metadata(result.get('capture')),
                container='current' if f"--container={self.cfg['container']}" in args else 'diagnostic',
                previous='--previous' in args,
                stdout=self.diagnostic_stream_metadata(result['stdout_bytes']),
                stderr=self.diagnostic_stream_metadata(result.get('stderr_bytes', b'')))
        if self.diagnostic_poll is not None and args[:2] in (('get', 'pod'), ('get', 'pods'), ('get', 'namespace'), ('get', 'service')):
            self.diagnostic('object-api-receipt', object_kind=args[1], returncode=_diagnostic_int(result['returncode'], signed=True),
                capture=_diagnostic_capture_metadata(result.get('capture')),
                raw_sha256=hashlib.sha256(result['stdout_bytes']).hexdigest(),
                raw_bytes=len(result['stdout_bytes']),
                stderr=self.diagnostic_stream_metadata(result.get('stderr_bytes', b'')))
        if self.diagnostic_deadline is not None and not _diagnostic_capture_complete(result):
            raise OSError('LAB_DIAGNOSTIC_CAPTURE_INCOMPLETE')
        if result['returncode'] != 0:
            raise OSError('LAB_ACTIVATION_KUBECTL_FAILED')
        return result['stdout_bytes']

    def verify_owned(self):
        raw = self._kubectl('get', 'namespace', self.cfg['namespace'], '-o', 'json')
        namespace = strict_json(raw, limit=262144)
        if namespace.get('metadata', {}).get('labels', {}).get(OWNED_LABEL) != 'true':
            raise ActivationRefused('LAB_ACTIVATION_NAMESPACE_NOT_OWNED')
        self.diagnostic_namespace = _diagnostic_namespace_identity(namespace, self.cfg['namespace'])

    def _pods(self):
        raw = self._kubectl('get', 'pods', '-l', self.cfg['selector'], '-o', 'json')
        return [p for p in strict_json(raw, limit=2 * 1024 ** 2).get('items', []) if not p['metadata'].get('deletionTimestamp')]

    def ready_pods(self):
        def ready(pod):
            return any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in pod.get('status', {}).get('conditions', []))
        return [p['metadata']['name'] for p in self._pods() if ready(p)]

    def _exec(self, program, *args, stdin=None, pod=None, pod_uid=None, isolated=False):
        if pod is None:
            pods = self.ready_pods()
            if not pods:
                raise OSError('LAB_ACTIVATION_NO_READY_POD')
            pod = pods[0]
        else:
            identities = {p['metadata']['name']: p['metadata'].get('uid') for p in self._pods()}
            if pod not in identities or pod_uid is not None and identities[pod] != pod_uid:
                raise OSError('LAB_ACTIVATION_TARGET_POD_GENERATION_CHANGED')
        flags = []
        if isolated:
            if program != SENSOR_SESSION:
                raise ValueError('LAB_ACTIVATION_ISOLATED_PROGRAM_INVALID')
            flags = ['-I', '-S', '-B']
            remaining = min(KUBECTL_CALL_TIMEOUT, self.deadline - self.clock())
            if self.observation_deadline is not None:
                remaining = min(remaining, self.observation_deadline - self.clock())
            if remaining <= 0:
                raise TimeoutError('LAB_ACTIVATION_BUDGET_EXHAUSTED')
            args = (*args, str(remaining))
        return json.loads(self._kubectl('exec', pod, f"--container={self.cfg['container']}", '--',
            'python3', *flags, '-c', program, *args))

    def requests(self, items, *, timeout=REQUEST_CALL_TIMEOUT, progress_path=None, url=None):
        payload = json.dumps({'url': url or self.cfg['service_url'], 'requests': items,
            'progress_path': progress_path}).encode()
        raw = self._kubectl('exec', '-i', self.cfg['probe_pod'], f"--container={self.cfg['probe_container']}", '--',
            'python3', '-c', CLIENT, stdin=payload, timeout=timeout, cap=8 * 1024 ** 2)
        return json.loads(raw)

    def probe_progress(self, path, action):
        raw = self._kubectl('exec', self.cfg['probe_pod'], f"--container={self.cfg['probe_container']}", '--',
            'python3', '-c', PROGRESS, path, action)
        return json.loads(raw)['valid'] is True

    def chat(self, prompt='ping', **options):
        body = {'model': options.pop('model', self.cfg['model']), 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': options.pop('max_tokens', 1),
            'temperature': 0, 'stream': options.pop('stream', False)}
        return {'method': 'POST', 'path': '/v1/chat/completions', 'body': body, **options}

    def normal_request(self, label, *, model=None, url=None):
        try:
            row, = self.requests([self.chat(timeout=self.cfg['deadline_ms'] / 1000,
                model=model or self.cfg['model'])], **({'url': url} if url else {}))
        except (OSError, TimeoutError, ValueError):
            row = {'outcome': 'error'}
        good = row.get('outcome') == 200 and row.get('complete') is True
        self.normal.append(good); self.note(label, outcome=row.get('outcome'), succeeded=good)
        return row if good else None

    def metric(self, name):
        row, = self.requests([{'method': 'GET', 'path': '/metrics', 'metrics': True, 'timeout': 10}])
        found = re.findall(rf'^{re.escape(name)}(?:{{[^}}]*}})? ([0-9.e+-]+)$', row.get('metrics', ''), re.M)
        return sum(float(value) for value in found) if found else None

    def census(self, pod=None, pod_uid=None):
        return self._exec(CENSUS, self.cfg['process_pattern'], pod=pod, pod_uid=pod_uid)

    def rss_bytes(self):
        value = self.census()
        return sum(p['rss_pages'] for p in value['processes']) * value['page_bytes']

    def selected_worker(self):
        pods = self.ready_pods()
        if 'worker_pod' in self.cfg:
            pods = [pod for pod in pods if pod == self.cfg['worker_pod']]
        if len(pods) != 1:
            raise ActivationRefused('LAB_ACTIVATION_WORKER_POD_AMBIGUOUS')
        pod = pods[0]
        pod_uid = next((p['metadata'].get('uid') for p in self._pods() if p['metadata']['name'] == pod), None)
        if not pod_uid:
            raise ActivationRefused('LAB_ACTIVATION_WORKER_POD_UID_UNAVAILABLE')
        processes = {p['pid']: p for p in self.census(pod, pod_uid)['processes']}
        selected = list(processes.values())
        if 'worker_pid' in self.cfg:
            selected = [p for p in selected if p['pid'] == self.cfg['worker_pid']]
        if 'worker_start_ticks' in self.cfg:
            selected = [p for p in selected if p.get('start_ticks') == self.cfg['worker_start_ticks']]
        if 'worker_rank' in self.cfg:
            expression = self.cfg.get('worker_rank_regex')
            if not expression:
                raise ActivationRefused('LAB_ACTIVATION_WORKER_RANK_REGEX_REQUIRED')
            try:
                rank = re.compile(expression)
                selected = [p for p in selected if (match := rank.search(p.get('command', '') + ' ' + p.get('comm', '')))
                    and match.group(1) == str(self.cfg['worker_rank'])]
            except (re.error, IndexError) as error:
                raise ActivationRefused('LAB_ACTIVATION_WORKER_RANK_REGEX_INVALID') from error
        if len(selected) != 1 or not isinstance(selected[0].get('start_ticks'), int):
            raise ActivationRefused('LAB_ACTIVATION_WORKER_IDENTITY_AMBIGUOUS')
        return {'pod': pod, 'pod_uid': pod_uid, **{key: selected[0][key] for key in ('pid', 'start_ticks', 'state')}}

    def signal_worker(self, number, target=None):
        target = target or self.selected_worker()
        current = {p['pid']: p for p in self.census(target['pod'], target['pod_uid'])['processes']}.get(target['pid'])
        if current is None or current.get('start_ticks') != target['start_ticks']:
            raise OSError('LAB_ACTIVATION_WORKER_GENERATION_CHANGED')
        self.mutated = True
        self._exec(SIGNAL, str(target['pid']), str(int(number)), str(target['start_ticks']),
            pod=target['pod'], pod_uid=target['pod_uid'])
        return target

    def touch_fault(self, target):
        if 'fault_directory' not in self.cfg:
            return False
        self.mutated = True
        self._exec(TOUCH, self.cfg['fault_directory'] + '/' + FAULT_FILE,
            pod=target['pod'], pod_uid=target['pod_uid'])
        return True

    def rollout(self):
        self.mutated = True
        self._kubectl('rollout', 'restart', f"deployment/{self.cfg['deployment']}")

    def apply(self, name):
        raw = Path(self.cfg[name]).read_bytes()
        pin_key = name + '_sha256'
        if pin_key in self.cfg:
            expected = self.cfg[pin_key]
            if (type(expected) is not str or not re.fullmatch('[0-9a-f]{64}', expected)
                    or hashlib.sha256(raw).hexdigest() != expected):
                raise ValueError('LAB_ACTIVATION_GOOD_MANIFEST_CHANGED' if name == 'good_manifest'
                    else 'LAB_ACTIVATION_UPDATE_MANIFEST_CHANGED')
        self.mutated = True
        self._kubectl('apply', '-f', '-', stdin=raw)

    def object_digests(self, names):
        """복원 대상의 내용 해시. 값 자체는 남기지 않는다."""
        found = {}
        for name in names:
            kind, _, item = name.partition('/')
            if kind not in RESTORE_KINDS or not item:
                self.miss('RESTORE_OBJECT_KIND_UNSUPPORTED:' + kind); continue
            raw = strict_json(self._kubectl('get', kind, item, '-o', 'json'), limit=2 * 1024 ** 2)
            body = {key: raw.get(key) for key in ('data', 'binaryData', 'stringData')}
            found[name] = hashlib.sha256(canonical_json(body)).hexdigest()
        return found

    def references(self, names):
        """Ready Pod의 구성 선언상 참조. 실제 파일 소비 근거는 아니다."""
        wanted = set(names); used = set(); ready = set(self.ready_pods())
        for pod in self._pods():
            if pod['metadata']['name'] not in ready:
                continue
            used.update(_restore_references(pod))
        return sorted(wanted & used)

    def log_contains(self, pod, marker):
        """선언한 한 구성요소의 실패 표식이 로그에 있는지만 본다. 로그 원문은 남기지 않는다."""
        for extra in ((), ('--previous',)):  # 이미 재시작한 컨테이너는 직전 실행의 로그에 남는다
            try:
                if marker.encode() in self._kubectl('logs', pod, '--all-containers', '--tail=2000', *extra, cap=8 * 1024 ** 2):
                    return True
            except OSError:
                continue
        return False

    def engine_container_identity(self, pod, *, require_ready=True):
        statuses = pod.get('status', {}).get('containerStatuses', [])
        status = next((s for s in statuses if s.get('name') == self.cfg['container']), None)
        if status is None or require_ready and status.get('ready') is not True:
            return None
        state = status.get('state') or {}
        kind = 'running' if type(state.get('running')) is dict else (
            'terminated' if type(state.get('terminated')) is dict else None)
        if kind is None or len(state) != 1 or require_ready and kind != 'running':
            return None
        container_id = status.get('containerID')
        restart_count = status.get('restartCount')
        started_at = state[kind].get('startedAt')
        if type(container_id) is not str or not container_id or type(restart_count) is not int or restart_count < 0 \
                or type(started_at) is not str or not started_at:
            return None
        return (kind, container_id, restart_count, started_at)

    def current_engine_marker(self, pod_name, pod_uid, marker):
        """현재 engine 컨테이너 로그만 읽고 전후 신원이 동일할 때만 marker를 결합한다."""
        before = next((p for p in self._pods() if p['metadata'].get('name') == pod_name
            and p['metadata'].get('uid') == pod_uid), None)
        identity = self.engine_container_identity(before, require_ready=False) if before else None
        if self.diagnostic_poll is not None:
            self.diagnostic('marker-before', pod=pod_name, pod_uid=pod_uid,
                identity=identity, snapshot=_diagnostic_pod_identity(before, self.cfg['container'], self.cfg['namespace']))
        if identity is None:
            if self.diagnostic_poll is not None:
                self.diagnostic('marker-unavailable', pod=pod_name, pod_uid=pod_uid,
                    reason='INITIAL_CONTAINER_CREATING' if before is not None
                    and self.initial_engine_container_wait(before) else 'CURRENT_ENGINE_IDENTITY_UNAVAILABLE')
            if before is not None and self.initial_engine_container_wait(before):
                return None, None
            raise CurrentEngineMarkerChanged('CURRENT_ENGINE_IDENTITY_UNAVAILABLE')
        raw = self._kubectl('logs', pod_name, f"--container={self.cfg['container']}", '--tail=2000',
            cap=8 * 1024 ** 2)
        after = next((p for p in self._pods() if p['metadata'].get('name') == pod_name
            and p['metadata'].get('uid') == pod_uid), None)
        if self.diagnostic_poll is not None:
            self.diagnostic('marker-after', pod=pod_name, pod_uid=pod_uid,
                snapshot=_diagnostic_pod_identity(after, self.cfg['container'], self.cfg['namespace']),
                identity=self.engine_container_identity(after, require_ready=False) if after else None,
                marker_found=marker.encode() in raw)
        if after is None or self.engine_container_identity(after, require_ready=False) != identity:
            raise CurrentEngineMarkerChanged('CURRENT_ENGINE_CHANGED_DURING_LOG')
        return marker.encode() in raw, identity

    def initial_engine_container_wait(self, pod):
        """Recognize only an unstarted ContainerCreating status, never a prior incarnation."""
        statuses = pod.get('status', {}).get('containerStatuses', [])
        selected = [s for s in statuses if s.get('name') == self.cfg['container']]
        if len(selected) != 1:
            return False
        status = selected[0]
        return (status.get('state') == {'waiting': {'reason': 'ContainerCreating'}}
            and status.get('ready') is False
            and type(status.get('restartCount')) is int and status['restartCount'] == 0
            and status.get('containerID') in (None, '') and status.get('imageID') in (None, '')
            and status.get('lastState', {}) == {})

    def wait_ready(self):
        end = self.clock() + self.cfg['deadline_ms'] / 1000
        while True:
            try:
                if self.ready_pods():
                    return True
            except (OSError, TimeoutError, ValueError):
                pass
            if self.clock() >= end:
                return False
            self.wait(POLL_SECONDS)

    def wait_idle(self, limit_ms):
        """실행 중 요청 지표가 0이 되는 시간. 지표가 없으면 측정 불가로 남긴다."""
        began = self.clock()
        while True:
            value = self.metric('vllm:num_requests_running')
            if value is None:
                self.miss('RUNNING_REQUEST_METRIC_UNAVAILABLE'); return None
            if value == 0:
                return int((self.clock() - began) * 1000)
            if (self.clock() - began) * 1000 >= limit_ms:
                return False
            self.wait(POLL_SECONDS)

    def note(self, step, **facts):
        self.steps.append({'step': step, **facts})

    def violate(self, code, **facts):
        self.violations.append({'code': code, **facts})

    def miss(self, code):
        if code not in self.missing:
            self.missing.append(code)

    def restore(self):
        """시나리오 뒤 정상 세대로 되돌리고 정상 대조로 확인한다."""
        if 'good_manifest' in self.cfg and self.mutated:
            self.apply('good_manifest')
        elif self.mutated:
            self.rollout()
        if not self.wait_ready():
            self.miss('RECOVERY_NOT_READY_WITHIN_DEADLINE')
            return False
        return self.normal_request('recovered', model=self.restore_model) is not None


def _limit(values, key, lab):
    value = values.get(key)
    return value if type(value) is int and value > 0 else lab.cfg['deadline_ms']


def _needs(lab, *keys):
    absent = [key for key in keys if key not in lab.cfg]
    for key in absent:
        lab.miss('CONFIG_REQUIRED:' + key)
    return not absent


def _budget(lab, values):
    # RSS and one HTTP control do not establish per-request, queue, KV or GPU budgets.
    lab.miss('REQUEST_AND_STAGE_BUDGET_UNOBSERVED')
    lab.miss('QUEUE_KV_GPU_BUDGET_UNOBSERVED')
    lab.normal_request('normal-before'); before = lab.rss_bytes()
    queue_before = lab.metric('vllm:num_requests_waiting')
    running_before = lab.metric('vllm:num_requests_running')
    count = lab.cfg.get('burst_requests')
    if type(count) is not int or count < 2:
        return lab.miss('CONFIG_REQUIRED:burst_requests')
    wait_limit = _limit(values, 'buffer_max_send_wait_ms', lab)
    load = [lab.chat('budget ' * 64, max_tokens=64, timeout=wait_limit / 1000 + 30) for _ in range(count)]
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(lab.requests, load)
        lab.wait(POLL_SECONDS); peak = lab.rss_bytes()
        queue_peak = lab.metric('vllm:num_requests_waiting')
        running_peak = lab.metric('vllm:num_requests_running')
        control = lab.normal_request('normal-under-load')
        rows = running.result()
    shed = sum(r['outcome'] in (429, 503) for r in rows)
    lab.note('load', requests=count, shed=shed, served=sum(r['outcome'] == 200 for r in rows),
        outcomes=[r['outcome'] for r in rows], rss_before=before, rss_peak=peak,
        queue_before=queue_before, queue_peak=queue_peak, running_before=running_before, running_peak=running_peak)
    if control is None and not shed:
        lab.violate('NORMAL_CONTROL_STARVED_WITHOUT_ADMISSION_CONTROL')
    elif control is not None and control['elapsed_ms'] > wait_limit:
        lab.violate('NORMAL_CONTROL_LATENCY_OVER_BUDGET', elapsed_ms=control['elapsed_ms'])
    limit = values.get('buffer_max_process_rss_bytes')
    if type(limit) is not int:
        lab.miss('RSS_BUDGET_POLICY_UNAVAILABLE')
    elif peak > limit:
        lab.violate('PROCESS_RSS_OVER_BUDGET', rss_peak=peak)
    if lab.wait_idle(_limit(values, 'buffer_release_deadline_ms', lab)) is False:
        lab.violate('LOAD_NOT_RELEASED_BEFORE_DEADLINE')
    lab.normal_request('normal-after')


def _slow_reader(lab, values):
    lab.normal_request('normal-before'); before = lab.rss_bytes()
    row, = lab.requests([lab.chat('slow reader', max_tokens=64, stream=True, read_delay_ms=200, close_after_bytes=256,
        timeout=lab.cfg['deadline_ms'] / 1000)])
    peak = lab.rss_bytes(); limit = values.get('buffer_max_process_rss_bytes')
    lab.note('slow-close', outcome=row['outcome'], closed_early=row['closed_early'], rss_before=before, rss_peak=peak)
    if type(limit) is not int:
        lab.miss('RSS_BUDGET_POLICY_UNAVAILABLE')
    elif peak > limit:
        lab.violate('SLOW_READER_RSS_OVER_BUDGET', rss_peak=peak)
    if lab.wait_idle(_limit(values, 'buffer_release_deadline_ms', lab)) is False:
        lab.violate('SLOW_READER_NOT_RELEASED_BEFORE_DEADLINE')
    lab.normal_request('normal-after')


def _replace(lab, change, *, before_model=None, after_model=None):
    """진행 요청을 둔 채 세대를 바꾸고, 전환 중 응답의 모델이 허용 범위인지 본다."""
    before_model = before_model or lab.cfg['model']
    after_model = after_model or before_model
    allowed = lab.cfg.get('allowed_models')
    if not allowed:
        lab.miss('CONFIG_REQUIRED:allowed_models')
    lab.miss('SERVER_INFLIGHT_REQUEST_AND_GENERATION_UNOBSERVED')
    lab.normal_request('normal-before', model=before_model)
    progress_path = None
    directory = lab.cfg.get('probe_progress_directory')
    if directory:
        try:
            if lab.probe_progress(directory, 'directory'):
                progress_path = posixpath.join(directory, 'byul-overlap-' + uuid.uuid4().hex)
        except (OSError, TimeoutError, ValueError, KeyError):
            pass
    inflight = None; overlapping = False
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            inflight = pool.submit(lab.requests, [lab.chat('inflight', model=before_model, max_tokens=256,
                stream=True, timeout=lab.cfg['deadline_ms'] / 1000)], progress_path=progress_path)
            observed = False
            if progress_path:
                end = lab.clock() + lab.cfg['deadline_ms'] / 1000
                while lab.clock() < end and not inflight.done():
                    try:
                        if lab.probe_progress(progress_path, 'file'):
                            observed = True; break
                    except (OSError, TimeoutError, ValueError, KeyError):
                        break
                    lab.wait(POLL_SECONDS)
            else:
                lab.wait(POLL_SECONDS)
            if not observed:
                lab.miss('STREAM_FIRST_BODY_BEFORE_CHANGE_UNOBSERVED')
            if inflight.done():
                lab.miss('INFLIGHT_FINISHED_BEFORE_CHANGE')
            lab.note('inflight-before-change', requested_model=before_model, first_body_seen=observed,
                request_pending=not inflight.done())
            overlapping = observed and not inflight.done()
            change()
            end = lab.clock() + lab.cfg['deadline_ms'] / 1000; rows = []
            while lab.clock() < end:
                try:
                    row, = lab.requests([lab.chat(model=after_model, timeout=5)]); rows.append(row)
                except (OSError, TimeoutError, ValueError):
                    rows.append({'outcome': 'error'})
                if lab.ready_pods() and rows[-1].get('outcome') == 200:
                    break
                lab.wait(POLL_SECONDS)
            first, = inflight.result()
    finally:
        # 후속 수집 실패와 독립적으로 완료된 원 요청의 위반을 보존한다.
        if inflight is not None and inflight.done():
            try:
                result, = inflight.result()
                if result['outcome'] == 'timeout':
                    if overlapping:
                        lab.violate('INFLIGHT_REQUEST_HUNG_ACROSS_TRANSITION')
                    else:
                        lab.miss('INFLIGHT_TIMEOUT_WITHOUT_SERVER_ATTRIBUTION')
                if len(result.get('models', [])) > 1:
                    lab.violate('MIXED_GENERATIONS_IN_ONE_REQUEST', models=result['models'])
            except Exception as error:  # noqa: BLE001 - 앞선 판정과 분리한다
                lab.miss('INFLIGHT_RESULT_UNAVAILABLE:' + type(error).__name__)
        if progress_path:
            try:
                if lab.probe_progress(progress_path, 'file') and not lab.probe_progress(progress_path, 'remove'):
                    lab.miss('PROBE_PROGRESS_CLEANUP_FAILED')
            except (OSError, TimeoutError, ValueError, KeyError):
                lab.miss('PROBE_PROGRESS_CLEANUP_FAILED')
    lab.note('inflight', requested_model=before_model, outcome=first['outcome'], complete=first['complete'],
        models=first.get('models', []), first_chunk_elapsed_ms=first.get('first_chunk_elapsed_ms'))
    models = {r.get('model') for r in rows if r.get('outcome') == 200}
    lab.note('during-transition', requested_model=after_model, samples=len(rows), models=sorted(m for m in models if m))
    if allowed and models - set(allowed) - {None}:
        lab.violate('DISALLOWED_MODEL_SERVED_DURING_TRANSITION')
    if not rows or rows[-1].get('outcome') != 200:
        lab.miss('TRANSITION_DID_NOT_RETURN_TO_SERVICE')


def _redeploy(lab, values):
    if not _needs(lab, 'update_manifest', 'good_manifest', 'generation_models'):
        return
    generations = lab.cfg['generation_models']
    if (type(generations) is not dict or set(generations) != {'before', 'after'}
            or type(generations['before']) is not str or type(generations['after']) is not str
            or not generations['before'] or not generations['after']
            or generations['before'] == generations['after']):
        return lab.miss('GENERATION_NOT_DISTINGUISHABLE')
    lab.restore_model = generations['before']
    lab.note('redeploy-generations', generations=generations)
    _replace(lab, lambda: lab.apply('update_manifest'), before_model=generations['before'],
        after_model=generations['after'])


def _live_change(lab, values):
    """선정 변경 경로의 정상 변경과 같은 경로의 부분 실패를 각각 일으킨다.

    세대는 응답의 모델 별칭으로만 구분한다. 같은 별칭 안에서 weights·cache 일부만 바뀌는
    혼합은 이 방법으로 보이지 않으므로 두 세대 별칭이 다르지 않으면 범위를 채우지 않는다.
    """
    if not _needs(lab, 'update_manifest', 'faulty_manifest', 'good_manifest', 'change_path',
                  'generation_models', 'failure_marker'):
        return
    generations = lab.cfg['generation_models']
    if (type(generations) is not dict or set(generations) != {'before', 'after'}
            or generations['before'] == generations['after']):
        return lab.miss('GENERATION_NOT_DISTINGUISHABLE')
    lab.restore_model = generations['before']
    lab.note('change-path', path=lab.cfg['change_path'], generations=generations)
    _replace(lab, lambda: lab.apply('update_manifest'), before_model=generations['before'],
        after_model=generations['after'])
    _rejected(lab, 'PARTIAL_CHANGE_ACCEPTED', marker=lab.cfg['failure_marker'],
        request_model=generations['after'], require_new_faulty_uid=True)


def _rejected(lab, code, marker=None, *, request_model=None, require_new_faulty_uid=False,
              restore_targets=None):
    """잘못된 세대는 Ready가 되지 않고, 그동안 나간 응답도 허용 세대에서만 와야 한다."""
    allowed = set(lab.cfg.get('allowed_models') or ())
    fresh_required = require_new_faulty_uid or restore_targets is not None
    prior_uids = {p['metadata'].get('uid') for p in lab._pods()} if fresh_required else set()
    if require_new_faulty_uid:
        lab.miss('FAULTY_REQUEST_GENERATION_UNATTRIBUTED')
    lab.apply('faulty_manifest')
    end = lab.clock() + lab.cfg['deadline_ms'] / 1000; served = False; seen = set(); activated = marker is None
    faulty = []; faulty_uids = set(); marker_bindings = {}; diagnostic_uids = set()
    ready_uids = set(); accepted_uids = set(); referenced_uids = set(); current_container_unverified = False
    pending_container_uids = set()
    poll = 0
    while lab.clock() < end:
        poll += 1; lab.diagnostic_poll = poll
        lab.diagnostic('poll-start')
        faulty = [p for p in lab._pods() if p['metadata'].get('labels', {}).get('byul.lab/generation') == 'faulty']
        if fresh_required:
            faulty = [p for p in faulty if p['metadata'].get('uid') and p['metadata']['uid'] not in prior_uids]
            faulty_uids.update(p['metadata']['uid'] for p in faulty)
        if not activated or require_new_faulty_uid:
            for pod in faulty:
                uid = pod['metadata'].get('uid')
                try:
                    if require_new_faulty_uid:
                        identity = lab.engine_container_identity(pod, require_ready=False)
                        if uid in marker_bindings and marker_bindings[uid] == identity:
                            continue
                        found, stable_identity = lab.current_engine_marker(pod['metadata']['name'], uid, marker)
                        if stable_identity is None:
                            # A newly scheduled Pod has no container incarnation yet.
                            # Keep it incomplete until it is observed; an identity lost
                            # during a log read remains an independent collection gap.
                            if uid not in marker_bindings and lab.initial_engine_container_wait(pod):
                                pending_container_uids.add(uid)
                            else:
                                current_container_unverified = True
                        if found is True:
                            marker_bindings[uid] = stable_identity
                            pending_container_uids.discard(uid)
                            activated = True
                        elif uid not in diagnostic_uids and lab.log_contains(pod['metadata']['name'], marker):
                            diagnostic_uids.add(uid)
                    elif lab.log_contains(pod['metadata']['name'], marker):
                        activated = True
                except CurrentEngineMarkerChanged:
                    lab.diagnostic('marker-unbound', pod=pod['metadata']['name'], pod_uid=uid,
                        reason='CURRENT_ENGINE_CHANGED_OR_UNAVAILABLE')
                    current_container_unverified = True
                    try:
                        if uid not in diagnostic_uids and lab.log_contains(pod['metadata']['name'], marker):
                            diagnostic_uids.add(uid)
                    except (OSError, TimeoutError, ValueError):
                        pass
                except (OSError, TimeoutError, ValueError) as error:
                    lab.diagnostic('marker-unavailable', pod=pod['metadata']['name'], pod_uid=uid,
                        reason=_diagnostic_error_kind(error))
        # 로그 조회 뒤 Pod를 다시 읽는다. 조회 전 Ready는 현재 로그 세대의 승인 근거가 아니다.
        faulty = [p for p in lab._pods() if p['metadata'].get('labels', {}).get('byul.lab/generation') == 'faulty']
        if fresh_required:
            faulty = [p for p in faulty if p['metadata'].get('uid') and p['metadata']['uid'] not in prior_uids]
            faulty_uids.update(p['metadata']['uid'] for p in faulty)
        current_ready_uids = {p['metadata'].get('uid') for p in faulty if any(
            c.get('type') == 'Ready' and c.get('status') == 'True'
            for c in p.get('status', {}).get('conditions', []))}
        ready_uids.update(current_ready_uids)
        if restore_targets is not None:
            referenced_uids.update(p['metadata']['uid'] for p in faulty
                if _restore_references(p) & set(restore_targets))
        if current_ready_uids:
            served = True
            accepted_uids = ({p['metadata']['uid'] for p in faulty if p['metadata'].get('uid') in current_ready_uids
                and p['metadata']['uid'] in marker_bindings and lab.engine_container_identity(p) is not None
                and lab.engine_container_identity(p)[0] == 'running'
                and marker_bindings[p['metadata']['uid']] == lab.engine_container_identity(p)}
                if require_new_faulty_uid else current_ready_uids if activated else set())
            if accepted_uids and restore_targets is None:
                lab.violate(code)
                lab.diagnostic('poll-end', reason='EXISTING_ACCEPTED_UID', accepted_uids=sorted(accepted_uids))
                break
        try:
            row, = lab.requests([lab.chat(model=request_model or lab.cfg['model'], timeout=5)])
            lab.diagnostic('service-request', result=_diagnostic_http_metadata(row), attribution='SERVICE_ONLY')
            if row.get('outcome') == 200:
                seen.add(row.get('model'))
                if allowed and row.get('model') is not None and row['model'] not in allowed:
                    lab.violate('DISALLOWED_MODEL_SERVED_DURING_FAILED_CHANGE')
        except (OSError, TimeoutError, ValueError) as error:
            lab.diagnostic('service-request-error', reason=_diagnostic_error_kind(error))
        lab.wait(POLL_SECONDS)
        lab.diagnostic('poll-end', reason='CONTINUE')
    lab.diagnostic_poll = None
    lab.note('faulty-generation', became_ready=served, served_models=sorted(m for m in seen if m),
        failure_marker_seen=None if marker is None else activated,
        new_faulty_uids=sorted(faulty_uids) if fresh_required else None,
        restore_reference_uids=sorted(referenced_uids) if restore_targets is not None else None,
        marker_uids=sorted(marker_bindings) if require_new_faulty_uid else None,
        diagnostic_marker_uids=sorted(diagnostic_uids) if require_new_faulty_uid else None,
        ready_uids=sorted(uid for uid in ready_uids if uid) if fresh_required else None)
    if accepted_uids and restore_targets is None and not any(v['code'] == code for v in lab.violations):
        lab.violate(code)
    if allowed and seen - allowed - {None} and not any(
            v['code'] == 'DISALLOWED_MODEL_SERVED_DURING_FAILED_CHANGE' for v in lab.violations):
        lab.violate('DISALLOWED_MODEL_SERVED_DURING_FAILED_CHANGE')
    if not activated:
        lab.miss('PARTIAL_FAILURE_ACTIVATION_UNVERIFIED')
    if fresh_required and not faulty_uids:
        lab.miss('NEW_FAULTY_POD_UID_UNOBSERVED')
    if restore_targets is not None and not referenced_uids:
        lab.miss('TAMPERED_RESTORE_REFERENCE_UNOBSERVED')
    if require_new_faulty_uid and (current_container_unverified or pending_container_uids) and not accepted_uids:
        lab.miss('FAULTY_CURRENT_ENGINE_CONTAINER_UNVERIFIED')
    if require_new_faulty_uid and diagnostic_uids and not marker_bindings:
        lab.miss('FAULTY_MARKER_ONLY_IN_PREVIOUS_OR_OTHER_CONTAINER')
    if require_new_faulty_uid and marker_bindings and ready_uids and not accepted_uids:
        lab.miss('FAULTY_MARKER_READY_CONTAINER_MISMATCH' if set(marker_bindings) & ready_uids
            else 'FAULTY_MARKER_READY_UID_MISMATCH')

    if require_new_faulty_uid:
        # 필수 Service/marker 관측과 판정을 끝낸 뒤 별도 남은 실행 예산으로 한 번만 진단한다.
        diagnostic_end = _optional_observation_end(lab)
        lab.diagnostic_poll = poll
        if diagnostic_end - lab.clock() > 5 + POLL_SECONDS:
            _diagnostic_fault_pod_request(lab, faulty[:1], diagnostic_end,
                request_model or lab.cfg['model'], idle_end=diagnostic_end)
        else:
            lab.diagnostic('direct-request-skipped', reason='FINAL_RESTORE_BUDGET_RESERVED')
        lab.diagnostic_poll = None


def _restore_references(pod):
    """Pod spec의 ConfigMap/Secret 종류와 이름만 추출한다."""
    used = set()

    def add(kind, name):
        if type(name) is str and name:
            used.add(kind + '/' + name)

    spec = pod.get('spec', {})
    for volume in spec.get('volumes', []):
        add('configmap', (volume.get('configMap') or {}).get('name'))
        add('secret', (volume.get('secret') or {}).get('secretName'))
        for source in (volume.get('projected') or {}).get('sources', []):
            add('configmap', (source.get('configMap') or {}).get('name'))
            add('secret', (source.get('secret') or {}).get('name'))
    for container in (*spec.get('initContainers', []), *spec.get('containers', [])):
        for item in container.get('envFrom', []):
            add('configmap', (item.get('configMapRef') or {}).get('name'))
            add('secret', (item.get('secretRef') or {}).get('name'))
        for item in container.get('env', []):
            source = item.get('valueFrom') or {}
            add('configmap', (source.get('configMapKeyRef') or {}).get('name'))
            add('secret', (source.get('secretKeyRef') or {}).get('name'))
    return used


def _restore_material(lab, values):
    """복원 대상과 승인을 기록하고, 변조 사본 복원이 거부되며 승인 원본 복구 뒤 잔존이 없는지 본다.

    선택 snapshot 분기는 별도 TP1 구성 시험이라 이 시나리오에 넣지 않는다.
    """
    if not _needs(lab, 'faulty_manifest', 'good_manifest', 'restore_objects', 'tampered_objects'):
        return
    lab.miss('RESTORE_CONSUMPTION_AND_EXTERNAL_GATE_UNOBSERVED')
    lab.miss('RESTORE_NONREADY_VOLUME_GATEWAY_RESIDUAL_UNOBSERVED')
    approved = lab.cfg['restore_objects']
    if set(approved) & set(lab.cfg['tampered_objects']):
        return lab.miss('RESTORE_TAMPERED_NAMES_OVERLAP_APPROVAL')
    before = lab.object_digests(approved)
    lab.note('restore-targets', objects=sorted(approved), matches_approval=before == approved)
    if before != approved:
        return lab.miss('RESTORE_TARGETS_NOT_APPROVED_AT_START')
    lab.normal_request('normal-before')
    _rejected(lab, 'TAMPERED_RESTORE_ACCEPTED', restore_targets=lab.cfg['tampered_objects'])
    if lab.restore() is not True:
        return lab.miss('RESTORE_NOT_CONFIRMED')
    lab.mutated = False
    after = lab.object_digests(approved)
    if after != approved:
        lab.violate('RESTORED_MATERIAL_DIFFERS_FROM_APPROVAL')
    residual = lab.references(lab.cfg['tampered_objects'])
    lab.note('after-restore', matches_approval=after == approved, tampered_still_referenced=residual)
    if residual:
        lab.miss('TAMPERED_MATERIAL_SPEC_REFERENCE_REMAINS')
        lab.violate('TAMPERED_MATERIAL_STILL_REFERENCED', objects=residual)


def _optional_observation_end(lab):
    # 최종 복원의 apply, 마지막 Ready 조회, 정상 요청의 기존 최대 호출 시간을 보존한다.
    reserve = 2 * KUBECTL_CALL_TIMEOUT + REQUEST_CALL_TIMEOUT + lab.cfg['deadline_ms'] / 1000 + POLL_SECONDS
    return min(lab.clock() + 10, lab.deadline - reserve)


def _pod_ready(pod):
    return any(c.get('type') == 'Ready' and c.get('status') == 'True'
        for c in pod.get('status', {}).get('conditions', []))


def _bound_pod_request(lab, pod, item, *, require_ready=False, observation_end=None):
    """직접 Pod 주소 요청과 전후 컨테이너 신원만 연결한다. Service 귀속은 주장하지 않는다."""
    original_deadline = lab.observation_deadline
    lab.observation_deadline = min(lab.deadline, lab.clock() + 10,
        observation_end if observation_end is not None else lab.deadline)
    try:
        before = lab.engine_container_identity(pod)
        if before is None or require_ready and not _pod_ready(pod):
            return None
        url = _owned_direct_pod_url(lab, pod)
        if url is None:
            return None
        row, = lab.requests([item], url=url)
        current = next((p for p in lab._pods()
            if p['metadata'].get('uid') == pod['metadata']['uid']), None)
        if (current is None or require_ready and not _pod_ready(current)
                or lab.engine_container_identity(current) != before
                or current.get('status', {}).get('podIP') != pod.get('status', {}).get('podIP')
                or current.get('spec', {}).get('nodeName') != pod.get('spec', {}).get('nodeName')):
            return None
        return row
    except (OSError, TimeoutError, ValueError, KeyError, TypeError, AttributeError):
        return None

    finally:
        lab.observation_deadline = original_deadline


def _startup_gate(lab, values):
    lab.miss('STARTUP_NATIVE_RANK_MODEL_BACKEND_UNOBSERVED')
    lab.miss('STARTUP_RESPONSE_POD_UNATTRIBUTED')
    lab.miss('STARTUP_RANK_DELAY_CHALLENGE_UNIMPLEMENTED')
    lab.miss('STARTUP_THREE_CASE_CHALLENGES_UNIMPLEMENTED')
    lab.miss('STARTUP_TRANSITION_NATIVE_SESSION_UNBOUND')
    lab.miss('STARTUP_NEW_ENGINE_CONTAINER_UNVERIFIED')
    generations = lab.cfg.get('generation_models')
    distinct = (type(generations) is dict and set(generations) == {'before', 'after'}
        and generations['before'] != generations['after'])
    before_model = generations['before'] if distinct else lab.cfg['model']
    after_model = generations['after'] if distinct else lab.cfg['model']
    if not distinct:
        lab.miss('STARTUP_SERVING_GENERATION_UNOBSERVED')
    old_uids = {p['metadata'].get('uid') for p in lab._pods()}
    if None in old_uids or not old_uids:
        lab.miss('STARTUP_POD_GENERATION_UNOBSERVED')
    lab.normal_request('normal-before', model=before_model)
    if 'update_manifest' in lab.cfg and 'update_manifest_sha256' in lab.cfg:
        lab.apply('update_manifest')
    else:
        lab.rollout()
    end = lab.clock() + lab.cfg['deadline_ms'] / 1000; premature = 0; samples = 0; ready = False; early = 0
    new_uids_seen = set()
    while lab.clock() < end:
        new_pods = [p for p in lab._pods() if p['metadata'].get('uid') not in old_uids]
        new_uids_seen.update(p['metadata'].get('uid') for p in new_pods if p['metadata'].get('uid'))
        ready = any(any(c.get('type') == 'Ready' and c.get('status') == 'True'
            for c in p.get('status', {}).get('conditions', [])) for p in new_pods)
        try:
            row, = lab.requests([lab.chat(model=after_model, timeout=5)])
        except (OSError, TimeoutError, ValueError):
            row = {'outcome': 'error'}
        samples += 1
        if ready and row.get('outcome') != 200:
            premature += 1
        if distinct and new_pods and not ready and row.get('outcome') == 200 and row.get('model') == after_model:
            early += 1
        if ready and row.get('outcome') == 200 and (not distinct or row.get('model') == after_model):
            break
        lab.wait(POLL_SECONDS)
    lab.note('startup', samples=samples, ready_without_service=premature,
        after_alias_response_while_new_pod_unready=early, new_generation_ready=ready,
        old_pod_uids=sorted(uid for uid in old_uids if uid), requested_model=after_model,
        manifest_apply_attempted='update_manifest_sha256' in lab.cfg,
        new_pod_uids_seen=sorted(new_uids_seen), rank_delay_injected=False,
        transition_native_session_bound=False)
    if premature:
        lab.miss('READY_WITHOUT_SERVICE_REQUEST_POD_UNATTRIBUTED')
        # 원 Service 관측을 완료한 뒤 현재 Ready 상태만 별도 예산으로 확인한다.
        # 과거 premature 샘플의 응답 귀속은 여전히 missing이다.
        observation_end = _optional_observation_end(lab)
        if observation_end - lab.clock() > 5:
            try:
                candidates = [p for p in lab._pods() if p['metadata'].get('uid') in new_uids_seen]
                for pod in candidates[:1]:
                    if any(c.get('type') == 'Ready' and c.get('status') == 'True'
                            for c in pod.get('status', {}).get('conditions', [])):
                        direct = _bound_pod_request(lab, pod,
                            lab.chat(model=after_model, timeout=5), require_ready=True, observation_end=observation_end)
                        if direct is not None and type(direct.get('outcome')) is int and 500 <= direct['outcome'] < 600:
                            lab.violate('READY_BEFORE_INFERENCE_AVAILABLE', pod_uid=pod['metadata']['uid'])
            except (OSError, TimeoutError, ValueError, KeyError, AttributeError):
                lab.miss('STARTUP_DIRECT_POD_OBSERVATION_UNAVAILABLE')
        else:
            lab.miss('STARTUP_DIRECT_POD_OBSERVATION_SKIPPED_RESTORE_BUDGET')
    if early:
        lab.miss('EARLY_ADMISSION_POD_UNATTRIBUTED')
    if not ready:
        lab.miss('STARTUP_NOT_READY_WITHIN_DEADLINE')
    if not new_uids_seen:
        lab.miss('STARTUP_TRANSITION_GENERATION_UNOBSERVED')


def _partial_start(lab, values):
    """한 구성요소만 실패시킨 세대. 그 실패가 실제로 일어났다는 로그 표식이 있어야 범위가 찬다."""
    if _needs(lab, 'faulty_manifest', 'good_manifest', 'failure_marker'):
        _rejected(lab, 'PARTIAL_INITIALIZATION_REPORTED_READY', lab.cfg['failure_marker'],
            require_new_faulty_uid=True)


FAULTS = {'death': (POSIX_SIGKILL, 'rank_death_detection_ms'), 'delay': (POSIX_SIGSTOP, 'rank_delay_detection_ms'),
    'communication-stall': (POSIX_SIGSTOP, 'rank_communication_stall_detection_ms')}


def _rank_faults(lab, values):
    for fault, (number, key) in FAULTS.items():
        if fault == 'communication-stall':
            continue
        _one_fault(lab, fault, lambda target: lab.signal_worker(number, target), _limit(values, key, lab), number)
        if lab.fault_restore_failed:
            lab.miss('SUBSEQUENT_FAULTS_BLOCKED_BY_RESTORE_FAILURE')
            return
    # SIGSTOP is a process delay; it cannot demonstrate a distinct IPC stall.
    lab.miss('COMMUNICATION_STALL_INJECTION_UNOBSERVED')
    injected = {}
    def exception(target):
        injected['done'] = lab.touch_fault(target)
    _one_fault(lab, 'exception', exception, _limit(values, 'rank_exception_detection_ms', lab), None)
    if 'fault_directory' not in lab.cfg:
        lab.miss('CONFIG_REQUIRED:fault_directory')
    elif injected.get('done'):
        # The fixed callback file protocol has no rank field or acknowledgment.
        lab.miss('EXCEPTION_RANK_ATTRIBUTION_UNOBSERVED')


def _one_fault(lab, fault, inject, limit_ms, number):
    """각 장애의 모든 종료 경로에서 복원하고 실패하면 다음 주입을 막는다."""
    if lab.fault_restore_failed:
        lab.miss('SUBSEQUENT_FAULTS_BLOCKED_BY_RESTORE_FAILURE')
        return
    try:
        lab.normal_request('normal-before-' + fault)
        began = lab.clock()
        target = None; stopped = None; before_release = None
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(lab.requests, [lab.chat('fault ' * 16, max_tokens=256, timeout=limit_ms / 1000 + 5)])
                lab.wait(POLL_SECONDS); injected_ms = (lab.clock() - began) * 1000
                target = lab.selected_worker()
                inject(target)
                if number == POSIX_SIGSTOP:
                    stopped = next((p['state'] for p in lab.census(target['pod'], target['pod_uid'])['processes']
                        if p['pid'] == target['pid'] and p.get('start_ticks') == target['start_ticks']), None)
                row, = running.result()
                if number == POSIX_SIGSTOP:
                    before_release = next((p['state'] for p in lab.census(target['pod'], target['pod_uid'])['processes']
                        if p['pid'] == target['pid'] and p.get('start_ticks') == target['start_ticks']), None)
        except (OSError, TimeoutError, ValueError) as error:
            lab.miss('FAULT_OBSERVATION_INTERRUPTED:' + fault + ':' + type(error).__name__)
            return lab.miss('FAULT_NOT_INJECTED:' + fault)
        finally:
            if number == POSIX_SIGSTOP and target is not None:
                try:
                    lab.signal_worker(POSIX_SIGCONT, target)
                except Exception as error:  # noqa: BLE001 - 계속해서 정상 세대 복원을 시도한다
                    lab.miss('FAULT_NOT_RELEASED:' + fault + ':' + type(error).__name__)
        lab.note('fault-' + fault, outcome=row['outcome'], elapsed_ms=row['elapsed_ms'], complete=row['complete'],
            injected_ms=int(injected_ms), target=target, stopped_state=stopped, before_release_state=before_release)
        if number == POSIX_SIGSTOP and stopped not in ('T', 't'):
            lab.miss('WORKER_STOP_NOT_OBSERVED:' + fault)
        if number == POSIX_SIGSTOP and before_release not in ('T', 't'):
            lab.miss('WORKER_RESUMED_OR_REPLACED_BEFORE_RELEASE:' + fault)
        if row['outcome'] == 200 and row['complete'] and row['elapsed_ms'] < injected_ms:
            lab.miss('FAULT_AFTER_REQUEST_FINISHED:' + fault)
        elif row['outcome'] == 'timeout' or row['elapsed_ms'] > limit_ms:
            lab.violate('FAULT_NOT_DETECTED_BEFORE_DEADLINE', fault=fault, elapsed_ms=row['elapsed_ms'])
        elif row['outcome'] == 200 and not row['complete']:
            lab.violate('INCOMPLETE_WORK_REPORTED_AS_SUCCESS', fault=fault)
    finally:
        try:
            if lab.restore() is not True:
                lab.fault_restore_failed = True
                lab.miss('FAULT_RESTORE_NOT_CONFIRMED:' + fault)
                lab.cleanup_errors.append('LAB_ACTIVATION_FAULT_RESTORE_NOT_CONFIRMED:' + fault)
            else:
                lab.mutated = False
        except Exception as error:  # noqa: BLE001 - 원 오류와 복원 오류를 각각 보존한다
            lab.fault_restore_failed = True
            lab.miss('FAULT_RESTORE_FAILED:' + fault + ':' + type(error).__name__)
            lab.cleanup_errors.append('LAB_ACTIVATION_FAULT_RESTORE_FAILED:' + fault + ':' + type(error).__name__)

def _disconnect(lab, values):
    lab.miss('REQUEST_SCOPED_CORE_WORKER_AND_REFERENCE_RELEASE_UNOBSERVED')
    lab.miss('CANCEL_AND_TIMEOUT_PATHS_UNOBSERVED')
    lab.normal_request('normal-before')
    request_id = f'byul-lif10-{time.time_ns()}'
    row, = lab.requests([lab.chat('cancel', max_tokens=256, stream=True, close_after_bytes=64, timeout=30,
        headers={'X-Request-Id': request_id})])
    lab.note('disconnect', client_request_id=request_id, outcome=row['outcome'], closed_early=row['closed_early'])
    if not row['closed_early']:
        lab.miss('DISCONNECT_NOT_ACHIEVED')
    idle_ms = lab.wait_idle(_limit(values, 'termination_max_delay_ms', lab))
    lab.note('disconnect-aggregate-running', idle_ms=idle_ms)
    if idle_ms is False:
        lab.miss('AGGREGATE_RUNNING_NOT_REQUEST_ATTRIBUTED')
    lab.normal_request('normal-after')


def _controller_ref(value, kind):
    refs = [row for row in value.get('metadata', {}).get('ownerReferences', [])
        if row.get('controller') is True and row.get('kind') == kind
        and type(row.get('uid')) is str and type(row.get('name')) is str]
    return refs[0] if len(refs) == 1 else None


def _deployment_lineage(lab, pod):
    """Bind a Pod to the selected Deployment through its controller ReplicaSet."""
    try:
        deployment = strict_json(lab._kubectl('get', 'deployment', lab.cfg['deployment'], '-o', 'json'), limit=262144)
        uid = deployment['metadata']['uid']
        pod_ref = _controller_ref(pod, 'ReplicaSet')
        if deployment['metadata'].get('namespace') != lab.cfg['namespace'] or not pod_ref or not uid:
            return None
        replica = strict_json(lab._kubectl('get', 'replicaset', pod_ref['name'], '-o', 'json'), limit=262144)
        parent = _controller_ref(replica, 'Deployment')
        if (replica['metadata'].get('namespace') != lab.cfg['namespace']
                or replica['metadata'].get('uid') != pod_ref['uid']
                or parent is None or parent['uid'] != uid
                or parent['name'] != lab.cfg['deployment']):
            return None
        return uid
    except (KeyError, OSError, TimeoutError, ValueError):
        return None


def _diagnostic_int(value, *, signed=False):
    return value if type(value) is int and (signed or value >= 0) else None


def _diagnostic_error_kind(error):
    return next((kind.__name__ for kind in (TimeoutError, OSError, ValueError, KeyError, TypeError)
        if isinstance(error, kind)), 'OTHER_ERROR')


def _diagnostic_capture_metadata(value):
    if type(value) is not dict:
        return None
    flags = {key: value.get(key) if type(value.get(key)) is bool else None
        for key in ('bounded_at_source', 'complete', 'timed_out', 'truncated', 'pipes_closed')}
    errors = value.get('read_errors')
    flags['read_error_count'] = len(errors) if type(errors) is list else None
    flags['read_errors'] = [item for item in ('stdout', 'stderr', 'stdin', 'child_reap')
        if type(errors) is list and item in errors]
    return flags


def _diagnostic_capture_complete(result):
    value = result.get('capture')
    if type(value) is not dict or value.get('schema') != 'guard-pipe-capture/1' \
            or any(value.get(key) is not True for key in ('bounded_at_source', 'complete', 'pipes_closed')) \
            or any(value.get(key) is not False for key in ('timed_out', 'truncated')) \
            or type(value.get('read_errors')) is not list or value['read_errors']:
        return False
    streams = value.get('streams')
    if type(streams) is not dict or set(streams) != {'stdout', 'stderr'}:
        return False
    for name in ('stdout', 'stderr'):
        row = streams[name]; raw = result.get(name + '_bytes')
        if type(row) is not dict or type(raw) is not bytes or row.get('truncated') is not False \
                or type(row.get('limit')) is not int or not 0 < row['limit'] <= 16 * 1024 ** 2 \
                or type(row.get('retained_bytes')) is not int or row['retained_bytes'] != len(raw) \
                or type(row.get('observed_bytes')) is not int or row['observed_bytes'] != len(raw) \
                or len(raw) > row['limit'] or row.get('retained_sha256') != hashlib.sha256(raw).hexdigest():
            return False
    return True


def _diagnostic_http_metadata(row):
    outcome = row.get('outcome')
    if not (type(outcome) is int and 100 <= outcome <= 599) and outcome not in ('error', 'timeout'):
        outcome = 'invalid'
    digest = row.get('sha256')
    return {'outcome': outcome, 'sha256': digest if type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest) else None,
        'bytes': _diagnostic_int(row.get('bytes')), 'elapsed_ms': _diagnostic_int(row.get('elapsed_ms')),
        'complete': row.get('complete') if type(row.get('complete')) is bool else None}


def _diagnostic_namespace_identity(value, namespace):
    if type(value) is not dict or type(value.get('metadata')) is not dict:
        return None
    meta = value.get('metadata', {})
    if type(meta.get('labels')) is not dict:
        return None
    if meta.get('name') != namespace or type(meta.get('uid')) is not str or not meta['uid'] \
            or meta.get('deletionTimestamp') or meta.get('labels', {}).get(OWNED_LABEL) != 'true':
        return None
    return {'namespace': namespace, 'namespace_uid': meta['uid']}


def _diagnostic_pod_identity(pod, container, namespace):
    """Strict projection only: no environment, settings, or mounted data."""
    try:
        meta = pod['metadata']; spec = pod['spec']; status = pod['status']
        if any(type(value) is not dict for value in (meta, spec, status)):
            return None
        if type(meta.get('labels')) is not dict:
            return None
        if meta.get('namespace') != namespace or meta.get('deletionTimestamp') or spec.get('hostNetwork', False) is not False:
            return None
        values = [meta['name'], meta['uid'], spec['nodeName'], status['podIP']]
        if any(type(value) is not str or not value for value in values):
            return None
        address = ipaddress.ip_address(values[3])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if address.is_unspecified or address.is_loopback or address.is_multicast or address.is_link_local:
            return None
        if type(status.get('containerStatuses')) is not list or type(spec.get('containers')) is not list \
                or any(type(row) is not dict for row in [*status['containerStatuses'], *spec['containers']]):
            return None
        selected = [row for row in status['containerStatuses'] if row.get('name') == container]
        declared = [row for row in spec['containers'] if row.get('name') == container]
        if len(selected) != 1 or len(declared) != 1:
            return None
        row, = selected; state = row['state']
        if type(state) is not dict or len(state) != 1:
            return None
        kind, = state
        if kind not in ('running', 'terminated') or type(state[kind]) is not dict:
            return None
        cid = row['containerID']; restart = row['restartCount']; started = state[kind]['startedAt']
        if type(cid) is not str or not re.fullmatch(r'[a-z][a-z0-9+.-]*://[0-9a-f]{64}', cid) \
                or type(restart) is not int or restart < 0 or type(started) is not str or not started:
            return None
        if type(row.get('ready')) is not bool:
            return None
        return {'namespace': namespace, 'pod': values[0], 'pod_uid': values[1], 'node': values[2],
            'pod_ip': values[3], 'container': container, 'container_id': cid, 'restart_count': restart,
            'state': kind, 'started_at': started, 'ready': row.get('ready') is True}
    except (KeyError, TypeError, ValueError):
        return None


def _diagnostic_fault_pod_request(lab, pods, end, model, *, idle_end):
    """Bounded diagnostic only; never feeds served/accepted/missing/violations."""
    # 필수 관측 종료 후 호출한다. 남은 별도 예산이 부족하면 선택 진단만 생략한다.
    remaining = min(end, lab.deadline) - lab.clock()
    if remaining <= 5 + POLL_SECONDS:
        lab.diagnostic('direct-request-skipped', reason='SERVICE_BUDGET_RESERVED')
        return
    if lab.diagnostic_idle_overrun or lab.diagnostic_namespace is None:
        lab.diagnostic('direct-request-skipped', reason='PRIOR_DIAGNOSTIC_IDLE_OVERRUN'
            if lab.diagnostic_idle_overrun else 'BASE_NAMESPACE_IDENTITY_UNVERIFIED')
        return
    lab.diagnostic_deadline = min(idle_end, end, lab.deadline)
    request_id = None
    try:
        for candidate in pods:
            if lab.clock() >= lab.diagnostic_deadline:
                lab.diagnostic('direct-request-skipped', reason='DIAGNOSTIC_BUDGET_EXHAUSTED')
                break
            if _diagnostic_pod_identity(candidate, lab.cfg['container'], lab.cfg['namespace']) is None:
                lab.diagnostic('direct-request-unbound', reason='CANDIDATE_IDENTITY_INVALID')
                continue
            name = candidate['metadata']['name']; uid = candidate['metadata']['uid']
            def snapshot(pod_name, container):
                raw = strict_json(lab._kubectl('get', 'pod', pod_name, '-o', 'json'), limit=2 * 1024 ** 2)
                identity = _diagnostic_pod_identity(raw, container, lab.cfg['namespace'])
                return raw, identity if identity is not None and identity['pod'] == pod_name else None
            def namespace_snapshot():
                raw = strict_json(lab._kubectl('get', 'namespace', lab.cfg['namespace'], '-o', 'json'), limit=262144)
                return _diagnostic_namespace_identity(raw, lab.cfg['namespace'])
            namespace = namespace_snapshot()
            before, engine = snapshot(name, lab.cfg['container'])
            _, probe = snapshot(lab.cfg['probe_pod'], lab.cfg['probe_container'])
            if namespace != lab.diagnostic_namespace or engine is None or probe is None or engine['pod_uid'] != uid \
                    or before['metadata'].get('labels', {}).get('byul.lab/generation') != 'faulty' \
                    or probe['state'] != 'running':
                lab.diagnostic('direct-request-unbound', reason='BEFORE_IDENTITY_INVALID',
                    namespace=namespace, engine=engine, probe=probe)
                continue
            url = _owned_direct_pod_url(lab, before, diagnostic=True)
            if url is None:
                lab.diagnostic('direct-request-unbound', reason='SERVICE_TARGET_BINDING_INVALID', engine=engine, probe=probe)
                continue
            timeout = min(5, lab.diagnostic_deadline - lab.clock())
            if timeout <= 0:
                lab.diagnostic('direct-request-skipped', reason='DIAGNOSTIC_BUDGET_EXHAUSTED')
                break
            request_id = 'byul-fault-diagnostic-' + uuid.uuid4().hex
            item = lab.chat(model=model, timeout=timeout, headers={'X-Request-Id': request_id})
            request_id = hashlib.sha256(request_id.encode()).hexdigest()
            record = {'request_id_sha256': request_id, 'target_port': 8000, 'namespace_before': namespace,
                'engine_before': engine, 'probe_before': probe,
                'request_sha256': hashlib.sha256(canonical_json(item)).hexdigest(),
                'attribution': 'POD_ADDRESS_DIAGNOSTIC_ONLY'}
            lab.diagnostic('direct-request-start', **record)
            row, = lab.requests([item], timeout=timeout, url=url)
            # Keep the result even when the subsequent identity read fails.
            lab.diagnostic('direct-request-result', request_id_sha256=request_id, result=_diagnostic_http_metadata(row))
            _, engine_after = snapshot(name, lab.cfg['container'])
            _, probe_after = snapshot(lab.cfg['probe_pod'], lab.cfg['probe_container'])
            namespace_after = namespace_snapshot()
            bound = engine == engine_after and probe == probe_after and namespace == namespace_after
            lab.diagnostic('direct-request-binding', request_id_sha256=request_id, engine_after=engine_after,
                probe_after=probe_after, namespace_after=namespace_after, bound=bound,
                reason='STABLE_IDENTITIES' if bound else 'IDENTITY_CHANGED')
    except (OSError, TimeoutError, ValueError, KeyError, TypeError, AttributeError) as error:
        lab.diagnostic('direct-request-error', request_id_sha256=request_id, reason=_diagnostic_error_kind(error))
    finally:
        if lab.clock() > idle_end:
            lab.diagnostic_idle_overrun = True
            lab.diagnostic('direct-request-idle-overrun', reason='TRANSPORT_EXCEEDED_ORIGINAL_IDLE')
        lab.diagnostic_deadline = None


def _owned_direct_pod_url(lab, pod, *, diagnostic=False):
    """Resolve a Service port to this engine Pod's declared listening port."""
    try:
        selected = urlsplit(lab.cfg['service_url'])
        if selected.scheme != 'http' or selected.username or selected.password or selected.path not in ('', '/'):
            return None
        hostname = selected.hostname or ''
        suffix = '.' + lab.cfg['namespace'] + '.svc'
        service_name = hostname.removesuffix('.cluster.local').removesuffix(suffix)
        if (hostname.removesuffix('.cluster.local') != service_name + suffix
                or not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?', service_name)):
            return None
        service = strict_json(lab._kubectl('get', 'service', service_name, '-o', 'json'), limit=262144)
        if service['metadata'].get('namespace') != lab.cfg['namespace']:
            return None
        if diagnostic:
            selector_items = [item.split('=', 1) for item in lab.cfg['selector'].split(',')]
            selector = dict(selector_items)
            if not selector or len(selector) != len(selector_items) \
                    or any(not key or not value or '=' in value for key, value in selector.items()) \
                    or service['spec'].get('selector') != selector \
                    or any(pod['metadata'].get('labels', {}).get(key) != value for key, value in selector.items()):
                return None
            declared_ports = service['spec']['ports']
            if selected.port != 8000 or len(declared_ports) != 1 \
                    or type(declared_ports[0].get('port')) is not int or declared_ports[0]['port'] != 8000 \
                    or type(declared_ports[0].get('targetPort')) is not int or declared_ports[0]['targetPort'] != 8000 \
                    or declared_ports[0].get('protocol', 'TCP') != 'TCP':
                return None
        exposed = selected.port or 80
        ports = [row for row in service['spec']['ports'] if row.get('port') == exposed
            and row.get('protocol', 'TCP') == 'TCP']
        if len(ports) != 1:
            return None
        target_port = ports[0].get('targetPort', exposed)
        engine = [row for row in pod['spec']['containers'] if row.get('name') == lab.cfg['container']]
        if len(engine) != 1:
            return None
        declared = [row for row in engine[0].get('ports', []) if row.get('protocol', 'TCP') == 'TCP']
        argv = [*(engine[0].get('command') or []), *(engine[0].get('args') or [])]
        explicit = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == '--port']
        explicit += [value.split('=', 1)[1] for value in argv if value.startswith('--port=')]
        if len(explicit) > 1:
            return None
        if type(target_port) is str:
            selected_ports = [row['containerPort'] for row in declared if row.get('name') == target_port]
            if len(selected_ports) != 1:
                return None
            target_port = selected_ports[0]
        elif type(target_port) is int:
            named = [row for row in declared if row.get('containerPort') == target_port]
            if not named and not (len(explicit) == 1 and explicit[0] == str(target_port)):
                return None
        else:
            return None
        if explicit and explicit[0] != str(target_port):
            return None
        if not 0 < target_port < 65536:
            return None
        address = ipaddress.ip_address(pod['status']['podIP'])
        host = f'[{address}]' if address.version == 6 else str(address)
        return f'http://{host}:{target_port}'
    except (KeyError, OSError, TimeoutError, ValueError):
        return None


def _sensor_mount(pod, container):
    engines = [v for v in pod['spec']['containers'] if v.get('name') == container]
    if len(engines) != 1:
        raise ValueError('RESTORE_ENGINE_CONTAINER_INVALID')
    mounts = engines[0].get('volumeMounts', [])
    callbacks = [v for v in mounts if v.get('name') in ('sensor', 'guard-callbacks')
        and v.get('mountPath') in ('/guard-lifecycle', '/guard-lifecycle/engine-callbacks')
        and v.get('readOnly') is True and 'subPath' not in v and 'subPathExpr' not in v]
    if len(callbacks) != 1:
        raise ValueError('RESTORE_SENSOR_MOUNT_INVALID')
    selected = callbacks[0]
    for mount in mounts:
        path = mount.get('mountPath')
        if type(path) is not str:
            raise ValueError('RESTORE_VOLUME_MOUNT_PATH_INVALID')
        if mount is selected:
            continue
        if path == '/guard-lifecycle/data':
            if (mount.get('name') not in ('data', 'guard-data')
                    or mount.get('readOnly', False) is not False
                    or 'subPath' in mount or 'subPathExpr' in mount):
                raise ValueError('RESTORE_DATA_MOUNT_INVALID')
        elif path == '/' or path == '/guard-lifecycle' or path.startswith('/guard-lifecycle/'):
            raise ValueError('RESTORE_SHADOW_MOUNT')
    volumes = [v for v in pod['spec']['volumes'] if v.get('name') == selected['name']
        and type(v.get('hostPath', {}).get('path')) is str]
    if len(volumes) != 1:
        raise ValueError('RESTORE_SENSOR_HOSTPATH_INVALID')
    return volumes[0]['hostPath']['path'], selected['mountPath']


# 실험실 소유 고정 프로그램. 경로만 argv로 받고 임의 소스·비밀값은 반환하지 않는다.
SENSOR_SESSION = r'''import json,math,os,re,signal,stat,sys

def fail(): raise ValueError('RESTORE_SETTINGS_UNTRUSTED')
def identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,
        info.st_size,info.st_mtime_ns,info.st_ctime_ns)
def directory(info):
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0,os.geteuid()) or info.st_mode&0o022: fail()
def pairs(rows):
    result={}
    for key,value in rows:
        if key in result: fail()
        result[key]=value
    return result
path=sys.argv[1]
seconds=float(sys.argv[2])
if not math.isfinite(seconds) or not 0<seconds<=60: fail()
signal.signal(signal.SIGALRM,lambda *_:fail())
signal.setitimer(signal.ITIMER_REAL,seconds)
if path not in ('/guard-lifecycle/settings.json','/guard-lifecycle/engine-callbacks/settings.json'): fail()
fds=[]
try:
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fds.append(fd)
    directory(os.fstat(fd))
    for component in path.split('/')[1:-1]:
        fd=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);fds.append(fd)
        directory(os.fstat(fd))
    parent=fd
    fd=os.open('settings.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent);fds.append(fd)
    before=os.fstat(fd)
    if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid()
            or stat.S_IMODE(before.st_mode) not in (0o400,0o600) or not 0<before.st_size<=65536): fail()
    raw=b''
    while len(raw)<=65536:
        chunk=os.read(fd,min(65537-len(raw),8192))
        if not chunk: break
        raw+=chunk
    after=os.fstat(fd)
    current=os.stat('settings.json',dir_fd=parent,follow_symlinks=False)
    if len(raw)!=before.st_size or identity(before)!=identity(after) or identity(after)!=identity(current): fail()
    for index,directory_fd in enumerate(fds[:-1]):
        directory(os.fstat(directory_fd))
        if index:
            actual=os.stat(path.split('/')[index],dir_fd=fds[index-1],follow_symlinks=False)
            if (actual.st_dev,actual.st_ino)!=(os.fstat(directory_fd).st_dev,os.fstat(directory_fd).st_ino): fail()
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:fail())
    if type(value) is not dict or type(value.get('session')) is not str or not re.fullmatch('[0-9a-f]{32}',value['session']): fail()
    print(json.dumps({'session':value['session']}))
finally:
    for fd in reversed(fds): os.close(fd)
    signal.setitimer(signal.ITIMER_REAL,0)
'''
SENSOR_SESSION_SHA256 = hashlib.sha256(SENSOR_SESSION.encode()).hexdigest()


def _sensor_identity(lab, pod):
    identity = lab.engine_container_identity(pod)
    node = pod.get('spec', {}).get('nodeName')
    if identity is None or type(node) is not str or not node:
        raise ValueError('RESTORE_SENSOR_TARGET_IDENTITY_UNAVAILABLE')
    return (pod['metadata']['name'], pod['metadata']['uid'], node, identity)


def _sensor_session(lab, pod, mount):
    before = _sensor_identity(lab, pod)
    result = lab._exec(SENSOR_SESSION, posixpath.join(mount[1], 'settings.json'),
        pod=pod['metadata']['name'], pod_uid=pod['metadata']['uid'], isolated=True)
    current = next((p for p in lab._pods() if p['metadata'].get('uid') == pod['metadata']['uid']), None)
    if current is None or _sensor_identity(lab, current) != before or _sensor_mount(current, lab.cfg['container']) != mount:
        raise ValueError('RESTORE_SENSOR_TARGET_IDENTITY_CHANGED')
    session = result.get('session')
    if set(result) != {'session'} or type(session) is not str or re.fullmatch(r'[0-9a-f]{32}', session) is None:
        raise ValueError('RESTORE_SENSOR_SESSION_INVALID')
    return session


def _restore_sensor_session(lab, old_pod, new_pod):
    """Observe the distinct prepared session mounted and loaded by the new engine."""
    try:
        def mount_defaults(mounts):
            # Kubernetes omits readOnly:false in live JSON. Normalize a copy;
            # preserve every mount, its order, and all other declared fields.
            value = deepcopy(mounts)
            if type(value) is not list:
                raise ValueError('RESTORE_VOLUME_MOUNTS_INVALID')
            for row in value:
                if type(row) is not dict:
                    raise ValueError('RESTORE_VOLUME_MOUNT_INVALID')
                if 'readOnly' not in row:
                    row['readOnly'] = False
            return value

        raw = Path(lab.cfg['good_manifest']).read_bytes()
        approved = lab.cfg.get('good_manifest_sha256')
        if (type(approved) is not str or not re.fullmatch('[0-9a-f]{64}', approved)
                or hashlib.sha256(raw).hexdigest() != approved):
            raise ValueError('RESTORE_MANIFEST_DIGEST_UNAPPROVED')
        manifest = yaml.safe_load(raw)
        if type(manifest) is not dict:
            raise ValueError('RESTORE_MANIFEST_SCHEMA_INVALID')
        if (manifest.get('kind') != 'Deployment'
                or manifest.get('metadata', {}).get('name') != lab.cfg['deployment']
                or manifest.get('metadata', {}).get('namespace') != lab.cfg['namespace']):
            raise ValueError('RESTORE_MANIFEST_TARGET_MISMATCH')
        template_meta = manifest['spec']['template']['metadata']
        labels = template_meta['labels']
        if (labels.get('guard.run') != new_pod['metadata'].get('labels', {}).get('guard.run')
                or labels.get('byul.lab/generation') != new_pod['metadata'].get('labels', {}).get('byul.lab/generation')):
            raise ValueError('RESTORE_MANIFEST_LABELS_MISMATCH')
        template = manifest['spec']['template']['spec']
        desired_engine = [v for v in template['containers'] if v.get('name') == lab.cfg['container']]
        actual_engine = [v for v in new_pod['spec']['containers'] if v.get('name') == lab.cfg['container']]
        if len(desired_engine) != 1 or len(actual_engine) != 1:
            raise ValueError('RESTORE_ENGINE_CONTAINER_COUNT_INVALID')
        for key in ('image', 'command', 'args', 'securityContext', 'resources', 'volumeMounts'):
            if key in desired_engine[0]:
                wanted, observed = desired_engine[0][key], actual_engine[0].get(key)
                if key == 'volumeMounts':
                    wanted, observed = mount_defaults(wanted), mount_defaults(observed)
                    if canonical_json(wanted) != canonical_json(observed):
                        raise ValueError('RESTORE_VOLUME_MOUNTS_DIFFER')
                elif wanted != observed:
                    raise ValueError('RESTORE_ENGINE_CONFIG_DIFFERS')
        expected = _sensor_mount({'spec': template}, lab.cfg['container'])
        old = _sensor_mount(old_pod, lab.cfg['container'])
        current = _sensor_mount(new_pod, lab.cfg['container'])
        if (expected is None or old is None or current is None
                or expected == old or current != expected):
            raise ValueError('RESTORE_SENSOR_HOSTPATH_MISMATCH')
        prior_record = getattr(lab, '_restore_prior_session', None)
        if prior_record is not None and prior_record[:2] == (old_pod['metadata']['uid'], old):
            prior = prior_record[2]
        else:
            prior = _sensor_session(lab, old_pod, old)
        observed = _sensor_session(lab, new_pod, current)
        if observed == prior:
            raise ValueError('RESTORE_SESSION_REUSED')
        return observed
    except (KeyError, OSError, TimeoutError, ValueError, TypeError, yaml.YAMLError) as error:
        # 경로·세션·비밀값은 기록하지 않고 실패 단계와 오류 종류만 남긴다.
        reason = str(error) if re.fullmatch(r'(RESTORE|LAB_ACTIVATION)_[A-Z_]+', str(error)) else type(error).__name__
        lab.miss('RESTORE_SENSOR_SESSION_UNVERIFIED:' + reason)
        return None



def _complete_census(value):
    if type(value) is not dict or value.get('process_scope_complete') is not True:
        return False
    before, after = value.get('process_count_before'), value.get('process_count_after')
    return (type(value.get('process_scope_errors')) is list and value['process_scope_errors'] == []
        and type(before) is int and type(after) is int and 0 <= before == after)


def _worker_cleanup(lab, values):
    lab.miss('IPC_OWNERSHIP_AND_GPU_RESOURCE_RELEASE_UNOBSERVED')
    lab.miss('FAILED_GROUP_ABSENCE_DEADLINE_UNOBSERVED')
    lab.normal_request('normal-before'); target = lab.selected_worker()
    old_uids = {target['pod_uid'], *(p['metadata'].get('uid') for p in lab._pods())}
    selected_pod = next((p for p in lab._pods() if p['metadata'].get('uid') == target['pod_uid']), None)
    deployment_uid = _deployment_lineage(lab, selected_pod) if selected_pod is not None else None
    if deployment_uid is None:
        return lab.miss('SELECTED_WORKER_DEPLOYMENT_UNVERIFIED')
    if 'good_manifest' in lab.cfg:
        try:
            mount = _sensor_mount(selected_pod, lab.cfg['container'])
            lab._restore_prior_session = (selected_pod['metadata']['uid'], mount,
                _sensor_session(lab, selected_pod, mount))
        except (KeyError, OSError, TimeoutError, ValueError, TypeError) as error:
            lab.miss('RESTORE_PRIOR_SESSION_UNVERIFIED:' + type(error).__name__)
    before = lab.census(target['pod'], target['pod_uid'])
    lab.signal_worker(POSIX_SIGKILL, target); lab.wait(POLL_SECONDS)
    try:
        after = lab.census(target['pod'], target['pod_uid']); left = len(after['processes']); shm = after['shm_entries']
    except (OSError, TimeoutError, ValueError):
        after = None; left = shm = None
    lab.note('worker-failure', processes_before=len(before['processes']), processes_after=left,
        target=target, process_identities_before=[(p['pid'], p.get('start_ticks')) for p in before['processes']],
        process_identities_after=None if after is None else [(p['pid'], p.get('start_ticks')) for p in after['processes']],
        shm_before=before['shm_entries'], shm_after=shm,
        shm_names_before=before.get('shm_names'), shm_names_after=None if after is None else after.get('shm_names'),
        process_scope_complete_after=_complete_census(after),
        process_scope_errors_after=None if after is None else after.get('process_scope_errors'))
    death_observed = _complete_census(after) and all(
        p['pid'] != target['pid'] or p.get('start_ticks') != target['start_ticks']
        for p in after['processes'])
    if not _complete_census(after):
        lab.miss('SELECTED_WORKER_DEATH_CENSUS_INCOMPLETE')
    if not death_observed:
        lab.miss('SELECTED_WORKER_DEATH_UNOBSERVED')
    if 'good_manifest' in lab.cfg:
        lab.apply('good_manifest')
    else:
        lab.miss('RESTORE_NATIVE_SESSION_UNVERIFIED')
        lab.rollout()
    end = lab.clock() + lab.cfg['deadline_ms'] / 1000
    new_ready = []; new_owned_seen = set()
    while lab.clock() < end:
        try:
            owned = [p for p in lab._pods() if p['metadata'].get('uid') not in old_uids
                and p['metadata'].get('uid') and _deployment_lineage(lab, p) == deployment_uid]
            new_owned_seen.update(p['metadata']['uid'] for p in owned)
            new_ready = [p for p in owned if any(c.get('type') == 'Ready' and c.get('status') == 'True'
                    for c in p.get('status', {}).get('conditions', []))]
            if new_ready:
                break
        except (OSError, TimeoutError, ValueError):
            pass
        lab.wait(POLL_SECONDS)
    lab.note('worker-restart-generation', old_pod_uids=sorted(uid for uid in old_uids if uid),
        new_ready_pod_uids=sorted(p['metadata'].get('uid') for p in new_ready),
        selected_worker_death_observed=death_observed)
    if len(new_ready) != 1:
        if not new_ready and new_owned_seen and death_observed:
            lab.violate('NEXT_START_BLOCKED_AFTER_WORKER_FAILURE')
        lab.miss('NEXT_START_NEW_GENERATION_UNOBSERVED')
        return
    pod = new_ready[0]
    before_identity = lab.engine_container_identity(pod)
    restored_session = (_restore_sensor_session(lab, selected_pod, pod)
        if 'good_manifest' in lab.cfg else None)
    if restored_session is None:
        lab.miss('RESTORE_SENSOR_SESSION_UNVERIFIED')
    direct_url = _owned_direct_pod_url(lab, pod)
    if direct_url is None:
        lab.miss('NEXT_START_DIRECT_POD_ADDRESS_UNAVAILABLE')
        return
    if before_identity is None:
        lab.miss('NEXT_START_ENGINE_IDENTITY_UNAVAILABLE')
        return
    response = lab.normal_request('normal-after-restart', url=direct_url)
    current = next((p for p in lab._pods() if p['metadata'].get('uid') == pod['metadata']['uid']), None)
    stable = (current is not None and _deployment_lineage(lab, current) == deployment_uid
        and lab.engine_container_identity(current) == before_identity)
    lab.note('worker-next-start-direct', pod_uid=pod['metadata']['uid'],
        engine_identity_before=before_identity, engine_identity_stable=stable,
        selected_worker_death_observed=death_observed, normal_response=response is not None,
        restore_sensor_session=restored_session)
    if not stable:
        lab.miss('NEXT_REQUEST_ENGINE_GENERATION_CHANGED')
    elif response is None and death_observed:
        lab.violate('NEXT_REQUEST_FAILED_AFTER_RESTART')
    if stable and response is not None and death_observed and restored_session is not None:
        lab.mutated = False


def _retry(lab, values):
    """끊긴 요청 하나가 엔진 입력 토큰을 몇 번 소비했는지 정상 요청 기준으로 잰다."""
    name = 'vllm:prompt_tokens_total'; prompt = 'retry ' * 32
    lab.normal_request('normal-before')
    first = lab.metric(name)
    if first is None:
        return lab.miss('ATTEMPT_COUNTER_METRIC_UNAVAILABLE')
    baseline_id = f'byul-lif12-baseline-{uuid.uuid4().hex}'
    disconnected_id = f'byul-lif12-disconnect-{uuid.uuid4().hex}'
    lab.requests([lab.chat(prompt, timeout=30, headers={'X-Request-Id': baseline_id})])
    baseline_after = lab.metric(name)
    before = lab.metric(name)
    if baseline_after is None or before is None:
        return lab.miss('ATTEMPT_COUNTER_METRIC_UNAVAILABLE')
    single = baseline_after - first
    lab.requests([lab.chat(prompt, max_tokens=64, stream=True, close_after_bytes=64, timeout=30,
        headers={'X-Request-Id': disconnected_id})])
    if lab.wait_idle(lab.cfg['deadline_ms']) is False:
        lab.miss('DISCONNECTED_WORK_DID_NOT_FINISH')
    after = lab.metric(name)
    if after is None:
        return lab.miss('ATTEMPT_COUNTER_METRIC_UNAVAILABLE')
    attempts = (after - before) / single if single > 0 else None
    lab.note('disconnect-attempts', baseline_client_id=baseline_id, disconnected_client_id=disconnected_id,
        aggregate_token_ratio=attempts, client_attempts=1)
    lab.miss('REQUEST_ATTEMPT_ID_AND_TOKEN_ATTRIBUTION_UNOBSERVED')
    if attempts is None or attempts < 1:
        lab.miss('ATTEMPT_COUNT_UNAVAILABLE')
    lab.normal_request('normal-after')


def _profiler(lab, values):
    if not _needs(lab, 'trace_directory'):
        return
    # A clean stop alone cannot answer the separate backend stop-failure branch.
    lab.miss('PROFILER_STOP_FAILURE_BRANCH_UNOBSERVED')
    lab.normal_request('normal-before'); directory = lab.cfg['trace_directory']
    def control(path):
        row, = lab.requests([{'method': 'POST', 'path': path, 'body': None, 'timeout': 60}])
        return row['outcome']
    lab.mutated = True
    started = control('/start_profile'); lab.normal_request('normal-active'); stopped = control('/stop_profile')
    first = lab._exec(FILE_COUNT, directory)['count']; lab.wait(lab.cfg['deadline_ms'] / 1000)
    second = lab._exec(FILE_COUNT, directory)['count']
    next_start = control('/start_profile')
    if next_start == 200:
        lab.normal_request('normal-next-active')
    else:
        lab.miss('NEXT_PROFILE_NOT_STARTED')
    next_stop = control('/stop_profile')
    third = lab._exec(FILE_COUNT, directory)['count']
    lab.mutated = not (stopped == 200 and next_stop == 200)
    lab.note('profiler', start=started, stop=stopped, trace_files=[first, second, third],
        next_start=next_start, next_stop=next_stop)
    if stopped == 200 and second != first:
        lab.violate('COLLECTION_CONTINUED_AFTER_STOP')
    if stopped == 200 and next_start != 200:
        lab.violate('NEXT_START_BLOCKED_AFTER_REPORTED_STOP')
    if next_start == 200 and next_stop == 200 and third <= second:
        lab.miss('NEXT_PROFILE_TRACE_NOT_OBSERVED')
    if started != 200:
        lab.miss('PROFILER_NOT_ENABLED')
    lab.normal_request('normal-after')


def _record_state(lab, values):
    # A service health response is not attributed to the selected worker Pod.
    # Neither an unavailable census nor a different healthy replica proves a lie.
    lab.miss('STATE_HEALTH_LOG_WORKER_BINDING_UNOBSERVED')
    lab.normal_request('normal-before')
    target = lab.signal_worker(POSIX_SIGKILL); lab.wait(POLL_SECONDS)
    row, = lab.requests([{'method': 'GET', 'path': '/health', 'timeout': 10}])
    try:
        before_census = lab.census(target['pod'], target['pod_uid'])
        alive = len(before_census['processes'])
    except (OSError, TimeoutError, ValueError):
        alive = None; before_census = None
        lab.miss('STATE_FAILED_WORKER_CENSUS_UNAVAILABLE')
    direct = None; after_alive = None; after_census = None
    before_complete = _complete_census(before_census)
    if alive == 0 and not before_complete:
        lab.miss('STATE_WORKER_ABSENCE_CENSUS_INCOMPLETE')
    if alive == 0 and before_complete:
        original_deadline = getattr(lab, 'observation_deadline', None)
        try:
            observation_end = _optional_observation_end(lab)
            if observation_end - lab.clock() <= 5 + POLL_SECONDS:
                lab.miss('STATE_DIRECT_HEALTH_SKIPPED_RESTORE_BUDGET')
            else:
                # 직접 응답 뒤 census/신원 확인까지 같은 선택 관측 예산에 포함한다.
                lab.observation_deadline = observation_end
                pod = next((p for p in lab._pods() if p['metadata'].get('uid') == target['pod_uid']), None)
                if pod is not None:
                    direct = _bound_pod_request(lab, pod,
                            {'method': 'GET', 'path': '/health', 'timeout': 10}, observation_end=observation_end)
                    if direct is not None:
                        after_census = lab.census(target['pod'], target['pod_uid'])
                        after_alive = len(after_census['processes'])
                        if not _complete_census(after_census):
                            lab.miss('STATE_HEALTH_POST_CENSUS_INCOMPLETE')
                            direct = None
                        current = next((p for p in lab._pods() if p['metadata'].get('uid') == target['pod_uid']), None)
                        if (current is None or lab.engine_container_identity(current) != lab.engine_container_identity(pod)
                                or current.get('spec', {}).get('nodeName') != pod.get('spec', {}).get('nodeName')):
                            lab.miss('STATE_HEALTH_WORKER_GENERATION_CHANGED')
                            direct = None
                        if after_alive != 0:
                            lab.miss('STATE_WORKERS_REAPPEARED_DURING_HEALTH')
                            direct = None
        except (OSError, TimeoutError, ValueError, KeyError, AttributeError):
            direct = None
            lab.miss('STATE_HEALTH_POST_CENSUS_OR_BINDING_UNAVAILABLE')
        finally:
            lab.observation_deadline = original_deadline
    lab.note('state-after-failure', health=row['outcome'], processes=alive, target=target,
        health_worker_binding_verified=direct is not None, processes_after_health=after_alive,
        process_scope_complete_before=before_complete,
        process_scope_complete_after=_complete_census(after_census),
        process_scope_errors_before=None if before_census is None else before_census.get('process_scope_errors'),
        process_scope_errors_after=None if after_census is None else after_census.get('process_scope_errors'))
    if alive == 0 and direct is not None and direct.get('outcome') == 200:
        lab.violate('HEALTH_REPORTED_AFTER_WORKERS_GONE')
    elif row['outcome'] == 200 and alive == 0:
        lab.miss('STATE_SERVICE_HEALTH_WITHOUT_SELECTED_POD_ATTRIBUTION')


def _late_result(lab, values):
    identifier = 'byul-activation-same-id'
    baseline = lab.normal_request('normal-before')
    first, = lab.requests([lab.chat('same request', max_tokens=8, headers={'X-Request-Id': identifier})])
    lab.requests([lab.chat('other request', max_tokens=256, stream=True, close_after_bytes=64, timeout=30,
        headers={'X-Request-Id': identifier})])
    second, = lab.requests([lab.chat('same request', max_tokens=8, headers={'X-Request-Id': identifier})])
    lab.note('same-external-id', baseline=bool(baseline), first=first['outcome'], second=second['outcome'])
    if first['outcome'] != 200 or second['outcome'] != 200:
        lab.miss('SAME_ID_REQUEST_NOT_SERVED')
    elif first.get('answer') is None or second.get('answer') is None:
        lab.miss('SAME_ID_ANSWER_UNAVAILABLE')
    elif first['answer'] != second['answer']:
        lab.violate('LATE_RESULT_CHANGED_FOLLOWING_REQUEST')
    lab.normal_request('normal-after')


SCENARIOS = {'LIF-03': _budget, 'LIF-04': _slow_reader, 'LIF-05': _redeploy, 'LIF-06': _live_change, 'LIF-07': _startup_gate,
    'LIF-08': _rank_faults, 'LIF-09': _partial_start, 'LIF-10': _disconnect, 'LIF-11': _worker_cleanup, 'LIF-12': _retry,
    'LIF-14': _profiler, 'LIF-16': _restore_material, 'OBS-02': _record_state, 'REQ-12': _late_result}


def run_scenario(check_id, cfg, values, *, runner=None, clock=None, wait=None):
    lab = Lab(cfg, runner=runner, clock=clock, wait=wait)
    lab.verify_owned()
    try:
        SCENARIOS[check_id](lab, values)
    except Exception as error:  # noqa: BLE001 - 임의의 수집 오류도 복원을 우회하지 않는다
        lab.miss('SCENARIO_INTERRUPTED:' + type(error).__name__)
    finally:
        _final_restore(lab)
    return {'activation_steps': lab.steps, 'activation_violations': lab.violations, 'activation_missing': lab.missing,
        'activation_scope_complete': not lab.missing and bool(lab.normal) and all(lab.normal)}, lab


def _final_restore(lab):
    if lab.mutated:
        try:
            if lab.restore() is not True:
                lab.miss('FINAL_RESTORE_NOT_CONFIRMED')
                lab.cleanup_errors.append('LAB_ACTIVATION_FINAL_RESTORE_NOT_CONFIRMED')
        except Exception as error:  # noqa: BLE001 - cleanup 실패가 앞선 위반을 지우면 안 된다
            lab.miss('FINAL_RESTORE_FAILED:' + type(error).__name__)
            lab.cleanup_errors.append('LAB_ACTIVATION_FINAL_RESTORE_FAILED:' + type(error).__name__)



class ActivatedExecutor:
    """연결된 수동 수집기 뒤에 능동 시나리오를 실행해 한 증거로 합친다."""

    def __init__(self, child, cfg, *, runner=None, clock=None, wait=None):
        self.child = child; self.cfg = cfg; self.runner = runner; self.clock = clock; self.wait = wait

    def __getattr__(self, name):
        return getattr(self.child, name)

    def run(self, check, env, policy_values, adapter, workdir):
        workdir = Path(workdir); started = utcnow(); passive = None; passive_gap = None
        try:
            passive = self.child.run(check, env, policy_values, adapter, workdir / 'passive')
        except Exception as error:  # noqa: BLE001 - 수동 수집 실패가 능동 측정을 막지 않는다
            passive_gap = type(error).__name__
        try:
            facts, lab = run_scenario(check.id, self.cfg, policy_values, runner=self.runner, clock=self.clock, wait=self.wait)
        except ActivationRefused as error:
            return self._refused(check, passive, str(error), started)
        if passive_gap:
            facts['activation_steps'].append({'step': 'passive-collection', 'unavailable': passive_gap})
            facts['activation_missing'].append('PASSIVE_COLLECTION_UNAVAILABLE')
            facts['activation_scope_complete'] = False
        if passive is not None and passive.cleanup_errors:
            facts['activation_missing'].append('PASSIVE_CLEANUP_ERRORS')
            facts['activation_scope_complete'] = False
        from .lifecycle_common_input_consumption import merge_activation_inputs
        facts = merge_activation_inputs(facts, passive)
        workdir.mkdir(parents=True, exist_ok=True)
        artifact = None; primary_artifact_gap = False
        try:
            path = workdir / 'lab-activation.json'; path.write_bytes(canonical_json(facts))
            artifact = DraftArtifact(name=path.name, path=path, collector='lab_activation/0.1.0')
        except OSError:
            if passive is None or 'b_common_inputs' not in passive.config:
                raise  # Preserve the original path when no B common input was consumed.
            primary_artifact_gap = True
            facts['activation_missing'].append('LAB_ACTIVATION_COMMON_INPUT_ARTIFACT_UNAVAILABLE')
            facts['activation_scope_complete'] = False
        # Preserve the primary verdict facts before optional diagnostic writes.
        try:
            diagnostics = lab.write_diagnostics(workdir)
        except Exception as error:  # noqa: BLE001 - optional diagnostics cannot erase an independent violation
            lab.diagnostic_write_errors.append({'reason': _diagnostic_error_kind(error)})
            diagnostics = []
        observations = {**(passive.observations if passive else {}),
            **{k: Observation(value=v, collected=True) for k, v in facts.items()}}
        complete = not primary_artifact_gap and (facts['activation_scope_complete'] or bool(facts['activation_violations']))
        origin = DataOrigin.EXAMPLE if self.runner is not None else DataOrigin.MEASURED if complete else DataOrigin.DECLARED
        if passive is not None and 'b_common_inputs' in passive.config:
            # Registration does not authenticate an external record. Preserve
            # both collected FAILs without upgrading the new combined evidence.
            origin = min(origin, passive.data_origin, env.data_origin, key=lambda value: ORIGIN_STRENGTH[value])
        return EvidenceDraft(status=ExecStatus.COMPLETED if complete else ExecStatus.ABORTED,
            data_origin=origin,
            normal_control={'attempted': bool(lab.normal), 'succeeded': bool(lab.normal) and all(lab.normal),
                'detail': 'Parent-measured normal requests around the activated fault.'},
            challenges=[{'step': step, 'attempted': facts['activation_scope_complete']} for step in check.challenge_steps],
            observations=observations, artifacts=[*(passive.artifacts if passive else []), *diagnostics, *([artifact] if artifact else [])],
            config={**(passive.config if passive else {}), 'lab_activation': {k: v for k, v in self.cfg.items() if k != 'kubectl_context'}},
            started_at=started, finished_at=utcnow(),
            abort_reason=None if complete else 'LAB_ACTIVATION_COMMON_INPUT_ARTIFACT_UNAVAILABLE' if primary_artifact_gap else 'LAB_ACTIVATION_SCOPE_INCOMPLETE',
            cleanup_errors=[*(passive.cleanup_errors if passive else []), *lab.cleanup_errors,
                *(['LAB_ACTIVATION_COMMON_INPUT_ARTIFACT_UNAVAILABLE'] if primary_artifact_gap else []),
                *(['LAB_ACTIVATION_DIAGNOSTIC_ARTIFACT_UNAVAILABLE'] if lab.diagnostic_write_errors else [])])

    def _refused(self, check, passive, reason, started):
        if passive is not None:
            return passive
        return EvidenceDraft(status=ExecStatus.ABORTED, data_origin=DataOrigin.DECLARED,
            normal_control={'attempted': False, 'succeeded': None}, started_at=started, finished_at=utcnow(), abort_reason=reason)


def activated(factory):
    def build(cfg):
        child = factory(cfg)
        settings = cfg.get('lab_activation') if type(cfg) is dict else None
        return ActivatedExecutor(child, settings) if settings else child
    return build
