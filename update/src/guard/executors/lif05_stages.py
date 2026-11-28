"""Bind an owned LIF-05 stopped redeployment to its real, sequential generations."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import re
from secrets import token_bytes

from guard.models import canonical_json, sha256_hex


SETTING = 'staged_lif05_sha256'
CASES = {'before': 'old', 'inflight': 'old', 'during': 'old', 'after': 'new', 'recovered': 'new'}
PHASES = ('before', 'during', 'after', 'recovered')
_POD_TIME_PRECISION = timedelta(seconds=1)
LIF05_STEPS = ('기존 요청 하나 진행', '새 묶음 재배포', '각 요청의 model generation·drain·라우팅 추적')
OWNER_FIELDS = frozenset({
    'context', 'cluster_uid', 'namespace', 'namespace_uid', 'deployment', 'deployment_uid', 'replicaset',
    'replicaset_uid', 'template_sha256', 'launch_sha256', 'node', 'image', 'image_id', 'version',
    'model_inventory_sha256', 'model_sha256', 'probe_pod_uid',
})


def require(value, diagnostic):
    if not value:
        raise ValueError(diagnostic)


def timestamp(value):
    result = datetime.fromisoformat(value)
    require(result.utcoffset() is not None, 'LIF05_STAGE_CLOCK_UNVERIFIED')
    return result.astimezone(timezone.utc)


def fingerprint(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def stock_response_attributed(trial):
    rows = trial.get('transport', {}).get('requests', [])
    label = 'before' if trial['case_id'] == 'before' else 'after'
    return len(rows) == 1 and rows[0].get('id') == label and rows[0].get('binding_verified_before_after') is True


def combine_dispatch(trial, case=None):
    """Keep physical dispatch and stock endpoint attribution independent."""
    observed = case is not None and case['request_observer_invoked'] is True
    client = trial['transport_dispatched']
    require(client is None or type(client) is bool, 'LIF05_STAGE_CLIENT_DISPATCH_INVALID')
    combined = True if observed else client
    require(case is None or (case['transport_dispatched'] is combined and (not observed or client is not False)),
            'LIF05_STAGE_REQUEST_TRACE_SCOPE_INVALID')
    attributed = observed if trial['case_id'] == 'inflight' else stock_response_attributed(trial)
    return {**trial, 'transport_dispatched':combined, 'client_transport_dispatched':client,
            'same_generation_asgi_received':observed, 'stock_generation_attribution_verified':attributed,
            'normal_shape_verified':trial['normal_shape_verified'] is True and attributed,
            'dispatch_evidence':[*trial['dispatch_evidence'], *(['same_generation_asgi_entered'] if observed else [])]}


def generation_record(generation, owner, observed_at):
    """Use the original target, prepared session, and exact K8s owner identity."""
    binding = generation.target['kubernetes']['engine']
    require(set(owner) == OWNER_FIELDS
            and all(type(value) is str and value for value in owner.values())
            and all(fingerprint(owner[key]) for key in (
                'template_sha256', 'launch_sha256', 'model_inventory_sha256', 'model_sha256'))
            and generation.target['lab'] is False
            and generation.env.data_origin != 'EXAMPLE'
            and generation.target['kubernetes']['context'] == owner['context']
            and generation.target['kubernetes']['probe']['pod_uid'] == owner['probe_pod_uid']
            and all(binding[key] == owner[key] for key in ('namespace', 'namespace_uid', 'node', 'image', 'image_id'))
            and binding['restart_count'] == 0 and generation.env.engine.version == owner['version']
            and generation.env.engine.image_digest == owner['image']
            and generation.env.model.inventory_sha256 == owner['model_inventory_sha256']
            and sha256_hex(canonical_json(generation.env.model.model_dump(mode='json'))) == owner['model_sha256']
            and timestamp(binding['started_at']) <= timestamp(observed_at), 'LIF05_STAGE_OWNED_GENERATION_UNVERIFIED')
    return {
        'owner': deepcopy(owner), 'generation': generation.key, 'environment_ref': generation.env.ref().model_dump(mode='json'),
        'target_sha256': sha256_hex(canonical_json(generation.target)),
        'prepared_sha256': sha256_hex(canonical_json(generation.prepared)), 'session': generation.prepared['session'],
        'pod': binding['pod'], 'pod_uid': binding['pod_uid'], 'container_id': binding['container_id'],
        'container_started_at': binding['started_at'], 'observed_at': observed_at,
    }


class StagedLif05:
    """A single-use handoff from the live coordinator. No receipt-file replay."""

    def __init__(self, attempt_id, side, policy_ref, pack_ref, source_sha256, old, owner, observed_at):
        require(re.fullmatch(r'ATTEMPT-[A-Za-z0-9][A-Za-z0-9-]{0,95}', attempt_id) is not None
                and side in ('baseline', 'candidate') and policy_ref['kind'] == 'POL'
                and pack_ref['kind'] == 'PACK' and fingerprint(source_sha256) and old.key == 'old',
                'LIF05_STAGE_SCOPE_INVALID')
        self._scope = {'attempt_id': attempt_id, 'side': side, 'policy_ref': deepcopy(policy_ref),
                       'pack_ref': deepcopy(pack_ref), 'source_sha256': source_sha256,
                       'old': generation_record(old, owner, observed_at)}
        self._scope_sha256 = sha256_hex(canonical_json(self._scope))
        self._retirement = None
        self._new = None
        self._capture = None
        self._consumed = False
        self._request_keys = {'old': token_bytes(32), 'new': token_bytes(32)}

    def scope(self):
        return deepcopy(self._scope)

    @property
    def scope_sha256(self):
        return self._scope_sha256

    def retire(self, receipt):
        old = self._scope['old']
        require(self._retirement is None and self._new is None
                and receipt['scope_sha256'] == self.scope_sha256
                and all(receipt[key] == old[key] for key in ('pod_uid', 'container_id'))
                and receipt['namespace_uid'] == old['owner']['namespace_uid']
                and receipt['deployment_uid'] == old['owner']['deployment_uid']
                and all(receipt[key] is True for key in (
                    'old_pod_absent', 'replicas_zero', 'gpu_idle', 'old_container_task_absent'))
                and timestamp(old['observed_at']) < timestamp(receipt['stop_requested_at'])
                <= timestamp(receipt['absent_at']) <= timestamp(receipt['gpu_observed_at']),
                'LIF05_STAGE_RETIREMENT_UNVERIFIED')
        self._retirement = deepcopy(receipt)

    def bind_replacement(self, new, owner, observed_at):
        old = self._scope['old']
        require(self._retirement is not None and self._new is None and new.key == 'new',
                'LIF05_STAGE_REPLACEMENT_OUT_OF_ORDER')
        record = generation_record(new, owner, observed_at)
        release = timestamp(self._retirement['gpu_observed_at'])
        require(owner == old['owner']
                and all(record[key] != old[key] for key in ('pod', 'pod_uid', 'container_id', 'environment_ref', 'session'))
                and release < timestamp(observed_at)
                # Kubernetes container start timestamps have second precision.
                and release.replace(microsecond=0) <= timestamp(record['container_started_at']),
                'LIF05_STAGE_REPLACEMENT_IDENTITY_MISMATCH')
        self._new = record

    def seal(self, *, phases, trials, samples, diagnostics, cleanup, stream, request_trace, replacement_scale):
        require(self._new is not None and self._capture is None
                and set(phases) == set(PHASES) and phases['during'] == self._retirement['stop_requested_at']
                and timestamp(self._scope['old']['observed_at']) <= timestamp(phases['before'])
                and timestamp(self._new['observed_at']) <= timestamp(phases['after']),
                'LIF05_STAGE_CAPTURE_INCOMPLETE')
        moments = [timestamp(phases[phase]) for phase in PHASES]
        require(all(a < b for a, b in zip(moments, moments[1:]))
                and (moments[-1] - moments[0]).total_seconds() <= 240,
                'LIF05_STAGE_PHASE_WINDOW_INVALID')
        require(len(trials) == len(CASES) and {row.get('case_id') for row in trials} == set(CASES)
                and all(row.get('generation') == CASES[row['case_id']]
                        and timestamp(row['started_at']) <= timestamp(row['finished_at']) for row in trials),
                'LIF05_STAGE_TRIAL_SCOPE_INVALID')
        request_ids = [row['request_id_sha256'] for row in trials if row['request_id_sha256'] is not None]
        require(all(map(fingerprint, request_ids)) and len(request_ids) == len(set(request_ids)),
                'LIF05_STAGE_REQUEST_REUSED')
        generations = {'old': self._scope['old'], 'new': self._new}
        by_case = {row['case_id']: row for row in trials}
        during = by_case['during']
        require(during['attempted'] is True
                and during['transport_attempt']['target_pod_uid'] == generations['old']['pod_uid']
                and during['transport_attempt']['probe_pod_uid'] == generations['old']['owner']['probe_pod_uid']
                and timestamp(self._retirement['gpu_observed_at']) <= timestamp(during['started_at'])
                <= timestamp(during['finished_at']) <= timestamp(replacement_scale['replacement_requested_at'])
                <= min(timestamp(replacement_scale['acknowledged_at']),
                       timestamp(self._new['container_started_at']) + _POD_TIME_PRECISION)
                and timestamp(replacement_scale['acknowledged_at']) <= timestamp(self._new['observed_at']),
                'LIF05_STAGE_DURING_PROBE_SCOPE_INVALID')
        require(len(request_trace) == len(generations)
                and {row['scope']['generation'] for row in request_trace} == set(generations),
                'LIF05_STAGE_REQUEST_TRACE_SCOPE_INVALID')
        trace_by_case = {}
        for row in request_trace:
            scope = row['scope']
            key = scope['generation']
            generation = generations[key]
            cases = row['cases']
            planned = {'before', 'inflight'} if key == 'old' else {'after', 'recovered'}
            require(scope['attempt_id'] == self._scope['attempt_id']
                    and all(scope[field] == generation[field] for field in ('session','pod_uid','container_id'))
                    and all(scope[field] == generation['owner'][field] for field in
                            ('image','image_id','namespace_uid','probe_pod_uid'))
                    and len(cases) == len(planned) and {case['case_id'] for case in cases} == planned
                    and all((case['assignment_bound'] is not True or
                                 case['request_observer_invoked'] is True and row['source_verified'] is True)
                            for case in cases), 'LIF05_STAGE_REQUEST_TRACE_SCOPE_INVALID')
            trace_by_case.update((case['case_id'], case) for case in cases)
        trials = [combine_dispatch(trial, trace_by_case.get(trial['case_id'])) for trial in trials]
        require({row.get('generation') for row in samples} == set(generations), 'LIF05_STAGE_SAMPLE_SCOPE_INVALID')
        for row in samples:
            generation = generations[row['generation']]
            packet = row['native']
            started, ended = timestamp(row['sample_started_at']), timestamp(row['sample_finished_at'])
            require(timestamp(generation['observed_at']) <= started <= ended
                    and (row['generation'] != 'old' or ended <= moments[1])
                    and (packet is None or packet.get('session') == generation['session']),
                    'LIF05_STAGE_SAMPLE_SCOPE_INVALID')
            for boundary in ('before', 'after'):
                if packet is not None and packet.get(boundary) is not None:
                    snapshot_owner = packet[boundary]['owner']
                    require(snapshot_owner['namespace_uid'] == generation['owner']['namespace_uid']
                            and all(snapshot_owner[field] == generation[field] for field in ('pod_uid', 'container_id')),
                            'LIF05_STAGE_SAMPLE_OWNER_MISMATCH')
        self._capture = deepcopy({'phases': phases, 'trials': trials, 'samples': samples, 'diagnostics': diagnostics,
                                  'cleanup': cleanup, 'stream': stream, 'request_trace': request_trace,
                                  'replacement_scale': replacement_scale})

    @property
    def started_at(self):
        return timestamp(self._capture['phases']['before'])

    def binding(self):
        return {'phase_generation': {phase: CASES[phase] for phase in PHASES},
                'sample_offsets_ms': {phase: int((timestamp(at) - self.started_at).total_seconds() * 1000)
                                      for phase, at in self._capture['phases'].items()}}

    def _identity(self):
        return {'scope': self._scope, 'retirement': self._retirement, 'replacement': self._new,
                'capture': self._capture}

    def digest(self):
        require(self._capture is not None, 'LIF05_STAGE_CAPTURE_INCOMPLETE')
        return sha256_hex(canonical_json(self._identity()))

    def qualify(self, digest, *, attempt_id, side, policy_ref, pack_ref, env):
        require(not self._consumed and fingerprint(digest) and digest == self.digest()
                and self._scope_sha256 == sha256_hex(canonical_json(self._scope))
                and (attempt_id, side, policy_ref, pack_ref, env.ref().model_dump(mode='json')) ==
                    (self._scope['attempt_id'], self._scope['side'], self._scope['policy_ref'], self._scope['pack_ref'],
                     self._scope['old']['environment_ref']),
                'LIF05_STAGE_PLAN_SCOPE_MISMATCH')

    def consume(self, digest, generations, schedule, phase_generation):
        require(not self._consumed and digest == self.digest() and set(generations) == {'old', 'new'},
                'LIF05_STAGE_RECEIPT_INVALID')
        for key, record in (('old', self._scope['old']), ('new', self._new)):
            actual = generation_record(generations[key], record['owner'], record['observed_at'])
            require(actual == record, 'LIF05_STAGE_GENERATION_PLAN_MISMATCH')
        require(phase_generation == self.binding()['phase_generation']
                and all(0 <= (timestamp(self._capture['phases'][key]) - schedule[key]).total_seconds() < 0.002
                    for key in PHASES), 'LIF05_STAGE_PHASE_PLAN_MISMATCH')
        self._consumed = True
        return deepcopy(self._capture), deepcopy(self._retirement)

    def scrub(self, value, generation='old', key=''):
        """Export session HMACs only, including for legacy producer request digests."""
        if isinstance(value, dict):
            generation = value.get('generation', generation)
            if generation not in self._request_keys:
                generation = 'old'
            return {k: self.scrub(v, generation, k) for k, v in value.items()
                    if k not in {'request_id', 'raw_request_id', 'prompt', 'body', 'headers', 'stdout', 'stderr'}}
        if isinstance(value, (list, tuple)):
            return [self.scrub(item, generation, key) for item in value]
        if ('request' in key or 'invocation' in key or key == 'internal_sha256') and fingerprint(value):
            return hmac.new(self._request_keys[generation], b'guard-lif05-request/1\0' + bytes.fromhex(value), sha256).hexdigest()
        return value

    def public_receipt(self):
        return {'scope': self.scope(), 'scope_sha256': self.scope_sha256, 'capture_sha256': self.digest(),
                'retirement': deepcopy(self._retirement), 'replacement': deepcopy(self._new),
                'replacement_scale': deepcopy(self._capture['replacement_scale']),
                'phases': self._capture['phases'].copy(), 'stream': self.scrub(self._capture['stream']),
                'request_fingerprints': 'session HMAC-SHA-256', 'raw_request_records_retained': False}
