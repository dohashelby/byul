"""One bounded ordinary SSE request to the exact owned old endpoint."""

from datetime import datetime, timezone
import json
import os
import re
import select
import subprocess
import time
from pathlib import Path
from secrets import token_bytes
from uuid import uuid4

from guard.executors.lif05_stages import require, timestamp
from guard.models import sha256_hex
from guard.runtime.bounded import capture_pipes, strict_json
import owned_lif05_request_trace as trace


PROGRAM = r"""
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPException
import ipaddress
import json
import os
import sys
import time

address, model, request = sys.argv[1:]
address = str(ipaddress.IPv4Address(address))
conn = HTTPConnection(address, 8000, timeout=10)
clock = os.stat('/proc/self/ns/time')
count = 0
done = False
eof = False
error = None

def emit(event, **fields):
    print(json.dumps(dict(event=event, utc=datetime.now(timezone.utc).isoformat(),
                          monotonic_ns=time.monotonic_ns(), **fields), separators=(',', ':')), flush=True)

emit('started', clock=[clock.st_dev, clock.st_ino])
try:
    conn.request('POST', '/v1/completions', body=json.dumps({
        'model': model, 'prompt': 'List five common colors.', 'max_tokens': 16,
        'temperature': 0, 'stream': True, 'stream_options': {'include_usage': True},
    }), headers={'Content-Type':'application/json', 'Accept':'text/event-stream',
                  'Connection':'close', 'X-Request-Id':request})
    emit('request_sent')
    with conn.getresponse() as response:
        emit('headers', status=response.status)
        if response.status == 200 and response.getheader('Content-Type', '').split(';')[0] == 'text/event-stream':
            for _ in range(128):
                line = response.readline(8193)
                if not line:
                    eof = True
                    break
                if len(line) > 8192:
                    error = 'line_bound'
                    break
                if line.rstrip() == b'data: [DONE]':
                    done = True
                    emit('done')
                elif line.startswith(b'data: '):
                    count += 1
                    if count == 1:
                        emit('first_sse')
            else:
                error = 'frame_bound'
        else:
            error = 'unexpected_http_response'
except (OSError, HTTPException) as exc:
    error = type(exc).__name__
finally:
    conn.close()
    emit('finished', event_count=count, done=done, eof=eof, error=error)
"""


def now():
    return datetime.now(timezone.utc).isoformat()


class ProbeTransportReceipt:
    """Capture client milestones before the stock transport rechecks ownership."""

    def __init__(self, generation, case, request_id, runner=None):
        binding = generation.target['kubernetes']
        self.engine, self.probe = binding['engine'], binding['probe']
        self.context = binding['context']
        self.url = generation.target['engine']['trusted_url'] + '/v1/chat/completions'
        self.label = 'before' if case in ('before', 'inflight') else 'after'
        self.request_id = request_id
        self.runner = runner or capture_pipes
        self.engine_lookups = []
        self.probe_exec_invocations = 0
        self.client_probe = None

    def __call__(self, argv, **options):
        args = argv[3:]
        expected_probe = [f"--namespace={self.probe['namespace']}", 'exec', '-i', self.probe['pod'],
                          f"--container={self.probe['container']}"]
        expected_engine = [f"--namespace={self.engine['namespace']}", 'get', 'pod', self.engine['pod'], '-o', 'json']
        if args[1:2] == ['exec']:
            self.probe_exec_invocations += 1
        started = now() if args == expected_engine else None
        result = self.runner(argv, **options)
        if (argv[1:2] == [f'--context={self.context}'] and args[:5] == expected_probe
                and len(args) == 10 and args[5:8] == ['--','python3','-c']):
            self.client_probe = {'source_sha256':sha256_hex(args[8].encode()),
                'exit_code':result['returncode'], 'pipe_capture_complete':result['capture']['complete'] is True,
                'observation':self.client_observation(result, options)}
        if started is not None:
            complete = result['capture']['complete'] is True
            not_found = rb'Error from server \(NotFound\): pods "' + re.escape(self.engine['pod'].encode()) + rb'" not found\s*'
            if complete and result['returncode'] == 0:
                state = 'selected_pod_document_returned'
            elif complete and result['returncode'] != 0 and re.fullmatch(not_found, result['stderr_bytes']):
                state = 'selected_pod_not_found'
            else:
                state = 'selected_pod_lookup_unverified'
            self.engine_lookups.append({'state': state, 'exit_code': result['returncode'],
                'capture_complete': complete, 'started_at': started, 'finished_at': now()})
        return result

    def client_observation(self, result, options):
        if result['returncode'] != 0 or result['capture']['complete'] is not True:
            return None
        try:
            document = strict_json(options.get('input_bytes'), limit=128*1024)
            request, = document['requests']
            row, = strict_json(result['stdout_bytes'], limit=2*1024**2)
            timeout = document['timeout']
            if type(timeout) not in (int,float) or not 0 < timeout <= 12:
                return None
            limit = int(timeout * 1000)
            if (type(request) is not dict or type(row) is not dict or not 0 < limit <= 12000
                    or request.get('url') != self.url or request.get('headers') != {'X-Request-Id':self.request_id}
                    or any(v.get('id') != self.label or v.get('mode') != 'json' or v.get('method') != 'POST'
                           for v in (request,row))
                    or row.get('timing_basis') != 'batch_monotonic_client_connection_only'):
                return None
        except (KeyError, OverflowError, TypeError, ValueError):
            return None
        dispatched, sent = (row.get(key) for key in ('dispatch_ms','request_sent_ms'))
        if not (type(dispatched) is int and type(sent) is int and 0 <= dispatched <= sent <= limit):
            return None
        headers, status = row.get('headers_ms'), row.get('outcome')
        response_seen = type(headers) is int and sent <= headers <= limit and type(status) is int and 100 <= status <= 599
        return {'timing_basis':row['timing_basis'], 'dispatch_ms':dispatched, 'request_sent_ms':sent,
                'headers_ms':headers if response_seen else None, 'http_status':status if response_seen else None}

    def report(self, stock_report):
        client = self.client_probe
        if (self.probe_exec_invocations != 1 or client is None
                or client['source_sha256'] != stock_report.get('probe_source_sha256')):
            client = None
        blocked = self.probe_exec_invocations == 0 and any(
            row['state'] == 'selected_pod_not_found' for row in self.engine_lookups)
        evidence = ['selected_pod_not_found_before_exec'] if blocked else []
        if client is not None and client['observation'] is not None:
            evidence.append('client_request_sent')
            if client['observation']['http_status'] is not None:
                evidence.append('client_response_headers')
        return {'target_pod_uid': self.engine['pod_uid'], 'probe_pod_uid': self.probe['pod_uid'],
                'probe_exec_invocations': self.probe_exec_invocations,
                'zero_dispatch_verified': blocked, 'client_probe':client,
                'dispatch_evidence':evidence, 'selected_engine_lookups': self.engine_lookups}


EVENT_FIELDS = {
    'observer_opened': {'scope','pid','start_ticks','time_namespace','sensor_sha256'},
    'installed_assignment_verified': {'sources','line'}, 'installed_assignment_unverified': set(),
    'asgi_entered': set(), 'assignment_invoked': set(),
    'assignment_returned': {'external_hmac','internal_hmac','identifier_changed'},
    'asgi_disconnect_received': set(), 'asgi_response_start_returned': {'status'},
    'asgi_terminal_send_returned': set(), 'asgi_app_returned': set(), 'asgi_app_raised': set(),
}
CASE_EVENTS = set(EVENT_FIELDS) - {'observer_opened','installed_assignment_verified','installed_assignment_unverified'}
HEX = re.compile(r'[0-9a-f]{64}')


class TraceRegistration:
    """Install one session key and project only its verified, scoped journal."""

    def __init__(self, attempt, generation, journal):
        binding = generation.target['kubernetes']
        engine, probe = binding['engine'], binding['probe']
        self.generation = generation.key
        self.scope = {'attempt_id':attempt, 'generation':self.generation, 'session':generation.prepared['session'],
                      'pod_uid':engine['pod_uid'], 'container_id':engine['container_id'], 'image':engine['image'],
                      'image_id':engine['image_id'], 'namespace_uid':engine['namespace_uid'],
                      'probe_pod_uid':probe['pod_uid'], 'probe_ip':probe['pod_ip']}
        self.cases = ('before','inflight') if self.generation == 'old' else ('after','recovered')
        self.tokens = {case:uuid4().hex for case in self.cases}
        self.key = token_bytes(32)
        self.slots = {case:trace.request_slot(self.key, case, token) for case,token in self.tokens.items()}
        self.directory = journal / ('lif05-http-' + self.scope['session'])

    def install(self, runtime):
        source = Path(trace.__file__).read_bytes()
        asset = runtime / trace.ASSET
        if asset.exists():
            require(asset.read_bytes() == source, 'LIF05_TRACE_ASSET_MISMATCH')
        else:
            with asset.open('xb') as stream:
                stream.write(source)
            asset.chmod(0o444)
        self.directory.mkdir(mode=0o700)
        os.chown(self.directory, 2000, 0)
        private = runtime / trace.PRIVATE
        fd = os.open(private, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            os.fchown(fd, 2000, 0)
            json.dump({'scope':self.scope,'slots':list(self.slots.values()),'key':self.key.hex()},output)
        return {trace.ASSET:{'bytes':len(source), 'sha256':trace.sha256(source).hexdigest()}}

    def read(self, trials, mounted, *, owner=2000):
        output = {'scope':self.scope, 'sensor_sha256':mounted[trace.ASSET]['sha256'], 'journal_sha256':None,
                  'source_verified':False, 'diagnostic':None, 'events':[], 'cases':[]}
        try:
            raw = trace.checked_read(self.directory / trace.JOURNAL, trace.MAX_JOURNAL, owner=owner)
            events = self.validate(raw, output['sensor_sha256'])
        except FileNotFoundError:
            output['diagnostic'] = 'LIF05_REQUEST_JOURNAL_MISSING'
        except (OSError, TypeError, ValueError, KeyError):
            output['diagnostic'] = 'LIF05_REQUEST_JOURNAL_UNVERIFIED'
        else:
            output.update(journal_sha256=trace.sha256(raw).hexdigest(), events=events,
                          source_verified=any(row['event'] == 'installed_assignment_verified' for row in events))
        for trial in trials:
            case = trial['case_id']
            if case in self.slots:
                require(trial['generation'] == self.generation, 'LIF05_REQUEST_TRACE_GENERATION_MISMATCH')
                output['cases'].append(self.case_result(trial, output['events'], output['source_verified']))
        return output

    def validate(self, raw, mounted_sha):
        events = [json.loads(line) for line in raw.splitlines()]
        require(bool(events and len(events) <= 96 and raw.endswith(b'\n')
                  and events[0]['event'] == 'observer_opened' and events[0]['scope'] == self.scope
                  and events[0]['sensor_sha256'] == mounted_sha), 'LIF05_REQUEST_OBSERVER_IDENTITY')
        arm = []
        calls = {}
        previous_ns = -1
        for index,row in enumerate(events):
            event = row['event']
            case = event in CASE_EVENTS
            fields = {'index','event','utc','monotonic_ns'} | EVENT_FIELDS[event]
            require(set(row) == fields | ({'case_id','header_hmac','invocation'} if case else set())
                    and type(row['index']) is int and row['index'] == index
                    and type(row['monotonic_ns']) is int and row['monotonic_ns'] >= previous_ns,
                    'LIF05_REQUEST_EVENT_SHAPE')
            previous_ns = row['monotonic_ns']
            timestamp(row['utc'])
            if event == 'observer_opened':
                clock = row['time_namespace']
                require(index == 0 and all(type(row[k]) is int and row[k] > 0 for k in ('pid','start_ticks'))
                        and type(clock) is list and len(clock) == 2
                        and all(type(item) is int and item > 0 for item in clock), 'LIF05_REQUEST_PROCESS_IDENTITY')
            if event.startswith('installed_assignment_'):
                arm.append(event)
                require(len(arm) == 1, 'LIF05_REQUEST_ASSIGNMENT_SOURCE_REPEATED')
            if event == 'installed_assignment_verified':
                pins = dict(zip(trace.PATHS, trace.PINS[self.scope['image']], strict=True))
                require(row['sources'] == pins and type(row['line']) is int and row['line'] > 0,
                        'LIF05_REQUEST_ASSIGNMENT_PIN_MISMATCH')
            if case:
                slot = self.slots[row['case_id']]
                invocation = row['invocation']
                require(row['header_hmac'] == slot['header_hmac'] and type(invocation) is int and invocation > 0,
                        'LIF05_REQUEST_CASE_IDENTITY')
                if event == 'asgi_entered':
                    require(invocation not in calls, 'LIF05_REQUEST_DUPLICATE_INVOCATION')
                    calls[invocation] = row['case_id']
                require(calls.get(invocation) == row['case_id'], 'LIF05_REQUEST_INVOCATION_UNVERIFIED')
                if event == 'assignment_returned':
                    require(arm == ['installed_assignment_verified'] and row['external_hmac'] == slot['external_hmac']
                            and type(row['internal_hmac']) is str and HEX.fullmatch(row['internal_hmac'])
                            and type(row['identifier_changed']) is bool, 'LIF05_REQUEST_ASSIGNMENT_UNVERIFIED')
                if event == 'asgi_response_start_returned':
                    require(type(row['status']) is int and 100 <= row['status'] <= 599, 'LIF05_REQUEST_HTTP_STATUS')
        return events

    @staticmethod
    def case_result(trial, events, source_verified):
        selected = [row for row in events if row.get('case_id') == trial['case_id']]
        entries = [row for row in selected if row['event'] == 'asgi_entered']
        began, finished = (timestamp(trial[key]) for key in ('started_at','finished_at'))
        invoked = (len(entries) == 1 and trial['transport_dispatched'] is not False
                   and began <= timestamp(entries[0]['utc']) <= finished)
        assignment_calls = [row for row in selected if row['event'] == 'assignment_invoked']
        assignments = [row for row in selected if row['event'] == 'assignment_returned']
        linked = (invoked and source_verified and len(assignment_calls) == len(assignments) == 1
                  and entries[0]['index'] < assignment_calls[0]['index'] < assignments[0]['index']
                  and timestamp(assignments[0]['utc']) <= finished)
        return {'case_id':trial['case_id'], 'transport_dispatched':True if invoked else trial['transport_dispatched'],
                'request_observer_invoked':invoked, 'asgi_entries':len(entries),
                'assignment_invocation_count':len(assignment_calls), 'assignment_bound':linked,
                'asgi_terminal_send_observed':invoked and any(r['event'] == 'asgi_terminal_send_returned' for r in selected),
                'app_terminal':next((r['event'] for r in selected
                                     if invoked and r['event'] in ('asgi_app_returned','asgi_app_raised')),None)}


def trial(generation, case, token, *, runner=None):
    from guard.executors.lifecycle_transition import LifecycleTransitionExecutor

    transport = ProbeTransportReceipt(generation, case, token, runner=runner)
    started = now()
    observed = LifecycleTransitionExecutor({}, runner=transport)._probe(generation, case, token, 12)
    finished = now()
    receipt = transport.report(observed['transport'])
    evidence = receipt['dispatch_evidence']
    dispatched = False if receipt['zero_dispatch_verified'] else True if evidence else None
    return {'case_id': case, 'generation': generation.key, 'request_id_sha256': sha256_hex(token.encode()),
            'attempted': True, 'started_at': started, 'finished_at': finished,
            'transport_attempt':receipt, 'transport_dispatched':dispatched, 'dispatch_evidence':evidence,
            **observed}


def stream_and_retire(kubeconfig, generation, request, stop):
    target = generation.target
    probe, engine = (target['kubernetes'][role] for role in ('probe', 'engine'))
    command = ['kubectl', '--kubeconfig', str(kubeconfig), '--context', target['kubernetes']['context'],
               '-n', probe['namespace'], 'exec', probe['pod'], '-c', probe['container'], '--',
               'python3', '-I', '-S', '-B', '-c', PROGRAM, engine['pod_ip'],
               target['model_mapping']['served_model_name'], request]
    times = []
    start = now()
    deadline = time.monotonic() + 14
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0) as client:
        try:
            while time.monotonic() < deadline:
                if not select.select([client.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                    break
                line = client.stdout.readline(1025)
                if not line:
                    break
                require(len(line) < 1025 and len(times) < 6, 'LIF05_PROBE_EVENT_BOUND')
                row = json.loads(line)
                times.append(row)
                if row['event'] in ('first_sse', 'finished'):
                    break
            before = time.monotonic_ns()
            stopped = stop()
            try:
                tail, _ = client.communicate(timeout=13)
            except subprocess.TimeoutExpired:
                client.terminate()
                tail, _ = client.communicate(timeout=3)
            require(len(tail) <= 5120, 'LIF05_PROBE_EVENT_BOUND')
            times.extend(json.loads(line) for line in tail.splitlines())
        finally:
            if client.poll() is None:
                client.kill()
                client.communicate(timeout=3)
        exit_code = client.returncode
    table = {row['event']: row for row in times}
    require(len(table) == len(times) and set(table) <= {'started','request_sent','headers','first_sse','done','finished'}
            and all(timestamp(a['utc']) <= timestamp(b['utc']) for a, b in zip(times, times[1:])),
            'LIF05_PROBE_EVENT_INVALID')
    clock = os.stat('/proc/self/ns/time')
    same_clock = table.get('started', {}).get('clock') == [clock.st_dev, clock.st_ino]
    first, done, finished = table.get('first_sse'), table.get('done'), table.get('finished')
    return stopped, {'events': times, 'http_status': table.get('headers', {}).get('status'),
        'request_sent_observed': 'request_sent' in table,
        'probe_exit_code': exit_code, 'started_at': table.get('started', {}).get('utc', start),
        'finished_at': finished['utc'] if finished else now(), 'same_monotonic_namespace': same_clock,
        'stop_call_monotonic_ns': before,
        'client_open_at_stop_call': (first['monotonic_ns'] <= before < finished['monotonic_ns']
                                     if same_clock and first and finished else None),
        'done_before_stop_call': (done['monotonic_ns'] <= before if same_clock and done else None),
        'normal_completion_verified': bool(finished and finished['done'] and finished['eof']
                                           and not finished['error'] and first and exit_code == 0),
        'engine_inflight_at_stop_verified': None, 'max_tokens': 16}
