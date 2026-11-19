"""OBS-03 normal-request evidence from the selected owned Pod's engine logs."""
from hashlib import sha256
import re

from guard.automation_policy import string_list
from guard.models import DataOrigin, ExecStatus, ORIGIN_STRENGTH, canonical_json, utcnow
from guard.runtime.kubernetes_functional import LOGICAL_ID
from guard.runtime.kubernetes_http import KubernetesHTTP, LOG_INTEGRITY_PROFILE, KubernetesBindingError

from .content_common import control_state
from .kubernetes_container_config import KubernetesContainerConfigExecutor
from .log_analysis import REQUIRED_BRANCHES, SUPPORTED_BRANCHES, SUPPORTED_MARKER_CONTEXTS
from .log_analysis_contract import STRICT_JSON_PROFILE, analyze_log_captures, strict_json_loads
from .log_projection import protect_analysis

PROFILE = 'kubernetes-owned-normal-log-observation-v1'
LIMIT_BYTES = 65536
POLICY_KEYS = frozenset({'allowed_log_fields', 'forbidden_log_fields', 'forbidden_marker_contexts',
                         'required_sources', 'required_branches'})
TIMESTAMPED_JSON = re.compile(r'^\d{4}-\d{2}-\d{2}T\S+\s+\{')
_PIPE_FLAGS = ('bounded_at_source', 'complete', 'timed_out', 'truncated', 'pipes_closed')


def _runner_status(returncode, pipes):
    code = returncode if type(returncode) is int and -255 <= returncode <= 255 else None
    flags = {key: pipes.get(key) for key in _PIPE_FLAGS}
    errors = pipes.get('read_errors')
    return {'stage': 'runner_returned_nonzero' if code else 'runner_result_rejected', 'returncode': code,
            **{key: value if type(value) is bool else None for key, value in flags.items()},
            'child_reap_unverified': 'child_reap' in errors if type(errors) is list else None}


class KubernetesLogAnalysisExecutor(KubernetesContainerConfigExecutor):
    name = 'log_analysis'
    version = '0.1.0+k8s-normal'

    def validate_policy(self, check, values):
        if check.id != 'OBS-03':
            raise ValueError('KUBERNETES_LOG_ANALYSIS_CHECK_UNSUPPORTED')
        if type(values) is not dict or set(values) != POLICY_KEYS:
            raise ValueError('KUBERNETES_LOG_ANALYSIS_POLICY_INVALID')
        for key in sorted(POLICY_KEYS):
            string_list(values, key, empty=key in ('forbidden_log_fields', 'forbidden_marker_contexts'))
        if ('engine' not in values['required_sources'] or not REQUIRED_BRANCHES <= set(values['required_branches'])
                or set(values['required_branches']) - SUPPORTED_BRANCHES
                or set(values['forbidden_marker_contexts']) - SUPPORTED_MARKER_CONTEXTS):
            raise ValueError('KUBERNETES_LOG_ANALYSIS_POLICY_SCOPE_UNSUPPORTED')

    def preview_reuse_inputs(self, check, env, values, adapter):
        return None

    def collect(self, ctx):
        normal_transport = ctx.cprobe
        remaining = normal_transport.deadline - normal_transport.clock()
        # Both transports revalidate the same selected engine/probe identities.
        log_transport = KubernetesHTTP(ctx.target, ctx.env, duration=remaining, binary=normal_transport.binary,
                                     runner=self._kubectl_runner, profile=LOG_INTEGRITY_PROFILE)
        started = utcnow().isoformat()
        request = {'id': LOGICAL_ID, 'method': 'POST', 'url': normal_transport.endpoint.geturl() + '/v1/completions',
                   'headers': {}, 'body': {'model': ctx.api_model, 'prompt': 'ping', 'max_tokens': 1}, 'want_body': True}
        correlation, captures = {}, []
        gaps = ['NORMAL_ENGINE_LOG_ONLY', 'CALLER_INPUT_MARKER_NOT_SENT']
        normal_outcome = 'no_response'
        normal_status = {'stage': 'binding_or_capture_unavailable'}
        try:
            response, = normal_transport.normal_requests('kubernetes-probe', [request], ctx.timeout)
            outcome, captured = response.get('outcome'), response.get('capture_complete')
            normal_status = {'stage': 'response_received', 'binding_verified_before_after': True,
                             'capture_complete': captured if type(captured) is bool else None,
                             'http_status': outcome if type(outcome) is int and 100 <= outcome <= 599 else None}
            normal_outcome = control_state(response, ctx.api_model, '/v1/completions')
            if normal_outcome == 'pass':
                body = strict_json_loads(response['body'])
                ident = body.get('id')
                if type(ident) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', ident):
                    correlation['normal'] = ident
                    normal_status['stage'] = 'response_id_observed'
        except (KubernetesBindingError, ValueError, TypeError, RecursionError):
            gaps.append('NORMAL_REQUEST_BINDING_UNAVAILABLE')
        if not correlation:
            gaps.append('NORMAL_RESPONSE_ID_UNAVAILABLE')

        ctx._normal_log_source = {'byte_count': None, 'sha256': None, 'available': False, 'payload_retained': False}
        log_status = {'stage': 'binding_or_capture_unavailable'}
        try:
            result = log_transport.engine_logs(started, LIMIT_BYTES)
            raw, pipes = result.get('stdout_bytes'), result.get('capture') or {}
            returncode = result.get('returncode')
            log_status = _runner_status(returncode, pipes)
            if (returncode != 0 or type(raw) is not bytes or len(raw) > LIMIT_BYTES
                    or pipes.get('bounded_at_source') is not True):
                raise KubernetesBindingError()
            truncated = len(raw) == LIMIT_BYTES or pipes.get('truncated') is True
            complete = pipes.get('complete') is True and pipes.get('timed_out') is False and not truncated
            text = raw.decode('utf-8', errors='replace')
            # The transport requested CRI timestamps. Do not treat a raw JSON
            # line with a missing timestamp as a scoped observation.
            lines = text.splitlines()
            if any(line.strip() and not TIMESTAMPED_JSON.match(line) for line in lines):
                gaps.append('ENGINE_LOG_FORMAT_PARTIAL')
                text = '\n'.join(line for line in lines if TIMESTAMPED_JSON.match(line))
            captures.append({'source': {'id': 'engine', 'kind': 'kubernetes_pod_logs', 'role': 'engine'},
                             'text': text, 'window': {'since': started, 'until': utcnow().isoformat()},
                             'returncode': 0, 'identity_stable': True, 'source_complete': complete,
                             'timed_out': pipes.get('timed_out'), 'truncated': truncated})
            ctx._normal_log_source = {'byte_count': len(raw), 'sha256': sha256(raw).hexdigest(),
                                      'available': True, 'payload_retained': False}
            log_status['stage'] = 'bounded_capture_complete' if complete else 'bounded_capture_partial'
        except (KubernetesBindingError, ValueError, TypeError):
            gaps.append('OWNED_ENGINE_LOG_UNAVAILABLE')
        ctx._normal_log_cleanup = list(log_transport.report()['cleanup_errors'])
        observed = protect_analysis(analyze_log_captures(captures, ctx.policy_values, {}, correlation,
            initial_gaps=gaps, profile=STRICT_JSON_PROFILE), ctx.policy_values)
        # The response and log strings never enter EVD or the status summary.
        fixture = {'profile': PROFILE, 'target_source': 'owned-engine-pod-stdout', 'logical_request': LOGICAL_ID,
                   'policy_sha256': sha256(canonical_json(ctx.policy_values)).hexdigest()}
        summary = {'profile': PROFILE, 'source': ctx._normal_log_source, 'fixture': fixture,
                   'collection_status': {'normal_control': normal_status, 'engine_stdout': log_status},
                   'gaps': observed['log_coverage_gaps'], 'violations': observed.pop('log_violations')}
        observed['functional_probes_planned'] = [LOGICAL_ID]
        observed['functional_probe_results'] = {LOGICAL_ID: normal_outcome}
        normal = {'attempted': True, 'succeeded': normal_outcome == 'pass', 'detail': 'One owned fixed completion'}
        challenges = [{'step': step, 'attempted': False} for step in ctx.check.challenge_steps]
        observations = {key: {'value': value, 'collected': True} for key, value in observed.items()}
        if 'normal' not in observed['log_sources_tested'].get('engine', ()):
            observations['log_fields_seen'] = {'value': None, 'collected': False}
        observations['log_marker_contexts_seen'] = {'value': None, 'collected': False}
        return observations, normal, challenges, summary

    def _finish(self, ctx):
        draft = super()._finish(ctx)
        draft.cleanup_errors.extend(ctx._normal_log_cleanup)
        if not ctx._normal_log_source['available'] or draft.cleanup_errors:
            draft.status = ExecStatus.ABORTED
            draft.abort_reason = draft.abort_reason or 'OWNED_ENGINE_LOG_CAPTURE_INCOMPLETE'
            draft.data_origin = min(draft.data_origin, DataOrigin.DECLARED, key=ORIGIN_STRENGTH.get)
        return draft
