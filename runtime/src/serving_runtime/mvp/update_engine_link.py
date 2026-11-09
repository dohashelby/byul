"""Connect existing sealed UPDATE observations to exact runtime request records.

The original observation and verdict ceilings stay with their exact EVD Ref.
This adapter checks identity and interval joins; it supplies no new engine verdict.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import re
from typing import Any
from .target import _image_digest


CHECKS = frozenset({'REQ-12', 'REQ-15', 'LIF-03', 'LIF-04', 'LIF-07', 'LIF-08', 'LIF-09', 'LIF-10'})
CONTEXT_KEYS = ('request_id', 'upstream_request_id', 'observation_session_id', 'run_id',
                'environment', 'backend_kind', 'target_id', 'target', 'policy', 'policy_bundle', 'route')


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('ENGINE_LINK_CLOCK_UNQUALIFIED')
    return parsed


def select_request(records: list[dict], request_id: str) -> dict:
    selected = [r for r in records if r.get('request_id') == request_id]
    starts = [r for r in selected if r.get('phase') == 'started']
    finals = [r for r in selected if r.get('phase') == 'final']
    if len(starts) != 1 or len(finals) != 1:
        raise ValueError('ENGINE_LINK_UNIQUE_REQUEST_INTERVAL_REQUIRED')
    first, last = starts[0], finals[0]
    if any(first.get(key) != last.get(key) for key in CONTEXT_KEYS):
        raise ValueError('ENGINE_LINK_REQUEST_CONTEXT_CHANGED')
    if _time(first['recorded_at']) > _time(last['recorded_at']):
        raise ValueError('ENGINE_LINK_REQUEST_INTERVAL_INVALID')
    return {'context': {key: deepcopy(first.get(key)) for key in CONTEXT_KEYS},
            'started_at': first['recorded_at'], 'finished_at': last['recorded_at'],
            'final_outcome': last.get('final_outcome'), 'backend_cancel': last.get('backend_cancel')}


def _transports(payload: dict) -> list[dict]:
    return [item for item in (payload.get('http_transport'), payload.get('transport'),
            (payload.get('native') or {}).get('transport')) if type(item) is dict]


def link_evidence(records: list[dict], request_id: str, evidence: Any,
                  artifact_name: str, artifact_bytes: bytes) -> dict:
    """Verify bytes/Ref first, then retain every missing binding as a gap."""
    from guard.runtime.bounded import strict_json
    if not evidence.hash_ok() or evidence.check_id not in CHECKS:
        raise ValueError('ENGINE_LINK_SEALED_SUPPORTED_EVIDENCE_REQUIRED')
    artifacts = [a for a in evidence.artifacts if a.name == artifact_name]
    if len(artifacts) != 1:
        raise ValueError('ENGINE_LINK_UNIQUE_ARTIFACT_REQUIRED')
    artifact = artifacts[0]
    if len(artifact_bytes) != artifact.bytes or hashlib.sha256(artifact_bytes).hexdigest() != artifact.sha256:
        raise ValueError('ENGINE_LINK_ARTIFACT_BYTES_MISMATCH')
    payload = strict_json(artifact_bytes, limit=16 * 1024 * 1024)
    request = select_request(records, request_id)
    context = request['context']; target = context.get('target') or {}
    gaps = []
    if context.get('backend_kind') == 'mock':
        gaps.append('MOCK_BACKEND_HAS_NO_NATIVE_ENGINE_EVIDENCE')
    if not context.get('run_id') or not context.get('observation_session_id'):
        gaps.append('RUNTIME_RUN_SESSION_UNAVAILABLE')
    if not (context.get('policy') or {}).get('sha256'):
        gaps.append('RUNTIME_POLICY_SHA256_UNAVAILABLE')
    if not context.get('policy_bundle'):
        gaps.append('RUNTIME_POLICY_BUNDLE_UNAVAILABLE')
    expected = {key: target.get(key) for key in ('pod_uid', 'container_id', 'image_digest')}
    if any(not value for value in expected.values()) or not target.get('instance_id'):
        gaps.append('RUNTIME_TARGET_GENERATION_UNAVAILABLE')
    # Existing Kubernetes transport reports include the actual selected engine
    # Pod UID/container ID/imageID. A declaration of an instance ID alone is
    # never enough to match another capture generation.
    transports = _transports(payload)
    bindings = [row.get('binding') for row in transports if type(row.get('binding')) is dict]
    engines = [row.get('engine') for row in bindings if type(row.get('engine')) is dict]
    same_generation = bool(engines) and all(
        row.get('pod_uid') == expected['pod_uid'] and row.get('container_id') == expected['container_id']
        and isinstance(row.get('image_id'), str)
        and _image_digest(row['image_id']) == expected['image_digest'] for row in engines
    ) if all(expected.values()) else False
    if not same_generation:
        gaps.append('UPDATE_TARGET_GENERATION_UNBOUND')
    if not bindings or any(row.get('run_id') != context.get('run_id') for row in bindings):
        gaps.append('UPDATE_RUNTIME_RUN_UNBOUND')
    if any(row.get('collection_gap') is not False for row in transports):
        gaps.append('UPDATE_TRANSPORT_COLLECTION_INCOMPLETE')
    began, ended = evidence.execution.started_at, evidence.execution.finished_at
    overlaps = bool(began is not None and ended is not None and began <= ended
                    and began <= _time(request['finished_at']) and _time(request['started_at']) <= ended)
    if not overlaps:
        gaps.append('UPDATE_REQUEST_INTERVAL_UNBOUND')
    upstream = context.get('upstream_request_id') or request_id
    upstream_sha = hashlib.sha256(upstream.encode()).hexdigest()
    native = payload.get('native_projection') or {}
    native_rows = [native.get('anchor') or {}, *(native.get('bindings') or [])]
    request_bound = any(row.get('asgi_request_sha256') == upstream_sha or
                        row.get('external_request_sha256') == upstream_sha for row in native_rows)
    if evidence.check_id == 'REQ-15':
        request_bound = any(row.get('runtime_request_sha256') == hashlib.sha256(request_id.encode()).hexdigest()
                            and row.get('native_rows_observed') is True
                            for row in (payload.get('safe_facts') or {}).get('runtime_request_bindings', []))
    if evidence.check_id in {'REQ-12', 'REQ-15', 'LIF-10'} and not request_bound:
        gaps.append('UPDATE_NATIVE_REQUEST_UNBOUND')
    # Worker readiness/failure and process pressure are generation-scoped.
    # A shared generation/window supplies correlation, never request causality.
    return {'schema': 'byul-engine-link/1', 'request': request,
            'evidence_ref': evidence.ref().model_dump(mode='json'),
            'environment_ref': evidence.environment.model_dump(mode='json'),
            'plan_ref': evidence.plan.model_dump(mode='json'),
            'artifact': artifact.model_dump(mode='json'),
            'binding': {'state': 'bound' if not gaps else 'incomplete', 'gaps': gaps,
                        'scope': 'native_request' if evidence.check_id in {'REQ-12', 'REQ-15', 'LIF-10'} else 'target_generation_and_interval',
                        'target_generation_observed': same_generation,
                        'native_request_observed': request_bound, 'interval_overlap': overlaps},
            'check_id': evidence.check_id, 'data_origin': evidence.data_origin.value,
            'execution': evidence.execution.model_dump(mode='json'),
            'observations': {key: value.model_dump(mode='json') for key, value in evidence.observations.items()},
            'new_security_verdict': None, 'actual_engine_execution_verified': False}


def project_termination(records: list[dict], request_id: str, packet: dict | None,
                        max_delay_ms: int, required_scopes: list[str], *, source_binding: dict | None = None,
                        source_data_origin: str | None = None, checkpoint: bool = False) -> dict:
    """Reuse the source-qualified native projector for one runtime disconnect."""
    from guard.adapters.vllm_request_termination import project_native, SCOPES
    if type(max_delay_ms) is not int or not 1 <= max_delay_ms <= 600000:
        raise ValueError('ENGINE_LINK_FINITE_TERMINATION_DEADLINE_REQUIRED')
    if not required_scopes or not set(required_scopes) <= SCOPES:
        raise ValueError('ENGINE_LINK_SUPPORTED_TERMINATION_SCOPES_REQUIRED')
    request = select_request(records, request_id)
    context = request['context']
    profiles={r['capture']['records'][0]['payload'].get('profile')for r in (packet or {}).get('recordings',[])if type(r)is dict and type(r.get('capture'))is dict and r['capture'].get('records')}
    backend=context.get('backend_kind');route=context.get('route')
    modern=(profiles=={'vllm-native-lifecycle-metadata/23'}and backend=='vllm'or profiles=={'sglang-native-lifecycle-metadata/1'}and backend=='sglang')and route in {'POST /v1/completions','POST /v1/chat/completions'}
    supported=bool(modern or backend=='vllm'and route=='POST /v1/completions'and not profiles.intersection({'vllm-native-lifecycle-metadata/23','sglang-native-lifecycle-metadata/1'}))
    upstream = context.get('upstream_request_id') or request_id
    selected_sha = hashlib.sha256(upstream.encode()).hexdigest()
    native = project_native(packet if supported else None, selected_sha, max_delay_ms, required_scopes)
    if checkpoint and modern:
        if backend == 'vllm':
            from guard.adapters.vllm_termination_projection import project_checkpoint
        else:
            from guard.adapters.sglang_request_termination import project_checkpoint
        native = project_checkpoint(packet, selected_sha, max_delay_ms, required_scopes)
        if backend == 'sglang':native['whole_scope_complete'] = native['native_termination_verified']
    gaps=[]if modern else['RUNTIME_NATIVE_PROFILE_OR_ROUTE_UNQUALIFIED'];target=context.get('target')or{};binding=source_binding if type(source_binding)is dict else{};engine=binding.get('engine')or{}
    expected_image=_image_digest(target.get('image_digest'))if type(target.get('image_digest'))is str else None
    actual_image=_image_digest(engine.get('image_id'))if type(engine.get('image_id'))is str else None
    if not(all(target.get(k)for k in ('pod_uid','container_id','instance_id'))and type(target.get('restart_count'))is int and target['restart_count']>=0 and type(engine.get('restart_count'))is int and expected_image and actual_image==expected_image and all(engine.get(k)==target[k]for k in ('pod_uid','container_id','restart_count'))):gaps.append('RUNTIME_NATIVE_TARGET_GENERATION_UNBOUND')
    if not context.get('run_id')or binding.get('run_id')!=context['run_id']or not binding.get('context'):gaps.append('RUNTIME_NATIVE_RUN_UNBOUND')
    policy_sha=(context.get('policy')or{}).get('sha256');bundle_sha=(context.get('policy_bundle')or{}).get('sha256')
    if not context.get('observation_session_id')or not(type(policy_sha)is str and re.fullmatch('[0-9a-f]{64}',policy_sha))or not(type(bundle_sha)is str and re.fullmatch('[0-9a-f]{64}',bundle_sha)):gaps.append('RUNTIME_NATIVE_POLICY_SNAPSHOT_UNAVAILABLE')
    for side in ('before','after'):
        owner=((packet or{}).get(side)or{}).get('owner')or{}
        image=_image_digest(owner.get('reported_image_id'))if type(owner.get('reported_image_id'))is str else None
        if not(engine.get('namespace_uid')and owner.get('namespace_uid')==engine['namespace_uid']and owner.get('pod_uid')==target.get('pod_uid')and owner.get('container_id')==target.get('container_id')and type(owner.get('restart_count'))is int and owner.get('restart_count')==target.get('restart_count')and image is not None and image==expected_image):gaps.append('RUNTIME_NATIVE_CAPTURE_OWNER_UNBOUND');break
    if source_data_origin not in {'EXAMPLE','DECLARED','MEASURED'}:gaps.append('RUNTIME_NATIVE_SOURCE_ORIGIN_UNQUALIFIED')
    bound=bool(modern and not gaps)
    terminated=request['final_outcome']in {'client_disconnected','deadline_exceeded','ENGINE_DEADLINE'}
    complete=bool(bound and terminated and native.get('whole_scope_complete'))
    return {'schema': 'byul-runtime-termination/1', 'request': request, 'native_projection': native,
            **({'closure_basis':'live_checkpoint','checkpoint_coverage_verified':native.get('checkpoint_coverage_verified') is True,'source_capture_mode':packet.get('capture_mode')} if checkpoint else {}),
            'support': 'source_qualified_native_lifecycle'if modern else 'source_qualified_vllm_completions' if supported else 'unsupported_native_route_or_engine',
            'runtime_native_binding':{'state':'bound'if bound else'incomplete','gaps':gaps},'source_data_origin':source_data_origin,
            'configured_deadline_ms': max_delay_ms, 'required_scopes': required_scopes,
            'gateway_terminated': request['final_outcome'] in {'client_disconnected', 'deadline_exceeded', 'ENGINE_DEADLINE'},
            'native_termination_verified': complete, 'worker_gpu_release_verified': bool(complete and native.get('worker_gpu_release_verified')),
            'actual_engine_execution_verified':False,'new_security_verdict': None}
