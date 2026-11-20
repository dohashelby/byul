"""Offline paired lifecycle input materialization through the existing readiness consumer."""

from functools import partial
import json
from pathlib import Path
import socket
import subprocess
import tomllib

import pytest

from guard import application
from guard.executors import kubernetes_log_integrity, lifecycle_transition as transition
from guard.executors.retry_record_contract import TARGET_BINDING_KEY as RETRY_TARGET_BINDING
from guard.models import DataOrigin, Environment, canonical_json, sha256_hex
from tests import test_redeployment_preflight as fixture
from tests.builders import T0, policy
from tests.test_a1_retry_record_contract import example as retry_example
from tests.test_kubernetes_log_integrity import GAP_VALUES
from tests.test_lifecycle_capture import external
from tests.test_model_mapping import declared_mapping
from tools import mvp_lifecycle_transition_inputs as materializer, mvp_live_readiness as readiness
from tools import mvp_metadata_wiring as metadata, mvp_retry_record_inputs as retry_inputs
from tools.j02_run_config import write_config


def _pointer(path):
    path = Path(path)
    return {'path': str(path), 'sha256': sha256_hex(path.read_bytes())}


def _selected_side(env, rows, binding):
    return {'environment_ref': env.ref().model_dump(mode='json'),
            'generations': [{key: value if key == 'id' else _pointer(value) for key, value in row.items()}
                            for row in rows],
            'phase_generation': dict(binding['phase_generation']),
            'sample_offsets_ms': dict(binding['sample_offsets_ms'])}


def _offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline lifecycle input materialization attempted to launch or contact a target')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(transition.KubernetesHTTP, '_verify', forbidden)
    monkeypatch.setattr(transition.KubernetesHTTP, '__call__', forbidden)
    monkeypatch.setattr(transition.LifecycleTransitionExecutor, 'run', forbidden)
    monkeypatch.setattr(fixture, 'environment', partial(fixture.environment, origin=DataOrigin.DECLARED))


def _retry_pair(directory, sources, pack, policy_path, confirmed, example_scope):
    sides = {}
    for number, (side, files) in enumerate(sources.items(), 1):
        target = json.loads(files['target'].read_bytes())
        producer = directory / f'{side}-producer.txt'
        producer.write_bytes(b'# Inert EXAMPLE source; never executed\n')
        sides[side] = {'environment_ref': target['environment_ref'], 'target_sha256': sha256_hex(canonical_json(target)),
                       'run_id': target['kubernetes']['run_id'], 'record_session': f'{number + 10:032x}',
                       'record_producer': _pointer(producer),
                       'subjects': [row for row in example_scope['subjects'] if row['scenario'] == 'NORMAL'],
                       **{key: example_scope[key] for key in ('producer_id', 'producer_code_sha256',
                                                             'configuration_sha256', 'native_source_sha256')}}
    selection = directory / 'retry-selection.json'
    selection.write_bytes(canonical_json({'schema': retry_inputs.SELECTION_SCHEMA,
        'policy_ref': confirmed.ref().model_dump(mode='json'), 'sides': sides}))
    output = directory / 'prepared-retry'
    retry_inputs.prepare(sources, policy_path, selection, _pointer(selection)['sha256'], output, pack=pack)
    return json.loads((output / 'retry-record-inputs.json').read_bytes())


@pytest.mark.parametrize(('check_id', 'case', 'error'), [
    ('LIF-05', 'ordinary', None),
    ('LIF-16', 'same-pod-restart', None),
    ('LIF-05', 'no-observed-version', None),
    ('LIF-05', 'selection-changed', 'MVP_TRANSITION_SELECTION_CHANGED'),
    ('LIF-16', 'raw-target-changed', 'MVP_TRANSITION_DECLARATION_FILE_CHANGED'),
    ('LIF-05', 'lab-target', 'MVP_TRANSITION_GENERATION_TARGET_MISMATCH'),
    ('LIF-05', 'wrong-before-env', 'MVP_TRANSITION_BEFORE_ENVIRONMENT_MISMATCH'),
    ('LIF-16', 'other-release', 'MVP_TRANSITION_GENERATION_ENVIRONMENT_MISMATCH'),
    ('LIF-16', 'other-target-release', 'MVP_TRANSITION_GENERATION_TARGET_MISMATCH'),
    ('LIF-16', 'null-observed-version', 'MVP_TRANSITION_GENERATION_TARGET_MISMATCH'),
    ('LIF-05', 'cross-side-owner', 'TRANSITION_GENERATION_SIDE_REUSE'),
    ('LIF-16', 'duplicate-offset', 'TRANSITION_POLICY_INVALID'),
    ('LIF-05', 'manifest-rewritten', 'MVP_TRANSITION_DECLARATION_FILE_CHANGED'),
])
def test_materializes_one_paired_redeployment_check_without_collection(external, monkeypatch, check_id, case, error):
    _offline_only(monkeypatch)
    pack, envs, _, cfg = fixture._configuration(external, check_id, change=case, mvp_inputs=True,
                                               lab=case == 'lab-target')
    confirmed = policy(pack.ref(), scope={'include': [check_id]}, values={}, data_origin=DataOrigin.DECLARED,
                       status='CONFIRMED', confirmed_by='EXAMPLE operator', confirmed_at=T0)
    policy_path = external / 'policy.json'
    policy_path.write_bytes(canonical_json(confirmed.to_dict()))
    binding = cfg['run_binding'][check_id]
    sides = {}
    for side, env in zip(readiness.SIDES, envs, strict=True):
        rows = cfg['sides'][side]['check_config'][check_id]['generations']
        sides[side] = _selected_side(env, rows, binding)

    candidate = sides['candidate']
    candidate['sample_offsets_ms']['recovered'] += 100
    after = candidate['generations'][1]
    target = Path(after['target']['path'])
    if case == 'raw-target-changed':
        target.write_bytes(target.read_bytes() + b'\n')
    elif case in {'no-observed-version', 'other-target-release', 'null-observed-version'}:
        declaration = json.loads(target.read_bytes())
        if case == 'no-observed-version':
            del declaration['observed_version']
        else:
            declaration['observed_version'] = '0.30.0' if case == 'other-target-release' else None
        target.write_bytes(canonical_json(declaration))
        after['target'] = _pointer(target)
    elif case == 'wrong-before-env':
        candidate['environment_ref'] = Environment.model_validate_json(
            Path(after['environment']['path']).read_bytes()).ref().model_dump(mode='json')
    elif case == 'duplicate-offset':
        candidate['sample_offsets_ms']['during'] = candidate['sample_offsets_ms']['before']
    elif case == 'manifest-rewritten':
        manifest = Path(after['prepared']['path'])
        original = transition.LifecycleTransitionExecutor.prepared_generations

        def rewrite_after_read(executor, check, env, values):
            prepared = original(executor, check, env, values)
            if env.ref() == envs[1].ref():
                manifest.write_bytes(manifest.read_bytes() + b'\n')
            return prepared

        monkeypatch.setattr(transition.LifecycleTransitionExecutor, 'prepared_generations', rewrite_after_read)

    selection_path = external / 'selection.json'
    selection_path.write_bytes(canonical_json({'schema': materializer.SELECTION_SCHEMA, 'check_id': check_id,
                                               'policy_ref': confirmed.ref().model_dump(mode='json'), 'sides': sides}))
    selection_pin = '0' * 64 if case == 'selection-changed' else sha256_hex(selection_path.read_bytes())
    output = external / 'assembled'
    if error is not None:
        with pytest.raises(ValueError, match=f'^{error}$'):
            materializer.prepare(policy_path, selection_path, selection_pin, output, pack=pack)
        assert not output.exists()
        return

    assembled, index = materializer.prepare(policy_path, selection_path, selection_pin, output, pack=pack)
    config_path = output / materializer.CONFIG_NAME
    index_path = output / materializer.INDEX_NAME
    assert tomllib.loads(config_path.read_text(encoding='utf-8')) == assembled
    assert json.loads(index_path.read_bytes()) == index
    assert assembled['update']['allowed_checks'] == [check_id]
    assert assembled['executors'] == {'lifecycle_transition': 'kubernetes'}
    assert [item['side'] for item in index['inputs']] == list(readiness.SIDES)
    for item in index['inputs']:
        side = item['side']
        expected = cfg['sides'][side]['check_config'][check_id]
        digest = sha256_hex(canonical_json(transition.inputs_manifest(expected)))
        assert assembled['sides'][side]['check_config'] == {
            check_id: {**expected, transition.GENERATION_MANIFEST_PIN: digest}}
        assert assembled['sides'][side]['run_binding'] == {
            check_id: {key: sides[side][key] for key in binding}}
        assert item['configuration'] == _pointer(config_path)
        assert item['environment'] == sides[side]['generations'][0]['environment']

    report = readiness.audit(index_path, pack=pack)
    selected = {row['side']: row for row in report['rows'] if row['check_id'] == check_id}
    assert set(selected) == set(readiness.SIDES)
    for row in selected.values():
        assert row['file_binding_valid'] and row['binding_schema'] == 'guard-run-binding/2'
        assert row['blocking_codes'] == ['LIVE_EXECUTOR_PREFLIGHT_REQUIRED']
        assert not row['engine_executed'] and not row['ready_for_execution'] and row['actual_results'] is None

    target_data = json.loads(target.read_bytes())
    target_data['kubernetes']['probe']['pod_uid'] = '2187f050-0a54-41ea-8c12-f03c406400cc'
    target.write_bytes(canonical_json(target_data))
    report = readiness.audit(index_path, pack=pack)
    changed = next(row for row in report['rows'] if row['check_id'] == check_id and row['side'] == 'candidate')
    assert not changed['file_binding_valid']


@pytest.mark.parametrize('case,error', [
    ('paired', None),
    ('duplicate', 'MVP_TRANSITION_SELECTION_DUPLICATE'),
    ('foreign-policy', 'MVP_TRANSITION_CONFIRMED_POLICY_REQUIRED'),
    ('second-pin-changed', 'MVP_TRANSITION_SELECTION_CHANGED'),
    ('different-before', 'MVP_TRANSITION_PAIRED_ENVIRONMENT_MISMATCH'),
])
def test_both_redeployment_checks_share_one_configuration(external, monkeypatch, case, error):
    _offline_only(monkeypatch)
    checks = ('LIF-05', 'LIF-16')
    pack, envs, _, cfg = fixture._configuration(external, checks[0], mvp_inputs=True, lab=False)
    confirmed = policy(pack.ref(), scope={'include': list(checks)}, values={}, data_origin=DataOrigin.DECLARED,
                       status='CONFIRMED', confirmed_by='EXAMPLE operator', confirmed_at=T0)
    policy_path = external / 'policy.json'
    policy_path.write_bytes(canonical_json(confirmed.to_dict()))
    policy_ref = confirmed.ref().model_dump(mode='json')
    payload = {check_id: {'schema': materializer.SELECTION_SCHEMA, 'check_id': check_id,
                          'policy_ref': policy_ref, 'sides': {}} for check_id in checks}
    binding = cfg['run_binding'][checks[0]]
    for index, (side, env) in enumerate(zip(readiness.SIDES, envs, strict=True)):
        rows = cfg['sides'][side]['check_config'][checks[0]]['generations']
        payload[checks[0]]['sides'][side] = _selected_side(env, rows, binding)
        old = rows[0]
        if case == 'different-before' and side == 'candidate':
            env, old = fixture._generation(external / 'foreign-before', 'old', 200, version=env.engine.version,
                                           mvp_inputs=True, lab=False)
        _, restored = fixture._generation(external / f'{side}-restored', 'restored', 300 + index,
                                          version=env.engine.version, mvp_inputs=True, lab=False,
                                          profile=transition.endpoint_sensor.PROFILE)
        restored_binding = {'phase_generation': {**binding['phase_generation'], 'after': 'restored',
                                                  'recovered': 'restored'},
                            'sample_offsets_ms': {**binding['sample_offsets_ms'], 'recovered': 4500}}
        payload[checks[1]]['sides'][side] = _selected_side(env, [old, restored], restored_binding)

    if case == 'duplicate':
        payload[checks[1]]['check_id'] = checks[0]
    elif case == 'foreign-policy':
        other = confirmed.model_copy(update={'id': 'POL-OTHER-EXAMPLE'}).seal()
        payload[checks[1]]['policy_ref'] = other.ref().model_dump(mode='json')
    selections = []
    for check_id, document in payload.items():
        path = external / f'{check_id}-selection.json'
        path.write_bytes(canonical_json(document))
        digest = '0' * 64 if case == 'second-pin-changed' and check_id == checks[1] else _pointer(path)['sha256']
        selections.append((path, digest))

    output = external / 'assembled'
    if error is not None:
        with pytest.raises(ValueError, match=f'^{error}$'):
            materializer.prepare_many(policy_path, selections, output, pack=pack)
        assert not output.exists()
        return

    args = ['--pack', str(pack.path), '--policy', str(policy_path), '--output', str(output)]
    for path, digest in selections:
        args.extend(['--selection', str(path), '--selection-sha256', digest])
    materializer.main(args)
    config = tomllib.loads((output / materializer.CONFIG_NAME).read_text(encoding='utf-8'))
    index_path = output / materializer.INDEX_NAME
    index = json.loads(index_path.read_bytes())
    assert config['update'] == {'mode': 'external', 'allowed_checks': list(checks)}
    assert config['executors'] == {'lifecycle_transition': 'kubernetes'}
    assert {(item['check_id'], item['side']) for item in index['inputs']} == {
        (check_id, side) for check_id in checks for side in readiness.SIDES}
    for side in readiness.SIDES:
        settings = config['sides'][side]
        assert set(settings['check_config']) == set(settings['run_binding']) == set(checks)
        l05, l16 = (settings['check_config'][check_id] for check_id in checks)
        assert l05['generations'][0] == l16['generations'][0]
        assert l05[transition.GENERATION_MANIFEST_PIN] != l16[transition.GENERATION_MANIFEST_PIN]
        assert settings['run_binding']['LIF-05']['phase_generation']['after'] == 'new'
        assert settings['run_binding']['LIF-16']['phase_generation']['after'] == 'restored'
    report = readiness.audit(index_path, pack=pack)
    assert report['sides'] == 138 and report['file_binding_valid_sides'] == 4
    assert not report['engine_executed'] and not report['ready_for_execution'] and report['actual_results'] is None
    prepared = application.update_readiness(pack, envs, confirmed, config)
    assert next(row for row in prepared['checks'] if row['check'] == 'inputs')['ok'], prepared


@pytest.mark.parametrize('case,error', [
    ('prebound', None),
    ('obs11', None),
    ('obs11-missing-pins', 'MVP_TRANSITION_METADATA_INPUT_INVALID'),
    ('obs11-target-pin', 'MVP_TRANSITION_METADATA_RECORD_INPUT_MISMATCH'),
    ('obs11-session-pin', 'MVP_TRANSITION_METADATA_RECORD_INPUT_MISMATCH'),
    ('obs11-stray-binding', 'MVP_TRANSITION_METADATA_INPUT_INVALID'),
    ('obs11-staged', 'MVP_TRANSITION_PREBOUND_METADATA_REQUIRED'),
    ('lif12', None),
    ('lif12-without-optional-target', None),
    ('lif12-missing-pins', 'MVP_TRANSITION_METADATA_INPUT_INVALID'),
    ('lif12-session-pin', 'MVP_TRANSITION_METADATA_RECORD_INPUT_MISMATCH'),
    ('lif12-raw-target', 'MVP_TRANSITION_METADATA_RECORD_INPUT_MISMATCH'),
    ('lif12-changed-scope', 'MVP_TRANSITION_DECLARATION_FILE_CHANGED'),
    ('lif12-changed-producer', 'MVP_TRANSITION_DECLARATION_FILE_CHANGED'),
    ('lif12-canonical-target', 'MVP_TRANSITION_METADATA_RECORD_INPUT_MISMATCH'),
    ('lif12-late-scope-change', 'MVP_TRANSITION_DECLARATION_FILE_CHANGED'),
    ('staged', 'MVP_TRANSITION_PREBOUND_METADATA_REQUIRED'),
    ('duplicate', 'MVP_TRANSITION_METADATA_DUPLICATE'),
    ('other-policy', 'MVP_TRANSITION_METADATA_POLICY_MISMATCH'),
    ('other-env', 'MVP_TRANSITION_METADATA_ENVIRONMENT_MISMATCH'),
    ('changed-target', 'MVP_TRANSITION_DECLARATION_FILE_CHANGED'),
    ('scope-mismatch', 'MVP_TRANSITION_METADATA_INPUT_INVALID'),
    ('option-not-true', 'MVP_TRANSITION_METADATA_INPUT_INVALID'),
    ('output-alias', 'outside source inputs'),
])
def test_explicit_prebound_metadata_pair_keeps_its_own_pins_and_scope(external, monkeypatch, case, error):
    _offline_only(monkeypatch)
    check_id = 'LIF-16'
    include_status = case.startswith('obs11') or case == 'lif12'
    include_retry = case.startswith('lif12')
    if include_status or include_retry:
        _, mapping = declared_mapping()
        build_environment = fixture.environment

        def mapped_environment(*, model, **kwargs):
            model = {**model, 'id': mapping['repository_id'], 'revision': mapping['revision']}
            return build_environment(model=model, **kwargs)

        def no_status_read(*args, **kwargs):
            pytest.fail('Offline preparation attempted to read the OBS-11 collection-status file')

        monkeypatch.setattr(fixture, 'environment', mapped_environment)
        monkeypatch.setattr(kubernetes_log_integrity, '_read_collection_status', no_status_read)

    pack, envs, _, cfg = fixture._configuration(external, check_id, mvp_inputs=True, lab=False)
    policy_values = {'SUP-04': {'approved_image_digests': [env.engine.image_digest for env in envs]}}
    stock_checks = ['SUP-04']
    status_sessions = None
    if include_retry:
        stock_checks.append('LIF-12')
        retry_scope, retry_values, _, _ = retry_example()
        policy_values['LIF-12'] = {key: retry_values[key] for key in ('retry_policy_mode', 'retry_max_record_delay_ms')}
        policy_values['LIF-12']['retry_limits'] = retry_scope['limits']
    if include_status:
        stock_checks.append('OBS-11')
        policy_values['OBS-11'] = {key: value for key, value in GAP_VALUES.items() if key != 'collection_session'}
        status_sessions = {side: {'record_session': f'{number:032x}'}
                           for number, side in enumerate(readiness.SIDES, 1)}
    if case == 'option-not-true':
        stock_checks.append('PLT-06')
        policy_values['PLT-06'] = {'worker_isolation_requirement': 'independent-uid',
                                   'worker_trust_group_markers': ['VLLM', 'Worker']}
    confirmed = policy(pack.ref(), scope={'include': [*stock_checks, check_id]}, values=policy_values,
                       data_origin=DataOrigin.DECLARED, status='CONFIRMED',
                       confirmed_by='EXAMPLE operator', confirmed_at=T0)
    policy_path = external / 'policy.json'
    policy_path.write_bytes(canonical_json(confirmed.to_dict()))
    stock = external / 'separate-metadata'
    stock.mkdir()
    stock_policy = policy_path
    if case == 'other-policy':
        stock_policy = stock / 'policy.json'
        stock_policy.write_bytes(canonical_json(confirmed.model_copy(update={'id': 'POL-OTHER-FIXTURE'}).seal().to_dict()))
    sides, stock_sources = {}, {}
    for side, env in zip(readiness.SIDES, envs, strict=True):
        rows = cfg['sides'][side]['check_config'][check_id]['generations']
        sides[side] = _selected_side(env, rows, cfg['run_binding'][check_id])
        base = rows[0]
        target = json.loads(Path(base['target']).read_bytes())
        if include_status or include_retry:
            target['model_mapping'] = {**mapping, 'environment_ref': env.ref().model_dump(mode='json'),
                                       'inventory_sha256': env.model.inventory_sha256}
        env_path = Path(base['environment'])
        if case == 'other-env' and side == 'candidate':
            alternate = env.model_copy(update={'id': 'ENV-OTHER-FIXTURE'}).seal()
            env_path = stock / 'candidate-env.json'
            env_path.write_bytes(canonical_json(alternate.to_dict()))
            target['environment_ref'] = alternate.ref().model_dump(mode='json')
        path = stock / f'{side}-target.json'
        path.write_bytes(canonical_json(target))
        stock_sources[side] = {'target': path, 'environment': env_path}

    retry_bindings = None
    if include_retry:
        retry_bindings = _retry_pair(external, stock_sources, pack, stock_policy, confirmed, retry_scope)
        if case == 'lif12-without-optional-target':
            for declaration in retry_bindings.values():
                del declaration[RETRY_TARGET_BINDING]
    stock_config, stock_rows, decisions = metadata.assemble(
        stock_sources, stock_policy, pack=pack, store=stock / 'store',
        collection_status_bindings=status_sessions, retry_record_inputs=retry_bindings)
    assert stock_config['update']['allowed_checks'] == stock_checks
    if case == 'staged':
        stock_config = metadata.staged_config(stock_config, stock / 'later-target.json')
    if case == 'duplicate':
        stock_rows.append(stock_rows[0])
    elif case == 'scope-mismatch':
        stock_config['update']['allowed_checks'].append(check_id)
    elif case == 'option-not-true':
        stock_config['check_config']['PLT-06']['worker_trust_metadata_only'] = 1
    config_path = stock / 'guard.toml'
    index_path = stock / 'input-sources.json'
    record_checks = {'LIF-12', 'OBS-11'} & set(stock_checks)
    if record_checks:
        metadata._publish_prebound(stock, config=stock_config, bindings=stock_rows, checks=decisions,
                                   pack=pack, preparations=None, record_checks=record_checks)
        index = json.loads(index_path.read_bytes())
        modified_check = 'LIF-12' if include_retry else 'OBS-11'
        record = next(row for row in index['inputs'] if row['check_id'] == modified_check and row['side'] == 'candidate')
        if case in {'obs11-missing-pins', 'lif12-missing-pins'}:
            del record['record_inputs']
        elif case == 'obs11-target-pin':
            record['record_inputs']['target_file_sha256'] = '0' * 64
        elif case in {'obs11-session-pin', 'lif12-session-pin'}:
            record['record_inputs']['record_session_sha256'] = '0' * 64
        elif case == 'obs11-stray-binding':
            stock_config['sides']['candidate']['run_binding']['LIF-12'] = {'record_session': '3' * 32}
        elif case == 'obs11-staged':
            stock_config['update']['kubernetes_candidate_binding'] = 'after_baseline'
        elif case == 'lif12-canonical-target':
            binding = stock_config['sides']['candidate']['check_config']['LIF-12'][RETRY_TARGET_BINDING]
            binding['target_sha256'] = '0' * 64
        if case in {'obs11-stray-binding', 'obs11-staged', 'lif12-canonical-target'}:
            write_config(stock_config, config_path)
            config_pointer = _pointer(config_path)
            for row in index['inputs']:
                row['configuration'] = config_pointer
        if case in {'obs11-missing-pins', 'obs11-target-pin', 'obs11-session-pin', 'obs11-stray-binding',
                    'obs11-staged', 'lif12-missing-pins', 'lif12-session-pin', 'lif12-canonical-target'}:
            index_path.write_bytes(canonical_json(index))
    else:
        write_config(stock_config, config_path)
        index = readiness.template(pack)
        index['inputs'] = [{**row, 'configuration': _pointer(config_path)} for row in stock_rows]
        index_path.write_bytes(canonical_json(index))
    sources = {'index': _pointer(index_path), 'targets': {side: _pointer(paths['target'])
                                                        for side, paths in stock_sources.items()}}
    if case in {'changed-target', 'lif12-raw-target'}:
        target = stock_sources['candidate']['target']
        target.write_bytes(target.read_bytes() + b'\n')
        if case == 'lif12-raw-target':
            sources['targets']['candidate'] = _pointer(target)
    if case in {'lif12-changed-scope', 'lif12-changed-producer'}:
        key = 'retry_scope' if case == 'lif12-changed-scope' else 'record_producer'
        path = Path(retry_bindings['candidate'][key])
        path.write_bytes(path.read_bytes() + b'\n')
    elif case == 'lif12-late-scope-change':
        original_audit = readiness.audit

        def replace_after_audit(*args, **kwargs):
            result = original_audit(*args, **kwargs)
            path = Path(retry_bindings['candidate']['retry_scope'])
            path.write_bytes(path.read_bytes() + b'\n')
            return result

        monkeypatch.setattr(readiness, 'audit', replace_after_audit)

    selection = external / 'lifecycle-selection.json'
    selection.write_bytes(canonical_json({'schema': materializer.SELECTION_SCHEMA, 'check_id': check_id,
                                          'policy_ref': confirmed.ref().model_dump(mode='json'), 'sides': sides}))
    selections = [(selection, _pointer(selection)['sha256'])]
    output = (stock if case == 'output-alias' else external) / 'joint'
    if error is not None:
        with pytest.raises(ValueError, match=error):
            materializer.prepare_many(policy_path, selections, output, pack=pack, prebound_metadata=sources)
        assert not output.exists()
        return

    declaration = external / 'prebound-metadata.json'
    declaration.write_bytes(canonical_json(sources))
    materializer.main(['--pack', str(pack.path), '--policy', str(policy_path), '--output', str(output),
        '--selection', str(selection), '--selection-sha256', selections[0][1],
        '--prebound-metadata', str(declaration), '--prebound-metadata-sha256', _pointer(declaration)['sha256']])
    materialized = tomllib.loads((output / materializer.CONFIG_NAME).read_text(encoding='utf-8'))
    joined = json.loads((output / materializer.INDEX_NAME).read_bytes())
    assert materialized['update']['allowed_checks'] == [*stock_checks, check_id]
    for side in readiness.SIDES:
        settings = materialized['sides'][side]
        assert settings['container_config'] == stock_config['sides'][side]['container_config']
        assert set(settings['check_config']) == {check_id} | (record_checks & {'LIF-12'})
        assert set(settings['run_binding']) == {check_id} | record_checks
        if include_status:
            assert settings['log_integrity'] == stock_config['sides'][side]['log_integrity']
            assert settings['run_binding']['OBS-11'] == status_sessions[side]
        if include_retry:
            assert settings['request_termination'] == stock_config['sides'][side]['request_termination']
            assert settings['check_config']['LIF-12'] == stock_config['sides'][side]['check_config']['LIF-12']
            assert settings['run_binding']['LIF-12'] == {
                key: retry_bindings[side][key] for key in ('record_session', 'record_producer')}
    assert all(item['configuration'] == _pointer(output / 'guard.toml') for item in joined['inputs'])
    for item in joined['inputs']:
        if item['check_id'] in stock_checks:
            assert item['target'] == sources['targets'][item['side']]
        else:
            assert 'target' not in item
        if item['check_id'] in record_checks:
            assert item['record_inputs'] == decisions[item['check_id']]['prepared_record_input_sha256_by_side'][item['side']]
        else:
            assert 'record_inputs' not in item
    assert _pointer(index_path) == sources['index'] and _pointer(config_path) == index['inputs'][0]['configuration']
    report = readiness.audit(output / materializer.INDEX_NAME, pack=pack)
    expected_bound = 2 * (len(stock_checks) + 1)
    assert report['sides'] == 138 and report['file_binding_valid_sides'] == expected_bound
    for record_check in record_checks:
        assert {row['side']: row['record_inputs'] for row in report['rows'] if row['check_id'] == record_check} == (
            decisions[record_check]['prepared_record_input_sha256_by_side'])
    assert not report['engine_executed'] and not report['ready_for_execution'] and report['actual_results'] is None
    prepared = application.update_readiness(pack, envs, confirmed, materialized)
    assert next(row for row in prepared['checks'] if row['check'] == 'inputs')['ok'], prepared

    if include_retry:
        for role in ('retry_scope', 'record_producer'):
            path = Path(retry_bindings['candidate'][role])
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            changed = readiness.audit(output / materializer.INDEX_NAME, pack=pack)
            assert changed['file_binding_valid_sides'] == expected_bound - 1
            retry_row = next(row for row in changed['rows'] if row['check_id'] == 'LIF-12' and row['side'] == 'candidate')
            assert not retry_row['file_binding_valid']
            path.write_bytes(original)

    target = stock_sources['candidate']['target']
    original = target.read_bytes()
    target.write_bytes(original + b'\n')
    changed = readiness.audit(output / materializer.INDEX_NAME, pack=pack)
    assert changed['file_binding_valid_sides'] == expected_bound - len(stock_checks)
    stock_row = next(row for row in changed['rows'] if row['check_id'] == 'SUP-04' and row['side'] == 'candidate')
    assert not stock_row['file_binding_valid']

    target.write_bytes(original)
    alternate = stock / 'same-bytes-different-path.json'
    alternate.write_bytes(original)
    pointer = next(row for row in joined['inputs'] if row['check_id'] == 'SUP-04' and row['side'] == 'candidate')
    pointer['target'] = _pointer(alternate)
    assert pointer['target']['sha256'] == sources['targets']['candidate']['sha256']
    wrong_index = output / 'wrong-index.json'
    wrong_index.write_bytes(canonical_json(joined))
    wrong_path = readiness.audit(wrong_index, pack=pack)
    assert wrong_path['file_binding_valid_sides'] == expected_bound - 1
    stock_row = next(row for row in wrong_path['rows'] if row['check_id'] == 'SUP-04' and row['side'] == 'candidate')
    assert not stock_row['file_binding_valid']
