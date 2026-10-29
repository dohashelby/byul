"""REQ18 V1 slot-mask diagnostic: separate CPU and owned Kubernetes runs.

No PASS rule, shared registration, GPU readback, state injection or CPU→live
promotion. Unconfigured source/profile/provenance capabilities fail closed.
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
import uuid

import yaml

from guard.collectors.lifecycle import load_prepared
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry, kubernetes_lifecycle_capture as readers, lifecycle_session
from guard.executors.base import DraftArtifact, EvidenceDraft
from guard.executors.http_common import incomplete_artifact_capture
from guard.models import CheckDef, DataOrigin, Environment, ExecStatus, Policy, canonical_json, load_pack
from guard.runtime import kubernetes_http
from guard.runtime.bounded import strict_json
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import get_pack, put_pack
from tools import lif14_profiler_cpu as component_gate

PROFILE = 'byul-req18-v1-slot-diagnostic/1'
EXECUTOR = 'req18_slot_diagnostic'
SOURCE_COMMIT = 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
# Root configures reviewed literals after the new native module is frozen.
COMPONENT_COMMIT = 'ee1ff38421dc710685e3ebaa670b1e083991e9b5'
GATE_SHA256 = '5f9e45cb57979970e821857a2a26de06509622f5b681d8d6fb0938b254dbf7e2'
COLLECTOR_SHA256 = 'e0fd82cba3189964e00d1049a76882cd711eb08077c21e666528c7c045b1490f'
CALLBACK_SHA256 = '659d615f133cdc96ef54163678753a60e6b8ee51aa0e78e0560e0d308f7f50da'
CALLBACK_CODE_SHA256 = None
A_SOURCE_PINS = None
POINT_CONTRACTS = None
_CASES = ('standalone-b', 'condense-reuse', 'swap')
_ROLES = ('standalone-b', 'request-a', 'request-b', 'followup-b')


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def native_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _date(value):
    try:
        value = datetime.fromisoformat(value)
        return value.astimezone(timezone.utc) if value.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _read(path):
    with Path(path).open('rb') as stream:
        return strict_json(stream.read(262145), limit=262144)


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)


def _identity(module, function, path, expected):
    try:
        if (not _sha(expected) or path.is_symlink() or not Path(module.__file__).resolve().samefile(path)
                or not Path(inspect.getsourcefile(function)).resolve().samefile(path)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError
    except (OSError, TypeError, ValueError, AttributeError):
        raise ValueError('REQ18_SOURCE_IDENTITY_OR_BYTES_MISMATCH') from None


def verify_components(repo):
    _identity(component_gate, component_gate.verify_component, repo / 'update/tools/lif14_profiler_cpu.py', GATE_SHA256)
    if (not isinstance(COMPONENT_COMMIT, str) or re.fullmatch('[0-9a-f]{40}', COMPONENT_COMMIT) is None
            or component_gate.PRODUCT_COMPONENT_COMMIT != COMPONENT_COMMIT):
        raise ValueError('REQ18_COMPONENT_PIN_UNCONFIGURED')
    component_gate.verify_component(repo)
    collector = importlib.import_module('tools.b_req18_slot_cpu')
    callback = importlib.import_module('guard.adapters.vllm_slot_mask')
    _identity(collector, collector.capture_slot_cpu, repo / 'update/tools/b_req18_slot_cpu.py', COLLECTOR_SHA256)
    _identity(callback, callback.snapshot_slot_mask, repo / 'update/src/guard/adapters/vllm_slot_mask.py', CALLBACK_SHA256)
    return collector.capture_slot_cpu


def _diagnostic_check():
    repo = Path(__file__).resolve().parents[2]
    check = yaml.safe_load((repo / 'update/packs/PACK-BASELINE/checks/REQ-18.yaml').read_text())
    check['executor'] = EXECUTOR
    check['judge'] = {'plugin': 'rule_table', 'params': {'fail_when_any': [
        {'obs': 'req18_mask_inheritance_mismatch', 'op': 'eq', 'value': True}]}}
    check['required_observations'] = [
        {'key': 'req18_mask_inheritance_mismatch', 'type': 'bool'},
        {'key': 'req18_full_live_coverage', 'type': 'bool'}]
    check['limitations'] = ['B external DRAFT source-path diagnostic; no whole-live REQ18 PASS or GPU-value claim.']
    return CheckDef.model_validate(check)


def proposal_pack(folder):
    check = _diagnostic_check()
    folder = Path(folder)
    (folder / 'checks').mkdir(parents=True, exist_ok=False)
    body = yaml.safe_dump(check.model_dump(mode='json'), allow_unicode=True, sort_keys=False).encode()
    (folder / 'checks/REQ-18.yaml').write_bytes(body)
    (folder / 'pack.yaml').write_text(yaml.safe_dump({'pack_id': 'PACK-B-REQ18-SLOT-DIAGNOSTIC', 'revision': 1,
        'engines': ['vllm'], 'checks': ['REQ-18'], 'review': {'status': 'DRAFT'},
        'files_sha256': [{'path': 'checks/REQ-18.yaml', 'sha256': hashlib.sha256(body).hexdigest()}]}, sort_keys=False))
    return load_pack(folder)


def _cpu_result(record):
    """Strict source-invariant evidence; never infer a mismatch from output alone."""
    if (not _sha(COLLECTOR_SHA256) or not _sha(CALLBACK_SHA256) or not isinstance(record, dict)
            or record.get('schema') != 'byul-req18-slot-cpu-capture/1' or record.get('source_profile') != 'target031'
            or record.get('source_commit') != SOURCE_COMMIT
            or record.get('own_producer_sha256') != COLLECTOR_SHA256
            or record.get('native_callback_sha256') != CALLBACK_SHA256):
        return None
    started, finished = _date(record.get('capture_started_at')), _date(record.get('capture_finished_at'))
    if (type(record.get('collection_id')) is not str or re.fullmatch('req18-slot-cpu-[0-9a-f]{32}', record['collection_id']) is None
            or started is None or finished is None or finished < started):
        return None
    validation = record.get('source_validation', {})
    post = validation.get('post', {})
    if validation.get('pre') != 'verified':
        return None
    rows = record.get('results')
    if (type(rows) is not list or not 1 <= len(rows) <= 3 or any(not isinstance(r, dict) for r in rows)
            or len({r.get('case_id') for r in rows}) != len(rows)
            or not {r.get('case_id') for r in rows} <= set(_CASES)
            or 'standalone-b' not in {r.get('case_id') for r in rows}):
        return None
    by_case = {r['case_id']: r for r in rows}
    standalone = by_case['standalone-b']
    details, collected, mismatch = [], False, False
    for key in _CASES:
        if key not in by_case:
            continue
        row = by_case[key]
        safe = {k: row[k] for k in ('constraint_sha256', 'logits_sha256', 'host_mask_sha256', 'metadata_mask_sha256',
            'sampling_metadata_token', 'sampling_mask_token', 'sampled_ids_sha256', 'standalone_output_sha256', 'standalone_mask_sha256') if _sha(row.get(k))}
        safe.update({k: None for k in ('host_mask_sha256', 'metadata_mask_sha256', 'standalone_mask_sha256',
            'host_masked_count', 'metadata_masked_count', 'sampling_mask_token') if k in row and row[k] is None})
        safe.update({k: row[k] for k in ('host_mask_status', 'metadata_mask_status') if row.get(k) in {'absent', 'collected', 'unavailable'}})
        if row.get('mask_comparison_basis') == 'effective_unrestricted_branches':
            safe['mask_comparison_basis'] = row['mask_comparison_basis']
        safe.update({k: row[k] for k in ('current_b_unrestricted', 'slot_reused', 'mask_matches_standalone',
            'output_matches_standalone', 'sampler_returned', 'metadata_argument_matches_refresh',
            'restricted_request_preserved', 'metadata_mask_present', 'effective_unrestricted') if type(row.get(k)) is bool})
        safe.update({k: row[k] for k in ('host_masked_count', 'metadata_masked_count', 'sampled_count')
                     if type(row.get(k)) is int and row[k] >= 0})
        row_validation = row.get('source_validation', {})
        qualified = (row_validation.get('pre') == 'verified' and row_validation.get('post', {}).get('status') == 'verified'
            and standalone.get('collected') is True and standalone.get('sampler_returned') is True
            and standalone.get('source_validation', {}).get('post', {}).get('status') == 'verified'
            and row.get('collected') is True and row.get('current_b_unrestricted') is True
            and row.get('constraint_sha256') == native_digest(None) and row.get('sampler_returned') is True
            and row.get('metadata_argument_matches_refresh') is True and _sha(row.get('sampling_metadata_token'))
            and (_sha(row.get('sampling_mask_token')) if row.get('metadata_mask_present') is True else
                 row.get('sampling_mask_token') is None and row.get('metadata_mask_status') == 'absent')
            and _sha(row.get('logits_sha256')) and row.get('logits_sha256') == standalone.get('logits_sha256')
            and row.get('sampled_count') == 1 and _sha(row.get('sampled_ids_sha256'))
            and row.get('standalone_output_sha256') == standalone.get('sampled_ids_sha256')
            and row.get('standalone_mask_sha256') == standalone.get('host_mask_sha256'))
        safe['source_validation'] = {'pre': row_validation.get('pre') if row_validation.get('pre') == 'verified' else 'unavailable',
            'post': {'status': row_validation.get('post', {}).get('status') if row_validation.get('post', {}).get('status') in {'verified', 'mismatch', 'unavailable'} else 'unavailable'},
            'boundary': 'source_component'}
        if key != 'standalone-b':
            qualified = qualified and row.get('slot_reused') is True
        # A stale CPU host row is consequential only if the real Sampler received
        # that mismatched mask and its identical-logits output changed as well.
        failure = bool(qualified and key != 'standalone-b' and type(row.get('host_masked_count')) is int
            and row['host_masked_count'] > 0 and type(row.get('metadata_masked_count')) is int and row['metadata_masked_count'] > 0 and _sha(row.get('host_mask_sha256'))
            and row.get('metadata_mask_sha256') == row['host_mask_sha256']
            and row.get('host_mask_sha256') != standalone.get('host_mask_sha256')
            and row['sampled_ids_sha256'] != standalone.get('sampled_ids_sha256')
            and row.get('mask_matches_standalone') is False and row.get('output_matches_standalone') is False)
        mismatch |= failure
        if key != 'standalone-b':
            collected |= bool(qualified)
        details.append({'case_id': key, 'qualified': bool(qualified), 'mismatch': failure, **safe})
    return {'mode': 'cpu', 'rows': details, 'collected': collected, 'mismatch': mismatch,
            'complete': len(details) == 3 and record.get('capture_status') == 'completed' and post.get('status') == 'verified' and all(r['qualified'] for r in details),
            'collection_id': record.get('collection_id'), 'started_at': record.get('capture_started_at'),
            'finished_at': record.get('capture_finished_at'), 'installed_target': None,
            'gpu_execution_verified': False, 'full_live_coverage': False,
            'source_commit': SOURCE_COMMIT, 'collector_sha256': COLLECTOR_SHA256, 'callback_sha256': CALLBACK_SHA256,
            'source_validation': {'pre': 'verified', 'post': {'status': post.get('status') if post.get('status') in {'verified', 'unavailable', 'mismatch'} else 'unavailable'}, 'boundary': 'source_component'}}


def _capture_target(env, target):
    return {'schema': 'guard-kubernetes-lifecycle-target/1', 'run_id': target['kubernetes']['run_id'],
        'environment_ref': env.ref().model_dump(mode='json'), 'lab': target['lab'],
        'kubernetes': {'context': target['kubernetes']['context'], 'engine': deepcopy(target['kubernetes']['engine'])}}


def _validate_manifest(env, target, prepared, manifest, transport):
    keys = {'schema', 'environment_ref', 'target_sha256', 'session', 'run_id', 'owner', 'recovery_owner',
            'model_inventory_sha256', 'vocab_size', 'cohort'}
    if (type(manifest) is not dict or set(manifest) != keys or manifest['schema'] != 'byul-req18-cohort/1'
            or manifest['environment_ref'] != env.ref().model_dump(mode='json')
            or manifest['target_sha256'] != digest(target) or manifest['session'] != prepared['session']
            or manifest['run_id'] != target['kubernetes']['run_id']
            or not _sha(env.model.inventory_sha256) or manifest['model_inventory_sha256'] != env.model.inventory_sha256
            or type(manifest['vocab_size']) is not int or not 0 < manifest['vocab_size'] <= 262144
            or any(type(manifest[k]) is not str or not re.fullmatch('[A-Za-z0-9._@-]{1,100}', manifest[k])
                   for k in ('owner', 'recovery_owner'))):
        raise ValueError('REQ18_COHORT_BINDING_INVALID')
    cohort = manifest['cohort']
    if type(cohort) is not list or not 1 <= len(cohort) <= 4:
        raise ValueError('REQ18_COHORT_INVALID')
    seen = set()
    for group in cohort:
        if type(group) is not list or not 1 <= len(group) <= 2:
            raise ValueError('REQ18_COHORT_INVALID')
        for request in group:
            if type(request) is not dict or set(request) != {'role', 'body'} or request['role'] not in _ROLES or request['role'] in seen:
                raise ValueError('REQ18_COHORT_INVALID')
            body = request['body']
            if (type(body) is not dict or set(body) - {'model', 'prompt', 'max_tokens', 'temperature', 'allowed_token_ids'}
                    or body.get('model') != transport.api_model or type(body.get('prompt')) is not str
                    or len(canonical_json(body)) > 131072 or type(body.get('max_tokens')) is not int
                    or not 1 <= body['max_tokens'] <= 4 or type(body.get('temperature')) not in (int, float) or body.get('temperature') != 0):
                raise ValueError('REQ18_COHORT_REQUEST_INVALID')
            allowed = body.get('allowed_token_ids')
            if allowed is not None and (type(allowed) is not list or not allowed or len(allowed) > 4096
                    or any(type(v) is not int or not 0 <= v < manifest['vocab_size'] for v in allowed)):
                raise ValueError('REQ18_ALLOWED_IDS_INVALID')
            if request['role'] != 'request-a' and allowed is not None:
                raise ValueError('REQ18_DIAGNOSTIC_B_MUST_BE_UNRESTRICTED')
            seen.add(request['role'])
    if not {'standalone-b', 'request-b'} <= seen:
        raise ValueError('REQ18_B_CONTROL_REQUIRED')
    bodies = {r['role']: r['body'] for g in cohort for r in g}
    if bodies['standalone-b'] != bodies['request-b'] or ('followup-b' in bodies and bodies['followup-b'] != bodies['request-b']):
        raise ValueError('REQ18_B_CONTROL_INPUT_MISMATCH')


def _native_events(packet, prepared):
    """Proposed A namespace; module/source/code and callback provenance required."""
    if not isinstance(POINT_CONTRACTS, dict) or not _sha(CALLBACK_CODE_SHA256):
        return []
    events = []
    for entry in packet.get('recordings', []):
        captured = entry.get('capture') or {}
        if (entry.get('session_owner_match') is not True or entry.get('listed_file_identity_match') is not True
                or captured.get('image_digest_verified') is not True or captured.get('process_context_verified') is not True):
            continue
        provenance = captured.get('slot_callback_provenance', {})
        if provenance != {'module': 'guard.adapters.vllm_slot_mask', 'bytes_sha256': CALLBACK_SHA256,
                          'code_sha256': CALLBACK_CODE_SHA256}:
            continue
        records = captured.get('records', [])
        if not records:
            continue
        context = records[0]['payload']['context']
        ready = {r['payload']['module']: r['payload'] for r in records if r.get('event') == 'MODULE'
                 and r['payload'].get('verified') is True}
        for record in records:
            payload = record.get('payload', {})
            snapshot = payload.get('metadata', {}).get('slot_mask')
            contract = POINT_CONTRACTS.get(payload.get('point'))
            if record.get('event') != 'NATIVE' or not isinstance(snapshot, dict) or not isinstance(contract, dict):
                continue
            module = ready.get(contract.get('module'), {})
            if (module.get('source_sha256') != contract.get('source_sha256') or not _sha(contract.get('source_sha256'))
                    or module.get('code_sha256') != contract.get('code_sha256') or not _sha(contract.get('code_sha256'))
                    or payload.get('source_sha256') != contract['source_sha256']
                    or prepared.get('source_sha256', {}).get(contract['module']) != contract['source_sha256']):
                continue
            copy = contract.get('copy_helper')
            if copy is not None and (not isinstance(copy, dict) or not _sha(copy.get('source_sha256'))
                    or not _sha(copy.get('code_sha256')) or ready.get(copy.get('module'), {}).get('source_sha256') != copy['source_sha256']
                    or ready.get(copy.get('module'), {}).get('code_sha256') != copy['code_sha256']):
                continue
            binding = snapshot.get('binding', {})
            if (snapshot.get('schema') != 'byul-v1-slot-mask-snapshot/1' or snapshot.get('point') != payload.get('point')
                    or binding.get('pid') != context['pid']
                    or binding.get('session_id_sha256') != native_digest(prepared['session'])
                    or binding.get('process_start_sha256') != native_digest(context['start_ticks'])
                    or not _sha(binding.get('call_id_sha256'))):
                continue
            caller = None
            if snapshot.get('point') == 'Sampler.forward' and snapshot.get('phase') == 'RETURN':
                point = 'GPUModelRunner.sample_tokens'
                expected = POINT_CONTRACTS.get(point, {})
                module = ready.get(expected.get('module'), {})
                hook = captured.get('slot_writer_coverage', {}).get('points', {}).get(point, {})
                chain = payload.get('call_chain', [])
                if (not _sha(expected.get('source_sha256')) or not _sha(expected.get('code_sha256'))
                        or module.get('source_sha256') != expected['source_sha256']
                        or module.get('code_sha256') != expected['code_sha256']
                        or prepared.get('source_sha256', {}).get(expected.get('module')) != expected['source_sha256']
                        or hook.get('hook_installed') is not True or hook.get('source_sha256') != expected['source_sha256']
                        or hook.get('code_sha256') != expected['code_sha256'] or not isinstance(chain, list)
                        or not any(isinstance(frame, dict) and frame.get('point') == point for frame in chain)):
                    continue
                caller = {k: expected[k] for k in ('module', 'source_sha256', 'code_sha256')}
                caller['point'] = point
            events.append({'seq': record['seq'], 'domain': (context['pid'], context['start_ticks']),
                           'snapshot': snapshot, 'caller_provenance': caller})
    return sorted(events, key=lambda e: (e['domain'], e['seq']))


def _bootstrap(events, manifest):
    candidates = []
    for event in events:
        s = event['snapshot']; m = s.get('metadata', {}); d = m.get('dimensions', {})
        if (s.get('point') == 'InputBatch.__init__' and s.get('phase') == 'RETURN'
                and _sha(m.get('batch_token')) and d.get('vocab_size') == manifest['vocab_size']
                and d.get('is_pooling_model') is False and d.get('device_type') in {'cpu', 'cuda'}):
            candidates.append((event['domain'], m['batch_token'], deepcopy(d)))
    return candidates[0] if len(candidates) == 1 else None


def _request_map(packet, prepared):
    """Reuse source-qualified assign_request_id observations, not header guesses."""
    mapping, ambiguous = {}, set()
    contract = (POINT_CONTRACTS or {}).get('InputProcessor.assign_request_id', {})
    if not contract or not _sha(contract.get('source_sha256')) or not _sha(contract.get('code_sha256')):
        return mapping
    for entry in packet.get('recordings', []):
        capture = entry.get('capture') or {}
        if (entry.get('session_owner_match') is not True or entry.get('listed_file_identity_match') is not True
                or capture.get('image_digest_verified') is not True or capture.get('process_context_verified') is not True):
            continue
        ready = {r['payload']['module']: r['payload'] for r in capture.get('records', [])
                 if r.get('event') == 'MODULE' and r['payload'].get('verified') is True}
        module = ready.get(contract.get('module'), {})
        if (module.get('source_sha256') != contract.get('source_sha256')
                or module.get('code_sha256') != contract.get('code_sha256')
                or prepared.get('source_sha256', {}).get(contract.get('module')) != contract.get('source_sha256')):
            continue
        for event in capture.get('records', []):
            payload = event.get('payload', {})
            if (payload.get('point') != 'InputProcessor.assign_request_id'
                    or payload.get('source_sha256') != contract.get('source_sha256')):
                continue
            metadata = payload.get('metadata', {}).get('request_selection', {})
            asgi, native = metadata.get('asgi_request_sha256'), metadata.get('selected_request_id_sha256')
            if not _sha(asgi) or not _sha(native) or metadata.get('request_id_observed') is not True:
                continue
            if asgi in mapping and mapping[asgi] != native:
                ambiguous.add(asgi)
            mapping[asgi] = native
    return {k: v for k, v in mapping.items() if k not in ambiguous}


def _interval_verified(packet, process, prepared=None):
    # New A hook-registration evidence is an explicit pending capability.
    # No manifest flag or B snapshot can assert that absent writers were hooked.
    if packet.get('recorded_files_covered') is not True or packet.get('live_census_stable') is not True:
        return False
    if prepared is None:
        return False
    qualified = {(e['domain'], e['seq']) for e in _native_events(packet, prepared)}
    found = False
    writers = {'InputBatch.add_request', 'InputBatch.remove_request', 'InputBatch.swap_states',
               'InputBatch.condense', 'InputBatch.refresh_metadata', 'GPUModelRunner.sample_tokens', 'Sampler.forward'}
    for entry in packet.get('recordings', []):
        capture = entry.get('capture') or {}
        records = capture.get('records', [])
        if not records:
            continue
        context = records[0]['payload']['context']
        if (context['pid'], context['start_ticks']) != process:
            continue
        found = True
        for record in records:
            payload = record.get('payload', {})
            if record.get('event') == 'NATIVE' and payload.get('point', '').startswith('InputBatch.') and payload.get('point') in writers:
                snapshot = payload.get('metadata', {}).get('slot_mask', {})
                if (((context['pid'], context['start_ticks']), record.get('seq')) not in qualified
                        or snapshot.get('phase') == 'RAISE' or snapshot.get('metadata', {}).get('slot_map_complete') is not True):
                    return False
        if (capture.get('summary', {}).get('recording_closed') is not True
                or capture.get('summary', {}).get('recording_end') != 'SEALED'
                or capture.get('summary', {}).get('incomplete_tail') is True):
            return False
        hooks = capture.get('slot_writer_coverage', {})
        if hooks.get('schema') != 'byul-v1-slot-hook-coverage/1':
            return False
        points = hooks.get('points', {})
        for point in writers:
            actual, expected = points.get(point, {}), (POINT_CONTRACTS or {}).get(point, {})
            if (actual.get('hook_installed') is not True or not _sha(expected.get('source_sha256')) or not _sha(expected.get('code_sha256'))
                    or actual.get('source_sha256') != expected.get('source_sha256')
                    or actual.get('code_sha256') != expected.get('code_sha256')):
                return False
    return found


def _sampler_flow(events, index, batch):
    current = events[index]['snapshot']['metadata']
    token, mask_token = current.get('sampling_metadata_token'), current.get('sampling_mask_token')
    if not _sha(token):
        return None
    for i in range(index + 1, len(events)):
        call = events[i]['snapshot']; m = call.get('metadata', {})
        if m.get('batch_token') in (None, batch) and call.get('point', '').startswith('InputBatch.'):
            return None
        if call.get('point') != 'Sampler.forward' or call.get('phase') != 'CALL':
            continue
        if (m.get('sampling_metadata_token') != token or m.get('sampling_mask_token') != mask_token
                or m.get('sampling_mask') != current.get('sampling_mask')):
            continue
        identity = call.get('binding', {}).get('call_id_sha256')
        for end in events[i + 1:]:
            s = end['snapshot']; em = s.get('metadata', {})
            if em.get('batch_token') in (None, batch) and s.get('point', '').startswith('InputBatch.'):
                return None
            if s.get('point') != 'Sampler.forward' or s.get('binding', {}).get('call_id_sha256') != identity:
                continue
            if (s.get('phase') != 'RETURN' or em.get('sampling_metadata_token') != token
                    or em.get('sampling_mask_token') != mask_token or em.get('sampling_mask') != current.get('sampling_mask')):
                return None
            caller = end.get('caller_provenance', {}) or {}
            expected = (POINT_CONTRACTS or {}).get('GPUModelRunner.sample_tokens', {})
            if (caller.get('point') != 'GPUModelRunner.sample_tokens' or not _sha(expected.get('source_sha256'))
                    or not _sha(expected.get('code_sha256')) or any(caller.get(k) != expected.get(k)
                        for k in ('module', 'source_sha256', 'code_sha256'))):
                return None
            if (POINT_CONTRACTS or {}).get('Sampler.forward', {}).get('branch') != 'apply_nonnull_allowed_mask':
                return None
            return m
    return None


def _mask_row_consumed(metadata, row, dimensions, *, refresh_rebuilt=False):
    mask = metadata.get('sampling_mask', {})
    cpu = row.get('cpu_mask', {})
    if (mask.get('dtype') not in {'bool', 'torch.bool'} or mask.get('device_type') not in {'cpu', 'cuda'}
            or cpu.get('status') != 'collected' or cpu.get('cells') != dimensions['vocab_size']
            or not _sha(cpu.get('mask_sha256')) or type(cpu.get('masked_count')) is not int
            or not 0 <= cpu['masked_count'] <= cpu['cells']):
        return False
    if mask['device_type'] == 'cuda':
        contract = (POINT_CONTRACTS or {}).get('InputBatch.refresh_metadata', {})
        return (refresh_rebuilt and metadata.get('has_allowed_token_ids') is True
                and contract.get('branch') == 'copy_active_host_mask_rows' and isinstance(contract.get('copy_helper'), dict))
    matches = [r for r in mask.get('cpu_rows', []) if r.get('slot_index') == row.get('slot_index')]
    return (mask.get('values_collected') is True and len(matches) == 1
            and matches[0].get('cpu_mask') == cpu)


def _native_result(events, bootstrap, request_b_sha, *, standalone_b_sha=None, prior_request_sha=None, prior_constraint_sha=None, interval_complete=False):
    """Conditional stale-host-mask source argument diagnostic; GPU values unknown."""
    empty = {'mode': 'kubernetes', 'rows': [], 'collected': False, 'mismatch': False, 'complete': False,
             'gpu_execution_verified': False, 'full_live_coverage': False}
    if not interval_complete or not _sha(standalone_b_sha) or not _sha(prior_request_sha) or not _sha(prior_constraint_sha):
        return empty
    process, batch, dimensions = bootstrap
    selected = [e for e in events if e['domain'] == process]
    previous, transitions, constraints = {}, {}, {}
    findings, normal = [], False
    for index, event in enumerate(selected):
        s = event['snapshot']; m = s.get('metadata', {}); rows = m.get('rows', [])
        if m.get('batch_token') == batch and s.get('point') == 'InputBatch.add_request' and s.get('phase') == 'RETURN':
            constraint = m.get('argument_constraint', {})
            assigned, request = m.get('assigned_slot_index'), m.get('argument_request_sha256')
            if (constraint.get('status') == 'collected' and _sha(request) and type(assigned) is int
                    and any(r.get('slot_index') == assigned and r.get('request_sha256') == request for r in rows)):
                constraints[request] = constraint
        if m.get('batch_token') == batch and s.get('phase') == 'RETURN' and m.get('slot_map_complete') is True:
            current = {r['slot_index']: r for r in rows if type(r.get('slot_index')) is int and _sha(r.get('request_sha256'))}
            for slot, row in current.items():
                if slot in previous and previous[slot]['request_sha256'] != row['request_sha256']:
                    transitions[(slot, row['request_sha256'])] = previous[slot]
            previous.update(current)  # Retain the last occupant across an empty slot.
        if (s.get('point') != 'InputBatch.refresh_metadata' or s.get('phase') != 'RETURN'
                or m.get('batch_token') != batch or m.get('dimensions') != dimensions or m.get('slot_map_complete') is not True):
            continue
        calls = [e['snapshot'] for e in selected[:index] if e['snapshot'].get('point') == 'InputBatch.refresh_metadata'
                 and e['snapshot'].get('phase') == 'CALL' and e['snapshot'].get('binding', {}).get('call_id_sha256') == s.get('binding', {}).get('call_id_sha256')]
        old = calls[-1].get('metadata', {}) if len(calls) == 1 else {}
        rebuilt = (old.get('batch_token') == batch and _sha(old.get('sampling_metadata_token'))
                   and old['sampling_metadata_token'] != m.get('sampling_metadata_token')
                   and old.get('has_allowed_token_ids') == m.get('has_allowed_token_ids')
                   and old.get('rows') == m.get('rows'))
        consumed = _sampler_flow(selected, index, batch)
        if consumed is None:
            continue
        mask = m.get('sampling_mask', {})
        absent = mask.get('status') == 'absent' and m.get('sampling_mask_token') is None
        allocated = (_sha(m.get('sampling_mask_token')) and mask.get('shape') == [len(rows), dimensions['vocab_size']])
        for row in rows:
            constraint = constraints.get(row.get('request_sha256'), {})
            if (row.get('restricted') is not False or constraint.get('restricted') is not False
                    or constraint.get('sha256') != native_digest(None) or constraint.get('count') != 0):
                continue
            if row.get('request_sha256') == standalone_b_sha:
                normal |= bool(absent or allocated and _mask_row_consumed(m, row, dimensions, refresh_rebuilt=rebuilt) and row['cpu_mask']['masked_count'] == 0)
            prior = transitions.get((row.get('slot_index'), request_b_sha), {})
            if (not allocated or row.get('request_sha256') != request_b_sha or prior.get('request_sha256') != prior_request_sha
                    or constraints.get(prior_request_sha, {}).get('restricted') is not True
                    or constraints.get(prior_request_sha, {}).get('sha256') != prior_constraint_sha
                    or not _mask_row_consumed(m, row, dimensions, refresh_rebuilt=rebuilt)):
                continue
            cpu = row['cpu_mask']
            inherited = cpu['masked_count'] > 0 and cpu['mask_sha256'] == prior.get('cpu_mask', {}).get('mask_sha256')
            findings.append({'slot_index': row['slot_index'], 'request_sha256': request_b_sha,
                'prior_request_sha256': prior_request_sha, 'host_mask_sha256': cpu['mask_sha256'],
                'host_masked_count': cpu['masked_count'], 'sampling_metadata_token': m['sampling_metadata_token'],
                'sampling_mask_token': m['sampling_mask_token'], 'mask_device_type': mask.get('device_type'),
                'source_mask_argument_matched': True, 'mismatch': inherited})
    return {**empty, 'rows': findings, 'collected': bool(findings) and normal,
            'mismatch': normal and any(f['mismatch'] for f in findings), 'complete': bool(findings) and normal,
            'standalone_unrestricted_source_path_observed': normal}


def _capture_kubernetes(env, target, prepared, manifest, *, duration, binary):
    if not isinstance(A_SOURCE_PINS, dict) or not A_SOURCE_PINS or not isinstance(POINT_CONTRACTS, dict) or not _sha(CALLBACK_CODE_SHA256):
        raise ValueError('REQ18_KUBERNETES_CAPABILITY_UNAVAILABLE')
    root = Path(__file__).resolve().parents[1] / 'src/guard'
    for module_name, expected in A_SOURCE_PINS.items():
        module = importlib.import_module(module_name)
        file = root / ('/'.join(module_name.split('.')[1:]) + '.py')
        _identity(module, module.__dict__.get('__loader__').get_code if False else module, file, expected)
    # Existing public APIs; new profile and seal are proposed A capabilities.
    transport = kubernetes_http.KubernetesHTTP(target, env, duration=duration, binary=binary,
                                               profile=kubernetes_http.SLOT_MASK_PROFILE)
    capture_target = _capture_target(env, target)
    reader = readers.KubernetesLifecycleReader(capture_target, env, prepared, duration=duration, binary=binary)
    def collect():
        engine = reader.binding['engine']
        return lifecycle_session.capture_session(reader, capture_target, container=engine['container'],
            image=engine['image'], prepared=prepared, owner_identity=reader.owner_identity,
            read_file=reader.read_file, image_check=reader.image_check)
    began = datetime.now(timezone.utc).isoformat()
    sealed, cleanup, result, gaps = False, [], None, []
    try:
        before = reader.snapshot()
        bootstrap = _bootstrap(_native_events(collect(), prepared), manifest)
        if bootstrap is None:
            raise ValueError('REQ18_LIVE_DOMAIN_UNAVAILABLE')
        ids, token = {}, 'byul-slot-' + uuid.uuid4().hex
        for group in manifest['cohort']:
            requests = []
            for item in group:
                rid = token + '-' + item['role']
                ids[item['role']] = hashlib.sha256(rid.encode()).hexdigest()
                requests.append({'id': 'req18-slot-' + item['role'], 'mode': 'json', 'method': 'POST',
                    'url': target['engine']['trusted_url'] + '/v1/completions', 'headers': {'X-Request-Id': rid},
                    'body': deepcopy(item['body']), 'read_delay_ms': 0})
            try:
                transport('kubernetes-probe', requests, timeout=30)
            except Exception:
                gaps.append('REQ18_COHORT_REQUEST_INCOMPLETE')
        try:
            sealed = reader.seal_session().get('sealed') is True
        except Exception:
            gaps.append('REQ18_SEAL_UNAVAILABLE')
        finally:
            packet = collect()  # Preserve already checked events if seal fails.
            # Proposed A request index must join sent ASGI hashes to actual native
            # request IDs. Never assume a serving prefix or use HTTP200 as proof.
            mapping = packet.get('slot_request_index', {})
            b = mapping.get(ids.get('request-b'))
            if _sha(b):
                result = _native_result(_native_events(packet, prepared), bootstrap, b,
                    standalone_b_sha=mapping.get(ids.get('standalone-b')), prior_request_sha=mapping.get(ids.get('request-a')),
                    prior_constraint_sha=native_digest(next((r['body'].get('allowed_token_ids') for g in manifest['cohort'] for r in g if r['role'] == 'request-a'), None)),
                    interval_complete=_interval_verified(packet, bootstrap[0], prepared))
        if reader.snapshot() != before or not sealed:
            if result is not None:
                result['complete'] = False
    except Exception:
        gaps.append('REQ18_NATIVE_INTERVAL_INCOMPLETE')
        if result is not None:
            result['complete'] = False
    finally:
        try:
            if not sealed:
                sealed = reader.seal_session().get('sealed') is True
            if not sealed or transport.report().get('cleanup_errors'):
                cleanup.append('REQ18_OWNED_CLEANUP_UNVERIFIED')
        except Exception:
            cleanup.append('REQ18_OWNED_CLEANUP_UNVERIFIED')
    if result is not None:
        result.update(collection_id=token, started_at=began, finished_at=datetime.now(timezone.utc).isoformat(),
                      cleanup_errors=cleanup, gaps=gaps, target_sha256=digest(target), manifest_sha256=digest(manifest))
        result['complete'] &= not cleanup and not gaps
    return result


def run_pipeline(mode, env, policy, pack, output, *, source_root=None, target=None, prepared=None,
                 manifest=None, execute=False, duration=180, binary='kubectl', _example_capture=None):
    if sys.flags.optimize or mode not in {'cpu', 'kubernetes'}:
        raise ValueError('REQ18_MODE_OR_PYTHON_INVALID')
    if (not env.hash_ok() or not policy.hash_ok() or policy.pack != pack.ref() or env.engine.name != 'vllm'
            or pack.manifest.review.status.value != 'DRAFT' or set(pack.checks) != {'REQ-18'}
            or pack.checks['REQ-18'].model_dump(mode='json') != _diagnostic_check().model_dump(mode='json')
            or policy.values not in ({}, {'REQ-18': {}})):
        raise ValueError('REQ18_INPUT_BINDING_INVALID')
    if _example_capture is not None and env.data_origin != DataOrigin.EXAMPLE:
        raise ValueError('REQ18_INJECTION_REQUIRES_EXAMPLE_ENV')
    if mode == 'cpu':
        if (env.engine.version != 'source-' + SOURCE_COMMIT or env.engine.image_digest is not None
                or any(v is not None for v in (env.model.id, env.model.revision, env.model.inventory_sha256)) or source_root is None or any(v is not None for v in (target, prepared, manifest))):
            raise ValueError('REQ18_CPU_ENV_MUST_BE_SEPARATE')
    else:
        if env.engine.version != '0.31.0' or source_root is not None or target is None or prepared is None:
            raise ValueError('REQ18_KUBERNETES_ENV_MUST_BE_SEPARATE')
        transport = kubernetes_http.KubernetesHTTP(target, env, duration=duration, binary=binary,
                                                   profile=kubernetes_http.REQUEST_BINDING_PROFILE)
        readers.validate_target(_capture_target(env, target), env)
        _validate_manifest(env, target, prepared, manifest, transport)
    plan = select(pack, [env], policy)
    if len([item for item in plan.items if item.selected]) != 1:
        raise ValueError('REQ18_NOT_SELECTED')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    store = Store(output / 'store')
    put_pack(store, pack.path)
    store.put_many([env, policy, plan])
    state = {'record': None, 'capture_calls': 0, 'diagnostics': []}
    repo = Path(__file__).resolve().parents[2]
    class BoundExecutor:
        name, version = EXECUTOR, '0.1.0'
        def run(self, check, environment, values, adapter, workdir):
            if environment.ref() != env.ref() or values:
                raise ValueError('REQ18_BOUND_ENV_INVALID')
            if execute:
                try:
                    if _example_capture is not None:
                        capture = _example_capture
                    else:
                        cpu_capture = verify_components(repo)
                        capture = cpu_capture if mode == 'cpu' else _capture_kubernetes
                    state['capture_calls'] += 1
                    if mode == 'cpu':
                        raw = capture(Path(source_root), Path(workdir) / 'cpu', source_profile='target031')
                        state['record'] = _cpu_result(raw)
                    else:
                        state['record'] = capture(env, deepcopy(target), deepcopy(prepared), deepcopy(manifest), duration=duration, binary=binary)
                        if not isinstance(state['record'], dict) or state['record'].get('mode') != 'kubernetes':
                            state['record'] = None
                    if state['record'] is None:
                        state['diagnostics'].append('REQ18_OBSERVATION_UNAVAILABLE')
                except Exception:
                    state['diagnostics'].append('REQ18_CAPTURE_OR_CAPABILITY_UNAVAILABLE')
                if _example_capture is None:
                    try:
                        verify_components(repo)
                    except Exception:
                        state['diagnostics'].append('REQ18_COMPONENT_POSTCHECK_UNAVAILABLE')
                        if state['record'] is not None:
                            state['record']['complete'] = False
            else:
                state['diagnostics'].append('REQ18_DRY_RUN')
            record = state['record']
            collected = record is not None and record.get('collected') is True
            complete = collected and record.get('complete') is True
            draft = EvidenceDraft(status=ExecStatus.COMPLETED if complete else ExecStatus.ABORTED,
                data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
                normal_control={'attempted': record is not None, 'succeeded': complete if record is not None else None},
                observations={'req18_mask_inheritance_mismatch': {'value': bool(record.get('mismatch')) if collected else None, 'collected': collected},
                              'req18_full_live_coverage': {'value': False, 'collected': True}},
                challenges=[{'step': step, 'attempted': collected} for step in check.challenge_steps],
                config={'profile': PROFILE, 'mode': mode, 'source_commit': SOURCE_COMMIT,
                    'collection_id': record.get('collection_id') if record else None, 'installed_target': None if mode == 'cpu' else digest(target),
                    'gpu_execution_verified': False, 'full_live_coverage': False},
                started_at=_date(record.get('started_at')) if record else None,
                finished_at=_date(record.get('finished_at')) if record else None,
                cleanup_errors=record.get('cleanup_errors', []) if record else [],
                abort_reason=None if complete else 'REQ18_DIAGNOSTIC_INCOMPLETE')
            if record is not None:
                try:
                    path = Path(workdir) / 'slot-diagnostic.json'
                    _write(path, {**record, 'producer_diagnostics': state['diagnostics']})
                    draft.artifacts = [DraftArtifact(name=path.name, path=path, collector=PROFILE)]
                except Exception:
                    draft = incomplete_artifact_capture(draft)
            return draft
    executor = BoundExecutor()
    key = EXECUTOR + ':diagnostic'
    previous = registry.EXECUTORS.get(key)
    registry.EXECUTORS[key] = lambda cfg: executor
    try:
        evidence, = run_plan(store, pack, plan, RunConfig(executors={EXECUTOR: 'diagnostic'}))
    finally:
        if previous is None:
            registry.EXECUTORS.pop(key, None)
        else:
            registry.EXECUTORS[key] = previous
    now = datetime.now(timezone.utc)
    result, = judge_plan(store, pack, plan, as_of=now)
    report = build_report(store, pack, plan, as_of=now)
    store.put(report)
    summary = {'profile': PROFILE, 'mode': mode, 'capture_calls': state['capture_calls'], 'diagnostics': state['diagnostics'],
        'full_live_coverage': False, 'gpu_execution_verified': False, 'installed_target': None if mode == 'cpu' else digest(target),
        'execution_status': evidence.execution.status.value, 'data_origin': evidence.data_origin.value,
        'security': result.verdicts.security.value, 'pack': pack.ref().model_dump(mode='json'),
        'environment': env.ref().model_dump(mode='json'), 'policy': policy.ref().model_dump(mode='json'),
        'plan': plan.ref().model_dump(mode='json'), 'evidence': evidence.ref().model_dump(mode='json'),
        'result': result.ref().model_dump(mode='json'), 'report': report.ref().model_dump(mode='json'),
        'store_verify_errors': store.verify(), 'exact_ref_rejudgment_unchanged': False, 'restore_verified': False}
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
        summary['diagnostics'] = [*summary['diagnostics'], 'REQ18_REPLAY_OR_BUNDLE_UNAVAILABLE']
    _write(output / 'summary.json', summary)
    return summary


def run_cpu_pipeline(source_root, output, *, execute=True):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    pack = proposal_pack(output / 'proposal-pack')
    env = Environment(id='ENV-B-REQ18-SOURCE-CPU', data_origin=DataOrigin.DECLARED,
        engine={'name': 'vllm', 'version': 'source-' + SOURCE_COMMIT}).seal()
    policy = Policy(id='POL-B-REQ18-SOURCE-CPU', data_origin=DataOrigin.DECLARED,
        status='DRAFT', pack=pack.ref(), scope={'include': ['REQ-18']}).seal()
    return run_pipeline('cpu', env, policy, pack, output / 'run', source_root=source_root, execute=execute)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare-pack'); prepare.add_argument('--output', type=Path, required=True)
    cpu = sub.add_parser('cpu')
    cpu.add_argument('--source-root', type=Path, required=True); cpu.add_argument('--output', type=Path, required=True)
    cpu.add_argument('--execute', action='store_true')
    live = sub.add_parser('kubernetes')
    for key in ('env', 'policy', 'pack', 'target', 'prepared', 'manifest', 'output'):
        live.add_argument('--' + key, type=Path, required=True)
    live.add_argument('--execute', action='store_true'); live.add_argument('--duration', type=float, default=180)
    live.add_argument('--kubectl', default='kubectl')
    args = parser.parse_args()
    if args.command == 'prepare-pack':
        print(json.dumps(proposal_pack(args.output).ref().model_dump(mode='json'))); return 0
    if args.command == 'cpu':
        summary = run_cpu_pipeline(args.source_root, args.output, execute=args.execute)
    else:
        summary = run_pipeline('kubernetes', Environment.model_validate(_read(args.env)), Policy.model_validate(_read(args.policy)),
            load_pack(args.pack), args.output, target=_read(args.target), prepared=load_prepared(args.prepared),
            manifest=_read(args.manifest), execute=args.execute, duration=args.duration, binary=args.kubectl)
    print(json.dumps(summary))
    return 0 if summary['execution_status'] == 'COMPLETED' and summary['restore_verified'] else 2


if __name__ == '__main__':
    raise SystemExit(main())


