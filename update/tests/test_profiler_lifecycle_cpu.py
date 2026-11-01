"""EXAMPLE lifecycle/projection contracts; real Torch observations run separately."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from guard.executors import profiler_lifecycle_cpu as module
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict
from tests.builders import baseline_pack, environment, evidence
from tools import lif14_profiler_cpu as tool


def check():
    return baseline_pack().checks['LIF-14']


def record(*, native_after=False):
    phase = {'native_probe_qualified': True, 'wrapper_running_after': False, 'native_after': {'value': native_after, 'collected': True},
        'recorded_tag_events': 1, 'output': {'tag_events': 1}}
    return {'capture_status': 'completed', 'rows': [{'case_id': name, 'source_preverified': True, 'capture_status': 'completed', 'primary': dict(phase), 'next': dict(phase),
                     'final_native': {'value': False, 'collected': True}} for name in module.CASE_IDS]}


def verdict(observations):
    return RuleTableJudge().judge(check(), evidence(check_id='LIF-14', observations=observations), module.judge_params(), {}).verdicts


def test_only_functional_stop_mismatch_can_fail_and_missing_deadline_cannot_pass():
    obs = module.project(record(native_after=True))
    assert verdict(obs)['functional'] == Verdict.FAIL and verdict(obs)['security'] == Verdict.UNKNOWN
    obs = module.project(record())
    assert all(verdict(obs)[axis] == Verdict.UNKNOWN for axis in ('functional', 'security', 'performance'))
    assert obs['profiler_cpu_deadline_policy'] == {'value': None, 'collected': False}


def test_none_return_and_export_warning_do_not_imply_native_still_collecting():
    r = record()
    r['rows'][1]['primary'].update(stop_return='none', stop_warning_types=['RuntimeError'], output={'trace_files': 0})
    assert verdict(module.project(r))['functional'] == Verdict.UNKNOWN
    assert module.project(r)['profiler_cpu_stop_mismatch']['value'] is False


def test_missing_native_probe_is_unknown_not_false():
    state = module.native_state(SimpleNamespace(autograd=SimpleNamespace()))
    assert state == {'value': None, 'collected': False}
    r = record()
    for row in r['rows']:
        row['primary']['native_after'] = state
        row['next']['native_after'] = state
    assert module.project(r)['profiler_cpu_stop_mismatch'] == {'value': None, 'collected': False}


def test_native_probe_exception_text_is_omitted():
    def probe():
        raise RuntimeError('PRIVATE_NATIVE_PROBE')
    state = module.native_state(SimpleNamespace(autograd=SimpleNamespace(_profiler_enabled=probe)))
    assert not state['collected'] and 'PRIVATE_' not in json.dumps(state)


def test_trace_summary_retains_only_fixed_tag_count_digest_and_size(tmp_path):
    path = tmp_path / 'private-name.pt.trace.json'
    path.write_text(json.dumps({'traceEvents': [{'name': module.TAG}, {'name': 'PRIVATE_STACK_OR_INPUT'}], 'other': 'PRIVATE_METADATA'}))
    summary = module.output_summary(tmp_path)
    assert summary['trace_files'] == summary['tag_events'] == 1
    assert len(summary['files'][0]['sha256']) == 64
    assert 'PRIVATE_' not in json.dumps(summary) and 'private-name' not in json.dumps(summary)


@pytest.mark.parametrize('failure', [False, True])
def test_draft_and_attachment_failure_preserve_observed_functional_mismatch(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(module, 'capture', lambda **cfg: record(native_after=True))
    if failure:
        (tmp_path / 'profiler-lifecycle-cpu.json').write_text('existing append-only evidence')
    env = environment(origin=DataOrigin.DECLARED, engine={'name': 'vllm', 'version': 'reference-' + module.SOURCE_COMMIT})
    draft = module.ProfilerLifecycleCPUExecutor({'source_root': '/fixture', 'owned_parent': str(tmp_path)}).run(check(), env, {}, None, tmp_path)
    assert draft.data_origin == DataOrigin.DECLARED
    assert verdict({k: v.model_dump() for k, v in draft.observations.items()})['functional'] == Verdict.FAIL
    if failure:
        assert draft.status == ExecStatus.ABORTED and not draft.artifacts
        assert draft.abort_reason == 'ARTIFACT_CAPTURE_INCOMPLETE'
        assert (tmp_path / 'profiler-lifecycle-cpu.json').read_text() == 'existing append-only evidence'
    else:
        assert draft.status == ExecStatus.COMPLETED and len(draft.artifacts) == 1


def test_wrong_environment_blocks_before_cpu_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'capture', lambda **cfg: pytest.fail('CPU invoked for wrong ENV'))
    env = environment(engine={'name': 'other-engine', 'version': 'reference-' + module.SOURCE_COMMIT})
    with pytest.raises(ValueError, match='PROFILER_CPU_ENVIRONMENT_BINDING_INVALID'):
        module.ProfilerLifecycleCPUExecutor({'source_root': '/fixture', 'owned_parent': str(tmp_path)}).run(check(), env, {}, None, tmp_path)


def test_unconfigured_component_pin_blocks_actual_execution(monkeypatch):
    monkeypatch.setattr(tool, "PRODUCT_COMPONENT_COMMIT", None)
    with pytest.raises(ValueError, match='LIF14_PRODUCT_COMPONENT_PIN_UNCONFIGURED'):
        tool.verify_component(Path(__file__).resolve().parents[2])


def test_optional_torch_import_is_lazy():
    update = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-c',
        'import sys; import guard.executors.profiler_lifecycle_cpu; print("torch" in sys.modules)'],
        cwd=update, env={**os.environ, 'PYTHONPATH': os.pathsep.join([str(update / 'src'), str(update)])}, text=True, capture_output=True)
    assert result.returncode == 0 and result.stdout.strip() == 'False'


@pytest.mark.parametrize('during,qualified', [(True, True), (False, False), (None, False)])
def test_native_after_requires_before_false_and_during_true(during, qualified):
    state = {'value': during, 'collected': during is not None}
    actual, after = module.qualify_native_after({'value': False, 'collected': True}, state,
                                                {'value': False, 'collected': True})
    assert actual is qualified
    assert after == ({'value': False, 'collected': True} if qualified else {'value': None, 'collected': False})


def test_recovery_restores_only_the_owned_regular_fixture(tmp_path):
    owner = tmp_path / 'owned'
    owner.mkdir()
    path = owner / 'primary'
    path.write_bytes(module.COLLISION_BYTES)
    module.restore_owned_directory(path, owner)
    assert path.is_dir()
    other = tmp_path / 'other'
    other.write_bytes(module.COLLISION_BYTES)
    with pytest.raises(ValueError, match='PROFILER_CPU_RECOVERY_OWNERSHIP_UNVERIFIED'):
        module.restore_owned_directory(other, owner)
    assert other.is_file()


@pytest.mark.parametrize('failure,status', [(OSError('PRIVATE_POST_SOURCE_IO'), 'unavailable'),
    (ValueError('STRUCTURED_TOOLS_REFERENCE_MISMATCH'), 'source_mismatch')])
def test_post_source_check_retains_verified_child_facts_and_records_attribution(monkeypatch, failure, status):
    row = record(native_after=True)['rows'][0]
    def unavailable(*a, **kw):
        raise failure
    monkeypatch.setattr(module, '_verify_source', unavailable)
    result = module.post_source_check(row, Path('/EXAMPLE-source'))
    assert result['capture_status'] == 'post_source_check_incomplete'
    assert result['source_postcheck'] == {'status': status, 'error_type': type(failure).__name__}
    assert result['source_attribution'] != 'pre_and_post_verified'
    observation = module.project({'rows': [result]})
    assert observation['profiler_cpu_stop_mismatch'] == {'value': True, 'collected': True}
    assert 'PRIVATE_' not in json.dumps(result)


@pytest.mark.parametrize('failure,status', [(OSError('PRIVATE_PARENT_SOURCE_IO'), 'unavailable'),
    (ValueError('STRUCTURED_TOOLS_REFERENCE_MISMATCH'), 'source_mismatch')])
def test_parent_postcheck_failure_keeps_qualified_fail_through_core(tmp_path, monkeypatch, failure, status):
    from guard.core.run import RunConfig, run_plan
    from guard.core.select import select
    from guard.core.judge import judge_evidence
    from guard.executors import registry
    from guard.models.checkdef import AppliesWhen, JudgeSpec, ObsSpec
    from guard.store import Store
    from tests.builders import policy
    validations = []
    def verify(*a, **kw):
        validations.append(True)
        if len(validations) > 1:
            raise failure
    monkeypatch.setattr(module, '_verify_source', verify)
    child_rows = record(native_after=True)['rows']
    def launch(argv, **kw):
        row = dict(child_rows[module.CASE_IDS.index(argv[argv.index('--child-case') + 1])],
                   source_commit=module.SOURCE_COMMIT, source_postcheck={'status': 'verified'},
                   source_attribution='pre_and_post_verified')
        return SimpleNamespace(stdout=module.PREFIX + json.dumps(row), returncode=0)
    monkeypatch.setattr(module.subprocess, 'run', launch)
    pack = baseline_pack()
    selected = check().model_copy(update={'applies_when': AppliesWhen(always=True), 'executor': module.ProfilerLifecycleCPUExecutor.name,
        'required_observations': [ObsSpec(key=key,type='bool' if key.endswith('mismatch') else 'list[dict]') for key in
            ('profiler_cpu_stop_mismatch','profiler_cpu_trials')],
        'judge': JudgeSpec(plugin='rule_table',params=module.judge_params())})
    pack.checks['LIF-14'] = selected  # Explicit EXAMPLE in-memory proposal.
    env = environment(engine={'name': 'vllm','version': 'reference-' + module.SOURCE_COMMIT})
    pol = policy(pack.ref(), values={}, scope={'include':['LIF-14']})
    store = Store(tmp_path/'store');store.put_many([env,pol])
    plan = select(pack,[env],pol);store.put(plan)
    monkeypatch.setitem(registry.EXECUTORS,'profiler_lifecycle_cpu:unit',lambda cfg: module.ProfilerLifecycleCPUExecutor(cfg))
    evd, = run_plan(store,pack,plan,RunConfig(executors={'profiler_lifecycle_cpu':'unit'},
        raw={'source_root':str(tmp_path/'source'),'owned_parent':str(tmp_path/'owned')}))
    assert evd.execution.status == ExecStatus.ABORTED
    assert all(item.attempted for item in evd.challenges)
    assert evd.observations['profiler_cpu_stop_mismatch'].value is True
    judged = judge_evidence(selected,evd,pol,plan.created_at,plan=plan)
    assert judged.verdicts.functional == Verdict.FAIL and judged.verdicts.security == Verdict.UNKNOWN
    artifact = json.loads((store.root / evd.artifacts[0].path).read_text())
    assert artifact['source_postcheck']['status'] == status
    assert all(row['source_postcheck']['status']=='verified' for row in artifact['rows'])
    assert 'PRIVATE_' not in json.dumps(artifact)


def test_unverified_initial_source_is_not_a_collected_false():
    data = record(native_after=True)
    for row in data['rows']:
        row['source_preverified'] = False
    assert module.project(data)['profiler_cpu_stop_mismatch'] == {'value': None, 'collected': False}


def test_component_rejects_foreign_noop_helper_before_output(tmp_path):
    update = Path(tool.__file__).resolve().parents[1]
    foreign = tmp_path/'foreign'/'tools';foreign.mkdir(parents=True)
    (foreign/'__init__.py').write_text('')
    marker = tmp_path/'noop-was-called'
    (foreign/'b1_integration_cpu.py').write_text("BASE_SHA=" + repr(tool.BASE_SHA) + "\nPINNED_PATHS=()\n"
        "def verify_snapshot(*a,**kw):\n from pathlib import Path\n Path(" + repr(str(marker)) + ").write_text('noop')\n")
    output = tmp_path/'output'
    result = subprocess.run([sys.executable,str(Path(tool.__file__).resolve()),'--source-root',str(tmp_path/'missing'),
        '--output-root',str(output)],cwd=update,
        env={**os.environ,'PYTHONPATH':os.pathsep.join([str(foreign.parent),str(update/'src'),str(update)])},
        text=True,capture_output=True)
    assert result.returncode != 0 and 'LIF14_SNAPSHOT_HELPER_IDENTITY_OR_SCOPE_MISMATCH' in result.stderr
    assert not marker.exists() and not output.exists()


def test_component_rejects_empty_scope_even_for_own_helper(monkeypatch):
    monkeypatch.setattr(tool.snapshot_helper,'PINNED_PATHS',())
    with pytest.raises(ValueError,match='LIF14_SNAPSHOT_HELPER_IDENTITY_OR_SCOPE_MISMATCH'):
        tool.verify_component(Path(__file__).resolve().parents[2])


@pytest.mark.parametrize('status', ['unavailable', 'source_mismatch'])
def test_child_postcheck_incomplete_rows_remain_facts_under_parent_verified_snapshot(tmp_path, monkeypatch, status):
    monkeypatch.setattr(module, '_verify_source', lambda *a, **kw: None)
    rows = record(native_after=True)['rows']
    def launch(argv, **kw):
        row = dict(rows[module.CASE_IDS.index(argv[argv.index('--child-case')+1])],
                   source_commit=module.SOURCE_COMMIT, capture_status='post_source_check_incomplete',
                   source_postcheck={'status':status,'error_type':'OSError' if status=='unavailable' else 'ValueError'},
                   source_attribution='postcheck_unavailable' if status=='unavailable' else 'unconfirmed_after_source_mismatch')
        return SimpleNamespace(stdout=module.PREFIX+json.dumps(row),returncode=0)
    monkeypatch.setattr(module.subprocess,'run',launch)
    result = module.capture(tmp_path/'source',tmp_path/'owned')
    assert result['capture_status']=='child_capture_incomplete'
    assert result['source_postcheck']['status']=='verified'
    assert all(row['source_postcheck']['status']==status for row in result['rows'])
    assert verdict(module.project(result))['functional']==Verdict.FAIL
