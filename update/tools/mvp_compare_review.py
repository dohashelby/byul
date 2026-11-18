"""Review the security UNKNOWN limit from original saved update RPTs and stores."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

from guard.core.select import _in_scope, evaluate_check
from guard.models import Ref, load_pack
from guard.models.objects import object_from_dict
from guard.update_acceptance import assess_update
from guard.validation_evidence import read_objects, verify_store_files
from tools.mvp_versions import versions

ROOT = Path(__file__).resolve().parents[1]
SIDES = ('baseline', 'candidate')
UNKNOWN_LIMIT = 6


def _dump(ref):
    return ref.model_dump(mode='json')


def _verified_rows(audit, objects, pack, expected):
    by_ref = {obj.ref().sha256: obj for obj in map(object_from_dict, objects)}

    def resolve(ref):
        obj = by_ref[ref.sha256]
        if obj.ref() != ref:
            raise ValueError('STORED_REF_MISMATCH')
        return obj

    plan = resolve(Ref.model_validate(audit['refs']['plan']))
    report = resolve(Ref.model_validate(audit['refs']['report']))
    policy = resolve(plan.inputs.policy)
    envs = [resolve(ref) for ref in plan.inputs.environments]
    for side, env in zip(SIDES, envs, strict=True):
        engine = expected[side]
        if (env.engine.name, env.engine.version, env.engine.image_digest) != (
                engine['engine'], engine['version'], engine['image']):
            raise ValueError('ENGINE_PAIR_MISMATCH')
    rows = {}
    base, cand = envs
    model_keys = ('id', 'revision', 'inventory_sha256', 'tokenizer_revision', 'components')
    differences = ['model.' + key for key in model_keys if getattr(base.model, key) != getattr(cand.model, key)]
    if base.runtime != cand.runtime:
        differences.append('runtime')
    paired_envs = dict(zip(SIDES, map(_dump, plan.inputs.environments), strict=True))
    for check_id, pair in audit['checks'].items():
        item = plan.item(check_id)
        rows[check_id] = {
            'selected': True,
            'comparison': pair['comparison'], 'functional_comparison': pair['functional_comparison'],
            'comparison_reasons': [*pair['comparison_reasons'], *differences],
            'applicability': item.applicability.value, 'selection_gaps': [gap.value for gap in item.gaps],
            **{side: {'environment': paired_envs[side], 'evidence': pair[side]['evidence'],
                      'result': pair[side]['result'], 'verdicts': pair[side]['actual']}
               for side in SIDES},
        }
    report_rows = {row.check_id: row for row in report.by_check}
    for item in plan.items:
        if item.not_selected_reason != 'NOT_APPLICABLE' or item.check_id not in pack.checks:
            continue
        check = pack.checks[item.check_id]
        row = report_rows.get(item.check_id)
        if (row is None or row.selected or row.applicability != 'NOT_APPLICABLE' or row.gaps
                or row.baseline is not None or row.candidate is not None or row.single is not None
                or item.applicability != 'NOT_APPLICABLE' or item.gaps or item.check_revision != check.revision
                or item.executor != check.executor or not _in_scope(item.check_id, policy)):
            continue
        evaluations = [evaluate_check(check, [env]) for env in envs]
        if (item.conditions == [condition for _, conditions, _ in evaluations for condition in conditions]
                and all(applicability == 'NOT_APPLICABLE'
                        and any(c.result == 'FALSE' and c.origin in ('MEASURED', 'DECLARED') for c in conditions)
                        for applicability, conditions, _ in evaluations)):
            rows[item.check_id] = {'selected': False, 'applicability': 'NOT_APPLICABLE', **{
                side: {'environment': paired_envs[side],
                       'conditions': [{'fact': c.fact, 'origin': c.origin, 'result': c.result} for c in conditions]}
                for side, (_, conditions, _) in zip(SIDES, evaluations, strict=True)}}
    return rows


def review(run_inputs, *, pack, expected):
    """Aggregate immutable observations without changing strict execution acceptance."""
    active = [check.id for check in pack.active_checks()]
    if len(active) != len(set(active)):
        raise ValueError('ACTIVE_CHECK_IDS_DUPLICATED')
    reviews, policies = [], []
    by_check = defaultdict(list)
    for report, objects, original_pack in run_inputs:
        if original_pack.ref() != pack.ref():
            reviews.append({'verified': False, 'reason': 'PACK_REF_MISMATCH'})
            continue
        audit = assess_update(report, objects, original_pack, profile='plan-scope/2')
        if not audit['evidence_contract_valid']:
            reviews.append({'verified': False, 'reason': 'EVIDENCE_CONTRACT_INVALID'})
            continue
        try:
            rows = _verified_rows(audit, objects, pack, expected)
        except ValueError as error:
            if str(error) not in {'ENGINE_PAIR_MISMATCH', 'STORED_REF_MISMATCH'}:
                raise
            reviews.append({'verified': False, 'reason': str(error), 'refs': audit['refs']})
            continue
        policies.append(audit['refs']['policy'])
        reviews.append({'verified': True, 'official': audit['official'], 'refs': audit['refs']})
        for check_id, row in rows.items():
            by_check[check_id].append({**row, 'report': audit['refs']['report']})

    inventory, unknown, unresolved = {}, [], []
    for check_id in sorted(active):
        candidates = by_check[check_id]
        if len(candidates) != 1:
            status = 'ORIGINAL_ROW_MISSING' if not candidates else 'ORIGINAL_ROW_AMBIGUOUS'
            inventory[check_id] = {'status': status, 'reports': [row['report'] for row in candidates]}
            unresolved.append(check_id)
            continue
        row, = candidates
        if not row['selected']:
            inventory[check_id] = {'status': 'SELECTION_VERIFIED', **row}
            continue
        unknown_sides = [side for side in SIDES if row[side]['verdicts']['security'] == 'UNKNOWN']
        if unknown_sides:
            unknown.append(check_id)
        blockers = []
        if row['comparison_reasons'] or 'NOT_COMPARABLE' in (row['comparison'], row['functional_comparison']):
            blockers.append('COMPARISON_UNRESOLVED')
        if row['applicability'] != 'APPLICABLE' or row['selection_gaps']:
            blockers.append('SELECTION_UNRESOLVED')
        if blockers:
            unresolved.append(check_id)
        inventory[check_id] = {'status': 'MEASURED_ROWS_VERIFIED', **row,
                               'security_unknown_sides': unknown_sides, 'blocking_codes': blockers}

    exact_policy = bool(policies) and all(policy == policies[0] for policy in policies)
    verified_inputs = bool(reviews) and all(report['verified'] for report in reviews)
    complete = not unresolved and exact_policy and verified_inputs
    return {
        'schema': 'guard-mvp-compare-review/1', 'pack_ref': _dump(pack.ref()),
        'policy_ref': policies[0] if exact_policy else None, 'active_checks': len(active), 'reports': reviews,
        'unknown_security_check_ids': unknown, 'unknown_security_count': len(unknown),
        'unknown_security_limit': UNKNOWN_LIMIT, 'unresolved_check_ids': unresolved,
        'comparison_complete': complete, 'unknown_security_limit_met': complete and len(unknown) <= UNKNOWN_LIMIT,
        'all_reports_official': verified_inputs and all(report['official'] for report in reviews), 'rows': inventory,
    }


def _read_runs(pairs):
    for store, report_path in pairs:
        objects = read_objects(store)
        yield json.loads(report_path.read_bytes()), objects, verify_store_files(store, objects)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', metavar=('RESTORED_STORE', 'ORIGINAL_REPORT'), nargs=2,
                        action='append', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    if len(pack.active_checks()) != 69:
        raise ValueError('MVP_ACTIVE_SCOPE_MISMATCH')
    if any(args.output.resolve().is_relative_to(store.resolve()) for store, _ in args.run):
        parser.error('--output must be outside the restored stores')
    result = review(_read_runs(args.run), pack=pack, expected=versions())
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
