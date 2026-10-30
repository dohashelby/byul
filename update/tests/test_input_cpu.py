"""Fixture/projection contracts; actual CPU validation is recorded separately."""
import json
import pytest
from guard.executors.input_cpu import fixtures, describe, project, judge_params
from guard.judges.rule_table import RuleTableJudge
from guard.models import Verdict
from tests.builders import evidence
from tests.test_sampling_validation import check


def test_scoped_fixtures_do_not_invent_shape_policy():
    rows = fixtures()
    assert len(rows) == 26 and len({r['id'] for r in rows}) == 26
    assert {r['check_id'] for r in rows} == {'REQ-07', 'REQ-08', 'REQ-09'}
    assert all(r['expected'] is None for r in rows if r['kind'] == 'shape')
    rows[0]['value'].clear()
    assert fixtures()[0]['value']


def test_descriptions_omit_raw_inputs():
    for row in fixtures():
        value = describe(row)
        assert set(value) == {'case_id', 'check_id', 'stage', 'expected', 'fixture_canonical_sha256'}
        assert len(value['fixture_canonical_sha256']) == 64 and 'hello' not in json.dumps(value)


@pytest.mark.parametrize('check_id', ['REQ-07', 'REQ-08', 'REQ-09'])
def test_violation_survives_other_missing_rows_and_followup_error(check_id):
    fixture = next(r for r in fixtures() if r['check_id'] == check_id and r['expected'] == 'rejected')
    row = {**describe(fixture), 'outcome': 'accepted', 'after': {'outcome': 'execution_error'}}
    obs = project([row])[check_id]
    assert RuleTableJudge().judge(check(), evidence(observations=obs), judge_params(), {}).verdicts['security'] == Verdict.FAIL
    for other in {'REQ-07', 'REQ-08', 'REQ-09'} - {check_id}:
        assert not project([row])[other]['input_cpu_confirmed_violations']['collected']


@pytest.mark.parametrize('outcome', ['rejected', 'execution_error', None])
def test_rejection_error_missing_do_not_produce_full_check_pass(outcome):
    fixture = next(r for r in fixtures() if r['expected'] == 'rejected')
    obs = project([{**describe(fixture), 'outcome': outcome}])['REQ-08']
    assert RuleTableJudge().judge(check(), evidence(observations=obs), judge_params(), {}).verdicts['security'] == Verdict.UNKNOWN


def test_unknown_shape_rule_is_not_a_confirmed_violation():
    fixture = next(r for r in fixtures() if r['kind'] == 'shape')
    assert project([{**describe(fixture), 'outcome': 'accepted'}])['REQ-09']['input_cpu_confirmed_violations']['value'] == []


@pytest.mark.parametrize('field,value', [('check_id', 'REQ-13'), ('stage', 'model_consumption'), ('fixture_canonical_sha256', '0'*64)])
def test_wrong_attribution_or_boundary_stops_projection(field, value):
    with pytest.raises(ValueError): project([{**describe(fixtures()[0]), field: value}])


def test_duplicate_unknown_rows_stop_projection():
    row = describe(fixtures()[0])
    with pytest.raises(ValueError): project([row, row])
    with pytest.raises(ValueError): project([{**row, 'case_id': 'unknown'}])


def test_observed_truncation_mismatch_is_independent_violation_but_missing_value_is_unknown():
    from guard.executors.input_cpu import digest
    case = next(r for r in fixtures() if r['kind'] == 'truncate')
    row = {**describe(case), 'outcome': 'accepted'}
    assert not project([row])['REQ-08']['input_cpu_confirmed_violations']['value']
    row['admitted_metadata'] = {'token_ids_canonical_sha256': digest([0])}
    assert project([row])['REQ-08']['input_cpu_confirmed_violations']['value'] == [case['id']]
    row['admitted_metadata']['token_ids_canonical_sha256'] = digest(case['expected_admitted_ids'])
    assert not project([row])['REQ-08']['input_cpu_confirmed_violations']['value']


@pytest.fixture
def req09_example(tmp_path,monkeypatch):
    """EXAMPLE 함수·텐서 대역이며 실제 target031 CPU는 실행하지 않는다."""
    import os
    import sys
    from types import ModuleType,SimpleNamespace
    from tools import b1_input_cpu as collector
    state=SimpleNamespace(accept_invalid=False,normalize_invalid=False,after_error=False,post='verified',calls=[],verify_calls=0)
    root=tmp_path/'source';(root/'vllm').mkdir(parents=True)
    source=root/'vllm'/'example.py';source.write_text('# EXAMPLE source identity only\n')
    class Rejected(Exception):pass
    class Tensor:
        def __init__(self,shape):self.shape=tuple(shape);self.dtype='torch.float32'
        def detach(self):return self
        def cpu(self):return self
        def contiguous(self):return self
        def numpy(self):return self
        def tobytes(self):
            import math
            return b'\0'*(math.prod(self.shape)*4)
    class Sampling:
        def __init__(self,**kwargs):self.__dict__.update(kwargs)
    class Base:
        def render_cmpl(self,prompts):return [{'type':'token','prompt_token_ids':[3,4]}]
    class Processor:
        def __init__(self):
            self.closed=False
            self.model_config=SimpleNamespace(max_model_len=16,get_vocab_size=lambda:32,get_hidden_size=lambda:4)
            self.renderer=Base();self.renderer.shutdown=lambda:setattr(self,'closed',True)
        def _validate_prompt_len(self,*args):pass
        def _validate_model_input(self,prompt):
            self._validate_prompt_len()
            mask=prompt.get('is_token_ids')
            if mask is not None and len(mask)!=3 and not state.accept_invalid:raise Rejected()
        def process_inputs(self,name,prompt,params,tasks):
            state.calls.append(name)
            self._validate_model_input(prompt)
            if name.startswith('b1-req09-after') and state.after_error:raise RuntimeError('PRIVATE_AFTER')
            if not 0<=params.routed_experts_prompt_start<=3 and not state.accept_invalid:raise Rejected()
            mask=prompt.get('is_token_ids')
            if state.normalize_invalid and mask is not None:mask=[False]*3
            return SimpleNamespace(prompt_token_ids=prompt.get('prompt_token_ids'),prompt_embeds=prompt.get('prompt_embeds'),
                prompt_is_token_ids=mask,sampling_params=params)
    def module(name,**attrs):
        obj=ModuleType(name);obj.__dict__.update(attrs);monkeypatch.setitem(sys.modules,name,obj)
    for name in ('vllm','vllm.renderers','vllm.v1','vllm.v1.engine'):module(name)
    module('torch',version=SimpleNamespace(cuda=None),zeros=Tensor)
    module('vllm.renderers.base',BaseRenderer=Base)
    module('vllm.v1.engine.input_processor',InputProcessor=Processor)
    module('vllm.sampling_params',SamplingParams=Sampling)
    module('vllm.inputs',embeds_input=lambda tensor,**kwargs:{'type':'embeds','prompt_embeds':tensor,**kwargs})
    module('vllm.exceptions',VLLMValidationError=Rejected)
    def verify(*args):state.verify_calls+=1
    monkeypatch.setattr(collector,'_online_verify_profile',verify)
    monkeypatch.setattr(collector,'_online_post',lambda *a,**kw:{'status':state.post})
    monkeypatch.setattr(collector.inspect,'getsourcefile',lambda fn:str(source))
    monkeypatch.setattr(collector.importlib.metadata,'version',lambda name:'EXAMPLE')
    processor=Processor();state.processor=processor;state.cache_seen={}
    def build(folder):
        state.cache_seen={key:os.environ.get(key) for key in ('VLLM_CACHE_ROOT','HF_HOME','HF_HUB_OFFLINE','CUDA_VISIBLE_DEVICES')}
        folder.mkdir();(folder/'config.json').write_text('{"vocab_size":32,"hidden_size":4}')
        return processor
    monkeypatch.setattr(collector,'build_context',build)
    state.collector=collector;state.root=root;state.work=tmp_path/'work'
    return state


def test_req09_target031_subset_keeps_original_twelve_fixtures_and_real_call_shape(req09_example):
    import os
    state=req09_example;before=dict(os.environ)
    result=state.collector.capture_req09_target031(state.root,state.work)
    assert result['profile']=='b1-req09-target031-cpu/1' and result['capture_status']=='completed'
    assert result['data_origin']=='DECLARED' and result['installed_target'] is None
    assert result['runner_consumption_verified'] is False
    cases=[case for case in fixtures() if case['check_id']=='REQ-09']
    assert len(result['results'])==len(cases)==12 and len(state.calls)==24
    assert state.verify_calls==13 and state.processor.closed
    assert dict(os.environ)==before and state.cache_seen['HF_HUB_OFFLINE']=='1'
    for row,case in zip(result['results'],cases):
        assert all(row[key]==value for key,value in describe(case).items())
        assert row['source_validation']['pre']==row['source_validation']['post']['status']=='verified'
        assert row['after']['outcome']=='accepted'
        assert row['after']['admitted_metadata']['prompt_count']==2
        assert row['input_metadata']['canonical_sha256']
        assert row['outcome']==('rejected' if case['expected']=='rejected' else 'accepted')
    assert result['observations_by_check']['REQ-09']['input_cpu_confirmed_violations']['value']==[]
    assert json.loads((state.work/'result.json').read_text())==result
    assert 'hello world' not in json.dumps(result) and 'prompt_token_ids' not in json.dumps(result)


@pytest.mark.parametrize('late',['after','final-source','auxiliary'])
def test_req09_target031_qualified_violation_survives_later_gaps(req09_example,monkeypatch,late):
    from pathlib import Path
    state=req09_example;state.accept_invalid=True
    if late=='after':state.after_error=True
    elif late=='final-source':
        calls=[]
        def post(*args,**kwargs):
            calls.append(1)
            return {'status':'unavailable' if len(calls)==13 else 'verified'}
        monkeypatch.setattr(state.collector,'_online_post',post)
    else:
        original=Path.open
        def opening(path,*args,**kwargs):
            if path.name=='result.json':raise OSError('PRIVATE_AUXILIARY')
            return original(path,*args,**kwargs)
        monkeypatch.setattr(Path,'open',opening)
    result=state.collector.capture_req09_target031(state.root,state.work)
    assert result['capture_status']=='incomplete'
    violations=result['observations_by_check']['REQ-09']['input_cpu_confirmed_violations']['value']
    assert 'mask-0' in violations and 'offset--1' in violations
    assert not any(case['id'] in violations for case in fixtures() if case['kind']=='shape')
    assert state.processor.closed
    assert 'PRIVATE_' not in json.dumps(result)


def test_req09_target031_normalized_mask_is_not_projected_as_original_invalid_admission(req09_example):
    state=req09_example;state.accept_invalid=True;state.normalize_invalid=True
    result=state.collector.capture_req09_target031(state.root,state.work)
    violations=result['observations_by_check']['REQ-09']['input_cpu_confirmed_violations']['value']
    assert not any(value.startswith('mask-') for value in violations)
    assert 'offset--1' in violations


def test_req09_target031_unavailable_pre_source_gate_never_builds_or_writes(req09_example,monkeypatch):
    state=req09_example
    def denied(*args):raise ValueError('EXAMPLE_SOURCE_MISMATCH')
    monkeypatch.setattr(state.collector,'_online_verify_profile',denied)
    with pytest.raises(ValueError):state.collector.capture_req09_target031(state.root,state.work)
    assert state.calls==[] and not state.work.exists()


@pytest.mark.parametrize('suite,profile,check_id',[('sampling','target031','REQ-09'),('embeddings','target031','REQ-09'),
    ('online','target031','REQ-09'),('input','reference5d','REQ-09'),('sampling','target031',None)])
def test_req09_cli_rejects_other_suites_and_source_combinations(monkeypatch,suite,profile,check_id):
    import sys
    from tools import b1_input_cpu as collector
    argv=['b1_input_cpu','--source-root','/not-read','--workdir','/not-written','--suite',suite,'--source-profile',profile]
    if check_id:argv+=['--check',check_id]
    monkeypatch.setattr(sys,'argv',argv)
    with pytest.raises(SystemExit) as error:collector.main()
    assert error.value.code==2


def test_req09_cli_exact_combination_calls_only_new_capture(monkeypatch):
    import sys
    from tools import b1_input_cpu as collector
    calls=[]
    monkeypatch.setattr(sys,'argv',['b1_input_cpu','--source-root','/source','--workdir','/work',
        '--suite','input','--check','REQ-09','--source-profile','target031'])
    monkeypatch.setattr(collector,'capture_req09_target031',lambda *args:calls.append(args) or {'capture_status':'completed'})
    assert collector.main()==0 and len(calls)==1
