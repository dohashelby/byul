"""Regression checks for the seven execution-review findings; no AWS access."""
import argparse,copy,hashlib,io,json,re,shutil,tarfile,tempfile,uuid
from pathlib import Path
from unittest.mock import patch
from server_checks import Live,CheckFailure,JudgmentDeferred

def run():
    checks=[]
    from rehearsal_checks import FakeLive,Process
    with tempfile.TemporaryDirectory() as t,patch('server_checks.subprocess.Popen',Process),patch('server_checks.time.sleep',lambda _:None):
        live=FakeLive(Path(t),'nostart')
        try:live.N_07()
        except JudgmentDeferred:pass
        else:raise AssertionError('Timeout before stream start accepted')
        assert live.backend==live.original
        checks.append('N07 pre-stream timeout held and original settings restored')
    class Responses(Live):
        def __init__(self,out,bad=None):self.out=out;self.bad=bad
        def normal(self):
            usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}
            if self.bad=='zero':usage['completion_tokens']=0
            if self.bad=='sum':usage['total_tokens']=6
            if self.bad=='type':usage['prompt_tokens']='3'
            return {'status':200,'headers':{'X-Request-ID':'normal'},'body':json.dumps({'choices':[{'message':{'content':'' if self.bad=='content' else 'answer'}}],'usage':usage})}
        def api(self,*args,**kwargs):
            content='' if self.bad=='stream' else 'answer'
            return {'status':200,'headers':{'X-Request-ID':'stream'},'body':'data: '+json.dumps({'choices':[{'delta':{'content':content}}]})+'\n\n'+('' if self.bad=='done' else 'data: [DONE]\n\n')}
        def detail(self,rid):return {'final_outcome':'failed' if self.bad=='completed' else 'completed'}
    with tempfile.TemporaryDirectory() as t:
        for bad in [None,'zero','sum','type','content','stream','done','completed']:
            out=Path(t)/str(bad);out.mkdir();live=Responses(out,bad)
            try:live.R_10()
            except CheckFailure:
                if bad is None:raise
            else:assert bad is None,'R10 invalid response accepted: '+str(bad)
        checks.append('R10 real content/positive numeric usage/DONE/completed with seven negative probes')
    import prepare_lab_bundle as lab
    source=Path(__file__).resolve().parents[4]/'contracts/examples/bundle'
    for failure in ['query','patch_after_apply','late_record',None]:
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);shutil.copytree(source,root/'old');(root/'current').symlink_to('old')
            args=['--policy-bundle','/etc/serving/bundles/old'];initial=list(args);calls=[]
            def kube(*argv):
                calls.append(argv)
                if argv[0]=='get':
                    if failure=='query':raise ValueError('QUERY_FAILED')
                    return json.dumps({'spec':{'template':{'spec':{'containers':[{'name':'gateway','args':list(args)}]}}}})
                operations=json.loads(argv[-1]);assert operations[0]['value']==args;args[:]=operations[1]['value']
                if failure=='patch_after_apply' and len([x for x in calls if x[0]=='patch'])==1:raise ValueError('PATCH_RESPONSE_LOST')
                return ''
            write=Path.write_text
            def write_text(p,*a,**kw):
                if failure=='late_record' and p.name.startswith('lab-generation-'):raise OSError('RECORD_FAILED')
                return write(p,*a,**kw)
            with patch.object(lab.os,'chown'),patch.object(Path,'write_text',write_text):
                try:lab.activate({}, {'expires_at':'2026-11-15T22:00:00+09:00'},kube,root,root)
                except (ValueError,OSError):assert failure is not None
                else:assert failure is None
            assert calls[0][0]=='get'
            if failure:
                assert (root/'current').readlink()==Path('old') and args==initial
                if failure=='query':assert not list(root.glob('wp7-approved-*'))
            else:assert (root/'current').readlink()!=Path('old')
    checks.append('LAB query first, pointer last, rollback on query/partial patch/late failure')
    import execute_from_mac as mac
    for mode in ['success_after_diagnostic','bootstrap_failure','export_failure']:
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);art=root/'art';art.mkdir();hashes={}
            for name in ['serving-gateway-rt-7741687d.tar','byul-7741687d.bundle']:
                (art/name).write_text('fixture');hashes[name]=hashlib.sha256((art/name).read_bytes()).hexdigest()
            (art/'build.json').write_text(json.dumps(hashes));cfg=root/'server.json';cfg.write_text('{}')
            c={'aws_profile':'fixture','budget_name':'fixture','ssh_alias':'fixture','remote_dir':'/home/ubuntu/wp7-fixture','server_checkout':'/home/ubuntu/checkout','server_config_file':str(cfg),'instance_id':'fixture-instance'}
            a=argparse.Namespace(round=1,approval_file='fixture',config='fixture',ledger=str(root/'ledger.json'),artifacts=str(art),output=str(root/'download'))
            calls=[];timer_calls=0;archive=root/'fixture.tgz';remote_evidence=None
            def make_archive(prefix):
                with tarfile.open(archive,'w:gz') as tar:
                    raw=json.dumps({'items':[]}).encode();item=tarfile.TarInfo(prefix+'/round-summary.json');item.size=len(raw);tar.addfile(item,io.BytesIO(raw))
                return hashlib.sha256(archive.read_bytes()).hexdigest()
            def aws(config,*argv,**kw):
                calls.append(argv)
                if argv[:2]==('ec2','describe-instances'):return {'Reservations':[{'Instances':[{'State':{'Name':'running'}}]}]}
                return {}
            def command(argv,**kw):
                nonlocal timer_calls,remote_evidence
                if argv[0]=='scp':
                    if str(argv[-1]).endswith('.tgz'):shutil.copy2(archive,argv[-1])
                    return 0,''
                text=argv[-1]
                if 'sudo shutdown -h +' in text:
                    timer_calls+=1
                    if timer_calls==1:return 1,'synthetic SSH failure'
                if 'bootstrap_server.py' in text and mode=='bootstrap_failure':return 1,'bootstrap failed'
                if 'nohup sh -c' in text:
                    remote_evidence=re.search(r'--evidence ([^ ]+)',text)[1]
                if text.startswith('if test -f '):
                    if mode=='export_failure':return 0,json.dumps({'export_failed':True})
                    sha=make_archive(Path(remote_evidence+'-export').name);return 0,json.dumps({'archive_sha256':sha})
                if 'failure_export.py' in text:
                    sha=make_archive('failure-export');return 0,json.dumps({'archive':'/home/ubuntu/wp7-fixture/failure-export.tgz','archive_sha256':sha})
                return 0,''
            with patch.object(mac,'approval',return_value={'rounds':[1]}),patch.object(mac,'private_json',return_value=c),patch.object(mac,'budget'),patch.object(mac,'choose'),patch.object(mac,'aws',side_effect=aws),patch.object(mac,'run',side_effect=command),patch.object(mac,'inventory',return_value={'compute_stopped':True}),patch.object(mac.time,'sleep'):
                try:mac.execute(a)
                except ValueError:assert mode!='success_after_diagnostic'
                else:assert mode=='success_after_diagnostic'
            diag=Path(str(a.output)+'-diagnostics');assert list(diag.glob('remote-failure-*'))
            assert (diag/'cleanup.json').exists()
            if mode=='success_after_diagnostic':assert (Path(a.output)/'round-export.tgz').exists()
            else:assert not Path(a.output).exists();assert list(diag.glob('server-failure-*.tgz'))
            assert any(x[:2]==('ec2','stop-instances') for x in calls)
            assert any(x[:3]==('ec2','wait','instance-stopped') for x in calls)
    checks.append('launcher diagnostic/download separation, setup log recovery, immediate export-failure stop')
    return {'checks':checks,'passed':len(checks),'AWS_commands':0}
if __name__=='__main__':print(json.dumps(run(),indent=2))
