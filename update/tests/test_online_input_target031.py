"""EXAMPLE orchestration regressions; actual target CPU runs are separate."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType,SimpleNamespace

import pytest
from guard.executors import online_input_cpu as product
from guard.executors.input_cpu import digest
from tools import b1_input_cpu as collector


class ExampleRequest:
    def __init__(self,**kwargs):
        self.model_fields_set=set(kwargs)
        self.__dict__.update(kwargs)
        if not hasattr(self,'kv_transfer_params'):self.kv_transfer_params=None
    def to_sampling_params(self,**kwargs):return SimpleNamespace(extra_args={})


class ExampleBase:
    def render_cmpl(self):pass
    def render_chat(self):pass
    async def render_chat_async(self):pass


def example_reuse(request):
    return (request.kv_transfer_params or {}).pop('prompt_token_ids',None)


def example_mm_gate():pass


class ExampleOnline:
    creations=0
    fail_at=None
    def __init__(self,config,renderer,**kwargs):
        type(self).creations+=1
        if type(self).creations==self.fail_at:raise OSError('PRIVATE_CONSTRUCTOR_ERROR')
        self.trust_request_chat_template=kwargs['trust_request_chat_template']
        self.trust_request_mm_kwargs=kwargs['trust_request_mm_kwargs']
    def validate_chat_template(self):pass
    async def preprocess_chat(self,request):
        example_mm_gate()
        ids=example_reuse(request)
        return {'prompt_token_ids':ids or [3,4]}
    async def render_chat(self,request):
        self.validate_chat_template()
        return [],[await self.preprocess_chat(request)]


class ExampleProcessor:
    def __init__(self):
        self.model_config=SimpleNamespace(max_model_len=16,get_vocab_size=lambda:32,dtype='float32',enforce_eager=True)
        self.shutdown=False
        self.renderer=SimpleNamespace(tokenizer=SimpleNamespace(max_token_id=31),shutdown=lambda:setattr(self,'shutdown',True))
    def _validate_prompt_len(self):pass
    def _validate_model_input(self):self._validate_prompt_len()
    def process_inputs(self,name,inputs,params,tasks):
        self._validate_model_input()
        return SimpleNamespace(prompt_token_ids=inputs['prompt_token_ids'])


@pytest.fixture
def example_runtime(monkeypatch):
    def module(name,**attrs):
        obj=ModuleType(name);obj.__dict__.update(attrs);monkeypatch.setitem(sys.modules,name,obj)
    for name in ('vllm','vllm.renderers','vllm.v1','vllm.v1.engine','vllm.entrypoints',
                 'vllm.entrypoints.openai','vllm.entrypoints.openai.chat_completion',
                 'vllm.entrypoints.serve','vllm.entrypoints.serve.engine','vllm.entrypoints.generate','vllm.entrypoints.generate.base'):
        module(name)
    module('torch',version=SimpleNamespace(cuda=None))
    module('vllm.renderers.base',BaseRenderer=ExampleBase)
    module('vllm.v1.engine.input_processor',InputProcessor=ExampleProcessor)
    module('vllm.renderers.online_renderer',OnlineRenderer=ExampleOnline,_reused_prompt_token_ids=example_reuse)
    module('vllm.entrypoints.openai.chat_completion.protocol',ChatCompletionRequest=ExampleRequest)
    module('vllm.entrypoints.serve.engine.protocol',ErrorResponse=type('ExampleErrorResponse',(),{}))
    module('vllm.entrypoints.generate.base.protocol',validate_request_mm_kwargs=example_mm_gate)
    module('vllm.exceptions',VLLMValidationError=type('ExampleRejection',(Exception,),{}))
    monkeypatch.setattr(collector,'_online_verify_profile',lambda *a:None)
    monkeypatch.setattr(collector,'_online_post',lambda *a,**kw:{'status':'verified'})
    monkeypatch.setattr(collector.importlib.metadata,'version',lambda name:'EXAMPLE')
    # The functions belong to this test, not target source. Only the source-path
    # formatting is replaced; the EXAMPLE target calls and profile events execute.
    monkeypatch.setattr(collector.inspect,'getsourcefile',lambda fn:collector.__file__)
    processor=ExampleProcessor();seen_env=[]
    def build(folder):
        seen_env.append({k:os.environ.get(k) for k in ('VLLM_CACHE_ROOT','HF_HOME','HF_MODULES_CACHE','VLLM_PLUGINS')})
        folder.mkdir();(folder/'config.json').write_text('{"fixture":"EXAMPLE"}')
        return processor
    monkeypatch.setattr(collector,'build_context',build)
    ExampleOnline.creations=0;ExampleOnline.fail_at=None
    return processor,seen_env


def capture_example(tmp_path):
    return collector.capture_online(Path(collector.__file__).resolve().parents[1],tmp_path/'capture',source_profile='target031')


def test_target_corpus_identity_preserved_and_one_collection_covers_all_checks(tmp_path,example_runtime):
    processor,env=example_runtime
    before={k:os.environ.get(k) for k in env[0]} if env else dict(os.environ)
    record=capture_example(tmp_path)
    assert record['capture_status']=='completed' and len(record['results'])==16
    assert len({r['collection_id'] for r in record['results']})==1
    assert record['collection_id'].startswith('online-cpu-')
    assert datetime.fromisoformat(record['capture_started_at'])<=datetime.fromisoformat(record['capture_finished_at'])
    assert datetime.fromisoformat(record['capture_started_at']).utcoffset() is not None
    for row,case in zip(record['results'],product.fixtures()):
        assert all(row[k]==v for k,v in product.describe(case).items())
        assert row['source_validation']['pre']=='verified' and row['source_validation']['post']['status']=='verified'
        assert row['after']['outcome']=='accepted' and 'process_inputs' in row['after']['events']
        assert row['after']['safe_metadata']['admission_completed'] is True
    assert record['source_features']['validate_token_ids']=={'present':False}
    assert record['effective_config']=={'max_model_len':16,'vocab_size':32,'dtype':'float32','enforce_eager':True,'max_token_id':31}
    assert processor.shutdown and env[0]['VLLM_PLUGINS']==''
    assert env[0]['VLLM_CACHE_ROOT']==str(tmp_path/'capture/vllm-cache')
    assert env[0]['HF_HOME']==str(tmp_path/'capture/hf-home')
    assert dict(os.environ)==before
    assert 'PRIVATE_' not in json.dumps(record)


@pytest.mark.parametrize('case_id',['reuse-empty','reuse-bool','reuse-echo-ignores-ids'])
def test_target_source_unsupported_stronger_expectations_are_observation_only(tmp_path,example_runtime,case_id):
    record=capture_example(tmp_path)
    row=next(r for r in record['results'] if r['case_id']==case_id)
    assert row['source_scope_eligibility']=={'eligible':False,'reason':'source_expectation_unavailable'}
    assert row['safe_metadata']['request_parsed'] is True and row['safe_metadata']['admission_completed'] is True
    assert case_id not in record['observations_by_check']['REQ-08']['online_input_confirmed_violations']['value']


def test_actual_field_and_reuse_input_hash_are_separate_from_request_source(tmp_path,example_runtime):
    record=capture_example(tmp_path);row=next(r for r in record['results'] if r['case_id']=='reuse-negative')
    assert row['source_scope_eligibility']=={'eligible':True,'reason':'source_contract_observed'}
    meta=row['safe_metadata'];field=meta['parsed_fields']['kv_transfer_params']
    assert field=={'present':True,'fixture_matches':True,'canonical_sha256':digest({'prompt_token_ids':[-1]})}
    assert meta['processor_input_ids_sha256']==meta['admitted_token_ids_canonical_sha256']==digest([-1])
    assert meta['processor_input_matches_reuse'] is True
    assert 'reuse-negative' in record['observations_by_check']['REQ-08']['online_input_confirmed_violations']['value']


def test_constructor_failure_after_known_range_violation_preserves_rows_and_window(tmp_path,example_runtime):
    ExampleOnline.fail_at=6
    record=capture_example(tmp_path)
    assert record['capture_status']=='incomplete' and len(record['results'])==16
    failed=record['results'][5]
    assert failed['outcome']=='execution_error' and failed['error_type']=='OSError'
    assert failed['target_started'] is False and failed['after']['outcome']=='not_collected'
    assert 'reuse-negative' in record['observations_by_check']['REQ-08']['online_input_confirmed_violations']['value']
    assert record['results'][-1]['after']['outcome']=='accepted'
    assert all(r['collection_id']==record['collection_id'] for r in record['results'])
    assert datetime.fromisoformat(record['capture_started_at'])<=datetime.fromisoformat(record['capture_finished_at'])
    assert 'PRIVATE_' not in json.dumps(record)


def test_auxiliary_write_failure_keeps_independent_bad_admission(tmp_path,example_runtime,monkeypatch):
    original=Path.open
    def failed(path,*a,**kw):
        if path==tmp_path/'capture/result.json':raise OSError('PRIVATE_AUXILIARY_ERROR')
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'open',failed)
    record=capture_example(tmp_path)
    assert record['capture_status']=='incomplete' and len(record['results'])==16
    assert record['auxiliary_write']=={'status':'failed','error_type':'OSError'}
    assert 'reuse-negative' in record['observations_by_check']['REQ-08']['online_input_confirmed_violations']['value']
    assert 'PRIVATE_' not in json.dumps(record)


def test_parent_postcheck_failure_keeps_qualified_primary_facts(tmp_path,example_runtime,monkeypatch):
    def post(*a,**kw):
        return {'status':'unavailable','error_type':'OSError'} if len(a)==5 and a[3] is not None else {'status':'verified'}
    monkeypatch.setattr(collector,'_online_post',post)
    record=capture_example(tmp_path)
    assert record['capture_status']=='incomplete'
    assert 'reuse-negative' in record['observations_by_check']['REQ-08']['online_input_confirmed_violations']['value']


def test_failed_followup_observer_does_not_overwrite_primary_admission(example_runtime,monkeypatch):
    from guard.executors import structured_limits
    original=structured_limits.observe_call;calls=[]
    def observe(*a,**kw):
        calls.append(True)
        if len(calls)==2:raise OSError('PRIVATE_FOLLOWUP_ERROR')
        return original(*a,**kw)
    monkeypatch.setattr(structured_limits,'observe_call',observe)
    processor,_=example_runtime
    tracked,_=collector._online_inventory(ExampleBase,ExampleProcessor,ExampleOnline,example_reuse,example_mm_gate)
    case=next(c for c in product.fixtures() if c['id']=='reuse-negative')
    row=collector._online_collect_case(case,processor,ExampleOnline,ExampleRequest,type('ExampleError',(),{}),ValueError,tracked)
    assert row['outcome']=='accepted' and row['safe_metadata']['admission_completed'] is True
    assert row['after']['outcome']=='not_collected' and row['error_type']=='OSError'
    assert collector._online_eligibility(case,row,'target031')['eligible'] is True


def test_typed_error_code_or_empty_trace_alone_does_not_prove_consumption():
    case=next(c for c in product.fixtures() if c['id']=='template-untrusted')
    for row in ({'outcome':'rejected','events':[],'safe_metadata':{'native_error_code':400}},
                {'outcome':'accepted','events':[],'safe_metadata':{}}):
        assert collector._online_eligibility(case,row,'target031')=={'eligible':False,'reason':'consumption_unavailable'}


def test_reference_keeps_existing_empty_bool_expectation_contract():
    case=next(c for c in product.fixtures() if c['id']=='reuse-empty')
    row={'outcome':'accepted','events':['online_render_chat','reuse_input_gate'],
        'safe_metadata':{'request_parsed':True,'admission_completed':True,
            'parsed_fields':{'kv_transfer_params':{'present':True,'fixture_matches':True}}}}
    assert collector._online_eligibility(case,row,'reference5d')['eligible']
    assert not collector._online_eligibility(case,row,'target031')['eligible']


def test_online_default_dispatch_uses_reference_without_touching_other_suites(tmp_path,monkeypatch):
    seen=[]
    monkeypatch.setattr(collector,'capture_online',lambda *a,**kw:seen.append(kw) or {'capture_status':'completed'})
    monkeypatch.setattr(sys,'argv',['input','--suite','online','--source-root',str(tmp_path),'--workdir',str(tmp_path/'out')])
    assert collector.main()==0 and seen==[{'source_profile':'reference5d'}]
    monkeypatch.setattr(sys,'argv',['input','--suite','input','--source-profile','target031','--source-root',str(tmp_path),'--workdir',str(tmp_path/'out')])
    with pytest.raises(SystemExit) as error:collector.main()
    assert error.value.code==2 and not (tmp_path/'out').exists()


@pytest.mark.parametrize('opt',['-O','-OO'])
def test_optimized_parent_rejected_before_source_or_output(tmp_path,opt):
    update=Path(collector.__file__).resolve().parents[1]
    process=subprocess.run([sys.executable,opt,collector.__file__,'--suite','online',
        '--source-root',str(tmp_path/'missing'),'--workdir',str(tmp_path/'output')],cwd=update,
        env={**os.environ,'PYTHONPATH':os.pathsep.join([str(update/'src'),str(update)])},capture_output=True,text=True)
    assert process.returncode!=0 and 'ONLINE_CPU_REQUIRES_NORMAL_PYTHON' in process.stderr
    assert not (tmp_path/'output').exists()


def test_profiles_choose_fixed_source_and_target_dependencies(tmp_path,monkeypatch):
    from guard.executors import structured_tools_cpu
    seen=[]
    monkeypatch.setattr(collector,'_online_verify_checkout',lambda:None)
    monkeypatch.setattr(structured_tools_cpu,'_verify_source',lambda root,**kw:seen.append(kw['expected_commit']))
    monkeypatch.setattr(structured_tools_cpu,'_validate_target031_dependencies',lambda:seen.append('target-deps'))
    collector._online_verify_profile(tmp_path,'reference5d')
    collector._online_verify_profile(tmp_path,'target031')
    assert seen==[collector.SOURCE_COMMIT,collector.ONLINE_SOURCE_PROFILES['target031'],'target-deps']
    with pytest.raises(ValueError):collector._online_verify_profile(tmp_path,'HEAD')


def test_non_online_suite_and_context_asts_remain_reviewed_4a30a64():
    import ast
    tree=ast.parse(Path(collector.__file__).read_text());actual={}
    for node in ast.walk(tree):
        if isinstance(node,ast.FunctionDef) and node.name=='build_context':
            actual['build_context']=hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()
        if (isinstance(node,ast.If) and isinstance(node.test,ast.Compare)
                and ast.unparse(node.test.left)=='args.suite'
                and isinstance(node.test.comparators[0],ast.Constant)
                and node.test.comparators[0].value in ('input','sampling','embeddings')):
            name=node.test.comparators[0].value
            actual.setdefault(name,[]).append(hashlib.sha256(ast.dump(ast.Module(body=node.body,type_ignores=[]),include_attributes=False).encode()).hexdigest())
    # Reviewed base AST hashes under Python 3.12; no runtime Git history/HEAD approval.
    assert actual=={
        'build_context':'bf749d2c42e4b6b3e63dd3c040cd30c881abb763e6234f06cda524a6c12510e2',
        'sampling':['1fb7219de3a8c8f009336d8191b42dd7553064bcbbbf40d19726ac7b7a277bfb',
                    'e5507eca181970f39036fa570b4d45fb0ca42264c93b56708961b620f03243a3'],
        'input':['91b39c8d55e28f2ff6609024fb274ea5f67d9b88a04816b250539dbd4b147f50'],
        'embeddings':['a01fe42f57d3bcdd1f92fd370c8d08a85b992a80891c90abd12347dd957d10f9']}


@pytest.mark.parametrize('case_id,eligible',[
    ('reuse-negative',True),('kwargs-trusted-overbound',True),
    ('template-untrusted',True),('code-revision-default-deny',False)])
def test_strong_admission_proof_survives_missing_gate_trace_with_mm_scope_limit(case_id,eligible):
    case=next(c for c in product.fixtures() if c['id']==case_id)
    ids=case['fields'].get('kv_transfer_params',{}).get('prompt_token_ids',[3,4])
    row={'outcome':'accepted','events':[], 'safe_metadata':{
        'request_parsed':True,'admission_completed':True,
        'parsed_fields':{name:{'present':True,'fixture_matches':True,'canonical_sha256':digest(value)}
                         for name,value in case['fields'].items()},
        'processor_input_matches_reuse':True,'processor_input_ids_sha256':digest(ids),
        'admitted_token_ids_canonical_sha256':digest(ids),'prompt_count':16,
        'effective_max_model_len':16}}
    assert collector._online_eligibility(case,row,'target031')['eligible'] is eligible


def test_target_range_rejection_requires_actual_processor_input_consumption():
    case=next(c for c in product.fixtures() if c['id']=='reuse-negative')
    row={'outcome':'rejected','events':['online_render_chat','reuse_input_gate'],
         'safe_metadata':{'request_parsed':True,'parsed_fields':{
             'kv_transfer_params':{'present':True,'fixture_matches':True}}}}
    assert collector._online_eligibility(case,row,'target031')['eligible'] is False
    row['events'].append('process_inputs');row['safe_metadata']['processor_input_matches_reuse']=True
    assert collector._online_eligibility(case,row,'target031')['eligible'] is True
