"""Architecture conformance. All runtime collaborators here are EXAMPLE only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.adapters import engine_profiles, engine_provider
from guard.adapters.base import EngineAdapter
from guard.adapters.openai_engine import SGLangAdapter, TensorRTAdapter
from guard.executors.engine_sensor import EngineSensorExecutor, PROFILES
from guard.runtime.comparison import execution_conditions
from guard.runtime.sensor_binding import KEYS, SCHEMA, SUFFIX, migrate_pack
from tests.builders import baseline_pack, environment, policy
from guard.models import load_pack


def current_pack():
    return load_pack(Path(__file__).parents[1] / 'packs/PACK-BASELINE')


@pytest.mark.parametrize('engine', ['vllm', 'sglang', 'tensorrt-llm'])
@pytest.mark.parametrize('version', ['0.31.0', '0.32.0', '0.33.0'])
def test_metadata_transport_does_not_certify_unreviewed_native_source(engine, version):
    support = engine_profiles.resolve(engine, version, 'pod_identity')
    assert support.available and not support.runtime_qualified
    native = engine_profiles.resolve(engine, version, 'scoped_consumption')
    assert native.available is (engine == 'vllm' and version == '0.31.0')
    assert not native.runtime_qualified


@pytest.mark.parametrize('adapter', [SGLangAdapter(), TensorRTAdapter()])
def test_optional_evidence_contracts_are_explicit_and_absent_is_unsupported(adapter):
    assert isinstance(adapter, EngineAdapter)
    for name in ('supports_template_profile', 'request_binding_contract', 'native_log_contract'):
        assert engine_provider.evidence_contract(adapter, name, {}) is None
    assert not engine_provider.get_provider(adapter.name).managed_available


def test_common_sandbox_dispatches_provider_without_vllm_launch_knowledge(monkeypatch):
    from guard.runtime import sandbox
    seen = []
    provider = SimpleNamespace(runtime_profile=lambda opts, name, base: {'EXAMPLE': name},
                               launch_plan=lambda opts: seen.append(opts) or 'EXAMPLE-plan')
    monkeypatch.setitem(engine_provider.PROVIDERS, 'EXAMPLE-engine', provider)
    assert sandbox._engine_profile('EXAMPLE-engine', {}, 'hardened') == {'EXAMPLE': 'hardened'}
    assert engine_provider.get_provider('EXAMPLE-engine').launch_plan({'model': 'EXAMPLE'}) == 'EXAMPLE-plan'
    assert seen == [{'model': 'EXAMPLE'}]


def test_sensor_readiness_uses_the_same_profile_resolver_as_execution():
    for version, implemented in [('0.31.0', True), ('0.32.0', False)]:
        env = environment(engine={'name': 'vllm', 'version': version})
        state = EngineSensorExecutor({}).support('REQ-14', PROFILES['REQ-14'], env)
        assert state['implemented'] is implemented
        assert state['profile_available'] is implemented
        assert state['code_available'] and not state['runtime_qualified']


def test_cpu_image_length_cause_implementation_was_removed():
    # 2026-11-12 GPU-only decision: the CPU-image REQ-09 length-cause profile is gone.
    env = environment(engine={'name': 'vllm', 'version': '0.32.0',
        'image_digest': 'vllm/vllm-openai-cpu@sha256:827ef38187f9b52f2bb838695ef815ec5b65f4bf3e4c061b002b2b0a19cb0701'})
    for backend in ('cpu', 'gpu'):
        assert engine_profiles.implementation('length_cause', env, backend=backend) is None


def test_version_provenance_can_change_while_effective_conditions_stay_equal():
    target = {'engine_kind': 'vllm', 'comparison_config': {'profile': 'EXAMPLE'}}
    before = {'constraint_engine_version': '0.31.0', 'constraint_source': 'release-031', 'max_output_tokens': 4}
    after = {**before, 'constraint_engine_version': '0.32.0', 'constraint_source': 'release-032'}
    assert execution_conditions(target, before, []) == execution_conditions(target, after, [])
    assert execution_conditions(target, before, [], profile='execution-v1') != execution_conditions(target, after, [], profile='execution-v1')
    after['max_output_tokens'] += 1
    assert execution_conditions(target, before, []) != execution_conditions(target, after, [])


def test_new_binding_pack_is_a_new_draft_and_preserves_old_policy_and_rules(tmp_path):
    original = current_pack()
    before = {p.relative_to(original.path): p.read_bytes() for p in original.path.rglob('*') if p.is_file()}
    migrated = migrate_pack(original.path, tmp_path / 'new-pack')
    assert migrated.manifest.revision == original.manifest.revision + 1
    assert not migrated.reviewed
    for cid, old in original.checks.items():
        new = migrated.checks[cid]
        if old.executor == 'engine_sensor':
            assert new.revision == old.revision + 1 and new.collection_profile == old.collection_profile + SUFFIX
            assert not KEYS & {p.key for p in new.policy_params}
            assert new.judge == old.judge and new.required_observations == old.required_observations
        else:
            assert new == old
    assert before == {p.relative_to(original.path): p.read_bytes() for p in original.path.rglob('*') if p.is_file()}
    binding = {'schema': SCHEMA, **{key: 'a' * 64 for key in KEYS}}
    executor = EngineSensorExecutor({'binding': binding})
    executor.validate_policy(migrated.checks['REQ-14'], {})
    with pytest.raises(ValueError, match='BELONGS_TO_EXECUTION'):
        executor.validate_policy(migrated.checks['REQ-14'], {key: 'b' * 64 for key in KEYS})


def test_packaged_source_cpu_verification_and_command_do_not_require_checkout(tmp_path):
    from guard.reference.integrity import verify_packaged
    from guard.executors.engine_sensor import _component_argv, PACKAGED_CPU_PROGRAM
    assert 'reference/slot_cpu.py' in verify_packaged('slot')
    assert 'reference/template_cpu.py' in verify_packaged('template')
    cfg = {'source_root': str(tmp_path / 'EXAMPLE-source'), 'owned_parent': str(tmp_path / 'capture')}
    argv = _component_argv(cfg, tmp_path / 'pycache')
    assert PACKAGED_CPU_PROGRAM in argv
    assert 'tools' not in PACKAGED_CPU_PROGRAM and 'tests' not in PACKAGED_CPU_PROGRAM
    assert '-I' in argv and '-B' in argv


def test_source_analysis_profiles_do_not_lend_vllm_rules_to_other_engines():
    import yaml
    from guard.adapters.source_analysis import get_source_analysis
    from guard.adapters.git_diff import classify_path
    for engine in ('sglang', 'tensorrt-llm'):
        selected = get_source_analysis(engine)
        assert not selected.reviewed
        assert classify_path('vllm/v1/core/sched/scheduler.py', yaml.safe_load(selected.rules.read_text())) == ([], False)
        assert selected.entrypoint('vllm/entrypoints/serve.py') is None


def test_new_qualification_receipt_cannot_cross_engine_version_backend_or_tp(monkeypatch):
    from tests.test_scoped_consumption import capture, register, SCOPE
    from guard.collectors import scoped_consumption as scoped
    register(monkeypatch)
    value, good = capture()
    assert good['complete']
    for key, replacement in [('engine', 'sglang'), ('engine_version', '0.32.0'), ('backend', 'gpu'), ('tp_size', 1)]:
        changed = deepcopy(value)
        changed['qualification_scope'][key] = replacement
        result = scoped.validate(changed, **SCOPE)
        assert not result['complete'] and not result['runtime_qualified']


def test_new_binding_pack_selection_has_no_policy_gap_for_technical_hashes(tmp_path):
    from guard.core.select import select
    from guard.models import Mode
    migrated = migrate_pack(current_pack().path, tmp_path / 'pack')
    pol = policy(migrated.ref(), scope={'include': ['REQ-14']}, values={})
    plan = select(migrated, [environment()], pol, mode=Mode.audit)
    assert 'NO_POLICY_VALUE' not in {gap.value for gap in plan.item('REQ-14').gaps}


def test_plan_acceptance_scope_does_not_read_the_historical_five_constants(tmp_path, monkeypatch):
    from tests.update_builders import build_pure_update_case
    from guard import update_acceptance as acceptance
    report, objects, pack = build_pure_update_case(tmp_path)
    before = deepcopy((report, objects))
    monkeypatch.setattr(acceptance, 'CHECKS', ('EXAMPLE-LEGACY-ONLY',))
    result = acceptance.assess_update(report, objects, pack, profile='plan-scope/2')
    assert result['accepted'], result['problems']
    assert (report, objects) == before


def test_new_acceptance_preparation_uses_plan_scope_and_equivalent_version_context():
    from tests.test_update_validation import declared_inputs
    from guard.update_validation import validate_inputs
    pack, envs, pol, cfg = declared_inputs()
    pol.scope.include = ['REQ-01']
    pol = pol.seal()
    cfg['sides'] = {'baseline': {'sampling_validation': {'constraint_engine_version': envs[0].engine.version}},
                    'candidate': {'sampling_validation': {'constraint_engine_version': envs[1].engine.version}}}
    result = validate_inputs(pack, envs, pol, cfg, profile='plan-scope/2')
    assert result['selected_checks'] == ['REQ-01']
    cfg['sides']['candidate']['sampling_validation']['max_output_tokens'] = 8
    with pytest.raises(ValueError, match='execution conditions'):
        validate_inputs(pack, envs, pol, cfg, profile='plan-scope/2')


def test_legacy_manual_log_policy_contract_stays_frozen_for_other_collectors():
    from guard.log_policy_profiles import policy_supported
    values = {'collector_profile': 'guard-vllm-native-info-v1',
              'client_request_id_trust': 'untrusted_correlation', 'required_trusted_fields': ['process', 'source']}
    assert policy_supported(values)
    assert not policy_supported({**values, 'collector_profile': 'EXAMPLE-other-engine'})
    assert not policy_supported(values, 'EXAMPLE-unreviewed-contract')
