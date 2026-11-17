"""All active sides stay visible; offline and synthetic inputs never become live proof."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess

import pytest

from guard.models import load_pack
from tools import mvp_live_readiness as tool
from tests.test_mvp_a_input_preflight import sources  # noqa: F401


def run(tmp_path, value):
    index = tmp_path / 'index.json'
    index.write_text(json.dumps(value), encoding='utf-8')
    return tool.audit(index)


def blank():
    return tool.template(load_pack(tool.ROOT / 'packs/PACK-BASELINE'))


def test_all_69_checks_and_138_sides_remain_visible_without_network_or_execution(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('offline readiness must not access a target or execute commands')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    report = run(tmp_path, blank())
    assert report['active_checks'] == 69 and len(report['rows']) == report['sides'] == 138
    assert len({(r['check_id'], r['side']) for r in report['rows']}) == 138
    assert all('ACTUAL_INPUT_FILES_MISSING' in r['blocking_codes'] for r in report['rows'])
    assert report['blocking_counts']['SECURITY_RULES_EMPTY'] == 2
    assert {r['check_id'] for r in report['rows'] if not r['security_rules_present']} == {'SUP-03'}
    assert all(r['engine_executed'] is r['ready_for_execution'] is r['live_target_verified'] is False
               and r['actual_results'] is None for r in report['rows'])
    assert report['engine_executed'] is report['ready_for_execution'] is False


@pytest.mark.parametrize('damage', ['pack', 'retired', 'side', 'duplicate', 'extra'])
def test_invalid_or_ambiguous_sources_are_rejected(tmp_path, damage):
    value = blank()
    item = dict(check_id='SUP-08', side='baseline', environment=None, policy=None, configuration=None)
    value['inputs'] = [item]
    if damage == 'pack': value['pack_ref']['revision'] -= 1
    if damage == 'retired': item['check_id'] = 'SUP-01'
    if damage == 'side': item['side'] = 'single'
    if damage == 'duplicate': value['inputs'].append(deepcopy(item))
    if damage == 'extra': item['ready_for_execution'] = True
    with pytest.raises(ValueError, match='MVP_SOURCE_'):
        run(tmp_path, value)


def test_supplied_file_hash_mismatch_is_recorded_without_hiding_other_137_sides(tmp_path):
    value = blank();path = tmp_path / 'env.json';path.write_text('{}')
    pointer = {'path': str(path), 'sha256': '0' * 64}
    value['inputs'] = [dict(check_id='SUP-08', side='baseline', environment=pointer,
                            policy=pointer, configuration=pointer)]
    report = run(tmp_path, value)
    damaged = next(r for r in report['rows'] if (r['check_id'], r['side']) == ('SUP-08', 'baseline'))
    assert damaged['blocking_codes'] == ['MVP_INPUT_HASH_MISMATCH']
    assert report['blocking_counts']['ACTUAL_INPUT_FILES_MISSING'] == 137


def test_example_file_binding_does_not_issue_live_readiness(tmp_path, sources):  # noqa: F811
    value = blank();value['inputs'] = sources['inputs']
    report = run(tmp_path, value)
    bound = [r for r in report['rows'] if r['file_binding_valid']]
    assert report['file_binding_valid_sides'] == len(bound) == 2
    assert all('EXAMPLE_ENV_NOT_ACTUAL' in r['blocking_codes'] for r in bound)
    assert all('LIVE_EXECUTOR_PREFLIGHT_REQUIRED' in r['blocking_codes'] for r in bound)
    assert all(not r['ready_for_execution'] and r['actual_results'] is None for r in bound)
    assert report['blocking_counts']['ACTUAL_INPUT_FILES_MISSING'] == 136


def test_fake_executor_cannot_be_an_active_execution_input(tmp_path, sources):  # noqa: F811
    value = blank();value['inputs'] = sources['inputs'][:1]
    pointer = value['inputs'][0]['configuration'];path = Path(pointer['path'])
    path.write_bytes(path.read_bytes().replace(b'kubernetes', b'fake'))
    pointer['sha256'] = sha256(path.read_bytes()).hexdigest()
    report = run(tmp_path, value)
    row = next(r for r in report['rows'] if (r['check_id'], r['side']) == ('SUP-08', 'baseline'))
    assert row['file_binding_valid'] is False
    assert row['blocking_codes'] == ['MVP_ACTIVE_EXECUTOR_REQUIRED']
