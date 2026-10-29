"""Fixed supplied-template source CPU boundary; no engine/GPU or policy PASS."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import inspect
import json
import marshal
import os
from pathlib import Path
import sys
import uuid
from guard.adapters import vllm_template_boundary as native

SOURCE_COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
PRODUCT_COMPONENT_COMMIT=None
HELPER_SHA256='858666f5595fa6328a2975ef5a91eb9db19ad0712d11be08f64d8296cadaf6bf'
BASE_SHA='c9af729cd1893eda14d0bb222a8fab46c2a6d7b8'
PINNED_PATHS=('update/src/guard','update/packs','update/uv.lock','update/pyproject.toml','update/reference/framework')
BUILDER_SHA256='cf92c4c32588314f83a637523785e0ebb5e6dd6bbab50954fa1a24c2f86e39bb'
MAX_EVENTS=128
MAX_WRAPPER_ARGS=32
TEMPLATE_SHA256='38110f6e668d55f240ecfde0d38f5fecc7c11a742d76823ffd9d0918abb9fe36'


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_component():
    from tools import b1_integration_cpu as helper
    repo=Path(__file__).resolve().parents[2];file=repo/'update/tools/b1_integration_cpu.py'
    if not isinstance(PRODUCT_COMPONENT_COMMIT,str) or len(PRODUCT_COMPONENT_COMMIT)!=40:raise ValueError('SUP13_COMPONENT_PIN_UNCONFIGURED')
    if (file.is_symlink() or not Path(helper.__file__).resolve().samefile(file)
        or not Path(inspect.getsourcefile(helper.verify_snapshot)).resolve().samefile(file)
        or _sha(file)!=HELPER_SHA256 or helper.BASE_SHA!=BASE_SHA or tuple(helper.PINNED_PATHS)!=PINNED_PATHS):raise ValueError('SUP13_HELPER_MISMATCH')
    expected=repo/'update/src/guard/adapters/vllm_template_boundary.py'
    if not Path(native.__file__).resolve().samefile(expected) or not Path(inspect.getsourcefile(native.snapshot_template_boundary)).resolve().samefile(expected):raise ValueError('SUP13_NATIVE_IMPORT_MISMATCH')
    helper.verify_snapshot(repo,PRODUCT_COMPONENT_COMMIT,PINNED_PATHS)


def _verify_source(root):
    from guard.executors.structured_tools_cpu import _verify_source as verify,_validate_target031_dependencies
    verify(root,expected_commit=SOURCE_COMMIT);_validate_target031_dependencies()
    for name,module in tuple(sys.modules.items()):
        if name=='vllm' or name.startswith('vllm.'):
            file=getattr(module,'__file__',None)
            if file is None or not Path(file).resolve().is_relative_to(root/'vllm'):raise ValueError('SUP13_SOURCE_IMPORT_MISMATCH')


def _post(root,own,callback,*,component=True):
    try:
        if component:verify_component()
        _verify_source(root)
        if _sha(__file__)!=own or _sha(native.__file__)!=callback:raise ValueError('SUP13_BYTES_CHANGED')
        return {'status':'verified'}
    except ValueError as error:return {'status':'mismatch','error_type':type(error).__name__}
    except Exception as error:return {'status':'unavailable','error_type':type(error).__name__}



def _snapshot_arguments(point,values):
    """Normalize only bounded builtin wrapper containers, never descriptors."""
    kwargs={k:values[k] for k in ('conversation','messages','chat_template') if k in values}
    if point!='tokenizer.apply_chat_template':return (),kwargs
    wrapped_args=values.get('args',())
    wrapped_kwargs=values.get('kwargs',{})
    if type(wrapped_args) is not tuple or len(wrapped_args)>MAX_WRAPPER_ARGS or type(wrapped_kwargs) is not dict or len(wrapped_kwargs)>MAX_WRAPPER_ARGS:
        return (),{}
    for key in ('conversation','messages','chat_template'):
        if key in wrapped_kwargs:kwargs[key]=wrapped_kwargs[key]
    # Method callback convention includes actual self before public positional args.
    return (values.get('self'),*wrapped_args),kwargs


def _retain_event(row,snapshot):
    if len(row['snapshots'])>=MAX_EVENTS:
        gaps=row.setdefault('observation_gaps',[])
        if 'event_limit_exceeded' not in gaps:gaps.append('event_limit_exceeded')
        row['dropped_event_count']=row.get('dropped_event_count',0)+1
        return False
    row['snapshots'].append(snapshot)
    return True


def _run_actual(root,workdir,record,*,component=True):
    # Direct boundary use still pins source and producer bytes; only the formal component is pending root freeze.
    _verify_source(root)
    from tools import b1_input_cpu as builder
    expected=Path(__file__).resolve().with_name('b1_input_cpu.py')
    if expected.is_symlink() or not Path(builder.__file__).resolve().samefile(expected) or _sha(expected)!=BUILDER_SHA256 or not Path(inspect.getsourcefile(builder.build_context)).resolve().samefile(expected):raise ValueError('SUP13_BUILDER_MISMATCH')
    template_file=Path(native.__file__).with_name('assets')/'chat/guard-type-boundary-v1.jinja'
    if template_file.is_symlink() or _sha(template_file)!=TEMPLATE_SHA256:raise ValueError('SUP13_FIXTURE_MISMATCH')
    template=template_file.read_text()
    import importlib.metadata as md
    if md.version('jinja2')!='3.1.6':raise ValueError('SUP13_JINJA_VERSION_UNSUPPORTED')
    from vllm.renderers.hf import safe_apply_chat_template,resolve_chat_template
    from jinja2.sandbox import SandboxedEnvironment,ImmutableSandboxedEnvironment
    if not Path(inspect.getsourcefile(safe_apply_chat_template)).resolve().samefile(root/'vllm/renderers/hf.py'):raise ValueError('SUP13_TARGET_IMPORT_MISMATCH')
    processor=None
    try:
        record['dependency_versions']={key:md.version(key) for key in ('jinja2','transformers','torch')}
        processor=builder.build_context(workdir/'local-config');tokenizer=processor.renderer.tokenizer
        functions={'safe_apply_chat_template':safe_apply_chat_template,'resolve_chat_template':resolve_chat_template,
            'tokenizer.apply_chat_template':tokenizer.apply_chat_template,
            'SandboxedEnvironment.is_safe_attribute':SandboxedEnvironment.is_safe_attribute,
            'ImmutableSandboxedEnvironment.is_safe_attribute':ImmutableSandboxedEnvironment.is_safe_attribute}
        codes={fn.__code__:(point,fn) for point,fn in functions.items()}
        record['function_inventory']={point:{'file_sha256':_sha(inspect.getsourcefile(fn)),
            'code_sha256':hashlib.sha256(marshal.dumps(fn.__code__)).hexdigest(),'module':fn.__module__,'qualname':fn.__qualname__} for point,fn in functions.items()}
        record['config_file_bytes_sha256']={p.name:_sha(p) for p in (workdir/'local-config').iterdir() if p.is_file()}
        for case,messages in (('before',native.NORMAL),('challenge',native.CHALLENGE),('after',native.NORMAL)):
            row={'case_id':case,'fixture_sha256':TEMPLATE_SHA256,'messages_sha256':native.digest(messages),'outcome':'unavailable','result_summary':None,'exception_type':None,'invocation_id_sha256':hashlib.sha256(uuid.uuid4().bytes).hexdigest(),'snapshots':[],
                'source_validation':{'pre':'verified','boundary':'source_component'}}
            record['results'].append(row)
            pre=_post(root,record['own_producer_sha256'],record['native_callback_sha256'],component=component)
            if pre['status']!='verified':row['source_validation']['pre']='unavailable';row['source_validation']['post']=pre;continue
            bindings={};events=row['snapshots']
            def observe(frame,event,arg):
                selected=codes.get(frame.f_code)
                if selected is None:return trace
                point,fn=selected
                if event=='call':
                    binding={'call_id_sha256':hashlib.sha256(uuid.uuid4().bytes).hexdigest(),'invocation_id_sha256':row['invocation_id_sha256'],'pid':os.getpid()}
                    parent=frame.f_back
                    while parent is not None:
                        if id(parent) in bindings:
                            binding['parent_call_id_sha256']=bindings[id(parent)]['call_id_sha256'];break
                        parent=parent.f_back
                    bindings[id(frame)]=binding
                phase={'call':'CALL','return':'RETURN','exception':'RAISE'}.get(event)
                if phase and len(events)>=MAX_EVENTS:
                    _retain_event(row,None)
                elif phase:
                    values=frame.f_locals
                    if point.endswith('.is_safe_attribute'):
                        args=(values.get('self'),values.get('obj'),values.get('attr'),values.get('value'));context='unavailable';ancestor=frame.f_back
                        while ancestor is not None:
                            module=ancestor.f_globals.get('__name__')
                            if module in ('jinja2.compiler','jinja2.optimizer','jinja2.nodes'):context='compile_or_optimize';break
                            if ancestor.f_code.co_filename=='<template>':context='template_runtime'
                            ancestor=ancestor.f_back
                        kwargs={'evaluation_context':context}
                    else:args,kwargs=_snapshot_arguments(point,values)
                    _retain_event(row,native.snapshot_template_boundary(point,args,kwargs,phase=phase,result=arg if event=='return' else None,exception=arg[1] if event=='exception' else None,binding=bindings.get(id(frame))))
                if event=='return':bindings.pop(id(frame),None)
                return trace
            def trace(frame,event,arg):
                try:observe(frame,event,arg)
                except BaseException as error:
                    row['observer_error_type']=type(error).__name__
                return trace
            prior=sys.gettrace()
            try:
                if prior is not None:raise ValueError('SUP13_EXISTING_TRACE_UNSUPPORTED')
                sys.settrace(trace)
                value=safe_apply_chat_template(processor.model_config,tokenizer,messages,chat_template=template,tokenize=False,add_generation_prompt=True)
                row.update(outcome='returned',result_summary=native._summary(value))
            except Exception as error:row.update(outcome='raised',exception_type=type(error).__name__)
            finally:sys.settrace(prior)
            row['snapshots']=events
            row['source_validation']['post']=_post(root,record['own_producer_sha256'],record['native_callback_sha256'],component=component)
    finally:
        if processor is not None:
            try:processor.renderer.shutdown()
            except Exception as error:record['shutdown_error_type']=type(error).__name__


def capture_template_cpu(source_root,workdir,*,source_profile='target031'):
    if source_profile!='target031' or sys.flags.optimize or sys.version_info[:2]!=(3,12):raise ValueError('SUP13_PROFILE_INVALID')
    verify_component();root=Path(source_root).resolve()
    workdir=Path(workdir);workdir.mkdir(parents=True,exist_ok=False)
    for key,folder in (('VLLM_CACHE_ROOT','cache'),('HF_HOME','hf'),('XDG_CACHE_HOME','cache')):os.environ[key]=str(workdir/folder)
    _verify_source(root)
    # Callers must choose the CPU/offline process environment before importing vLLM.
    record={'schema':'byul-sup13-template-cpu-capture/1','source_profile':source_profile,'source_commit':SOURCE_COMMIT,
        'own_producer_sha256':_sha(__file__),'native_callback_sha256':_sha(native.__file__),'fixture_sha256':TEMPLATE_SHA256,
        'collection_id':'sup13-template-cpu-'+uuid.uuid4().hex,'capture_started_at':datetime.now(timezone.utc).isoformat(),
        'source_validation':{'pre':'verified','boundary':'source_component'},'scope':'actual_source_cpu_template_only','data_origin':'DECLARED',
        'installed_target':None,'gpu_execution_verified':False,'full_scope_coverage':False,'results':[]}
    try:_run_actual(root,workdir,record)
    except Exception as error:record['error_type']=type(error).__name__
    record['capture_finished_at']=datetime.now(timezone.utc).isoformat();record['source_validation']['post']=_post(root,record['own_producer_sha256'],record['native_callback_sha256'])
    record['capture_status']='completed' if len(record['results'])==3 and all(r['source_validation']['post']['status']=='verified' and r['snapshots'] and 'observer_error_type' not in r and not r.get('observation_gaps') for r in record['results']) and record['source_validation']['post']['status']=='verified' and 'error_type' not in record and 'shutdown_error_type' not in record else 'incomplete'
    record['auxiliary_write']={'status':'completed'}
    try:(workdir/'result.json').write_text(json.dumps(record,indent=2))
    except Exception as error:record.update(capture_status='incomplete',auxiliary_write={'status':'failed','error_type':type(error).__name__})
    return record


def main():
    p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,required=True);p.add_argument('--workdir','--output',dest='workdir',type=Path,required=True);a=p.parse_args()
    r=capture_template_cpu(a.source_root,a.workdir);print(r['capture_status']);return 0 if r['capture_status']=='completed' else 2

if __name__=='__main__':raise SystemExit(main())
