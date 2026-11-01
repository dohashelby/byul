"""EXAMPLE child-process contracts using an approved synthetic collector fixture.

No Torch/source runtime or Kubernetes target is executed. No registry replacement
is used; A's permanent common-CLI registration is a separate integration step.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest
from guard.core.select import select, PolicyValueError
from guard.executors.template_source_cpu import TemplateSourceCPUExecutor, SOURCE_COMMIT
from guard.models import DataOrigin, ExecStatus, Environment, Policy
from tests.builders import baseline_pack
from tests.test_template_boundary_evidence import record
from tests.test_template_sandbox import definition, judge

PRIVATE = 'PRIVATE_SOURCE_CHILD_STDERR_OR_EXCEPTION'
VALUES = {'forbidden_template_attributes': ['__class__']}


def world(tmp_path, *, consumed=False, action='', raw=None, captured=None):
    """The fixture bytes are the independent approval in this EXAMPLE test."""
    repo = tmp_path / 'approved-producer'
    tools = repo / 'update/tools'
    tools.mkdir(parents=True)
    (tools / '__init__.py').write_text('')
    callback = repo / 'update/src/guard/adapters/vllm_template_boundary.py'
    callback.parent.mkdir(parents=True)
    callback.write_bytes(b'# EXAMPLE approved callback bytes\n')
    value = deepcopy(captured) if captured is not None else record(consumed)
    value['native_callback_sha256'] = hashlib.sha256(callback.read_bytes()).hexdigest()
    code = """import argparse,hashlib,json,os,pathlib,sys
p=argparse.ArgumentParser();p.add_argument('--source-root',required=True);p.add_argument('--workdir',required=True);a=p.parse_args()
out=pathlib.Path(a.workdir);out.mkdir(exist_ok=False)
value=json.loads(RECORD)
value['own_producer_sha256']=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
(out/'result.json').write_text(json.dumps(value))
(out.parent/'receipt.json').write_text(json.dumps({'argv':sys.argv[1:],'optimize':sys.flags.optimize,
'cwd':os.getcwd(),'env':{k:os.environ.get(k) for k in ('CUDA_VISIBLE_DEVICES','VLLM_PLUGINS','VLLM_USE_V1','VLLM_TARGET_DEVICE','HF_HUB_OFFLINE','PYTHONPATH','TMPDIR','HF_HOME')}}))
print('PRIVATE_SOURCE_CHILD_STDERR_OR_EXCEPTION',file=sys.stderr)
ACTION
""".replace('RECORD', repr(json.dumps(value))).replace('ACTION', action)
    if raw is not None:
        code += "\n(out/'result.json').write_bytes(" + repr(raw) + ")\n"
    collector = tools / 'b_sup13_template_cpu.py'
    collector.write_text(code)
    source = tmp_path / 'EXAMPLE-source'
    (source / 'vllm').mkdir(parents=True)
    cfg = {'source_root': str(source), 'python_binary': sys.executable, 'producer_checkout': str(repo),
        'collector_sha256': hashlib.sha256(code.encode()).hexdigest(), 'callback_sha256': value['native_callback_sha256']}
    env = Environment(id='ENV-SUP13-CHILD-EXAMPLE', data_origin=DataOrigin.EXAMPLE,
        engine={'name': 'vllm', 'version': 'source-' + SOURCE_COMMIT}).seal()
    work = tmp_path / 'work'; work.mkdir()
    return cfg, env, work, collector


def run(world, values=None):
    cfg, env, work, _ = world
    return TemplateSourceCPUExecutor(cfg).run(definition(), env, VALUES if values is None else values, None, work)


def test_real_child_owned_path_fixed_argv_and_safe_draft_without_parent_torch(tmp_path):
    setup = world(tmp_path)
    draft = run(setup)
    cfg, _, work, _ = setup
    receipt = json.loads((work / 'receipt.json').read_text())
    assert receipt['argv'] == ['--source-root', cfg['source_root'], '--workdir', str(work / 'source-cpu-capture')]
    assert receipt['optimize'] == 0
    assert receipt['env']['CUDA_VISIBLE_DEVICES'] == '' and receipt['env']['VLLM_TARGET_DEVICE'] == 'cpu'
    assert receipt['env']['HF_HUB_OFFLINE'] == '1'
    assert receipt['env']['PYTHONPATH'].split(os.pathsep) == [cfg['producer_checkout'] + '/update', cfg['producer_checkout'] + '/update/src', cfg['source_root']]
    assert Path(receipt['env']['TMPDIR']).is_relative_to(work)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert draft.config['installed_target'] is None and not draft.config['gpu_execution_verified']
    assert not draft.observations['template_sandbox_scope_complete'].value
    assert draft.observations['functional_probe_results'].value == {'POST /v1/chat/completions': 'unknown'}
    assert judge(draft).verdicts['security'].value == 'UNKNOWN'
    assert judge(draft).verdicts['functional'].value == 'UNKNOWN'
    artifact = draft.artifacts[0].path.read_text()
    assert PRIVATE not in artifact and 'guard sandbox' not in artifact


def test_common_runner_fresh_nested_workdir_collects_and_saves_artifact(tmp_path):
    cfg, env, _, collector = world(tmp_path, consumed=True)
    work = tmp_path / 'store' / 'work' / 'PLAN-new' / 'SUP-13' / 'candidate'
    assert not work.exists()
    draft = run((cfg, env, work, collector))
    assert draft.status == ExecStatus.COMPLETED
    assert draft.normal_control.succeeded is True
    assert judge(draft).verdicts['security'].value == 'FAIL'
    assert len(draft.artifacts) == 1
    assert draft.artifacts[0].path == work / 'template-source-cpu.json'
    assert draft.artifacts[0].path.is_file()


@pytest.mark.parametrize('action', ['', 'sys.exit(9)', 'value=None; raise OSError("PRIVATE_SOURCE_CHILD_STDERR_OR_EXCEPTION")'])
def test_independent_existing_rule_fail_survives_child_late_failure(tmp_path, action):
    draft = run(world(tmp_path, consumed=True, action=action))
    assert draft.observations['forbidden_template_type_reference_verified'].value is True
    assert judge(draft).verdicts['security'].value == 'FAIL'
    assert draft.status == (ExecStatus.ABORTED if action else ExecStatus.COMPLETED)
    assert draft.normal_control.succeeded is True
    assert PRIVATE not in json.dumps(draft.model_dump(mode='json'))


def test_artifact_failure_preserves_independent_policy_fail(tmp_path):
    setup = world(tmp_path, consumed=True)
    artifact = setup[2] / 'template-source-cpu.json'
    artifact.write_text('existing append-only evidence')
    draft = run(setup)
    assert draft.status == ExecStatus.ABORTED and not draft.artifacts
    assert draft.normal_control.succeeded is True
    assert artifact.read_text() == 'existing append-only evidence'
    assert judge(draft).verdicts['security'].value == 'FAIL'


@pytest.mark.parametrize('kind', ['collector', 'callback'])
def test_unapproved_bytes_block_child_before_launch(tmp_path, kind):
    setup = world(tmp_path, consumed=True)
    setup[0][kind + '_sha256'] = 'f' * 64
    draft = run(setup)
    assert draft.status == ExecStatus.ABORTED and not (setup[2] / 'receipt.json').exists()
    assert draft.observations['forbidden_template_type_reference_verified'].value is None
    assert judge(draft).verdicts['security'].value == 'UNKNOWN'


def test_changed_collector_after_run_cannot_qualify_json_self_claims(tmp_path):
    draft = run(world(tmp_path, consumed=True, action="pathlib.Path(__file__).write_text('# changed')"))
    assert draft.status == ExecStatus.ABORTED
    assert draft.observations['forbidden_template_type_reference_verified'].value is None
    assert judge(draft).verdicts['security'].value == 'UNKNOWN'


@pytest.mark.parametrize('raw', [b'{"schema":1,"schema":2}', b'not-json', b' ' * (1024 * 1024 + 1)])
def test_invalid_or_oversize_child_result_is_uncollected(tmp_path, raw):
    draft = run(world(tmp_path, consumed=True, raw=raw))
    assert draft.status == ExecStatus.ABORTED
    assert draft.observations['forbidden_template_type_reference_verified'].value is None


def test_stale_child_result_cannot_be_imported_as_new_observation(tmp_path):
    setup = world(tmp_path, consumed=True)
    owned = setup[2] / 'source-cpu-capture'; owned.mkdir(); (owned / 'result.json').write_text('{}')
    draft = run(setup)
    assert draft.status == ExecStatus.ABORTED and not (setup[2] / 'receipt.json').exists()


def test_valid_empty_policy_remains_unknown_and_null_rejected(tmp_path):
    setup = world(tmp_path, consumed=True)
    draft = run(setup, {'forbidden_template_attributes': []})
    assert draft.observations['forbidden_template_type_reference_verified'].value is False
    assert judge(draft, {'forbidden_template_attributes': []}).verdicts['security'].value == 'UNKNOWN'
    with pytest.raises(ValueError): run(setup, {'forbidden_template_attributes': None})


def test_original_select_preserves_missing_and_null_policy_without_a_registry_override(tmp_path):
    pack = baseline_pack()
    cfg, env, _, _ = world(tmp_path)
    policy = Policy(id='POL-SUP13-SOURCE-EXAMPLE', data_origin=DataOrigin.EXAMPLE, status='DRAFT', pack=pack.ref(),
        scope={'include': ['SUP-13']}).seal()
    plan = select(pack, [env], policy)
    item = next(item for item in plan.items if item.check_id == 'SUP-13')
    assert 'NO_POLICY_VALUE' in [gap.value for gap in item.gaps]
    policy = policy.model_copy(update={'values': {'SUP-13': {'forbidden_template_attributes': None}}}).seal()
    with pytest.raises(PolicyValueError): select(pack, [env], policy)


@pytest.mark.parametrize('change', ['version', 'model', 'image'])
def test_source_cpu_binding_cannot_become_live_or_reuse_model_environment(tmp_path, change):
    cfg, env, work, _ = world(tmp_path)
    data = env.model_dump(mode='python')
    if change == 'version': data['engine']['version'] = '0.31.0'
    elif change == 'image': data['engine']['image_digest'] = 'sha256:' + 'a' * 64
    else: data['model']['id'] = 'actual-served-model'
    env = Environment.model_validate(data).seal()
    with pytest.raises(ValueError, match='BINDING_INVALID'):
        TemplateSourceCPUExecutor(cfg).run(definition(), env, VALUES, None, work)
    assert not (work / 'receipt.json').exists()


def test_no_command_or_external_json_ingestion_configuration(tmp_path):
    cfg, _, _, _ = world(tmp_path)
    for key in ('command', 'result_json', 'capture', 'source_profile'):
        with pytest.raises(ValueError, match='CONFIGURATION_INVALID'):
            TemplateSourceCPUExecutor({**cfg, key: 'external'})


def test_inherited_optimization_is_cleared_for_actual_child(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTHONOPTIMIZE', '2')
    setup = world(tmp_path)
    assert run(setup).status == ExecStatus.COMPLETED
    assert json.loads((setup[2] / 'receipt.json').read_text())['optimize'] == 0


def test_symlink_result_cannot_ingest_an_external_record(tmp_path):
    other = tmp_path / 'external-result.json'
    other.write_text(json.dumps(record(True)))
    action = "(out/'result.json').unlink(); (out/'result.json').symlink_to(" + repr(str(other)) + ")"
    draft = run(world(tmp_path, consumed=True, action=action))
    assert draft.status == ExecStatus.ABORTED
    assert not draft.observations['forbidden_template_type_reference_verified'].collected


def test_failed_child_before_output_does_not_collect_empty_projection(tmp_path):
    setup = world(tmp_path, consumed=True)
    path = setup[3]
    code = "raise OSError('PRIVATE_SOURCE_CHILD_STDERR_OR_EXCEPTION')\n"
    path.write_text(code)
    setup[0]['collector_sha256'] = hashlib.sha256(code.encode()).hexdigest()
    draft = run(setup)
    assert draft.status == ExecStatus.ABORTED
    assert draft.observations['forbidden_template_type_reference_verified'].value is None
    assert not draft.observations['forbidden_template_type_reference_verified'].collected


def test_inherited_plugins_are_disabled_for_the_actual_owned_child(tmp_path, monkeypatch):
    monkeypatch.setenv('VLLM_PLUGINS', 'unapproved-inherited-plugin')
    setup = world(tmp_path)
    draft = run(setup)
    assert draft.status == ExecStatus.COMPLETED
    assert json.loads((setup[2] / 'receipt.json').read_text())['env']['VLLM_PLUGINS'] == ''


def test_normal_success_survives_auxiliary_write_failure(tmp_path):
    captured = record(True)
    captured.update(capture_status='incomplete', auxiliary_write={'status': 'failed'})
    draft = run(world(tmp_path, captured=captured))
    assert draft.status == ExecStatus.ABORTED and draft.normal_control.succeeded is True
    assert judge(draft).verdicts['security'].value == 'FAIL'


@pytest.mark.parametrize('status', ['missing', 'unqualified', 'unavailable', 'wrong_render', 'raised'])
def test_normal_control_uses_actual_outcomes_instead_of_global_capture_status(tmp_path, status):
    from tests.test_template_boundary_evidence import text
    captured = record(True)
    captured['capture_status'] = 'incomplete'
    after = captured['results'][2]
    if status == 'missing': captured['results'].pop()
    elif status == 'unqualified': after['source_validation']['post']['status'] = 'unavailable'
    elif status == 'unavailable': after.update(outcome='unavailable', result_summary=None)
    elif status == 'wrong_render': after['result_summary'] = text('EXAMPLE actual wrong normal render')
    else: after.update(outcome='raised', result_summary=None)
    draft = run(world(tmp_path, captured=captured))
    assert draft.normal_control.succeeded is (False if status in {'wrong_render', 'raised'} else None)
    assert draft.status == ExecStatus.ABORTED
    assert judge(draft).verdicts['security'].value == 'FAIL'


def test_source_template_execution_does_not_force_v1_runner_selection(tmp_path, monkeypatch):
    monkeypatch.setenv('VLLM_USE_V1', '0')
    setup = world(tmp_path)
    assert run(setup).status == ExecStatus.COMPLETED
    assert json.loads((setup[2] / 'receipt.json').read_text())['env']['VLLM_USE_V1'] == '0'
