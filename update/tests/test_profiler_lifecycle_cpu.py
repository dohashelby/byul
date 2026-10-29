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
    phase = {'wrapper_running_after': False, 'native_after': {'value': native_after, 'collected': True},
        'recorded_tag_events': 1, 'output': {'tag_events': 1}}
    return {'rows': [{'case_id': name, 'capture_status': 'completed', 'primary': dict(phase), 'next': dict(phase),
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
