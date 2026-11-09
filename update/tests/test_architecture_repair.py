"""CPU architecture regressions. No engine, daemon, GPU, or production receipt."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import runpy
import threading

import pytest

from guard import application
from guard.adapters import engine_profiles, engine_provider, observer_runtime
from guard.adapters.fake import FakeAdapter
from guard.adapters.openai_engine import SGLangAdapter, TensorRTAdapter
from guard.adapters.vllm import VllmAdapter
from guard.collectors import actor_consumption as actors, producer_approvals, scoped_consumption as scoped
from guard.core.execution_recipe import bind_recipe, execution_lock
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.base import EvidenceDraft
from guard.models import DataOrigin, canonical_json, sha256_hex
from guard.runtime.callback_pages import assemble
from guard.store import Store
from tests.builders import T0, baseline_pack, environment, policy, req01_obs
from tests.test_scoped_consumption import capture, native, EXAMPLE_SCOPE
from tests.test_gpu_input_producers import POD, R1, SCOPE, api_rows
from tests.test_runner_consumption import worker_rows
from tests.test_change_selection import reuse_case, _change


def test_unknown_rank_then_observed_rank_preserves_rows_and_independent_violation():
    processes = native([{'pid': 1, 'start_ticks': 1, 'events': api_rows()},
        {'pid': 112, 'start_ticks': 2, 'rank': 0, 'events': worker_rows(logit_bias=0)}])
    for row in processes[1]['events']:
        if row['point'].endswith('_update_states'): row['consumption_scope']['rank'] = None
        if row.get('fields', {}).get('applied'): row['fields']['applied'][0]['top_k'] += 1
    value, result = scoped.record(processes, pod=POD, expected_stage_ranks={'runner_received': [0], 'sampler_applied': [0]},
        qualification_scope={**EXAMPLE_SCOPE, 'tp_size': 1}, expected_requests=[R1], **SCOPE)
    assert len(value['events']) >= 3 and not result['complete']
    assert 'GPU_INPUT_WORKER_RANK_UNOBSERVED' in result['gaps']
    assert any(v.get('field') == 'top_k' for v in result['violation_candidates'])


def topology_capture():
    value, _ = capture()
    workers = deepcopy(value['bindings'][1:])
    for index, binding in enumerate(workers): binding['worker_pid'] += 100; binding['start_ticks'] += 100
    value['bindings'].extend(workers)
    for row in list(value['events']):
        if row['event'] in actors.STAGES:
            row = deepcopy(row); row['binding'] += 2
            row['source_call']['process_sha256'] = hashlib.sha256(str(row['binding']).encode()).hexdigest()
            value['events'].append(row)
    for seq, row in enumerate(value['events']): row['seq'] = seq
    topology = {'schema': 'guard-observed-topology/1', 'groups': [], 'actors': [],
        'bootstrap_binding': {'schema': 'guard-topology-bootstrap/1', 'producer': {'name': 'EXAMPLE-bootstrap', 'version': '1', 'source_sha256': 'b' * 64},
            'source_ended': True, **{k: SCOPE[k] for k in ('environment_ref', 'target_sha256', 'session')}}}
    for group_index, group in enumerate(('group-a', 'group-b')):
        topology['groups'].append({'id': group, 'role': 'worker', 'ranks': [0, 1]})
        for rank in (0, 1):
            binding = value['bindings'][1 + group_index * 2 + rank]
            topology['actors'].append({'id': f'{group}-{rank}', 'group': group, 'rank': rank,
                'binding': {k: binding[k] for k in actors.IDENTITY}, 'stages': list(actors.STAGES)})
    assignments = {R1: {stage: [a['id'] for a in topology['actors']] for stage in actors.STAGES}}
    topology['bootstrap_binding']['assignments_sha256'] = sha256_hex(canonical_json(assignments))
    topology['bootstrap_sha256'] = sha256_hex(canonical_json({k: topology[k] for k in ('bootstrap_binding', 'groups', 'actors')}))
    value.update(schema=actors.SCHEMA, producer=actors.producer(), topology=topology, request_actors=assignments)
    return value


def approve_example(value, tmp_path, monkeypatch, *, bootstrap=True):
    scope = {**value['qualification_scope'], 'topology_layout_sha256': sha256_hex(canonical_json(actors.topology_layout(value['topology'])))}
    rows = [{'receipt': 'EXAMPLE-only', 'producer': value['producer'], 'wire_schema': actors.SCHEMA, 'scope': scope}]
    if bootstrap: rows.append({'receipt': 'EXAMPLE-bootstrap-only', 'producer': value['topology']['bootstrap_binding']['producer'], 'wire_schema': 'guard-topology-bootstrap/1', 'scope': scope})
    path = tmp_path / 'EXAMPLE-receipts.json'
    path.write_text(json.dumps({'schema': 'guard-producer-qualifications/1', 'receipts': rows}))
    monkeypatch.setattr(actors, 'qualified', lambda p, s, v: producer_approvals.qualified(p, s, v, path=path))


def test_multiple_groups_cannot_disappear_or_narrow_their_own_scope(tmp_path, monkeypatch):
    value = topology_capture(); approve_example(value, tmp_path, monkeypatch)
    assert actors.validate(value, **SCOPE)['complete']
    value['events'] = [r for r in value['events'] if r['binding'] < 3]
    for seq, row in enumerate(value['events']): row['seq'] = seq
    result = actors.validate(value, **SCOPE)
    assert not result['complete'] and any('ACTOR_COVERAGE_MISMATCH' in g for g in result['gaps'])
    value['topology']['groups'] = value['topology']['groups'][:1]
    value['topology']['actors'] = value['topology']['actors'][:2]
    value['topology']['bootstrap_sha256'] = sha256_hex(canonical_json({k: value['topology'][k] for k in ('bootstrap_binding', 'groups', 'actors')}))
    assert not actors.validate(value, **SCOPE)['runtime_qualified']


def test_declared_topology_is_not_a_qualified_bootstrap(tmp_path, monkeypatch):
    value = topology_capture(); approve_example(value, tmp_path, monkeypatch, bootstrap=False)
    result = actors.validate(value, **SCOPE)
    assert not result['complete'] and 'GPU_INPUT_BOOTSTRAP_SOURCE_UNQUALIFIED' in result['gaps']


def test_qualification_shape_is_independent_of_deployment_group_labels(tmp_path, monkeypatch):
    value = topology_capture(); approve_example(value, tmp_path, monkeypatch)
    before = actors.topology_layout(value['topology'])
    topology = value['topology']
    for group in topology['groups']: group['id'] = 'new-deployment-' + group['id']
    for actor in topology['actors']: actor['group'] = 'new-deployment-' + actor['group']
    topology['bootstrap_sha256'] = sha256_hex(canonical_json({k: topology[k] for k in ('bootstrap_binding', 'groups', 'actors')}))
    assert actors.topology_layout(topology) == before
    assert actors.validate(value, **SCOPE)['complete']


def test_request_actor_assignment_can_use_one_replica_without_hiding_other_groups(tmp_path, monkeypatch):
    value = topology_capture()
    value['events'] = [r for r in value['events'] if r['binding'] < 3]
    for seq, row in enumerate(value['events']): row['seq'] = seq
    value['request_actors'] = {R1: {stage: ['group-a-0', 'group-a-1'] for stage in actors.STAGES}}
    topology = value['topology']
    topology['bootstrap_binding']['assignments_sha256'] = sha256_hex(canonical_json(value['request_actors']))
    topology['bootstrap_sha256'] = sha256_hex(canonical_json({k: topology[k] for k in ('bootstrap_binding', 'groups', 'actors')}))
    approve_example(value, tmp_path, monkeypatch)
    assert actors.validate(value, **SCOPE)['complete']
    del value['request_actors']
    result = actors.validate(value, **SCOPE)
    assert not result['complete'] and 'GPU_INPUT_REQUEST_ASSIGNMENT_UNOBSERVED' in result['gaps']


def test_malformed_actor_assignment_is_a_gap_and_retains_independent_observations(tmp_path, monkeypatch):
    value = topology_capture(); approve_example(value, tmp_path, monkeypatch)
    value['request_actors'] = {R1: []}
    topology = value['topology']
    topology['bootstrap_binding']['assignments_sha256'] = sha256_hex(canonical_json(value['request_actors']))
    topology['bootstrap_sha256'] = sha256_hex(canonical_json({k: topology[k] for k in ('bootstrap_binding', 'groups', 'actors')}))
    assert not actors.validate(value, **SCOPE)['complete']


def test_receipt_file_registration_has_no_self_hash_cycle(tmp_path):
    before = scoped.producer()
    row = {'receipt': 'EXAMPLE', 'producer': before, 'wire_schema': scoped.SCHEMA, 'scope': EXAMPLE_SCOPE,
        'stage_ranks': {'runner_received': [0, 1], 'sampler_applied': [0, 1]}}
    path = tmp_path / 'receipts.json'; path.write_text(json.dumps({'schema': 'guard-producer-qualifications/1', 'receipts': [row]}))
    supported, receipts = producer_approvals.legacy_scoped(path)
    assert scoped.producer() == before
    assert supported[before['name'], before['version']] == before['source_sha256']
    assert receipts[0].matches(before, scoped.SCHEMA, EXAMPLE_SCOPE, row['stage_ranks'])


@pytest.mark.parametrize('engine', ['vllm', 'sglang', 'tensorrt-llm', 'example-new-engine'])
def test_engine_extension_runs_actual_common_select_run_judge_report_without_core_edit(tmp_path, monkeypatch, engine):
    provider = engine_provider.EngineProvider(engine, FakeAdapter)
    class ExampleExecutor:
        name, version = 'EXAMPLE-native', '1'
        def run(self, check, env, values, adapter, workdir):
            assert env.engine.name == engine
            return EvidenceDraft(status='COMPLETED', data_origin='EXAMPLE', normal_control={'attempted': True, 'succeeded': True},
                challenges=[{'step': s, 'attempted': True} for s in check.challenge_steps], observations=req01_obs(), config={'EXAMPLE': True})
    profile = engine_profiles.EngineProfile('EXAMPLE-' + engine, engine, ('EXAMPLE-next',), frozenset({'direct_route'}))
    monkeypatch.setattr(engine_profiles, 'PROFILES', (*engine_profiles.PROFILES, profile))
    provider.bind_executor('direct_route', 'kubernetes', lambda cfg: ExampleExecutor(), profile=profile.id)
    monkeypatch.setitem(engine_provider.PROVIDERS, engine, provider)
    assert registry.resolve_executor('direct_route', 'kubernetes', engine=engine, version='EXAMPLE-next') is not None
    # No fallback to a frozen vendor implementation for another release.
    assert provider.executor('direct_route', 'kubernetes', 'EXAMPLE-unreviewed') is None
    pack = baseline_pack(); env = environment({'gateway': {'value': True, 'origin': 'EXAMPLE', 'source': 'EXAMPLE'}}, engine={'name': engine, 'version': 'EXAMPLE-next'})
    pol = policy(pack.ref(), scope={'include': ['REQ-01'], 'exclude': []}); store = Store(tmp_path / 'store'); store.put_many([env, pol])
    plan = select(pack, [env], pol); store.put(plan)
    cfg = RunConfig(executors={'direct_route': 'kubernetes'}, adapter=engine, raw={})
    # Lab activation is inert without its explicit configuration.
    evidence, = run_plan(store, pack, plan, cfg)
    judge_plan(store, pack, plan, T0); report = build_report(store, pack, plan, T0)
    assert evidence.execution.status.value == 'COMPLETED' and evidence.data_origin == DataOrigin.EXAMPLE
    row = next(r for r in report.by_check if r.check_id == 'REQ-01')
    assert row.single.security.value == 'PASS' and store.verify() == []


def test_catalog_alone_cannot_dispatch_a_new_native_implementation(monkeypatch):
    monkeypatch.setattr(engine_profiles, 'PROFILES', (*engine_profiles.PROFILES,
        engine_profiles.EngineProfile('EXAMPLE-new-native', 'vllm', ('0.32.0',), frozenset({'native_lifecycle'}))))
    assert engine_profiles.resolve('vllm', '0.32.0', 'native_lifecycle').available
    assert registry.resolve_executor('initialization', 'kubernetes', engine='vllm', version='0.32.0') is None


def test_continuous_example_releases_preserve_prior_evidence_and_results(tmp_path, monkeypatch):
    from guard.models.objects import Comparability
    versions = ('0.31.0', '0.32.0', '0.33.0')  # EXAMPLE fixture versions, not upstream support claims.
    provider = engine_provider.EngineProvider('sglang', FakeAdapter)
    profile = engine_profiles.EngineProfile('EXAMPLE-release-chain', 'sglang', versions, frozenset({'direct_route'}))
    monkeypatch.setattr(engine_profiles, 'PROFILES', (*engine_profiles.PROFILES, profile))
    class ExampleExecutor:
        name, version = 'EXAMPLE-chain', '1'
        def run(self, check, env, *args):
            return EvidenceDraft(status='COMPLETED', data_origin='EXAMPLE',
                normal_control={'attempted': True, 'succeeded': True},
                challenges=[{'step': s, 'attempted': True} for s in check.challenge_steps],
                observations=req01_obs(direct={'GET /v1/models': 200} if env.engine.version == versions[1] else None),
                comparability=Comparability(model_inventory_sha256='a' * 64, fixture_sha256='b' * 64,
                    config_sha256='c' * 64, gpu='EXAMPLE', driver='EXAMPLE'))
    provider.bind_executor('direct_route', 'kubernetes', lambda cfg: ExampleExecutor(), profile=profile.id)
    monkeypatch.setitem(engine_provider.PROVIDERS, 'sglang', provider)
    envs = [environment({'gateway': {'value': True, 'origin': 'EXAMPLE', 'source': 'EXAMPLE'}},
        id='ENV-EXAMPLE-' + v.replace('.', '-'), engine={'name': 'sglang', 'version': v}) for v in versions]
    pack = baseline_pack(); pol = policy(pack.ref(), scope={'include': ['REQ-01'], 'exclude': []})
    store = Store(tmp_path / 'store'); store.put_many([*envs, pol]); preserved = []
    for index, expected in enumerate(('REGRESSED', 'FIXED')):
        plan = select(pack, envs[index:index + 2], pol, mode='update'); store.put(plan)
        run_plan(store, pack, plan, RunConfig(adapter='sglang', executors={'direct_route': 'kubernetes'}))
        judge_plan(store, pack, plan, T0); report = build_report(store, pack, plan, T0); store.put(report)
        row = next(r for r in report.by_check if r.check_id == 'REQ-01')
        assert row.comparison.value == expected and not report.official
        for ref, raw in preserved: assert canonical_json(store.resolve(ref).to_dict()) == raw
        for kind in ('EVD', 'RES'):
            for obj in store.iter_kind(kind): preserved.append((obj.ref(), canonical_json(obj.to_dict())))
    assert store.verify() == [] and len(list(store.iter_kind('EVD'))) == 4


def test_candidate_procedure_must_also_match_before_reusing_pass(reuse_case, monkeypatch):
    from dataclasses import replace
    case = reuse_case; baseline = case['env']
    candidate = baseline.model_copy(update={'id': 'ENV-CANDIDATE', 'engine': baseline.engine.model_copy(update={
        'version': '1.0.1', 'source_commit': 'b' * 40, 'image_digest': 'image@sha256:' + 'b' * 64})}).seal()
    original = case['preview']
    monkeypatch.setattr(case['module'], 'preview_inputs', lambda check, env, values, cfg:
        replace(original, fixture_sha256='f' * 64) if env.id == candidate.id else original)
    context = case['module'].build_context(case['store'], case['previous'], case['pack'], [baseline, candidate],
        case['policy'], {}, change=_change(['AUTH']), mode='update', as_of=T0)
    assert 'PLT-04' not in context.carried_over
    assert context.triggers['PLT-04'][0].refs == ['CANDIDATE_PROCEDURE_OR_FIXTURE_CHANGED_OR_UNCONFIRMED']


def test_plan_lock_and_attempt_path_prevent_evidence_mixing(tmp_path):
    with execution_lock(tmp_path, 'PLAN-EXAMPLE'):
        with pytest.raises(ValueError, match='ALREADY_RUNNING'):
            with execution_lock(tmp_path, 'PLAN-EXAMPLE'): pass


def test_recipe_change_and_expired_policy_are_rejected_before_executor(tmp_path):
    pack = baseline_pack(); env = environment(); pol = policy(pack.ref()); store = Store(tmp_path / 'store'); store.put_many([env, pol])
    plan = bind_recipe(select(pack, [env], pol), {'EXAMPLE': 'selected'}); store.put(plan)
    with pytest.raises(ValueError, match='RECIPE_CHANGED'):
        run_plan(store, pack, plan, RunConfig(raw={'EXAMPLE': 'changed'}))
    old = pol.model_copy(update={'id': 'POL-EXPIRED', 'valid_until': T0}).seal(); store.put(old)
    expired = select(pack, [env], old); store.put(expired)
    with pytest.raises(ValueError, match='POLICY_EXPIRED'):
        run_plan(store, pack, expired, RunConfig())


def test_deferred_target_derivation_preserves_recipe_but_baseline_tampering_does_not(tmp_path):
    from guard.core.execution_recipe import configuration_sha256
    baseline, candidate = tmp_path / 'baseline.json', tmp_path / 'candidate.json'
    baseline.write_text('{"EXAMPLE":"baseline"}')
    cfg = {'update': {'kubernetes_candidate_binding': 'after_baseline'}, 'sides': {
        'baseline': {'request_binding': {'target': str(baseline)}},
        'candidate': {'request_binding': {'target': str(candidate)}}}}
    before = configuration_sha256(cfg)
    candidate.write_text('{"EXAMPLE":"observed-candidate-generation"}')
    assert configuration_sha256(cfg) == before
    baseline.write_text('{"EXAMPLE":"changed-baseline"}')
    assert configuration_sha256(cfg) != before


@pytest.mark.parametrize('adapter', [VllmAdapter(), SGLangAdapter(), TensorRTAdapter()])
def test_discovered_unknown_get_is_not_a_safe_probe(adapter):
    route, = adapter.routes_from_openapi({'paths': {'/EXAMPLE_new_mutating_action': {'get': {}}}})
    assert adapter.probe_request(route, 'EXAMPLE') is None


def test_active_journal_needs_a_cut_and_old_rows_do_not_consume_next_trial_budget(tmp_path):
    reader = runpy.run_path(str(Path(observer_runtime.__file__).parents[1] / 'runtime/assets/callback_page.py.txt'))['page']
    sink = observer_runtime.JournalSink(tmp_path)
    try:
        for start in range(0, 4100, 100):
            for index in range(start, min(start + 100, 4100)): sink({'EXAMPLE': index})
            sink.queue.join()
        assert 'CALLBACK_CAPTURE_NOT_FINALIZED' in reader(tmp_path)['gaps']
        cut = reader(tmp_path, capture_id='a' * 32, seek_end=True)
        for index in range(4): sink({'EXAMPLE-new': index})
        value = reader(tmp_path, cursor=cut['cursor'], capture_id='b' * 32)
        captured = assemble([value])
        assert not captured['truncated'] and captured['source_events'] == 4
        assert [r['EXAMPLE-new'] for process in captured['processes'] for r in process['events']] == list(range(4))
    finally: sink.close()


def test_cas_concurrent_identical_publications_are_idempotent(tmp_path):
    store = Store(tmp_path); results, errors = [], []
    def publish():
        try: results.append(store.put_artifact(b'EXAMPLE same bytes'))
        except Exception as error: errors.append(type(error).__name__)
    threads = [threading.Thread(target=publish) for _ in range(12)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert not errors and len(results) == 12 and len({r.sha256 for r in results}) == 1 and store.verify() == []


def test_foreign_process_cannot_touch_parent_journal_locks_or_state(tmp_path, monkeypatch):
    import os
    class InheritedLockedMutex:
        def __enter__(self): raise AssertionError('inherited parent mutex was acquired')
        def __exit__(self, *args): pass
    sink = observer_runtime.JournalSink(tmp_path); sink.close()
    before = sink.state.read_bytes(); sink.lock = InheritedLockedMutex()
    with monkeypatch.context() as patch:
        patch.setattr(os, 'getpid', lambda: sink.pid + 1)
        with pytest.raises(ValueError, match='PROCESS_CHANGED'): sink({'EXAMPLE': True})
        sink.close()
    assert sink.state.read_bytes() == before


def test_foreign_callback_preserves_original_result_and_exception_without_parent_mutex(monkeypatch):
    import os
    from types import ModuleType
    module = ModuleType('EXAMPLE_fork_observer')
    exec("class Example:\n def work(self, value):\n  if value == 'raise': raise RuntimeError('EXAMPLE-original-exception')\n  return value + 1\n", module.__dict__)
    Example = module.Example
    class InheritedLockedMutex:
        def __enter__(self): raise AssertionError('inherited parent mutex was acquired')
        def __exit__(self, *args): pass
    class Owner(observer_runtime.QualifiedObserver):
        def _qualified_codes(self, module, targets, sources):
            return {'Example.work': observer_runtime.cache._digest(Example.work.__code__, module.__name__)}
    owner = Owner(); owner.pid = os.getpid(); owner.lock = InheritedLockedMutex(); owner.gaps = set()
    owner.targets = {module.__name__: ('Example.work',)}; owner.sources = {module.__name__: 'a' * 64}
    wrapped = owner.wrap(module, 'Example.work')
    with monkeypatch.context() as patch:
        patch.setattr(os, 'getpid', lambda: owner.pid + 1)
        assert wrapped(Example(), 2) == 3
        with pytest.raises(RuntimeError, match='EXAMPLE-original-exception'): wrapped(Example(), 'raise')
    assert owner.gaps == {'CACHE_CALLBACK_PROCESS_CHANGED'}
