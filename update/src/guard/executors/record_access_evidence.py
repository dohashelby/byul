"""Selected Pod log access -> original OBS-06 DECLARED manual evidence.

The source store is read only. Only a fresh restored copy receives append-only
manual evidence. The fixed collector is imported only after explicit execution.
"""
from __future__ import annotations

import argparse
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

PACK_REF = {'kind': 'PACK', 'id': 'PACK-BASELINE', 'revision': 31,
            'sha256': '54f4f2357533a8b201ad4397d4b182b08b89ee4821ffe67763e3fbecd8f369ac'}
PROFILE = 'kubernetes-owned-pod-log-access/1'
CASES = {'allowed-before': 'authorized_reader', 'unauthorized': 'unauthorized_reader',
         'allowed-after': 'authorized_reader'}
_IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}')
LIMITATIONS = [
    'Selected Pod log read only; original/collector/backup coverage UNKNOWN.',
    'Storage encryption, key/decryption access and independent audit UNKNOWN.',
    'Whole original normal control not attempted; selected read control is recorded separately.',
    'Functional/performance scope UNKNOWN; no PASS submitted.',
    'Subjects are a declared local plan bound to POL Ref; principal policy and entered_by are not authenticated.',
]


def _digest(value):
    return sha256_hex(canonical_json(value))


def _read(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('RECORD_ACCESS_INPUT_SYMLINK')
    with path.open('rb') as stream:
        return strict_json(stream.read(262145), limit=262144)


def _inventory(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(path).rglob('*')) if p.is_file()}


def _write_json(path, value):
    with Path(path).open('xb') as stream:
        stream.write(canonical_json(value))


def _time(value):
    if type(value) is not str:
        raise ValueError('RECORD_ACCESS_CAPTURE_TIME_INVALID')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('RECORD_ACCESS_CAPTURE_TIME_INVALID')
    return result


def preflight(*, store, plan_id, pack, target, subjects, output, entered_by):
    source = Store.read_only(Path(store))
    verify_complete(source)
    plan = source.latest(plan_id)
    if (plan.kind.value != 'PLAN' or plan.mode != Mode.audit
            or len(plan.inputs.environments) != 1):
        raise ValueError('RECORD_ACCESS_AUDIT_SINGLE_REQUIRED')
    original = load_pack(Path(pack))
    stored = get_pack(source, plan.inputs.pack)
    if (original.ref() != plan.inputs.pack or stored.ref() != original.ref()
            or original.ref().model_dump(mode='json') != PACK_REF):
        raise ValueError('RECORD_ACCESS_ORIGINAL_PACK31_REQUIRED')
    check = original.checks['OBS-06']
    if (check.revision != 4 or check.executor is not None or check.judge is not None
            or len(check.required_observations_prose) != 2 or len(check.challenge_steps) != 6
            or not plan.item('OBS-06').selected):
        raise ValueError('RECORD_ACCESS_ORIGINAL_OBS06_REQUIRED')
    environment = source.resolve(plan.inputs.environments[0])
    policy = source.resolve(plan.inputs.policy)
    if policy.pack != original.ref():
        raise ValueError('RECORD_ACCESS_POLICY_PACK_MISMATCH')
    target_value, subject_value = _read(target), _read(subjects)
    validate_capture_target(target_value, environment)
    if (type(subject_value) is not dict
            or set(subject_value) != {'schema', 'environment_ref', 'target_sha256', 'policy_ref', 'subjects'}
            or subject_value['schema'] != 'guard-record-access-subjects/1'
            or subject_value['environment_ref'] != environment.ref().model_dump(mode='json')
            or subject_value['target_sha256'] != _digest(target_value)
            or subject_value['policy_ref'] != plan.inputs.policy.model_dump(mode='json')):
        raise ValueError('RECORD_ACCESS_SUBJECT_PLAN_BINDING_INVALID')
    rows = subject_value['subjects']
    if (type(rows) is not list or len(rows) != 2
            or any(type(row) is not dict or set(row) != {'role', 'username', 'groups'} for row in rows)
            or {row['role'] for row in rows} != {'authorized_reader', 'unauthorized_reader'}):
        raise ValueError('RECORD_ACCESS_DISTINCT_SUBJECTS_REQUIRED')
    for row in rows:
        if (type(row['username']) is not str or _IDENTITY.fullmatch(row['username']) is None
                or type(row['groups']) is not list or not 1 <= len(row['groups']) <= 32
                or any(type(group) is not str or _IDENTITY.fullmatch(group) is None for group in row['groups'])
                or len(set(row['groups'])) != len(row['groups'])):
            raise ValueError('RECORD_ACCESS_EXPLICIT_SUBJECT_REQUIRED')
    if rows[0]['username'] == rows[1]['username']:
        raise ValueError('RECORD_ACCESS_DISTINCT_SUBJECTS_REQUIRED')
    output = Path(output).absolute()
    if (output.exists() or output.is_symlink() or output.resolve().is_relative_to(source.root.resolve())
            or source.root.resolve().is_relative_to(output.resolve())):
        raise ValueError('RECORD_ACCESS_FRESH_EXTERNAL_OUTPUT_REQUIRED')
    if type(entered_by) is not str or not entered_by.strip() or len(entered_by) > 100:
        raise ValueError('RECORD_ACCESS_ENTERED_BY_REQUIRED')
    return source, original, plan, environment, target_value, subject_value


def _codes(value):
    if type(value) is not list:
        return ['RECORD_ACCESS_GAPS_UNAVAILABLE']
    if any(type(item) is not str or not re.fullmatch('[A-Z][A-Z0-9_]{0,99}', item) for item in value):
        return ['RECORD_ACCESS_REDACTED_GAP']
    return sorted(set(value))


def _capture(environment, target, subjects, *, expected_policy_ref, workdir, binary, duration, timeout):
    from guard.executors.kubernetes_record_access import capture_record_access
    return capture_record_access(environment, target, subjects, expected_policy_ref=expected_policy_ref,
                                 workdir=workdir, binary=binary, duration=duration, timeout=timeout)


def _project_capture(raw, environment, target, subjects, policy_ref):
    expected = {'schema': 'guard-record-access-capture/1', 'profile': PROFILE,
                'environment_ref': environment.ref().model_dump(mode='json'),
                'target_sha256': _digest(target), 'subject_plan_sha256': _digest(subjects),
                'policy_ref': policy_ref.model_dump(mode='json')}
    if type(raw) is not dict or any(raw.get(k) != v for k, v in expected.items()):
        raise ValueError('RECORD_ACCESS_CAPTURE_BINDING_INVALID')
    if raw.get('data_origin') not in {'EXAMPLE', 'DECLARED', 'MEASURED'}:
        raise ValueError('RECORD_ACCESS_CAPTURE_ORIGIN_INVALID')
    started, finished = _time(raw.get('started_at')), _time(raw.get('finished_at'))
    if (finished < started or type(raw.get('collection_id')) is not str
            or not re.fullmatch('[A-Za-z0-9._-]{1,100}', raw['collection_id'])):
        raise ValueError('RECORD_ACCESS_CAPTURE_ID_TIME_INVALID')
    rows, seen = [], set()
    if type(raw.get('rows')) is not list or len(raw['rows']) > 3:
        raise ValueError('RECORD_ACCESS_CAPTURE_ROWS_INVALID')
    for row in raw['rows']:
        if (type(row) is not dict or row.get('case_id') not in CASES
                or row['case_id'] in seen or row.get('role') != CASES[row['case_id']]
                or any(type(row.get(k)) is not bool for k in ('attempted', 'identity_verified', 'binding_verified'))
                or row.get('read_verified') is not None and type(row['read_verified']) is not bool
                or row.get('returncode') is not None and type(row['returncode']) is not int
                or row.get('byte_count') is not None and (type(row['byte_count']) is not int
                    or not 0 <= row['byte_count'] <= 16 * 1024**2)):
            raise ValueError('RECORD_ACCESS_CAPTURE_ROWS_INVALID')
        seen.add(row['case_id'])
        rows.append({**{k: row.get(k) for k in ('case_id', 'role', 'attempted', 'identity_verified',
                      'binding_verified', 'read_verified', 'returncode', 'byte_count')},
                     'gaps': _codes(row.get('gaps'))})
    control = raw.get('normal_control') or {}
    cleanup = raw.get('cleanup') or {}
    return {**expected, 'data_origin': raw['data_origin'], 'started_at': started.isoformat(),
            'finished_at': finished.isoformat(), 'collection_id': raw['collection_id'], 'rows': rows,
            'normal_control': {'attempted': control.get('attempted') is True,
                               'succeeded': control.get('succeeded') if type(control.get('succeeded')) is bool else None},
            'unauthorized_read_verified': (raw.get('unauthorized_read_verified')
                if type(raw.get('unauthorized_read_verified')) is bool else None),
            'cleanup': {'complete': cleanup.get('complete') is True,
                        'collection_gap': cleanup.get('collection_gap') is not False},
            'gaps': _codes(raw.get('gaps')), 'full_scope_coverage': False,
            'storage_encryption': 'UNKNOWN', 'independent_audit': 'UNKNOWN'}


def _known_failure(capture):
    return capture['unauthorized_read_verified'] is True and any(
        row['case_id'] == 'unauthorized' and row['attempted'] and row['identity_verified']
        and row['binding_verified'] and row['read_verified'] is True and row['returncode'] == 0
        and type(row['byte_count']) is int and row['byte_count'] > 0
        and not row['gaps'] for row in capture['rows'])


def manual_document(store, pack, plan, capture, *, entered_by, attachment):
    doc = build_template(store, pack, plan)
    doc['checks'] = [entry for entry in doc['checks']
                     if entry['check_id'] == 'OBS-06' and entry['side'] == 'single']
    if len(doc['checks']) != 1:
        raise ValueError('RECORD_ACCESS_OBS06_SINGLE_ENTRY_REQUIRED')
    entry = doc['checks'][0]
    entry['verdicts'] = {'security': 'FAIL' if _known_failure(capture) else 'UNKNOWN',
                         'functional': 'UNKNOWN', 'performance': 'UNKNOWN'}
    entry['normal_control']['result'] = 'not_attempted'
    for index, challenge in enumerate(entry['challenges']):
        challenge['attempted'] = index == 1 and any(
            row['case_id'] == 'unauthorized' and row['attempted'] for row in capture['rows'])
    entry['observation_notes'][0]['answer'] = (
        f"Selected Pod log read only; capture origin={capture['data_origin']}; "
        f"unauthorized_read_verified={capture['unauthorized_read_verified']}; "
        f"selected_read_control={capture['normal_control']['succeeded']}. "
        'Whole original normal control not attempted. Subject policy is declared; '
        'original/collector/backup copies and independent audit UNKNOWN.')
    entry['observation_notes'][1]['answer'] = 'UNKNOWN: storage encryption requirements, configuration and decryption roles not collected.'
    entry['basis'] = ('Confirmed selected unauthorized identity read nonempty Pod logs; direct access violation.'
                      if _known_failure(capture) else 'Selected access evidence is incomplete or no confirmed unauthorized read; UNKNOWN.')
    entry['limitations'] = list(LIMITATIONS)
    entry['entered_by'] = entered_by
    entry['entered_at'] = capture['finished_at']
    entry['started_at'], entry['finished_at'] = capture['started_at'], capture['finished_at']
    entry['attachments'] = [attachment] if attachment else []
    return doc


def _refs(store):
    return sorted((obj.kind.value, obj.id, obj.revision, obj.content_sha256)
                  for kind in ('ENV', 'POL', 'CHG', 'PLAN', 'EVD', 'RES', 'RPT') for obj in store.iter_kind(kind))


def run(*, store, plan_id, pack, target, subjects, output, entered_by, execute=False,
        binary='kubectl', duration=60, timeout=5):
    source, original, plan, environment, target_value, subject_value = preflight(
        store=store, plan_id=plan_id, pack=pack, target=target, subjects=subjects,
        output=output, entered_by=entered_by)
    binding = {'plan': plan.ref().model_dump(mode='json'), 'pack': original.ref().model_dump(mode='json'),
               'policy': plan.inputs.policy.model_dump(mode='json'),
               'environment': environment.ref().model_dump(mode='json'),
               'target_sha256': _digest(target_value), 'subject_plan_sha256': _digest(subject_value)}
    if not execute:
        return {'preflight': True, 'executed': False, 'collector_calls': 0, **binding}
    before = _inventory(source.root)
    input_before = {str(Path(p)): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (target, subjects)}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    export_bundle(source.root, output / 'source.zip')
    restore_bundle(output / 'source.zip', output / 'store')
    destination = Store(output / 'store')
    capture = _project_capture(_capture(environment, target_value, subject_value,
        expected_policy_ref=plan.inputs.policy, workdir=output / 'capture', binary=binary,
        duration=duration, timeout=timeout), environment, target_value, subject_value, plan.inputs.policy)
    issues = []
    attachment = 'record-access.json'
    try:
        _write_json(output / attachment, capture)
    except OSError:
        attachment = None
        issues.append('RECORD_ACCESS_ARTIFACT_WRITE_UNAVAILABLE')
    doc = manual_document(destination, original, plan, capture, entered_by=entered_by, attachment=attachment)
    form_path = output / 'manual-evidence.yaml'
    try:
        with form_path.open('x', encoding='utf-8') as stream:
            stream.write(dump_template(doc))
    except OSError:
        issues.append('RECORD_ACCESS_FORM_WRITE_UNAVAILABLE')
    # The common importer accepts a prevalidated in-memory document; even a
    # missing attachment/form cannot erase a confirmed independent FAIL.
    import_evidence(destination, original, form_path, document=doc)
    as_of = datetime.now(timezone.utc).replace(microsecond=0)
    results = judge_plan(destination, original, plan, as_of=as_of)
    report = build_report(destination, original, plan, as_of=as_of)
    destination.put(report)
    verify_complete(destination)
    refs = _refs(destination)
    export_bundle(destination.root, output / 'evidence.zip')
    restore_bundle(output / 'evidence.zip', output / 'restored')
    restored = Store(output / 'restored')
    if _refs(restored) != refs:
        raise ValueError('RECORD_ACCESS_RESTORED_REFS_MISMATCH')
    rejudged = judge_plan(restored, original, plan, as_of=as_of)
    rereport = build_report(restored, original, plan, as_of=as_of)
    report_fields = {'id', 'created_at', 'content_sha256'}
    if ([result.ref() for result in rejudged] != [result.ref() for result in results]
            or rereport.model_dump(exclude=report_fields) != report.model_dump(exclude=report_fields)):
        raise ValueError('RECORD_ACCESS_RESTORED_JUDGMENT_MISMATCH')
    verify_complete(restored)
    source_unchanged = _inventory(source.root) == before
    inputs_unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
                           for path, digest in input_before.items())
    if not source_unchanged or not inputs_unchanged:
        raise ValueError('RECORD_ACCESS_ORIGINAL_INPUT_CHANGED')
    summary = {'executed': True, 'collector_calls': 1, **binding, 'as_of': as_of.isoformat(),
               'report': report.ref().model_dump(mode='json'), 'exact_refs_preserved': True,
               'source_unchanged': source_unchanged, 'inputs_unchanged': inputs_unchanged,
               'security': next(result.verdicts.security.value for result in results if result.check_id == 'OBS-06'),
               'storage_encryption': 'UNKNOWN', 'full_scope_coverage': False, 'issues': issues}
    _write_json(output / 'summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('store', 'pack', 'target', 'subjects', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--plan', dest='plan_id', required=True)
    parser.add_argument('--entered-by', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(**vars(args)), ensure_ascii=False))
    except Exception:
        # kubectl output, principals, file paths and exception bodies are not output channels.
        parser.exit(1, 'RECORD_ACCESS_EVIDENCE_UNAVAILABLE\n')


if __name__ == '__main__':
    main()
