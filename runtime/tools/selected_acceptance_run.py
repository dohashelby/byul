"""Root-operated checkpoints for the seven reviewed selected acceptance helpers.

No cloud, deployment, policy, collector or credential mutation. A public config
pins one source and one stage. Root changes environments between invocations.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

MODULES = {
    'tools.selected_acceptance_run':'runtime/tools/selected_acceptance_run.py',
    'tools.selected_runtime_check':'runtime/tools/selected_runtime_check.py',
    'tools.selected_request_checks':'runtime/tools/selected_request_checks.py',
    'tools.selected_response_checks':'runtime/tools/selected_response_checks.py',
    'tools.selected_copy_checks':'runtime/tools/selected_copy_checks.py',
    'tools.selected_model_checks':'runtime/tools/selected_model_checks.py',
    'tools.selected_observation_checks':'runtime/tools/selected_observation_checks.py',
    'serving_runtime.mvp.validator':'runtime/src/serving_runtime/mvp/validator.py',
    'serving_runtime.mvp.retention':'runtime/src/serving_runtime/mvp/retention.py',
    'serving_runtime.mvp.response_timeout':'runtime/src/serving_runtime/mvp/response_timeout.py',
    'byul_platform.main':'platform/api/src/byul_platform/main.py',
    'byul_platform.sources.runtime':'platform/api/src/byul_platform/sources/runtime.py',
    'byul_platform.sources.checklist_data':'platform/api/src/byul_platform/sources/checklist_data.py',
    'guard.executors.stream_probe':'update/src/guard/executors/stream_probe.py',
}
STAGES = ('live','requests','separation','model-before','model-after','observe-baseline','observe-gap','observe-recovered','isolated','copies')
SHA = re.compile(r'[0-9a-f]{64}')
ENV = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}')
UUID = re.compile(r'[0-9a-f]{32}')
SCOPE = ('backend_kind','policy_sha256','target_id','run_id','target_instance_id','pod_uid','container_id','restart_count','image_digest')


def digest(raw):return hashlib.sha256(raw).hexdigest()


def bounded(path, cap=2*1024*1024):
    path=Path(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size>cap:raise ValueError('regular bounded public file required')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(cap+1)
        after=os.fstat(fd);current=os.lstat(path)
        identity=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_ctime_ns,v.st_mode,v.st_uid,v.st_gid)
        if len(raw)>cap or len(raw)!=before.st_size or identity(before)!=identity(after) or identity(after)!=identity(current):raise ValueError('public input changed')
        return raw
    finally:os.close(fd)


def checked_document(path,expected=None):
    raw=bounded(path);sha=digest(raw)
    if expected is not None and sha!=expected:raise ValueError('public input hash mismatch')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('object required')
    return value,sha,raw


def read_json(path):return checked_document(path)[0]



def origin(value):
    parsed=urlsplit(value)
    if (parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or
            parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment or not parsed.port):
        raise ValueError('explicit loopback HTTP origin required')
    return value.rstrip('/')


def source_revision(source):
    marker=Path(source)/'SOURCE_SHA'
    return (bounded(marker,128).decode().strip() if marker.exists() else subprocess.check_output(
        ['git','rev-parse','HEAD'],cwd=source,text=True,timeout=5,stderr=subprocess.DEVNULL).strip())


def source_pins(source):
    source=Path(source).resolve()
    sha=source_revision(source)
    if not re.fullmatch(r'[0-9a-f]{40}',sha):raise ValueError('full source commit required')
    files={relative:digest(bounded(source/relative)) for relative in MODULES.values()}
    dist=source/'platform/web/dist'
    # Vite output has a bounded explicit index+asset set; no source/raw traversal.
    web=[dist/'index.html',*sorted((dist/'assets').glob('*.js')),*sorted((dist/'assets').glob('*.css'))]
    if len(web)>32:raise ValueError('web asset cap')
    return {'path':str(source),'sha':sha,'files':files,'web':{str(p.relative_to(dist)):digest(bounded(p,8*1024*1024)) for p in web if p.is_file()}}


def verify_source(config, *, imports=True):
    expected=config['source'];actual=source_pins(expected['path'])
    if actual!=expected:raise ValueError('source pin mismatch')
    if not expected['web'] or 'index.html' not in expected['web']:raise ValueError('built web required')
    if imports:
        source=Path(expected['path'])
        sys.path[:0]=[str(source/p) for p in ('runtime','runtime/src','update/src','platform/api/src')]
        for name,relative in MODULES.items():
            module=importlib.import_module(name)
            if Path(module.__file__).resolve()!=source/relative:raise ValueError('foreign module import')
    return actual


def validate(config):
    allowed={'schema','session','source','engine','gateway_url','platform_url','platform_receipt','inference_token_env','viewer_token_env',
             'model','stage','expected_scope','options','input_sha256','witnesses','timeout_seconds','stage_deadline_seconds'}
    if set(config)-allowed or config.get('schema')!='byul-selected-acceptance-run/1':raise ValueError('config schema')
    if config.get('stage') not in STAGES or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',config.get('session','')):raise ValueError('stage/session')
    for name in ('gateway_url','platform_url'):origin(config[name])
    for name in ('inference_token_env','viewer_token_env'):
        if not ENV.fullmatch(config[name]):raise ValueError('environment reference')
    for name,maximum in (('timeout_seconds',60),('stage_deadline_seconds',900)):
        number=config[name]
        if type(number) not in (int,float) or not math.isfinite(number) or not 1<=number<=maximum:raise ValueError('finite deadline')
    engine=config['engine'];scope=config['expected_scope']
    if (set(engine)!={'kind','image_digest','model_revision','inventory_sha256'} or engine.get('kind') not in ('vllm','sglang') or not re.fullmatch(r'sha256:[0-9a-f]{64}',engine.get('image_digest','')) or
            not re.fullmatch(r'[0-9a-f]{40}',engine.get('model_revision','')) or not SHA.fullmatch(engine.get('inventory_sha256',''))):raise ValueError('engine/model pins')
    if (set(scope)!=set(SCOPE) or any(not isinstance(scope[k],str) or not scope[k] for k in SCOPE if k not in ('target_instance_id','restart_count')) or
            scope['backend_kind']!=engine['kind'] or scope['image_digest']!=engine['image_digest'] or not SHA.fullmatch(scope['policy_sha256']) or
            type(scope['restart_count']) is not int or scope['restart_count']<0 or scope['target_instance_id'] is not None and not isinstance(scope['target_instance_id'],str)):
        raise ValueError('complete expected scope')
    model=config['model']
    if not isinstance(model,str) or not 1<=len(model)<=256 or any(ord(c)<32 for c in model):raise ValueError('model selector')
    stage=config['stage'];opt=config.get('options',{})
    keys={'live':{'forbidden_keys'},'requests':set(),'separation':set(),'model-before':{'replacement_model','backend_config'},
          'model-after':{'replacement_model','backend_config','before_report'},'isolated':{'test_data_dir','backend_config','engine_forward_receipt'},
          'copies':{'files','marker_envs','credential_envs','allowed_reader','denied_reader','volume_evidence'}}
    if stage.startswith('observe-'):keys[stage]={'source','expected_state'}
    if not isinstance(opt,dict) or set(opt)-keys[stage]:raise ValueError('stage options')
    if stage.startswith('model-') and (not isinstance(opt.get('replacement_model'),str) or opt['replacement_model']==model):raise ValueError('distinct replacement model required')
    if stage=='model-after' and not opt.get('before_report'):raise ValueError('before report required')
    if stage.startswith('observe-') and (opt.get('source') not in ('hubble','tetragon') or opt.get('expected_state') not in ('ok','partial','error','stale','not_configured')):raise ValueError('source/state required')
    if stage=='isolated' and (not opt.get('test_data_dir') or Path(opt['test_data_dir']).exists() or not opt.get('backend_config') or not opt.get('engine_forward_receipt')):raise ValueError('new owned isolated tree and exact forward required')
    if stage=='copies':
        if not isinstance(opt.get('files'),list) or not 1<=len(opt['files'])<=16 or not opt.get('marker_envs') or not opt.get('credential_envs'):raise ValueError('explicit copies and value refs')
        if not opt.get('allowed_reader') or not opt.get('denied_reader') or not opt.get('volume_evidence'):raise ValueError('existing readers and volume evidence required')
        if any(not ENV.fullmatch(n) for n in opt['marker_envs']+opt['credential_envs']):raise ValueError('copy env refs')
    inputs=config.get('input_sha256',{})
    required={key for key in ('backend_config','before_report','engine_forward_receipt','volume_evidence') if opt.get(key)}
    if set(inputs)!=required:raise ValueError('exact stage input pins required')
    for key in required:
        if not SHA.fullmatch(inputs[key]) or digest(bounded(opt[key]))!=inputs[key]:raise ValueError('stage input pin mismatch')
    if stage=='live' and (not isinstance(opt.get('forbidden_keys',[]),list) or len(opt.get('forbidden_keys',[]))>32 or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',key) for key in opt.get('forbidden_keys',[]))):raise ValueError('metadata key list')
    witnesses=config.get('witnesses',[])
    if not isinstance(witnesses,list) or len(witnesses)>32:raise ValueError('witness cap')
    for item in [config['platform_receipt'],*witnesses]:
        if set(item)-{'path','sha256','role'} or not SHA.fullmatch(item.get('sha256','')):raise ValueError('public witness ref')
        if digest(bounded(item['path']))!=item['sha256']:raise ValueError('witness hash mismatch')
    return config


def snapshot_inputs(config,private):
    """Only verified bytes reach the child; original paths are never consumed."""
    frozen=copy.deepcopy(config);records=[]
    def snapshot(path,expected,name):
        _value,sha,raw=checked_document(path,expected)
        target=private/(name+'.json')
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o400)
        with os.fdopen(fd,'wb') as output:output.write(raw)
        records.append({'input_path':str(path),'snapshot_path':str(target),'sha256':sha})
        return str(target)
    ref=frozen['platform_receipt'];ref['path']=snapshot(ref['path'],ref['sha256'],'platform-receipt')
    for index,ref in enumerate(frozen.get('witnesses',[])):
        ref['path']=snapshot(ref['path'],ref['sha256'],'witness-'+str(index))
    for key,sha in frozen.get('input_sha256',{}).items():
        frozen['options'][key]=snapshot(frozen['options'][key],sha,key)
    return frozen,records


def inputs_bound(config):
    opt=config.get('options',{})
    refs=[config['platform_receipt'],*config.get('witnesses',[])]
    return (all(digest(bounded(item['path']))==item['sha256'] for item in refs) and
            all(digest(bounded(opt[key]))==sha for key,sha in config.get('input_sha256',{}).items()))


def command(config, output):
    stage=config['stage'];opt=config.get('options',{})
    mode='lifecycle' if stage.startswith('model-') else 'observe' if stage.startswith('observe-') else stage
    args=[sys.executable,str(Path(config['source']['path'])/'runtime/tools/selected_runtime_check.py'),mode,'--output',str(output),'--timeout',str(config['timeout_seconds'])]
    if mode not in ('copies','observe'):args+=['--model',config['model']]
    if mode!='isolated' and mode!='copies':args+=['--gateway-url',config['gateway_url'],'--viewer-token-env',config['viewer_token_env']]
    if mode in ('live','requests','lifecycle','observe'):args+=['--platform-url',config['platform_url']]
    if mode in ('live','requests','separation','lifecycle'):args+=['--inference-token-env',config['inference_token_env']]
    if mode=='live':
        for key in opt.get('forbidden_keys',[]):args+=['--forbidden-key',key]
    if mode=='lifecycle':
        args+=['--phase',stage.removeprefix('model-'),'--replacement-model',opt['replacement_model']]
        for key in ('backend_config','before_report'):
            if opt.get(key):args+=['--'+key.replace('_','-'),opt[key]]
    if mode=='observe':args+=['--source',opt['source'],'--expected-state',opt['expected_state'],'--wait-seconds','5']
    if mode=='isolated':args+=['--test-data-dir',opt['test_data_dir'],'--backend',config['engine']['kind'],'--backend-config',opt['backend_config'],'--fixture-count','200','--retention-age-seconds','1','--retention-max-bytes','4096']
    if mode=='copies':
        for key,flag in (('files','--file'),('marker_envs','--marker-env'),('credential_envs','--credential-env')):
            for value in opt[key]:args += [flag,value]
        for key in ('allowed_reader','denied_reader','volume_evidence'):args+=['--'+key.replace('_','-'),opt[key]]
    return args


def status_scope(value):
    target=value.get('target') or {};current=((target.get('instance') or {}).get('current') or {})
    return {'backend_kind':value.get('backend_kind') or (value.get('backend') or {}).get('kind'),
            'policy_sha256':(value.get('policy') or {}).get('sha256'),'target_id':target.get('target_id'),
            'run_id':value.get('run_id'),'target_instance_id':current.get('instance_id'),
            **{key:current.get(key) for key in ('pod_uid','container_id','restart_count','image_digest')}}


def capture(config, deadline):
    from tools.selected_runtime_check import request,document
    remaining=deadline-time.monotonic()
    if remaining<=0:return {'confirmed':False,'reason':'stage_deadline'}
    reply=request('GET',config['gateway_url']+'/api/status',token=os.environ.get(config['viewer_token_env']),timeout=min(config['timeout_seconds'],remaining))
    value=document(reply) if reply.status==200 else {}
    scope=status_scope(value)
    protected='GET /api/*' in (value.get('auth') or {}).get('protected_routes',[])
    return {'http_status':reply.status,'confirmed':reply.status==200 and protected and scope==config['expected_scope'],
            'protected_viewer':protected,'scope':scope,'observation_session_id':value.get('observation_session_id')}


def platform_bound(config):
    receipt=read_json(config['platform_receipt']['path']);source=config['source'];imports=receipt.get('imports') or {}
    return (receipt.get('source_sha')==source['sha'] and receipt.get('runtime_base_url')==config['gateway_url'] and
            receipt.get('host')=='127.0.0.1' and receipt.get('port')==urlsplit(config['platform_url']).port and
            receipt.get('web_dist_sha256')==source['web'] and all(
                (imports.get(name) or {}).get('sha256')==source['files'][MODULES[name]] and
                (imports.get(name) or {}).get('file')==str(Path(source['path'])/MODULES[name]) for name in ('byul_platform.main','byul_platform.sources.runtime')))


def forward_bound(config):
    if config['stage']!='isolated':return True
    receipt=read_json(config['options']['engine_forward_receipt']);scope=config['expected_scope']
    parsed=urlsplit(read_json(config['options']['backend_config'])['base_url'])
    argv=receipt.get('argv',[])
    before=receipt.get('pod_before') or {};after=receipt.get('pod_after') or {}
    return (before==after and bool(before.get('started_at')) and receipt.get('ready') is True and receipt.get('same_pod_generation') is True and
            parsed.hostname=='127.0.0.1' and bool(parsed.port) and receipt.get('bind')=='127.0.0.1:'+str(parsed.port) and
            len(argv)>=3 and argv[-1]=='--address=127.0.0.1' and argv[-3].startswith('pod/') and
            argv[-2]==str(parsed.port)+':'+str(receipt.get('remote_port')) and all(
                all((receipt.get(side) or {}).get(key)==scope[key] for key in ('pod_uid','container_id','restart_count')) for side in ('pod_before','pod_after')))


def request_ids(value):
    found=set()
    if isinstance(value,dict):
        for key,item in value.items():
            if key=='request_id' and isinstance(item,str) and UUID.fullmatch(item):found.add(item)
            elif key=='request_ids' and isinstance(item,list):found.update(x for x in item if isinstance(x,str) and UUID.fullmatch(x))
            else:found.update(request_ids(item))
    elif isinstance(value,list):
        for item in value:found.update(request_ids(item))
    return found


def case_index(result,witnesses,public_witnesses=None):
    public_witnesses=public_witnesses if public_witnesses is not None else witnesses
    refs=[]
    for item,public in zip(witnesses,public_witnesses,strict=True):
        document,_sha,_raw=checked_document(item['path'],item['sha256'])
        refs.append({**public,'ids':request_ids(document)})
    return [{'case_id':row.get('id'),'result':row.get('result'),'request_ids':sorted(request_ids(row)),
             'helper_case_index':index,'witness_refs':[{k:w[k] for k in ('path','sha256','role') if k in w} | {'matching_request_ids':sorted(request_ids(row)&w['ids'])}
                 for w in refs if request_ids(row)&w['ids']]} for index,row in enumerate(result.get('cases',[])) if isinstance(row,dict)]


def invoke(argv,env,timeout):
    process=subprocess.Popen(argv,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    try:return process.wait(timeout=timeout)
    except BaseException:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait(timeout=5)
        raise


def secrets(config):
    opt=config.get('options',{});names=[config['inference_token_env'],config['viewer_token_env'],*opt.get('marker_envs',[]),*opt.get('credential_envs',[])]
    return [os.environ[n] for n in names if os.environ.get(n)]


def contains_secret(document,values):
    variants=set()
    for value in values:
        if not value:continue
        raw=value.encode('utf-8','surrogatepass')
        variants.update((value,raw.hex(),raw.hex().upper(),base64.b64encode(raw).decode(),base64.urlsafe_b64encode(raw).decode()))
        variants.update(json.dumps(value,ensure_ascii=ascii_only)[1:-1] for ascii_only in (False,True))
    def visit(value):
        if isinstance(value,str):return any(secret in value for secret in variants)
        if isinstance(value,dict):return any(visit(k) or visit(v) for k,v in value.items())
        if isinstance(value,list):return any(visit(v) for v in value)
        return False
    return visit(document)


def write_public(path,value,values):
    if contains_secret(value,values):raise ValueError('credential export blocked')
    raw=json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)+'\n'
    with Path(path).open('x') as output:output.write(raw)
    return digest(raw.encode())


def run(config,output, *, prepare=False):
    deadline=time.monotonic()+config['stage_deadline_seconds']
    validate(config);verify_source(config)
    values=secrets(config)
    output=Path(output);output.mkdir(mode=0o700,parents=False,exist_ok=False)
    private=output/'private';private.mkdir(mode=0o700)
    public_config=config
    config,snapshot_records=snapshot_inputs(config,private)
    helper=private/'helper.json';argv=command(config,helper)
    plan={'schema':'byul-selected-run-plan/1','session':config['session'],'stage':config['stage'],'source':config['source'],
          'engine':config['engine'],'config_sha256':digest(json.dumps(config,sort_keys=True).encode()),'expected_scope':config['expected_scope'],'argv':argv,'stage_deadline_seconds':config['stage_deadline_seconds'],
          'platform_receipt':public_config['platform_receipt'],'input_sha256':config.get('input_sha256',{}),'input_snapshots':snapshot_records,'witnesses':public_config.get('witnesses',[]),'AWS_mutation':False,'production_auth_or_retention_change':False}
    plan_digest=write_public(output/'plan.json',plan,values)
    if prepare:return {'execution':'prepared','plan':str(output/'plan.json'),'inference_posts':0}
    report={'schema':'byul-selected-run/1','stage':config['stage'],'session':config['session'],'plan_sha256':plan_digest,
            'security_verdict':None,'gpu_execution_verified':False,'AWS_mutation':False,'witness_claims_independently_verified':False}
    try:
        needs_inference=config['stage'] in ('live','requests','separation','model-before','model-after')
        if not os.environ.get(config['viewer_token_env']) or needs_inference and not os.environ.get(config['inference_token_env']):raise ValueError('required environment unavailable')
        before=capture(config,deadline);report['before']=before
        if not before['confirmed'] or not platform_bound(config) or not forward_bound(config):
            report.update(execution='not_run',scope_result='unknown',reason='source_platform_forward_or_scope_precondition')
        else:
            env={**os.environ,'PYTHONPATH':os.pathsep.join(str(Path(config['source']['path'])/p) for p in ('runtime','runtime/src','update/src','platform/api/src'))}
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('stage deadline')
            if not inputs_bound(config):raise ValueError('stage input changed before helper')
            code=invoke(argv,env,remaining);report['helper_exit_code']=code
            result,_helper_digest,_raw=checked_document(helper)
            if contains_secret(result,values):
                report['credential_export_blocked']=True
                helper.unlink()
                raise ValueError('helper credential export blocked')
            expected_mode='lifecycle' if config['stage'].startswith('model-') else 'observe' if config['stage'].startswith('observe-') else config['stage']
            if result.get('mode')!=expected_mode or result.get('source_sha256')!=config['source']['files'][MODULES['tools.selected_runtime_check']]:raise ValueError('helper source or mode mismatch')
            rows=result.get('cases')
            if not isinstance(rows,list) or not rows or any(not isinstance(row,dict) or row.get('result') not in ('passed','failed','unknown') or not isinstance(row.get('id'),str) for row in rows):raise ValueError('helper case contract')
            for name,loaded in result.get('local_tool_source_files',{}).items():
                if name not in MODULES or loaded.get('sha256')!=config['source']['files'][MODULES[name]] or Path(loaded.get('path','')).resolve()!=Path(config['source']['path'])/MODULES[name]:raise ValueError('helper foreign source')
            try:report['after']=capture(config,deadline)
            except Exception as error:report['after']={'confirmed':False,'error_class':type(error).__name__}
            report['input_bindings_unchanged']=inputs_bound(config)
            verify_source(config,imports=False)
            report['same_engine_scope']=report['after']['confirmed'] is True and before['scope']==report['after']['scope']
            report['case_index']=case_index(result,config.get('witnesses',[]),public_config.get('witnesses',[]))
            report['helper_sha256']=write_public(output/'helper.json',result,values);report['execution']='completed'
            outcomes=[row['result'] for row in report['case_index']]
            report['scope_result']='failed' if code==1 or 'failed' in outcomes else 'passed' if code==0 and outcomes and all(v=='passed' for v in outcomes) and report['same_engine_scope'] and report['input_bindings_unchanged'] else 'unknown'
    except Exception as error:
        report.update(execution='incomplete',scope_result='failed' if report.get('helper_exit_code')==1 or report.get('credential_export_blocked') else 'unknown',error_class=type(error).__name__)
    write_public(output/'run.json',report,values)
    files=[output/'plan.json',output/'run.json']
    # A helper may have failed before producing its safe report. Never auto-export
    # partial/raw data, generated test trees, tokens, or arbitrary witness files.
    if 'helper_sha256' in report:files.append(output/'helper.json')
    allow={'schema':'byul-selected-public-allowlist/1','recursive_export':False,'files':[{'name':p.name,'sha256':digest(bounded(p))} for p in files]}
    write_public(output/'ALLOWLIST.json',allow,values)
    return report


def link(run_dir,witnesses_path,output):
    root=Path(run_dir);report,report_sha,_raw=checked_document(root/'run.json')
    plan,_sha,_raw=checked_document(root/'plan.json',report.get('plan_sha256'))
    helper,_sha,_raw=checked_document(root/'helper.json',report.get('helper_sha256'))
    if not report.get('plan_sha256') or not report.get('helper_sha256'):raise ValueError('completed run artifacts required')
    document=read_json(witnesses_path);items=document.get('witnesses')
    if not isinstance(items,list) or len(items)>32:raise ValueError('witness list cap')
    fixed=[];witnessed=set()
    for item in items:
        if set(item)-{'path','sha256','role'} or not SHA.fullmatch(item.get('sha256','')):raise ValueError('public witness ref')
        data,_sha,_raw=checked_document(item['path'],item['sha256'])
        ids=request_ids(data);witnessed.update(ids);fixed.append((item,ids))
    rows=[]
    for index,row in enumerate(helper.get('cases',[])):
        ids=request_ids(row)
        rows.append({'case_id':row.get('id'),'result':row.get('result'),'request_ids':sorted(ids),'helper_case_index':index,
                     'witness_refs':[{**item,'matching_request_ids':sorted(ids&known)} for item,known in fixed if ids&known]})
    result={'schema':'byul-selected-request-links/1','run_sha256':report_sha,
            'helper_sha256':report['helper_sha256'],'scope_result_preserved':report.get('scope_result'),
            'case_index':rows,'unmatched_witness_request_ids':sorted(witnessed-request_ids(helper)),
            'source_sha':plan['source']['sha'],'stage':plan['stage'],'session':plan['session'],
            'witness_claims_independently_verified':False,'security_verdict':None}
    refs=[plan['argv'][i+1] for i,arg in enumerate(plan['argv'][:-1]) if arg in ('--inference-token-env','--viewer-token-env','--credential-env','--marker-env')]
    write_public(output,result,[os.environ[name] for name in refs if os.environ.get(name)])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    pins=sub.add_parser('pins');pins.add_argument('--source',required=True);pins.add_argument('--output',required=True,type=Path)
    joins=sub.add_parser('link');joins.add_argument('--run-dir',required=True,type=Path);joins.add_argument('--witnesses',required=True,type=Path);joins.add_argument('--output',required=True,type=Path)
    for name in ('prepare','run'):
        command_parser=sub.add_parser(name);command_parser.add_argument('--config',required=True,type=Path);command_parser.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args()
    try:
        if args.action=='pins':
            write_public(args.output,source_pins(args.source),[]);print(json.dumps({'source_pins':str(args.output)}));return 0
        if args.action=='link':
            link(args.run_dir,args.witnesses,args.output);print(json.dumps({'request_links':str(args.output)}));return 0
        result=run(read_json(args.config),args.output_dir,prepare=args.action=='prepare')
        print(json.dumps({key:result[key] for key in ('execution','scope_result','stage') if key in result}))
        return 1 if result.get('scope_result')=='failed' else 2 if result.get('execution') in ('not_run','incomplete') else 0
    except Exception as error:
        print(json.dumps({'execution':'aborted','error_class':type(error).__name__}));return 2


if __name__=='__main__':raise SystemExit(main())
