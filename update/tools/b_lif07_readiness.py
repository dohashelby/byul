"""LIF07: worker 준비 단계 정제 관측과 원 manual 증거 연결."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import hashlib
import os
import stat
from pathlib import Path
import re

from guard.collectors.lifecycle import load_prepared
from guard.collectors.manual import build_template, dump_template, import_evidence
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.models import Mode, canonical_json, load_pack
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle, verify_complete
from guard.store.packs import get_pack
from tools.b_req20_graph import PACK_REF, _capture_target, _inventory, _refs, _read, _write, _sha, digest

SCHEMA = 'byul-lif07-worker-readiness-capture/1'
PROFILE = 'owned-worker-phase-readiness/1'
# 공개 승인 worker/READY-send/Unready HTTP/controlled-delay 계약 수신 전 실행 관문은 닫힌다.
NATIVE_CONTRACT = None
UNREADY_HTTP_CONTRACT = None
DELAY_CONTRACT = None
NATIVE_OBSERVATION_PROFILE = 'vllm-native-lifecycle-metadata/21'
GAPS = frozenset({'WORKER_SOURCE_CONTRACT_UNAVAILABLE', 'UNREADY_HTTP_UNAVAILABLE',
    'CONTROLLED_DELAY_UNAVAILABLE', 'READY_SEND_UNOBSERVED', 'NATIVE_OUTER_BINDING_UNVERIFIED',
    'ORDER_UNVERIFIED', 'INVALID_SNAPSHOT', 'SNAPSHOT_COORDINATE_AMBIGUOUS', 'WORKER_CLEANUP_UNVERIFIED'})


class EvidencePreservationError(RuntimeError):
    def __init__(self, safe_facts, stages, issues, output):
        super().__init__('LIF07_EVIDENCE_PRESERVATION_FAILED')
        self.safe_facts = deepcopy(safe_facts)
        self.recovery = {'status': 'failed', 'success': False, 'output': str(output),
            'stages': deepcopy(stages), 'issues': sorted(set(issues + ['EVIDENCE_PRESERVATION_FAILED'])),
            'capture_rerun_required': False, 'owned_partial_files_retained': True}


def _capability(prepared):
    # None를 입력/현재 실행 hash나 다른 점검 profile로 대체하지 않는다.
    return False


def preflight(*, store, plan_id, pack, target, prepared, manifest, output, entered_by):
    from guard.runtime.kubernetes_binding import validate_capture_target as validate_target
    from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
    source = Store.read_only(Path(store)); verify_complete(source)
    original = load_pack(Path(pack)); plan = source.latest(plan_id)
    if (plan.kind != 'PLAN' or plan.mode != Mode.audit or len(plan.inputs.environments) != 1
            or plan.inputs.pack != original.ref() or original.ref().model_dump(mode='json') != PACK_REF
            or get_pack(source, plan.inputs.pack).ref() != original.ref()):
        raise ValueError('LIF07_ORIGINAL_PACK31_SINGLE_AUDIT_PLAN_REQUIRED')
    check = original.checks['LIF-07']
    if (check.revision != 5 or check.executor is not None or check.judge is not None
            or [(p.key, p.type, p.required) for p in check.policy_params] != [('required_readiness_items', 'list[str]', True)]
            or check.required_observations or len(check.required_observations_prose) != 1
            or len(check.challenge_steps) != 3 or not plan.item('LIF-07').selected):
        raise ValueError('LIF07_ORIGINAL_MANUAL_DEFINITION_REQUIRED')
    env, policy = source.resolve(plan.inputs.environments[0]), source.resolve(plan.inputs.policy)
    if (policy.pack != original.ref() or env.engine.name != 'vllm' or env.engine.version != '0.31.0'
            or env.runtime.gpu_count != 1 or env.runtime.tp_groups != 1 or not env.model.id
            or not env.model.revision or not _sha(env.model.inventory_sha256)):
        raise ValueError('LIF07_SELECTED_ENV_FILTER_REQUIRED')
    for obj, kind in ((env, 'ENV'), (policy, 'POL')):
        if re.fullmatch(kind + '-[0-9A-HJKMNP-TV-Z]{26}', obj.id) is None:
            raise ValueError('LIF07_CANONICAL_ENV_POL_IDS_REQUIRED')
    t, m, assets = _read(target), _read(manifest), load_prepared(prepared)
    validate_target(_capture_target(env, t), env)
    from guard.collectors.model_mapping import validate_model_mapping
    validate_model_mapping(env, t.get('model_mapping'))
    transport = KubernetesHTTP(t, env, duration=180, profile=REQUEST_BINDING_PROFILE)
    if (type(m) is not dict or set(m) != {'schema', 'environment_ref', 'target_sha256', 'session', 'run_id',
            'owner', 'recovery_owner', 'request'} or m['schema'] != 'byul-lif07-cohort/1'
            or m['environment_ref'] != env.ref().model_dump(mode='json') or m['target_sha256'] != digest(t)
            or m['session'] != assets['session'] or m['run_id'] != t['kubernetes']['run_id']
            or any(type(m[k]) is not str or not m[k].strip() or len(m[k]) > 200 for k in ('owner', 'recovery_owner'))):
        raise ValueError('LIF07_EXACT_OWNER_SESSION_MANIFEST_REQUIRED')
    body = m['request']
    if (type(body) is not dict or set(body) != {'model', 'messages', 'max_tokens', 'temperature'}
            or body['model'] != transport.api_model or type(body['max_tokens']) is not int or body['max_tokens'] != 1
            or type(body['temperature']) not in (int, float) or body['temperature'] != 0
            or type(body['messages']) is not list or len(body['messages']) != 1
            or type(body['messages'][0]) is not dict or set(body['messages'][0]) != {'role', 'content'}
            or body['messages'][0]['role'] != 'user' or type(body['messages'][0]['content']) is not str
            or not body['messages'][0]['content'] or len(canonical_json(body)) > 65536):
        raise ValueError('LIF07_EXISTING_BOUNDED_CANARY_REQUIRED')
    out = Path(output).absolute()
    if (out.exists() or out.is_symlink() or any(p.is_symlink() for p in out.parents)
            or out.resolve().is_relative_to(source.root.resolve()) or source.root.resolve().is_relative_to(out.resolve())
            or out.resolve().is_relative_to(Path(__file__).resolve().parents[2])
            or type(entered_by) is not str or not entered_by.strip()):
        raise ValueError('LIF07_FRESH_OWNED_OUTPUT_REQUIRED')
    return source, original, plan, env, policy, t, assets, m


def _empty(env, target, prepared, manifest):
    now = datetime.now(timezone.utc).isoformat()
    return {'schema': SCHEMA, 'profile': PROFILE, 'environment_ref': env.ref().model_dump(mode='json'),
        'target_sha256': digest(target), 'session': prepared['session'], 'run_id': manifest['run_id'],
        'started_at': now, 'finished_at': now, 'data_origin': 'EXAMPLE' if env.data_origin.value == 'EXAMPLE' else 'DECLARED',
        'snapshots': [], 'cleanup': {'owned_host_complete': True, 'worker_observer_closed': None}}


def _safe_snapshot(value, *, native_observation=False):
    # producer가 소유하는 closed vocabulary를 실제 소비 때만 읽는다.
    from guard.adapters import vllm_worker_readiness as native
    if (type(value) is not dict or value.get('schema') != native.SCHEMA
            or type(value.get('point')) is not str or value.get('point') not in native.POINTS | {'unsupported'} or type(value.get('phase')) is not str or value.get('phase') not in native.PHASES | {'unsupported'}
            or type(value.get('status')) is not str or value.get('status') not in native.STATUSES
            or not (type(value.get('seq')) is int and value['seq'] > 0 or value.get('seq') is None and value.get('status') == 'unavailable')):
        return None
    binding = value.get('binding'); binding = binding if type(binding) is dict else {}
    safe_binding = {k: binding[k] for k in ('call_id_sha256', 'session_id_sha256', 'process_start_sha256',
        'asgi_request_id_sha256', 'native_request_id_sha256') if _sha(binding.get(k))}
    safe_binding['pid'] = binding.get('pid') if type(binding.get('pid')) is int and binding['pid'] > 0 else None
    metadata = value.get('metadata'); metadata = metadata if type(metadata) is dict else {}
    safe = {k: metadata.get(k) if _sha(metadata.get(k)) else None for k in ('owner_token', 'thread_token')}
    for k in ('rank', 'local_rank'):
        safe[k] = metadata.get(k) if type(metadata.get(k)) is int and 0 <= metadata[k] < 2**31 else None
    for k in ('enforce_eager', 'explicit_load_dummy_weights'):
        safe[k] = metadata.get(k) if type(metadata.get(k)) is bool else None
    safe['configured_graph_mode'] = metadata.get('configured_graph_mode') if type(metadata.get('configured_graph_mode')) is str and metadata.get('configured_graph_mode') in native.GRAPH_MODES else None
    transition = metadata.get('call_transition'); transition = transition if type(transition) is dict else {}
    safe['call_transition'] = {k: transition.get(k) if type(transition.get(k)) is bool else None for k in ('paired', 'same_owner', 'same_thread', 'returned')}
    safe['call_transition'].update({k: transition.get(k) if type(transition.get(k)) is int and type(value.get('seq')) is int and 0 < transition[k] <= value['seq'] else None for k in ('call_seq', 'end_seq')})
    safe.update(source_qualified=False, actual_worker_observed=False, gpu_completion_verified=None,
        all_model_ready_verified=None, ready_send_verified=None, service_gate_verified=None)
    outer = value.get('native_outer'); outer = outer if type(outer) is dict else {}
    safe_outer = {k: outer.get(k) if _sha(outer.get(k)) else None for k in ('source_sha256', 'code_sha256', 'body_code_sha256', 'entry_code_sha256', 'generation_sha256', 'session_id_sha256', 'process_start_sha256')}
    safe_outer['native_record_seq'] = outer.get('native_record_seq') if type(outer.get('native_record_seq')) is int and outer['native_record_seq'] > 0 else None
    safe_outer['recording'] = outer.get('recording') if type(outer.get('recording')) is int and outer['recording'] >= 0 else None
    safe_outer['source_contract_verified'] = bool(native_observation and outer.get('source_contract_verified') is True)
    safe_outer.update({k: outer.get(k) if type(outer.get(k)) is int and 0 <= outer[k] < 2**63 else None for k in ('pid', 'start_ticks')})
    domain = outer.get('process_domain'); domain = domain if type(domain) is dict else {}
    safe_outer['process_domain'] = {k: domain.get(k) if _sha(domain.get(k)) else None for k in ('boot_sha256', 'time_offsets_sha256')}
    safe_outer['process_domain'].update({k: domain.get(k) if type(domain.get(k)) is int and 0 <= domain[k] < 2**63 else None for k in ('pid', 'start_ticks', 'pid_namespace', 'time_namespace')})
    safe_outer['inner_body_entry_observed'] = False
    safe_outer['actual_worker_verified'] = False
    safe_outer['gpu_completion_verified'] = False
    safe_outer['entry_kind'] = outer.get('entry_kind') if outer.get('entry_kind') in ('direct-body', 'otel-sync-wrapper') else None
    if native_observation:
        from guard.adapters.vllm_worker_generation_sensor import RUNTIME_CODE_CONTRACTS
        safe_outer['observer_runtime'] = outer.get('observer_runtime') if type(outer.get('observer_runtime')) is str and outer['observer_runtime'] in RUNTIME_CODE_CONTRACTS else None
    else:
        safe_outer['observer_runtime'] = None
    for key in ('source_verified', 'callback_code_verified', 'callback_error', 'process_binding_verified'):
        safe_outer[key] = outer.get(key) if native_observation and type(outer.get(key)) is bool else False
    return {'stage': value.get('stage') if value.get('stage') in ('initial', 'final') else None, 'schema': native.SCHEMA, 'point': value['point'], 'phase': value['phase'], 'status': value['status'],
        'seq': value['seq'], 'binding': safe_binding, 'metadata': safe, 'native_outer': safe_outer,
        'gaps': sorted({g for g in value.get('gaps', []) if type(g) is str and g in native.GAPS})}


def _collect_native(env, target, prepared, manifest):
    # 관측 전용 helper를 선택한 실행에서만 import한다. 기본/preflight는 읽지 않는다.
    from tools.b_lif07_native_capture import collect_startup
    return collect_startup(env, target, prepared, manifest)


def _safe_native21(value):
    """A가 parser 검증한 projection의 고정 scalar만 보존한다. 실제 역할 자격은 부여하지 않는다."""
    from tools import b_lif07_native_capture as helper
    from guard.adapters import vllm_worker_generation_sensor as generation
    from guard.adapters import vllm_worker_generation_contract as generation_contract
    from guard.adapters import vllm_worker_startup_contract as startup_contract
    if (type(value) is not dict or value.get('schema') != 'guard-worker-generation-projection/1'
            or value.get('recording_profile') != NATIVE_OBSERVATION_PROFILE):
        return None
    names = ('events', 'model_candidates', 'generation_events', 'generation_candidates', 'ready_sends')
    if any(type(value.get(k)) is not list or len(value[k]) > helper.MAX_NATIVE21_ROWS for k in names):
        return None
    hashes = frozenset({'session_sha256', 'process_sha256', 'observer_code_sha256', 'source_sha256',
        'body_code_sha256', 'entry_code_sha256', 'call_sha256', 'parent_call_sha256', 'main_call_sha256',
        'constructor_call_sha256', 'owner_token', 'worker_token', 'worker_payload_sha256', 'worker_call_sha256',
        'wrapper_call_sha256', 'pipe_token', 'pipe_call_sha256', 'delegate_code_sha256', 'root_load_sha256', 'model_object_sha256'})
    integers = frozenset({'recording', 'pid', 'start_ticks', 'event_seq', 'rank', 'local_rank', 'operation_started_ns',
        'operation_finished_ns', 'constructor_event_seq', 'wrapper_event_seq', 'worker_event_seq', 'link_event_seq',
        'worker_start_seq', 'worker_end_seq', 'root_event_seq', 'start_seq', 'end_seq'})
    booleans = frozenset({'process_binding_verified', 'source_verified', 'callback_code_verified', 'callback_error',
        'local_object_generation_candidate', 'local_worker_generation_model_candidate', 'same_startup_model_root_candidate',
        'paired', 'source_main_entry_observed', 'original_send_return_observed', 'original_send_raise_observed',
        'original_close_return_observed', 'original_close_raise_observed', 'outer_call_pair_verified', 'callback_method_pair_verified',
        'returned', 'source_entry_observed', 'main_active_or_end_unobserved'})
    words = {'point': generation_contract.POINTS | set(startup_contract.SPAN_NAMES),
        'worker_point': set(startup_contract.SPAN_NAMES), 'phase': {'CALL', 'RETURN', 'RAISE'},
        'terminal_phase': {'CALL', 'RETURN', 'RAISE'}, 'entry_kind': {'direct-body', 'otel-sync-wrapper'},
        'operation': {'SEND', 'CLOSE'}, 'status': {'READY', 'other'}, 'exception_kind': {'exception', 'base-exception'},
        'observer_runtime': set(generation.RUNTIME_CODE_CONTRACTS),
        'source_module': set(generation_contract.SOURCE_SHA256) | set(startup_contract.SOURCE_SHA256),
        'stage': {'initial', 'final'}}
    def row(item, *, nested=False):
        if type(item) is not dict:
            return None
        safe = {k: item[k] for k in hashes if _sha(item.get(k))}
        safe.update({k: item[k] for k in integers if type(item.get(k)) is int and 0 <= item[k] < 2**63})
        safe.update({k: item[k] for k in booleans if type(item.get(k)) is bool})
        safe.update({k: item[k] for k, allowed in words.items() if type(item.get(k)) is str and item[k] in allowed})
        sources = item.get('source_files')
        if type(sources) is dict:
            safe['source_files'] = {k: v for k, v in sources.items() if startup_contract.SOURCE_SHA256.get(k) == v}
        if not nested and type(item.get('worker_generation_candidates')) is list and len(item['worker_generation_candidates']) <= 256:
            safe['worker_generation_candidates'] = [r for v in item['worker_generation_candidates'] if (r := row(v, nested=True)) is not None]
        safe.update({k: False for k in generation.TRUTH})
        return safe
    missing = helper.PROJECTION_MISSING_CODES
    reasons = value.get('missing') if type(value.get('missing')) is list else []
    result = {'schema': value['schema'], 'recording_profile': value['recording_profile'],
        **{k: [r for v in value[k] if (r := row(v)) is not None] for k in names},
        'missing': sorted({v for v in reasons if type(v) is str and v in missing}),
        'additional_missing_count': sum(type(v) is not str or v not in missing for v in reasons),
        **{k: False for k in generation.TRUTH}}
    return result


def _safe_native_collection(raw):
    from tools import b_lif07_native_capture as helper
    from guard.adapters import vllm_worker_readiness as callback
    value = raw.get('native_collection'); value = value if type(value) is dict else {}
    result = {k: value.get(k) if type(value.get(k)) is bool else None for k in (
        'initial_capture_attempted', 'final_capture_attempted', 'live_census_stable', 'recorded_files_covered', 'reader_collection_gap')}
    result['snapshot_omitted_count'] = value.get('snapshot_omitted_count') if type(value.get('snapshot_omitted_count')) is int and value['snapshot_omitted_count'] >= 0 else None
    result.update(node_runtime_image_verified=False, producer_authenticated=False, record_authenticity_verified=False)
    def identity(item):
        if type(item) is not dict:
            return None
        return {'stage': item.get('stage') if item.get('stage') in ('initial', 'final') else None,
            **{k: item.get(k) if type(item.get(k)) is int and 0 <= item[k] < 2**63 else None
               for k in ('recording', 'pid', 'start_ticks', 'event_seq')}}
    recordings = value.get('process_recordings'); recordings = recordings if type(recordings) is list and len(recordings) <= helper.MAX_PROCESS_RECORDINGS else []
    result['process_recordings'] = []
    for item in recordings:
        if (safe := identity(item)) is not None:
            safe.update({k: item.get(k) if type(item.get(k)) is bool else None for k in
                ('process_context_verified', 'session_owner_match', 'listed_file_identity_match')})
            result['process_recordings'].append(safe)
    states = value.get('capture_states'); states = states if type(states) is list and len(states) <= 2 else []
    result['capture_states'] = [dict(stage=v.get('stage') if v.get('stage') in ('initial', 'final') else None,
        **{k: v.get(k) if type(v.get(k)) is bool else None for k in ('capture_succeeded', 'live_census_stable', 'recorded_files_covered')}) for v in states if type(v) is dict]
    codes = value.get('parser_diagnostic_codes'); codes = codes if type(codes) is list else []
    allowed = getattr(helper, 'PARSER_DIAGNOSTIC_CODES', frozenset())
    result['parser_diagnostic_codes'] = sorted({v for v in codes if type(v) is str and v in allowed})
    result['additional_parser_diagnostic_count'] = sum(type(v) is not str or v not in allowed for v in codes)
    closes, generation_closes = [], []
    for name, output in (('observer_closes', closes), ('generation_closes', generation_closes)):
        values = raw.get(name); values = values if type(values) is list and len(values) <= helper.MAX_CLOSE_ROWS else []
        for item in values:
            safe = identity(item)
            if safe is None:
                continue
            if name == 'observer_closes':
                close = item.get('result'); close = close if type(close) is dict else {}
                safe['result'] = {k: close.get(k) if type(close.get(k)) is bool else None for k in ('complete', 'collection_gap')}
                safe['result']['pending_calls'] = close.get('pending_calls') if type(close.get('pending_calls')) is int and 0 <= close['pending_calls'] <= 512 else None
                safe['result']['gaps'] = sorted({v for v in close.get('gaps', []) if type(v) is str and v in callback.GAPS})
            else:
                safe['local_observer_closed'] = item.get('local_observer_closed') if type(item.get('local_observer_closed')) is bool else None
                safe['cleared_local_worker_bindings'] = item.get('cleared_local_worker_bindings') if type(item.get('cleared_local_worker_bindings')) is int and 0 <= item['cleared_local_worker_bindings'] <= 256 else None
            output.append(safe)
    return result, closes, generation_closes


def project(raw, env, target, prepared, manifest, *, native_observation=False):
    if (type(raw) is not dict or raw.get('schema') != SCHEMA or raw.get('profile') != PROFILE
            or raw.get('environment_ref') != env.ref().model_dump(mode='json') or raw.get('target_sha256') != digest(target)
            or raw.get('session') != prepared['session'] or raw.get('run_id') != manifest['run_id']
            or raw.get('data_origin') not in {'DECLARED', 'EXAMPLE'}):
        raise ValueError('LIF07_CAPTURE_BINDING_MISMATCH')
    started, finished = (datetime.fromisoformat(raw[k]) for k in ('started_at', 'finished_at'))
    if started.tzinfo is None or finished.tzinfo is None or finished < started:
        raise ValueError('LIF07_CAPTURE_TIME_INVALID')
    values = raw.get('snapshots', [])
    if type(values) is not list or len(values) > 512:
        raise ValueError('LIF07_BOUNDED_SNAPSHOTS_REQUIRED')
    rows, seen, gaps = [], {}, set(GAPS - {'INVALID_SNAPSHOT', 'SNAPSHOT_COORDINATE_AMBIGUOUS'})
    for value in values:
        row = _safe_snapshot(value, native_observation=native_observation)
        if row is None:
            gaps.add('INVALID_SNAPSHOT'); continue
        coordinate = (row['stage'], row['point'], row['phase'], row['seq'], digest(row['binding']), row['metadata']['owner_token'], row['metadata']['thread_token'], digest(row['native_outer']))
        if coordinate in seen:
            if seen[coordinate] == row: continue
            gaps.add('SNAPSHOT_COORDINATE_AMBIGUOUS')
        seen[coordinate] = row; rows.append(row)
    # B observer sequence만으로 같은 owner/thread/call의 메서드 반환 관계를 보존한다.
    pairs = []
    for row in rows:
        tr = row['metadata']['call_transition']
        if row['phase'] not in {'RETURN', 'RAISE'} or type(row['seq']) is not int or tr['paired'] is not True or tr['same_owner'] is not True or tr['same_thread'] is not True: continue
        if (row['binding'].get('session_id_sha256') != digest(prepared['session'])
                or not _sha(row['binding'].get('process_start_sha256')) or row['binding'].get('pid') is None
                or tr['returned'] is not (row['phase'] == 'RETURN')): continue
        calls = [c for c in rows if c['phase'] == 'CALL' and c['stage'] == row['stage'] and c['point'] == row['point']
            and type(c['seq']) is int and c['seq'] == tr['call_seq'] and c['seq'] < row['seq'] == tr['end_seq']
            and c['binding'] == row['binding'] and all(c['native_outer'].get(k) == row['native_outer'].get(k) for k in ('recording', 'generation_sha256', 'source_sha256', 'body_code_sha256', 'entry_code_sha256', 'session_id_sha256', 'process_start_sha256', 'process_domain')) and c['metadata']['owner_token'] == row['metadata']['owner_token']
            and c['metadata']['thread_token'] == row['metadata']['thread_token']
            and row['metadata']['owner_token'] is not None and row['metadata']['thread_token'] is not None]
        if len(calls) == 1 and 'SNAPSHOT_COORDINATE_AMBIGUOUS' not in gaps:
            pairs.append({'stage': row['stage'], 'point': row['point'], 'call_seq': tr['call_seq'], 'end_seq': row['seq'], 'phase': row['phase'],
                'source_qualified': False, 'actual_worker_observed': False, 'generation_verified': False})
    cleanup = raw.get('cleanup'); cleanup = cleanup if type(cleanup) is dict else {}
    safe_cleanup = {k: cleanup.get(k) if type(cleanup.get(k)) is bool else None for k in ('owned_host_complete', 'worker_observer_closed', 'pre_snapshot_attempted', 'pre_snapshot_verified',
        'post_snapshot_attempted', 'post_snapshot_verified', 'api_epoch_same', 'selected_session_seal_attempted',
        'selected_session_sealed', 'final_capture_attempted', 'final_capture_complete')}
    safe_cleanup['gpu_model_recovery_verified'] = None
    close = raw.get('observer_close'); safe_close = None
    if type(close) is dict:
        from guard.adapters import vllm_worker_readiness as native
        safe_close = {k: close.get(k) if type(close.get(k)) is bool else None for k in ('complete', 'collection_gap')}
        safe_close['pending_calls'] = close.get('pending_calls') if type(close.get('pending_calls')) is int and 0 <= close['pending_calls'] <= 512 else None
        safe_close['gaps'] = sorted({g for g in close.get('gaps', []) if type(g) is str and g in native.GAPS})
    native_view = _safe_native21(raw.get('native21')) if native_observation else None
    native_info, closes, generation_closes = _safe_native_collection(raw) if native_observation else (None, [], [])
    if native_observation:
        from tools import b_lif07_native_capture as helper
        gaps.update(v for v in raw.get('gaps', []) if type(v) is str and v in helper.GAPS)
        if native_view is None: gaps.add('NATIVE21_PROJECTION_UNAVAILABLE')
    attempted = [safe_cleanup.get(k) for k in ('pre_snapshot_attempted', 'post_snapshot_attempted', 'selected_session_seal_attempted', 'final_capture_attempted')] if native_observation else [False]
    if native_info is not None: attempted += [native_info.get(k) for k in ('initial_capture_attempted', 'final_capture_attempted')]
    target_io = True if any(v is True for v in attempted) else False if all(v is False for v in attempted) else None
    source_validation = None
    if native_observation:
        claimed = raw.get('source_validation'); claimed = claimed if type(claimed) is dict else {}
        source_validation = {'reviewed_common_commit': helper.REVIEWED_COMMON_COMMIT if claimed.get('reviewed_common_commit') == helper.REVIEWED_COMMON_COMMIT else None,
            'approved_native_source_commit': helper.APPROVED_SOURCE_COMMIT if claimed.get('approved_native_source_commit') == helper.APPROVED_SOURCE_COMMIT else None,
            'runtime_hash_approval': False, 'producer_authenticated': False}
    return {'schema': SCHEMA, 'profile': PROFILE, 'environment_ref': env.ref().model_dump(mode='json'),
        'native21': native_view, 'native_collection': native_info, 'native_source_validation': source_validation, 'observer_closes': closes, 'generation_closes': generation_closes,
        'target_io_attempted': target_io, 'target_http_requests': 0, 'controlled_delay_requests': 0,
        'target_sha256': digest(target), 'started_at': started.isoformat(), 'finished_at': finished.isoformat(),
        'data_origin': 'EXAMPLE' if raw['data_origin'] == 'EXAMPLE' or env.data_origin.value == 'EXAMPLE' else 'DECLARED',
        'rows': rows, 'method_pairs': pairs, 'qualified_rows': 0, 'requests': 0,
        'ready_send_observed': None, 'pre_ready_admission': None, 'post_ready_canary': None,
        'controlled_delay_activated': None, 'resumed_receipt': None, 'order_verified': False,
        'full_scope_coverage': False, 'expected_ranks': None, 'rank_coverage_verified': None, 'confirmed_violations': [], 'cleanup': safe_cleanup, 'observer_close': safe_close,
        'gaps': sorted(gaps)}


def _regular_bytes(path):
    """기존 2MiB JSON 상한과 strict parser를 위한 안정된 소유 파일 읽기."""
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('LIF07_INPUT_SYMLINK')
    selected = path.lstat()
    if not stat.S_ISREG(selected.st_mode) or not 0 < selected.st_size <= 2097152:
        raise ValueError('LIF07_INPUT_REGULAR_BOUNDED_FILE_REQUIRED')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 2097152
                or (selected.st_dev, selected.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError('LIF07_INPUT_REGULAR_BOUNDED_FILE_REQUIRED')
        raw = stream.read(2097153); after = os.fstat(stream.fileno()); current = path.lstat()
        # Windows에서 path-stat과 FD-stat의 ctime 의미가 다를 수 있다.
        # 같은 계열의 ctime 변화는 그대로 거부하고, 계열 사이는 inode/내용 시각으로 결합한다.
        identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        stable = lambda st: (*identity(st), st.st_mode, st.st_ctime_ns)
        if (stable(before) != stable(after) or stable(selected) != stable(current)
                or identity(selected) != identity(before) or identity(after) != identity(current)
                or not stat.S_ISREG(current.st_mode) or len(raw) != before.st_size
                or any(p.is_symlink() for p in (path, *path.parents))):
            raise ValueError('LIF07_INPUT_CHANGED_DURING_READ')
        return raw


def _input_hash(path):
    return hashlib.sha256(_regular_bytes(path)).hexdigest()


def _unchanged(source, before, inputs):
    try:
        return _inventory(source.root) == before and all(_input_hash(p) == h for p, h in inputs.items())
    except (OSError, ValueError):
        return False


def _import_capture(path):
    from guard.runtime.bounded import strict_json
    raw = _regular_bytes(path)
    value = strict_json(raw, limit=2097152)
    if type(value) is not dict or value.get('schema') != SCHEMA:
        raise ValueError('LIF07_BOUND_CAPTURE_OBJECT_REQUIRED')
    return value, hashlib.sha256(raw).hexdigest()


def run(*, store, plan_id, pack, target, prepared, manifest, output, entered_by, execute=False, import_capture=None, collect_native=False, _example_capture=None):
    source, original, plan, env, policy, t, assets, m = preflight(store=store, plan_id=plan_id, pack=pack,
        target=target, prepared=prepared, manifest=manifest, output=output, entered_by=entered_by)
    # preflight가 확인한 원본을 backend/project 실행 전에 고정한다.
    before = _inventory(source.root)
    input_paths = [Path(p) for p in (target, prepared, manifest)] + [Path(prepared).parent / n for n in assets['assets']]
    inputs = {str(p): _input_hash(p) for p in input_paths}
    if (sum((bool(execute), import_capture is not None, bool(collect_native))) > 1
            or collect_native and _example_capture is not None
            or import_capture is not None and _example_capture is not None):
        raise ValueError('LIF07_IMPORT_AND_LIVE_MODES_EXCLUSIVE')
    if _example_capture is not None and env.data_origin.value != 'EXAMPLE':
        raise ValueError('LIF07_EXAMPLE_CAPTURE_REQUIRES_EXAMPLE_ENV')
    binding = {k: v.ref().model_dump(mode='json') for k, v in [('environment', env), ('policy', policy), ('plan', plan), ('pack', original)]}
    if not execute and import_capture is None and not collect_native:
        return {'executed': False, 'requests': 0, 'capability_supported': False, 'gaps': sorted(GAPS - {'INVALID_SNAPSHOT', 'SNAPSHOT_COORDINATE_AMBIGUOUS'}), **binding}
    out = Path(output)
    stages = {k: False for k in ('artifact_stored', 'manual_evidence_stored', 'report_stored', 'bundle_restored', 'summary_stored')}; issues = []
    record = project(_empty(env, t, assets, m), env, t, assets, m)
    try:
        if import_capture is not None:
            raw, capture_hash = _import_capture(import_capture); inputs[str(Path(import_capture))] = capture_hash
        elif collect_native:
            raw = _collect_native(env, t, assets, m)
        else:
            # 승인 실제 capability가 없으므로 backend는 호출하지 않는다.
            raw = _empty(env, t, assets, m) if _example_capture is None else _example_capture(env, t, assets, m)
        record = project(raw, env, t, assets, m, native_observation=collect_native)
        record['input_mode'] = 'native_startup_observation' if collect_native else 'imported_observation_file' if import_capture is not None else 'EXAMPLE_callback' if _example_capture is not None else 'closed_live_capability'
        record['source_authentication_verified'] = False
        if not _unchanged(source, before, inputs):
            issues.append('ORIGINAL_INPUT_CHANGED')
            raise ValueError('LIF07_ORIGINAL_INPUT_CHANGED')
        out.mkdir(parents=True, exist_ok=False)
        export_bundle(source.root, out / 'source.zip'); restore_bundle(out / 'source.zip', out / 'store'); dest = Store(out / 'store')
        attachment = 'worker-readiness.json'
        try: _write(out / attachment, record); stages['artifact_stored'] = True
        except OSError: attachment = None; issues.append('ARTIFACT_WRITE_UNAVAILABLE')
        doc = build_template(dest, original, plan, include_automated=True)
        doc['checks'] = [x for x in doc['checks'] if x['check_id'] == 'LIF-07' and x['side'] == 'single']; entry, = doc['checks']
        entry.update(entered_by=entered_by, entered_at=record['finished_at'], started_at=record['started_at'], finished_at=record['finished_at'],
            attachments=[attachment] if attachment else [], verdicts={k: 'UNKNOWN' for k in ('security', 'functional', 'performance')},
            basis='worker 메서드 단계의 정제 metadata를 보존한다. 실제 READY-send/지연/접수·준비후canary 미관측으로 전체 준비 판정은 UNKNOWN이다.',
            limitations=['required_readiness_items와 기업 기한·수락 기준은 입력 그대로다. 단계 RETURN과 generator factory 반환은 준비 완료 근거로 사용하지 않는다.',
                'PodReady/HTTP200/단발 CPU generation_readiness를 rank·model 준비 완료로 올리지 않는다.',
                'host/probe/native clock domain은 독립이다. A 인과 receipt 없는 교차 시각 순서는 미확인이다.',
                'HTTP 요청·Pod 변경·지연 0. native 관측 모드의 읽기·선정 session seal 시도와 결과는 별도 보존한다. EXAMPLE snapshot과 외곽 source/code 사실을 실제 worker/GPU·상위 READY·서비스 자격으로 올리지 않는다.'])
        entry['normal_control']['result'] = 'not_attempted'
        for c in entry['challenges']: c['attempted'] = False
        entry['observation_notes'][0]['answer'] = '출처=' + record['data_origin'] + '; metadata행=' + str(len(record['rows'])) + '; 메서드짝=' + str(len(record['method_pairs'])) + '; 실제 READY/지연/접수/복구 receipt 미수집. gaps=' + str(record['gaps'])
        if attachment is None: entry['observation_notes'][0]['answer'] += '\nLIF07_SAFE_FACTS=' + canonical_json(record).decode('utf-8')
        form = out / 'manual-evidence.yaml'
        try:
            with form.open('x', encoding='utf-8') as stream: stream.write(dump_template(doc))
        except OSError: issues.append('MANUAL_FORM_WRITE_UNAVAILABLE')
        import_evidence(dest, original, form, document=doc); stages['manual_evidence_stored'] = True
        as_of = datetime.now(timezone.utc).replace(microsecond=0)
        results = judge_plan(dest, original, plan, as_of=as_of); report = build_report(dest, original, plan, as_of=as_of); dest.put(report)
        stages['report_stored'] = True; verify_complete(dest); refs = _refs(dest)
        export_bundle(dest.root, out / 'evidence.zip'); restore_bundle(out / 'evidence.zip', out / 'restored'); restored = Store(out / 'restored')
        replay = judge_plan(restored, original, plan, as_of=as_of)
        if _refs(restored) != refs or [r.ref() for r in replay] != [r.ref() for r in results]: raise ValueError('LIF07_EXACT_REF_RESTORE_MISMATCH')
        verify_complete(restored); stages['bundle_restored'] = True
        if not _unchanged(source, before, inputs):
            issues.append('ORIGINAL_INPUT_CHANGED'); raise ValueError('LIF07_ORIGINAL_INPUT_CHANGED')
        summary = {'executed': True, 'actual_target_executed': record['target_io_attempted'] if collect_native and record['data_origin'] != 'EXAMPLE' else False, 'native_observation_invoked': collect_native, 'target_io_attempted': record['target_io_attempted'], 'target_io_origin': record['data_origin'], 'selected_session_seal_attempted': record['cleanup'].get('selected_session_seal_attempted'), 'selected_session_sealed': record['cleanup'].get('selected_session_sealed'), 'requests': 0, 'status': 'stored', **binding,
            'capability_supported': False, 'full_scope_coverage': False, 'capture_origin': record['data_origin'], 'input_mode': record['input_mode'], 'imported_from_file': import_capture is not None, 'source_authentication_verified': False,
            'source_unchanged': True, 'exact_refs_restored': True, 'issues': issues,
            'verdicts': {k: 'UNKNOWN' for k in ('security', 'functional', 'performance')}, 'report': report.ref().model_dump(mode='json')}
        if attachment is None: summary['safe_facts'] = record
        _write(out / 'summary.json', summary); stages['summary_stored'] = True
        return summary
    except Exception:
        if import_capture is not None and 'input_mode' not in record:
            issues.append('CAPTURE_IMPORT_OR_PROJECT_UNAVAILABLE')
        raise EvidencePreservationError(record, stages, issues, out) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('store', 'pack', 'target', 'prepared', 'manifest', 'output'): parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--import-capture', type=Path)
    parser.add_argument('--collect-native', action='store_true')
    parser.add_argument('--plan', dest='plan_id', required=True); parser.add_argument('--entered-by', required=True); parser.add_argument('--execute', action='store_true')
    try: print(json.dumps(run(**vars(parser.parse_args(argv))), ensure_ascii=False))
    except EvidencePreservationError as error: parser.exit(1, json.dumps({'recovery': error.recovery, 'safe_facts': error.safe_facts}, ensure_ascii=False) + '\n')
    except Exception: parser.exit(1, 'LIF07_WORKER_READINESS_UNAVAILABLE\n')

if __name__ == '__main__': main()
