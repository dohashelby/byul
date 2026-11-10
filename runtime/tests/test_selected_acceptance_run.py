"""Checkpoint orchestration: local config/process tests, no AWS/inference."""
import copy
import base64
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from tools import selected_acceptance_run as runner


def save(path,value):
    path.write_text(json.dumps(value));return runner.digest(path.read_bytes())


@pytest.fixture
def config(tmp_path,monkeypatch):
    source={'path':str(tmp_path/'source'),'sha':'a'*40,'files':{p:'b'*64 for p in runner.MODULES.values()},'web':{'index.html':'c'*64}}
    receipt=tmp_path/'platform.json'
    hashed=save(receipt,{'source_sha':source['sha'],'host':'127.0.0.1','port':18700,'runtime_base_url':'http://127.0.0.1:18080',
        'imports':{name:{'file':str(Path(source['path'])/runner.MODULES[name]),'sha256':source['files'][runner.MODULES[name]]} for name in ('byul_platform.main','byul_platform.sources.runtime')},'web_dist_sha256':source['web']})
    scope={'backend_kind':'vllm','policy_sha256':'d'*64,'target_id':'fixed-target','run_id':'run-synthetic','target_instance_id':'instance',
        'pod_uid':'pod-uid','container_id':'containerd://synthetic','restart_count':0,'image_digest':'sha256:'+'e'*64}
    cfg={'schema':'byul-selected-acceptance-run/1','session':'synthetic','source':source,
        'engine':{'kind':'vllm','image_digest':scope['image_digest'],'model_revision':'f'*40,'inventory_sha256':'a'*64},
        'gateway_url':'http://127.0.0.1:18080','platform_url':'http://127.0.0.1:18700','platform_receipt':{'path':str(receipt),'sha256':hashed},
        'inference_token_env':'BYUL_TEST_INFERENCE','viewer_token_env':'BYUL_TEST_VIEWER','model':'test-model','stage':'requests','expected_scope':scope,
        'options':{},'input_sha256':{},'witnesses':[],'timeout_seconds':1,'stage_deadline_seconds':2}
    monkeypatch.setenv('BYUL_TEST_INFERENCE','SYNTHETIC_INFERENCE_SECRET_NO_EXPORT');monkeypatch.setenv('BYUL_TEST_VIEWER','SYNTHETIC_VIEWER_SECRET_NO_EXPORT')
    monkeypatch.setattr(runner,'verify_source',lambda c,**kw: c['source'])
    return cfg


def status(config):return {'confirmed':True,'scope':config['expected_scope'],'observation_session_id':'synthetic-observation'}


def mock_helper(monkeypatch,config,*,result='passed',code=0,mode='requests',rows=None):
    def invoke(argv,env,timeout):
        output=Path(argv[argv.index('--output')+1])
        save(output,{'mode':mode,'source_sha256':config['source']['files'][runner.MODULES['tools.selected_runtime_check']],
             'cases':rows if rows is not None else [{'id':'synthetic-case','result':result,'facts':{'request_id':'1'*32,'journal':{'request_id':'1'*32}}}]})
        return code
    monkeypatch.setattr(runner,'invoke',invoke);monkeypatch.setattr(runner,'capture',lambda c,d:status(c))


def test_scope_includes_actual_target_generation_and_protected_viewer(config,monkeypatch):
    from tools import selected_runtime_check as tool
    scope=config['expected_scope'];current={k:v for k,v in scope.items() if k in ('pod_uid','container_id','restart_count','image_digest')}
    current['instance_id']=scope['target_instance_id']
    raw={'backend':{'kind':'vllm'},'run_id':scope['run_id'],'policy':{'sha256':scope['policy_sha256']},
         'target':{'target_id':scope['target_id'],'instance':{'current':current}},'auth':{'protected_routes':['GET /api/*']}}
    monkeypatch.setattr(tool,'request',lambda *a,**kw:tool.HTTPResponse(200,{},json.dumps(raw).encode()))
    assert runner.capture(config,time.monotonic()+1)['confirmed'] is True
    current['container_id']='new-generation'
    assert runner.capture(config,time.monotonic()+1)['confirmed'] is False
    current['container_id']=scope['container_id'];raw['auth']['protected_routes']=[]
    assert runner.capture(config,time.monotonic()+1)['confirmed'] is False


@pytest.mark.parametrize('bad', ['remote-origin','partial-scope','deadline','unknown-option','private-engine-key','changed-receipt'])
def test_config_rejects_incomplete_or_unbounded_inputs(config,bad):
    if bad=='remote-origin':config['gateway_url']='http://example.com:80'
    if bad=='partial-scope':config['expected_scope']['pod_uid']=None
    if bad=='deadline':config['stage_deadline_seconds']=float('inf')
    if bad=='unknown-option':config['options']={'shell':'echo forbidden'}
    if bad=='private-engine-key':config['engine']['token']='not-a-public-input'
    if bad=='changed-receipt':Path(config['platform_receipt']['path']).write_text('{}')
    with pytest.raises(ValueError):runner.validate(config)


def test_prepare_performs_no_transport_and_writes_only_env_references(config,tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'capture',lambda *a:pytest.fail('prepare is offline'))
    result=runner.run(config,tmp_path/'prepared',prepare=True)
    assert result['execution']=='prepared'
    plan=read(tmp_path/'prepared/plan.json');assert 'BYUL_TEST_INFERENCE' in plan['argv']
    assert os.environ['BYUL_TEST_INFERENCE'] not in json.dumps(plan)


def read(path):return json.loads(Path(path).read_text())


@pytest.mark.parametrize('outcome,code,expected', [('passed',0,'passed'),('unknown',0,'unknown'),('failed',1,'failed')])
def test_helper_failures_and_unknown_not_promoted(config,tmp_path,monkeypatch,outcome,code,expected):
    mock_helper(monkeypatch,config,result=outcome,code=code)
    result=runner.run(config,tmp_path/'run')
    assert result['scope_result']==expected and result['case_index'][0]['request_ids']==['1'*32]
    allow=read(tmp_path/'run/ALLOWLIST.json');assert {r['name'] for r in allow['files']}=={'plan.json','run.json','helper.json'}


def test_unconfirmed_scope_sends_no_helper(config,tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'capture',lambda *a:{'confirmed':False})
    monkeypatch.setattr(runner,'invoke',lambda *a:pytest.fail('must not dispatch'))
    result=runner.run(config,tmp_path/'run')
    assert result['execution']=='not_run' and result['scope_result']=='unknown'


def test_after_changed_generation_unknown_but_existing_failure_preserved(config,tmp_path,monkeypatch):
    for index,(outcome,code,expected) in enumerate((('passed',0,'unknown'),('failed',1,'failed'))):
        mock_helper(monkeypatch,config,result=outcome,code=code)
        snapshots=iter([status(config),{'confirmed':False,'scope':{}}])
        monkeypatch.setattr(runner,'capture',lambda *a:next(snapshots))
        result=runner.run(config,tmp_path/('run'+str(index)))
        assert result['scope_result']==expected and not result['same_engine_scope']


def test_after_transport_error_cannot_hide_failed_case(config,tmp_path,monkeypatch):
    mock_helper(monkeypatch,config,result='failed',code=1);calls=[]
    def capture(*a):
        calls.append(1)
        if len(calls)==2:raise TimeoutError('synthetic')
        return status(config)
    monkeypatch.setattr(runner,'capture',capture)
    result=runner.run(config,tmp_path/'run')
    assert result['scope_result']=='failed' and result['after']['confirmed'] is False


@pytest.mark.parametrize('rows',[[],[{'id':'fake','result':'passed'},'invalid-row']])
def test_incomplete_case_contract_never_passes(config,tmp_path,monkeypatch,rows):
    mock_helper(monkeypatch,config,rows=rows)
    result=runner.run(config,tmp_path/'run')
    assert result['scope_result']=='unknown' and result['execution']=='incomplete'


def test_missing_platform_build_binding_does_not_dispatch(config,tmp_path,monkeypatch):
    document=read(config['platform_receipt']['path']);document['web_dist_sha256']={'index.html':'0'*64}
    config['platform_receipt']['sha256']=save(Path(config['platform_receipt']['path']),document)
    monkeypatch.setattr(runner,'capture',lambda c,d:status(c));monkeypatch.setattr(runner,'invoke',lambda *a:pytest.fail('wrong build'))
    assert runner.run(config,tmp_path/'run')['execution']=='not_run'


def test_explicit_witness_ids_cannot_attach_other_request(config,tmp_path,monkeypatch):
    mock_helper(monkeypatch,config);runner.run(config,tmp_path/'run')
    witness=tmp_path/'witness.json';sha=save(witness,{'requests':[{'request_id':'1'*32},{'request_id':'2'*32}]})
    refs=tmp_path/'refs.json';save(refs,{'witnesses':[{'path':str(witness),'sha256':sha,'role':'engine_log'}]})
    result=runner.link(tmp_path/'run',refs,tmp_path/'links.json')
    assert result['case_index'][0]['witness_refs'][0]['matching_request_ids']==['1'*32]
    assert result['unmatched_witness_request_ids']==['2'*32]
    assert result['witness_claims_independently_verified'] is False
    helper=tmp_path/'run/helper.json';helper.write_text('{}')
    with pytest.raises(ValueError):runner.link(tmp_path/'run',refs,tmp_path/'links-tampered.json')


def test_forward_requires_same_complete_snapshot_and_bound_backend(config,tmp_path):
    config['stage']='isolated';scope=config['expected_scope']
    pod={k:scope[k] for k in ('pod_uid','container_id','restart_count')};pod.update(started_at='2026-11-06T00:00:00Z')
    forward=tmp_path/'forward.json';backend=tmp_path/'backend.json'
    config['options']={'test_data_dir':str(tmp_path/'NEW'),'backend_config':str(backend),'engine_forward_receipt':str(forward)}
    save(backend,{'base_url':'http://127.0.0.1:18000'})
    data={'argv':['kubectl','port-forward','pod/exact','18000:8000','--address=127.0.0.1'],'bind':'127.0.0.1:18000','remote_port':8000,'ready':True,'same_pod_generation':True,'pod_before':pod,'pod_after':copy.deepcopy(pod)}
    save(forward,data);assert runner.forward_bound(config) is True
    data['pod_after']['started_at']='different';save(forward,data);assert runner.forward_bound(config) is False
    data['pod_after']=pod;save(forward,data);save(backend,{'base_url':'http://127.0.0.1:18001'})
    assert runner.forward_bound(config) is False


def test_all_seven_modes_use_fixed_helper_argv_without_shell(config,tmp_path):
    stages={'live':{},'requests':{},'separation':{},'model-before':{'replacement_model':'new','backend_config':'/explicit/config'},
        'model-after':{'replacement_model':'new','before_report':'/explicit/before'},'observe-gap':{'source':'tetragon','expected_state':'error'},
        'isolated':{'test_data_dir':'/NEW','backend_config':'/explicit/config'},
        'copies':{'files':['/explicit/one'],'marker_envs':['MARKER'],'credential_envs':['CREDENTIAL'],'allowed_reader':'ubuntu','denied_reader':'nobody','volume_evidence':'/public/volume'}}
    modes=set()
    for stage,options in stages.items():
        config.update(stage=stage,options=options);argv=runner.command(config,tmp_path/'helper.json');modes.add(argv[2])
        assert argv[0]==sys.executable and argv[1].endswith('/runtime/tools/selected_runtime_check.py')
        assert not any(value in argv for value in (os.environ['BYUL_TEST_INFERENCE'],os.environ['BYUL_TEST_VIEWER']))
    assert modes=={'live','requests','separation','lifecycle','observe','isolated','copies'}


def test_real_owned_subprocess_deadline_kills_only_its_group(monkeypatch):
    original=subprocess.Popen;children=[]
    def spawn(*a,**kw):
        process=original(*a,**kw);children.append(process);return process
    monkeypatch.setattr(runner.subprocess,'Popen',spawn)
    with pytest.raises(subprocess.TimeoutExpired):runner.invoke([sys.executable,'-c','import time; time.sleep(30)'],os.environ.copy(),.05)
    assert len(children)==1 and children[0].poll()==-signal.SIGKILL


def test_current_worktree_module_files_are_exactly_loaded(monkeypatch):
    # Commit metadata is a test double; every file hash and __file__ is real.
    monkeypatch.setattr(runner,'source_revision',lambda source:'a'*40)
    source=Path(__file__).resolve().parents[2]
    pins=runner.source_pins(source)
    assert runner.verify_source({'source':pins})==pins
    pins['files'][runner.MODULES['tools.selected_request_checks']]='0'*64
    with pytest.raises(ValueError):runner.verify_source({'source':pins})


def test_real_existing_helper_child_is_reused_without_cloud_or_socket(config,tmp_path,monkeypatch):
    # Actual existing copies helper runs in a separate process; only the outer
    # environment GET capture is a test double. It is not an AWS/OS-role PASS.
    source=Path(__file__).resolve().parents[2];monkeypatch.setattr(runner,'source_revision',lambda source:'a'*40);config['source']=runner.source_pins(source)
    receipt=read(config['platform_receipt']['path']);receipt.update(source_sha=config['source']['sha'],web_dist_sha256=config['source']['web'])
    receipt['imports']={name:{'file':str(source/runner.MODULES[name]),'sha256':config['source']['files'][runner.MODULES[name]]} for name in ('byul_platform.main','byul_platform.sources.runtime')}
    config['platform_receipt']['sha256']=save(Path(config['platform_receipt']['path']),receipt)
    sample=tmp_path/'explicit.log';sample.write_text('safe synthetic metadata only\n')
    volume=tmp_path/'volumes.json';sha=save(volume,{'Volumes':[{'VolumeId':'vol-synthetic','Encrypted':True}]})
    config.update(stage='copies',options={'files':[str(sample)],'marker_envs':['BYUL_TEST_MARKER'],'credential_envs':['BYUL_TEST_INFERENCE'],
        'allowed_reader':'nobody','denied_reader':'nobody','volume_evidence':str(volume)},input_sha256={'volume_evidence':sha},stage_deadline_seconds=5)
    monkeypatch.setenv('BYUL_TEST_MARKER','SYNTHETIC_NOT_PRESENT_MARKER')
    monkeypatch.setattr(runner,'capture',lambda c,d:status(c))
    result=runner.run(config,tmp_path/'run')
    helper=read(tmp_path/'run/helper.json')
    assert result['execution']=='completed' and helper['mode']=='copies'
    assert helper['source_sha256']==config['source']['files'][runner.MODULES['tools.selected_runtime_check']]
    assert sample.read_text()=='safe synthetic metadata only\n'
    assert result['gpu_execution_verified'] is False


def test_original_input_changes_do_not_change_consumed_snapshot(config,tmp_path,monkeypatch):
    mock_helper(monkeypatch,config)
    original=runner.invoke
    def mutate(*args):
        code=original(*args);Path(config['platform_receipt']['path']).write_text('{}');return code
    monkeypatch.setattr(runner,'invoke',mutate)
    result=runner.run(config,tmp_path/'run')
    assert result['scope_result']=='passed' and result['input_bindings_unchanged'] is True


def test_observation_checkpoint_needs_viewer_only(config,tmp_path,monkeypatch):
    config.update(stage='observe-gap',options={'source':'tetragon','expected_state':'error'})
    monkeypatch.delenv('BYUL_TEST_INFERENCE')
    mock_helper(monkeypatch,config,mode='observe')
    result=runner.run(config,tmp_path/'run')
    assert result['execution']=='completed' and result['scope_result']=='passed'
    assert '--inference-token-env' not in read(tmp_path/'run/plan.json')['argv']


@pytest.mark.parametrize('variant',['raw','json-escaped','hex','HEX','base64','urlsafe-base64'])
@pytest.mark.parametrize('where',['key','nested-value'])
def test_public_writer_rejects_decoded_and_encoded_secrets(tmp_path,variant,where):
    secret='SYNTHETIC_é_"quote\\slash\nline'
    raw=secret.encode()
    variants={'raw':secret,'json-escaped':json.dumps(secret)[1:-1],'hex':raw.hex(),'HEX':raw.hex().upper(),
              'base64':base64.b64encode(raw).decode(),'urlsafe-base64':base64.urlsafe_b64encode(raw).decode()}
    value=variants[variant];document={value:'safe'} if where=='key' else {'nested':[{'field':value}]}
    path=tmp_path/'public.json'
    with pytest.raises(ValueError):runner.write_public(path,document,[secret])
    assert not path.exists()


def test_escaped_helper_secret_stays_private_and_outside_allowlist(config,tmp_path,monkeypatch):
    secret='SYNTHETIC_"quote\\slash\nline';monkeypatch.setenv(config['inference_token_env'],secret)
    mock_helper(monkeypatch,config,rows=[{'id':'synthetic','result':'passed','facts':{'value':secret}}])
    result=runner.run(config,tmp_path/'run');allow=read(tmp_path/'run/ALLOWLIST.json')
    assert result['execution']=='incomplete' and result['scope_result']=='failed' and result['credential_export_blocked'] is True
    assert not (tmp_path/'run/private/helper.json').exists()
    assert not (tmp_path/'run/helper.json').exists()
    assert 'helper.json' not in {item['name'] for item in allow['files']}
    assert all(secret not in (tmp_path/'run'/item['name']).read_text() for item in allow['files'])


def test_backend_original_A_B_A_rotation_cannot_change_child_input(config,tmp_path,monkeypatch):
    backend=tmp_path/'backend.json';forward=tmp_path/'forward.json';scope=config['expected_scope']
    original={'base_url':'http://127.0.0.1:18000'};backend_sha=save(backend,original)
    pod={k:scope[k] for k in ('pod_uid','container_id','restart_count')};pod['started_at']='2026-11-06T00:00:00Z'
    forward_sha=save(forward,{'argv':['kubectl','port-forward','pod/exact','18000:8000','--address=127.0.0.1'],
        'bind':'127.0.0.1:18000','remote_port':8000,'ready':True,'same_pod_generation':True,'pod_before':pod,'pod_after':copy.deepcopy(pod)})
    config.update(stage='isolated',options={'test_data_dir':str(tmp_path/'NEW'),'backend_config':str(backend),'engine_forward_receipt':str(forward)},
        input_sha256={'backend_config':backend_sha,'engine_forward_receipt':forward_sha})
    mock_helper(monkeypatch,config,mode='isolated');invoke=runner.invoke;consumed=[]
    def rotate(argv,env,timeout):
        snapshot=Path(argv[argv.index('--backend-config')+1]);saved=backend.read_bytes()
        save(backend,{'base_url':'http://127.0.0.1:18001'})
        consumed.append(read(snapshot));backend.write_bytes(saved)
        assert snapshot!=backend and snapshot.stat().st_mode&0o777==0o400
        return invoke(argv,env,timeout)
    monkeypatch.setattr(runner,'invoke',rotate)
    result=runner.run(config,tmp_path/'run')
    assert consumed==[original] and result['scope_result']=='passed'
    assert result['input_bindings_unchanged'] is True
    assert all(not item['name'].startswith('private') for item in read(tmp_path/'run/ALLOWLIST.json')['files'])


def test_private_snapshot_tampering_keeps_unknown(config,tmp_path,monkeypatch):
    mock_helper(monkeypatch,config);invoke=runner.invoke
    def mutate(argv,env,timeout):
        code=invoke(argv,env,timeout)
        snapshot=Path(argv[argv.index('--output')+1]).parent/'platform-receipt.json'
        snapshot.chmod(0o600);snapshot.write_text('{}');return code
    monkeypatch.setattr(runner,'invoke',mutate)
    result=runner.run(config,tmp_path/'run')
    assert result['scope_result']=='unknown' and result['input_bindings_unchanged'] is False
