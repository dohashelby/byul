"""선정 Pod stdout의 정제된 최소수집 관측을 원 OBS-03 수동 증거에 연결한다."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from guard.automation_policy import string_list
from guard.collectors.manual import build_template, dump_template, import_evidence
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.models import Mode, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_binding import validate_capture_target
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle, verify_complete
from guard.store.packs import get_pack

PACK_REF = {'kind': 'PACK', 'id': 'PACK-BASELINE', 'revision': 31,
    'sha256': '54f4f2357533a8b201ad4397d4b182b08b89ee4821ffe67763e3fbecd8f369ac'}
PROFILE = 'kubernetes-owned-pod-log-minimization/1'
OVERRIDE = 'selected Pod stdout and actual error_input marker only; model inference and other copies not executed'
POLICY_KEYS = ('allowed_log_fields', 'forbidden_log_fields', 'forbidden_marker_contexts',
               'required_sources', 'required_branches')
OBSERVATIONS = ('log_coverage_complete', 'log_coverage_gaps', 'log_sources_tested', 'log_fields_seen',
    'log_marker_contexts_seen', 'log_source_ids_tested', 'log_required_branches_tested',
    'functional_probes_planned', 'functional_probe_results')
VIOLATIONS = {'FIELD_POLICY_VIOLATION', 'FORBIDDEN_ERROR_INPUT'}
_GAPS = {'CALLER_INPUT_NOT_SENT', 'PRIVATE_SYSTEM_NOT_SENT', 'OTHER_SOURCES_NOT_COLLECTED',
    'BUDGET_EXHAUSTED', 'REQUEST_BINDING_UNAVAILABLE', 'SOURCE_BINDING_UNAVAILABLE',
    'SOURCE_READ_UNAVAILABLE', 'SOURCE_CAPTURE_INCOMPLETE', 'SOURCE_JSON_UNAVAILABLE',
    'SOURCE_EVENT_UNLINKED', 'A_CAPABILITY_UNAVAILABLE', 'PRIMARY_UNAVAILABLE',
    'BEFORE_CONTROL_UNAVAILABLE', 'AFTER_CONTROL_UNAVAILABLE', 'HTTP_UNAVAILABLE',
    'SOURCE_POSTCHECK_UNAVAILABLE', 'CLEANUP_UNVERIFIED', 'EXCEPTION_DUMP_NOT_OBSERVED'}


def _digest(value):
    return sha256_hex(canonical_json(value))


def _inventory(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(root).rglob('*')) if path.is_file()}


def _refs(store):
    return sorted((obj.kind.value, obj.id, obj.revision, obj.content_sha256)
        for kind in ('ENV', 'POL', 'CHG', 'PLAN', 'EVD', 'RES', 'RPT') for obj in store.iter_kind(kind))


def _write(path, value):
    with Path(path).open('xb') as stream:
        stream.write(canonical_json(value))


def preflight(*, store, plan_id, pack, target, source_id, output, entered_by):
    source = Store.read_only(Path(store))
    verify_complete(source)
    original, plan = load_pack(Path(pack)), source.latest(plan_id)
    if (plan.kind != 'PLAN' or plan.mode != Mode.audit or len(plan.inputs.environments) != 1
            or plan.inputs.pack != original.ref() or original.ref().model_dump(mode='json') != PACK_REF
            or get_pack(source, plan.inputs.pack).ref() != original.ref()):
        raise ValueError('OBS03_ORIGINAL_PACK31_AUDIT_PLAN_REQUIRED')
    check = original.checks['OBS-03']
    if (check.revision != 5 or check.executor != 'log_analysis'
            or check.judge is None or check.judge.plugin != 'rule_table'
            or [(key.key, key.type, key.required) for key in check.policy_params] !=
                [(key, 'list[str]', True) for key in POLICY_KEYS]
            or tuple(key.key for key in check.required_observations) != OBSERVATIONS
            or len(check.challenge_steps) != 3 or len(check.required_observations_prose) != 1
            or not plan.item('OBS-03').selected):
        raise ValueError('OBS03_ORIGINAL_DEFINITION_REQUIRED')
    env, policy = source.resolve(plan.inputs.environments[0]), source.resolve(plan.inputs.policy)
    if policy.pack != original.ref():
        raise ValueError('OBS03_POLICY_PACK_MISMATCH')
    values = policy.values.get('OBS-03', {})
    for key in POLICY_KEYS:
        string_list(values, key, empty=key in {'forbidden_log_fields', 'forbidden_marker_contexts'})
    if (set(values['forbidden_marker_contexts']) - {'caller_input', 'private_system', 'error_input'}
            or not {'normal', 'error', 'exception_dump'} <= set(values['required_branches'])
            or set(values['required_branches']) - {'normal', 'error', 'exception_dump', 'debug', 'trace'}
            or source_id not in values['required_sources']):
        raise ValueError('OBS03_DECLARED_POLICY_SCOPE_INVALID')
    target_path = Path(target)
    if target_path.is_symlink():
        raise ValueError('OBS03_TARGET_SYMLINK')
    with target_path.open('rb') as stream:
        target_value = strict_json(stream.read(262145), limit=262144)
    if type(target_value) is not dict or target_value.get('environment_ref') != env.ref().model_dump(mode='json'):
        raise ValueError('OBS03_TARGET_ENVIRONMENT_MISMATCH')
    kube = target_value.get('kubernetes', {})
    if type(kube) is not dict:
        raise ValueError('OBS03_TARGET_BINDING_INVALID')
    validate_capture_target({'schema': 'guard-kubernetes-lifecycle-target/1',
        'environment_ref': env.ref().model_dump(mode='json'), 'run_id': kube.get('run_id'),
        'lab': target_value.get('lab'), 'kubernetes': {'context': kube.get('context'), 'engine': kube.get('engine')}}, env)
    output = Path(output).absolute()
    if (output.exists() or output.is_symlink() or output.resolve().is_relative_to(source.root.resolve())
            or source.root.resolve().is_relative_to(output.resolve())
            or type(entered_by) is not str or not entered_by.strip()):
        raise ValueError('OBS03_FRESH_OUTPUT_AND_OPERATOR_REQUIRED')
    return source, original, plan, env, values, target_value


def _capture(env, target, values, *, source_id, expected_policy_ref, workdir):
    from guard.executors.kubernetes_log_minimization import capture_log_minimization
    return capture_log_minimization(env, target, values, source_id=source_id,
                                    expected_policy_ref=expected_policy_ref, workdir=workdir)


def _codes(value, allowed=None):
    if type(value) is not list or any(type(item) is not str or not re.fullmatch('[A-Z][A-Z0-9_]{0,99}', item)
                                     for item in value):
        return ['OBS03_REDACTED_GAP'] if allowed is None else []
    if allowed is not None:
        return sorted(set(value) & allowed)
    return sorted((set(value) & _GAPS) | ({'OBS03_COLLECTION_GAP'} if set(value) - _GAPS else set()))


def _project(raw, env, target, values, policy_ref, source_id):
    expected = {'schema': 'guard-log-minimization-capture/1', 'profile': PROFILE,
        'environment_ref': env.ref().model_dump(mode='json'), 'target_sha256': _digest(target),
        'policy_ref': policy_ref.model_dump(mode='json'), 'policy_values_sha256': _digest(values),
        'source_id': source_id, 'source_kind': 'kubernetes_pod_logs'}
    if type(raw) is not dict or any(raw.get(key) != value for key, value in expected.items()):
        raise ValueError('OBS03_CAPTURE_BINDING_INVALID')
    dates = {}
    for key in ('started_at', 'finished_at'):
        value = raw.get(key)
        if type(value) is not str:
            raise ValueError('OBS03_CAPTURE_TIME_INVALID')
        dates[key] = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dates[key].tzinfo is None:
            raise ValueError('OBS03_CAPTURE_TIME_INVALID')
    if (dates['finished_at'] < dates['started_at'] or type(raw.get('collection_id')) is not str
            or re.fullmatch('[A-Za-z0-9._-]{1,100}', raw['collection_id']) is None
            or raw.get('data_origin') != ('EXAMPLE' if env.data_origin.value == 'EXAMPLE' else 'DECLARED')):
        raise ValueError('OBS03_CAPTURE_ID_ORIGIN_INVALID')
    result = {**expected, **{key: value.isoformat() for key, value in dates.items()},
        'collection_id': raw['collection_id'], 'data_origin': raw['data_origin'],
        'coverage_complete': False, 'full_scope_coverage': False, 'model_inference_verified': False,
        'normal_control_scope': 'tokenize_api_shape_only',
        'analysis_binding_verified': raw.get('analysis_binding_verified') is True,
        'source_generation_verified': raw.get('source_generation_verified') is True,
        'violation_codes': _codes(raw.get('violation_codes'), VIOLATIONS),
        'marker_contexts_seen': ['error_input'] if 'error_input' in (raw.get('marker_contexts_seen') or []) else [],
        'branches_tested': sorted(set(raw.get('branches_tested') or []) &
            ({'normal', 'error', 'exception_dump'} if raw.get('exception_dump_verified') is True else {'normal', 'error'})),
        'exception_dump_verified': True if raw.get('exception_dump_verified') is True else None,
        'request_summaries': [], 'source_reads': [], 'gaps': _codes(raw.get('gaps'))}
    for key in ('field_violation_count', 'forbidden_error_input_count'):
        value = raw.get(key)
        if type(value) is not int or not 0 <= value <= 1_000_000:
            raise ValueError('OBS03_PREDICATE_COUNT_INVALID')
        result[key] = value
    requests = raw.get('request_summaries')
    reads = raw.get('source_reads')
    if type(requests) is not list or len(requests) > 3 or type(reads) is not list or len(reads) > 16:
        raise ValueError('OBS03_CAPTURE_ROWS_INVALID')
    seen = set()
    for row in requests:
        if (type(row) is not dict or row.get('case_id') not in {'before', 'primary', 'after'}
                or row['case_id'] in seen or any(type(row.get(k)) is not bool for k in ('attempted', 'binding_verified'))
                or row.get('outcome') is not None and (type(row['outcome']) is not int or not 100 <= row['outcome'] <= 599)):
            raise ValueError('OBS03_REQUEST_SUMMARY_INVALID')
        seen.add(row['case_id'])
        result['request_summaries'].append({key: row.get(key) for key in ('case_id', 'attempted', 'binding_verified', 'outcome')})
    for row in reads:
        if type(row) is not dict or any(type(row.get(key)) is not bool for key in ('attempted', 'binding_verified', 'complete')):
            raise ValueError('OBS03_SOURCE_READ_INVALID')
        safe = {key: row[key] for key in ('attempted', 'binding_verified', 'complete')}
        for key in ('returncode', 'byte_count', 'event_count', 'trial_linked_event_count'):
            value = row.get(key)
            if value is not None and (type(value) is not int or key != 'returncode' and not 0 <= value <= 16 * 1024**2):
                raise ValueError('OBS03_SOURCE_READ_COUNT_INVALID')
            safe[key] = value
        result['source_reads'].append(safe)
    control, cleanup = raw.get('normal_control') or {}, raw.get('cleanup') or {}
    result['normal_control'] = {'attempted': control.get('attempted') is True,
        'succeeded': control.get('succeeded') if type(control.get('succeeded')) is bool else None}
    result['cleanup'] = {'complete': cleanup.get('complete') is True,
                         'collection_gap': cleanup.get('collection_gap') is not False}
    return result


def _known_failure(capture, values):
    return (capture['analysis_binding_verified'] and capture['source_generation_verified']
        and any(row['binding_verified'] and row['returncode'] == 0
            and type(row['trial_linked_event_count']) is int and row['trial_linked_event_count'] > 0
            for row in capture['source_reads'])
        and (capture['field_violation_count'] > 0 and 'FIELD_POLICY_VIOLATION' in capture['violation_codes']
             or capture['forbidden_error_input_count'] > 0 and 'FORBIDDEN_ERROR_INPUT' in capture['violation_codes']
             and 'error_input' in values['forbidden_marker_contexts'] and 'error_input' in capture['marker_contexts_seen']))


def run(*, store, plan_id, pack, target, source_id, output, entered_by, execute=False):
    source, original, plan, env, values, target_value = preflight(store=store, plan_id=plan_id,
        pack=pack, target=target, source_id=source_id, output=output, entered_by=entered_by)
    binding = {'plan': plan.ref().model_dump(mode='json'), 'pack': original.ref().model_dump(mode='json'),
        'policy': plan.inputs.policy.model_dump(mode='json'), 'environment': env.ref().model_dump(mode='json'),
        'target_sha256': _digest(target_value), 'policy_values_sha256': _digest(values), 'source_id': source_id}
    if not execute:
        return {'preflight': True, 'executed': False, 'collector_calls': 0, **binding}
    before, target_before = _inventory(source.root), hashlib.sha256(Path(target).read_bytes()).hexdigest()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    export_bundle(source.root, output / 'source.zip')
    restore_bundle(output / 'source.zip', output / 'store')
    destination = Store(output / 'store')
    capture = _project(_capture(env, target_value, values, source_id=source_id,
        expected_policy_ref=plan.inputs.policy, workdir=output / 'capture'), env, target_value, values, plan.inputs.policy, source_id)
    attachment, issues = 'log-minimization.json', []
    try:
        _write(output / attachment, capture)
    except OSError:
        attachment = None
        issues.append('OBS03_ARTIFACT_WRITE_UNAVAILABLE')
    doc = build_template(destination, original, plan, include_automated=True)
    doc['checks'] = [entry for entry in doc['checks'] if entry['check_id'] == 'OBS-03' and entry['side'] == 'single']
    if len(doc['checks']) != 1:
        raise ValueError('OBS03_ORIGINAL_MANUAL_ENTRY_REQUIRED')
    entry = doc['checks'][0]
    entry.update(override_reason=OVERRIDE, entered_by=entered_by, entered_at=capture['finished_at'],
        started_at=capture['started_at'], finished_at=capture['finished_at'], attachments=[attachment] if attachment else [],
        verdicts={'security': 'FAIL' if _known_failure(capture, values) else 'UNKNOWN', 'functional': 'UNKNOWN', 'performance': 'UNKNOWN'},
        basis='정확 POL·선정 Pod 세대와 신선한 요청에 귀속된 strict JSON/predicate 관측만 사용했다. 원문은 저장하지 않았다.',
        limitations=['선정 Pod stdout/error_input 부분 관측. 원본·collector·backup 등 다른 사본 전체 UNKNOWN.',
            'caller_input/private_system 미전송, 실제 연결된 exception dump가 없으면 UNKNOWN.',
            '전체 normal control·모델 추론·기능/성능 미실행. 신고된 정책·입력자 진위는 인증하지 않음.'])
    entry['normal_control']['result'] = 'not_attempted'
    for step in entry['challenges']: step['attempted'] = False
    entry['observation_notes'][0]['answer'] = (
        f"출처={capture['data_origin']}; 선정 stdout의 정제 위반코드={capture['violation_codes']}; "
        f"부분 tokenize 정상={capture['normal_control']['succeeded']}. "
        '원 필드명·본문·원문 hash는 미수집이며 전체 모델 정상 대조/다른 문맥·사본은 UNKNOWN이다.')
    read = any(row['attempted'] and row['binding_verified'] and row['returncode'] == 0 for row in capture['source_reads'])
    global_branches = [branch for branch in values['required_branches']
        if set(values['required_sources']) == {source_id} and branch in capture['branches_tested']]
    observations = {
        'log_coverage_complete': (False, True), 'log_coverage_gaps': (['OBS03_SELECTED_SOURCE_PARTIAL_SCOPE', *capture['gaps']], True),
        'log_source_ids_tested': ([source_id] if read else None, read),
        'log_sources_tested': ({source_id: capture['branches_tested']} if read else None, read),
        'log_required_branches_tested': (global_branches if read else None, read),
        'log_marker_contexts_seen': (capture['marker_contexts_seen'] if read else None, read)}
    for obs in entry['required_observations']:
        value, collected = observations.get(obs['key'], (None, False))
        obs.update(value=value, collected=collected)
    form = output / 'manual-evidence.yaml'
    try:
        with form.open('x', encoding='utf-8') as stream: stream.write(dump_template(doc))
    except OSError:
        issues.append('OBS03_MANUAL_FORM_WRITE_UNAVAILABLE')
    import_evidence(destination, original, form, document=doc)
    as_of = datetime.now(timezone.utc).replace(microsecond=0)
    results = judge_plan(destination, original, plan, as_of=as_of)
    report = build_report(destination, original, plan, as_of=as_of)
    destination.put(report)
    verify_complete(destination)
    refs = _refs(destination)
    export_bundle(destination.root, output / 'evidence.zip')
    restore_bundle(output / 'evidence.zip', output / 'restored')
    restored = Store(output / 'restored')
    replay = judge_plan(restored, original, plan, as_of=as_of)
    rereport = build_report(restored, original, plan, as_of=as_of)
    if (_refs(restored) != refs or [item.ref() for item in replay] != [item.ref() for item in results]
            or rereport.model_dump(exclude={'id', 'created_at', 'content_sha256'}) != report.model_dump(exclude={'id', 'created_at', 'content_sha256'})):
        raise ValueError('OBS03_EXACT_REF_RESTORE_MISMATCH')
    verify_complete(restored)
    if _inventory(source.root) != before or hashlib.sha256(Path(target).read_bytes()).hexdigest() != target_before:
        raise ValueError('OBS03_ORIGINAL_INPUT_CHANGED')
    summary = {'executed': True, 'collector_calls': 1, **binding, 'as_of': as_of.isoformat(),
        'security': next(item.verdicts.security.value for item in results if item.check_id == 'OBS-03'),
        'exact_refs_preserved': True, 'source_unchanged': True, 'inputs_unchanged': True,
        'full_scope_coverage': False, 'issues': issues}
    _write(output / 'summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('store', 'pack', 'target', 'output'): parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--plan', dest='plan_id', required=True)
    parser.add_argument('--source-id', required=True)
    parser.add_argument('--entered-by', required=True)
    parser.add_argument('--execute', action='store_true')
    try:
        print(json.dumps(run(**vars(parser.parse_args(argv))), ensure_ascii=False))
    except Exception:
        parser.exit(1, 'OBS03_LOG_MINIMIZATION_EVIDENCE_UNAVAILABLE\n')


if __name__ == '__main__':
    main()
