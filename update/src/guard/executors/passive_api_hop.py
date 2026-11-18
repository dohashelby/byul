"""PLT-08 read-only projection of the same attempt's sealed PLT-01 control."""
from datetime import datetime
import os
from pathlib import Path
import re
import stat

from guard.configuration import KUBERNETES_BOUND_CHECK_OPTIONS, for_check, merge_config
from guard.models import DataOrigin, Environment, Evidence, ExecStatus, Kind, Mode, Plan, Policy, Side, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_functional import LOGICAL_ID, PROFILE as CONTROL_PROFILE, WIRE_ID
from guard.runtime.kubernetes_http import KubernetesBindingError, REQUEST_BINDING_PROFILE, _validate
from guard.runtime.kubernetes_process import PROFILE as PROCESS_PROFILE, SOURCE_SHA256 as PROCESS_SOURCE_SHA256
from guard.store import IntegrityError
from .base import EvidenceDraft
from .run_binding import SCHEMA_2, _stamp

PROFILE = 'passive-owned-api-hop-v1'
VERSION = '0.1.0+passive-api-hop'
CHECK = 'PLT-08'
SOURCE_CHECK = 'PLT-01'
EXECUTOR = 'api_transport'
OPTION = KUBERNETES_BOUND_CHECK_OPTIONS[CHECK]
_TARGET_BYTES = 65536
_PRODUCER = 'kubernetes-process-permission-consumer-v1'
# Exact stream-probe wrapper dispatched by the PLT-01 producer. A wire source
# change requires review before its receipts qualify for this projection.
_PROBE_SOURCE_SHA256 = 'a84a47605aa38cb36303acc2b6c5f90e19e217be21cf476445f1b535f455a62d'
_MAX_ARTIFACT_BYTES = 1024 * 1024


class PassiveApiHopUnavailable(ValueError):
    pass


def _require(condition, code):
    if not condition:
        raise PassiveApiHopUnavailable(code)


def _options(raw):
    checks = raw.get('check_config')
    values = checks.get(CHECK) if type(checks) is dict else None
    return values if type(values) is dict else {}


def requested(raw):
    if type(raw) is not dict:
        return False
    section = raw.get(EXECUTOR)
    # Catch the misplaced option too so a malformed request cannot dispatch
    # the active collector as a fallback.
    return OPTION in _options(raw) or type(section) is dict and OPTION in section


def validate_scope(pack, configurations, allowed_checks, *, plan=None, explicitly_requested=False):
    """Admit only the exact two-sided, explicitly scoped product path."""
    configurations = tuple(configurations)
    if not explicitly_requested and not any(requested(raw) for raw in configurations):
        return False
    required = {CHECK, SOURCE_CHECK}
    _require(len(configurations) == 2 and allowed_checks is not None and required <= allowed_checks
             and {cid for cid in allowed_checks if pack.checks[cid].executor == EXECUTOR} == {CHECK},
             'PASSIVE_API_HOP_EXECUTION_SCOPE_INVALID')
    if plan is not None:
        _require(plan.mode == Mode.update and required <= {item.check_id for item in plan.items if item.selected},
                 'PASSIVE_API_HOP_EXECUTION_SCOPE_INVALID')
    for raw in configurations:
        code = 'PASSIVE_API_HOP_CONFIGURATION_INVALID'
        _require(type(raw) is dict, code)
        update, executors = raw.get('update', {}), raw.get('executors', {})
        section = raw.get(EXECUTOR)
        producer = for_check(raw, 'container_config', SOURCE_CHECK).get('container_config')
        opts = _options(raw)
        _require(type(update) is dict and update.get('mode', 'external') == 'external'
                 and type(executors) is dict and executors.get(EXECUTOR) == 'kubernetes'
                 and executors.get('container_config') == 'kubernetes'
                 and set(opts) == {OPTION} and opts[OPTION] is True
                 and type(section) is dict and set(section) == {'target'}
                 and type(producer) is dict and isinstance(section['target'], (str, Path))
                 and producer.get('target') == section['target'], code)
    return True


def validate_run_scope(pack, plan, config, allowed_checks):
    """Compare the execution's effective routes with the recipe inputs."""
    raw = (config.requested if config.requested is not None else config.raw) or {}
    sides = raw.get('sides', {})
    declared_sides = tuple(sides.get(side.value, {}) for side in (Side.baseline, Side.candidate)) if type(sides) is dict else ()
    effective = tuple(config.side_configs.get(side.value, config) for side in (Side.baseline, Side.candidate))
    if not (requested(raw) or any(type(side) is dict and requested(side) for side in declared_sides)
            or any(requested(side.raw) for side in effective)):
        return False
    _require(plan.mode == Mode.update, 'PASSIVE_API_HOP_EXECUTION_SCOPE_INVALID')
    declarations = tuple(merge_config(raw, side) for side in declared_sides)
    code = 'PASSIVE_API_HOP_CONFIGURATION_INVALID'
    validate_scope(pack, declarations, allowed_checks, plan=plan, explicitly_requested=True)
    validate_scope(pack, (side.raw for side in effective), allowed_checks, plan=plan, explicitly_requested=True)
    for declared, actual in zip(declarations, effective):
        _require(actual.raw[EXECUTOR] == declared[EXECUTOR]
                 and all(actual.executors.get(name) == 'kubernetes' for name in (EXECUTOR, 'container_config')),
                 code)
    return True


def validate_policy(check, values):
    _require(check.id == CHECK and check.executor == EXECUTOR
             and getattr(check, 'collection_profile', None) == 'explicit-owned-api-plaintext-v1'
             and type(values) is dict and set(values) == {'plaintext_forbidden'}
             and type(values['plaintext_forbidden']) is bool,
             'PASSIVE_API_HOP_POLICY_UNVERIFIED')


def read_target(path, env):
    """Read the selected target declaration without invoking Kubernetes."""
    code = 'PASSIVE_API_HOP_TARGET_UNVERIFIED'
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            _require(stat.S_ISREG(metadata.st_mode) and 0 < metadata.st_size <= _TARGET_BYTES, code)
            raw = stream.read(_TARGET_BYTES + 1)
        _require(len(raw) == metadata.st_size, code)
        target = strict_json(raw, limit=_TARGET_BYTES)
    except (OSError, TypeError, ValueError):
        raise PassiveApiHopUnavailable(code) from None
    validate_target(target, env)
    return target


def validate_target(target, env):
    code = 'PASSIVE_API_HOP_TARGET_UNVERIFIED'
    _require(type(target) is dict and target.get('lab') is False
             and target.get('schema') == 'guard-kubernetes-request-binding-target/1'
             and target.get('environment_ref') == env.ref().model_dump(mode='json'), code)
    try:
        return _validate(target, env)
    except (AttributeError, KeyError, KubernetesBindingError, TypeError, ValueError):
        raise PassiveApiHopUnavailable(code) from None


def execution_binding(draft, *, side, environment_ref, attempt_id):
    trial, = draft.observations['api_transport_trials'].value
    files = {key: trial[key] for key in ('source_artifact_sha256', 'target_sha256')}
    return {'schema': SCHEMA_2, 'check_id': CHECK, 'side': side.value, 'attempt_id': attempt_id,
            'environment_sha256': environment_ref.sha256, 'files': files, 'validity': None,
            'issued_at': _stamp(draft.started_at),
            'values': {'profile': PROFILE, 'source_evidence': trial['source_evidence'], **files}}


def _source(store, plan_ref, side, attempt_id, source_ref):
    code = 'PASSIVE_API_HOP_SOURCE_SCOPE_UNVERIFIED'
    _require(plan_ref.kind == Kind.PLAN and source_ref.kind == Kind.EVD
             and isinstance(side, Side) and side in (Side.baseline, Side.candidate)
             and type(attempt_id) is str and bool(attempt_id), code)
    plan = store.resolve(plan_ref)
    _require(isinstance(plan, Plan) and plan.ref() == plan_ref and plan.mode == Mode.update
             and len(plan.inputs.environments) == 2 and plan.inputs.execution_recipe_sha256 is not None, code)
    items = [item for item in plan.items if item.check_id in ('PLT-01', 'PLT-08')]
    _require(sorted(item.check_id for item in items if item.selected) == ['PLT-01', 'PLT-08'] and len(items) == 2, code)
    env_ref = plan.inputs.environments[0 if side == Side.baseline else 1]
    environment = store.resolve(env_ref)
    source = store.resolve(source_ref)
    _require(isinstance(environment, Environment) and environment.ref() == env_ref
             and isinstance(source, Evidence) and source.ref() == source_ref and source.plan == plan_ref
             and source.check_id == 'PLT-01' and source.environment == env_ref and source.side == side
             and plan_ref in source.refs and env_ref in source.refs and source.execution.attempt_id == attempt_id
             and source.execution.status == ExecStatus.COMPLETED and source.execution.operator is None
             and source.execution.override_reason is None and source.execution.abort_reason is None
             and not source.execution.cleanup_errors and source.data_origin in (DataOrigin.MEASURED, DataOrigin.EXAMPLE), code)
    pol = store.resolve(plan.inputs.policy)
    _require(isinstance(pol, Policy) and pol.ref() == plan.inputs.policy and pol.pack == plan.inputs.pack, code)
    plaintext_forbidden = pol.values.get('PLT-08', {}).get('plaintext_forbidden')
    _require(type(plaintext_forbidden) is bool, 'PASSIVE_API_HOP_POLICY_UNVERIFIED')
    return source, environment, plaintext_forbidden


def _read_summary(store, source):
    code = 'PASSIVE_API_HOP_ARTIFACT_UNVERIFIED'
    artifacts = [item for item in source.artifacts if item.name == 'probe_log.jsonl']
    _require(len(artifacts) == 1 and source.execution.executor is not None
             and source.execution.executor.name == 'container_config', code)
    artifact, = artifacts
    _require(re.fullmatch(r'[0-9a-f]{64}', artifact.sha256) is not None
             and artifact.path == 'artifacts/' + artifact.sha256
             and 0 < artifact.bytes <= _MAX_ARTIFACT_BYTES
             and artifact.collector == 'container_config/' + source.execution.executor.version, code)
    directory = os.open(store.root / 'artifacts', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(artifact.sha256, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            _require(stat.S_ISREG(metadata.st_mode) and metadata.st_size == artifact.bytes, code)
            raw = stream.read(artifact.bytes + 1)
    finally:
        os.close(directory)
    _require(len(raw) == artifact.bytes and sha256_hex(raw) == artifact.sha256, code)
    lines = raw.splitlines()
    _require(0 < len(lines) <= 32, code)
    decoded = [strict_json(line, limit=_MAX_ARTIFACT_BYTES) for line in lines]
    summaries = [row for row in decoded if type(row) is dict and row.get('summary') is True]
    _require(len(summaries) == 1 and summaries[0] is decoded[-1], code)
    return summaries[0], artifact.sha256


def _bound_request(summary, source, binding, target_sha256):
    code = 'PASSIVE_API_HOP_CONTROL_UNVERIFIED'
    process = summary['process_observation']
    transport = summary['kubernetes']
    _require(summary['profile'] == _PRODUCER and summary['lab'] is False
             and summary['topology'] == 'kubernetes-owned-direct' and process['profile'] == PROCESS_PROFILE
             and process['program_sha256'] == PROCESS_SOURCE_SHA256 and process['available'] is True
             and process['binding_verified_before_after'] is True and process['target_sha256'] == target_sha256
             and process['binding_before'] == process['binding_after'] == binding['engine']
             and process['cleanup_errors'] == [] and process['data_origin'] == source.data_origin.value
             and transport['profile'] == REQUEST_BINDING_PROFILE and transport['binding'] == binding
             and transport['binding_sha256'] == sha256_hex(canonical_json(binding))
             and transport['probe_source_sha256'] == _PROBE_SOURCE_SHA256
             and transport['collection_gap'] is False and transport['cleanup_errors'] == [], code)
    requests = transport['requests']
    _require(type(requests) is list and len(requests) == 1, code)
    row, = requests
    _require(row['id'] == LOGICAL_ID and row['profile'] == CONTROL_PROFILE and row['wire_id'] == WIRE_ID
             and row['probe_source_sha256'] == _PROBE_SOURCE_SHA256 and row['binding_verified_before_after'] is True
             and row['capture_complete'] is True and type(row['outcome']) is int and row['outcome'] == 200, code)
    control = source.observations['functional_probe_results']
    _require(source.normal_control.attempted is True and source.normal_control.succeeded is True
             and control.collected is True and control.value == {LOGICAL_ID.removeprefix('func|'): 'pass'}, code)
    first = datetime.fromisoformat(row['started_at'])
    last = datetime.fromisoformat(row['finished_at'])
    _require(source.execution.started_at is not None and source.execution.finished_at is not None
             and first.utcoffset() is not None and last.utcoffset() is not None
             and source.execution.started_at <= first <= last <= source.execution.finished_at, code)
    return first, last


def _verified_projection(store, *, plan_ref, side, attempt_id, source_ref, target, expected_target_sha256):
    try:
        source, env, prohibited = _source(store, plan_ref, side, attempt_id, source_ref)
        target_sha256 = sha256_hex(canonical_json(target))
        _require(type(expected_target_sha256) is str and target_sha256 == expected_target_sha256,
                 'PASSIVE_API_HOP_TARGET_UNVERIFIED')
        binding, _ = validate_target(target, env)
        summary, digest = _read_summary(store, source)
        first, last = _bound_request(summary, source, binding, target_sha256)
    except PassiveApiHopUnavailable:
        raise
    except (AttributeError, IntegrityError, KeyError, KubernetesBindingError, OSError, TypeError, ValueError):
        raise PassiveApiHopUnavailable('PASSIVE_API_HOP_INPUT_UNVERIFIED') from None

    trial = {'profile': PROFILE, 'source_evidence': source_ref.model_dump(mode='json'), 'source_artifact_sha256': digest,
             'target_sha256': target_sha256, 'original_source_data_origin': source.data_origin.value,
             'transport': 'plain_http', 'source_role': 'selected_owned_kubernetes_probe',
             'destination_role': 'selected_owned_kubernetes_engine_pod', 'method': 'POST', 'route': '/v1/completions',
             'outcome': 200, 'capture_complete': True, 'owner_binding_verified_before_after': True,
             'started_at': first.isoformat(), 'finished_at': last.isoformat(), 'global_plaintext_forbidden': prohibited,
             'hop_policy_adjudicated': False, 'deployed_caller_path_verified': False}
    return source, {
        'api_transport_trials': {'value': [trial], 'collected': True},
        'forbidden_plaintext_successes': {'value': None, 'collected': False},
        'api_transport_scope_complete': {'value': False, 'collected': True},
    }


def read_passive_api_hop(store, *, plan_ref, side, attempt_id, source_ref, target, expected_target_sha256):
    """Read exact original control and return PLT-08 observations without dispatch."""
    _, observations = _verified_projection(store, plan_ref=plan_ref, side=side, attempt_id=attempt_id,
        source_ref=source_ref, target=target, expected_target_sha256=expected_target_sha256)
    return observations


def draft_passive_api_hop(store, *, plan_ref, side, attempt_id, source_ref, target, expected_target_sha256):
    """Project the active attempt's previously committed normal control into a draft.

    The caller supplies the exact PLT-01 Ref from the active attempt and adds
    that Ref to the destination EVD's immutable refs before sealing it. The
    draft's grade and comparison conditions describe the original acquisition.
    The timestamps describe this local projection; it does not run a control.
    """
    started_at = utcnow()
    source, observations = _verified_projection(store, plan_ref=plan_ref, side=side, attempt_id=attempt_id,
        source_ref=source_ref, target=target, expected_target_sha256=expected_target_sha256)
    return EvidenceDraft(
        status=ExecStatus.COMPLETED, data_origin=source.data_origin,
        normal_control={'attempted': False, 'succeeded': None,
                        'detail': 'Reused a verified normal control from the same attempt without sending a request.'},
        observations=observations, comparability=source.comparability.model_copy(deep=True),
        config={'profile': PROFILE, 'source_check_id': 'PLT-01'}, started_at=started_at, finished_at=utcnow(),
    )
