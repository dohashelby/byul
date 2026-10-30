"""원 LIF13: Pod 내부 native producer 명령 준비와 선언 증거 소비."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from guard.collectors.manual import build_template, dump_template, import_evidence
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.models import Mode, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_binding import validate_capture_target
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle, verify_complete
from guard.store.packs import get_pack

SOURCE_COMMIT = 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
PACK_REF = {'kind': 'PACK', 'id': 'PACK-BASELINE', 'revision': 31,
    'sha256': '54f4f2357533a8b201ad4397d4b182b08b89ee4821ffe67763e3fbecd8f369ac'}
CONFIG_SCHEMA = 'byul-lif13-native-config/1'
CAPTURE_SCHEMA = 'byul-lif13-native-capture/1'
CACHE_FIELDS = {'checkpoint', 'model_arch', 'tp_size', 'tp_rank', 'dtype', 'quantization', 'quant_config_hash',
    'revision', 'vllm_version', 'is_draft', 'dp_size', 'dp_rank'}
STATUS = {'ok', 'error', 'mismatch', 'unavailable', 'not_attempted', 'released', 'timeout', 'failed'}
# 원 client 파일의 고정 Git blob과의 일치만 검토한다. daemon 실행 소스 인증이 아니다.
CLIENT_SOURCE_FILES = {
    'vllm/model_executor/model_loader/weight_cache/daemon.py': 'f4fc42549b0cce117cc006506d80b0d493f095f58825f89494a8d6ce43849e63',
    'vllm/model_executor/model_loader/weight_cache/protocol.py': 'cee8d02d5d5bdfbcd4a8e873cc9095008f3cd0f4280e7eb4f74369b6bec654db',
    'vllm/model_executor/model_loader/weight_cache/ipc_loader.py': '45c3a633a1556deab41404b5f5a4fb049f5f9a4dd1a21fb3a61bea16eea9f2da'}


def digest(value): return sha256_hex(canonical_json(value))
def _sha(value): return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _inventory(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(root).rglob('*')) if p.is_file()}


def _refs(store):
    return sorted((o.kind.value, o.id, o.revision, o.content_sha256)
        for kind in ('ENV', 'POL', 'CHG', 'PLAN', 'EVD', 'RES', 'RPT') for o in store.iter_kind(kind))


def _read(path):
    path = Path(path)
    if path.is_symlink(): raise ValueError('LIF13_INPUT_SYMLINK')
    with path.open('rb') as stream: return strict_json(stream.read(2097153), limit=2097152)


def _write(path, value):
    with Path(path).open('xb') as stream: stream.write(canonical_json(value))


class EvidencePreservationError(RuntimeError):
    def __init__(self, facts, stages, issues, output):
        super().__init__('LIF13_EVIDENCE_PRESERVATION_FAILED')
        self.safe_facts = deepcopy(facts)
        self.recovery = {'status': 'failed', 'success': False, 'output': str(output), 'stages': deepcopy(stages),
            'issues': sorted(set(issues + ['EVIDENCE_PRESERVATION_FAILED'])), 'capture_rerun_required': False,
            'owned_partial_files_retained': True}


def _config(value, env, policy, plan, target):
    keys = {'schema', 'data_origin', 'environment_ref', 'policy_ref', 'plan_ref', 'target_sha256', 'source_commit',
        'mode', 'owner', 'recovery_owner', 'allow_global_release', 'allow_empty_after', 'daemon', 'cache_key',
        'entry_name', 'device_index', 'read_elements', 'max_response_bytes', 'duration_seconds', 'rpc_timeout_seconds'}
    if (type(value) is not dict or set(value) != keys or value['schema'] != CONFIG_SCHEMA
            or value['data_origin'] not in {'DECLARED', 'EXAMPLE'} or value['mode'] != 'zero_copy'
            or value['source_commit'] != SOURCE_COMMIT
            or value['environment_ref'] != env.ref().model_dump(mode='json')
            or value['policy_ref'] != policy.ref().model_dump(mode='json')
            or value['plan_ref'] != plan.ref().model_dump(mode='json') or value['target_sha256'] != digest(target)
            or value['allow_global_release'] is not True or value['allow_empty_after'] is not True
            or env.data_origin.value == 'EXAMPLE' and value['data_origin'] != 'EXAMPLE'):
        raise ValueError('LIF13_EXACT_CONFIG_REFS_AND_SELECTED_ZERO_COPY_REQUIRED')
    for key in ('owner', 'recovery_owner', 'entry_name'):
        if type(value[key]) is not str or not value[key].strip() or len(value[key]) > (1024 if key == 'entry_name' else 200) or any(c in value[key] for c in '\0\r\n'):
            raise ValueError('LIF13_EXPLICIT_OWNER_RECOVERY_AND_ENTRY_REQUIRED')
    daemon, cache = value['daemon'], value['cache_key']
    if (type(daemon) is not dict or set(daemon) != {'socket_path', 'pid', 'uid', 'start_ticks', 'boot_id_sha256',
            'pid_namespace_inode', 'mount_namespace_inode', 'gpu_uuid'}
            or type(daemon['socket_path']) is not str or not daemon['socket_path'].startswith('/')
            or len(daemon['socket_path'].encode()) > 107 or '\x00' in daemon['socket_path']
            or not _sha(daemon['boot_id_sha256']) or type(daemon['gpu_uuid']) is not str or not daemon['gpu_uuid']
            or any(type(daemon[k]) is not int or daemon[k] < (0 if k == 'uid' else 1)
                for k in ('pid', 'uid', 'start_ticks', 'pid_namespace_inode', 'mount_namespace_inode'))):
        raise ValueError('LIF13_EXPLICIT_DAEMON_GENERATION_REQUIRED')
    if (type(cache) is not dict or set(cache) != CACHE_FIELDS or cache['tp_size'] != 1 or cache['tp_rank'] != 0
            or cache['dp_size'] != 1 or cache['dp_rank'] != 0 or type(cache['is_draft']) is not bool
            or any(type(cache[k]) is not int for k in ('tp_size', 'tp_rank', 'dp_size', 'dp_rank'))
            or cache['vllm_version'] != env.engine.version or cache['revision'] != env.model.revision):
        raise ValueError('LIF13_ORIGINAL_CACHE_KEY_REQUIRED')
    for key in CACHE_FIELDS - {'tp_size', 'tp_rank', 'dp_size', 'dp_rank', 'is_draft', 'quantization', 'revision'}:
        if type(cache[key]) is not str or len(cache[key]) > 1024 or any(c in cache[key] for c in '\0\r\n'):
            raise ValueError('LIF13_ORIGINAL_CACHE_KEY_REQUIRED')
    for key in ('quantization', 'revision'):
        if cache[key] is not None and (type(cache[key]) is not str or not cache[key] or len(cache[key]) > 1024
                or any(c in cache[key] for c in '\0\r\n')):
            raise ValueError('LIF13_ORIGINAL_CACHE_KEY_REQUIRED')
    for key, maximum in [('device_index', 0), ('read_elements', 16), ('max_response_bytes', 8 * 1024**2),
            ('duration_seconds', 300), ('rpc_timeout_seconds', 30)]:
        minimum = 0 if key == 'device_index' else 1024 if key == 'max_response_bytes' else 10 if key == 'duration_seconds' else 1
        if type(value[key]) is not int or not minimum <= value[key] <= maximum:
            raise ValueError('LIF13_BOUNDED_SELECTED_INPUT_REQUIRED')
    return value


def preflight(*, store, plan_id, pack, target, config, output, entered_by):
    source = Store.read_only(Path(store)); verify_complete(source)
    original, plan = load_pack(Path(pack)), source.latest(plan_id)
    if (plan.kind != 'PLAN' or plan.mode != Mode.audit or len(plan.inputs.environments) != 1
            or plan.inputs.pack != original.ref() or original.ref().model_dump(mode='json') != PACK_REF
            or get_pack(source, plan.inputs.pack).ref() != original.ref()):
        raise ValueError('LIF13_ORIGINAL_PACK31_SINGLE_AUDIT_PLAN_REQUIRED')
    check = original.checks['LIF-13']
    if (check.revision != 4 or check.executor is not None or check.judge is not None or check.policy_params
            or check.required_observations or len(check.required_observations_prose) != 1
            or len(check.challenge_steps) != 3 or not plan.item('LIF-13').selected):
        raise ValueError('LIF13_ORIGINAL_MANUAL_DEFINITION_REQUIRED')
    env, policy = source.resolve(plan.inputs.environments[0]), source.resolve(plan.inputs.policy)
    if (policy.pack != original.ref() or env.engine.name != 'vllm' or env.engine.version != '0.31.0'
            or env.runtime.gpu_count != 1 or env.runtime.tp_groups != 1):
        raise ValueError('LIF13_EXACT_SELECTED_ENV_POLICY_REQUIRED')
    for obj, kind in ((env, 'ENV'), (policy, 'POL')):
        if re.fullmatch(kind + '-[0-9A-HJKMNP-TV-Z]{26}', obj.id) is None:
            raise ValueError('LIF13_CANONICAL_ENV_POL_IDS_REQUIRED')
    target_value = _read(target); validate_capture_target(target_value, env)
    config_value = _config(_read(config), env, policy, plan, target_value)
    out = Path(output).absolute()
    if (out.exists() or out.is_symlink() or any(p.is_symlink() for p in out.parents)
            or out.resolve().is_relative_to(source.root.resolve()) or source.root.resolve().is_relative_to(out.resolve())
            or out.resolve().is_relative_to(Path(__file__).resolve().parents[2])
            or type(entered_by) is not str or not entered_by.strip()):
        raise ValueError('LIF13_FRESH_OUTPUT_AND_DECLARED_OPERATOR_REQUIRED')
    return source, original, plan, env, policy, target_value, config_value


def pod_commands(*, pod_config, pod_source_root, pod_output):
    values = [str(v) for v in (pod_config, pod_source_root, pod_output)]
    if any(not v.startswith('/') or '\x00' in v for v in values) or values[0] == values[2]:
        raise ValueError('LIF13_EXPLICIT_POD_PATHS_REQUIRED')
    argv = ['python', '-m', 'tools.b_lif13_weights_native', '--config', values[0], '--source-root', values[1], '--output', values[2]]
    return {'execution_location': 'selected_existing_pod', 'preflight_argv': argv,
        'execution_argv': [*argv, '--execute'], 'executed': False, 'module_installation_verified': False,
        'generic_kubernetes_exec_added': False}


def _source_status(value):
    value = value if type(value) is dict else {}
    post = value.get('post'); post = post.get('status') if type(post) is dict else post
    return {'pre': value.get('pre') if value.get('pre') in {'verified', 'mismatch', 'unavailable'} else 'unavailable',
        'post': post if post in {'verified', 'mismatch', 'unavailable'} else 'unavailable'}


def _exports(value):
    value = value if type(value) is dict else {}
    counts = {k: value.get(k) if type(value.get(k)) is int and value[k] >= 0 else None
        for k in ('received_cuda_entries', 'rebuilt_cuda_entries', 'cpu_by_value_entries')}
    return {**counts, 'complete_claimed': value.get('complete') is True,
        'all_received_cuda_rebuilt_claimed': bool(value.get('complete') is True
            and counts['received_cuda_entries'] is not None and counts['rebuilt_cuda_entries'] == counts['received_cuda_entries']),
        'cuda_handoff_seen_claimed': bool(counts['received_cuda_entries'] is not None and counts['received_cuda_entries'] > 0)}


def _rpc_fact(value):
    value = value if type(value) is dict else {}
    return {'attempted': value.get('attempted') is True, 'response_received': value.get('response_received') is True,
        'status': value.get('status') if value.get('status') in STATUS else 'unavailable',
        'peer_generation_verified_claimed': value.get('peer_generation_verified') is True,
        'daemon_generation_sha256_claimed': value.get('daemon_generation_sha256') if _sha(value.get('daemon_generation_sha256')) else None}


def _read_fact(value, config):
    value = value if type(value) is dict else {}; shape, count = value.get('shape'), value.get('selected_count')
    valid = (value.get('collected') is True and value.get('read_completion') is True
        and type(count) is int and 0 < count <= config['read_elements']
        and _sha(value.get('digest_sha256')) and type(shape) is list and 1 <= len(shape) <= 8
        and all(type(n) is int and 0 <= n < 2**63 for n in shape)
        and type(value.get('dtype')) is str and re.fullmatch('torch\\.[a-zA-Z0-9_]{1,48}', value['dtype']) is not None)
    runtime = value.get('runtime', {}); runtime = runtime if type(runtime) is dict else {}
    version = runtime.get('torch_version')
    return {'get_state_claimed': _rpc_fact(value.get('get_state')), 'exports_claimed': _exports(value.get('exports')),
        'runtime_claimed': {'torch_version': version if type(version) is str and re.fullmatch('[A-Za-z0-9.+_-]{1,100}', version) else None,
            'torch_reductions_file_sha256': runtime.get('torch_reductions_file_sha256') if _sha(runtime.get('torch_reductions_file_sha256')) else None},
        'collected': bool(valid), 'selected_count': count if valid else None,
        'digest_sha256': value.get('digest_sha256') if valid else None, 'shape': shape if valid else None,
        'dtype': value.get('dtype') if valid else None, 'cuda_ipc_rebuilt_claimed': value.get('cuda_ipc_rebuilt') is True,
        'is_cuda_claimed': value.get('is_cuda') is True, 'read_completion_claimed': value.get('read_completion') is True,
        'device_index': value.get('device_index') if type(value.get('device_index')) is int else None,
        'native_path_claimed': bool(valid and value.get('is_cuda') is True and value.get('cuda_ipc_rebuilt') is True
            and value.get('device_index') == config['device_index']),
        'source_validation_claimed': _source_status(value.get('source_validation')),
        'peer_generation_verified_claimed': value.get('peer_generation_verified') is True,
        'daemon_generation_sha256_claimed': value.get('daemon_generation_sha256') if _sha(value.get('daemon_generation_sha256')) else None,
        'status': value.get('status') if value.get('status') in {'read', 'unavailable', 'read_error', 'unsupported_non_cuda',
            'unsupported_layout', 'unsupported_empty', 'not_attempted'} else 'unavailable'}


def project(raw, config):
    expected = {'schema': CAPTURE_SCHEMA, 'config_sha256': digest(config), 'environment_ref': config['environment_ref'],
        'policy_ref': config['policy_ref'], 'plan_ref': config['plan_ref'], 'target_sha256': config['target_sha256'],
        'source_commit': SOURCE_COMMIT, 'data_origin': config['data_origin']}
    if type(raw) is not dict or any(raw.get(k) != v for k, v in expected.items()):
        raise ValueError('LIF13_CAPTURE_CONFIG_OR_REF_MISMATCH')
    dates = {k: datetime.fromisoformat(raw[k].replace('Z', '+00:00')) for k in ('started_at', 'finished_at')}
    if any(v.tzinfo is None for v in dates.values()) or dates['finished_at'] < dates['started_at']:
        raise ValueError('LIF13_CAPTURE_TIME_INVALID')
    collection_id = raw.get('collection_id')
    if type(collection_id) is not str or re.fullmatch('lif13-[0-9a-f]{32}', collection_id) is None:
        raise ValueError('LIF13_CAPTURE_ID_INVALID')
    consumers = raw.get('consumers')
    if type(consumers) is not list or len(consumers) > 2:
        raise ValueError('LIF13_CAPTURE_CONSUMERS_INVALID')
    reads, followups, seen = [], [], set()
    for row in consumers:
        if type(row) is not dict or type(row.get('slot')) is not int or row['slot'] not in (0, 1) or row['slot'] in seen:
            raise ValueError('LIF13_CAPTURE_CONSUMER_SLOT_INVALID')
        seen.add(row['slot']); before = _read_fact(row.get('baseline'), config); after = _read_fact(row.get('held_after'), config)
        comparable = (before['native_path_claimed'] and after['native_path_claimed'] and before['selected_count'] == after['selected_count']
            and before['shape'] == after['shape'] and before['dtype'] == after['dtype'])
        relation = ('same' if before['digest_sha256'] == after['digest_sha256'] else 'changed') if comparable else 'unavailable'
        reads.append({'slot': row['slot'], 'baseline': before, 'held_after': after, 'digest_relation': relation,
            'native_measurement_verified': False,
            'cleanup_claimed': {k: row.get('cleanup', {}).get(k) is True for k in ('references_dropped', 'device_sync_complete')},
            'process_cleanup_claimed': {k: row.get('process_cleanup', {}).get(k) is True for k in ('joined', 'forced', 'exit_ok')}})
        follow = row.get('followup_get_state', {}); follow = follow if type(follow) is dict else {}
        followups.append({'slot': row['slot'], 'attempted': follow.get('attempted') is True,
            'status': follow.get('status') if follow.get('status') in STATUS else 'unavailable',
            'rpc_claimed': _rpc_fact(follow), 'exports_claimed': _exports(follow.get('exports')),
            'reason': follow.get('reason') if follow.get('reason') in {'weights_released', 'cache_key_mismatch', 'daemon_error', 'rpc_error', 'unavailable', 'not_attempted', 'ok'} else 'unavailable'})
    release = raw.get('release', {}); release = release if type(release) is dict else {}
    source_files = raw.get('source_file_bytes_sha256', {})
    client_claim = bool(CLIENT_SOURCE_FILES and source_files == CLIENT_SOURCE_FILES)
    cleanup = raw.get('cleanup', {}); cleanup = cleanup if type(cleanup) is dict else {}
    return {**expected, **{k: v.isoformat() for k, v in dates.items()}, 'collection_id': collection_id,
        'executed_claimed': raw.get('executed') is True, 'mode': 'zero_copy', 'full_scope_coverage': False,
        'qualification': {'client_source_claim_matches_fixed_files': client_claim,
            'daemon_source_verified': False, 'external_generation_receipt_verified': False,
            'client_source_validation_claimed': _source_status(raw.get('source_validation')),
            'peer_generation_verified_claimed': raw.get('peer_generation_verified') is True,
            'own_producer_sha256_claimed': raw.get('own_producer_sha256') if _sha(raw.get('own_producer_sha256')) else None,
            'release_authorization_verified': False, 'measurement_verified': False},
        'release_actor_scope': {'daemon_uid_declared': config['daemon']['uid'],
            'actor_real_uid_claimed': release.get('actor_real_uid') if type(release.get('actor_real_uid')) is int and release['actor_real_uid'] >= 0 else None,
            'actor_effective_uid_claimed': release.get('actor_effective_uid') if type(release.get('actor_effective_uid')) is int and release['actor_effective_uid'] >= 0 else None,
            'rpc_claimed': _rpc_fact(release), 'mode': 'zero_copy',
            'rank': config['cache_key']['tp_rank'], 'daemon_role': 'draft' if config['cache_key']['is_draft'] else 'target',
            'release_attempted': release.get('attempted') is True,
            'release_status': release.get('status') if release.get('status') in STATUS else 'unavailable',
            'response_received': release.get('response_received') is True, 'authorization_verified': False},
        'post_acquisition': {'consumers': followups, 'fallback_collected': False, 'fallback_approval_verified': None,
            'copy_mode_collected': False}, 'existing_native_references': {'consumers': reads, 'scope': 'selected_tensor_bounded_prefix_only'},
        'daemon_generation_sha256_claimed': raw.get('daemon_generation_sha256') if _sha(raw.get('daemon_generation_sha256')) else None,
        'cleanup': {'owned_children_complete_claimed': cleanup.get('owned_children_complete') is True,
            'daemon_left_empty_allowed_declared': config['allow_empty_after'], 'daemon_recovery_complete': None, 'daemon_native_cleanup_verified': None,
            'exported_counters_reclaimed_verified': None, 'allocator_global_reclamation_verified': None,
            'external_recovery_owner_declared': True,
            'collection_gap_claimed': cleanup.get('collection_gap') is not False},
        'gaps': ['ENTERPRISE_POLICY_NOT_VERIFIED', 'DAEMON_SOURCE_NOT_ATTESTED', 'EXTERNAL_GENERATION_RECEIPT_UNAVAILABLE',
            'FULL_MODEL_CONTROL_NOT_COLLECTED', 'FALLBACK_NOT_COLLECTED', 'COPY_MODE_NOT_SELECTED',
            'DAEMON_NATIVE_CLEANUP_UNVERIFIED', 'PARTIAL_HANDOFF_CAN_REQUIRE_EXTERNAL_RECOVERY'], 'collection_gaps': sorted(set(raw.get('gaps', [])) & {
            'LINUX_AMD64_CUDA_REQUIRED', 'PEER_GENERATION_UNAVAILABLE', 'RELEASE_RESPONSE_UNAVAILABLE',
            'CHILD_COMMAND_UNAVAILABLE', 'AFTER_READ_INCOMPLETE', 'BASELINE_COHORT_UNAVAILABLE', 'COLLECTION_INCOMPLETE'})}


def run(*, store, plan_id, pack, target, config, output, entered_by, capture=None, import_capture=False,
        pod_config=None, pod_source_root=None, pod_output=None):
    source, original, plan, env, policy, target_value, cfg = preflight(store=store, plan_id=plan_id, pack=pack,
        target=target, config=config, output=output, entered_by=entered_by)
    binding = {k: v.ref().model_dump(mode='json') for k, v in [('environment', env), ('policy', policy), ('plan', plan), ('pack', original)]}
    commands = pod_commands(pod_config=pod_config, pod_source_root=pod_source_root, pod_output=pod_output)
    if not import_capture:
        return {'preflight': True, 'evidence_imported': False, 'native_runs': 0, 'rpc_calls': 0,
            'config_sha256': digest(cfg), 'pod_commands': commands, **binding}
    if capture is None: raise ValueError('LIF13_EXISTING_NATIVE_CAPTURE_REQUIRED')
    before = _inventory(source.root)
    inputs = {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (target, config, capture)}
    facts = project(_read(capture), cfg)
    out = Path(output); out.mkdir(parents=True, exist_ok=False)
    export_bundle(source.root, out / 'source.zip'); restore_bundle(out / 'source.zip', out / 'store'); dest = Store(out / 'store')
    issues, stages = [], {k: False for k in ('artifact_stored', 'manual_evidence_stored', 'report_stored', 'bundle_restored', 'summary_stored')}
    try:
        attachment = 'weights-lifetime.json'
        try: _write(out / attachment, facts); stages['artifact_stored'] = True
        except OSError: attachment = None; issues.append('ARTIFACT_WRITE_UNAVAILABLE')
        doc = build_template(dest, original, plan, include_automated=True)
        doc['checks'] = [e for e in doc['checks'] if e['check_id'] == 'LIF-13' and e['side'] == 'single']
        if len(doc['checks']) != 1: raise ValueError('LIF13_ORIGINAL_SINGLE_MANUAL_FORM_REQUIRED')
        entry = doc['checks'][0]
        entry.update(entered_by=entered_by, entered_at=facts['finished_at'], started_at=facts['started_at'], finished_at=facts['finished_at'],
            attachments=[attachment] if attachment else [], verdicts={k: 'UNKNOWN' for k in ('security', 'functional', 'performance')},
            basis='선정 zero_copy native producer의 안전 facts를 원 LIF13 세 필수 결과로 나눠 선언 증거에 보존했다.',
            limitations=['client source bytes 주장은 daemon 실행 소스·기업 release 권한 인증과 구분한다.',
                '외부 target/generation/인증 receipt가 없어 standalone 파일을 MEASURED로 승격하지 않는다.',
                '실제 fallback/copy/full-model 정상 대조와 원 challenge 전체는 미수행이다. 입력자는 신고자다.'])
        entry['normal_control']['result'] = 'not_attempted'
        for challenge in entry['challenges']: challenge['attempted'] = False
        entry['observation_notes'][0]['answer'] = 'LIF13_SAFE_FACTS=' + canonical_json(facts).decode('utf-8')
        if not attachment: entry['limitations'].append('정제 첨부 보관 실패: 같은 safe facts를 원 manual 노트와 반환 summary에 보존했다.')
        form = out / 'manual-evidence.yaml'
        try:
            with form.open('x', encoding='utf-8') as stream: stream.write(dump_template(doc))
        except OSError: issues.append('MANUAL_FORM_WRITE_UNAVAILABLE')
        import_evidence(dest, original, form, document=doc); stages['manual_evidence_stored'] = True
        as_of = datetime.now(timezone.utc).replace(microsecond=0)
        results = judge_plan(dest, original, plan, as_of=as_of); report = build_report(dest, original, plan, as_of=as_of)
        dest.put(report); stages['report_stored'] = True; verify_complete(dest); refs = _refs(dest)
        export_bundle(dest.root, out / 'evidence.zip'); restore_bundle(out / 'evidence.zip', out / 'restored')
        restored = Store(out / 'restored'); replay = judge_plan(restored, original, plan, as_of=as_of); verify_complete(restored)
        if _refs(restored) != refs or [r.ref() for r in replay] != [r.ref() for r in results]: raise ValueError('LIF13_EXACT_REF_RESTORE_MISMATCH')
        stages['bundle_restored'] = True
        if _inventory(source.root) != before or any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in inputs.items()):
            raise ValueError('LIF13_ORIGINAL_INPUT_CHANGED')
        summary = {'status': 'stored', 'evidence_imported': True, 'native_runs': 0, 'rpc_calls': 0, **binding,
            'report': report.ref().model_dump(mode='json'), 'verdicts': {k: 'UNKNOWN' for k in ('security', 'functional', 'performance')},
            'source_unchanged': True, 'exact_refs_restored': True, 'facts_storage': 'artifact_and_manual_notes' if attachment else 'manual_notes',
            'safe_facts': facts, 'issues': issues, 'full_scope_coverage': False}
        _write(out / 'summary.json', summary); stages['summary_stored'] = True
        return summary
    except Exception:
        raise EvidencePreservationError(facts, stages, issues, out) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('store', 'pack', 'target', 'config', 'output', 'pod_config', 'pod_source_root', 'pod_output'):
        parser.add_argument('--' + key.replace('_', '-'), required=True, type=Path)
    parser.add_argument('--plan', dest='plan_id', required=True); parser.add_argument('--entered-by', required=True)
    parser.add_argument('--capture', type=Path); parser.add_argument('--import-capture', action='store_true')
    try: print(json.dumps(run(**vars(parser.parse_args(argv))), ensure_ascii=False))
    except EvidencePreservationError as error:
        parser.exit(1, json.dumps({'recovery': error.recovery, 'safe_facts': error.safe_facts}, ensure_ascii=False) + '\n')
    except Exception: parser.exit(1, 'LIF13_WEIGHTS_EVIDENCE_UNAVAILABLE\n')


if __name__ == '__main__': main()
