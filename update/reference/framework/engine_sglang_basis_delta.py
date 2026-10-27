#!/usr/bin/env python3
"""Compare the historical SGLang profile criteria basis to a pinned current checklist.

This compares JSON and historical hashes. It does not re-review engine behavior.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / '엔진별_적용검토/SGLang_v0.5.22.json'
OUTPUT = ROOT / '엔진별_적용검토/SGLang_v0.5.22_기준차이_20261027.json'
CURRENT_SHA = 'cf4903efe9a60543ceff5276a09ac2c40bb16d54658b21e5deafb03c948f8da7'
GROUPS = ['supply_assessment_draft', 'request_state_assessment_draft', 'platform_assessment_draft', 'lifecycle_assessment_draft', 'observability_assessment_draft']
HASH_FIELDS = ['applies_when', 'satisfied_if', 'fail_criteria', 'unknown_criteria', 'test_branch_conditions']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def criteria_hash(check):
    return sha(json.dumps({k: check[k] for k in HASH_FIELDS}, ensure_ascii=False, sort_keys=True).encode())


def build_delta(old_path, current_path):
    profile = json.loads(PROFILE.read_text())
    old_bytes, new_bytes = Path(old_path).read_bytes(), Path(current_path).read_bytes()
    if sha(old_bytes) != profile['canonical_sha256'] or sha(new_bytes) != CURRENT_SHA:
        raise ValueError('historical/current checklist pin mismatch')
    old, new = json.loads(old_bytes), json.loads(new_bytes)
    old_checks = {c['id']: c for g in GROUPS for c in old[g]}
    new_checks = {c['id']: c for g in GROUPS for c in new[g]}
    if set(old_checks) != set(new_checks) or len(new_checks) != 77:
        raise ValueError('check ID set changed')
    byid = {c['check_id']: c for c in profile['checks']}
    rows = []
    for group in GROUPS:
        for index, old_check in enumerate(old[group]):
            cid = old_check['id']
            current = new_checks[cid]
            historical_hash, current_hash = criteria_hash(old_check), criteria_hash(current)
            if byid[cid]['criteria_sha256'] != historical_hash:
                raise ValueError(f'historical profile hash mismatch: {cid}')
            changed = [k for k in sorted(set(old_check) | set(current)) if old_check.get(k) != current.get(k)]
            if changed:
                rows.append({'check_id': cid, 'json_pointer': f'/{group}/{index}', 'changed_fields': changed,
                             'historical_criteria_sha256': historical_hash, 'current_criteria_sha256': current_hash,
                             'historical_hash_fields_changed': historical_hash != current_hash,
                             'criteria_field_deltas': {k: {'old': old_check[k], 'current': current[k]} for k in HASH_FIELDS if old_check[k] != current[k]},
                             'engine_mapping_revalidation_status': 'pending_for_selected_environment'})
    return {'schema_version': '1.0', 'kind': 'engine_profile_criteria_basis_delta', 'reviewed_at': '2026-10-27',
            'historical_framework_commit': profile['framework_commit'], 'historical_checklist_sha256': sha(old_bytes),
            'current_basis_framework_commit': 'c67d03a78115d0996dfb5b92e9231429a6aa703b', 'current_checklist_sha256': sha(new_bytes),
            'method': 'json_field_and_historical_profile_hash_comparison', 'historical_hash_fields': HASH_FIELDS,
            'check_ids_preserved': 77, 'changed_check_records': len(rows),
            'unchanged_check_records': 77-len(rows), 'changed_historical_hashes': sum(r['historical_hash_fields_changed'] for r in rows),
            'changed_test_branch_conditions': [r['check_id'] for r in rows if 'test_branch_conditions' in r['changed_fields']],
            'scope': 'criteria-basis comparison only; historical profile rows/hashes unchanged; no engine/runtime revalidation',
            'changes': rows}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--historical-checklist', required=True, type=Path)
    p.add_argument('--current-checklist', type=Path, default=ROOT/'data/checklist.json')
    p.add_argument('--output', type=Path, default=OUTPUT)
    p.add_argument('--check', action='store_true')
    a = p.parse_args()
    d = build_delta(a.historical_checklist, a.current_checklist)
    rendered = json.dumps(d, ensure_ascii=False, indent=2)+'\n'
    if a.check:
        if a.output.read_text() != rendered:
            raise SystemExit('stored criteria delta differs')
    else:
        a.output.write_text(rendered)
    print(json.dumps({k: d[k] for k in ['method', 'check_ids_preserved', 'changed_check_records', 'changed_historical_hashes', 'changed_test_branch_conditions']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
