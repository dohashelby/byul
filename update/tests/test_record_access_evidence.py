"""Original PACK31/manual store integration; one EXAMPLE capture, Kubernetes=0."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.executors import record_access_evidence as bridge
from guard.core.select import select
from guard.models import DataOrigin, Mode
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import baseline_pack, environment, policy, T0, PACK_DIR

PRIVATE = 'EXAMPLE-PRIVATE-LOG-USERNAME-GROUP-EXCEPTION-DO-NOT-PERSIST'


@pytest.fixture
def case(tmp_path, monkeypatch):
    pack = baseline_pack()
    image = 'example/engine@sha256:' + 'a' * 64
    env = environment(engine={'name': 'vllm', 'version': '0.31.0', 'image_digest': image})
    pol = policy(pack.ref(), values={}, scope={'include': ['OBS-06'], 'exclude': []})
    store = Store(tmp_path / 'source')
    store.put(env); store.put(pol); put_pack(store, PACK_DIR)
    plan = select(pack, [env], pol); store.put(plan)
    target = {'schema': 'guard-kubernetes-lifecycle-target/1', 'run_id': 'example-obs06',
        'environment_ref': env.ref().model_dump(mode='json'), 'lab': True,
        'kubernetes': {'context': 'example-context', 'engine': {
            'namespace': 'example', 'namespace_uid': '00000000-0000-0000-0000-000000000001',
            'pod': 'example-pod', 'pod_uid': '10000000-0000-0000-0000-000000000001',
            'container': 'engine', 'container_id': 'containerd://' + '1' * 64,
            'image': image, 'image_id': image, 'restart_count': 0,
            'node': 'example-node', 'pod_ip': '127.0.0.1', 'started_at': '2026-10-26T00:00:00Z'}}}
    subjects = {'schema': 'guard-record-access-subjects/1',
        'environment_ref': env.ref().model_dump(mode='json'), 'target_sha256': bridge._digest(target),
        'policy_ref': pol.ref().model_dump(mode='json'), 'subjects': [
            {'role': 'authorized_reader', 'username': 'example-allowed', 'groups': ['example-allowed-group']},
            {'role': 'unauthorized_reader', 'username': 'example-denied', 'groups': ['example-denied-group']}]}
    target_path, subjects_path = tmp_path / 'target.json', tmp_path / 'subjects.json'
    target_path.write_text(json.dumps(target)); subjects_path.write_text(json.dumps(subjects))
    capture = {'schema': 'guard-record-access-capture/1', 'profile': bridge.PROFILE,
        'environment_ref': env.ref().model_dump(mode='json'), 'target_sha256': bridge._digest(target),
        'subject_plan_sha256': bridge._digest(subjects), 'policy_ref': pol.ref().model_dump(mode='json'),
        'data_origin': 'EXAMPLE', 'collection_id': 'example-obs06-1',
        'started_at': '2026-10-30T00:00:00Z', 'finished_at': '2026-10-30T00:00:01Z',
        'rows': [{'case_id': name, 'role': role, 'attempted': True, 'identity_verified': True,
                  'binding_verified': True, 'read_verified': True, 'returncode': 0,
                  'byte_count': 12, 'content_sha256': None, 'gaps': []}
                 for name, role in bridge.CASES.items()],
        'normal_control': {'attempted': True, 'succeeded': True},
        'unauthorized_read_verified': True, 'cleanup': {'complete': True, 'collection_gap': False}, 'gaps': []}
    calls = []
    def capture_example(*args, **kwargs):
        calls.append((args, kwargs))
        return deepcopy(capture)
    # The only injected collection seam. No A class, transport or privilege patch.
    monkeypatch.setattr(bridge, '_capture', capture_example)
    arguments = dict(store=store.root, plan_id=plan.id, pack=PACK_DIR, target=target_path,
                     subjects=subjects_path, output=tmp_path / 'output', entered_by='EXAMPLE-operator')
    return {'store': store, 'pack': pack, 'plan': plan, 'env': env, 'policy': pol,
            'capture': capture, 'calls': calls, 'args': arguments, 'subjects': subjects}


def latest_evidence(case):
    return next(Store.read_only(case['args']['output'] / 'store').iter_kind('EVD'))


def test_preflight_keeps_exact_original_refs_and_performs_no_collection(case):
    result = bridge.run(**case['args'])
    assert result['preflight'] and result['collector_calls'] == 0 and not case['calls']
    assert result['pack'] == bridge.PACK_REF and result['policy'] == case['policy'].ref().model_dump(mode='json')
    assert result['environment'] == case['env'].ref().model_dump(mode='json')
    assert not case['args']['output'].exists()


def test_original_manual_question_prose_and_partial_normal_control(case):
    original = case['pack'].checks['OBS-06']
    projected = bridge._project_capture(case['capture'], case['env'],
        bridge._read(case['args']['target']), case['subjects'], case['policy'].ref())
    doc = bridge.manual_document(case['store'], case['pack'], case['plan'], projected,
                                 entered_by='EXAMPLE-operator', attachment=None)
    (entry,) = doc['checks']
    assert entry['question'] == original.question and original.revision == 4
    assert [note['item'] for note in entry['observation_notes']] == original.required_observations_prose
    assert [row['step'] for row in entry['challenges']] == original.challenge_steps
    assert [row['attempted'] for row in entry['challenges']] == [False, True, False, False, False, False]
    assert entry['normal_control'] == {'description': original.normal_control, 'result': 'not_attempted'}
    assert 'EXAMPLE' in entry['observation_notes'][0]['answer']
    assert entry['observation_notes'][1]['answer'].startswith('UNKNOWN')
    assert 'PASS' not in entry['verdicts'].values()


def test_known_read_fail_manual_roundtrip_original_preservation_and_public_cli(case):
    before = bridge._inventory(case['store'].root)
    result = bridge.run(**case['args'], execute=True)
    assert result['security'] == 'FAIL' and result['exact_refs_preserved'] and len(case['calls']) == 1
    assert bridge._inventory(case['store'].root) == before
    evd = latest_evidence(case)
    assert evd.data_origin == DataOrigin.DECLARED
    assert evd.environment == case['env'].ref() and evd.plan == case['plan'].ref()
    assert not evd.normal_control.attempted and evd.normal_control.succeeded is None
    assert evd.observations['declared_verdicts'].value == {
        'security': 'FAIL', 'functional': 'UNKNOWN', 'performance': 'UNKNOWN'}
    for folder in ('store', 'restored'):
        check = CliRunner().invoke(app, ['verify', '--store', str(case['args']['output'] / folder)])
        assert check.exit_code == 0, check.output
    report = CliRunner().invoke(app, ['report', '--plan', case['plan'].id, '--store',
        str(case['args']['output'] / 'restored'), '--as-of', result['as_of'], '--json',
        str(case['args']['output'] / 'public-report.json')])
    assert report.exit_code == 0, report.output


@pytest.mark.parametrize('change', ['no_proof', 'no_identity', 'no_binding', 'empty', 'truncated', 'policy_missing', 'no_collection'])
def test_uncertain_access_never_submits_pass(case, change):
    unauthorized = next(row for row in case['capture']['rows'] if row['case_id'] == 'unauthorized')
    if change == 'no_proof': case['capture']['unauthorized_read_verified'] = None
    elif change == 'no_identity': unauthorized['identity_verified'] = False
    elif change == 'no_binding': unauthorized['binding_verified'] = False
    elif change == 'empty': unauthorized['byte_count'] = 0
    elif change == 'truncated': unauthorized['gaps'] = ['RECORD_ACCESS_INCOMPLETE']
    elif change == 'policy_missing':
        case['capture']['unauthorized_read_verified'] = None
        unauthorized['read_verified'] = None
    else:
        for row in case['capture']['rows']:
            row.update(attempted=False, identity_verified=False, binding_verified=False,
                       read_verified=None, returncode=None, byte_count=None)
        case['capture']['normal_control'] = {'attempted': False, 'succeeded': None}
        case['capture']['unauthorized_read_verified'] = None
    result = bridge.run(**case['args'], execute=True)
    assert result['security'] == 'UNKNOWN' and result['storage_encryption'] == 'UNKNOWN'
    assert 'PASS' not in latest_evidence(case).observations['declared_verdicts'].value.values()


@pytest.mark.parametrize('late', ['after', 'cleanup', 'artifact', 'export'])
def test_known_fail_survives_independent_later_failure(case, monkeypatch, late):
    if late == 'after':
        case['capture']['rows'][-1].update(read_verified=None, byte_count=None,
            returncode=None, gaps=['RECORD_ACCESS_AFTER_UNAVAILABLE'])
        case['capture']['normal_control']['succeeded'] = None
    elif late == 'cleanup':
        case['capture']['cleanup'] = {'complete': False, 'collection_gap': True}
        case['capture']['gaps'] = ['RECORD_ACCESS_CLEANUP_UNAVAILABLE']
    elif late == 'artifact':
        original = bridge._write_json
        def write(path, value):
            if Path(path).name == 'record-access.json': raise OSError(PRIVATE)
            return original(path, value)
        monkeypatch.setattr(bridge, '_write_json', write)
    else:
        original = bridge.export_bundle
        def export(source, output):
            if Path(output).name == 'evidence.zip': raise OSError(PRIVATE)
            return original(source, output)
        monkeypatch.setattr(bridge, 'export_bundle', export)
    if late == 'export':
        with pytest.raises(OSError): bridge.run(**case['args'], execute=True)
    else:
        assert bridge.run(**case['args'], execute=True)['security'] == 'FAIL'
    assert latest_evidence(case).observations['declared_verdicts'].value['security'] == 'FAIL'
    stored = Store.read_only(case['args']['output'] / 'store')
    assert next(stored.iter_kind('RES')).verdicts.security.value == 'FAIL'


def test_sensitive_capture_fields_and_raw_hashes_not_stored(case):
    case['capture'].update(raw_logs=PRIVATE, username=PRIVATE, groups=[PRIVATE], exception=PRIVATE)
    for row in case['capture']['rows']:
        row.update(stdout=PRIVATE, content_sha256='f' * 64, username=PRIVATE)
    bridge.run(**case['args'], execute=True)
    raw = (case['args']['output'] / 'record-access.json').read_text()
    assert PRIVATE not in raw and 'content_sha256' not in raw and 'username' not in raw
    for blob in (case['args']['output'] / 'store/artifacts').iterdir():
        assert PRIVATE.encode() not in blob.read_bytes()


@pytest.mark.parametrize('invalid', ['subject_ref', 'same_principal', 'no_groups', 'not_lab', 'existing_output', 'mode'])
def test_invalid_or_unapproved_inputs_fail_before_collection(case, invalid):
    if invalid in ('subject_ref', 'same_principal', 'no_groups'):
        value = deepcopy(case['subjects'])
        if invalid == 'subject_ref': value['policy_ref']['sha256'] = '0' * 64
        elif invalid == 'same_principal': value['subjects'][1]['username'] = value['subjects'][0]['username']
        else: value['subjects'][1]['groups'] = []
        case['args']['subjects'].write_text(json.dumps(value))
    elif invalid == 'not_lab':
        value = bridge._read(case['args']['target']); value['lab'] = False
        case['args']['target'].write_text(json.dumps(value))
    elif invalid == 'existing_output': case['args']['output'].mkdir()
    else:
        changed = case['plan'].model_copy(update={'mode': Mode.update, 'revision': 2}).seal()
        case['store'].put(changed)
    with pytest.raises(ValueError): bridge.run(**case['args'], execute=True)
    assert not case['calls']


def test_fresh_output_and_append_only_source_evidence(case):
    prior = deepcopy(case['capture']); prior['unauthorized_read_verified'] = None
    projected = bridge._project_capture(prior, case['env'], bridge._read(case['args']['target']),
                                         case['subjects'], case['policy'].ref())
    doc = bridge.manual_document(case['store'], case['pack'], case['plan'], projected,
                                 entered_by='EXAMPLE-operator', attachment=None)
    bridge.import_evidence(case['store'], case['pack'], case['store'].root.parent / 'prior.yaml', document=doc)
    original = next(case['store'].iter_kind('EVD'))
    bridge.run(**case['args'], execute=True)
    copied = Store.read_only(case['args']['output'] / 'store')
    assert copied.resolve(original.ref()).ref() == original.ref()
    assert copied.latest(original.id).revision == original.revision + 1
    assert case['store'].latest(original.id).ref() == original.ref()
    first = bridge._inventory(case['args']['output'])
    with pytest.raises(ValueError): bridge.run(**case['args'], execute=True)
    assert bridge._inventory(case['args']['output']) == first and len(case['calls']) == 1
