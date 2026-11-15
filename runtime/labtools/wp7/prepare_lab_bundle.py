"""Create an explicitly approved, time-bounded LAB generation from deployed policy."""
import argparse,hashlib,json,os,shutil,subprocess,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from common import approval,private_json

def renew(source,destination,receipt):
    shutil.copytree(source,destination)
    manifest=json.loads((destination/'bundle.json').read_text())
    now=datetime.now(timezone.utc);ref='lab-wp7-'+uuid.uuid4().hex
    validity={'valid_from':(now-timedelta(seconds=60)).isoformat(),'valid_until':receipt['expires_at']}
    approval_record={'approved_by':'민기 (LAB WP7 실행 승인)','approved_at':now.isoformat(),'approval_ref':ref}
    by_role={x['role']:x for x in manifest['files']}
    policy_path=destination/by_role['gateway_policy']['path'];policy=json.loads(policy_path.read_text())
    policy.update(validity);policy['approval_ref']=ref
    policy_path.write_text(json.dumps(policy,ensure_ascii=False,indent=2)+'\n')
    approvals_path=destination/by_role['policy_approvals']['path']
    approvals=json.loads(approvals_path.read_text())
    approvals['entries']=[{'policy_id':policy['policy_id'],'policy_version':policy['policy_version'],
                           'sha256':hashlib.sha256(policy_path.read_bytes()).hexdigest(),
                           'approval_ref':ref,'approved_at':now.isoformat(),'valid_until':receipt['expires_at']}]
    approvals_path.write_text(json.dumps(approvals,ensure_ascii=False,indent=2)+'\n')
    manifest.update(bundle_id=destination.name,created_at=now.isoformat(),approval=approval_record,validity=validity)
    for entry in manifest['files']:
        f=destination/entry['path'];entry.update(bytes=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest())
    (destination/'bundle.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return manifest

def activate(config,receipt,kube,root,record_dir):
    # Query and validate every required live field before creating or switching anything.
    deployment=json.loads(kube('get','deployment','serving-gateway','-o','json'))
    containers=deployment['spec']['template']['spec']['containers'];idx=next(i for i,x in enumerate(containers) if x['name']=='gateway')
    args=list(containers[idx]['args']);i=args.index('--policy-bundle');old=args[i+1]
    original_link=os.readlink(root/'current');source=(root/'current').resolve(strict=True)
    destination=root/('wp7-approved-'+uuid.uuid4().hex);renew(source,destination,receipt)
    for f in [destination,*destination.rglob('*')]:os.chown(f,0,10000);os.chmod(f,0o750 if f.is_dir() else 0o640)
    from serving_runtime.policy.policy_bundle import verify_policy_bundle
    verify_policy_bundle(destination)
    new_args=list(args);new_args[i+1]='/etc/serving/bundles/current';patched=False;switched=False
    record={'previous_current':original_link,'LAB_generation':destination.name,'valid_until':receipt['expires_at'],
            'technical_policy_unchanged':True,'authority':'LAB execution approval only'}
    path=record_dir/('lab-generation-'+uuid.uuid4().hex+'.json')
    def args_patch(expected,value):
        kube('patch','deployment','serving-gateway','--type=json','-p',json.dumps([
            {'op':'test','path':f'/spec/template/spec/containers/{idx}/args','value':expected},
            {'op':'replace','path':f'/spec/template/spec/containers/{idx}/args','value':value}]))
    try:
        if new_args!=args:
            patched=True;args_patch(args,new_args)
        if os.readlink(root/'current')!=original_link:raise ValueError('CURRENT_CHANGED_BY_OTHER_WRITER')
        link=root/('current.wp7-'+uuid.uuid4().hex);link.symlink_to(destination.name);os.replace(link,root/'current');switched=True
        record['activated']=True;path.write_text(json.dumps(record,indent=2)+'\n');path.chmod(0o600)
    except Exception:
        if switched and os.readlink(root/'current')==destination.name:
            link=root/('current.wp7-restore-'+uuid.uuid4().hex);link.symlink_to(original_link);os.replace(link,root/'current')
        if patched:
            observed=json.loads(kube('get','deployment','serving-gateway','-o','json'))['spec']['template']['spec']['containers'][idx]['args']
            if observed==new_args:args_patch(new_args,args)
            elif observed!=args:raise ValueError('GATEWAY_ARGS_CHANGED_BY_OTHER_WRITER')
        raise
    return {'LAB_generation_verified':True,'technical_policy_unchanged':True,'valid_until':receipt['expires_at'],'previous_generation_preserved':True}

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--approval-file',required=True);a=p.parse_args()
    receipt=approval(a.approval_file);config=private_json(a.config)
    def kube(*args):
        r=subprocess.run(['kubectl','--kubeconfig',config['kubeconfig'],'--context',config['context'],'-n','serving-lab',*args],capture_output=True,text=True,timeout=30)
        if r.returncode:raise ValueError('LAB_PREPARATION_KUBE_FAILED')
        return r.stdout
    result=activate(config,receipt,kube,Path(config['bundle_root']),Path(a.config).parent);print(json.dumps(result))
if __name__=='__main__':main()
