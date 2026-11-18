"""Read inherited decisions and fill declared runtime values without changing policy rules."""

from pathlib import Path

from guard.core.select import _in_scope
from guard.models import DataOrigin, PolicyStatus, sha256_hex, utcnow
from guard.runtime.bounded import strict_json

ROOT = Path(__file__).resolve().parents[1]
AMENDMENT = ROOT / 'docs/dev-1/mvp-enterprise-policy-20261118.json'
BASE_DECISIONS = ROOT / 'docs/dev-1/mvp-enterprise-policy-20261116.json'
POLICY_MAX_BYTES = 256 * 1024


def approved_rules(policy, pack, check_id):
    amendment_raw = AMENDMENT.read_bytes()
    amendment = strict_json(amendment_raw)
    base_raw = BASE_DECISIONS.read_bytes()
    base = strict_json(base_raw, limit=POLICY_MAX_BYTES)
    if (not policy.hash_ok() or policy.pack != pack.ref() or policy.status != PolicyStatus.CONFIRMED
            or policy.data_origin != DataOrigin.DECLARED or not _in_scope(check_id, policy)
            or policy.source_document_sha256 != sha256_hex(amendment_raw)
            or amendment['status'] != 'CONFIRMED' or amendment['pack_ref'] != policy.pack.model_dump(mode='json')
            or base['status'] != 'CONFIRMED' or amendment['base_document']['id'] != base['id']
            or amendment['base_document']['file_sha256'] != sha256_hex(base_raw)):
        raise ValueError('MVP_APPROVED_POLICY_REQUIRED')
    decisions = {row['key']: row['value'] for row in [*base['decisions'], *amendment['decisions']]
                 if row['check_id'] == check_id}
    rules = {row['key']: row['rule'] for row in base['runtime_materialization_required'] if row['check_id'] == check_id}
    fixed = {key: value for key, value in decisions.items() if key not in rules}
    existing = {key: value for key, value in policy.values.get(check_id, {}).items() if key not in rules}
    if not rules or any(decisions.get(key) != value for key, value in rules.items()) or fixed != existing:
        raise ValueError('MVP_APPROVED_POLICY_VALUES_MISMATCH')
    return rules


def fill_runtime_values(policy, check_id, materialized):
    values = policy.values[check_id]
    if any(key in values and values[key] != value for key, value in materialized.items()):
        raise ValueError('MVP_EXISTING_RUNTIME_VALUES_MISMATCH')
    if materialized.keys() <= values.keys():
        return policy
    return policy.model_copy(update={'revision': policy.revision + 1, 'created_at': utcnow(),
        'refs': [*policy.refs, policy.ref()],
        'values': {**policy.values, check_id: {**values, **materialized}}}).seal()
