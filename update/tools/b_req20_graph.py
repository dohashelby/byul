"""원 REQ20: 기존 소유 Pod HTTP·native graph callback·manual 증거 연결."""
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
import uuid

from guard.collectors.lifecycle import load_prepared
from guard.collectors.manual import build_template, dump_template, import_evidence
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.models import Mode, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import strict_json
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle, verify_complete
from guard.store.packs import get_pack

PACK_REF = {'kind': 'PACK', 'id': 'PACK-BASELINE', 'revision': 31,
    'sha256': '54f4f2357533a8b201ad4397d4b182b08b89ee4821ffe67763e3fbecd8f369ac'}
SCHEMA = 'byul-req20-graph-capture/1'
PROFILE = 'owned-pod-native-graph-buffer-lifetime/1'
# A가 승인한 실제 worker/source/code/callback 계약만 수신한다. HEAD나 입력에서 생성하지 않는다.
NATIVE_CONTRACT = None
POINTS = {'GPUModelRunner._prepare_input_ids', 'GPUModelRunner._prepare_inputs', 'CUDAGraphWrapper.__call__',
    'AsyncGPUModelRunnerOutput.__init__', 'AsyncGPUModelRunnerOutput.get_output'}
MODES = {'NONE', 'FULL', 'PIECEWISE', 'FULL_AND_PIECEWISE'}
GAPS = {'NATIVE_GRAPH_CAPABILITY_UNAVAILABLE', 'COLLECTION_INCOMPLETE', 'GENERATION_CHANGED',
    'TOKEN_LENGTH_UNVERIFIED', 'TOKEN_LENGTH_DIFFERENT', 'CLEANUP_UNVERIFIED',
    'GRAPH_OUTPUT_CONSUMPTION_UNOBSERVED', 'LOGITS_SAMPLER_LINEAGE_UNOBSERVED',
    'CONSUMER_LINEAGE_UNVERIFIED', 'OUTPUT_BOUND', 'OUTPUT_METADATA_UNAVAILABLE',
    'COPY_COMPLETION_UNOBSERVED', 'VALUE_COMPARISON_UNOBSERVED', 'NO_NATIVE_GRAPH_EVENTS', 'AFTER_CONTROL_UNAVAILABLE'}


def digest(value):
    return sha256_hex(canonical_json(value))


def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _inventory(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(root).rglob('*')) if p.is_file()}


def _refs(store):
    return sorted((o.kind.value, o.id, o.revision, o.content_sha256)
        for kind in ('ENV', 'POL', 'CHG', 'PLAN', 'EVD', 'RES', 'RPT') for o in store.iter_kind(kind))


def _write(path, value):
    with Path(path).open('xb') as stream:
        stream.write(canonical_json(value))


def _read(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('REQ20_INPUT_SYMLINK')
    with path.open('rb') as stream:
        return strict_json(stream.read(2097153), limit=2097152)


def _capture_target(env, target):
    return {'schema': 'guard-kubernetes-lifecycle-target/1', 'environment_ref': env.ref().model_dump(mode='json'),
        'run_id': target['kubernetes']['run_id'], 'lab': target['lab'], 'kubernetes': {
        'context': target['kubernetes']['context'], 'engine': deepcopy(target['kubernetes']['engine'])}}


def _bodies(manifest, transport):
    bodies = manifest.get('requests')
    if type(bodies) is not dict or set(bodies) != {'A', 'B'}:
        raise ValueError('REQ20_EXPLICIT_HARMLESS_INPUTS_REQUIRED')
    for body in bodies.values():
        if (type(body) is not dict or set(body) != {'model', 'messages', 'max_tokens', 'temperature'}
                or body['model'] != transport.api_model or type(body['max_tokens']) is not int or body['max_tokens'] != 1
                or type(body['temperature']) not in (int, float) or body['temperature'] != 0
                or type(body['messages']) is not list or len(body['messages']) != 1
                or type(body['messages'][0]) is not dict or set(body['messages'][0]) != {'role', 'content'}
                or body['messages'][0]['role'] != 'user' or type(body['messages'][0]['content']) is not str
                or not body['messages'][0]['content'] or len(canonical_json(body)) > 65536):
            raise ValueError('REQ20_EXISTING_BOUNDED_REQUEST_SHAPE_REQUIRED')
    if bodies['A'] == bodies['B']:
        raise ValueError('REQ20_DISTINCT_INPUTS_REQUIRED')
    return bodies


def _capability(prepared):
    contract = NATIVE_CONTRACT
    if (type(contract) is not dict or set(contract) != {'profile', 'callback_sha256', 'callback_code_sha256', 'points'}
            or prepared.get('profile') != contract['profile'] or not _sha(contract['callback_sha256'])
            or not _sha(contract['callback_code_sha256']) or type(contract['points']) is not dict
            or not POINTS | {'InputProcessor.assign_request_id'} <= set(contract['points'])):
        return False
    for p in contract['points'].values():
        if (type(p) is not dict or set(p) != {'module', 'source_sha256', 'code_sha256'}
                or not _sha(p['source_sha256']) or not _sha(p['code_sha256'])
                or prepared.get('source_sha256', {}).get(p['module']) != p['source_sha256']):
            return False
    try:
        callback = importlib.import_module('guard.adapters.vllm_graph_buffers')
        path = Path(__file__).resolve().parents[1] / 'src/guard/adapters/vllm_graph_buffers.py'
        return (not path.is_symlink() and Path(callback.__file__).resolve() == path
            and Path(inspect.getsourcefile(callback.GraphBufferObserver)).resolve() == path
            and hashlib.sha256(path.read_bytes()).hexdigest() == contract['callback_sha256'])
    except (ImportError, OSError, TypeError, AttributeError):
        return False


def preflight(*, store, plan_id, pack, target, prepared, manifest, output, entered_by):
    from guard.executors.kubernetes_lifecycle_capture import validate_target
    from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
    source = Store.read_only(Path(store)); verify_complete(source)
    original = load_pack(Path(pack)); plan = source.latest(plan_id)
    if (plan.kind != 'PLAN' or plan.mode != Mode.audit or len(plan.inputs.environments) != 1
            or plan.inputs.pack != original.ref() or original.ref().model_dump(mode='json') != PACK_REF
            or get_pack(source, plan.inputs.pack).ref() != original.ref()):
        raise ValueError('REQ20_ORIGINAL_PACK31_SINGLE_AUDIT_PLAN_REQUIRED')
    check = original.checks['REQ-20']
    if (check.revision != 3 or check.executor is not None or check.judge is not None or check.policy_params
            or check.required_observations or len(check.required_observations_prose) != 1
            or len(check.challenge_steps) != 3 or not plan.item('REQ-20').selected):
        raise ValueError('REQ20_ORIGINAL_MANUAL_DEFINITION_REQUIRED')
    env, policy = source.resolve(plan.inputs.environments[0]), source.resolve(plan.inputs.policy)
    if (policy.pack != original.ref() or env.engine.name != 'vllm' or env.engine.version != '0.31.0'
            or env.runtime.gpu_count != 1 or env.runtime.tp_groups != 1 or not env.model.id
            or not env.model.revision or not _sha(env.model.inventory_sha256)):
        raise ValueError('REQ20_EXACT_GPU1_TP1_TARGET_REQUIRED')
    t, m, assets = _read(target), _read(manifest), load_prepared(prepared)
    validate_target(_capture_target(env, t), env)
    transport = KubernetesHTTP(t, env, duration=180, profile=REQUEST_BINDING_PROFILE)
    if (type(m) is not dict or set(m) != {'schema', 'environment_ref', 'target_sha256', 'session', 'run_id',
            'owner', 'recovery_owner', 'model_inventory_sha256', 'declared_graph_mode', 'requests'}
            or m['schema'] != 'byul-req20-cohort/1' or m['environment_ref'] != env.ref().model_dump(mode='json')
            or m['target_sha256'] != digest(t) or m['session'] != assets['session']
            or m['run_id'] != t['kubernetes']['run_id'] or m['model_inventory_sha256'] != env.model.inventory_sha256
            or m['declared_graph_mode'] not in MODES - {'NONE'}
            or any(type(m[k]) is not str or not m[k].strip() or len(m[k]) > 200 for k in ('owner', 'recovery_owner'))):
        raise ValueError('REQ20_EXPLICIT_OWNER_SESSION_MODE_INPUT_REQUIRED')
    _bodies(m, transport)
    out = Path(output).absolute()
    if (out.exists() or out.is_symlink() or out.resolve().is_relative_to(source.root.resolve())
            or source.root.resolve().is_relative_to(out.resolve()) or type(entered_by) is not str
            or not entered_by.strip() or any(p.is_symlink() for p in out.parents)
            or out.resolve().is_relative_to(Path(__file__).resolve().parents[2])):
        raise ValueError('REQ20_FRESH_OWNED_OUTPUT_REQUIRED')
    return source, original, plan, env, policy, t, assets, m


def _empty(env, target, prepared, manifest):
    now = datetime.now(timezone.utc).isoformat()
    return {'schema': SCHEMA, 'profile': PROFILE, 'environment_ref': env.ref().model_dump(mode='json'),
        'target_sha256': digest(target), 'manifest_sha256': digest(manifest), 'session_sha256': digest(prepared['session']),
        'data_origin': 'EXAMPLE' if env.data_origin.value == 'EXAMPLE' or target['lab'] is True else 'DECLARED',
        'collection_id': 'req20-' + uuid.uuid4().hex, 'started_at': now, 'finished_at': now,
        'request_summaries': [], 'native_rows': [], 'gaps': [], 'cleanup': {'sealed': False, 'complete': False}}


def _token_count(rows):
    counts = []
    for row in rows:
        if (row.get('outcome') != 200 or row.get('capture_complete') is not True
                or row.get('body_truncated') is not False or row.get('utf8_valid') is not True):
            return None
        try:
            body = strict_json(row['body'].encode(), limit=131072)
        except (KeyError, AttributeError, ValueError):
            return None
        tokens = body.get('tokens') if type(body) is dict else None
        if type(tokens) is not list or not tokens or any(type(x) is not int or x < 0 for x in tokens) or body.get('count') != len(tokens):
            return None
        counts.append(len(tokens))
    return counts if len(counts) == 2 else None


def _safe_snapshot(snapshot):
    binding, metadata = snapshot.get('binding', {}), snapshot.get('metadata', {})
    if type(binding) is not dict or type(metadata) is not dict or snapshot.get('point') not in POINTS or snapshot.get('phase') not in {'CALL', 'RETURN', 'RAISE'}:
        return None
    out = {'point': snapshot['point'], 'phase': snapshot['phase'], 'binding': {}, 'metadata': {}}
    for key in ('session_id_sha256', 'process_start_sha256', 'call_id_sha256', 'asgi_request_id_sha256', 'native_request_id_sha256'):
        out['binding'][key] = binding.get(key) if _sha(binding.get(key)) else None
    out['binding']['pid'] = binding.get('pid') if type(binding.get('pid')) is int and binding['pid'] > 0 else None
    m = out['metadata']
    for key in ('owner_token', 'input_batch_token', 'entry_token', 'graph_token', 'event_token', 'stream_token', 'retained_device_output_token', 'actual_graph_key_token'):
        m[key] = metadata.get(key) if _sha(metadata.get(key)) else None
    for key in ('runtime_mode', 'selected_runtime_mode'):
        m[key] = metadata.get(key) if metadata.get(key) in MODES else None
    m['forward_context_state'] = metadata.get('forward_context_state') if metadata.get('forward_context_state') in {'available', 'absent', 'unavailable'} else 'unavailable'
    for key in ('forward_context_source_verified', 'graph_key_identity_matched', 'graph_key_fields_matched'):
        m[key] = metadata.get(key) if type(metadata.get(key)) is bool else None
    transition = metadata.get('call_transition', {}); transition = transition if type(transition) is dict else {}
    m['call_transition'] = {k: transition.get(k) if type(transition.get(k)) is int and transition[k] >= 0 else None for k in ('call_seq', 'return_seq')}
    m['call_transition']['paired'] = transition.get('paired') is True
    m['call_transition']['same_owner'] = transition.get('same_owner') if type(transition.get('same_owner')) is bool else None
    for key, names in [('descriptor', ('num_tokens', 'num_reqs')), ('sizes', ('num_reqs', 'total_num_scheduled_tokens'))]:
        value = metadata.get(key, {}); value = value if type(value) is dict else {}
        m[key] = {k: value[k] for k in names if type(value.get(k)) is int and 0 <= value[k] < 2**31}
    consumer = metadata.get('consumer', {}); consumer = consumer if type(consumer) is dict else {}
    m['consumer'] = {k: consumer.get(k) if type(consumer.get(k)) is bool else None for k in (
        'copy_enqueue_boundary_returned', 'get_output_boundary_returned', 'pending_output_version_changed', 'retained_reference_released')}
    # 실제 native Event query만 사용한다. pending False는 미수집 None과 구별한다.
    query = metadata.get('event_query', {}); query = query if type(query) is dict else {}
    collected = (query.get('collected') is True and type(query.get('complete')) is bool
        and query.get('initialized') is True and query.get('capture_active') is False
        and query.get('device_type') == 'cuda' and type(query.get('device_index')) is int
        and query['device_index'] >= 0 and _sha(m.get('event_token')))
    m['event_query'] = {'collected': bool(collected), 'complete': query.get('complete') if collected else None,
        'initialized': query.get('initialized') if type(query.get('initialized')) is bool else None,
        'capture_active': query.get('capture_active') if type(query.get('capture_active')) is bool else None,
        'device_type': 'cuda' if query.get('device_type') == 'cuda' else None,
        'device_index': query.get('device_index') if collected else None}
    m['consumer']['event_query_collected'] = bool(collected and consumer.get('event_query_collected') is True)
    # source-qualified pair는 project 단계에서 별도로 검사한다.
    m['consumer']['event_completion_verified'] = (consumer.get('event_completion_verified')
        if m['consumer']['event_query_collected'] and consumer.get('event_completion_verified') == query.get('complete')
        and type(consumer.get('event_completion_verified')) is bool else None)
    m['consumer']['value_comparison_verified'] = None
    m['consumer']['paired_init_call_seq'] = (consumer.get('paired_init_call_seq')
        if type(consumer.get('paired_init_call_seq')) is int and consumer['paired_init_call_seq'] >= 0 else None)
    reasons = {'NATIVE_CUDA_EVENT_UNAVAILABLE', 'CUDA_EVENT_UNINITIALIZED', 'CUDA_EVENT_DEVICE_MISMATCH',
        'CUDA_CAPTURE_ACTIVE', 'CUDA_EVENT_QUERY_UNAVAILABLE', 'NATIVE_EVENT_QUERY'}
    m['event_query']['reason'] = query.get('reason') if query.get('reason') in reasons else 'NATIVE_CUDA_EVENT_UNAVAILABLE'
    buffers = metadata.get('buffers', {}); buffers = buffers if type(buffers) is dict else {}; m['buffers'] = {}
    for name in ('input_ids', 'positions', 'inputs_embeds', 'is_token_ids'):
        value = buffers.get(name, {}); value = value if type(value) is dict else {}; desc = value.get('descriptor', value); shape = desc.get('shape') if type(desc) is dict else None
        if type(shape) is list and len(shape) <= 8 and all(type(n) is int and 0 <= n < 2**31 for n in shape):
            m['buffers'][name] = {'shape': shape, 'values_collected': False,
                'buffer_token': value.get('buffer_token') if _sha(value.get('buffer_token')) else None,
                'mutation_version': value.get('mutation_version') if type(value.get('mutation_version')) is int else None}
    output = metadata.get('output', {}); output = output if type(output) is dict else {}
    tensors = output.get('tensor_items'); output_gaps = snapshot.get('gaps', [])
    m['output_metadata'] = {'complete': False, 'tensor_count': None, 'shapes': [], 'tensor_tokens': []}
    if type(tensors) is list:
        shapes = []; tokens = []
        valid = 0 < len(tensors) <= 8 and output.get('complete') is True and 'OUTPUT_BOUND' not in output_gaps
        for value in tensors[:8]:
            desc = value.get('descriptor', {}) if type(value) is dict else {}
            shape = desc.get('shape') if type(desc) is dict else None
            good = (type(value) is dict and _sha(value.get('tensor_token')) and desc.get('status') == 'collected'
                and type(shape) is list and len(shape) <= 8 and all(type(n) is int and 0 <= n < 2**31 for n in shape))
            valid = bool(valid and good)
            if good:
                shapes.append(shape); tokens.append(value['tensor_token'])
        m['output_metadata'] = {'complete': valid, 'tensor_count': min(len(tensors), 8), 'shapes': shapes, 'tensor_tokens': tokens}
    out['observer_seq'] = snapshot.get('observer_seq', snapshot.get('seq'))
    if type(out['observer_seq']) is not int or out['observer_seq'] < 0:
        out['observer_seq'] = None
    return out


def _native_rows(packet, prepared, request_ids):
    """frontend 선택과 실제 worker 관측을 source-qualified native ID로 연결한다."""
    contract = NATIVE_CONTRACT
    if type(contract) is not dict:
        return []
    qualified_packets, mapping = [], {}
    for entry in packet.get('recordings', []):
        cap = entry.get('capture') or {}; records = cap.get('records', [])
        if (entry.get('session_owner_match') is not True or entry.get('listed_file_identity_match') is not True
                or cap.get('image_digest_verified') is not True or cap.get('process_context_verified') is not True):
            continue
        ready = {r['payload']['module']: r['payload'] for r in records if r.get('event') == 'MODULE' and r['payload'].get('verified') is True}
        qualified = []
        for record in records:
            p = record.get('payload', {}); c = contract['points'].get(p.get('point'))
            module = ready.get(c['module'], {}) if c else {}
            if (c and all(module.get(k) == c[k] for k in ('source_sha256', 'code_sha256'))
                    and p.get('source_sha256') == c['source_sha256']
                    and prepared.get('source_sha256', {}).get(c['module']) == c['source_sha256']):
                qualified.append(record)
        context = records[0].get('payload', {}).get('context', {}) if records else {}
        graph_callback = cap.get('graph_callback_provenance') == {'module': 'guard.adapters.vllm_graph_buffers',
            'bytes_sha256': contract['callback_sha256'], 'code_sha256': contract['callback_code_sha256']}
        owner = cap.get('provenance', {}).get('owner')
        generation = digest(owner) if type(owner) is dict and owner else None
        qualified_packets.append((qualified, context, graph_callback, generation))
        for record in qualified:
            p = record['payload']; selected = p.get('metadata', {}).get('request_selection', {})
            if (p.get('point') == 'InputProcessor.assign_request_id' and p.get('phase') == 'RETURN'
                    and selected.get('request_id_observed') is True):
                a, n = selected.get('asgi_request_sha256'), selected.get('selected_request_id_sha256')
                if a in request_ids and _sha(n):
                    mapping.setdefault(a, set()).add(n)
    native = {next(iter(v)) for v in mapping.values() if len(v) == 1}
    rows = []
    for records, context, graph_callback, generation in qualified_packets:
        if not graph_callback or not _sha(generation):
            continue
        for record in records:
            p = record['payload']; snapshot = p.get('metadata', {}).get('graph_buffers')
            if record.get('event') != 'NATIVE' or type(snapshot) is not dict:
                continue
            safe = _safe_snapshot(snapshot)
            if (safe and snapshot.get('schema') == 'byul-v1-graph-buffer-snapshot/1'
                    and snapshot.get('status') == 'metadata_collected' and safe['phase'] == p.get('phase')
                    and safe['point'] == p.get('point') and safe['binding']['session_id_sha256'] == digest(prepared['session'])
                    and safe['binding']['native_request_id_sha256'] in native
                    and safe['binding']['pid'] == context.get('pid')
                    and safe['binding']['process_start_sha256'] == digest(context.get('start_ticks'))
                    and _sha(safe['binding']['call_id_sha256'])):
                rows.append({**snapshot, 'source_qualified': True, 'actual_worker_observed': True,
                    'native_record_seq': record['seq'], 'generation_sha256': generation})
    return rows


def _capture_kubernetes(env, target, prepared, manifest, *, duration, binary):
    record = _empty(env, target, prepared, manifest)
    if not _capability(prepared):
        record.update(gaps=['NATIVE_GRAPH_CAPABILITY_UNAVAILABLE'], cleanup={'attempted': False, 'sealed': False, 'complete': True})
        return record
    from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
    from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
    from guard.executors.lifecycle_session import capture_session
    transport = KubernetesHTTP(target, env, duration=duration, binary=binary, profile=REQUEST_BINDING_PROFILE)
    ct = _capture_target(env, target); reader = KubernetesLifecycleReader(ct, env, prepared, duration=duration, binary=binary)
    ids = set(); sealed = False
    def collect():
        e = reader.binding['engine']; packet = capture_session(reader, ct, container=e['container'], image=e['image'],
            prepared=prepared, owner_identity=reader.owner_identity, read_file=reader.read_file, image_check=reader.image_check)
        record['native_rows'].extend(_native_rows(packet, prepared, ids))
    def send(label, body, endpoint):
        rid = 'byul-req20-' + uuid.uuid4().hex; ids.add(hashlib.sha256(rid.encode()).hexdigest())
        rows = transport('kubernetes-probe', [{'id': label, 'mode': 'json', 'method': 'POST',
            'url': target['engine']['trusted_url'] + endpoint, 'headers': {'X-Request-Id': rid},
            'body': deepcopy(body), 'read_delay_ms': 0}], timeout=min(duration, 30))
        record['request_summaries'].extend({'id': r['id'], 'outcome': r.get('outcome') if type(r.get('outcome')) is int else None,
            'capture_complete': r.get('capture_complete') is True} for r in rows)
        return rows
    bodies = _bodies(manifest, transport)
    try:
        before = reader.snapshot(); token_rows = []
        for key in ('A', 'B'):
            token_rows.extend(send('tokenize-' + key, {'model': transport.api_model, 'messages': bodies[key]['messages']}, '/tokenize'))
        counts = _token_count(token_rows); record['input_token_counts'] = counts
        if counts is None or counts[0] != counts[1]:
            record['gaps'].append('TOKEN_LENGTH_UNVERIFIED' if counts is None else 'TOKEN_LENGTH_DIFFERENT')
            return record
        for label, key in [('before', 'A'), ('json-distinct_body-A', 'A'), ('json-distinct_body-B', 'B')]:
            send(label, bodies[key], '/v1/chat/completions'); collect()
        try:
            send('after', bodies['B'], '/v1/chat/completions')
        except Exception:
            record['gaps'].append('AFTER_CONTROL_UNAVAILABLE')
        if reader.snapshot() != before:
            record['gaps'].append('GENERATION_CHANGED')
    except Exception:
        record['gaps'].append('COLLECTION_INCOMPLETE')
    finally:
        try:
            sealed = reader.seal_session().get('sealed') is True; collect()
        except Exception:
            record['gaps'].append('CLEANUP_UNVERIFIED')
        record['cleanup'] = {'attempted': True, 'sealed': sealed,
            'owned_probe_cleanup_complete': not bool(transport.report().get('cleanup_errors')),
            'worker_observer_close_verified': None, 'complete': False}
        if not record['cleanup']['complete']:
            record['gaps'].append('CLEANUP_UNVERIFIED')
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
    return record


def project(raw, env, target, prepared, manifest):
    expected = _empty(env, target, prepared, manifest)
    keys = ('schema', 'profile', 'environment_ref', 'target_sha256', 'manifest_sha256', 'session_sha256', 'data_origin')
    if type(raw) is not dict or any(raw.get(k) != expected[k] for k in keys):
        raise ValueError('REQ20_CAPTURE_BINDING_MISMATCH')
    started, finished = (datetime.fromisoformat(raw[k]) for k in ('started_at', 'finished_at'))
    if (started.tzinfo is None or finished.tzinfo is None or finished < started
            or type(raw.get('collection_id')) is not str or not re.fullmatch('req20-[0-9a-f]{32}', raw['collection_id'])):
        raise ValueError('REQ20_CAPTURE_TIME_OR_ID_INVALID')
    rows = raw.get('native_rows', [])
    if type(rows) is not list or len(rows) > 4096:
        raise ValueError('REQ20_CAPTURE_ROWS_INVALID')
    safe = {k: raw[k] for k in (*keys, 'started_at', 'finished_at', 'collection_id')}
    safe.update(rows=[], candidate_count=0, confirmed_violations=[], full_scope_coverage=False,
        numerical_comparison_verified=False, gaps=sorted(set(raw.get('gaps', [])) & GAPS),
        cleanup={'complete': raw.get('cleanup', {}).get('complete') is True,
            'sealed': raw.get('cleanup', {}).get('sealed') is True,
            'owned_probe_cleanup_complete': raw.get('cleanup', {}).get('owned_probe_cleanup_complete') is True,
            'worker_observer_close_verified': None})
    counts = raw.get('input_token_counts')
    safe['input_token_counts'] = (counts if type(counts) is list and len(counts) == 2
        and all(type(n) is int and n > 0 for n in counts) else None)
    safe['request_summaries'] = []
    for item in raw.get('request_summaries', [])[:6]:
        if type(item) is dict and item.get('id') in {'tokenize-A', 'tokenize-B', 'before', 'after', 'json-distinct_body-A', 'json-distinct_body-B'}:
            safe['request_summaries'].append({'id': item['id'], 'outcome': item.get('outcome') if type(item.get('outcome')) is int else None,
                'capture_complete': item.get('capture_complete') is True})
    seen = set()
    for row in rows:
        if type(row) is not dict:
            continue
        snapshot = _safe_snapshot(row)
        if snapshot is None:
            continue
        b, m = snapshot['binding'], snapshot['metadata']; unique = (b['call_id_sha256'], snapshot['point'], snapshot['phase'])
        if unique in seen:
            continue
        seen.add(unique)
        provenance = (row.get('source_qualified') is True and row.get('actual_worker_observed') is True
            and all(_sha(b.get(k)) for k in ('call_id_sha256', 'native_request_id_sha256', 'session_id_sha256', 'process_start_sha256'))
            and b['session_id_sha256'] == digest(prepared['session']) and type(b['pid']) is int and _sha(row.get('generation_sha256')))
        safe['rows'].append({**snapshot, 'source_qualified': bool(provenance), 'graph_context_qualified': False,
            'qualified': False, 'version_change_candidate': False,
            'generation_sha256': row.get('generation_sha256') if _sha(row.get('generation_sha256')) else None,
            'native_record_seq': row.get('native_record_seq') if type(row.get('native_record_seq')) is int and row['native_record_seq'] >= 0 else None})
    def domain(row):
        return (row['generation_sha256'], row['binding']['session_id_sha256'], *tuple(row['binding'][k] for k in ('pid', 'process_start_sha256', 'native_request_id_sha256')))
    # buffer/validlen, 실제 graph key, consumer event를 실제 producer의 서로 다른 point에서 결합한다.
    graph_by_request = {}; safe['graph_contexts'] = []
    for row in safe['rows']:
        b, m = row['binding'], row['metadata']
        if (not row['source_qualified'] or row['point'] != 'CUDAGraphWrapper.__call__'
                or row['phase'] != 'RETURN' or row['native_record_seq'] is None
                or m['forward_context_state'] != 'available' or m['forward_context_source_verified'] is not True
                or m['graph_key_fields_matched'] is not True or not _sha(m['actual_graph_key_token'])
                or m['selected_runtime_mode'] != manifest['declared_graph_mode'] or m['runtime_mode'] != manifest['declared_graph_mode']
                or not _sha(m['graph_token']) or not m['descriptor']):
            continue
        inputs = [r for r in safe['rows'] if r['source_qualified'] and domain(r) == domain(row)
            and r['point'] in {'GPUModelRunner._prepare_input_ids', 'GPUModelRunner._prepare_inputs'}
            and r['phase'] == 'RETURN' and r['native_record_seq'] is not None and r['native_record_seq'] < row['native_record_seq']
            and r['metadata']['buffers'] and type(r['metadata']['sizes'].get('total_num_scheduled_tokens')) is int]
        if not inputs:
            continue
        recent = max(inputs, key=lambda r: r['native_record_seq']); valid_length = recent['metadata']['sizes']['total_num_scheduled_tokens']
        shapes = [v['shape'] for v in recent['metadata']['buffers'].values() if v['shape']]
        if (valid_length <= 0 or not shapes or not all(valid_length <= shape[0] for shape in shapes)
                or type(m['descriptor'].get('num_tokens')) is not int or valid_length > m['descriptor']['num_tokens']
                or not all(m['descriptor']['num_tokens'] <= shape[0] for shape in shapes)):
            continue
        row['graph_context_qualified'] = True
        graph_by_request.setdefault(domain(row), set()).add((m['actual_graph_key_token'], m['graph_token']))
        safe['graph_contexts'].append({'request_sha256': b['native_request_id_sha256'],
            'actual_graph_key_token': m['actual_graph_key_token'], 'graph_token': m['graph_token'],
            'input_shapes': shapes, 'valid_length': valid_length, 'declared_mode_matches_observed': True,
            'replay_verified': None})
    calls = {}
    for row in safe['rows']:
        b, m = row['binding'], row['metadata']
        row['qualified'] = bool(row['source_qualified'] and len(graph_by_request.get(domain(row), set())) == 1)
        row['version_change_candidate'] = bool(row['source_qualified'] and row['point'].startswith('AsyncGPUModelRunnerOutput.')
            and m['consumer']['pending_output_version_changed'] is True)
        safe['candidate_count'] += int(row['version_change_candidate'])
        call = (*domain(row), b['call_id_sha256'], row['point'])
        calls.setdefault(call, []).append(row)
    safe['sampled_output_copy_completion'] = []
    for group in calls.values():
        call = next((r for r in group if r['phase'] == 'CALL'), None)
        returned = next((r for r in group if r['phase'] == 'RETURN'), None)
        if (not call or not returned or not call['source_qualified'] or not returned['source_qualified']
                or returned['point'] != 'AsyncGPUModelRunnerOutput.get_output'
                or call['native_record_seq'] is None or returned['native_record_seq'] is None or returned['native_record_seq'] <= call['native_record_seq']):
            continue
        same_event = (_sha(call['metadata']['event_token']) and call['metadata']['event_token'] == returned['metadata']['event_token'])
        inits = [r for r in safe['rows'] if r['source_qualified'] and r['point'] == 'AsyncGPUModelRunnerOutput.__init__'
            and r['phase'] == 'RETURN' and domain(r) == domain(call) and r['metadata']['owner_token'] == call['metadata']['owner_token']
            and r['metadata']['event_token'] == call['metadata']['event_token']
            and r['metadata']['consumer']['copy_enqueue_boundary_returned'] is True and r['native_record_seq'] is not None and r['native_record_seq'] < call['native_record_seq']
            and r['metadata']['call_transition']['call_seq'] is not None
            and r['metadata']['call_transition']['call_seq'] == call['metadata']['consumer']['paired_init_call_seq']
            and returned['metadata']['consumer']['paired_init_call_seq'] == call['metadata']['consumer']['paired_init_call_seq']
            and r['observer_seq'] is not None and call['observer_seq'] is not None and returned['observer_seq'] is not None
            and r['observer_seq'] < call['observer_seq'] < returned['observer_seq']]
        intervening = [r for r in safe['rows'] if r['source_qualified'] and domain(r) == domain(call)
            and r['point'] == 'AsyncGPUModelRunnerOutput.__init__' and r['phase'] == 'RETURN'
            and r['metadata']['owner_token'] == call['metadata']['owner_token'] and r['native_record_seq'] is not None
            and call['native_record_seq'] < r['native_record_seq'] < returned['native_record_seq']]
        paired = (returned['metadata']['call_transition']['paired'] and returned['metadata']['call_transition']['same_owner'] is True
            and _sha(call['metadata']['owner_token']) and call['metadata']['owner_token'] == returned['metadata']['owner_token']
            and returned['metadata']['call_transition']['call_seq'] == call['observer_seq']
            and returned['metadata']['call_transition']['return_seq'] == returned['observer_seq'])
        completion = returned['metadata']['consumer']['event_completion_verified'] if same_event and len(inits) == 1 and not intervening and paired else None
        safe['sampled_output_copy_completion'].append({'point': returned['point'], 'same_event': bool(same_event), 'init_lineage_matched': len(inits) == 1 and not intervening and paired,
            'completion_collected': type(completion) is bool, 'event_complete': completion if type(completion) is bool else None})
    if any(not pair['init_lineage_matched'] for pair in safe['sampled_output_copy_completion']):
        safe['gaps'].append('CONSUMER_LINEAGE_UNVERIFIED')
    safe['gaps'] = sorted(set(safe['gaps']) | {'VALUE_COMPARISON_UNOBSERVED'})
    if not any(p['completion_collected'] for p in safe['sampled_output_copy_completion']):
        safe['gaps'].append('COPY_COMPLETION_UNOBSERVED')
    if not any(r['graph_context_qualified'] for r in safe['rows']):
        safe['gaps'].append('NO_NATIVE_GRAPH_EVENTS')
    safe['graph_output_consumption'] = {'collected': False, 'verified': None,
        'sampled_output_is_graph_output_verified': None}
    safe['gaps'] = sorted(set(safe['gaps']) | {'GRAPH_OUTPUT_CONSUMPTION_UNOBSERVED', 'LOGITS_SAMPLER_LINEAGE_UNOBSERVED'})
    return safe


def run(*, store, plan_id, pack, target, prepared, manifest, output, entered_by, execute=False,
        duration=180, binary='kubectl', _example_capture=None):
    source, original, plan, env, policy, t, assets, m = preflight(store=store, plan_id=plan_id, pack=pack,
        target=target, prepared=prepared, manifest=manifest, output=output, entered_by=entered_by)
    if _example_capture is not None and env.data_origin.value != 'EXAMPLE':
        raise ValueError('REQ20_EXAMPLE_CAPTURE_REQUIRES_EXAMPLE_ENV')
    if type(duration) not in (int, float) or not 0 < duration <= 300 or type(binary) is not str or not binary.strip():
        raise ValueError('REQ20_BOUNDED_EXECUTION_REQUIRED')
    supported = _capability(assets)
    binding = {k: v.ref().model_dump(mode='json') for k, v in [('environment', env), ('policy', policy), ('plan', plan), ('pack', original)]}
    if not execute:
        return {'executed': False, 'requests': 0, 'native_capability_supported': supported, **binding}
    before = _inventory(source.root); inputs = {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (target, prepared, manifest)}
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    export_bundle(source.root, output / 'source.zip'); restore_bundle(output / 'source.zip', output / 'store'); dest = Store(output / 'store')
    raw = (_example_capture or _capture_kubernetes)(env, t, assets, m, duration=duration, binary=binary)
    record = project(raw, env, t, assets, m); issues = []; attachment = 'graph-capture.json'
    try:
        _write(output / attachment, record)
    except OSError:
        attachment = None; issues.append('ARTIFACT_WRITE_UNAVAILABLE')
    doc = build_template(dest, original, plan, include_automated=True)
    doc['checks'] = [x for x in doc['checks'] if x['check_id'] == 'REQ-20' and x['side'] == 'single']
    if len(doc['checks']) != 1:
        raise ValueError('REQ20_ORIGINAL_SINGLE_MANUAL_FORM_REQUIRED')
    entry = doc['checks'][0]
    entry.update(entered_by=entered_by, entered_at=record['finished_at'], started_at=record['started_at'],
        finished_at=record['finished_at'], attachments=[attachment] if attachment else [],
        verdicts={'security': 'UNKNOWN', 'functional': 'UNKNOWN', 'performance': 'UNKNOWN'},
        basis='source/세대/request/graph 계보와 정제 callback 관측만 사용한다. version 변화 후보는 GPU overwrite 확정이 아니다.',
        limitations=['전체 정상 대조·challenge·기업 수치/성능 기준 미수집.',
            '실제 입력/출력 값 비교 producer가 없어 확정 위반/PASS를 제출하지 않는다.',
            'EXAMPLE capture는 실제 worker/GPU 실측으로 집계하지 않는다. 입력자는 신고자다.'])
    entry['normal_control']['result'] = 'not_attempted'
    for c in entry['challenges']:
        c['attempted'] = False
    entry['observation_notes'][0]['answer'] = (f"출처={record['data_origin']}; qualified행={sum(r['qualified'] for r in record['rows'])}; "
        f"version변화후보={record['candidate_count']}; gap={record['gaps']}. 값 정합·전체 정상 대조는 UNKNOWN이다.")
    form = output / 'manual-evidence.yaml'
    try:
        with form.open('x', encoding='utf-8') as stream:
            stream.write(dump_template(doc))
    except OSError:
        issues.append('MANUAL_FORM_WRITE_UNAVAILABLE')
    import_evidence(dest, original, form, document=doc); as_of = datetime.now(timezone.utc).replace(microsecond=0)
    results = judge_plan(dest, original, plan, as_of=as_of); report = build_report(dest, original, plan, as_of=as_of)
    dest.put(report); verify_complete(dest); refs = _refs(dest)
    export_bundle(dest.root, output / 'evidence.zip'); restore_bundle(output / 'evidence.zip', output / 'restored')
    restored = Store(output / 'restored'); replay = judge_plan(restored, original, plan, as_of=as_of)
    if _refs(restored) != refs or [r.ref() for r in replay] != [r.ref() for r in results]:
        raise ValueError('REQ20_EXACT_REF_RESTORE_MISMATCH')
    verify_complete(restored)
    if _inventory(source.root) != before or any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in inputs.items()):
        raise ValueError('REQ20_ORIGINAL_INPUT_CHANGED')
    summary = {'executed': True, **binding, 'collector_calls': 1, 'security': 'UNKNOWN',
        'candidate_count': record['candidate_count'], 'qualified_rows': sum(r['qualified'] for r in record['rows']),
        'capture_origin': record['data_origin'], 'real_gpu_execution_verified': False,
        'native_capability_supported': supported, 'full_scope_coverage': False, 'issues': issues,
        'source_unchanged': True, 'exact_refs_restored': True, 'report': report.ref().model_dump(mode='json')}
    _write(output / 'summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('store', 'pack', 'target', 'prepared', 'manifest', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--plan', dest='plan_id', required=True); parser.add_argument('--entered-by', required=True)
    parser.add_argument('--execute', action='store_true'); parser.add_argument('--duration', type=float, default=180)
    parser.add_argument('--binary', default='kubectl')
    try:
        print(json.dumps(run(**vars(parser.parse_args(argv))), ensure_ascii=False))
    except Exception:
        parser.exit(1, 'REQ20_GRAPH_EVIDENCE_UNAVAILABLE\n')


if __name__ == '__main__':
    main()
