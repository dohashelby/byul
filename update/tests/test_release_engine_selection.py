"""Engine selection is release data; the Pod declaration and comparison retain it."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from guard.adapters.engine_provider import VllmProvider
from guard.adapters.vllm_release_profiles import release_profile
from guard.models import canonical_json, sha256_hex
from guard.runtime.comparison import execution_conditions
from tests.test_gpu_a_run_inputs import deployed
from tests.test_ma6_release_profiles import pair
from tools import gpu_a_aws_lab as lab
from tools import gpu_a_run_inputs as inputs

HANDOFF = Path(__file__).parent / 'fixtures/aws-handoff-20261026'


def render(version, image, **kwargs):
    return lab.render(HANDOFF, generation=kwargs.pop('generation', 'v2-default'),
                      profile='dev', run_id='engine-selection', engine_image=image,
                      engine_version=version, model_revision='a' * 40,
                      host_root='/srv/byul-a', **kwargs)


def test_selected_engine_is_separate_from_upstream_default_and_image_qualification():
    for role, version, image in pair():
        profile = release_profile(version)
        raw, _, record = render(version, image, release_launch=True)
        deployment, _ = yaml.safe_load_all(raw)
        env = deployment['spec']['template']['spec']['containers'][0]['env']
        assert profile['launch']['default_engine'] == ('V0' if role == '기준 판본' else 'V1')
        assert record['launch']['engine'] == profile['launch']['engine'] == 'V1'
        assert record['launch']['env'] == profile['launch']['env']
        assert all({'name': k, 'value': v} in env for k, v in record['launch']['env'].items())
        assert profile['pin_status'] == 'candidate_pin'
        if role == '기준 판본':
            assert record['launch']['env'] == {'VLLM_USE_V1': '1'}


def test_future_engine_selection_and_rejected_options_need_only_profile_data(monkeypatch):
    version, image = '9.8.7', 'example/engine@sha256:' + 'd' * 64
    row = {'images': [image], 'check_launch_args': {}, 'launch': {
        'command': ['example-server'], 'model_argument': 'positional', 'engine': 'EXAMPLE-next',
        'env': {'EXAMPLE_ENGINE_MODE': 'next'}, 'unsupported_args': ['--example-incompatible']}}
    monkeypatch.setattr(VllmProvider, 'release_profile', lambda self, selected: deepcopy(row) if selected == version else None)
    raw, _, record = render(version, image, release_launch=True)
    deployment, _ = yaml.safe_load_all(raw)
    container, = deployment['spec']['template']['spec']['containers']
    assert container['command'] == ['example-server']
    assert {'name': 'EXAMPLE_ENGINE_MODE', 'value': 'next'} in container['env']
    assert record['launch']['engine'] == 'EXAMPLE-next'
    for option in ['--example-incompatible', '--example-incompatible=true']:
        with pytest.raises(ValueError, match='AWS_RELEASE_LAUNCH_ARGUMENT_UNSUPPORTED'):
            render(version, image, release_launch=True, extra_engine_args=[option])


def test_engine_selection_cannot_leak_to_another_image_or_conflicting_generation(monkeypatch):
    _, version, image = pair()[0]
    _, _, record = render(version, image[:-64] + 'f' * 64)
    assert record['launch']['env'] == {} and record['launch']['engine'] is None
    generation = deepcopy(lab.GENERATIONS['v2-default'])
    generation['env'] = {'VLLM_USE_V1': '0'}
    monkeypatch.setitem(lab.GENERATIONS, 'example-conflict', generation)
    with pytest.raises(ValueError, match='AWS_RELEASE_ENGINE_ENV_CONFLICT'):
        render(version, image, generation='example-conflict')


def test_baseline_v1_does_not_render_the_previous_lora_generation():
    _, version, image = pair()[0]
    with pytest.raises(ValueError, match='AWS_RELEASE_LAUNCH_ARGUMENT_UNSUPPORTED'):
        render(version, image, generation='v1-req16-lab')


def test_v1_selection_does_not_inherit_v0_observation_gaps_or_unknown_approvals():
    _, version, _ = pair()[0]
    profile = release_profile(version)
    for executor, gap in profile['executor_gaps'].items():
        assert gap['cause'] not in {'V0_OBSERVER_NOT_IMPLEMENTED', 'V0_TARGET_TABLE_ABSENT', 'APPROVED_UNKNOWN'}, executor
        assert 'V0' not in (gap.get('next') or ''), executor


@pytest.mark.parametrize('damage', ['missing', 'wrong-value', 'duplicate', 'value-from'])
def test_engine_env_must_match_the_observed_pod_declaration(tmp_path, damage):
    _, version, image = pair()[0]
    pods, record = deployed(tmp_path, 'v2-default', engine_version=version, engine_image=image)
    env = inputs.environment(record, gpu_model='EXAMPLE-GPU', driver='EXAMPLE', data_origin='EXAMPLE')
    namespace = json.loads((tmp_path / 'namespace.json').read_text())
    target = inputs.target(env, record, namespace, pods['engine'], pods['probe'], context='EXAMPLE')
    assert 'engine_requested=V1' in env.runtime.platform
    assert target['comparison_config']['engine_selection'] == 'V1'
    entries = pods['engine']['spec']['containers'][0]['env']
    selected, = (item for item in entries if item['name'] == 'VLLM_USE_V1')
    if damage == 'missing':
        entries.remove(selected)
    elif damage == 'wrong-value':
        selected['value'] = '0'
    elif damage == 'duplicate':
        entries.append(dict(selected))
    else:
        selected.pop('value')
        selected['valueFrom'] = {'fieldRef': {'fieldPath': 'metadata.name'}}
    with pytest.raises(ValueError, match='RUN_INPUTS_RENDERED_ENGINE_ENV_MISMATCH'):
        inputs.target(env, record, namespace, pods['engine'], pods['probe'], context='EXAMPLE')


def test_equivalent_selections_compare_but_historical_unspecified_selection_is_not_promoted(tmp_path):
    conditions = []
    for role, version, image in pair():
        folder = tmp_path / version
        folder.mkdir()
        pods, record = deployed(folder, 'v2-code-selection-trust', engine_version=version, engine_image=image,
                                model_id='local-byul/sup08-phi3v-derived', code_revision='b' * 40)
        env = inputs.environment(record, gpu_model='EXAMPLE-GPU', driver='EXAMPLE',
                                 observed_engine_version=version, data_origin='EXAMPLE')
        namespace = json.loads((folder / 'namespace.json').read_text())
        target = inputs.target(env, record, namespace, pods['engine'], pods['probe'], context='EXAMPLE')
        conditions.append(execution_conditions(target, {}, []))
        if role == '기준 판본':
            old = deepcopy(record)
            old['launch'].pop('engine')
            old['launch'].pop('env')
            pods['engine']['spec']['containers'][0]['env'] = [
                entry for entry in pods['engine']['spec']['containers'][0]['env'] if entry['name'] != 'VLLM_USE_V1']
            historical_env = inputs.environment(old, gpu_model='EXAMPLE-GPU', driver='EXAMPLE',
                                                observed_engine_version=version, data_origin='EXAMPLE')
            historical = inputs.target(historical_env, old, namespace, pods['engine'], pods['probe'], context='EXAMPLE')
            assert 'engine_requested=V1' not in historical_env.runtime.platform
            assert 'engine_selection' not in historical['comparison_config']
            assert execution_conditions(historical, {}, []) != conditions[-1]
    assert sha256_hex(canonical_json(conditions[0])) == sha256_hex(canonical_json(conditions[1]))
