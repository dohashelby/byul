"""Existing route collectors/CLI with injected exact four-Pod API responses."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
from urllib.parse import urlsplit

import pytest
from typer.testing import CliRunner

from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.executors.kubernetes_routes import KubernetesDirectRouteExecutor, KubernetesRoleRouteExecutor
from guard.models import canonical_json, DataOrigin
from guard.runtime import kubernetes_http as kube
from guard.runtime.kubernetes_routes import KubernetesRoutesHTTP, EXTERNAL
from guard.store import Store
from tests.builders import baseline_pack, environment, policy, T0
from tests.test_kubernetes_request_binding import env as binding_env, target as binding_target
from tests.test_direct_route import World as DirectWorld, ALLOWED, DENIED, KNOWN
from tests.test_role_route import RoleWorld, GENERAL, ADMIN, WRONG

PRIVATE = 'EXAMPLE_ROUTE_PRIVATE_729ef'


def inputs(check='REQ-01'):
    base = binding_env()
    env = environment({'gateway': {'value': True, 'origin': 'EXAMPLE', 'source': 'EXAMPLE'}},
                      engine=base.engine.model_dump(), model=base.model.model_dump())
    t = binding_target(env)
    extra = {}
    for i, role in enumerate(('external_probe','gateway'), 3):
        b = deepcopy(t['kubernetes']['probe'])
        b.update(namespace=role.replace('_','-'),namespace_uid=f'00000000-0000-0000-0000-{i:012d}',
                 pod=role.replace('_','-')+'-pod',pod_uid=f'10000000-0000-0000-0000-{i:012d}',
                 container_id='containerd://'+str(i)*64,pod_ip='127.0.0.'+str(i))
        extra[role]=b
    t.update(kubernetes_routes=extra,gateway={'url':'http://127.0.0.4:8080'},
             direct_targets=[{'origin':'external','url':t['engine']['trusted_url']}],
             gateway_known_routes=KNOWN if check=='REQ-01' else [])
    t['origins']['external']={'network':EXTERNAL}
    return env,t


class World:
    def __init__(self, t, check='REQ-01', mode=None):
        self.t,self.check,self.mode=t,check,mode
        self.calls,self.packets=[],[]
        self.backend=DirectWorld(exposed=mode in {'violation','late-cleanup','late-restart'}) if check=='REQ-01' else RoleWorld(enforced=mode!='violation')
        self.changed=False
        self.tamper=None

    def result(self, value):
        return {'returncode':0,'stdout_bytes':json.dumps(value).encode(),
                'stderr_bytes':PRIVATE.encode(),'capture':{'complete':True}}

    def runner(self, argv, **kwargs):
        assert argv[0]=='kubectl' and argv[1]=='--context=fixture-context'
        assert PRIVATE not in ' '.join(argv)
        self.calls.append(argv)
        bindings={**self.t['kubernetes'],**self.t['kubernetes_routes']}
        bindings={k:v for k,v in bindings.items() if type(v) is dict}
        if 'namespaces' in argv:
            return self.result({'kind':'List','items':[{'kind':'Namespace','metadata':{'name':bindings[k]['namespace'],'uid':bindings[k]['namespace_uid']}} for k in ('engine','probe')]})
        if 'namespace' in argv:
            name=argv[argv.index('namespace')+1];b=next(x for x in bindings.values() if x['namespace']==name)
            return self.result({'kind':'Namespace','metadata':{'name':name,'uid':b['namespace_uid']}})
        if 'get' in argv:
            name=argv[argv.index('pod')+1];role,b=next((k,v) for k,v in bindings.items() if v['pod']==name)
            value={'kind':'Pod','metadata':{'name':name,'namespace':b['namespace'],'uid':b['pod_uid'],
                    'labels':{'guard.run':'guard-fixture'},'annotations':{'private':PRIVATE}},
                'spec':{'nodeName':b['node'],'containers':[{'name':b['container'],'image':b['image'],
                         'env':[{'name':'SECRET','value':PRIVATE}],'args':[PRIVATE]}]},
                'status':{'phase':'Running','podIP':b['pod_ip'],'conditions':[{'type':'Ready','status':'True'}],
                    'containerStatuses':[{'name':b['container'],'containerID':b['container_id'],'imageID':b['image_id'],
                        'restartCount':1 if self.changed and role=='gateway' else 0,'ready':True,
                        'state':{'running':{'startedAt':b['started_at']}}}]}}
            if self.packets and self.tamper:self.tamper(role,value)
            return self.result(value)
        assert 'exec' in argv and 'input_bytes' in kwargs
        requests=json.loads(kwargs['input_bytes'])['requests'];self.packets.append(deepcopy(requests))
        origin='trusted' if self.t['kubernetes']['probe']['pod'] in argv else 'external'
        rows=[]
        for request in requests:
            r=deepcopy(request);u=urlsplit(r['url'])
            r['url']=('http://g:8080' if u.hostname=='127.0.0.4' else 'http://e:8000')+u.path
            row=self.backend.handle(origin,r)
            row.update(detail=PRIVATE,body_preview=PRIVATE,headers={'Authorization':PRIVATE})
            rows.append(row)
        result=self.result(rows)
        if requests[0]['id']=='positive' and self.mode=='late-cleanup':
            result['capture'].update(complete=False,pipes_closed=False,read_errors=['child_reap'])
        if requests[0]['id']=='positive' and self.mode=='late-restart':
            self.changed=True
        if self.mode=='wrong-id':rows[0]['id']='unrelated';result=self.result(rows)
        return result


def configured(tmp_path,check='REQ-01',mode=None):
    env,t=inputs(check);world=World(t,check,mode)
    path=tmp_path/'target.json';path.write_bytes(canonical_json(t))
    cfg={'target':str(path),'request_timeout':1}
    if check=='REQ-01':
        cfg.update(roles={'allowed':'KA'},wrong_key_env='KW')
        values={'allowed_callers':['allowed']};keys={'KA':ALLOWED,'KW':DENIED}
    else:
        cfg.update(roles={'general':'KG','admin':'KA'},wrong_key_env='KW')
        values={'general_allowed_kinds':['inference']};keys={'KG':GENERAL,'KA':ADMIN,'KW':WRONG}
    return env,t,world,cfg,values,keys


@pytest.mark.parametrize('check,mode',[(c,m) for c in ('REQ-01','REQ-03')
    for m in (None,'violation','wrong-id','attachment','missing-policy')]
    + [('REQ-01','late-cleanup'),('REQ-01','late-restart')])
def test_real_registry_cli_safe_existing_collectors_and_exact_refs(tmp_path,monkeypatch,check,mode):
    env,t,world,cfg,values,keys=configured(tmp_path,check,mode)
    pack=baseline_pack();pol=policy(pack.ref(),scope={'include':[check]},values={} if mode=='missing-policy' else {check:values})
    for name,value in [('environment',env.to_dict()),('policy',pol.to_dict())]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    name='direct_route' if check=='REQ-01' else 'role_route'
    config=tmp_path/'guard.toml';config.write_text('[executors]\n'+name+'="kubernetes"\n[adapter]\nname="vllm"\n['+name+']\ntarget='+json.dumps(cfg['target'])+'\nrequest_timeout=1\nwrong_key_env="KW"\n['+name+'.roles]\n'+''.join(k+'='+json.dumps(v)+'\n' for k,v in cfg['roles'].items()),encoding='utf-8')
    for k,v in keys.items():monkeypatch.setenv(k,v)
    monkeypatch.setattr(kube,'capture_pipes',world.runner)
    def forbidden(*a,**k):raise AssertionError('no Docker/process/socket outside existing injected boundary')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    runner=CliRunner();common=['--config',str(config),'--store',str(tmp_path/'store')]
    def cli(args,expected_exit=0):
        r=runner.invoke(app,args);assert r.exit_code==expected_exit,(args,r.output,repr(r.exception));return r.output
    plan=cli(['select','--pack',str(pack.path),'--env',str(tmp_path/'environment.json'),'--policy',str(tmp_path/'policy.json'),*common]).strip().splitlines()[-1]
    with monkeypatch.context() as capture:
        if mode=='attachment':
            original,calls=Store.put_artifact,[]
            def partial(self,data):
                calls.append(1)
                if len(calls)==2:raise OSError(PRIVATE)
                return original(self,data)
            capture.setattr(Store,'put_artifact',partial)
        cli(['run','--plan',plan,'--pack',str(pack.path),*common],expected_exit=1 if mode=='late-cleanup' else 0)
    cli(['judge','--plan',plan,'--as-of',T0.isoformat(),*common])
    cli(['report','--plan',plan,'--as-of',T0.isoformat(),'--json',str(tmp_path/'report.json'),*common])
    cli(['verify',*common]);st=Store.read_only(tmp_path/'store');e,=st.iter_kind('EVD');r,=st.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE'
    assert e.execution.status.value==('NOT_RUN' if mode=='missing-policy' else 'ABORTED' if mode=='attachment' else 'COMPLETED')
    if mode=='late-cleanup':assert e.execution.cleanup_errors==['KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED']
    assert r.verdicts.security.value==('PASS' if mode is None else 'FAIL' if mode in {'violation','late-cleanup','late-restart'} else 'UNKNOWN')
    if mode=='missing-policy':assert not world.calls
    else:
        assert e.execution.executor.name==name
        assert all('create' not in call and 'delete' not in call and 'patch' not in call for call in world.calls)
    objects=[x for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for x in st.iter_kind(kind)]
    cli(['evidence','export','--store',str(tmp_path/'store'),'--output',str(tmp_path/'bundle.zip')])
    cli(['evidence','restore','--bundle',str(tmp_path/'bundle.zip'),'--destination',str(tmp_path/'restored')])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(x.ref()).to_dict()==x.to_dict() for x in objects)
    cli(['judge','--plan',plan,'--as-of',T0.isoformat(),'--store',str(tmp_path/'restored'),'--config',str(config)])
    assert [x.ref() for x in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[r.ref()]
    blob=''.join(p.read_bytes().decode('utf-8','replace') for p in (tmp_path/'store').rglob('*') if p.is_file())
    assert PRIVATE not in blob and all(v not in blob for v in keys.values())


@pytest.mark.parametrize('mode',['late-cleanup','late-restart'])
def test_later_identity_cleanup_gap_preserves_earlier_independent_denied_caller_reach(tmp_path,mode):
    env,t,world,cfg,values,keys=configured(tmp_path,mode=mode)
    draft=KubernetesDirectRouteExecutor(cfg,runner=world.runner,environ=keys).run(
        baseline_pack().checks['REQ-01'],env,values,VllmAdapter(),tmp_path/'work')
    assert any(type(x) is int and 200<=x<300 for x in draft.observations['direct_outcomes'].value.values())
    assert draft.observations['trusted_origin_reachable'].value is False
    detail=json.loads((tmp_path/'work/kubernetes_route_binding.json').read_text())
    assert detail['collection_gap'] is True
    if mode=='late-cleanup':assert draft.cleanup_errors==['KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED']


@pytest.mark.parametrize('damage',['missing-origin','same-pod','gateway-service','tls','host','wrong-model','pin','mutating'])
def test_unsupported_or_unbound_inputs_rejected_before_any_api(tmp_path,damage):
    env,t,world,cfg,values,keys=configured(tmp_path)
    pin=None
    if damage=='missing-origin':del t['kubernetes_routes']['external_probe']
    elif damage=='same-pod':t['kubernetes_routes']['gateway']=deepcopy(t['kubernetes']['engine'])
    elif damage=='gateway-service':t['gateway']['url']='http://service.invalid:8080'
    elif damage=='tls':t['gateway']['url']='https://127.0.0.4:8080'
    elif damage=='host':t['direct_targets'].append({'origin':'host','url':'http://127.0.0.1:18000'})
    elif damage=='wrong-model':t['model_mapping']['repository_id']='foreign'
    elif damage=='pin':pin='0'*64
    else:cfg['allow_mutating_routes']=True
    Path(cfg['target']).write_bytes(canonical_json(t))
    def forbidden(*a,**k):pytest.fail('invalid input must be rejected before kubectl/HTTP')
    with pytest.raises((ValueError,RuntimeError)):
        KubernetesDirectRouteExecutor(cfg,runner=forbidden,environ=keys,expected_target_sha256=pin).run(
            baseline_pack().checks['REQ-01'],env,values,VllmAdapter(),tmp_path/'work')


def test_injected_actual_declaration_stays_example_and_no_static_reuse(tmp_path):
    env,t,world,cfg,values,keys=configured(tmp_path)
    env=env.model_copy(update={'data_origin':DataOrigin.MEASURED}).seal()
    t.update(lab=False,environment_ref=env.ref().model_dump(mode='json'));Path(cfg['target']).write_bytes(canonical_json(t))
    executor=KubernetesDirectRouteExecutor(cfg,runner=world.runner,environ=keys)
    assert executor.preview_reuse_inputs(baseline_pack().checks['REQ-01'],env,values,VllmAdapter()) is None
    assert not world.calls
    draft=executor.run(baseline_pack().checks['REQ-01'],env,values,VllmAdapter(),tmp_path/'work')
    assert draft.data_origin.value=='EXAMPLE'


def test_exact_route_probe_program_runs_local_http_and_tcp_with_credentials_only_on_stdin():
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import sys,threading
    from guard.runtime.bounded import capture_pipes
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers['Authorization']=='Bearer EXAMPLE_LOCAL_KEY'
            raw=b'EXAMPLE local control';self.send_response(200)
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever);thread.start()
    try:
        env,t=inputs();transport=KubernetesRoutesHTTP(t,env,runner=lambda *a,**k:pytest.fail('no kubectl in this local source-program test'))
        url='http://127.0.0.1:'+str(server.server_port)
        requests=[{'id':'health','method':'GET','url':url+'/health','want_body':True,
                   'headers':{'Authorization':'Bearer EXAMPLE_LOCAL_KEY'}},
                  {'id':'tcp','method':'TCP','mode':'tcp','url':url}]
        command=[sys.executable,'-c',transport.source,'5']
        assert all('EXAMPLE_LOCAL_KEY' not in x for x in command)
        result=capture_pipes(command,input_bytes=json.dumps({'requests':requests,'timeout':2}).encode(),
                             timeout=10,env_extra={},max_stdout_bytes=65536,max_stderr_bytes=4096)
        assert result['returncode']==0 and result['capture']['complete']
        rows=json.loads(result['stdout_bytes']);assert rows[0]['outcome']==200 and rows[0]['body']=='EXAMPLE local control'
        assert rows[1]['outcome']=='connected' and rows[1]['phase']=='connect'
    finally:
        server.shutdown();thread.join();server.server_close()


@pytest.mark.parametrize('role',['engine','probe','external_probe','gateway'])
@pytest.mark.parametrize('field',['pod_uid','container_id','image_id','restart_count','started_at'])
def test_all_four_selected_generations_are_rechecked_after_dispatch(role,field):
    env,t=inputs();world=World(t)
    def mutate(which,value):
        if which!=role:return
        if field=='pod_uid':value['metadata']['uid']='replacement'
        else:
            row=value['status']['containerStatuses'][0]
            if field=='started_at':row['state']['running']['startedAt']='2026-10-26T00:00:01Z'
            else:row[{'container_id':'containerID','image_id':'imageID','restart_count':'restartCount'}[field]]=1
    world.tamper=mutate
    transport=KubernetesRoutesHTTP(t,env,runner=world.runner)
    row,=transport('kubernetes-probe',[{'id':'openapi','method':'GET','url':t['engine']['trusted_url']+'/openapi.json','want_body':True}],1)
    assert row['outcome']=='probe_unavailable' and len(world.packets)==1
    assert transport.report()['collection_gap'] and PRIVATE not in json.dumps(transport.report())


def test_serial_socket_timeouts_return_complete_batch_without_a_second_sum_timeout_gate():
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import sys,threading,time
    from guard.runtime.bounded import capture_pipes
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(.15)
            try:self.send_response(200);self.end_headers()
            except (BrokenPipeError,ConnectionResetError):pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever);thread.start()
    try:
        env,t=inputs();url='http://127.0.0.1:'+str(server.server_port)
        t['engine']['trusted_url']=url;t['direct_targets']=[{'origin':'external','url':url}]
        world=World(t)
        def actual(argv,**kwargs):
            if 'exec' in argv:return capture_pipes([sys.executable,'-c',argv[-2],argv[-1]],**kwargs)
            return world.runner(argv,**kwargs)
        transport=KubernetesRoutesHTTP(t,env,duration=5,runner=actual)
        rows=transport('kubernetes-probe',[{'id':str(i),'method':'GET','url':url+'/slow'} for i in range(2)],.05)
        assert [x['outcome'] for x in rows]==['error','error']
        assert all(x['phase']=='after_connect' for x in rows)
        assert transport.report()['collection_gap'] is False
    finally:
        server.shutdown();thread.join();server.server_close()
