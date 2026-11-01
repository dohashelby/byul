"""Private EXAMPLE contract packs; published PACK31 is never rewritten here."""
from copy import deepcopy
from pathlib import Path
import hashlib
import yaml
import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.select import select
from guard.core.run import RunConfig, run_plan
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.executors import registry
from guard.executors import kubernetes_stream_buffer as stream
from guard.executors import kubernetes_process_rss as rss
from guard.executors import stream_buffer_contract as contract
from guard.models import Policy, load_pack, utcnow, canonical_json
from guard.models.checkdef import JudgeSpec, ObsSpec, PolicyParam
from guard.store import Store
from guard.store.packs import put_pack, get_pack
from guard.store.bundle import export_bundle, restore_bundle
from tests.builders import baseline_pack
from tests.test_kubernetes_lifecycle import group, target as native_target
from tests.test_lifecycle_capture import case, external, revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_a1_stream_buffer_product import inputs
from tests.test_budget_process_rss import example_rss_runner


def private_pack(folder, check):
    (folder/'checks').mkdir(parents=True)
    body = yaml.safe_dump(check.model_dump(mode='json'), allow_unicode=True).encode('utf-8')
    (folder/'checks'/f'{check.id}.yaml').write_bytes(body)
    manifest = {'pack_id': 'PACK-OWNER2-PRODUCT-CONTRACT', 'revision': 1, 'engines': ['vllm'],
        'checks': [check.id], 'review': {'status': 'DRAFT'},
        'files_sha256': [{'path': f'checks/{check.id}.yaml', 'sha256': hashlib.sha256(body).hexdigest()}]}
    (folder/'pack.yaml').write_text(yaml.safe_dump(manifest), encoding='utf-8')
    return load_pack(folder)


def pipeline(folder, check, env, values, executor_config):
    pack = private_pack(folder/'pack', check)
    policy = Policy(id='POL-OWNER2-PRODUCT-EXAMPLE', data_origin='EXAMPLE', status='DRAFT',
        pack=pack.ref(), scope={'include': [check.id]}, values={check.id: values}).seal()
    store = Store(folder/'store'); put_pack(store, pack.path)
    store.put_many([env, policy]); plan = select(pack, [env], policy); store.put(plan)
    evd, = run_plan(store, pack, plan, RunConfig(executors={check.executor: 'kubernetes'},
        adapter='vllm', raw={check.executor: executor_config}))
    now = utcnow(); result, = judge_plan(store, pack, plan, as_of=now)
    report = build_report(store, pack, plan, as_of=now); store.put(report)
    export_bundle(store.root, folder/'bundle.zip'); restore_bundle(folder/'bundle.zip', folder/'restored')
    restored = Store(folder/'restored')
    replay, = judge_plan(restored, get_pack(restored, plan.inputs.pack), restored.resolve(plan.ref()), as_of=now)
    assert replay.ref() == result.ref() and restored.verify() == []
    assert next(restored.iter_kind('RPT')).ref() == report.ref()
    return evd, result


@pytest.mark.parametrize('mode,expected', [('normal','FAIL'), ('cleanup','FAIL'), ('missing','UNKNOWN')])
def test_lif03_real_registered_factory_through_saved_pack_rejudgment(tmp_path, group, native_target, monkeypatch, mode, expected):
    world, values, _, _ = inputs(tmp_path, group, native_target, 'normal')
    values['buffer_max_process_rss_bytes'] = 1
    target = tmp_path/'target.json'; target.write_bytes(canonical_json(world.target))
    check = baseline_pack().checks['LIF-03'].model_copy(deep=True)
    check.executor = 'stream_buffer'; check.collection_profile = stream.BUDGET_PROFILE
    check.required_observations = [ObsSpec(key=key, type=kind) for key, kind in {
        **contract.OBSERVATIONS, **rss.OBSERVATIONS}.items()]
    check.policy_params = [PolicyParam(key=key, type=kind) for key, kind in contract.POLICY_TYPES.items()]
    check.judge = JudgeSpec(plugin='rule_table', params={
        'fail_when_any': [{'obs': 'budget_process_rss_exceeded', 'op': 'eq', 'value': True}],
        'pass_when_all': [{'obs': 'buffer_scope_complete', 'op': 'eq', 'value': True}]})
    # Exercise the installed registry factory and original class. Only its OS I/O
    # boundary is replaced by an explicitly EXAMPLE native/proc transport.
    original = stream.KubernetesStreamBufferExecutor.__init__
    def initialize(self, cfg, **kwargs):
        kwargs['runner'] = example_rss_runner(world.runner, mode)
        original(self, cfg, **kwargs)
    monkeypatch.setattr(stream.KubernetesStreamBufferExecutor, '__init__', initialize)
    assert registry.resolve_executor('stream_buffer', 'kubernetes') is not None
    evd, result = pipeline(tmp_path/'product', check, group.env, values,
        {'target': str(target), 'prepared': str(group.manifest_path)})
    assert evd.execution.executor.name == 'stream_buffer'
    assert evd.data_origin.value == 'EXAMPLE'
    assert result.verdicts.security.value == expected
    assert evd.observations['buffer_scope_complete'].value is False
    if mode == 'cleanup':
        assert evd.execution.status.value == 'ABORTED'
        assert evd.observations['budget_process_rss_exceeded'].value is True
    if mode == 'missing':
        assert evd.observations['budget_process_rss_exceeded'].collected is False
    assert len(evd.artifacts) > 0


@pytest.mark.parametrize('mode', ['normal', 'missing-profiler'])
def test_obs02_separate_producers_registered_factory_and_stored_pack(tmp_path, group, native_target, monkeypatch, mode):
    from guard.executors import kubernetes_observation_composite as composite
    from guard.executors import kubernetes_profiler_observation as profiler
    from guard.executors.kubernetes_observation_identity import KubernetesObservationIdentityExecutor
    from tests.test_pack_owner2_extensions import separate_profiler_target
    from tests.test_a1_observation_identity_product import inputs as identity_inputs
    from tests.test_a1_profiler_product import inputs as profiler_inputs
    identity_group = deepcopy(group); profiler_group = deepcopy(group)
    identity_dir = tmp_path/'identity'; identity_dir.mkdir()
    profiler_dir = tmp_path/'profiler'; profiler_dir.mkdir()
    identity_world, identity_values, _ = identity_inputs(identity_dir, identity_group, native_target, monkeypatch, 'normal')
    selected = deepcopy(native_target); separate_profiler_target(selected, native=True)
    profiler_world, profiler_values, _ = profiler_inputs(profiler_dir, profiler_group, selected, monkeypatch, mode)
    profiler_world.target['engine']['trusted_url'] = 'http://127.0.0.3:8000'
    from guard.models import sha256_hex
    profiler_values['observation_state_target_sha256'] = sha256_hex(canonical_json(profiler_world.target))
    assert identity_group.env.ref() == profiler_group.env.ref()
    cfg = {}; values = {**profiler_values, **identity_values}
    del values['observation_state_target_sha256']
    for label, world, child_values, directory in (
        ('identity', identity_world, identity_values, identity_dir), ('profiler', profiler_world, profiler_values, profiler_dir)):
        target = tmp_path/(label+'-target.json'); target.write_bytes(canonical_json(world.target))
        cfg[label+'_target'] = str(target)
        cfg[label+'_prepared'] = str(directory/'prepared/manifest.json')
        values[label+'_target_sha256'] = child_values['observation_state_target_sha256']
    check = baseline_pack().checks['OBS-02'].model_copy(deep=True)
    check.executor = 'observation_state'; check.collection_profile = composite.PROFILE
    check.required_observations = [ObsSpec(key=key, type=kind) for key, kind in composite.OBSERVATIONS.items()]
    check.policy_params = [PolicyParam(key=key, type=kind) for key, kind in composite.POLICY_TYPES.items()]
    check.judge = JudgeSpec(plugin='rule_table', params={'pass_when_all': [
        {'obs': 'state_collection_scope_complete', 'op': 'eq', 'value': True}]})
    for implementation, world in ((KubernetesObservationIdentityExecutor, identity_world),
                                  (profiler.KubernetesProfilerObservationExecutor, profiler_world)):
        original = implementation.__init__
        def initialize(self, config, _original=original, _runner=world.runner, **kwargs):
            kwargs['runner'] = _runner
            _original(self, config, **kwargs)
        monkeypatch.setattr(implementation, '__init__', initialize)
    evd, result = pipeline(tmp_path/'product', check, identity_group.env, values, cfg)
    sessions = evd.observations['state_collection_sessions'].value
    assert len({row['session_sha256'] for row in sessions}) == 2
    assert len({row['target_sha256'] for row in sessions}) == 2
    assert evd.data_origin.value == 'EXAMPLE' and result.verdicts.security.value == 'UNKNOWN'
    assert evd.observations['identity_native_models'].collected
    assert len(evd.artifacts) == 2


def test_transition_registered_factory_with_select_supported_policy_and_stored_pack(tmp_path, monkeypatch):
    from guard.executors import lifecycle_transition as transition
    from tests.test_lifecycle_transition import harness, policy as old_policy
    observed, env, log = harness(monkeypatch, failure='cleanup')
    original = transition.LifecycleTransitionExecutor.__init__
    def initialize(self, cfg, **kwargs):
        kwargs.update(runner=lambda *a: None, clock=observed.clock, wait=observed.wait)
        original(self, cfg, **kwargs)
        self._reader = observed._reader; self._probe = observed._probe; self._capture = observed._capture
    monkeypatch.setattr(transition.LifecycleTransitionExecutor, '__init__', initialize)
    value = old_policy()
    values = {'transition_inputs_sha256': value['transition_inputs_sha256'],
        **{f'transition_{phase}_at': value['transition_sample_at'][phase] for phase in transition.PHASES},
        **{f'transition_{phase}_generation': value['transition_phase_generation'][phase] for phase in transition.PHASES}}
    check = baseline_pack().checks['LIF-05'].model_copy(deep=True)
    check.executor = 'lifecycle_transition'; check.collection_profile = 'lif05-stopped-redeploy-v1'
    check.required_observations = [ObsSpec(key=key, type=kind) for key, kind in transition.OBSERVATIONS.items()]
    check.policy_params = [PolicyParam(key=key, type=kind) for key, kind in transition.POLICY_TYPES.items()]
    check.judge = JudgeSpec(plugin='rule_table', params={'pass_when_all': [
        {'obs': 'transition_scope_complete', 'op': 'eq', 'value': True}]})
    evd, result = pipeline(tmp_path/'product', check, env, values, {})
    assert evd.execution.executor.name == 'lifecycle_transition'
    assert evd.execution.status.value == 'ABORTED'
    assert evd.observations['transition_request_configuration_conflicts'].value
    assert result.verdicts.security.value == 'UNKNOWN'
    assert evd.observations['transition_actual_consumption_verified'].value is False


@pytest.mark.parametrize('mode', ['normal', 'followup-error'])
def test_profiler_control_registered_factory_and_exact_saved_pack(tmp_path, external, monkeypatch, mode):
    from guard.executors import kubernetes_profiler_control as control
    from guard.executors import kubernetes_profiler_observation as dispatch
    from tests.test_kubernetes_profiler_control import inputs as profiler_inputs
    env, world, cfg, values = profiler_inputs(tmp_path, external, monkeypatch, mode)
    original = dispatch.KubernetesProfilerObservationExecutor.__init__
    def initialize(self, config, **kwargs):
        kwargs['runner'] = world.runner
        original(self, config, **kwargs)
    monkeypatch.setattr(dispatch.KubernetesProfilerObservationExecutor, '__init__', initialize)
    class Reader:
        def __init__(self, *a, **kw): self.binding = {'engine': world.target['kubernetes']['engine']}
        def snapshot(self): return {}
        def seal_session(self): return {'sealed': True}
        def report(self): return {}
    monkeypatch.setattr(control, 'KubernetesLifecycleReader', Reader)
    monkeypatch.setattr(control.KubernetesProfilerControlExecutor, '_capture', lambda *a: world.packet())
    check = baseline_pack().checks['LIF-14'].model_copy(deep=True)
    check.executor = 'profiler_observation'; check.collection_profile = control.PROFILE
    check.required_observations = [ObsSpec(key=key, type=kind) for key, kind in control.OBSERVATIONS.items()]
    check.policy_params = [PolicyParam(key=key, type=kind) for key, kind in control.POLICY_TYPES.items()]
    check.judge = JudgeSpec(plugin='rule_table', params={'pass_when_all': [
        {'obs': 'profiler_scope_complete', 'op': 'eq', 'value': True}]})
    evd, result = pipeline(tmp_path/'product', check, env, values, cfg)
    assert evd.execution.executor.name == 'profiler_observation'
    assert evd.observations['profiler_next_start_api_linked'].value is True
    assert evd.observations['profiler_scope_complete'].value is False
    assert evd.observations['profiler_backend_collection_stopped_verified'].value is False
    assert result.verdicts.security.value == 'UNKNOWN'
    if mode == 'followup-error':
        assert evd.execution.status.value == 'ABORTED'
        assert evd.observations['profiler_control_flag_adapter_conflicts'].value
