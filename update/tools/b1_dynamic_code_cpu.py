"""Run real local custom-code loaders in fresh Python normal/-O/-OO children."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata as metadata
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4
from guard.executors.dynamic_code_cpu import fixtures,identity,clean_phases,project
SOURCE_COMMIT='5d35ce3934a7804b298559d2d99eec33792e1eb9'
FIXTURE=Path(__file__).resolve().parents[1]/'tests/fixtures/b_dynamic_code'
TARGET031_COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
SOURCE_PROFILES={'reference5d':SOURCE_COMMIT,'target031':TARGET031_COMMIT}
GATE_SHA256='1af43f4ae68271b494a8528cd1190763612735bb963ec00f0a1f4992fb802308'
FIXTURE_SHA256={
    'model/config.json':'25189bcbe70696eb0dc17af32def6890888d561abd4cf901733dad9167dcf8fe',
    'model/configuration_b1_probe.py':'47eb4be6c67ce9b7f086017787999c323a9c5f5b46669d0043cf8d07c3683ea2',
    'builtin/config.json':'6ce375807d8a2848eb10cb90ab0d2140ed6b72851774eb400ecb69c1ea267b09',
}


def verify_fixture(folder):
    actual={}
    for name in ('model','builtin'):
        base=folder/name
        if base.is_symlink():raise ValueError('DYNAMIC_CPU_FIXTURE_MISMATCH')
        for path in base.rglob('*'):
            if '__pycache__' in path.parts and path.suffix=='.pyc':
                continue
            if path.is_symlink():raise ValueError('DYNAMIC_CPU_FIXTURE_MISMATCH')
            if path.is_file():actual[str(path.relative_to(folder))]=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual!=FIXTURE_SHA256:raise ValueError('DYNAMIC_CPU_FIXTURE_MISMATCH')
    return actual


def verify_checkout():
    from tools import lif14_profiler_cpu as gate
    repo=Path(__file__).resolve().parents[2]
    expected=repo/'update/tools/lif14_profiler_cpu.py'
    try:
        if (expected.is_symlink() or not Path(gate.__file__).resolve().samefile(expected)
            or not Path(inspect.getsourcefile(gate.verify_component)).resolve().samefile(expected)
            or hashlib.sha256(expected.read_bytes()).hexdigest()!=GATE_SHA256):
            raise ValueError
    except (OSError,TypeError,AttributeError,ValueError):
        raise ValueError('DYNAMIC_CPU_COMPONENT_GATE_IDENTITY_MISMATCH') from None
    gate.verify_component(repo)


def verify_profile(root, source_profile, *, folder=None):
    if source_profile not in SOURCE_PROFILES or sys.version_info[:2]!=(3,12):
        raise ValueError('DYNAMIC_CPU_SOURCE_PROFILE_INVALID')
    verify_checkout()
    from guard.executors.structured_tools_cpu import _verify_source, _validate_target031_dependencies
    _verify_source(root,expected_commit=SOURCE_PROFILES[source_profile])
    if source_profile=='target031':_validate_target031_dependencies()
    for name,module in tuple(sys.modules.items()):
        if name=='vllm' or name.startswith('vllm.'):
            filename=getattr(module,'__file__',None)
            if filename is None or not Path(filename).resolve().is_relative_to(root/'vllm'):
                raise ValueError('DYNAMIC_CPU_TARGET_IMPORT_MISMATCH')
    return verify_fixture(FIXTURE if folder is None else folder)


def post_validation(root, source_profile, producer_sha256, *, folder=None):
    try:
        verify_profile(root,source_profile,folder=folder)
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=producer_sha256:
            raise ValueError('DYNAMIC_CPU_COLLECTOR_BYTES_CHANGED')
        return {'status':'verified'}
    except ValueError as error:return {'status':'mismatch','error_type':type(error).__name__}
    except Exception as error:return {'status':'unavailable','error_type':type(error).__name__}



def read_events(path):
    events=[];status='completed'
    if not path.exists():return events,status
    try:lines=path.read_text().splitlines()
    except (OSError,UnicodeError):return [],'read_error'
    for line in lines:
        try:events.extend(clean_phases([json.loads(line)]))
        except (ValueError,TypeError):status='partial'
    return events,status


def trust_rejection(error):
    seen=set()
    while error is not None and id(error) not in seen:
        seen.add(id(error));tb=error.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code.co_name=='resolve_trust_remote_code' and Path(tb.tb_frame.f_code.co_filename).name=='dynamic_module_utils.py':return True
            tb=tb.tb_next
        error=error.__cause__ or error.__context__
    return False


def execute(case,folder,*,source_root=None,source_profile='reference5d',collection_id=None):
    record={**case,'case_identity':identity(case),'outcome':'execution_error','events':[],
            'source_profile':source_profile,'source_commit':SOURCE_PROFILES[source_profile],
            'collection_id':collection_id,'target_started':False,'python_optimize':sys.flags.optimize}
    producer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    record['source_validation']={'pre':'unavailable','boundary':'source_fixture_component'}
    try:
        if sys.flags.optimize!={'normal':0,'O':1,'OO':2}[case['mode']]:raise RuntimeError('Python mode mismatch')
        if source_root is None:raise ValueError('DYNAMIC_CPU_SOURCE_REQUIRED')
        verify_profile(source_root,source_profile,folder=folder)
        record['source_validation']['pre']='verified'
        from transformers import AutoConfig
        from transformers import dynamic_module_utils as dynamic
        from vllm.transformers_utils.config import get_config
        dist=metadata.distribution('transformers')
        for obj,relative in ((dynamic,'transformers/dynamic_module_utils.py'),
                             (AutoConfig,'transformers/models/auto/configuration_auto.py')):
            if not Path(inspect.getfile(obj)).resolve().samefile(dist.locate_file(relative)):
                raise ValueError('DYNAMIC_CPU_TRANSFORMERS_IMPORT_MISMATCH')
        verify_profile(source_root,source_profile,folder=folder)
        source=folder/'model';builtin=folder/'builtin'
        loader=(lambda path,trust:AutoConfig.from_pretrained(path,trust_remote_code=trust,local_files_only=True)) if case['entry']=='autoconfig' else (lambda path,trust:get_config(path,trust_remote_code=trust))
        events_path=folder/'events.jsonl'
        expected=hashlib.sha256((source/'configuration_b1_probe.py').read_bytes()).hexdigest()
        def control():
            obj=loader(builtin,False)
            return {'outcome':'accepted','model_type':obj.model_type}
        record['before']=control()
        calls=[]
        def profile(frame,event,arg):
            if event=='call' and frame.f_code==dynamic.resolve_trust_remote_code.__code__:
                calls.append({k:frame.f_locals.get(k) for k in ('trust_remote_code','has_local_code','has_remote_code')})
        if sys.getprofile() is not None:raise RuntimeError('Existing profiler unavailable')
        start=time.perf_counter()
        try:
            sys.setprofile(profile)
            record['target_started']=True
            obj=loader(source,case['trust'])
            record['outcome']='accepted'
            path=Path(inspect.getfile(type(obj)))
            record['selected_module_bytes_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            record['selected_fixture_source_matches']=record['selected_module_bytes_sha256']==expected
        except Exception as error:
            record.update(outcome='rejected_at_trust_gate' if not case['trust'] and trust_rejection(error) else 'execution_error',error_type=type(error).__name__)
        finally:sys.setprofile(None)
        record['target_elapsed_seconds']=time.perf_counter()-start
        record['effective_gate_flags']=calls
        record['events'],record['event_collection_status']=read_events(events_path)
        try:record['after']=control()
        except Exception:record['after']={'outcome':'execution_error'}
        record['python_optimize']=sys.flags.optimize
        record['source_bytes_sha256']={str(Path(inspect.getfile(fn))):hashlib.sha256(Path(inspect.getfile(fn)).read_bytes()).hexdigest() for fn in (dynamic.resolve_trust_remote_code,get_config)}
        record['dependencies']={k:metadata.version(k) for k in ('transformers','pydantic','torch')}
    except Exception as error:
        record.update(error_type=type(error).__name__)
        if record['source_validation']['pre']!='verified':
            record['source_validation']['pre']='mismatch' if isinstance(error,ValueError) else 'unavailable'
        record['events'],record['event_collection_status']=read_events(folder/'events.jsonl')
    record['source_validation']['post']=post_validation(source_root,source_profile,producer_sha256,folder=folder)
    (folder/'case.json').write_text(json.dumps(record,indent=2))
    return 0


def capture(source_root:Path,workdir:Path,*,source_profile='reference5d')->dict:
    if sys.flags.optimize:raise ValueError('Parent must run normally; child modes are explicit')
    root=source_root.resolve()
    fixture_hashes=verify_profile(root,source_profile)
    producer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    collection_id='dynamic-cpu-'+uuid4().hex
    started=datetime.now(timezone.utc).isoformat()
    workdir.mkdir(parents=True,exist_ok=False);rows=[];start=time.perf_counter()
    for case in fixtures():
        folder=workdir/case['case_id'];folder.mkdir()
        shutil.copytree(FIXTURE/'model',folder/'model');shutil.copytree(FIXTURE/'builtin',folder/'builtin')
        env=os.environ.copy();env.update(HF_MODULES_CACHE=str(folder/'hf-modules'),
            BYUL_B_DYNAMIC_EVENTS=str(folder/'events.jsonl'),VLLM_PLUGINS='')
        flags={'normal':[],'O':['-O'],'OO':['-OO']}[case['mode']]
        cmd=[sys.executable,*flags,str(Path(__file__).resolve()),'--source-root',str(root),
            '--workdir',str(folder),'--case',case['case_id'],'--source-profile',source_profile,
            '--collection-id',collection_id]
        began=time.perf_counter()
        try:
            completed=subprocess.run(cmd,env=env,capture_output=True)
            file=folder/'case.json'
            row=json.loads(file.read_text()) if file.exists() else {**case,'case_identity':identity(case),'outcome':'execution_error'}
            row.update(exit_code=completed.returncode,parent_wall_seconds=time.perf_counter()-began)
        except Exception as error:
            row={**case,'case_identity':identity(case),'outcome':'execution_error','error_type':type(error).__name__}
        rows.append(row)
    finished=datetime.now(timezone.utc).isoformat()
    post=post_validation(root,source_profile,producer_sha256)
    observations=project(rows)
    qualified=all(r.get('source_validation',{}).get('pre')=='verified'
        and r.get('source_validation',{}).get('post',{}).get('status')=='verified'
        and r.get('source_profile')==source_profile and r.get('source_commit')==SOURCE_PROFILES[source_profile]
        and r.get('collection_id')==collection_id and r.get('target_started') is True
        and r.get('python_optimize')=={'normal':0,'O':1,'OO':2}[r['mode']]
        and r.get('exit_code')==0 for r in rows)
    result={'scope':'reference_local_custom_config_cpu_only','source_commit':SOURCE_PROFILES[source_profile],
            'source_profile':source_profile,'installed_target':None,'runner_hook_verified':False,
            'fixture_origin':'DECLARED owned harmless module','rows':rows,'observations':observations,
            'fixture_file_bytes_sha256':fixture_hashes,'parent_wall_seconds':time.perf_counter()-start,
            'own_producer_sha256':producer_sha256,'collection_id':collection_id,
            'capture_started_at':started,'capture_finished_at':finished,
            'source_validation':{'pre':'verified','post':post,'boundary':'source_fixture_component'},
            'capture_status':'completed' if observations['complete'] and qualified and post['status']=='verified' else 'incomplete',
            'auxiliary_write':{'status':'completed'}}
    try:(workdir/'result.json').write_text(json.dumps(result,indent=2))
    except Exception as error:
        result['capture_status']='incomplete'
        result['auxiliary_write']={'status':'failed','error_type':type(error).__name__}
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--workdir',type=Path,required=True);parser.add_argument('--case',choices=[c['case_id'] for c in fixtures()])
    parser.add_argument('--source-profile',choices=SOURCE_PROFILES,default='reference5d')
    parser.add_argument('--collection-id')
    args=parser.parse_args()
    if args.case:
        return execute(next(c for c in fixtures() if c['case_id']==args.case),args.workdir,
            source_root=args.source_root.resolve(),source_profile=args.source_profile,collection_id=args.collection_id)
    result=capture(args.source_root,args.workdir,source_profile=args.source_profile)
    print(json.dumps({'capture_status':result['capture_status'],'rows':len(result['rows']),
        'source_profile':result['source_profile'],'collection_id':result['collection_id']}))
    return 0 if result['capture_status']=='completed' else 2


if __name__=='__main__':raise SystemExit(main())
