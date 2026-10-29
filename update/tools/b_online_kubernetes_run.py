"""Owned Online Kubernetes orchestration; unavailable A capabilities fail closed.

The proposed A adapter ABI below is deliberately not a published guard ABI.
No CPU collection, response-only admission inference, or kubectl mutation occurs.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import importlib
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
from guard.executors import registry
from guard.executors.base import DraftArtifact, EvidenceDraft
from guard.executors.http_common import incomplete_artifact_capture
from guard.executors import kubernetes_lifecycle_capture as lifecycle_reader
from guard.executors import lifecycle_session
from guard.executors import lifecycle_capture
from guard.executors.online_input_cpu import judge_params
from guard.models import CheckDef, DataOrigin, Environment, ExecStatus, Policy, canonical_json, load_pack
from guard.runtime import kubernetes_http
from guard.runtime.bounded import strict_json
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import get_pack, put_pack

CHECKS = ('REQ-07', 'REQ-08', 'SUP-08')
EXECUTOR = 'online_input_kubernetes'
PROFILE = 'byul-online-input-kubernetes-orchestration/1'
# A must review/freeze the ABI and exact source before these are configured.
# Runtime discovery, supplied manifests and verified=true cannot approve source.
A_SOURCE_PINS = {
    'runtime/kubernetes_http.py': None,
    'executors/kubernetes_lifecycle_capture.py': None,
    'executors/lifecycle_capture.py': None,
    'executors/lifecycle_session.py': None,
    'adapters/vllm_online_input.py': 'bd15958029e91ba189c2fe588009a97ddd4d3d339e32824e5e5016a52fa736b1',
    'adapters/vllm_lifecycle_profiles.py': None,
    'adapters/vllm_lifecycle_sensor.py': None,
}
# A must supply reviewed point/module/source/code contracts and callback code
# provenance. Diagnostic records cannot authorize these literals.
ONLINE_POINT_CONTRACTS = None
ONLINE_CALLBACK_CODE_SHA256 = None
PROPOSED_A_ABI = {
    'http': 'ONLINE_INPUT_PROFILE; existing KubernetesHTTP.__call__ with online case IDs',
    'native': 'source-qualified NATIVE metadata.online_input snapshot namespace',
    'seal': 'KubernetesLifecycleReader.seal_session() -> {sealed: bool}; own selected session only',
}
_BODY_KEYS = {'model', 'messages', 'max_tokens', 'stream', 'kv_transfer_params', 'echo',
              'chat_template', 'chat_template_kwargs', 'mm_processor_kwargs', 'media_io_kwargs'}
_HASH_KEYS = {'asgi_request_id_sha256', 'native_request_id_sha256', 'call_id_sha256',
              'session_id_sha256', 'process_start_sha256', 'parsed_fields_sha256',
              'input_ids_sha256', 'admitted_ids_sha256', 'domain_sha256', 'config_sha256'}
_BOOL_KEYS = {'native_returned', 'request_ids_in_domain', 'request_ids_within_model_vocab',
              'default_deny_bypass', 'length_source_verified', 'trust_request_chat_template',
              'trust_request_mm_kwargs', 'source_field_supported', 'after_admitted'}


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _read(path, limit=262144):
    with Path(path).open('rb') as stream:
        return strict_json(stream.read(limit + 1), limit=limit)


def _write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def proposal_pack(folder):
    """External DRAFT only, never a permanent registry or shared-pack change."""
    folder = Path(folder)
    (folder / 'checks').mkdir(parents=True, exist_ok=False)
    files = []
    for cid in CHECKS:
        check = CheckDef(id=cid, revision=1, area=cid.split('-')[0], purpose='실제 Online 입력 소비 확인',
            question='귀속된 native 입력 소비가 선정 source/config 경계를 위반했는가?',
            review_order='owned_direct_pod_native', applies_when={'always': True},
            normal_control='각 요청 후 같은 serving generation의 정상 admission',
            challenge_steps=['승인된 manifest의 직접 Pod 요청과 native session join'],
            required_observations=[{'key': 'online_input_confirmed_violations', 'type': 'list[str]'},
                                   {'key': 'online_kubernetes_complete', 'type': 'bool'}],
            satisfied_if='선정 source/config에 귀속된 실제 native 수집',
            fail_criteria='독립 domain 또는 실제 trust/length 경계 위반의 native 소비',
            unknown_criteria='실제 source/필드/세대/요청/native 귀속 미확인',
            executor=EXECUTOR, judge={'plugin': 'rule_table', 'params': judge_params()},
            limitations=['DRAFT B runner. A Online HTTP/native join/seal 미지원 시 ABORTED.'])
        raw = yaml.safe_dump(check.model_dump(mode='json'), allow_unicode=True, sort_keys=False).encode()
        name = 'checks/' + cid + '.yaml'
        (folder / name).write_bytes(raw)
        files.append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest()})
    (folder / 'pack.yaml').write_text(yaml.safe_dump({'pack_id': 'PACK-B-ONLINE-KUBERNETES-PROPOSAL',
        'revision': 1, 'engines': ['vllm'], 'checks': list(CHECKS), 'files_sha256': files,
        'review': {'status': 'DRAFT'}}, sort_keys=False))
    return load_pack(folder)


def _safe_text_body(body):
    if len(canonical_json(body)) > 131072:
        raise ValueError('ONLINE_K8S_REQUEST_TOO_LARGE')
    for message in body['messages']:
        if (type(message) is not dict or set(message) != {'role', 'content'}
                or message['role'] not in {'system', 'user', 'assistant'}
                or type(message['content']) is not str
                or re.search(r'(?i)(?:[a-z][a-z0-9+.-]*://|data:|www\.)', message['content'])):
            raise ValueError('ONLINE_K8S_TEXT_ONLY_MESSAGES_REQUIRED')


def validate_inputs(env, policy, pack, target, prepared, manifest, *, duration, binary):
    if (not env.hash_ok() or not policy.hash_ok() or env.engine.name != 'vllm'
            or policy.pack != pack.ref() or set(pack.checks) != set(CHECKS)
            or set(policy.scope.include) != set(CHECKS) or policy.scope.exclude or policy.values or policy.exceptions):
        raise ValueError('ONLINE_K8S_ENV_POLICY_PACK_MISMATCH')
    for check in pack.checks.values():
        if (check.executor != EXECUTOR or check.judge.plugin != 'rule_table'
                or check.judge.params != judge_params() or check.policy_params
                or {o.key: o.type for o in check.required_observations} != {
                    'online_input_confirmed_violations': 'list[str]', 'online_kubernetes_complete': 'bool'}):
            raise ValueError('ONLINE_K8S_PACK_CONTRACT_MISMATCH')
    # Constructor does no cluster I/O. Existing public API performs exact target,
    # ENV, alias, container, digest, address and duration validation.
    transport = kubernetes_http.KubernetesHTTP(target, env, duration=duration, binary=binary,
        profile=kubernetes_http.REQUEST_BINDING_PROFILE)
    lifecycle_reader.validate_target(_capture_target(env, target), env)
    expected = {'schema', 'environment_ref', 'target_sha256', 'session', 'run_id', 'owner',
                'recovery_owner', 'isolated_run', 'domain_config_sha256', 'cases'}
    if (type(manifest) is not dict or set(manifest) != expected
            or manifest['schema'] != 'byul-online-kubernetes-run-input/1'
            or manifest['environment_ref'] != env.ref().model_dump(mode='json')
            or manifest['target_sha256'] != transport.target_sha256
            or manifest['session'] != prepared['session']
            or manifest['run_id'] != target['kubernetes']['run_id']
            or not _sha(manifest['domain_config_sha256']) or manifest['isolated_run'] is not True
            or any(type(manifest[k]) is not str or not re.fullmatch('[A-Za-z0-9._@-]{1,100}', manifest[k])
                   for k in ('owner', 'recovery_owner'))):
        raise ValueError('ONLINE_K8S_OWNERSHIP_MANIFEST_MISMATCH')
    cases = manifest['cases']
    if type(cases) is not list or not 1 <= len(cases) <= 16:
        raise ValueError('ONLINE_K8S_CASES_INVALID')
    seen, checks = set(), set()
    for case in cases:
        if (type(case) is not dict or set(case) != {'id', 'check_id', 'body', 'after_body'}
                or type(case['id']) is not str or not re.fullmatch('[a-z][a-z0-9-]{0,59}', case['id'])
                or case['id'] in seen or case['check_id'] not in CHECKS):
            raise ValueError('ONLINE_K8S_CASES_INVALID')
        if set(case['after_body']) - {'model', 'messages', 'max_tokens', 'stream'}:
            raise ValueError('ONLINE_K8S_AFTER_MUST_BE_NORMAL')
        for body in (case['body'], case['after_body']):
            if (type(body) is not dict or set(body) - _BODY_KEYS or body.get('model') != transport.api_model
                    or body.get('stream', False) is not False or type(body.get('max_tokens')) is not int or body.get('max_tokens') != 1
                    or type(body.get('messages')) is not list or not body['messages']):
                raise ValueError('ONLINE_K8S_REQUEST_INVALID')
            _safe_text_body(body)
        seen.add(case['id']); checks.add(case['check_id'])
    return transport


def _capture_target(env, target):
    return {'schema': 'guard-kubernetes-lifecycle-target/1',
        'run_id': target['kubernetes']['run_id'], 'environment_ref': env.ref().model_dump(mode='json'),
        'lab': target['lab'], 'kubernetes': {'context': target['kubernetes']['context'],
                                           'engine': deepcopy(target['kubernetes']['engine'])}}


def _approved_capabilities():
    """Source pins are set only after A approves the small HTTP/native/seal delta."""
    if (any(not _sha(value) for value in A_SOURCE_PINS.values())
            or not isinstance(ONLINE_POINT_CONTRACTS, dict) or not _sha(ONLINE_CALLBACK_CODE_SHA256)):
        raise RuntimeError('ONLINE_K8S_A_CAPABILITY_UNAVAILABLE')
    root = Path(__file__).resolve().parents[1] / 'src/guard'
    modules = (kubernetes_http, lifecycle_reader, lifecycle_capture, lifecycle_session,
        importlib.import_module('guard.adapters.vllm_online_input'),
        importlib.import_module('guard.adapters.vllm_lifecycle_profiles'),
        importlib.import_module('guard.adapters.vllm_lifecycle_sensor'))
    for module, (relative, expected) in zip(modules, A_SOURCE_PINS.items()):
        path = root / relative
        if (path.is_symlink() or not Path(module.__file__).resolve().samefile(path)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise RuntimeError('ONLINE_K8S_A_SOURCE_MISMATCH')
    for function, path in ((kubernetes_http.KubernetesHTTP.__call__, 'runtime/kubernetes_http.py'),
                           (lifecycle_session.capture_session, 'executors/lifecycle_session.py'),
                           (lifecycle_reader.KubernetesLifecycleReader.snapshot, 'executors/kubernetes_lifecycle_capture.py'),
                           (lifecycle_reader.KubernetesLifecycleReader.seal_session, 'executors/kubernetes_lifecycle_capture.py'),
                           (modules[4].snapshot_online_input, 'adapters/vllm_online_input.py')):
        if not Path(inspect.getsourcefile(function)).resolve().samefile(root / path):
            raise RuntimeError('ONLINE_K8S_A_FUNCTION_ORIGIN_MISMATCH')
    if not isinstance(kubernetes_http.ONLINE_INPUT_PROFILE, str):
        raise RuntimeError('ONLINE_K8S_A_PROFILE_UNAVAILABLE')


class _ProductionSession:
    """B adapter: existing public HTTP, reader and capture_session; proposed seal.

    Native metadata.online_input and seal_session are pending A review. Missing
    source approval prevents every network call, including reader.snapshot().
    """
    def __init__(self, env, target, prepared, manifest, *, duration, binary):
        _approved_capabilities()
        self.transport = kubernetes_http.KubernetesHTTP(target, env, duration=duration, binary=binary,
            profile=kubernetes_http.ONLINE_INPUT_PROFILE)
        self.capture_target = _capture_target(env, target)
        self.reader = lifecycle_reader.KubernetesLifecycleReader(self.capture_target, env, prepared,
                                                                duration=duration, binary=binary)
        self.target, self.prepared, self.manifest = target, prepared, manifest
        self.pending = []
        self.before = None
        self.packet = None
        self.sealed = False
        self.request_prefix = 'byul-online-' + uuid.uuid4().hex

    def preflight(self):
        self.before = self.reader.snapshot()
        return True  # snapshot verifies the selected generation or raises.

    def case(self, case, target):
        request_hashes = [hashlib.sha256((self.request_prefix + '-' + case['id'] + '-' + suffix).encode()).hexdigest()
                          for suffix in ('primary', 'after')]
        self.pending.append((deepcopy(case), request_hashes))
        for suffix, key in (('primary', 'body'), ('after', 'after_body')):
            rid = self.request_prefix + '-' + case['id'] + '-' + suffix
            request = {'id': 'online-' + case['id'] + '-' + suffix, 'mode': 'json', 'method': 'POST',
                'url': target['engine']['trusted_url'] + '/v1/chat/completions',
                'headers': {'X-Request-Id': rid}, 'body': deepcopy(case[key]), 'read_delay_ms': 0}
            # Raw responses stay transient and never establish native consumption.
            self.transport('kubernetes-probe', [request], timeout=30)
        return None  # All native files are read once after seal, not per check.

    def seal(self):
        error = None
        try:
            self.sealed = self.reader.seal_session().get('sealed') is True
        except Exception as exc:
            error = exc
        finally:
            engine = self.reader.binding['engine']
            self.packet = lifecycle_session.capture_session(self.reader, self.capture_target,
                container=engine['container'], image=engine['image'], prepared=self.prepared,
                owner_identity=self.reader.owner_identity, read_file=self.reader.read_file,
                image_check=self.reader.image_check)
        coverage = (self.packet.get('recorded_files_covered') is True
                    and self.packet.get('live_census_stable') is True
                    and all((r.get('capture') or {}).get('summary', {}).get('recording_closed') is True
                            for r in self.packet.get('recordings', [])))
        rows = [_join_native(self.packet, case, hashes, target=self.target,
                             prepared=self.prepared, manifest=self.manifest)
                for case, hashes in self.pending]
        try:
            after = self.reader.snapshot()
            generation = after == self.before
        except Exception:
            generation = False
        return {'sealed': self.sealed and error is None and coverage, 'generation_verified': generation,
                'rows': [row for row in rows if row is not None]}

    def close(self):
        # HTTP has a bounded own-process watchdog. No engine/probe Pod is deleted.
        # Retry only sealing the exact owned session when its first seal failed.
        if not self.sealed:
            self.sealed = self.reader.seal_session().get('sealed') is True
        report = self.transport.report()
        return {'owned_probe_reaped': not report.get('cleanup_errors', []),
                'owned_session_closed': self.sealed}


def _native_digest(value):
    # Callback canonical values use the sensor's ASCII JSON convention.
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _native_admission(snapshots):
    calls = [r for r in snapshots if r.get('point') == 'InputProcessor.process_inputs' and r.get('phase') == 'CALL']
    returns = [r for r in snapshots if r.get('point') == 'InputProcessor.process_inputs' and r.get('phase') == 'RETURN']
    if len(calls) != 1 or len(returns) != 1:
        return None
    # Reused ASGI IDs, multiple native requests or mismatched call generations
    # cannot be silently folded into one admission.
    first, last = calls[0], returns[0]
    binding = first.get('binding', {})
    later = last.get('binding', {})
    if (any(not _sha(binding.get(k)) or binding[k] != later.get(k) for k in
            ('call_id_sha256', 'native_request_id_sha256', 'asgi_request_id_sha256', 'process_start_sha256'))
            or last.get('status') != 'collected'):
        return None
    meta = last.get('metadata', {})
    if (first.get('metadata', {}).get('processor_input') != meta.get('processor_input')
            or first.get('metadata', {}).get('effective_config') != meta.get('effective_config')
            or meta.get('native_request_id_sha256') != binding['native_request_id_sha256']
            or meta.get('native_request_binding_matches') is not True):
        return None
    for key in ('processor_input', 'admitted'):
        item = meta.get(key, {})
        if (item.get('status') != 'collected' or not _sha(item.get('canonical_sha256'))
                or type(item.get('count')) is not int or item['count'] < 0):
            return None
    return last


def _join_native(packet, case, request_hashes, *, target, prepared, manifest):
    """Join only this run's parser-checked native files, never external facts JSON.

    metadata.online_input is a proposed A namespace, currently source-pin closed.
    Matching snapshots stay in one actual PID/start and one ASGI request; native
    CALL/RETURN must have the same native request and call identity.
    """
    if not isinstance(ONLINE_POINT_CONTRACTS, dict) or not _sha(ONLINE_CALLBACK_CODE_SHA256):
        return None
    domains = {}
    for row in packet.get('recordings', []):
        capture = row.get('capture') or {}
        if (not row.get('session_owner_match') or not row.get('listed_file_identity_match')
                or capture.get('process_context_verified') is not True
                or capture.get('image_digest_verified') is not True):
            continue
        callback = capture.get('online_callback_provenance', {})  # proposed A output, not operator input
        if (callback.get('module') != 'guard.adapters.vllm_online_input'
                or callback.get('bytes_sha256') != A_SOURCE_PINS['adapters/vllm_online_input.py']
                or callback.get('code_sha256') != ONLINE_CALLBACK_CODE_SHA256):
            continue
        records = capture.get('records', [])
        if not records:
            continue
        context = records[0]['payload']['context']
        modules = {r['payload'].get('module'): r['payload'] for r in records
                   if r.get('event') == 'MODULE' and r['payload'].get('verified') is True}
        for record in records:
            payload = record.get('payload', {})
            snapshot = payload.get('metadata', {}).get('online_input')
            if record.get('event') != 'NATIVE' or not isinstance(snapshot, dict):
                continue
            contract = ONLINE_POINT_CONTRACTS.get(payload.get('point'), {})
            module = modules.get(contract.get('module'), {})
            if (not contract or snapshot.get('point') != payload.get('point')
                    or not _sha(contract.get('source_sha256')) or not _sha(contract.get('code_sha256'))
                    or prepared.get('source_sha256', {}).get(contract['module']) != contract['source_sha256']
                    or payload.get('source_sha256') != contract['source_sha256']
                    or module.get('source_sha256') != contract['source_sha256']
                    or module.get('code_sha256') != contract['code_sha256']):
                continue
            binding = snapshot.get('binding', {})
            asgi = binding.get('asgi_request_id_sha256')
            if (snapshot.get('schema') != 'byul-online-input-snapshot/1'
                    or binding.get('pid') != context.get('pid')
                    or binding.get('session_id_sha256') != _native_digest(prepared['session'])
                    or binding.get('process_start_sha256') != _native_digest(context.get('start_ticks'))
                    or asgi not in request_hashes):
                continue
            domains.setdefault((context['pid'], context['start_ticks']), {h: [] for h in request_hashes})[asgi].append(snapshot)
    candidates = []
    for snapshots in domains.values():
        admitted = _native_admission(snapshots[request_hashes[0]])
        if admitted is not None:
            candidates.append((snapshots, admitted))
    if len(candidates) != 1:
        return None
    snapshots, admitted = candidates[0]
    metadata = admitted['metadata']
    primary = snapshots[request_hashes[0]]
    fields = _case_fields(case)
    rendered = _pair(primary, 'OnlineRenderer.render_chat')
    if rendered is None or not _fields_match(rendered[0]['metadata'].get('parsed_fields'), fields, explicit=True):
        return None
    domain = metadata.get('token_id_domain', {})
    config = metadata.get('effective_config', {})
    config_hash = digest({'effective_config': config, 'token_id_domain_sha256': domain.get('canonical_sha256')})
    if config_hash != manifest['domain_config_sha256']:
        return None
    native = {**admitted['binding'], 'parsed_fields_sha256': digest(fields), 'native_returned': True,
        'source_field_supported': True, 'input_ids_sha256': metadata['processor_input']['canonical_sha256'],
        'admitted_ids_sha256': metadata['admitted']['canonical_sha256'],
        'prompt_count': metadata['admitted']['count'], 'config_sha256': config_hash,
        'effective_max_model_len': config.get('max_model_len'),
        'after_admitted': _normal_after(snapshots[request_hashes[1]], config, domain, case['after_body'])}
    if (domain.get('status') == 'collected' and domain.get('request_ids_canonical_sha256') == native['input_ids_sha256']):
        native.update(domain_sha256=domain.get('canonical_sha256'),
            request_ids_in_domain=domain.get('request_ids_in_domain'),
            request_ids_within_model_vocab=domain.get('request_ids_within_model_vocab'))
    native['default_deny_bypass'] = _gate_bypass(primary, fields)
    native['runner_type'] = config.get('runner_type')
    native['prompt_type'] = 'decoder' if _decoder_length(primary, metadata) else 'unavailable'
    native['length_source_verified'] = native['prompt_type'] == 'decoder'
    return {**native, 'case_id': case['id'], 'check_id': case['check_id'], 'target_sha256': digest(target),
        'manifest_sha256': digest(manifest), 'session': prepared['session'],
        'source_verified': True, 'generation_verified': True, 'request_join_verified': True}


def _pair(snapshots, point):
    calls = [s for s in snapshots if s.get('point') == point and s.get('phase') == 'CALL']
    returns = [s for s in snapshots if s.get('point') == point and s.get('phase') == 'RETURN']
    if len(calls) != 1 or len(returns) != 1:
        return None
    if (not _sha(calls[0].get('binding', {}).get('call_id_sha256'))
            or calls[0]['binding']['call_id_sha256'] != returns[0].get('binding', {}).get('call_id_sha256')):
        return None
    return calls[0], returns[0]


def _fields_match(parsed, fields, *, explicit=False):
    return isinstance(parsed, dict) and all(isinstance(parsed.get(k), dict)
        and parsed[k].get('present') is True and parsed[k].get('status') == 'collected'
        and (not explicit or parsed[k].get('explicitly_set') is True)
        and parsed[k].get('canonical_sha256') == _native_digest(v) for k, v in fields.items())


def _gate_bypass(snapshots, fields):
    if not isinstance(ONLINE_POINT_CONTRACTS, dict):
        return False
    for point, trust, names, contract_name in (
        ('OnlineRenderer.validate_chat_template', 'trust_request_chat_template', ('chat_template', 'chat_template_kwargs'), 'template_nonnull'),
        ('protocol.validate_request_mm_kwargs', 'trust_request_mm_kwargs', ('mm_processor_kwargs', 'media_io_kwargs'), 'mm_nonempty')):
        contract = ONLINE_POINT_CONTRACTS.get(point, {})
        if contract.get('default_deny') != contract_name:
            continue
        selected = {k: fields[k] for k in names if k in fields}
        forbidden = ((selected.get('chat_template') is not None or
            isinstance(selected.get('chat_template_kwargs'), dict) and selected['chat_template_kwargs'].get('chat_template') is not None)
            if contract_name == 'template_nonnull' else any(bool(v) for v in selected.values()))
        pair = _pair(snapshots, point)
        if not forbidden or pair is None:
            continue
        if all(s.get('metadata', {}).get(trust) is False
               and _fields_match(s['metadata'].get('parsed_fields'), selected) for s in pair):
            return True
    return False


def _decoder_length(snapshots, metadata):
    count = metadata.get('admitted', {}).get('count')
    for snapshot in snapshots:
        point = snapshot.get('point')
        if snapshot.get('phase') != 'CALL' or point not in {'InputProcessor._validate_prompt_len', 'InputProcessor._validate_model_input'}:
            continue
        if ONLINE_POINT_CONTRACTS.get(point, {}).get('default_deny') != 'decoder_length':
            continue
        m = snapshot.get('metadata', {})
        same = (m.get('candidate_prompt_count') == count if point.endswith('_validate_prompt_len') else
                m.get('processor_input', {}).get('canonical_sha256') == metadata.get('processor_input', {}).get('canonical_sha256'))
        if m.get('prompt_type') == 'decoder' and m.get('effective_config') == metadata.get('effective_config') and same:
            return True
    return False


def _normal_after(snapshots, configuration, domain, body):
    admitted = _native_admission(snapshots)
    rendered = _pair(snapshots, 'OnlineRenderer.render_chat')
    if admitted is None or rendered is None or _case_fields({'body': body}):
        return False
    parsed = rendered[0].get('metadata', {}).get('parsed_fields', {})
    for field in ('chat_template', 'chat_template_kwargs', 'mm_processor_kwargs', 'media_io_kwargs', 'kv_transfer_params', 'echo'):
        item = parsed.get(field, {})
        neutral = (False,) if field == 'echo' else (None, {})
        if (item.get('status') != 'collected' or item.get('explicitly_set') is not False
                or item.get('canonical_sha256') not in {_native_digest(v) for v in neutral}):
            return False
    m = admitted['metadata']
    if m.get('effective_config') != configuration:
        return False
    for key, ids_key in (('token_id_domain', 'processor_input'), ('admitted_token_id_domain', 'admitted')):
        proof = m.get(key, {})
        if (proof.get('status') != 'collected' or proof.get('canonical_sha256') != domain.get('canonical_sha256')
                or proof.get('request_ids_in_domain') is not True or proof.get('request_ids_within_model_vocab') is not True
                or proof.get('request_ids_canonical_sha256') != m[ids_key]['canonical_sha256']
                or m[ids_key].get('integer_ids') is not True or m[ids_key].get('count', 0) <= 0):
            return False
    maximum = configuration.get('max_model_len')
    count = m['admitted']['count']
    return (type(maximum) is int and maximum > 0 and count <= maximum
            and not (count == maximum and configuration.get('runner_type') == 'generate')
            and m['processor_input']['canonical_sha256'] == m['admitted']['canonical_sha256'])


def _case_fields(case):
    return {k: v for k, v in case['body'].items() if k not in {'model', 'messages', 'stream', 'max_tokens'}}


def _qualify(raw, case, *, target_sha256, manifest_sha256, session):
    """A-checked join is required; no HTTP status, CPU source or JSON promotion."""
    if type(raw) is not dict:
        return None
    safe = {k: raw[k] for k in _HASH_KEYS if _sha(raw.get(k))}
    safe.update({k: raw[k] for k in _BOOL_KEYS if type(raw.get(k)) is bool})
    for key in ('prompt_count', 'effective_max_model_len'):
        if type(raw.get(key)) is int and raw[key] >= 0:
            safe[key] = raw[key]
    for key, values in (('runner_type', {'generate', 'pooling', 'draft'}), ('prompt_type', {'decoder', 'encoder'})):
        if raw.get(key) in values:
            safe[key] = raw[key]
    qualified = (raw.get('case_id') == case['id'] and raw.get('check_id') == case['check_id']
        and raw.get('target_sha256') == target_sha256 and raw.get('manifest_sha256') == manifest_sha256
        and raw.get('session') == session and raw.get('source_verified') is True
        and raw.get('generation_verified') is True and raw.get('request_join_verified') is True
        and safe.get('source_field_supported') is True
        and all(k in safe for k in ('asgi_request_id_sha256', 'native_request_id_sha256',
                                    'call_id_sha256', 'process_start_sha256', 'parsed_fields_sha256'))
        and safe['parsed_fields_sha256'] == digest(_case_fields(case)))
    violation = False
    if qualified and safe.get('native_returned') is True:
        same_admission = (_sha(safe.get('input_ids_sha256')) and
            safe.get('input_ids_sha256') == safe.get('admitted_ids_sha256'))
        length_violation = (same_admission and safe.get('length_source_verified') is True
            and safe.get('prompt_type') == 'decoder' and type(safe.get('prompt_count')) is int
            and type(safe.get('effective_max_model_len')) is int and safe['effective_max_model_len'] > 0
            and (safe['prompt_count'] > safe['effective_max_model_len'] or
                 safe['prompt_count'] == safe['effective_max_model_len'] and safe.get('runner_type') == 'generate'))
        if case['check_id'] == 'REQ-08':
            violation = bool(same_admission and _sha(safe.get('domain_sha256')) and _sha(safe.get('config_sha256'))
                and (safe.get('request_ids_in_domain') is False or safe.get('request_ids_within_model_vocab') is False)) or length_violation
        elif case['check_id'] == 'REQ-07':
            violation = safe.get('default_deny_bypass') is True or length_violation
        else:
            violation = safe.get('default_deny_bypass') is True
    return {'case_id': case['id'], 'check_id': case['check_id'], 'qualified': bool(qualified),
            'violation': bool(qualified and violation), 'metadata': safe}


def run_pipeline(env, policy, pack, target, prepared, manifest, output, *, execute=False,
                 duration=300, binary='kubectl', _example_session=None):
    if sys.flags.optimize:
        raise ValueError('ONLINE_K8S_REQUIRES_NORMAL_PYTHON')
    transport = validate_inputs(env, policy, pack, target, prepared, manifest, duration=duration, binary=binary)
    if _example_session is not None and env.data_origin != DataOrigin.EXAMPLE:
        raise ValueError('ONLINE_K8S_INJECTION_REQUIRES_EXAMPLE_ENV')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest_sha = digest(manifest)
    record = {'profile': PROFILE, 'collection_id': 'online-kubernetes-' + uuid.uuid4().hex,
        'target_sha256': transport.target_sha256, 'manifest_sha256': manifest_sha,
        'session_sha256': digest(prepared['session']), 'rows': [], 'errors': [], 'cleanup_errors': [],
        'started_at': None, 'finished_at': None, 'sealed': False, 'generation_verified': False}
    store = Store(output / 'store')
    put_pack(store, pack.path)
    store.put_many([env, policy])
    plan = select(pack, [env], policy)
    if {item.check_id for item in plan.items if item.selected} != set(CHECKS):
        raise ValueError('ONLINE_K8S_PLAN_SELECTION_MISMATCH')
    store.put(plan)
    session = None
    try:
        if execute:
            session = _example_session or _ProductionSession(env, target, prepared, manifest, duration=duration, binary=binary)
            if session.preflight() is not True:
                raise RuntimeError('PREFLIGHT')
            record['started_at'] = datetime.now(timezone.utc).isoformat()
            for case in manifest['cases']:
                try:
                    raw = session.case(deepcopy(case), deepcopy(target))
                    safe = _qualify(raw, case, target_sha256=transport.target_sha256,
                                    manifest_sha256=manifest_sha, session=prepared['session'])
                    if safe is not None:
                        record['rows'].append(safe)
                except Exception:
                    record['errors'].append('ONLINE_K8S_CASE_UNAVAILABLE')
            try:
                final = session.seal()
                for raw in final.get('rows', []):
                    case = next((c for c in manifest['cases'] if c['id'] == raw.get('case_id')), None)
                    if case is not None and not any(r['case_id'] == case['id'] for r in record['rows']):
                        safe = _qualify(raw, case, target_sha256=transport.target_sha256, manifest_sha256=manifest_sha, session=prepared['session'])
                        if safe is not None:
                            record['rows'].append(safe)
                record['sealed'] = final.get('sealed') is True
                record['generation_verified'] = final.get('generation_verified') is True
            except Exception:
                record['errors'].append('ONLINE_K8S_SEAL_UNAVAILABLE')
    except Exception:
        record['errors'].append('ONLINE_K8S_A_CAPABILITY_OR_PREFLIGHT_UNAVAILABLE')
    finally:
        if record['started_at']:
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
        if session is not None:
            try:
                cleanup = session.close()
                if cleanup.get('owned_probe_reaped') is not True or cleanup.get('owned_session_closed') is not True:
                    raise RuntimeError('CLEANUP')
            except Exception:
                record['cleanup_errors'].append('ONLINE_K8S_OWNED_CLEANUP_UNVERIFIED')
    if not execute:
        record['errors'].append('ONLINE_K8S_DRY_RUN_NO_COLLECTION')
    complete = (len(record['rows']) == len(manifest['cases']) and all(r['qualified'] and r['metadata'].get('after_admitted') is True
        for r in record['rows']) and record['sealed'] and record['generation_verified']
        and not record['errors'] and not record['cleanup_errors'])
    class BoundExecutor:
        name, version = EXECUTOR, '0.1.0'
        def run(self, check, environment, values, adapter, workdir):
            if environment.ref() != env.ref() or values:
                raise ValueError('ONLINE_K8S_BOUND_ENV_MISMATCH')
            rows = [r for r in record['rows'] if r['check_id'] == check.id and r['qualified']]
            check_complete = complete and bool(rows)
            draft = EvidenceDraft(status=ExecStatus.COMPLETED if check_complete else ExecStatus.ABORTED,
                data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
                normal_control={'attempted': record['started_at'] is not None, 'succeeded': check_complete if record['started_at'] else None},
                observations={'online_input_confirmed_violations': {'value': [r['case_id'] for r in rows if r['violation']] if rows else None,
                    'collected': bool(rows)}, 'online_kubernetes_complete': {'value': bool(check_complete), 'collected': True}},
                challenges=[{'step': step, 'attempted': record['started_at'] is not None} for step in check.challenge_steps],
                config={k: record[k] for k in ('profile', 'collection_id', 'target_sha256', 'manifest_sha256', 'session_sha256')},
                started_at=datetime.fromisoformat(record['started_at']) if record['started_at'] else None,
                finished_at=datetime.fromisoformat(record['finished_at']) if record['finished_at'] else None,
                abort_reason=None if check_complete else 'ONLINE_K8S_CAPTURE_INCOMPLETE', cleanup_errors=record['cleanup_errors'])
            try:
                path = Path(workdir) / 'online-native.json'
                _write(path, {**deepcopy(record), 'check_id': check.id})
                draft.artifacts = [DraftArtifact(name=path.name, path=path, collector=PROFILE)]
            except Exception:
                draft = incomplete_artifact_capture(draft)
            return draft
    executor = BoundExecutor()
    key = EXECUTOR + ':owned-native'
    previous = registry.EXECUTORS.get(key)
    registry.EXECUTORS[key] = lambda cfg: executor
    try:
        evidences = run_plan(store, pack, plan, RunConfig(executors={EXECUTOR: 'owned-native'}))
    finally:
        if previous is None:
            registry.EXECUTORS.pop(key, None)
        else:
            registry.EXECUTORS[key] = previous
    now = datetime.now(timezone.utc)
    results = judge_plan(store, pack, plan, as_of=now)
    report = build_report(store, pack, plan, as_of=now)
    store.put(report)
    summary = {k: record[k] for k in ('profile', 'collection_id', 'started_at', 'finished_at', 'errors', 'cleanup_errors')}
    summary.update({'execution_complete': complete, 'live_support_available': False,
        'origin': 'EXAMPLE' if env.data_origin == DataOrigin.EXAMPLE else 'DECLARED',
        'environment': env.ref().model_dump(mode='json'), 'policy': policy.ref().model_dump(mode='json'),
        'pack': pack.ref().model_dump(mode='json'), 'plan': plan.ref().model_dump(mode='json'),
        'evidence': [e.ref().model_dump(mode='json') for e in evidences],
        'results': [r.ref().model_dump(mode='json') for r in results], 'report': report.ref().model_dump(mode='json'),
        'store_verify_errors': store.verify(), 'exact_ref_rejudgment_unchanged': False, 'restore_verified': False})
    try:
        replay = judge_plan(store, get_pack(store, plan.inputs.pack), plan, as_of=now)
        if [r.ref() for r in replay] != [r.ref() for r in results]:
            raise ValueError('REJUDGE')
        summary['exact_ref_rejudgment_unchanged'] = True
        export_bundle(store.root, output / 'evidence.zip')
        restore_bundle(output / 'evidence.zip', output / 'restored')
        restored = Store(output / 'restored')
        restored_plan = restored.resolve(plan.ref())
        replay = judge_plan(restored, get_pack(restored, plan.inputs.pack), restored_plan, as_of=now)
        if restored.verify() or [r.ref() for r in replay] != [r.ref() for r in results]:
            raise ValueError('RESTORE')
        summary['restore_verified'] = True
    except Exception:
        summary['errors'] = [*summary['errors'], 'ONLINE_K8S_REPLAY_OR_BUNDLE_UNAVAILABLE']
    _write(output / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare-pack')
    prepare.add_argument('--output', required=True, type=Path)
    run = sub.add_parser('run')
    for key in ('env', 'policy', 'pack', 'target', 'prepared', 'case-manifest', 'output'):
        run.add_argument('--' + key, required=True, type=Path)
    run.add_argument('--execute', action='store_true')
    run.add_argument('--duration', type=float, default=300)
    run.add_argument('--kubectl', default='kubectl')
    args = parser.parse_args()
    if args.command == 'prepare-pack':
        print(json.dumps(proposal_pack(args.output).ref().model_dump(mode='json')))
        return 0
    summary = run_pipeline(Environment.model_validate(_read(args.env)), Policy.model_validate(_read(args.policy)),
        load_pack(args.pack), _read(args.target), load_prepared(args.prepared), _read(args.case_manifest), args.output,
        execute=args.execute, duration=args.duration, binary=args.kubectl)
    print(json.dumps(summary))
    return 0 if summary['execution_complete'] and summary['restore_verified'] and not summary['errors'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
