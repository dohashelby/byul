"""Execute the pinned real renderer/InputProcessor with a tiny local config.

No weights, model download, engine process, HTTP server or device execution.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import inspect
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
from guard.executors.input_cpu import PROFILE, run_reference, project
from guard.executors.structured_tools import SOURCE_COMMIT


def build_context(folder):
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import GPT2Config, TokenizersBackend
    from vllm.config import ModelConfig, VllmConfig, DeviceConfig
    from vllm.v1.engine.input_processor import InputProcessor
    folder.mkdir()
    GPT2Config(vocab_size=32, n_embd=4, n_layer=1, n_head=1, n_positions=16,
               bos_token_id=1, eos_token_id=2, architectures=['GPT2LMHeadModel']).save_pretrained(folder)
    vocab = {'[UNK]': 0, '[BOS]': 1, '[EOS]': 2, 'hello': 3, 'world': 4, 'user': 5, 'assistant': 6}
    vocab.update({f'word{i}': i for i in range(7, 32)})
    token = Tokenizer(WordLevel(vocab, unk_token='[UNK]'))
    token.pre_tokenizer = Whitespace()
    tokenizer = TokenizersBackend(tokenizer_object=token, unk_token='[UNK]', bos_token='[BOS]', eos_token='[EOS]')
    tokenizer.chat_template = "{% for m in messages %}{{ m['role'] }} {{ m['content'] }} {% endfor %}"
    tokenizer.save_pretrained(folder)
    model_config = ModelConfig(model=str(folder), tokenizer=str(folder), dtype='float32',
                               max_model_len=16, enforce_eager=True, enable_prompt_embeds=True)
    config = VllmConfig(model_config=model_config, device_config=DeviceConfig(device='cpu'))
    return InputProcessor(config)



ONLINE_SOURCE_PROFILES={'reference5d':SOURCE_COMMIT,'target031':'db9527a46873454610df6dbedf79a36d6bf1a7f6'}
ONLINE_GATE_SHA256='6a43fc77530e1b6be3b08e7776ffac9ca1be790a9d1d69366280ee0ae4191bc3'
ONLINE_EXCLUDED_TARGET031={'reuse-empty','reuse-bool','reuse-echo-ignores-ids'}


def _online_verify_checkout():
    from tools import lif14_profiler_cpu as gate
    expected=Path(__file__).resolve().parent/'lif14_profiler_cpu.py'
    try:
        if (expected.is_symlink() or not Path(gate.__file__).resolve().samefile(expected)
            or not Path(inspect.getsourcefile(gate.verify_component)).resolve().samefile(expected)
            or hashlib.sha256(expected.read_bytes()).hexdigest()!=ONLINE_GATE_SHA256):raise ValueError
    except (OSError,TypeError,AttributeError,ValueError):
        raise ValueError('ONLINE_CPU_COMPONENT_GATE_IDENTITY_MISMATCH') from None
    gate.verify_component(Path(__file__).resolve().parents[2])


def _online_verify_profile(root,profile):
    if profile not in ONLINE_SOURCE_PROFILES or sys.flags.optimize or sys.version_info[:2]!=(3,12):
        raise ValueError('ONLINE_CPU_PROFILE_OR_PYTHON_INVALID')
    _online_verify_checkout()
    from guard.executors.structured_tools_cpu import _verify_source,_validate_target031_dependencies
    _verify_source(root,expected_commit=ONLINE_SOURCE_PROFILES[profile])
    if profile=='target031':_validate_target031_dependencies()
    for name,module in tuple(sys.modules.items()):
        if name=='vllm' or name.startswith('vllm.'):
            file=getattr(module,'__file__',None)
            if file is None or not Path(file).resolve().is_relative_to(root/'vllm'):
                raise ValueError('ONLINE_CPU_IMPORT_MISMATCH')


def _online_post(root,profile,producer_sha256,config_folder=None,config_hashes=None):
    try:
        _online_verify_profile(root,profile)
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=producer_sha256:
            raise ValueError('ONLINE_CPU_COLLECTOR_BYTES_CHANGED')
        if config_folder is not None and _online_config_hashes(config_folder)!=config_hashes:
            raise ValueError('ONLINE_CPU_CONFIG_BYTES_CHANGED')
        return {'status':'verified'}
    except ValueError as error:return {'status':'mismatch','error_type':type(error).__name__}
    except Exception as error:return {'status':'unavailable','error_type':type(error).__name__}


def _online_config_hashes(folder):
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*') if p.is_file()}


def _online_inventory(base,processor,online,reuse,mm_gate):
    selected=[('render_cmpl',base.render_cmpl),('render_chat',base.render_chat),
        ('render_chat_async',base.render_chat_async),('process_inputs',processor.process_inputs),
        ('validate_prompt_len',processor._validate_prompt_len),('validate_model_input',processor._validate_model_input),
        ('online_render_chat',online.render_chat),('online_preprocess_chat',online.preprocess_chat),
        ('reuse_input_gate',reuse),('template_gate',online.validate_chat_template),('mm_kwargs_gate',mm_gate)]
    inventory={name:{'present':True} for name,_ in selected}
    present=hasattr(base,'validate_token_ids');inventory['validate_token_ids']={'present':present}
    if present:selected.append(('validate_token_ids',base.validate_token_ids))
    return selected,inventory


def _online_eligibility(case,row,profile):
    if profile=='target031' and case['id'] in ONLINE_EXCLUDED_TARGET031:
        return {'eligible':False,'reason':'source_expectation_unavailable'}
    metadata=row.get('safe_metadata',{})
    fields=metadata.get('parsed_fields',{})
    parsed=metadata.get('request_parsed') is True and all(
        fields.get(name,{}).get('present') is True and fields[name].get('fixture_matches') is True
        for name in case['fields'])
    events=set(row.get('events',[]))
    gate={'REQ-08':'reuse_input_gate','REQ-07':'template_gate','SUP-08':'mm_kwargs_gate'}[case['check_id']]
    if not parsed:return {'eligible':False,'reason':'consumption_unavailable'}
    # Direct InputProcessor returns are independent facts; trace alone and
    # text-only admission do not establish application of MM overrides.
    from guard.executors.input_cpu import digest
    consumed=metadata.get('admission_completed') is True
    guarded=gate in events and 'online_render_chat' in events
    range_case=case['id'] in ('reuse-negative','reuse-oov','reuse-long')
    if row.get('outcome')=='rejected':
        observed=(metadata.get('processor_input_matches_reuse') is True and 'process_inputs' in events
                  if profile=='target031' and range_case else guarded)
    elif row.get('outcome')=='accepted':
        if case['check_id']=='REQ-08' and case['id'] not in ('reuse-empty','reuse-bool','reuse-echo-ignores-ids'):
            ids=case['fields'].get('kv_transfer_params',{}).get('prompt_token_ids')
            observed=consumed and metadata.get('processor_input_matches_reuse') is True and all(
                metadata.get(key)==digest(ids) for key in ('processor_input_ids_sha256','admitted_token_ids_canonical_sha256'))
        elif case['check_id']=='REQ-07':
            count=metadata.get('prompt_count')
            applied_override=case['id'] in ('template-untrusted','template-nested-untrusted') and all(
                metadata.get(key)==digest([3,4]) for key in ('processor_input_ids_sha256','admitted_token_ids_canonical_sha256'))
            exceeded=case['id']=='kwargs-trusted-overbound' and metadata.get('effective_max_model_len')==16 and type(count) is int and count>=16
            observed=consumed and (guarded or applied_override or exceeded)
        else:observed=consumed and guarded
    else:observed=False
    if observed:return {'eligible':True,'reason':'source_contract_observed'}
    return {'eligible':False,'reason':'consumption_unavailable'}




def _online_token_id_domain(config_folder,model_vocab_size,request_ids):
    """Read independent owned tokenizer IDs; never use the cached max as oracle."""
    from guard.executors.input_cpu import digest
    try:
        tokenizer_bytes=(config_folder/'tokenizer.json').read_bytes()
        model_bytes=(config_folder/'config.json').read_bytes()
        tokenizer=json.loads(tokenizer_bytes);model=json.loads(model_bytes)
        if (type(model_vocab_size) is not int or model_vocab_size<=0
                or type(model.get('vocab_size')) is not int or model['vocab_size']!=model_vocab_size):
            raise ValueError('ONLINE_CPU_MODEL_DOMAIN_UNAVAILABLE')
        vocab=tokenizer['model']['vocab']
        if not isinstance(vocab,dict):raise ValueError('ONLINE_CPU_TOKENIZER_DOMAIN_UNAVAILABLE')
        values=list(vocab.values())+[item['id'] for item in tokenizer.get('added_tokens',[])]
        if not values or any(type(value) is not int or value<0 for value in values):
            raise ValueError('ONLINE_CPU_TOKENIZER_DOMAIN_UNAVAILABLE')
        ids=sorted(set(values))
        if not isinstance(request_ids,list):raise ValueError('ONLINE_CPU_REQUEST_DOMAIN_UNAVAILABLE')
        return {'status':'collected','model_vocab_size':model_vocab_size,'tokenizer_id_count':len(ids),
            'min':ids[0],'max':ids[-1],'canonical_sha256':digest(ids),
            'contiguous_from_zero':ids[0]==0 and ids[-1]+1==len(ids),
            'request_ids_in_domain':all(type(value) is int and value in set(ids) for value in request_ids),
            'request_ids_within_model_vocab':all(type(value) is int and 0<=value<model_vocab_size for value in request_ids),
            'request_ids_canonical_sha256':digest(request_ids),
            'tokenizer_file_sha256':hashlib.sha256(tokenizer_bytes).hexdigest(),
            'model_config_file_sha256':hashlib.sha256(model_bytes).hexdigest()}
    except Exception as error:
        return {'status':'unavailable','error_type':type(error).__name__}


def _online_collect_case(case,processor,online_class,request_class,error_class,rejection_type,tracked,*,config_folder=None):
    from guard.executors import online_input_cpu as product
    from guard.executors.input_cpu import digest
    from guard.executors.structured_limits import observe_call
    row={**product.describe(case),'target_started':False}
    try:
        online=online_class(processor.model_config,processor.renderer,request_logger=None,chat_template=None,
            chat_template_content_format='auto',trust_request_chat_template=case['trust_template'],
            trust_request_mm_kwargs=case['trust_mm'])
        def invoke(fields,details,*,primary=False):
            try:
                obj=request_class(model='fixture-model',messages=[{'role':'user','content':'hello world'}],
                    max_tokens=1,**deepcopy(fields))
                details.update(effective_max_model_len=processor.model_config.max_model_len,
                    effective_vocab_size=processor.model_config.get_vocab_size(),
                    effective_max_token_id=processor.renderer.tokenizer.max_token_id,
                    request_parsed=True,parsed_fields={name:{
                    'present':name in obj.model_fields_set,
                    'fixture_matches':digest(getattr(obj,name,None))==digest(value),
                    'canonical_sha256':digest(getattr(obj,name,None))} for name,value in fields.items()},
                    trust_request_chat_template=online.trust_request_chat_template,
                    trust_request_mm_kwargs=online.trust_request_mm_kwargs)
                if primary:row['target_started']=True
                result=__import__('asyncio').run(online.render_chat(obj))
                if isinstance(result,error_class):
                    code=result.error.code
                    if type(code) is int and code in (400,422):
                        details.update(rejection_basis='native_typed_error_response',native_error_code=code)
                        raise product.NativeRequestRejected()
                    raise RuntimeError('ONLINE_CPU_NATIVE_ERROR_CATEGORY_UNSUPPORTED')
                _,inputs=result
                params=obj.to_sampling_params(max_tokens=1,default_sampling_params={})
                ids=inputs[0].get('prompt_token_ids')
                details.update(processor_input_count=len(ids) if ids is not None else None,
                    processor_input_ids_sha256=digest(ids) if ids is not None else None,
                    processor_input_matches_reuse=digest(ids)==digest(fields.get('kv_transfer_params',{}).get('prompt_token_ids')))
                details['token_id_domain']=_online_token_id_domain(config_folder,
                    processor.model_config.get_vocab_size(),ids)
                admitted=processor.process_inputs('b1-online-fixture',inputs[0],params,('generate',))
                details.update(admission_completed=True,prompt_count=len(admitted.prompt_token_ids),
                    admitted_token_ids_canonical_sha256=digest(admitted.prompt_token_ids),
                    reuse_field_removed='prompt_token_ids' not in (obj.kv_transfer_params or {}),
                    sampling_reuse_field_present='prompt_token_ids' in ((params.extra_args or {}).get('kv_transfer_params') or {}))
            except Exception as error:
                details['error_type']=type(error).__name__
                raise
        details={}
        row.update(observe_call(lambda:invoke(case['fields'],details,primary=True),(rejection_type,product.NativeRequestRejected),tracked))
        row['safe_metadata']=details
        after={}
        row['after']=observe_call(lambda:invoke({},after),(rejection_type,product.NativeRequestRejected),tracked)
        row['after']['safe_metadata']=after
    except Exception as error:
        row['error_type']=type(error).__name__
        row.setdefault('outcome','execution_error');row.setdefault('events',[])
        row.setdefault('after',{'outcome':'not_collected','events':[]})
        row.setdefault('safe_metadata',{})
    return row


def capture_online(source_root:Path,workdir:Path,*,source_profile='reference5d')->dict:
    from guard.executors import online_input_cpu as product
    if sys.flags.optimize:raise ValueError('ONLINE_CPU_REQUIRES_NORMAL_PYTHON')
    root=source_root.resolve()
    _online_verify_profile(root,source_profile)
    workdir.mkdir(parents=True,exist_ok=False)
    producer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    collection_id='online-cpu-'+uuid4().hex
    started=datetime.now(timezone.utc).isoformat();began=time.perf_counter()
    record={'profile':'b1-online-input-cpu-v1','suite':'online','scope':'reference_cpu_admission_only',
        'source_profile':source_profile,'source_commit':ONLINE_SOURCE_PROFILES[source_profile],
        'installed_target':None,'runner_consumption_verified':False,'runner_hook_verified':False,
        'own_producer_sha256':producer_sha256,'collection_id':collection_id,'capture_started_at':started,
        'fixture_config':{'origin':'DECLARED','vocab_size':32,'max_model_len':16,'hidden_size':4},
        'source_validation':{'pre':'verified','boundary':'source_fixture_component'},'results':[]}
    settings={'VLLM_CACHE_ROOT':str(workdir/'vllm-cache'),'HF_HOME':str(workdir/'hf-home'),
        'HF_HUB_CACHE':str(workdir/'hf-home/hub'),'HF_MODULES_CACHE':str(workdir/'hf-modules'),
        'VLLM_PLUGINS':'','VLLM_TARGET_DEVICE':'cpu','CUDA_VISIBLE_DEVICES':'',
        'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
    previous={k:os.environ.get(k) for k in settings};os.environ.update(settings)
    processor=None;config_folder=workdir/'local-config';config_hashes={}
    try:
        import torch
        if torch.version.cuda is not None:raise ValueError('ONLINE_CPU_REQUIRES_CPU_TORCH')
        from vllm.renderers.base import BaseRenderer
        from vllm.v1.engine.input_processor import InputProcessor
        from vllm.renderers.online_renderer import OnlineRenderer,_reused_prompt_token_ids
        from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
        from vllm.entrypoints.serve.engine.protocol import ErrorResponse
        from vllm.entrypoints.generate.base.protocol import validate_request_mm_kwargs
        from vllm.exceptions import VLLMValidationError
        processor=build_context(config_folder)
        config_hashes=_online_config_hashes(config_folder)
        record['effective_config']={'max_model_len':processor.model_config.max_model_len,
            'vocab_size':processor.model_config.get_vocab_size(),'dtype':str(processor.model_config.dtype),
            'enforce_eager':processor.model_config.enforce_eager,
            'max_token_id':processor.renderer.tokenizer.max_token_id}
        tracked,inventory=_online_inventory(BaseRenderer,InputProcessor,OnlineRenderer,_reused_prompt_token_ids,validate_request_mm_kwargs)
        record.update(source_features=inventory,config_file_bytes_sha256=config_hashes,
            observed_source_file_bytes_sha256={str(Path(inspect.getsourcefile(fn)).resolve().relative_to(root)):
                hashlib.sha256(Path(inspect.getsourcefile(fn)).read_bytes()).hexdigest() for _,fn in tracked},
            dependencies={k:importlib.metadata.version(k) for k in ('torch','transformers','tokenizers','pydantic')})
        for case in product.fixtures():
            row={**product.describe(case),'outcome':'execution_error','target_started':False,'events':[],
                'after':{'outcome':'not_collected','events':[]},'safe_metadata':{}}
            try:
                _online_verify_profile(root,source_profile)
                row=_online_collect_case(case,processor,OnlineRenderer,ChatCompletionRequest,ErrorResponse,VLLMValidationError,tracked,config_folder=config_folder)
                pre='verified'
            except Exception as error:
                row['error_type']=type(error).__name__
                pre='mismatch' if isinstance(error,ValueError) else 'unavailable'
            row.update(source_profile=source_profile,source_commit=ONLINE_SOURCE_PROFILES[source_profile],collection_id=collection_id,
                source_validation={'pre':pre,'post':_online_post(root,source_profile,producer_sha256,config_folder,config_hashes),
                                   'boundary':'source_fixture_component'})
            row['source_scope_eligibility']=_online_eligibility(case,row,source_profile)
            record['results'].append(row)
    except Exception as error:
        record['error_type']=type(error).__name__
    finally:
        if processor is not None:
            try:processor.renderer.shutdown()
            except Exception as error:record['shutdown_error_type']=type(error).__name__
        record['capture_finished_at']=datetime.now(timezone.utc).isoformat()
        record['source_validation']['post']=_online_post(root,source_profile,producer_sha256,
            config_folder if config_hashes else None,config_hashes)
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
    record['config_file_bytes_sha256']=config_hashes
    record['observations_by_check']=product.project([r for r in record['results']
        if r['source_scope_eligibility']['eligible'] and r['source_validation']['pre']=='verified'
        and r['source_validation']['post']['status'] in ('verified','unavailable')])
    complete=len(record['results'])==len(product.fixtures()) and all(
        r.get('outcome') in ('accepted','rejected') and r.get('after',{}).get('outcome')=='accepted'
        and r['source_validation']['pre']=='verified' and r['source_validation']['post']['status']=='verified'
        and bool(r.get('events')) and r.get('after',{}).get('safe_metadata',{}).get('admission_completed') is True
        and 'process_inputs' in r.get('after',{}).get('events',[]) for r in record['results'])
    record.update(capture_status='completed' if complete and record['source_validation']['post']['status']=='verified'
        and 'error_type' not in record and 'shutdown_error_type' not in record else 'incomplete',
        parent_wall_seconds=time.perf_counter()-began,auxiliary_write={'status':'completed'})
    try:(workdir/'result.json').write_text(json.dumps(record,indent=2))
    except Exception as error:
        record.update(capture_status='incomplete',auxiliary_write={'status':'failed','error_type':type(error).__name__})
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--suite', choices=('input', 'sampling', 'embeddings', 'online'), default='input')
    parser.add_argument('--source-profile',choices=ONLINE_SOURCE_PROFILES,default='reference5d')
    args = parser.parse_args()
    if args.suite=='online':
        result=capture_online(args.source_root,args.workdir,source_profile=args.source_profile)
        print(result['capture_status'])
        return 0 if result['capture_status']=='completed' else 2
    if args.source_profile!='reference5d':parser.error('source-profile applies only to online suite')
    root = args.source_root.resolve()
    if subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() != SOURCE_COMMIT:
        raise SystemExit('Reference SHA mismatch')
    if subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--', 'vllm'], text=True):
        raise SystemExit('Reference is modified')
    args.workdir.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    if args.suite == 'sampling':
        for key in ('VLLM_MAX_STOP_STRINGS', 'VLLM_MAX_N_SEQUENCES', 'VLLM_MAX_NUM_BAD_WORDS'):
            os.environ[key] = '4'
    record = {'profile': PROFILE, 'source_commit': SOURCE_COMMIT, 'scope': 'reference_cpu_admission_only',
              'installed_target': None, 'runner_consumption_verified': False, 'suite': args.suite}
    processor = None
    try:
        import torch
        from vllm.renderers import ChatParams
        from vllm.renderers.base import BaseRenderer
        from vllm.sampling_params import SamplingParams
        from vllm.inputs import embeds_input
        from vllm.exceptions import VLLMValidationError
        from vllm.v1.engine.input_processor import InputProcessor
        if torch.version.cuda is not None:
            raise RuntimeError('CPU-only torch required')
        source_file = Path(sys.modules[InputProcessor.__module__].__file__).resolve()
        if not source_file.is_relative_to(root / 'vllm'):
            raise RuntimeError('Unexpected import origin')
        from guard.executors import input_cpu
        own_src = Path(__file__).resolve().parents[1] / 'src'
        if not Path(input_cpu.__file__).resolve().is_relative_to(own_src):
            raise RuntimeError('B-1 module import origin mismatch')
        record['b1_module_file_bytes_sha256'] = hashlib.sha256(Path(input_cpu.__file__).read_bytes()).hexdigest()
        processor = build_context(args.workdir / 'local-config')
        tracked = [('render_cmpl', BaseRenderer.render_cmpl), ('render_chat', BaseRenderer.render_chat),
                   ('validate_token_ids', BaseRenderer.validate_token_ids), ('process_inputs', InputProcessor.process_inputs),
                   ('validate_prompt_len', InputProcessor._validate_prompt_len), ('validate_model_input', InputProcessor._validate_model_input)]
        if args.suite == 'input':
            from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
            from vllm.entrypoints.openai.completion.protocol import CompletionRequest
            rows = run_reference(processor, SamplingParams, ChatParams, embeds_input, torch.zeros, VLLMValidationError, tracked,
                                 {'chat': ChatCompletionRequest, 'completion': CompletionRequest})
            observations = project(rows)
        elif args.suite == 'sampling':
            from pydantic import ValidationError
            from guard.executors import sampling_cpu
            from vllm import sampling_params
            from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest, BatchChatCompletionRequest
            from vllm.entrypoints.openai.completion.protocol import CompletionRequest
            from vllm.entrypoints.openai.responses.protocol import ResponsesRequest
            from vllm import envs
            if (envs.VLLM_MAX_STOP_STRINGS, envs.VLLM_MAX_N_SEQUENCES, envs.VLLM_MAX_NUM_BAD_WORDS) != (4, 4, 4):
                raise RuntimeError('Sampling fixture configuration mismatch')
            classes = {'completion': CompletionRequest, 'chat': ChatCompletionRequest,
                       'batch': BatchChatCompletionRequest, 'responses': ResponsesRequest}
            tracked = [('sequence_cap', sampling_params._verify_num_sequences),
                       ('bad_word_tokenization', SamplingParams.update_from_tokenizer),
                       ('beam_conversion', ChatCompletionRequest.to_beam_search_params)]
            rows = sampling_cpu.run_reference(classes, SamplingParams, sampling_params.BeamSearchParams,
                processor.renderer.tokenizer, (VLLMValidationError, ValidationError), tracked)
            observations = {'REQ-10': sampling_cpu.project(rows)}
            record['sampling_fixture_caps'] = {'origin': 'DECLARED', 'n': 4, 'stop_strings': 4, 'tokenized_bad_words': 4}
        elif args.suite == 'embeddings':
            import base64
            import io
            from guard.executors import embedding_cpu
            from vllm.renderers.embed_utils import safe_load_prompt_embeds
            from vllm.config import ModelConfig
            folder = args.workdir / 'local-config'
            disabled = ModelConfig(model=str(folder), tokenizer=str(folder), dtype='float32',
                                   max_model_len=16, enforce_eager=True, enable_prompt_embeds=False)
            def make_payload(shape, dtype):
                buffer = io.BytesIO()
                torch.save(torch.zeros(shape, dtype=getattr(torch, dtype)), buffer)
                return base64.b64encode(buffer.getvalue())
            tracked = [('embedding_loader', safe_load_prompt_embeds)]
            rows = embedding_cpu.run_reference(safe_load_prompt_embeds, processor.model_config, disabled,
                                              make_payload, VLLMValidationError, tracked)
            observations = {'REQ-09': embedding_cpu.project(rows)}
        else:
            from guard.executors import online_input_cpu
            from vllm.renderers.online_renderer import OnlineRenderer, _reused_prompt_token_ids
            from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
            from vllm.entrypoints.serve.engine.protocol import ErrorResponse
            from vllm.entrypoints.generate.base.protocol import validate_request_mm_kwargs
            tracked += [('online_render_chat', OnlineRenderer.render_chat),
                        ('online_preprocess_chat', OnlineRenderer.preprocess_chat),
                        ('reuse_input_gate', _reused_prompt_token_ids),
                        ('template_gate', OnlineRenderer.validate_chat_template),
                        ('mm_kwargs_gate', validate_request_mm_kwargs)]
            rows = online_input_cpu.run_reference(processor, OnlineRenderer, ChatCompletionRequest,
                                                 ErrorResponse, VLLMValidationError, tracked)
            observations = online_input_cpu.project(rows)
        record['observed_source_file_bytes_sha256'] = {
            str(Path(sys.modules[fn.__module__].__file__).resolve().relative_to(root)): hashlib.sha256(Path(sys.modules[fn.__module__].__file__).read_bytes()).hexdigest()
            for _, fn in tracked if Path(sys.modules[fn.__module__].__file__).resolve().is_relative_to(root)}
        case_errors = any(r.get('outcome') == 'execution_error' or r.get('after', {}).get('outcome') == 'execution_error' for r in rows)
        record.update(status='completed_with_case_errors' if case_errors else 'completed', results=rows, observations_by_check=observations,
                      fixture_config={'origin': 'DECLARED', 'vocab_size': 32, 'max_model_len': 16, 'hidden_size': 4},
                      source_file_bytes_sha256=hashlib.sha256(source_file.read_bytes()).hexdigest(),
                      dependencies={k: importlib.metadata.version(k) for k in ('torch', 'transformers', 'tokenizers', 'pydantic')})
    except Exception as error:
        record.update(status='execution_error', error_type=type(error).__name__, missing_module=getattr(error, 'name', None),
                      frames=[{'file': Path(f.filename).name, 'function': f.name, 'line': f.lineno} for f in traceback.extract_tb(error.__traceback__)[-8:]])
    finally:
        if processor is not None:
            processor.renderer.shutdown()
    record['parent_wall_seconds'] = time.perf_counter() - start
    record['config_file_bytes_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.workdir / 'local-config').glob('*') if p.is_file()}
    (args.workdir / 'result.json').write_text(json.dumps(record, indent=2))
    print(record['status'])
    return 0 if record['status'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
