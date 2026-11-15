"""Fake-input checks of the live N-06/N-07 scripts, including rollback."""
import contextlib,copy,json,tempfile,threading,types,uuid
from pathlib import Path
from unittest.mock import patch
from server_checks import Live,CheckFailure

class Socket:
    def setsockopt(self,*_):pass
    def close(self):pass
class Response:
    def __init__(self,sock):self.fp=types.SimpleNamespace(raw=types.SimpleNamespace(_sock=sock))
    def getheader(self,_):return 'stream'
    def getheaders(self):return [('X-Request-ID','stream')]
    @property
    def status(self):return 200
    def readline(self):return b'data: {"choices":[]}\n'
    def close(self):pass
class Connection:
    def __init__(self,*_,**__):self.sock=Socket()
    def request(self,*_):pass
    def getresponse(self):
        response=Response(self.sock);self.sock=None;return response
    def close(self):pass
class Process:
    def __init__(self,*_,**__):pass
    def terminate(self):pass
    def wait(self,**_):pass

class FakeLive(Live):
    def __init__(self,out,mutation=None):
        self.out=out/uuid.uuid4().hex;self.out.mkdir();self.gateway='http://localhost:18080';self.context='byul-aws-lab';self.ns='serving-lab'
        self.tokens={'RUNTIME_INFERENCE_TOKEN':'synthetic-fixture'};self.forward=None
        self.command_log=[];self.output_log=[];self.mutation=mutation;self.lock=threading.Lock()
        self.sent=0;self.normal_count=0;self.restored=0
        self.backend={'backend.json':json.dumps({'timeouts':{'stream_idle_timeout_s':30,'request_deadline_s':300}})}
        self.original=copy.deepcopy(self.backend)
    @contextlib.contextmanager
    def deployment(self,_):
        try:yield {'spec':{'template':{'spec':{'containers':[{'name':'gateway','args':[]}]}}}}
        finally:self.restored+=1
    def set_template(self,*_):pass
    def fresh_gateway(self):pass
    def normal(self):self.normal_count+=1;return {'status':200}
    def api(self,path,**_):
        if path=='/api/status':return {'status':200,'body':json.dumps({'concurrency':{'in_flight':0}})}
        if self.backend!=self.original:
            timeout=json.loads(self.backend['backend.json'])['timeouts']
            self.expected='idle_timeout' if timeout['stream_idle_timeout_s']<1 else 'deadline_exceeded'
            return {'status':200,'headers':{'X-Request-ID':'timeout'},'body':''}
        with self.lock:self.sent+=1;which=self.sent
        return {'status':200 if which==1 else 429,'headers':{'X-Request-ID':'allowed' if which==1 else 'denied',
                    'Retry-After':'2' if self.mutation=='retry' else '1'},'body':''}
    def stream_probe(self,body):
        timeout=json.loads(self.backend['backend.json'])['timeouts']
        self.expected='idle_timeout' if timeout['stream_idle_timeout_s']<1 else 'deadline_exceeded'
        return {'status':200,'request_id':'timeout','stream_started':self.mutation!='nostart',
                'first_content_at':'2026-11-14T00:00:00+00:00','ended_at':'2026-11-14T00:00:01+00:00'}
    def detail(self,rid):
        if rid=='denied':
            return {'decision':{'reason_code':'CONCURRENCY_LIMIT'},'stream':self.mutation!='stream'}
        if rid=='stream':
            return {'final_outcome':'completed' if self.mutation=='disconnect' else 'client_disconnected'}
        return {'final_outcome':'completed' if self.mutation=='timeout' else self.expected}
    def kube(self,*args,**kwargs):
        if args[:2]==('rollout','restart') and any(str(x).startswith('--timeout') for x in args[2:]):
            raise CheckFailure('UNSUPPORTED_KUBECTL_RESTART_TIMEOUT')
        if args[:2]==('get','configmap'):return json.dumps({'data':copy.deepcopy(self.backend)})
        if args[:2]==('patch','configmap'):
            self.backend=json.loads(kwargs['input'])['data']
        return ''

def check_live_script_logic():
    probes=0
    with tempfile.TemporaryDirectory() as temp:
        with patch('server_checks.http.client.HTTPConnection',Connection):
            good=FakeLive(Path(temp));good.N_06()
            assert good.normal_count==2 and good.restored==1
            for mutation in ['retry','stream','disconnect']:
                f=FakeLive(Path(temp),mutation)
                try:f.N_06()
                except CheckFailure:pass
                else:raise AssertionError('N06 negative input passed')
                assert f.restored==1;probes+=1
        with patch('server_checks.subprocess.Popen',Process),patch('server_checks.time.sleep',lambda _:None):
            good=FakeLive(Path(temp));good.N_07()
            assert good.backend==good.original
            bad=FakeLive(Path(temp),'timeout')
            try:bad.N_07()
            except CheckFailure:pass
            else:raise AssertionError('N07 negative input passed')
            assert bad.backend==bad.original;probes+=1
    return {'N06_script_success':True,'N07_script_success':True,'negative_probes':probes,
            'rollback_on_success_and_failure':True,'network_calls':0}

def check_feed_upgrade():
    from server_checks import upgraded_feed_unit
    original='[Service]\nWorkingDirectory=/old/runtime\nExecStart=/usr/bin/python3 -B tools/k8s_observation_feed.py --kube-context byul-aws-lab --hubble-only --reader-group 10010\n'
    upgraded=upgraded_feed_unit(original)
    assert '--target-namespace serving-lab' in upgraded and '--hubble-only' in upgraded
    assert '--reader-group 10010' in upgraded and '--kube-context byul-aws-lab' in upgraded
    assert upgraded_feed_unit(upgraded)==upgraded
    try:upgraded_feed_unit(original.replace('--hubble-only','--target-namespace another-namespace'))
    except CheckFailure:pass
    else:raise AssertionError('Wrong namespace overwritten')
    return {'existing_scoped_stream_option_preserved':True,'namespace_added':True,'idempotent':True,
            'unexpected_namespace_rejected':True}

def check_mac_failure_stop():
    import argparse,hashlib,itertools
    import execute_from_mac as mac
    import cleanup_check
    with patch.object(cleanup_check,'run',return_value=(0,'')):
        assert cleanup_check.aws({'aws_profile':'offline-fixture'},'ec2','wait','instance-stopped')=={}
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);art=root/'art';art.mkdir()
        expected={}
        for name in ['serving-gateway-rt-7741687d.tar','byul-7741687d.bundle']:
            (art/name).write_text('synthetic fixture only')
            expected[name]=hashlib.sha256((art/name).read_bytes()).hexdigest()
        (art/'build.json').write_text(json.dumps(expected))
        c={'aws_profile':'offline-fixture','budget_name':'offline-fixture','ssh_alias':'offline-fixture',
           'remote_dir':'/home/ubuntu/wp7-fixture','server_checkout':'/home/ubuntu/byul-fixture',
           'server_config_file':'/secure/fixture.json','instance_id':'i-'+'a'*17}
        a=argparse.Namespace(round=1,approval_file='never-created',config='never-created',
                            ledger=str(root/'ledger.json'),artifacts=str(art),output=str(root/'output'))
        calls=[]
        def fake_aws(config,*args,**kwargs):
            calls.append(args)
            if args[:2]==('ec2','describe-instances'):
                return {'Reservations':[{'Instances':[{}]}]}
            return {}
        with patch.object(mac,'approval',return_value={'rounds':[1]}),patch.object(mac,'private_json',return_value=c),\
             patch.object(mac,'budget'),patch.object(mac,'choose',return_value={'BlockDeviceMappings':[]}),\
             patch.object(mac,'aws',side_effect=fake_aws),patch.object(mac,'inventory',return_value={'compute_stopped':True,'resources_zero':False}),\
             patch.object(mac,'run',return_value=(1,'')),patch.object(mac.time,'monotonic',side_effect=itertools.count(0,300).__next__):
            try:mac.execute(a)
            except ValueError:pass
            else:raise AssertionError('Broken SSH accepted')
        assert any(x[:2]==('ec2','start-instances') for x in calls)
        assert any(x[:2]==('ec2','stop-instances') for x in calls)
        assert any(x[:2]==('ec2','wait') and x[2]=='instance-stopped' for x in calls)
        with patch.object(mac,'approval',side_effect=ValueError('NOT_APPROVED')),patch.object(mac,'aws') as aws:
            try:mac.execute(a)
            except ValueError:pass
            else:raise AssertionError('Missing approval accepted')
            assert not aws.called
    return {'approval_rejected_before_any_AWS_call':True,'SSH_failure_triggers_stop_and_confirmation':True,
            'all_cloud_calls_mocked':True,'empty_successful_AWS_output_supported':True,'real_AWS_commands':0}

def check_cilium_and_launch_fake_inputs():
    from types import SimpleNamespace
    from serving_runtime.observation.cilium_status import CiliumPolicyCollector
    from serving_runtime.target.launch_settings import observe,comparison_rows
    from serving_runtime.policy.revalidation import evaluate_config_drift
    calls=[]
    def runner(argv,**kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0,stdout=json.dumps({'items':[{
            'metadata':{'name':'runtime-engine-ingress'},'status':{'nodes':{'fixture':{'enforcing':True}}}}]}))
    value,origin=CiliumPolicyCollector(timeout_s=2,context='byul-aws-lab',namespace='serving-lab',runner=runner).collect()
    assert value['object_count']==1 and value['policies'][0]['node_status']['enforcing']==1
    assert calls[0][1:3]==['--context','byul-aws-lab'] and origin=='kubernetes_api'
    counts=[]
    for flag in [False,True,False]:
        snapshot={'state':'ok','last_value':{'launch_settings':observe(
            {'command':['python3'],'args':['-m','vllm.entrypoints.openai.api_server']+
             (['--trust-remote-code'] if flag else [])},'vllm')}}
        rows=comparison_rows({'trust_request_mm_kwargs':False},snapshot,'vllm')
        remote=next(x for x in rows if x['field']=='launch_settings.trust_remote_code')
        assert remote['state']=='unapproved' and remote['deviates_from_default']==flag
        counts.append(len(evaluate_config_drift({'rows':rows}).conditions))
    assert counts==[0,1,0]
    return {'Cilium_actual_collector_with_fake_runner':True,'launch_default_drift_return_counts':counts,
            'actual_cluster_or_engine_verified':False,'network_calls':0}
