"""Collect LIF-04 normal-stream and owned container memory observations."""

from pathlib import PurePosixPath
import re
import uuid

from guard.models import sha256_hex, utcnow
from guard.runtime.kubernetes_stream_buffer_http import NORMAL_SLOTS, completion_shape

MEMORY_SOURCE = r'''
import hashlib, json, os, re
from pathlib import Path, PurePosixPath

def read(path, limit):
    with Path(path).open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError()
    return value.decode('ascii')

def membership(pid):
    rows = read('/proc/' + pid + '/cgroup', 4096).splitlines()
    if len(rows) != 1 or not re.fullmatch(r'0::/[A-Za-z0-9_./:-]{0,511}', rows[0]):
        raise ValueError()
    path = PurePosixPath(rows[0][3:])
    if '..' in path.parts or str(path) != rows[0][3:]:
        raise ValueError()
    return path

def context():
    own = membership('self')
    if own != membership('1'):
        raise ValueError()
    namespaces = [(os.stat('/proc/self/ns/' + name).st_ino, os.stat('/proc/1/ns/' + name).st_ino)
                  for name in ('pid', 'mnt', 'cgroup')]
    if any(a != b for a, b in namespaces):
        raise ValueError()
    mounts = []
    for line in read('/proc/self/mountinfo', 65536).splitlines():
        prefix, filesystem = line.split(' - ', 1)
        fields = prefix.split()
        if fields[4] == '/sys/fs/cgroup' and filesystem.split()[0] == 'cgroup2':
            mounts.append((fields[0], PurePosixPath(fields[3])))
    if len(mounts) != 1:
        raise ValueError()
    mount_id, root = mounts[0]
    path = Path('/sys/fs/cgroup') / own.relative_to(root) / 'memory.current'
    if path.resolve(strict=True) != path:
        raise ValueError()
    info = path.stat()
    return path, (namespaces, mount_id, info.st_dev, info.st_ino, str(own))

try:
    path, before = context()
    raw = read(path, 64).strip()
    if not re.fullmatch(r'[0-9]{1,19}', raw) or not 0 <= int(raw) < 2**63 or context() != (path, before):
        raise ValueError()
    print(json.dumps({'schema': 'guard-owned-container-memory/1', 'memory_current_bytes': int(raw),
                      'domain_sha256': hashlib.sha256(json.dumps(before).encode()).hexdigest()}))
except (ValueError, OSError, IndexError):
    print('{"schema":"guard-owned-container-memory/1","unavailable":true}')
'''
MEMORY_SOURCE_SHA256 = sha256_hex(MEMORY_SOURCE.encode())
PROFILE = 'kubernetes-owned-normal-stream-observation-v1'
NORMAL_CONFIG_KEYS = {'target', 'normal_stream_only', 'kubectl_binary', 'max_duration_seconds'}


def _engine_pod_isolated(pod, container):
    spec = pod['spec']
    if spec.get('hostPID') is True or spec.get('shareProcessNamespace') is True:
        return False
    match = [row for row in spec['containers'] if row.get('name') == container]
    if len(match) != 1:
        return False
    protected = [PurePosixPath('/proc'), PurePosixPath('/sys/fs/cgroup')]
    mounts = [PurePosixPath(row['mountPath']) for row in match[0].get('volumeMounts', [])]
    return not any(path.is_relative_to(mount) or mount.is_relative_to(path) for path in protected for mount in mounts)


def memory_sample(transport, stage):
    row = {'stage': stage, 'started_at': utcnow().isoformat(), 'collected': False,
           'memory_current_bytes': None, 'domain_sha256': None, 'source_sha256': MEMORY_SOURCE_SHA256}
    try:
        base = transport.base
        engine = base.binding['engine']
        if not _engine_pod_isolated(base._verify(capture_snapshot=True)['engine'], engine['container']):
            raise ValueError()
        sample = base._call([f"--namespace={engine['namespace']}", 'exec', engine['pod'],
            f"--container={engine['container']}", '--', 'python3', '-I', '-S', '-B', '-c', MEMORY_SOURCE],
            cap=1024, budget=5)
        if not _engine_pod_isolated(base._verify(capture_snapshot=True)['engine'], engine['container']):
            raise ValueError()
        if (type(sample) is not dict or set(sample) != {'schema', 'memory_current_bytes', 'domain_sha256'}
                or sample['schema'] != 'guard-owned-container-memory/1' or type(sample['memory_current_bytes']) is not int
                or not 0 <= sample['memory_current_bytes'] < 2**63 or type(sample['domain_sha256']) is not str
                or re.fullmatch('[0-9a-f]{64}', sample['domain_sha256']) is None):
            raise ValueError()
        row.update(collected=True, memory_current_bytes=sample['memory_current_bytes'], domain_sha256=sample['domain_sha256'])
    except (KeyError, ValueError, TypeError, OSError, RuntimeError):
        row['diagnostic'] = 'STREAM_BUFFER_OWNED_MEMORY_SAMPLE_UNAVAILABLE'
    row['finished_at'] = utcnow().isoformat()
    return row


def collect(ctx, start, end):
    """One normal SSE path and two host-memory samples in its exact Pod scope."""
    token = uuid.uuid4().hex
    trials, samples, missing = [], [], []
    for slot in NORMAL_SLOTS:
        observed = utcnow()
        if not start <= observed <= end:
            missing.append('STREAM_BUFFER_POLICY_WINDOW_UNVERIFIED')
            break
        case = 'lif04-' + slot
        streaming = slot == 'normal'
        request = {'id': case, 'method': 'POST', 'mode': 'sse' if streaming else 'json',
                   'url': ctx.engine_url + '/v1/completions', 'headers': {'X-Request-Id': f'byul-lif04-{token}-{slot}'},
                   'body': {'model': ctx.api_model, 'prompt': 'hello', 'max_tokens': 8 if streaming else 1,
                            'temperature': 0, 'stream': streaming}}
        trial = {'case_id': case, 'started_at': observed.isoformat(), 'normal_shape_verified': False}
        try:
            response, = ctx.cprobe('kubernetes-probe', [request], ctx.timeout)
            observed_fields = ('outcome', 'binding_verified', 'capture_complete', 'body_bytes', 'body_sha256', 'event_count')
            trial.update({key: response[key] for key in observed_fields if key in response})
            shape = (response.get('normal_event_sequence_verified') is True if streaming
                     else completion_shape(response['body'], ctx.api_model))
            trial['normal_shape_verified'] = response['outcome'] == 200 and response['capture_complete'] is True and shape
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            missing.append('STREAM_BUFFER_NORMAL_RESPONSE_UNAVAILABLE')
        finished = utcnow()
        trials.append({**trial, 'finished_at': finished.isoformat()})
        if not trial['normal_shape_verified'] or not start <= finished <= end:
            missing.append('STREAM_BUFFER_NORMAL_CONTROL_INCOMPLETE')
            break
        if slot != 'normal-after':
            samples.append(memory_sample(ctx.cprobe, 'before-stream' if slot == 'normal-before' else 'after-stream'))
    same_domain = (len(samples) == 2 and all(sample['collected'] for sample in samples)
                   and samples[0]['domain_sha256'] == samples[1]['domain_sha256'])
    if not same_domain:
        missing.append('STREAM_BUFFER_SAME_OWNED_MEMORY_DOMAIN_UNVERIFIED')
    transport = ctx.cprobe.report()
    if transport['collection_gap']:
        missing.append('STREAM_BUFFER_TRANSPORT_INCOMPLETE')
    ctx._buffer_cleanup = transport['cleanup_errors']
    ctx._buffer_gap = bool(missing or ctx._buffer_cleanup)
    ctx._buffer_diagnostic_codes = tuple(missing)
    observed = {'profile': PROFILE, 'trials': trials, 'container_memory_samples': samples,
                'memory_scope': 'engine_container_cgroup_v2_including_collector',
                'same_memory_domain_verified': same_domain, 'per_connection_peak_or_release_verified': False}
    limitations = ('per_connection_buffer_and_peak_unobserved', 'send_wait_and_cleanup_unobserved',
                   'slow_receiver_and_debug_buffering_unobserved')
    values = {'activation_steps': [observed], 'activation_violations': [],
              'activation_missing': [*limitations, *missing], 'activation_scope_complete': False}
    observations = {key: {'value': value, 'collected': True} for key, value in values.items()}
    summary = {'fixture': {'profile': PROFILE, 'memory_source_sha256': MEMORY_SOURCE_SHA256,
                          'probe_source_sha256': transport['probe_source_sha256']},
               'target_sha256': transport['target_sha256'], 'observations': values}
    control = {'attempted': bool(trials), 'succeeded': None,
               'detail': 'Observed normal request completion and sampled owned container memory around the stream.'}
    return observations, control, [], summary
