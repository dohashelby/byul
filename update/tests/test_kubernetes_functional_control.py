"""Same live entry/CheckDef/source protocol; injected EXAMPLE and local sockets."""
from copy import deepcopy
import json
import sys

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.executors.http_common import Ctx, functional_requests, functional_results
from guard.models import sha256_hex, utcnow
from guard.runtime.bounded import capture_pipes
from guard.runtime.kubernetes_http import KubernetesHTTP, KubernetesBindingError, REQUEST_BINDING_PROFILE, PROFILE
from guard.runtime.kubernetes_functional import LOGICAL_ID, WIRE_ID
from tests.builders import baseline_pack
from tests.test_kubernetes_sampling import World as PodWorld, PRIVATE
from tests.test_kubernetes_request_binding import env, target


def response():
    return json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE,'finish_reason':'length'}],
        'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})


class World(PodWorld):
    def __init__(self):
        super().__init__(target());self.damage=None;self.actual_probe=False

    def runner(self,argv,**kwargs):
        if 'exec' not in argv:return super().runner(argv,**kwargs)
        assert '-i' in argv and '--container=probe' in argv and PRIVATE not in ' '.join(argv)
        spec=json.loads(kwargs['input_bytes']);self.execs.extend(deepcopy(spec['requests']));self.after_exec=True
        if self.actual_probe:
            return capture_pipes([sys.executable,'-c',argv[-2],argv[-1]],**kwargs)
        text=response()
        value={'id':WIRE_ID,'method':'POST','mode':'json','outcome':200,'body':text,
            'body_bytes':len(text.encode()),'body_sha256':sha256_hex(text.encode()),
            'body_truncated':False,'capture_complete':True,'eof':True,'utf8_valid':True,
            'stop_reason':'eof','detail':PRIVATE,'body_preview':PRIVATE,'headers':{'Authorization':PRIVATE}}
        rows=[value]
        if self.damage:self.damage(rows)
        return {'returncode':0,'stdout_bytes':json.dumps(rows).encode(),'stderr_bytes':PRIVATE.encode(),
                'capture':{'complete':True}}


def setup(tmp_path,world=None,check_id='PLT-01'):
    world=world or World()
    transport=KubernetesHTTP(world.target,env(),duration=30,runner=world.runner,profile=REQUEST_BINDING_PROFILE)
    ctx=Ctx(check=baseline_pack().checks[check_id],env=env(),adapter=VllmAdapter(),target=world.target,
        policy_values={},timeout=3,allow_mutating=False,keys={},secrets=[],cprobe=transport,
        workdir=tmp_path,started=utcnow())
    requests,probes=functional_requests(ctx,via='engine')
    return world,transport,ctx,requests,probes


@pytest.mark.parametrize('check_id',['PLT-01','PLT-04'])
def test_existing_check_body_and_functional_validator_use_exact_bound_api_alias(tmp_path,check_id):
    world,transport,ctx,requests,probes=setup(tmp_path,check_id=check_id)
    assert requests==[{'id':LOGICAL_ID,'method':'POST','url':world.target['engine']['trusted_url']+'/v1/completions',
        'headers':{},'body':{'model':'fixture-model','prompt':'ping','max_tokens':1},'want_body':True}]
    original=deepcopy(requests)
    rows=transport.normal_requests(ctx.trusted_net,requests,3)
    assert requests==original and rows[0]['id']==LOGICAL_ID and rows[0]['capture_complete'] is True
    assert world.execs[0]['id']==WIRE_ID and world.execs[0]['body']==requests[0]['body']
    assert world.execs[0]['url']==requests[0]['url']
    functional,_,normal=functional_results(probes,{next(iter(probes)):rows[0]})
    assert list(functional.values())==['pass'] and normal['succeeded'] is True
    ctx.record('trusted','functional-engine',next(iter(probes)),'none',rows[0])
    assert PRIVATE not in json.dumps([transport.report(),ctx.log])
    assert transport.report()['requests'][0]['probe_source_sha256']==transport.source_sha256
    # Existing REQ11 dispatch remains strict; this is an explicit new method.
    with pytest.raises(KubernetesBindingError):transport(ctx.trusted_net,requests,3)


@pytest.mark.parametrize('damage',['id','path','query','host','scheme','model','prompt','max','bool',
    'extra-body','headers','want-body','extra','duplicate','network','timeout','profile'])
def test_wrong_scope_is_rejected_before_any_remote_io(tmp_path,damage):
    world,transport,ctx,requests,_=setup(tmp_path)
    network=ctx.trusted_net;timeout=3;r=requests[0]
    if damage=='id':r['id']='before'
    elif damage=='path':r['url']=r['url'].replace('/v1/completions','/v1/chat/completions')
    elif damage=='query':r['url']+='?key='+PRIVATE
    elif damage=='host':r['url']='http://127.0.0.9:8000/v1/completions'
    elif damage=='scheme':r['url']=r['url'].replace('http:','https:')
    elif damage=='model':r['body']['model']=env().model.id
    elif damage=='prompt':r['body']['prompt']=PRIVATE
    elif damage=='max':r['body']['max_tokens']=2
    elif damage=='bool':r['body']['max_tokens']=True
    elif damage=='extra-body':r['body']['stream']=True
    elif damage=='headers':r['headers']['Authorization']=PRIVATE
    elif damage=='want-body':r['want_body']=False
    elif damage=='extra':r['unsafe']=True
    elif damage=='duplicate':requests.append(deepcopy(r))
    elif damage=='network':network='other'
    elif damage=='timeout':timeout=float('nan')
    else:transport=KubernetesHTTP(world.target,env(),duration=30,runner=world.runner,profile=PROFILE)
    with pytest.raises(KubernetesBindingError):transport.normal_requests(network,requests,timeout)
    assert not world.calls and not world.execs
    assert PRIVATE not in json.dumps(transport.report())


@pytest.mark.parametrize('damage',['id','method','mode','missing','duplicate','not-dict'])
def test_exact_wire_response_correlation_required(tmp_path,damage):
    world,transport,ctx,requests,_=setup(tmp_path)
    def mutate(rows):
        if damage in {'id','method','mode'}:rows[0][damage]='other'
        elif damage=='missing':rows.clear()
        elif damage=='duplicate':rows.append(deepcopy(rows[0]))
        else:rows[0]=None
    world.damage=mutate
    with pytest.raises(KubernetesBindingError):transport.normal_requests(ctx.trusted_net,requests,3)
    assert transport.report()['collection_gap'] is True and PRIVATE not in json.dumps(transport.report())


@pytest.mark.parametrize('field,value',[('capture_complete',False),('eof',False),('utf8_valid',False),
    ('body_truncated',True),('stop_reason','timeout'),('body_bytes',1),('body_bytes',True),
    ('body_sha256','0'*64),('body',None)])
def test_partial_or_inconsistent_200_cannot_pass_existing_validator(tmp_path,field,value):
    world,transport,ctx,requests,probes=setup(tmp_path)
    world.damage=lambda rows:rows[0].update({field:value})
    rows=transport.normal_requests(ctx.trusted_net,requests,3)
    assert rows[0]['outcome']==200 and rows[0]['body'] is None and rows[0]['capture_complete'] is False
    functional,_,normal=functional_results(probes,{next(iter(probes)):rows[0]})
    assert list(functional.values())==['no_response'] and normal['succeeded'] is False
    assert transport.report()['collection_gap'] is True and PRIVATE not in json.dumps(transport.report())


@pytest.mark.parametrize('role',['engine','probe'])
@pytest.mark.parametrize('field',['pod_uid','container_id','image_id','restart_count','namespace_uid'])
def test_later_generation_gap_does_not_remove_an_earlier_bound_control(tmp_path,role,field):
    world,transport,ctx,requests,_=setup(tmp_path)
    first=transport.normal_requests(ctx.trusted_net,requests,3)
    def mutate(which,value,after):
        if len(world.execs)!=2 or not after:return
        if field=='namespace_uid':
            if which=='namespaces':
                next(x for x in value['items'] if x['metadata']['name']==world.target['kubernetes'][role]['namespace'])['metadata']['uid']='replaced'
        elif which==role:
            if field=='pod_uid':value['metadata']['uid']='replaced'
            else:value['status']['containerStatuses'][0][{'container_id':'containerID','image_id':'imageID','restart_count':'restartCount'}[field]]=1
    world.mutate=mutate
    with pytest.raises(KubernetesBindingError):transport.normal_requests(ctx.trusted_net,requests,3)
    report=transport.report()
    assert first[0]['capture_complete'] is True
    assert report['requests'][0]['binding_verified_before_after'] is True
    assert report['requests'][1]['binding_verified_before_after'] is False
    assert PRIVATE not in json.dumps(report)


def test_denial_and_parent_cleanup_have_separate_known_facts(tmp_path):
    world,transport,ctx,requests,probes=setup(tmp_path)
    world.damage=lambda rows:rows[0].update(outcome=403)
    rows=transport.normal_requests(ctx.trusted_net,requests,3)
    functional,_,normal=functional_results(probes,{next(iter(probes)):rows[0]})
    assert list(functional.values())==['fail'] and normal['succeeded'] is False
    original=world.runner
    def runner(argv,**kwargs):
        value=original(argv,**kwargs)
        if 'exec' in argv:value['capture'].update(complete=False,pipes_closed=False,read_errors=['child_reap'])
        return value
    transport.runner=runner
    with pytest.raises(KubernetesBindingError):transport.normal_requests(ctx.trusted_net,requests,3)
    assert transport.report()['cleanup_errors']==['KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED']
    assert transport.report()['requests'][0]['outcome']==403


def test_pinned_source_against_real_local_complete_and_truncated_http(tmp_path):
    from tests.test_stream_probe import _server, _response
    def handle(conn,request,index,stop):
        assert b'POST /v1/completions ' in request
        assert json.loads(request.partition(b'\r\n\r\n')[2])=={'model':'fixture-model','prompt':'ping','max_tokens':1}
        text=response().encode()
        if index==0:_response(conn,text)
        else:conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(text)+5).encode()+b'\r\nContent-Type: application/json\r\n\r\n'+text)
    with _server(handle) as (url,accepted):
        world=World();world.target['engine']['trusted_url']=url;world.actual_probe=True
        _,transport,ctx,requests,probes=setup(tmp_path,world)
        first=transport.normal_requests(ctx.trusted_net,requests,3)
        second=transport.normal_requests(ctx.trusted_net,requests,3)
        assert functional_results(probes,{next(iter(probes)):first[0]})[2]['succeeded'] is True
        assert second[0]['outcome']==200 and second[0]['body'] is None
        assert functional_results(probes,{next(iter(probes)):second[0]})[2]['succeeded'] is False
        assert len(accepted)==2 and PRIVATE not in json.dumps(transport.report())
