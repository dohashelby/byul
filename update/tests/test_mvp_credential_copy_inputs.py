"""The paired PLT-12 inventory enters the unchanged product policy and file readers."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
import tomllib

import pytest

from guard.collectors.file import load_policy
from guard.core.select import select
from guard.executors import credential_copy_contract as contract, run_binding
from guard.executors.kubernetes_credential_copy import KubernetesCredentialCopyRecordExecutor, load_inventory
from guard.models import DataOrigin, Environment, Gap, canonical_json, load_pack, sha256_hex
from guard.store import Store
from tests.builders import environment, policy
from tests.test_mvp_metadata_wiring import sources
from tools import mvp_credential_copy_inputs as inputs
from tools import mvp_metadata_wiring as wiring
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


def test_prebound_publication_rechecks_copy_record_pins_and_preserves_staged_index(tmp_path, monkeypatch, sources):
    def forbidden(*args, **kwargs):
        pytest.fail('PLT-12 declaration preparation must not contact a workload or run a producer')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    pack, sides, _ = sources
    _, base, _, selection = owned_declarations()
    base = base.model_copy(update={'scope': base.scope.model_copy(update={'include': ['PLT-12']})}).seal()
    env_paths = {side: files['environment'] for side, files in sides.items()}
    selection.update(base_policy_ref=base.ref().model_dump(mode='json'), environments={
        side: Environment.model_validate_json(path.read_bytes()).ref().model_dump(mode='json')
        for side, path in env_paths.items()})
    base_path, selection_path = tmp_path / 'base.json', tmp_path / 'selection.json'
    base_path.write_bytes(canonical_json(base.to_dict()))
    selection_path.write_bytes(canonical_json(selection))
    prepared = tmp_path / 'prepared-copy'
    manifest = inputs.prepare(base_path, env_paths, selection_path, sha256_hex(selection_path.read_bytes()), prepared)
    policy_path = prepared / 'policy.json'

    declarations, pinned = {}, {}
    for number, side in enumerate(sides, 1):
        producer = tmp_path / f'{side}-producer.txt'
        producer.write_bytes(b'# inert EXAMPLE producer bytes\n')
        inventory = tmp_path / f'{side}-inventory.json'
        inventory.write_bytes((prepared / 'copy-inventory.json').read_bytes())
        declarations[side] = {'record_session': f'{number:032x}', 'record_producer': str(producer),
                              'copy_inventory': str(inventory)}
        pinned[side] = {**declarations[side], 'input_sha256': {
            'copy_inventory_sha256': manifest['files']['copy-inventory.json'],
            'copy_record_source_sha256': sha256_hex(producer.read_bytes())}}

    wrong = deepcopy(pinned)
    wrong['candidate']['input_sha256']['copy_inventory_sha256'] = '0' * 64
    store = tmp_path / 'store'
    config, rows, decisions = wiring.assemble(sides, policy_path, pack=pack, store=store,
                                             copy_record_inputs=wrong, prebound_copy_records=True)
    assert not config['update']['allowed_checks'] and not rows
    assert decisions['PLT-12']['sides']['candidate'] == 'METADATA_COPY_RECORD_INPUT_SHA256_MISMATCH'

    pinned_path = tmp_path / 'pinned-inputs.json'
    pinned_path.write_bytes(canonical_json(pinned))
    args = ['mvp_metadata_wiring', '--policy', str(policy_path), '--store', str(store)]
    for side, files in sides.items():
        for role, path in files.items():
            args.extend([f'--{side}-{role}', str(path)])
    prebound = tmp_path / 'prebound'
    monkeypatch.setattr(sys, 'argv', [*args, '--output', str(prebound), '--copy-record-inputs', str(pinned_path)])
    wiring.main()
    index = prebound / 'input-sources.json'
    replay = wiring.readiness.audit(index, pack=pack)
    assert replay['file_binding_valid_sides'] == 2
    assert replay['actual_results'] is None and not replay['engine_executed']
    for row in json.loads(index.read_bytes())['inputs']:
        side = row['side']
        assert row['record_inputs'] == {
            **pinned[side]['input_sha256'], 'target_file_sha256': sha256_hex(sides[side]['target'].read_bytes()),
            'record_session_sha256': sha256_hex(declarations[side]['record_session'].encode('ascii'))}
        checked = next(item for item in replay['rows'] if (item['check_id'], item['side']) == ('PLT-12', side))
        assert checked['record_inputs'] == row['record_inputs']

    for role in ('copy_inventory', 'record_producer'):
        path = Path(declarations['candidate'][role])
        raw = path.read_bytes()
        path.write_bytes(raw + b'\n')
        changed = wiring.readiness.audit(index, pack=pack)
        path.write_bytes(raw)
        assert changed['file_binding_valid_sides'] == 1
        candidate = next(row for row in changed['rows'] if (row['check_id'], row['side']) == ('PLT-12', 'candidate'))
        assert not candidate['file_binding_valid'] and 'MVP_INPUT_FILE_CHANGED' in candidate['blocking_codes']

    paths_only = tmp_path / 'paths-only.json'
    paths_only.write_bytes(canonical_json(declarations))
    staged = tmp_path / 'staged'
    monkeypatch.setattr(sys, 'argv', [*args, '--output', str(staged), '--copy-record-inputs', str(paths_only),
        '--candidate-target-after-baseline', str(tmp_path / 'future-target.json')])
    wiring.main()
    stage_config = tomllib.loads((staged / 'guard.toml').read_text(encoding='utf-8'))
    assert stage_config['update'] == {'mode': 'external', 'allowed_checks': ['PLT-12'],
                                       'kubernetes_candidate_binding': 'after_baseline'}
    items = json.loads((staged / 'input-sources.json').read_bytes())['inputs']
    assert len(items) == 2 and all('record_inputs' not in row for row in items)
    assert all(Path(row['configuration']['path']) == staged / 'preliminary-guard.toml' for row in items)


def test_prebound_copy_producer_uses_the_existing_binding_size_limit(tmp_path):
    pack, policy, environments, selection = owned_declarations()
    _, inventory = inputs.materialize(policy, pack, environments, selection)
    inventory_path = tmp_path / 'copy-inventory.json'
    inventory_bytes = canonical_json(inventory)
    inventory_path.write_bytes(inventory_bytes)
    producer = tmp_path / 'EXAMPLE-producer.bytes'
    with producer.open('xb') as stream:
        stream.truncate(8 * 1024**2 + 1)
    producer_sha256 = run_binding.producer_sha256(str(producer))
    declarations = {side: {'record_session': f'{number:032x}', 'copy_inventory': str(inventory_path),
                           'record_producer': str(producer)} for number, side in enumerate(environments, 1)}
    pinned = {side: {**row, 'input_sha256': {'copy_inventory_sha256': sha256_hex(inventory_bytes),
                                             'copy_record_source_sha256': producer_sha256}}
              for side, row in declarations.items()}

    assert wiring._paired_record_inputs(pinned, 'PLT-12', prebound_copy_records=True) == pinned
    with pytest.raises(ValueError, match='MA7_INPUT_FILE_INVALID'):
        wiring._paired_record_inputs(declarations, 'PLT-12')

    with producer.open('r+b') as stream:
        stream.truncate(run_binding._MAX_PRODUCER_BYTES + 1)
    with pytest.raises(ValueError, match='MA7_INPUT_FILE_INVALID'):
        wiring._paired_record_inputs(pinned, 'PLT-12', prebound_copy_records=True)
    with pytest.raises(run_binding.RunBindingUnavailable, match='RUN_BINDING_FILE_UNAVAILABLE'):
        run_binding.producer_sha256(str(producer))


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
