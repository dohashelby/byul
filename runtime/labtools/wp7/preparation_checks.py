import ast,contextlib,hashlib,importlib.util,io,json,os,sys,tempfile,types
from pathlib import Path
from unittest.mock import patch
repo=Path(__file__).resolve().parents[4];wp=Path(__file__).resolve().parent;sys.path[:0]=[str(wp),str(repo/'runtime/src')]
import prepare_lab_bundle,server_inputs
from serving_runtime.policy.policy_bundle import verify_policy_bundle
from serving_runtime.policy.policy import load_policy
def run():
    results=[]
    with tempfile.TemporaryDirectory(prefix='wp7-prepare-check-') as t:
     root=Path(t);src=repo/'contracts/examples/bundle';dst=root/'renewed'
     before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir() if p.is_file()}
     prepare_lab_bundle.renew(src,dst,{'expires_at':'2026-11-15T22:00:00+09:00'})
     verify_policy_bundle(dst);assert load_policy(dst/'runtime-policy.json').ok
     old=json.loads((src/'runtime-policy.json').read_text());new=json.loads((dst/'runtime-policy.json').read_text())
     for k in ['approval_ref','valid_from','valid_until']:old.pop(k,None);new.pop(k,None)
     assert old==new
     assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir() if p.is_file()}
     results.append({'check':'real_bundle_and_policy_verifiers_after_renewal','passed':True,'technical_policy_preserved':True,'source_preserved':True})
     tree=ast.parse((wp/'bootstrap_server.py').read_text());script=next(n.value.value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='script' for x in n.targets))
     pkg=root/'pkg';pkg.mkdir();(pkg/'src').mkdir();(pkg/'pyproject.toml').write_text('[project]\nversion="0.0.0.dev0"\n')
     site=root/'site'
     with patch.object(sys,'argv',['-c',str(pkg),str(site)]):exec(script,{})
     assert (site/'wp7-runtime.pth').read_text().splitlines()==[str(pkg/'src'),str(pkg)]
     assert (site/'serving_runtime-0.0.0.dev0.dist-info/METADATA').read_text().splitlines()==['Metadata-Version: 2.1','Name: serving-runtime','Version: 0.0.0.dev0']
     results.append({'check':'actual_nested_offline_install_script_writes_valid_files','passed':True})
    with tempfile.TemporaryDirectory(prefix='wp7-server-input-fixture-') as t:
     root=Path(t)
     def path(value):
      raw=str(value)
      if raw.startswith(str(root)):return Path(value)
      if raw.startswith(('/home/','/etc/','/srv/','/var/')):return root/raw.lstrip('/')
      return Path(value)
     def write(name,text):
      p=path(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text);p.chmod(0o600);return p
     write('/etc/systemd/system/serving-runtime-agent.service','ExecStart=python agent --server server-test --context byul-aws-lab\n')
     write('/etc/serving-runtime/agent.env','PLATFORM_URL=https://platform.example.invalid\n')
     write('/etc/serving-runtime/agent.token','PRIVATE_AGENT_TEST_TOKEN\n')
     write('/etc/serving-runtime/viewer.env','RUNTIME_VIEWER_TOKEN=PRIVATE_VIEWER_TEST_TOKEN\n')
     write('/etc/serving-runtime/kubeconfig','PRIVATE_CONFIG_TEST\n')
     write('/home/ubuntu/runtime-acceptance.env','RUNTIME_VIEWER_TOKEN=PRIVATE_VIEWER_TEST_TOKEN\nRUNTIME_INFERENCE_TOKEN=PRIVATE_INFERENCE_TEST_TOKEN\n')
     write('/srv/serving/runtime/data/placeholder','x');write('/srv/serving/observation/tetragon.jsonl','{}\n')
     write('/srv/serving/bundles/current/target.json',json.dumps({'engine':{'image_digest':'sha256:'+'a'*64}}))
     write('/srv/serving/bundles/current/intent.json','{}\n')
     output=path('/home/ubuntu/wp7/server-config.json');stream=io.StringIO()
     user=types.SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid(),pw_name='ubuntu')
     with patch.object(server_inputs,'Path',path),patch.object(server_inputs.pwd,'getpwnam',return_value=user),patch.object(server_inputs.pwd,'getpwuid',return_value=user),patch.object(sys,'argv',['server_inputs','--output',str(output)]),contextlib.redirect_stdout(stream):server_inputs.main()
     config=json.loads(stream.getvalue());assert config['context']=='byul-aws-lab';assert 'PRIVATE_' not in stream.getvalue();assert Path(config['installer_env']).is_file();assert Path(config['token_env_file']).stat().st_mode&0o077==0
     entries=server_inputs.pairs(Path(config['installer_env']));assert entries['SERVER_ID']=='server-test';assert entries['PLATFORM_URL']=='https://platform.example.invalid'
     results.append({'check':'reconstruct_deployed_inputs_without_original_install_env','passed':True,'credential_values_exported':False})
    # Execute the old LAB-switch embedded script against a real file, including its JSON writes.
    with tempfile.TemporaryDirectory(prefix='wp7-lab-switch-check-') as t:
     root=Path(t);old=root/'old';old.mkdir();(old/'target.json').write_text('{}');(old/'bundle.json').write_text(json.dumps({'files':[{'role':'target','path':'target.json'}]}))
     tree=ast.parse((wp/'server_checks.py').read_text());fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='lab_bundle');script=next(n.value.value for n in ast.walk(fn) if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='script' for x in n.targets))
     with patch.object(sys,'argv',['script',str(root),'old','new','{"trust_remote_code": false}']),patch.object(os,'chown',return_value=None):exec(script,{})
     json.loads((root/'new/target.json').read_text());json.loads((root/'new/bundle.json').read_text())
     results.append({'check':'actual_nested_LAB_switch_JSON_files_parse','passed':True})
    return {'checks':results,'passed':len(results),'AWS_commands':0}

if __name__=='__main__':print(json.dumps(run()))
