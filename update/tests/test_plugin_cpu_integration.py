"""EXAMPLE producer/core contracts; live captures run after joint source freeze."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from guard.executors import plugin_loading, registry
from guard.models import DataOrigin, Environment, ExecStatus
from guard.store import Store
from tools import b1_plugin_integration_cpu as pipeline

CANARY = 'SUP10_PRIVATE_EXCEPTION_DO_NOT_SAVE'
EXAMPLE_PRODUCER_SHA256 = 'a' * 64


def example_record(source_profile='reference5d'):
    rows = []
    for case, group, configured in plugin_loading.CASES:
        rows.append({'case_id': case, 'group': group, 'configured_names': configured,
            'status': 'completed', 'target_started': True, 'event_collection_status': 'completed',
            'events': [], 'after': {'status': 'completed', 'events': [
                {'plugin': 'byul-approved', 'phase': 'general_hook'}]}, 'private': CANARY})
    return {'source_profile': source_profile, 'source_commit': pipeline.SOURCE_PROFILES[source_profile],
        'capture_status': 'completed', 'installed_target': None, 'own_producer_sha256': EXAMPLE_PRODUCER_SHA256,
        'source_validation': {'pre': 'verified', 'post': {'status': 'verified', 'exception': CANARY},
                              'boundary': 'source_fixture_component'},
        'fixture_file_bytes_sha256': {'byul_b_cpu_probe/approved.py': 'b' * 64},
        'results': rows, 'observations': {'raw_unused': CANARY}, 'exception': CANARY}


def violation(record, *, later_failure=False):
    row = next(row for row in record['results'] if row['case_id'] == 'general-disabled')
    row['events'] = [{'plugin': 'byul-unapproved', 'phase': 'import'}]
    if later_failure:
        row['status'] = 'execution_error'
        row['after'] = {'status': 'execution_error', 'exception': CANARY}
        record['capture_status'] = 'incomplete'
    return record


def example_env(source_profile='reference5d', **changes):
    values = {'id': 'ENV-EXAMPLE-PLUGIN-CPU', 'created_at': datetime.now(timezone.utc),
        'data_origin': DataOrigin.EXAMPLE, 'engine': {'name': 'vllm',
        'version': 'reference-' + pipeline.SOURCE_PROFILES[source_profile]}}
    values.update(changes)
    return Environment(**values).seal()


def private_draft(tmp_path, record, monkeypatch, *, env=None, source_profile='reference5d'):
    pack = pipeline.proposal_pack(tmp_path / 'pack', source_profile)
    def capture(source_root, workdir, *, source_profile):
        assert isinstance(source_root, Path) and isinstance(workdir, Path)
        assert source_profile == record['source_profile']
        return deepcopy(record)
    return pipeline._produce_draft(pack.checks['SUP-10'], env or example_env(source_profile), tmp_path / 'work',
        source_root=tmp_path / 'source', source_profile=source_profile,
        capture_fn=capture, producer_sha256=EXAMPLE_PRODUCER_SHA256)


def test_private_projection_reuses_product_rules_and_redacts_extras(tmp_path, monkeypatch):
    record = example_record()
    # These phases are observations in the existing product, not new policy FAILs.
    record['results'][7]['events'] = [{'plugin': 'byul-unapproved', 'phase': 'import'}]
    draft = private_draft(tmp_path, record, monkeypatch)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert draft.normal_control.succeeded is True
    assert draft.observations['plugin_loading_confirmed_violations'].value == []
    assert draft.observations['runner_hook_verified'].value is False
    assert draft.config['installed_target'] is None
    assert len(draft.artifacts) == 1
    assert CANARY not in draft.artifacts[0].path.read_text()


@pytest.mark.parametrize('post_status', ['unavailable', 'mismatch'])
def test_independent_violation_survives_postcheck_failure(tmp_path, monkeypatch, post_status):
    record = violation(example_record())
    record['capture_status'] = 'incomplete'
    record['source_validation']['post']['status'] = post_status
    draft = private_draft(tmp_path, record, monkeypatch)
    assert draft.status == ExecStatus.ABORTED
    assert draft.observations['plugin_loading_confirmed_violations'].value == ['general-disabled']
    assert draft.normal_control.succeeded is False
    assert CANARY not in draft.artifacts[0].path.read_text()


def test_local_artifact_failure_keeps_violation(tmp_path, monkeypatch):
    def unavailable(*args):
        raise OSError(CANARY)
    monkeypatch.setattr(pipeline, '_write_artifact', unavailable)
    draft = private_draft(tmp_path, violation(example_record()), monkeypatch)
    assert draft.status == ExecStatus.ABORTED and draft.abort_reason == 'ARTIFACT_CAPTURE_INCOMPLETE'
    assert not draft.artifacts
    assert draft.observations['plugin_loading_confirmed_violations'].value == ['general-disabled']
    assert CANARY not in draft.model_dump_json()


@pytest.mark.parametrize('case', ['source_profile', 'source_commit', 'producer_hash', 'precheck', 'installed_target'])
def test_wrong_record_attribution_is_uncollected(tmp_path, monkeypatch, case):
    record = violation(example_record())
    if case == 'source_profile':
        record['source_profile'] = 'target031'
    elif case == 'source_commit':
        record['source_commit'] = pipeline.SOURCE_PROFILES['target031']
    elif case == 'producer_hash':
        record['own_producer_sha256'] = '0' * 64
    elif case == 'precheck':
        record['source_validation']['pre'] = 'unavailable'
    else:
        record['installed_target'] = {'raw': CANARY}
    # This capture explicitly returns the mismatched record; no external file is loaded.
    pack = pipeline.proposal_pack(tmp_path / 'pack', 'reference5d')
    draft = pipeline._produce_draft(pack.checks['SUP-10'], example_env(), tmp_path / 'work',
        source_root=tmp_path / 'source', source_profile='reference5d',
        capture_fn=lambda *a, **kw: record, producer_sha256=EXAMPLE_PRODUCER_SHA256)
    assert draft.status == ExecStatus.ABORTED and not draft.observations and not draft.artifacts
    assert CANARY not in draft.model_dump_json()


@pytest.mark.parametrize('engine', [{'name': 'other', 'version': 'reference-' + pipeline.SOURCE_PROFILES['reference5d']},
    {'name': 'vllm', 'version': 'reference-' + pipeline.SOURCE_PROFILES['target031']}])
def test_wrong_environment_blocks_capture(tmp_path, engine):
    pack = pipeline.proposal_pack(tmp_path / 'pack', 'reference5d')
    with pytest.raises(ValueError, match='SUP10_CPU_ENVIRONMENT_MISMATCH'):
        pipeline._produce_draft(pack.checks['SUP-10'], example_env(engine=engine), tmp_path / 'work',
            source_root=tmp_path / 'source', source_profile='reference5d',
            capture_fn=lambda *a, **kw: pytest.fail('capture for a mismatched ENV'),
            producer_sha256=EXAMPLE_PRODUCER_SHA256)


def test_environment_hash_and_source_profile_are_checked_before_capture(tmp_path):
    pack = pipeline.proposal_pack(tmp_path / 'pack', 'reference5d')
    env = example_env().model_copy(update={'content_sha256': '0' * 64})
    with pytest.raises(ValueError, match='SUP10_CPU_ENVIRONMENT_MISMATCH'):
        pipeline._produce_draft(pack.checks['SUP-10'], env, tmp_path / 'work',
            source_root=tmp_path / 'source', source_profile='reference5d', capture_fn=lambda *a, **kw: pytest.fail(),
            producer_sha256=EXAMPLE_PRODUCER_SHA256)
    with pytest.raises(ValueError, match='SUP10_SOURCE_PROFILE_UNSUPPORTED'):
        pipeline.proposal_pack(tmp_path / 'unsupported', 'unsupported')


@pytest.mark.parametrize('scenario,security,execution', [
    ('normal', 'UNKNOWN', 'COMPLETED'), ('violation', 'FAIL', 'COMPLETED'),
    ('uncollected', 'UNKNOWN', 'ABORTED'), ('unsupported', 'UNKNOWN', 'ABORTED'),
    ('violation_after_failure', 'FAIL', 'ABORTED'), ('artifact_failure', 'FAIL', 'ABORTED'),
])
def test_example_capture_core_refs_privacy_and_registry_restoration(tmp_path, monkeypatch, scenario, security, execution):
    record = example_record('target031')
    if scenario in ('violation', 'violation_after_failure', 'artifact_failure'):
        violation(record, later_failure=scenario == 'violation_after_failure')
    if scenario == 'unsupported':
        record['capture_status'] = 'incomplete'
        for row in record['results']:
            row.update(status='unsupported', target_started=False, after={'status': 'execution_error'})
    def capture(*args, **kwargs):
        if scenario == 'uncollected':
            raise RuntimeError(CANARY)
        assert kwargs == {'source_profile': 'target031'}
        return deepcopy(record)
    monkeypatch.setattr(pipeline, 'verify_runtime_components', lambda repo: None)
    monkeypatch.setattr(pipeline, 'COLLECTOR_SHA256', EXAMPLE_PRODUCER_SHA256)
    monkeypatch.setattr(pipeline.collector, 'capture', capture, raising=False)
    if scenario == 'artifact_failure':
        original_put_artifact = Store.put_artifact
        def unavailable_plugin_artifact(store, data):
            try:
                value = json.loads(data)
            except (ValueError, UnicodeError):
                return original_put_artifact(store, data)
            if isinstance(value, dict) and value.get('profile') == pipeline.PROFILE:
                raise OSError(CANARY)
            return original_put_artifact(store, data)
        monkeypatch.setattr(Store, 'put_artifact', unavailable_plugin_artifact)
    before = dict(registry.EXECUTORS)
    output = tmp_path / 'EXAMPLE-pipeline'
    summary = pipeline.run_pipeline(tmp_path / 'source', output, source_profile='target031', origin=DataOrigin.EXAMPLE)
    assert registry.EXECUTORS == before
    assert summary['execution_status'] == execution and summary['data_origin'] == 'EXAMPLE'
    assert summary['verdicts']['security'] == security
    assert summary['runner_hook_verified'] is False and summary['installed_target'] is None
    assert summary['permanent_registration_verified'] is False and summary['exact_ref_rejudgment_unchanged']
    assert summary['store_verify_errors'] == []
    assert not json.loads((output / 'RPT.json').read_text())['official']
    for path in output.rglob('*'):
        if path.is_file():
            assert CANARY not in path.read_text(errors='replace')
    # The reused product judge has no functional PASS rule (INV-18).
    assert summary['verdicts']['functional'] == 'UNKNOWN'
    if scenario == 'normal':
        assert summary['artifact_count'] == 1
        assert json.loads((output / 'EVD.json').read_text())['normal_control']['succeeded'] is True


def _module_file(root, name, function):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('def ' + function + '(*args, **kwargs): return None\n')
    namespace = {}
    exec(compile(path.read_text(), str(path), 'exec'), namespace)
    return path, namespace[function]


@pytest.fixture
def runtime_gate(tmp_path, monkeypatch):
    gate_path, gate = _module_file(tmp_path, 'update/tools/lif14_profiler_cpu.py', 'verify_component')
    collector_path, capture = _module_file(tmp_path, 'update/tools/b1_plugin_cpu.py', 'capture')
    called = []
    # Compile a recording gate with the same own filename.
    namespace = {'called': called}
    exec(compile('def verify_component(repo): called.append(repo)\n', str(gate_path), 'exec'), namespace)
    monkeypatch.setattr(pipeline, 'component_gate', SimpleNamespace(__file__=str(gate_path),
        verify_component=namespace['verify_component'], PRODUCT_COMPONENT_COMMIT=pipeline.COMPONENT_COMMIT))
    monkeypatch.setattr(pipeline, 'collector', SimpleNamespace(__file__=str(collector_path), capture=capture))
    monkeypatch.setattr(pipeline, 'GATE_SHA256', hashlib.sha256(gate_path.read_bytes()).hexdigest())
    monkeypatch.setattr(pipeline, 'COLLECTOR_SHA256', hashlib.sha256(collector_path.read_bytes()).hexdigest())
    return tmp_path, gate_path, collector_path, called


def test_runtime_gate_checks_tools_then_reuses_component_guard(runtime_gate):
    root, _, _, called = runtime_gate
    pipeline.verify_runtime_components(root)
    assert called == [root]


@pytest.mark.parametrize('mutation', ['gate_bytes', 'gate_function', 'collector_bytes', 'collector_function', 'unconfigured'])
def test_foreign_or_unpinned_tool_cannot_reach_component_guard(runtime_gate, monkeypatch, mutation):
    root, gate_path, collector_path, called = runtime_gate
    if mutation == 'gate_bytes':
        gate_path.write_text('different gate bytes\n')
    elif mutation == 'collector_bytes':
        collector_path.write_text('different collector bytes\n')
    elif mutation == 'unconfigured':
        monkeypatch.setattr(pipeline, 'COLLECTOR_SHA256', None)
    else:
        _, foreign = _module_file(root, 'foreign.py', 'capture')
        if mutation == 'gate_function':
            monkeypatch.setattr(pipeline.component_gate, 'verify_component', foreign)
        else:
            monkeypatch.setattr(pipeline.collector, 'capture', foreign)
    with pytest.raises(ValueError, match='SUP10_'):
        pipeline.verify_runtime_components(root)
    assert not called


def test_existing_component_scope_and_helper_pin_are_unchanged():
    assert pipeline.component_gate.HELPER_SHA256 == '858666f5595fa6328a2975ef5a91eb9db19ad0712d11be08f64d8296cadaf6bf'
    assert pipeline.component_gate.PINNED_PATHS == ('update/src/guard', 'update/packs', 'update/uv.lock',
        'update/pyproject.toml', 'update/reference/framework')
    assert pipeline.component_gate.PRODUCT_COMPONENT_COMMIT == pipeline.COMPONENT_COMMIT


@pytest.mark.parametrize('flag', ['-O', '-OO'])
def test_optimized_actual_entry_rejects_before_missing_source(tmp_path, flag):
    script = Path(pipeline.__file__).resolve()
    output = tmp_path / 'must-not-exist'
    env = {'PYTHONPATH': str(script.parents[1]) + ':' + str(script.parents[1] / 'src')}
    import os
    result = subprocess.run([sys.executable, flag, str(script), '--source-root', str(tmp_path / 'missing'),
        '--output-root', str(output)], env={**os.environ, **env}, capture_output=True, text=True)
    assert result.returncode != 0 and 'SUP10_CPU_REQUIRES_NORMAL_PYTHON312' in result.stderr
    assert not output.exists()



def test_missing_followup_cannot_claim_completed_capture(tmp_path, monkeypatch):
    record = violation(example_record())
    record['results'][0].pop('after')
    draft = private_draft(tmp_path, record, monkeypatch)
    assert draft.status == ExecStatus.ABORTED and draft.normal_control.succeeded is False
    assert draft.observations['plugin_loading_confirmed_violations'].value == ['general-disabled']


def test_runtime_postcheck_keeps_prior_artifact_failure_and_facts(tmp_path, monkeypatch):
    record = violation(example_record())
    calls = []
    def component(repo):
        calls.append(repo)
        if len(calls) > 1:
            raise ValueError(CANARY)
    monkeypatch.setattr(pipeline, 'verify_runtime_components', component)
    monkeypatch.setattr(pipeline, 'COLLECTOR_SHA256', EXAMPLE_PRODUCER_SHA256)
    monkeypatch.setattr(pipeline.collector, 'capture', lambda *a, **kw: record, raising=False)
    monkeypatch.setattr(pipeline, '_write_artifact', lambda *a: (_ for _ in ()).throw(OSError(CANARY)))
    pack = pipeline.proposal_pack(tmp_path / 'pack', 'reference5d')
    executor = pipeline.PluginCPUProposalExecutor({'source_root': str(tmp_path / 'source'), 'source_profile': 'reference5d'})
    draft = executor.run(pack.checks['SUP-10'], example_env(), {}, None, tmp_path / 'work')
    assert len(calls) == 2 and draft.status == ExecStatus.ABORTED
    assert draft.abort_reason == 'ARTIFACT_CAPTURE_INCOMPLETE; SUP10_COMPONENT_POSTCHECK_UNAVAILABLE'
    assert draft.observations['plugin_loading_confirmed_violations'].value == ['general-disabled']
    assert CANARY not in draft.model_dump_json()
