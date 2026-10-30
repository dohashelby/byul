"""원 PACK31 REQ09 수동 연결의 EXAMPLE 계약 시험. 실제 source CPU 실행 0회."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.core.select import select
from guard.models import DataOrigin
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import baseline_pack, environment, policy, PACK_DIR
from tools import b1_input_integration_cpu as pipeline

PRIVATE = 'REQ09_EXAMPLE_PRIVATE_EXCEPTION_DO_NOT_SAVE'
HASH = 'a' * 64
COLLECTION = 'req09-target031-cpu-' + 'b' * 32


def metadata(base, *, admitted=False):
    value = deepcopy(base)
    value['canonical_sha256'] = pipeline.req09_product.digest(base)
    if admitted:
        value.update(admission_completed=True, processor_input_sha256=value['canonical_sha256'],
                     processor_output_sha256=value['canonical_sha256'])
    return value


def fixture_input(case):
    base = dict(prompt_count=3, token_ids_canonical_sha256=None, embedding_shape=None,
        embedding_dtype=None, embedding_bytes_sha256=None, mask_count=None,
        mask_canonical_sha256=None, routed_experts_prompt_start=0)
    if case['kind'] == 'offset':
        base.update(token_ids_canonical_sha256=pipeline.req09_product.digest([3, 4, 5]),
                     routed_experts_prompt_start=case['value'])
    else:
        shape = case['value']['shape']
        byte_count = 4
        for size in shape: byte_count *= size
        base.update(embedding_shape=shape, embedding_dtype='torch.float32',
                     embedding_bytes_sha256=hashlib.sha256(bytes(byte_count)).hexdigest())
        if case['kind'] == 'mask':
            length = case['value']['mask_length']
            base.update(token_ids_canonical_sha256=pipeline.req09_product.digest([3, 4, 5]),
                         mask_count=length, mask_canonical_sha256=pipeline.req09_product.digest([False] * length))
    return base


def example_record():
    valid = {'pre': 'verified', 'post': {'status': 'verified'}, 'boundary': 'source_fixture_component'}
    after = dict(prompt_count=2, token_ids_canonical_sha256=pipeline.req09_product.digest([3, 4]),
        embedding_shape=None, embedding_dtype=None, embedding_bytes_sha256=None, mask_count=None,
        mask_canonical_sha256=None, routed_experts_prompt_start=0)
    rows = []
    for case in pipeline.req09_product.fixtures():
        if case['check_id'] != 'REQ-09': continue
        base = fixture_input(case)
        outcome = case['expected'] or 'accepted'
        rows.append({**pipeline.req09_product.describe(case), 'source_profile': 'target031',
            'source_commit': pipeline.SOURCE_PROFILES['target031'], 'collection_id': COLLECTION,
            'source_validation': deepcopy(valid), 'outcome': outcome, 'events': ['process_inputs', 'validate_model_input'],
            'input_metadata': metadata(base), 'admitted_metadata': metadata(base, admitted=True) if outcome == 'accepted' else None,
            'after': {'outcome': 'accepted', 'events': ['render_cmpl', 'process_inputs'],
                      'input_metadata': metadata(after), 'admitted_metadata': metadata(after, admitted=True)}})
    return {'profile': pipeline.REQ09_PROFILE, 'source_profile': 'target031',
        'source_commit': pipeline.SOURCE_PROFILES['target031'], 'scope': 'source_cpu_input_processor_boundary_only',
        'data_origin': 'DECLARED', 'installed_target': None, 'runner_consumption_verified': False,
        'collection_id': COLLECTION, 'own_producer_sha256': HASH,
        'started_at': '2026-10-30T01:00:00+00:00', 'finished_at': '2026-10-30T01:00:01+00:00',
        'fixture_config': {'origin': 'DECLARED', 'vocab_size': 32, 'max_model_len': 16, 'hidden_size': 4},
        'effective_config': {'vocab_size': 32, 'max_model_len': 16, 'hidden_size': 4},
        'source_validation': valid, 'config_file_bytes_sha256': {'config.json': 'c' * 64, 'tokenizer.json': 'd' * 64},
        'capture_status': 'completed', 'auxiliary_write': {'status': 'completed'}, 'results': rows}


def row(raw, case_id):
    return next(item for item in raw['results'] if item['case_id'] == case_id)


def admit(raw, case_id):
    target = row(raw, case_id)
    target['outcome'] = 'accepted'
    target['admitted_metadata'] = metadata({key: target['input_metadata'][key] for key in pipeline._REQ09_META}, admitted=True)
    return target


@pytest.fixture
def case(tmp_path, monkeypatch):
    pack = baseline_pack()
    env = environment(engine={'name': 'vllm', 'version': 'source-' + pipeline.SOURCE_PROFILES['target031']})
    pol = policy(pack.ref(), values={'REQ-09': {'max_input_tokens': 16, 'rejection_codes': ['400', '422'],
        'allowed_endpoints': ['POST /v1/completions'], 'allowed_input_formats': ['token_ids']}},
        scope={'include': ['REQ-09'], 'exclude': []})
    source = Store(tmp_path / 'source')
    source.put_many([env, pol]); put_pack(source, PACK_DIR)
    plan = select(pack, [env], pol); source.put(plan)
    raw, captures, gates = example_record(), [], []
    def capture(source_root, output):
        assert env.data_origin == DataOrigin.EXAMPLE
        captures.append((source_root, output))
        return deepcopy(raw)
    def gate(repo):
        assert env.data_origin == DataOrigin.EXAMPLE
        gates.append(repo)
    # 승인 bytes 대역은 EXAMPLE ENV 계약에만 사용한다. 실제 gate/pin은 제품에서 그대로다.
    monkeypatch.setattr(pipeline, 'COLLECTOR_SHA256', HASH)
    monkeypatch.setattr(pipeline, 'verify_runtime_components', gate)
    monkeypatch.setattr(pipeline.collector, 'capture_req09_target031', capture, raising=False)
    args = dict(source_root=tmp_path / 'EXAMPLE-source', output_root=tmp_path / 'output',
        source_store=source.root, plan_id=plan.id, pack_path=PACK_DIR, entered_by='EXAMPLE-operator')
    return dict(pack=pack, env=env, pol=pol, source=source, plan=plan, raw=raw,
                captures=captures, gates=gates, args=args)


def evd(case):
    return next(Store.read_only(case['args']['output_root'] / 'store').iter_kind('EVD'))


def test_original_req09_definition_scope_and_public_cli_roundtrip(case):
    before = pipeline._req09_inventory(case['source'].root)
    result = pipeline.run_req09_pipeline(**case['args'])
    assert result['pack'] == pipeline.REQ09_PACK_REF and result['capture_invocations'] == 1
    assert result['confirmed_input_violations'] == [] and result['checks']['REQ-09']['verdicts'] == {
        'security': 'UNKNOWN', 'functional': 'UNKNOWN', 'performance': 'UNKNOWN'}
    assert len(case['captures']) == 1 and len(case['gates']) == 2
    assert pipeline._req09_inventory(case['source'].root) == before
    evidence = evd(case)
    assert evidence.data_origin == DataOrigin.DECLARED and evidence.plan == case['plan'].ref()
    assert evidence.environment == case['env'].ref() and evidence.execution.override_reason == pipeline.REQ09_OVERRIDE
    assert not evidence.normal_control.attempted and all(not step.attempted for step in evidence.challenges)
    assert evidence.observations['confirmed_input_violations'].collected
    assert evidence.observations['confirmed_input_violations'].value == []
    for key in pipeline._REQ09_OBSERVATIONS.keys() - {'confirmed_input_violations'}:
        assert evidence.observations[key].value is None and not evidence.observations[key].collected
    form = pipeline.yaml.safe_load((case['args']['output_root'] / 'manual-evidence.yaml').read_text())
    (entry,) = form['checks']; original = case['pack'].checks['REQ-09']
    assert entry['question'] == original.question
    assert [item['step'] for item in entry['challenges']] == original.challenge_steps
    assert [note['item'] for note in entry['observation_notes']] == original.required_observations_prose
    assert form['policy'] == case['pol'].ref().model_dump(mode='json')
    for directory in ('store', 'restored'):
        check = CliRunner().invoke(app, ['verify', '--store', str(case['args']['output_root'] / directory)])
        assert check.exit_code == 0, check.output


@pytest.mark.parametrize('case_id', ['mask-0', 'mask-1', 'mask-2', 'mask-4', 'offset--1', 'offset-4', 'offset-170141183460469231731687303715884105728'])
def test_only_actual_bad_mask_or_offset_admission_is_independent_fail(case, case_id):
    admit(case['raw'], case_id)
    result = pipeline.run_req09_pipeline(**case['args'])
    assert result['confirmed_input_violations'] == [case_id]
    assert result['checks']['REQ-09']['verdicts']['security'] == 'FAIL'
    assert result['checks']['REQ-09']['verdicts']['functional'] == 'UNKNOWN'
    assert evd(case).observations['input_endpoints_tested'].value is None


@pytest.mark.parametrize('change', ['shape', 'normalized_mask', 'normalized_offset', 'source_mismatch', 'post_missing', 'post_invalid', 'duplicate', 'fixture', 'input_hash', 'no_trace'])
def test_unknown_without_exact_supported_violation(case, change):
    target = admit(case['raw'], 'offset--1')
    if change == 'shape':
        target['outcome'] = 'rejected'
        assert row(case['raw'], 'shape-3')['expected'] is None
    elif change in ('normalized_mask', 'normalized_offset'):
        if change == 'normalized_mask':
            target['outcome'] = 'rejected'; target = admit(case['raw'], 'mask-1')
            target['admitted_metadata'].update(mask_count=3, mask_canonical_sha256=pipeline.req09_product.digest([False] * 3))
        else: target['admitted_metadata']['routed_experts_prompt_start'] = 0
        base = {key: target['admitted_metadata'][key] for key in pipeline._REQ09_META}
        target['admitted_metadata'].update(canonical_sha256=pipeline.req09_product.digest(base),
                                          processor_output_sha256=pipeline.req09_product.digest(base))
    elif change == 'source_mismatch': target['source_validation']['post']['status'] = 'mismatch'
    elif change == 'post_missing': target['source_validation']['post'] = {}
    elif change == 'post_invalid': target['source_validation']['post']['status'] = 'invalid'
    elif change == 'duplicate': case['raw']['results'].append(deepcopy(target)); case['raw']['results'].pop(0)
    elif change == 'fixture': target['fixture_canonical_sha256'] = '0' * 64
    elif change == 'input_hash': target['input_metadata']['canonical_sha256'] = '0' * 64
    else: target['events'] = []
    result = pipeline.run_req09_pipeline(**case['args'])
    assert result['checks']['REQ-09']['verdicts']['security'] == 'UNKNOWN'
    assert not result['confirmed_input_violations']


@pytest.mark.parametrize('case_id', ['mask-1', 'offset--1'])
def test_known_invalid_admission_survives_unavailable_later_source_check(case, case_id):
    target = admit(case['raw'], case_id)
    target['source_validation']['post']['status'] = 'unavailable'
    result = pipeline.run_req09_pipeline(**case['args'])
    assert result['confirmed_input_violations'] == [case_id]
    assert result['checks']['REQ-09']['verdicts']['security'] == 'FAIL'
    evidence = evd(case)
    assert evidence.observations['confirmed_input_violations'].value == [case_id]
    assert evidence.observations['declared_verdicts'].value['security'] == 'FAIL'
    artifact = json.loads((case['args']['output_root'] / 'req09-input-cpu.json').read_text())
    assert artifact['capture_status'] == 'incomplete' and artifact['full_scope_coverage'] is False
    assert next(item for item in artifact['results'] if item['case_id'] == case_id)['qualified']


@pytest.mark.parametrize('late', ['after', 'top_post', 'artifact', 'component_post', 'export'])
def test_qualified_fail_survives_later_independent_failure(case, monkeypatch, late):
    target = admit(case['raw'], 'offset--1')
    if late == 'after': target['after'] = {'outcome': 'execution_error', 'events': [], 'error': PRIVATE}
    elif late == 'top_post': case['raw']['source_validation']['post']['status'] = 'unavailable'
    elif late == 'artifact':
        original = pipeline._write_artifact
        def write(path, record):
            if path.name == 'req09-input-cpu.json': raise OSError(PRIVATE)
            return original(path, record)
        monkeypatch.setattr(pipeline, '_write_artifact', write)
    elif late == 'component_post':
        def gate(repo):
            case['gates'].append(repo)
            if len(case['gates']) > 1: raise ValueError(PRIVATE)
        monkeypatch.setattr(pipeline, 'verify_runtime_components', gate)
    else:
        original = pipeline.export_bundle
        def export(source, output):
            if output.name == 'evidence.zip': raise OSError(PRIVATE)
            return original(source, output)
        monkeypatch.setattr(pipeline, 'export_bundle', export)
    if late == 'export':
        with pytest.raises(OSError): pipeline.run_req09_pipeline(**case['args'])
    else:
        assert pipeline.run_req09_pipeline(**case['args'])['checks']['REQ-09']['verdicts']['security'] == 'FAIL'
    evidence = evd(case)
    assert evidence.observations['confirmed_input_violations'].value == ['offset--1']
    assert evidence.observations['declared_verdicts'].value['security'] == 'FAIL'


def test_raw_private_fields_are_not_in_attached_or_manual_bytes(case):
    case['raw'].update(exception=PRIVATE, private=PRIVATE)
    for item in case['raw']['results']:
        item.update(private=PRIVATE, error_type=PRIVATE)
        item['input_metadata']['private'] = PRIVATE
        item['after']['private'] = PRIVATE
    pipeline.run_req09_pipeline(**case['args'])
    for path in (case['args']['output_root'] / 'store/artifacts').iterdir():
        assert PRIVATE.encode() not in path.read_bytes()


@pytest.mark.parametrize('invalid', ['model', 'image', 'version', 'policy', 'output'])
def test_invalid_input_closes_before_collector(case, invalid):
    if invalid == 'output': case['args']['output_root'].mkdir()
    elif invalid == 'policy':
        changed = case['pol'].model_copy(update={'revision': 2, 'values': {}}).seal()
        case['source'].put(changed)
        new_plan = select(case['pack'], [case['env']], changed); case['source'].put(new_plan)
        case['args']['plan_id'] = new_plan.id
    else:
        values = case['env'].model_dump()
        values.update(revision=2)
        if invalid == 'model': values['model']['id'] = 'EXAMPLE-model'
        elif invalid == 'image': values['engine']['image_digest'] = 'example/engine@sha256:' + 'e' * 64
        else: values['engine']['version'] = '0.31.0'
        changed = type(case['env']).model_validate(values).seal(); case['source'].put(changed)
        new_plan = select(case['pack'], [changed], case['pol']); case['source'].put(new_plan)
        case['args']['plan_id'] = new_plan.id
    with pytest.raises(ValueError): pipeline.run_req09_pipeline(**case['args'])
    assert not case['captures']


def test_output_is_fresh_and_previous_refs_are_retained(case):
    pipeline.run_req09_pipeline(**case['args'])
    first = evd(case)
    original_store = case['args']['output_root'] / 'store'
    before = pipeline._req09_inventory(original_store)
    second_args = {**case['args'], 'source_store': original_store, 'output_root': case['args']['output_root'].parent / 'second'}
    pipeline.run_req09_pipeline(**second_args)
    copied = Store.read_only(second_args['output_root'] / 'store')
    assert copied.resolve(first.ref()).ref() == first.ref() and copied.latest(first.id).revision == first.revision + 1
    assert pipeline._req09_inventory(original_store) == before
    with pytest.raises(ValueError): pipeline.run_req09_pipeline(**second_args)


def test_cli_keeps_legacy_default_and_selects_only_explicit_req09(monkeypatch, tmp_path):
    calls = []
    response = {'checks': {'REQ-09': {'execution_status': 'COMPLETED'}}, 'store_verify_errors': []}
    monkeypatch.setattr(pipeline, 'run_pipeline', lambda *args, **kwargs: calls.append(('legacy', kwargs)) or response)
    monkeypatch.setattr(pipeline, 'run_req09_pipeline', lambda *args, **kwargs: calls.append(('req09', kwargs)) or response)
    base = ['tool', '--source-root', str(tmp_path), '--output-root', str(tmp_path / 'new')]
    monkeypatch.setattr(sys, 'argv', base)
    assert pipeline.main() == 0 and calls[-1] == ('legacy', {'source_profile': 'reference5d'})
    monkeypatch.setattr(sys, 'argv', base + ['--check', 'REQ-09', '--source-profile', 'target031',
        '--source-store', str(tmp_path), '--plan', 'PLAN-EXAMPLE', '--pack', str(PACK_DIR), '--entered-by', 'EXAMPLE'])
    assert pipeline.main() == 0 and calls[-1][0] == 'req09'
