"""두 판본 기동/HTTP 후보 데이터는 자격 증명이나 실측을 대신하지 않는다."""
import json
from pathlib import Path
import re

import yaml
import pytest

from guard.adapters import engine_profiles
from guard.adapters.base import Route
from guard.adapters.engine_provider import get_provider
from guard.adapters.git_diff import RULES_PATH, classify_path
from guard.adapters.vllm import VllmAdapter
from guard.adapters.vllm_release_profiles import release_profile, http_profile
from guard.executors.registry import resolve_executor
from tools import gpu_a_aws_lab as lab

ROOT = Path(__file__).resolve().parents[1]


def pair():
    text = (ROOT / 'docs/WORK_ORDER_VLLM_MVP.md').read_text()
    section = text.split('### 0.1 MVP 판본 표 (데이터)', 1)[1].split('### 0.2', 1)[0]
    rows = re.findall(r'^\| (기준 판본|후보 판본) \| vLLM ([^| ]+) \| `([^`]+)`', section, re.M)
    assert len(rows) == 2
    return rows


def test_both_launch_profiles_follow_the_work_order_without_runtime_qualification():
    provider = get_provider('vllm')
    for role, version, image in pair():
        row = provider.release_profile(version)
        assert row['role'] == role and image in row['images']
        assert row['pin_status'] == 'candidate_pin' and row['launch']['command']
        assert row['launch']['model_argument'] in {'--model', 'positional'}
        assert row['launch']['default_engine'] == ('V0' if role == '기준 판본' else 'V1')
        support = engine_profiles.resolve('vllm', version, 'native_input')
        assert support.available and not support.runtime_qualified
        if role == '후보 판본':
            assert resolve_executor('engine_sensor', 'kubernetes', engine='vllm', version=version) is not None
            assert resolve_executor('request_termination', 'kubernetes', engine='vllm', version=version) is None
        else:
            assert resolve_executor('engine_sensor', 'kubernetes', engine='vllm', version=version) is None
        row['launch']['command'].clear()
        assert provider.release_profile(version)['launch']['command']
    assert provider.release_profile('EXAMPLE-future') is None


def test_candidate_http_rows_cannot_enable_safe_mutating_probes_or_rejection_proof():
    adapter = VllmAdapter()
    for role, version, image in pair():
        row = adapter.http_profile(version, image)
        assert row['pin_status'] == 'candidate_pin' and row['length_error_format']
        assert http_profile(version, image + '0') is None
        route = Route('POST', '/scale_elastic_ep', kind='admin', mutating=True)
        assert adapter.probe_request(route, 'EXAMPLE', version, image) is None
        proof = adapter.input_rejection_proof(engine_version=version, engine_image_digest=image,
            case='length', method='POST', path='/v1/completions',
            request_body={'model': 'EXAMPLE', 'prompt': [0] * 5, 'max_tokens': 1},
            response_status=400, response_body={'error': {'message': 'EXAMPLE'}})
        assert proof == {'state': 'unverified', 'reason': 'INPUT_REJECTION_CANDIDATE_PIN_NOT_IMAGE_VERIFIED', 'sources': []}
        if role == '기준 판본':
            assert row['safe_probe_candidates'] == []
            assert not engine_profiles.supports('vllm', version, 'native_lifecycle')
        else:
            assert len(row['safe_probe_candidates']) == 2
            support = engine_profiles.resolve('vllm', version, 'native_lifecycle')
            assert support.available and not support.runtime_qualified


def test_moved_path_review_maps_exact_files_and_retains_unmapped_paths():
    report = json.loads((ROOT / 'docs/dev-1/ma6-change-path-review.json').read_bytes())
    rules = yaml.safe_load(RULES_PATH.read_bytes())
    entries = [row for row in rules['rules'] if row.get('basis') == 'mvp-reviewed-exact-files']
    assert len(entries) == len(report['reviewed_paths']) == 17
    assert report['counts']['changed_paths'] == 8002
    assert report['counts']['unmapped_after'] > 0
    assert len(report['unmapped_after']) == report['counts']['unmapped_after']
    for row in report['reviewed_paths']:
        assert classify_path(row['path'], rules) == (row['categories'], False)
        assert re.fullmatch('[0-9a-f]{64}', row['source_sha256'])
        assert row['source_bytes'] > 0 and row['pin_status'] == 'candidate_pin'
    for path in ['vllm/worker/unknown_future.py', 'vllm/core/new_block_allocator.py',
                 'vllm/v1/engine/core.py', 'vllm/config/vllm.py']:
        assert classify_path(path, rules) == ([], False)


@pytest.mark.parametrize('check_id', ['REQ-16', 'REQ-17'])
def test_approved_cache_comparison_enables_both_sides_explicitly(check_id):
    handoff = ROOT / 'tests/fixtures/aws-handoff-20261026'
    for role, version, image in pair():
        raw, _, record = lab.render(handoff, generation='v2-default', profile='dev', run_id='cache-compare',
            engine_image=image, engine_version=version, model_revision='a' * 40,
            host_root='/srv/byul-a', check_id=check_id)
        deployment, _ = yaml.safe_load_all(raw)
        args = deployment['spec']['template']['spec']['containers'][0]['args']
        assert args.count('--enable-prefix-caching') == 1
        assert record['launch']['prefix_cache'] is True
        with pytest.raises(ValueError, match='AWS_CHECK_LAUNCH_PROFILE_UNAVAILABLE'):
            lab.render(handoff, generation='v2-default', profile='dev', run_id='cache-compare',
                engine_image=image[:-64] + 'e' * 64, engine_version=version, model_revision='a' * 40,
                host_root='/srv/byul-a', check_id=check_id)
