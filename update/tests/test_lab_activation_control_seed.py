"""The actor selects a physical worker only after the selected profile parser."""
from types import SimpleNamespace
import pytest
from guard.executors import lab_activation as actor
from guard.adapters import vllm_rank_failure_metadata as legacy


@pytest.fixture
def seed(monkeypatch):
    monkeypatch.setattr(legacy,'validate_sample',lambda *_:(_ for _ in ()).throw(AssertionError('legacy13 parser must not run')))
    calls=[]
    def validate(value):calls.append(value)
    module=SimpleNamespace(PROFILE='vllm-native-lifecycle-metadata/24',validate_source_rank=validate)
    engine={'pod':'selected','pod_uid':'selected-uid'};census_calls=[]
    def census(*args):
        census_calls.append(args);return {'processes':[{'pid':107,'start_ticks':123,'state':'R'}]}
    lab=SimpleNamespace(cfg={},census=census);ctx={'module':module,'binding':{'engine':engine}}
    def recording(pid,value):
        return {'session_owner_match':True,'listed_file_identity_match':True,'capture':{
            'process_context_verified':True,'image_digest_verified':True,'records':[
                {'event':'START','payload':{'context':{'pid':pid,'start_ticks':123}}},
                {'event':'RANK_FAILURE','payload':value}]}}
    executor={'source_verified':True,'role':'executor','world_size':1,'configured_output_rank':0}
    worker={'source_verified':True,'role':'worker','rank':0,'point':'WorkerProc._execute_worker_rpc','rpc_method':'execute_model'}
    packet={'schema':'guard-lifecycle-session-capture/1','before':{'metadata':{'sealed':False}},
        'after':{'metadata':{'sealed':False}},'recordings':[recording(1,executor),recording(107,worker)]}
    return SimpleNamespace(lab=lab,ctx=ctx,module=module,packet=packet,calls=calls,census_calls=census_calls,worker=worker)


def test_seed_delegates_both_rank_samples_to_selected_profile_and_keeps_pid_epoch(seed):
    f=seed;target=actor._control_seed(f.lab,f.ctx,f.packet,0)
    assert len(f.calls)==2 and f.calls[-1]is f.worker
    assert target=={'pod':'selected','pod_uid':'selected-uid','pid':107,'start_ticks':123,'state':'R'}
    assert f.census_calls==[('selected','selected-uid')]


@pytest.mark.parametrize('validator',[None,False,{},'not-callable'])
def test_missing_profile_validator_refuses_before_census_with_no_legacy_fallback(seed,validator):
    f=seed;f.module.validate_source_rank=validator
    with pytest.raises(actor.ActivationRefused,match='RANK_VALIDATOR_UNAVAILABLE'):
        actor._control_seed(f.lab,f.ctx,f.packet,0)
    assert f.census_calls==[]


def test_selected_parser_rejection_never_selects_physical_pid(seed):
    f=seed;f.module.validate_source_rank=lambda *_:(_ for _ in ()).throw(ValueError('source/body ABI rejected'))
    with pytest.raises(ValueError,match='ABI rejected'):actor._control_seed(f.lab,f.ctx,f.packet,0)
    assert f.census_calls==[]
