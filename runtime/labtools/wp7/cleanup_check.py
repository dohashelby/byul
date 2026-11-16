"""Read-only cloud inventory; dry-run is offline and default."""
from __future__ import annotations
import argparse,json
from common import approval,private_json,run
from export_evidence import Sanitizer,leaks

def aws(config,*args,timeout=45):
    region='us-east-1' if args and args[0]=='budgets' else 'ap-northeast-2'
    code,text=run(['aws','--profile',config['aws_profile'],'--region',region,
                   '--no-cli-pager',*args,'--output','json'],timeout=timeout)
    if code:raise ValueError('AWS_QUERY_FAILED')  # Never print raw AWS diagnostics.
    return json.loads(text) if text.strip() else {}

def inventory(c):
    filters=['Name=tag-value,Values=byul-check']
    args=['ec2','describe-instances']
    if c.get('instance_id'):args+=['--instance-ids',c['instance_id']]
    else:args+=['--filters',*filters]
    instances=[x for r in aws(c,*args)['Reservations'] for x in r['Instances']]
    ids={x['InstanceId'] for x in instances}
    volumes={v['VolumeId']:v for v in aws(c,'ec2','describe-volumes','--filters',*filters)['Volumes']}
    if ids:
        for v in aws(c,'ec2','describe-volumes','--filters','Name=attachment.instance-id,Values='+','.join(sorted(ids)))['Volumes']:
            volumes[v['VolumeId']]=v
    addresses={x['AllocationId']:x for x in aws(c,'ec2','describe-addresses','--filters',*filters)['Addresses']}
    if ids:
        for x in aws(c,'ec2','describe-addresses','--filters','Name=instance-id,Values='+','.join(sorted(ids)))['Addresses']:
            addresses[x['AllocationId']]=x
    snaps={x['SnapshotId']:x for x in aws(c,'ec2','describe-snapshots','--owner-ids','self','--filters',*filters)['Snapshots']}
    if volumes:
        for x in aws(c,'ec2','describe-snapshots','--owner-ids','self','--filters','Name=volume-id,Values='+','.join(volumes))['Snapshots']:
            snaps[x['SnapshotId']]=x
    raw={'instances':[{'id':x['InstanceId'],'state':x['State']['Name']} for x in instances],
         'volumes':[{'id':x['VolumeId'],'state':x['State'],'size_gib':x['Size']} for x in volumes.values()],
         'elastic_addresses':[{'id':x['AllocationId']} for x in addresses.values()],
         'snapshots':[{'id':x['SnapshotId'],'state':x['State']} for x in snaps.values()]}
    safe=Sanitizer().value(raw)
    safe['compute_stopped']=all(x['state'] in {'stopped','terminated'} for x in raw['instances'])
    safe['resources_zero']=all(x['state']=='terminated' for x in raw['instances']) and not volumes and not addresses and not snaps
    if leaks(json.dumps(safe)):raise ValueError('INVENTORY_EXPORT_UNSAFE')
    return safe

def main():
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--dry-run',action='store_true')
    p.add_argument('--approval-file');p.add_argument('--config');a=p.parse_args()
    if not a.execute:
        print(json.dumps({'mode':'offline-plan','read_only_calls':['describe-instances','describe-volumes',
            'describe-addresses','describe-snapshots'],'selection':'private existing dev ID or tag value byul-check',
            'between_rounds_expected':{'compute_stopped':True,'resources_zero':False,'reason':'retained disk'},
            'final_expected':{'compute_stopped':True,'resources_zero':True},
            'aws_commands_executed':0},indent=2));return
    approval(a.approval_file);print(json.dumps(inventory(private_json(a.config)),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
