"""The root's reviewed Gateway probe runs between failure and pinned restore."""
import hashlib,importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from guard.executors import lab_activation as actor
from guard.models import Policy
from tools import b_lif08_fault_control as tool

_spec=importlib.util.spec_from_file_location('_window_primary_fixture',Path(__file__).with_name('test_lab_activation_primary_capture.py'))
_fixture=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_fixture)


@pytest.fixture
def trial(tmp_path,monkeypatch):
    lab=_fixture.Roundtrip();lab.cfg['initialization_failure_kind']='model';operations=[]
    original=lab.apply
    def apply(key):operations.append(key);original(key)
    lab.apply=apply
    for key in ('faulty_manifest','good_manifest'):
        p=tmp_path/(key+'.yaml');p.write_text(key)
        lab.cfg[key]=str(p);lab.cfg[key+'_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    def project(receipt,*,expected):
        assert lab.stage=='after' and receipt['recovery'] is not None
        return {'activation_steps':[],'activation_missing':[],'activation_violations':[],'activation_scope_complete':True}
    module=SimpleNamespace(project_terminal_case=project,__file__=actor.__file__)
    monkeypatch.setitem(sys.modules,'guard.executors.initialization_terminal_case',module)
    monkeypatch.setattr(actor,'Lab',lambda *_args,**_kwargs:lab)
    policy=Policy(id='POL-test',data_origin='DECLARED',status='DRAFT',pack={
        'kind':'PACK','id':'PACK-test','revision':1,'sha256':'a'*64},values={'LIF-09':{'init_preparation_deadline_ms':10000}}).seal()
    return SimpleNamespace(lab=lab,policy=policy,operations=operations,module=module)


def begin(trial):return tool.begin_initialization_failure(trial.lab.cfg,trial.policy,'f'*40)


def test_explicit_failure_window_is_open_for_probe_and_restore_is_idempotent(trial,tmp_path):
    window=begin(trial)
    assert trial.lab.stage=='bad' and trial.operations==['faulty_manifest']
    assert window.receipt['admission']['engine']['attempt']['exit_code']==1
    assert window.receipt['recovery'] is None and 0<window.gateway_probe_budget_seconds<=5
    # Root uses its existing A source validator; B preserves an opaque hash ref.
    ref_path=tmp_path/'gateway-original.json';ref_path.write_text('{"actual_probe":"source-owned-by-A"}')
    ref={'path':str(ref_path),'sha256':hashlib.sha256(ref_path.read_bytes()).hexdigest()}
    result=tool.finish_initialization_restore(window,gateway_evidence_ref=ref)
    assert trial.lab.stage=='after' and trial.operations==['faulty_manifest','good_manifest']
    assert result['gateway_observation_ref']==ref and result['gateway_target_delivery_binding_verified'] is False
    assert result['activation']['activation_scope_complete'] is True
    result['activation']['activation_scope_complete']=False
    again=tool.finish_initialization_restore(window)
    assert again['activation']['activation_scope_complete'] is True and len(trial.operations)==2


def test_gateway_probe_exception_still_closes_window_and_restores(trial):
    window=begin(trial)
    with pytest.raises(RuntimeError,match='probe failure'):
        with window:
            assert trial.lab.stage=='bad'
            raise RuntimeError('probe failure')
    assert trial.lab.stage=='after' and window.result['receipt']['recovery'] is not None


def test_invalid_gateway_hash_cannot_block_restore(trial,tmp_path):
    window=begin(trial)
    result=tool.finish_initialization_restore(window,gateway_evidence_ref={'path':str(tmp_path/'missing.json'),'sha256':None})
    assert trial.lab.stage=='after' and result['gateway_observation_ref'] is None
    assert 'INITIALIZATION_GATEWAY_REFERENCE_UNVERIFIED' in result['activation']['activation_missing']


def test_changed_config_restores_original_owned_scope_and_preserves_gap(trial):
    window=begin(trial);original=trial.lab.cfg['namespace']
    trial.lab.cfg['namespace']='another-namespace'
    result=tool.finish_initialization_restore(window)
    assert trial.lab.cfg['namespace']==original and trial.lab.stage=='after'
    assert 'INITIALIZATION_WINDOW_CONFIG_CHANGED' in result['activation']['activation_missing']


def test_another_process_cannot_consume_or_mutate_parent_window(trial,monkeypatch):
    window=begin(trial);original=tool.os.getpid
    monkeypatch.setattr(tool.os,'getpid',lambda:original()+1)
    with pytest.raises(ValueError,match='PROCESS_MISMATCH'):tool.finish_initialization_restore(window)
    assert trial.lab.stage=='bad' and trial.operations==['faulty_manifest']
    monkeypatch.setattr(tool.os,'getpid',original)
    assert tool.finish_initialization_restore(window)['receipt']['recovery'] is not None


def test_source_change_is_unknown_after_pinned_restore(trial,monkeypatch):
    window=begin(trial);monkeypatch.setattr(tool,'module_sha',lambda _module:'0'*64)
    result=tool.finish_initialization_restore(window)
    assert trial.lab.stage=='after' and 'INITIALIZATION_WINDOW_SOURCE_CHANGED' in result['activation']['activation_missing']
