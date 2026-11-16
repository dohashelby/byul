"""Server-only acceptance orchestration. No commands run at import time."""
from __future__ import annotations
import concurrent.futures, contextlib, copy, hashlib, http.client, json, os
from pathlib import Path
import shlex, socket, subprocess, sys, tempfile, time, uuid
from urllib.parse import urlsplit
from common import now, run, save

class CheckFailure(Exception): pass
class JudgmentDeferred(CheckFailure): pass
class RecoveryFailure(CheckFailure): pass

def require(condition, code):
    if not condition: raise CheckFailure(code)

def flatten(value):
    if isinstance(value,dict):
        yield value
        for v in value.values(): yield from flatten(v)
    elif isinstance(value,list):
        for v in value: yield from flatten(v)

def upgraded_feed_unit(text):
    lines=text.splitlines();found=False
    for index,line in enumerate(lines):
        if line.startswith('ExecStart='):
            require(not found,'MULTIPLE_EXECSTART_NEEDS_REVIEW');found=True
            args=shlex.split(line.split('=',1)[1])
            script=next((i for i,x in enumerate(args) if x.endswith('k8s_observation_feed.py')),None)
            require(script is not None and script>0,'EXPECTED_PYTHON_FEED_UNIT')
            args[0]='/opt/byul/runtime/.venv/bin/python'
            args[script]='/opt/byul/runtime/tools/k8s_observation_feed.py'
            if '--target-namespace' in args:
                require(args[args.index('--target-namespace')+1]=='serving-lab','UNEXPECTED_EXISTING_NAMESPACE')
            else:args.extend(['--target-namespace','serving-lab'])
            lines[index]='ExecStart='+shlex.join(args)
        elif line.startswith('WorkingDirectory='):lines[index]='WorkingDirectory=/opt/byul/runtime'
    require(found,'MISSING_FEED_EXECSTART')
    return '\n'.join(lines)+'\n'

class Live:
    def __init__(self, config, evidence):
        self.c=config; self.root=Path(config['runtime_root']);self.out=Path(evidence)
        self.python=str(self.root/'.venv/bin/python')
        self.context=config.get('context','byul-aws-lab');self.ns='serving-lab'
        self.gateway=config.get('gateway_url','http://localhost:18080')
        self.command_log=[];self.output_log=[];self.tokens={}
        token_file=Path(config['token_env_file'])
        require(token_file.is_file() and not token_file.is_symlink() and token_file.stat().st_mode & 0o077==0,
                'PRIVATE_TOKEN_FILE_REQUIRED')
        for line in token_file.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                k,v=line.split('=',1);self.tokens[k]=v
        require(set(self.tokens)=={'RUNTIME_VIEWER_TOKEN','RUNTIME_INFERENCE_TOKEN'},'TOKEN_ENV_KEYS')
        self.forward=None
    def command(self, argv, *, timeout=60, input=None, private=False):
        self.command_log.append(shlex.join(map(str,argv)))
        code,text=run(argv,timeout=timeout,cwd=self.root,input=input)
        for value in self.tokens.values():
            text=text.replace(value,'[REMOVED]').replace(hashlib.sha256(value.encode()).hexdigest(),'[REMOVED]')
        if not private:self.output_log.append(text)
        require(code==0,'COMMAND_FAILED_'+str(code))
        return text
    def kube(self,*args,timeout=60,input=None,private=False):
        return self.command(['kubectl','--context',self.context,'-n',self.ns,*args],
                            timeout=timeout,input=input,private=private)
    def api(self,path,*,body=None,stream=False,base=None,authenticated=True):
        u=urlsplit(base or self.gateway);conn=http.client.HTTPConnection(u.hostname,u.port,timeout=65)
        headers={'Content-Type':'application/json'}
        if authenticated:
            key='RUNTIME_INFERENCE_TOKEN' if body is not None else 'RUNTIME_VIEWER_TOKEN'
            headers['Authorization']='Bearer '+self.tokens[key]
        self.command_log.append(('POST' if body is not None else 'GET')+' '+path+' (credential from private file)')
        try:
            conn.request('POST' if body is not None else 'GET',path,
                         json.dumps(body).encode() if body is not None else None,headers)
            response=conn.getresponse()
            read_error=None
            try:data=response.read()
            except http.client.IncompleteRead as error:
                data=error.partial;read_error='IncompleteRead'
            result={'status':response.status,'headers':dict(response.getheaders()),
                    'body':data.decode('utf-8',errors='strict'),'read_error':read_error}
            save(self.out/('http-'+uuid.uuid4().hex+'.json'),result)
            return result
        finally:conn.close()
    def chat(self,stream=False,tokens=8):
        return {'model':'test-model','messages':[{'role':'user','content':'Count slowly from one. Use the available answer length.'}],
                'max_tokens':tokens,'stream':stream}
    def normal(self):
        r=self.api('/v1/chat/completions',body=self.chat())
        require(r['status']==200,'NORMAL_REQUEST_FAILED')
        require(bool(json.loads(r['body']).get('choices')),'NORMAL_RESPONSE_INVALID')
        return r
    def detail(self,rid):
        end=time.monotonic()+15
        while time.monotonic()<end:
            r=self.api('/api/requests/'+rid);d=json.loads(r['body'])
            rows=[x for x in d.get('gateway_records',[]) if x.get('phase')=='final']
            if len(rows)==1:return rows[0]
            time.sleep(.1)
        raise CheckFailure('FINAL_RECORD_NOT_OBSERVED')
    def fresh_gateway(self):
        if self.forward:
            self.forward.terminate();self.forward.wait(timeout=10)
        port=urlsplit(self.gateway).port
        self.forward=subprocess.Popen(['kubectl','--context',self.context,'-n',self.ns,
             'port-forward','deployment/serving-gateway',str(port)+':8080'],
             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        end=time.monotonic()+180
        while time.monotonic()<end:
            try:
                self.normal();return
            except (OSError,http.client.HTTPException,CheckFailure):time.sleep(1)
        raise RecoveryFailure('GATEWAY_RECOVERY_FAILED')
    def close(self):
        if self.forward:
            self.forward.terminate()
            try:self.forward.wait(timeout=10)
            except subprocess.TimeoutExpired:self.forward.kill();self.forward.wait()
    def services(self):
        enabled_code,enabled=run(['systemctl','is-enabled','serving-runtime-agent'],timeout=10)
        self.command_log.append('systemctl is-enabled serving-runtime-agent');self.output_log.append(enabled)
        # is-active inactive deliberately exits 3.
        code,text=run(['systemctl','is-active','serving-runtime-agent'],timeout=10)
        self.command_log.append('systemctl is-active serving-runtime-agent')
        self.output_log.append(text)
        require(enabled_code==1 and enabled.strip()=='disabled' and code==3 and text.strip()=='inactive','AGENT_MUST_STAY_DISABLED_INACTIVE')
        for name in ['tetragon-status','gpu-status','monitor-window']:
            require(self.command(['systemctl','is-active','serving-runtime-'+name]).strip()=='active','COLLECTOR_INACTIVE')
    def acceptance(self,cases,flags=(),privileged=False):
        parent=self.out/('acceptance-'+uuid.uuid4().hex);parent.mkdir(mode=0o700)
        argv=[self.python,'-B','-m','labtools.aws_lab_acceptance','--kube-context',self.context,
              '--namespace',self.ns,'--engine','vllm','--token-env-file',self.c['token_env_file'],
              '--gateway-url',self.gateway,'--evidence-dir',str(parent),'--cases',','.join(cases),*flags]
        if privileged:
            argv=['sudo','-E','env','KUBECONFIG='+self.c.get('kubeconfig',str(Path.home()/'.kube/config')),
                  'PATH='+os.environ.get('PATH',''),*argv]
        self.command_log.append(shlex.join(argv))
        code,text=run(argv,timeout=1100,cwd=self.root)
        self.output_log.append(text)
        if privileged:
            self.command(['sudo','chown','-R',str(os.getuid())+':'+str(os.getgid()),str(parent)])
        reports=list(parent.glob('*/report.json'))
        require(len(reports)==1,'ACCEPTANCE_REPORT_MISSING')
        report=json.loads(reports[0].read_text());by={x['case_id']:x for x in report['cases']}
        if (by.get('AWS-04',{}).get('facts',{}).get('direct_connected') is True or
                by.get('AWS-05',{}).get('facts',{}).get('engine_receiver_failed') is False):
            raise RecoveryFailure('ACTUAL_NETWORK_BLOCK_FAILURE')
        if any(x.get('facts',{}).get('cleanup_ok') is False for x in by.values()):
            raise RecoveryFailure('ACCEPTANCE_RECOVERY_FAILED')
        if any(by.get(x,{}).get('result')=='failed' for x in ['AWS-01','AWS-02'] if x in cases):
            raise RecoveryFailure('NORMAL_OR_AUTH_ACCEPTANCE_FAILED')
        require(set(by)==set(cases) and code==0 and all(x['result']=='passed' for x in by.values()),'ACCEPTANCE_NOT_ALL_PASSED')
        return by
    @contextlib.contextmanager
    def deployment(self,name):
        # Full template kept in memory for exact rollback; never emit env/Secret values.
        d=json.loads(self.kube('get','deployment',name,'-o','json',private=True))
        old=copy.deepcopy(d['spec']['template'])
        try:yield d
        finally:
            try:
                self.kube('patch','deployment',name,'--type=json','--patch-file=/dev/stdin',
                          input=json.dumps([{'op':'replace','path':'/spec/template','value':old}]),private=True)
                self.kube('rollout','status','deployment/'+name,'--timeout=420s',timeout=430)
                actual=json.loads(self.kube('get','deployment',name,'-o','json',private=True))
                # kubectl rollout can add restart annotation; exact supplied template must match.
                require(actual['spec']['template']==old,'TEMPLATE_RESTORE_MISMATCH')
                self.fresh_gateway()
            except Exception as e:raise RecoveryFailure('EXACT_DEPLOYMENT_RESTORE_FAILED') from e
    def set_template(self,d,name):
        self.kube('patch','deployment',name,'--type=json','--patch-file=/dev/stdin',
                  input=json.dumps([{'op':'replace','path':'/spec/template','value':d['spec']['template']}]),private=True)
        self.kube('rollout','status','deployment/'+name,'--timeout=420s',timeout=430)
        self.fresh_gateway()
    def wait_api(self,path,predicate,limit=60):
        end=time.monotonic()+limit
        while time.monotonic()<end:
            d=json.loads(self.api(path)['body'])
            if predicate(d):return d
            time.sleep(2)
        raise CheckFailure('EXPECTED_API_STATE_NOT_OBSERVED')
    def run(self,ident):
        return getattr(self,ident.replace('-','_'))()
    def emergency_snapshot(self):
        state={'deployments':{},'bundle':None,'backend':None,'compare':{}}
        for name in ['serving-engine','serving-gateway']:
            d=json.loads(self.kube('get','deployment',name,'-o','json',private=True))
            state['deployments'][name]={'template':d['spec']['template'],'replicas':d['spec']['replicas']}
        state['backend']=json.loads(self.kube('get','configmap','serving-gateway-backend','-o','json',private=True))['data']
        root=self.c.get('bundle_root','/srv/serving/bundles')
        state['bundle']=self.command(['sudo','readlink',root+'/current']).strip()
        for kind,name in [('pod','runtime-compare-client'),('pod','runtime-compare-receiver'),
                          ('ciliumnetworkpolicy','runtime-compare-ingress')]:
            text=self.kube('get',kind,name,'--ignore-not-found','-o','name',private=True)
            state['compare'][(kind,name)]=bool(text.strip())
        return state
    def emergency_restore(self,state):
        root=self.c.get('bundle_root','/srv/serving/bundles');target=state['bundle']
        require(Path(target).name==target and target not in {'','.','..'},'INVALID_SAVED_BUNDLE')
        current=self.command(['sudo','readlink',root+'/current']).strip()
        if current!=target:
            name=root+'/current.wp7-emergency-'+uuid.uuid4().hex
            self.command(['sudo','ln','-s',target,name]);self.command(['sudo','mv','-Tf',name,root+'/current'])
        self.kube('patch','configmap','serving-gateway-backend','--type=merge','--patch-file=/dev/stdin',
                  input=json.dumps({'data':state['backend']}),private=True)
        for name,old in state['deployments'].items():
            self.kube('patch','deployment',name,'--type=json','--patch-file=/dev/stdin',
                input=json.dumps([{'op':'replace','path':'/spec/template','value':old['template']},
                                  {'op':'replace','path':'/spec/replicas','value':old['replicas']}]),private=True)
            self.kube('rollout','status','deployment/'+name,'--timeout=420s',timeout=430)
        for (kind,name),existed in state['compare'].items():
            if not existed:self.kube('delete',kind,name,'--ignore-not-found','--wait=true','--timeout=30s')
        self.command(['systemctl','--user','start','byul-observation-feed'])
        self.fresh_gateway();self.services()
        save(self.out/'emergency-restore.json',{'completed_at':now(),'restored':True})
    def R_01(self):
        if self.c.get('customer_install_validation'):
            args=['sudo','bash','deploy/install/install-runtime-node.sh','--config',self.c['installer_env'],'--capture-baseline']
            self.command([*args,'--dry-run'],timeout=90)
            self.command(args,timeout=450)
        else:
            self.command(['sudo',self.python,'deploy/install/install_runtime_node.py','--config',self.c['installer_env']],timeout=450)
        self.services()
    def R_02(self):
        self.command(['sudo',self.python,'tools/migrate_gateway_data_permissions.py','--data-dir','/srv/serving/runtime/data'])
        if self.c.get('gateway_manifest'):
            self.kube('apply','-f',self.c['gateway_manifest'])
        self.kube('set','image','deployment/serving-gateway','gateway=serving-gateway:rt-7741687d')
        self.kube('rollout','status','deployment/serving-gateway','--timeout=180s',timeout=190)
        self.fresh_gateway()
    def N_03(self):
        require(len(list(Path('/opt/byul/runtime/labtools').glob('*.py')))==25,'LABTOOLS_COUNT_NOT_25')
        self.fresh_gateway()
        self.command(['/opt/byul/runtime/.venv/bin/python','-B','/opt/byul/runtime/labtools/aws_lab_preflight.py','--engine','vllm',
            '--context',self.context,'--namespace',self.ns,'--expect-gateway-tag','rt-7741687d',
            '--expect-engine-digest',self.c['engine_digest'],'--env-file',self.c['token_env_file'],
            '--gateway-url',self.gateway,'--evidence-dir',str(self.out),'--json'],timeout=110)
    def N_01(self):
        # Upgrade only interpreter/source/working directory/namespace, retaining
        # existing scoped-stream --hubble-only and every other reviewed argument.
        unit=Path.home()/'.config/systemd/user/byul-observation-feed.service'
        require(unit.is_file() and not unit.is_symlink(),'EXISTING_USER_FEED_UNIT_REQUIRED')
        original=unit.read_text();updated=upgraded_feed_unit(original)
        if updated!=original:
            backup=Path.home()/'.local/state/wp7-unit-backups'/uuid.uuid4().hex
            backup.mkdir(parents=True,mode=0o700)
            (backup/'feed.service').write_text(original);(backup/'feed.service').chmod(0o600)
            try:
                unit.write_text(updated);unit.chmod(0o600)
                self.command(['systemctl','--user','daemon-reload'])
                self.command(['systemctl','--user','restart','byul-observation-feed'])
                time.sleep(15)
            except Exception:
                unit.write_text(original)
                self.command(['systemctl','--user','daemon-reload'])
                self.command(['systemctl','--user','restart','byul-observation-feed'])
                raise
        require(self.command(['systemctl','--user','is-active','byul-observation-feed']).strip()=='active','FEED_INACTIVE')
        args=self.command(['systemctl','--user','show','byul-observation-feed','-p','ExecStart'])
        require('--target-namespace serving-lab' in args,'NAMESPACE_ARGUMENT_MISSING')
        # Both templates inspected locally. Only the active vLLM unit is started this round.
        for path in ['deploy/gpu-lab/host/byul-observation-feed.service','deploy/gpu-lab-sglang/byul-observation-feed.service']:
            require('--target-namespace serving-lab' in (self.root/path).read_text(),'TEMPLATE_ARGUMENT_MISSING')
        from datetime import datetime,timezone
        for path in ['target-pods.json','hubble.heartbeat.json','tetragon.heartbeat.json']:
            d=json.loads((Path('/srv/serving/observation')/path).read_text())
            at=d.get('collected_at') or d.get('updated_at')
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(at.replace('Z','+00:00'))).total_seconds()
            require(0<=age<=15 and d.get('state') in {'ok','running'},'FEED_NOT_FRESH_HEALTHY')
            save(self.out/path,d)
        logs=self.command(['journalctl','--user','-u','byul-observation-feed','--since',self.c['round_started_at'],'--no-pager'])
        require(not any(x in logs.lower() for x in ['unrecognized arguments','argumenterror','invalid choice']),'SERVICE_ARGUMENT_ERROR')
    def R_03(self):
        env=os.environ.copy();env.update(self.tokens)
        env['PYTHONPATH']=os.pathsep.join([str(self.root),str(self.root/'src'),str(self.root.parent/'update/src'),
                                         str(self.root.parent/'platform/api/src')])
        argv=[self.python,'-B','-m','labtools.selected_runtime_check','requests','--output',str(self.out/'matrix.json'),
              '--model','test-model','--gateway-url',self.gateway,
              '--inference-token-env','RUNTIME_INFERENCE_TOKEN','--viewer-token-env','RUNTIME_VIEWER_TOKEN']
        self.command_log.append(shlex.join(argv))
        code,text=run(argv,timeout=460,cwd=self.root,env=env);self.output_log.append(text)
        d=json.loads((self.out/'matrix.json').read_text())
        require(code==0 and len(d['cases'])==31 and all(x['result']=='passed' for x in d['cases']),'MATRIX_NOT_31_OF_31')
    def R_04(self):
        self.acceptance(['AWS-01','AWS-02','AWS-03','AWS-04','AWS-05','AWS-06','AWS-12'],['--allow-compare-policy'])
    def R_05(self):
        d=self.acceptance(['AWS-04','AWS-05','AWS-09'])
        facts=d['AWS-04']['facts']
        require(facts.get('direct_failed') is True and facts.get('tokenize_direct_failed') is True and
                facts.get('hubble_dropped') is True and facts.get('gateway_tcp_control') is True and
                facts.get('gateway_control_200') is True,'DIRECT_TOKENIZE_STRICT_CRITERIA')
        # Additional normal request before/after is owned by the round runner.
    def R_06(self):
        intent=json.loads(Path(self.c['resolved_intent']).read_text())
        intent.pop('tetragon_kernel_identity',None)
        path=self.out/'intent-default.json';save(path,intent)
        output=self.out/'render'
        self.command([self.python,'-m','serving_runtime','enforce','render','--intent',str(path),'--out',str(output)])
        from serving_runtime.enforcement.attribution import KERNEL_DATA
        paths=list(output.rglob('tetragon-tg-code-01.json'))
        require(len(paths)==1,'TG_CODE_RENDER_MISSING')
        manifest=json.loads(paths[0].read_text())
        require(all(x.get('data')==KERNEL_DATA for x in manifest['spec']['kprobes']),
                'KERNEL_IDENTITY_DATA_MISSING')
    def raw_code_count(self,start,end):
        from datetime import datetime
        count=0;scanned=0;window=[]
        for line in Path(self.c['raw_tetragon_file']).read_text().splitlines():
            d=json.loads(line);stamp=d.get('time') or d.get('timestamp')
            if not stamp:continue
            stamp=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
            if not start<=stamp<=end:continue
            window.append(d)
            scanned+=1
            if any('tg-code' in str(x.get('policy_name',x.get('policyName',''))).lower() for x in flatten(d)):count+=1
        require(scanned>0,'RAW_TETRAGON_WINDOW_HAS_NO_EVENTS')
        p=self.out/'raw-tetragon-window.jsonl'
        p.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in window));p.chmod(0o600)
        return count,scanned
    def R_07(self):
        started=time.time()
        for _ in range(60):self.normal()
        ended=time.time();time.sleep(8)
        count,scanned=self.raw_code_count(started,ended)
        save(self.out/'window.json',{'normal_requests':60,'raw_code_events':count,'raw_events_scanned':scanned,
                                   'started_epoch':started,'ended_epoch':ended})
        require(count==0,'TG_CODE_FALSE_POSITIVE')
    def R_08(self):
        probe='/cache/hf/modules/byul_wp7_'+uuid.uuid4().hex+'.py'
        code="import importlib.util,pathlib,time; p=pathlib.Path("+repr(probe)+");time.sleep(12);p.parent.mkdir(parents=True,exist_ok=True);p.write_text('VALUE=7\\n');s=importlib.util.spec_from_file_location('byul_wp7',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);assert m.VALUE==7;p.unlink()"
        self.normal()
        self.kube('exec','deployment/serving-engine','-c','engine','--','python3','-c',code)
        def found(d):
            return any(x.get('code_load_verdict')=='detected' and
                       x.get('phase')==['inferring'] and
                       (x.get('identity') or {}).get('state')=='confirmed' and probe in json.dumps(x)
                       for x in flatten(d))
        self.wait_api('/api/observations?source=tetragon',found)
    def R_10(self):
        normal=self.normal();doc=json.loads(normal['body'])
        choices=doc.get('choices',[]);usage=doc.get('usage')
        content=''.join(str((x.get('message') or {}).get('content') or '') for x in choices)
        require(bool(content.strip()),'NORMAL_CONTENT_EMPTY')
        require(isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>0
                    for k in ['prompt_tokens','completion_tokens','total_tokens']),'USAGE_VALUES_INVALID')
        require(usage['total_tokens']==usage['prompt_tokens']+usage['completion_tokens'],'USAGE_TOTAL_MISMATCH')
        normal_id=next(v for k,v in normal['headers'].items() if k.lower()=='x-request-id')
        require(self.detail(normal_id).get('final_outcome')=='completed','NORMAL_FINAL_NOT_COMPLETED')
        response=self.api('/v1/chat/completions',body=self.chat(True))
        require(response['status']==200 and 'data: [DONE]' in response['body'],'STREAM_DONE_MISSING')
        chunks=[json.loads(x[6:]) for x in response['body'].splitlines() if x.startswith('data: ') and x!='data: [DONE]']
        streamed=''.join(str((c.get('delta') or {}).get('content') or '') for x in chunks for c in x.get('choices',[]))
        require(bool(streamed.strip()),'STREAM_CONTENT_EMPTY')
        rid=next(v for k,v in response['headers'].items() if k.lower()=='x-request-id')
        require(self.detail(rid).get('final_outcome')=='completed','STREAM_FINAL_NOT_COMPLETED')
        save(self.out/'response-values.json',{'normal_content':content,'usage':usage,'normal_request_id':normal_id,
             'stream_content':streamed,'stream_DONE':True,'stream_request_id':rid,'both_final_outcomes':'completed'})
    def R_11(self):
        self.acceptance(['AWS-07','AWS-08'],['--allow-collector-stop','--allow-engine-restart'])
        self.fresh_gateway();self.services()
    def R_12(self):
        self.acceptance(['AWS-13'],['--allow-bundle-switch'],privileged=True)
        self.fresh_gateway()
        self.acceptance(['AWS-14','AWS-15','AWS-16','AWS-17'],
                        ['--allow-gateway-restart','--allow-collector-stop','--allow-engine-restart'])
        self.fresh_gateway();self.services()
    def R_13(self):
        # AWS-11 requires 09+10 in the same process for its exact before/after window.
        self.acceptance(['AWS-09','AWS-10','AWS-11','AWS-18','AWS-19'])
    @contextlib.contextmanager
    def lab_bundle(self,settings):
        """Explicit LAB self-approval test generation; preserve the original pointer."""
        root=Path(self.c.get('bundle_root','/srv/serving/bundles'))
        previous=self.command(['sudo','readlink',str(root/'current')]).strip()
        require(Path(previous).name==previous and previous not in {'','.','..'},'CURRENT_NOT_RELATIVE_BUNDLE')
        ident='wp7-lab-'+uuid.uuid4().hex
        # Run as root because the original bundle root intentionally stays 2750.
        script="""
import json,pathlib,shutil,hashlib,os,sys
root=pathlib.Path(sys.argv[1]);old=sys.argv[2];new=sys.argv[3];settings=json.loads(sys.argv[4])
p=root/new;shutil.copytree(root/old,p);m=json.loads((p/'bundle.json').read_text())
for entry in m['files']:
 if entry['role']=='target':
  f=p/entry['path'];d=json.loads(f.read_text());d['launch_settings']=settings
  f.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\\n')
  entry['bytes']=f.stat().st_size;entry['sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
m['bundle_id']=new
(p/'bundle.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\\n')
for f in p.rglob('*'):
 os.chown(f,0,10000);os.chmod(f,0o750 if f.is_dir() else 0o640)
os.chown(p,0,10000);os.chmod(p,0o750)
"""
        self.command(['sudo',self.python,'-c',script,str(root),previous,ident,json.dumps(settings)])
        def switch(target):
            self.command(['sudo',self.python,'-m','serving_runtime','bundle','verify','--bundle-dir',str(root/target)])
            # Never overwrite another operator's incomplete link.
            self.command(['sudo','ln','-s',target,str(root/'current.wp7-next')])
            self.command(['sudo','mv','-Tf',str(root/'current.wp7-next'),str(root/'current')])
            self.kube('rollout','restart','deployment/serving-gateway')
            self.kube('rollout','status','deployment/serving-gateway','--timeout=180s',timeout=190)
            self.fresh_gateway()
        try:
            switch(ident);yield
        finally:
            try:switch(previous)
            except Exception as e:raise RecoveryFailure('LAB_BUNDLE_ROLLBACK_FAILED') from e
            # Keep the LAB test generation for diagnosis until the final cleanup decision.
    def launch_rows(self):
        return json.loads(self.api('/api/baseline')['body'])['rows']
    def assert_launch(self,mm_state,remote_state):
        def correct(d):
            rows={x['field']:x for x in d.get('rows',[])}
            return all(rows.get('launch_settings.'+key,{}).get('state')==state
                       for key,state in [('trust_request_mm_kwargs',mm_state),('trust_remote_code',remote_state)])
        return self.wait_api('/api/baseline',correct)
    def flag(self,d,flag):
        containers=d['spec']['template']['spec']['containers']
        engine=next(x for x in containers if x['name']=='engine')
        require(not any(str(x).startswith('--config') for x in engine.get('args',[])),'WRAPPER_CONFIG_NEEDS_REVIEW')
        require(flag not in engine.get('args',[]),'FLAG_ALREADY_PRESENT')
        engine.setdefault('args',[]).append(flag)
    def R_09(self):
        with self.lab_bundle({'trust_remote_code':False,'trust_request_mm_kwargs':False}):
            self.assert_launch('match','match')
            with self.deployment('serving-engine') as d:
                self.flag(d,'--trust-request-mm-kwargs');self.set_template(d,'serving-engine')
                changed=self.assert_launch('drift','match')
                row=next(x for x in changed['rows'] if x['field']=='launch_settings.trust_request_mm_kwargs')
                require(row['expected'] is False and row['observed'] is True,'D7_EXPECTED_FALSE_OBSERVED_TRUE')
                self.wait_api('/api/revalidation',lambda d:any(
                    x.get('trigger')=='config_drift' and x.get('current_evidence_present') is True and
                    'launch_settings.trust_request_mm_kwargs' in json.dumps(x) for x in flatten(d)))
                self.normal()
            self.assert_launch('match','match');self.normal()
    def N_05(self):
        with self.lab_bundle({'trust_request_mm_kwargs':False}):
            def zero(d):return d.get('current_condition_count')==0
            self.wait_api('/api/revalidation',zero)
            with self.deployment('serving-engine') as d:
                self.flag(d,'--trust-remote-code');self.set_template(d,'serving-engine')
                changed=self.assert_launch('match','unapproved')
                row=next(x for x in changed['rows'] if x['field']=='launch_settings.trust_remote_code')
                require(row.get('observed') is True and row.get('deviates_from_default') is True,'D8_TRUE_NOT_OBSERVED')
                self.wait_api('/api/revalidation',lambda d:any(
                    x.get('trigger')=='config_drift' and x.get('current_evidence_present') is True and
                    'launch_settings.trust_remote_code' in json.dumps(x) for x in flatten(d)))
            self.wait_api('/api/revalidation',zero);self.normal()
    def N_04(self):
        # Existing AWS-04 probe logic, reused for direct denial while Gateway is down.
        from labtools.aws_lab_acceptance import AWSAcceptance,parse_args,TOKENIZE_PROBE_CODE
        args=parse_args(['--kube-context',self.context,'--token-env-file',self.c['token_env_file'],
                         '--gateway-url',self.gateway,'--cases','AWS-04','--evidence-dir',str(self.out/'up')])
        accept=AWSAcceptance(args)
        from labtools.aws_lab_acceptance import evaluate_case
        facts,evidence=accept.aws_04()
        save(self.out/'gateway-up-direct-control.json',{'facts':facts,'evidence':evidence})
        require(evaluate_case('AWS-04',facts)[0]=='passed','DIRECT_PRECONTROL_FAILED')
        replicas=int(json.loads(self.kube('get','deployment','serving-gateway','-o','json',private=True))['spec']['replicas'])
        require(replicas==1,'EXPECTED_SINGLE_GATEWAY')
        try:
            self.kube('scale','deployment/serving-gateway','--replicas=0')
            self.kube('wait','--for=delete','pod','-l','app=serving-gateway','--timeout=90s',timeout=100)
            tcp=accept._tcp_probe('pod/other-client','client','serving-engine',8000)
            token=accept._tcp_probe('pod/other-client','client','serving-engine',8000,probe_code=TOKENIZE_PROBE_CODE)
            save(self.out/'gateway-down-probes.json',{'tcp':tcp,'tokenize':token})
            require(tcp['connection_timed_out'] is True and token['connection_timed_out'] is True,'GATEWAY_DOWN_DIRECT_REACHABLE')
        finally:
            try:
                self.kube('scale','deployment/serving-gateway','--replicas='+str(replicas))
                self.kube('rollout','status','deployment/serving-gateway','--timeout=180s',timeout=190)
                self.fresh_gateway()
            except Exception as e:raise RecoveryFailure('GATEWAY_SCALE_RESTORE_FAILED') from e
            accept._close_forward()
    @contextlib.contextmanager
    def direct_cli(self):
        # Own a separate local listener/data directory, sharing the real engine.
        with tempfile.TemporaryDirectory(prefix='wp7-direct-') as temp:
            directory=Path(temp)
            root=Path(self.c.get('bundle_root','/srv/serving/bundles'))
            m=json.loads(self.command(['sudo','cat',str(root/'current/bundle.json')],private=True))
            roles={}
            for entry in m['files']:
                if entry['role'] in {'target','gateway_policy','policy_approvals'}:
                    value=json.loads(self.command(['sudo','cat',str(root/'current'/entry['path'])],private=True))
                    target=directory/(entry['role']+'.json');save(target,value);roles[entry['role']]=str(target)
            auth={'schema_version':'runtime-auth/1.0','auth_ref':'WP7-LAB',
                  'principals':[{'principal_id':'wp7-'+role,'roles':[role],
                    'token_sha256':hashlib.sha256(self.tokens[key].encode()).hexdigest()}
                    for role,key in [('viewer','RUNTIME_VIEWER_TOKEN'),('inference','RUNTIME_INFERENCE_TOKEN')]],
                  'route_roles':{'POST /v1/chat/completions':['inference'],'GET /api/*':['viewer']}}
            save(directory/'auth.json',auth)
            # The collector-only CLI is not used to send inference, so mock backend
            # avoids a host-to-engine path outside NetworkPolicy.
            argv=[self.python,'-m','serving_runtime','mvp','--backend','mock','--gateway-port','18083',
                  '--engine-port','18084','--policy',roles['gateway_policy'],'--policy-approvals',roles['policy_approvals'],
                  '--target',roles['target'],'--auth-config',str(directory/'auth.json'),
                  '--data-dir',str(directory/'records'),'--cilium-policy-status','--kube-context',self.context]
            self.command_log.append(shlex.join(argv))
            child=subprocess.Popen(argv,cwd=self.root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:yield argv
            finally:
                child.terminate()
                try:child.wait(timeout=20)
                except subprocess.TimeoutExpired:child.kill();child.wait()
    def N_02(self):
        with self.direct_cli() as argv:
            self.wait_api_direct('http://localhost:18083')
            bad=list(argv);bad[bad.index('--gateway-port')+1]='18085';bad[bad.index('--engine-port')+1]='18086'
            bad[bad.index('--kube-context')+1]='wp7-intentionally-wrong'
            code,text=run(bad,timeout=20,cwd=self.root)
            self.command_log.append(shlex.join(bad));self.output_log.append(text)
            require(code not in {0,124} and 'context' in text.lower(),'CONTEXT_MISMATCH_NOT_REJECTED')
    def wait_api_direct(self,base):
        end=time.monotonic()+45
        while time.monotonic()<end:
            try:
                d=json.loads(self.api('/api/collectors',base=base)['body'])
                sources=[x for x in d.get('collectors',[]) if x.get('source')=='cilium_policy']
                require(any(x.get('state')=='ok' and (x.get('last_value') or {}).get('object_count',0)>0 and
                            (x.get('last_value') or {}).get('policies') for x in sources),'CILIUM_VALUES_EMPTY')
                return
            except (OSError,CheckFailure,http.client.HTTPException):time.sleep(2)
        raise CheckFailure('DIRECT_CLI_CILIUM_NOT_CONFIRMED')
    def change_gateway_limit(self,d,flag,value):
        c=next(x for x in d['spec']['template']['spec']['containers'] if x['name']=='gateway')
        args=c['args']
        if flag in args:args[args.index(flag)+1]=str(value)
        else:args.extend([flag,str(value)])
    def N_06(self):
        with self.deployment('serving-gateway') as d:
            self.change_gateway_limit(d,'--max-concurrency',1);self.set_template(d,'serving-gateway')
            import threading
            barrier=threading.Barrier(3)
            def send():
                barrier.wait();return self.api('/v1/chat/completions',body=self.chat(True,256))
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(send) for _ in range(2)];barrier.wait()
                replies=[f.result(timeout=70) for f in futures]
            require(sorted(x['status'] for x in replies)==[200,429],'PARALLEL_STREAMS_DID_NOT_PROVE_OVERLAP')
            denied=next(x for x in replies if x['status']==429)
            require(any(k.lower()=='retry-after' and v=='1' for k,v in denied['headers'].items()),'RETRY_AFTER_NOT_ONE')
            rid=next(v for k,v in denied['headers'].items() if k.lower()=='x-request-id')
            final=self.detail(rid)
            require(final['decision']['reason_code']=='CONCURRENCY_LIMIT','CONCURRENCY_REASON_MISMATCH')
            require(final.get('stream') is True,'STREAM_429_RECORD_NOT_TRUE')
            self.normal()
            self.wait_api('/api/status',lambda d:d.get('concurrency',{}).get('in_flight')==0,limit=10)
            disconnect_url=self.gateway
            if getattr(self,'c',{}).get('disconnect_direct_gateway_tcp'):
                # kubectl port-forward terminates the client TCP connection itself.
                # Reset the actual Gateway TCP connection for the disconnect probe.
                deployment=json.loads(self.kube('get','deployment','serving-gateway','-o','json',private=True))
                selector=','.join(k+'='+v for k,v in sorted(deployment['spec']['selector']['matchLabels'].items()))
                pods=json.loads(self.kube('get','pods','-l',selector,'-o','json',private=True))['items']
                ready=[p for p in pods if not p['metadata'].get('deletionTimestamp') and
                       p['status'].get('phase')=='Running' and
                       any(x.get('type')=='Ready' and x.get('status')=='True' for x in p['status'].get('conditions',[]))]
                require(len(ready)==1,'EXACTLY_ONE_READY_GATEWAY_POD_REQUIRED')
                disconnect_url='http://'+ready[0]['status']['podIP']+':8080'
            u=urlsplit(disconnect_url);conn=http.client.HTTPConnection(u.hostname,u.port,timeout=60);response=None
            try:
                conn.request('POST','/v1/chat/completions',json.dumps(self.chat(True,256)),
                    {'Content-Type':'application/json','Authorization':'Bearer '+self.tokens['RUNTIME_INFERENCE_TOKEN']})
                response=conn.getresponse()
                save(self.out/'disconnect-response-start.json',{'status':response.status,'headers':dict(response.getheaders()),
                     'observed_at':now(),'direct_gateway_tcp':bool(getattr(self,'c',{}).get('disconnect_direct_gateway_tcp')),'body_if_not_200':response.read().decode(errors='replace') if response.status!=200 else None})
                require(response.status==200,'DISCONNECT_STREAM_NOT_STARTED')
                rid=response.getheader('X-Request-ID')
                line=response.readline();require(bool(line),'DISCONNECT_STREAM_EMPTY')
                # RST closes the actual client socket while the upstream stream is active.
                import struct
                actual_socket=response.fp.raw._sock
                actual_socket.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
                response.close();actual_socket.close()
                save(self.out/'disconnect-closed.json',{'closed_at':now(),'request_id':rid,'first_line_seen':bool(line)})
            finally:
                if response is not None:response.close()
                conn.close()
            final=self.detail(rid)
            require(final.get('final_outcome')=='client_disconnected','CLIENT_DISCONNECT_NOT_OBSERVED')
            self.normal()
    def stream_probe(self,body):
        u=urlsplit(self.gateway);conn=http.client.HTTPConnection(u.hostname,u.port,timeout=65)
        response=None;started=now();begin=time.monotonic();first_at=None;first_elapsed=None;frames=[];error=None
        try:
            conn.request('POST','/v1/chat/completions',json.dumps(body),
                {'Content-Type':'application/json','Authorization':'Bearer '+self.tokens['RUNTIME_INFERENCE_TOKEN']})
            response=conn.getresponse();status=response.status;rid=response.getheader('X-Request-ID')
            try:
                while True:
                    line=response.readline()
                    if not line:break
                    text=line.decode('utf-8');frames.append(text)
                    if text.startswith('data: ') and text.strip()!='data: [DONE]':
                        chunk=json.loads(text[6:])
                        if any((x.get('delta') or {}).get('content') for x in chunk.get('choices',[])) and first_at is None:
                            first_at=now();first_elapsed=time.monotonic()-begin
            except (http.client.IncompleteRead,ConnectionError,TimeoutError,OSError) as exc:error=type(exc).__name__
            result={'status':status,'request_id':rid,'request_started_at':started,'first_content_at':first_at,
                    'first_content_elapsed_s':first_elapsed,'ended_at':now(),'elapsed_s':time.monotonic()-begin,
                    'stream_started':status==200 and first_at is not None,'body':''.join(frames),'transport_error':error}
            save(self.out/('stream-timing-'+uuid.uuid4().hex+'.json'),result);return result
        finally:
            if response is not None:response.close()
            conn.close()
    def N_07(self):
        original=json.loads(self.kube('get','configmap','serving-gateway-backend','-o','json',private=True))
        before=copy.deepcopy(original['data']);outcomes=[]
        def settings(idle,deadline):
            data=copy.deepcopy(before);backend=json.loads(data['backend.json'])
            backend['timeouts']['stream_idle_timeout_s']=idle;backend['timeouts']['request_deadline_s']=deadline
            data['backend.json']=json.dumps(backend)
            self.kube('patch','configmap','serving-gateway-backend','--type=merge','--patch-file=/dev/stdin',input=json.dumps({'data':data}),private=True)
            self.kube('rollout','restart','deployment/serving-gateway')
            self.kube('rollout','status','deployment/serving-gateway','--timeout=180s',timeout=190)
            if self.forward:self.forward.terminate();self.forward.wait(timeout=10);self.forward=None
            port=urlsplit(self.gateway).port
            self.forward=subprocess.Popen(['kubectl','--context',self.context,'-n',self.ns,'port-forward',
                'deployment/serving-gateway',str(port)+':8080'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            # Pod snapshot writer + Gateway reader each use a five-second cycle.
            time.sleep(12)
        try:
            for reason,candidates in [('idle_timeout',[(.1,60),(.03,60),(.01,60)]),
                                      ('deadline_exceeded',[(30,1.0),(30,.5),(30,.25)])]:
                observations=[];stable=False
                for idle,deadline in candidates:
                    settings(idle,deadline);matched=0
                    for _ in range(2):
                        reply=self.stream_probe(self.chat(True,256));rid=reply.get('request_id')
                        final=self.detail(rid) if rid and reply['stream_started'] else {}
                        actual=final.get('final_outcome');reason_code=(final.get('decision') or {}).get('reason_code')
                        valid=reply['stream_started'] and reason in {actual,reason_code}
                        observations.append({'settings':{'idle':idle,'deadline':deadline},'timing':reply,
                                             'final_record':final,'after_stream_start_timeout':valid})
                        if not valid:break
                        matched+=1
                    if matched==2:stable=True;break
                outcomes.append({'case':reason,'result':'passed' if stable else 'held',
                                 'criteria':'Two consecutive timeouts after actual content SSE starts','observations':observations})
            save(self.out/'timeout-after-stream-start.json',{'cases':outcomes})
        finally:
            try:
                self.kube('patch','configmap','serving-gateway-backend','--type=merge','--patch-file=/dev/stdin',input=json.dumps({'data':before}),private=True)
                self.kube('rollout','restart','deployment/serving-gateway')
                self.kube('rollout','status','deployment/serving-gateway','--timeout=180s',timeout=190)
                after=json.loads(self.kube('get','configmap','serving-gateway-backend','-o','json',private=True))
                require(after['data']==before,'TIMEOUT_CONFIG_RESTORE_MISMATCH');self.fresh_gateway()
            except Exception as e:raise RecoveryFailure('TIMEOUT_CONFIG_RESTORE_FAILED') from e
        if any(x['result']=='held' for x in outcomes):raise JudgmentDeferred('VLLM_AFTER_START_TIMEOUT_NOT_STABLY_REPRODUCED')
