"""PLT-08 read-only projection of the same attempt's sealed PLT-01 control."""
from datetime import datetime
import os
import re
import stat

from guard.models import DataOrigin, Environment, Evidence, ExecStatus, Kind, Mode, Plan, Policy, Side, canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_functional import LOGICAL_ID, PROFILE as CONTROL_PROFILE, WIRE_ID
from guard.runtime.kubernetes_http import KubernetesBindingError, REQUEST_BINDING_PROFILE, _validate
from guard.runtime.kubernetes_process import PROFILE as PROCESS_PROFILE, SOURCE_SHA256 as PROCESS_SOURCE_SHA256
from guard.store import IntegrityError

PROFILE = 'passive-owned-api-hop-v1'
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


def read_passive_api_hop(store, *, plan_ref, side, attempt_id, source_ref, target, expected_target_sha256):
    """Read exact original control and return PLT-08 observations without dispatch.

    The caller supplies the active run's trusted references and verified target
    hash, not the latest matching object. Preserve the source reference as an
    evidence dependency. Global plaintext policy is recorded as context; the
    selected probe hop has no scoped enforcement decision in this projection.
    """
    try:
        source, env, prohibited = _source(store, plan_ref, side, attempt_id, source_ref)
        target_sha256 = sha256_hex(canonical_json(target))
        _require(type(expected_target_sha256) is str and target_sha256 == expected_target_sha256
                 and target.get('lab') is False and target.get('schema') == 'guard-kubernetes-request-binding-target/1'
                 and target.get('environment_ref') == env.ref().model_dump(mode='json'), 'PASSIVE_API_HOP_TARGET_UNVERIFIED')
        binding, _ = _validate(target, env)
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
    return {
        'api_transport_trials': {'value': [trial], 'collected': True},
        'forbidden_plaintext_successes': {'value': None, 'collected': False},
        'api_transport_scope_complete': {'value': False, 'collected': True},
    }
