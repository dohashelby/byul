"""Closed profiler start/stop requests against one explicitly owned Pod binding.

Reuses bounded HTTP and target verification, plus the opt-in owned trace fixture.
No engine lifecycle, dynamic routes, credentials, body previews or backend-state claim.
"""
from copy import deepcopy
from pathlib import Path
import re

from guard.models import canonical_json, sha256_hex
from .bounded import strict_json
from .kubernetes_http import KubernetesHTTP, KubernetesBindingError, REQUEST_BINDING_PROFILE
from . import profiler_owned_trace as trace_fixture

PROFILE = 'kubernetes-owned-profiler-control-http-v1'
SLOTS = ('normal-before', 'start', 'normal-active', 'stop', 'next-start', 'normal-next', 'next-stop', 'normal-after')
CONTROL_PATHS = {'start': '/start_profile', 'stop': '/stop_profile',
    'next-start': '/start_profile', 'next-stop': '/stop_profile'}


class KubernetesProfilerControlHTTP(KubernetesHTTP):
    def __init__(self, target, env, **kwargs):
        if target.get('lab') is not True or env.engine.name != 'vllm' or env.engine.version != '0.31.0':
            raise KubernetesBindingError()
        super().__init__(target, env, profile=REQUEST_BINDING_PROFILE, **kwargs)
        self._last_slot = -1; self._control_token = None
        self._trace_receipt = None; self._trace_recovery = None; self._trace_session = None
        self._trace_attempts = []

    def __call__(self, *args, **kwargs):
        raise KubernetesBindingError()

    def trace_fault(self, session, action):
        """Existing dedicated lab only; one arm and one exact-marker recovery."""
        if type(session) is not str or re.fullmatch('[0-9a-f]{32}', session) is None:
            raise KubernetesBindingError()
        with self.lock:
            if (action not in {'arm', 'recover'} or action in self._trace_attempts
                    or action == 'arm' and self._last_slot != SLOTS.index('normal-before')
                    or action == 'recover' and (self._trace_receipt is None or self._trace_session != session
                        or self._last_slot < SLOTS.index('start') or self._last_slot >= SLOTS.index('next-start'))):
                raise KubernetesBindingError()
            self._trace_attempts.append(action)
            self._trace_session = session
        def retain(value):
            if (type(value) is not dict or value.get('schema') != trace_fixture.SCHEMA
                    or value.get('session') != session or value.get('action') != action
                    or value.get('marker_sha256') != sha256_hex(trace_fixture.marker(session))):
                raise ValueError()
            if action == 'arm':
                if value.get('fault_activation_verified') is not True or value.get('recovery_verified') is not False:
                    raise ValueError()
                self._trace_receipt = deepcopy(value)
            else:
                if (value.get('recovery_verified') is not True or value.get('original_marker_retained') is not True
                        or value.get('marker_identity') != self._trace_receipt['marker_identity']):
                    raise ValueError()
                self._trace_recovery = deepcopy(value)
        self._verify()
        budget = min(10, self.deadline - self.clock())
        if budget <= 0: raise KubernetesBindingError()
        engine = self.binding['engine']
        source = Path(trace_fixture.__file__).read_text(encoding='utf-8')
        result = self._call(['--namespace=' + engine['namespace'], 'exec', '-i', engine['pod'],
            '--container=' + engine['container'], '--', 'python3', '-c', source, session, action],
            payload=canonical_json(self._trace_receipt) if action == 'recover' else b'',
            cap=4096, budget=budget, on_document=retain)
        self._verify()
        return deepcopy(result)

    def perform(self, slot, request_id):
        # Fixed sequence permits omission after a failed normal precondition but
        # never repetition/reordering or arbitrary API paths/bodies.
        with self.lock:
            if slot not in SLOTS or type(request_id) is not str:
                raise KubernetesBindingError()
            index = SLOTS.index(slot)
            match = re.fullmatch('byul-lif14-([0-9a-f]{32})-' + slot, request_id)
            if (index <= self._last_slot or self._last_slot < 0 and index != 0 or match is None
                    or self._control_token is not None and match[1] != self._control_token):
                raise KubernetesBindingError()
            self._last_slot = index; self._control_token = match[1]
        row = {'id': slot, 'case_id': slot, 'request_id_sha256': sha256_hex(request_id.encode()), 'outcome': None,
            'binding_verified_before_after': False, 'capture_complete': False,
            'normal_shape_verified': False, 'profiler_control_response_success': False}
        try:
            self._verify()
            budget = min(10, self.deadline - self.clock())
            if budget <= 0: raise KubernetesBindingError()
            path = CONTROL_PATHS.get(slot, '/v1/completions')
            body = None if slot in CONTROL_PATHS else {'model': self.api_model, 'prompt': 'hello',
                'max_tokens': 1, 'temperature': 0, 'stream': False}
            request = {'id': slot, 'method': 'POST', 'mode': 'json',
                'url': self.endpoint.scheme + '://' + self.endpoint.netloc + path,
                'headers': {'X-Request-Id': request_id}, 'body': body}
            probe = self.binding['probe']
            packet = self._call(['--namespace=' + probe['namespace'], 'exec', '-i', probe['pod'],
                '--container=' + probe['container'], '--', 'python3', '-c', self.source, str(budget)],
                payload=canonical_json({'requests': [request], 'timeout': budget}), cap=2 * 1024**2, budget=budget)
            self._verify()
            if type(packet) is not list or len(packet) != 1 or type(packet[0]) is not dict: raise ValueError
            value = packet[0]; status = value.get('outcome')
            if (value.get('id'), value.get('method'), value.get('mode')) != (slot, 'POST', 'json'): raise ValueError
            if not (type(status) is int and 100 <= status <= 599 or type(status) is str and status in {
                    'refused', 'timeout', 'unreachable', 'dns_error', 'error', 'probe_error'}): raise ValueError
            text = value.get('body')
            complete = bool(type(text) is str and len(text.encode()) <= 131072
                and value.get('capture_complete') is True and value.get('eof') is True
                and value.get('utf8_valid') is True and value.get('body_truncated') is False
                and type(value.get('body_bytes')) is int and value['body_bytes'] == len(text.encode())
                and value.get('body_sha256') == sha256_hex(text.encode()))
            row.update(outcome=status, binding_verified_before_after=True, capture_complete=complete)
            if slot in CONTROL_PATHS:
                row['profiler_control_response_success'] = bool(complete and type(status) is int and status == 200)
            elif complete and type(status) is int and status == 200:
                decoded = strict_json(text.encode(), limit=131072)
                choices = decoded.get('choices') if type(decoded) is dict else None
                row['normal_shape_verified'] = bool(type(decoded) is dict and decoded.get('model') == self.api_model
                    and type(choices) is list and 1 <= len(choices) <= 8
                    and all(type(c) is dict and type(c.get('text')) is str for c in choices))
            return deepcopy(row)
        except Exception:
            row['diagnostic'] = 'PROFILER_CONTROL_HTTP_UNAVAILABLE'
            self.failed = True
            raise KubernetesBindingError() from None
        finally:
            with self.lock: self.rows.append(row)

    def report(self):
        result = super().report()
        # Parent assumes generic request IDs; this closed profile has safe slot IDs.
        result.update(profile=PROFILE, requests=deepcopy(self.rows), engine_started=False,
            fault_injection_implemented=True, actual_backend_state_verified=False,
            trace_fault_receipt=deepcopy(self._trace_receipt), trace_recovery_receipt=deepcopy(self._trace_recovery),
            trace_fault_attempts=list(self._trace_attempts))
        return result
