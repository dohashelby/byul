"""Closed LIF07 health/chat samples. HTTP facts never establish worker readiness."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re
import threading
import math
import time

from guard.models import canonical_json, sha256_hex, utcnow
from .bounded import strict_json
from .kubernetes_http import KubernetesHTTP, KubernetesBindingError, REQUEST_BINDING_PROFILE
from .kubernetes_startup_binding import read_startup_binding

PROFILE = 'kubernetes-owned-worker-readiness-http-v1'
SCHEMA = 'guard-kubernetes-worker-readiness-http/1'
SLOTS = ('health-before', 'canary', 'health-after')
POLICY_KEYS = frozenset({'readiness_environment_sha256', 'readiness_target_sha256',
    'readiness_session', 'readiness_cohort_sha256', 'readiness_validity_start', 'readiness_validity_end'})
NETWORK_OUTCOMES = frozenset({'refused', 'timeout', 'unreachable', 'dns_error', 'error', 'probe_error'})
MAX_BODY = 128 * 1024
FLAGS = ('producer_authenticated', 'startup_causal_binding_verified', 'expected_ranks_verified',
    'runtime_hash_approval', 'upper_READY_delivery_verified', 'service_gate_verified',
    'worker_model_GPU_complete', 'fault_or_delay_activated', 'whole_scope_complete')
CLOCK_DOMAIN = 'collector UTC only; no native/probe/host clock translation'
LIMITATIONS = ('선정 라벨·ENV/Pod imageID 결합은 승인·이미지 내용/ancestry·실제 worker source 인증이 아니다.',
    'HTTP200·PodReady·응답 형상은 모든 rank/model/GPU·상위 READY/서비스 접수 완료가 아니다.',
    'session/cohort SHA와 실행 중 읽은 source SHA는 실제 native 인과·시계 receipt나 코드 승인값이 아니다.')
DIAGNOSTICS = frozenset({'WORKER_READINESS_ENGINE_METADATA_UNAVAILABLE', 'WORKER_READINESS_PARENT_CLEANUP_UNVERIFIED',
    'WORKER_READINESS_RESPONSE_PARTIAL', 'WORKER_READINESS_SAMPLE_UNAVAILABLE',
    'WORKER_READINESS_FINAL_BINDING_UNAVAILABLE', 'WORKER_READINESS_COLLECTOR_CLOCK_UNVERIFIED'})


def _stamp(value):
    if type(value) is not str or not 1 <= len(value) <= 40:
        raise ValueError
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed


def _digest(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _policy(values, environment_sha256, target_sha256):
    try:
        if type(values) is not dict or set(values) != POLICY_KEYS:
            raise ValueError
        if (not _digest(values['readiness_environment_sha256']) or values['readiness_environment_sha256'] != environment_sha256
                or not _digest(values['readiness_target_sha256']) or values['readiness_target_sha256'] != target_sha256
                or not _digest(values['readiness_cohort_sha256']) or type(values['readiness_session']) is not str
                or re.fullmatch('[0-9a-f]{32}', values['readiness_session']) is None):
            raise ValueError
        start, end = _stamp(values['readiness_validity_start']), _stamp(values['readiness_validity_end'])
        if not 0 < (end - start).total_seconds() <= 600 or not start <= utcnow() <= end:
            raise ValueError
        return start, end
    except (KeyError, TypeError, ValueError):
        raise ValueError('WORKER_READINESS_POLICY_INVALID') from None


def _response(value, request, api_model):
    if (type(value) is not list or len(value) != 1 or type(value[0]) is not dict
            or tuple(value[0].get(k) for k in ('id', 'method', 'mode')) != (request['id'], request['method'], 'json')):
        raise ValueError('WORKER_READINESS_RESPONSE_INVALID')
    value = value[0]
    status = value.get('outcome')
    if not (type(status) is int and 100 <= status <= 599 or type(status) is str and status in NETWORK_OUTCOMES):
        raise ValueError('WORKER_READINESS_RESPONSE_INVALID')
    text, count = value.get('body'), value.get('body_bytes')
    valid_text = type(text) is str and len(text.encode('utf-8')) <= MAX_BODY
    complete = (valid_text and type(count) is int and count == len(text.encode('utf-8'))
        and value.get('body_sha256') == sha256_hex(text.encode('utf-8'))
        and value.get('capture_complete') is True and value.get('eof') is True
        and value.get('utf8_valid') is True and value.get('body_truncated') is False)
    complete = complete and type(status) is int
    shape = None if request['method'] == 'GET' else False
    if request['method'] == 'POST' and complete and status == 200:
        try:
            body = strict_json(text.encode('utf-8'), limit=MAX_BODY)
            choices = body.get('choices') if type(body) is dict else None
            shape = (type(body) is dict and body.get('model') == api_model and type(choices) is list and len(choices) == 1
                and type(choices[0]) is dict and type(choices[0].get('message')) is dict
                and type(choices[0]['message'].get('content')) is str and bool(choices[0]['message']['content']))
        except (TypeError, ValueError):
            shape = False
    # Raw body and its hash are validated in memory, then discarded. Extra
    # response/header/error fields never enter the saved packet or report.
    return {'outcome': status, 'probe_document_validated': True, 'http_reply_complete': bool(complete),
            'probe_result_complete': bool(complete) if type(status) is int else True,
            'retained_body_bytes': count if valid_text and type(count) is int and count == len(text.encode('utf-8')) else None,
            'normal_shape_verified': shape, 'health_status_200': status == 200 and bool(complete) if request['method'] == 'GET' else None}


class KubernetesWorkerReadinessHTTP:
    profile = PROFILE

    def __init__(self, target, env, *, runner=None, duration=120, binary='kubectl', clock=None):
        if (type(target) is not dict or target.get('lab') is not True or env.engine.name != 'vllm'
                or env.engine.version != '0.31.0' or not _digest(env.model.inventory_sha256)
                or type(target.get('model_mapping')) is not dict
                or target['model_mapping'].get('schema') != 'guard.model-request-mapping/1'):
            raise KubernetesBindingError()
        selected_clock, last_clock = time.monotonic if clock is None else clock, [None]
        if not callable(selected_clock):
            raise KubernetesBindingError()

        def bounded_clock():
            try:
                value = selected_clock()
                if (type(value) not in (int, float) or not math.isfinite(value)
                        or last_clock[0] is not None and value < last_clock[0]):
                    raise ValueError
                last_clock[0] = value
                return value
            except Exception:
                raise KubernetesBindingError() from None

        self.base = KubernetesHTTP(target, env, runner=runner, duration=duration, binary=binary,
            profile=REQUEST_BINDING_PROFILE, clock=bounded_clock)
        self.binding = deepcopy(self.base.binding)
        self.environment_ref = env.ref().model_dump(mode='json')
        self.target_sha256 = sha256_hex(canonical_json(target))
        self.api_model = self.base.api_model
        self.engine_url = target['engine']['trusted_url']
        self._lock, self._used, self._last = threading.Lock(), False, None

    def __call__(self, *args, **kwargs):
        raise KubernetesBindingError()

    def _snapshot(self):
        value = read_startup_binding(self.base)
        if value['probe_ready_condition_count'] != 1:
            raise KubernetesBindingError()
        return deepcopy(value['engine_readiness'])

    def sample(self, policy_values, *, sample_id):
        with self._lock:
            start, end = _policy(policy_values, self.environment_ref['sha256'], self.target_sha256)
            if self._used or type(sample_id) is not str or re.fullmatch('[0-9a-f]{32}', sample_id) is None:
                raise ValueError('WORKER_READINESS_SAMPLE_INVALID')
            try:
                collector_source = sha256_hex(Path(__file__).read_bytes())
                binding_source = sha256_hex(Path(__file__).with_name('kubernetes_startup_binding.py').read_bytes())
            except Exception:
                raise ValueError('WORKER_READINESS_SOURCE_UNAVAILABLE') from None
            self._used = True
            packet = {'schema': SCHEMA, 'profile': PROFILE, 'sample_id': sample_id,
                'environment_ref': deepcopy(self.environment_ref), 'target_sha256': self.target_sha256,
                'session': policy_values['readiness_session'], 'cohort_sha256': policy_values['readiness_cohort_sha256'],
                'run_id': self.binding['run_id'], 'binding_sha256': sha256_hex(canonical_json(self.binding)),
                'probe_source_sha256': self.base.source_sha256, 'collector_source_sha256': collector_source,
                'binding_source_sha256': binding_source,
                'data_origin': 'EXAMPLE', 'collector_clock_domain': CLOCK_DOMAIN,
                'started_at': utcnow().isoformat(), 'finished_at': None, 'requests': [], 'api_readiness': [],
                'sampling_complete': False, 'collection_gap': True, 'diagnostic_codes': [], 'cleanup_errors': [],
                'producer_authenticated': False, 'startup_causal_binding_verified': False, 'expected_ranks_verified': False,
                'runtime_hash_approval': False,
                'upper_READY_delivery_verified': False, 'service_gate_verified': False, 'worker_model_GPU_complete': False,
                'fault_or_delay_activated': False, 'whole_scope_complete': False,
                'limitations': list(LIMITATIONS)}
            self._last = packet

            def snapshot(stage):
                entry = {'stage': stage, 'collected_at': utcnow().isoformat(), 'collected': False, 'engine_readiness': None}
                packet['api_readiness'].append(entry)
                if not start <= utcnow() <= end:
                    raise KubernetesBindingError()
                entry.update(engine_readiness=self._snapshot(), collected=True)
                if entry['engine_readiness'] is None:
                    packet['diagnostic_codes'].append('WORKER_READINESS_ENGINE_METADATA_UNAVAILABLE')
                if not start <= utcnow() <= end:
                    entry['collected'] = False
                    raise KubernetesBindingError()
                return entry['engine_readiness']

            try:
                for slot in SLOTS:
                    request_id = 'byul-lif07-http-' + sample_id + '-' + slot
                    request = {'id': 'lif07-http-' + slot, 'mode': 'json', 'method': 'POST' if slot == 'canary' else 'GET',
                        'url': self.engine_url + ('/v1/chat/completions' if slot == 'canary' else '/health'),
                        'headers': {'X-Request-Id': request_id}}
                    if slot == 'canary':
                        request['body'] = {'model': self.api_model, 'messages': [{'role': 'user', 'content': 'hello'}],
                                          'max_tokens': 1, 'temperature': 0, 'stream': False}
                    row = {'slot': slot, 'request_id_sha256': sha256_hex(request_id.encode('utf-8')),
                        'attempted': False, 'probe_document_validated': False, 'probe_result_complete': False, 'transport_complete': False,
                        'outcome': None, 'http_reply_complete': None, 'retained_body_bytes': None,
                        'normal_shape_verified': None, 'health_status_200': None,
                        'binding_before_verified': False, 'binding_after_verified': False,
                        'engine_readiness_before': None, 'engine_readiness_after': None}
                    packet['requests'].append(row)
                    row['engine_readiness_before'] = snapshot(slot + '-before')
                    row['binding_before_verified'] = True
                    budget = min(10, self.base.deadline - self.base.clock(), (end - utcnow()).total_seconds())
                    if budget <= 0:
                        raise KubernetesBindingError()
                    probe = self.binding['probe']
                    row['attempted'] = True

                    def preserve(document):
                        row.update(_response(document, request, self.api_model))

                    self.base._call([f"--namespace={probe['namespace']}", 'exec', '-i', probe['pod'],
                        f"--container={probe['container']}", '--', 'python3', '-c', self.base.source, str(budget)],
                        payload=canonical_json({'requests': [request], 'timeout': budget}),
                        cap=2 * 1024**2, budget=budget, on_document=preserve)
                    row['transport_complete'] = True
                    row['engine_readiness_after'] = snapshot(slot + '-after')
                    row['binding_after_verified'] = True
                    if row['probe_result_complete'] is not True:
                        packet['diagnostic_codes'].append('WORKER_READINESS_RESPONSE_PARTIAL')
                        break
                    if self.base.cleanup_errors:
                        packet['diagnostic_codes'].append('WORKER_READINESS_PARENT_CLEANUP_UNVERIFIED')
                        break
            except Exception:
                packet['diagnostic_codes'].append('WORKER_READINESS_SAMPLE_UNAVAILABLE')
            finally:
                # A failed response, postcheck or attachment must not erase
                # earlier safe facts. This API read never retries HTTP.
                try:
                    snapshot('final')
                except Exception:
                    packet['diagnostic_codes'].append('WORKER_READINESS_FINAL_BINDING_UNAVAILABLE')
                packet['cleanup_errors'] = list(self.base.cleanup_errors)
                packet['finished_at'] = utcnow().isoformat()
                wall_times = [_stamp(packet['started_at'])] + [_stamp(row['collected_at']) for row in packet['api_readiness']] + [_stamp(packet['finished_at'])]
                if any(later < earlier for earlier, later in zip(wall_times, wall_times[1:])):
                    packet['diagnostic_codes'].append('WORKER_READINESS_COLLECTOR_CLOCK_UNVERIFIED')
                packet['sampling_complete'] = (len(packet['requests']) == 3
                    and all(row['probe_document_validated'] and row['probe_result_complete'] and row['transport_complete']
                            and row['binding_before_verified'] and row['binding_after_verified'] for row in packet['requests'])
                    and not packet['diagnostic_codes'] and not packet['cleanup_errors'])
                packet['collection_gap'] = not packet['sampling_complete']
                packet['diagnostic_codes'] = sorted(set(packet['diagnostic_codes']))
            return deepcopy(packet)

    def report(self):
        with self._lock:
            return deepcopy(self._last)


ROOT_FIELDS = frozenset({'schema', 'profile', 'sample_id', 'environment_ref', 'target_sha256', 'session',
    'cohort_sha256', 'run_id', 'binding_sha256', 'probe_source_sha256', 'collector_source_sha256',
    'binding_source_sha256', 'data_origin', 'collector_clock_domain', 'started_at', 'finished_at',
    'requests', 'api_readiness', 'sampling_complete', 'collection_gap', 'diagnostic_codes', 'cleanup_errors',
    'limitations', *FLAGS})
ROW_FIELDS = frozenset({'slot', 'request_id_sha256', 'attempted', 'probe_document_validated', 'probe_result_complete',
    'transport_complete', 'outcome', 'http_reply_complete', 'retained_body_bytes', 'normal_shape_verified',
    'health_status_200', 'binding_before_verified', 'binding_after_verified', 'engine_readiness_before', 'engine_readiness_after'})


def validate_snapshot(value, *, sample_id, environment_ref, target_sha256, session, cohort_sha256, run_id,
                      binding_sha256, expected_sources):
    """Validate the transport envelope against separately reviewed fixed inputs.

    expected_sources must come from a reviewed immutable source/manifest, never
    from this packet or a newly read running target. Even a match authenticates
    neither a worker nor a native startup/HTTP causal relationship.
    """
    def require(condition):
        if not condition:
            raise ValueError

    def readiness(row):
        require(row is None or type(row) is dict and set(row) == {'pod_ready', 'container_ready'}
                and (row['pod_ready'] is None or type(row['pod_ready']) is bool) and type(row['container_ready']) is bool)

    try:
        require(type(value) is dict and set(value) == ROOT_FIELDS and value['schema'] == SCHEMA and value['profile'] == PROFILE)
        ref = value['environment_ref']
        require(type(ref) is dict and set(ref) == {'kind', 'id', 'revision', 'sha256'} and ref == environment_ref
                and ref['kind'] == 'ENV' and type(ref['id']) is str and 1 <= len(ref['id']) <= 256
                and type(ref['revision']) is int and 1 <= ref['revision'] < 2**31 and _digest(ref['sha256']))
        for key, expected in (('target_sha256', target_sha256), ('cohort_sha256', cohort_sha256), ('binding_sha256', binding_sha256)):
            require(_digest(expected) and value[key] == expected)
        require(type(session) is str and re.fullmatch('[0-9a-f]{32}', session) is not None and value['session'] == session)
        require(type(run_id) is str and re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', run_id) is not None and value['run_id'] == run_id)
        require(type(sample_id) is str and re.fullmatch('[0-9a-f]{32}', sample_id) is not None and value['sample_id'] == sample_id)
        require(type(expected_sources) is dict and set(expected_sources) == {'collector', 'binding', 'probe'})
        for key, field in (('collector', 'collector_source_sha256'), ('binding', 'binding_source_sha256'), ('probe', 'probe_source_sha256')):
            require(_digest(expected_sources[key]) and value[field] == expected_sources[key])
        require(value['data_origin'] == 'EXAMPLE' and value['collector_clock_domain'] == CLOCK_DOMAIN
                and type(value['limitations']) is list and value['limitations'] == list(LIMITATIONS))
        require(all(value[key] is False for key in FLAGS))
        began, ended = _stamp(value['started_at']), _stamp(value['finished_at'])
        codes, cleanup = value['diagnostic_codes'], value['cleanup_errors']
        require(type(codes) is list and len(codes) <= len(DIAGNOSTICS)
                and all(type(code) is str and code in DIAGNOSTICS for code in codes) and len(codes) == len(set(codes)))
        require(type(cleanup) is list and len(cleanup) <= 1
                and all(code == 'KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED' for code in cleanup))
        requests, snapshots = value['requests'], value['api_readiness']
        require(type(requests) is list and 1 <= len(requests) <= 3 and type(snapshots) is list and 1 <= len(snapshots) <= 7)
        stages = [item for slot in SLOTS for item in (slot + '-before', slot + '-after')] + ['final']
        snapshot_map, previous_stage = {}, -1
        for row in snapshots:
            require(type(row) is dict and set(row) == {'stage', 'collected_at', 'collected', 'engine_readiness'})
            require(type(row['stage']) is str and row['stage'] in stages and stages.index(row['stage']) > previous_stage)
            previous_stage = stages.index(row['stage'])
            _stamp(row['collected_at']);require(type(row['collected']) is bool);readiness(row['engine_readiness'])
            snapshot_map[row['stage']] = row
            if row['collected'] and row['engine_readiness'] is None:
                require('WORKER_READINESS_ENGINE_METADATA_UNAVAILABLE' in codes)
        require('final' in snapshot_map)
        wall_times = [began] + [_stamp(row['collected_at']) for row in snapshots] + [ended]
        if any(later < earlier for earlier, later in zip(wall_times, wall_times[1:])):
            require('WORKER_READINESS_COLLECTOR_CLOCK_UNVERIFIED' in codes)
        if not snapshot_map['final']['collected']:
            require('WORKER_READINESS_FINAL_BINDING_UNAVAILABLE' in codes)
        require(all(stage == 'final' or stage.rsplit('-', 1)[0] in SLOTS[:len(requests)] for stage in snapshot_map))
        for index, row in enumerate(requests):
            require(type(row) is dict and set(row) == ROW_FIELDS and row['slot'] == SLOTS[index])
            wire_id = 'byul-lif07-http-' + value['sample_id'] + '-' + row['slot']
            require(row['request_id_sha256'] == sha256_hex(wire_id.encode('utf-8')))
            for key in ('attempted', 'probe_document_validated', 'probe_result_complete', 'transport_complete', 'binding_before_verified', 'binding_after_verified'):
                require(type(row[key]) is bool)
            require(row['http_reply_complete'] is None or type(row['http_reply_complete']) is bool)
            require(row['normal_shape_verified'] is None or type(row['normal_shape_verified']) is bool)
            require(row['health_status_200'] is None or type(row['health_status_200']) is bool)
            require(row['retained_body_bytes'] is None or type(row['retained_body_bytes']) is int and 0 <= row['retained_body_bytes'] <= MAX_BODY)
            readiness(row['engine_readiness_before']);readiness(row['engine_readiness_after'])
            require(row['slot'] + '-before' in snapshot_map)
            require(not row['attempted'] or row['binding_before_verified'])
            for slot, key, field in (('before', 'binding_before_verified', 'engine_readiness_before'), ('after', 'binding_after_verified', 'engine_readiness_after')):
                if row[key]:
                    snapshot = snapshot_map.get(row['slot'] + '-' + slot)
                    require(snapshot is not None and snapshot['collected'] and snapshot['engine_readiness'] == row[field])
            require(not row['transport_complete'] or row['probe_document_validated'])
            require(not row['binding_after_verified'] or row['binding_before_verified'] and row['transport_complete'])
            if row['probe_document_validated']:
                status = row['outcome']
                require(row['attempted'] and row['binding_before_verified']
                        and (type(status) is int and 100 <= status <= 599 or type(status) is str and status in NETWORK_OUTCOMES))
                require(type(row['http_reply_complete']) is bool)
                require(row['probe_result_complete'] == (row['http_reply_complete'] if type(status) is int else True))
                if type(status) is str:
                    require(row['http_reply_complete'] is False)
                if row['slot'] == 'canary':
                    require(type(row['normal_shape_verified']) is bool and row['health_status_200'] is None)
                    require(not row['normal_shape_verified'] or status == 200 and row['http_reply_complete'])
                else:
                    require(row['normal_shape_verified'] is None and row['health_status_200'] == (status == 200 and row['http_reply_complete']))
            else:
                require(row['outcome'] is None and row['http_reply_complete'] is None and row['retained_body_bytes'] is None
                        and row['normal_shape_verified'] is None and row['health_status_200'] is None and row['probe_result_complete'] is False)
        complete = (len(requests) == 3 and all(row['probe_document_validated'] and row['probe_result_complete'] and row['transport_complete']
            and row['binding_before_verified'] and row['binding_after_verified'] for row in requests) and not codes and not cleanup)
        require(type(value['sampling_complete']) is bool and value['sampling_complete'] == complete
                and type(value['collection_gap']) is bool and value['collection_gap'] == (not complete))
        return deepcopy(value)
    except (KeyError, TypeError, ValueError):
        raise ValueError('WORKER_READINESS_SNAPSHOT_INVALID') from None
