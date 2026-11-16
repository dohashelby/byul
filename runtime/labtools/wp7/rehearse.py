"""Offline/local-only rehearsal, including actual Gateway/mock request matrix."""
from __future__ import annotations
import argparse,contextlib,copy,hashlib,importlib.util,io,json,os,secrets,sys,tempfile,time
from datetime import datetime,timezone,timedelta
from ipaddress import IPv4Address,IPv6Address
from pathlib import Path
from unittest.mock import patch
from common import BASELINE,approval,now,run,save
from export_evidence import Sanitizer,export,leaks,protected
from plan import STEPS,describe

def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=a.runtime_root.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False,mode=0o700)
    sys.path[:0]=[str(root),str(root/'src'),str(root.parent/'update/src'),str(root.parent/'platform/api/src')]
    results=[];log=[]
    def record(name,function):
        start=now()
        try:detail=function();ok=True
        except Exception as e:detail={'error_class':type(e).__name__};ok=False
        results.append({'name':name,'started_at':start,'ended_at':now(),'passed':ok,'details':detail})
        log.append(name+' '+('PASS' if ok else 'FAIL')+' '+json.dumps(detail,ensure_ascii=False))
    directory=Path(__file__).resolve().parent
    def syntax():
        import ast
        py=list(directory.glob('*.py'));shell=list(directory.glob('*.sh'))
        for f in py:ast.parse(f.read_text(),filename=f.name)
        for f in shell:
            code,_=run(['bash','-n',str(f)]);assert code==0
        assert len(STEPS)==20 and len({x[0] for x in STEPS})==20
        assert all(x[2]>0 and hasattr(__import__('server_checks').Live,x[0].replace('-','_')) for x in STEPS)
        return {'python_files':len(py),'shell_files':len(shell),'mandatory_ids':20}
    record('syntax_and_twenty_ID_coverage',syntax)
    from resume_rule_checks import check_revision_rules
    record('approved_budget_and_N03_revision',check_revision_rules)
    from preparation_checks import run as check_preparation_boundaries
    record('actual_preparation_file_boundaries',check_preparation_boundaries)
    from execution_review_checks import run as check_execution_review
    record('reviewed_execution_failure_boundaries',check_execution_review)
    def dryplans():
        commands=[['bash',str(directory/'build_and_ship.sh'),'--dry-run'],
                  ['bash',str(directory/'run_round1.sh'),'--dry-run'],
                  ['bash',str(directory/'run_round2.sh'),'--dry-run'],
                  [sys.executable,str(directory/'cleanup_check.py'),'--dry-run'],
                  [sys.executable,str(directory/'execute_from_mac.py')]]
        for n,argv in enumerate(commands):
            code,text=run(argv,timeout=15);assert code==0
            save(out/('plan-'+str(n)+'.json'),{'command':Path(argv[0]).name+' '+Path(argv[1]).name+(' '+' '.join(argv[2:]) if argv[2:] else ''),'exit_code':code,'output':text})
        return {'plans_executed':5,'aws_commands':0}
    record('offline_dry_run_plans',dryplans)
    def privacy():
        values=[str(IPv4Address((192<<24)+(2<<8)+7)),str(IPv6Address(1)),
                'i-'+'a'*17,'vol-'+'b'*17,'7'*12,'AKIA'+'Z'*16]
        credential=secrets.token_urlsafe(40)
        original={'state':'passed','count':31,'passed':True,'started_at':now(),
                  'raw':' '.join(values)+' Bearer '+credential,
                  'nested':{'viewer_token':credential},'numeric_values':[0,1,60,25,1.2021]}
        before=copy.deepcopy(original)
        s=Sanitizer([credential]);after=s.value(original)
        assert before==original and not leaks(json.dumps(after),[credential])
        assert protected(original)==protected(after)
        assert all(x not in json.dumps(after) for x in values+[credential])
        assert s.value(after)==after
        with tempfile.TemporaryDirectory(prefix='wp7-private-rehearsal-') as temp:
            src=Path(temp)/'raw';src.mkdir()
            # These are synthetic values, never real cloud or authentication values.
            save(src/'probe.json',original)
            destination=Path(temp)/'export'
            result=export(src,destination,[credential])
            assert json.loads((src/'probe.json').read_text())==original
            assert result['leak_hits']==0 and result['decision_fields_preserved']
            (src/'unsafe.bin').write_bytes(b'unknown-binary')
            try:export(src,Path(temp)/'reject',[credential])
            except ValueError:pass
            else:raise AssertionError('Unknown binary accepted')
        return {'sensitive_classes':7,'numeric_state_time_preserved':True,'original_preserved':True,
                'repeat_sanitization_stable':True,'unknown_binary_rejected':True,'leak_hits':0}
    record('sanitized_export_positive_and_negative',privacy)
    def guards():
        with tempfile.TemporaryDirectory() as temp:
            f=Path(temp)/'receipt.json'
            save(f,{'approved_by':'not-approved','scope':'WP7 AWS execution','baseline':BASELINE,
                    'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()})
            try:approval(f)
            except ValueError:pass
            else:raise AssertionError('Invalid approval accepted')
            f.chmod(0o644)
            try:approval(f)
            except ValueError:pass
            else:raise AssertionError('Public receipt accepted')
        from round_runner import must_stop
        assert must_stop('stop','CheckFailure')
        assert must_stop('continue','RecoveryFailure')
        assert not must_stop('continue','CheckFailure')
        code,_=run([sys.executable,'-c','import time;time.sleep(5)'],timeout=.1)
        assert code==124
        return {'invalid_receipt_rejected':True,'unsafe_permission_rejected':True,'timeout_exit':124,
                'stop_continue_rules_verified':True}
    record('approval_timeout_failure_guards',guards)
    def predicates():
        from labtools.aws_lab_acceptance import evaluate_case
        facts={'direct_failed':True,'direct_connected':False,'tokenize_direct_failed':True,
               'gateway_tcp_control':True,'gateway_control_200':True,'hubble_dropped':True}
        assert evaluate_case('AWS-04',facts)[0]=='passed'
        probes=0
        for key in ['direct_failed','gateway_tcp_control','gateway_control_200','hubble_dropped']:
            changed={**facts,key:False};assert evaluate_case('AWS-04',changed)[0]!='passed';probes+=1
        assert evaluate_case('AWS-04',{**facts,'direct_connected':True})[0]=='failed'
        assert evaluate_case('AWS-12',{'cleanup_ok':False})[0]=='failed'
        from server_checks import require,CheckFailure
        rejected=0
        for state in ['failed','unknown','not_run',None]:
            try:require(state=='passed','NOT_PASSED')
            except CheckFailure:rejected+=1
        assert rejected==4
        # Matrix exact count; 30/32 or an unknown must not pass.
        matrix=lambda rows:len(rows)==31 and all(x=='passed' for x in rows)
        assert matrix(['passed']*31)
        assert not any(matrix(rows) for rows in [['passed']*30,['passed']*32,['passed']*30+['unknown']])
        return {'existing_AWS_evaluator_reused':True,'AWS_negative_probes':probes+2,'nonpass_states_rejected':4,
                'matrix_exact_count_negative_probes':3}
    record('existing_and_round_predicate_fake_inputs',predicates)
    from rehearsal_checks import check_live_script_logic,check_mac_failure_stop,check_feed_upgrade,check_cilium_and_launch_fake_inputs
    record('live_N06_N07_scripts_fake_input_and_rollback',check_live_script_logic)
    record('Mac_launcher_approval_and_failure_stop_fake_calls',check_mac_failure_stop)
    record('user_feed_unit_minimal_upgrade_fake_inputs',check_feed_upgrade)
    record('Cilium_and_D8_real_functions_fake_inputs',check_cilium_and_launch_fake_inputs)
    def render():
        from serving_runtime.enforcement.intent import parse_intent
        from serving_runtime.enforcement.tetragon import render_manifests
        from serving_runtime.enforcement.attribution import KERNEL_DATA
        d=json.loads((root/'deploy/gpu-lab/intent.aws-lab.json').read_text())
        d.pop('tetragon_kernel_identity',None)
        rendered=render_manifests(parse_intent(d))['tetragon-tg-code-01.json']
        assert all(x['data']==KERNEL_DATA for x in rendered['spec']['kprobes'])
        save(out/'default-code-render.json',rendered)
        return {'default_kernel_identity_data':True,'cluster_applied':False}
    record('actual_local_TG_CODE_render',render)
    def installer():
        path=root/'deploy/install/install_runtime_node.py'
        spec=importlib.util.spec_from_file_location('wp7_installer',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='wp7-install-') as temp:
            temp=Path(temp).resolve()
            for name in ['agent.token','viewer.env','kubeconfig']:
                f=temp/name;f.write_text('synthetic-local-fixture\n');f.chmod(0o600)
            config=temp/'install.env'
            config.write_text('\n'.join(['CONTEXT=byul-aws-lab','NAMESPACE=serving-lab','SERVER_ID=wp7-local',
                'PLATFORM_URL=https://platform.example.invalid','OBSERVATION_USER='+__import__('getpass').getuser(),
                'AGENT_TOKEN_FILE='+str(temp/'agent.token'),'VIEWER_ENV_FILE='+str(temp/'viewer.env'),
                'KUBECONFIG_FILE='+str(temp/'kubeconfig')])+'\n');config.chmod(0o600)
            code,text=run([sys.executable,str(path),'--config',str(config),'--dry-run'],timeout=30)
            # Expected macOS capability failure is preserved; not reported as installer success.
            (out/'installer-actual-dry-run.txt').write_text(text)
            assert code==1 and '중단:' in text and '설정·치환 검사 완료' in text
            with patch.object(module,'preflight',return_value=None) as preflight,patch.object(module,'install') as install:
                captured=io.StringIO()
                with contextlib.redirect_stdout(captured):
                    status=module.main(['--config',str(config),'--dry-run'])
                assert status==0 and preflight.called and not install.called
        return {'actual_mac_dry_run_exit':code,'linux_host_prerequisites':'AWS에서 처음 실행됨',
                'mocked_host_preflight_dry_run_exit':0,'install_not_called':True}
    record('installer_actual_and_fake_host_dry_run',installer)
    def matrix():
        from serving_runtime.gateway.app import GatewayApp
        from serving_runtime.devtools.mock_engine import MockEngine
        from serving_runtime.config.config import MVPConfig
        from serving_runtime.policy.policy import load_policy
        from labtools.selected_runtime_check import main as selected
        with tempfile.TemporaryDirectory(prefix='wp7-mock-') as temp:
            temp=Path(temp);inf,viewer=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
            auth={'schema_version':'runtime-auth/1.0','auth_ref':'WP7-SYNTHETIC',
                  'principals':[{'principal_id':'wp7-'+role,'roles':[role],
                    'token_sha256':hashlib.sha256(token.encode()).hexdigest()}
                    for role,token in [('inference',inf),('viewer',viewer)]],
                  'route_roles':{'POST /v1/chat/completions':['inference'],'GET /api/*':['viewer']}}
            save(temp/'auth.json',auth)
            policy={'schema_version':'runtime-mvp/1.0','policy_id':'wp7-mock','policy_version':'1',
                    'target_id':'wp7-mock','allowed_models':['test-model'],'output_default_tokens':8,
                    'output_max_tokens':16,'max_body_bytes':65536}
            save(temp/'policy.json',policy)
            config=MVPConfig(gateway_port=0,engine_port=0,policy_path=str(temp/'policy.json'),
                auth_config_path=str(temp/'auth.json'),data_dir=str(temp/'records'))
            engine=MockEngine(port=0).start();gateway=None
            try:
                gateway=GatewayApp(config,load_policy(temp/'policy.json'),engine_address=engine.address).start()
                url='http://localhost:'+str(gateway.address[1])
                with patch.dict(os.environ,{'WP7_LOCAL_INFERENCE':inf,'WP7_LOCAL_VIEWER':viewer}):
                    code=selected(['requests','--output',str(temp/'matrix.json'),'--model','test-model',
                         '--gateway-url',url,'--inference-token-env','WP7_LOCAL_INFERENCE',
                         '--viewer-token-env','WP7_LOCAL_VIEWER'])
                data=json.loads((temp/'matrix.json').read_text())
                count=len(data['cases']);passed=sum(x['result']=='passed' for x in data['cases'])
                # Export original selected-tool details after credential/address filtering.
                data=Sanitizer([inf,viewer]).value(data)
                data=json.loads(json.dumps(data).replace(str(root.parent),'$CHECKOUT'))
                assert not leaks(json.dumps(data),[inf,viewer])
                save(out/'mock-request-matrix.json',data)
                assert code==0 and count==passed==31
                return {'gateway_started':True,'mock_engine_started':True,'cases':count,'passed':passed,
                        'AWS_or_GPU_verified':False}
            finally:
                if gateway:gateway.stop()
                engine.stop()
    record('actual_Gateway_mock_and_existing_request_matrix',matrix)
    result={'started_as':'WP7 v2 offline preparation','finished_at':now(),'checks':results,
            'passed':sum(x['passed'] for x in results),'total':len(results),'AWS_commands_executed':0,
            'AWS_GPU_cases':'AWS에서 처음 실행됨'}
    save(out/'rehearsal.json',result)
    (out/'commands-and-output.txt').write_text('\n'.join(log)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if all(x['passed'] for x in results) else 1)
if __name__=='__main__':main()
