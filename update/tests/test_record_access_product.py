"""OBS-06 product and manual evidence with EXAMPLE Kubernetes replies."""
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.executors import kubernetes_record_access as collector
from guard.executors import kubernetes_record_access_product as product
from guard.executors import record_access_evidence as bridge
from guard.executors import run_binding
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from tests.builders import environment, policy
from tests.test_record_access_evidence import case as prepared_case
from tests.test_kubernetes_record_access import World, PRIVATE, context as access_context


def test_real_collector_reaches_original_pack_evidence_and_restored_verdict(prepared_case, monkeypatch):
    case = prepared_case
    assert case['env'].data_origin == DataOrigin.EXAMPLE
    # The shared metadata protocol fixture explicitly selects fixture-context.
    target = bridge._read(case['args']['target'])
    target['kubernetes']['context'] = 'fixture-context'
    case['args']['target'].write_text(json.dumps(target))
    case['subjects']['target_sha256'] = bridge._digest(target)
    case['args']['subjects'].write_text(json.dumps(case['subjects']))
    calls = []

    def collect(environment, target, subjects, **kwargs):
        world = World(SimpleNamespace(env=environment, target=target, subjects=subjects,
                                      work=kwargs['workdir']))
        value = collector.capture_record_access(environment, target, subjects,
                                                _runner=world.runner, **kwargs)
        calls.append((world, value))
        return value

    # Only the explicit EXAMPLE transport seam is injected; the real collector,
    # public A metadata reader, manual importer and judges run together.
    monkeypatch.setattr(bridge, '_capture', collect)
    result = bridge.run(**case['args'], execute=True)
    assert len(calls) == 1
    world, observed = calls[0]
    assert (world.meta_calls, world.who_calls, world.logs_calls) == (12, 6, 3)
    assert observed['normal_control'] == {'attempted': True, 'succeeded': True}
    assert observed['cleanup'] == {'complete': True, 'collection_gap': False}
    assert all(not path.exists() for path in world.scratch)
    assert result['security'] == 'FAIL' and result['storage_encryption'] == 'UNKNOWN'
    assert result['source_unchanged'] and result['inputs_unchanged'] and result['exact_refs_preserved']
    for folder in ('store', 'restored'):
        store = Store.read_only(case['args']['output'] / folder)
        assert store.verify() == []
        evd, = list(store.iter_kind('EVD'))
        assert evd.environment == case['env'].ref() and not evd.normal_control.attempted
        assert evd.observations['declared_verdicts'].value == {
            'security': 'FAIL', 'functional': 'UNKNOWN', 'performance': 'UNKNOWN'}
        assert len(evd.artifacts) == 1
        record = json.loads((store.root / evd.artifacts[0].path).read_bytes())
        serialized = json.dumps(record)
        assert PRIVATE not in serialized and 'content_sha256' not in serialized
        assert all(subject['username'] not in serialized for subject in case['subjects']['subjects'])
        assert record['data_origin'] == 'EXAMPLE' and record['storage_encryption'] == 'UNKNOWN'


@pytest.mark.parametrize(('issue', 'reason'), [
    (None, None),
    ('target-link-after-preparation', None),
    ('subjects-link-after-preparation', None),
    ('stale-environment', 'RECORD_ACCESS_SUBJECT_PLAN_INVALID'),
    ('stale-policy', 'RECORD_ACCESS_CONFIGURATION_INVALID'),
    ('stale-target', 'RECORD_ACCESS_SUBJECT_PLAN_INVALID'),
    ('wrong-target-pin', 'RECORD_ACCESS_CONFIGURATION_INVALID'),
    ('duration', 'RECORD_ACCESS_BUDGET_INVALID'),
])
def test_obs06_preparation_uses_bound_subject_source_without_collection(access_context, tmp_path, monkeypatch, issue, reason):
    case = access_context
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    check = pack.checks['OBS-06']
    pol = policy(pack.ref(), scope={'include': ['OBS-06']}, values={})
    subjects = {**case.subjects, 'policy_ref': pol.ref().model_dump(mode='json')}
    if issue == 'stale-environment':
        subjects['environment_ref'] = {**subjects['environment_ref'], 'sha256': 'd' * 64}
    elif issue == 'stale-policy':
        subjects['policy_ref'] = {**subjects['policy_ref'], 'sha256': 'd' * 64}
    elif issue == 'stale-target':
        subjects['target_sha256'] = 'd' * 64
    target_path, subjects_path, producer_path = (tmp_path / name for name in ('target.json', 'subjects.json', 'producer.py'))
    target_bytes = canonical_json(case.target)
    target_path.write_bytes(target_bytes)
    subjects_bytes = canonical_json(subjects)
    subjects_path.write_bytes(subjects_bytes)
    producer_path.write_bytes(b'# EXAMPLE metadata only\n')
    settings = {'target': str(target_path), 'subjects': str(subjects_path)}
    if issue == 'duration':
        settings['max_duration_seconds'] = 301
    _, values = run_binding.effective_values(check, settings, case.env, {}, side='candidate', attempt_id=None,
        issued_at=utcnow(), policy_ref=pol.ref(), run_cfg={'record_session': 'a' * 32, 'record_producer': str(producer_path)})
    assert values['record_subject_plan_sha256'] == sha256_hex(subjects_bytes)

    def forbidden(*args, **kwargs):
        pytest.fail('Static OBS-06 admission must not collect logs or inventory')

    monkeypatch.setattr(product, 'capture_record_access', forbidden)
    monkeypatch.setattr(product.KubernetesRecordAccessExecutor, '_inventory', forbidden)
    executor = product.KubernetesRecordAccessExecutor(settings, runner=forbidden)
    expected = 'f' * 64 if issue == 'wrong-target-pin' else sha256_hex(target_bytes)
    if reason is None:
        executor.prepared_record_access(check, case.env, values, expected_target_sha256=expected)
    else:
        with pytest.raises(ValueError, match=f'^{reason}$'):
            executor.prepared_record_access(check, case.env, values, expected_target_sha256=expected)

    if issue in ('target-link-after-preparation', 'subjects-link-after-preparation'):
        selected = target_path if issue.startswith('target-') else subjects_path
        original = selected.with_name('original-' + selected.name)
        selected.rename(original)
        selected.symlink_to(original.name)
        with pytest.raises(ValueError, match='^RECORD_ACCESS_CONFIGURATION_INVALID$'):
            executor.run(check, case.env, values, VllmAdapter(), tmp_path / 'work')
        assert not (tmp_path / 'work').exists()


@pytest.mark.parametrize('change', ['matching', 'wrong-target', 'wrong-policy', 'after-window', 'future-read',
                                   'unverified-owner'])
def test_canonical_obs06_artifact_keeps_qualified_ordinary_capture(access_context, tmp_path, monkeypatch, change):
    case = access_context
    target_path, subjects_path = tmp_path / 'target.json', tmp_path / 'subjects.json'
    target_path.write_bytes(canonical_json(case.target))
    subjects_path.write_bytes(canonical_json(case.subjects))
    now = utcnow()
    policy_end = now + timedelta(minutes=1)
    values = {
        'record_environment_sha256': case.env.ref().sha256,
        'record_target_sha256': sha256_hex(target_path.read_bytes()),
        'record_subject_plan_sha256': sha256_hex(subjects_path.read_bytes()),
        'record_inventory_session': 'a' * 32, 'record_inventory_source_sha256': 'b' * 64,
        'record_validity_start': (now - timedelta(minutes=1)).isoformat(), 'record_validity_end': policy_end.isoformat(),
        'record_access_policy_ref': case.ref.model_dump(mode='json'),
    }
    world = World(case, 'initial-binding' if change == 'unverified-owner' else 'identity-before')
    produced = []

    def capture(*args, **kwargs):
        result = collector.capture_record_access(*args, **kwargs)
        produced.append(result)
        if change == 'wrong-target':
            return {**result, 'target_sha256': 'f' * 64}
        if change == 'wrong-policy':
            return {**result, 'policy_ref': {**result['policy_ref'], 'sha256': 'f' * 64}}
        if change == 'after-window':
            return {**result, 'finished_at': (policy_end + timedelta(seconds=1)).isoformat()}
        if change == 'future-read':
            return {**result, 'finished_at': (policy_end - timedelta(seconds=1)).isoformat()}
        return result

    def unavailable(*args, **kwargs):
        raise ValueError('No inventory producer in the EXAMPLE session')

    monkeypatch.setattr(product, 'capture_record_access', capture)
    monkeypatch.setattr(product.KubernetesRecordAccessExecutor, '_inventory', unavailable)
    executor = product.KubernetesRecordAccessExecutor(
        {'target': str(target_path), 'subjects': str(subjects_path)}, runner=world.runner)
    check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['OBS-06']
    draft = executor.run(check, case.env, values, VllmAdapter(), case.work)
    original, = produced
    assert world.logs_calls == (0 if change == 'unverified-owner' else 2)
    assert not original['rows'][1]['attempted']
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    verified = draft.observations['record_normal_read_verified']
    assert verified.value is {'matching': True, 'unverified-owner': False}.get(change)
    assert verified.collected is (change in {'matching', 'unverified-owner'})
    assert draft.observations['record_inventory_authenticated'].value is False
    assert not draft.observations['record_copy_inventory'].collected
    artifact, = draft.artifacts
    serialized = artifact.path.read_text(encoding='utf-8')
    summary = json.loads(serialized)
    assert summary['copies'] is None
    for private in (PRIVATE, 'example-allowed', 'example-unallowed', 'system:authenticated', str(target_path)):
        assert private not in serialized
    if change == 'matching':
        assert summary['ordinary_capture'] == {
            **{key: original[key] for key in ('schema', 'profile', 'collection_id', 'target_sha256', 'subject_plan_sha256',
                                              'data_origin', 'started_at', 'finished_at')},
            'environment_sha256': case.env.ref().sha256, 'policy_sha256': case.ref.sha256,
            'authorized_read_owners': {
                row['case_id']: row['owner_sha256'] for row in original['rows'] if row['role'] == 'authorized_reader'},
        }
    else:
        assert 'ordinary_capture' not in summary
    if change in {'wrong-target', 'wrong-policy', 'after-window', 'future-read'}:
        assert summary['rows'] is None and not draft.observations['record_access_rows'].collected
        assert not draft.normal_control.attempted and draft.normal_control.succeeded is None
        assert 'RECORD_ACCESS_LOG_SOURCE_UNVERIFIED' in summary['diagnostic_codes']


@pytest.mark.parametrize('condition', ['matching', 'wrong-env', 'wrong-target', 'wrong-source', 'late-owner',
                                       'unstable-file', 'parent-cleanup', 'wrong-access-target', 'expired-before-inventory',
                                       'expired-during-inventory', 'inventory-clock-reversed',
                                       'owned-baseline', 'owned-candidate', 'owner-mismatch', 'read-windows-overlap'])
def test_obs06_keeps_only_the_exact_bound_inventory_receipt(access_context, tmp_path, monkeypatch, condition):
    from tests.test_kubernetes_lifecycle import World as InventoryWorld
    from tests.test_lifecycle_capture import Docker as FileProtocol

    case = access_context
    joined_cases = {'owned-baseline', 'owned-candidate', 'owner-mismatch', 'read-windows-overlap'}
    if condition in joined_cases:
        from tools.mvp_versions import versions

        release = versions()['baseline' if condition == 'owned-baseline' else 'candidate']
        case.env = environment(engine={'name': release['engine'], 'version': release['version'], 'image_digest': release['image']})
        case.target['environment_ref'] = case.env.ref().model_dump(mode='json')
        case.target['kubernetes']['engine'].update(image=release['image'], image_id=release['image'])
        case.subjects.update(environment_ref=case.target['environment_ref'], target_sha256=sha256_hex(canonical_json(case.target)))
    check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['OBS-06']
    target_path, subjects_path, producer_path = (tmp_path / name for name in ('target.json', 'subjects.json', 'producer'))
    target_path.write_bytes(canonical_json(case.target))
    subjects_path.write_bytes(canonical_json(case.subjects))
    producer_path.write_bytes(b'EXAMPLE inventory writer metadata')
    cfg = {'target': str(target_path), 'subjects': str(subjects_path)}
    _, values = run_binding.effective_values(check, cfg, case.env, {}, side='candidate', attempt_id=None,
        issued_at=utcnow(), policy_ref=case.ref, run_cfg={'record_session': 'a' * 32, 'record_producer': str(producer_path)})
    timed_inventory_gap = condition in {'expired-before-inventory', 'expired-during-inventory', 'inventory-clock-reversed'}
    if timed_inventory_gap or condition in {'matching', 'wrong-access-target'}:
        end = product.stamp(values['record_validity_end'])
        opened = end - timedelta(microseconds=2)
        outside = end + timedelta(microseconds=1)
        inventory_instants = {
            'expired-before-inventory': (outside,),
            'expired-during-inventory': (opened, outside),
            'inventory-clock-reversed': (opened, opened - timedelta(microseconds=1)),
        }.get(condition, (opened, end))
        # Matching has an unavailable ordinary collector, so only its opening
        # clock is read. When the collector returns, the product reads both.
        ordinary_instants = (None,) if condition == 'matching' else (None, None)
        instants = iter((*ordinary_instants, *inventory_instants))

        def now(zone):
            instant = next(instants, None)
            return datetime.now(zone) if instant is None else instant

        monkeypatch.setattr(product, 'datetime', SimpleNamespace(now=now))
        if not timed_inventory_gap:
            draft_instants = iter((utcnow(), end))
            monkeypatch.setattr(product, 'utcnow', lambda: next(draft_instants))
    copy = {'location_sha256': '1' * 64, 'kind': 'original', 'grade': 'EXAMPLE-diagnostic',
            'encryption_required': True, 'encryption_applied': True, 'key_reference_sha256': None,
            'unauthorized_export': None, 'unauthorized_decrypt': None}
    packet = {'schema': product.INVENTORY_SCHEMA, 'environment_ref': case.env.ref().model_dump(mode='json'),
              'target_sha256': values['record_target_sha256'], 'source_sha256': values['record_inventory_source_sha256'],
              'copies': [copy]}
    if condition == 'wrong-env':
        packet['environment_ref']['sha256'] = 'f' * 64
    elif condition == 'wrong-target':
        packet['target_sha256'] = 'f' * 64
    elif condition == 'wrong-source':
        packet['source_sha256'] = 'f' * 64
    raw = canonical_json(packet) + b'\n'
    world = InventoryWorld(SimpleNamespace(), case.target, 'late-file-owner' if condition == 'late-owner' else None)
    world.docker = FileProtocol(SimpleNamespace(raw=raw))

    def unavailable(*args, **kwargs):
        raise ValueError('EXAMPLE principal collection unavailable')

    produced = []
    if timed_inventory_gap or condition in {'parent-cleanup', 'wrong-access-target'} | joined_cases:
        ordinary = World(case, 'identity-before')

        def ordinary_capture(*args, **kwargs):
            captured = collector.capture_record_access(*args, **{**kwargs, '_runner': ordinary.runner})
            produced.append(captured)
            if condition == 'wrong-access-target':
                return {**captured, 'target_sha256': 'f' * 64}
            if condition == 'owner-mismatch':
                return {**captured, 'rows': [
                    {**row, 'owner_sha256': 'f' * 64} if row['role'] == 'authorized_reader' else row
                    for row in captured['rows']]}
            return captured

        def runner(argv, **kwargs):
            response = world.runner(argv, **kwargs)
            if condition == 'parent-cleanup' and 'exec' in argv:
                response['capture']['pipes_closed'] = False
            return response
    else:
        ordinary_capture, runner = unavailable, world.runner
        if condition == 'unstable-file':
            def runner(argv, **kwargs):
                response = world.runner(argv, **kwargs)
                if 'exec' in argv:
                    source_packet = json.loads(response['stdout_bytes'])
                    source_packet['path_after']['inode'] += 1
                    response['stdout_bytes'] = canonical_json(source_packet)
                return response

    if condition == 'read-windows-overlap':
        clocks = iter(('ordinary-open', 'ordinary-check', 'inventory-open', 'inventory-close'))

        def reversed_inventory_start(zone):
            if next(clocks, None) == 'inventory-open':
                return datetime.fromisoformat(produced[0]['finished_at']) - timedelta(microseconds=1)
            return datetime.now(zone)

        monkeypatch.setattr(product, 'datetime', SimpleNamespace(now=reversed_inventory_start))
    monkeypatch.setattr(product, 'capture_record_access', ordinary_capture)
    executor = product.KubernetesRecordAccessExecutor(cfg, runner=runner)
    draft = executor.run(check, case.env, values, VllmAdapter(), case.work)
    artifact, = draft.artifacts
    summary = json.loads(artifact.path.read_bytes())
    assert world.execs == ([] if condition == 'expired-before-inventory' else ['file'])
    assert draft.status is ExecStatus.ABORTED and draft.data_origin is DataOrigin.EXAMPLE
    if condition in joined_cases:
        assert ordinary.logs_calls == 2 and not produced[0]['rows'][1]['attempted']
        assert draft.observations['record_normal_read_verified'].value is True
        assert draft.observations['record_copy_inventory'].collected
        assert draft.observations['record_inventory_authenticated'].value is False
        assert draft.observations['record_scope_complete'].value is False
        read, inventory = summary['ordinary_capture'], summary['inventory_receipt']
        if condition.startswith('owned-'):
            owner = inventory['owner_sha256']
            assert summary['ordinary_inventory_owner_join'] == {
                'owner_sha256': owner, 'authorized_read_cases': ['allowed-after', 'allowed-before'],
                'inventory_file_sha256': sha256_hex(raw)}
            assert read['authorized_read_owners'] == {'allowed-before': owner, 'allowed-after': owner}
        else:
            assert 'ordinary_inventory_owner_join' not in summary
            if condition == 'read-windows-overlap':
                assert inventory['started_at'] < read['finished_at']
            else:
                assert set(read['authorized_read_owners'].values()) == {'f' * 64}
        return
    assert 'ordinary_inventory_owner_join' not in summary
    if timed_inventory_gap:
        assert ordinary.logs_calls == 2 and draft.normal_control.succeeded is True
        assert 'ordinary_capture' in summary and 'inventory_receipt' not in summary
        assert summary['copies'] is None and not draft.cleanup_errors
        assert all(not draft.observations[key].collected for key in ('record_copy_inventory', 'record_copy_violations'))
        assert summary['diagnostic_codes'] == ['RECORD_ACCESS_INVENTORY_UNAVAILABLE',
                                               'RECORD_ACCESS_INVENTORY_WINDOW_UNVERIFIED']
        return
    if condition == 'parent-cleanup':
        code = 'RECORD_ACCESS_INVENTORY_PARENT_CLEANUP_UNVERIFIED'
        assert ordinary.logs_calls == 2 and draft.normal_control.succeeded
        assert draft.observations['record_normal_read_verified'].value is True
        assert 'ordinary_capture' in summary and 'inventory_receipt' not in summary
        assert summary['copies'] is None and not draft.observations['record_copy_inventory'].collected
        assert draft.cleanup_errors == [code]
        assert summary['diagnostic_codes'] == ['RECORD_ACCESS_INVENTORY_UNAVAILABLE', code]
        return
    assert not draft.cleanup_errors
    assert 'ordinary_capture' not in summary and not draft.normal_control.attempted
    assert draft.observations['record_inventory_authenticated'].value is False
    assert draft.observations['record_scope_complete'].value is False
    if condition in {'matching', 'wrong-access-target'}:
        assert draft.started_at <= opened <= end <= draft.finished_at
        binding = case.target['kubernetes']['engine']
        owner = {key: binding[key] for key in ('namespace_uid', 'pod_uid', 'container', 'container_id',
                                                'node', 'restart_count', 'started_at')}
        owner.update(transport='kubernetes', image_reference=binding['image'], reported_image_id=binding['image_id'])
        file_identity = {'device': 1, 'inode': 2, 'size': len(raw), 'mtime_ns': 3, 'ctime_ns': 4}
        assert summary['inventory_receipt'] == {
            'file_sha256': sha256_hex(raw), 'file_bytes': len(raw), 'environment_sha256': case.env.ref().sha256,
            'target_sha256': packet['target_sha256'], 'declared_source_sha256': packet['source_sha256'],
            'owner_binding_verified': True, 'owner_sha256': sha256_hex(canonical_json(owner)),
            'file_identity_sha256': sha256_hex(canonical_json(file_identity)),
            'record_inventory_session_sha256': sha256_hex(values['record_inventory_session'].encode('ascii')),
            'policy_sha256': case.ref.sha256, 'started_at': opened.isoformat(), 'finished_at': end.isoformat(),
        }
        assert summary['copies'] == [copy] and draft.observations['record_copy_inventory'].collected
        diagnostic = 'RECORD_ACCESS_LOG_SOURCE_UNVERIFIED' if condition == 'wrong-access-target' else 'RECORD_ACCESS_LOG_READ_UNAVAILABLE'
        assert summary['diagnostic_codes'] == [diagnostic]
    else:
        assert 'inventory_receipt' not in summary and summary['copies'] is None
        assert not draft.observations['record_copy_inventory'].collected
        assert summary['diagnostic_codes'] == ['RECORD_ACCESS_LOG_READ_UNAVAILABLE', 'RECORD_ACCESS_INVENTORY_UNAVAILABLE']
