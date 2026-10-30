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
    from guard.executors.kubernetes_lifecycle_capture import validate_target
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


def _safe_snapshot(value):
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
    safe_outer = {k: outer.get(k) if _sha(outer.get(k)) else None for k in ('source_sha256', 'code_sha256', 'generation_sha256', 'session_id_sha256', 'process_start_sha256')}
    safe_outer['native_record_seq'] = outer.get('native_record_seq') if type(outer.get('native_record_seq')) is int and outer['native_record_seq'] > 0 else None
    safe_outer['source_contract_verified'] = False
    return {'schema': native.SCHEMA, 'point': value['point'], 'phase': value['phase'], 'status': value['status'],
        'seq': value['seq'], 'binding': safe_binding, 'metadata': safe, 'native_outer': safe_outer,
        'gaps': sorted({g for g in value.get('gaps', []) if type(g) is str and g in native.GAPS})}


def project(raw, env, target, prepared, manifest):
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
        row = _safe_snapshot(value)
        if row is None:
            gaps.add('INVALID_SNAPSHOT'); continue
        coordinate = (row['point'], row['phase'], row['seq'], digest(row['binding']), row['metadata']['owner_token'], row['metadata']['thread_token'], digest(row['native_outer']))
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
        calls = [c for c in rows if c['phase'] == 'CALL' and c['point'] == row['point']
            and type(c['seq']) is int and c['seq'] == tr['call_seq'] and c['seq'] < row['seq'] == tr['end_seq']
            and c['binding'] == row['binding'] and c['native_outer']['generation_sha256'] == row['native_outer']['generation_sha256'] and c['metadata']['owner_token'] == row['metadata']['owner_token']
            and c['metadata']['thread_token'] == row['metadata']['thread_token']
            and row['metadata']['owner_token'] is not None and row['metadata']['thread_token'] is not None]
        if len(calls) == 1 and 'SNAPSHOT_COORDINATE_AMBIGUOUS' not in gaps:
            pairs.append({'point': row['point'], 'call_seq': tr['call_seq'], 'end_seq': row['seq'], 'phase': row['phase'],
                'source_qualified': False, 'actual_worker_observed': False, 'generation_verified': False})
    cleanup = raw.get('cleanup'); cleanup = cleanup if type(cleanup) is dict else {}
    safe_cleanup = {k: cleanup.get(k) if type(cleanup.get(k)) is bool else None for k in ('owned_host_complete', 'worker_observer_closed')}
    close = raw.get('observer_close'); safe_close = None
    if type(close) is dict:
        from guard.adapters import vllm_worker_readiness as native
        safe_close = {k: close.get(k) if type(close.get(k)) is bool else None for k in ('complete', 'collection_gap')}
        safe_close['pending_calls'] = close.get('pending_calls') if type(close.get('pending_calls')) is int and 0 <= close['pending_calls'] <= 512 else None
        safe_close['gaps'] = sorted({g for g in close.get('gaps', []) if type(g) is str and g in native.GAPS})
    return {'schema': SCHEMA, 'profile': PROFILE, 'environment_ref': env.ref().model_dump(mode='json'),
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
        identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if (identity(before) != identity(after) or identity(after) != identity(current)
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


def run(*, store, plan_id, pack, target, prepared, manifest, output, entered_by, execute=False, import_capture=None, _example_capture=None):
    source, original, plan, env, policy, t, assets, m = preflight(store=store, plan_id=plan_id, pack=pack,
        target=target, prepared=prepared, manifest=manifest, output=output, entered_by=entered_by)
    # preflight가 확인한 원본을 backend/project 실행 전에 고정한다.
    before = _inventory(source.root)
    input_paths = [Path(p) for p in (target, prepared, manifest)] + [Path(prepared).parent / n for n in assets['assets']]
    inputs = {str(p): _input_hash(p) for p in input_paths}
    if import_capture is not None and (_example_capture is not None or execute):
        raise ValueError('LIF07_IMPORT_AND_LIVE_MODES_EXCLUSIVE')
    if _example_capture is not None and env.data_origin.value != 'EXAMPLE':
        raise ValueError('LIF07_EXAMPLE_CAPTURE_REQUIRES_EXAMPLE_ENV')
    binding = {k: v.ref().model_dump(mode='json') for k, v in [('environment', env), ('policy', policy), ('plan', plan), ('pack', original)]}
    if not execute and import_capture is None:
        return {'executed': False, 'requests': 0, 'capability_supported': False, 'gaps': sorted(GAPS - {'INVALID_SNAPSHOT', 'SNAPSHOT_COORDINATE_AMBIGUOUS'}), **binding}
    out = Path(output)
    stages = {k: False for k in ('artifact_stored', 'manual_evidence_stored', 'report_stored', 'bundle_restored', 'summary_stored')}; issues = []
    record = project(_empty(env, t, assets, m), env, t, assets, m)
    try:
        if import_capture is not None:
            raw, capture_hash = _import_capture(import_capture); inputs[str(Path(import_capture))] = capture_hash
        else:
            # 승인 실제 capability가 없으므로 backend는 호출하지 않는다.
            raw = _empty(env, t, assets, m) if _example_capture is None else _example_capture(env, t, assets, m)
        record = project(raw, env, t, assets, m)
        record['input_mode'] = 'imported_observation_file' if import_capture is not None else 'EXAMPLE_callback' if _example_capture is not None else 'closed_live_capability'
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
                '실제 대상 요청·Pod 변경·지연 0. EXAMPLE snapshot은 실제 worker/GPU 실증으로 집계하지 않는다. 입력 관측 파일은 신고된 관측의 증거화이며 외부 source/worker/READY 진위 인증은 미수집이다.'])
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
        summary = {'executed': True, 'actual_target_executed': False, 'requests': 0, 'status': 'stored', **binding,
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
    parser.add_argument('--plan', dest='plan_id', required=True); parser.add_argument('--entered-by', required=True); parser.add_argument('--execute', action='store_true')
    try: print(json.dumps(run(**vars(parser.parse_args(argv))), ensure_ascii=False))
    except EvidencePreservationError as error: parser.exit(1, json.dumps({'recovery': error.recovery, 'safe_facts': error.safe_facts}, ensure_ascii=False) + '\n')
    except Exception: parser.exit(1, 'LIF07_WORKER_READINESS_UNAVAILABLE\n')

if __name__ == '__main__': main()
