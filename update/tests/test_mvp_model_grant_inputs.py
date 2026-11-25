from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from guard.core.select import select
from guard.executors import run_binding
from guard.executors.kubernetes_model_authorization import normalize_policy
from guard.models import DataOrigin, Gap, canonical_json, load_pack, sha256_hex
from guard.store import Store
from tests.builders import environment, policy
from tools import mvp_model_grant_inputs as inputs
from tools.mvp_runtime_policy import AMENDMENT, BASE_DECISIONS, ROOT


def declarations():
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    decisions = json.loads(BASE_DECISIONS.read_bytes())['decisions']
    values = {row['key']: row['value'] for row in decisions if row['check_id'] == 'REQ-05' and row['key'] != inputs.GRANT}
    base = policy(pack.ref(), status='CONFIRMED', confirmed_by='tests', confirmed_at='2026-11-17T00:00:00Z',
        data_origin=DataOrigin.DECLARED, source_document_sha256=sha256_hex(AMENDMENT.read_bytes()), values={'REQ-05': values})
    model = {'id': 'Qwen/Qwen2.5-0.5B-Instruct', 'revision': '7ae557604adf67be50417f59c2c2f167def9a775',
             'inventory_sha256': 'a' * 64}
    envs, targets, binding = {}, {}, {}
    for side, row in inputs.versions().items():
        env = environment(id=f'ENV-{side}', origin=DataOrigin.DECLARED, model=model,
            engine={'name': row['engine'], 'version': row['version'], 'image_digest': row['image']},
            facts={'gateway': {'value': True, 'source': 'owned-gateway-inventory', 'origin': 'DECLARED'}})
        ref = env.ref().model_dump(mode='json')
        target = {'environment_ref': ref, 'model_mapping': {'schema': 'guard.model-request-mapping/1',
            'environment_ref': ref, 'repository_id': model['id'], 'revision': model['revision'],
            'served_model_name': 'owned-normal-model', 'host_snapshot': '/owned/snapshot',
            'container_mount': '/owned/model', 'inventory_sha256': model['inventory_sha256']}}
        envs[side], targets[side] = env, target
        binding[side] = {'environment_ref': ref, 'target_sha256': sha256_hex(canonical_json(target)),
                         'gateway_policy_sha256': 'b' * 64, 'principal_sha256': values['allowed_principal_sha256'],
                         'served_model_name': 'owned-normal-model'}
    return pack, base, envs, targets, {'schema': inputs.SCHEMA, 'base_policy_ref': base.ref().model_dump(mode='json'),
                                     'sides': binding}


def test_materialized_grant_enters_existing_plan_and_policy_validator(tmp_path):
    pack, base, envs, targets, receipt = declarations()
    revised = inputs.materialize(base, pack, envs, targets, receipt)
    ordered = [envs[side] for side in ('baseline', 'candidate')]
    assert Gap.NO_POLICY_VALUE in select(pack, ordered, base, mode='update').item('REQ-05').gaps
    assert Gap.NO_POLICY_VALUE not in select(pack, ordered, revised, mode='update').item('REQ-05').gaps
    assert revised.hash_ok() and revised.refs[-1] == base.ref()
    assert revised.values['REQ-05']['retired_model_revisions'] == base.values['REQ-05']['retired_model_revisions']
    for side, env in envs.items():
        target = tmp_path / f'{side}.json'
        target.write_bytes(canonical_json(targets[side]))
        _, effective = run_binding.effective_values(pack.checks['REQ-05'], {'target': str(target)}, env,
            revised.values['REQ-05'], side=side, attempt_id='test-req05',
            issued_at=datetime(2026, 11, 18, 18, tzinfo=timezone.utc))
        _, _, grants, _ = normalize_policy(effective)
        assert grants == {(base.values['REQ-05']['allowed_principal_sha256'], sha256_hex(env.model.id.encode()),
                           sha256_hex(env.model.revision.encode()))}


@pytest.mark.parametrize(('change', 'error'), [
    (None, None),
    ('replaced-with-identical-bytes', 'MVP_MODEL_GRANT_SOURCE_CHANGED'),
    ('other-gateway-policy', 'MVP_MODEL_GRANT_GATEWAY_POLICY_PAIR_MISMATCH'),
], ids=['unchanged', 'replaced-with-identical-bytes', 'other-gateway-policy'])
def test_prepare_checks_saved_source_identity_before_writing(tmp_path, monkeypatch, change, error):
    _, base, envs, targets, receipt = declarations()

    def write(name, value):
        path = tmp_path / name
        path.write_bytes(canonical_json(value))
        return path

    policy_path = write('policy.json', base.to_dict())
    if change == 'other-gateway-policy':
        receipt['sides']['candidate']['gateway_policy_sha256'] = sha256_hex(b'other-runtime-policy-fixture')
    inventory_path = write('inventory.json', receipt)
    environment_paths = {side: write(f'{side}-env.json', env.to_dict()) for side, env in envs.items()}
    target_paths = {side: write(f'{side}-target.json', target) for side, target in targets.items()}
    if change == 'replaced-with-identical-bytes':
        materialize = inputs.materialize

        def replace_after_materialization(*args):
            revised = materialize(*args)
            source = target_paths['candidate']
            replacement = tmp_path / 'replacement.json'
            replacement.write_bytes(source.read_bytes())
            replacement.replace(source)
            return revised

        monkeypatch.setattr(inputs, 'materialize', replace_after_materialization)

    selection_pin = sha256_hex(inventory_path.read_bytes())
    output, store = tmp_path / 'revised.json', tmp_path / 'store'
    arguments = policy_path, environment_paths, target_paths, inventory_path, selection_pin, output
    if error:
        with pytest.raises(ValueError, match=f'^{error}$'):
            inputs.prepare(*arguments, store=store)
        assert not output.exists() and not store.exists()
    else:
        ref = inputs.prepare(*arguments, store=store)
        revised = Store.read_only(store).resolve(ref)
        assert output.read_bytes() == canonical_json(revised.to_dict())
        assert revised.refs[-1] == base.ref()


@pytest.mark.parametrize('field,value,diagnostic', [
    ('principal_sha256', 'c' * 64, 'MVP_MODEL_GRANT_MAPPING_MISMATCH'),
    ('target_sha256', 'f' * 64, 'MVP_MODEL_GRANT_MAPPING_MISMATCH'),
    ('served_model_name', 'another-model', 'MVP_MODEL_GRANT_ALIAS_MISMATCH'),
])
def test_unbound_gateway_receipt_cannot_change_approved_grant(field, value, diagnostic):
    pack, base, envs, targets, receipt = declarations()
    receipt['sides']['candidate'][field] = value
    with pytest.raises(ValueError, match=diagnostic):
        inputs.materialize(base, pack, envs, targets, receipt)


def test_direct_environment_cannot_masquerade_as_gateway():
    pack, base, envs, targets, receipt = declarations()
    candidate = envs['candidate'].model_copy(update={'facts': {}}).seal()
    envs['candidate'] = candidate
    receipt['sides']['candidate']['environment_ref'] = candidate.ref().model_dump(mode='json')
    target = deepcopy(targets['candidate'])
    target['environment_ref'] = receipt['sides']['candidate']['environment_ref']
    targets['candidate'] = target
    with pytest.raises(ValueError, match='MVP_MODEL_GRANT_GATEWAY_ENVIRONMENT_REQUIRED'):
        inputs.materialize(base, pack, envs, targets, receipt)
