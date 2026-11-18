"""One declared side covers all 69 checks without live or security promotion."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess

import pytest

from guard.models import load_pack
from tests.test_mvp_a_input_preflight import sources
from tools import mvp_live_readiness as tool


def index_file(tmp_path,inputs=()):
    data=tool.template(load_pack(tool.ROOT/'packs/PACK-BASELINE'))
    data['inputs']=list(inputs);path=tmp_path/'index.json'
    path.write_text(json.dumps(data),encoding='utf-8')
    return path


@pytest.mark.parametrize('side',tool.SIDES)
def test_one_side_keeps_all_69_checks_and_never_runs_targets(tmp_path,monkeypatch,side):
    def forbidden(*args,**kwargs):raise AssertionError('offline inventory must not execute or connect')
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    report=tool.audit(index_file(tmp_path),side=side)
    assert report['active_checks']==report['sides']==len(report['rows'])==69
    assert len({row['check_id']for row in report['rows']})==69
    assert {row['side']for row in report['rows']}=={side}
    assert report['selection']=={'mode':'one-declared-side','side':side,'expected_engine':tool.versions()[side]}
    assert all('ACTUAL_INPUT_FILES_MISSING'in row['blocking_codes']for row in report['rows'])
    assert report['engine_executed']is report['ready_for_execution']is False
    assert all(row['engine_executed']is row['ready_for_execution']is row['live_target_verified']is False
        and row['actual_results']is None for row in report['rows'])


@pytest.mark.parametrize('side',['single','production','',True,1])
def test_invalid_side_does_not_read_the_input_file(tmp_path,side):
    with pytest.raises(ValueError,match='MVP_SIDE_SELECTION_INVALID'):
        tool.audit(tmp_path/'absent.json',side=side)


def test_one_side_cannot_silently_drop_other_side_sources(tmp_path,sources):
    with pytest.raises(ValueError,match='MVP_SOURCE_INDEX_SCOPE_MISMATCH'):
        tool.audit(index_file(tmp_path,sources['inputs']),side='candidate')


@pytest.mark.parametrize('side',tool.SIDES)
def test_valid_example_files_remain_one_bound_example_and_68_missing_checks(tmp_path,sources,side):
    items=[item for item in sources['inputs']if item['side']==side]
    assert len(items)==1
    report=tool.audit(index_file(tmp_path,items),side=side)
    assert report['file_binding_valid_sides']==1 and report['sides']==69
    assert report['blocking_counts']['ACTUAL_INPUT_FILES_MISSING']==68
    row,=[row for row in report['rows']if row['file_binding_valid']]
    assert 'EXAMPLE_ENV_NOT_ACTUAL'in row['blocking_codes']
    assert 'LIVE_EXECUTOR_PREFLIGHT_REQUIRED'in row['blocking_codes']
    assert row['ready_for_execution']is row['engine_executed']is False and row['actual_results']is None


def test_one_side_keeps_exact_engine_and_environment_binding_validation(tmp_path,sources):
    items=deepcopy([item for item in sources['inputs']if item['side']=='candidate'])
    other,=[item for item in sources['inputs']if item['side']=='baseline']
    items[0]['environment']=deepcopy(other['environment'])
    report=tool.audit(index_file(tmp_path,items),side='candidate')
    row,=[row for row in report['rows']if row['check_id']==items[0]['check_id']]
    assert row['file_binding_valid']is False and row['blocking_codes']==['MVP_ENGINE_SIDE_MISMATCH']
    assert report['file_binding_valid_sides']==0


def test_default_two_side_inventory_is_preserved(tmp_path):
    report=tool.audit(index_file(tmp_path))
    assert report['active_checks']==69 and report['sides']==138
    assert 'selection'not in report and {row['side']for row in report['rows']}==set(tool.SIDES)
