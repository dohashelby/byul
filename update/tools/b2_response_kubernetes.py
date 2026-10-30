"""REQ13 error-only Kubernetes collector → EVD/RES/RPT and verified recovery.

External DRAFT only. Existing REQ13 policy/selection rules and unsupported
private/options observations remain intact. No inference, Gateway or log probe.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import re
import sys

import yaml

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.base import DraftArtifact, EvidenceDraft
from guard.executors.http_common import incomplete_artifact_capture
from guard.models import CheckDef, DataOrigin, ExecStatus, Environment, Policy, canonical_json, load_pack
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import get_pack, put_pack
from tools import lif14_profiler_cpu as component_gate

PROFILE = 'byul-req13-error-kubernetes-producer/1'
CAPTURE_SCHEMA = 'byul-req13-error-kubernetes-capture/1'
CAPTURE_PROFILE = 'kubernetes-owned-validation-error-http-v1'
SOURCE_PROFILE = 'vllm-031-validation-error'
_REQUEST_PATHS = {'req13-error-before': '/tokenize', 'req13-error-primary': '/v1/chat/completions', 'req13-error-after': '/tokenize'}
EXECUTOR = 'response_error_kubernetes'
COMPONENT_COMMIT = '42e62a14776893dec8d70d7bd8e49769ce08b4de'
GATE_SHA256 = '3245ecaac5b086905fc7fb6b7e37c7e3d355e4e50ab30a9abb2a51cbca961791'
COLLECTOR_SHA256 = 'd1c18ad127ed56d7844e22381fb9b21b20af684224a57df10f65b550cb078a46'
COLLECTOR_MODULE = 'tools.b2_response_kubernetes_capture'
_GAPS = {'A_CAPABILITY_UNAVAILABLE', 'TRANSPORT_UNAVAILABLE', 'TRANSPORT_IDENTITY_MISMATCH',
    'BEFORE_CONTROL_UNAVAILABLE', 'BEFORE_CONTROL_FAILED', 'PRIMARY_UNAVAILABLE',
    'PRIMARY_NOT_ERROR_STATUS', 'PRIMARY_CAPTURE_INCOMPLETE', 'EXPECTED_ERROR_STATUS_UNCONFIRMED',
    'AFTER_CONTROL_UNAVAILABLE', 'AFTER_CONTROL_FAILED', 'CLEANUP_UNAVAILABLE', 'CLEANUP_INCOMPLETE', 'POLICY_UNSET',
    'A_SOURCE_POSTCHECK_UNAVAILABLE', 'PRIMARY_OBSERVATION_UNAVAILABLE', 'UNSUPPORTED_ENGINE_VERSION'}
_HELPER_GAPS = {'MARKER_POLICY_UNSET', 'BODY_UNAVAILABLE', 'EXPECTED_ERROR_STATUS_UNCONFIRMED',
    'JSON_CAPTURE_UNAVAILABLE', 'JSON_AMBIGUOUS', 'ERROR_FIELDS_UNAVAILABLE', 'FIELD_POLICY_UNSET'}
_SCOPE_GAPS = ['REQ13_SUCCESS_FIELDS_NOT_COLLECTED', 'REQ13_PRIVATE_AND_OPTIONS_NOT_COLLECTED',
               'REQ13_NATIVE_HANDLER_ROUTING_UNVERIFIED']


def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _read(path):
    with Path(path).open('rb') as stream:
        return strict_json(stream.read(262145), limit=262144)


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def _verify_import(module, function, path, expected):
    try:
        if (not _sha(expected) or path.is_symlink() or not Path(module.__file__).resolve().samefile(path)
                or not Path(inspect.getsourcefile(function)).resolve().samefile(path)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError
    except (OSError, TypeError, AttributeError, ValueError):
        raise ValueError('REQ13_COMPONENT_IDENTITY_OR_BYTES_MISMATCH') from None


def verify_runtime_components(repo):
    repo = Path(repo)
    _verify_import(component_gate, component_gate.verify_component,
                   repo / 'update/tools/lif14_profiler_cpu.py', GATE_SHA256)
    if component_gate.PRODUCT_COMPONENT_COMMIT != COMPONENT_COMMIT:
        raise ValueError('REQ13_COMPONENT_PIN_MISMATCH')
    component_gate.verify_component(repo)
    if not _sha(COLLECTOR_SHA256):
        raise ValueError('REQ13_COLLECTOR_PIN_UNCONFIGURED')
    collector = importlib.import_module(COLLECTOR_MODULE)
    _verify_import(collector, collector.capture_validation_error,
                   repo / 'update/tools/b2_response_kubernetes_capture.py', COLLECTOR_SHA256)
    return collector.capture_validation_error


def _scoped_check():
    repo = Path(__file__).resolve().parents[2]
    original = CheckDef.model_validate(yaml.safe_load((repo / 'update/packs/PACK-BASELINE/checks/REQ-13.yaml').read_text()))
    raw = original.model_dump(mode='json')
    raw['executor'] = EXECUTOR
    # Same allowed_error_fields policy violation, represented by helper hashes.
    # Raw error_fields_seen stays uncollected; never compare hashes to raw paths.
    raw['required_observations'].append({'key': 'response_violations', 'type': 'list[str]'})
    raw['judge']['params']['fail_when_any'].append({'obs': 'response_violations', 'op': 'nonempty'})
    raw['limitations'].append('B external DRAFT: one direct-Pod error response and CPU tokenize controls only; full REQ13 remains incomplete.')
    return CheckDef.model_validate(raw)


def proposal_pack(folder):
    folder = Path(folder)
    (folder / 'checks').mkdir(parents=True, exist_ok=False)
    body = yaml.safe_dump(_scoped_check().model_dump(mode='json'), allow_unicode=True, sort_keys=False).encode()
    (folder / 'checks/REQ-13.yaml').write_bytes(body)
    (folder / 'pack.yaml').write_text(yaml.safe_dump({'pack_id': 'PACK-B-REQ13-ERROR-KUBERNETES-PROPOSAL',
        'revision': 1, 'engines': ['vllm'], 'checks': ['REQ-13'], 'review': {'status': 'DRAFT'},
        'files_sha256': [{'path': 'checks/REQ-13.yaml', 'sha256': hashlib.sha256(body).hexdigest()}]}, sort_keys=False))
    return load_pack(folder)


def _validate_inputs(env, policy, pack, target, *, duration, binary):
    if (not env.hash_ok() or not policy.hash_ok() or policy.pack != pack.ref()
            or env.engine.name != 'vllm' or pack.manifest.review.status.value != 'DRAFT' or set(pack.checks) != {'REQ-13'}
            or pack.checks['REQ-13'].model_dump(mode='json') != _scoped_check().model_dump(mode='json')):
        raise ValueError('REQ13_ENV_POLICY_PACK_MISMATCH')
    # Existing public constructor validates identity/alias/digest/address without I/O.
    transport = KubernetesHTTP(target, env, duration=duration, binary=binary, profile=REQUEST_BINDING_PROFILE)
    return transport.api_model


def _time(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _control(value):
    value = value if isinstance(value, dict) else {}
    safe = {k: value[k] for k in ('attempted', 'collected') if type(value.get(k)) is bool}
    safe.update({k: value.get(k) for k in ('succeeded', 'binding_verified', 'capture_complete')
                 if type(value.get(k)) is bool or value.get(k) is None})
    safe.update({k: value[k] for k in ('http_status', 'token_count', 'max_model_len')
                 if type(value.get(k)) is int and value[k] >= 0})
    safe.update({k: value[k] for k in ('tokens_sha256', 'response_sha256', 'request_id_sha256') if _sha(value.get(k))})
    return safe


def _project(record, env, target, api_model, allowed_fields):
    """Only the approved direct collector's in-memory return reaches production."""
    if not isinstance(record, dict):
        return None
    started, finished = _time(record.get('capture_started_at')), _time(record.get('capture_finished_at'))
    alias_hash = hashlib.sha256(api_model.encode()).hexdigest()
    alias_unavailable_without_requests = (record.get('attempted_requests') == 0
        and record.get('api_model_sha256') is None and record.get('error_observation') is None)
    if (record.get('schema') != CAPTURE_SCHEMA or record.get('profile') != CAPTURE_PROFILE
            or record.get('source_profile') != SOURCE_PROFILE
            or record.get('environment_ref') != env.ref().model_dump(mode='json')
            or record.get('target_sha256') != _digest(target)
            or not (record.get('api_model_sha256') == alias_hash or alias_unavailable_without_requests)
            or record.get('allowed_error_fields_sha256') != _digest(allowed_fields)
            or type(record.get('collection_id')) is not str
            or re.fullmatch('req13-error-k8s-[0-9a-f]{32}', record['collection_id']) is None
            or record.get('data_origin') not in {'EXAMPLE', 'DECLARED'}
            or env.data_origin != DataOrigin.EXAMPLE and record['data_origin'] == 'EXAMPLE'
            or started is None or finished is None or finished < started
            or type(record.get('attempted_requests')) is not int or not 0 <= record['attempted_requests'] <= 3):
        return None
    summaries = record.get('request_summaries')
    if type(summaries) is not list or len(summaries) > 3:
        return None
    requests = {}
    for item in summaries:
        if (type(item) is not dict or item.get('case_id') not in _REQUEST_PATHS or item['case_id'] in requests
                or item.get('method') != 'POST' or item.get('path') != _REQUEST_PATHS[item['case_id']]
                or type(item.get('attempted')) is not bool or not _sha(item.get('request_id_sha256'))
                or not _sha(item.get('body_sha256'))):
            return None
        begin, end = _time(item.get('started_at')), _time(item.get('finished_at'))
        if item['attempted'] and (begin is None or end is None or not started <= begin <= end <= finished):
            return None
        if not item['attempted'] and (item.get('started_at') is not None or item.get('finished_at') is not None):
            return None
        requests[item['case_id']] = {'case_id': item['case_id'], 'method': 'POST', 'path': item['path'],
            'request_id_sha256': item['request_id_sha256'], 'body_sha256': item['body_sha256'],
            'attempted': item['attempted'], 'binding_verified': item.get('binding_verified') is True,
            'started_at': begin.isoformat() if begin else None, 'finished_at': end.isoformat() if end else None,
            'raw_response_bytes_sha256': item.get('raw_response_bytes_sha256') if _sha(item.get('raw_response_bytes_sha256')) else None}
    if sum(item['attempted'] for item in requests.values()) != record['attempted_requests']:
        return None
    controls = record.get('normal_controls') if isinstance(record.get('normal_controls'), dict) else {}
    cleanup = record.get('cleanup') if isinstance(record.get('cleanup'), dict) else {}
    safe = {'profile': PROFILE, 'collection_id': record['collection_id'],
        'environment_ref': env.ref().model_dump(mode='json'), 'target_sha256': _digest(target),
        'api_model_sha256': record['api_model_sha256'], 'allowed_error_fields_sha256': _digest(allowed_fields), 'data_origin': record['data_origin'],
        'capture_started_at': started.isoformat(), 'capture_finished_at': finished.isoformat(),
        'attempted_requests': record['attempted_requests'], 'handler_consumption_verified': False,
        'native_handler_consumption_verified': False, 'source_routing_verified': False, 'model_inference_verified': False,
        'capture_status': 'completed' if record.get('capture_status') == 'completed' else 'incomplete',
        'schema': CAPTURE_SCHEMA, 'source_profile': SOURCE_PROFILE, 'capture_profile': CAPTURE_PROFILE,
        'request_summaries': list(requests.values()),
        'normal_controls': {key: _control(controls.get(key)) for key in ('before', 'after')},
        'gaps': sorted({v for v in record.get('gaps', []) if type(v) is str and v in _GAPS}) if isinstance(record.get('gaps'), list) else [],
        'cleanup': {k: cleanup[k] for k in ('collected', 'complete', 'collection_gap') if type(cleanup.get(k)) is bool},
        'error_observation': None}
    safe['cleanup']['error_sha256'] = sorted({h for h in cleanup.get('error_sha256', []) if _sha(h)}) if isinstance(cleanup.get('error_sha256'), list) else []
    observation = record.get('error_observation')
    primary = requests.get('req13-error-primary', {})
    if (record.get('api_model_sha256') == alias_hash and env.engine.version == '0.31.0' and record['attempted_requests'] >= 2 and primary.get('attempted') is True
            and primary.get('binding_verified') is True and _sha(primary.get('raw_response_bytes_sha256'))
            and isinstance(observation, dict) and type(observation.get('outcome')) is int and 400 <= observation['outcome'] <= 599):
        fields = observation.get('field_path_sha256')
        if type(fields) is list and all(_sha(v) for v in fields):
            fields = sorted(set(fields))
            allowed_hashes = {hashlib.sha256(path.encode()).hexdigest() for path in allowed_fields} if allowed_fields is not None else None
            derived = ['NONPERMITTED_FIELD_SHA256:' + h for h in fields if allowed_hashes is not None and h not in allowed_hashes]
            helper = observation.get('violations')
            if type(helper) is list and sorted(set(helper)) == derived:
                safe['error_observation'] = {'surface': 'error_response', 'outcome': observation['outcome'],
                    'field_path_sha256': fields, 'violations': derived,
                    'coverage_gaps': sorted({g for g in observation.get('coverage_gaps', []) if type(g) is str and g in _HELPER_GAPS})
                        if isinstance(observation.get('coverage_gaps'), list) else [],
                    'complete': observation.get('complete') is True}
                for key in ('capture_sha256',):
                    if _sha(observation.get(key)):
                        safe['error_observation'][key] = observation[key]
                if type(observation.get('capture_bytes')) is int and observation['capture_bytes'] >= 0:
                    safe['error_observation']['capture_bytes'] = observation['capture_bytes']
    return safe


def run_pipeline(env, policy, pack, target, output, *, execute=False, duration=60, timeout=10,
                 binary='kubectl', _example_capture=None):
    if sys.flags.optimize:
        raise ValueError('REQ13_NORMAL_PYTHON_REQUIRED')
    api_model = _validate_inputs(env, policy, pack, target, duration=duration, binary=binary)
    if _example_capture is not None and env.data_origin != DataOrigin.EXAMPLE:
        raise ValueError('REQ13_INJECTION_REQUIRES_EXAMPLE_ENV')
    # select retains required policy params and exact input Ref. Explicit None
    # raises its existing type error; absent policy produces NOT_RUN below.
    plan = select(pack, [env], policy)
    if not any(item.selected for item in plan.items):
        raise ValueError('REQ13_NOT_SELECTED')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    store = Store(output / 'store')
    put_pack(store, pack.path)
    store.put_many([env, policy, plan])
    state = {'record': None, 'calls': 0, 'diagnostics': []}
    repo = Path(__file__).resolve().parents[2]
    class ErrorOnlyScopedBoundExecutor:
        name, version = EXECUTOR, '0.1.0'
        def run(self, check, environment, values, adapter, workdir):
            if environment.ref() != env.ref() or values != policy.values.get('REQ-13', {}):
                raise ValueError('REQ13_BOUND_ENV_OR_POLICY_MISMATCH')
            if execute and 'engine' in values.get('required_targets', []):
                try:
                    capture = _example_capture or verify_runtime_components(repo)
                    state['calls'] += 1
                    raw = capture(env, deepcopy(target), duration=duration, timeout=timeout, binary=binary,
                        allowed_error_fields=deepcopy(values.get('allowed_error_fields')), forbidden_contexts=None)
                    state['record'] = _project(raw, env, target, api_model, values.get('allowed_error_fields'))
                    if state['record'] is None:
                        state['diagnostics'].append('REQ13_CAPTURE_BINDING_OR_PROJECTION_UNAVAILABLE')
                except Exception:
                    state['diagnostics'].append('REQ13_CAPTURE_OR_COMPONENT_UNAVAILABLE')
                if _example_capture is None:
                    try:
                        verify_runtime_components(repo)
                    except Exception:
                        state['diagnostics'].append('REQ13_COMPONENT_POSTCHECK_UNAVAILABLE')
            else:
                state['diagnostics'].append('REQ13_DRY_RUN' if not execute else 'REQ13_ENGINE_NOT_SELECTED')
            record = state['record']
            observations = {o.key: {'value': None, 'collected': False} for o in check.required_observations}
            observations['response_coverage_complete'] = {'value': False, 'collected': True}
            observations['response_coverage_gaps'] = {'value': _SCOPE_GAPS, 'collected': True}
            error = record['error_observation'] if record else None
            if error is not None:
                observations['response_cases_tested'] = {'value': ['engine:safe_error'], 'collected': True}
                if error['field_path_sha256']:
                    observations['response_violations'] = {'value': error['violations'], 'collected': True}
            controls = record['normal_controls'] if record else {}
            attempted = any(v.get('attempted') is True for v in controls.values())
            normal = bool(controls) and all(controls.get(k, {}).get('collected') is True
                and controls[k].get('succeeded') is True and controls[k].get('binding_verified') is True
                and controls[k].get('capture_complete') is True for k in ('before', 'after'))
            cleanup = record['cleanup'] if record else {}
            cleanup_errors = ['REQ13_OWNED_PROBE_CLEANUP_UNVERIFIED'] if record and (
                cleanup.get('collected') is not True or cleanup.get('complete') is not True or cleanup.get('collection_gap') is True) else []
            draft = EvidenceDraft(status=ExecStatus.ABORTED,
                data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
                normal_control={'attempted': attempted, 'succeeded': normal if attempted else None}, observations=observations,
                challenges=[{'step': step, 'attempted': index == 2 and error is not None}
                            for index, step in enumerate(check.challenge_steps)],
                config={'profile': PROFILE, 'capture_calls': state['calls'], 'full_req13_coverage': False,
                        'normal_control_scope': 'tokenize_api_shape_only',
                        'handler_consumption_verified': False, 'target_sha256': _digest(target),
                        'collection_id': record['collection_id'] if record else None},
                started_at=_time(record['capture_started_at']) if record else None,
                finished_at=_time(record['capture_finished_at']) if record else None,
                abort_reason='REQ13_ERROR_ONLY_PARTIAL_SCOPE', cleanup_errors=cleanup_errors)
            if record is not None:
                try:
                    path = Path(workdir) / 'req13-error.json'
                    _write(path, {**record, 'producer_diagnostics': state['diagnostics']})
                    draft.artifacts = [DraftArtifact(name=path.name, path=path, collector=PROFILE)]
                except Exception:
                    draft = incomplete_artifact_capture(draft)
            return draft
    executor = ErrorOnlyScopedBoundExecutor()
    key = EXECUTOR + ':error-only-scoped'
    previous = registry.EXECUTORS.get(key)
    registry.EXECUTORS[key] = lambda cfg: executor
    try:
        evidence, = run_plan(store, pack, plan, RunConfig(executors={EXECUTOR: 'error-only-scoped'}))
    finally:
        if previous is None:
            registry.EXECUTORS.pop(key, None)
        else:
            registry.EXECUTORS[key] = previous
    now = datetime.now(timezone.utc)
    result, = judge_plan(store, pack, plan, as_of=now)
    report = build_report(store, pack, plan, as_of=now)
    store.put(report)
    summary = {'profile': PROFILE, 'full_req13_coverage': False, 'handler_consumption_verified': False,
        'capture_calls': state['calls'], 'collector_status': state['record']['capture_status'] if state['record'] else None,
        'diagnostics': state['diagnostics'], 'execution_status': evidence.execution.status.value,
        'data_origin': evidence.data_origin.value, 'security': result.verdicts.security.value,
        'environment': env.ref().model_dump(mode='json'), 'policy': policy.ref().model_dump(mode='json'),
        'pack': pack.ref().model_dump(mode='json'), 'plan': plan.ref().model_dump(mode='json'),
        'evidence': evidence.ref().model_dump(mode='json'), 'result': result.ref().model_dump(mode='json'),
        'report': report.ref().model_dump(mode='json'), 'store_verify_errors': store.verify(),
        'exact_ref_rejudgment_unchanged': False, 'restore_verified': False}
    try:
        replay, = judge_plan(store, get_pack(store, plan.inputs.pack), plan, as_of=now)
        if replay.ref() != result.ref():
            raise ValueError('REJUDGE')
        summary['exact_ref_rejudgment_unchanged'] = True
        export_bundle(store.root, output / 'evidence.zip')
        restore_bundle(output / 'evidence.zip', output / 'restored')
        restored = Store(output / 'restored')
        replay, = judge_plan(restored, get_pack(restored, plan.inputs.pack), restored.resolve(plan.ref()), as_of=now)
        if restored.verify() or replay.ref() != result.ref():
            raise ValueError('RESTORE')
        summary['restore_verified'] = True
    except Exception:
        summary['diagnostics'] = [*summary['diagnostics'], 'REQ13_REPLAY_OR_BUNDLE_UNAVAILABLE']
    _write(output / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare-pack')
    prep.add_argument('--output', required=True, type=Path)
    run = sub.add_parser('run')
    for key in ('env', 'policy', 'pack', 'target', 'output'):
        run.add_argument('--' + key, required=True, type=Path)
    run.add_argument('--execute', action='store_true')
    run.add_argument('--duration', type=float, default=60)
    run.add_argument('--timeout', type=float, default=10)
    run.add_argument('--kubectl', default='kubectl')
    args = parser.parse_args()
    if args.command == 'prepare-pack':
        print(json.dumps(proposal_pack(args.output).ref().model_dump(mode='json')))
        return 0
    summary = run_pipeline(Environment.model_validate(_read(args.env)), Policy.model_validate(_read(args.policy)),
        load_pack(args.pack), _read(args.target), args.output, execute=args.execute,
        duration=args.duration, timeout=args.timeout, binary=args.kubectl)
    print(json.dumps(summary))
    return 2  # Error-only never reports full REQ13 completion/PASS.


if __name__ == '__main__':
    raise SystemExit(main())
