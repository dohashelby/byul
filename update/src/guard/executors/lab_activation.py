"""소유 실험 환경 전용 능동 활성화. 실제 장애·전환을 일으키고 부모가 직접 측정한다.

수동 수집기는 연결된 실행기가 그대로 수행한다. 이 모듈은 그 뒤에 점검의 챌린지를
실행해 activation_* 관측을 더한다. 네임스페이스에 소유 표식이 없으면 어떤 변경도
보내지 않는다. 측정하지 못한 분기는 missing에 남고 위반으로 바꾸지 않는다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import time

from guard.models import DataOrigin, ExecStatus, canonical_json, utcnow
from guard.models.objects import Observation
from guard.runtime.bounded import capture_pipes, strict_json

from .base import DraftArtifact, EvidenceDraft

OWNED_LABEL = 'byul.lab/owned'
OBSERVATIONS = {'activation_steps': 'list', 'activation_violations': 'list', 'activation_missing': 'list',
    'activation_scope_complete': 'bool'}
CONFIG_KEYS = {'namespace', 'selector', 'container', 'probe_pod', 'probe_container', 'service_url', 'model', 'kubectl_binary',
    'kubectl_context', 'deployment', 'process_pattern', 'deadline_ms', 'burst_requests', 'faulty_manifest', 'good_manifest',
    'update_manifest', 'allowed_models', 'trace_directory', 'fault_directory', 'max_duration_seconds',
    'generation_models', 'change_path', 'failure_marker', 'restore_objects', 'tampered_objects'}
# 해시로 비교할 수 있는 복원 대상. volume·snapshot 내용은 이 실행기가 읽지 않는다.
RESTORE_KINDS = frozenset({'configmap', 'secret'})
REQUIRED_KEYS = {'namespace', 'selector', 'container', 'probe_pod', 'probe_container', 'service_url', 'model',
    'deployment', 'process_pattern', 'deadline_ms'}
POLL_SECONDS = 0.5
FAULT_FILE = 'lab-fault-exception'
# Signals are sent by a Python helper inside the Linux engine container.
# They must not depend on the controller host's signal module (e.g. Windows).
POSIX_SIGKILL, POSIX_SIGSTOP, POSIX_SIGCONT = 9, 19, 18

CLIENT = r'''
import concurrent.futures as f,hashlib,http.client,json,re,sys,time
from urllib.parse import urlsplit
spec=json.load(sys.stdin);base=urlsplit(spec["url"])
def one(item):
    began=time.monotonic();row=dict(outcome="error",elapsed_ms=None,sha256=None,model=None,closed_early=False,complete=False)
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
pattern=re.compile(sys.argv[1]);found=[]
for name in os.listdir("/proc"):
    if not name.isdigit():continue
    try:
        with open(f"/proc/{name}/cmdline","rb") as stream:command=stream.read(4096).replace(b"\0",b" ").decode("utf-8","replace")
        with open(f"/proc/{name}/stat") as stream:state=stream.read();state=state[state.rfind(")")+2:].split()
    except OSError:continue
    if pattern.search(command) and int(name)!=os.getpid():found.append(dict(pid=int(name),state=state[0],rss_pages=int(state[21])))
try:shm=len(os.listdir("/dev/shm"))
except OSError:shm=None
print(json.dumps(dict(processes=found,shm_entries=shm,page_bytes=os.sysconf("SC_PAGE_SIZE"))))
'''

SIGNAL = "import os,sys\nos.kill(int(sys.argv[1]),int(sys.argv[2]))\nprint('{}')\n"
FILE_COUNT = "import json,os,sys\nprint(json.dumps(dict(count=sum(len(f) for _,_,f in os.walk(sys.argv[1])))))\n"
TOUCH = "import pathlib,sys\npathlib.Path(sys.argv[1]).write_text('1')\nprint('{}')\n"


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

    def _kubectl(self, *args, stdin=None, cap=2 * 1024 ** 2, timeout=60):
        remaining = min(timeout, self.deadline - self.clock())
        if remaining <= 0:
            raise TimeoutError('LAB_ACTIVATION_BUDGET_EXHAUSTED')
        context = [f"--context={self.cfg['kubectl_context']}"] if 'kubectl_context' in self.cfg else []
        options = {'input_bytes': stdin} if stdin is not None else {}
        result = self.runner([self.binary, *context, f"--namespace={self.cfg['namespace']}", f'--request-timeout={remaining:.0f}s', *args],
            timeout=remaining, env_extra={}, max_stdout_bytes=cap, max_stderr_bytes=4096, **options)
        if result['returncode'] != 0:
            raise OSError('LAB_ACTIVATION_KUBECTL_FAILED')
        return result['stdout_bytes']

    def verify_owned(self):
        raw = self._kubectl('get', 'namespace', self.cfg['namespace'], '-o', 'json')
        if strict_json(raw, limit=262144).get('metadata', {}).get('labels', {}).get(OWNED_LABEL) != 'true':
            raise ActivationRefused('LAB_ACTIVATION_NAMESPACE_NOT_OWNED')

    def _pods(self):
        raw = self._kubectl('get', 'pods', '-l', self.cfg['selector'], '-o', 'json')
        return [p for p in strict_json(raw, limit=2 * 1024 ** 2).get('items', []) if not p['metadata'].get('deletionTimestamp')]

    def ready_pods(self):
        def ready(pod):
            return any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in pod.get('status', {}).get('conditions', []))
        return [p['metadata']['name'] for p in self._pods() if ready(p)]

    def _exec(self, program, *args, stdin=None):
        pods = self.ready_pods()
        if not pods:
            raise OSError('LAB_ACTIVATION_NO_READY_POD')
        return json.loads(self._kubectl('exec', pods[0], f"--container={self.cfg['container']}", '--', 'python3', '-c', program, *args))

    def requests(self, items, *, timeout=120):
        payload = json.dumps({'url': self.cfg['service_url'], 'requests': items}).encode()
        raw = self._kubectl('exec', '-i', self.cfg['probe_pod'], f"--container={self.cfg['probe_container']}", '--',
            'python3', '-c', CLIENT, stdin=payload, timeout=timeout, cap=8 * 1024 ** 2)
        return json.loads(raw)

    def chat(self, prompt='ping', **options):
        body = {'model': self.cfg['model'], 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': options.pop('max_tokens', 1),
            'temperature': 0, 'stream': options.pop('stream', False)}
        return {'method': 'POST', 'path': '/v1/chat/completions', 'body': body, **options}

    def normal_request(self, label):
        try:
            row, = self.requests([self.chat(timeout=self.cfg['deadline_ms'] / 1000)])
        except (OSError, TimeoutError, ValueError):
            row = {'outcome': 'error'}
        good = row.get('outcome') == 200 and row.get('complete') is True
        self.normal.append(good); self.note(label, outcome=row.get('outcome'), succeeded=good)
        return row if good else None

    def metric(self, name):
        row, = self.requests([{'method': 'GET', 'path': '/metrics', 'metrics': True, 'timeout': 10}])
        found = re.findall(rf'^{re.escape(name)}(?:{{[^}}]*}})? ([0-9.e+-]+)$', row.get('metrics', ''), re.M)
        return sum(float(value) for value in found) if found else None

    def census(self):
        return self._exec(CENSUS, self.cfg['process_pattern'])

    def rss_bytes(self):
        value = self.census()
        return sum(p['rss_pages'] for p in value['processes']) * value['page_bytes']

    def signal_worker(self, number):
        processes = self.census()['processes']
        if not processes:
            raise OSError('LAB_ACTIVATION_PROCESS_NOT_FOUND')
        self.mutated = True
        return self._exec(SIGNAL, str(processes[-1]['pid']), str(int(number)))

    def touch_fault(self):
        if 'fault_directory' not in self.cfg:
            return False
        self.mutated = True
        self._exec(TOUCH, self.cfg['fault_directory'] + '/' + FAULT_FILE)
        return True

    def rollout(self):
        self.mutated = True
        self._kubectl('rollout', 'restart', f"deployment/{self.cfg['deployment']}")

    def apply(self, name):
        self.mutated = True
        raw = Path(self.cfg[name]).read_bytes()
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
        """Ready Pod가 volume·env로 아직 참조하는 대상 이름."""
        wanted = {name.partition('/')[2] for name in names}; used = set(); ready = set(self.ready_pods())
        for pod in self._pods():
            if pod['metadata']['name'] not in ready:
                continue
            spec = pod.get('spec', {})
            for volume in spec.get('volumes', []):
                used.add((volume.get('configMap') or {}).get('name')); used.add((volume.get('secret') or {}).get('secretName'))
                for source in (volume.get('projected') or {}).get('sources', []):
                    used.add((source.get('configMap') or {}).get('name')); used.add((source.get('secret') or {}).get('name'))
            for container in spec.get('containers', []):
                for item in container.get('envFrom', []):
                    used.add((item.get('configMapRef') or {}).get('name')); used.add((item.get('secretRef') or {}).get('name'))
                for item in container.get('env', []):
                    source = item.get('valueFrom') or {}
                    used.add((source.get('configMapKeyRef') or {}).get('name')); used.add((source.get('secretKeyRef') or {}).get('name'))
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
        return self.normal_request('recovered') is not None


def _limit(values, key, lab):
    value = values.get(key)
    return value if type(value) is int and value > 0 else lab.cfg['deadline_ms']


def _needs(lab, *keys):
    absent = [key for key in keys if key not in lab.cfg]
    for key in absent:
        lab.miss('CONFIG_REQUIRED:' + key)
    return not absent


def _budget(lab, values):
    lab.normal_request('normal-before'); before = lab.rss_bytes()
    count = lab.cfg.get('burst_requests')
    if type(count) is not int or count < 2:
        return lab.miss('CONFIG_REQUIRED:burst_requests')
    wait_limit = _limit(values, 'buffer_max_send_wait_ms', lab)
    load = [lab.chat('budget ' * 64, max_tokens=64, timeout=wait_limit / 1000 + 30) for _ in range(count)]
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(lab.requests, load)
        lab.wait(POLL_SECONDS); peak = lab.rss_bytes()
        control = lab.normal_request('normal-under-load')
        rows = running.result()
    shed = sum(r['outcome'] in (429, 503) for r in rows)
    lab.note('load', requests=count, shed=shed, served=sum(r['outcome'] == 200 for r in rows), rss_before=before, rss_peak=peak)
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


def _replace(lab, change):
    """진행 요청을 둔 채 세대를 바꾸고, 전환 중 응답의 모델이 허용 범위인지 본다."""
    allowed = lab.cfg.get('allowed_models')
    if not allowed:
        lab.miss('CONFIG_REQUIRED:allowed_models')
    lab.normal_request('normal-before')
    with ThreadPoolExecutor(max_workers=1) as pool:
        inflight = pool.submit(lab.requests, [lab.chat('inflight', max_tokens=256, stream=True, timeout=lab.cfg['deadline_ms'] / 1000)])
        lab.wait(POLL_SECONDS); change()
        end = lab.clock() + lab.cfg['deadline_ms'] / 1000; rows = []
        while lab.clock() < end:
            try:
                row, = lab.requests([lab.chat(timeout=5)]); rows.append(row)
            except (OSError, TimeoutError, ValueError):
                rows.append({'outcome': 'error'})
            if lab.ready_pods() and rows[-1].get('outcome') == 200:
                break
            lab.wait(POLL_SECONDS)
        first, = inflight.result()
    lab.note('inflight', outcome=first['outcome'], complete=first['complete'], models=first.get('models', []))
    if first['outcome'] == 'timeout':
        lab.violate('INFLIGHT_REQUEST_HUNG_ACROSS_TRANSITION')
    # 한 stream 안에서 세대 별칭이 바뀌면 한 요청이 두 세대를 소비한 것이다.
    if len(first.get('models', [])) > 1:
        lab.violate('MIXED_GENERATIONS_IN_ONE_REQUEST', models=first['models'])
    models = {r.get('model') for r in rows if r.get('outcome') == 200}
    lab.note('during-transition', samples=len(rows), models=sorted(m for m in models if m))
    if allowed and models - set(allowed) - {None}:
        lab.violate('DISALLOWED_MODEL_SERVED_DURING_TRANSITION')
    if not rows or rows[-1].get('outcome') != 200:
        lab.miss('TRANSITION_DID_NOT_RETURN_TO_SERVICE')


def _redeploy(lab, values):
    _replace(lab, lab.rollout)


def _live_change(lab, values):
    """선정 변경 경로의 정상 변경과 같은 경로의 부분 실패를 각각 일으킨다.

    세대는 응답의 모델 별칭으로만 구분한다. 같은 별칭 안에서 weights·cache 일부만 바뀌는
    혼합은 이 방법으로 보이지 않으므로 두 세대 별칭이 다르지 않으면 범위를 채우지 않는다.
    """
    if not _needs(lab, 'update_manifest', 'faulty_manifest', 'good_manifest', 'change_path', 'generation_models'):
        return
    generations = lab.cfg['generation_models']
    if (type(generations) is not dict or set(generations) != {'before', 'after'}
            or generations['before'] == generations['after']):
        return lab.miss('GENERATION_NOT_DISTINGUISHABLE')
    lab.note('change-path', path=lab.cfg['change_path'], generations=generations)
    _replace(lab, lambda: lab.apply('update_manifest'))
    _rejected(lab, 'PARTIAL_CHANGE_ACCEPTED')


def _rejected(lab, code, marker=None):
    """잘못된 세대는 Ready가 되지 않고, 그동안 나간 응답도 허용 세대에서만 와야 한다."""
    allowed = set(lab.cfg.get('allowed_models') or ())
    lab.apply('faulty_manifest')
    end = lab.clock() + lab.cfg['deadline_ms'] / 1000; served = False; seen = set(); activated = marker is None
    while lab.clock() < end:
        faulty = [p for p in lab._pods() if p['metadata'].get('labels', {}).get('byul.lab/generation') == 'faulty']
        if any(any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in p.get('status', {}).get('conditions', []))
                for p in faulty):
            served = True; break
        if not activated:
            for pod in faulty:
                try:
                    activated = activated or lab.log_contains(pod['metadata']['name'], marker)
                except (OSError, TimeoutError, ValueError):
                    pass
        try:
            row, = lab.requests([lab.chat(timeout=5)])
            if row.get('outcome') == 200:
                seen.add(row.get('model'))
        except (OSError, TimeoutError, ValueError):
            pass
        lab.wait(POLL_SECONDS)
    lab.note('faulty-generation', became_ready=served, served_models=sorted(m for m in seen if m),
        failure_marker_seen=None if marker is None else activated)
    if served:
        lab.violate(code)
    if allowed and seen - allowed - {None}:
        lab.violate('DISALLOWED_MODEL_SERVED_DURING_FAILED_CHANGE')
    if not activated:
        lab.miss('PARTIAL_FAILURE_ACTIVATION_UNVERIFIED')


def _restore_material(lab, values):
    """복원 대상과 승인을 기록하고, 변조 사본 복원이 거부되며 승인 원본 복구 뒤 잔존이 없는지 본다.

    선택 snapshot 분기는 별도 TP1 구성 시험이라 이 시나리오에 넣지 않는다.
    """
    if not _needs(lab, 'faulty_manifest', 'good_manifest', 'restore_objects', 'tampered_objects'):
        return
    approved = lab.cfg['restore_objects']
    before = lab.object_digests(approved)
    lab.note('restore-targets', objects=sorted(approved), matches_approval=before == approved)
    if before != approved:
        return lab.miss('RESTORE_TARGETS_NOT_APPROVED_AT_START')
    lab.normal_request('normal-before')
    _rejected(lab, 'TAMPERED_RESTORE_ACCEPTED')
    lab.restore(); lab.mutated = False
    after = lab.object_digests(approved); residual = lab.references(lab.cfg['tampered_objects'])
    lab.note('after-restore', matches_approval=after == approved, tampered_still_referenced=residual)
    if after != approved:
        lab.violate('RESTORED_MATERIAL_DIFFERS_FROM_APPROVAL')
    if residual:
        lab.violate('TAMPERED_MATERIAL_STILL_REFERENCED', objects=residual)


def _startup_gate(lab, values):
    lab.normal_request('normal-before'); lab.rollout()
    end = lab.clock() + lab.cfg['deadline_ms'] / 1000; premature = 0; samples = 0; ready = False
    while lab.clock() < end:
        ready = bool(lab.ready_pods())
        try:
            row, = lab.requests([lab.chat(timeout=5)])
        except (OSError, TimeoutError, ValueError):
            row = {'outcome': 'error'}
        samples += 1
        if ready and row.get('outcome') != 200:
            premature += 1
        if ready and row.get('outcome') == 200:
            break
        lab.wait(POLL_SECONDS)
    lab.note('startup', samples=samples, ready_without_service=premature, ready=ready)
    if premature:
        lab.violate('READY_BEFORE_INFERENCE_AVAILABLE', samples=premature)
    if not ready:
        lab.miss('STARTUP_NOT_READY_WITHIN_DEADLINE')


def _partial_start(lab, values):
    """한 구성요소만 실패시킨 세대. 그 실패가 실제로 일어났다는 로그 표식이 있어야 범위가 찬다."""
    if _needs(lab, 'faulty_manifest', 'good_manifest', 'failure_marker'):
        _rejected(lab, 'PARTIAL_INITIALIZATION_REPORTED_READY', lab.cfg['failure_marker'])


FAULTS = {'death': (POSIX_SIGKILL, 'rank_death_detection_ms'), 'delay': (POSIX_SIGSTOP, 'rank_delay_detection_ms'),
    'communication-stall': (POSIX_SIGSTOP, 'rank_communication_stall_detection_ms')}


def _rank_faults(lab, values):
    for fault, (number, key) in FAULTS.items():
        _one_fault(lab, fault, lambda: lab.signal_worker(number), _limit(values, key, lab), number)
    injected = {}
    def exception():
        injected['done'] = lab.touch_fault()
    _one_fault(lab, 'exception', exception, _limit(values, 'rank_exception_detection_ms', lab), None)
    if not injected.get('done'):
        lab.miss('CONFIG_REQUIRED:fault_directory')


def _one_fault(lab, fault, inject, limit_ms, number):
    """긴 요청이 진행 중일 때 주입한다. 주입 전에 끝난 요청은 근거로 쓰지 않는다."""
    lab.normal_request('normal-before-' + fault)
    began = lab.clock()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(lab.requests, [lab.chat('fault ' * 16, max_tokens=256, timeout=limit_ms / 1000 + 5)])
            lab.wait(POLL_SECONDS); injected_ms = (lab.clock() - began) * 1000; inject()
            row, = running.result()
    except (OSError, TimeoutError, ValueError):
        return lab.miss('FAULT_NOT_INJECTED:' + fault)
    finally:
        if number == POSIX_SIGSTOP:
            try:
                lab.signal_worker(POSIX_SIGCONT)
            except (OSError, TimeoutError, ValueError):
                lab.miss('FAULT_NOT_RELEASED:' + fault)
    lab.note('fault-' + fault, outcome=row['outcome'], elapsed_ms=row['elapsed_ms'], complete=row['complete'], injected_ms=int(injected_ms))
    if row['outcome'] == 200 and row['complete'] and row['elapsed_ms'] < injected_ms:
        lab.miss('FAULT_AFTER_REQUEST_FINISHED:' + fault)
    elif row['outcome'] == 'timeout' or row['elapsed_ms'] > limit_ms:
        lab.violate('FAULT_NOT_DETECTED_BEFORE_DEADLINE', fault=fault, elapsed_ms=row['elapsed_ms'])
    elif row['outcome'] == 200 and not row['complete']:
        lab.violate('INCOMPLETE_WORK_REPORTED_AS_SUCCESS', fault=fault)
    lab.restore()


def _disconnect(lab, values):
    lab.normal_request('normal-before')
    row, = lab.requests([lab.chat('cancel', max_tokens=256, stream=True, close_after_bytes=64, timeout=30)])
    lab.note('disconnect', outcome=row['outcome'], closed_early=row['closed_early'])
    if not row['closed_early']:
        lab.miss('DISCONNECT_NOT_ACHIEVED')
    if lab.wait_idle(_limit(values, 'termination_max_delay_ms', lab)) is False:
        lab.violate('WORK_CONTINUED_PAST_TERMINATION_DEADLINE')
    lab.normal_request('normal-after')


def _worker_cleanup(lab, values):
    lab.normal_request('normal-before'); before = lab.census()
    lab.signal_worker(POSIX_SIGKILL); lab.wait(POLL_SECONDS)
    try:
        after = lab.census(); left = len(after['processes']); shm = after['shm_entries']
    except (OSError, TimeoutError, ValueError):
        left = shm = None
    lab.note('worker-failure', processes_before=len(before['processes']), processes_after=left,
        shm_before=before['shm_entries'], shm_after=shm)
    lab.rollout()
    if not lab.wait_ready():
        lab.violate('NEXT_START_BLOCKED_AFTER_WORKER_FAILURE')
    lab.mutated = False
    if lab.normal_request('normal-after-restart') is None:
        lab.violate('NEXT_REQUEST_FAILED_AFTER_RESTART')


def _retry(lab, values):
    """끊긴 요청 하나가 엔진 입력 토큰을 몇 번 소비했는지 정상 요청 기준으로 잰다."""
    name = 'vllm:prompt_tokens_total'; prompt = 'retry ' * 32
    lab.normal_request('normal-before')
    first = lab.metric(name)
    if first is None:
        return lab.miss('ATTEMPT_COUNTER_METRIC_UNAVAILABLE')
    lab.requests([lab.chat(prompt, timeout=30)])
    single = lab.metric(name) - first
    before = lab.metric(name)
    lab.requests([lab.chat(prompt, max_tokens=64, stream=True, close_after_bytes=64, timeout=30)])
    if lab.wait_idle(lab.cfg['deadline_ms']) is False:
        lab.miss('DISCONNECTED_WORK_DID_NOT_FINISH')
    attempts = (lab.metric(name) - before) / single if single > 0 else None
    lab.note('disconnect-attempts', engine_attempts=attempts, client_attempts=1)
    if attempts is None or attempts < 1:
        lab.miss('ATTEMPT_COUNT_UNAVAILABLE')
    elif attempts >= 2 and values.get('retry_policy_mode') in (None, 'forbidden'):
        lab.violate('FORBIDDEN_AUTOMATIC_RETRY_OBSERVED', engine_attempts=attempts)
    lab.normal_request('normal-after')


def _profiler(lab, values):
    if not _needs(lab, 'trace_directory'):
        return
    lab.normal_request('normal-before'); directory = lab.cfg['trace_directory']
    def control(path):
        row, = lab.requests([{'method': 'POST', 'path': path, 'body': None, 'timeout': 60}])
        return row['outcome']
    lab.mutated = True
    started = control('/start_profile'); lab.normal_request('normal-active'); stopped = control('/stop_profile')
    first = lab._exec(FILE_COUNT, directory)['count']; lab.wait(lab.cfg['deadline_ms'] / 1000)
    second = lab._exec(FILE_COUNT, directory)['count']
    next_start = control('/start_profile'); next_stop = control('/stop_profile'); lab.mutated = False
    lab.note('profiler', start=started, stop=stopped, trace_files=[first, second], next_start=next_start, next_stop=next_stop)
    if stopped == 200 and second != first:
        lab.violate('COLLECTION_CONTINUED_AFTER_STOP')
    if stopped == 200 and next_start != 200:
        lab.violate('NEXT_START_BLOCKED_AFTER_REPORTED_STOP')
    if started != 200:
        lab.miss('PROFILER_NOT_ENABLED')
    lab.normal_request('normal-after')


def _record_state(lab, values):
    lab.normal_request('normal-before')
    lab.signal_worker(POSIX_SIGKILL); lab.wait(POLL_SECONDS)
    row, = lab.requests([{'method': 'GET', 'path': '/health', 'timeout': 10}])
    try:
        alive = len(lab.census()['processes'])
    except (OSError, TimeoutError, ValueError):
        alive = 0
    lab.note('state-after-failure', health=row['outcome'], processes=alive)
    if row['outcome'] == 200 and alive == 0:
        lab.violate('HEALTH_REPORTED_AFTER_WORKERS_GONE')


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
    except (OSError, TimeoutError, ValueError, KeyError) as error:
        lab.miss('SCENARIO_INTERRUPTED:' + type(error).__name__)
    if lab.mutated:
        lab.restore()
    return {'activation_steps': lab.steps, 'activation_violations': lab.violations, 'activation_missing': lab.missing,
        'activation_scope_complete': not lab.missing and bool(lab.normal) and all(lab.normal)}, lab


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
        workdir.mkdir(parents=True, exist_ok=True)
        path = workdir / 'lab-activation.json'; path.write_bytes(canonical_json(facts))
        artifact = DraftArtifact(name=path.name, path=path, collector='lab_activation/0.1.0')
        observations = {**(passive.observations if passive else {}),
            **{k: Observation(value=v, collected=True) for k, v in facts.items()}}
        complete = facts['activation_scope_complete'] or bool(facts['activation_violations'])
        return EvidenceDraft(status=ExecStatus.COMPLETED if complete else ExecStatus.ABORTED,
            data_origin=DataOrigin.EXAMPLE if self.runner is not None else DataOrigin.MEASURED if complete else DataOrigin.DECLARED,
            normal_control={'attempted': bool(lab.normal), 'succeeded': bool(lab.normal) and all(lab.normal),
                'detail': 'Parent-measured normal requests around the activated fault.'},
            challenges=[{'step': step, 'attempted': complete} for step in check.challenge_steps],
            observations=observations, artifacts=[*(passive.artifacts if passive else []), artifact],
            config={**(passive.config if passive else {}), 'lab_activation': {k: v for k, v in self.cfg.items() if k != 'kubectl_context'}},
            started_at=started, finished_at=utcnow(),
            abort_reason=None if complete else 'LAB_ACTIVATION_SCOPE_INCOMPLETE',
            cleanup_errors=[*(passive.cleanup_errors if passive else [])])

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
