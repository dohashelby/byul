"""The paired PLT-12 inventory enters the unchanged product policy and file readers."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from guard.collectors.file import load_policy
from guard.core.select import select
from guard.executors import credential_copy_contract as contract, run_binding
from guard.executors.kubernetes_credential_copy import KubernetesCredentialCopyRecordExecutor, load_inventory
from guard.models import DataOrigin, Gap, canonical_json, load_pack, sha256_hex
from guard.store import Store
from tests.builders import environment, policy
from tools import mvp_credential_copy_inputs as inputs
from tools import mvp_runtime_policy as runtime_policy


def owned_declarations():
    pack = load_pack(runtime_policy.ROOT / 'packs/PACK-BASELINE')
    base = policy(pack.ref(), status='CONFIRMED', confirmed_by='tests', confirmed_at='2026-11-17T00:00:00Z',
                  data_origin=DataOrigin.DECLARED, source_document_sha256=sha256_hex(runtime_policy.AMENDMENT.read_bytes()),
                  values={'PLT-12': {'copy_max_record_delay_ms': 5000}})
    environments = {side: environment(id='ENV-' + side, origin=DataOrigin.DECLARED,
        engine={'name': row['engine'], 'version': row['version'], 'image_digest': row['image']})
        for side, row in inputs.versions().items()}
    source = {
        'schema': inputs.SELECTION_SCHEMA, 'base_policy_ref': base.ref().model_dump(mode='json'),
        'environments': {side: env.ref().model_dump(mode='json') for side, env in environments.items()},
        'started_at': '2026-11-19T03:00:00.125+09:00', 'credential_family_id': '1' * 32,
        'copies': [{'copy_id': '2' * 32, 'generation': 1, 'location_id': '3' * 32,
                    'location_kind': 'FILE', 'recipient_id': '4' * 32}],
    }
    return pack, base, environments, source


def test_materialized_files_pass_the_product_binding(tmp_path, monkeypatch):
    pack, base, environments, source = owned_declarations()
    policy_path = tmp_path / 'base.json'
    policy_path.write_bytes(canonical_json(base.to_dict()))
    paths = {side: tmp_path / f'{side}.json' for side in environments}
    for side, path in paths.items():
        path.write_bytes(canonical_json(environments[side].to_dict()))
    selection = tmp_path / 'selection.json'
    selection.write_bytes(canonical_json(source))
    out = tmp_path / 'out'
    store_path = tmp_path / 'run-store'
    with pytest.raises(ValueError, match='COPY_SELECTION_SOURCE_CHANGED'):
        inputs.prepare(policy_path, paths, selection, '0' * 64, out, store=store_path)
    assert not out.exists() and not store_path.exists()
    digest = sha256_hex(selection.read_bytes())
    manifest = inputs.prepare(policy_path, paths, selection, digest, out, store=store_path)
    assert not manifest['actual_membership_verified'] and not manifest['treatment_observed']
    assert manifest == json.loads((out / 'manifest.json').read_bytes())
    assert all(sha256_hex((out / name).read_bytes()) == digest for name, digest in manifest['files'].items())

    revised = load_policy(out / 'policy.json', pack)
    assert revised.ref().model_dump(mode='json') == manifest['policy_ref']
    assert revised.id == base.id and revised.revision == base.revision + 1
    assert revised.refs[-1] == base.ref() and revised.scope == base.scope
    assert (revised.confirmed_by, revised.confirmed_at) == (base.confirmed_by, base.confirmed_at)
    store = Store.read_only(store_path)
    assert store.resolve(base.ref()) == base and store.resolve(revised.ref()) == revised
    monkeypatch.setattr(runtime_policy, 'utcnow', lambda: revised.created_at + timedelta(seconds=1))
    replay = inputs.prepare(policy_path, paths, selection, digest, tmp_path / 'retry', store=store_path)
    assert replay == manifest and store.revisions(base.id) == [base.revision, revised.revision]
    ordered = [environments[side] for side in ('baseline', 'candidate')]
    assert Gap.NO_POLICY_VALUE in select(pack, ordered, base, mode='update').item('PLT-12').gaps
    item = select(pack, ordered, revised, mode='update').item('PLT-12')
    assert item.selected and Gap.NO_POLICY_VALUE not in item.gaps

    cfg = {'copy_inventory': str(out / 'copy-inventory.json'), 'target': str(tmp_path / 'target.json')}
    producer = tmp_path / 'producer.py'
    producer.write_text('# test fixture\n', encoding='utf-8')
    for side, env in environments.items():
        (tmp_path / 'target.json').write_bytes(canonical_json({'environment_ref': env.ref().model_dump(mode='json')}))
        _, effective = run_binding.effective_values(pack.checks['PLT-12'], cfg, env, revised.values['PLT-12'],
            side=side, attempt_id='test-plt12', issued_at=datetime(2026, 11, 18, 18, tzinfo=timezone.utc),
            run_cfg={'record_session': '5' * 32, 'record_producer': str(producer)})
        KubernetesCredentialCopyRecordExecutor(cfg).validate_policy(pack.checks['PLT-12'], effective)
        inventory = load_inventory(cfg['copy_inventory'], effective['copy_inventory_sha256'], effective['copy_policy_sha256'])
        assert contract.treatments(inventory) == revised.values['PLT-12']['copy_treatments']
        decision = inventory['copies'][0]
        assert decision['required_action'] == 'delete' and decision['retention_exception'] is None
        assert contract.stamp(decision['deadline_at']) - contract.stamp(source['started_at']) == timedelta(seconds=30)


@pytest.mark.parametrize('case,diagnostic', [
    ('wrong-policy', 'MVP_APPROVED_POLICY_REQUIRED'),
    ('scope', 'MVP_APPROVED_POLICY_REQUIRED'),
    ('wrong-fixed-value', 'MVP_APPROVED_POLICY_VALUES_MISMATCH'),
    ('wrong-environment', 'COPY_SELECTION_ENVIRONMENT_MISMATCH'),
    ('empty', 'COPY_SELECTION_INVENTORY_INVALID'),
    ('duplicate', 'COPY_INVENTORY_INVALID'),
    ('credential-value', 'COPY_SELECTION_INVENTORY_INVALID'),
    ('existing-conflict', 'MVP_EXISTING_RUNTIME_VALUES_MISMATCH'),
])
def test_cannot_replace_policy_or_accept_unbound_inventory(case, diagnostic):
    pack, base, environments, source = owned_declarations()
    if case == 'wrong-policy':
        base = base.model_copy(update={'source_document_sha256': '0' * 64}).seal()
    elif case == 'scope':
        base = policy(pack.ref(), status='CONFIRMED', confirmed_by='tests', confirmed_at='2026-11-17T00:00:00Z',
            data_origin=DataOrigin.DECLARED, source_document_sha256=base.source_document_sha256,
            scope={'exclude': ['PLT-12']})
    elif case in {'wrong-fixed-value', 'existing-conflict'}:
        values = deepcopy(base.values)
        if case == 'wrong-fixed-value':
            values['PLT-12']['copy_max_record_delay_ms'] = 1
        else:
            values['PLT-12']['copy_treatments'] = {}
        base = base.model_copy(update={'values': values}).seal()
        source['base_policy_ref'] = base.ref().model_dump(mode='json')
    elif case == 'wrong-environment':
        source['environments']['candidate'] = source['environments']['baseline']
    elif case == 'empty':
        source['copies'] = []
    elif case == 'duplicate':
        source['copies'].append(deepcopy(source['copies'][0]))
    elif case == 'credential-value':
        source['copies'][0]['credential_value'] = 'should never be included'
    with pytest.raises(ValueError, match=diagnostic):
        inputs.materialize(base, pack, environments, source)
