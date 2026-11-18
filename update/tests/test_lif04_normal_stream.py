"""The LIF-04 normal branch uses the shipped collector with an isolated fixture."""

from datetime import timedelta
import json
from pathlib import Path
import os
import subprocess
import sys

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.executors import normal_stream_buffer as normal, stream_probe
from guard.executors.kubernetes_stream_buffer import KubernetesStreamBufferExecutor
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from tests.test_model_preparation_integration import FullWorld, full_inputs
from tests.test_stream_probe import _server

PACK_PATH = Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE'
PRIVATE_OUTPUT = 'EXAMPLE-normal-response-content'


def reply(connection, raw, _index, _stop):
    request = json.loads(raw.partition(b'\r\n\r\n')[2])
    output = canonical_json({'model': request['model'], 'choices': [{'text': PRIVATE_OUTPUT}]})
    if request['stream']:
        output = b'data: ' + output + b'\n\ndata: [DONE]\n\n'
    content_type = b'text/event-stream' if request['stream'] else b'application/json'
    connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: ' + content_type
                       + b'\r\nContent-Length: ' + str(len(output)).encode() + b'\r\nConnection: close\r\n\r\n' + output)


class World(FullWorld):
    def __init__(self, target, endpoint, mode):
        super().__init__(target)
        self.endpoint = endpoint
        self.mode = mode
        self.memory_reads = 0
        self.requests = []

    def runner(self, argv, **kwargs):
        if 'exec' in argv:
            if argv[-1] == normal.MEMORY_SOURCE:
                assert '--container=engine' in argv and 'input_bytes' not in kwargs
                self.memory_reads += 1
                domain = 'b' if self.mode == 'changed-domain' and self.memory_reads == 2 else 'a'
                value = {'schema': 'guard-owned-container-memory/1', 'memory_current_bytes': 1000 + self.memory_reads,
                         'domain_sha256': domain * 64}
                if self.mode == 'missing-memory' and self.memory_reads == 2:
                    value = {'schema': 'guard-owned-container-memory/1', 'unavailable': True}
            else:
                request, = json.loads(kwargs['input_bytes'])['requests']
                assert request['url'] == self.target['engine']['trusted_url'] + '/v1/completions'
                assert 'read_profile' not in request
                self.requests.append(request['id'])
                request['url'] = self.endpoint + '/v1/completions'  # Test loopback only.
                value = stream_probe.collect([request], 2)
                if self.mode == 'wrong-stream' and request['mode'] == 'sse':
                    value[0]['events'][0]['data'] = '{"model":"different-model","choices":[{"text":"sample"}]}'
            return {'returncode': 0, 'stdout_bytes': canonical_json(value), 'stderr_bytes': b'', 'capture': {'complete': True}}
        result = super().runner(argv, **kwargs)
        document = json.loads(result['stdout_bytes'])
        if self.mode == 'shared-pid' and document.get('kind') == 'Pod' and document['metadata']['name'] == self.target['kubernetes']['engine']['pod']:
            document['spec']['shareProcessNamespace'] = True
        result['stdout_bytes'] = canonical_json(document)
        return result


def configuration(tmp_path):
    env, target = full_inputs()
    target['lab'] = False
    doc = tmp_path / 'engine.json'
    doc.write_bytes(canonical_json(target))
    digest = sha256_hex(canonical_json(target))
    start = utcnow()
    values = {'buffer_environment_sha256': env.ref().sha256, 'buffer_target_sha256': digest,
        'buffer_validity_start': (start - timedelta(minutes=1)).isoformat(),
        'buffer_validity_end': (start + timedelta(minutes=2)).isoformat(),
        'buffer_max_write_bytes': 1048576, 'buffer_max_process_rss_bytes': 2**34,
        'buffer_max_send_wait_ms': 5000, 'buffer_release_deadline_ms': 5000,
        'buffer_read_chunk_bytes': 4096, 'buffer_read_interval_ms': 50, 'buffer_read_max_reads': 32,
        'buffer_close_chunk_bytes': 4096, 'buffer_close_interval_ms': 50, 'buffer_close_max_reads': 8,
        'buffer_debug_mode': 'unverified'}
    return env, target, {'target': str(doc), 'normal_stream_only': True}, values


@pytest.mark.parametrize('mode', ['normal', 'changed-domain', 'missing-memory', 'shared-pid', 'wrong-stream'])
def test_normal_stream_and_memory_are_collected_without_slow_slots(tmp_path, mode):
    env, target, cfg, values = configuration(tmp_path)
    check = load_pack(PACK_PATH).checks['LIF-04']
    with _server(reply) as (endpoint, _):
        world = World(target, endpoint, mode)
        executor = KubernetesStreamBufferExecutor(cfg, runner=world.runner, environ={},
                                                   expected_target_sha256=values['buffer_target_sha256'])
        draft = executor.run(check, env, values, VllmAdapter(), tmp_path / 'work')
    assert draft.data_origin == 'EXAMPLE'
    assert draft.status == ('COMPLETED' if mode == 'normal' else 'ABORTED')
    assert draft.observations['activation_scope_complete']['value'] is False
    assert draft.observations['activation_violations']['value'] == []
    row, = draft.observations['activation_steps']['value']
    assert row['same_memory_domain_verified'] is (mode == 'normal')
    assert [t['case_id'] for t in row['trials']] == world.requests
    assert world.requests == (['lif04-normal-before', 'lif04-normal'] if mode == 'wrong-stream'
                              else ['lif04-normal-before', 'lif04-normal', 'lif04-normal-after'])
    assert world.memory_reads == (0 if mode == 'shared-pid' else 1 if mode == 'wrong-stream' else 2)
    if mode == 'normal':
        samples = row['container_memory_samples']
        assert [sample['memory_current_bytes'] for sample in samples] == [1001, 1002]
        assert row['trials'][1]['event_count'] == 2 and len(row['trials'][1]['body_sha256']) == 64
    assert PRIVATE_OUTPUT not in draft.artifacts[0].path.read_text()


@pytest.mark.parametrize('bad', ['missing-pin', 'prepared', 'wrong-check', 'mixed-common'])
def test_normal_stream_requires_exact_readonly_admission_before_io(tmp_path, bad):
    env, _, cfg, values = configuration(tmp_path)
    check = load_pack(PACK_PATH).checks['LIF-03' if bad == 'wrong-check' else 'LIF-04']
    if bad == 'prepared':
        cfg['prepared'] = 'must-not-read.json'
    if bad == 'mixed-common':
        cfg['common_inputs'] = {}

    def no_read(*args, **kwargs):
        raise AssertionError('invalid normal mode dispatched I/O')

    pin = None if bad == 'missing-pin' else values['buffer_target_sha256']
    executor = KubernetesStreamBufferExecutor(cfg, runner=no_read, environ={}, expected_target_sha256=pin)
    with pytest.raises(ValueError, match='STREAM_BUFFER_.*CONFIGURATION_INVALID'):
        executor.run(check, env, values, VllmAdapter(), tmp_path / 'work')


@pytest.mark.parametrize('same_group', [True, False])
def test_memory_source_reads_the_selected_cgroup_fixture(tmp_path, same_group):
    proc, cgroup = tmp_path / 'proc', tmp_path / 'cgroup'
    for process in ('self', '1'):
        (proc / process / 'ns').mkdir(parents=True)
    for namespace in ('pid', 'mnt', 'cgroup'):
        source = proc / 'self' / 'ns' / namespace
        source.touch()
        os.link(source, proc / '1' / 'ns' / namespace)
    (proc / 'self/cgroup').write_text('0::/owned/engine\n', encoding='ascii')
    (proc / '1/cgroup').write_text('0::/owned/engine\n' if same_group else '0::/owned/other\n', encoding='ascii')
    (proc / 'self/mountinfo').write_text(f'29 24 0:27 / {cgroup} ro - cgroup2 cgroup rw\n', encoding='ascii')
    (cgroup / 'owned/engine').mkdir(parents=True)
    (cgroup / 'owned/engine/memory.current').write_text('424242\n', encoding='ascii')
    # The shipped producer receives no path arguments. This fixture substitutes only the roots.
    replacements = {'/proc/': str(proc) + '/', '/proc/self/ns/': str(proc / 'self/ns') + '/',
                    '/proc/1/ns/': str(proc / '1/ns') + '/', '/proc/self/mountinfo': str(proc / 'self/mountinfo'),
                    '/sys/fs/cgroup': str(cgroup)}
    script = normal.MEMORY_SOURCE
    for original, replacement in replacements.items():
        script = script.replace(repr(original), repr(replacement))
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script], capture_output=True, check=True, timeout=5)
    packet = json.loads(result.stdout)
    assert packet['schema'] == 'guard-owned-container-memory/1'
    if same_group:
        assert packet['memory_current_bytes'] == 424242 and len(packet['domain_sha256']) == 64
    else:
        assert packet == {'schema': 'guard-owned-container-memory/1', 'unavailable': True}
